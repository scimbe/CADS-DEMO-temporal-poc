# CADS-DEMO-temporal-poc

Self-hosted [Temporal](https://github.com/temporalio/temporal) as the reliability backbone for the
`bunsenbrenner.org` demo portfolio's multi-step tool-orchestration pipelines — architecture design +
a real, self-contained, kill-a-worker proof of concept.

Tracking issue: [`scimbe/CADS-agent-marketplace#30`](https://github.com/scimbe/CADS-agent-marketplace/issues/30).

## What this is

Two deliverables:

1. **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)** — the concrete distributed architecture: a lean,
   centrally-hosted Temporal Server (Server + Postgres, no Elasticsearch) that does coordination *only*;
   Workers that run on customer-owned infrastructure, connecting outbound-only, distributed as a
   marketplace-installable manifest; one Temporal Namespace per tenant; a controlled,
   parameterized-template approach to workflow generation (no free-form LLM-generated workflow code);
   and the one path that lets real LLM-generated *Activity* code run at all — inside the bwrap sandbox
   from [marketplace PR#20](https://github.com/scimbe/CADS-agent-marketplace/pull/20).
2. **`poc/`** — a small, self-contained reference workflow (render a markdown fixture to PDF) run
   against a real local Temporal dev server, that demonstrably survives its worker being `SIGKILL`ed
   mid-execution and automatically resumes on a second worker process. `evidence/` holds the real
   `temporal workflow describe`/`show` output from an actual run proving this happened.

This repo does **not** stand up `temporal.bunsenbrenner.org`, the marketplace manifest, or the generic
workflow interpreter — those are designed in the architecture doc and explicitly left as future work
(see the doc's closing section). What's built and proven here is the reliability mechanism itself.

## What's real vs. what's a known limitation

**Real, run and verified in this repo:**
- A real local Temporal dev server (Temporal Server + sqlite persistence, `temporal server start-dev`
  — no Docker, no Postgres, no mocks) is started, used, and torn down by `scripts/run_demo.sh`.
- A real per-tenant namespace is created against that live server
  (`temporal operator namespace create`).
- A real OS process (`python poc/worker.py`) is started, actually begins executing the Activity, and is
  actually `SIGKILL`ed (`kill -9`) mid-execution — not a simulated or in-code failure.
- Temporal Server's own heartbeat-timeout detection (not any code in this repo) declares the killed
  worker's attempt dead and schedules a retry.
- A second, independent OS process picks up the retry and completes the workflow.
- The evidence in `evidence/` is real captured output from `temporal workflow describe -o json` and
  `temporal workflow show [--detailed|-o json]` against that live run — not hand-written or asserted.
- `scripts/check_acceptance.py` automatically verifies, from that evidence file plus the activity's own
  attempts log, that the retry genuinely happened, was genuinely caused by the heartbeat timeout, and
  was genuinely picked up by a different OS process than the one that was killed.
- All 4 unit/SDK tests in `tests/` pass for real (output below).

**Known limitations / honest gaps:**
- `docs/ARCHITECTURE.md` sections 1, 3, 4 (partially), 5, 6, 7 describe infrastructure (the shared
  server's public deployment, the marketplace manifest, the `TemplateWorkflow` interpreter, the
  `run_generated_code` sandbox wiring) that is **designed, not implemented**. Read the doc's own
  "Divergences and open items" section for the complete list — it's written to be checked against, not
  taken on faith.
- The reference workflow is a self-contained markdown→PDF example, **not** the Diagram-from-description
  demo issue #30 itself suggests — a deliberate divergence per this build round's brief, explained in
  `docs/ARCHITECTURE.md` §9.
- The PoC's `RenderPdfWorkflow` is a normal hand-written Temporal workflow, **not** an instance of the
  generic `TemplateWorkflow` form-driven interpreter the architecture doc argues for (§6) — the PoC
  proves the retry/recovery mechanism; the interpreter itself is future work.
- This build round's shared litellm-proxy key (`$HOME/dev-workspace-scratch/demo-portfolio-llm.env`)
  was **not used** by this PoC — nothing in the reference workflow calls an LLM (the failure/recovery
  demo doesn't need one, and the brief's LLM-generated-Activity-code path (§7 of the architecture doc)
  is explicitly designed-not-built in this issue). Noting this plainly rather than pretending an LLM
  call happened.
- `temporal server start-dev` is an explicitly non-production single-process dev server (its own
  `--help` text says so) — this is the correct choice for "a local, lightweight self-hosted Temporal dev
  setup" per the brief, and is a deliberately different deployment shape from the production
  Server+Postgres topology `docs/ARCHITECTURE.md` §1 describes for `temporal.bunsenbrenner.org`.

## Repo layout

```
docs/ARCHITECTURE.md       deliverable (a) -- the full architecture
poc/
  workflows.py              RenderPdfWorkflow
  activities.py              render_markdown_to_pdf (heartbeats, writes evidence trail)
  worker.py                   runs a Temporal worker process
  start_workflow.py           starts a workflow, returns immediately
  wait_for_result.py          blocks on a workflow's result
  fixtures/sample.md          the markdown rendered in the demo
  requirements.txt            pinned: temporalio, Markdown, xhtml2pdf
  .run/                       gitignored -- dev.db, logs, attempts.log, ready.flag, output.pdf
evidence/
  mid-recovery-describe.json  temporal workflow describe -o json, captured DURING the retry gap
  event-history-detailed.txt  temporal workflow show --detailed, after completion
  event-history.json          temporal workflow show -o json, after completion
scripts/
  run_demo.sh                 the end-to-end kill-a-worker demo
  check_acceptance.py         automated pass/fail check against the evidence
tests/
  test_render_activity.py     pure unit test of the markdown->PDF logic, no Temporal needed
  test_workflow_retry.py      SDK time-skipping test of the RetryPolicy wiring
requirements-dev.txt          pytest + pytest-asyncio, for tests/ only
```

## Prerequisites

- Python 3.10+ (developed/verified on 3.12.3).
- The [Temporal CLI](https://docs.temporal.io/cli) (`temporal server start-dev`, `temporal operator`,
  `temporal workflow`). Install with no sudo required:
  ```
  curl -sSf https://temporal.download/cli.sh | sh
  export PATH="$PATH:$HOME/.temporalio/bin"
  ```
- No Docker, no Postgres, no Elasticsearch needed for the PoC — the dev server persists to a local
  sqlite file.

## Running the demo

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r poc/requirements.txt

export PATH="$PATH:$HOME/.temporalio/bin"
bash scripts/run_demo.sh
```

The script is fully self-contained: it starts the dev server, creates a namespace, starts worker A,
starts the workflow, waits for the activity to actually begin, `SIGKILL`s worker A, waits past the
heartbeat timeout, captures interim evidence, starts worker B, waits for completion, captures final
evidence, runs the acceptance check, and cleans up every process it started (exact PIDs, never a broad
`pkill`) — leaving ports 7233/8233 free again. Re-run it as many times as you like; each run starts from
a fresh dev-server database and namespace.

### Real output from an actual run (2026-08-28, this host)

```
--- step 6: SIGKILL worker A mid-activity (the injected failure) ---
killing pid=1887508
confirmed: worker A (1887508) is dead
--- step 7: sleep past heartbeat_timeout (5s) so Temporal Server detects the dead worker ---
--- step 8: capture INTERIM evidence (worker A dead, worker B not started yet) ---
pendingActivities (should show attempt 2, heartbeat timeout, lastWorkerIdentity=1887508@*):
[
  {
    "activityId": "1",
    "activityType": { "name": "render_markdown_to_pdf" },
    "state": "PENDING_ACTIVITY_STATE_SCHEDULED",
    "attempt": 2,
    "maximumAttempts": 3,
    "lastFailure": {
      "message": "activity Heartbeat timeout",
      "source": "Server",
      "timeoutFailureInfo": { "timeoutType": "TIMEOUT_TYPE_HEARTBEAT" }
    },
    "lastWorkerIdentity": "1887508@cads-lambda",
    ...
  }
]
--- step 9: start worker B (fresh process) ---
worker B pid=1890345 (worker A was 1887508)
--- step 10: wait for the workflow to complete on worker B ---
result: attempt=2 pid=1890345 sha256=ea36f50dd79474ab4c5471253d23c119e5851400e68129f742434b389d9f79cf bytes=2458
--- step 11: capture FINAL evidence ---
wrote evidence/event-history-detailed.txt and evidence/event-history.json
--- step 12: run the automated acceptance check ---
PASS: attempt 2 recovered on pid 1890345 after heartbeat timeout from pid 1887508; 2458-byte PDF written; workflow completed.
```

The final `event-history.json` (checked into `evidence/`) has exactly 11 events (Temporal's
`workflow show` collapses retried attempts into a single Activity row across all attempts — the retry
proof is in event 6's *attributes*, not in an extra event):

```
1  EVENT_TYPE_WORKFLOW_EXECUTION_STARTED
2  EVENT_TYPE_WORKFLOW_TASK_SCHEDULED
3  EVENT_TYPE_WORKFLOW_TASK_STARTED
4  EVENT_TYPE_WORKFLOW_TASK_COMPLETED
5  EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
6  EVENT_TYPE_ACTIVITY_TASK_STARTED   <- attempt=2, identity=1890345@cads-lambda,
                                         lastFailure.timeoutFailureInfo.timeoutType=TIMEOUT_TYPE_HEARTBEAT
7  EVENT_TYPE_ACTIVITY_TASK_COMPLETED
8  EVENT_TYPE_WORKFLOW_TASK_SCHEDULED
9  EVENT_TYPE_WORKFLOW_TASK_STARTED
10 EVENT_TYPE_WORKFLOW_TASK_COMPLETED
11 EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED
```

### Running the tests

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

Real output from this repo:

```
tests/test_render_activity.py::test_renders_fixture_to_valid_pdf PASSED  [ 25%]
tests/test_render_activity.py::test_renders_simple_markdown_features PASSED [ 50%]
tests/test_workflow_retry.py::test_activity_retried_then_succeeds PASSED [ 75%]
tests/test_workflow_retry.py::test_activity_exhausts_retries_and_workflow_fails PASSED [100%]

============================== 4 passed in 1.54s ===============================
```

`test_workflow_retry.py` downloads a Temporal time-skipping test-server binary on first run
(`temporalio.testing.WorkflowEnvironment.start_time_skipping()`) — needs network access once; cached
locally after that.

## A real bug found and fixed while building this

The first version of `render_markdown_to_pdf` used `time.sleep(1)` inside its heartbeat loop. Because
the activity is declared `async def`, it runs on the worker's asyncio event loop — the same loop that
drives the SDK's background heartbeat-sending machinery. A blocking `time.sleep()` starves that loop, so
heartbeats never actually reached the server even though `activity.heartbeat()` was being called every
second. Result: **the workflow failed with a heartbeat timeout even when worker A was never killed** —
three same-pid attempts, all failing, confirmed live in this repo before the fix. Fixed by switching to
`await asyncio.sleep(1)` (see the comment in `poc/activities.py`). Re-ran end-to-end after the fix,
twice, both clean passes (see `evidence/` and the output above) — this is exactly the kind of thing
`docs/ARCHITECTURE.md` §8 means by "measured, not asserted": the bug was only visible because real
command output was checked, not because the design looked right on paper.

## Part of the bunsenbrenner.org demo portfolio

This PoC is published as a signed manifest on the live registry, alongside the rest of the
[bunsenbrenner.org](https://bunsenbrenner.org) demo portfolio. Verified present on
[`registry.bunsenbrenner.org/manifests`](https://registry.bunsenbrenner.org/manifests) as
`temporal-poc` v0.1.1, carrying a manifest `signature` and `publisher_pubkey` (checked at
activation time). Its guardrail verdict is `binary-kind`: no static compose-YAML scan applies to
this installer kind, so trust rests on the publisher-pubkey allowlist rather than a bundle scan.

Note that `temporal.bunsenbrenner.org` itself is **designed, not deployed** (see the limitations
section above) — as of this writing the host does not resolve. The registry entry is the
installable manifest, not a running shared server.

## License / scope note

This is an internal architecture/PoC repo for the `bunsenbrenner.org` demo portfolio build round, not a
general-purpose Temporal starter kit.
