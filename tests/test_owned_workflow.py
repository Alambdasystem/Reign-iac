"""Cancellation contracts: one owned workflow, no replay, real MCP scopes."""
import asyncio
from datetime import timedelta
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import McpError
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from reign.mcp_client import CancellableClientSession
from reign.owned_workflow import execute_owned_workflow


class Handle:
    def __init__(self, result=None):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.completion = asyncio.get_running_loop().create_future()
        if result is not None:
            self.completion.set_result(result)
        self.cancel_calls = []

    async def result(self):
        self.started.set()
        return await self.completion

    async def cancel(self, **kwargs):
        self.cancel_calls.append(kwargs)
        self.cancelled.set()


class OwnedWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.id = "reign-action-" + str(uuid4())
        self.handle = Handle()
        self.client = Mock()
        self.client.start_workflow = AsyncMock(return_value=self.handle)
        self.client.get_workflow_handle.return_value = self.handle
        self.timing = patch.multiple("reign.owned_workflow", START_SECONDS=0.08,
                                     CANCEL_SECONDS=0.04, CLEANUP_SECONDS=0.2)
        self.timing.start()
        self.addCleanup(self.timing.stop)

    async def run_owned(self):
        return await execute_owned_workflow(self.client, "BrowserAction", {"id": "session-fixture"},
                                            id=self.id, task_queue="fixture-queue")

    async def test_success_starts_once_with_duplicate_and_retry_protection(self):
        self.handle.completion.set_result({"acted": True})
        self.assertEqual(await self.run_owned(), {"acted": True})
        self.client.start_workflow.assert_awaited_once()
        call = self.client.start_workflow.await_args
        self.assertEqual(call.args, ("BrowserAction", {"id": "session-fixture"}))
        self.assertEqual(call.kwargs["id"], self.id)
        self.assertEqual(call.kwargs["id_reuse_policy"], WorkflowIDReusePolicy.REJECT_DUPLICATE)
        self.assertEqual(call.kwargs["retry_policy"].maximum_attempts, 1)
        self.assertEqual(self.handle.cancel_calls, [])
        self.client.get_workflow_handle.assert_not_called()

    async def test_cancel_before_execution_starts_nothing(self):
        task = asyncio.create_task(self.run_owned())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.client.start_workflow.assert_not_awaited()
        self.client.get_workflow_handle.assert_not_called()

    async def test_cancel_while_waiting_result_cancels_only_returned_handle(self):
        task = asyncio.create_task(self.run_owned())
        await self.handle.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.handle.cancel_calls), 1)
        self.assertIn("not undone", self.handle.cancel_calls[0]["reason"])
        self.assertLessEqual(self.handle.cancel_calls[0]["rpc_timeout"].total_seconds(), 0.04)
        self.client.start_workflow.assert_awaited_once()
        self.client.get_workflow_handle.assert_not_called()

    async def test_cancel_during_start_waits_for_the_one_accepted_run(self):
        gate = asyncio.get_running_loop().create_future()
        started = asyncio.Event()
        async def start(*args, **kwargs):
            started.set()
            return await gate
        self.client.start_workflow.side_effect = start
        task = asyncio.create_task(self.run_owned())
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(gate.cancelled())
        gate.set_result(self.handle)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(self.handle.cancel_calls), 1)
        self.client.start_workflow.assert_awaited_once()
        self.client.get_workflow_handle.assert_not_called()

    async def test_rejected_duplicate_is_never_cancelled(self):
        gate = asyncio.get_running_loop().create_future()
        started = asyncio.Event()
        async def start(*args, **kwargs):
            started.set()
            return await gate
        self.client.start_workflow.side_effect = start
        task = asyncio.create_task(self.run_owned())
        await started.wait()
        task.cancel()
        gate.set_exception(WorkflowAlreadyStartedError(self.id, "BrowserAction"))
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.client.get_workflow_handle.assert_not_called()
        self.assertEqual(self.handle.cancel_calls, [])

    async def test_unknown_start_cancels_exact_id_then_late_run_without_replay(self):
        gate = asyncio.get_running_loop().create_future()
        started = asyncio.Event()
        async def start(*args, **kwargs):
            started.set()
            return await gate
        self.client.start_workflow.side_effect = start
        fallback = Mock(cancel=AsyncMock(side_effect=RuntimeError("fixture workflow not accepted yet")))
        self.client.get_workflow_handle.return_value = fallback
        task = asyncio.create_task(self.run_owned())
        await started.wait()
        task.cancel()
        before = time.monotonic()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertLess(time.monotonic() - before, 0.5)
        self.client.get_workflow_handle.assert_called_once_with(self.id)
        fallback.cancel.assert_awaited_once()
        gate.set_result(self.handle)
        await asyncio.wait_for(self.handle.cancelled.wait(), 0.5)
        self.assertEqual(len(self.handle.cancel_calls), 1)
        self.client.start_workflow.assert_awaited_once()

    async def test_successful_id_cancel_is_not_repeated_after_late_ack(self):
        gate = asyncio.get_running_loop().create_future()
        started = asyncio.Event()
        async def start(*args, **kwargs):
            started.set()
            return await gate
        self.client.start_workflow.side_effect = start
        fallback = Mock(cancel=AsyncMock())
        self.client.get_workflow_handle.return_value = fallback
        task = asyncio.create_task(self.run_owned())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        gate.set_result(self.handle)
        await asyncio.sleep(0.02)
        fallback.cancel.assert_awaited_once()
        self.assertEqual(self.handle.cancel_calls, [])
        self.client.start_workflow.assert_awaited_once()

    async def test_late_failed_handshake_rechecks_owned_id_after_not_found(self):
        gate = asyncio.get_running_loop().create_future()
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def start(*args, **kwargs):
            started.set()
            return await gate
        async def cancel(**kwargs):
            if fallback.cancel.await_count == 1:
                raise RuntimeError("fixture workflow not accepted yet")
            cancelled.set()
        fallback = Mock(cancel=AsyncMock(side_effect=cancel))
        self.client.start_workflow.side_effect = start
        self.client.get_workflow_handle.return_value = fallback
        task = asyncio.create_task(self.run_owned())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        gate.set_exception(RuntimeError("fixture dropped start acknowledgement"))
        await asyncio.wait_for(cancelled.wait(), 0.5)
        self.assertEqual([call.args for call in self.client.get_workflow_handle.call_args_list], [(self.id,), (self.id,)])
        self.assertEqual(fallback.cancel.await_count, 2)
        self.client.start_workflow.assert_awaited_once()

    async def test_cancel_rpc_timeout_is_bounded_and_does_not_hide_cancellation(self):
        async def stall(**kwargs):
            await asyncio.Event().wait()
        self.handle.cancel = AsyncMock(side_effect=stall)
        task = asyncio.create_task(self.run_owned())
        await self.handle.started.wait()
        task.cancel()
        before = time.monotonic()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertLess(time.monotonic() - before, 0.5)
        self.handle.cancel.assert_awaited_once()
        self.client.start_workflow.assert_awaited_once()

    async def test_cancel_rpc_failure_never_leaks_its_body(self):
        self.handle.cancel = AsyncMock(side_effect=RuntimeError("fixture-private-cancel-body"))
        task = asyncio.create_task(self.run_owned())
        await self.handle.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError) as failure:
            await task
        self.assertNotIn("private", str(failure.exception))
        self.handle.cancel.assert_awaited_once()
        self.client.start_workflow.assert_awaited_once()


class MCPCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def scenario(self, *, timeout=False):
        """Use actual SDK sessions/cancel notifications and server request scopes."""
        client_send, server_receive = anyio.create_memory_object_stream(16)
        server_send, client_receive = anyio.create_memory_object_stream(16)
        handles = {name: Handle() for name in ("first", "second")}
        clients = {}
        server = Server("reign-cancellation-fixture")
        @server.list_tools()
        async def list_tools():
            return [types.Tool(name=name, inputSchema={"type": "object", "properties": {},
                                                       "additionalProperties": False})
                    for name in handles]
        @server.call_tool(validate_input=False)
        async def call_tool(name, arguments):
            client = Mock()
            client.start_workflow = AsyncMock(return_value=handles[name])
            client.get_workflow_handle.return_value = handles[name]
            clients[name] = client
            return await execute_owned_workflow(client, "BrowserAction", {},
                id="reign-action-" + str(uuid4()), task_queue="fixture-queue")
        async with anyio.create_task_group() as group:
            group.start_soon(server.run, server_receive, server_send, server.create_initialization_options())
            async with CancellableClientSession(client_receive, client_send,
                                                read_timeout_seconds=timedelta(seconds=2)) as session:
                await session.initialize()
                first = asyncio.create_task(session.call_tool("first", {},
                    read_timeout_seconds=timedelta(seconds=1.5) if timeout else None))
                second = asyncio.create_task(session.call_tool("second", {}))
                # The timeout must exercise cancellation of an already-started
                # server request, not an incidental CI scheduling delay before
                # the MCP fixture receives it. Retain the real SDK timeout path.
                await asyncio.wait_for(handles["first"].started.wait(), 3)
                await asyncio.wait_for(handles["second"].started.wait(), 3)
                if timeout:
                    with self.assertRaises(McpError) as failure:
                        await first
                    self.assertEqual(failure.exception.error.code, 408)
                else:
                    first.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await first
                await asyncio.wait_for(handles["first"].cancelled.wait(), 1)
                self.assertFalse(handles["second"].cancelled.is_set())
                clients["first"].start_workflow.assert_awaited_once()
                clients["second"].start_workflow.assert_awaited_once()
                self.assertEqual(len(handles["first"].cancel_calls), 1)
                second.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await second
                await asyncio.wait_for(handles["second"].cancelled.wait(), 1)
            group.cancel_scope.cancel()

    async def test_local_cancel_reaches_only_matching_server_request_and_workflow(self):
        await self.scenario()

    async def test_client_timeout_cancels_server_scope_without_replaying(self):
        await self.scenario(timeout=True)


if __name__ == "__main__":
    unittest.main()
