import os
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from meeting_service.app import app, Allowance, build_graph


class FakeModel:
    async def ainvoke(self, messages):
        self.messages = messages
        return AIMessage(content="Suggestion: agree on an owner and the next milestone.")


class BoundaryTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"MEETING_TOKEN": "t" * 40, "OPENAI_API_KEY": "test"})
        self.env.start()
        self.model = FakeModel()
        app.state.meeting_graph = build_graph(self.model)
        self.client = TestClient(app)
        self.headers = {"Authorization": "Bearer " + "t" * 40}

    def tearDown(self):
        self.env.stop()
        del app.state.meeting_graph

    def test_token_does_not_unlock_admin_routes(self):
        self.assertEqual(self.client.post("/reply", json={"message": "Hello"}).status_code, 401)
        self.assertEqual(self.client.get("/api/accounts", headers=self.headers).status_code, 404)
        self.assertFalse(self.client.get("/healthz").json()["workspace_access"])

    def test_real_graph_with_mock_model_is_stateless(self):
        response = self.client.post("/reply", headers=self.headers,
                                    json={"message": "Summarize", "context": [{"speaker": "Jim", "text": "Plan a pilot."}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["orchestration"], "langgraph")
        self.assertIn("Plan a pilot", self.model.messages[1].content)
        self.client.post("/reply", headers=self.headers, json={"message": "Different meeting"})
        self.assertNotIn("Plan a pilot", self.model.messages[1].content)

    def test_limits_and_fixed_errors(self):
        self.assertEqual(self.client.post("/reply", headers=self.headers, json={"message": "x" * 1201}).status_code, 422)
        self.assertEqual(self.client.post("/reply", headers=self.headers, content=b"x" * 17000).status_code, 413)
        budget = Allowance()
        self.assertTrue(budget.reserve())
        self.assertTrue(budget.reserve())
        self.assertFalse(budget.reserve())
