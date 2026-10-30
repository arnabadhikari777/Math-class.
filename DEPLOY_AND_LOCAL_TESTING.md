# Local testing & deployment

## Local
```bash
pip install -r requirements.txt
cp .env.example .env      # fill SECRET_KEY, TEACHER_USERNAME, TEACHER_PASSWORD (+ FORCE_HTTPS=0, FLASK_DEBUG=1 locally)
python -m unittest discover -s tests -v     # safe: uses a temp copy of the DB
python app.py             # NOTE: first start migrates database.db (backup is made first). Use a COPY of the DB for experiments.
```
## PythonAnywhere
See MIGRATION_NOTES.md → "Deployment-এর আগে". নতুন pip package লাগে না. WSGI ফাইল অপরিবর্তিত (`from app import app`).
Deploy-এর পর: ব্রাউজারে একবার সাইট খুলুন (SW v7 পুরোনো cache মুছবে), `/api/v1/health` দেখুন।
## .gitignore
লাইভ `database.db` ও `uploads/notes/*` Git-এ রাখবেন না (এখন `database.db` কমেন্ট করা — আনকমেন্ট করুন)।
