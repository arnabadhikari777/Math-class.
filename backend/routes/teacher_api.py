# -*- coding: utf-8 -*-
"""JSON API for the teacher/admin (prefix /api/v1/teacher).
Reads: overview, students search, per-test results, attendance roster.
Writes: attendance only (other write actions stay on the existing app.py routes)."""
from datetime import datetime

from flask import Blueprint, request

from ..db import get_db
from ..http import fail, ok, page_meta, paging, teacher_api
from ..services import attendance_service as att
from ..services import teacher_service as svc

bp = Blueprint("api_teacher", __name__, url_prefix="/api/v1/teacher")


@bp.get("/overview")
@teacher_api
def overview():
    with get_db() as conn:
        return ok(svc.overview(conn))


@bp.get("/students")
@teacher_api
def students():
    status = request.args.get("status", "all")
    if status not in ("all", "verified", "pending", "blocked"):
        return fail("invalid_param", "status must be all, verified, pending or blocked.")
    page, per_page, offset = paging()
    with get_db() as conn:
        items, total = svc.search_students(
            conn, request.args.get("q", "").strip()[:100],
            request.args.get("class", "").strip()[:50], status, per_page, offset)
    return ok(items, page_meta(page, per_page, total))


@bp.get("/tests/<int:test_id>/results")
@teacher_api
def test_results(test_id):
    page, per_page, offset = paging()
    with get_db() as conn:
        test, rows, total = svc.test_results(conn, test_id, per_page, offset)
    if test is None:
        return fail("not_found", "Test not found.", 404)
    for r in rows:
        r["percentage"] = round(r["score"] / r["total_marks"] * 100, 1) if r["total_marks"] else 0
    return ok({"test": test, "results": rows}, page_meta(page, per_page, total))


@bp.get("/attendance")
@teacher_api
def attendance_roster():
    try:
        day = att.parse_date(request.args.get("date") or datetime.now().strftime("%Y-%m-%d"))
    except att.ValidationError as exc:
        return fail("invalid_param", str(exc))
    with get_db() as conn:
        rows = att.roster(conn, day, request.args.get("class", "").strip()[:50])
    return ok(rows, {"date": day, "statuses": list(att.STATUSES)})


@bp.post("/attendance")
@teacher_api
def attendance_mark():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return fail("invalid_json", "Send a JSON object: {date, records:[{student_id,status,note}]}.")
    try:
        day = att.parse_date(body.get("date"))
        with get_db() as conn:
            count = att.mark(conn, day, body.get("records"))
            conn.execute(
                "INSERT INTO activity_log (actor, action, detail, created_at) VALUES (?,?,?,?)",
                ("teacher", "attendance", f"{day}: {count} record(s)", datetime.now().isoformat()))
            conn.commit()
    except att.ValidationError as exc:
        return fail("invalid_param", str(exc))
    return ok({"date": day, "saved": count})
