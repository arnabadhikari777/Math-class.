# -*- coding: utf-8 -*-
"""Teacher-only "My Questions" (the central question library; code name: qbank) + legacy image-library URLs.

URL prefix: /teacher/bank   (kept so old bookmarks and forms keep working; only the visible names changed)
    /images                      redirects to the Image Library (Test Workspace area)
    /questions                   My Questions: list, create, edit, delete
    /import/<test_id>            copy selected My Questions into the chosen test (draft tests only)

Design rules (see the feature brief):
  * Images live on Cloudinary; the database only stores url / public_id / metadata.
  * A bank question is COPIED into the test (questions table) when imported, so the
    existing take-test / submit / result / marks code keeps working unchanged and
    later bank edits never rewrite a test students already sat.
  * A library image that any bank question or test question uses cannot be deleted.
  * CSRF is enforced globally by backend/middleware/security.py; here we only add the teacher check.
"""
from datetime import datetime
from functools import wraps

from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   session, url_for)

from backend.services import cloudinary_client as cdn
from backend.services import image_service
from backend.services import publish_service
from backend.services import question_library as qlib
from backend.utils.question_hash import content_hash
from backend.db import escape_like, get_db

from backend.utils.helpers import safe_back  # noqa: E402
bp = Blueprint("qbank", __name__, url_prefix="/teacher/bank")

MAX_FILES_PER_UPLOAD = 10
MAX_IMPORT_AT_ONCE = 200
LIST_LIMIT = 300
PICKER_LIMIT = 2000


def teacher_page(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_teacher"):
            flash("Teacher login is required.", "error")
            return redirect(url_for("teacher_login"))
        if session.get("impersonating"):
            abort(403)  # a teacher "viewing as a student" must not manage the bank
        return f(*args, **kwargs)
    return wrapper


@bp.app_template_filter("cdn_thumb")
def _cdn_thumb(url, width=320):
    return cdn.thumb_url(url, width)


def question_image_url(question_id):
    """Jinja global ``qimg(id)``: image URL of a test question, or None. Lets the result pages
    show the picture without changing the SQL in the route files. Never raises."""
    try:
        with get_db() as conn:
            row = conn.execute("SELECT image_url FROM questions WHERE id=?", (question_id,)).fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _now():
    return datetime.now().isoformat()


# ------------------------------------------------------------------ helpers
def _image_usage(conn, image_id):
    """(question_bank_count, everything_else_count) - delegates to the ONE shared checker."""
    u = image_service.usage(conn, image_id)
    return u["bank_questions"], u["total"] - u["bank_questions"]


def _categories(conn):
    return conn.execute(
        """SELECT c.id, c.name, COUNT(i.id) AS image_count
           FROM bank_categories c LEFT JOIN bank_images i ON i.category_id = c.id
           GROUP BY c.id ORDER BY c.name COLLATE NOCASE""").fetchall()


# --------------------------------------------------------------- image library
@bp.route("/")
@teacher_page
def home():
    return redirect(url_for("qbank.questions"))


@bp.route("/images")
@teacher_page
def images():
    """The Image Library has ONE screen now (Test Workspace area -> Image Library).
    This old address stays so bookmarks and the upload/delete forms keep working."""
    cat = _int(request.args.get("category"))
    return redirect(url_for("studio.library", category=cat) if cat else url_for("studio.library"))


@bp.route("/categories/create", methods=["POST"])
@teacher_page
def category_create():
    name = " ".join(request.form.get("name", "").split())[:60]
    if not name:
        flash("Category-র নাম দিন।", "error")
        return redirect(url_for("qbank.images"))
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM bank_categories WHERE name=? COLLATE NOCASE", (name,)).fetchone():
            flash("এই নামে Category আগে থেকেই আছে।", "error")
            return redirect(url_for("qbank.images"))
        cid = conn.execute("INSERT INTO bank_categories (name, created_at) VALUES (?,?)", (name, _now())).lastrowid
        conn.commit()
    flash("Category তৈরি হয়েছে: %s" % name, "success")
    return redirect(url_for("qbank.images", category=cid))


@bp.route("/categories/<int:cid>/delete", methods=["POST"])
@teacher_page
def category_delete(cid):
    with get_db() as conn:
        cat = conn.execute("SELECT * FROM bank_categories WHERE id=?", (cid,)).fetchone()
        if cat is None:
            abort(404)
        n = conn.execute("SELECT COUNT(*) FROM bank_images WHERE category_id=?", (cid,)).fetchone()[0]
        if n:
            flash("Category-তে %d টি ছবি আছে। আগে ছবিগুলো মুছুন বা অন্য Category-তে রাখুন; তাই এটি মোছা হয়নি।" % n, "error")
            return redirect(url_for("qbank.images", category=cid))
        conn.execute("DELETE FROM bank_categories WHERE id=?", (cid,))
        conn.commit()
    flash("Category মুছে ফেলা হয়েছে।", "success")
    return redirect(url_for("qbank.images"))


@bp.route("/images/upload", methods=["POST"])
@teacher_page
def image_upload():
    cid = _int(request.form.get("category_id"))
    with get_db() as conn:
        cat = conn.execute("SELECT * FROM bank_categories WHERE id=?", (cid,)).fetchone() if cid else None
    if cat is None:
        flash("আগে একটি Category বেছে নিন (না থাকলে নতুন Category তৈরি করুন)।", "error")
        return redirect(url_for("qbank.images"))
    files = [f for f in request.files.getlist("images") if f and f.filename]
    if not files:
        flash("কোনো ছবি বাছা হয়নি।", "error")
        return redirect(url_for("qbank.images", category=cid))
    if len(files) > MAX_FILES_PER_UPLOAD:
        flash("একবারে সর্বোচ্চ %d টি ছবি আপলোড করা যাবে।" % MAX_FILES_PER_UPLOAD, "error")
        return redirect(url_for("qbank.images", category=cid))
    done, errors = 0, []
    for f in files:
        data = f.read(cdn.max_bytes() + 1)
        try:
            image_service.upload_and_register(f.filename, data, cat)
            done += 1
        except image_service.ImageError as exc:
            errors.append(str(exc))
            if not cdn.is_configured():
                break
    if done:
        flash("%d টি ছবি আপলোড হয়েছে।" % done, "success")
    for e in errors:
        flash(e, "error")
    return redirect(url_for("qbank.images", category=cid))


@bp.route("/images/<int:image_id>/delete", methods=["POST"])
@teacher_page
def image_delete(image_id):
    with get_db() as conn:
        img = conn.execute("SELECT * FROM bank_images WHERE id=?", (image_id,)).fetchone()
    if img is None:
        abort(404)
    state, message = image_service.safe_delete(image_id)
    flash(message, "success" if state == "deleted" else "error")
    return redirect(url_for("qbank.images", category=img["category_id"]))


# ------------------------------------------------------------ question bank
def _read_question_form():
    f = request.form
    data = {
        "question_text": f.get("question_text", "").strip(),
        "option_a": f.get("option_a", "").strip(), "option_b": f.get("option_b", "").strip(),
        "option_c": f.get("option_c", "").strip(), "option_d": f.get("option_d", "").strip(),
        "correct_option": f.get("correct_option", "").strip().upper(),
        "image_id": _int(f.get("image_id")),
    }
    return data


def _validate_question(conn, data):
    if not data["question_text"] and not data["image_id"]:
        return "প্রশ্নের লেখা অথবা ছবি অন্তত একটি দিতে হবে।"
    if not all(data[k] for k in ("option_a", "option_b", "option_c", "option_d")):
        return "চারটি Option-ই পূরণ করুন।"
    if data["correct_option"] not in ("A", "B", "C", "D"):
        return "সঠিক উত্তর (A/B/C/D) বাছুন।"
    if data["image_id"] and conn.execute("SELECT 1 FROM bank_images WHERE id=?", (data["image_id"],)).fetchone() is None:
        return "বাছাই করা ছবিটি Library-তে পাওয়া যায়নি।"
    return None


def _hash_of(data):
    return content_hash(data["question_text"], data["option_a"], data["option_b"], data["option_c"],
                        data["option_d"], data["correct_option"], data["image_id"])


def _picker_images(conn):
    return conn.execute(
        """SELECT i.id, i.url, i.category_id, c.name AS category_name
           FROM bank_images i JOIN bank_categories c ON c.id = i.category_id
           ORDER BY i.id DESC LIMIT ?""", (PICKER_LIMIT,)).fetchall()


@bp.route("/questions")
@teacher_page
def questions():
    q = request.args.get("q", "").strip()[:100]
    cat = _int(request.args.get("category"))
    only_img = request.args.get("with_image") == "1"
    where, args = [], []
    if q:
        where.append("b.question_text LIKE ? ESCAPE '\\'")
        args.append("%" + escape_like(q) + "%")
    if cat:
        where.append("i.category_id=?")
        args.append(cat)
    if only_img:
        where.append("b.image_id IS NOT NULL")
    sql = """SELECT b.*, i.url AS image_url, c.name AS category_name
             FROM bank_questions b LEFT JOIN bank_images i ON i.id = b.image_id
             LEFT JOIN bank_categories c ON c.id = i.category_id"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    with get_db() as conn:
        rows = conn.execute(sql + " ORDER BY b.id DESC LIMIT ?", (*args, LIST_LIMIT)).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM bank_questions").fetchone()[0]
        cats = _categories(conn)
    return render_template("teacher/bank/bank_questions.html", rows=rows, total=total, categories=cats,
                           q=q, selected=cat, with_image=only_img)


@bp.route("/questions/new", methods=["GET", "POST"])
@teacher_page
def question_new():
    with get_db() as conn:
        if request.method == "POST":
            data = _read_question_form()
            err = _validate_question(conn, data)
            if err:
                flash(err, "error")
                return render_template("teacher/bank/bank_question_form.html", item=data, images=_picker_images(conn),
                                       categories=_categories(conn), editing=False), 400
            h = _hash_of(data)
            dup = qlib.find_bank_duplicate(conn, h)
            if dup:
                flash("এই প্রশ্নটি আগে থেকেই My Questions-এ আছে (প্রশ্ন #%d)। আবার সেভ করা হয়নি।" % dup, "error")
                return render_template("teacher/bank/bank_question_form.html", item=data, images=_picker_images(conn),
                                       categories=_categories(conn), editing=False), 409
            now = _now()
            conn.execute(
                """INSERT INTO bank_questions (question_text, image_id, option_a, option_b, option_c, option_d,
                   correct_option, created_at, updated_at, content_hash) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (data["question_text"], data["image_id"], data["option_a"], data["option_b"], data["option_c"],
                 data["option_d"], data["correct_option"], now, now, h))
            conn.commit()
            flash("প্রশ্নটি My Questions-এ সংরক্ষিত হয়েছে (এটি এখনো কোনো Test-এ যোগ হয়নি)।", "success")
            if request.form.get("save_and_new"):
                return redirect(url_for("qbank.question_new"))
            return redirect(url_for("qbank.questions"))
        return render_template("teacher/bank/bank_question_form.html", item={"correct_option": "A"}, images=_picker_images(conn),
                               categories=_categories(conn), editing=False)


@bp.route("/questions/<int:qid>/edit", methods=["GET", "POST"])
@teacher_page
def question_edit(qid):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM bank_questions WHERE id=?", (qid,)).fetchone()
        if row is None:
            abort(404)
        if request.method == "POST":
            data = _read_question_form()
            err = _validate_question(conn, data)
            if err:
                flash(err, "error")
                data["id"] = qid
                return render_template("teacher/bank/bank_question_form.html", item=data, images=_picker_images(conn),
                                       categories=_categories(conn), editing=True), 400
            h = _hash_of(data)
            dup = qlib.find_bank_duplicate(conn, h, exclude_id=qid)
            if dup:
                flash("এই লেখা, Option ও ছবির আরেকটি প্রশ্ন My Questions-এ আগেই আছে (প্রশ্ন #%d)।" % dup, "error")
                data["id"] = qid
                return render_template("teacher/bank/bank_question_form.html", item=data, images=_picker_images(conn),
                                       categories=_categories(conn), editing=True), 409
            conn.execute(
                """UPDATE bank_questions SET question_text=?, image_id=?, option_a=?, option_b=?, option_c=?,
                   option_d=?, correct_option=?, updated_at=?, content_hash=? WHERE id=?""",
                (data["question_text"], data["image_id"], data["option_a"], data["option_b"], data["option_c"],
                 data["option_d"], data["correct_option"], _now(), h, qid))
            conn.commit()
            flash("প্রশ্ন আপডেট হয়েছে। (যেসব Test-এ আগেই নেওয়া হয়েছে সেগুলোর কপি বদলায়নি।)", "success")
            return redirect(url_for("qbank.questions"))
        return render_template("teacher/bank/bank_question_form.html", item=dict(row), images=_picker_images(conn),
                               categories=_categories(conn), editing=True)


@bp.route("/questions/<int:qid>/delete", methods=["POST"])
@teacher_page
def question_delete(qid):
    with get_db() as conn:
        # Only this bank row goes. Library images and copies already placed in tests stay untouched.
        cur = conn.execute("DELETE FROM bank_questions WHERE id=?", (qid,))
        conn.commit()
    if cur.rowcount:
        flash("প্রশ্নটি My Questions থেকে মুছে ফেলা হয়েছে। (Test-এ থাকা কপি ও Library-র ছবি অক্ষত আছে।)", "success")
    else:
        flash("প্রশ্নটি পাওয়া যায়নি।", "error")
    return redirect(safe_back("qbank.questions"))


# ------------------------------------------------- My Questions -> chosen test
@bp.route("/import/<int:test_id>", methods=["GET", "POST"])
@teacher_page
def import_to_test(test_id):
    """Import saved questions into ONE chosen test. Draft tests only - checked here in the backend
    (the page also hides the form), duplicates are refused here too (not only in JavaScript)."""
    workspace = url_for("studio.test_questions", test_id=test_id)
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)
        if not publish_service.is_editable(test):
            flash(qlib.PUBLISHED_MESSAGE, "error")
            return redirect(workspace)
        if request.method == "POST":
            ids = []
            for raw in request.form.getlist("question_id"):
                v = _int(raw)
                if v and v not in ids:
                    ids.append(v)
            if not ids:
                flash("কোনো প্রশ্ন বাছা হয়নি। আগে তালিকা থেকে অন্তত একটি প্রশ্ন টিক দিন।", "error")
                return redirect(url_for("qbank.import_to_test", test_id=test_id))
            if len(ids) > MAX_IMPORT_AT_ONCE:
                flash("একবারে সর্বোচ্চ %d টি প্রশ্ন নেওয়া যাবে।" % MAX_IMPORT_AT_ONCE, "error")
                return redirect(url_for("qbank.import_to_test", test_id=test_id))
            marks = max(1, min(100, _int(request.form.get("marks"), 1) or 1))
            marks_by_id = {i: max(1, min(100, _int(request.form.get("marks_%d" % i), marks) or marks)) for i in ids}
            conn.execute("BEGIN IMMEDIATE")
            try:
                res = qlib.import_into_test(conn, test, ids, marks, marks_by_id)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            if res["added"]:
                flash("%d টি প্রশ্ন \"%s\" Test-এ যোগ হয়েছে।" % (res["added"], test["title"]), "success")
            if res["skipped"]:
                reasons = {}
                for sk in res["skipped"]:
                    reasons.setdefault(sk["reason"], 0)
                    reasons[sk["reason"]] += 1
                for reason, n in reasons.items():
                    flash("%d টি প্রশ্ন যোগ হয়নি: %s" % (n, reason), "error")
            return redirect(workspace)
        q = request.args.get("q", "").strip()[:100]
        cat = _int(request.args.get("category"))
        where, args = [], []
        if q:
            where.append("b.question_text LIKE ? ESCAPE '\\'")
            args.append("%" + escape_like(q) + "%")
        if cat:
            where.append("i.category_id=?")
            args.append(cat)
        sql = """SELECT b.*, i.url AS image_url FROM bank_questions b
                 LEFT JOIN bank_images i ON i.id = b.image_id"""
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = conn.execute(sql + " ORDER BY b.id DESC LIMIT ?", (*args, LIST_LIMIT)).fetchall()
        taken_ids = qlib.ids_already_in_test(conn, test_id, rows)
        cats = _categories(conn)
    return render_template("teacher/bank/bank_import.html", test=test, rows=rows, categories=cats, q=q, selected=cat,
                           taken_ids=taken_ids, limit=MAX_IMPORT_AT_ONCE)
