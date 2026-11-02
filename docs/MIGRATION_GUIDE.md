# MIGRATION_GUIDE

Migrations run automatically at app start (`backend/db/migrations.py`, tracked in `schema_migrations`), each in its own transaction with an automatic file backup in `backups/` first. A failed migration is rolled back and reported in the log; the app keeps running on the old schema.

| ID | What | Rollback |
|----|------|----------|
| 001 test_attempts | legacy attempt table | destructive |
| 002 perf_indexes | indexes | safe |
| 003 unique_results | unique index; **skipped if duplicates exist** (reported) | safe |
| 004 unique_attendance | unique index; **skipped if duplicates exist** (reported) — 008 now archives duplicates and enforces uniqueness | safe |
| 005 normalize_class_names | data update | safe |
| 006 ai_chat | creates AI tables | **destructive** (drops them) |
| 007 question_bank | creates bank tables | **destructive** |
| 008 exam_system | see DATABASE_SCHEMA.md; runs with `foreign_keys=OFF` and `PRAGMA foreign_key_check` before COMMIT | **refused** |
| 009 drop_legacy_attempts | drops `test_attempts` only when fully copied | **refused** |
| 010 support_tables | audit/queue/columns | **refused** |

**Documentation fix:** the old notes presented rollback as a recovery tool. Rollbacks of 001, 006–010 delete tables/data and now raise `PermissionError` unless `force_destructive=True` / `--force-destructive`. Recovery = restore a backup (BACKUP_RESTORE_GUIDE.md).

Commands (`cd` to the project, same virtualenv):
```
python -m backend.db.migrations status
python -m backend.db.migrations migrate
python -m backend.db.migrations check            # integrity + foreign keys + duplicates + applied versions
python -m backend.db.migrations duplicates
python -m backend.db.migrations verify-backup FILE
python -m backend.db.migrations restore-test FILE
python -m backend.services.image_service         # retry queued Cloudinary / file deletes (schedule daily)
```
Verified on your real `database.db`: 007 → 010 applied, integrity ok, 0 foreign-key violations, result #1 (score 0/2) kept with its 2 answers.


## 014_workspace
Runs automatically at start (backup first). Manual: `python -m backend.db.migrations migrate`. Check: `python -m backend.db.migrations check`.
It only adds nullable columns and indexes, so rolling back the code needs no database change (old code ignores the new columns). `down` refuses on purpose: restore the pre-migration backup if you must undo it.
Important: the exam code now orders questions by `COALESCE(sort_order, id)`, so **do not run the new code with `BACKEND_AUTO_MIGRATE=0` before running `migrate` once.**
