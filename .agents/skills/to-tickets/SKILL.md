---
name: to-tickets
description: Turn approved requirements and design into clear vertical-slice tasks for the repository-local Beads harness.
---

# To tickets

Adapted from Matt Pocock's pinned skill; provenance and license are beside this file.

Work from current approved requirements/design. Draft independently verifiable vertical slices with explicit blocking dependencies. Each slice crosses only the layers needed for its outcome. Prefer a thin working path; sequence wide mechanical refactors as expand, migrate, contract with a final integration check where needed.

Use the canonical task fields and task template documented in `docs/harness/README.md`. Include the outcome, functional/non-functional requirements, invariants/scenarios, relevant approved interfaces/types/design references, acceptance, exact check IDs, ownership and dependencies. Link authoritative artifacts instead of repeating the full design. Preparation tasks may list questions; implementation intent must resolve consequential choices.

Present the breakdown and any consequential choices for explicit human approval. Submit the canonical feature package to the trusted coordinator's `plan` command; it renders Beads bodies and creates native blocking edges. Implementation starts only after the coordinator verifies the audited operator confirmation. Use Beads as the task store; there is no external tracker publishing step or additional setup skill.

Routine repairs are execution attempts under the existing approved intent. Changed requirements, ownership, public contracts, invariants or budgets require HITL and a newly confirmed amendment. The coordinator owns tracker transitions and closure. Agents leave interactive confirmation to the operator.
