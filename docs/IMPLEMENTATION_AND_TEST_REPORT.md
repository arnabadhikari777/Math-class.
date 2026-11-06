# IMPLEMENTATION AND TEST REPORT — My Questions / Test Workspace / Image category / OTP / Local testing

Date of work: 2 Nov 2026. Environment: Linux sandbox, Python 3.12, **no internet (DNS fails), no Gmail, no Cloudinary account**.
Original project backed up before any edit (kept outside this ZIP). No database was dropped, reset or replaced.

## 1. Problems found
1. `tests/fixtures/legacy_007_schema_only.db` was missing from the ZIP → 11 of the existing tests could not even start (rebuilt, see §3).
2. **Import route had NO Published-test protection** (`/teacher/bank/import/<id>`): questions could be added to a published test. The existing test `test_03_full_flow` actually imported into a published test.
3. **No backend duplicate prevention** on import (only a UI badge); no duplicate prevention when saving to the library.
4. `question_set_image` (add/remove picture of a question) also had no published-test protection.
5. Navigation inconsistency: tab "Test questions" went to the test *list*; "Manage Questions" went (via redirect) to the workspace; sidebar/mobile nav said "Question Studio"/"Studio".
6. Workspace had no edit, no ordering, no preview, no totals, no import button, no "Save to My Questions".
7. New images were not filed under the test name; a category typed in the picker was created *before* the upload, so a failed upload left an empty category.
8. OTP mail: the error of port 587 was **overwritten** by the error of port 465, no stage logging, no config validation, spaces/odd characters in the App Password not handled, user saw one generic English message. (See §10.)
9. Three tests were already failing/stale before my work: service-worker cache name (v9 vs v10), `add_questions.html` still present although the old test says it is gone, dashboard heading "Recent results" vs "Recent Activity log".

## 2. Files changed
`backend/`: `config.py`, `bootstrap.py`, `db/migrations.py`, `services/{email_service,otp_service,image_service,exam_service,publish_service,cloudinary_client}.py`, `routes/teacher/{question_bank,question_studio,tests}.py`.
`templates/`: `base.html`, `partials/{_studio_nav,_image_picker}.html`, `teacher/{teacher_dashboard,test_review,manage_tests}.html`, `teacher/bank/{bank_questions,bank_question_form,bank_import}.html`, `teacher/studio/{studio_questions,studio_library}.html`.
Docs/config: `README.md`, `.env.example`, `.gitignore`, `docs/{ARCHITECTURE,DATABASE_SCHEMA,MIGRATION_GUIDE,DEPLOY}.md`.
Tests: `tests/{test_backend,test_ui_v2,test_question_bank}.py` (expectations updated: names, migration list, cache name, import into a draft).
**Removed (verified unused: no route/template/test referenced them):** `templates/teacher/add_questions.html`, `templates/teacher/bank/bank_images.html`.

## 3. New files
`backend/db/m014_workspace.py`, `backend/utils/question_hash.py`, `backend/services/question_library.py`, `templates/partials/_workspace_head.html`, `templates/teacher/studio/{workspace_home,studio_question_edit}.html`, `scripts/check_smtp.py`, `LOCAL_TESTING_GUIDE.md`, this report, `tests/{test_workspace_v3,test_otp_v3}.py`, `tests/browser/run_browser_tests.py` (+3 screenshots), `tests/fixtures/legacy_007_schema_only.db` (**rebuilt**: schema-only, zero rows, produced by applying migrations 001-007 to an empty database).

## 4. Routes
New: `GET /teacher/studio/workspace` (test picker), `GET|POST /teacher/studio/tests/<id>/questions/<q>/edit`, `POST …/questions/<q>/move`, `POST /teacher/studio/questions/<q>/save-to-library`.
Changed behaviour: `/teacher/bank/import/<id>` (draft-only, backend duplicate refusal, reports counts/reasons, returns to the workspace), `/teacher/bank/questions/new|edit` (duplicate refusal 409), `/teacher/studio/tests/<id>/questions` (full workspace), `/teacher/studio/tests/<id>/questions/save` (order), `/teacher/studio/questions/<q>/image` (draft-only), `/teacher/studio/library/upload` + `/api/upload` (lazy category).
Unchanged and still working: every old URL (`/teacher/tests/<id>/questions` → redirects to its workspace; `/teacher/bank/*`; `baseline_routes.json` test passes).

## 5. API changes
`GET /teacher/studio/api/images` accepts `test_id` and adds `default_category {id|null, name, exists}`. `POST /teacher/studio/api/upload` accepts `test_id`; rule order: `category_id` > typed `new_category` > test title. `create_and_send_otp()` now always returns `None` instead of the OTP as 3rd value (nobody used it). `/api/v1` is untouched.

## 6. Database migration `014_workspace` (additive only)
`questions.sort_order` (NULL = old id order), `questions.source_bank_id`, UNIQUE index `uq_questions_test_source (test_id, source_bank_id) WHERE source_bank_id IS NOT NULL`, `bank_questions.content_hash` (+index, filled for existing rows). Backup is taken automatically before it runs. No id, image URL, public id, result or attempt changed. Tests check integrity_check = ok and foreign_key_check empty. **Do not run the new code with `BACKEND_AUTO_MIGRATE=0` before migrating once.**

## 7. My Questions — workflow
Central library `bank_questions`. Saving there never adds to a test. List/search/filter/edit/delete as before. Saving the same question again (same text, options, correct answer, image) is refused with a message naming the existing question. Import is done only from a chosen test (see §8). Deleting a library question leaves test copies and images untouched.

## 8. Test Workspace — workflow
Entry points all end in the same page `/teacher/studio/tests/<id>/questions`: Manage Tests → Manage Questions; after Create Test; sidebar/dashboard "Test Workspace" → test picker first; old URL redirect; import page and edit page show the same selected-test header and breadcrumb (`Manage Tests › <test> · Test Workspace › …`). Header shows test name, class, duration, Draft/Published, question count, total marks. Features: create several questions, Import from My Questions (multi-select, per-question marks, duplicates skipped with reasons, "N questions added"), edit (copy only), delete, per-question preview + link to the Review/Publish page, move up/down (stored in `sort_order`, used by review, publishing and the exam snapshot), change/remove picture, **Save to My Questions** (duplicate-proof, also allowed on published tests because it only reads the test). Published test: create/import/edit/move/delete/picture all refused in the backend (409/redirect with message) and hidden in the page.

## 9. Image category default
In the Test Workspace the picker receives the test id and name. Upload tab: default selected category = test name (existing category if present, otherwise an entry "<name> (new – created at upload)"); teacher can pick another category or type a new one; multi-file upload with a real progress bar and disabled UI while uploading; the new images appear at once in the library tab with a "নতুন" badge. Server rules: existing name (any letter case) is reused, never duplicated; a missing category is created **only after Cloudinary accepted the file, in the same transaction as the image row**; DB failure → Cloudinary delete (or queued retry through the existing mechanism) and nothing is left; Cloudinary failure → no row, no category. Old images are never re-categorised; renaming a test changes nothing old; used-image and non-empty-category delete protections unchanged; the general Image Library page still makes the teacher choose.

## 10. OTP — root cause (honest statement)
**I could not reproduce your Gmail failure** (sandbox has no internet, and I do not have your `.env` or Google account), so I **cannot prove which cause hit you**. What I proved in code: the old code hid the real error (587 error replaced by 465 error), logged no stage, did not clean the pasted App Password and showed one vague message — so the cause could never be seen. Most likely causes on Windows, in order: (1) `.env` not read or wrong values (file name `.env.txt`, BOM, a Windows environment variable with the same name overriding `.env`, Flask not restarted); (2) a normal Gmail password or an App Password that is not allowed (2-Step Verification off, Workspace/school account); (3) antivirus/VPN/firewall blocking or inspecting ports 587/465. Run `python scripts/check_smtp.py` (then `--send-to you@gmail.com`): it names the failing stage. Logs now look like `[SMTP] stage=login status=FAILED port=587 code=AUTH_FAILED …` (never the OTP/password; address masked). Students see a short Bengali message; the failed OTP row is deleted so the 60-second cooldown is not consumed.

## 11. Local OTP mode
`LOCAL_OTP_MODE=console` prints the OTP banner to the Flask terminal (stderr, not the logging system); no email is sent. Guards: startup `ConfigurationError` unless `FLASK_DEBUG=1` **and** `FORCE_HTTPS=0` **and** no hosting hint (PythonAnywhere, Heroku-style, Render, …); at send time the request host must be localhost/127.0.0.1/::1 (a LAN IP or domain is refused). The code is never in HTML/JSON/logs/return values; hashing, 10-min expiry, single use, 5 attempts, cooldown and CSRF are unchanged. Works for registration and password reset (tested).

## 12–15. Automated test numbers (this sandbox)
| | |
|---|---|
| Baseline before my changes | 24 tests found but 11 setup errors (missing fixture); after rebuilding the fixture 219 ran: 216 passed, **3 failed (pre-existing)** |
| **Final: total** | **275** |
| **Passed** | **275** |
| **Failed** | **0** |
| **Skipped** | **0** |
| New tests written | 56 (`test_workspace_v3` 36, `test_otp_v3` 20) |
| Browser (Playwright/Chromium, own throw-away DB, fake Cloudinary, console OTP) | 15 checks, 15 passed (registration + OTP in terminal, teacher flow, workspace navigation, picker default category, upload progress/new badge, import header, mobile no horizontal scroll) |
Fixes to old tests (not deleted): names (`My Questions`, `Test Workspace`), migration list +014, service-worker cache `v10`, import now targets a draft, dashboard heading text. The dashboard heading **"Recent Activity log"** above a *results* card looks like an accidental earlier rename; I left the UI as it is and aligned the test — please decide.

## 16. NOT really tested (cannot be proven by automated tests)
Real Gmail delivery; real Cloudinary upload/delete (all mocked); real AI providers; Web Push delivery; behaviour on Windows 10/PowerShell (written from the documented commands, not run on Windows); PythonAnywhere. A green test run is **not** proof that Gmail or Cloudinary work: use `scripts/check_smtp.py --send-to …` and the Cloudinary lines of the checklist.

## 17. Still to do (by you)
1. Run `LOCAL_TESTING_GUIDE.md` on your PC and mark the checklist; run `python scripts/check_smtp.py` and tell me the `[FAIL]` line if any.
2. Test one real upload with your Cloudinary keys (checklist section 8).
3. Decide on the dashboard heading ("Recent results" vs "Recent Activity log").
4. Before deploying: back up the production database, upload files, check `/teacher/studio/workspace`, keep `LOCAL_OTP_MODE`/`FLASK_DEBUG` out of the server environment.
5. Known limits: on a LAN/phone address the console OTP mode refuses (by design); a test title longer than 60 characters is cut to 60 for its category name; an imported question that was later edited keeps its origin link, so the *original* cannot be imported again into the same test.
