# DATABASE_SCHEMA — changes in this upgrade (schema version = last row of `schema_migrations`, now `010_support_tables`)

## New tables
* **exam_versions**(id, test_id→tests RESTRICT, version_no, content_hash, negative_marks, is_frozen, frozen_at, created_at) UNIQUE(test_id,version_no)
* **exam_version_questions**(version_id→exam_versions, position, original_question_id, question_text, image_url, bank_image_id, image_info, option_a–d, correct_option, marks, negative_marks)
* **exam_attempts**(id, test_id, student_id, version_id, attempt_no, status in_progress|submitted|auto_submitted|rejected, started_at, deadline_at, duration_seconds, grace_seconds, submitted_at, submit_source, score, total_marks, is_counted, draft_json, draft_saved_at) UNIQUE(test_id,student_id,attempt_no); partial UNIQUE(test_id,student_id) WHERE status='in_progress'; all FKs RESTRICT
* **attempt_questions** — the frozen question set one attempt saw (same columns as version questions + attempt_id, version_id)
* **impersonation_log**(student_id, teacher_label, started_at, ended_at, end_reason, ip_address)
* **deletion_queue**(kind file|cloudinary, ref, reason, status pending|done|failed, attempts, last_error) UNIQUE(kind,ref)
* **attendance_duplicates_archive** — duplicate attendance rows moved aside by migration 008

## Rebuilt tables (data copied, nothing lost)
* **results**(id, test_id, student_id, score, total_marks, submitted_at, **attempt_id**) UNIQUE(test_id,student_id); FKs RESTRICT. One row per student/test = the counted attempt.
* **answers**(id, result_id→results SET NULL, **attempt_id**→exam_attempts RESTRICT, question_id (plain number, no FK), position, question_text, image_url, bank_image_id, option_a–d, correct_option, marks, negative_marks, selected_option, is_correct, marks_awarded) — self-contained snapshot.
* **attendance** UNIQUE(student_id,date), FK RESTRICT · **login_history**, **ai_conversations** FK RESTRICT (ai_messages still cascade from its conversation).

## Added columns
students: is_archived, archived_at, archive_reason · tests: max_attempts, result_policy, late_policy, grace_seconds, negative_marks, is_archived, archived_at · notes: original_filename, file_sha256, file_size, is_deleted, deleted_at · bank_images: status, status_changed_at · push_subscriptions: host, expires_at, fail_count, last_success_at, last_failure_at.

## Dropped
`test_attempts` (migration 009) — only when every row already exists in `exam_attempts`; otherwise it is kept and the migration reports SKIP. Nothing else was deleted. Not touched: `bank_questions`, `bank_categories`, Cloudinary data, uploads, env, push config.

## Relationship summary
tests 1─* exam_versions 1─* exam_version_questions · tests 1─* exam_attempts *─1 students · exam_attempts *─1 exam_versions · exam_attempts 1─* attempt_questions · exam_attempts 1─* answers *─0..1 results. With `RESTRICT`, a raw `DELETE` of a student or test that has history fails instead of cascading; the app's permanent-delete routes delete in explicit order after confirmation.
