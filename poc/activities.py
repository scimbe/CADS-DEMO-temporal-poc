"""render_markdown_to_pdf -- the PoC activity.

Writes a deterministic trail of evidence to poc/.run/ as it runs, so the
harness (scripts/run_demo.sh) never has to guess timing with sleeps:

- poc/.run/attempts.log : one JSON line per activity *attempt* that actually
  started, `{"attempt": n, "pid": <os pid>, "ts": <unix time>}`. This is the
  OS-level ground truth that two different processes really executed the
  activity (see scripts/check_acceptance.py assertion 1 and 4).
- poc/.run/ready.flag   : written once, on attempt 1, containing the pid.
  The harness polls for this file's existence instead of sleeping a guessed
  duration before SIGKILLing worker A.
- poc/.run/output.pdf   : the real rendered PDF, written only once the
  (possibly retried) activity actually completes.

The only artificial slowness is the RENDER_HOLD_SECONDS heartbeat loop below;
the actual markdown->PDF conversion is well under 100ms.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import time
from pathlib import Path

import markdown
from temporalio import activity
from xhtml2pdf import pisa

RUN_DIR = Path(__file__).parent / ".run"
ATTEMPTS_LOG = RUN_DIR / "attempts.log"
READY_FLAG = RUN_DIR / "ready.flag"
OUTPUT_PDF = RUN_DIR / "output.pdf"

RENDER_HOLD_SECONDS = int(os.environ.get("RENDER_HOLD_SECONDS", "8"))


@activity.defn
async def render_markdown_to_pdf(markdown_text: str) -> str:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    info = activity.info()
    attempt = info.attempt
    pid = os.getpid()

    with ATTEMPTS_LOG.open("a") as f:
        f.write(json.dumps({"attempt": attempt, "pid": pid, "ts": time.time()}) + "\n")

    if attempt == 1:
        READY_FLAG.write_text(str(pid))

    # Artificial slowness: the ONLY delay in this activity. Heartbeating here
    # is what lets Temporal Server detect a killed worker within
    # heartbeat_timeout (5s), rather than waiting out the full
    # start_to_close_timeout (60s).
    #
    # MUST be asyncio.sleep, not time.sleep: this is an `async def` activity,
    # so it runs on the worker's asyncio event loop, which is also what
    # drives the SDK's background heartbeat-sending machinery. A blocking
    # time.sleep() here starves that event loop and silently prevents
    # heartbeats from actually reaching the server -- discovered live in this
    # session as a false heartbeat-timeout failure on an *unkilled* worker
    # (three same-pid attempts, all failing with TIMEOUT_TYPE_HEARTBEAT, no
    # SIGKILL involved). Confirmed the fix by re-running end to end.
    for _ in range(RENDER_HOLD_SECONDS):
        activity.heartbeat(f"attempt={attempt} pid={pid}")
        await asyncio.sleep(1)

    html = markdown.markdown(markdown_text)
    out = io.BytesIO()
    result = pisa.CreatePDF(io.StringIO(html), dest=out)
    if result.err:
        raise RuntimeError(f"xhtml2pdf reported {result.err} error(s) rendering PDF")

    pdf_bytes = out.getvalue()
    OUTPUT_PDF.write_bytes(pdf_bytes)

    digest = hashlib.sha256(pdf_bytes).hexdigest()
    return f"attempt={attempt} pid={pid} sha256={digest} bytes={len(pdf_bytes)}"
