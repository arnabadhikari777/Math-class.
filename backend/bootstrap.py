# -*- coding: utf-8 -*-
"""Wires the whole site together. Importing this module builds the full app.

Order matters in two places (marked below): the ``before_request`` hooks run
in the order their modules are imported.
"""
import logging
import os

from backend.core import app
from backend.db import db_path, migrations
from backend.db.schema import init_db
from backend.services.teacher_auth_service import _teacher_creds

log = logging.getLogger(__name__)

# 1) Middleware  (ORDER MATTERS: https redirect -> csrf -> session check)
import backend.middleware.security      # noqa: E402,F401
import backend.middleware.auth          # noqa: E402,F401
import backend.middleware.errors        # noqa: E402,F401
import backend.middleware.context       # noqa: E402,F401

# 2) Website pages (HTML)
import backend.routes.web.public        # noqa: E402,F401
import backend.routes.web.auth          # noqa: E402,F401
import backend.routes.web.student       # noqa: E402,F401
import backend.routes.web.notifications # noqa: E402,F401
import backend.routes.web.pwa           # noqa: E402,F401

# 3) Teacher panel pages
import backend.routes.teacher.dashboard      # noqa: E402,F401
import backend.routes.teacher.students       # noqa: E402,F401
import backend.routes.teacher.notes          # noqa: E402,F401
import backend.routes.teacher.tests          # noqa: E402,F401
import backend.routes.teacher.results        # noqa: E402,F401
import backend.routes.teacher.cleanup        # noqa: E402,F401
import backend.routes.teacher.communication  # noqa: E402,F401

from backend.middleware import guards                                       # noqa: E402
from backend.routes.api import ai_admin, ai_api, public_api, student_api, teacher_api  # noqa: E402
from backend.routes.teacher import question_bank, question_studio           # noqa: E402

# Ensure tables exist on import (e.g. gunicorn / production)
init_db()
if not all(_teacher_creds()):
    log.warning("TEACHER_USERNAME / TEACHER_PASSWORD are not set in .env - teacher login is disabled until they are.")

# Additive migrations (with automatic backup first). Disable with BACKEND_AUTO_MIGRATE=0
if os.environ.get("BACKEND_AUTO_MIGRATE", "1").strip().lower() not in ("0", "false", "no"):
    try:
        for row in migrations.run_pending(db_path(app)):
            log.info("backend migration: %s", row)
    except Exception as exc:  # the site must still start if a migration cannot run
        log.error("backend migrations could not run: %s", exc)

# 4) Cross-cutting guards + JSON API (/api/v1) + teacher question tools
guards.register(app)
app.register_blueprint(public_api.bp)
app.register_blueprint(student_api.bp)
app.register_blueprint(teacher_api.bp)
app.register_blueprint(ai_api.bp)
app.register_blueprint(ai_admin.bp)
app.register_blueprint(question_bank.bp)
app.register_blueprint(question_studio.bp)
app.jinja_env.globals["qimg"] = question_bank.question_image_url
