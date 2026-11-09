import asyncio
import json
import os
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from reign.agent import AgentRuntime, MAX_STEPS, MAX_TURNS, model_client
from reign.session_errors import BROWSER_OCCUPIED_CODE, BROWSER_OCCUPIED_MESSAGE, BrowserOccupied
from reign.tool_errors import BrowserActionError

SID = str(uuid.uuid4())


def call(name, args=None):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {}, "id": str(uuid.uuid4()), "type": "tool_call"}])


class Model:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.seen = []

    async def ainvoke(self, messages):
        self.seen.append(messages)
        return self.answers.pop(0)


class Adapter:
    def __init__(self):
        self.mode, self.epoch = "agent", 0
        self.opens, self.navigations, self.statuses = 0, 0, 0
        self.reject = False

    async def status(self):
        self.statuses += 1
        return {"status": "ready", "components": {"core": "ready"}}

    async def session(self, sid):
        return {"id": sid, "mode": self.mode, "epoch": self.epoch}

    async def open(self, url):
        self.opens += 1
        return {"id": SID, "url": url, "mode": "agent", "epoch": 0}

    async def navigate(self, sid, url, epoch):
        if self.reject:
            raise HTTPException(409, "sensitive internal detail")
        self.navigations += 1
        assert epoch == self.epoch
        return {"id": sid, "url": url, "mode": self.mode, "epoch": epoch}


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-never-live"})
        self.env.start()
        self.adapter = Adapter()
        self.runtime = None

    async def asyncTearDown(self):
        if self.runtime:
            await self.runtime.shutdown()
        self.env.stop()

    def setup_run(self, *answers):
        self.model = Model(*answers)
        self.runtime = AgentRuntime(lambda: self.model, self.adapter)
        return self.runtime

    async def finish(self, result):
        thread = self.runtime.get(result["thread_id"])
        await thread.task
        return thread, thread.snapshot()

    async def test_real_graph_uses_tools_and_retains_conversation(self):
        runtime = self.setup_run(call("workspace_status"), AIMessage(content="Ready.", usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}), AIMessage(content="Your name is James."))
        thread, result = await self.finish(runtime.submit("My name is James. Check status."))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.adapter.statuses, 1)
        self.assertEqual(result["usage"]["input_tokens"], 10)
        self.assertEqual([e["kind"] for e in result["events"]], ["model_started", "model_completed", "tool_started", "tool_completed", "model_started", "model_completed", "completed"])
        # Instrumentation is sanitized metadata, never the user's prompt or model answer.
        self.assertEqual(len(result["trace_spans"]), 3)
        self.assertEqual([span["operation"] for span in result["trace_spans"]],
                         ["agent.model", "agent.tool", "agent.model"])
        self.assertEqual(result["trace_spans"][1]["tool_name"], "workspace_status")
        self.assertEqual({span["trace_id"] for span in result["trace_spans"]}, {result["trace_id"]})
        self.assertNotIn("My name is James", str(result["trace_spans"]))
        self.assertNotIn("Ready.", str(result["trace_spans"]))
        await self.finish(runtime.submit("What is my name?", thread.id))
        self.assertTrue(any(isinstance(m, HumanMessage) and "James" in m.content for m in self.model.seen[-1]))

    async def test_pause_resume_preserves_checkpoint_without_replaying_tool(self):
        self.adapter.mode = "human"
        runtime = self.setup_run(call("workspace_status"), call("navigate_browser", {"url": "https://alambda-forge.onrender.com/"}), AIMessage(content="Navigation complete."))
        thread, result = await self.finish(runtime.submit("Check status and navigate", sid=SID))
        self.assertEqual(result["status"], "paused")
        self.assertEqual(self.adapter.navigations, 0)
        with self.assertRaises(HTTPException):
            await runtime.resume(thread.id)
        self.adapter.mode, self.adapter.epoch = "agent", 2
        await self.finish(await runtime.resume(thread.id))
        self.assertEqual(thread.status, "completed")
        self.assertEqual(self.adapter.navigations, 1)
        self.assertEqual(self.adapter.statuses, 1)

    async def test_allowlist_and_tool_schema_rejected_before_effect(self):
        runtime = self.setup_run(call("open_browser", {"url": "http://127.0.0.1"}), call("workspace_status", {"secret": "bad"}), AIMessage(content="Not allowed."))
        _, result = await self.finish(runtime.submit("test"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.adapter.opens, 0)
        self.assertEqual(self.adapter.statuses, 0)

    async def test_worker_epoch_conflict_does_not_retry_or_leak(self):
        self.adapter.reject = True
        runtime = self.setup_run(call("navigate_browser"), AIMessage(content="Control changed."))
        _, result = await self.finish(runtime.submit("navigate", sid=SID))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.adapter.navigations, 0)
        self.assertTrue(any(e.get("ok") is False for e in result["events"]))
        self.assertNotIn("sensitive", str(self.model.seen))

    async def test_tool_opened_session_is_exposed_for_live_view(self):
        runtime = self.setup_run(call("open_browser"), AIMessage(content="Opened."))
        _, result = await self.finish(runtime.submit("open"))
        self.assertEqual(result["session_id"], SID)
        self.assertEqual(self.adapter.opens, 1)

    async def test_interactive_action_uses_observed_target_and_trusted_epoch(self):
        observation = str(uuid.uuid4())
        self.adapter.epoch = 4
        self.adapter.observe = AsyncMock(return_value={
            "id": SID, "observation_id": observation, "text": "Repository form",
            "elements": [{"ref": "e1", "role": "textbox", "name": "Name"}],
        })
        self.adapter.action = AsyncMock(return_value={"id": SID, "acted": "fill"})
        runtime = self.setup_run(call("browser_observe"), call("browser_fill", {
            "observation_id": observation, "ref": "e1", "text": "disposable-repository",
        }), AIMessage(content="Repository name entered; nothing submitted."))
        _, result = await self.finish(runtime.submit("Enter a repository name", sid=SID))
        self.assertEqual(result["status"], "completed")
        self.adapter.observe.assert_awaited_once_with(SID, 4)
        self.adapter.action.assert_awaited_once_with("browser_fill", SID, {
            "observation_id": observation, "ref": "e1", "text": "disposable-repository",
        }, 4)
        self.assertIn("Repository form", str(self.model.seen[-1]))

    async def test_uncertain_action_or_human_verification_halts_without_model_retry(self):
        for code in ("action_uncertain", "human_verification_required"):
            with self.subTest(code=code):
                self.adapter.action = AsyncMock(side_effect=BrowserActionError(code))
                runtime = self.setup_run(call("browser_click", {
                    "observation_id": str(uuid.uuid4()), "ref": "e1",
                }), call("browser_click", {
                    "observation_id": str(uuid.uuid4()), "ref": "e1",
                }))
                thread, result = await self.finish(runtime.submit("Submit", sid=SID))
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["answer"], BrowserActionError(code).detail)
                self.assertEqual(len(self.model.seen), 1)
                self.adapter.action.assert_awaited_once()
                # A later explicit user turn can proceed after checking the page.
                self.model.answers = [AIMessage(content="Waiting for your next instruction.")]
                _, continued = await self.finish(runtime.submit("Wait", thread.id))
                self.assertEqual(continued["answer"], "Waiting for your next instruction.")
                await runtime.shutdown()

    async def test_secure_account_entry_event_contains_only_safe_form_metadata(self):
        self.adapter.vault = AsyncMock(return_value={
            "status": "secure_entry_required", "label": "Git High",
            "origin": "https://alambda-forge.onrender.com",
        })
        runtime = self.setup_run(call("vault_request_account", {
            "label": "Git High", "origin": "https://alambda-forge.onrender.com",
        }), AIMessage(content="Enter your login in Accounts."))
        _, result = await self.finish(runtime.submit("Save my login"))
        events = [event for event in result["events"] if event["kind"] == "account_entry_requested"]
        self.assertEqual(len(events), 1)
        self.assertEqual(set(events[0]), {"sequence", "kind", "origin", "label"})
        self.assertEqual(events[0]["label"], "Git High")

    async def test_lost_mcp_creation_response_stops_the_actual_graph_loop(self):
        from types import SimpleNamespace
        from reign.mcp_client import MCPTools, WorkspaceMCP
        for name, args in (("open_browser", {}), ("vault_prepare_signup", {
                "label": "Fixture", "origin": "https://alambda-forge.onrender.com", "username": "fixture"})):
            with self.subTest(tool=name):
                connection = WorkspaceMCP()
                connection.session = SimpleNamespace(call_tool=AsyncMock(side_effect=ConnectionError("private diagnostic")))
                self.adapter = MCPTools(connection)
                runtime = self.setup_run(call(name, args), call(name, args))
                _, result = await self.finish(runtime.submit("Run the requested operation"))
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["answer"], BrowserActionError("action_uncertain").detail)
                self.assertEqual(len(self.model.seen), 1)
                connection.session.call_tool.assert_awaited_once()
                self.assertNotIn("private diagnostic", str(result))
                await runtime.shutdown()

    async def test_vault_use_pauses_before_dispatch_and_rechecks_current_epoch(self):
        self.adapter.mode = "human"
        self.adapter.vault = AsyncMock(return_value={"filled": True, "status": "credentials_filled"})
        args = {"account_id": str(uuid.uuid4()), "observation_id": str(uuid.uuid4())}
        runtime = self.setup_run(call("vault_use_account", args), AIMessage(content="Filled; sign-in is not yet verified."))
        thread, result = await self.finish(runtime.submit("Use the saved login", sid=SID))
        self.assertEqual(result["status"], "paused")
        self.adapter.vault.assert_not_awaited()
        self.adapter.mode, self.adapter.epoch = "agent", 2
        await self.finish(await runtime.resume(thread.id))
        self.adapter.vault.assert_awaited_once_with("vault_use_account", args, SID, 2)
        self.assertEqual(thread.status, "completed")

    async def test_recovered_session_is_reused_without_open_or_navigation(self):
        runtime = self.setup_run(call("open_browser"), call("browser_status"), AIMessage(content="Existing browser recovered."))
        _, result = await self.finish(runtime.submit("Open the browser, then report its status", sid=SID))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["session_id"], SID)
        self.assertEqual(self.adapter.opens, 0)
        self.assertEqual(self.adapter.navigations, 0)
        self.assertEqual((self.adapter.mode, self.adapter.epoch), ("agent", 0))

    async def test_occupied_browser_is_not_reported_as_outage_or_control_change(self):
        self.adapter.open = AsyncMock(side_effect=BrowserOccupied())
        runtime = self.setup_run(call("open_browser"), AIMessage(content="Reconnect to the existing browser."))
        _, result = await self.finish(runtime.submit("Open a browser"))
        self.assertEqual(result["status"], "completed")
        self.assertIsNone(result["session_id"])
        self.adapter.open.assert_awaited_once()
        tool_messages = [message for message in self.model.seen[-1] if message.type == "tool"]
        self.assertEqual(json.loads(tool_messages[0].content), {
            "error": BROWSER_OCCUPIED_MESSAGE, "status": 409, "code": BROWSER_OCCUPIED_CODE,
        })
        self.assertTrue(any(event.get("tool") == "open_browser" and event.get("ok") is False for event in result["events"]))

    async def test_loop_budget_stops_rogue_model(self):
        runtime = self.setup_run(*[call("workspace_status") for _ in range(MAX_STEPS + 1)])
        _, result = await self.finish(runtime.submit("check"))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.adapter.statuses, MAX_STEPS)

    async def test_provider_error_is_sanitized_and_thread_cannot_continue(self):
        runtime = self.setup_run()
        async def failure(messages):
            raise RuntimeError("secret-provider-response-body")
        self.model.ainvoke = failure
        thread, result = await self.finish(runtime.submit("hi"))
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("secret-provider", str(result))
        with self.assertRaises(HTTPException):
            runtime.submit("retry", thread.id)

    async def test_cancel_unstarted_task_and_expiry(self):
        runtime = self.setup_run(AIMessage(content="hello"))
        result = runtime.submit("hi")
        await runtime.delete(result["thread_id"])
        with self.assertRaises(HTTPException):
            runtime.get(result["thread_id"])
        thread, _ = await self.finish(runtime.submit("hi"))
        thread.updated -= 1900
        runtime.prune()
        self.assertEqual(runtime.threads, {})

    async def test_unconfigured_has_no_mock_success(self):
        runtime = self.setup_run()
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaises(HTTPException) as error:
                runtime.submit("hi")
            self.assertEqual(error.exception.status_code, 503)

    async def test_openai_configuration_matches_workmates_responses_contract(self):
        with patch.dict(os.environ, {"OPENAI_MODEL": "gpt-4.1-mini", "OPENAI_API_KEY": "test-key-never-live"}, clear=True):
            bound = model_client()
            self.assertTrue(bound.bound.use_responses_api)
            self.assertFalse(bound.bound.store)
            self.assertEqual(bound.bound.openai_api_base, "https://api.openai.com/v1")
            self.assertFalse(bound.kwargs["parallel_tool_calls"])
            self.assertEqual({tool["function"]["name"] for tool in bound.kwargs["tools"]}, {
                "workspace_status", "browser_status", "open_browser", "navigate_browser",
                "browser_observe", "browser_click", "browser_fill", "browser_press", "browser_scroll",
                "vault_status", "vault_list_accounts", "vault_request_account", "vault_prepare_signup", "vault_use_account"})

    async def test_explicit_provider_base_never_falls_back_to_another_host(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-never-live",
                "OPENAI_MODEL": "gpt-4.1-mini",
                "OPENAI_BASE_URL": "https://voice.example.com/v1/"}, clear=True):
            self.assertEqual(model_client().bound.openai_api_base, "https://voice.example.com/v1")
            for invalid in ("", "http://voice.example.com/v1", "https://user:secret@voice.example.com/v1",
                            "https://voice.example.com/v1?key=secret"):
                with patch.dict(os.environ, {"OPENAI_BASE_URL": invalid}):
                    with self.assertRaisesRegex(ValueError, "Invalid OpenAI base URL"):
                        model_client()

    async def test_voice_can_identify_exhausted_thread_without_retrying_a_task(self):
        runtime = self.setup_run(*[AIMessage(content="Ready.") for _ in range(MAX_TURNS)])
        tid = None
        for count in range(1, MAX_TURNS + 1):
            thread, result = await self.finish(runtime.submit("status", tid))
            tid = thread.id
            self.assertEqual(result["turns"], count)
            self.assertEqual(result["max_turns"], MAX_TURNS)
            self.assertEqual(result["continuable"], count < MAX_TURNS)
        with self.assertRaises(HTTPException) as error:
            runtime.submit("one more", tid)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(self.model.seen), MAX_TURNS)
