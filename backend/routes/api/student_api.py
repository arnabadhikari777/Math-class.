# -*- coding: utf-8 -*-
"""JSON API for the logged-in student  (prefix /api/v1, all GET, read-only)."""
from flask import Blueprint, request, session

from backend.db import get_db
from backend.utils.api_response import fail, ok, page_meta, paging, student_api
from backend.services import attendance_service as att
from backend.services import student_service as svc

bp = Blueprint("api_student", __name__, url_prefix="/api/v1")


def _student(conn):
    return svc.get_student(conn, session["student_id"])


@bp.get("/me")
@student_api
def me():
    with get_db() as conn:
        data = svc.profile(conn, session["student_id"])
    if data is None:
        return fail("not_found", "Student not found.", 404)
    data["impersonated_by_teacher"] = bool(session.get("impersonating"))
    return ok(data)


@bp.get("/me/summary")
@student_api
def me_summary():
    with get_db() as conn:
        return ok(svc.summary(conn, _student(conn)))


@bp.get("/tests")
@student_api
def tests():
    status = request.args.get("status", "all")
    if status not in ("all", "available", "attempted"):
        return fail("invalid_param", "status must be all, available or attempted.")
    page, per_page, offset = paging()
    with get_db() as conn:
        items, total = svc.list_tests(conn, _student(conn), status, page, per_page, offset)
    return ok(items, page_meta(page, per_page, total))


@bp.get("/results")
@student_api
def results():
    page, per_page, offset = paging()
    with get_db() as conn:
        items, total = svc.list_results(conn, session["student_id"], per_page, offset)
    return ok(items, page_meta(page, per_page, total))


@bp.get("/results/<int:test_id>")
@student_api
def result_detail(test_id):
    with get_db() as conn:
        data = svc.result_detail(conn, session["student_id"], test_id)
    if data is None:
        return fail("not_found", "No result for this test.", 404)
    return ok(data)


@bp.get("/notes")
@student_api
def notes():
    q = request.args.get("q", "").strip()[:100]
    page, per_page, offset = paging()
    with get_db() as conn:
        items, total = svc.list_notes(conn, _student(conn), q, per_page, offset)
    return ok(items, page_meta(page, per_page, total))


@bp.get("/announcements")
@student_api
def announcements():
    page, per_page, offset = paging(default_per_page=10)
    with get_db() as conn:
        items, total = svc.list_announcements(conn, _student(conn), per_page, offset)
    return ok(items, page_meta(page, per_page, total))


@bp.get("/progress")
@student_api
def progress():
    with get_db() as conn:
        data = svc.progress(conn, session["student_id"])
    return ok(data)


@bp.get("/attendance")
@student_api
def my_attendance():
    try:
        with get_db() as conn:
            rows, counts = att.student_history(conn, session["student_id"], request.args.get("month"))
    except att.ValidationError as exc:
        return fail("invalid_param", str(exc))
    return ok(rows, {"counts": counts})
