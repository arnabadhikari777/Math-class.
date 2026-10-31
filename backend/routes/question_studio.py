# -*- coding: utf-8 -*-
"""Image Library + ছবিসহ প্রশ্ন তৈরি (Question Studio) - নতুন করে লেখা।

URL prefix: /teacher/studio
    /library                        ছবির Library (Category, আপলোড, মোছা)
    /api/images, /api/upload        picker ও drag-drop আপলোডের JSON API
    /tests/<id>/questions           Test-এ প্রশ্ন যোগ (ছবি বেছে / আপলোড করে)
    /questions/<id>/image           আগের কোনো প্রশ্নে ছবি বসানো / সরানো

পুরনো ডাটাবেস টেবিলই ব্যবহার হয় (bank_categories, bank_images, questions.image_url,
questions.bank_image_id), তাই আগের সব ছবি ও প্রশ্ন যেমন ছিল তেমনই থাকে।
Question Bank আর লাগে না - প্রশ্ন সরাসরি Test-এ যায়।
"""
from datetime import datetime
from functools import wraps

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template, request,
                   session, url_for)

from .. import image_store as store
from ..db import get_db

bp = Blueprint("studio", __name__, url_prefix="/teacher/studio")

MAX_FILES_PER_UPLOAD = 10
MAX_QUESTIONS_PER_SAVE = 100
PICKER_LIMIT = 1000


def teacher_only(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("is_teacher"):
            if request.path.startswith("/teacher/studio/api/") or request.is_json:
                return jsonify({"ok": False, "error": "Teacher login দরকার।"}), 401
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
    return " ".join(str(raw or "").split())[:60]


# ----------------------------------------------------------------- queries
def _categories(conn):
    return conn.execute(
        """SELECT c.id, c.name, COUNT(i.id) AS image_count
           FROM bank_categories c LEFT JOIN bank_images i ON i.category_id = c.id
           GROUP BY c.id ORDER BY c.name COLLATE NOCASE""").fetchall()


def _usage(conn, image_id, url):
    """একটি ছবি কতগুলো Test-প্রশ্নে / (পুরনো) Bank-প্রশ্নে ব্যবহার হচ্ছে।"""
    tests = conn.execute("SELECT COUNT(*) FROM questions WHERE bank_image_id=? OR image_url=?",
                         (image_id, url)).fetchone()[0]
    bank = conn.execute("SELECT COUNT(*) FROM bank_questions WHERE image_id=?", (image_id,)).fetchone()[0]
    return tests, bank


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


def _get_or_create_category(conn, category_id=None, new_name=None):
    """category_id থাকলে সেটি, নইলে new_name দিয়ে (আগে থাকলে সেটাই) Category। None মানে অবৈধ।"""
    cid = _int(category_id)
    if cid:
        return conn.execute("SELECT * FROM bank_categories WHERE id=?", (cid,)).fetchone()
    name = _clean_name(new_name)
    if not name:
        return None
    row = conn.execute("SELECT * FROM bank_categories WHERE name=? COLLATE NOCASE", (name,)).fetchone()
    if row:
        return row
    new_id = conn.execute("INSERT INTO bank_categories (name, created_at) VALUES (?,?)", (name, _now())).lastrowid
    conn.commit()
    return conn.execute("SELECT * FROM bank_categories WHERE id=?", (new_id,)).fetchone()


def _save_uploads(files, cat):
    """ফাইলগুলো Cloudinary-তে তুলে ডাটাবেসে রাখে। Return (uploaded_dicts, error_messages)।"""
    uploaded, errors = [], []
    problem = store.config_problem()
    if problem:
        return uploaded, [problem]
    for f in files:
        data = f.read(store.max_bytes() + 1)
        try:
            info = store.upload(f.filename, data, category=cat["name"])
        except store.ImageStoreError as exc:
            errors.append(str(exc))
            continue
        try:
            with get_db() as conn:
                new_id = conn.execute(
                    """INSERT INTO bank_images (category_id, url, public_id, original_name, width, height,
                       bytes, format, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (cat["id"], info["url"], info["public_id"], (f.filename or "")[:150], info["width"],
                     info["height"], info["bytes"], info["format"], _now())).lastrowid
                conn.commit()
            uploaded.append({"id": new_id, "url": info["url"], "thumb": store.thumb(info["url"], 320),
                             "name": (f.filename or "")[:150], "category_id": cat["id"], "category": cat["name"]})
        except Exception:  # ডাটাবেসে না গেলে Cloudinary-তে অনাথ ছবি রাখা হয় না
            try:
                store.destroy(info["public_id"])
            except store.ImageStoreError:
                pass
            errors.append("'%s': ডাটাবেসে সংরক্ষণ করা যায়নি।" % f.filename)
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
    return render_template("studio_library.html", categories=cats, images=rows, selected=cat,
                           problem=store.config_problem(), max_mb=store.max_bytes() // (1024 * 1024),
                           max_files=MAX_FILES_PER_UPLOAD)


@bp.route("/library/category", methods=["POST"])
@teacher_only
def category_create():
    name = _clean_name(request.form.get("name"))
    if not name:
        flash("Category-র নাম দিন।", "error")
        return redirect(url_for("studio.library"))
    with get_db() as conn:
        exists = conn.execute("SELECT id FROM bank_categories WHERE name=? COLLATE NOCASE", (name,)).fetchone()
        if exists:
            flash("এই নামে Category আগে থেকেই আছে।", "error")
            return redirect(url_for("studio.library", category=exists["id"]))
        cid = conn.execute("INSERT INTO bank_categories (name, created_at) VALUES (?,?)", (name, _now())).lastrowid
        conn.commit()
    flash("Category তৈরি হয়েছে: %s" % name, "success")
    return redirect(url_for("studio.library", category=cid))


@bp.route("/library/category/<int:cid>/delete", methods=["POST"])
@teacher_only
def category_delete(cid):
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM bank_categories WHERE id=?", (cid,)).fetchone() is None:
            abort(404)
        n = conn.execute("SELECT COUNT(*) FROM bank_images WHERE category_id=?", (cid,)).fetchone()[0]
        if n:
            flash("Category-তে %d টি ছবি আছে, তাই মোছা হয়নি। আগে ছবিগুলো মুছুন।" % n, "error")
            return redirect(url_for("studio.library", category=cid))
        conn.execute("DELETE FROM bank_categories WHERE id=?", (cid,))
        conn.commit()
    flash("Category মুছে ফেলা হয়েছে।", "success")
    return redirect(url_for("studio.library"))


@bp.route("/library/upload", methods=["POST"])
@teacher_only
def library_upload():
    """সাধারণ form আপলোড (JS বন্ধ থাকলেও কাজ করে)।"""
    with get_db() as conn:
        cat = _get_or_create_category(conn, request.form.get("category_id"), request.form.get("new_category"))
    if cat is None:
        flash("আগে একটি Category বেছে নিন বা নতুন নাম দিন।", "error")
        return redirect(url_for("studio.library"))
    files = [f for f in request.files.getlist("images") if f and f.filename]
    if not files:
        flash("কোনো ছবি বাছা হয়নি।", "error")
    elif len(files) > MAX_FILES_PER_UPLOAD:
        flash("একবারে সর্বোচ্চ %d টি ছবি আপলোড করা যাবে।" % MAX_FILES_PER_UPLOAD, "error")
    else:
        done, errors = _save_uploads(files, cat)
        if done:
            flash("%d টি ছবি আপলোড হয়েছে।" % len(done), "success")
        for e in errors:
            flash(e, "error")
    return redirect(url_for("studio.library", category=cat["id"]))


@bp.route("/library/images/<int:image_id>/delete", methods=["POST"])
@teacher_only
def image_delete(image_id):
    with get_db() as conn:
        img = conn.execute("SELECT * FROM bank_images WHERE id=?", (image_id,)).fetchone()
        if img is None:
            abort(404)
        tests, bank = _usage(conn, image_id, img["url"])
    back = url_for("studio.library", category=img["category_id"])
    if tests or bank:
        flash("ছবিটি %d টি প্রশ্নে ব্যবহৃত হচ্ছে, তাই মোছা হয়নি। আগে সেই প্রশ্ন থেকে ছবি সরান।" % (tests + bank), "error")
        return redirect(back)
    try:
        store.destroy(img["public_id"])
    except store.ImageStoreError as exc:
        flash(str(exc), "error")
        return redirect(back)
    with get_db() as conn:
        tests, bank = _usage(conn, image_id, img["url"])  # Cloudinary-র উত্তরের ফাঁকে কেউ ব্যবহার করলো কি না
        if tests or bank:
            flash("ছবিটি এইমাত্র কোনো প্রশ্নে ব্যবহৃত হয়েছে; Library রেকর্ড রাখা হয়েছে।", "error")
        else:
            conn.execute("DELETE FROM bank_images WHERE id=?", (image_id,))
            conn.commit()
            flash("ছবি মুছে ফেলা হয়েছে।", "success")
    return redirect(back)


# ================================================================ JSON API
@bp.route("/api/images")
@teacher_only
def api_images():
    with get_db() as conn:
        images = _list_images(conn, _int(request.args.get("category")), request.args.get("q", "").strip()[:60])
        cats = [{"id": c["id"], "name": c["name"], "count": c["image_count"]} for c in _categories(conn)]
    return jsonify({"ok": True, "images": images, "categories": cats})


@bp.route("/api/upload", methods=["POST"])
@teacher_only
def api_upload():
    """Picker-এর ভেতর থেকে সরাসরি আপলোড: category_id অথবা new_category + images[]।"""
    files = [f for f in request.files.getlist("images") if f and f.filename]
    if not files:
        return jsonify({"ok": False, "error": "কোনো ছবি বাছা হয়নি।"}), 400
    if len(files) > MAX_FILES_PER_UPLOAD:
        return jsonify({"ok": False, "error": "একবারে সর্বোচ্চ %d টি ছবি।" % MAX_FILES_PER_UPLOAD}), 400
    with get_db() as conn:
        cat = _get_or_create_category(conn, request.form.get("category_id"), request.form.get("new_category"))
    if cat is None:
        return jsonify({"ok": False, "error": "Category বেছে নিন বা নতুন নাম দিন।"}), 400
    done, errors = _save_uploads(files, cat)
    return jsonify({"ok": bool(done), "images": done, "errors": errors,
                    "category": {"id": cat["id"], "name": cat["name"]}}), (200 if done else 400)


# ====================================================== Test-এ প্রশ্ন (ছবিসহ)
@bp.route("/tests/<int:test_id>/questions")
@teacher_only
def test_questions(test_id):
    with get_db() as conn:
        test = conn.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
        if test is None:
            abort(404)
        questions = conn.execute("SELECT * FROM questions WHERE test_id=? ORDER BY id", (test_id,)).fetchall()
    return render_template("studio_questions.html", test=test, questions=questions,
                           problem=store.config_problem(), max_files=MAX_FILES_PER_UPLOAD,
                           max_mb=store.max_bytes() // (1024 * 1024))


def _validate_item(conn, item, n):
    """একটি প্রশ্নের JSON যাচাই। Return (clean_dict, error_or_None)।"""
    if not isinstance(item, dict):
        return None, "প্রশ্ন %d: ডেটা ভুল।" % n
    text = str(item.get("text") or "").strip()
    opts = {k: str(item.get(k) or "").strip() for k in ("a", "b", "c", "d")}
    correct = str(item.get("correct") or "").strip().upper()
    image_id = _int(item.get("image_id"))
    marks = max(1, min(100, _int(item.get("marks"), 1) or 1))
    image_url = None
    if image_id:
        row = conn.execute("SELECT url FROM bank_images WHERE id=?", (image_id,)).fetchone()
        if row is None:
            return None, "প্রশ্ন %d: বাছাই করা ছবিটি Library-তে নেই।" % n
        image_url = row["url"]
    if not text and not image_url:
        return None, "প্রশ্ন %d: প্রশ্নের লেখা অথবা ছবি অন্তত একটি দিন।" % n
    if not all(opts.values()):
        return None, "প্রশ্ন %d: চারটি Option-ই পূরণ করুন।" % n
    if correct not in ("A", "B", "C", "D"):
        return None, "প্রশ্ন %d: সঠিক উত্তর (A/B/C/D) বাছুন।" % n
    return {"text": text, "opts": opts, "correct": correct, "marks": marks,
            "image_id": image_id, "image_url": image_url}, None


@bp.route("/tests/<int:test_id>/questions/save", methods=["POST"])
@teacher_only
def test_questions_save(test_id):
    """সব প্রশ্ন একসাথে যাচাই; একটিও ভুল থাকলে কিছুই সেভ হয় না (ডেটা হারায় না, ফর্ম খোলা থাকে)।"""
    payload = request.get_json(silent=True) or {}
    items = payload.get("questions")
    if not isinstance(items, list) or not items:
        return jsonify({"ok": False, "errors": [{"index": 0, "message": "কোনো প্রশ্ন পাঠানো হয়নি।"}]}), 400
    if len(items) > MAX_QUESTIONS_PER_SAVE:
        return jsonify({"ok": False, "errors": [{"index": 0, "message": "একবারে সর্বোচ্চ %d টি প্রশ্ন।" % MAX_QUESTIONS_PER_SAVE}]}), 400
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM tests WHERE id=?", (test_id,)).fetchone() is None:
            return jsonify({"ok": False, "errors": [{"index": 0, "message": "Test পাওয়া যায়নি।"}]}), 404
        clean, errors = [], []
        for i, item in enumerate(items):
            c, err = _validate_item(conn, item, i + 1)
            if err:
                errors.append({"index": i, "message": err})
            else:
                clean.append(c)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        for c in clean:
            conn.execute(
                """INSERT INTO questions (test_id, question_text, option_a, option_b, option_c, option_d,
                   correct_option, marks, image_url, bank_image_id) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (test_id, c["text"], c["opts"]["a"], c["opts"]["b"], c["opts"]["c"], c["opts"]["d"],
                 c["correct"], c["marks"], c["image_url"], c["image_id"]))
        conn.commit()
    flash("%d টি প্রশ্ন যোগ হয়েছে।" % len(clean), "success")
    return jsonify({"ok": True, "added": len(clean)})


@bp.route("/questions/<int:qid>/image", methods=["POST"])
@teacher_only
def question_set_image(qid):
    """আগের কোনো প্রশ্নে ছবি বসায় বা (image_id খালি হলে) সরায়। Test-এর বাকি কিছু বদলায় না।"""
    with get_db() as conn:
        q = conn.execute("SELECT id, test_id, question_text FROM questions WHERE id=?", (qid,)).fetchone()
        if q is None:
            abort(404)
        image_id = _int(request.form.get("image_id"))
        if image_id:
            img = conn.execute("SELECT url FROM bank_images WHERE id=?", (image_id,)).fetchone()
            if img is None:
                flash("বাছাই করা ছবিটি Library-তে পাওয়া যায়নি।", "error")
                return redirect(url_for("studio.test_questions", test_id=q["test_id"]))
            conn.execute("UPDATE questions SET image_url=?, bank_image_id=? WHERE id=?", (img["url"], image_id, qid))
            flash("প্রশ্নে ছবি বসানো হয়েছে।", "success")
        else:
            if not (q["question_text"] or "").strip():
                flash("এই প্রশ্নে লেখা নেই, তাই ছবি সরানো যাবে না (প্রশ্ন ফাঁকা হয়ে যাবে)।", "error")
                return redirect(url_for("studio.test_questions", test_id=q["test_id"]))
            conn.execute("UPDATE questions SET image_url=NULL, bank_image_id=NULL WHERE id=?", (qid,))
            flash("প্রশ্ন থেকে ছবি সরানো হয়েছে (Library-তে ছবি আছে)।", "success")
        conn.commit()
    return redirect(url_for("studio.test_questions", test_id=q["test_id"]))
