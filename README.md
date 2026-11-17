# Arnab Math Class

Online notes, timed MCQ tests, student analytics and a teacher admin panel, built mobile-first for JEE-minded students.
Flask + SQLite, no ORM. Installable as a PWA.

## Features
- Student registration with email OTP, login, forgot/reset password (OTP by email only)
- Notes (PDF), timed MCQ tests: draft/publish, one attempt, two-step start, one-question-at-a-time exam page with safe autosave, negative marking, teacher-controlled result release, answer review, leaderboards (see STAGE_3_4_REPORT.md and STAGE_5_6_REPORT.md)
- Smart dashboards: average ring, score trend, subject strength, rank, streak, coach tips
- Teacher panel: admit/verify/block/delete students, login-as, CSV export, tests and questions, results (+PDF), announcements, contact inbox, activity log, data cleanup
- My Questions (central question library) and Test Workspace (create / import / edit / order the questions of one selected test), Cloudinary image library whose upload category defaults to the test name
- AI assistant for students (Gemini/Groq/...) with chat monitoring for Sir
- JEE Main countdowns, Auto/Light/Dark theme, push notifications, JSON API `/api/v1`

## Doubts, profile picture, login activity (Nov 2026 upgrade)
- **Doubt Management** (`backend/routes/web/doubts.py`, `backend/services/doubt_service.py`, migrations `m018_doubts.py` + the safety/repair migration `m019_doubts_schema_check.py`): students ask doubts (`/doubts`, optional Cloudinary picture); Sir answers in *Teacher > Doubts* (`/teacher/doubts`). The teacher answer and the AI answer are stored separately - only a teacher answer marks a doubt "Answered".
- **AI + doubts**: each doubt owns one `ai_conversations` row (`doubt_id`), so its AI history is never mixed with another doubt. The chat API accepts `doubt_id`; messages like "solve my maths doubt" are matched against the student's OWN doubts and the student picks when several match.
- **Profile picture**: *Profile > Add picture* (Cloudinary, initials stay as fallback). **Login**: "Email or Username" (no `@` needed).
- **Teacher > Login Activity** shows login date/time by student name in Asia/Kolkata. Students no longer see login history. New timestamps are stored zone-aware (`+05:30`); old zone-less rows are shown unchanged.
- **Answer-sheet PDF** embeds Cloudinary question images (only `res.cloudinary.com` of your cloud, size/time limited) and refuses to print Bengali with a font that cannot draw it (see `PDF_FONT_PATH` in `.env.example`).

## Quick start
```bash
pip install -r requirements.txt
cp .env.example .env        # then fill SECRET_KEY, TEACHER_USERNAME, TEACHER_PASSWORD, email settings
python app.py               # local: set FORCE_HTTPS=0 and FLASK_DEBUG=1 in .env
python -m unittest discover -s tests -v
python scripts/check_smtp.py   # finds out why Gmail OTP mail fails
```
No Gmail yet? Set `LOCAL_OTP_MODE=console` (with `FLASK_DEBUG=1`, `FORCE_HTTPS=0`) and the OTP is printed in the terminal. Windows beginner guide: **[LOCAL_TESTING_GUIDE.md](LOCAL_TESTING_GUIDE.md)**.
Teacher login stays disabled until `TEACHER_USERNAME` and `TEACHER_PASSWORD` are set in `.env`.

## Project structure
```
app.py                entry point
backend/              all server code (config, db, middleware, routes, services, ai, utils)
templates/            HTML  (public/ auth/ student/ teacher/ partials/)
static/               css/ js/ icons/ + manifest.json, sw.js
uploads/notes/        uploaded PDF notes (not in git)
tests/                unit tests (run on a temp copy, never touch database.db)
docs/                 ARCHITECTURE, DATABASE, DEPLOY, API, AI
```
Where to put new code: see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Common edits
| What | Where |
|---|---|
| JEE exam dates | `backend/config.py` -> `JEE_SESSIONS` |
| Pass mark, OTP limits, site name | `backend/config.py` |
| Colours / design tokens | top of `templates/base.html` |
| AI keys and student-facing AI names | `.env` (see `.env.example`) |

## Security notes
- Never commit `.env`, `database.db`, `.secret_key` or `uploads/notes/*` (all in `.gitignore`).
- CSRF on every POST, rate limits on login/OTP/contact, HTTPS redirect + HSTS, security headers.
- Use a strong random `SECRET_KEY` and a Gmail **App Password** for email.

## Docs
[Local testing guide](LOCAL_TESTING_GUIDE.md) · [Implementation & test report](IMPLEMENTATION_AND_TEST_REPORT.md) · [Architecture](docs/ARCHITECTURE.md) · [Database & migrations](docs/DATABASE.md) · [Deploy](docs/DEPLOY.md) · [JSON API](docs/API.md) · [AI assistant](docs/AI.md)
