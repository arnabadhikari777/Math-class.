# -*- coding: utf-8 -*-
"""Student result PDF generation (reportlab)."""

import io
import os

from backend.utils.helpers import percentage


# PDF generation (Bengali-capable via FreeSerif)
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdf_canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False


class PdfFontError(Exception):
    """The PDF contains Bengali text but this server has no font that can draw it."""


_BN_PROBE = (0x0995, 0x09BE, 0x09CD)    # ক, া, ্  - a font missing these cannot render Bengali
_FONT_PAIRS = [
    # (regular, bold) - first pair that registers AND covers Bengali wins
    ("/usr/share/fonts/truetype/freefont/FreeSerif.ttf", "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf"),
    ("/usr/share/fonts/truetype/noto/NotoSerifBengali-Regular.ttf", "/usr/share/fonts/truetype/noto/NotoSerifBengali-Bold.ttf"),
    ("/usr/share/fonts/truetype/noto/NotoSansBengali-Regular.ttf", "/usr/share/fonts/truetype/noto/NotoSansBengali-Bold.ttf"),
    ("/usr/share/fonts/truetype/lohit-bengali/Lohit-Bengali.ttf", None),
]
_FONT_CACHE = {}


def _font_dirs():
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return [os.path.join(here, "static", "fonts")]


def _covers_bengali(path):
    try:
        face = TTFont("probe", path).face
        return all(c in face.charToGlyph for c in _BN_PROBE)
    except Exception:
        return False


def _register_pdf_fonts():
    """Returns (regular, bold) font names that can draw Bengali, or (None, None).

    Order: PDF_FONT_PATH / PDF_FONT_BOLD_PATH from .env, a font file dropped in static/fonts/,
    then the system FreeSerif / Noto Bengali fonts. A font that cannot draw Bengali is never used
    for Bengali text (see build_student_result_pdf)."""
    if not REPORTLAB_OK:
        return None, None
    if "fonts" in _FONT_CACHE:
        return _FONT_CACHE["fonts"]
    pairs = []
    env_r, env_b = os.environ.get("PDF_FONT_PATH", "").strip(), os.environ.get("PDF_FONT_BOLD_PATH", "").strip()
    if env_r:
        pairs.append((env_r, env_b or None))
    for d in _font_dirs():
        if os.path.isdir(d):
            for name in sorted(os.listdir(d)):
                if name.lower().endswith((".ttf", ".otf")) and "bold" not in name.lower():
                    bold = os.path.join(d, name.replace("Regular", "Bold"))
                    pairs.append((os.path.join(d, name), bold if os.path.isfile(bold) and bold != os.path.join(d, name) else None))
    pairs.extend(_FONT_PAIRS)
    result = (None, None)
    for reg, bold in pairs:
        if not (os.path.isfile(reg) and _covers_bengali(reg)):
            continue
        try:
            pdfmetrics.registerFont(TTFont("AppFont", reg))
            name_b = "AppFont"
            if bold and os.path.isfile(bold) and _covers_bengali(bold):
                pdfmetrics.registerFont(TTFont("AppFontBold", bold))
                name_b = "AppFontBold"
            result = ("AppFont", name_b)
            break
        except Exception:
            continue
    _FONT_CACHE["fonts"] = result
    return result


def has_bengali(text):
    return any("\u0980" <= ch <= "\u09ff" for ch in str(text or ""))


# ------------------------------------------------------------------ question images (Cloudinary)
MAX_IMAGE_BYTES = 4 * 1024 * 1024
IMAGE_TIMEOUT = (4, 8)           # connect, read seconds
MAX_IMAGES_PER_PDF = 60
ALLOWED_IMAGE_HOSTS = ("res.cloudinary.com",)


def _safe_image_url(url):
    """Only https://res.cloudinary.com/<configured cloud>/image/upload/... is ever fetched (no SSRF)."""
    from urllib.parse import urlparse
    try:
        u = urlparse(str(url or "").strip())
    except ValueError:
        return None
    if u.scheme != "https" or (u.hostname or "").lower() not in ALLOWED_IMAGE_HOSTS or u.username or u.password:
        return None
    if u.port not in (None, 443) or "/image/upload/" not in u.path:
        return None
    from backend.services import image_store
    cloud = image_store.config().get("cloud")
    if cloud and not u.path.startswith("/%s/" % cloud):
        return None                  # somebody else's Cloudinary account
    # ask Cloudinary for a PDF-friendly JPEG of sane size (smaller download, always a format reportlab reads)
    path = u.path.replace("/image/upload/", "/image/upload/f_jpg,q_80,w_1100,c_limit/", 1) \
        if "f_jpg" not in u.path else u.path
    return "https://res.cloudinary.com" + path


def fetch_question_image(url):
    """Download one image into memory. Returns bytes or None; NEVER raises (a broken image must not break the PDF)."""
    safe = _safe_image_url(url)
    if not safe:
        return None
    try:
        import requests
        r = requests.get(safe, timeout=IMAGE_TIMEOUT, stream=True, allow_redirects=False,
                         headers={"User-Agent": "ArnabMathClass-PDF/1.0"})
        try:
            if r.status_code != 200 or not str(r.headers.get("Content-Type", "")).lower().startswith("image/"):
                return None
            if int(r.headers.get("Content-Length") or 0) > MAX_IMAGE_BYTES:
                return None
            data = b""
            for chunk in r.iter_content(65536):
                data += chunk
                if len(data) > MAX_IMAGE_BYTES:
                    return None
        finally:
            r.close()
        from backend.services import image_store
        return data if image_store.detect_type(data[:16]) else None
    except Exception:
        return None


def prefetch_images(urls, fetch=fetch_question_image):
    """{url: bytes|None} for the distinct image urls (a few downloads at once, bounded)."""
    from concurrent.futures import ThreadPoolExecutor
    uniq = []
    for u in urls:
        if u and u not in uniq:
            uniq.append(u)
    uniq = uniq[:MAX_IMAGES_PER_PDF]
    if not uniq:
        return {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        return dict(zip(uniq, ex.map(lambda u: fetch(u), uniq)))


def _m(v):
    """21.0 -> '21', 2.5 -> '2.5'"""
    try:
        v = round(float(v), 4)
    except (TypeError, ValueError):
        return "0"
    return str(int(v)) if v == int(v) else ("%.4f" % v).rstrip("0").rstrip(".")


def _row_image(a):
    try:
        return a["image_url"] or ""
    except (KeyError, IndexError):
        return ""


def _breakdown_line(answers):
    ok = sum(1 for a in answers if a["is_correct"])
    none = sum(1 for a in answers if not a["selected_option"])
    pos = sum(float(a["marks_awarded"] or 0) for a in answers if float(a["marks_awarded"] or 0) > 0)
    neg = -sum(float(a["marks_awarded"] or 0) for a in answers if float(a["marks_awarded"] or 0) < 0)
    return (f"Correct: {ok}  |  Incorrect: {len(answers) - ok - none}  |  Unanswered: {none}  |  "
            f"Earned: +{_m(pos)}  |  Negative deducted: -{_m(neg)}")


def build_student_result_pdf(test, student, result, answers, image_fetcher=fetch_question_image):
    """Generate PDF bytes of student's answer sheet (Bengali-safe, with question images)."""
    if not REPORTLAB_OK:
        return None
    font, font_bold = _register_pdf_fonts()
    if not font:
        # No Bengali-capable font. Plain English sheets may still use Helvetica, Bengali text may NOT.
        texts = [test["title"], student["name"]] + [
            str(a[k] or "") for a in answers for k in ("question_text", "option_a", "option_b", "option_c", "option_d")]
        if any(has_bengali(t) for t in texts):
            raise PdfFontError(
                "This answer sheet has Bengali text but the server has no Bengali-capable font. Install "
                "fonts-freefont-ttf, or put a Bengali .ttf in static/fonts/ (or set PDF_FONT_PATH in .env), "
                "then reload the web app.")
        font = font_bold = "Helvetica"
    images = prefetch_images([_row_image(a) for a in answers], image_fetcher)

    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    left = 18 * mm
    right = width - 18 * mm
    y = height - 18 * mm
    line_h = 6.5 * mm
    max_w = right - left

    def new_page_if_needed(need=20 * mm):
        nonlocal y
        if y < need:
            c.showPage()
            y = height - 18 * mm
            return True
        return False

    def draw_wrapped(text, x, fontsize=10, bold=False, color=(0, 0, 0)):
        nonlocal y
        f = font_bold if bold else font
        c.setFont(f, fontsize)
        c.setFillColorRGB(*color)
        words = (text or "").split()
        if not words:
            return
        lines = []
        cur = words[0]
        for w in words[1:]:
            trial = cur + " " + w
            if c.stringWidth(trial, f, fontsize) <= max_w:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        for ln in lines:
            new_page_if_needed(12 * mm)
            c.drawString(x, y, ln)
            y -= line_h * 0.85

    def draw_question_image(a, imgs):
        """Insert the question's picture: aspect ratio kept, scaled to the page width, moved to a new page if needed."""
        nonlocal y
        url = _row_image(a)
        if not url:
            return
        data = imgs.get(url)
        if not data:
            draw_wrapped("  [Question image could not be loaded]", left, fontsize=8, color=(0.6, 0.3, 0.0))
            return
        try:
            from reportlab.lib.utils import ImageReader
            ir = ImageReader(io.BytesIO(data))
            iw, ih = ir.getSize()
            if not iw or not ih:
                raise ValueError("empty image")
            scale = min(max_w * 0.8 / iw, 85 * mm / ih, 1.0 if iw < 400 else 10.0)
            w, h = iw * scale, ih * scale
            if y - h < 20 * mm:
                c.showPage()
                y = height - 18 * mm
            c.drawImage(ir, left + 4 * mm, y - h, width=w, height=h, mask="auto")
            y -= h + 3 * mm
        except Exception:
            draw_wrapped("  [Question image could not be drawn]", left, fontsize=8, color=(0.6, 0.3, 0.0))

    c.setFont(font_bold, 14)
    c.setFillColorRGB(0.12, 0.23, 0.54)
    c.drawString(left, y, "Arnab Math Class — Student Answer Sheet")
    y -= line_h * 1.2
    c.setStrokeColorRGB(0.12, 0.23, 0.54)
    c.setLineWidth(1.5)
    c.line(left, y, right, y)
    y -= line_h * 1.1

    pct = percentage(result["score"], result["total_marks"])
    meta = [
        f"Test: {test['title']}",
        f"Student: {student['name']}  |  Class: {student['class_name']}  |  Roll: {student['roll'] or '-'}",
        f"Score: {_m(result['score'])} / {_m(result['total_marks'])}  ({pct}%)",
        _breakdown_line(answers),
        f"Submitted: {str(result['submitted_at'])[:16].replace('T', ' ')}",
    ]
    for m in meta:
        draw_wrapped(m, left, fontsize=10, bold=True)
        y -= 1 * mm

    y -= 3 * mm
    c.setStrokeColorRGB(0.85, 0.85, 0.85)
    c.line(left, y, right, y)
    y -= line_h

    for i, a in enumerate(answers, 1):
        new_page_if_needed(40 * mm)
        got = float(a["marks_awarded"] or 0)
        status = ("Correct +" + _m(got)) if a["is_correct"] else (
            ("Incorrect -" + _m(abs(got))) if a["selected_option"] else "Not answered 0")
        color = (0.02, 0.59, 0.41) if a["is_correct"] else ((0.86, 0.15, 0.15) if a["selected_option"] else (0.45, 0.45, 0.45))
        draw_wrapped(
            f"Q{i}. {a['question_text']}  [{a['marks']} mark(s)]  {status}",
            left, fontsize=10, bold=True, color=color
        )
        y -= 1 * mm
        draw_question_image(a, images)
        for letter, key in [("A", "option_a"), ("B", "option_b"), ("C", "option_c"), ("D", "option_d")]:
            prefix = ""
            col = (0.1, 0.1, 0.1)
            if letter == a["correct_option"]:
                prefix = " [Correct]"
                col = (0.02, 0.5, 0.35)
            if letter == a["selected_option"]:
                prefix += " <- Student chose"
                if not a["is_correct"]:
                    col = (0.75, 0.1, 0.1)
            draw_wrapped(f"  {letter}) {a[key]}{prefix}", left, fontsize=9, color=col)
        if not a["selected_option"]:
            draw_wrapped("  (No answer selected)", left, fontsize=9, color=(0.7, 0.1, 0.1))
        y -= 3 * mm

    y -= 4 * mm
    new_page_if_needed(15 * mm)
    c.setFont(font, 8)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawString(left, y, "Generated by Arnab Math Class")
    c.save()
    buf.seek(0)
    return buf.read()
