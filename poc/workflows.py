"""RenderPdfWorkflow -- the PoC workflow.

Deliberately simple: one activity, one retry policy. The point of this PoC is
to prove the *mechanism* (kill a worker mid-activity, watch Temporal retry it
on a different worker after the heartbeat times out) using real Temporal
Server behaviour -- not to demonstrate anything fancy about workflow authoring.

See docs/ARCHITECTURE.md section 6 for why the *generic, templated*
workflow-generation approach this repo argues for is intentionally NOT what
this file is: RenderPdfWorkflow is a normal, hand-written Temporal workflow.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from activities import render_markdown_to_pdf


@workflow.defn
class RenderPdfWorkflow:
    """Render a markdown string to PDF via a single heartbeating Activity.

    heartbeat_timeout=5s is the mechanism that makes the kill-a-worker demo
    deterministic: once worker A is SIGKILLed, it stops calling
    activity.heartbeat(), and Temporal Server declares the activity attempt
    dead ~5s later (independent of the artificial RENDER_HOLD_SECONDS delay),
    then schedules attempt 2 for pickup by whichever worker is polling the
    task queue next -- worker B.
    """

    @workflow.run
    async def run(self, markdown_text: str) -> str:
        return await workflow.execute_activity(
            render_markdown_to_pdf,
            markdown_text,
            start_to_close_timeout=timedelta(seconds=60),
            heartbeat_timeout=timedelta(seconds=5),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(seconds=1),
                backoff_coefficient=1.0,
                maximum_attempts=3,
            ),
        )
