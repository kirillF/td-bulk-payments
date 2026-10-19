# Project-local commit workflow

Applies to td-bulk-payments only, by the operator's explicit project-local instruction.
The shared codex-harness workflow is unchanged.

1. Commit each coherent milestone after the relevant validation: a documentation
   contract, a working migration slice, or a focused correction. Include the source,
   tests and required configuration that make that milestone reviewable together.
2. Inspect the worktree and index first. Stage explicit owned paths or selected hunks;
   preserve unrelated work and existing staging. Keep `.env`, approval-store contents,
   generated files and private `.harness/` evidence out of commits.
3. Use a concrete commit subject and record the checks run and material unrun checks
   in its body. A commit is a source checkpoint, not proof of harness delivery,
   independent review or final acceptance.
4. Commit locally as work progresses. Push, merge, release and history rewriting
   require their own authorization. Preserve audited gates, artifacts and counters;
   a Git checkpoint grants no authority to apply an unapproved amendment.

For an existing mixed worktree, make only scoped commits with honest validation
limits. Do not relabel all inherited work as one new implementation slice.
