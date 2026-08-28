#!/usr/bin/env bash
# End-to-end kill-a-worker demo for scimbe/CADS-agent-marketplace#30.
#
# Starts a local Temporal dev server, creates a per-tenant namespace, starts
# worker A, starts a workflow, SIGKILLs worker A mid-activity, waits past the
# heartbeat timeout, captures interim evidence showing Temporal Server has
# detected the dead worker and scheduled a retry, starts worker B, waits for
# the workflow to complete on worker B, captures final evidence, runs the
# automated acceptance check, and cleans up every process it started (exact
# PIDs only, per this project's shared-box hygiene rule).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

export PATH="$PATH:$HOME/.temporalio/bin"
export TEMPORAL_NAMESPACE="${TEMPORAL_NAMESPACE:-demo-poc}"
export TASK_QUEUE="${TASK_QUEUE:-render-pdf-poc}"
export RENDER_HOLD_SECONDS="${RENDER_HOLD_SECONDS:-8}"

RUN_DIR="$REPO_ROOT/poc/.run"
mkdir -p "$RUN_DIR" "$REPO_ROOT/evidence"
# Fresh dev server + fresh namespace every run, so this script is idempotent
# and re-runnable by a reviewer without manual cleanup between runs.
rm -f "$RUN_DIR"/attempts.log "$RUN_DIR"/ready.flag "$RUN_DIR"/output.pdf "$RUN_DIR"/dev.db

if [ ! -f "$REPO_ROOT/.venv/bin/activate" ]; then
  echo "ERROR: .venv not found. Run: python3 -m venv .venv && source .venv/bin/activate && pip install -r poc/requirements.txt" >&2
  exit 1
fi
# shellcheck disable=SC1091
source "$REPO_ROOT/.venv/bin/activate"

SERVER_PID=""
WORKER_A_PID=""
WORKER_B_PID=""

cleanup() {
  echo "--- cleanup ---"
  for p in "$WORKER_B_PID" "$WORKER_A_PID" "$SERVER_PID"; do
    if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then
      kill "$p" 2>/dev/null
      wait "$p" 2>/dev/null
    fi
  done
}
trap cleanup EXIT

echo "--- step 1: start local Temporal dev server (Server+sqlite, no external deps) ---"
temporal server start-dev \
  --db-filename "$RUN_DIR/dev.db" \
  --port 7233 \
  --ui-port 8233 \
  --log-level warn \
  > "$RUN_DIR/server.log" 2>&1 &
SERVER_PID=$!
echo "server pid=$SERVER_PID"

echo "waiting for 127.0.0.1:7233 to accept connections..."
for i in $(seq 1 60); do
  if (exec 3<>"/dev/tcp/127.0.0.1/7233") 2>/dev/null; then
    exec 3>&- 3<&-
    echo "server is up after ${i}s"
    break
  fi
  sleep 1
  if [ "$i" -eq 60 ]; then
    echo "ERROR: temporal server did not come up within 60s" >&2
    cat "$RUN_DIR/server.log" >&2
    exit 1
  fi
done

echo "--- step 2: create per-tenant namespace '$TEMPORAL_NAMESPACE' (real proof of the namespace-per-tenant mechanism) ---"
temporal operator namespace create --namespace "$TEMPORAL_NAMESPACE" --address 127.0.0.1:7233 || {
  echo "ERROR: namespace create failed" >&2
  exit 1
}
# Namespace registration can take a moment to propagate before workers can poll it.
sleep 2

echo "--- step 3: start worker A ---"
python poc/worker.py > "$RUN_DIR/worker_a.log" 2>&1 &
WORKER_A_PID=$!
echo "worker A pid=$WORKER_A_PID"
sleep 2

echo "--- step 4: start the workflow ---"
WF_ID="render-$(date +%s)"
echo "workflow id: $WF_ID"
python poc/start_workflow.py --id "$WF_ID"

echo "--- step 5: wait for worker A to signal it has actually started attempt 1 ---"
for i in $(seq 1 20); do
  if [ -f "$RUN_DIR/ready.flag" ]; then
    echo "ready.flag present after ${i}s: $(cat "$RUN_DIR/ready.flag")"
    break
  fi
  sleep 1
  if [ "$i" -eq 20 ]; then
    echo "ERROR: ready.flag never appeared -- worker A never started the activity" >&2
    cat "$RUN_DIR/worker_a.log" >&2
    exit 1
  fi
done

echo "--- step 6: SIGKILL worker A mid-activity (the injected failure) ---"
echo "killing pid=$WORKER_A_PID"
kill -9 "$WORKER_A_PID"
sleep 1
if kill -0 "$WORKER_A_PID" 2>/dev/null; then
  echo "ERROR: worker A ($WORKER_A_PID) is still alive after SIGKILL" >&2
  exit 1
fi
echo "confirmed: worker A ($WORKER_A_PID) is dead"
WORKER_A_PID_DEAD="$WORKER_A_PID"
WORKER_A_PID=""

echo "--- step 7: sleep past heartbeat_timeout (5s) so Temporal Server detects the dead worker ---"
sleep 6

echo "--- step 8: capture INTERIM evidence (worker A dead, worker B not started yet) ---"
temporal workflow describe --workflow-id "$WF_ID" --namespace "$TEMPORAL_NAMESPACE" --address 127.0.0.1:7233 -o json \
  > "evidence/mid-recovery-describe.json"
echo "wrote evidence/mid-recovery-describe.json"
echo "pendingActivities (should show attempt 2, heartbeat timeout, lastWorkerIdentity=$WORKER_A_PID_DEAD@*):"
python -c "import json,sys; d=json.load(open('evidence/mid-recovery-describe.json')); print(json.dumps(d.get('pendingActivities', d), indent=2))" || cat "evidence/mid-recovery-describe.json"

echo "--- step 9: start worker B (fresh process) ---"
python poc/worker.py > "$RUN_DIR/worker_b.log" 2>&1 &
WORKER_B_PID=$!
echo "worker B pid=$WORKER_B_PID (worker A was $WORKER_A_PID_DEAD)"

echo "--- step 10: wait for the workflow to complete on worker B ---"
python poc/wait_for_result.py --id "$WF_ID"
RESULT_EXIT=$?
if [ "$RESULT_EXIT" -ne 0 ]; then
  echo "ERROR: workflow did not complete successfully (exit $RESULT_EXIT)" >&2
  exit 1
fi

echo "--- step 11: capture FINAL evidence ---"
temporal workflow show --workflow-id "$WF_ID" --namespace "$TEMPORAL_NAMESPACE" --address 127.0.0.1:7233 --detailed \
  > "evidence/event-history-detailed.txt"
temporal workflow show --workflow-id "$WF_ID" --namespace "$TEMPORAL_NAMESPACE" --address 127.0.0.1:7233 -o json \
  > "evidence/event-history.json"
echo "wrote evidence/event-history-detailed.txt and evidence/event-history.json"

echo "--- step 12: run the automated acceptance check ---"
python scripts/check_acceptance.py "evidence/event-history.json" "$RUN_DIR/attempts.log" "$RUN_DIR/output.pdf"
ACCEPT_EXIT=$?

echo "--- step 13: cleanup (handled by trap on exit) ---"
exit $ACCEPT_EXIT
