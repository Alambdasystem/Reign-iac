"""Accounts DOM acceptance against the assembled, disposable AX/Core vault.

Account create/list/delete and sign-in expiry use real service endpoints. The
separate microphone case explicitly simulates media, provider and graph replies;
it makes no claim about WebRTC, physical audio, or an external website login.
Only disposable CI values are entered. Passwords never enter returned evidence.
"""
import asyncio
import secrets
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from playwright.async_api import expect


TARGET_ORIGIN = "https://alambda-forge.onrender.com"
ACCOUNT_INPUTS = ("accountLabel", "accountOrigin", "accountUsername", "accountPassword")
METADATA_FIELDS = {"id", "label", "origin", "username", "created_at", "updated_at"}


def _matches(response, origin, method, path):
    return (response.url == origin + path and response.request.method == method)


async def _open_accounts(page):
    await page.locator('.tabs [data-tab="Work"]').click()
    await expect(page.locator("#accountsOpen")).to_be_enabled()
    await page.locator("#accountsOpen").click()
    await expect(page.locator("#accountsDialog")).to_be_visible()


async def _ready(page):
    await expect(page.locator("#accountSave")).to_be_enabled()
    for identifier in ACCOUNT_INPUTS:
        await expect(page.locator("#" + identifier)).to_be_enabled()


async def _fill(page, label, username, password):
    await page.locator("#accountLabel").fill(label)
    await page.locator("#accountOrigin").fill(TARGET_ORIGIN)
    await page.locator("#accountUsername").fill(username)
    await page.locator("#accountPassword").fill(password)


async def _save(page, origin, *, status=201):
    async with page.expect_response(lambda response: _matches(response, origin, "POST", "/api/accounts")) as pending:
        await page.locator("#accountSave").click()
    response = await pending.value
    assert response.status == status, "Unexpected secure account save status"
    assert response.headers.get("cache-control") == "no-store", "Account save must not be cached"
    result = await response.json()
    await expect(page.locator("#accountPassword")).to_have_value("")
    if status == 201:
        assert set(result) == {"account"}, "Account save exposed an extra field"
        account = result["account"]
        assert set(account) == METADATA_FIELDS, "Account save must return metadata only"
        assert str(UUID(account["id"])) == account["id"], "Account identifier is invalid"
        await expect(page.locator('[data-account-remove="' + account["id"] + '"]')).to_be_visible()
        return account
    assert set(result) == {"code", "detail"}, "Account errors must use the safe public shape"
    return result


async def _list(page, origin):
    async with page.expect_response(lambda response: _matches(response, origin, "GET", "/api/accounts")) as pending:
        await page.locator("#accountsRefresh").click()
    response = await pending.value
    assert response.status == 200, "Real account list did not succeed"
    assert response.headers.get("cache-control") == "no-store", "Account metadata must not be cached"
    result = await response.json()
    assert result["vault"]["ready"] is True and result["vault"]["durable"] is True, "Smoke requires the real durable vault"
    assert all(set(account) == METADATA_FIELDS for account in result["accounts"]), "Account list exposed an extra field"
    await _ready(page)
    return result["accounts"]


async def _remove(page, origin, account_id):
    await page.locator('[data-account-remove="' + account_id + '"]').click()
    await expect(page.locator("#accountRemoval")).to_be_visible()
    async with page.expect_response(lambda response: _matches(response, origin, "DELETE", "/api/accounts/" + account_id)) as pending:
        await page.locator("#accountRemoveConfirm").click()
    response = await pending.value
    assert response.status == 200, "Confirmed saved-login removal failed"
    assert await response.json() == {"status": "deleted", "id": account_id}, "Removal did not identify the selected account"
    await expect(page.locator('[data-account-remove="' + account_id + '"]')).to_have_count(0)
    await expect(page.locator("#accountRemoval")).to_be_hidden()
    await _ready(page)


async def _private_surfaces(page, markers):
    # The visible Accounts metadata list is intentionally excluded. Conversation,
    # captions, URL/history state, and both browser stores must remain separate.
    leaked = await page.evaluate("""markers => {
      const storage = store => Object.keys(store).map(key => [key,store.getItem(key)]);
      const surfaces = [JSON.stringify(storage(localStorage)),JSON.stringify(storage(sessionStorage)),
        JSON.stringify(history.state),location.href,document.querySelector('#feed').textContent,
        document.querySelector('#voiceCaption').textContent,
        JSON.stringify(window.__reignVoiceSimulation?.sent || [])];
      return surfaces.some(value => markers.some(marker => String(value).includes(marker)));
    }""", markers)
    assert not leaked, "Disposable account data escaped the secure Accounts surface"


async def _page(browser, storage_state, origin, *, voice_bootstrap=None):
    context = await browser.new_context(ignore_https_errors=True, storage_state=storage_state,
                                        viewport={"width": 390, "height": 844})
    if voice_bootstrap:
        await context.add_init_script(voice_bootstrap)
    page = await context.new_page()
    page.set_default_timeout(20000)
    failures = []
    page.on("pageerror", lambda error: failures.append(type(error).__name__))
    return context, page, failures


async def _voice_interlock(browser, storage_state, origin, voice_bootstrap, markers):
    assert voice_bootstrap, "Pass the existing explicitly synthetic voice bootstrap"
    context, page, failures = await _page(browser, storage_state, origin, voice_bootstrap=voice_bootstrap)
    thread = str(uuid4())
    answer = "Synthetic Accounts microphone check."
    requested_label = "CI secure entry request"
    graph_started, release_graph = asyncio.Event(), asyncio.Event()
    account_writes = []
    page.on("request", lambda request: account_writes.append(request.method)
            if request.method != "GET" and urlsplit(request.url).path.startswith("/api/accounts") else None)
    snapshot = {"thread_id": thread, "run_id": "accounts-ui-synthetic-run", "status": "completed",
                "session_id": None, "events": [{"sequence": 1, "kind": "account_entry_requested",
                                                  "label": requested_label, "origin": TARGET_ORIGIN},
                                                 {"sequence": 2, "kind": "completed"}],
                "answer": answer, "error": None, "usage": {"input_tokens": 0, "output_tokens": 0},
                "turns": 1, "max_turns": 24, "continuable": True}
    async def synthetic(route):
        path = urlsplit(route.request.url).path
        if path == "/api/voice":
            result = {"configured": True, "model": "synthetic-accounts-realtime", "voice": "synthetic",
                      "max_duration_seconds": 900}
        elif path == "/api/voice/offer":
            result = {"sdp": "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n", "type": "answer",
                      "model": "synthetic-accounts-realtime", "max_duration_seconds": 900}
        elif path == "/api/agent":
            result = {"configured": True, "model": "synthetic-accounts-graph", "max_turns": 24}
        elif path == "/api/chat" or path == "/api/chat/" + thread:
            if route.request.post_data:
                assert not any(marker in route.request.post_data for marker in markers), "Account data entered the synthetic graph request"
            if path == "/api/chat" and route.request.method == "POST":
                graph_started.set()
                await release_graph.wait()
            result = snapshot
        else:
            raise AssertionError("Unexpected synthetic Accounts voice route")
        await route.fulfill(status=200, json=result)
    try:
        for pattern in ("/api/voice**", "/api/agent", "/api/chat**"):
            await context.route(origin + pattern, synthetic)
        await page.goto(origin)
        await expect(page.locator("#voiceStart")).to_be_enabled()
        await page.locator("#voiceStart").click()
        await page.wait_for_function("window.__reignVoiceSimulation.tracks.length > 0 && window.__reignVoiceSimulation.tracks.every(track => track.enabled)")
        await expect(page.locator("#voiceEnd")).to_be_visible()
        await asyncio.wait_for(graph_started.wait(), 20)
        release_graph.set()
        await expect(page.locator("#accountsDialog")).to_be_visible()
        await _ready(page)
        await expect(page.locator("#accountLabel")).to_have_value(requested_label)
        await expect(page.locator("#accountOrigin")).to_have_value(TARGET_ORIGIN)
        await expect(page.locator("#accountUsername")).to_have_value("")
        await expect(page.locator("#accountPassword")).to_have_value("")
        await expect(page.locator("#accountsVoiceNotice")).to_contain_text("microphone is muted")
        await expect(page.locator("#mute")).to_have_attribute("aria-pressed", "true")
        await expect(page.locator("#mute")).to_be_disabled()
        assert await page.evaluate("window.__reignVoiceSimulation.tracks.every(track => !track.enabled)"), "Secure entry did not mute every synthetic track"
        await page.locator("#accountPassword").fill(markers[-1])
        await page.locator("#accountsClose").click()
        await expect(page.locator("#accountPassword")).to_have_value("")
        await expect(page.locator("#mute")).to_have_attribute("aria-pressed", "true")
        await expect(page.locator("#mute")).to_be_enabled()
        assert await page.evaluate("window.__reignVoiceSimulation.tracks.every(track => !track.enabled)"), "Closing Accounts automatically unmuted the microphone"
        await _private_surfaces(page, markers)
        await page.locator("#voiceEnd").click()
        await expect(page.locator("#voiceEnd")).to_be_hidden()
        assert not account_writes, "Secure-entry event automatically changed a saved account"
        assert not failures, "Assembled Accounts/voice UI raised a page error"
        return {"status": "passed", "mode": "synthetic_provider_graph_and_microphone",
                "microphone_muted_for_entry": "passed", "password_cleared": "passed",
                "secure_entry_event_presets_only_metadata": "passed",
                "no_automatic_unmute": "passed", "physical_device": "not_run",
                "webrtc_provider": "not_run", "account_api": "real_ax_core_vault"}
    finally:
        release_graph.set()
        await context.close()


async def accounts_ui_smoke(browser, storage_state, *, origin="https://ax:10000", phase="initial",
                            previous=None, voice_bootstrap=None):
    """Return sanitized acceptance evidence; never return a password or cookie.

    Call initial before leaving the restart browser open. Restart Core with the
    same disposable encrypted vault volume, then pass initial evidence as
    ``previous`` to phase='restart'. No external website account is created.
    """
    assert phase in {"initial", "restart"}, "Unknown Accounts smoke phase"
    context, page, failures = await _page(browser, storage_state, origin)
    pending_gate = asyncio.Event()
    release_gate = asyncio.Event()
    mutations, markers = [], []
    escaped_request = False
    def inspect_request(request):
        nonlocal escaped_request
        path = urlsplit(request.url).path
        if request.method in {"POST", "DELETE"} and path.startswith("/api/accounts"):
            mutations.append((request.method, path))
        # The one authorized credential form is the only request body that may
        # contain these disposable fields. Do not retain its plaintext payload.
        surfaces = [request.url]
        if not (request.url == origin + "/api/accounts" and request.method == "POST"):
            surfaces.append(request.post_data or "")
        if any(marker in surface for marker in markers for surface in surfaces):
            escaped_request = True
    page.on("request", inspect_request)
    try:
        await page.goto(origin)
        await expect(page.locator("#console")).to_be_visible()
        assert await page.locator(".tabs button").all_text_contents() == ["Chat", "Work"], "Only Chat and Work belong in the top navigation"
        assert await page.locator('[data-tab="Agents"],[data-tab="Tools"]').count() == 0, "Removed navigation is still present"
        assert not any("MCP" in text or "Agents" in text for text in await page.locator(".nav button").all_text_contents()), "Removed bottom navigation is still present"
        if phase == "restart":
            assert previous and previous.get("fixture_account"), "Initial Accounts receipt is required"
            expected = previous["fixture_account"]
            await _open_accounts(page)
            await _ready(page)
            accounts = await _list(page, origin)
            restored = next((account for account in accounts if account["id"] == expected["id"]), None)
            assert restored == expected, "Encrypted account metadata changed or disappeared across Core restart"
            row = page.locator(".account-row").filter(has=page.locator('[data-account-remove="' + expected["id"] + '"]'))
            await expect(row).to_contain_text(expected["label"])
            await expect(row).to_contain_text(expected["username"])
            await _private_surfaces(page, [expected["label"], expected["username"]])
            await _remove(page, origin, expected["id"])
            assert all(account["id"] != expected["id"] for account in await _list(page, origin)), "Disposable account cleanup was not verified"
            assert not failures, "Assembled Accounts UI raised a page error"
            return {**previous, "status": "passed", "metadata_after_core_restart": "passed",
                    "disposable_account_cleanup": "passed"}

        async def delay_real_list(route):
            if route.request.method == "GET":
                pending_gate.set()
                await release_gate.wait()
            await route.continue_()
        await context.route(origin + "/api/accounts", delay_real_list)
        await _open_accounts(page)
        await asyncio.wait_for(pending_gate.wait(), 20)
        await expect(page.locator("#accountSave")).to_be_disabled()
        for identifier in ACCOUNT_INPUTS:
            await expect(page.locator("#" + identifier)).to_be_disabled()
        release_gate.set()
        await _ready(page)
        await context.unroute(origin + "/api/accounts", delay_real_list)
        await _list(page, origin)
        assert await page.evaluate("document.querySelector('#accountsDialog').scrollWidth <= document.querySelector('#accountsDialog').clientWidth"), "Accounts modal overflows a mobile viewport"

        suffix = uuid4().hex[:12]
        label, username = "CI saved login " + suffix, "ci-ui-user-" + suffix
        password, replacement = "  ci-" + secrets.token_urlsafe(28) + "  ", "ci-replacement-" + secrets.token_urlsafe(24)
        markers = [label, username, password, password.strip(), replacement]
        await _fill(page, label, username, password)
        account = await _save(page, origin)
        assert account["label"] == label and account["username"] == username and account["origin"] == TARGET_ORIGIN, "Saved metadata did not match the form"
        await _private_surfaces(page, markers)

        # An explicit repeat is idempotent; changed credentials never overwrite
        # the original through the Add form. The password remains server-private.
        await _fill(page, label, username, password)
        assert await _save(page, origin) == account, "Identical repeat created a second saved login"
        await _fill(page, label, "changed-" + username, replacement)
        duplicate = await _save(page, origin, status=409)
        assert duplicate["code"] == "account_exists", "Changed duplicate must not replace a login"
        await expect(page.locator("#accountsError")).to_contain_text("Choose a different label")
        current = await _list(page, origin)
        assert [item for item in current if item["label"] == label] == [account], "Duplicate Add changed the stored metadata"
        await _private_surfaces(page, markers)

        # This second saved login tests cancel/confirm without consuming the
        # first fixture, whose encrypted metadata must survive the restart.
        await _fill(page, label + " removal", username + "-removal", replacement)
        removable = await _save(page, origin)
        before = sum(method == "DELETE" for method, path in mutations)
        await page.locator('[data-account-remove="' + removable["id"] + '"]').click()
        await expect(page.locator("#accountRemoval")).to_be_visible()
        assert sum(method == "DELETE" for method, path in mutations) == before, "Remove dispatched before confirmation"
        await page.locator("#accountRemoveCancel").click()
        await expect(page.locator("#accountRemoval")).to_be_hidden()
        await expect(page.locator('[data-account-remove="' + removable["id"] + '"]')).to_be_visible()
        assert sum(method == "DELETE" for method, path in mutations) == before, "Cancel removed a saved login"
        await _remove(page, origin, removable["id"])

        await page.locator("#accountPassword").fill(password)
        await page.locator("#accountsClose").click()
        await expect(page.locator("#accountPassword")).to_have_value("")
        await _open_accounts(page)
        await _ready(page)
        await expect(page.locator("#accountPassword")).to_have_value("")
        await page.locator("#accountPassword").fill(replacement)
        # Expire only this isolated context's real auth cookie. The subsequent
        # real API 401 must close the modal and erase the pending password.
        await context.clear_cookies()
        await page.locator("#accountsRefresh").click()
        await expect(page.locator("#loginPanel")).to_be_visible()
        await expect(page.locator("#accountsDialog")).to_be_hidden()
        await expect(page.locator("#accountPassword")).to_have_value("")
        await _private_surfaces(page, markers)
        assert not escaped_request, "Account data entered an unrelated request or URL"
        assert not failures, "Assembled Accounts UI raised a page error"
    finally:
        release_gate.set()
        await context.close()

    # Fault injection aborts the transport; it never substitutes vault metadata.
    # Refresh then reaches the actual durable vault again.
    context, page, failures = await _page(browser, storage_state, origin)
    async def unavailable(route):
        await route.abort("failed")
    try:
        await page.goto(origin)
        await context.route(origin + "/api/accounts", unavailable)
        await _open_accounts(page)
        await expect(page.locator("#accountsError")).to_be_visible()
        await expect(page.locator("#accountSave")).to_be_disabled()
        for identifier in ACCOUNT_INPUTS:
            await expect(page.locator("#" + identifier)).to_be_disabled()
        await context.unroute(origin + "/api/accounts", unavailable)
        await _list(page, origin)
        await _private_surfaces(page, markers)
        assert not failures, "Accounts transport recovery raised a page error"
    finally:
        await context.close()
    voice = await _voice_interlock(browser, storage_state, origin, voice_bootstrap, markers)
    return {"status": "awaiting_restart_check", "mode": "real_ax_core_durable_vault_dom",
            "navigation_removed": "passed", "readiness_pending_and_transport_failure": "passed",
            "secure_save_list": "passed", "duplicate_preserves_original": "passed",
            "remove_requires_confirmation": "passed", "password_clear_close_and_real_401": "passed",
            "no_account_data_in_storage_history_chat": "passed", "mobile_dialog": "passed",
            "fixture_account": account, "voice_interlock": voice,
            "external_website_account": "not_created"}
