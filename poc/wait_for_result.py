"""Blocks on a workflow's result and prints it. Exits 0 on success.

Usage: python poc/wait_for_result.py --id <workflow-id>
"""

from __future__ import annotations

import argparse
import asyncio
import os

from temporalio.client import Client

TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
TEMPORAL_NAMESPACE = os.environ.get("TEMPORAL_NAMESPACE", "demo-poc")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True, help="Workflow ID")
    args = parser.parse_args()

    client = await Client.connect(TEMPORAL_ADDRESS, namespace=TEMPORAL_NAMESPACE)
    handle = client.get_workflow_handle(args.id)
    result = await handle.result()
    print(f"result: {result}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
