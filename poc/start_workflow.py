"""Starts RenderPdfWorkflow and exits immediately (does not wait for result).

Usage: python poc/start_workflow.py --id <workflow-id> [--fixture path/to.md]
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from temporalio.client import Client

from workflows import RenderPdfWorkflow

TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
TEMPORAL_NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "demo-poc")
TASK_QUEUE = os.environ.get("TASK_QUEUE", "render-pdf-poc")

DEFAULT_FIXTURE = Path(__file__).parent / "fixtures" / "sample.md"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True, help="Workflow ID")
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    args = parser.parse_args()

    markdown_text = Path(args.fixture).read_text()

    client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)
    handle = await client.start_workflow(
        RenderPdfWorkflow.run,
        markdown_text,
        id=args.id,
        task_queue=TASK_QUEUE,
    )
    print(f"started workflow_id={handle.id} run_id={handle.result_run_id}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
