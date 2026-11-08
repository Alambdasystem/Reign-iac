"""Single browser worker pilot. No public CDP, shell, arbitrary code, or filesystem API."""
import asyncio
import contextlib
import os
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from playwright.async_api import async_playwright
from temporalio import activity
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from reign.config import secret, checked_url, temporal_address, QUEUE, NAMESPACE
from reign.workflows import OpenBrowser, NavigateBrowser


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

    async def guard(self, route):
        try:
            checked_url(route.request.url)
            await route.continue_()
        except ValueError:
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
                "title": await s["page"].title(), "url": s["page"].url,
                "http_status": s["http_status"], "viewport": {"width": 1280, "height": 800}}

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
            await context.route("**/*", self.guard)
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
            if s["mode"] != "agent" or s["epoch"] != epoch:
                raise HTTPException(409, "Automation blocked: control changed")
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
            s["mode"] = mode
            s["epoch"] += 1
            return await self.info(sid)

    async def input(self, sid, data):
        async with self.lock:
            s = self.get(sid)
            if s["mode"] != "human" or s["epoch"] != data.epoch:
                raise HTTPException(409, "Human input requires current takeover ownership")
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
        raise ApplicationError(str(e), non_retryable=True) from e


@activity.defn
async def navigate_browser(data: dict) -> dict:
    try:
        return await manager.navigate(data["id"], data["url"], data["epoch"])
    except (ValueError, HTTPException) as e:
        raise ApplicationError(str(e), non_retryable=True) from e


async def run_worker():
    while True:
        try:
            client = await Client.connect(temporal_address(), namespace=NAMESPACE)
            async with Worker(client, task_queue=QUEUE, workflows=[OpenBrowser, NavigateBrowser],
                              activities=[open_browser, navigate_browser], max_concurrent_activities=1):
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


def authorize(request: Request):
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not secrets.compare_digest(token, app.state.token):
        raise HTTPException(401, "Unauthorized")


@app.get("/healthz", dependencies=[Depends(authorize)])
async def health():
    if not manager.worker_ready:
        raise HTTPException(503, "Temporal worker not ready")
    return {"status": "ready", "worker": "reign-computer", "sessions": len(manager.sessions)}


@app.get("/sessions/{sid}", dependencies=[Depends(authorize)])
async def info(sid: str):
    async with manager.lock:
        return await manager.info(sid)


@app.get("/sessions/{sid}/frame", dependencies=[Depends(authorize)])
async def frame(sid: str):
    async with manager.lock:
        page = manager.get(sid)["page"]
        data = await page.screenshot(type="jpeg", quality=65, timeout=5000)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


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
