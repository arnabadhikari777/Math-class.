# Stage 3 + 4 — exam page, autosave/timer, negative marking, result visibility

Builds on stages 1–2 (draft/publish, one attempt, two-step start). No table or row is deleted or rewritten.

## Root causes found (not guessed — each one is covered by a test)
| # | Problem | Cause | Fix |
|---|---|---|---|
| 1 | Students had to answer **every** question | every radio had `required` | removed; unanswered = 0 marks |
| 2 | Old local answers could overwrite newer server answers | page applied the server draft, then localStorage on top; submit let the form override the draft | server draft is the only truth; every change carries its own time; older changes are ignored |
| 3 | Autosave vs. submit race | autosave replaced the whole draft with whatever the page sent | autosave and submit are per-question merges inside one write transaction (`BEGIN IMMEDIATE`) |
| 4 | Final score ≠ formula | `_grade` did `max(0, score)` (silent floor) | formula shown as is; floor is opt-in (`MARKS_FLOOR_AT_ZERO=1`) |
| 5 | Result page showed *unanswered* as "wrong"/"Incorrect" | insights used `total - correct` | wrong / unanswered counted separately everywhere |
| 6 | No positive/negative breakdown on new results | migration 011 added the columns but nothing filled them for new attempts | filled when an attempt is closed, copied to `results` |
| 7 | "Results after teacher publishes" did nothing | `result_release` was stored but never checked and there was no release button | enforced on result page, leaderboard, dashboard, profile, `/api/v1`, AI context; teacher release/hide button |
| 8 | Double submit showed an error | second submit → flash + dashboard | second submit just goes to the (single) result |

## Database (migration 012, additive)
`exam_attempts.draft_meta TEXT` and `exam_attempts.draft_rev INTEGER NOT NULL DEFAULT 0`. Existing rows keep working (empty meta = "no timestamps yet"). Auto-backup runs first (existing migration runner). Rollback = restore the backup (columns are harmless if left).

## Student exam page (`templates/student/take_test.html`)
One question at a time · Previous / Next / **Skip** / Clear answer · palette with Answered / Not answered / Not visited · server-driven timer (monotonic clock, re-synced on every save, warnings at 5 min and 1 min) · **Saving… / Saved / Save failed – retrying / Offline** indicator · retry with back-off + on `online` · refresh-safe · final dialog (Total / Answered / Unanswered) · auto-submit at 0 · `beforeunload` warning only while unsent changes exist.
localStorage keeps **only unsent changes** (to replay after a refresh/offline); it is never shown as truth.

## Marking (`exam_service._grade`, the only place marks are computed)
`score = Σ marks(correct) − Σ negative(wrong)`; unanswered = 0, never negative; read from the attempt's frozen snapshot; rounded to 4 decimals (fractional negatives like 0.33 stay exact). Result page: Total, Obtained, Correct, Incorrect, Unanswered, Positive earned, Negative deducted, Final score, Percentage, Pass/Fail, per-question marks.

## Result publication
`result_release = immediate` → visible at once. `manual` → after submit the student sees *"Your examination has been submitted successfully. Results will be available after publication by your teacher."* (no score, answers, rank, leaderboard). Teacher → Tests → Results → **Release results / Hide results again** (sends no notification by itself).

## Files
Modified: `backend/services/exam_service.py`, `backend/routes/web/student.py`, `backend/routes/teacher/results.py`, `backend/services/student_service.py`, `backend/services/insights_service.py`, `backend/ai/context.py`, `backend/middleware/context.py` (`marks` filter), `backend/db/migrations.py`, `templates/student/take_test.html`, `templates/student/test_result.html`, `templates/teacher/test_results_view.html`, `tests/test_backend.py`, `tests/test_hardening.py` (floor assertion), `.env.example`.
Added: `backend/db/m012_autosave.py`, `tests/test_exam_stage34.py`, `STAGE_3_4_REPORT.md`.
Removed: nothing.

## Tests
`python -m unittest discover -s tests` → **191 tests, 0 failures** (17 new). Real-browser run (headless Chromium, mobile viewport): start flow, autosave, skip/palette, offline → reconnect, reload, clear, confirm dialog counts, submit, direct-URL blocks, auto-submit at deadline, 0 JS errors.
Expected-score checks: spec example 21/40; all correct; all wrong (−5); all unanswered (0); negative off; fractional 0.33 (7.01) and 0.5.

## Deploy (PythonAnywhere)
1. Bash: back up (`BACKUP_RESTORE_GUIDE.md` steps 2–4). 2. Upload/replace the files above (keep `.env`, `database.db`, `uploads/`). 3. No new packages. 4. Web tab → **Reload**; log shows `Migration 012…`; then `python -m backend.db.migrations check`. 5. Smoke test: publish a test → take it (answer some, skip some, refresh) → submit → result page → try opening it again.
Rollback: restore the backup DB and the previous files.

## Deliberately NOT in this stage
Teacher analytics/PDF polish (§13), old-code cleanup incl. best/latest leftovers in `refresh_counted_result` (§15), remember-me login (§12), soft-delete dialog (§11) — later stages. Old multi-attempt history is untouched.
