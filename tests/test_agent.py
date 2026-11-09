import asyncio
import os
import unittest
import uuid
from unittest.mock import patch

from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from reign.agent import AgentRuntime, MAX_STEPS, MAX_TURNS, model_client

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
            self.assertEqual(len(bound.kwargs["tools"]), 4)

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
