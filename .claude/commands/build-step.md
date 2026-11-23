---
description: Run one Matsui CS build step through the build loop (e.g. /build-step bs-02; no argument = next eligible step)
argument-hint: "[bs-NN]  (optional; default = next eligible step)"
---

Run exactly ONE build step of the Matsui CS POC, following `docs/BUILD_LOOP.md`.

Requested step: `$ARGUMENTS`

Sources of truth: `docs/requirements-registry.json`, `docs/build-steps.json`, `docs-src/hld.md`, `docs-src/lld.md`,
`DECISIONS.md`. No local Docker: GCP only (LLD §18, D-015).

## 1. Pick the step
- Normalise the argument: `bs-2`, `BS-02`, `2` all mean `BS-02`.
- **No argument:** take the first step with status `in_progress`, else the lowest-numbered `todo` step whose
  `depends_on` are all `done`. If none remain, run `make docs` and `make test`, report the totals and stop.
- **Argument given:**
  - Unknown id: list valid ids and stop.
  - Already `done`: say so, show its notes, and stop. Don't redo it unless the user explicitly asked to rework it.
  - Any `depends_on` not `done`: list the unfinished dependencies and stop. Don't start out of order.
  - `blocked`: show the reason and say what would unblock it, then stop.

## 2. Before coding
- Set the step to `in_progress` with `started` = today (keep an earlier `started`).
- Re-read its `lld_ref` sections in `docs-src/lld.md`, and every requirement in `implements`.
- Look at `DECISIONS.md` open decisions (`OD-*`) that this step's "Decide by" column names. If one needs the user's
  call (provider, model, brand, hosting…), ask with AskUserQuestion, recommended default first. If unanswered,
  use the documented default and record that in the step notes.
- Billable or outward-facing actions (new GCP resources, deploys, sending email) beyond what earlier steps
  already set up: confirm with the user first.

## 3. Implement, verify, record
- Implement the checklist items, scoped to this step only. Follow LLD contracts exactly. If a contract must
  change, update `docs-src/lld.md` (and `hld.md` if architectural) and add a `D-nnn` row to `DECISIONS.md`.
- Run every command in the step's `verify` list and fix until green. Never mark `done` with a failing check.
  Long commands (Cloud Build, Cloud SQL) run in the background; `make dev` is started in the background, probed
  with curl, then stopped.
- Update the step in `docs/build-steps.json`:
  - `progress_pct` = completed checklist items / total × 100.
  - `notes` = evidence: test counts, URLs, build ids, deviations (D-nnn), known gaps.
  - When everything passes: `status` = `done`, `completed` = today. If you can't proceed: `blocked` with the reason.
- In `docs/requirements-registry.json`, set each requirement in `implements` to `in_progress`, or to `implemented`
  only when every acceptance criterion is met. If other build steps also trace to it, it stays `in_progress`
  until they're done.
- Run `make docs` (sync_docs.py through uv; must print `OK`). Then `LLD.html` §19 shows the new status and progress.

## 4. Commit and report
- Check that no secrets are staged (`.env`, credentials, tokens). Commit with this message:
  `BS-xx: <title> (<progress>%)`, then a body with `Implements: <requirement ids>` and a short change list.
- Report in a few lines: step id, status, progress, verify results, deviations, gaps, and the next eligible
  step (`/build-step` to run it).

Known project facts: there's no `python` on PATH (use `uv run` / `make docs`). Cloud Run health is `/health`, not
`/healthz` (D-018). Run `make env` to refresh `.env` from Secret Manager.
