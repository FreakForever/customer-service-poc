# Build loop prompt (use with Claude Code /loop or /goal)

```text
You are building the Matsui CS POC. Sources of truth: docs/requirements-registry.json, docs/build-steps.json,
docs-src/hld.md, docs-src/lld.md. Each iteration:
1. Read docs/build-steps.json. Pick the first step with status "in_progress", else the lowest-numbered "todo"
   step whose depends_on are all "done". If none remain, run `python tools/sync_docs.py --check` and `make test`, report, stop.
2. Set it to "in_progress" (started = today) and re-read its LLD sections (lld_ref) and the requirements in implements.
3. Implement the next unchecked checklist items. Keep changes scoped to this step. Follow LLD contracts exactly;
   if a contract must change, update docs-src/lld.md (and hld.md if architectural), add an entry to DECISIONS.md.
4. Run every command in the step's verify list. Fix until green. Never mark done with failing checks.
5. Update the step: progress_pct = completed checklist items / total * 100; notes = evidence (test counts, metrics, commit);
   status = "done" and completed = today when all checks pass; "blocked" with a reason if you cannot proceed.
6. Update requirement statuses in the registry ("in_progress" / "implemented") for the step's implements.
7. Run `python tools/sync_docs.py` (regenerates Requirements.html, HighLevelDesign.html, LLD.html; must print OK).
8. Commit: "BS-xx: <title> (<progress>%)" with the requirement ids in the body.
Stop the iteration after one step is done or after meaningful progress; report step id, progress and next step.
```
