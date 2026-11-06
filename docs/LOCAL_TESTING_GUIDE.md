# LOCAL TESTING GUIDE — Arnab Math Class

For a **beginner on Windows 10 + VS Code**. Every step has the exact command, where to type it and what you should see.
Everything here runs on **your own computer with a new, empty local database. Never use the real (production) database for testing.**

> Words in `code font` are typed exactly. Lines starting with `PS>` are typed in the VS Code terminal (PowerShell).
> "Expected" tells you what a correct result looks like. If you see something else, go to [Troubleshooting](#f-troubleshooting).

---

## A. Initial setup

### A1. Extract the ZIP
1. Right-click `Math-class_-main.zip` → **Extract All…** → choose `C:\Projects\` → Extract.
2. You get a folder such as `C:\Projects\Math-class.-main` that contains `app.py`, `backend`, `templates`…
   *Expected:* `app.py` is directly inside the folder you open in step A2 (not inside one more sub-folder).

### A2. Open the folder in VS Code
`File → Open Folder…` → select `Math-class.-main` → **Select Folder**. Then `Terminal → New Terminal` (a **PowerShell** terminal opens at the bottom, already inside the project folder).

### A3. Check Python
```
PS> py --version
```
*Expected:* `Python 3.10` or newer (3.12 tested). If `py` is not found, install Python from python.org and tick **"Add python.exe to PATH"**.

### A4. Create the virtual environment (`.venv`)
```
PS> py -m venv .venv
```
*Expected:* a new folder `.venv` appears. Nothing is printed.

### A5. Activate it
```
PS> .\.venv\Scripts\Activate.ps1
```
*Expected:* the line starts with `(.venv)`.
**If you see "running scripts is disabled on this system"** use one of these safe options:
- Only for this terminal window (changes nothing permanent):
  ```
  PS> Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  PS> .\.venv\Scripts\Activate.ps1
  ```
- Or use the Command Prompt style script instead (no policy needed): `PS> .\.venv\Scripts\activate.bat`
- Or skip activation and call Python directly every time: `PS> .\.venv\Scripts\python.exe app.py`

### A6. Upgrade pip
```
PS> python -m pip install --upgrade pip
```

### A7. Install requirements
```
PS> pip install -r requirements.txt
```
*Expected:* `Successfully installed Flask-… reportlab-… pywebpush-… python-dotenv-…`

### A8. Verify the installation
```
PS> python -c "import flask, dotenv, reportlab, pywebpush, requests; print('imports OK', flask.__version__)"
```
*Expected:* `imports OK 3.x`. If it prints `ModuleNotFoundError`, the environment is not active (repeat A5) or A7 failed.

### A9. Create `.env` from `.env.example`
```
PS> Copy-Item .env.example .env
```
Open `.env` in VS Code and fill it as in [section B](#b-env-configuration). **Save it as plain "UTF-8"** (bottom-right of VS Code; not "UTF-8 with BOM").
`.env` is private and already in `.gitignore`: never upload it anywhere.

### A10. Database initialisation / migration
There is nothing to install. On the first start the app **creates `database.db` in the project folder** and applies all migrations automatically (it makes a backup first when a database already exists). To see the status yourself:
```
PS> python -m backend.db.migrations status
```
*Expected:* a list where every line starts with `[applied]` once the app has started once (`001_…` to `014_workspace`). Before the first start the file does not exist and every line says `[pending]`.

### A11. Start the website
See [section D](#d-running-the-application).

---

## B. `.env` configuration

Copy this block into `.env` for local testing and replace the values marked `<…>`:

```
SECRET_KEY=<any long random text, e.g. 40 random characters>
TEACHER_USERNAME=sir
TEACHER_PASSWORD=<a strong password you choose>
FLASK_DEBUG=1
FORCE_HTTPS=0
LOCAL_OTP_MODE=console
EMAIL_ADDRESS=
EMAIL_APP_PASSWORD=
EMAIL_SENDER_NAME=Arnab Math Class
CLOUDINARY_CLOUD_NAME=
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=
```
Create a random `SECRET_KEY` with: `PS> python -c "import secrets; print(secrets.token_hex(32))"`.

| Variable | What it does | Local value |
|---|---|---|
| `SECRET_KEY` | Signs login cookies and hashes OTPs. If empty the app creates `.secret_key` by itself. | any long random text |
| `TEACHER_USERNAME`, `TEACHER_PASSWORD` | Teacher (Sir) login. **Empty = teacher login is switched off.** | your choice |
| `EMAIL_ADDRESS` | Gmail address that sends OTP mails. | empty while using `LOCAL_OTP_MODE=console` |
| `EMAIL_APP_PASSWORD` | 16-letter **Google App Password** (not your Gmail password). Spaces are ignored. | empty or real |
| `EMAIL_SENDER_NAME` | Name shown in the email "From". | `Arnab Math Class` |
| `FORCE_HTTPS` | `1` redirects http→https. **Local must be `0`**, otherwise the browser loops or fails on `http://localhost`. | `0` (local only) |
| `FLASK_DEBUG` | `1` = debug mode (error pages, secure-cookie off). **Never on the real server.** | `1` (local only) |
| `LOCAL_OTP_MODE` | `console` = OTP is printed in the terminal, no email. **Local only.** The app refuses to start if it is set without `FLASK_DEBUG=1` and `FORCE_HTTPS=0`, or on a hosted server. | `console` (local only) |
| `CLOUDINARY_CLOUD_NAME` / `_API_KEY` / `_API_SECRET` | Image storage. The cloud name is the *"Cloud name"* in the Cloudinary dashboard (no spaces), the key is only digits. Alternatively one line `CLOUDINARY_URL=cloudinary://key:secret@cloudname`. | real values, from your Cloudinary dashboard |
| `GEMINI_API_KEY`, `GROQ_API_KEY`, `OPENAI_API_KEY`, … | AI chat. Any one key is enough; more keys = automatic fallback. | optional |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_CLAIMS_EMAIL` | Web-push notifications (all three needed). | optional |

**Which feature needs which setting**

| Feature | Needs | If missing |
|---|---|---|
| Whole site, student pages, exams | nothing (just Python packages) | — |
| Teacher login | `TEACHER_USERNAME` + `TEACHER_PASSWORD` | teacher login disabled |
| Registration / reset OTP (local) | `LOCAL_OTP_MODE=console` + `FLASK_DEBUG=1` + `FORCE_HTTPS=0` | app refuses to start / no OTP printed |
| Registration / reset OTP (real mail) | `EMAIL_ADDRESS` + `EMAIL_APP_PASSWORD` | "email could not be sent" message, details in terminal log |
| Image upload | all three `CLOUDINARY_*` | Image page shows a red message; existing images still display |
| AI chat | at least one AI key | AI says it is not available |
| Web push | all three VAPID values | push switched off (bell still works) |

> **Never** paste real keys into this guide, a screenshot, a chat or GitHub.

---

## C. Local database safety

- **Where is it?** `database.db` in the project folder (created at first start). The production database is *not* in the ZIP and must never be copied into this folder.
- **Create / migrate:** automatic at start. Manual: `PS> python -m backend.db.migrations migrate`
- **Status:** `PS> python -m backend.db.migrations status`
- **Backup before risky tests:**
  ```
  PS> python -m backend.db.migrations backup
  ```
  *Expected:* `Backup written to …\backups\database-YYYYMMDD-HHMMSS.db`
- **Integrity + foreign keys + duplicates (read-only check):**
  ```
  PS> python -m backend.db.migrations check
  ```
  *Expected:* JSON that ends with `"ok": true`.
- **Start again from zero (local only!):** stop Flask, then `PS> Remove-Item database.db` — the next start builds a clean database. Do this **only** in your local folder.
- **Test data:** use the website itself: admit a student (Teacher → Students → Admit student), create a test, add questions (see the checklist). Fake emails like `kid1@test.local` are fine with `LOCAL_OTP_MODE=console`.
- **Troubleshooting:** "database is locked" → close DB viewers and other Flask windows; `no such column sort_order` → migration 014 did not run, run `migrate`; "unable to open database file" → the folder is read-only / opened from inside a ZIP.

---

## D. Running the application

```
PS> python app.py
```
*Expected output (shortly):* `* Running on http://127.0.0.1:5000` and the warning lines about Web Push being off (normal without VAPID keys).
Open **http://127.0.0.1:5000** (or `http://localhost:5000`) in Chrome/Edge.

Local-only settings (must **not** be on the real server): `FLASK_DEBUG=1`, `FORCE_HTTPS=0`, `LOCAL_OTP_MODE=console`.
Stop the server with **Ctrl + C**. Port busy? Close the old window or change the port in the last line of `app.py`.

Useful URLs: `/` home · `/register` · `/login` · `/teacher/login` · `/teacher/dashboard` · `/teacher/bank/questions` (My Questions) · `/teacher/studio/workspace` (Test Workspace) · `/teacher/studio/library` (Image Library).

### D1. Where is the OTP?
With `LOCAL_OTP_MODE=console`, after you press *Register*, look at the **terminal where `python app.py` runs**:
```
==============================================================
  LOCAL OTP MODE (development only - no email was sent)
  purpose : registration
  email   : kid@test.local
  OTP     : 123456   (valid 10 min, single use)
==============================================================
```
Type that code on the *Verify OTP* page. The code is never shown in the browser. The OTP rules (10 min expiry, single use, 5 wrong attempts, 60 s cooldown, hashed in the database) are the same as in production.

### D2. Real Gmail OTP (optional)
1. Turn on **2-Step Verification** in your Google account (App Passwords only exist then). Google Workspace/school accounts may block App Passwords: use a personal Gmail.
2. Google Account → Security → **App passwords** → create one for "Mail" → copy the 16 letters.
3. In `.env`: `EMAIL_ADDRESS=<that gmail>`, `EMAIL_APP_PASSWORD=<16 letters, spaces allowed>`. Remove or comment `LOCAL_OTP_MODE` to receive real mail.
4. Check before you start the website:
   ```
   PS> python scripts/check_smtp.py
   PS> python scripts/check_smtp.py --send-to you@example.com
   ```
   It tells you exactly which stage fails (config → DNS → port 587/465 → TLS → login → send). Look in the inbox **and spam**.

---

## E. Complete manual testing checklist

Mark each line by editing this file: put an `x` in one box, e.g. `[x] Passed`.
Lines marked *(needs …)* cannot pass without that service; mark them **Not Configured** if you do not have it.

### 1. Public website

**Homepage opens at /**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Navigation links work**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Mobile responsive (browser dev tools, 390px wide)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Contact page: send a message (Sir sees it in Inbox)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Download/Static pages (/download)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

### 2. Authentication

**Student registration (LOCAL_OTP_MODE=console)** *(needs nothing external)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**OTP verification: right code, wrong code, expired, 5 wrong attempts, 60 s cooldown, resend, single use** *(local)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Login / Logout (logout really ends the session: press Back)** *(local)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Forgot password -> OTP in terminal -> Reset password** *(local)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Real email OTP via Gmail** *(needs EMAIL_ADDRESS + EMAIL_APP_PASSWORD)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Session security (cookie HttpOnly, CSRF error on forged POST)** *(local)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

### 3. Teacher dashboard

**Teacher login (/teacher/login)** *(needs TEACHER_USERNAME + TEACHER_PASSWORD)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Dashboard cards and Quick actions (My Questions, Test Workspace)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Student management (admit, edit, block, delete, export CSV)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Test management list**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Settings page**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Activity logs**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Notices and Inbox**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

### 4. Test management

**Create test (a draft)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Edit test rules (/settings) while draft**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Delete / Archive / Restore test**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Publish test (students notified once)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Unpublish, edit, republish**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Manage Questions button opens that test's Test Workspace**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Review & Publish page shows the same question order as the workspace**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

### 5. My Questions

**Save a new question (it is NOT added to any test)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Save the same question twice -> refused with a message**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Search by text; filter by image category; 'with image only'**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Edit a saved question**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Delete a saved question (test copies stay)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Import to test: tick several, set marks, see 'N questions added'**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Import the same question again -> 'already in this test'**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Import into a Published test -> refused**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

### 6. Test Workspace

**Open selected test: name, class, duration, status, question count, total marks are shown**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Sidebar 'Test Workspace' shows a list of tests first (no test = no creating)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Create several questions at once and save**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Edit a question (My Questions original unchanged)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Delete a question**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Preview (per question, and 'Preview & Publish' page)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Marks and total marks**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Move question up/down; order is kept in the exam**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**'Save to My Questions' on a test question; pressing it twice is refused**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Navigation identical from Manage Tests, sidebar, dashboard, old URL /teacher/tests/<id>/questions**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Published test: create/import/edit/move/delete are all blocked and the page says why**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

### 7. Image Library

**Create category; duplicate name (any letter case) refused**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Upload image from the Image Library page (you choose the category)** *(needs Cloudinary)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Upload several images at once** *(needs Cloudinary)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Search image by file name in the picker**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Select an existing image for a question**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Delete an unused image** *(needs Cloudinary)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Delete a used image -> refused**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Cloudinary upload works (image visible in your Cloudinary Media Library)** *(needs Cloudinary)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Cloudinary failure: wrong API secret in .env -> clear error, no image row, no empty category** *(needs Cloudinary (wrong value on purpose))*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Database sync: picker list = Image Library list = Cloudinary folder** *(needs Cloudinary)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

### 8. Image category default (Test Workspace)

**Open test 'Mathematics Chapter 1' > create question > Pick image > Upload tab: category shows 'Mathematics Chapter 1'** *(needs Cloudinary)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Upload without changing category: image is saved in category 'Mathematics Chapter 1'** *(needs Cloudinary)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Choose another category first: image goes there, test-named category is NOT created** *(needs Cloudinary)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Type a new category: it is created once; typing it again in other letter case makes no duplicate** *(needs Cloudinary)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Upload a second image for the same test: same category, count increases, no duplicate category** *(needs Cloudinary)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Pick an OLD image from the library: its category stays unchanged** *(local)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Rename the test: old images and category names do not change** *(local)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**After upload the new image is shown at once in the picker with a 'নতুন' badge** *(needs Cloudinary)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

### 9. Student examination

**Student login and Available tests**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Test instructions page**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Start test (two-step confirmation)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Question navigation / palette**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Answer selection**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Autosave (reload the page: answers remain)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Timer (server-checked; try a 1-minute test)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Submit (confirmation) and Result page**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Answer review**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Negative marking (set negative marks 1, answer wrong)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**One attempt rule (second start is refused)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Multiple attempts (only if Sir enabled it for an old test)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

### 10. AI features

**AI chat opens (floating button)** *(needs at least one AI key)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**AI answers a maths question** *(needs AI key)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Provider failure -> next provider answers** *(needs 2 AI keys, one wrong)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**API key missing -> friendly message, no crash** *(local (leave keys empty))*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Sir's AI Chats page and 'Test all AIs'** *(needs AI key)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Error messages are readable** *(local)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

### 11. Notifications

**In-app bell shows 'New test' after Publish**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Web push configuration (VAPID keys present)** *(needs VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_CLAIMS_EMAIL)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Browser permission prompt** *(needs VAPID + http://localhost)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Subscription saved** *(needs VAPID)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Delivery failure handled (expired subscription removed)** *(needs VAPID)*  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

### 12. Reports and analytics

**Student results list**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Test report (View Results)**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**Performance analytics on dashboards**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**PDF export of a student's answers**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

**CSV export of students**  
`[ ] Not Tested` `[ ] Passed` `[ ] Failed` `[ ] Not Configured`

---

## F. Troubleshooting

Each entry: **cause → how to check → which file/setting → fix.**

**ModuleNotFoundError: No module named 'flask' (or any module)**
Cause: virtual environment not active, or requirements not installed. Check: the prompt must start with `(.venv)`; run `pip list`. File: `requirements.txt`. Fix: `.\.venv\Scripts\Activate.ps1` then `pip install -r requirements.txt`. In VS Code also pick the interpreter: `Ctrl+Shift+P` → *Python: Select Interpreter* → `.venv`.

**ImportError: cannot import name …**
Cause: half-extracted/changed files, a stale `__pycache__`, or a package version mismatch. Check: the full traceback names the file. Fix: re-extract the ZIP into an empty folder, delete all `__pycache__` folders, `pip install -r requirements.txt` again. Always start with `python app.py` from the project folder.

**Port already in use / `OSError: [WinError 10048]`**
Cause: an older Flask window is still running. Check: `PS> netstat -ano | findstr :5000`. Fix: close that window or `PS> taskkill /PID <number> /F`; or change `port=5000` in `app.py`.

**sqlite3.OperationalError: database is locked**
Cause: another program (DB Browser) or a second Flask process holds the file. Check: close other windows. File: `database.db`. Fix: close them; the app waits 15 s by itself; if it persists, restart.

**Missing environment variables (teacher login disabled, SECRET_KEY warnings)**
Cause: `.env` missing, wrongly named, or in the wrong folder. Check: `PS> Test-Path .env` must be `True` in the project root; `python scripts/check_smtp.py` also reports `.env` problems. Fix: copy `.env.example` to `.env`, fill it, **restart Flask** (`.env` is read only at start). A same-named Windows environment variable overrides `.env`: remove it (`Remove-Item Env:EMAIL_APP_PASSWORD`).

**SMTP authentication failed (log: `stage=login code=AUTH_FAILED`)**
Cause: not an App Password, 2-Step Verification off, wrong address, or Google blocked the sign-in. Check: `python scripts/check_smtp.py`. File: `.env` (`EMAIL_ADDRESS`, `EMAIL_APP_PASSWORD`). Fix: create a new App Password (D2), paste again, save `.env`, restart. App Passwords are unavailable for accounts without 2-Step Verification, with Advanced Protection, or managed by an organisation that disabled them — use another Gmail.

**Gmail OTP not received**
Cause: send failed (see terminal), wrong recipient, spam folder, or Gmail daily limit. Check: the terminal shows `[SMTP] stage=… status=ok/FAILED`; check Spam. Fix: fix the failing stage; use `LOCAL_OTP_MODE=console` for testing. Rate limit: 1 OTP per minute per email.

**SMTP connection timeout / `CONNECT_TIMEOUT`, `CONNECT_FAILED`, `DNS_FAILED`, `TLS_CERT_FAILED`**
Cause: firewall, antivirus "web/mail shield", VPN or school/office network blocks ports 587/465 or inspects TLS; no internet. Check: `python scripts/check_smtp.py` (the Network step shows which port). Fix: allow Python in the Windows Firewall/antivirus, switch off the antivirus *email/HTTPS scanning* for a test, try another network (mobile hotspot), update Windows certificates.

**Cloudinary authentication failed ("Cloud name ভুল" / "API Secret ভুল" / "API Key ভুল")**
Cause: wrong value copied. Check: the red message on Image Library names the setting. File: `.env`. Fix: Cloudinary dashboard → copy *Cloud name* (letters/digits/-/_), *API Key* (digits), *API Secret*; no quotes or spaces; restart.

**Cloudinary upload failed**
Cause: no internet, file > 5 MB, not a real JPG/PNG/WEBP/GIF, or account disabled. Check: the message under the picker; terminal. Fix: smaller/valid image; check the internet; check Cloudinary account status. A failed upload never creates a database row or an empty category.

**AI API key error**
Cause: key empty, wrong, or quota finished. Check: Teacher → AI Chats → *Test all AIs*. File: `.env` (`GEMINI_API_KEY` …). Fix: paste a valid key, restart; with several keys, the next one answers automatically.

**HTTPS redirect loop / page does not open on http://localhost**
Cause: `FORCE_HTTPS=1`. File: `.env`. Fix: `FORCE_HTTPS=0` (local only), restart, clear the site's cookies.

**CSRF error / "Security check failed"**
Cause: page open for a long time, cookies blocked, mixing `localhost` and `127.0.0.1`, or a form without token. Check: use one address only. Fix: reload the page and submit again; clear cookies for the site.

**TemplateNotFound**
Cause: started Flask from another folder, or the ZIP was extracted incompletely. Check: the error names the missing template. Fix: `cd` into the project root (where `app.py` is) and start there; re-extract.

**JavaScript error / picker or buttons do not respond**
Cause: browser cache, blocked CDN (offline), or a script error. Check: `F12` → *Console* tab. Fix: hard refresh `Ctrl+F5`; allow the font/icon CDNs (internet needed for icons); clear site data (also removes the old service worker).

**Static file not found (404 for /static/…)**
Cause: wrong folder or old cached service worker. Fix: `Ctrl+F5`; DevTools → Application → *Service Workers* → Unregister; check the file exists under `static/`.

**Migration error ("no such column: sort_order", "014 failed")**
Cause: a migration did not run (the log line `backend migrations could not run` or `Migration … failed and was rolled back`). Check: `python -m backend.db.migrations status`. Fix: `python -m backend.db.migrations migrate`; if it still fails, send the message (a backup was taken before; restore with the file in `backups/`).

**The app does not start: `ConfigurationError: LOCAL_OTP_MODE=console is for local development only`**
Cause: the safety guard: console mode needs `FLASK_DEBUG=1` and `FORCE_HTTPS=0`, and is blocked on hosted servers. Fix locally: set both. On the real server: **delete** `LOCAL_OTP_MODE` from its environment.

---

## G. Automated tests (optional but recommended)

```
PS> python -m unittest discover -s tests -v
```
*Expected:* `Ran 275 tests … OK`. The tests use a temporary copy and a throw-away database, never your `database.db`, and fake Cloudinary/Gmail (no network). **Passing tests do not prove that real Gmail or Cloudinary work — use sections D2 and the Cloudinary lines of the checklist for that.**

Browser test (optional): `pip install playwright` then `python -m playwright install chromium` then `python tests/browser/run_browser_tests.py`.
