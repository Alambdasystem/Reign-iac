from datetime import timedelta
from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class OpenBrowser:
    @workflow.run
    async def run(self, request: dict) -> dict:
        return await workflow.execute_activity(
            "open_browser", request, start_to_close_timeout=timedelta(seconds=90),
            retry_policy=RetryPolicy(maximum_attempts=2),
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
