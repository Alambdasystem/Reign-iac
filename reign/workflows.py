from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class OpenBrowser:
    @workflow.run
    async def run(self, request: dict) -> dict:
        return await workflow.execute_activity(
            "open_browser", request, start_to_close_timeout=timedelta(seconds=90),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


@workflow.defn
class NavigateBrowser:
    @workflow.run
    async def run(self, request: dict) -> dict:
        # Navigation is not retried: a human may take control after a timeout.
        return await workflow.execute_activity(
            "navigate_browser", request, start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


@workflow.defn
class BrowserAction:
    @workflow.run
    async def run(self, request: dict) -> dict:
        # A timeout can follow a real click or form effect. Never replay it.
        return await workflow.execute_activity(
            "browser_action", request, start_to_close_timeout=timedelta(seconds=20),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


@workflow.defn
class FillAccount:
    @workflow.run
    async def run(self, request: dict) -> dict:
        # Only a short-lived, single-use reference enters Temporal history.
        # The worker redeems credentials privately and never returns them here.
        return await workflow.execute_activity(
            "fill_account", request, start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
