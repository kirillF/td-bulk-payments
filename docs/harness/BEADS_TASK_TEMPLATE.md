# Beads task template

Use `task.schema.json` for canonical fields (implemented by the shared `task_model.py`); the coordinator renders this human view. Use one small executable outcome per slice; size it for early behavior and diff review, well below the handoff threshold. Task kinds: foundation, preparation, approval, slice, integration, review, acceptance. Repairs are linked attempts under an approved slice intent.

The coordinator also renders preparation and gate tasks from this template. Review their artifacts with the operator within product, architecture, program design and vertical-slice preparation; these are the four existing stages. Product artifacts include reviewed FR/NFR and concrete Gherkin. Slice preparation includes a walkthrough of the epic's success criteria and full tasks, with requirement/scenario/check mappings, before audited feature approval. Planning binds suggested outputs to actual artifact paths and feature IDs. Integration is a coordinator gate with approved check IDs; other gates name their `coordinator.*` completion predicates. Slice IDs cannot use the reserved `workflow.` prefix.

Before work, select and claim this task. During work, persist concise progress, decisions, open questions and artifact/evidence links in its notes. Before yielding, record the outcome and next action and update status; waiting for HITL is blocked. After awaited feedback is resolved, the coordinator uses the guarded reopen-then-claim procedure in OPERATIONAL_CONTRACT.md; a blocked task cannot be claimed directly. The coordinator applies completion gates, including `plan` for preparation closure. Workers report results without changing Beads.

## Goal and scope

Outcome and value; owner; included work and exclusions. Link the epic and canonical task ID.

## Requirements and invariants

Relevant functional/non-functional requirements and invariant/scenario IDs, with design artifact references. Planning tasks reference proposed artifacts; implementation references their approved versions. Use measurable thresholds where needed; otherwise reference project standards.

## Design references

Only relevant approved interfaces, types, responsibilities and organization. Omit this section when no design detail is needed. Preparation tasks name the artifact/questions they must resolve. Architecture preparation delivers an architecture document. Program preparation delivers API and code-design documents, including explicit public signatures/contracts; bind both paths to the program stage. Their operator reviews precede feature approval. Record resolved decisions and constraints in the referenced design artifact, including important boundary signatures/types, ownership/lifetimes and error behavior where applicable. Do not copy the conversation into tasks or reviewer context. Slice tasks reference the reviewed design rather than repeat it; consequential open decisions block implementation.

## Acceptance Criteria

Observable positive/negative outcomes and Gherkin references. Map IDs to approved executable checks. Human/visual procedures are supplemental unless a project-specific executable attestation adapter is approved. Mark stubs provisional; they cannot establish final acceptance.

State the verification oracle here: what observable result distinguishes correct behavior from a plausible incorrect implementation? For a user journey, include relevant persisted effects/API outcomes, not just a success message. Reference measurable non-functional thresholds where applicable. Reuse approved artifacts instead of repeating them.

## Execution

Owned paths, dependencies, context references and check IDs. Inspect integration/build/check entry points; include needed manifests, locks, adapters, scripts and tests. Context is read-only and dependency ownership is not inherited. Run the documented `check-scope` on intended edits and record its result in task notes before approval; correct gaps before dispatch. Implementation starts with no consequential unresolved decisions. Follow OPERATIONAL_CONTRACT.md → Implementation discovery and routine completion for local discovery and routine corrections. Plan a finite completion allowance covering focused checks, required review, in-scope corrections and qualification, with a verification/handoff reserve; name the existing runtime caps and remaining task allowances. Include expected fixture ownership before approval. A task or handoff adds no attempts.

Follow CODING_STANDARDS.md for complexity, readability and decomposition. Simplify within this task's approved ownership; do not create a separate refactoring stage.

Manage routine focused/review capacity within bounded commands without a separate operator budgeting ceremony. Preserve explicitly approved project worker/repair limits and any operator caps. Use the operational contract's completion rules; these authoring notes grant no extra retry or repair authority. Prefer a small independently verifiable outcome well below the handoff range.

Use existing check definitions for exact argv, coverage and deadlines. Identify focused checks for the edit loop and required journey/boundary checks before delivery; say what failure evidence to retain (for example a minimized counterexample, trace or screenshot). Keep randomness seeded, state isolated and failures concise. Use property tests for relevant invariants and differential checks for migrations when useful; no new mandatory test layer or per-function TDD rule.

## Implementation input and context handoff

The coordinator completes the broad discovery needed for an actionable approved assignment before dispatch. Give the implementation agent the full applicable approved FR/NFR, invariants, scenarios, acceptance criteria, API/design decisions, exact ownership and code locations, relevant exploration findings, dependencies, exact checks/evidence, authority and remaining budgets. Include the full original assignment verbatim and its recorded hash. Bind the input to the approved contract and candidate; links alone do not replace required input. Split oversized assignments into real requirement-linked Beads tasks before dispatch; do not truncate requirements or include secrets.

Implementation agents inspect assigned code and approved context, test implementation assumptions and refine private design in short executable slices under OPERATIONAL_CONTRACT.md → Implementation discovery and routine completion. Report consequential missing or contradictory decisions before affected coding. Record what the code and checks established, what remains uncertain and the next step; the coordinator reconciles working design notes with verified behavior. Keep domain requirements in project artifacts and contracts, not common harness policy.

The context thresholds below apply only to implementation agents, not reviewers or other roles.

Isolated implementation workers use automatic monitored continuation. The coordinator observes runtime usage and session time, interrupts for a read-only handoff, archives the checkpoint, creates a real Beads child and launches a fresh worker after the old native process scope is stopped. DONE/LEFT/next action and full immediate parent prompt are preserved. Rollover does not reset original attempts, repair charges, scope, acceptance or aggregate timeout. Runtime usage is an early signal; no hard token ceiling is guaranteed. Invalid handoff, changed authority, uncertain cleanup or exhausted session/time limits retains work and blocks further launch.

For implementation agents outside the monitored worker transport, checkpoint proactively at roughly128k and finish before200k or the smaller actual context limit. Use actual readings honestly; if no reliable meter is available, checkpoint earlier.

Keep progress current after safe work units. For unfinished work, the coordinator creates a real Beads child task linked to its parent, with the remaining requirements, acceptance criteria, owned paths, dependencies and checks. Keep the parent incomplete until its children and original aggregate acceptance are satisfied. Workers propose the handoff; the coordinator owns Beads updates and verification.

Start the child in a fresh session with these input sections:

- **DONE:** completed changes, requirement/check mapping, verified results and evidence; distinguish partial or unchecked work.
- **LEFT:** unfinished requirements, partial edits, known failures, exact next edits/commands and remaining checks.
- **IMMEDIATE NEXT ACTION:** the first safe edit/check or exact blocker to resolve, near LEFT rather than buried in history.
- **FULL PARENT PROMPT:** the complete original assignment actually sent to the immediate parent, verbatim, with its recorded hash. Capture it at dispatch; a summary or link alone is insufficient.

Include the verified commit and dirty/untracked state, relevant approved constraints, authority references and remaining aggregate budgets. Preserve exact requirements and the full parent prompt while compressing progress. If essential input leaves insufficient working room, split earlier or block; preserve it rather than truncate. Keep secrets out of dispatch and handoff inputs. Incomplete/dirty state and failures are valid retained handoff facts; distinguish them from completed GREEN work. Do not roll back or discard partial work to manufacture a clean session.

Before the child writes, the coordinator verifies the handoff against current files/evidence and confirms the old worker and owned processes are quiescent. Continue with the recorded next step. A child inherits the parent's approved scope and remaining budgets; creation grants no new approval, retry or attempt allowance. Preserve original work, approvals, failures and attempt history. If scope or budget cannot accommodate continuation, use the existing blocked/amendment gates. The monitored worker transport automates this sequence; other coordinator-managed sessions follow it explicitly.

## Completion

Coordinator-generated evidence: delivered revision/artifacts, verification, review references and linked follow-ups. This is completion output, not an extra input-schema field. The coordinator applies task-kind gates; workers never close Beads work.

For epics, use **Success Criteria**: final outcome, required children and approval/integration/review/acceptance references. Do not repeat child designs.

For a fresh implementation input, put the complete applicable requirements, ownership, constraints and immediate executable acceptance checks first. Reference the relevant approved source/design paths; load detailed source as needed. Omit unrelated tracker narratives and historical verdicts. Handoff still retains the full parent prompt verbatim with its hash; keep DONE, LEFT and immediate next action concise without omitting requirements or remaining limits.
