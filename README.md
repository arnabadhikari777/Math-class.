# Arnab Math Class

Online notes, timed MCQ tests, smart student analytics and a teacher admin panel, built for JEE-minded students and designed mobile-first.

## Highlights
- **Modern UI/UX**: glass navbar, gradient hero, rounded cards, progress rings, mobile bottom navigation, smooth micro-interactions
- **3-way theme switch**: Auto (follows the device) / Light / Dark. Saved per browser, applied before first paint (no white flash)
- **JEE Main 2027 countdowns**: Session 1 (22 Jan 2027, 9:00 AM IST) and Session 2 (2 Apr 2027, 9:00 AM IST), live in the header on every page and as big cards on Home and both dashboards
- **Smart student dashboard**: average ring, score trend, subject strength, class rank, daily streak, weekly goal, XP/levels and a personalised "Smart coach"
- **Smarter results page**: accuracy, rank, percentile, class average, a plain-language summary and a jump-list of the questions you got wrong
- **Safer, friendlier test taking**: question navigator, answered counter, answers and timer survive a page refresh, auto-submit when time ends
- **Teacher analytics**: class average, 7-day activity, average per test, students needing attention, top performers, hardest questions
- **Top utility bar** with Teacher Login + Notes/Download shortcut
- **Mandatory mobile** on registration (displayed; OTP only via email)
- **Teacher admin**: search students, admit without OTP, verify, block, delete, login-as student, export CSV
- **Contact Sir** form (teacher mobile never shown)
- Contact inbox, activity log, announcements, leaderboards, answer review, PWA, push notifications, AI assistant

## UI / UX
- Design tokens (colours, radius, shadows) live at the top of `templates/base.html`, so every page inherits the same look. Change `--primary` / `--primary-grad` there to re-brand the whole site.
- Mobile: a bottom tab bar appears for logged-in students (Home, Tests, Notes, Profile) and for Sir (Home, Students, Tests, Notices, Inbox). The AI chat button floats above it.
- Light and dark mode are fully themed, including forms, tables, badges, the notification panel and the AI assistant. The browser/status-bar colour follows the theme.
- Respects `prefers-reduced-motion`.

### Theme switch
The `🖥 ☀️ 🌙` control in the navbar sets **Auto**, **Light** or **Dark**. Auto follows the device and reacts live if the phone switches between day and night mode. The choice is stored in `localStorage` (`theme`).

## JEE Main countdown
- Configured in `app.py` -> `JEE_SESSIONS` (ISO time **with** the IST offset `+05:30`):
  ```python
  JEE_SESSIONS = [
      {"id": "s1", "short": "Session 1", "label": "JEE Main 2027 · Session 1",
       "start": "2027-01-22T09:00:00+05:30", "window_days": 10},
      {"id": "s2", "short": "Session 2", "label": "JEE Main 2027 · Session 2",
       "start": "2027-04-02T09:00:00+05:30", "window_days": 10},
  ]
  ```
- If NTA changes a date, edit that one line and restart. Nothing else needs touching.
- `window_days` is how long the card shows **Live now** after the start time before it is marked done and hidden from the header. Adjust it to the real exam window.
- The countdown runs in the browser (`static/countdown.js`) against an absolute timestamp, so it is correct in every time zone. A server-rendered "days left" is the no-JavaScript fallback.
- The coach tips change with the time left (e.g. "full-length mocks" in the last 2 weeks).

## Smart insights (how it works)
All analytics are computed read-only on the server from existing tables (`results`, `answers`, `tests`, `students`), so **no database migration is needed**.

| Where | What it shows |
|-------|---------------|
| Student dashboard | Average ring, best score, tests this week vs goal (3), pending tests, score trend (last 10), subject strength, class rank, streak, XP/level, Smart coach tips |
| Result page | Correct/wrong, accuracy, rank, percentile, class average, comparison with your usual average, list of wrong questions |
| Teacher dashboard | Class average, active students (7d), submissions per day, average per recent test, at-risk students (average below pass mark or inactive for 14+ days), top 5, hardest questions |

Subjects come from each test's **Subject** field, so fill it in when creating a test to get useful subject analysis. Helper functions: `build_student_insights`, `build_result_insights`, `build_teacher_insights` in `app.py` (they never raise: on any error the page simply renders without the extra panel).

## Test taking
- Question navigator chips turn solid as you answer; the header shows `answered / total`
- The timer deadline and your selected options are saved in the browser (per student and per test), so refreshing no longer restarts the clock. Saved data is removed on submit
- The timer turns red in the last 5 minutes; the test auto-submits at 00:00

## Setup

1. Clone / extract the project
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Create your environment file:
   ```bash
   cp .env.example .env
   ```
   Then edit `.env` and fill in real values (especially `SECRET_KEY`, `TEACHER_USERNAME`, `TEACHER_PASSWORD`, `EMAIL_APP_PASSWORD`).

4. Run:
   ```bash
   python app.py
   ```

Teacher login: set `TEACHER_USERNAME` and `TEACHER_PASSWORD` in `.env` (no default; login stays disabled until both are set).

## Important Security Notes
- Never commit the real `.env` file to Git (it is already in `.gitignore`).
- Use a strong random `SECRET_KEY`.
- Use a Gmail **App Password**, not your normal Gmail password.
- Use a strong teacher password in `.env`; keep `.env` private (never upload it to GitHub).

## Security (added)
- CSRF tokens on every POST form and API call
- Teacher username/password live only in `.env` (`TEACHER_USERNAME`, `TEACHER_PASSWORD`), compared in constant time and re-read on every login: edit `.env` and it applies immediately, no restart
- Student/teacher passwords: min 8 chars, letters + numbers (existing accounts keep working)
- Rate limits on login, teacher login, register, OTP verify/resend and contact
- Debug off by default (`FLASK_DEBUG=1` only locally); HTTPS redirect + HSTS (`FORCE_HTTPS=0` for local http)
- Random `.secret_key` auto-created if `SECRET_KEY` is not set
- Security headers (nosniff, frame protection, referrer policy)

## Forgot Password (email OTP)
- Login page -> **Forgot password?** -> enter email -> enter OTP + new password
- OTP goes to **email only** (never mobile): 6 digits, valid 10 minutes, single use
- Old OTP is invalidated whenever a new one is requested; OTPs are stored hashed (HMAC), not in plain text
- Rate limits: 60s cooldown + max 5 OTP requests per email per hour (counted even for unknown emails) + 10 requests/hour per IP
- Brute-force guard: an OTP is burned after 5 wrong guesses; reset failures all show one generic message
- Same response whether or not the email has an account (no account leak); mail is sent in the background so timing doesn't leak either
- Blocked / unverified accounts never receive a reset OTP
- Strong-password rule (8+ chars, letters + numbers) is checked *before* the OTP is consumed
- After a successful reset **every device is logged out** (`students.session_version`) and a "password changed" email is sent
- Blocking a student now also ends their live session immediately
- CSRF token on every form; new DB columns (`students.session_version`, `otp_codes.attempts`) are added automatically on first start

## Modular backend (added)
- `backend/` package: JSON API v1, attendance, migrations, access guards. `app.py` only calls `register_backend(app)`.
- Docs: `BACKEND_AUDIT.md`, `BACKEND_ARCHITECTURE.md`, `BACKEND_FEATURES.md`, `API_DOCUMENTATION.md`, `MIGRATION_NOTES.md`, `TEST_REPORT.md`.
- Tests: `python -m unittest discover -s tests -v` (runs on a temp copy; never touches `database.db`).
- Migrations: `python -m backend.migrations status|backup|migrate|rollback <id>`.

> AI Assistant ও Sir-এর chat monitoring: দেখুন `AI_INTEGRATION.md`।

## Project layout (UI-related files)
```
templates/base.html          shared layout, design tokens, theme + nav scripts
templates/_jee_cards.html    reusable JEE countdown cards
templates/index.html         public home
templates/student_dashboard.html, test_result.html, take_test.html
templates/teacher_dashboard.html
static/countdown.js          live countdown (chips + cards)
static/ai-widget.css         AI assistant styles (theme-aware)
```

## Changelog (UI/UX upgrade)
- New design system + Auto/Light/Dark theme switch
- JEE Main Session 1 and Session 2 countdowns
- Smart dashboards for students and Sir, richer result analysis
- Test page: navigator, refresh-safe timer and answers, safer toast rendering
- README refreshed; widget asset versions bumped (`?v=8`) so installed PWAs pick up the new look
