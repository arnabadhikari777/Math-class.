# BACKUP_RESTORE_GUIDE (PythonAnywhere)

**Before deploying**
1. Web tab → *Disable* is not needed; just open a Bash console in the project folder.
2. `cp database.db database.BEFORE-UPGRADE.db` (keep it outside the project folder too, and download it).
3. `python -m backend.db.migrations verify-backup database.BEFORE-UPGRADE.db` → must print `"ok": true`.
4. `python -m backend.db.migrations restore-test database.BEFORE-UPGRADE.db` → `"ok": true, "row_counts_match": true`.
The upgrade also makes its own copy in `backups/` before every migration.

**Restore (if anything looks wrong)**
1. Web tab → **Reload is not enough**: first stop traffic by renaming nothing — simply run:
2. `cp database.db database.BROKEN.db`
3. `cp database.BEFORE-UPGRADE.db database.db`
4. Put the *old* code back (your previous ZIP/Git commit). The old code cannot read the new schema, so DB and code must be restored together.
5. Web tab → Reload.

Never use `rollback 008/009/010` for recovery: they intentionally refuse to run. Uploads (`uploads/` PDFs), `.env` and Cloudinary images are never touched by migrations; back up `uploads/` separately (`cp -r uploads uploads.bak`).
