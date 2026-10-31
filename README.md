# Arnab Math Class

Online notes, timed MCQ tests, student analytics and a teacher admin panel, built mobile-first for JEE-minded students.
Flask + SQLite, no ORM. Installable as a PWA.

## Features
- Student registration with email OTP, login, forgot/reset password (OTP by email only)
- Notes (PDF), timed MCQ tests: draft/publish, one attempt, two-step start, one-question-at-a-time exam page with safe autosave, negative marking, teacher-controlled result release, answer review, leaderboards (see STAGE_3_4_REPORT.md)
- Smart dashboards: average ring, score trend, subject strength, rank, streak, coach tips
- Teacher panel: admit/verify/block/delete students, login-as, CSV export, tests and questions, results (+PDF), announcements, contact inbox, activity log, data cleanup
- Question Studio: Cloudinary image library + question builder
- AI assistant for students (Gemini/Groq/...) with chat monitoring for Sir
- JEE Main countdowns, Auto/Light/Dark theme, push notifications, JSON API `/api/v1`

## Quick start
```bash
pip install -r requirements.txt
cp .env.example .env        # then fill SECRET_KEY, TEACHER_USERNAME, TEACHER_PASSWORD, email settings
python app.py               # local: set FORCE_HTTPS=0 and FLASK_DEBUG=1 in .env
python -m unittest discover -s tests -v
```
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
[Architecture](docs/ARCHITECTURE.md) · [Database & migrations](docs/DATABASE.md) · [Deploy](docs/DEPLOY.md) · [JSON API](docs/API.md) · [AI assistant](docs/AI.md)
