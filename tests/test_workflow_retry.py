"""SDK time-skipping test of RenderPdfWorkflow's RetryPolicy wiring.

This is a *secondary*, fast sanity check that the retry policy on the
workflow is wired the way we intend (maximum_attempts=3, activity retried on
failure). It does NOT replace the real kill-a-worker demo
(scripts/run_demo.sh) as the acceptance proof for issue #30 -- it cannot,
because temporalio.testing.WorkflowEnvironment.start_time_skipping() runs
against an in-memory time-skipping test server with a mocked activity, so
there is no real OS process to kill and no real heartbeat-timeout detection
to observe. What it *does* prove, in seconds instead of ~20s and without
touching real processes, is that a failing activity is actually retried by
the workflow definition up to the configured attempt count before the
workflow either succeeds or gives up.

Requires network access on first run: WorkflowEnvironment.start_time_skipping()
lazily downloads a test-server binary if not already cached locally.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from temporalio import activity

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "poc"))

from workflows import RenderPdfWorkflow  # noqa: E402

TASK_QUEUE = "test-render-pdf-retry"


@pytest.mark.asyncio
async def test_activity_retried_then_succeeds():
    """Activity fails twice, then succeeds on attempt 3 -- within
    maximum_attempts=3, so the workflow should complete successfully."""

    attempts = {"count": 0}

    @activity.defn(name="render_markdown_to_pdf")
    async def flaky_render(markdown_text: str) -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError(f"synthetic failure on attempt {attempts['count']}")
        return f"ok on attempt {attempts['count']}"

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[RenderPdfWorkflow],
            activities=[flaky_render],
        ):
            result = await env.client.execute_workflow(
                RenderPdfWorkflow.run,
                "# irrelevant, activity is mocked",
                id=f"test-retry-{uuid.uuid4()}",
                task_queue=TASK_QUEUE,
            )
            assert result == "ok on attempt 3"
            assert attempts["count"] == 3


@pytest.mark.asyncio
async def test_activity_exhausts_retries_and_workflow_fails():
    """Activity always fails -- maximum_attempts=3 should be exhausted and
    the workflow should end up failed, not hang or silently succeed."""

    attempts = {"count": 0}

    @activity.defn(name="render_markdown_to_pdf")
    async def always_failing_render(markdown_text: str) -> str:
        attempts["count"] += 1
        raise RuntimeError(f"synthetic permanent failure on attempt {attempts['count']}")

    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=TASK_QUEUE,
            workflows=[RenderPdfWorkflow],
            activities=[always_failing_render],
        ):
            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    RenderPdfWorkflow.run,
                    "# irrelevant, activity is mocked",
                    id=f"test-retry-exhaust-{uuid.uuid4()}",
                    task_queue=TASK_QUEUE,
                )
            assert attempts["count"] == 3
