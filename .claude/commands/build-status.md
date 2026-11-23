---
description: Show Matsui CS build progress and the next eligible build step (read-only)
---

Read `docs/build-steps.json` and report, without changing any files:
- Overall progress (average `progress_pct`) and steps done / in progress / blocked / todo.
- Per-phase progress (P0–P5).
- Any `in_progress` or `blocked` steps, with their notes or reasons.
- The next eligible step: the first `in_progress`, else the lowest-numbered `todo` whose `depends_on` are all `done`.
  Give its title, checklist and any open decisions (`OD-*` in `DECISIONS.md`) it needs, and say
  `/build-step <id>` starts it.
- Other `todo` steps whose dependencies are already satisfied (they could run next).

Keep it to a compact table plus a few lines.
