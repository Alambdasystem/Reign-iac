"""Checkpoint restoration contract; simulated process reconstruction, NOT live production.

The same saver instance stands in for durable storage, exercising the original
thread ID and confirming completed tool steps are not repeated after reload.
An actual PostgreSQL connection, authenticated principal, and restart are NOT tested.
"""
import unittest
import uuid

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from reign.agent import Conversation


class FakeModel:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.seen = []

    async def ainvoke(self, messages):
        self.seen.append(messages)
        return self.responses.pop(0)


class FakeAdapter:
    def __init__(self):
        self.status_calls = 0

    async def status(self):
        self.status_calls += 1
        return {"status": "ready", "components": {"core": "ready"}}


class ThreadRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconstruct_same_thread_keeps_prior_messages_without_replaying_tool(self):
        saved = InMemorySaver()
        adapter = FakeAdapter()
        from langchain_core.messages import AIMessage
        tool = AIMessage(content="", tool_calls=[{
            "name": "workspace_status", "args": {},
            "id": str(uuid.uuid4()), "type": "tool_call",
        }])
        first_model = FakeModel(tool, AIMessage(content="Workspace is ready."))
        original = Conversation(first_model, adapter, checkpointer=saved)
        await original.run({"messages": [HumanMessage("Check workspace")],
                            "session_id": None, "steps": 0, "halted": False})
        self.assertEqual(original.status, "completed")
        self.assertEqual(adapter.status_calls, 1)

        # Simulated new process: reconstruct a new graph with the saved thread ID.
        next_model = FakeModel(AIMessage(content="We already checked it."))
        recovered = Conversation(next_model, adapter, checkpointer=saved,
                                 thread_id=original.id)
        self.assertEqual(recovered.id, original.id)
        await recovered.run({"messages": [HumanMessage("What did we check?")],
                             "session_id": None, "steps": 0, "halted": False})
        self.assertEqual(recovered.status, "completed")
        self.assertEqual(adapter.status_calls, 1)
        self.assertTrue(any(isinstance(message, HumanMessage)
                            and message.content == "Check workspace"
                            for message in next_model.seen[0]))
        self.assertEqual(recovered.memory_kind, "injected")

    async def test_restored_thread_identifier_must_be_valid_uuid(self):
        for invalid in ("", "not-a-uuid", "another-user", 14, None):
            if invalid is None:
                continue  # None deliberately creates a new thread.
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                Conversation(FakeModel(), FakeAdapter(), checkpointer=InMemorySaver(),
                             thread_id=invalid)
