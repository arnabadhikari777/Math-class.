# -*- coding: utf-8 -*-
"""Student result PDF generation (reportlab) with Cloudinary question images."""

import io
import logging
import os
from urllib.parse import urlparse

import requests

from backend.utils.helpers import percentage

log = logging.getLogger(__name__)

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdf_canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.utils import ImageReader
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False

_ALLOWED_IMAGE_HOSTS = (
    "res.cloudinary.com",
    "cloudinary.com",
)
_MAX_IMAGE_BYTES = 4 * 1024 * 1024
_IMAGE_TIMEOUT = 8


def _register_pdf_fonts():
    if not REPORTLAB_OK:
        return None, None
    font_paths = [
        "/usr/share/fonts/truetype/freefont/FreeSerif.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    ]
    regular = None
    bold = None
    for p in font_paths:
        if not os.path.isfile(p):
            continue
        base = os.path.basename(p).lower()
        try:
            if "bold" in base and bold is None:
                pdfmetrics.registerFont(TTFont("AppFontBold", p))
                bold = "AppFontBold"
            elif regular is None:
                pdfmetrics.registerFont(TTFont("AppFont", p))
                regular = "AppFont"
        except Exception as exc:
            log.warning("PDF font register failed for %s: %s", p, exc)
    if regular and not bold:
        bold = regular
    if not regular:
        log.warning(
            "No Unicode PDF font found. Bengali text may not render. Falling back to Helvetica."
        )
    return regular, bold


def _m(v):
    try:
        v = round(float(v), 4)
    except (TypeError, ValueError):
        return "0"
    return str(int(v)) if v == int(v) else ("%.4f" % v).rstrip("0").rstrip(".")


def _breakdown_line(answers):
    ok = sum(1 for a in answers if a["is_correct"])
    none = sum(1 for a in answers if not a["selected_option"])
    pos = sum(float(a["marks_awarded"] or 0) for a in answers if float(a["marks_awarded"] or 0) > 0)
    neg = -sum(float(a["marks_awarded"] or 0) for a in answers if float(a["marks_awarded"] or 0) < 0)
    return (f"Correct: {ok}  |  Incorrect: {len(answers) - ok - none}  |  Unanswered: {none}  |  "
            f"Earned: +{_m(pos)}  |  Negative deducted: -{_m(neg)}")


def _is_allowed_image_url(url):
    if not url or not isinstance(url, str):
        return False
    try:
        parsed = urlparse(url.strip())
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    for allowed in _ALLOWED_IMAGE_HOSTS:
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def _fetch_image_bytes(url):
    if not _is_allowed_image_url(url):
        log.warning("PDF image rejected (host not allowed): %s", (url or "")[:120])
        return None
    try:
        resp = requests.get(
            url, timeout=_IMAGE_TIMEOUT, stream=True,
            headers={"User-Agent": "ArnabMathClass-PDF/1.0"},
        )
        resp.raise_for_status()
        data = b""
        for chunk in resp.iter_content(chunk_size=65536):
            data += chunk
            if len(data) > _MAX_IMAGE_BYTES:
                log.warning("PDF image too large, skipping")
                return None
        return data or None
    except Exception as exc:
        log.warning("PDF image download failed: %s", exc)
        return None


def _draw_question_image(c, url, left, right, y, max_height_mm=55):
    if not REPORTLAB_OK or not url:
        return y
    raw = _fetch_image_bytes(url)
    if not raw:
        return y
    try:
        img = ImageReader(io.BytesIO(raw))
        iw, ih = img.getSize()
        if not iw or not ih:
            return y
        max_w = right - left
        max_h = max_height_mm * mm
        scale = min(max_w / float(iw), max_h / float(ih), 1.0)
        dw = iw * scale
        dh = ih * scale
        if y - dh < 20 * mm:
            c.showPage()
            y = A4[1] - 18 * mm
        c.drawImage(img, left, y - dh, width=dw, height=dh, preserveAspectRatio=True, mask="auto")
        return y - dh - 3 * mm
    except Exception as exc:
        log.warning("PDF drawImage failed: %s", exc)
        return y


def build_student_result_pdf(test, student, result, answers):
    """Generate PDF bytes of student's answer sheet (Bengali-safe + question images)."""
    if not REPORTLAB_OK:
        return None
    font, font_bold = _register_pdf_fonts()
    if not font:
        font = font_bold = "Helvetica"

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
        new_page_if_needed(50 * mm)
        got = float(a["marks_awarded"] or 0)
        status = ("Correct +" + _m(got)) if a["is_correct"] else (
            ("Incorrect -" + _m(abs(got))) if a["selected_option"] else "Not answered 0")
        color = (0.02, 0.59, 0.41) if a["is_correct"] else ((0.86, 0.15, 0.15) if a["selected_option"] else (0.45, 0.45, 0.45))
        qtext = a["question_text"] if "question_text" in a.keys() else a.get("question_text", "")
        draw_wrapped(
            f"Q{i}. {qtext}  [{a['marks']} mark(s)]  {status}",
            left, fontsize=10, bold=True, color=color
        )
        y -= 1 * mm

        img_url = None
        try:
            img_url = a["image_url"]
        except (KeyError, TypeError, IndexError):
            img_url = a.get("image_url") if hasattr(a, "get") else None
        if img_url:
            y = _draw_question_image(c, img_url, left, right, y)

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
