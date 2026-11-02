# ARCHITECTURE — কোড কোথায় আছে

আগে সব কোড একটাই `app.py`-তে (৩০০০ লাইন) ছিল। এখন `app.py` শুধু প্রবেশদ্বার; আসল কোড `backend/`-এ, কাজ অনুযায়ী ভাগ করা।

```
Math-class/
├── app.py                  # entry point (python app.py / gunicorn app:app / PythonAnywhere WSGI)
├── requirements.txt
├── .env.example            # কপি করে .env বানাও (secret এখানে, GitHub-এ নয়)
├── backend/
│   ├── bootstrap.py        # সব জোড়া লাগায়: middleware → pages → init_db → migrations → API
│   ├── config.py           # সব constant ও .env সেটিং (JEE_SESSIONS, OTP, VAPID ...)
│   ├── core.py             # Flask `app` অবজেক্ট + secret key + cookie সেটিং
│   ├── db/
│   │   ├── connection.py   # get_db(), escape_like()
│   │   ├── schema.py       # init_db(): সব টেবিল তৈরি
│   │   └── migrations.py   # নিরাপদ additive migration + backup + CLI
│   ├── middleware/         # প্রতিটি request-এর আগে/পরে যা চলে
│   │   ├── security.py     # https redirect, CSRF, security header
│   │   ├── auth.py         # session যাচাই, student_login_required, teacher_login_required
│   │   ├── rate_limit.py   # @rate_limit(...)
│   │   ├── guards.py       # অন্য class-এর test/note আটকানো, no-store cache
│   │   ├── context.py      # সব template-এ যায় এমন ভ্যারিয়েবল (class list, JEE, notification)
│   │   └── errors.py       # 404 / 500 / 413 পেজ
│   ├── routes/
│   │   ├── web/            # ওয়েবসাইটের HTML পেজ (ছাত্র + সবার জন্য)
│   │   │   ├── public.py        # home, download, contact
│   │   │   ├── auth.py          # login, register, OTP, forgot/reset password, teacher login
│   │   │   ├── student.py       # dashboard, notes, profile, test দেওয়া, result, leaderboard
│   │   │   ├── notifications.py # notification bell + push subscribe
│   │   │   └── pwa.py           # /manifest.json, /sw.js
│   │   ├── teacher/        # Sir-এর প্যানেল
│   │   │   ├── dashboard.py, students.py, notes.py, tests.py,
│   │   │   ├── results.py, cleanup.py, communication.py
│   │   │   ├── question_studio.py  # "Test Workspace" (এক Test-এর প্রশ্ন) + Image Library, URL /teacher/studio
│   │   │   └── question_bank.py    # "My Questions" (কেন্দ্রীয় লাইব্রেরি) + Import, URL /teacher/bank (পুরোনো URL একই আছে)
│   │   └── api/            # JSON API  (/api/v1/...)
│   │       └── public_api.py, student_api.py, teacher_api.py, ai_api.py, ai_admin.py
│   ├── services/           # business logic + SQL (route-এ সরাসরি SQL কম রাখার জন্য)
│   │   ├── otp_service.py, email_service.py, push_service.py, notification_service.py
│   │   ├── insights_service.py, pdf_service.py, activity_service.py, teacher_auth_service.py
│   │   ├── student_service.py, teacher_service.py, attendance_service.py   # API-র জন্য
│   │   ├── image_store.py, cloudinary_client.py, image_service.py          # Cloudinary ছবি (+ Test-নামের default Category)
│   │   └── question_library.py                                              # My Questions <-> Test: import, duplicate, order, published guard
│   ├── ai/                 # AI provider (Gemini/Groq/...) ও context
│   └── utils/              # ছোট helper: validators, helpers, jee, classes, api_response
├── templates/              # HTML (ফোল্ডার = কার পেজ)
│   ├── base.html, error.html
│   ├── public/  auth/  student/  teacher/ (teacher/bank, teacher/studio)  partials/
├── static/
│   ├── css/  js/  icons/   # + manifest.json, sw.js (PWA, root-এ থাকতে হয়)
├── uploads/notes/          # Sir-এর আপলোড করা PDF (git-এ যায় না)
├── tests/                  # python -m unittest discover -s tests
└── docs/                   # এই ডকুমেন্টগুলো
```

## নতুন ফিচার যোগ করবে কীভাবে?
1. **পেজ লাগলে**: `backend/routes/web/` বা `routes/teacher/`-এ নতুন ফাইল বানিয়ে `@app.route(...)` লেখো (`from backend.core import app`)। তারপর `backend/bootstrap.py`-তে এক লাইন `import backend.routes.web.<ফাইল>` যোগ করো।
2. **template**: `templates/<public|auth|student|teacher>/`-এ রাখো, `{% extends "base.html" %}`।
3. **জটিল logic/SQL**: `backend/services/<নাম>_service.py`-এ লিখে route থেকে call করো।
4. **নতুন টেবিল/কলাম**: `backend/db/migrations.py`-এর `MIGRATIONS`-এ নতুন `(id, desc, up, down)` — শুধু additive (DROP/DELETE নয়)।
5. **JSON API**: `backend/routes/api/<নাম>_api.py` (Blueprint), তারপর `bootstrap.py`-তে `app.register_blueprint(...)`।
6. **নতুন setting**: `backend/config.py`-তে।
7. **test**: `tests/`-এ।

## নিয়ম (ভাঙলে সাইট ভাঙবে)
- Route function-এর নাম বদলিও না: template-এর `url_for('login')` ও `middleware/guards.py` এই নামের উপর নির্ভর করে।
- `middleware` import-এর ক্রম বদলিও না (`security` → `auth`): `before_request` ওই ক্রমে চলে।
- `database.db`, `.env`, `uploads/` প্রজেক্ট ফোল্ডারের root-এই থাকবে।
- নিচের স্তর উপরের স্তর import করবে না: `config/core/db` ← `utils/services/middleware` ← `routes` ← `bootstrap`।


## My Questions / Test Workspace (v3)
* **My Questions** = table `bank_questions` (central library). Saving there never touches a test.
* **Test Workspace** = `/teacher/studio/tests/<id>/questions`, the ONE place to create / import / edit / order / preview the questions of a selected test. `/teacher/tests/<id>/questions` (old URL) redirects there. `/teacher/studio/workspace` is the test picker (no test = no creating).
* Copies are independent: import copies a library row into `questions` (`source_bank_id` remembers the origin); "Save to My Questions" copies a test question into `bank_questions`.
* Rules live in `services/question_library.py`: draft-only changes (`publish_service.is_editable`), duplicate refusal (unique index `uq_questions_test_source` + content check), order `COALESCE(sort_order, id)`.
* Image upload default category: `question_studio._resolve_upload_category` (category_id > typed new_category > test_id -> test title). A missing category is created only after Cloudinary accepted the file, in the same transaction as the image row.
* Email/OTP: `services/email_service.py` (stage logging, classification, `scripts/check_smtp.py`), `LOCAL_OTP_MODE=console` guarded by `config.validate_local_otp_mode()` at startup.
