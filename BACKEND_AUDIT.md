# BACKEND_AUDIT.md — Arnab Math Class

Audit করা হয়েছে: `app.py` (2573 লাইন, 58 route), 33 template, `static/sw.js`, `manifest.json`, `database.db` (read-only কপি), `requirements.txt`, `README.md`.
স্ট্যাক: Flask 3 + SQLite (raw `sqlite3`, ORM নেই) + session cookie + Gmail SMTP OTP + optional Web Push + reportlab PDF।

## 1. Feature status

| Feature | Status | প্রমাণ |
|---|---|---|
| Student registration + email OTP | Working | `/register`, `/verify-otp`, `verify_otp_code()` (HMAC hash, 5 attempt, expiry). Email পাঠানো `.env`-এ Gmail App Password-এর উপর নির্ভর → **Needs Verification** (এখানে পরীক্ষা সম্ভব নয়) |
| Student login/logout | Working | `/login`, `/logout`, rate limit 10/10min, `session_version` দিয়ে সব-device logout |
| Forgot/reset password | Working | `/forgot-password`, `/reset-password` (generic message, cooldown, rate limit) |
| Teacher login | Working | `.env` credentials, constant-time compare, 5/10min limit। একটাই teacher account (DB-তে নেই) |
| Role-based access | Partially Working | `student_login_required` / `teacher_login_required` সব route-এ আছে; কিন্তু **ownership/class check নেই** (নিচে ১ নং) |
| Student dashboard/profile/history | Working | `/dashboard`, `/profile` |
| Notes (PDF upload/list/download/delete) | Needs Fix | class check ছাড়া `download_note` (যেকোনো লগইন student যেকোনো note নামাতে পারে); search-এ `%`/`_` escape নেই |
| Tests (create/questions/toggle/delete) | Working | `/teacher/tests*` |
| Take test + submit + scoring | Needs Fix | scoring ঠিক; কিন্তু class/inactive check নেই, timer শুধু client-side, `results(test_id,student_id)`-এ UNIQUE নেই (race-এ double result সম্ভব) |
| Results / review / PDF / leaderboard | Working | `/test/<id>/result`, `/teacher/tests/<id>/result/<rid>/pdf`; leaderboard-এ class check নেই |
| Announcements | Working | `/teacher/announcements*` + notification তৈরি |
| In-app notifications (read/unread) | Working | `notifications`, `notification_reads`, `/api/notifications*` |
| Push notification | Partially Working | কোড আছে; `VAPID_*` key ডিফল্টে খালি এবং `pywebpush` এই পরিবেশে নেই → key না দিলে **কাজ করে না** |
| Contact inbox | Working | `/contact`, `/teacher/contacts*` |
| Activity log, data cleanup, CSV export | Working | `/teacher/activity`, `/teacher/cleanup*`, `/teacher/students/export` |
| Attendance | **Missing** | `attendance` টেবিল আছে (`init_db`), কিন্তু কোনো route/UI নেই |
| JSON API (frontend-এর জন্য) | **Missing** | কেবল `/api/notifications*` ও `/api/push/*`; student/test/result/notes-এর API নেই |
| Pagination | Missing | কোনো list-এ নেই (students, notes, results সব একসাথে) |
| PWA manifest/installability | Working | `manifest.json`, icons 192/512 |
| PWA service worker caching | **Needs Fix (High)** | নিচে ২ নং |
| Courses/lessons মডেল | Missing (প্রযোজ্য নয়) | প্রজেক্টে শুধু notes+tests; আলাদা course মডেল বানানো হয়নি (scope বাড়ত) |
| DB migration framework | Missing | শুধু `CREATE IF NOT EXISTS` + `_ensure_column` |

## 2. মূল সমস্যা (গুরুত্ব অনুযায়ী)

1. **Class/ownership authorization (Medium)** — `take_test`, `submit_test`, `test_leaderboard`, `download_note` কোনোটাই student-এর class মেলায় না; dashboard শুধু UI-তে ফিল্টার করে। URL জানলে অন্য class-এর test দেওয়া/note নামানো যায়। `submit_test` inactive test-ও নেয়।
2. **Service worker private data cache করে (High)** — `sw.js` v6 প্রতিটি same-origin GET (`/dashboard`, `/profile`, `/api/notifications`, result, teacher panel) cache-এ রাখত ও offline-এ দেখাত। Shared device-এ logout-এর পরও আগের ব্যবহারকারীর তথ্য দেখা যেতে পারত। কোনো `Cache-Control: no-store` হেডারও ছিল না।
3. **Double submission race (Medium)** — "already submitted" check ও INSERT আলাদা; DB-তে UNIQUE নেই।
4. **`database.db` Git-এ যাওয়ার ঝুঁকি (Medium)** — `.gitignore`-এ `database.db` কমেন্ট করা; ZIP-এ আসল DB (email, password hash) আছে। Public repo হলে ফাঁস।
5. **`.env.example` নেই** — README বলে `cp .env.example .env`, কিন্তু ফাইলটি নেই।
6. **Question মুছলে/যোগ করলে পুরোনো result অসঙ্গত (Medium, নীতিগত)** — `delete_question` result-এর `total_marks` বদলায় না, `answers` cascade-এ মুছে যায়। আচরণ বদলানো হয়নি; সিদ্ধান্ত আপনার (নিচে সুপারিশ)।
7. **`X-Forwarded-For` সরাসরি `login_history`-তে (Low)** — ProxyFix থাকা সত্ত্বেও raw header; spoof করা যায়। সুপারিশ: `request.remote_addr` ব্যবহার (app.py-তে 2 জায়গায় 1-লাইন পরিবর্তন) — **করা হয়নি**, কারণ app.py ন্যূনতম রাখার নির্দেশ।
8. **CSV export formula injection (Low)** — নাম `=...` দিয়ে শুরু হলে Excel-এ ফর্মুলা চলতে পারে। অপরিবর্তিত।
9. **Impersonation (Low)** — teacher "login-as" করে test submit করলে সেটা আসল student-এর result হয়ে যায়। অপরিবর্তিত; জানা থাকা দরকার।
10. **66টি `get_db()` কল, কপি-করা SQL** (result/answers query 3 জায়গায়), `print()` দিয়ে লগ (5টি), 400+ লাইনের কোনো function নেই কিন্তু `app.py` 2573 লাইন।
11. নতুন-ব্যবহারকারী enumeration: `/register` বলে "email আগে থেকেই আছে" (Low, UX-এর সঙ্গে ট্রেড-অফ)।
12. CSP হেডার নেই; templates-এ inline script/style বেশি, তাই CSP যোগ করা Phase 2-এর কাজ।

## 3. Database (SQLite, `database.db`)
13 টেবিল: students(2 সারি), otp_codes, login_history, notes, tests, questions, results, answers, announcements, contact_requests, activity_log, site_settings, attendance। কোনো explicit index নেই (শুধু UNIQUE email, site_settings.key)।
**গুরুত্বপূর্ণ:** ZIP-এর `database.db` কোডের চেয়ে পুরোনো — `notifications`, `notification_reads`, `push_subscriptions`, `rate_limit_hits`, `students.session_version`, `otp_codes.attempts` নেই। `init_db()` import-এ এগুলো যোগ করে (পুরোনো আচরণ, ডেটা বদলায় না)।
FK `ON DELETE CASCADE` চালু (`PRAGMA foreign_keys=ON`)।

## 4. Deployment
`requirements.txt`: Flask 3.0.3, Werkzeug 3.0.3, reportlab, pywebpush, python-dotenv — পর্যাপ্ত; **নতুন dependency লাগেনি**। gunicorn/WSGI ফাইল রিপোতে নেই (PythonAnywhere-এ নিজের WSGI ফাইল ব্যবহার হয়, `from app import app`)।
