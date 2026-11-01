# Upgrade v2 — security hardening, Question Studio merge, white-theme redesign

This release changes **code, templates and static files only**. It adds **no database migration** and
deletes **no data**. The AI provider system (`backend/ai/`, `backend/routes/api/ai_*.py` behaviour,
provider order, keys, fallback) is unchanged.

## What changed

| Area | Change |
|---|---|
| SQLite | `with get_db()` now also closes the connection; `busy_timeout` 15 s. WAL is **off by default** (set `SQLITE_WAL=1` to try it). |
| Rate limits | One limiter (`backend/middleware/rate_limit.py`) for the whole site incl. AI. If the database fails the request is refused and logged (it used to be allowed silently). Expired hits are cleaned at most every 10 minutes. |
| Session checks | One DB query per request (was two). If it cannot run: HTTP 503 + log (was: allowed). Logout still invalidates a copied cookie. |
| CSP | Scripts need a per-request nonce; `'unsafe-inline'` removed from `script-src`; `script-src-attr 'none'`. Inline `onclick/onsubmit` became `data-confirm`, `data-print`, `data-busy-text`, `data-disable-on-submit`, handled by `static/js/app.js`. `style-src` still allows inline `style=""`. |
| Dependencies | `openai` removed from `requirements.txt` (never imported; providers use `requests`). |
| Web Push | No built-in default VAPID keys. Without keys the "Enable alerts" button is hidden and a warning is logged at start-up. |
| Chat widget | Floating panel, no page scroll lock, Minimize/Close, own scroll for messages, input fixed at the bottom, phone = bottom sheet (~68 % height) so the dashboard stays visible; solid white styling. |
| Question Studio | One tab bar on every question/image page (Saved questions · Test questions · Image Library). `/teacher/bank/images` now redirects to the single Image Library. Question Bank (saved questions + import into tests) is kept. |
| Math | `static/js/math.js` renders `$…$`, `$$…$$`, `\(…\)`, `\[…\]` with KaTeX (from cdnjs) in exam, result, review and question-list pages. The AI widget keeps its own renderer. |
| Design | New `static/css/app.css`; new `base.html` shell (teacher sidebar with groups, student top bar, phone bottom navigation); new student and teacher dashboards. Dark mode and the JEE countdown were removed. |
| Removed code | `build_student_insights`, `build_teacher_insights`, `backend/utils/jee.py`, `partials/_jee_cards.html`, `static/js/countdown.js`, `templates/teacher/bank/bank_images.html`, dead `templates/teacher/add_questions.html`. `build_result_insights` (result review page) is kept. |

## Deploy on PythonAnywhere (safe order)

1. **Back up first** (Bash console):
   ```bash
   cd ~/mysite            # your project folder
   python -m backend.db.migrations backup        # writes backups/database-YYYYMMDD-HHMMSS.db
   cp database.db ~/database-before-v2.db        # second copy outside the project
   cp .env ~/env-before-v2.bak
   ```
2. Upload the new project files **over** the old ones, but **do not overwrite** `database.db`, `.env`,
   `.secret_key`, `uploads/` or `backups/` (this zip contains none of them).
3. `pip install --user -r requirements.txt` (nothing new; `openai` is simply no longer needed).
4. `.env`: optional additions — `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_CLAIMS_EMAIL` (push works only with your own keys). Leave `SQLITE_WAL` unset.
5. Web tab → **Reload**. Static files: the existing `/static/` mapping keeps working (new files are `static/css/app.css`, `static/js/app.js`, `static/js/math.js`). On phones that installed the PWA the service worker cache is renamed (`math-class-v9`), so the new CSS loads on the next visit.
6. Check: open `/login`, log in as a student, open the dashboard, open the chat; log in as teacher, open Dashboard → Question Studio → Image Library.
7. The migrations table is untouched; `python -m backend.db.migrations status` should list the same applied ids as before.

## Rollback

Code only: put back the previous project folder (keep the new `database.db` — it is compatible), Web tab → Reload.
If you ever need the data back: stop the site, `cp ~/database-before-v2.db database.db`, Reload.

## WAL (optional, advanced)

`SQLITE_WAL=1` lets reads and a write overlap. Do **not** enable it unless your PythonAnywhere storage supports it; turn it off again with `PRAGMA journal_mode=DELETE` if you see "disk I/O error".

## Known limits

* There is no test-start time in the database, so there is no "Upcoming tests" section (adding one would change the exam start rules).
* There is no separate teacher "Notifications" or "Results" page; results are reached from Tests.
* `style=""` attributes (≈200) remain, so `style-src` still allows `'unsafe-inline'`.
* KaTeX and Font Awesome come from cdnjs; offline, math stays as readable text and icons are blank (the mobile menu button uses a text glyph so it still works).
