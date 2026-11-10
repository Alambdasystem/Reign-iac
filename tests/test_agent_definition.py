"""Agent configuration must be independent of the trusted harness/tool boundary."""
import unittest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from reign.agent import AgentRuntime, Conversation
from reign.agent_definition import AgentDefinition, REIGN_DEFINITION


class RecordingModel:
    def __init__(self):
        self.seen = None

    async def ainvoke(self, messages):
        self.seen = messages
        return AIMessage(content="ok")


class AgentDefinitionTests(unittest.IsolatedAsyncioTestCase):
    def test_default_agent_is_reign(self):
        runtime = AgentRuntime(model_factory=RecordingModel, adapter=object())
        self.assertEqual(runtime.definition.agent_id, "reign")
        self.assertEqual(runtime.definition.instructions, REIGN_DEFINITION.instructions)

    def test_invalid_definition_rejected(self):
        with self.assertRaises(ValueError):
            AgentDefinition(agent_id="", name="other", instructions="Work.")
        with self.assertRaises(TypeError):
            AgentRuntime(model_factory=RecordingModel, adapter=object(), definition={})

    async def test_two_definitions_share_execution_harness_without_cross_talk(self):
        research = AgentDefinition(
            agent_id="research", name="Research",
            instructions="You are a read-only research assistant.",
        )
        reign_model = RecordingModel()
        research_model = RecordingModel()
        reign = Conversation(reign_model, object(), REIGN_DEFINITION)
        other = Conversation(research_model, object(), research)
        state = {"messages": [HumanMessage(content="Hello")], "steps": 0}
        await reign.call_model(state)
        await other.call_model(state)
        self.assertIsInstance(reign_model.seen[0], SystemMessage)
        self.assertEqual(reign_model.seen[0].content, REIGN_DEFINITION.instructions)
        self.assertEqual(research_model.seen[0].content, research.instructions)
        self.assertNotEqual(reign_model.seen[0].content, research_model.seen[0].content)
        # Definitions do not add tools, privileged identity or MCP connections.
        self.assertEqual(set(research.capabilities), set())
