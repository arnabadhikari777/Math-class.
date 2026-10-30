# -*- coding: utf-8 -*-
"""Arnab Math Class - modular backend.

app.py calls ``register_backend(app)`` once; everything else lives here.
See BACKEND_ARCHITECTURE.md for where to add new features.
"""
import logging
import os
import sys

log = logging.getLogger(__name__)


def register_backend(app):
    from . import guards, migrations
    from .db import db_path
    from .routes import ai_admin, ai_api, public_api, student_api, teacher_api

    # Business constants stay owned by app.py; read, never copy.
    host = sys.modules.get(app.import_name)
    if host is not None and hasattr(host, "PASS_PERCENTAGE"):
        app.config.setdefault("PASS_PERCENTAGE", host.PASS_PERCENTAGE)

    # Additive migrations on start (no automatic backup). Disable with BACKEND_AUTO_MIGRATE=0
    # Manual backup anytime: python -m backend.migrations backup
    if os.environ.get("BACKEND_AUTO_MIGRATE", "1").strip().lower() not in ("0", "false", "no"):
        try:
            for row in migrations.run_pending(db_path(app), backup=False):
                log.info("backend migration: %s", row)
        except Exception as exc:  # the site must still start if a migration cannot run
            log.error("backend migrations could not run: %s", exc)

    guards.register(app)
    app.register_blueprint(public_api.bp)
    app.register_blueprint(student_api.bp)
    app.register_blueprint(teacher_api.bp)
    app.register_blueprint(ai_api.bp)
    app.register_blueprint(ai_admin.bp)
