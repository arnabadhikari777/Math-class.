# PythonAnywhere deployment of this upgrade
1. Bash: `cd ~/<project>`; **backup first** (BACKUP_RESTORE_GUIDE.md steps 2–4).
2. Upload/replace the project files (keep your `.env`, `database.db`, `uploads/`). Do **not** delete `uploads/` or `.env`.
3. No new Python packages (requirements.txt unchanged).
4. Environment (.env): nothing required. `ENFORCE_TEST_TIMER` is now ON by default — remove it or set `1` (set `0` only as an emergency switch).
5. Web tab → Reload. Check the log for `Migration 008…010` lines, then run `python -m backend.db.migrations check`.
6. Optional: Tasks tab → daily `python -m backend.services.image_service` (retries failed Cloudinary/file deletes).
7. Smoke test: teacher login → Tests → Rules; student takes a test; reload mid-test; result page; Archive/Restore a test student.
Teacher UI added: Tests → **Rules** (duration, attempts, best/latest, late rule, grace, negative marks), **Archive**, Archived list; Students → **Archive/Restore**, Archived filter, Delete permanently…
