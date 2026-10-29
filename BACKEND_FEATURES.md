# BACKEND_FEATURES.md

## নতুন
- **JSON API v1** (student + teacher + public) — pagination, search, consistent errors। বিস্তারিত `API_DOCUMENTATION.md`।
- **Attendance** — teacher মার্ক করে (একসাথে ≤500, atomic, upsert), student শুধু নিজেরটা দেখে। আগের অব্যবহৃত `attendance` টেবিল ব্যবহার; schema অপরিবর্তিত।
- **Migration সিস্টেম** — backup + rollback সহ (`python -m backend.migrations`).
- **Test attempt রেকর্ড** — student কখন test খুলল (`test_attempts`)।
- **Optional server-side timer** — `ENFORCE_TEST_TIMER=1` হলে duration+২ মিনিটের পর submit বাতিল। **ডিফল্ট বন্ধ**, যাতে পুরোনো আচরণ বদলায় না।
- `.env.example`, `backups/` gitignore।

## সংশোধিত (বিদ্যমান route-এ app.py না ছুঁয়ে)
- `take_test`/`submit_test`/`test_leaderboard`/`download_note`: অন্য class-এর আইটেমে 404।
- `submit_test`: inactive test cold-submit 404; তবে আগে খুলে ফেলা student জমা দিতে পারে (শিক্ষক মাঝপথে বন্ধ করলে উত্তর হারাবে না)।
- Duplicate submit: DB-তে UNIQUE(test_id, student_id) (ডুপ্লিকেট না থাকলে) + `IntegrityError` → "Already submitted"।
- সব non-static response `Cache-Control: no-store`; `/sw.js` `no-cache`।
- `static/sw.js` v7: শুধু `/static/` cache; পেজ/API/PDF কখনো নয়; পুরোনো v6 cache মুছে যায়। **ট্রেড-অফ:** আগে offline-এ cached পেজ দেখা যেত, এখন offline থাকলে ব্রাউজারের offline পেজ আসবে (ব্যক্তিগত তথ্য নিরাপদ রাখার মূল্য)।
- API-এর notes/student search: LIKE wildcard escape।
- Performance index (results, answers, questions, notes, login_history, notifications ইত্যাদি)।

## অপরিবর্তিত (বিদ্যমান, ইচ্ছাকৃত)
Registration/OTP/reset, login/logout, teacher panel, scoring (`score += marks`, pass ≥ 40%), PDF, announcements, notifications, push, cleanup, CSV export, impersonation।

## করা হয়নি / পরামর্শ
- Course/lesson মডেল (প্রজেক্টে নেই; দরকার হলে আলাদা সিদ্ধান্ত)।
- Teacher account DB-তে (এখন `.env`-এ একজন)। Multi-teacher হলে role টেবিল লাগবে।
- Question edit/delete-এ result অসঙ্গতি: সুপারিশ — result আছে এমন test-এ question মুছতে না দেওয়া (policy বদল, তাই আপনার অনুমোদন চাই)।
- `app.py`-তে 1-লাইন সংশোধন: `X-Forwarded-For` → `request.remote_addr` (২ জায়গা); CSV formula-escape।
- CSP header (Phase 2, inline script সরানোর পর)।
