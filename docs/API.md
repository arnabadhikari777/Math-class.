# API_DOCUMENTATION.md — /api/v1

Auth: বিদ্যমান session cookie (লগইন আগের মতোই `/login`)। POST-এ header `X-CSRFToken: <token>` (token: `GET /api/v1/csrf`)।
Envelope: সফল `{"ok":true,"data":…,"meta":{…}}`, ব্যর্থ `{"ok":false,"error":{"code","message"}}`।
Pagination: `?page=1&per_page=20` (max 100; ভুল মান → ডিফল্ট)। Status: 200, 400 `invalid_param`/`invalid_json`, 401 `unauthenticated`, 403 `forbidden`, 404 `not_found`, 409 `conflict`।
দ্রষ্টব্য: অজানা `/api/v1/...` পাথে অ্যাপের পুরোনো HTML 404 পেজ আসে (backend/middleware/errors.py-র handler)।

## Public
| Method | Path | বিবরণ |
|---|---|---|
| GET | /health | `{status, database}` |
| GET | /csrf | `{csrf_token, header}` |
| GET | /session | `{role: anonymous\|student\|teacher, impersonating}` |

## Student (লগইন করা student; teacher "login-as" অবস্থায়ও)
| Method | Path | বিবরণ |
|---|---|---|
| GET | /me | id,name,class_name,roll,email,mobile,registered_at (password/notes_internal কখনো নয়) |
| GET | /me/summary | tests_attempted, average_percentage, tests_available, unread_notifications |
| GET | /tests?status=all\|available\|attempted | নিজের class-এর active test; question_count, total_marks, attempted, result_id |
| GET | /results | নিজের result + percentage + passed |
| GET | /results/<test_id> | বিস্তারিত উত্তরসহ; না থাকলে 404 |
| GET | /notes?q= | class-ফিল্টার্ড, search; `download_url` → `/notes/download/<id>` |
| GET | /announcements | সক্রিয়, class-ফিল্টার্ড |
| GET | /progress | সময়ক্রমে result (chart-এর জন্য) |
| GET | /attendance?month=YYYY-MM | নিজের হাজিরা + counts |

বিদ্যমান: `GET /api/notifications`, `POST /api/notifications/mark-read`, `/api/push/*` অপরিবর্তিত।

## Teacher (teacher session; impersonating অবস্থায় 403)
| Method | Path | বিবরণ |
|---|---|---|
| GET | /teacher/overview | গণনা (students, tests, results, নতুন contact…) |
| GET | /teacher/students?q=&class=&status= | status: all\|verified\|pending\|blocked; password hash নেই |
| GET | /teacher/tests/<id>/results | পাতাভিত্তিক ফলাফল + percentage |
| GET | /teacher/attendance?date=&class= | roster; `data[].status` null = মার্ক হয়নি |
| POST | /teacher/attendance | body `{"date":"YYYY-MM-DD","records":[{"student_id":1,"status":"present\|absent\|late\|excused","note":""}]}` → `{date,saved}`। ভবিষ্যৎ তারিখ/অজানা student/ভুল status → 400, কিছুই লেখা হয় না |

## HTML route-এর পরিবর্তিত আচরণ
অন্য class-এর `/test/<id>`, `/test/<id>/submit`, `/test/<id>/leaderboard`, `/notes/download/<id>` → 404।
