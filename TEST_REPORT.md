# TEST_REPORT

Command: `python -m unittest discover -s tests` — **154 tests, 0 failures, 0 errors** (run 4 times in a row; ~3 s). Tests run on a temporary copy; the real database is never opened. Extra manual check: the real `database.db` backup was migrated on a copy and 14 student/teacher pages returned 200 (integrity ok, 0 FK violations).

Not tested (no network here): real Cloudinary calls, real Web Push delivery, real email/OTP. These use mocks.

## New tests (tests/test_hardening.py — 39) — all PASS
Exam: start→snapshot+freeze · edit/delete questions mid-exam changes nothing · new version for new attempts · version editable before first attempt · reload resumes same deadline · browser cannot extend time, late submit uses saved draft · late submit rejected (policy) · server sweep closes expired attempt · double/parallel submit → one result · multiple attempts + best/latest + limit · negative marking · result review after questions deleted · migration of an old result builds snapshot.
Student: archive/restore/history kept/login blocked · block ≠ archive · permanent delete rules · raw DELETE blocked by FK · unauthorized access.
Teacher: archive test keeps results, previewed permanent delete, rules editing · impersonation read-only/audited/teacher pages closed/expiry/exit · login regenerates session.
PDF: valid, fake, wrong extension, empty, 16 MB, duplicate filename, shared file, safe delete, DB failure leaves no orphan, failed file delete retried.
Security: push endpoint validation (11 bad cases), ownership, duplicate subscribe, hijack 409, SSRF skip · logout/OTP POST+CSRF · unsafe redirect · CSV injection · CSP headers.
Database: FK integrity + health tools · destructive rollback refused · duplicate attendance archived · atomic upsert · AI context from snapshot.
Image library (tests/test_question_bank.py updated): used image blocked incl. exam snapshot, Cloudinary failure → hidden + queued, reconcile retries and deletes.

## Existing 115 tests — all PASS after being updated for intentional changes
(GET logout → POST, test_attempts → exam_attempts, impersonation is a student session, timer now default-on, numeric Cloudinary key in test env, `/resend-otp` POST in route baseline, rollback needs force flag).
