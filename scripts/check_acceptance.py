#!/usr/bin/env python3
"""Acceptance check for the kill-a-worker PoC (scimbe/CADS-agent-marketplace#30).

Loads the real Temporal event history (evidence/event-history.json, produced
by `temporal workflow show -o json`) and the activity's own attempts log
(poc/.run/attempts.log) and asserts, in order:

  1. attempts.log contains at least two distinct pids (two OS processes
     really executed the activity).
  2. event history has an EVENT_TYPE_ACTIVITY_TASK_STARTED event with
     attempt >= 2.
  3. that event's lastFailure.timeoutFailureInfo.timeoutType ==
     "TIMEOUT_TYPE_HEARTBEAT" (the retry was caused by the killed worker
     going silent, not a generic app error).
  4. that event's identity matches attempts.log's second pid, and differs
     from the first (ties Temporal-side evidence back to the OS-level kill).
  5. a terminal EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED event exists.
  6. the output PDF exists, starts with %PDF-, non-zero size.

Exit 0 with a one-line PASS summary only if all six hold; otherwise exit 1
naming which assertion failed.

Usage: python scripts/check_acceptance.py evidence/event-history.json poc/.run/attempts.log [poc/.run/output.pdf]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)

    history_path = Path(sys.argv[1])
    attempts_path = Path(sys.argv[2])
    pdf_path = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("poc/.run/output.pdf")

    if not history_path.exists():
        fail(f"event history file not found: {history_path}")
    if not attempts_path.exists():
        fail(f"attempts log not found: {attempts_path}")

    history = json.loads(history_path.read_text())
    events = history.get("events", history) if isinstance(history, dict) else history

    attempts_lines = [
        json.loads(line) for line in attempts_path.read_text().splitlines() if line.strip()
    ]
    pids = [str(a["pid"]) for a in attempts_lines]
    distinct_pids = list(dict.fromkeys(pids))  # preserve order, de-dupe

    # 1. two distinct OS processes really ran the activity
    if len(distinct_pids) < 2:
        fail(
            f"attempts.log shows only {len(distinct_pids)} distinct pid(s) "
            f"({distinct_pids}) -- expected >= 2 (a killed worker + a surviving worker)"
        )
    pid_a, pid_b = distinct_pids[0], distinct_pids[1]

    # 2 & 3: find the ACTIVITY_TASK_STARTED event for attempt >= 2 caused by heartbeat timeout
    retried_started_event = None
    for ev in events:
        if ev.get("eventType") != "EVENT_TYPE_ACTIVITY_TASK_STARTED":
            continue
        attrs = ev.get("activityTaskStartedEventAttributes", {})
        if attrs.get("attempt", 1) >= 2:
            retried_started_event = ev
            break

    if retried_started_event is None:
        fail(
            "no EVENT_TYPE_ACTIVITY_TASK_STARTED event with attempt >= 2 found in "
            "event history -- no retry happened"
        )

    attrs = retried_started_event["activityTaskStartedEventAttributes"]
    timeout_type = (
        attrs.get("lastFailure", {})
        .get("timeoutFailureInfo", {})
        .get("timeoutType")
    )
    if timeout_type != "TIMEOUT_TYPE_HEARTBEAT":
        fail(
            f"retried activity's lastFailure.timeoutFailureInfo.timeoutType was "
            f"{timeout_type!r}, expected 'TIMEOUT_TYPE_HEARTBEAT' -- retry was not "
            f"caused by the killed worker going silent"
        )

    # 4. identity of the retried attempt matches the SECOND pid from attempts.log,
    #    and differs from the first
    identity = attrs.get("identity", "")
    identity_pid = identity.split("@", 1)[0] if "@" in identity else identity
    if identity_pid != pid_b:
        fail(
            f"retried activity's identity pid ({identity_pid!r} from {identity!r}) "
            f"does not match attempts.log's second pid ({pid_b!r})"
        )
    if identity_pid == pid_a:
        fail(
            f"retried activity's identity pid ({identity_pid!r}) is the SAME as the "
            f"first (killed) pid ({pid_a!r}) -- expected a different worker process"
        )

    # 5. terminal completion event
    completed = any(
        ev.get("eventType") == "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED" for ev in events
    )
    if not completed:
        fail("no EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED event found -- workflow did not complete")

    # 6. real PDF output
    if not pdf_path.exists():
        fail(f"output PDF not found at {pdf_path}")
    pdf_bytes = pdf_path.read_bytes()
    if not pdf_bytes.startswith(b"%PDF-"):
        fail(f"{pdf_path} does not start with %PDF- (got {pdf_bytes[:16]!r})")
    if len(pdf_bytes) == 0:
        fail(f"{pdf_path} is zero bytes")

    workflow_id = "?"
    for ev in events:
        we = ev.get("workflowExecutionStartedEventAttributes")
        if we:
            workflow_id = history.get("workflowId", "?") if isinstance(history, dict) else "?"
            break

    print(
        f"PASS: attempt {attrs.get('attempt')} recovered on pid {pid_b} "
        f"after heartbeat timeout from pid {pid_a}; "
        f"{len(pdf_bytes)}-byte PDF written; workflow completed."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
