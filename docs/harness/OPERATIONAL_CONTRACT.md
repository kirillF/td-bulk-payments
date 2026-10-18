# Operational contract

## Applicability

Routine coordinator budgeting is an agent responsibility, not an operator approval
gate. Choose bounded command timeouts and checks/reviews needed to finish the agreed
outcome; do not ask the operator to approve time pools, review slots, or routine retry
counts. Preserve actual consumption and failed-attempt history without resets. Respect
any hard cap explicitly supplied by the operator and any separately chargeable external
action. Existing project worker/repair counters and isolation/check gates still apply.
Ask for a new decision only when scope, authority, external consequences, or an explicit
operator constraint changes. Historical agent-proposed budget approval ceremonies do
not govern routine continuation after this instruction.

Ordinary project work follows README.md → Normal project work: one outcome and finite
completion allowance,then direct implementation and routine corrections. The formal
protocol below applies to explicitly requested gated runs,acceptance and releases.
A blocked maintenance campaign does not block direct work;existing records remain intact.


`harness.py` orchestrates; `task_model.py` defines canonical task intent; `contract_checks.py` validates plans and gates; `codex_review.py` validates static review. Git/process adapters enforce isolation and ownership. `review_paths.py` owns review exclusions. The shared source’s `runtime.json` alone pins qualified versions, models and limits. README documents commands; the task template guides authoring. Historical proposals are not current contracts.

`AGENTS.md` is a lean repository entrypoint: real development/check commands, canonical document pointers and essential durable guardrails. Add stable, non-obvious repository facts only when useful; keep detailed standards/workflows in their owning files and load task-specific design on demand. Run IDs, approval hashes, holds and recovery narratives belong in Beads and `.harness/`, never in this always-loaded file. Brevity does not replace or relax executable approval/dispatch/completion gates. Existing user-authored guidance is preserved in previews and remains subject to operator review.

## Authority and installation

Without an explicit preview output path, foundation staging uses a private harness-owned temporary directory. It is removed only after installation completes; failures, interruptions and unexpected extra content retain it. Explicit preview destinations remain persistent. Cleanup must preserve the external approval store and the installed foundation/authorization records.

Only explicit installation activates this workflow. No global settings or skills change. Clarify stack, conventions, tools, deployment, testing, autonomy and invariants before scaffolding. The confirmed preview binds destination, existing preimages, final bytes, setup/start/check argv, service preference and disposable recovery caches. Scaffold source keeps selected replacement bytes; automatic merging applies only to guidance/configuration. Refreshing edited scaffold source updates its hashes and compact foundation summary. The foundation reference lists scaffold hashes and commands; approved preview files retain the exact source bytes. Ownership cannot overlap those caches. Scaffold replacements retain their selected final bytes; automatic merging applies only to guidance/configuration. Refreshing edited scaffold source updates its hashes and compact foundation summary. Merge existing guidance/config in preview and refresh inventories before confirmation.

Before requesting foundation approval, hold an explicit architecture discussion with the operator. Present the recommended architecture and a meaningful alternative when one exists, their tradeoffs, system boundaries, state/data ownership, integrations, deployment and verification constraints that affect the stack. Ask for acceptance or revisions and wait for the response; a document written by the agent or silence does not constitute discussion. Resolve consequential questions before preparing the approval request. Record the resulting architecture and agreed constraints in the existing foundation profile's `stack` and `conventions` fields; use the other profile fields for their existing purposes. No extra profile field, document or approval stage is required. Conversation feedback resolves design intent; only the existing audited foundation confirmation authorizes installation.

The human owns foundation, feature/amendment, exceptions, recovery attestations, acceptance and release decisions. The coordinator owns Beads mutations, claims, checks and integration. Workers/checks cannot access canonical project or approval state. Planning/design and implementation may use advisory memories; approved artifacts take precedence. Review receives none; its current task facts include exact approved scenario references and selected checks.

The trusted launcher selects an external private, owned, nonsymlinked approval store, defaulting beside the shared executable. Target configuration cannot select it; stores inside targets are rejected. `confirm` displays captured bytes, requires a terminal and APPROVE, then publishes an immutable payload, audit event and receipt. Events include UTC, OS identity, purpose/project/scope and exact payload/evidence digests; cancellations and repeats are separate events. Verification requires the exact payload, linked event/receipt and current bindings. Missing or altered evidence fails closed. No signing/key provisioning is required. The operator decides; the coordinator may record explicit chat consent as described below. Terminal input is supplied only by the operator; fixture simulations use disposable stores only.

This is a trusted-host audit, not cryptographic proof against same-user host processes. Back up the external store. Legacy signing installations, existing Beads databases and changed runtime/store bindings require operator-qualified migration; no automatic reapproval or destructive reset exists.

A no-follow repository lock covers installation and normal operations. Workflow creation persists intent and deterministic native identities before mutation; interrupted creation resumes those same issues. Interrupted planning must finish the saved document/base before a changed proposal, and new slice identities are deterministic. Installation rejects staged user work, substituted paths and external Git metadata, journals phases, resumes only identical approved input and imports foundation provenance into Beads. Approved/effective runtime manifests must match; a completed install requires every owned final byte.

## Plan and approve

`start` creates an epic, four preparation tasks and approval/integration/review/acceptance gates before design work. Product requirements define FR/NFR, scope, negative behavior, invariants and Gherkin scenarios. Architecture defines boundaries/data/integrations and consequential ADR/RFC decisions. Program design defines important signatures, types, responsibilities, organization and test boundaries. Vertical slices define bounded independently verifiable outcomes, ownership and dependencies. Keep documents concise; architecture is a document of its own, and program design produces an API document plus a code-design document. Avoid speculative implementation bodies.

Beads is the durable work ledger throughout planning and delivery. Before acting, the coordinator selects the existing epic/task for the bounded outcome and claims it; create missing work before starting. Workers receive the selected task and never mutate Beads themselves. Reuse the four preparation tasks for stage work rather than creating an issue for every message or tool call. Keep detailed requirements/design in the referenced artifacts.

As work proceeds, append concise task notes with artifact links, resolved decisions, open questions, evidence and the next action. Update progress before yielding or handing off; awaiting HITL is blocked, not completed. For preparation notes/status, use explicit native IDs returned by `start`, for example `bd -C TARGET --actor harness-coordinator update NATIVE_ID --claim --append-notes TEXT --json`; use `--status blocked --append-notes TEXT` when awaiting a decision. After the awaited feedback is resolved, record its outcome and reopen only that preparation task with `bd -C TARGET --actor harness-coordinator update NATIVE_ID --if-status blocked --status open --append-notes TEXT --json`, then claim it before further work. Beads refuses `--claim` on a blocked task. If the guarded update fails, reread the task and stop rather than force its state. This bookkeeping never resolves a consequential decision or grants implementation authority by itself. These coordinator updates do not replace canonical definitions or grant authority. Stage delivery/review progress is recorded immediately; `plan` validates the complete package and closes preparation tasks. Other completion statuses follow their existing coordinator gates. Newly discovered consequential work returns to the existing amended-plan/approval path before implementation.

Before approved foundation installation, persist discovery in the managed setup/session records; installation imports foundation provenance into Beads. Do not initialize a target tracker or change its files before foundation authority. Shared-harness maintenance uses its own repository-local Beads ledger and never the paused project's tracker.

Review is part of each of the four preparation stages, not a fifth stage or an extra confirmation ceremony. Present each stage's concrete artifacts, explain recommendations/material alternatives, ask for acceptance or revisions and wait for the operator's response before relying on consequential decisions in the next stage. Reuse explicit feedback already supplied; silence, generated headings and a generic continuation request do not prove review.

| Planning stage | Artifacts the operator reviews |
| --- | --- |
| Product | Identified FR and NFR, measurable thresholds, invariants, scope/exclusions and concrete Given/When/Then Gherkin scenarios, including negative/failure cases. Use `.feature` files or actual Gherkin in a shared document; scenario IDs or prose summaries alone are insufficient. |
| Architecture | An architecture document with boundaries, state/data ownership, integrations, deployment and material alternatives/tradeoffs; ADR/RFC only for consequential decisions. |
| Program design | An API document with explicit public API signatures, types, contracts, errors and compatibility, plus a code-design document covering modules/objects, responsibilities, organization, ownership/lifetimes, timing and verification boundaries where applicable. Resolve consequential choices without exhaustively specifying private implementation details. |
| Vertical slices | Beads epic success criteria and full canonical tasks: goal, FR/NFR/invariant/scenario references, acceptance criteria, ownership, dependencies and checks. Walk the operator through the requirement → scenario/check → task mapping, including NFR work. |

Record resolved intent in the existing hash-bound artifacts referenced by `design_layers`, `artifacts` and task `design`/`context`; each `design_layers` entry accepts one path or a nonempty path list. For example, `architecture` names `architecture.md`, and `program` lists `api.md` and `code-design.md`. Every named path must be in the hash-bound `artifacts` inventory. Existing single-path contracts remain readable; new plans produce the required distinct documents. Keep dialogue/history and conversational approval claims out of clean reviewer inputs. Gherkin describes required behavior during planning; executable bindings and passing results are implementation/verification evidence, never assumed present at approval.

After feedback is resolved, `plan` validates and projects those same canonical artifacts/tasks into Beads and the final approval request. The request includes the epic, four stage/artifact/preparation-task references, complete slice definitions with native Beads IDs, scenarios and exact check definitions. Approval import compares the authenticated display with that canonical package before changing authority or Beads; mismatches require a corrected proposal and explicit confirmation. Already imported historical approvals are retained without retroactive migration. Preparation closure records delivered/validated artifacts, not proof that the human reviewed them. Before requesting `confirm` or `request-confirmation`, present the resolved four-stage package and resolve its design questions. Reuse recorded stage feedback. For a narrow amendment with no unresolved design choice, explain the exact delta, reason and impact, then open the audited confirmation directly; the terminal review/APPROVE is the explicit decision, without a preceding conversational yes/no. `APPROVE` at the existing audited feature/amendment gate confirms review of that package and authorizes only its bound intent; discussion alone cannot authorize implementation. There are no per-stage audit events or agent-authored `reviewed=true` bypass flags.

Discussion quality is a trusted operator/planner obligation; validators enforce current artifact/task bindings, coverage and audited authority, not semantic proof of conversation. Preserve prior approvals as historical evidence. Do not automatically invalidate or migrate an existing project. If the operator identifies incomplete planning review, stop affected implementation, preserve partial work and approvals, revise the four-stage artifacts and use the existing audited amendment before resuming. Consequential choices discovered during implementation follow the same HITL rule.

Canonical task fields and deterministic Beads/review projections have one source. Preparation tasks bind suggested output paths to actual artifacts/IDs at planning. Gate `coordinator.*` names identify completion predicates; integration binds project checks and cannot become worker implementation. `workflow.` IDs are reserved. Repairs are attempts under a slice, not new intent.

During vertical-slice planning, inspect the actual source and build/check entry points for each outcome. Include all expected edits in `ownership`: implementation, manifests/locks, adapters, tool scripts and tests as needed. Files in `context` are readable references, not write permission; another task's ownership is not inherited through a dependency. Use `check-scope FEATURE_JSON TASK PATH...` with the concrete intended edit set before final approval and again before dispatch if integration needs changed. Record its result and paths in the existing Beads notes. It deterministically checks declared paths against ownership without mutation, setup or worker launch. It cannot infer missing edits from prose or prove design feasibility; the coordinator must inspect those dependencies. A missing path is corrected before approval or proposed as an audited amendment afterward; never silently broaden scope or retry the same blocked worker.

Before tracker mutation, validate exact fields, identifiers, paths, regular artifact bytes, finite limits, resolved/acyclic dependencies and executable checks. Every feature obligation AND each slice's own requirement/invariant/scenario IDs must be covered by its selected checks. No executable slice carries an unresolved consequential decision, owns control metadata/aliases or selects source/context conflicting with reserved review paths. Newly created source is checked again during collection/review.

Approval binds artifacts, canonical task intent, checks, environment and limits. Changed requirements/interfaces/invariants/ownership/budgets return to explicit HITL. Routine reversible internal choices may proceed. Consequential BLOCKED requests are saved before collection/projection and block dispatch/integration/review/acceptance regardless of Beads status. Resolution binds the exact request digest and coordinator-owned run ID. Amendments carry pending occurrences; repeated text in another run needs fresh confirmation. Exact authenticated resolutions remain beside runs; reconciliation restores lost projections.

Plans retain at least one executable slice. Omitting task IDs in an amendment proposes retirement. The approval request displays native IDs, prior task/contract digests and current scope. Tasks stay blocked, not retired, until confirmed approval. Import resolves outstanding decisions, closes retired tasks with the confirmation binding, removes active integration edges and archives the native ID mapping. History remains; retired IDs cannot be reused. Retrying interrupted import is idempotent. No additional stage is introduced.

## Execute and verify

### Implementation discovery and routine completion

Approval fixes requirements, invariants, public interfaces, compatibility, owned paths, check obligations and resource limits. Private code structure and algorithms remain provisional unless explicitly constrained. Implementation agents may inspect assigned source and readable context, test local assumptions and revise private design within those boundaries. The coordinator supplies the complete approved input; implementation discovery refines how to meet it rather than defining new product intent.

Use a small executable slice, focused checks and reviewable diffs to expose design assumptions early. Surface consequential discoveries while they are still cheap to resolve. Record concise findings and evidence in the task; update working design notes to reflect verified decisions without rewriting approved artifacts or their history. An edit to a hash-bound artifact follows its existing amendment gate.

Routine corrections include defects and fixtures within approved ownership that restore the same accepted behavior. The coordinator may continue justified corrections, focused checks, clean review and required qualification without a new approval for each fixture when the original audited package includes the relevant finite repair, review and execution allowances and the current repair-routing rules permit it. Every run consumes its existing allowance; retain failed results and attempt history. An unchanged failed or incomplete result does not by itself authorize a rerun; existing retry eligibility and triage still govern. Missing allowances or exhausted limits remain blocked.

Return consequential changes to the existing audited amendment: requirements, public interfaces, compatibility, invariants, ownership, authority, check obligations or resource limits. Local discovery grants no additional paths, weaker assertions, approval or budget. Existing decision, repair, repeated-review triage, acceptance and release gates remain in force.

### Completion allowances and early feedback

Approve the outcome, all expected owned edits, invariants, check obligations and a practical finite completion allowance together. State initial implementation batches separately from eligible correction rounds. A correction round groups edits addressing the same verified defect/oracle; it is not every edit or tool call. Count focused invocations, complete reviews, native batches and full qualification attempts independently, with shared pools across candidate revisions and continuation children. Each failed/INCOMPLETE launch remains charged. Prospective guidance never changes an older approval's counts or runtime repair eligibility.

Run cheap deterministic checks first where existing permissions permit, then correct eligible owned defects before expensive review. In-scope fixes restoring accepted behavior do not require per-fixture consent while approved allowances remain. Changed authority, observable requirements, public interfaces, ownership, check strength or limits require the existing audited amendment. A new candidate needs fresh applicable review; no unchanged-failure blind rerun, counter reset or stale/partial PASS aggregation. Complete current-source review still precedes privileged native checks, followed by required exhaustive qualification. Guidance-only review cannot qualify a release.

Keep all pools/counts/deadlines explicit. Before launch, the operation bound and protected verification/handoff reserve must fit. Preserve the original start and cumulative active work; exclude only recorded operator waiting with no active workload. Exhaustion stops affected execution with exact evidence and next action, never an unexplained warning. Four-stage review remains; a small correction references accepted architecture/API rather than replaying stages. Private implementation notes may follow verified code without rewriting immutable approved artifacts.

Failure feedback names the invariant/scenario, expected/actual evidence and one supported fix/check or the precise missing-evidence blocker. A recurring, stable, reproducible finding merits an existing-scope regression only when coverage is missing; do not add another permanent instruction or duplicate test for every incident. Runtime guards and new files need their own approved ownership. Keep entry guidance short and route detail to its canonical owner.

At completion, report the descriptive existing-record measures in docs/qualification.md using current audit/run/review/qualification evidence and Beads notes. Name the outcome/window and data coverage. Do not invent missing timestamps, human effort, tokens, cost or defect rates. Metrics grant no authority and add no acceptance threshold, measurement run, service or framework.

A paused session may retain incomplete/dirty work and failed evidence. Label it accurately and provide a recoverable mandatory handoff; do not delete work, roll back or claim GREEN merely to leave a superficially clean session. Completion, qualified candidate, promotion and project acceptance remain distinct states.

One operation/worker runs per repository. Implementation is fresh `codex exec` using runtime-selected low effort, fixed result schema, current task/design/check definitions and an explicit memory directory. User config/rules, apps/plugins/hooks and nested agents are disabled. Current project guidance/local skills remain available; `.codex` and canonical tracker/runtime state are excluded from checkout. Setup runs before narrow authentication is copied through no-follow writes.

Qualified isolation is macOS sandbox-exec with sealed process-label discovery. Worker/check writes stay in temporary roots; reads permit root/harness/toolchain/system paths and deny canonical project plus selected/default approval stores. Checks allow loopback; setup/workers may use outbound network. Inner full-access runs only within this boundary. Unsupported or nested-denied boundaries fail closed. Reviewer labels provide process ownership, not a host filesystem sandbox. No Docker fallback exists. Standalone review version/model calls persist ownership before launch; known direct-run recovery freezes the recorded dedicated runner before stopping descendants. Ambiguous ownership remains unresolved for audited operator recovery.

Host process visibility is checked before a probe allocates its run and before any gated workload records pending ownership or spawns. Inspection shares the remaining run deadline; timeout expires before pending ownership or workload spawn. Missing permission fails with a host-execution retry instruction, without inventing ambiguous ownership. Inspection or termination failures after spawn still retain ownership and require the existing recovery protocol. The existing filesystem boundary is unchanged. Isolated builds select the existing Apple compiler and SDK explicitly instead of using the system `cc` shim's host cache; no system developer setting or external cache write is enabled. This is shared host-platform configuration, with no project or language-specific branch.

Pending ownership precedes spawn; a trusted gate releases workload only after PID/profile/running records are durable. Cleanup discovers labelled descendants independently of leader/session changes, freezes/kills them and bounds output draining. Only then is completion recorded. Worker, check, review AND version-probe journals (including coordinated runtime qualification) block new execution across the repository after interruption. Run linkage precedes claim and repair-budget consumption. Standalone doctor uses the existing standalone execution/recovery protocol and retains exact observed runtime output in qualification. Heartbeats share the remaining workload deadline through bounded Beads I/O. Beads initialization uses the same owned-tool journal with a finite deadline; its trusted installer profile may write canonical storage.

Reconcile authenticates stored authority without requiring intact live artifacts, then stops execution before collection. Damaged ownership evidence is bound by its exact byte digest. An unreadable run descriptor blocks execution but can use the same audited attestation to stop surviving proven ownership and retain the root. Restore that descriptor from backup before normal reconciliation; missing task/authentication provenance is never inferred. Ambiguous, pending, missing or legacy ownership requires a confirmed exact recovery attestation after all owned processes stop; known live processes still refuse recovery. Probe recovery uses foundation authority even before feature approval and retains partial roots for inspection. Other recovery preserves commits, working/staged/conflict/deleted and ignored source plus failed Git state; only approved caches are omitted. Authentication omissions require verified original content or a same-layout credential refresh; changed/unclassifiable credential paths or inodes retain the private root. Collection requires a fresh private destination outside source; existing/substituted outputs fail before writes. Unsupported/incomplete collection retains roots. Delete source roots only after termination and successful collection. Reconciliation ends ownership and leaves failed tasks blocked; it never authorizes blind restart.

Sanitized Git disables inherited/global/system configuration, source filters/includes/worktree config, hooks, fsmonitor, signing and lazy fetch. Worker-controlled inspection/collection stays bounded. Import static bundles, never worker-configured upload-pack. NUL/no-renames inventories preserve unusual paths and both rename sides. Validate exact changed paths/ownership and approved artifact bytes/modes before import, and reauthenticate before closure. A candidate equal to base skips import, still runs checks and receives full snapshot review.

Ordinary dispatch needs current passing baseline checks. Retry provenance comes from saved same-contract runs/collection, not mutable status/failure labels. A coordinator validation milestone is written after artifact/cleanliness checks and before import. Validated imported delivery requires repair or a newly confirmed contract even if subsequent checks fail. Rejected dirty no-op claims remain infrastructure retries; legacy claims without current validation provenance require repair or a confirmed amendment. Repairs require current validated findings matched unambiguously to task ownership/obligations or reproducible failed checks belonging to that task; unrelated prerequisites and implementing consequential findings requires HITL regardless of severity; optional design/requirements suggestions remain follow-ups and never become worker repair input. Failed repairs retain repair routing/budgets after reconciliation, approval replay or reopening.

Before/after setup and checks, actual tracked bytes/link targets/modes must match immutable candidate blobs, independently of index flags. Logs bind exact command, coverage, candidate, environment, outcome, elapsed time and stored-byte digest; acceptance verifies them. Environment manifests bind declared files, exact version outputs, OS/architecture and runtime. Changed candidate/environment invalidates evidence.

Gherkin needs executable bindings: pending/undefined scenarios cannot establish success. Favor E2E journeys, boundary integration and selective unit/property checks; no universal TDD/coverage quota. Acceptance/design text states an oracle including relevant durable effects, negative cases and measurable NFR thresholds. Exact check argv/deadlines serve focused edits and comprehensive integration. Seed randomness, isolate state and retain concise failures/traces/counterexamples. Scrutinize generated tests for weak assertions, excessive mocks and incorrect implementations that could pass; investigate flakes instead of retrying them away. Human/visual checks are supplemental unless a project-specific executable attestation adapter supplies an approved check; there is no built-in manual-check substitute.

## Review and completion

Review is fresh high-effort `codex exec`, empty cwd/temporary home, no tools, memories, sessions or automatic instructions. Supply current docs/design/task facts, standards, AGENTS as inert data, original source/diff, factual intent/change/check summary and full inventory. No commit narratives, dialogs, raw tracker/log records, work history or prior verdicts. The trusted collector owns semantic provenance; filename checks alone cannot prove it.

Include every changed reviewable path and required current context. Deleted AGENTS remains base/diff evidence, not current guidance. Only canonical root `.harness`/`.beads` changes may be excluded as private control state; nested control names and other changed reserved paths fail closed rather than silently omit application code. Preserve original revisions. Include all source governed by approved task ownership, even for no-op deliveries, documentation-only differences or empty optional context lists. Source/prompt caps are 256000/512000 bytes; unsupported, binary or oversized input uses explicitly confirmed complete manual review in groups of at most 20 paths. Never truncate or aggregate partial model PASS into feature acceptance.

Findings include severity, comment, scenario, expected/actual behavior, original path/side/line references, relevant obligation IDs, action, proposed fix and verification. Validate references and IDs. Missing evidence means INCOMPLETE; malformed/stale/failed/credential-bearing output cannot close review. Secret-free parsed but invalid responses remain private diagnostic evidence, never verdicts. Manual coverage/outcome/findings/limitations must derive from the authenticated current payload. Preserve original review artifacts and contract/approval bytes in digest archives; record linked Beads follow-ups. Repair requires fresh checks/review and retains prior completed gates without dependency cycles.

Runtime bounds are deadlines and finite attempts, not a measured token/spend reserve. Features may tighten them. Normal launches are initial plus infrastructure retries; repair rounds and review launches have separate persistent bounds. INCOMPLETE consumes allowance; unchanged incomplete context cannot relaunch. Two reviews without blocker progress require human triage/amendment. No mutable status can reset these limits.

Closure requires current explicit approval, required closed tasks/gates, all current passing checks, complete current review and confirmed human acceptance. One confirmed exception binds one current blocker/rationale while preserving obligations; failed checks and invariants cannot be waived. Acceptance displays pending follow-ups. Merge/deployment remains separately authorized.

## Operator chat approval

After displaying the exact request, digest and consequential artifacts, the trusted
coordinator may record an explicit standalone operator `approve` or `cancel` with
`record-chat-approval REQUEST --request-sha256 SHA256 --evidence FILE`. Evidence is
a JSON object with exactly `decision`, `message`, `reference` and `request_sha256`.
The immutable event embeds the message/reference, labels its channel
`operator-chat-v1`, and records coordinator OS identity. The command checks both
digests and rejects missing evidence, unsupported decisions or inferred continuation
before publishing a receipt. `approved` and `reject` are also supported explicit replies.

This is a coordinator attestation under the trusted-host model, not authenticated
chat-provider identity. The coordinator must accurately associate the operator's
message with the request actually displayed; a CLI cannot prove that association.
Never manufacture a message, infer approval from “proceed”, or approve a changed
payload using an earlier message. Historical records are never rewritten. Existing
terminal events remain valid. A chat decision needs no second terminal decision.

The same external private store, exact immutable payload/event/receipt verification,
worker isolation and downstream gates apply. A recorded CANCEL publishes no receipt;
it does not revoke an earlier approval for identical bytes. Revocation remains a
separate authority operation. Source/contract drift and finite counters are still
validated by the consuming operation. Terminal confirmation below remains optional.
Where later workflow descriptions say request-confirmation, this explicit chat path
is an equivalent consent channel; it never substitutes for the matching import.

## Scripted human approval

`continue TARGET` verifies the installed foundation with the selected approval store and starts a fresh planner from current project/task/design facts; it neither reinstalls nor grants approval. Session protocol/active checks and a controller-held OS lock reject legacy or ended controllers before queueing a request; a surviving requester also checks liveness while waiting. A hard controller crash during terminal handoff may end the CLI job on qualified macOS; the terminal returns to the operator, records remain, and the existing phase-appropriate recovery starts a fresh session. Survival of that CLI across a hard crash is not promised. Requests, outcomes and audit records are retained, with phase-appropriate recovery: same-terminal `continue` for an installed project, `new-project` for an empty uninstalled target, or retained identical-input installation/manual confirmation recovery for a partial installation. Existing processes are never automatically stopped or replaced. `new-project` keeps Codex running with narrowly scoped state/store write grants. The `request-confirmation` tool/script captures the exact request and hash-bound proposed documents, automatically pauses its dedicated Codex process group, gives the controlling terminal to a read-only editor/pager, and collects the operator's explicit decision through the terminal prompt. The controller restores terminal settings/foreground ownership and resumes that same process; no terminal-exit gesture is required. It uses the existing immutable audit payload/event/receipt verification. Invalid input reprompts without a decision; cancellation records CANCEL and grants no authority. Changed proposals and failed terminal handoffs/viewers fail closed. Only an APPROVED result permits the matching validated transition. Successful installation/import records APPLIED separately, without treating approval as proof of execution.

Private Markdown review copies and JSON/text outcome files remain under `.harness/sessions/` after cancellation, errors or session exit; external approval events remain in the selected store. Automatic foundation previews are removed only after successful installation. This adds no workflow stage or global configuration. Existing manual confirmation and recovery remain available. The host operator/coordinator is trusted; this is not cryptographic proof of human presence. Agents may invoke the terminal script but may never supply its input or simulate terminal confirmation. The separate chat recorder attests only to actual explicit operator messages.

Collection has a separate 512 MiB total credential-coverage allowance defined alongside the scanner’s 64 MiB default, and uses the existing runtime check deadline (300 seconds); each regular-file read remains capped at 64 MiB. Other guard users retain their 64 MiB default. Budget exhaustion, oversized files or timeout retain the private root. Source, private-home data and prior partial collections are preserved; no new cache exclusion is implied.

For a verification batch with a setup command and explicitly approved `recovery_cache_paths`, prepare dependencies once in a journaled candidate checkout, then copy only those paths into independent fresh check roots. The preparation runs with existing setup permissions/deadline; source bytes are verified before/after setup and each check. Only the bounded trusted copier receives read-only access to prepared cache paths; check processes cannot read/write the preparation root or other check roots. No writable hardlinks, global caches, cross-batch reuse or new authority are introduced. Cache selection cannot overlap tracked source or control metadata. Caches must be self-contained/relocatable: absolute/escaping symlinks and special files fail closed, with source recovery retained. Missing optional caches are not invented. Projects without declared caches retain existing setup-per-check behavior.

The `cache_id` is SHA256 of candidate, environment fingerprint, approved setup argv, normalized cache paths and contract hash. It identifies preparation intent, not content validity or approval. A unique preparation-run ID distinguishes independent batches using the same key; run journals and progress entries carry both. Phase updates and bounded-run heartbeats report setup/copy/check work on stderr and durable progress JSONL. Existing check evidence still binds exact candidate/environment/argv; failure, timeout, uncertain cleanup and audited approvals keep their prior gates. Preparation itself is journaled as check ownership for ordinary recovery, never an extra approval stage. Prepared roots are removed after the batch only when ownership cleanup is established.

## Coordinator loop

After each tool result, persist the task outcome/evidence and take the next authorized action in the same session: installation → four-stage planning → audited approval import → dependency-ready slices → integration/checks → clean review/within-scope repairs → audited acceptance. Successful milestones and worker handoffs are progress updates, not reasons to end the turn or ask “continue?”. Claim before acting; do not dispatch parallel ownership or bypass prerequisite gates. Continue after an APPROVED result only when the matching import succeeds and current gates permit it.

When a consequential choice is unresolved, present the concrete alternatives and ask the specific question. When a proposal is ready, display its exact bindings and record explicit operator chat consent, or invoke `request-confirmation` for the terminal path. Do not ask for a duplicate decision once matching consent is recorded. CANCEL or failed/missing approval keeps work paused. If a controller, permission, recovery or finite budget blocks progress, preserve evidence, mark the affected task blocked and show the exact next action or request the necessary human decision. Never auto-approve, infer consent, widen scope, reset budgets or blindly retry. While a tool is running, give bounded progress updates; do not launch a duplicate operation. Resume from current facts after feedback, not by replaying completed stages. Final acceptance and release remain separate human decisions.

After reviewed planning and `plan`, use `run TARGET SCOPE` for bounded continuation by calling the existing approve/execute/integrate/review/repair/accept gates under the repository lock. It never authors planning artifacts or human decisions. Authenticated current state selects actions; `driver.json` is advisory progress and receipt handoff, never permission or a phase override. Audited confirmation uses the existing controller or explicit manual payload. Saved payload pointers are reauthenticated before import; acceptance revalidates current closure. Failures stop with retained evidence, not implicit retries, recovery attestations, scope changes or budget resets. Resume uses the same command; another active coordinator is never interrupted.

Coordinated review delegates descendant ownership and cleanup to the outer coordinator's existing sealed label/journal. The inner runner verifies its own kernel label, launches without a second host-inspection journal and stays in the inherited group. The inner workspace remains under the outer managed temporary root and is not deleted by the inner runner. The outer runner stops all labelled descendants (including detached ones) before deleting that root; uncertain termination retains it. Standalone review retains its own preflight, launch journal and recovery; parent-label flags without a matching inherited label fail closed.


## Revalidating unchanged delivered prerequisites

An approved amendment blocks previous slice projections. If an unchanged delivered prerequisite would otherwise need another worker, use `revalidate-request TARGET SCOPE TASK`, then the existing `request-confirmation REQUEST`, followed by `revalidate TARGET SCOPE IMMUTABLE_PAYLOAD`. This explicit audited transition authenticates both feature approvals and archived prior contract, exact task/native identity and unchanged non-slice artifacts/checks/environment/limits. It requires retained coordinator-validated imported delivery, ancestry, unchanged owned/context/environment tracked source and current passing own/baseline/transitive dependency checks with authenticated stored logs. Historical run descriptor digests are bound; a later failed or interrupted revalidation supersedes prior pending authority. Missing, changed, failed, stale or unresolved recovery/decision facts fail closed.

Import revalidates the exact current candidate/environment/evidence and the latest successful revalidation attempt, stores exact approval bytes before closing only that prerequisite and supports identical interrupted-import replay. Original delivery, approvals, logs and all attempt/repair counters remain unchanged. No worker runs or counters reset. Full integration, review and human acceptance remain required. Changed intent needs ordinary repair/amendment.

## Immutable invocation provenance

The stable launcher resolves one verified package and one external approval store
before loading the CLI. That selection remains fixed for all helpers and new
execution evidence in the invocation. New evidence uses additive
`invocation_release` metadata; legacy records remain unpinned and unchanged.
A package directory cannot silently redefine the default shared approval store.
Source verification occurs before helper launches and at transition import.

Release staging is unqualified and does not switch active code. Future selection
and existing-project adoption require separate audited transitions with fresh
occurrence IDs, exact source/qualification and selector/state/counter preimages,
quiescence, durable original approval bytes and idempotent receipt projections.
Missing, stale, cancelled, superseded or failed authority keeps the gate closed.
No transition stops active work, resets attempts, rewrites original approvals or
retroactively attributes a release to an old run. Full qualification and complete
current source review remain mandatory before promotion; a passing focused test
or an approved plan cannot substitute for either.


### Additive retained-delivery compatibility

The existing revalidation commands may prepare an explicit additive-retained-delivery-v1 request when retained task/dependency intent, referenced original artifacts, selected baseline/own/transitive checks, environment policy, limits and repair policy remain exact. All global changes are displayed; added project obligations remain pending. The common harness interprets generic contract structure only; domain requirements remain project data. Legacy payloads without a mode retain strict-v1 behavior; unknown modes fail closed.

Changed protected source requires exact imported sibling delivery provenance: governing audited intent/native identity, inactive protocol2 coordinator validation, complete actual diff and ownership, original registered check evidence, and exact ancestor/tree/blob/mode history. Unknown changes, ambiguous merges or unsupported history block; commit messages and shared paths grant no authority. Original checks are validated against their recorded historical environment; fresh current selected checks bind the exact clean candidate and current environment before the request. No worker launches and exhausted worker attempts remain exhausted.

The audit displays the complete delta, original and current environments, source proof, checks and counters. Import recomputes all facts, verifies display/current evidence and latest successful attempt, persists exact authenticated authority before closure and permits only identical interrupted replay. Failed or interrupted later attempts supersede pending requests. Preserve original delivery, approval and attempt records. This does not waive full integration, clean review or final feature acceptance, nor promote/adopt a release.

Coordinator-prepared complete implementation inputs and mandatory implementation-agent-only context handoffs follow BEADS_TASK_TEMPLATE.md. Bounded implementation discovery follows Implementation discovery and routine completion above; consequential unresolved decisions return to the coordinator. No runtime context enforcement is introduced.

Current-state summaries are read-only projections: identify the current candidate, authenticated gate, actual blocker, remaining allowance and one supported next action. Historical status text in immutable approved documents describes that document’s snapshot, not current dispatch authority. Reauthenticate original records for transitions; a conflicting or incomplete projection grants no authority. Ordinary recovery should use existing supported commands and retained evidence; when no safe supported path exists, name the product gap instead of presenting a reconstruction script as the normal interface.

## Explicit validation of an audited coordinator correction

`repair-validation-request` binds the complete reviewed correction chain, exact source,
original delivery, current contract/native task, environment, approval store and existing
repair allowance. Only its exact confirmed payload may authorize `repair
--validation-authorization`. This removes only the need for an artificial defect trigger;
ordinary repair routing remains unchanged. Fresh required checks and baseline must pass
before reservation. The prospective run ID becomes the real worker journal and single-use
reservation. Charge the existing epic repair counter and run marker together before claim
or workload release; normal attempts remain unchanged. Lost acknowledgment is reread,
never refunded or double charged. Uncertain reservations require existing reconciliation.

A real worker may honestly return an unchanged candidate. Existing collection, ownership,
artifact/mode validation, coordinator checks, review and acceptance gates remain mandatory.
Archive exact old delivery bytes before replacing the projection. Failed or interrupted
runs preserve source, evidence, approval and attempt history. No external-delivery importer,
synthetic worker attribution, parallel allowance or automatic controller branch is added.

## Automatic implementation-worker continuation

The default runtime uses Codex app-server within the existing native worker boundary.
It observes last-inference token usage (not cumulative billed tokens),requests interruption
at128000 tokens or60percent of a smaller reported window,and also checkpoints after a
bounded coding interval. A read-only handoff reports DONE,LEFT and the next action.
The outer runner stops the whole owned process scope before the coordinator captures
the dirty/staged/untracked/Git checkpoint and creates a real deterministic Beads child.
The child receives the full exact immediate parent input and its SHA256,original task,
constraints and remaining work. Worker prose is not acceptance evidence.

Three sessions share one original worker deadline and normal/repair attempt. The coordinator
alone writes Beads. Successful aggregate delivery and coordinator checks close continuation
children and the original task; a checkpoint never completes the parent. Stale authority,
conflicting child identity/parent,failed handoff,uncertain cleanup or exhausted limits retains
evidence and stops. No operator approval is required for an in-scope rollover. There is no
hard token ceiling claim: delayed notifications or one large tool/model step can overshoot
a threshold. The deferred repair-validation transport is unchanged.
