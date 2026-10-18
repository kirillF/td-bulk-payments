# Repository-local Codex harness

A shared CLI for approved vertical slices, Beads tracking, isolated implementation and verification. It activates only in explicitly installed projects. Global settings and skills stay untouched.

## Normal project work

Routine coordinator budgeting is an agent responsibility, not an operator approval
gate. Choose bounded command timeouts and checks/reviews needed to finish the agreed
outcome; do not ask the operator to approve time pools, review slots, or routine retry
counts. Preserve actual consumption and failed-attempt history without resets. Respect
any hard cap explicitly supplied by the operator and any separately chargeable external
action. Existing project worker/repair counters and isolation/check gates still apply.
Ask for a new decision only when scope, authority, external consequences, or an explicit
operator constraint changes. Historical agent-proposed budget approval ceremonies do
not govern routine continuation after this instruction.

Agree one useful outcome and constraints; the coordinator manages routine execution capacity. Claim its Beads
task,make small edits,use the application for feedback when useful,and continue routine
corrections without another approval package. Planning detail scales with uncertainty.
Use focused checks when requested or useful and report unrun verification honestly.
Complete model review and exhaustive qualification are explicitly requested operations,
not a prerequisite for every edit. Ask for a new decision when product scope,public
interfaces,authority,external publication or total budget changes.

New projects retain one foundation installation confirmation,then use this direct loop.
Four separate planning approvals apply only to explicitly requested formal delivery.
A blocked maintenance campaign does not block ordinary project development. Preserve
all existing approval,delivery and attempt records;direct edits do not fabricate gates.
Isolated implementation workers automatically roll over through monitored Codex app-server
sessions. At observed128k usage,60percent of a smaller context window,or a bounded session
deadline,the coordinator requests a read-only handoff,retains the complete assignment and
partial work,creates a real Beads child,and starts a fresh worker after native cleanup.
DONE/LEFT/next action and full immediate parent input carry forward. Up to three sessions
share the original worker deadline and one attempt/repair charge; no routine approval is
needed. Usage is an early signal,not a hard token ceiling. Invalid handoff,changed authority,
uncertain cleanup or exhausted limits stops with retained evidence. Deferred repair-validation
continues to use its existing transport and is not made safe by this feature.

The shared executable defaults to the explicitly authorized unqualified working source
named in `.harness/working-source.json`,for existing and new projects. The selected green
release remains unchanged. To use it instead: `HARNESS_USE_QUALIFIED_RELEASE=1 harness continue`.
This mode choice is not a qualified promotion/adoption. `.approvals` beside the shared
executable remains the default; `--approvals-dir` is optional when choosing another store.

## Approve once in chat

The coordinator displays the exact pending request and its SHA256. Reply `approve`
(or `cancel`). It records the decision and continues through the existing gates;
you do not need to repeat the decision in a terminal. A generic “proceed” is not
approval. Changed request bytes require a fresh decision.

The trusted coordinator uses `record-chat-approval REQUEST --request-sha256 SHA256
--evidence FILE`. The JSON evidence contains `decision` (`APPROVE` or `CANCEL`),
`message` (the exact standalone reply), `reference` (where the operator replied),
and `request_sha256`. The audit embeds this evidence with channel
`operator-chat-v1`; it is coordinator-attested consent, not independent proof of
chat-provider identity. Workers cannot access the approval store. Never fabricate
operator text or reuse an old decision for a new request.

`request-confirmation` and manual `confirm` remain available for terminal approval.
The terminal-only instructions below describe that optional path. Existing receipts,
checks, finite budgets, acceptance and release gates retain their meaning.

## Start with Codex

Run one command from your terminal; the project directory may be absent or empty:

```sh
~/devel/codex-harness/harness new-project ~/devel/my-project "My product idea"
```

This creates the directory if needed and opens a fresh interactive Codex CLI session using the shared repository’s `skills/new-project/SKILL.md`. Omit the idea to have Codex ask for it. Use `--codex /absolute/codex` to select an executable. Directories containing only an empty Git repository are also accepted. Use `new-project TARGET --force` to allow existing files and overwrite conflicting scaffold files through the approved foundation preview. Unrelated files and Git metadata are preserved; the launcher itself does not remove or overwrite files. A global `--approvals-dir /absolute/private-store` before `new-project` is passed into the session; the skill preserves it for harness calls and operator confirmation commands.

The skill owns startup guidance: clarify the stack and requirements, prepare an automatic temporary preview, and record one audited chat approval before installation (terminal confirmation is optional). You do not need a long initial prompt or a separate setup directory. When the operator chooses terminal approval, Codex calls the `request-confirmation` script. The launcher automatically pauses Codex and opens a captured read-only review package in your terminal editor (`$VISUAL`, then `$EDITOR`) or pager (`$PAGER`, default `less`). For example, start with `EDITOR=nvim ~/devel/codex-harness/harness new-project ...`. Exit the viewer (`:q` in nvim, `q` in less), then type `APPROVE`, `CANCEL`, or `REVIEW` at the explicit prompt. Invalid input reprompts without recording a decision. The launcher prints the outcome and audit reference, then resumes the same Codex process with the result. Cancellation keeps the gate closed so you can discuss revisions. No browser, terminal exit or copied payload paths are needed. Review copies, outcome JSON/text and audit references are retained under `.harness/sessions/`. Successful installation/import updates `APPROVED` to `APPLIED`; later failures leave records intact. Manual confirmation remains available for recovery. After installation,the agent uses the ordinary outcome-and-allowance loop above;formal planning and delivery start only when explicitly requested.

This command launches the terminal CLI, not a desktop chat. It does not register a personal skill or modify global configuration; the skill stays in this shared repository and the workflow activates only in the explicitly installed target. The launcher uses the workspace-write sandbox and grants access to the target, the shared `.harness/` state directory, the selected private approval store, for the running session. It creates the control directories if needed; creating an empty approval store grants no approval. Host process-inspection checks may require an explicit execution-permission prompt. Review copies and outcomes are retained under the shared `.harness/sessions/` directory; automatic foundation setup is still cleaned after successful installation. Other planner settings use your normal Codex configuration; bounded implementation and clean review retain the harness's configured models.

The startup conversation includes an explicit architecture discussion before foundation approval, which authorizes minimal tooling/scaffolding only. Explicitly requested formal feature planning reviews artifacts within its four existing stages: **product** (FR/NFR, invariants and actual Gherkin), **architecture** (an architecture document), **program design** (an API document with public signatures/contracts plus a code-design document), and **vertical slices** (the requirement-linked Beads epic/tasks, acceptance and checks). Codex presents each stage's artifacts, asks for feedback and resolves it before proceeding on consequential choices. See the [four-stage review contract](OPERATIONAL_CONTRACT.md#plan-and-approve). The existing audited feature/amendment approval follows the resolved plan; no fifth stage or extra audit ceremony is added. If planning review was missed, implementation stays paused until a reviewed amendment is approved. Existing approval history is preserved.

To continue an already installed project, run from its directory:

```sh
harness continue
```

The target defaults to the current directory. An explicit target still works: `harness continue /absolute/project`. The approval store defaults to `.approvals/` beside the shared harness source, including when the executable is reached through a symlink; no override is needed for the default store. For a different store, use `harness continue --approvals-dir /absolute/private-store` (the global prefix form also remains supported).

Make the executable available on PATH with a symlink to the shared launcher, for example `ln -s ~/devel/codex-harness/harness ~/.local/bin/harness` when that directory is on PATH and the command does not already exist. The launcher resolves its real source location; no project-local copy or shell alias is required.

Keep the same `--approvals-dir` override if a private store was selected. This starts a fresh planner from current project/design/task facts and verifies the installed foundation's authority; it does not reinstall, reset work or approve a pending request. If an older running launcher reports an inactive/incompatible controller, leave that CLI with `/quit`, then run `continue` in the same terminal. Existing audit records and proposals remain intact. Launching the new command does not take over or stop an existing session; use only one coordinator for a project.

## Manual setup

Requires Python 3.11+, Git and the macOS toolchain pinned in the shared source’s `runtime.json`. No Docker or signing setup is needed.

1. Run `~/devel/codex-harness/harness doctor`. Its result names a persistent diagnostic directory; recover an interrupted run with `doctor --recover-output DIRECTORY`.
2. Clarify product needs and discuss the proposed architecture, alternatives and material tradeoffs with the operator. Resolve feedback before foundation approval. Save the architecture in `stack` and `conventions`, alongside tooling, deployment, testing, autonomy and invariants as the existing seven string fields in `profile.json`. Optional `scaffold` contains text `files` and named `commands` (`setup`, `start`, `check` argv); include appropriate dependency locks and CI. Optional `recovery_cache_paths` lists explicitly disposable dependency directories, never source. Default recovery excludes none.
3. Preview and inspect every proposed file/command, confirm in your terminal, then install. Omitting the output path creates a private temporary setup directory automatically:

```sh
HARNESS=~/devel/codex-harness/harness
"$HARNESS" preview /absolute/project /absolute/profile.json
# Use the preview and approval_request paths printed above:
"$HARNESS" confirm PRINTED_APPROVAL_REQUEST
"$HARNESS" install PRINTED_PREVIEW CONFIRMED_PAYLOAD
```

Use `preview TARGET -` to supply the profile as JSON on standard input without creating a profile file. After successful installation, the harness removes its managed temporary setup; failed/interrupted installs retain it for inspection and retry. Unexpected extra files prevent automatic deletion. Explicitly supplying an output directory keeps that preview persistent. Audited confirmations remain in the external approval store, and the complete approved foundation remains in the project’s private installation record.

`confirm` prints an immutable payload path; use it for `CONFIRMED_PAYLOAD`. Agents request approval; the operator performs confirmation. Existing guidance/TOML is merged in the preview; use `refresh-preview PREVIEW` after edits, before confirmation. Installation resumes only the identical approved preview and rejects unrelated staged changes. Existing Beads installations need operator backup/migration qualification.

The default private approval store is `.approvals/` beside the shared CLI. A trusted launcher may prefix commands with `--approvals-dir /absolute/private-store`; use that same selection throughout. Keep this store in backups. `audit --project /absolute/project --scope feature-name` inspects immutable confirmation events, including cancellations. See [authority and recovery rules](OPERATIONAL_CONTRACT.md) for the trusted-host assumptions and receipt bindings.

## Repository instructions

New project guides come from `install-templates/AGENTS.md`; preview appends only the declared setup/start/check argv as shell-quoted commands. Review the generated guide with the foundation files. Keep `AGENTS.md` a short entry point: durable decision boundaries, exact working commands and pointers to authoritative documents. Put the product purpose and a small useful source map in the project's README when scaffolding it; do not invent paths before they exist. Detailed architecture/API, conventions and current task status belong in their existing documents/Beads, not duplicated here.

Keep only instructions that prevent a demonstrated mistake or explain a non-obvious repository constraint. Prefer formatter/linter/check configuration for mechanical rules. Update stale commands/links when tooling moves; remove obsolete rules instead of accumulating incident history. Add nested `AGENTS.md` only for genuinely different directory rules, without copying the root. Do not introduce parallel tool-specific instruction files unless actually needed.

This follows [OpenAI's layered repository guidance](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [Anthropic's concise instruction advice](https://code.claude.com/docs/en/best-practices#write-an-effective-claudemd), [HumanLayer's onboarding and progressive disclosure](https://www.humanlayer.dev/blog/writing-a-good-claude-md), and [Builder's minimal, transparent, tested context](https://site.builder.io/blog/agent-experience). These are authoring guidelines; executable harness gates enforce authority. Existing projects are not rewritten automatically.

## Feature loop

Prefix these commands with the shared `harness` path. `TARGET` is the absolute project path; `SCOPE` is a short lowercase feature name; `TASK` is a canonical slice ID.

1. `start TARGET SCOPE` resumes interrupted creation with the same native identities and creates the epic, four preparation tasks and existing approval/integration/review/acceptance gates. Prepare product requirements, architecture, program design and vertical slices; stage 2 produces an architecture document; stage 3 produces API and code-design documents. `design_layers` accepts one path or a path list per stage; every referenced document belongs in `artifacts`. Review artifacts with the operator within each of the four stages, including concrete Gherkin and the full requirement-linked Beads epic/task breakdown; resolve feedback before `plan`.
2. Author `feature.json` using [feature-example.json](feature-example.json), [task.schema.json](task.schema.json) and the [task template](BEADS_TASK_TEMPLATE.md). These references and this guide are installed under `docs/harness/`. After interrupted planning, repeat the saved document/base before proposing changed intent. `plan TARGET SCOPE feature.json` validates coverage/dependencies and prints the approval request.
3. Present the resolved four-stage package, including the Beads epic and full canonical tasks/native IDs printed in the request. Resolve design feedback (reuse recorded stage decisions); for a fully explained narrow amendment, proceed directly to `request-confirmation REQUEST` in the controlled session (or operator `confirm REQUEST` manually). Import the verified payload with `approve TARGET SCOPE CONFIRMED_PAYLOAD`.
4. `build TARGET SCOPE TASK --auth /absolute/auth.json --memories /absolute/memories` launches one isolated low-effort implementation worker. `--memories` supplies advisory context; `--codex /absolute/codex` selects the executable. Current approved design and task intent govern work.
5. `integrate TARGET SCOPE`, then `review TARGET SCOPE --auth /absolute/auth.json` runs comprehensive checks and fresh high-effort review, with no tools, memories or dialog history.
6. `repair TARGET SCOPE TASK ...` handles current findings or failed task checks within the approved finite allowance. Integrate/review again after changes. Consequential changes return to `plan` and explicit confirmation.
7. `acceptance-request TARGET SCOPE` emits current evidence bindings. The operator confirms them; `accept TARGET SCOPE CONFIRMED_PAYLOAD` closes the feature. Merge/deployment requires separate authority.

Before final approval, inspect each slice's integration needs, then run `check-scope feature.json TASK path/to/source path/to/build-script path/to/test` with its concrete intended edits. This read-only command reports all supplied paths outside the task's ownership; correct the proposal before approval. It does not infer edits from requirements or authorize execution. Include needed manifests/locks and build/check adapters; context and dependency tasks do not grant write access. Record the result in the existing task notes. Recheck before dispatch if intended edits changed.

Follow the coordinator loop in OPERATIONAL_CONTRACT.md: continue authorized steps after successful results; request specific design input or invoke audited confirmation when needed. Milestones do not require a generic “continue”. Stop only for completion, pending human input or a concrete blocker with a next action. Existing approvals and finite execution/recovery gates still apply.

### Resumable execution

After four-stage review and `plan`, the coordinator uses one command instead of manually advancing the feature loop:

```sh
~/devel/codex-harness/harness --approvals-dir /absolute/approval-store run /absolute/project SCOPE --auth /absolute/auth.json --memories /absolute/memories
```

It imports explicit feature approval, runs dependency-ready slices serially, integrates/checks, launches clean review, repairs unambiguous blockers within approved intent/budgets, and requests separate final acceptance. In a live `new-project`/`continue` controller it opens the existing terminal confirmation automatically and resumes after verified import. Outside that controller it prints the exact manual confirmation command; rerun with `--approval IMMUTABLE_PAYLOAD`. It never supplies approval input or invents a plan, amendment, recovery attestation or exception.

Rerun the same command after a pause or resolved blocker. The repository lock rejects another active coordinator; runs are never taken over. Existing authenticated state and gates determine progress; `.harness/features/SCOPE/driver.json` records the latest action/outcome and receipt pointer, with concise Beads notes. A saved receipt is reauthenticated by the normal importer, never treated as authority itself. If a hard interruption occurs before payload handoff is saved, use the retained approval outcome's immutable payload with `--approval`.

Exit codes: 0 complete; 2 blocked with a reason; 3 waiting for planning/HITL or the per-invocation step limit. `--max-steps` defaults to 20 (1–100); resuming does not reset persistent worker/review/repair budgets. Failed workers/checks, unresolved ownership/recovery, consequential or ambiguously owned findings and invalid/stale evidence stop for explicit triage; no blind retries/reopening. Each operation retains its existing deadlines/progress. Acceptance is revalidated on resume; a previously manual acceptance needs its original payload to prove completion. Release remains separate.

A scope reduction uses the same `plan`/confirm/`approve` amendment: omitted task IDs are displayed as retirements and close only after confirmed approval. Retired IDs remain historical and cannot be reused. Already delivered intent must use `repair` or a newly confirmed contract, even if someone reopens its Beads task.

## Recovery and alternatives

`reconcile TARGET SCOPE` stops interrupted ownership and exposes partial work. Ambiguous ownership emits a recovery request: inspect and stop all owned processes, confirm that request, then repeat with `--resolution CONFIRMED_PAYLOAD`. Probe recovery also works before the first feature approval; use scope `bootstrap` during an incomplete installation. Partial probe roots are retained for inspection. No blind restart occurs.

Unsupported or oversized static context uses `manual-request TARGET SCOPE`: inspect every declared group against original revisions, fill complete outcome/findings/coverage, confirm, then `manual-import TARGET SCOPE CONFIRMED_PAYLOAD`. Never combine partial model PASS results into feature acceptance. `exception-request TARGET SCOPE INDEX RATIONALE`, confirmation and `exception-import TARGET SCOPE CONFIRMED_PAYLOAD` bind one current blocker without waiving checks or invariants.

[OPERATIONAL_CONTRACT.md](OPERATIONAL_CONTRACT.md) is the authoritative workflow contract. `runtime.json` owns execution settings; installed project configuration only enables advisory planning memories. The shared source contains the coordinator and validators; it is not copied into projects. Historical proposals remain in the shared source’s `docs/history/`. Qualification evidence covers this harness; qualify your selected application stack and executable Gherkin bindings before unattended use.

Direct `codex-review.sh` diagnostics persist `execution.json` and `process.json` before launching the version probe and model. Use `codex-review.sh --recover-output OUTPUT` for a known interrupted standalone run. It stops the recorded dedicated runner and labelled descendants, retaining its private root for inspection. Missing, pending or altered ownership stays unresolved for audited operator recovery; normal project reviews use `reconcile` instead.

Verification prepares dependencies once per candidate/environment batch when the foundation declares `recovery_cache_paths`. Each check still gets a fresh checkout and independent copies of only those caches. Progress on stderr and `.harness/runs/*/progress.jsonl` distinguishes dependency setup, cache copying and checks, with elapsed heartbeats. The deterministic `cache_id` and unique preparation-run ID link the checks to their prepared inputs. No caches are reused across batches or candidates; absent declarations retain setup-per-check behavior. See OPERATIONAL_CONTRACT.md for cache safety and recovery.

Immutable-release execution and future audited selection/adoption are described
in [docs/releases.md](docs/releases.md). Staging, complete qualification, source
review, promotion and project adoption remain separate gates. A new launcher or
selected release is never published over active source users by implementation
or qualification commands.

## Recoverable continuation

Start with the current recorded failure, not a reinstall or counter reset. The coordinator explains the failed operation, exact missing/conflicting evidence, retained candidate/run/approval identity, consumed/remaining allowances and one supported next action. Exact authenticated interrupted imports may replay through the same importer; unknown process ownership uses the existing reconcile/attestation path. Missing original authority remains a named defect or scoped repair, never an inferred approval.

Command forms (replace inputs only with verified retained paths): `approve TARGET SCOPE IMMUTABLE_PAYLOAD`, `revalidate TARGET SCOPE IMMUTABLE_PAYLOAD`, or `run TARGET SCOPE --auth AUTH --memories MEMORIES --approval IMMUTABLE_PAYLOAD` for the appropriate saved gate. A revalidation payload belongs to `revalidate`, not to the feature-approval importer. `reconcile TARGET SCOPE [--resolution IMMUTABLE_PAYLOAD]` retains its process-ownership safeguards; it does not authorize restarting work. The coordinator chooses from current evidence and emits a concrete command, not an executable placeholder. Use the same approval store throughout.

No ordinary recovery path should depend on a new incident-specific reconstruction script. If no supported safe path exists, identify the missing evidence and record the product defect; preserve work and stop. Partial work, failed checks and dirty state may be retained in an explicitly incomplete handoff. Never claim completion or delete recoverable work to satisfy a cleanup checklist. Mandatory implementation-agent handoff remains in BEADS_TASK_TEMPLATE.md; this adds no runtime context enforcement.

For an interrupted or failed task, first identify the current candidate/gate, the failed check or conflicting evidence, preserved work, remaining allowance and one supported next command. Old “paused” or “not dispatchable” text in approved documents is historical; current authenticated records govern. Report setup, check and review turnaround, correction rounds and approval interruptions from existing records; label missing token/cost/attention measurements unavailable. Do not launch extra runs to fill metrics or claim savings from test totals.

### Validating an already corrected task

If an audited coordinator correction already passes checks, do not manufacture a failing
check or reset exhausted normal attempts. Prepare complete combined correction evidence:
`harness repair-validation-request TARGET SCOPE TASK --evidence /absolute/evidence.json`.
Confirm its printed request with the existing terminal gate, then use
`harness repair TARGET SCOPE TASK --validation-authorization IMMUTABLE_PAYLOAD --auth AUTH --memories MEMORIES`.
This consumes one existing repair round and launches a real worker; an honest no-op is
permitted. Current checks, collection, source review and acceptance still apply. The old
delivery is archived before replacement. A consumed request names its run for reconciliation;
never replace it to bypass recovery. This capability does not promote or adopt a release.

Evidence version 1 contains `base`, `candidate`, `corrections` (ordered `commit`/`approval`
pairs), `review` (`directory`, `packet`, `inputs`) and complete `checks` records. Review
artifacts must cover the full correction range and task context. Existing maintenance
payloads bind exact single-file pre/post hashes; multi-file payloads require per-path
`images` with those hashes. Legacy-unpinned trigger logs remain trigger evidence only;
fresh checks establish current dispatch evidence. Missing authority, source, provenance,
coverage or allowance is a blocker, never an implicit waiver.
