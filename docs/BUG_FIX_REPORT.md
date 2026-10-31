# BUG_FIX_REPORT — Arnab Math Class

Every item was verified against the source code before being fixed. Status: **Confirmed Bug**, **Potential Risk**, **Already Protected**.

## Examination
| # | Problem | Status | Fix |
|---|---------|--------|-----|
| E1 | `answers`/`results` were `ON DELETE CASCADE` from `questions`/`students`/`tests`; editing/deleting a question changed/erased old results; result page re-read live `questions` | Confirmed Bug | Per-attempt snapshot (`attempt_questions`), snapshot columns inside `answers`, FKs now `RESTRICT`; result review reads only the snapshot |
| E2 | No exam versions / freeze | Confirmed Bug | `exam_versions` + `exam_version_questions`. First attempt freezes the version; later question edits create a new version for new attempts only |
| E3 | Timer enforcement off by default (`ENFORCE_TEST_TIMER=0`), start time recorded only on GET | Confirmed Bug | Deadline stored in `exam_attempts.deadline_at` at start. Always enforced (env value is now an emergency kill-switch, default ON). Late submit follows the teacher's rule (auto-submit saved draft, or reject) |
| E4 | `UNIQUE(test_id, student_id)` → one attempt only | Confirmed Bug | `exam_attempts` with `attempt_no`; teacher sets attempts allowed (0 = unlimited) and best/latest rule. `results` stays one row per student/test = the *counted* attempt (existing pages, leaderboard, PDFs unchanged) |
| E5 | Double submit / race | Potential Risk | Conditional `UPDATE … WHERE status='in_progress'` inside `BEGIN IMMEDIATE`; partial unique index allows one running attempt per student/test |
| E6 | Reload / disconnect lost work | Potential Risk | Server autosave (`/test/<id>/save`, every 4 s); reload resumes the same attempt with the server's remaining time |
| E7 | Negative marking not supported | Missing | `tests.negative_marks` (snapshotted per question; score never below 0) |

## Students / tests
| S1 | Deleting a student cascaded away results, attendance, AI chats, login history | Confirmed Bug | Archive / Restore (separate from Block). FKs `RESTRICT`. Permanent delete only for archived students, shows impact, requires typing the email |
| S2 | `delete_test()` and Cleanup "delete inactive tests" erased all results | Confirmed Bug | Test Archive/Restore; Cleanup button now archives; permanent delete shows impact + typed-name confirmation |
| S3 | Leaderboard/attendance included archived students | Potential Risk | Filtered |

## Security
| Impersonation | `is_teacher` and `student_id` lived in one session; student write APIs were not blocked; no audit | Confirmed Bug | Impersonation is a **student** session (no `is_teacher`) → teacher pages/APIs reject it; all writes refused; opening a test (which starts a timer) refused; `impersonation_log` (start/end/ip/reason); 60-minute expiry; Exit rebuilds a clean teacher session |
| Session fixation | Student login did not clear the session | Confirmed Bug | `session.clear()` on login, logout, impersonation start/exit |
| Logout / OTP resend via GET | Confirmed Bug | POST + CSRF (GET `/logout` is now a harmless redirect). `/resend-otp` is POST-only (rate limit kept) |
| `request.referrer` redirects | Confirmed Bug (3 places) | `safe_back()` accepts only same-host referrers |
| Push unsubscribe | `DELETE … WHERE endpoint=?` | Confirmed Bug | Deletes only the caller's own row; subscribe validates https + known push-service hosts + keys + length; foreign-owned endpoint with different keys → 409; failing/expired subscriptions auto-removed; sender re-validates every endpoint (SSRF) |
| CSV formula injection | Confirmed Bug | Export-only sanitiser (`=`,`+`,`-`,`@`, tab, CR get a leading `'`); database values untouched |
| CSP / headers | Missing | CSP (see SECURITY_REPORT.md), COOP added |

## Storage
| Two image implementations (`cloudinary_client` vs `image_store`) and two usage checkers | Confirmed | One service `image_service` on `image_store`; `cloudinary_client` is a thin compatibility wrapper. One usage checker covering live questions, bank questions, exam versions, attempt snapshots and stored answers (by id **and** URL) |
| Image delete when Cloudinary fails | Potential Risk | Row hidden (`pending_delete`) + `deletion_queue` retry; `image_service.reconcile()` |
| Notes: `timestamp_name.pdf` collisions, extension-only validation, orphan file on DB error, delete could remove a shared file | Confirmed | uuid4 names, `%PDF-` signature, 15 MB limit, atomic write, orphan cleanup, reference-checked delete, failed file delete queued |
| Attendance duplicates | Already protected when no duplicates exist (verified on your backup: 0) | Migration 008 archives (never drops) duplicates, rebuilt table has `UNIQUE(student_id,date)`, saves use atomic upsert |
| AI context read live `questions` | Confirmed Bug | Built from answer snapshots (all attempts, with marks and date) |

## Not changed / limits (honest)
* CSP keeps `'unsafe-inline'` for scripts/styles because templates use inline code; other directives are strict. Removing it needs a template refactor you did not ask for.
* Structured (JSON) logging and removal of dead code/duplicate helpers were **not** done: no confirmed bug depended on them and the brief said no unnecessary refactoring.
* Live Cloudinary and real Web Push delivery were **not** tested (no internet here); they were tested with mocks.
* Results created before this upgrade had no snapshot. Their snapshot was rebuilt from the questions as they exist now (your backup: 1 result, 2 answers).
