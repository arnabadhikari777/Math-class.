# TEST_REPORT.md

**ফলাফল: 43 টেস্ট, সব পাস** (`python -m unittest discover -s tests -v`, Python 3.12, Flask 3.1.3)।
**নিরাপত্তা:** টেস্ট প্রজেক্ট (সহ `database.db`) একটি temp ফোল্ডারে কপি করে সেখান থেকে চলে; আসল DB খোলা/বদলানো হয়নি (SHA-256 টেস্টের আগে-পরে একই: `df2b43d1…7e84`)। টেস্ট শেষে temp মুছে যায়।

## কী কী যাচাই হয়েছে
- স্টার্টআপ/import, ৫৮টি আসল route ও তাদের HTTP method এখনো আছে (`tests/baseline_routes.json`)
- পাবলিক পেজ 200, অ-লগইনে সুরক্ষিত পেজ 302, লগইন-করা student/teacher পেজ রেন্ডার
- Student/teacher login-logout, ভুল পাসওয়ার্ড, blocked student, CSRF ছাড়া POST → 400
- Role সীমা: anonymous 401, student→teacher API 403, teacher→student API 401, impersonation, blocked সেশন শেষ
- API: ডেটা ফাঁস নেই (password_hash ইত্যাদি), class ফিল্টার, LIKE wildcard escape, pagination সীমা, invalid param
- Test-taking: স্কোরিং আগের মতো (10/10, pass), অন্য class → 404, inactive cold-submit 404, শুরু করা test জমা দেওয়া যায়, দ্বিতীয় submit উপেক্ষিত, ৪ থ্রেডে একসাথে submit → ১টি result, timer ডিফল্ট বন্ধ/চালুতে বাতিল
- Migration: পুরোনো সব সারি অপরিবর্তিত, শুধু নতুন অবজেক্ট, backup তৈরি ও বৈধ, idempotent, rollback, ডুপ্লিকেট থাকলে SKIP, DB-স্তরে ডুপ্লিকেট result আটকায়, IntegrityError → ভদ্র redirect
- PWA/cache: ব্যক্তিগত পেজ/API `no-store`, `/sw.js` `no-cache`, sw v7 শুধু `/static/` cache করে
- Attendance: upsert, atomic validation, ভবিষ্যৎ তারিখ/ভুল status/অজানা student/ভুল JSON → 400, student শুধু নিজেরটা, student POST 403
- Teacher API: search, overview, results, 404

## যা পরীক্ষা করা যায়নি / সীমাবদ্ধতা
- **Gmail OTP/reset ইমেইল বাস্তবে পাঠানো** (credential নেই, নেটওয়ার্ক বন্ধ) — শুধু লজিক পরীক্ষিত; **Needs Verification** লাইভ সার্ভারে।
- **Web Push** — `pywebpush` এই পরিবেশে নেই ও VAPID key নেই; পাঠানো যাচাই হয়নি।
- **PythonAnywhere-এ সরাসরি চালানো** হয়নি; শুধু সামঞ্জস্য-বিশ্লেষণ (নতুন dependency নেই, pure sqlite3/Flask)।
- **Service worker-এর ব্রাউজার আচরণ** (install/activate/cache মোছা) বাস্তব ব্রাউজারে দেখা হয়নি; JS সিনট্যাক্স ও লজিক-পাঠ + HTTP হেডার পরীক্ষিত। Deploy-এর পর DevTools → Application → Cache Storage-এ `math-class-v7` ও পুরোনো v6 মোছা দেখে নিন।
- বহু-worker প্রকৃত লোড টেস্ট হয়নি (শুধু ৪-থ্রেড race)।
- PDF note ডাউনলোডের ফাইল-কন্টেন্ট পরীক্ষা হয়নি (শুধু অনুমতি-সীমা)।

## টেস্ট তালিকা
```
test_01_app_imports_and_backend_registered
test_02_every_original_route_still_exists
test_03_public_pages_still_work
test_04_protected_pages_redirect_when_anonymous
test_05_logged_in_pages_render
test_10_existing_data_unchanged_after_startup_and_migrations
test_11_new_objects_are_additive_only
test_12_backup_created_before_migrating
test_13_migrations_idempotent_and_reversible
test_14_unique_index_skipped_when_duplicates_exist
test_15_database_blocks_duplicate_result
test_16_integrity_error_becomes_friendly_redirect
test_20_student_login_logout
test_21_wrong_password_and_blocked_student_rejected
test_22_teacher_login
test_23_post_without_csrf_is_rejected
test_30_anonymous_gets_401_json
test_31_student_cannot_use_teacher_api
test_32_teacher_cannot_use_student_api
test_33_impersonating_teacher_is_a_student_only
test_34_blocked_student_session_is_ended
test_35_public_endpoints
test_40_me_hides_secrets
test_41_tests_list_respects_class
test_42_notes_search_escapes_wildcards
test_43_pagination_is_bounded
test_44_results_summary_progress
test_45_student_never_sees_other_students_results
test_50_class_guard_blocks_other_class_test_and_note
test_51_all_class_test_open_to_everyone
test_52_inactive_test_cannot_be_submitted_cold
test_53_started_test_can_still_be_submitted_after_deactivation
test_54_opening_a_test_records_attempt
test_55_second_submission_is_ignored
test_56_simultaneous_submissions_make_one_result
test_57_timer_enforcement_is_opt_in
test_60_private_pages_are_no_store
test_61_service_worker_policy
test_70_attendance_roundtrip_and_upsert
test_71_attendance_validation_is_atomic
test_72_student_sees_only_own_attendance
test_80_teacher_students_search
test_81_teacher_overview_and_results
```
