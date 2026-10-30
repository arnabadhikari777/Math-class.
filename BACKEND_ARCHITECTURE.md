# BACKEND_ARCHITECTURE.md

## কাঠামো
```
app.py                  # অপরিবর্তিত + শেষে মাত্র ৪ লাইন (register_backend)
backend/
  __init__.py           # register_backend(app): config পড়ে, migration চালায়, guard + blueprint যুক্ত করে
  db.py                 # get_db() — app.py-র একই database.db; escape_like()
  http.py               # JSON envelope (ok/fail), student_api/teacher_api decorator, pagination
  migrations.py         # additive+reversible migration, auto backup, CLI
  guards.py             # বিদ্যমান route-এর উপর class-access, attempt রেকর্ড, no-store header, IntegrityError handler
  routes/
    public_api.py       # /api/v1/health, /csrf, /session
    student_api.py      # /api/v1/me, /me/summary, /tests, /results, /results/<id>, /notes, /announcements, /progress, /attendance
    teacher_api.py      # /api/v1/teacher/overview, /students, /tests/<id>/results, /attendance (GET+POST)
  services/
    student_service.py  # student-এর read logic (scoring আগের মতোই, নতুন করে লেখা হয়নি)
    teacher_service.py  # overview, student search, test results
    attendance_service.py # validation + upsert
tests/                  # 43 টি unittest; temp-কপিতে চলে
```
## দায়িত্ব ও নিয়ম
- **routes**: শুধু HTTP (auth, param parse, status code)। **services**: SQL + business rule। কোনো route সরাসরি SQL লেখে না।
- Circular import নেই: `backend` কখনো `app` import করে না; `app.py` → `backend` একমুখী। `PASS_PERCENTAGE` `sys.modules` থেকে পড়া হয় (কপি নয়)।
- Database/connection: একই ফাইল, একই `PRAGMA foreign_keys`; কোনো দ্বিতীয় DB/ORM নেই। Session key (`student_id`, `is_teacher`, `impersonating`) অ্যাপের আগেরগুলোই।
- CSRF/rate-limit/session-validation: `app.py`-র আগের `before_request` হুক `/api/v1`-এও প্রযোজ্য (POST-এ `X-CSRFToken` লাগে)।
- Response: `{"ok":true,"data":...,"meta":{page,per_page,total,pages}}` / `{"ok":false,"error":{"code","message"}}`।

## ভবিষ্যতে নতুন feature যোগ করার পদ্ধতি
1. `backend/services/<feature>_service.py` — logic + validation (connection নেয়, dict ফেরত দেয়)।
2. `backend/routes/<feature>_api.py` — `Blueprint` + `@student_api`/`@teacher_api`।
3. `backend/__init__.py`-তে `app.register_blueprint(...)` এক লাইন।
4. নতুন টেবিল/ইনডেক্স লাগলে `backend/migrations.py`-এর `MIGRATIONS`-এ নতুন `(id, desc, up, down)` — শুধু additive।
5. `tests/test_backend.py`-এ test। **`app.py` ছোঁয়ার দরকার নেই।**

## বিদ্যমান route সরানো হয়নি
Template-নির্ভর 58টি route `app.py`-তেই আছে (regression test প্রমাণ করে)। ধাপে ধাপে সেগুলো services-এ সরানো যাবে Phase 2-এর পরে, যখন UI redesign স্থির হবে — এখন সরালে ঝুঁকি বেশি, লাভ কম।
