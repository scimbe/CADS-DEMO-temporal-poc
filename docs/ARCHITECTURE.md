# Architecture: Temporal as the reliability backbone for the bunsenbrenner.org demo portfolio

Tracking: [`scimbe/CADS-agent-marketplace#30`](https://github.com/scimbe/CADS-agent-marketplace/issues/30)
(part of #21). This document is deliverable (a); the kill-a-worker PoC in `poc/` is deliverable (b) —
see the [README](../README.md) for how to run it and `evidence/` for the recorded proof.

This is an architecture decision, not yet a running production service. Nothing described below
(`temporal.bunsenbrenner.org`, the `install temporal-worker` manifest, the generic `TemplateWorkflow`
interpreter) is deployed. What *is* real: the topology decisions, the reasoning, and a working local
reference implementation of the core reliability mechanism (§8, `evidence/`).

## 1. Topology

```
                                   ┌─────────────────────────────────────────┐
                                   │        temporal.bunsenbrenner.org        │
                                   │     (central, one shared installation)   │
                                   │                                           │
                                   │  ┌─────────────┐      ┌────────────────┐ │
                                   │  │  Temporal    │◄────►│   Postgres     │ │
                                   │  │  Server      │      │  (persistence, │ │
                                   │  │ (coordination│      │   visibility)  │ │
                                   │  │  ONLY --     │      └────────────────┘ │
                                   │  │  no Activity │                        │
                                   │  │  execution)  │                        │
                                   │  └──────┬───────┘                        │
                                   │         │ gRPC, mTLS                     │
                                   │  ┌──────┴───────┐                        │
                                   │  │  Temporal Web │  behind Keycloak/     │
                                   │  │  UI           │  Caddy /gate/check     │
                                   │  └──────────────┘  (same pattern as kali) │
                                   └─────────┬─────────────────────────────────┘
                                             │ outbound-only gRPC from every worker
                    ┌────────────────────────┼────────────────────────┐
                    │                         │                        │
          ┌─────────┴─────────┐     ┌─────────┴─────────┐    ┌─────────┴─────────┐
          │ Customer A infra   │     │ Customer B infra   │    │ Central fallback   │
          │                     │     │                     │    │ worker pool        │
          │  ct-agent-hosted    │     │  ct-agent-hosted    │    │ (explicit, docu-   │
          │  Worker(s)          │     │  Worker(s)          │    │ mented exception,  │
          │  namespace: tenant-a│     │  namespace: tenant-b│    │ see §5)            │
          │  task queue(s):     │     │  task queue(s):     │    │                    │
          │   opt-in only       │     │   opt-in only       │    │                    │
          │  no inbound port    │     │  no inbound port    │    │                    │
          └─────────────────────┘     └─────────────────────┘    └────────────────────┘
```

**Server** (Server + Postgres, no Elasticsearch): a single, lean, centrally-hosted installation.
Production reference compose file:
[`temporalio/samples-server` → `compose/docker-compose-postgres.yml`](https://github.com/temporalio/samples-server/blob/main/compose/docker-compose-postgres.yml)
(confirmed present at that path). **Not** `temporalio/docker-compose` — that repo is archived as of
2026-01-05; don't cite it. No Elasticsearch means default (SQL-backed) visibility only — advanced
visibility queries (e.g. searching by custom attributes across many workflows) are out of scope for
this backbone; this is a deliberate cost/complexity trade-off for a *coordination*-only server, not
an oversight.

Reachable at `temporal.bunsenbrenner.org`. The Temporal Web UI **has no authentication of its own** —
it must sit behind the same Keycloak/Caddy `/gate/check` pattern already used for `kali.bunsenbrenner.org`
(see `docs/ARCHITECTURE.md` in the main dev-workspace repo for that pattern). **This is a hard
requirement, not a nice-to-have: an ungated Temporal Web UI leaks every tenant's workflow inputs/outputs
across the whole shared server.** Deferred to actual deployment, not built in this PoC.

## 2. Server does coordination only

The central Temporal Server process registers **zero** Activities and runs **zero** worker processes
of its own (with one explicit, documented exception — §5). Its job is exactly what Temporal Server is
for: accept `StartWorkflowExecution`, persist history, dispatch Workflow Tasks and Activity Tasks to
whichever workers are polling, evaluate timers/retries/timeouts. No customer code, no customer data
processing, ever executes on the machine that hosts `temporal.bunsenbrenner.org`. This is the whole
point of the split: the operator running the shared backbone never becomes a compute/execution
liability for tenant workloads.

## 3. Customer-owned Workers, distributed as a marketplace manifest

Workers — the processes that actually run Activities and therefore see tenant data/code — run
**primarily on customer-owned infrastructure**, dialing *out* to `temporal.bunsenbrenner.org:7233`
over gRPC. They never accept an inbound connection. This mirrors ct-agent's own zero-trust posture
exactly (a ct-agent already never listens for inbound traffic on customer infra; the browser-plane
tunnel is the one channel out).

**Not a new distribution mechanism.** A Temporal worker is packaged and distributed the same way any
other service on this platform is: a **signed `ServiceManifest`** (see
`CADS-agent-marketplace` `crates/manifest-core/src/manifest.rs`, confirmed live in this repo), installed
via the existing `installer-engine::activate` pipeline. Concretely, an `install temporal-worker`
manifest would set:

- `installer_kind: Binary` — the worker is a single executable (built with whichever Temporal SDK the
  publisher chooses; see §5), not a Compose bundle, because there is no multi-container topology to
  express here.
- `bundle: { url, sha256, compose_file: "<path to the worker binary inside the bundle>" }` — reusing
  `BundleRef`'s existing field, per the manifest crate's own convention of reusing "the one path that
  names the entrypoint" across installer kinds.
- `env_template` — **names only, never values** (`ADR-0014`'s operator-blind philosophy, already
  enforced by `ServiceManifest`): `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE`
  (customer opts in to exactly the task queue(s) their worker should poll — no "poll everything"
  default), plus whatever Activity-specific secrets the actual workload needs. Values are always
  supplied locally, out-of-band, at install time — never embedded in the published manifest.
- `verify.script` — a real post-install check (e.g. "worker process is up and has successfully polled
  the task queue at least once"), exit-code-driven like every other manifest's verify step, not
  assumed from process start alone.

Because `InstallerKind::Binary` runs an arbitrary executable with **no static-analysis-equivalent to
`guardrails::scan_compose`** (confirmed from `manifest.rs`'s own doc comment: *"Binary's trust boundary
is narrower than Compose's... this is a real, acknowledged reduction in defense-in-depth"*), a Temporal
worker manifest inherits that same reduced defense-in-depth. It should only ever be installed from a
publisher on the same trust allowlist you'd trust with Compose bundles — nothing about being a Temporal
worker relaxes that bar.

## 4. Namespace-per-tenant

Each customer/tenant gets one isolated **Temporal Namespace** on the single shared server:

```
temporal operator namespace create --namespace <tenant-id>
```

(Verified real command in this repo's `scripts/run_demo.sh`, run against a live server — see
`evidence/`.) Namespace is Temporal's native multi-tenancy boundary: separate workflow ID spaces,
separate visibility/search results, separate retention policy, separate task queues by construction
(a task queue name is namespace-scoped). A tenant's worker only ever connects with that tenant's
namespace in `TEMPORAL_ADDRESS`/`TEMPORAL_NAMESPACE`, so a compromised or misbehaving worker cannot
enumerate or interfere with another tenant's workflows at the Temporal Server API level. Namespace
retention/visibility defaults (30-day standard retention, SQL visibility since no Elasticsearch) are
left at Temporal's own defaults for this PoC's scope; a production rollout should set an explicit,
reviewed per-tenant retention rather than inherit whatever the server's default happens to be.

## 5. Minimal-principle on the customer side, with an explicit fallback

Each customer runs a **purpose-built** worker binary — not an all-in-one "run everything" package —
opted in to exactly the task queue(s) it should serve, matching the manifest's `env_template` above.

**Fallback for customers without their own infrastructure**: a small, centrally-hosted default worker
pool. This is the **one explicit exception** to "the server never runs Activities" (§2) — call it out
precisely: it means the operator's own infrastructure runs some customers' Activity code, for
customers who have chosen not to bring their own worker capacity. This is:

- **Documented, not silent.** It shows up in this architecture doc and should show up in whatever
  customer-facing description of the offering exists — never a quiet behavior nobody chose.
- **A separate, bounded compute surface from the coordination server itself.** It is not the Temporal
  Server process gaining Activity execution; it is a distinct, minimal worker deployment the operator
  runs and is responsible for, on infrastructure separate from `temporal.bunsenbrenner.org`'s own host
  process — the coordination plane's trust boundary is unchanged.
- **The reason it doesn't quietly violate the "no origin exposes a host port" trust model**: like every
  customer worker, the fallback pool also only dials *out* to the Temporal Server — it just happens to
  be operator-owned outbound infrastructure rather than customer-owned. Same wire protocol, same
  connection direction, different owner.

## 6. Controlled workflow generation: a form, not free-form codegen

The brief's requirement is specific: workflow **definitions** must come from a controlled,
deterministic process — not free-form LLM-generated workflow code. The design:

- One generic, fixed, **audited** `TemplateWorkflow` — a single Workflow type, written once by a human,
  reviewed like any other piece of this platform's code, and never regenerated per customer.
- Its input is a JSON-schema-validated `WorkflowSpec`: an ordered list of
  `{activity_name, params, retry_overrides?}` steps.
- `TemplateWorkflow.run(spec)` interprets `spec` at runtime via a fixed dispatch table
  (`activity_name -> registered Activity function`) — no code is generated, compiled, or `eval`'d
  per customer. Two customers with different `WorkflowSpec`s run the *same* Workflow code with
  different data.
- This is the literal "parameterized template / fill-in-a-form" the brief asks for: a customer (or an
  LLM assisting a customer) fills in a *form* (the JSON `WorkflowSpec`), never writes or generates a
  new Workflow definition.

**Scope note**: this section is a design, not an implementation. The PoC's `RenderPdfWorkflow`
(`poc/workflows.py`) is a normal, hand-written, single-purpose Temporal workflow — intentionally
*not* an instance of the generic `TemplateWorkflow` interpreter described here. The PoC exists to prove
the retry/recovery mechanism (§8), which does not depend on which workflow-authoring approach produced
the workflow definition; building the interpreter itself is future work, tracked as a follow-up to
this issue.

## 7. The one path to LLM-generated code, and what actually sandboxes it

`WorkflowSpec` steps are ordinary, pre-registered Activities — **except** one special, explicitly-named
step kind: `run_generated_code`. Only that step type is allowed to execute LLM-generated Activity
*code* (as opposed to LLM-generated *parameters* for an existing Activity, which is unrestricted since
it's just data flowing into an audited function).

That Activity's implementation must invoke ct-agent's existing Binary-installer sandboxing path from
[**marketplace PR#20**](https://github.com/scimbe/CADS-agent-marketplace/pull/20) (open, not merged at
the time of writing — flagged as a dependency to watch, cross-linked from this doc). Read precisely from
the PR's actual code and description, not assumed:

- `installer-engine::sandbox` defines a `SandboxBackend` trait (`wrap_command`, `name`,
  `isolation_summary`) — a backend rewrites the command line that `process::run_bounded` executes; it
  never spawns or waits on anything itself. `run_bounded` still owns spawning, timeout, and output
  capture, unchanged. This is deliberate defense in depth: `run_bounded`'s own env-scrub protects
  against an inherited ambient secret leaking into the sandbox *launcher*; the sandbox backend's own
  scrub (bwrap's `--clearenv`/`--setenv`) is a second, independent scrub of what the *sandboxed process*
  sees.
- The only implemented backend today is Linux **`bwrap`** (`sandbox/bwrap.rs`), selected at runtime by
  `select()`, which tries every compile-time-listed candidate for the current OS in order and returns
  the first one whose **real exec probe** (not just `bwrap --version`) succeeds. macOS has a
  `sandbox_exec` backend planned as Milestone 2 — not implemented yet; any other OS has zero candidates.
- Wired into `activate.rs` step 9's Binary arm with this exact, decided policy:
  - **Sandboxed when a backend is available.**
  - **Warn-and-proceed by default when no backend is available** — the install still runs, unsandboxed,
    with a pre-execution warning.
  - **Fail-closed only when `CT_REQUIRE_BINARY_SANDBOX=1`** is set — then an unavailable sandbox aborts
    the install instead of warning and proceeding.

**Recommendation for this specific installer kind**: because `run_generated_code`'s payload is
LLM-generated code executing on the *customer's own machine* (not a human-reviewed publisher binary),
this architecture doc recommends defaulting `CT_REQUIRE_BINARY_SANDBOX=1` specifically for
`run_generated_code` activations — i.e. warn-and-proceed is an acceptable default for ordinary
publisher-supplied Binary manifests (PR#20's general decided policy), but *not* an acceptable default
here, where the "publisher" is an LLM and the reviewer is nobody. This is stated here as an explicit,
reviewable recommendation for whoever implements `run_generated_code` — not silently assumed, and not
yet enforced by any code in this repo.

## 8. "See it fail. Watch it heal." — the PoC's actual mechanism

Plain-language version of what `poc/` demonstrates and `evidence/` proves happened, for real, once:

1. A workflow starts, executing one Activity (`render_markdown_to_pdf`) on **worker A**.
2. The Activity heartbeats once a second while doing its (artificially stretched) work.
3. Worker A's OS process is `SIGKILL`ed — an actual, hard process kill, not a raised exception in the
   code. This is deliberately the strongest failure this brief asks to prove: a worker cannot even run
   cleanup code when killed this way.
4. Temporal Server, having received no heartbeat for `heartbeat_timeout` (5s), independently declares
   the activity attempt dead and schedules a retry — **with no code anywhere told this would happen**;
   this is Temporal Server's own failure-detection mechanism operating on wall-clock silence.
5. **Worker B** — a separate process, started after the kill, with no shared state with worker A except
   the Temporal Server it's both polling — picks up the retried attempt from the task queue and
   completes it.
6. The workflow completes successfully, having survived the hard kill of the process that was doing its
   work, entirely through Temporal's own mechanism — no application-level retry loop, no external
   supervisor process watching workers, no polling script babysitting anything.

`evidence/mid-recovery-describe.json` is a `temporal workflow describe -o json` snapshot captured
**while worker A is dead and worker B has not started yet** — it shows the pending retry, the heartbeat
timeout failure, and worker A's identity, entirely from Temporal Server's own state, independent of
anything this repo's scripts assert. `evidence/event-history.json` / `event-history-detailed.txt` are
the completed workflow's full event history, captured after the fact. `scripts/check_acceptance.py` is
the automated, re-runnable version of "does the evidence actually show what it claims" — see the
[README](../README.md) for real command output from an actual run. This continues the same
measured-not-asserted convention the rest of this operator's projects use for HIGH/CRITICAL or
success claims: don't believe a claim of reliability, look at the event history.

## 9. Traceability note: why this repo's reference workflow isn't the Diagram-from-description demo

Issue #30 itself suggests wiring the existing Diagram-from-description demo through Temporal as the
reference workflow. This repo deliberately does **not** do that — per this build round's explicit task
brief, the reference workflow here is a self-contained markdown→PDF example instead, with **zero
dependency on any other demo repo's readiness or API**. This lets the Temporal reliability mechanism
(the actual point of this issue) be proven and re-run in isolation, without this repo's evidence being
contingent on another repo's state. Wiring an existing demo through this same `TemplateWorkflow`
approach (§6) is a natural, valuable follow-up once the interpreter itself exists — tracked as future
work, not done here.

## Divergences and open items, gathered in one place

- Reference workflow is markdown→PDF, not Diagram-from-description (§9) — intentional, per brief.
- `temporal.bunsenbrenner.org`, its Keycloak/Caddy gate, the `install temporal-worker` manifest, and
  the `TemplateWorkflow` interpreter are all **designed here, not built** — future work.
- `run_generated_code`'s `CT_REQUIRE_BINARY_SANDBOX=1` recommendation (§7) is not yet implemented
  anywhere; PR#20 itself is open, not merged.
- Central fallback worker pool (§5) needs its own operational ownership decision (who runs it, what
  its resource limits are) before it can go live — not decided here, flagged for whoever picks this up.
