# DATABASE

SQLite, একটাই ফাইল: `database.db` (প্রজেক্ট root-এ)। ORM নেই।

- টেবিল তৈরি: `backend/db/schema.py` (`init_db()`, প্রতিবার স্টার্টে চলে, `IF NOT EXISTS`)।
- পরবর্তী পরিবর্তন: `backend/db/migrations.py` — শুধু additive (নতুন টেবিল/ইনডেক্স), কোনো DROP/DELETE নেই। প্রতিটির `down` আছে। প্রথম pending migration-এর আগে নিজে থেকে `backups/`-এ backup নেয় (শেষ ১০টি থাকে)।
- অ্যাপ স্টার্টে migration নিজে চলে। বন্ধ করতে `.env`-এ `BACKEND_AUTO_MIGRATE=0`।

```bash
python -m backend.db.migrations status
python -m backend.db.migrations backup
python -m backend.db.migrations migrate
python -m backend.db.migrations rollback <migration_id>
```

> `003_unique_results` ও `004_unique_attendance` শুধু তখনই UNIQUE index বানায় যখন ডুপ্লিকেট ডেটা নেই; নইলে skip করে (ডেটা বদলায় না), পরের স্টার্টে আবার চেষ্টা করে।

**Live সাইটে সতর্কতা:** নতুন কোড আপলোড করার সময় live `database.db`, `.env`, `uploads/` ওভাররাইট করবে না।
