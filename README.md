# Arnab Math Class

Online notes, timed MCQ tests, and teacher admin panel.

## Highlights
- **Top utility bar** with Teacher Login + Notes/Download shortcut
- **Mandatory mobile** on registration (displayed; OTP only via email)
- **Teacher admin**: search students, admit without OTP, verify, block, delete, login-as student, export CSV
- **Contact Sir** form (teacher mobile never shown)
- Contact inbox, activity log, announcements, leaderboards, answer review, dark mode, PWA

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
   Then edit `.env` and fill in real values (especially `SECRET_KEY`, `TEACHER_PASSWORD`, `EMAIL_APP_PASSWORD`).

4. Run:
   ```bash
   python app.py
   ```

Teacher default (change immediately via `.env`):
- Username: `Arnab sir`
- Password: `2026`

## Important Security Notes
- Never commit the real `.env` file to Git (it is already in `.gitignore`).
- Use a strong random `SECRET_KEY`.
- Use a Gmail **App Password**, not your normal Gmail password.
- Change the teacher password from the default `2026`.

## Security (added)
- CSRF tokens on every POST form and API call
- Teacher password stored hashed (change it in Teacher panel -> Change Password)
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
