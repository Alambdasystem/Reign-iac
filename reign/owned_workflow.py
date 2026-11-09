"""Run one server-named Temporal workflow and forward caller cancellation.

Cancellation is a bounded request to this workflow only. It cannot undo input
already delivered to a browser and must never be reported as proof of no effect.
The caller must generate a fresh workflow ID; model-supplied IDs are not allowed.
"""
import asyncio
import contextlib
from datetime import timedelta

import anyio
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError


START_SECONDS = 5.0
CANCEL_SECONDS = 2.0
CLEANUP_SECONDS = START_SECONDS + CANCEL_SECONDS + 1.0
_late_cleanup = set()


async def _cancel_handle(handle):
    try:
        with anyio.move_on_after(CANCEL_SECONDS, shield=True):
            await handle.cancel(reason="REIGN caller stopped waiting; completed actions are not undone",
                                rpc_timeout=timedelta(seconds=CANCEL_SECONDS))
            return True
    except (Exception, asyncio.CancelledError):
        pass
    return False


def _background(coroutine):
    # A delayed start acknowledgement can arrive after the caller has gone.
    # Retain its short cancellation task, consume errors, and never restart work.
    task = asyncio.create_task(coroutine)
    _late_cleanup.add(task)
    def done(completed):
        _late_cleanup.discard(completed)
        with contextlib.suppress(BaseException):
            completed.result()
    task.add_done_callback(done)


async def _cancel_owned(client, workflow_id, start_task, handle, start_deadline):
    if handle is not None:
        await _cancel_handle(handle)
        return

    # Shielding the single start RPC lets us use the exact run-bound handle when
    # the server accepted it but the acknowledgement raced with cancellation.
    remaining = max(0.0, start_deadline - asyncio.get_running_loop().time())
    if not start_task.done() and remaining:
        await asyncio.wait({start_task}, timeout=remaining)
    if start_task.done():
        try:
            handle = start_task.result()
        except WorkflowAlreadyStartedError:
            # A rejected duplicate is not a workflow this invocation owns.
            return
        except (Exception, asyncio.CancelledError):
            handle = client.get_workflow_handle(workflow_id)
        await _cancel_handle(handle)
        return

    # No acknowledgement by the RPC deadline: request cancellation by the
    # fresh, trusted ID. If it was not accepted yet, a later returned handle is
    # cancelled once as well. Only cancellation may be repeated, never start or
    # a browser activity, and each cancellation RPC has its own deadline.
    finished = asyncio.Event()
    result = {"accepted": False}
    async def late_start(completed):
        try:
            late_handle = completed.result()
        except WorkflowAlreadyStartedError:
            return
        except (Exception, asyncio.CancelledError):
            # A late dropped acknowledgement can still follow server acceptance.
            # Recheck the same owned ID after an earlier NotFound/failed cancel.
            late_handle = client.get_workflow_handle(workflow_id)
        await finished.wait()
        if not result["accepted"]:
            await _cancel_handle(late_handle)
    start_task.add_done_callback(lambda completed: _background(late_start(completed)))
    try:
        result["accepted"] = await _cancel_handle(client.get_workflow_handle(workflow_id))
    finally:
        finished.set()


async def _cleanup(client, workflow_id, start_task, handle, start_deadline):
    # MCP cancels an AnyIO request scope. Shield that scope so the cancellation
    # notification can reach Temporal, while keeping cleanup itself bounded.
    with anyio.move_on_after(CLEANUP_SECONDS, shield=True):
        try:
            await _cancel_owned(client, workflow_id, start_task, handle, start_deadline)
        except (Exception, asyncio.CancelledError):
            pass


async def execute_owned_workflow(client, name, payload, *, id, task_queue):
    """Start exactly once; cancel only this fresh server-assigned workflow."""
    start_deadline = asyncio.get_running_loop().time() + START_SECONDS
    start_task = asyncio.create_task(client.start_workflow(
        name, payload, id=id, task_queue=task_queue,
        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        retry_policy=RetryPolicy(maximum_attempts=1),
        rpc_timeout=timedelta(seconds=START_SECONDS)))
    handle = None
    try:
        # wait_for only cancels the shield, leaving the one start handshake
        # available to the cleanup path. The RPC itself has the same deadline.
        handle = await asyncio.wait_for(asyncio.shield(start_task), START_SECONDS)
        return await handle.result()
    except asyncio.CancelledError:
        await _cleanup(client, id, start_task, handle, start_deadline)
        raise
    except (TimeoutError, RPCError):
        # A dropped start/result response does not prove no workflow ran.
        await _cleanup(client, id, start_task, handle, start_deadline)
        raise

