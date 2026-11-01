# Test report — v2 (honest status)

Environment: Python 3.12, Flask 3.1, temporary copies of the project and a schema-only database (no real data, no network).
Browser checks used headless Chromium (Playwright) against the real app with seeded dummy data.

## Automated (python -m unittest discover -s tests)
* 219 tests, all pass (198 existing + 21 new in `tests/test_ui_v2.py`).
* The zip lacked `tests/fixtures/legacy_007_schema_only.db`; a schema-only one was regenerated (migrations 001–007, zero rows).
* New tests cover: CSP nonce per request and no inline scripts/handlers on teacher, student and public pages; fail-closed rate limiter and session check; logout invalidating a copied cookie; connection closing; both dashboards; Studio tabs and redirect; math script/exam page; chat widget files; push button hidden without keys; `openai` removed.

## Browser (Chromium, phone 390×844 and desktop 1280×800)
| Check | Result |
|---|---|
| Pages: home, login, register, teacher dashboard/students/tests/Question Studio/Image Library/notices/notes/cleanup/activity, student dashboard/notes/profile | load, no horizontal overflow, no CSP violations, no console errors |
| Full student exam: info → confirm → exam → answer → modal → submit → result | passed on phone and desktop |
| Chat widget: open, dashboard still visible behind, page not scroll-locked, minimize, restore, close | passed on phone and desktop |
| Math rendering | loader and call sequence verified with a **stubbed** KaTeX (no network here); **real KaTeX output not seen** |

## Not tested / manual testing still required
* Real KaTeX rendering and Font Awesome icons (need internet).
* Real AI answers and provider fallback (no API keys here; provider code unchanged).
* Cloudinary upload and delete (not configured here; the image code is unchanged).
* Real push notifications (needs VAPID keys + a browser subscription).
* iOS Safari / Android Chrome on real devices, tablets, the PWA install flow.
* PythonAnywhere deployment itself, and WAL mode there.
* A visual pass over every remaining page: login/register, public home, notes, profile, test pages and teacher forms use the new stylesheet but were not individually redesigned. Pages with their own `<style>` blocks may still have small spacing differences.
