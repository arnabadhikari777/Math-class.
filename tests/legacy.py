# -*- coding: utf-8 -*-
"""Gives the test suites a pre-008 ("legacy") database, so migrations are tested on a realistic starting point.

The real database.db is never shipped in the project; when it is absent a schema-only copy of the
production schema (migrations 001-007 applied, zero rows, no personal data) is used instead."""
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "legacy_007_schema_only.db")


def ensure_legacy_db(path):
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        shutil.copy(FIXTURE, path)
    return path


def legacy_source(root):
    real = os.path.join(root, "database.db")
    return real if os.path.isfile(real) and os.path.getsize(real) > 0 else FIXTURE


def open_exam(client, test_id):
    """What GET /test/<id> used to do (start or resume). Now the flow is: continue -> confirm/start -> exam page.
    Returns the exam-page response, or the response of the step that refused (404 / redirect)."""
    with client.session_transaction() as s:
        tok = s.setdefault("_csrf_token", "tok")
    h = {"X-CSRFToken": tok}
    r = client.post(f"/test/{test_id}/continue", headers=h)
    if r.status_code != 302:
        return r
    r = client.post(f"/test/{test_id}/start", headers=h)
    if r.status_code != 302 or not r.headers.get("Location", "").endswith(f"/test/{test_id}/exam"):
        return r
    return client.get(f"/test/{test_id}/exam")
