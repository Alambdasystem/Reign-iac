"""Independent fake-adapter checks for the bounded Responses tool round trip."""
import asyncio
import copy
import json
import unittest
import uuid

import httpx

from reign.team_assistant import TeamAssistant
from reign.team_media import TeamMedia
from reign.team_store import TeamError

SETTINGS = {"base": "https://provider.example/v1", "key": "synthetic-tool-loop-key"}


def tool_response(call_id, name="room_browser_observe", arguments=None, **extra):
    return httpx.Response(200, json={"status": "completed", "output": [
        {"type": "function_call", "call_id": call_id, "name": name,
         "arguments": json.dumps({} if arguments is None else arguments), **extra}]})


def final_response(text="The actual browser result is available."):
    reply = {"segments": [{"text": text, "expression": "neutral"}], "documents": []}
    return httpx.Response(200, json={"status": "completed", "output": [
        {"type": "message", "content": [{"type": "output_text", "text": json.dumps(reply)}]}]})


class Provider:
    def __init__(self, responses): self.responses = list(responses); self.requests = []
    async def post(self, url, **kwargs):
        self.requests.append((url, copy.deepcopy(kwargs)))
        return self.responses.pop(0)


class Browser:
    def __init__(self): self.calls = []; self.result = {"ok": True, "text": "Actual room fixture page"}; self.error = None
    def tool_specs(self):
        return [{"type": "function", "name": "room_browser_observe", "description": "Fixture room observer",
                 "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}]
    async def execute(self, actor, room, name, arguments):
        self.calls.append((actor, room, name, arguments))
        if self.error: raise self.error
        return self.result


class TeamToolLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.actor = {"id": str(uuid.uuid4()), "role": "employee", "credential_revision": 1}
        self.room = str(uuid.uuid4()); self.browser = Browser()

    async def run_loop(self, responses, browser=True):
        self.provider = Provider(responses)
        assistant = TeamAssistant(self.provider, TeamMedia(), browser=self.browser if browser else None)
        payload = {"model": "fixture-model", "instructions": "Authorized room context only.", "store": False,
                   "input": [{"role": "user", "content": [{"type": "input_text", "text": "Inspect this room browser."}]}]}
        return await assistant.model_reply(SETTINGS, payload, self.actor, self.room)

    async def test_actual_tool_result_and_server_actor_room_are_carried_into_the_next_response(self):
        reply = await self.run_loop([tool_response("call-1"), final_response()])
        self.assertEqual(reply.segments[0].text, "The actual browser result is available.")
        self.assertEqual(self.browser.calls, [(self.actor, self.room, "room_browser_observe", {})])
        first, second = [value[1] for value in self.provider.requests]
        self.assertFalse(first["follow_redirects"]); self.assertFalse(second["follow_redirects"])
        self.assertFalse(first["json"]["parallel_tool_calls"])
        self.assertEqual(first["json"]["include"], ["reasoning.encrypted_content"])
        output = second["json"]["input"][-1]
        self.assertEqual((output["type"], output["call_id"]), ("function_call_output", "call-1"))
        self.assertEqual(json.loads(output["output"]), self.browser.result)
        self.assertNotIn("previous_response_id", second["json"])
        self.assertEqual(first["headers"]["Authorization"], "Bearer " + SETTINGS["key"])

    async def test_duplicate_call_id_reuses_receipt_but_changed_arguments_abort(self):
        await self.run_loop([tool_response("same-call"), tool_response("same-call"), final_response()])
        self.assertEqual(len(self.browser.calls), 1)
        repeated = [json.loads(value[1]["json"]["input"][-1]["output"]) for value in self.provider.requests[1:]]
        self.assertEqual(repeated, [self.browser.result, self.browser.result])
        self.browser.calls.clear()
        with self.assertRaises(TeamError):
            await self.run_loop([tool_response("same-call"), tool_response("same-call", arguments={"changed": True}), final_response()])
        self.assertEqual(len(self.browser.calls), 1)

    async def test_four_dispatches_maximum_then_final_reply_is_required(self):
        await self.run_loop([tool_response("call-" + str(i)) for i in range(4)] + [final_response()])
        self.assertEqual(len(self.browser.calls), 4)
        self.assertEqual(len(self.provider.requests), 5)
        self.assertEqual(self.provider.requests[-1][1]["json"]["tool_choice"], "none")
        self.browser.calls.clear()
        with self.assertRaises(TeamError): await self.run_loop([tool_response("call-" + str(i)) for i in range(5)])
        self.assertEqual(len(self.browser.calls), 4)

    async def test_unknown_parallel_and_malformed_calls_never_dispatch(self):
        parallel = tool_response("call-a").json()
        parallel["output"].append(tool_response("call-b").json()["output"][0])
        malformed = tool_response("call-b").json(); malformed["output"][0]["arguments"] = "[]"
        for response in (tool_response("call-a", name="vault_use_account"),
                         httpx.Response(200, json=parallel), httpx.Response(200, json=malformed)):
            with self.assertRaises(TeamError): await self.run_loop([response])
        self.assertEqual(self.browser.calls, [])
        with self.assertRaises(TeamError): await self.run_loop([tool_response("call-a")], browser=False)
        self.assertEqual(self.browser.calls, [])

    async def test_uncertain_result_is_evidence_and_never_implicitly_retried(self):
        self.browser.result = {"ok": False, "code": "action_uncertain", "detail": "The website result could not be confirmed."}
        await self.run_loop([tool_response("call-a"), final_response("The website result could not be confirmed.")])
        self.assertEqual(len(self.browser.calls), 1)
        result = self.provider.requests[-1][1]["json"]["input"][-1]
        self.assertEqual(json.loads(result["output"])["code"], "action_uncertain")

    async def test_revoked_identity_and_cancelled_tool_abort_without_another_model_call(self):
        for error in (TeamError("team_identity_required"), asyncio.CancelledError()):
            self.browser.error = error; self.browser.calls.clear()
            with self.assertRaises(type(error)): await self.run_loop([tool_response("call-a"), final_response()])
            self.assertEqual(len(self.provider.requests), 1)
            self.assertEqual(len(self.browser.calls), 1)

    async def test_large_result_is_replaced_with_honest_bounded_error_and_redirect_is_rejected(self):
        self.browser.result = {"text": "x" * 120001}
        await self.run_loop([tool_response("call-a"), final_response("The browser result exceeded this turn's limit.")])
        output = self.provider.requests[-1][1]["json"]["input"][-1]
        self.assertEqual(json.loads(output["output"])["code"], "tool_result_too_large")
        self.assertLess(len(output["output"]), 300)
        self.browser.calls.clear()
        with self.assertRaises(TeamError):
            await self.run_loop([tool_response("call-b"), httpx.Response(302, headers={"Location": "https://elsewhere.example"})])
        self.assertEqual(len(self.browser.calls), 1)


if __name__ == "__main__": unittest.main()
