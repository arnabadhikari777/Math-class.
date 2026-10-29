# MIGRATION_NOTES.md

## নীতি
শুধু additive: নতুন টেবিল/ইনডেক্স। কোনো DROP/DELETE/UPDATE নেই বিদ্যমান ডেটায়। DB ফাইল প্রতিস্থাপন বা reset নেই; SQLite-ই থাকছে।

## Migration তালিকা (`schema_migrations` টেবিলে রেকর্ড হয়)
| ID | কাজ | Rollback |
|---|---|---|
| 001_test_attempts | নতুন টেবিল `test_attempts` | টেবিল drop (শুধু attempt-এর সময়ের তথ্য যায়) |
| 002_perf_indexes | 9টি read-index | index drop |
| 003_unique_results | UNIQUE(test_id,student_id) — **শুধু যদি ডুপ্লিকেট না থাকে**, নইলে SKIP + warning, ডেটা অক্ষত, পরের স্টার্টে আবার চেষ্টা | index drop |
| 004_unique_attendance | UNIQUE(student_id,date) — একই শর্ত | index drop |

## স্বয়ংক্রিয় আচরণ
অ্যাপ স্টার্টে (`init_db()`-এর পরে) pending migration থাকলে প্রথমে `backups/database-YYYYmmdd-HHMMSS.db` (SQLite online backup, সর্বশেষ ১০টি রাখা হয়), তারপর প্রতিটি migration আলাদা transaction-এ। ব্যর্থ হলে ওই migration rollback হয়, সাইট চালু থাকে। বন্ধ করতে `.env`-এ `BACKEND_AUTO_MIGRATE=0`।

## Deployment-এর আগে (PythonAnywhere)
1. Bash console: `cd ~/<project> && cp database.db database.pre-backend-$(date +%F).db` (নিজস্ব ম্যানুয়াল backup)
2. নতুন ফাইল আপলোড/এক্সট্র্যাক্ট (**আপনার লাইভ `database.db` ও `uploads/`-কে ZIP-এর ফাইল দিয়ে ওভাররাইট করবেন না**)
3. `python -m backend.migrations status` → pending দেখাবে
4. Web tab → Reload (স্টার্টে migration চলবে) অথবা হাতে `python -m backend.migrations migrate`
5. `python -m backend.migrations status` → সব `[applied]` (003/004 skip হলে কারণ লগে থাকবে)

## Rollback
- একটি migration: `python -m backend.migrations rollback 002_perf_indexes` (আগে নিজে backup নেয়)
- পুরো ফেরত: অ্যাপ বন্ধ/Reload-এর আগে `cp backups/database-<stamp>.db database.db` — এতে তারপরের নতুন ডেটাও হারাবে, তাই শেষ উপায়
- কোড ফেরত: পুরোনো `app.py` ও `static/sw.js` রাখলেও `backend/` উপেক্ষিত হয়; ফাইল থাকা DB-কে ক্ষতি করে না

## পরীক্ষিত
Original DB-র কপিতে: সব পুরোনো সারি অপরিবর্তিত, backup তৈরি, migration idempotent ও reversible, ডুপ্লিকেট থাকলে SKIP (দেখুন TEST_REPORT.md)। আসল `database.db`-র SHA-256 পরীক্ষা শেষেও একই।
