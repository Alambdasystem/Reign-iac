"""Single browser worker pilot. No public CDP, shell, arbitrary code, or filesystem API."""
import asyncio
import contextlib
import html
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from urllib.parse import quote, quote_plus, urlsplit, urlunsplit

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from reign.config import secret, checked_url, temporal_address, QUEUE, NAMESPACE
from reign.browser_actions import (ACTION_TIMEOUT_MS, ActionRequest, Observation,
                                   allowed_origin, validate_target)
from reign.session_errors import BROWSER_OCCUPIED_MESSAGE
from reign.tool_errors import BrowserActionError, browser_action_error_response
from reign.workflows import OpenBrowser, NavigateBrowser, BrowserAction, FillAccount


class BrowserSessions:
    def __init__(self):
        self.sessions = {}
        self.lock = asyncio.Lock()
        self.browser = None
        self.playwright = None
        self.worker_ready = False

    async def launch(self):
        self.playwright = await async_playwright().start()
        launch = {"headless": True, "args": ["--disable-dev-shm-usage"]}
        # The container is dedicated to allowlisted Git High pages only. Render's
        # container runtime does not expose custom Chromium seccomp configuration.
        if os.getenv("BROWSER_PROXY"):
            launch["proxy"] = {"server": os.environ["BROWSER_PROXY"]}
        self.browser = await self.playwright.chromium.launch(**launch)

    async def guard(self, route, context=None):
        try:
            checked_url(route.request.url)
            # Once credentials have been injected, even another configured host
            # is not a permitted recipient for this context's requests.
            current = next((s for s in self.sessions.values() if s.get("context") is context), None)
            if current and current.get("credential_origin") and allowed_origin(route.request.url) != current["credential_origin"]:
                raise ValueError("Credential origin is pinned")
            await route.continue_()
        except (ValueError, BrowserActionError):
            await route.abort("blockedbyclient")

    def get(self, sid):
        s = self.sessions.get(sid)
        if s is None:
            raise HTTPException(404, "Browser session expired or worker restarted; open a new session")
        s["last_access"] = time.monotonic()
        return s

    async def info(self, sid):
        s = self.get(sid)
        return {"id": sid, "mode": s["mode"], "epoch": s["epoch"],
                "title": self.redact(s, await s["page"].title())[:300],
                "url": self.public_url(s, s["page"].url),
                "http_status": s["http_status"], "viewport": {"width": 1280, "height": 800}}

    @staticmethod
    def secret_variants(private):
        # Match the browser observation's whitespace normalization. Without
        # this, an echoed password with leading/repeated whitespace could leak.
        normalized = re.sub(r"[\u0009-\u000d\u0020\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]+", " ", private).strip(" ")
        variants = set()
        for value in (private, normalized):
            if value:
                variants.update((value, quote(value, safe=""), quote_plus(value), html.escape(value)))
        return variants

    @staticmethod
    def redact(session, value):
        """Remove injected credentials from public text, including URL encodings."""
        if isinstance(value, str):
            variants = set()
            for private in session.get("redactions", ()):
                if private:
                    variants.update(BrowserSessions.secret_variants(private))
            for variant in sorted(variants, key=len, reverse=True):
                value = value.replace(variant, "[redacted]")
            return value
        if isinstance(value, list):
            return [BrowserSessions.redact(session, item) for item in value]
        if isinstance(value, dict):
            return {key: BrowserSessions.redact(session, item) for key, item in value.items()}
        return value

    @staticmethod
    def public_url(session, value):
        # Query strings and fragments may contain authentication codes or tokens.
        try:
            parsed = urlsplit(value)
            public = urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))
        except ValueError:
            public = ""
        return BrowserSessions.redact(session, public)[:2048]

    async def invalidate(self, session):
        observation = session.pop("observation", None)
        if observation:
            await observation.dispose()

    async def secret_nodes(self, session):
        retained = []
        for element in session.get("secret_nodes", ()):
            try:
                if await element.evaluate("el => el.isConnected && el.ownerDocument===document"):
                    retained.append(element)
                    continue
            except Exception:
                pass
            with contextlib.suppress(Exception):
                await element.dispose()
        session["secret_nodes"] = retained
        return retained

    @staticmethod
    def require_agent(session, epoch):
        if session["mode"] != "agent" or session["epoch"] != epoch:
            raise HTTPException(409, "Automation blocked: control changed")

    def take_observation(self, session, observation_id, epoch):
        observation = session.pop("observation", None)
        if not observation:
            raise BrowserActionError("stale_observation")
        return observation

    @staticmethod
    def parse_action(data, *, preflight=False):
        try:
            if preflight:
                if not isinstance(data, dict) or "text" in data:
                    raise ValueError("Preflight must not contain text")
                data = dict(data)
                if data.get("action") == "fill":
                    data["text"] = ""
                if data.get("action") == "scroll" and "dy" not in data:
                    data["dy"] = 0
            return data if isinstance(data, ActionRequest) else ActionRequest.model_validate(data)
        except (ValueError, TypeError):
            raise BrowserActionError("invalid_action") from None

    async def validate_action(self, sid, data):
        request = self.parse_action(data, preflight=True)
        async with self.lock:
            s = self.get(sid)
            self.require_agent(s, request.epoch)
            observation = s.get("observation")
            if not observation or observation.id != request.observation_id:
                raise BrowserActionError("stale_observation")
            try:
                observation.check_context(s["page"], request.epoch)
                await validate_target(observation, request, s.get("credential_origin"))
            except BrowserActionError:
                raise
            except Exception:
                raise BrowserActionError("stale_observation") from None
            return {"valid": True}

    async def observe(self, sid, epoch):
        async with self.lock:
            s = self.get(sid)
            if s["epoch"] != epoch:
                raise BrowserActionError("stale_observation")
            await self.invalidate(s)
            try:
                variants = set().union(*(self.secret_variants(value) for value in s.get("redactions", ())))
                observation = await Observation.capture(s["page"], epoch, await self.secret_nodes(s),
                                                        sorted(variants, key=len, reverse=True))
                s["observation"] = observation
                payload = await observation.payload()
                payload["text"] = self.redact(s, payload["text"])
                for element in payload["elements"]:
                    element["name"] = self.redact(s, element["name"])
                result = {**await self.info(sid), "observation_id": observation.id,
                          **payload}
                return result
            except BrowserActionError:
                raise
            except Exception:
                await self.invalidate(s)
                raise BrowserActionError("stale_observation") from None

    async def act(self, sid, data):
        request = self.parse_action(data)
        async with self.lock:
            s = self.get(sid)
            self.require_agent(s, request.epoch)
            observation = self.take_observation(s, request.observation_id, request.epoch)
            started, element = False, None
            try:
                if observation.id != request.observation_id:
                    raise BrowserActionError("stale_observation")
                observation.check_context(s["page"], request.epoch)
                await validate_target(observation, request, s.get("credential_origin"))
                if request.text is not None and any(private and private in request.text for private in s.get("redactions", ())):
                    raise BrowserActionError("secret_field")
                if request.ref:
                    element = await observation.element(request.ref)
                    if element is None:
                        raise BrowserActionError("invalid_target")
                    await observation.arm(request.ref, request.action)
                # No retry is safe once an input event may have reached the page.
                started = True
                if request.action == "click":
                    await element.click(timeout=ACTION_TIMEOUT_MS)
                elif request.action == "fill":
                    await element.fill(request.text, timeout=ACTION_TIMEOUT_MS)
                elif request.action == "press":
                    await element.press(" " if request.key == "Space" else request.key,
                                        timeout=ACTION_TIMEOUT_MS)
                else:
                    await s["page"].mouse.wheel(0, request.dy)
                if request.ref:
                    await observation.check_dispatch(navigation_allowed=request.action in {"click", "press"})
                allowed_origin(s["page"].url)
                return {**await self.info(sid), "acted": True}
            except BrowserActionError:
                if started:
                    raise BrowserActionError("action_uncertain") from None
                raise
            except Exception:
                raise BrowserActionError("action_uncertain" if started else "stale_observation") from None
            finally:
                if element:
                    with contextlib.suppress(Exception):
                        await element.dispose()
                await observation.dispose()

    async def fill_account(self, sid, grant_id, observation_id, epoch):
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0 or not isinstance(observation_id, str):
            raise BrowserActionError("invalid_action")
        async with self.lock:
            s = self.get(sid)
            self.require_agent(s, epoch)
            observation = self.take_observation(s, observation_id, epoch)
            started = False
            credentials = None
            username = password = None
            try:
                if observation.id != observation_id:
                    raise BrowserActionError("stale_observation")
                observation.check_context(s["page"], epoch)
                await observation.account()
                if len(s.get("redactions", ())) >= 64:
                    raise BrowserActionError("credential_unavailable")
                if not isinstance(grant_id, str) or not 1 <= len(grant_id) <= 512:
                    raise BrowserActionError("credential_unavailable")
                try:
                    from reign.credential_transport import redeem_credentials
                    credentials = await redeem_credentials(grant_id, sid, observation_id, epoch, observation.origin)
                    username, password = credentials["username"], credentials["password"]
                    if not isinstance(username, str) or not 1 <= len(username) <= 320 or not isinstance(password, str) or not 1 <= len(password) <= 1024:
                        raise ValueError("Invalid credential material")
                except Exception:
                    raise BrowserActionError("credential_unavailable") from None
                # Redemption is one-use. Page changes while it is in flight do
                # not authorize filling a different form or refreshing the grant.
                observation.check_context(s["page"], epoch)
                await observation.account()
                user_field, password_fields = await observation.account_fields()
                # Usernames may be ordinary public account labels; passwords
                # must also be removed if a page echoes them outside a field.
                s.setdefault("redactions", set()).add(password)
                s.setdefault("secret_nodes", []).extend((user_field, *password_fields))
                s["credential_origin"] = observation.origin
                await observation.arm_account(-1)
                started = True
                await user_field.fill(username, timeout=ACTION_TIMEOUT_MS)
                await observation.check_dispatch()
                for index, password_field in enumerate(password_fields):
                    await observation.account(fresh=False)
                    await observation.arm_account(index)
                    await password_field.fill(password, timeout=ACTION_TIMEOUT_MS)
                    await observation.check_dispatch()
                await observation.account(fresh=False)
                matches = await user_field.evaluate("(el,value) => el.value===value", username)
                for password_field in password_fields:
                    matches = matches and await password_field.evaluate("(el,value) => el.value===value", password)
                if not matches:
                    raise BrowserActionError("action_uncertain")
                return {**await self.info(sid), "filled": True}
            except BrowserActionError:
                if started:
                    raise BrowserActionError("action_uncertain") from None
                raise
            except Exception:
                raise BrowserActionError("action_uncertain" if started else "stale_observation") from None
            finally:
                if isinstance(credentials, dict):
                    credentials.clear()
                username = password = None
                await observation.dispose()

    async def active(self):
        # This pilot has one authenticated administrator and one browser slot.
        # Discovery reconnects a lost tab reference without creating a browser
        # or changing the existing page, control owner, or ownership epoch.
        async with self.lock:
            sid = next(iter(self.sessions), None)
            return {"session": await self.info(sid) if sid is not None else None}

    async def open(self, sid, url):
        checked_url(url)
        async with self.lock:
            if sid in self.sessions:
                return await self.info(sid)
            if len(self.sessions) >= int(os.getenv("MAX_SESSIONS", "1")):
                raise HTTPException(409, "Worker is occupied; close the active session first")
            if self.browser is None:
                await self.launch()
            context = await self.browser.new_context(viewport={"width": 1280, "height": 800},
                                                     accept_downloads=False, service_workers="block")
            await context.route("**/*", lambda route: self.guard(route, context))
            # This pilot has no websocket applications; block sockets as well as HTTP.
            await context.route_web_socket("**/*", lambda ws: ws.close())
            page = await context.new_page()
            page.on("popup", lambda popup: asyncio.create_task(popup.close()))
            page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                checked_url(page.url)
                if response is None or response.status >= 400:
                    raise ValueError("Target did not return a successful HTTP response")
                self.sessions[sid] = {"context": context, "page": page, "mode": "agent", "epoch": 0,
                                      "last_access": time.monotonic(), "http_status": response.status}
                return await self.info(sid)
            except BaseException:
                await context.close()
                raise

    async def navigate(self, sid, url, epoch):
        checked_url(url)
        async with self.lock:
            s = self.get(sid)
            self.require_agent(s, epoch)
            if s.get("credential_origin") and allowed_origin(url) != s["credential_origin"]:
                raise BrowserActionError("origin_blocked")
            await self.invalidate(s)
            response = await s["page"].goto(url, wait_until="domcontentloaded", timeout=45000)
            checked_url(s["page"].url)
            s["http_status"] = response.status if response else None
            if response is None or response.status >= 400:
                raise ValueError("Target did not return a successful HTTP response")
            return await self.info(sid)

    async def control(self, sid, mode, epoch):
        async with self.lock:
            s = self.get(sid)
            if epoch != s["epoch"]:
                raise HTTPException(409, "Stale control state; refresh the session")
            if mode == s["mode"]:
                raise HTTPException(409, "Control already belongs to requested controller")
            if (s["mode"], mode) not in {("agent", "human"), ("human", "agent")}:
                raise HTTPException(409, "Invalid control transition")
            await self.invalidate(s)
            s["mode"] = mode
            s["epoch"] += 1
            return await self.info(sid)

    async def input(self, sid, data):
        async with self.lock:
            s = self.get(sid)
            if s["mode"] != "human" or s["epoch"] != data.epoch:
                raise HTTPException(409, "Human input requires current takeover ownership")
            await self.invalidate(s)
            page = s["page"]
            if data.kind == "click":
                await page.mouse.click(data.x, data.y)
            elif data.kind == "text":
                await page.keyboard.insert_text(data.text)
            elif data.kind == "key":
                if data.key not in {"Enter", "Tab", "Shift+Tab", "Backspace", "Escape", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "Control+A"}:
                    raise HTTPException(400, "Unsupported key")
                await page.keyboard.press(data.key)
            else:
                await page.mouse.wheel(0, data.dy)
            return await self.info(sid)

    async def close(self, sid):
        async with self.lock:
            s = self.sessions.pop(sid, None)
            if s:
                await self.invalidate(s)
                for element in s.get("secret_nodes", ()):
                    with contextlib.suppress(Exception):
                        await element.dispose()
                s.pop("redactions", None)
                await s["context"].close()

    async def reap(self):
        while True:
            await asyncio.sleep(30)
            for sid, s in list(self.sessions.items()):
                if time.monotonic() - s["last_access"] > 1800:
                    await self.close(sid)

    async def shutdown(self):
        for sid in list(self.sessions):
            await self.close(sid)
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()


manager = BrowserSessions()


@activity.defn
async def open_browser(data: dict) -> dict:
    try:
        return await manager.open(data["id"], data["url"])
    except (ValueError, HTTPException) as e:
        if isinstance(e, HTTPException) and e.status_code == 409:
            raise ApplicationError(BROWSER_OCCUPIED_MESSAGE, type="WorkerOccupied", non_retryable=True) from e
        raise ApplicationError(str(e), non_retryable=True) from e


@activity.defn
async def navigate_browser(data: dict) -> dict:
    try:
        return await manager.navigate(data["id"], data["url"], data["epoch"])
    except BrowserActionError as e:
        raise ApplicationError(e.code, type="BrowserActionRejected", non_retryable=True) from None
    except (ValueError, HTTPException) as e:
        if isinstance(e, HTTPException) and e.status_code == 409:
            raise ApplicationError("Control changed", type="ControlChanged", non_retryable=True) from None
        raise ApplicationError("invalid_action", type="BrowserActionRejected", non_retryable=True) from None
    except Exception:
        raise ApplicationError("action_uncertain", type="BrowserActionUncertain", non_retryable=True) from None


def action_application_error(error):
    if isinstance(error, BrowserActionError):
        return ApplicationError(error.code,
            type="BrowserActionUncertain" if error.code == "action_uncertain" else "BrowserActionRejected",
            non_retryable=True)
    if isinstance(error, HTTPException) and error.status_code == 409:
        return ApplicationError("Control changed", type="ControlChanged", non_retryable=True)
    if isinstance(error, HTTPException) and error.status_code == 404:
        return ApplicationError("invalid_target", type="BrowserActionRejected", non_retryable=True)
    return ApplicationError("action_uncertain", type="BrowserActionUncertain", non_retryable=True)


@activity.defn
async def browser_action(data: dict) -> dict:
    try:
        return await manager.act(data["id"], {key: value for key, value in data.items() if key != "id"})
    except Exception as error:
        raise action_application_error(error) from None


@activity.defn
async def fill_account(data: dict) -> dict:
    try:
        return await manager.fill_account(data["id"], data["grant_id"], data["observation_id"], data["epoch"])
    except Exception as error:
        raise action_application_error(error) from None


async def run_worker():
    while True:
        try:
            client = await Client.connect(temporal_address(), namespace=NAMESPACE)
            async with Worker(client, task_queue=QUEUE, workflows=[OpenBrowser, NavigateBrowser, BrowserAction, FillAccount],
                              activities=[open_browser, navigate_browser, browser_action, fill_account], max_concurrent_activities=1):
                manager.worker_ready = True
                await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            manager.worker_ready = False
            await asyncio.sleep(3)
        finally:
            manager.worker_ready = False


@asynccontextmanager
async def lifespan(app):
    app.state.token = secret("COMPUTER_TOKEN")
    tasks = [asyncio.create_task(run_worker()), asyncio.create_task(manager.reap())]
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await manager.shutdown()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_exception_handler(BrowserActionError, browser_action_error_response)


def authorize(request: Request):
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not secrets.compare_digest(token, app.state.token):
        raise HTTPException(401, "Unauthorized")


@app.get("/healthz", dependencies=[Depends(authorize)])
async def health():
    if not manager.worker_ready:
        raise HTTPException(503, "Temporal worker not ready")
    return {"status": "ready", "worker": "reign-computer", "sessions": len(manager.sessions)}


@app.get("/sessions/active", dependencies=[Depends(authorize)])
async def active_session():
    return await manager.active()


@app.get("/sessions/{sid}", dependencies=[Depends(authorize)])
async def info(sid: str):
    async with manager.lock:
        return await manager.info(sid)


@app.get("/sessions/{sid}/observe", dependencies=[Depends(authorize)])
async def observe(sid: str, epoch: int):
    return JSONResponse(await manager.observe(sid, epoch), headers={"Cache-Control": "no-store"})


@app.post("/sessions/{sid}/validate-action", dependencies=[Depends(authorize)])
async def validate_action(sid: str, data: dict):
    return JSONResponse(await manager.validate_action(sid, data), headers={"Cache-Control": "no-store"})


@app.get("/sessions/{sid}/frame", dependencies=[Depends(authorize)])
async def frame(sid: str):
    async with manager.lock:
        session = manager.get(sid)
        page = session["page"]
        # Mask all editable fields, including a password changed to type=text
        # by page script. Human viewing must not turn a credential into evidence.
        masks = [page.locator("input,textarea,select,[contenteditable]")]
        for private in session.get("redactions", ()):
            for variant in BrowserSessions.secret_variants(private):
                masks.append(page.get_by_text(variant, exact=False))
        try:
            data = await page.screenshot(type="jpeg", quality=65, timeout=5000,
                                         caret="initial", mask=masks)
        except Exception:
            raise HTTPException(503, "Browser frame unavailable") from None
        session["frame_sequence"] = session.get("frame_sequence", 0) + 1
        headers = {"Cache-Control": "no-store", "X-Session-ID": sid,
                   "X-Frame-Sequence": str(session["frame_sequence"]),
                   "X-Frame-Timestamp": str(time.time_ns())}
    return Response(data, media_type="image/jpeg", headers=headers)


class Control(BaseModel):
    mode: str = Field(pattern="^(agent|human)$")
    epoch: int = Field(ge=0)


class Input(BaseModel):
    kind: str = Field(pattern="^(click|text|key|scroll)$")
    epoch: int = Field(ge=0)
    x: float = Field(default=0, ge=0, lt=1280)
    y: float = Field(default=0, ge=0, lt=800)
    text: str = Field(default="", max_length=2000)
    key: str = Field(default="", max_length=30)
    dy: int = Field(default=0, ge=-1000, le=1000)


@app.post("/sessions/{sid}/control", dependencies=[Depends(authorize)])
async def control(sid: str, data: Control):
    return await manager.control(sid, data.mode, data.epoch)


@app.post("/sessions/{sid}/input", dependencies=[Depends(authorize)])
async def input_event(sid: str, data: Input):
    return await manager.input(sid, data)


@app.delete("/sessions/{sid}", dependencies=[Depends(authorize)])
async def close(sid: str):
    await manager.close(sid)
    return {"closed": True}
