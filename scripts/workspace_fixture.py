"""Controlled CI acceptance for the real browser/MCP/graph/vault stack.

Only the model and website are fixtures. Core/Computer HTTP, private stdio MCP,
LangGraph, Temporal workflows/activities, Playwright, credential encryption and
the mounted SQLite vault are the real application implementations. Run initial
and restart in different containers with the same disposable /fixture-vault
volume. This script cannot establish live-provider or external-account success.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import logging
import os
import re
import secrets
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ORIGIN = "https://alambda-forge.onrender.com"
LABEL = "Controlled workspace fixture login"
USERNAME = "fixture-user@example.invalid"
REPOSITORY = "reign-controlled-private-fixture"
CORE_URL = "http://127.0.0.1:18001"
COMPUTER_URL = "http://127.0.0.1:18002"
FIXTURE_TLS_PORT = 18443
STAGE = "configuration"
SITE_REQUESTS = []


def path_category(value):
    # A malformed form could put credentials in its URL. Diagnostics therefore
    # include only fixed categories, never a raw path, query, fragment, or body.
    return {"/": "root", "/login": "login", "/session": "session",
            "/workspace": "workspace", "/repositories": "repository_create",
            "/repositories/" + REPOSITORY: "repository_detail",
            "/favicon.ico": "favicon"}.get(value, "unknown")


class FixtureFailure(Exception):
    """Only fixed, credential-free failure codes may reach the receipt."""


def require(condition, code):
    if not condition:
        raise FixtureFailure(code)


def configure():
    phase = os.getenv("SMOKE_PHASE", "initial")
    require(phase in {"initial", "restart"}, "invalid_fixture_phase")
    address = os.getenv("TEMPORAL_ADDRESS", "temporal:7233")
    require(address in {"temporal:7233", "127.0.0.1:7233", "localhost:7233"}, "fixture_temporal_address_required")
    queue = os.getenv("TEMPORAL_TASK_QUEUE", "reign-workspace-fixture-" + uuid.uuid4().hex[:12])
    require(bool(re.fullmatch(r"reign-workspace-fixture-[A-Za-z0-9_-]{1,80}", queue)), "fixture_task_queue_required")
    directory = Path(os.getenv("REIGN_VAULT_DIR", "/fixture-vault"))
    require(directory.is_absolute() and directory.is_dir() and os.path.ismount(directory)
            and directory != Path("/"), "disposable_vault_mount_required")

    # These values exist solely for the controlled fixture. A stable seed lets a
    # fresh restart container decrypt the same disposable database without ever
    # persisting a key or plaintext credential beside it. Deployment credentials
    # are not read or reused. The default is deliberately not a production key.
    seed = os.getenv("REIGN_FIXTURE_SEED", "reign-controlled-ci-fixture-never-production")

    def material(purpose):
        return hashlib.sha256((seed + ":" + purpose).encode()).digest()

    password = base64.urlsafe_b64encode(material("website-password")).decode().rstrip("=")
    values = {
        "TEMPORAL_ADDRESS": address, "TEMPORAL_TASK_QUEUE": queue, "TEMPORAL_NAMESPACE": "default",
        "CORE_URL": CORE_URL, "COMPUTER_URL": COMPUTER_URL,
        "CORE_TOKEN": material("core-auth").hex(), "COMPUTER_TOKEN": material("computer-auth").hex(),
        "REIGN_VAULT_DIR": str(directory),
        "REIGN_VAULT_KEY": base64.b64encode(material("vault-key")).decode(),
        "CREDENTIAL_CHANNEL_KEY": base64.b64encode(material("credential-channel")).decode(),
        "ALLOWED_HOSTS": "alambda-forge.onrender.com", "MAX_SESSIONS": "1",
        "OPENAI_API_KEY": "controlled-scripted-model-no-live-provider",
        "OPENAI_MODEL": "controlled-scripted-model", "OPENAI_BASE_URL": "https://provider.invalid/v1",
    }
    os.environ.update(values)
    os.environ.pop("BROWSER_PROXY", None)
    forbidden = [password, values["CORE_TOKEN"], values["COMPUTER_TOKEN"],
                 values["REIGN_VAULT_KEY"], values["CREDENTIAL_CHANNEL_KEY"]]
    return phase, queue, directory, password, forbidden


class Audit:
    def __init__(self, forbidden):
        self.forbidden = [value.encode() for value in forbidden]
        self.model_messages = 0
        self.api_responses = 0
        self.history_events = 0

    def inspect(self, value, code):
        if isinstance(value, str):
            value = value.encode()
        elif not isinstance(value, bytes):
            value = json.dumps(value, default=str, ensure_ascii=False).encode()
        require(not any(secret in value for secret in self.forbidden), code)


def fixture_certificate(directory):
    """Ephemeral loopback-site TLS material; never an artifact or deployment key."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Controlled REIGN fixture")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=2))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("alambda-forge.onrender.com")]), critical=False)
        .sign(key, hashes.SHA256()))
    private_path, certificate_path = Path(directory) / "key.pem", Path(directory) / "certificate.pem"
    descriptor = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as file:
        file.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return str(private_path), str(certificate_path)


def document(title, body):
    # This deliberately blocked image proves the production guard still runs.
    # The fixture's proxy maps only the allowed host to its loopback TLS server.
    return ("<!doctype html><html><head><meta charset=utf-8><title>" + html.escape(title)
            + "</title><style>body{font:18px sans-serif;margin:32px}label{display:block;margin:16px 0}"
              "input,button{font:inherit;padding:8px}</style></head><body>"
            + body + '<img src="https://fixture-denied.invalid/pixel" width="1" height="1" alt="">'
            + "</body></html>")


class ControlledWebsite:
    def __init__(self, password):
        self.password = password
        self.login_count = 0
        self.creation_count = 0
        self.workspace_reads = 0
        self.policy_blocks = 0
        self.fixture_requests = 0
        self.repositories = {}
        self.failure = None
        self.proxy_tunnels = 0
        self.login_redirects = 0
        self.login_redirect_follows = 0
        self.repository_redirects = 0
        self.repository_redirect_follows = 0
        self.browser_login_redirect_follows = 0
        self.browser_repository_redirect_follows = 0

    def observe_browser_request(self, request):
        previous = request.redirected_from
        if previous is None or request.method != "GET" or previous.method != "POST":
            return
        source = path_category(urlsplit(previous.url).path)
        destination = path_category(urlsplit(request.url).path)
        if (source, destination) == ("session", "workspace"):
            self.browser_login_redirect_follows += 1
        elif (source, destination) == ("repository_create", "repository_detail"):
            self.browser_repository_redirect_follows += 1

    def authenticated(self, request):
        return secrets.compare_digest(request.cookies.get("fixture-session", ""), "authenticated")

    async def respond(self, request):
        from fastapi.responses import Response

        parsed = urlsplit(str(request.url))
        if len(SITE_REQUESTS) < 32:
            SITE_REQUESTS.append({
                "method": request.method if request.method in {"GET", "POST", "HEAD", "OPTIONS"} else "OTHER",
                "path_category": path_category(parsed.path),
            })
        require(parsed.scheme == "https" and parsed.netloc == "alambda-forge.onrender.com",
                "fixture_continuation_origin_mismatch")
        self.fixture_requests += 1
        status, headers, body = 200, {"Cache-Control": "no-store", "Content-Type": "text/html; charset=utf-8"}, ""
        if request.method == "GET" and parsed.path in {"/", "/login"}:
            body = document("Controlled fixture sign in", """<h1>Controlled fixture sign in</h1>
              <form method="post" action="/session">
                <label>Username <input name="username" autocomplete="username" required></label>
                <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
                <button type="submit">Sign in</button>
              </form>""")
        elif request.method == "POST" and parsed.path == "/session":
            fields = await self.form_fields(request)
            valid = (fields.get("username") == [USERNAME]
                     and len(fields.get("password", [])) == 1
                     and secrets.compare_digest(fields["password"][0], self.password))
            require(valid, "fixture_login_credentials_did_not_match")
            self.login_count += 1
            self.login_redirects += 1
            status = 303
            headers.update({"Location": ORIGIN + "/workspace",
                            "Set-Cookie": "fixture-session=authenticated; Path=/; Secure; HttpOnly; SameSite=Strict"})
        elif request.method == "GET" and parsed.path == "/workspace":
            require(self.authenticated(request), "fixture_workspace_not_authenticated")
            self.workspace_reads += 1
            if self.login_redirect_follows < self.login_redirects:
                self.login_redirect_follows += 1
            body = document("Authenticated controlled workspace", """<h1>Authenticated controlled workspace</h1>
              <p id="authenticated">Signed in successfully.</p>
              <form method="post" action="/repositories">
                <label>Repository name <input name="repository_name" required></label>
                <label><input type="checkbox" name="private">Private repository</label>
                <button type="submit">Create repository</button>
              </form>""")
        elif request.method == "POST" and parsed.path == "/repositories":
            require(self.authenticated(request), "fixture_repository_request_not_authenticated")
            fields = await self.form_fields(request)
            require(fields.get("repository_name") == [REPOSITORY], "fixture_repository_name_mismatch")
            require(fields.get("private") == ["on"], "fixture_repository_visibility_mismatch")
            require(REPOSITORY not in self.repositories, "fixture_repository_submission_replayed")
            self.repositories[REPOSITORY] = {"private": True}
            self.creation_count += 1
            self.repository_redirects += 1
            status = 303
            headers["Location"] = ORIGIN + "/repositories/" + REPOSITORY
        elif request.method == "GET" and parsed.path == "/repositories/" + REPOSITORY:
            require(self.authenticated(request) and REPOSITORY in self.repositories,
                    "fixture_created_repository_not_found")
            if self.repository_redirect_follows < self.repository_redirects:
                self.repository_redirect_follows += 1
            body = document("Created controlled private repository",
                '<h1 id="repository-name">' + REPOSITORY + '</h1><p id="repository-visibility">Visibility: Private</p>'
                '<p id="created">Repository created successfully.</p>')
        elif parsed.path == "/favicon.ico":
            status = 204
        else:
            raise FixtureFailure("unexpected_fixture_site_request")
        return Response(content=body, status_code=status, headers=headers)

    @staticmethod
    async def form_fields(request):
        body = bytearray()
        try:
            async for chunk in request.stream():
                body.extend(chunk)
                require(len(body) <= 8192, "fixture_form_body_too_large")
            return parse_qs(body.decode("utf-8"), strict_parsing=True)
        finally:
            body.clear()

    def app(self):
        from fastapi import FastAPI, Request
        from fastapi.responses import PlainTextResponse

        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

        async def endpoint(request):
            try:
                return await self.respond(request)
            except Exception as error:
                self.failure = (str(error) if isinstance(error, FixtureFailure)
                                else "fixture_site_runtime_failure")
                return PlainTextResponse("Controlled fixture request rejected.", status_code=500)

        # Keep Request concrete despite this file's deferred annotations. No
        # framework validation error may echo a request's secret-bearing input.
        endpoint.__annotations__["request"] = Request
        app.add_api_route("/{path:path}", endpoint, methods=["GET", "POST", "HEAD", "OPTIONS"])
        return app

    def install(self, computer):
        manager = computer.manager
        actual_guard = manager.guard
        actual_launch = manager.launch
        actual_proxy = computer.BrowserProxy
        fixture = self

        async def connect_fixture(host, port):
            # The production proxy performs its normal host/origin checks before
            # this constructor-only hook. No DNS/env override exists in the app.
            if host != "alambda-forge.onrender.com" or port != 443:
                raise OSError("Controlled fixture upstream blocked")
            fixture.proxy_tunnels += 1
            return await asyncio.open_connection("127.0.0.1", FIXTURE_TLS_PORT)

        computer.BrowserProxy = lambda: actual_proxy(connect_factory=connect_fixture)

        async def fixture_launch():
            await actual_launch()
            actual_new_context = manager.browser.new_context

            async def fixture_context(*args, **kwargs):
                # Only this isolated test context trusts the ephemeral fixture
                # certificate. Production browser TLS verification is unchanged.
                kwargs["ignore_https_errors"] = True
                context = await actual_new_context(*args, **kwargs)
                context.on("request", fixture.observe_browser_request)
                return context

            manager.browser.new_context = fixture_context

        manager.launch = fixture_launch

        async def guarded_route(route, browser_context=None):
            class Routed:
                request = route.request

                async def continue_(self, **kwargs):
                    await route.continue_(**kwargs)

                async def abort(self, error_code="failed"):
                    fixture.policy_blocks += 1
                    await route.abort(error_code)

            # Preserve the production guard and its actual network continuation.
            # Redirects are constrained independently by the production proxy.
            await actual_guard(Routed(), browser_context)

        manager.guard = guarded_route


class ScriptedModel:
    """A deterministic tool planner consuming actual returned observation refs."""
    def __init__(self, audit):
        self.audit = audit
        self.calls = []
        self.failure = None

    def target(self, observation, name, *, checked=None):
        matches = [element for element in observation.get("elements", [])
                   if element.get("name") == name and not element.get("disabled")]
        require(len(matches) == 1, "fixture_expected_element_not_observed")
        if checked is not None:
            require(matches[0].get("checked") is checked, "fixture_checkbox_state_mismatch")
        return {"observation_id": observation["observation_id"], "ref": matches[0]["ref"]}

    async def ainvoke(self, messages):
        from langchain_core.messages import AIMessage
        try:
            for message in messages:
                self.audit.inspect(message.model_dump_json(), "credential_in_model_messages")
                self.audit.model_messages += 1
            start = max(index for index, message in enumerate(messages) if message.type == "human")
            command = messages[start].content
            results = [json.loads(message.content) for message in messages[start + 1:] if message.type == "tool"]
            require(not any("error" in result for result in results), "fixture_tool_reported_failure")
            step = len(results)
            name, args = None, {}
            if command == "FIXTURE LOGIN":
                if step == 0:
                    name, args = "open_browser", {"url": ORIGIN + "/login"}
                elif step == 1:
                    name = "vault_list_accounts"
                elif step in {2, 4, 6}:
                    name = "browser_observe"
                elif step == 3:
                    accounts = [item for item in results[1]["accounts"] if item["label"] == LABEL]
                    require(len(accounts) == 1, "fixture_saved_account_not_found_by_tool")
                    name, args = "vault_use_account", {"account_id": accounts[0]["id"],
                        "observation_id": results[2]["observation_id"]}
                elif step == 5:
                    require(results[3].get("filled") is True, "fixture_credentials_not_filled")
                    name, args = "browser_click", self.target(results[4], "Sign in")
                elif step == 7:
                    require("Signed in successfully." in results[6].get("text", "")
                            and results[6].get("url") == ORIGIN + "/workspace", "fixture_login_not_observed")
                    return AIMessage(content="The controlled fixture login is authenticated.")
            elif command == "FIXTURE TAKEOVER":
                if step == 0:
                    name, args = "navigate_browser", {"url": ORIGIN + "/workspace"}
                elif step == 1:
                    name = "browser_observe"
                elif step == 2:
                    require("Signed in successfully." in results[1].get("text", ""), "fixture_resumed_page_not_observed")
                    return AIMessage(content="The controlled navigation resumed after handback.")
            elif command == "FIXTURE PRIVATE REPOSITORY":
                if step in {0, 2, 4, 6}:
                    name = "browser_observe"
                elif step == 1:
                    name, args = "browser_fill", {**self.target(results[0], "Repository name"), "text": REPOSITORY}
                elif step == 3:
                    name, args = "browser_click", self.target(results[2], "Private repository", checked=False)
                elif step == 5:
                    self.target(results[4], "Private repository", checked=True)
                    name, args = "browser_click", self.target(results[4], "Create repository")
                elif step == 7:
                    require(results[6].get("url") == ORIGIN + "/repositories/" + REPOSITORY
                            and "Visibility: Private" in results[6].get("text", "")
                            and "Repository created successfully." in results[6].get("text", ""),
                            "fixture_private_repository_not_observed")
                    return AIMessage(content="The controlled fixture repository exists and is private.")
            require(name is not None, "unexpected_fixture_model_step")
            self.calls.append(name)
            return AIMessage(content="", tool_calls=[{"name": name, "args": args,
                "id": str(uuid.uuid4()), "type": "tool_call"}])
        except FixtureFailure as error:
            self.failure = str(error)
            raise


async def run_fixture(phase, queue, directory, password, audit):
    global STAGE
    import httpx
    import uvicorn
    from temporalio.client import Client, WorkflowExecutionStatus
    from reign import computer, core

    site = ControlledWebsite(password)
    site.install(computer)
    model = ScriptedModel(audit)
    tls_directory = tempfile.TemporaryDirectory(prefix="reign-controlled-fixture-tls-")
    private_key_path, certificate_path = fixture_certificate(tls_directory.name)
    servers = [uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
        access_log=False, log_config=None, log_level="critical"))
        for app, port in ((computer.app, 18002), (core.app, 18001))]
    servers.insert(0, uvicorn.Server(uvicorn.Config(site.app(), host="127.0.0.1", port=FIXTURE_TLS_PORT,
        ssl_keyfile=private_key_path, ssl_certfile=certificate_path,
        access_log=False, log_config=None, log_level="critical")))
    tasks = [asyncio.create_task(server.serve()) for server in servers]
    started = time.monotonic()
    headers = {"Authorization": "Bearer " + os.environ["CORE_TOKEN"]}
    temporal = None
    try:
        STAGE = "services_ready"
        async with asyncio.timeout(45):
            while not all(server.started for server in servers):
                require(not any(task.done() for task in tasks), "fixture_service_start_failed")
                await asyncio.sleep(0.1)
        # The runtime and its MCP adapter were constructed by the real Core
        # lifespan. Substitute only the model factory, before the first chat.
        core.app.state.agent.model_factory = lambda: model
        require(core.app.state.agent.capabilities()["tool_protocol"] == "mcp_stdio", "fixture_mcp_not_active")
        temporal = await Client.connect(os.environ["TEMPORAL_ADDRESS"], namespace="default")
        query = "TaskQueue = '" + queue + "'"
        before = {(item.id, item.run_id) async for item in temporal.list_workflows(query, limit=100)}

        async with httpx.AsyncClient(base_url=CORE_URL, headers=headers, timeout=20,
                                     trust_env=False, follow_redirects=False) as client:
            async def api(method, path, data=None, expected=200):
                result = await client.request(method, path, json=data)
                audit.inspect(result.content, "credential_in_public_core_response")
                audit.api_responses += 1
                require(result.status_code == expected, "fixture_api_status_mismatch")
                return result.json()

            async with asyncio.timeout(45):
                while True:
                    result = await client.get("/api/readyz")
                    if result.status_code == 200:
                        break
                    await asyncio.sleep(0.25)
            require(computer.manager.worker_ready, "fixture_temporal_worker_not_ready")

            STAGE = "durable_account"
            status = await api("GET", "/api/accounts")
            require(status["vault"]["ready"] and status["vault"]["durable"], "fixture_durable_vault_not_ready")
            marker = directory / "fixture-acceptance-state.json"
            if phase == "initial":
                require(not status["accounts"] and not marker.exists(), "fixture_initial_volume_not_empty")
                saved = await api("POST", "/api/accounts", {"label": LABEL, "origin": ORIGIN,
                    "username": USERNAME, "password": password}, expected=201)
                account_id = saved["account"]["id"]
            else:
                require(marker.is_file(), "fixture_restart_marker_missing")
                previous = json.loads(marker.read_text())
                require(previous.get("initial_completed") is True, "fixture_initial_phase_not_completed")
                account_id = previous["account_id"]
                require(len(status["accounts"]) == 1 and status["accounts"][0]["id"] == account_id
                        and status["accounts"][0]["label"] == LABEL, "fixture_account_did_not_survive_restart")
            for suffix in ("", "-journal", "-wal", "-shm"):
                stored = directory / ("vault.sqlite3" + suffix)
                if stored.is_file():
                    audit.inspect(stored.read_bytes(), "credential_in_vault_file")

            async def finish_run(value, expected="completed"):
                tid = value["thread_id"]
                async with asyncio.timeout(70):
                    while value["status"] == "running":
                        await asyncio.sleep(0.1)
                        value = await api("GET", "/api/chat/" + tid)
                require(site.failure is None, site.failure or "fixture_route_failed")
                require(value["status"] == expected, model.failure or "fixture_graph_not_completed")
                require(not any(event.get("ok") is False for event in value["events"]), "fixture_graph_tool_failed")
                return value

            STAGE = "login_through_graph_and_vault"
            login = await finish_run(await api("POST", "/api/chat", {"message": "FIXTURE LOGIN"}))
            sid, tid = login["session_id"], login["thread_id"]
            require(site.login_count == 1, "fixture_login_submission_count_mismatch")
            require(site.login_redirects == 1 and site.login_redirect_follows == 1
                    and site.browser_login_redirect_follows == 1,
                    "fixture_native_login_redirect_not_followed")
            page = computer.manager.sessions[sid]["page"]
            require(await page.locator("#authenticated").inner_text() == "Signed in successfully.",
                    "fixture_authenticated_dom_missing")

            STAGE = "takeover_pause_and_handback"
            state = await api("GET", "/api/sessions/" + sid)
            human = await api("POST", "/api/sessions/" + sid + "/control", {"mode": "human", "epoch": state["epoch"]})
            prior_reads = site.workspace_reads
            paused = await finish_run(await api("POST", "/api/chat", {"message": "FIXTURE TAKEOVER",
                "thread_id": tid, "session_id": sid}), expected="paused")
            require(site.workspace_reads == prior_reads and page.url == ORIGIN + "/workspace", "fixture_effect_during_human_control")
            await api("POST", "/api/chat/" + tid + "/resume", expected=409)
            returned = await api("POST", "/api/sessions/" + sid + "/control", {"mode": "agent", "epoch": human["epoch"]})
            require(returned["epoch"] == state["epoch"] + 2, "fixture_takeover_epoch_mismatch")
            await finish_run(await api("POST", "/api/chat/" + tid + "/resume"))
            require(site.workspace_reads == prior_reads + 1, "fixture_resumed_navigation_replayed")

            STAGE = "private_repository_through_graph"
            repository = await finish_run(await api("POST", "/api/chat", {"message": "FIXTURE PRIVATE REPOSITORY",
                "thread_id": tid, "session_id": sid}))
            require(await page.locator("#repository-name").inner_text() == REPOSITORY,
                    "fixture_repository_dom_missing")
            require(await page.locator("#repository-visibility").inner_text() == "Visibility: Private",
                    "fixture_private_repository_dom_missing")
            require(site.creation_count == 1 and site.repositories[REPOSITORY]["private"], "fixture_repository_creation_count_mismatch")
            require(site.repository_redirects == 1 and site.repository_redirect_follows == 1
                    and site.browser_repository_redirect_follows == 1,
                    "fixture_native_repository_redirect_not_followed")
            require(site.policy_blocks > 0, "fixture_production_guard_not_exercised")
            require(site.proxy_tunnels > 0, "fixture_production_proxy_not_exercised")
            require("vault_use_account" in model.calls and "browser_fill" in model.calls,
                    "fixture_expected_tools_not_used")

            STAGE = "credential_leak_audit"
            audit.inspect(repository, "credential_in_graph_snapshot")
            thread = core.app.state.agent.get(tid)
            checkpoint = await thread.graph.aget_state(thread.config)
            for message in checkpoint.values["messages"]:
                audit.inspect(message.model_dump_json(), "credential_in_graph_checkpoint")
            executions = []
            async with asyncio.timeout(20):
                while True:
                    executions = [item async for item in temporal.list_workflows(query, limit=100)
                                  if (item.id, item.run_id) not in before]
                    if len(executions) >= 7 and all(item.status == WorkflowExecutionStatus.COMPLETED for item in executions):
                        break
                    await asyncio.sleep(0.25)
            require(len(executions) == 7, "fixture_workflow_count_mismatch")
            types = {}
            for execution in executions:
                types[execution.workflow_type] = types.get(execution.workflow_type, 0) + 1
                history = await temporal.get_workflow_handle(execution.id, run_id=execution.run_id).fetch_history()
                for event in history.events:
                    audit.inspect(event.SerializeToString(), "credential_in_temporal_history")
                    audit.history_events += 1
            require(types == {"OpenBrowser": 1, "NavigateBrowser": 1, "FillAccount": 1, "BrowserAction": 4},
                    "fixture_workflow_types_mismatch")
            require(audit.history_events > 0, "fixture_temporal_histories_not_scanned")

            if phase == "initial":
                with open(marker, "x") as file:
                    os.chmod(marker, 0o600)
                    json.dump({"initial_completed": True, "account_id": account_id}, file)
                    file.flush()
                    os.fsync(file.fileno())
            await api("DELETE", "/api/sessions/" + sid)
            await api("DELETE", "/api/chat/" + tid)

        return {
            "passed": True, "phase": phase, "evidence_kind": "controlled_workspace_acceptance",
            "model": "scripted_model", "website": "controlled_site",
            "live_provider_tested": False, "external_accounts_created": 0, "external_repositories_created": 0,
            "source_sha": os.getenv("SOURCE_SHA", "local")[:80],
            "real_components": ["core_http", "computer_http", "mcp_stdio", "langgraph", "temporal", "playwright", "browser_proxy", "fixture_https", "encrypted_sqlite_vault"],
            "checks": {"durable_vault": True, "login_authenticated_dom": True,
                       "password_absent_from_vault_files": True,
                       "native_login_redirect_followed": True, "native_repository_redirect_followed": True,
                       "account_survived_process_restart": phase == "restart",
                       "credential_save_requests": 1 if phase == "initial" else 0,
                       "private_repository_dom": True, "repository_creations": site.creation_count,
                       "human_takeover_paused": paused["status"] == "paused", "human_resume_rejected": True,
                       "handback_resumed_once": True, "production_request_guard": True, "production_proxy": True,
                       "password_absent_from_model_and_tool_messages": True,
                       "password_absent_from_graph_checkpoints": True, "password_absent_from_temporal_histories": True},
            "workflow_count": len(executions), "workflow_types": types,
            "history_events_scanned": audit.history_events, "model_messages_scanned": audit.model_messages,
            "public_responses_scanned": audit.api_responses, "tool_calls": model.calls,
            "site_requests": SITE_REQUESTS,
            "proxy_tunnels": site.proxy_tunnels,
            "duration_seconds": round(time.monotonic() - started, 2),
        }
    finally:
        # A phase exits only after both actual lifespans have shut down. The next
        # container therefore cannot overlap this fixture's Temporal worker.
        for server in reversed(servers):
            server.should_exit = True
        try:
            async with asyncio.timeout(20):
                await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            tls_directory.cleanup()


def main():
    # No raw exception or application logging is an acceptance artifact. Receipt
    # codes and stage names below are fixed; screenshots and tool bodies are not
    # written. The fixture subprocess already keeps MCP stderr private.
    logging.disable(logging.CRITICAL)
    phase = os.getenv("SMOKE_PHASE", "initial")
    try:
        phase, queue, directory, password, forbidden = configure()

        async def bounded():
            async with asyncio.timeout(180):
                return await run_fixture(phase, queue, directory, password, Audit(forbidden))

        receipt = asyncio.run(bounded())
        status = 0
    except Exception as error:
        receipt = {"passed": False, "phase": phase if phase in {"initial", "restart"} else "invalid",
                   "evidence_kind": "controlled_workspace_acceptance", "model": "scripted_model",
                   "website": "controlled_site", "stage": STAGE,
                   "code": str(error) if isinstance(error, FixtureFailure) else "fixture_runtime_failure",
                   "exception_type": type(error).__name__, "site_requests": SITE_REQUESTS}
        status = 1
    output = Path(os.getenv("REIGN_FIXTURE_OUTPUT", "/evidence"))
    output.mkdir(parents=True, exist_ok=True)
    target = output / ("workspace-" + receipt["phase"] + ".json")
    target.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, sort_keys=True), flush=True)
    raise SystemExit(status)


if __name__ == "__main__":
    main()
