# Native release QA requirements

This matrix is the acceptance contract, not a record of live-cloud verification.
`pending` means no release evidence yet. Automated contract tests with mocked models
cannot establish real Ollama quality or actual browser behavior. Harness reports live
outside the checkout, with case IDs, outcomes and safe failure categories. Every
pending row remains required. No customer identities or provider accounts are used.

| ID | User-visible claim/control | Happy path and failure scenario | Evidence/check | Acceptance criterion / status |
|---|---|---|---|---|
| C01 | Installer model before source | Noninteractive install; missing generation | Installed CLI install and saved profile | Source only after readiness; no fallback; pending |
| C02 | Help parity | Root/local/settings/provider help | Harness help contract plus flag comparison | Documented controls reachable; pending |
| C03 | Generation model selection | mistral installed; unknown/embedding-only model | CLI/API readiness and profile comparison | Reject wrong model and preserve old profile; pending |
| C04 | Independent listener | Start own port; occupied unrelated listener | Socket fixture + CLI | Reject collision, never adopt or stop listener; pending |
| C05 | Names and paths | Spaces in project/file/cwd; inaccessible file | Harness fixtures + installed CLI | Correct project identity and safe failure; pending |
| C06 | Installed command identity | Source cwd contains cli/logchat shadows | Installed CLI and MCP from shadow directory | Installed distribution executes; pending |
| C07 | Explicit file capture | from-start vs tail; nonexistent file | File contract + real auth demo | Existing lines only with opt-in; pending |
| C08 | Partial file line | Append fragments then newline | File watcher contract | Exactly one complete accepted event; pending |
| C09 | File rotation/truncation | Rename/replace and shorten file | File watcher contract | Resume and expose coverage gaps; pending |
| C10 | Restart catch-up/replay | Restart before checkpoint and retry | File contract + restart walkthrough | No lost accepted events or duplicate totals; pending |
| C11 | Wrapped app | stdout + stderr; exit 7 | Payments fixture + installed connect | Both streams accepted, exit status preserved; pending |
| C12 | Browser console capture | Button logs; malformed console record | Real Chrome + BrowserConsoleAdapter + push | Explicit capture reaches shared pipeline; pending |
| C13 | Docker inventory | Restricted synthetic container; absent daemon | Owned cached image fixture + adapter | Capture timestamps, safe unavailable category; pending |
| C14 | Custom NDJSON CLI | Finite timezone window; malformed/naive JSON | Executable fixture + provider normalization | Reject whole invalid window; pending |
| C15 | Cloud source authentication | Mock Railway/Vercel success; auth rejection | Executable mocked command fixtures | No token/argv/provider payload in errors; pending; never live cloud |
| C16 | Provider bounds | Normal window; cap/timeouts | Executable mocked fixture + bounded_run | No partial acceptance or cursor advancement; pending |
| C17 | Provider rate limits | Success after cooldown; 429 | Mock fixture + provider manager | Retry-after/backoff honored and cursor stable; pending |
| C18 | Reconnect/source identity | Same identity; changed identity/new-source | Provider onboarding contract + API | Preserve old cursor/history; pending |
| C19 | Hidden token/input | Prompt token; invalid source/token-env | CLI capture with synthetic canary | Input hidden; safe content-free errors; pending |
| C20 | Multiple apps/shared project | auth + payments; different project | Real installed multi-source walkthrough | One semantic pipeline, scoped context; pending |
| C21 | Logical environments | dev/preview/prod collide on error words | Real scoped queries and memory lookup | Identifiers/counts/durations never cross scope; pending |
| C22 | Selected fields | email/phone/custom code; omitted field | Compact contract + real query | Selected values survive; omissions/loss notes truthful; pending |
| C23 | Semantic and exact retrieval | Paraphrase and exact request/error IDs | Real Ollama walkthrough | Relevant supporting memory found by both; pending |
| C24 | No evidence/context only | Empty query scope; injection/solution request | Real query + answering contracts | Honest gaps; no invented totals or generated fixes; pending |
| C25 | Supporting memory permissions | Own memory; other project/environment ID | API/client contract + real scope | Unauthorized/out-of-scope evidence withheld; pending |
| C26 | Summary-only storage | Successful compact; unique raw body sentinel | SQLite/queue inspection contract | Original bodies not persisted; pending |
| C27 | Temporary capture | Generation unavailable; cap/expiry then recovery | Raw capture contract + isolated endpoint failure | Explicit bounded originals not searchable until indexed; pending |
| C28 | Failed summary cursor | Model failure then retry | Provider/file contracts | Checkpoint unchanged until accepted compact/raw intake; pending |
| C29 | Embedding outage | Durable job then unavailable embedding and retry | Scheduler contract + real recovery | Accepted compact data retained and later indexed; pending |
| C30 | Injection in log | Log asks to switch model/corrupt reference | Real/model contract | Fixed model/scope and validated membership; pending |
| C31 | Disconnect | Selected source while other apps run | Installed multi-app check | Only selected capture stops; history retained; pending |
| C32 | Instance stop/restart | Stop own instance and reopen | Installed multi-app walkthrough | Memories survive, independent apps alive; pending |
| C33 | Settings capture/UI/API auth | Valid updates; source token mutation | Settings contracts + CLI | Defaults hidden; control auth required; pending |
| B01 | Empty startup | Open fresh reader; no projects | Actual Chrome interaction | Useful empty state, no runtime error; pending |
| B02 | Project/context reader | Select project; unavailable backend | Actual click/visible result | Real selection and browser transport-outage failure passed finish iteration 5; backend process/model outages remain separate pending variants |
| B03 | Scoped query submission | Enter paraphrase and submit; empty input | Actual keyboard/click, real backend | Real dev/preview paraphrase submission and blank-input rejection passed; finish iteration 5 includes actual empty-project query |
| B04 | Context/provenance/gaps | Read query response; missing evidence | Real backend visible response | Real selected context/provenance and empty-project no-evidence response passed finish iteration 5 |
| B05 | Supporting memory list | One support button; multiple supports | Actual button -> all support list | Single, two fresh supports and zero-support panel passed finish iteration 5; selector count equals response/support count |
| B06 | Supporting memory view/copy | Select each support then copy; invalid ID | Actual controls/clipboard permission | Every selected memory ID/content copied and checked against real response in finish iteration 5; invalid-ID variant remains pending |
| B07 | Settings installed models | Load models; unavailable endpoint | Actual Settings controls | Installed list and browser transport-outage error/profile preservation passed finish iteration 5; actual Ollama endpoint outage remains pending |
| B08 | Failed model change | Submit missing model | Actual controls + profile API | Previous shared model remains selected; passed iteration 2 (real Chrome/API) |
| B09 | Capture policy | Toggle bounded retention and restore | Actual controls + API | Policy/limits persist correctly; policy toggle/restore passed iteration 2; changed-limit gate pending |
| B10 | Environment visibility | Opt in/select scope/hide | Actual controls + visible state | Hidden by default; opt-in/select preview/hide passed iteration 2; permission contracts remain required |
| B11 | Keyboard/dialog | Tab, Enter, Escape, close button | Actual keyboard focus inspection | Initial selector/empty-panel focus, Escape and return-focus passed finish iteration 5; explicit Tab/Enter cycle and close-button assertion remain pending |
| B12 | Responsive reader/settings | Desktop and 390px | Actual Chrome screenshots and dimensions | Passed finish iteration 1: populated reader, Settings, memory inspector and mobile recent-navigation controls; host review remains separate |
| B13 | Runtime errors | All main flows; labelled fake failures | Console/pageerror listeners | No pageerror in exercised real reader/Settings flows in finish iteration 5; intentional missing-model/offline network errors labelled; unexecuted variants remain pending |
| R01 | Existing regressions | Full pytest/native renderer contracts | External test report | All required tests pass; pending |
| R02 | Package release | Build + wheel/artifact audit | External build reports | Clean install/assets/metadata and no runtime artifacts; pending |
| R03 | Cleanup | Success and deliberate harness failure | Process/resource ownership checks | No owned processes/containers remain; pending |

Iteration 1 scope: reproducible fixture preparation, executable installed CLI/help and
adapter-boundary checks, harness cleanup contracts, full existing regression baseline
and package audit. Real multi-source retrieval and actual reader controls are iteration
2 release gates. Tests and fixture checks must not be reported as those gates.

## Iteration 1 evidence ledger

- C02: nine installed entry-point help checks passed; full documented-control parity
  remains pending. C14/C15/C16: 18 synthetic subprocess cases passed (including caps,
  auth failures, rate limits and timestamp rejection); manager/live-cloud gates remain
  pending. C11: fixture has both streams and exit 7; wrapped ingestion remains pending.
- C20/C21: installed real-model fixture starts five projects and three auth file sources.
  Final status indexes 7 events in preview/prod; dev stays unavailable at byte offset 0
  and accepted count 0. Real all-source queries and the cause of dev failure remain
  blocked/pending investigation, with safe status in `final-startup-status.json`.
- R01 passed: 599 tests, 177 subtests, 1 skip; required native renderer contract passed.
- R02 passed: rc9 wheel/sdist build, installed wheel audit and artifact structure audit.
- R03: foreground review fixture stopped successfully; regression cases prove API-failure
  cleanup and refusal to stop an existing instance. No demo server/container was started.
  Later multi-app/browser/container cleanup remains a required gate.
- B01–B13: actual reader browser interaction evidence pending. DOM doubles are not evidence.

Exact external evidence locations, commands and limitations are in [README](README.md).

## Iteration 2 evidence ledger (rc10)

The final real Chrome/installed Ollama walkthrough has **10 passed / 6 failed**
checks in external `fixture/evidence/walkthrough.json`. Earlier reports (8/7 and
11/5) are archived outside source; the final report tightens positive evidence and
workspace/control overflow assertions rather than accepting their earlier false
positives. No pending scenario was deleted. These are individual checks, not a
claim that every requirement in a combined ID is satisfied.

- C12/C22: host-reproduced BrowserConsoleAdapter input loss is fixed and regression
  tested: structured email/phone/custom library code and nested request ID survive
  normalization under the common 12,000-character bound. Actual Chrome clicks and
  adapter transport ran, but **real semantic intake returns HTTP 503** and indexes
  no console events. End-to-end console selected-field acceptance is blocked,
  not green based on the adapter test. Model failure diagnosis/fix remains required.
- C20/C21: real queries ran on all three colliding auth environments and the second
  console app. Preview/prod return scoped selected evidence and retain synthetic
  email/custom code. Dev still has zero accepted events and byte offset zero;
  console remains empty. Successful multi-app intake/retrieval remains blocked.
  Scope assertions exclude the other auth environment identifiers; supporting-memory
  lookup authorization, exact totals/durations, and complete multi-source acceptance
  remain separate pending gates.
- B02/B03/B04: actual project selection, scoped preview query, visible selected real
  context/provenance and empty-console response were exercised. Empty evidence now
  fails the positive context assertion. The positive preview query uses actual
  checkbox selection after explicitly opting into environment controls.
- B05/B06/B11: the real preview answer's single supporting memory was opened, selected,
  read and copied to Chrome clipboard; copied project/summary matched the visible
  reader. Focus and Escape close passed. Multiple-support coverage, Tab/Enter and
  invalid-memory failure remain pending.
- B07/B08/B09/B10: installed generation-model list loaded; missing-model save visibly
  rejected and preserved the complete saved model profile. Actual retention policy
  toggle/restore and environment opt-in/preview selection/hide passed. Original
  settings are restored through authenticated API even if a browser control fails.
  Unavailable-endpoint and changed retention-limit cases remain pending.
- B12: desktop/390px screenshots exist, but the final strengthened layout assertion
  **fails**. Document width alone masked clipping/overflow inside the workspace.
  This is a reproduced release blocker requiring a targeted layout diagnosis/fix;
  earlier screenshots also caught the navigation transition before it settled.
- B13: no unhandled JavaScript errors in exercised flows. Browser HTTP-error console
  categories are reported separately; intentional missing-model rejection and the
  browser's missing resource error do not establish a console-clean happy path.
- R01: 601 tests, 177 subtests passed, one skip; native renderer contract passed.
  R02: rc10 wheel/sdist and installed-wheel/artifact audits passed. R03: final
  walkthrough closes Chrome and stops its owned service; cleanup/port evidence is
  external. No Docker container or demo server was started.

Still pending: empty-startup/error UI variants, successful wrapped payments/custom
CLI/Docker ingestion, unavailable-generation/embedding recovery walkthroughs,
selected-source disconnect, restart/dedup acceptance and the remaining CLI contracts.
Cloud commands remain explicitly mocked; no live Railway/Vercel acceptance or
customer/provider credentials are claimed. The loop stop condition is unmet.

## Iteration 3 intake diagnosis and verification (rc10)

This iteration isolates the dev/console processing blockers. It does not close the
whole release matrix. `iteration3-exact-file-probe.jsonl` outside source reproduces
Mistral selecting a valid catalog ID twice (`f2`). All IDs were valid, but the
compact protocol rejected the duplicate and retried the same file indefinitely.
The core now removes exact repeated selections before source-grounded expansion;
unknown IDs, changed values, malformed fields, raw-body retention and bounds still
reject. Normalization is declared in the memory loss notes. Regression coverage
compares the complete evidence, event membership and exact metrics with a unique
selection, and rejects a duplicate list containing an unknown ID.

The console 503 was intake contention while dev was being retried, rather than
proof of a console model failure. Rejected push requests now expose allowlisted
processing category/phase headers. File status retains those same diagnostics
per source until a successful poll clears them, independently of the embedding
worker's global status. Tests verify content-free errors and failure/recovery with
an unchanged file cursor. The harness retries only explicit busy rejection with
identical event IDs/content; protocol/model failures remain failed checks.

- C12/C22: actual installed Chrome click, explicit browser normalization, authenticated
  real semantic intake, indexing and exact-code retrieval succeeded. Selected email,
  phone and `GlyphIndexMismatch` survive end to end; auth identifiers are absent.
- C20: auth and console are both indexed through the installed shared core, Mistral
  generation and separate nomic embeddings. Payments/Docker/custom CLI capture
  remains pending; this is two-app evidence, not all required apps.
- C21: dev/preview/prod scope now requires actual returned project/environment/source
  IDs. Inputs and independently browsed indexed memories have 2/3/4 events; selected
  context has 2/3/4 events and duration sums 85/525/3654 ms respectively. Cross-scope
  identifier assertions pass. Supporting lookup permission gates remain pending.
- B03/B04: visible real console and preview context succeeded. The walkthrough's
  support view/copy checks still permit one memory and compare to the displayed
  summary; they do **not** satisfy the host's independent multi-option ID/content
  acceptance requirement. B05/B06 multiple-support, mobile inspector and empty/error
  variants remain pending. B12 workspace clipping remains a reproduced blocker.

Evidence is external under `qa-run-20261004/evidence/iteration3-*` and
`fixture/evidence/`. See the QA README for exact commands and final counts.
No live cloud, Docker, wrapped payments or custom CLI ingestion is claimed.
The public-source export allowlist still needs the QA docs/scripts; sdist coverage
alone does not close that clean-release-archive gate. The loop stop condition is unmet.

## Finish iteration 1: mobile clipping

B12 now passes actual installed headless Chrome at 1440px and 390px. The retained
populated investigation reproduced a 501px scroll width inside a 390px workspace:
the reader grid's implicit auto column expanded to the minimum width of long
timestamps and coverage identifiers. An explicit `minmax(0, 1fr)` column and
wrapping transcript text preserve readable content and keep the composer in bounds.
Recent/new investigation actions also close the mobile drawer. The strengthened
walkthrough saves client/scroll widths and every control's horizontal bounds for
Settings, populated reader, reopened reader and memory inspector; it selects/copies
the available memory and closes with Escape on mobile as well as desktop.

The walkthrough reports **17 passed / 0 failed** individual checks. This does not
complete combined matrix rows: multiple-support expected-ID/content verification,
remaining source types, installed recovery and clean public export are still pending.
See README for commands and external evidence. Host Chrome review is separate.

## Finish iteration 3: bounded wrapped-payments gate

- C11 passed installed real-model capture: fresh stdout/stderr events both indexed,
  wrapped exit 7 preserved and no incomplete-coverage warning. The old 3-second
  transport timeout was reproduced; fresh delivery took 10.22 seconds.
- C21/C22 payments project/environment/service-scoped query passed with both fresh
  IDs, selected error code, 2 events and 300 ms, excluding auth/console identifiers.
  Same-project multi-source and explicit source-selection variants remain pending.
  Combined matrix rows are not complete where other variants remain unrun.
- R01: 607 tests, 177 subtests passed, 1 skip; native renderer passed. R02 build,
  wheel and artifact audits passed; installed built-wheel walkthrough and clean
  public export remain pending. R03 owned service cleanup passed; no browser or
  container started. Other demo types and recovery/UI gates remain required.

Run `scripts/qa_native.py --payments-walkthrough` using the dedicated installed
environment and external owned fixture. It exits nonzero for any required failed
check; exact commands and evidence are in README.

## Finish run: custom provider slice

- C14: actual installed custom finite NDJSON executable accepts four fresh events
  through the shared real-model index; invalid-window variants remain contracts.
- C18: installed reconnect preserves source ID, cursor and chunk history; forced
  installed provider poll retains one observed event. Two `--new-source` calls
  produce distinct IDs without replacing original history. Changed identity
  rejection remains contract-only, not newly verified by this walkthrough.
- C20/C21/C22: three custom sources coexist in one project and a fourth indexes in
  a separate project. Four positive service-scoped queries assert actual identity,
  selected email/error code, one event and 101/202/303/404 ms respectively, without
  other source/project markers. This is dev-only and service-filtered; mixed demo
  types in one project and explicit source-selection variants remain pending.
- Walkthrough **3 passed / 0 failed**; regressions **607 passed, 177 subtests passed,
  1 existing skip**. Native renderer/build/wheel/artifact checks pass. Owned service
  stops; port 18940 is free. No browser/container/independent producer started.

See README for commands, external evidence and the initial harness assertion
correction. Docker, installed recovery, remaining browser variants, full matrix
reconciliation, installed built-wheel walkthrough and public export remain open.

## Finish run iteration 2: wrapper drain review

C11's continued-busy/outage shutdown contracts now verify one shared drain budget,
explicit incomplete coverage for all 401 undelivered events, closed clients and no
active sender. Actual emitter acknowledgement beyond 8 seconds remains covered.
Installed payments again passes **3/3 checks**, including fresh real-model indexing
and scoped retrieval; installed outage remains separate from these unit contracts.
Regression: **609 passed, 177 subtests passed, 1 existing skip**; native renderer,
build and wheel/artifact audits pass. Cleanup confirms the owned service stopped
and port 18940 free. No other pending matrix variant is closed by this slice.

## Finish run iteration 3: Docker/shared-project evidence

- C13: installed `connect-provider docker` feeds two fresh stdout/stderr inventory
  events to the shared Mistral/nomic core; provider reports two observed events,
  cursor advancement and exact indexed **2 events / 333 ms**. Compact timestamps
  come from Docker capture despite deliberately stale application timestamps.
  The cached, networkless, mountless, read-only container drops all capabilities
  and uses no-new-privileges; its exact owned ID is removed in `finally`.
  Absent-daemon behavior remains contract-only.
- C20/C21/C22: an installed file source joins the same project with a distinct
  source ID and **1 event / 444 ms**. Both sources' history remains searchable;
  two positive service-scoped queries verify actual identities, selected
  IDs/emails/error codes, counts/durations and exclude other sources/projects.
  This dev-only execution does not close explicit source-selection variants.
- C31: installed Docker disconnect succeeds before container removal and retains
  its indexed history. The required variant with another active producer has
  not run and remains pending; no complete C31 claim is made.
- R01/R02: **612 tests, 177 subtests passed, 1 existing skip**; focused harness
  **10 passed**; native renderer, build, wheel audit (144 runtime files), artifact
  audit (2 artifacts) and whitespace checks pass. Built-wheel walkthrough and
  public-source export remain pending.
- R03: container removal, stopped owned service and free port 18940 verified.
  Failure cleanup is exercised by harness contracts, not an installed outage.

The walkthrough reports **3 passed / 0 failed** and required failures return
nonzero. Commands, safe evidence paths and remaining gates are in README.
No external blocker or production defect was found. Recovery, remaining actual
browser variants, complete matrix reconciliation and release export remain
required; the whole loop stop condition is unmet.

## Finish run iteration 4: installed basic file recovery

- C08 passes actual installed partial-fragment and complete-JSON-without-newline
  intake: offset/accepted remain zero and indexed evidence stays empty until newline;
  then exactly one event indexes. This extends the existing watcher contracts.
- C31 passes installed selected preview-source disconnect while a separate dev file
  producer stays alive and indexes another event in the same project. Preview's
  cursor, accepted count and saved memory stay unchanged despite additional output.
- C10/C32 basic restart passes: the independent dev producer writes while the owned
  instance is stopped, startup catches up, original chunk IDs survive and another
  poll retains exactly **3 events / 369 ms**. Preview remains stopped at **1 event /
  123 ms**. Pathological crash timing remains contract-only; rotation, retention and
  generation/embedding outage recovery are not covered by this walkthrough.
- Installed recovery report: **3 passed / 0 failed**. Regression: **614 tests,
  177 subtests passed, 1 existing skip**; native renderer, build and wheel/artifact
  audits pass. Owned producer/service cleanup passes. Browser variants, matrix
  reconciliation and installed built-wheel/clean public-export gates remain required.

## Finish iteration 5 evidence: actual Chrome and public export

Final `fixture/evidence/walkthrough.json`: **20 passed / 0 failed**. All checks use
the installed service, Mistral/nomic and clean headless Chrome, with no fake API.
Two separate console-button intakes in one fresh synthetic project must both be
indexed and selected by the actual paraphrase query. B05/B06 verify at least two
distinct IDs, support-list/selector count equality and every copied field against
the corresponding returned memory. B11 verifies initial focus, Escape and focus
restoration. B12 retains populated desktop/390px reader, Settings, inspector and
mobile recent-navigation evidence. Blank input, a genuinely empty project's
zero-evidence response/panel and real offline transport errors in reader/Settings
are verified separately; a browser offline flag does not establish installed
model outage/recovery.

The export now includes `docs/qa/README.md`, `docs/qa/requirements.md`,
`scripts/qa_native.py` and `scripts/qa_browser.py`. A regression checks byte-identical
QA fixtures/commands in the directory and ZIP while excluding private state. The
clean exported source builds wheel/sdist and passes wheel/artifact audits; command
reproduction uses the installed QA environment, not a claim of built-wheel execution.
Full regression: 615 tests and 177 subtests passed, one existing skip; focused
export/harness: 13 passed; native renderer passed. Exact reproduction commands and
safe evidence locations are in README's finish iteration 5 section.

Remaining variants stay required: B01 zero-project startup, B06 invalid ID, B07
actual model-endpoint outage, B11 explicit Tab/Enter and close-button assertion;
C27/C29 installed retention/generation/embedding outage recovery; all still-unreconciled
matrix variants and installed built-wheel critical walkthroughs. This slice does
not meet the full loop stop condition. Final cleanup stops owned service/Chrome;
no container or independent producer was launched.
