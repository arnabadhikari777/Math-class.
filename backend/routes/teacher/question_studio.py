# -*- coding: utf-8 -*-
"""Test Workspace (code name: studio) + Image Library.

URL prefix: /teacher/studio
    /workspace                      choose a test -> open its Test Workspace
    /tests/<id>/questions           THE Test Workspace of one test (create / import / edit / reorder / preview)
    /tests/<id>/questions/<q>/edit  edit one question of that test (draft only)
    /tests/<id>/questions/<q>/move  move a question up / down (draft only)
    /questions/<id>/save-to-library copy a test question into My Questions (duplicate-proof)
    /questions/<id>/image           set / remove the picture of a test question (draft only)
    /library                        Image Library (categories, upload, delete)
    /api/images, /api/upload        JSON API of the image picker

Old URLs keep working: /teacher/tests/<id>/questions and /teacher/bank/... redirect or render here.
Database tables are the same as before (bank_categories, bank_images, questions.image_url / bank_image_id),
so every existing image and question is untouched.
"""
from datetime import datetime
from functools import wraps

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template, request,
                   session, url_for)

from backend.services import image_service
from backend.services import image_store as store
from backend.services import publish_service
from backend.services import question_library as qlib
from backend.utils.question_hash import content_hash
from backend.db import get_db

bp = Blueprint("studio", __name__, url_prefix="/teacher/studio")

MAX_FILES_PER_UPLOAD = 10
MAX_QUESTIONS_PER_SAVE = 100
PICKER_LIMIT = 1000


def teacher_only(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_teacher"):
            if request.path.startswith("/teacher/studio/api/") or request.is_json:
                return jsonify({"ok": False, "error": "Teacher login required."}), 401
            flash("Teacher login is required.", "error")
            return redirect(url_for("teacher_login"))
        if session.get("impersonating"):
            abort(403)
        return f(*args, **kwargs)
    return wrapper


@bp.app_template_filter("img_thumb")
def _img_thumb(url, width=320):
    return store.thumb(url, width)


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _now():
    return datetime.now().isoformat()


def _clean_name(raw):
    return image_service.clean_category_name(raw)


# ----------------------------------------------------------------- queries
def _categories(conn):
    return conn.execute(
        """SELECT c.id, c.name, COUNT(i.id) AS image_count
           FROM bank_categories c LEFT JOIN bank_images i ON i.category_id = c.id
           GROUP BY c.id ORDER BY c.name COLLATE NOCASE""").fetchall()


def _usage(conn, image_id, url):
    """(test_count, bank_count) - delegates to the ONE shared checker (also covers exam snapshots)."""
    u = image_service.usage(conn, image_id, url)
    return u["total"] - u["bank_questions"], u["bank_questions"]


def _image_dict(row):
    return {"id": row["id"], "url": row["url"], "thumb": store.thumb(row["url"], 320),
            "name": row["original_name"] or ("Image #%d" % row["id"]),
            "category_id": row["category_id"], "category": row["category_name"]}


def _list_images(conn, category=None, q=None, limit=PICKER_LIMIT):
    sql = """SELECT i.id, i.url, i.original_name, i.category_id, c.name AS category_name
             FROM bank_images i JOIN bank_categories c ON c.id = i.category_id"""
    where, args = [], []
    if category:
        where.append("i.category_id=?")
        args.append(category)
    if q:
        where.append("i.original_name LIKE ?")
        args.append("%" + q.replace("%", "").replace("_", "") + "%")
    if where:
        sql += " WHERE " + " AND ".join(where)
    return [_image_dict(r) for r in conn.execute(sql + " ORDER BY i.id DESC LIMIT ?", (*args, limit)).fetchall()]


def _resolve_upload_category(conn, form):
    """-> (category, error). category = {"id": int | None, "name": str}; id None = "does not exist yet".

    Order of rules (the teacher's own choice always wins):
      1. category_id            -> that existing category
      2. new_category (typed)   -> the category with that name, or a new one
      3. test_id (Test Workspace) -> the category named like the test, or a new one
    A category that does not exist yet is NOT created here; it is created after the first successful
    Cloudinary upload (image_service.upload_and_register), so a failed upload leaves nothing behind."""
    cid = _int(form.get("category_id"))
    if cid:
        row = conn.execute("SELECT id, name FROM bank_categories WHERE id=?", (cid,)).fetchone()
        if row is None:
            return None, "The selected category was not found."
        return {"id": row["id"], "name": row["name"]}, None
    name = _clean_name(form.get("new_category"))
    if not name:
        tid = _int(form.get("test_id"))
        if tid:
            t = conn.execute("SELECT title FROM tests WHERE id=?", (tid,)).fetchone()
            if t is None:
                return None, "Test not found."
            name = _clean_name(t["title"])
    if not name:
        return None, "Select a category or enter a new name."
    row = image_service.find_category(conn, name)
    return ({"id": row["id"], "name": row["name"]} if row else {"id": None, "name": name}), None


def _save_uploads(files, cat):
    """ফাইলগুলো Cloudinary-তে তুলে ডাটাবেসে রাখে। Return (uploaded_dicts, error_messages)।"""
    uploaded, errors = [], []
    problem = store.config_problem()
    if problem:
        return uploaded, [problem]
    cat = dict(cat)
    for f in files:
        data = f.read(store.max_bytes() + 1)
        try:
            info = image_service.upload_and_register(f.filename, data, cat)
        except image_service.ImageError as exc:
            errors.append(str(exc))
            continue
        cat["id"], cat["name"] = info["category_id"], info["category_name"]   # later files reuse the same category
        uploaded.append({"id": info["id"], "url": info["url"], "thumb": store.thumb(info["url"], 320),
                         "name": (f.filename or "")[:150], "category_id": cat["id"], "category": cat["name"]})
    return uploaded, errors


# ============================================================ Image Library
@bp.route("/")
@teacher_only
def home():
    return redirect(url_for("studio.library"))


@bp.route("/library")
@teacher_only
def library():
    cat = _int(request.args.get("category"))
    with get_db() as conn:
        cats = _categories(conn)
        sql = """SELECT i.*, c.name AS category_name,
                        (SELECT COUNT(*) FROM questions t WHERE t.bank_image_id = i.id OR t.image_url = i.url) AS used_tests,
                        (SELECT COUNT(*) FROM bank_questions q WHERE q.image_id = i.id) AS used_bank
                 FROM bank_images i JOIN bank_categories c ON c.id = i.category_id"""
        args = ()
        if cat:
            sql += " WHERE i.category_id=?"
            args = (cat,)
        rows = conn.execute(sql + " ORDER BY i.id DESC LIMIT 500", args).fetchall()
    return render_template("teacher/studio/studio_library.html", categories=cats, images=rows, selected=cat,
                           problem=store.config_problem(), max_mb=store.max_bytes() // (1024 * 1024),
                           max_files=MAX_FILES_PER_UPLOAD)


@bp.route("/library/category", methods=["POST"])
@teacher_only
def category_create():
    name = _clean_name(request.form.get("name"))
    if not name:
        flash("Enter a category name.", "error")
        return redirect(url_for("studio.library"))
    with get_db() as conn:
        exists = conn.execute("SELECT id FROM bank_categories WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        if exists:
            flash("A category with this name already exists.", "error")
            return redirect(url_for("studio.library", category=exists["id"]))
        cid = conn.execute("INSERT INTO bank_categories (name, created_at) VALUES (?,?)", (name, _now())).lastrowid
        conn.commit()
    flash("Category created: %s" % name, "success")
    return redirect(url_for("studio.library", category=cid))


@bp.route("/library/category/<int:cid>/delete", methods=["POST"])
@teacher_only
def category_delete(cid):
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM bank_categories WHERE id=?", (cid,)).fetchone() is None:
            abort(404)
        n = conn.execute("SELECT COUNT(*) FROM bank_images WHERE category_id=?", (cid,)).fetchone()[0]
        if n:
            flash("This category contains %d image(s), so it was not deleted. Delete the images first." % n, "error")
            return redirect(url_for("studio.library", category=cid))
        conn.execute("DELETE FROM bank_categories WHERE id=?", (cid,))
        conn.commit()
    flash("Category deleted.", "success")
    return redirect(url_for("studio.library"))


@bp.route("/library/upload", methods=["POST"])
@teacher_only
def library_upload():
    """সাধারণ form আপলোড (JS বন্ধ থাকলেও কাজ করে)।"""
    with get_db() as conn:     # no test_id here: the general library asks the teacher to choose, as before
        cat, err = _resolve_upload_category(conn, {"category_id": request.form.get("category_id"),
                                                   "new_category": request.form.get("new_category")})
    if cat is None:
        flash("Select a category or enter a new name first.", "error")
        return redirect(url_for("studio.library"))
    files = [f for f in request.files.getlist("images") if f and f.filename]
    if not files:
        flash("No images selected.", "error")
    elif len(files) > MAX_FILES_PER_UPLOAD:
        flash("You can upload up to %d images at a time." % MAX_FILES_PER_UPLOAD, "error")
    else:
        done, errors = _save_uploads(files, cat)
        if done:
            flash("%d image(s) uploaded." % len(done), "success")
            cat = {"id": done[0]["category_id"], "name": done[0]["category"]}
        for e in errors:
            flash(e, "error")
    return redirect(url_for("studio.library", category=cat["id"]) if cat["id"] else url_for("studio.library"))


@bp.route("/library/images/<int:image_id>/delete", methods=["POST"])
@teacher_only
def image_delete(image_id):
    with get_db() as conn:
        img = conn.execute("SELECT * FROM bank_images WHERE id=?", (image_id,)).fetchone()
    if img is None:
        abort(404)
    state, message = image_service.safe_delete(image_id)
    flash(message, "success" if state == "deleted" else "error")
    return redirect(url_for("studio.library", category=img["category_id"]))


# ================================================================ JSON API
@bp.route("/api/images")
@teacher_only
def api_images():
    """Picker data. With ?test_id= it also says which category is the DEFAULT for a new upload
    (the one named like that test, even if it does not exist yet)."""
    test_id = _int(request.args.get("test_id"))
    default = None
    with get_db() as conn:
        images = _list_images(conn, _int(request.args.get("category")), request.args.get("q", "").strip()[:60])
        cats = [{"id": c["id"], "name": c["name"], "count": c["image_count"]} for c in _categories(conn)]
        if test_id:
            t = conn.execute("SELECT title FROM tests WHERE id=?", (test_id,)).fetchone()
            if t is not None:
                name = _clean_name(t["title"])
                row = image_service.find_category(conn, name) if name else None
                if name:
                    default = {"id": row["id"] if row else None, "name": row["name"] if row else name,
                               "exists": row is not None}
    return jsonify({"ok": True, "images": images, "categories": cats, "default_category": default})


@bp.route("/api/upload", methods=["POST"])
@teacher_only
def api_upload():
    """Picker upload: images[] + (category_id | new_category | test_id). See _resolve_upload_category."""
    files = [f for f in request.files.getlist("images") if f and f.filename]
    if not files:
        return jsonify({"ok": False, "error": "No images selected."}), 400
    if len(files) > MAX_FILES_PER_UPLOAD:
        return jsonify({"ok": False, "error": "A maximum of %d images at a time." % MAX_FILES_PER_UPLOAD}), 400
    with get_db() as conn:
        cat, err = _resolve_upload_category(conn, request.form)
    if cat is None:
        return jsonify({"ok": False, "error": err}), 400
    done, errors = _save_uploads(files, cat)
    if done:
        cat = {"id": done[0]["category_id"], "name": done[0]["category"]}
    return jsonify({"ok": bool(done), "images": done, "errors": errors,
                    "category": {"id": cat["id"], "name": cat["name"]}}), (200 if done else 400)


# ============================================================ Test Workspace
def _get_test(conn, test_id):
    test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
    if test is None:
        abort(404)
    return test


def _blocked_json(message="This test is published. Unpublish it to add or change questions."):
    return jsonify({"ok": False, "errors": [{"index": 0, "message": message}]}), 409


def render_workspace():
    """THE test-management page. /teacher/studio/workspace and the old /teacher/tests (All Tests) both render it,
    so there is only one management interface. Counts come from sub-queries: one query, no per-row lookups."""
    with get_db() as conn:
        tests = conn.execute(
            """SELECT t.*,
                      (SELECT COUNT(*) FROM questions q WHERE q.test_id=t.id) AS q_count,
                      (SELECT COALESCE(SUM(q.marks),0) FROM questions q WHERE q.test_id=t.id) AS total_marks,
                      (SELECT COUNT(*) FROM results r WHERE r.test_id=t.id) AS r_count
               FROM tests t WHERE COALESCE(t.is_archived,0)=0 ORDER BY t.id DESC""").fetchall()
        archived = conn.execute(
            """SELECT t.*, (SELECT COUNT(*) FROM results r WHERE r.test_id=t.id) AS r_count
               FROM tests t WHERE t.is_archived=1 ORDER BY t.archived_at DESC""").fetchall()
    return render_template("teacher/studio/workspace_home.html", tests=tests, archived_tests=archived)


@bp.route("/workspace")
@teacher_only
def workspace_home():
    return render_workspace()


@bp.route("/tests/<int:test_id>/questions")
@teacher_only
def test_questions(test_id):
    with get_db() as conn:
        test = _get_test(conn, test_id)
        questions = qlib.ordered_questions(conn, test_id)
        total_marks = sum((q["marks"] or 0) for q in questions)
        hashes = {q["id"]: qlib.test_question_hash(q) for q in questions}
        saved = set()
        if hashes:
            marks = ",".join("?" * len(set(hashes.values())))
            saved = {r[0] for r in conn.execute(
                "SELECT content_hash FROM bank_questions WHERE content_hash IN (%s)" % marks, tuple(set(hashes.values())))}
    in_library = {qid for qid, h in hashes.items() if h in saved}
    return render_template("teacher/studio/studio_questions.html", test=test, questions=questions,
                           total_marks=total_marks, editable=publish_service.is_editable(test),
                           in_library=in_library, problem=store.config_problem(),
                           max_files=MAX_FILES_PER_UPLOAD, max_mb=store.max_bytes() // (1024 * 1024))


def _validate_item(conn, item, n):
    """একটি প্রশ্নের JSON যাচাই। Return (clean_dict, error_or_None)।"""
    if not isinstance(item, dict):
        return None, "Question %d: invalid data." % n
    text = str(item.get("text") or "").strip()
    opts = {k: str(item.get(k) or "").strip() for k in ("a", "b", "c", "d")}
    correct = str(item.get("correct") or "").strip().upper()
    image_id = _int(item.get("image_id"))
    marks = max(1, min(100, _int(item.get("marks"), 1) or 1))
    image_url = None
    if image_id:
        row = conn.execute("SELECT url FROM bank_images WHERE id=?", (image_id,)).fetchone()
        if row is None:
            return None, "Question %d: the selected image is not in the library." % n
        image_url = row["url"]
    if not text and not image_url:
        return None, "Question %d: provide the question text or an image (at least one)." % n
    if not all(opts.values()):
        return None, "Question %d: fill in all four options." % n
    if correct not in ("A", "B", "C", "D"):
        return None, "Question %d: select the correct answer (A/B/C/D)." % n
    return {"text": text, "opts": opts, "correct": correct, "marks": marks,
            "image_id": image_id, "image_url": image_url}, None


@bp.route("/tests/<int:test_id>/questions/save", methods=["POST"])
@teacher_only
def test_questions_save(test_id):
    """সব প্রশ্ন একসাথে যাচাই; একটিও ভুল থাকলে কিছুই সেভ হয় না (ডেটা হারায় না, ফর্ম খোলা থাকে)।
    এই প্রশ্নগুলো সরাসরি এই Test-এ যায় (My Questions-এ নয়)।"""
    payload = request.get_json(silent=True) or {}
    items = payload.get("questions")
    if not isinstance(items, list) or not items:
        return jsonify({"ok": False, "errors": [{"index": 0, "message": "No questions were submitted."}]}), 400
    if len(items) > MAX_QUESTIONS_PER_SAVE:
        return jsonify({"ok": False, "errors": [{"index": 0, "message": "A maximum of %d questions at a time." % MAX_QUESTIONS_PER_SAVE}]}), 400
    with get_db() as conn:
        trow = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if trow is None:
            return jsonify({"ok": False, "errors": [{"index": 0, "message": "Test not found."}]}), 404
        if not publish_service.is_editable(trow):
            return _blocked_json()
        clean, errors = [], []
        for i, item in enumerate(items):
            c, err = _validate_item(conn, item, i + 1)
            if err:
                errors.append({"index": i, "message": err})
            else:
                clean.append(c)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        order = qlib.next_sort_order(conn, test_id)
        for c in clean:
            conn.execute(
                """INSERT INTO questions (test_id, question_text, option_a, option_b, option_c, option_d,
                   correct_option, marks, image_url, bank_image_id, sort_order) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (test_id, c["text"], c["opts"]["a"], c["opts"]["b"], c["opts"]["c"], c["opts"]["d"],
                 c["correct"], c["marks"], c["image_url"], c["image_id"], order))
            order += 1
        conn.commit()
    flash("%d question(s) added to the test \"%s\" (not to My Questions; click \"Save to My Questions\" next to a question to keep it)."
          % (len(clean), trow["title"]), "success")
    return jsonify({"ok": True, "added": len(clean)})


@bp.route("/tests/<int:test_id>/questions/<int:qid>/edit", methods=["GET", "POST"])
@teacher_only
def question_edit(test_id, qid):
    """Edit ONE question of this test (a copy: the My Questions original is never touched)."""
    with get_db() as conn:
        test = _get_test(conn, test_id)
        q = conn.execute("SELECT * FROM questions WHERE id=? AND test_id=?", (qid, test_id)).fetchone()
        if q is None:
            abort(404)
        if not publish_service.is_editable(test):
            flash(qlib.PUBLISHED_MESSAGE, "error")
            return redirect(url_for("studio.test_questions", test_id=test_id))
        if request.method == "POST":
            f = request.form
            item = {"text": f.get("question_text"), "a": f.get("option_a"), "b": f.get("option_b"),
                    "c": f.get("option_c"), "d": f.get("option_d"), "correct": f.get("correct_option"),
                    "marks": f.get("marks"), "image_id": f.get("image_id")}
            clean, err = _validate_item(conn, item, 1)
            if err:
                flash(err.split(": ", 1)[-1], "error")
                keep = dict(q)
                keep.update(question_text=item["text"], option_a=item["a"], option_b=item["b"], option_c=item["c"],
                            option_d=item["d"], correct_option=(item["correct"] or "A"), marks=item["marks"])
                return render_template("teacher/studio/studio_question_edit.html", test=test, q=keep,
                                       image_url=None, problem=store.config_problem(),
                                       max_files=MAX_FILES_PER_UPLOAD, max_mb=store.max_bytes() // (1024 * 1024)), 400
            conn.execute(
                """UPDATE questions SET question_text=?, option_a=?, option_b=?, option_c=?, option_d=?,
                   correct_option=?, marks=?, image_url=?, bank_image_id=? WHERE id=? AND test_id=?""",
                (clean["text"], clean["opts"]["a"], clean["opts"]["b"], clean["opts"]["c"], clean["opts"]["d"],
                 clean["correct"], clean["marks"], clean["image_url"], clean["image_id"], qid, test_id))
            conn.commit()
            flash("Question updated. (The original in My Questions is unchanged.)", "success")
            return redirect(url_for("studio.test_questions", test_id=test_id))
    return render_template("teacher/studio/studio_question_edit.html", test=test, q=q, image_url=q["image_url"],
                           problem=store.config_problem(), max_files=MAX_FILES_PER_UPLOAD,
                           max_mb=store.max_bytes() // (1024 * 1024))


@bp.route("/tests/<int:test_id>/questions/<int:qid>/move", methods=["POST"])
@teacher_only
def question_move(test_id, qid):
    """Move one question up or down. Draft tests only; order is stored in questions.sort_order."""
    direction = request.form.get("dir")
    if direction not in ("up", "down"):
        abort(400)
    with get_db() as conn:
        test = _get_test(conn, test_id)
        if not publish_service.is_editable(test):
            flash(qlib.PUBLISHED_MESSAGE, "error")
            return redirect(url_for("studio.test_questions", test_id=test_id))
        ids = [r["id"] for r in qlib.ordered_questions(conn, test_id)]
        if qid not in ids:
            abort(404)
        i = ids.index(qid)
        j = i - 1 if direction == "up" else i + 1
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
            for pos, question_id in enumerate(ids, 1):
                conn.execute("UPDATE questions SET sort_order=? WHERE id=? AND test_id=?", (pos, question_id, test_id))
            conn.commit()
    return redirect(url_for("studio.test_questions", test_id=test_id) + "#q%d" % qid)


@bp.route("/questions/<int:qid>/save-to-library", methods=["POST"])
@teacher_only
def question_save_to_library(qid):
    """Test question -> My Questions (a separate copy). Saving the same question twice is refused."""
    with get_db() as conn:
        q = conn.execute("SELECT test_id FROM questions WHERE id=?", (qid,)).fetchone()
        if q is None:
            abort(404)
        conn.execute("BEGIN IMMEDIATE")
        try:
            state, bank_id = qlib.save_to_my_questions(conn, qid)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    if state == "saved":
        flash("The question was saved to My Questions (question #%d). The test question is unchanged." % bank_id, "success")
    elif state == "exists":
        flash("This question already exists in My Questions (question #%d), so it was not saved again." % bank_id, "error")
    elif state == "no_image":
        flash("The image of this image-only question is not in the Image Library, so it could not be saved to My Questions.", "error")
    else:
        flash("Question not found.", "error")
    return redirect(url_for("studio.test_questions", test_id=q["test_id"]) + "#q%d" % qid)


@bp.route("/questions/<int:qid>/image", methods=["POST"])
@teacher_only
def question_set_image(qid):
    """আগের কোনো প্রশ্নে ছবি বসায় বা (image_id খালি হলে) সরায়। Draft test only; বাকি কিছু বদলায় না।"""
    with get_db() as conn:
        q = conn.execute("SELECT id, test_id, question_text FROM questions WHERE id=?", (qid,)).fetchone()
        if q is None:
            abort(404)
        test = conn.execute("SELECT * FROM tests WHERE id=?", (q["test_id"],)).fetchone()
        back = redirect(url_for("studio.test_questions", test_id=q["test_id"]))
        if test is None or not publish_service.is_editable(test):
            flash(qlib.PUBLISHED_MESSAGE, "error")
            return back
        image_id = _int(request.form.get("image_id"))
        if image_id:
            img = conn.execute("SELECT url FROM bank_images WHERE id=?", (image_id,)).fetchone()
            if img is None:
                flash("The selected image was not found in the library.", "error")
                return back
            conn.execute("UPDATE questions SET image_url=?, bank_image_id=? WHERE id=?", (img["url"], image_id, qid))
            flash("Image attached to the question.", "success")
        else:
            if not (q["question_text"] or "").strip():
                flash("This question has no text, so its image cannot be removed (the question would become empty).", "error")
                return back
            conn.execute("UPDATE questions SET image_url=NULL, bank_image_id=NULL WHERE id=?", (qid,))
            flash("Image removed from the question (it remains in the library).", "success")
        conn.commit()
    return back
