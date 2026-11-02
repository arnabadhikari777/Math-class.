# Local চালানো ও Deploy

## Local
```bash
pip install -r requirements.txt
cp .env.example .env     # SECRET_KEY, TEACHER_USERNAME, TEACHER_PASSWORD ভরো
                         # local-এ FORCE_HTTPS=0 এবং FLASK_DEBUG=1 দাও
python app.py            # http://localhost:5000
python -m unittest discover -s tests -v    # নিরাপদ: temp কপিতে চলে, আসল DB ছোঁয় না
```

## PythonAnywhere
- WSGI ফাইলে: `from app import app as application` (আগের মতোই, বদলাতে হবে না)।
- নতুন ZIP আপলোডের সময় **`database.db`, `.env`, `uploads/` ওভাররাইট করবে না**।
- Web ট্যাব → **Reload**। স্টার্টে migration নিজে চলবে (আগে backup নেয়)।
- নতুন static ফাইল (`static/css|js|icons`) আসলে ব্রাউজারে একবার সাইট খুলো; service worker পুরোনো cache মুছে নেবে।
- Health check: `/api/v1/health`।

## Git
`.gitignore` এ আছে: `.env`, `database.db`, `backups/`, `uploads/notes/*`, `.secret_key`। এগুলো কখনো commit করবে না।


## Upgrade notes (v3: My Questions, Test Workspace, OTP diagnostics)
1. Back up `database.db` (`python -m backend.db.migrations backup`), upload the new files, Reload the web app. Migration 014 runs by itself.
2. **Do NOT set `LOCAL_OTP_MODE`, `FLASK_DEBUG=1` on the server.** If `LOCAL_OTP_MODE=console` is found there the app refuses to start (by design).
3. Email problems: in a PythonAnywhere Bash console run `python scripts/check_smtp.py`; the error log shows `[SMTP] stage=… status=…` lines (never the OTP or password). Free PythonAnywhere accounts can only reach Gmail via the allow-listed SMTP hosts; check their current network policy.
4. Rollback: restore the previous code; the database needs no change (see MIGRATION_GUIDE).
