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
