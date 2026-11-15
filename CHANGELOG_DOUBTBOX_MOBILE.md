# Doubt Box reliability + mobile UI/UX upgrade

## Root cause of the Doubt Box HTTP 500
Two requests with the same `client_token` both passed the "already exists?" check; the second INSERT hit the unique
index `uq_doubts_token`, and the global `IntegrityError` handler re-raises on every route except `submit_test` -> 500.
Reproduced (`[302, 500]`) and fixed (`[302, 302]`, one row, one notification). No schema change / no new migration.
(Production logs were not available; this is the failure that could be reproduced.)

## Important bug fixes
- Atomic submit (`submit_doubt`): `BEGIN IMMEDIATE`, doubt + teacher notification in one transaction; token conflict returns the existing doubt; a failing notification (SAVEPOINT) no longer loses the doubt and is logged.
- Failure paths return 400/403/502/503 with friendly text; unexpected errors give a 6-char reference + server log, never a traceback. Form text is preserved.
- Orphan Cloudinary pictures are removed (or queued in `deletion_queue`) when the DB write fails or the request lost a race.
- Image validation is content-first (names without extension, .jfif OK; HEIC gets a clear message). One size limit (`BANK_IMAGE_MAX_MB`) is shown by the page, checked by JS and enforced by the server.
- 413 handler no longer sends students to a teacher-only page with the wrong limit.
- `onchange="..."` on the subject filter was blocked by the CSP (never worked) -> real JS listener + Search button.
- Teacher answer: notification failure is reported to the teacher instead of being silent; JSON save without reload.
- Profile picture now stays in the header after logout/login.
- Mobile header: Logout / notifications / theme were unreachable <=480px (header was 608px wide). Quick Actions button overlapped the AI button.
- Doubt-form text inputs had no `type` (no site styling, <16px -> iOS zoom).

## Features
- Ask a Doubt: subject chips + suggestions, counter, inline validation, picture picker (camera/gallery), preview, replace/remove, loading state, safe retry with the same token, draft kept in the session.
- My Doubts: search, subject + status filters (Waiting for Teacher / Answered / AI Answer Available), pagination that keeps filters, empty and error states.
- Doubt detail: picture zoom, copy answer, math rendering (existing KaTeX), Teacher vs AI sections clearly separated.
- Teacher: search, filters, previous/next/next-pending, inline save confirmation, unsaved-changes guard.
- Header: compact mobile header + accessible account menu (Logout / Exit to Teacher always one tap); desktop unchanged. Public pages get a "More" menu.
- Floating stack (AI button unchanged, Quick Actions above it), keyboard-open mode hides bottom bar/floating buttons, safe-area padding, bottom bar always returns at page end / on short pages.

## Tests
See the final message. New: `tests/test_doubt_reliability.py` (18), `tests/browser/mobile_audit.py`, `tests/browser/doubt_flow.py`.
One existing assertion changed: label "Answered by Teacher" -> "Answered" (as specified in the brief).
Pre-existing failure (also on the original upload): `test_old_unused_add_questions_template_is_gone`.
