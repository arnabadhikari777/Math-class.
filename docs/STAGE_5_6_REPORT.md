# Stage 5 + 6 — delete dialog, secure persistent login, teacher results, validation, cleanup

(This ZIP contains **everything changed since stage 1-2**, so it can be extracted over a project that already has stage 3-4.)

## Delete test (no more typing)
* Teacher → Tests → **Delete** opens a dialog: *"Are you sure you want to delete this test?"* + test name + **Cancel / Delete Test**.
* It is a **soft delete**: the test disappears for students, but every question, attempt, result and answer is kept. It moves to **Deleted tests (can be restored)** → *Restore*.
* **Delete forever** (only from "Deleted tests", own dialog) removes the test and its data. No name typing anywhere. (The Cleanup page button, which was silently failing because of the old typed-name check, works again.)

## Login: Keep me signed in + real logout
* Student and teacher login have a *Keep me signed in on this device* checkbox (checked by default). Checked → persistent cookie (30 days), survives closing the browser. Unchecked → cookie disappears when the browser closes.
* Cookie: HttpOnly, SameSite=Lax, Secure on HTTPS (`FORCE_HTTPS`). It only holds ids — no password, no token in JavaScript/localStorage.
* Every login gets a random session id. **Logout writes it to `revoked_sessions`**, so a copied/old cookie is dead immediately (before, logout only removed the cookie in that one browser). Password reset / block still log out everywhere (existing `session_version`).
* Nobody is logged out by the upgrade: sessions created before it have no id and stay valid until they expire.
* Migration 013 (additive): table `revoked_sessions`. Old rows are purged automatically.

## Teacher results
Summary (submitted, average, highest, lowest, passed), Print / Save as PDF for the whole list, per-student: Correct / Wrong / Unanswered, negative deducted, per-question marks (+4 / −1 / 0), per-student PDF with the same breakdown. Release / hide results (stage 4) unchanged. No Best/Latest analytics remain.

## Validation
Duration 1–600 (server), negative marks 0–100 and now "nan"/"inf" are rejected, empty / invalid tests cannot be published (stage 1), published tests are locked until unpublished.

## Cleanup (verified before removing)
* Removed `templates/teacher/add_questions.html` — referenced by no route, template or test (the `add_questions` route only redirects to Question Studio).
* Removed the best/latest result-picking logic in `refresh_counted_result` (one attempt per test). Old tests that already have several historic attempts keep the result they had.
* Checked: no duplicate routes, no unreachable code after `return`, no other unreferenced template.
* Kept on purpose: the legacy DB columns `max_attempts` / `result_policy` (constant values; removing a column from a live DB is riskier than leaving it), and the old confirm page `/teacher/tests/<id>/delete` (GET, no typing) as the no-JavaScript fallback.

## Tests
`python -m unittest discover -s tests` → **198 tests, 0 failures** (23 new across stage 3-6). Real-browser (Chromium) run of the exam flow also passes.

## Deploy / rollback
Back up → replace the files → keep `.env`, `database.db`, `uploads/` → Web → Reload (log: `Migration 013…`) → try: login with/without the checkbox, logout then press Back, delete + restore a test.
Rollback: restore the backup DB + previous files (013 only adds a table).
