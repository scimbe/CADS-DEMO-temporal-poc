"""Temporal worker process for the PoC.

Run this exact same script twice (worker A, then -- after A is SIGKILLed --
worker B) to get two independent OS processes both capable of picking up
`render_markdown_to_pdf` from the `render-pdf-poc` task queue. Identity is
set explicitly to "<pid>@<hostname>" so it is unambiguous in Temporal event
history and evidence which process handled which attempt (see
docs/ARCHITECTURE.md section 8 and scripts/check_acceptance.py).
"""

from __future__ import annotations

import asyncio
import os
import socket

from temporalio.client import Client
from temporalio.worker import Worker

from activities import render_markdown_to_pdf
from workflows import RenderPdfWorkflow

TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
TEMPORAL_NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "demo-poc")
TASK_QUEUE = os.environ.get("TASK_QUEUE", "render-pdf-poc")


async def main() -> None:
    identity = f"{os.getpid()}@{socket.gethostname()}"
    client = await Client.connect(
        TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE, identity=identity
    )
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[RenderPdfWorkflow],
        activities=[render_markdown_to_pdf],
    )
    print(f"[worker] identity={identity} task_queue={TASK_QUEUE} namespace={TEMPORAL_NAMESPACE}", flush=True)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
