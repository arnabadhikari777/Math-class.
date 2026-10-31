# -*- coding: utf-8 -*-
"""Cloudinary image storage for the Question Bank (server side only).

Uses Cloudinary's signed REST API through ``requests`` (already in
requirements.txt), so NO new Python package is needed. The API secret is read
from the environment (.env) and is only ever used here, on the server, to sign
requests. It is never sent to the browser or put in a template.

Environment variables (put them in .env):
    CLOUDINARY_CLOUD_NAME
    CLOUDINARY_API_KEY
    CLOUDINARY_API_SECRET
Optional:
    CLOUDINARY_FOLDER          default "arnab-math-class"
    BANK_IMAGE_MAX_MB          default 5   (per image, 1-10)
"""
import hashlib
import os
import re
import time

import requests

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
API_BASE = "https://api.cloudinary.com/v1_1"
TIMEOUT_SECONDS = 30


class CloudinaryError(Exception):
    """Raised with a teacher-friendly message when an upload/delete cannot be done."""


def _cfg():
    return {
        "cloud": os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip(),
        "key": os.environ.get("CLOUDINARY_API_KEY", "").strip(),
        "secret": os.environ.get("CLOUDINARY_API_SECRET", "").strip(),
        "folder": os.environ.get("CLOUDINARY_FOLDER", "arnab-math-class").strip().strip("/"),
    }


def is_configured():
    c = _cfg()
    return bool(c["cloud"] and c["key"] and c["secret"])


def max_bytes():
    try:
        mb = float(os.environ.get("BANK_IMAGE_MAX_MB", "5"))
    except ValueError:
        mb = 5
    return int(min(10, max(1, mb)) * 1024 * 1024)


def _sign(params, secret):
    """Cloudinary signature: sha1 of the sorted 'k=v&k=v' string + api_secret."""
    to_sign = "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] not in (None, ""))
    return hashlib.sha1((to_sign + secret).encode("utf-8")).hexdigest()


def detect_image_type(head):
    """Real file type from the first bytes (the file name/extension is not trusted)."""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def validate_image(filename, data):
    """Return the detected type or raise CloudinaryError. Checks extension, size and content."""
    name = filename or ""
    ext = name.rsplit(".", 1)[1].lower() if "." in name else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise CloudinaryError("'%s': শুধু JPG, PNG, WEBP বা GIF ছবি চলবে।" % name)
    if not data:
        raise CloudinaryError("'%s': ফাইলটি খালি।" % name)
    if len(data) > max_bytes():
        raise CloudinaryError("'%s': ছবির সাইজ সর্বোচ্চ %d MB হতে পারবে।" % (name, max_bytes() // (1024 * 1024)))
    kind = detect_image_type(data[:16])
    if kind is None:
        raise CloudinaryError("'%s': এটি আসল ছবি ফাইল নয়।" % name)
    return kind


def upload_image(filename, data, category=None):
    """Upload bytes to Cloudinary, optionally into a category-specific subfolder.
    Returns dict(url, public_id, width, height, bytes, format)."""
    validate_image(filename, data)
    c = _cfg()
    if not is_configured():
        raise CloudinaryError("Cloudinary সেট করা নেই (.env-এ CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET দিন)।")
    params = {"timestamp": str(int(time.time()))}
    safe_category = re.sub(r"[^A-Za-z0-9_-]+", "-", str(category or "")).strip("-_")[:60]
    folder_parts = [part for part in (c["folder"], safe_category) if part]
    if folder_parts:
        params["folder"] = "/".join(folder_parts)
    payload = dict(params, api_key=c["key"], signature=_sign(params, c["secret"]))
    try:
        resp = requests.post(
            f"{API_BASE}/{c['cloud']}/image/upload", data=payload,
            files={"file": (os.path.basename(filename or "image"), data)}, timeout=TIMEOUT_SECONDS)
    except requests.RequestException:
        raise CloudinaryError("Cloudinary-র সঙ্গে সংযোগ হয়নি। ইন্টারনেট/সার্ভারের আউটবাউন্ড অ্যাক্সেস দেখে আবার চেষ্টা করুন।")
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200 or "secure_url" not in body:
        msg = (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else None
        raise CloudinaryError("Cloudinary আপলোড ব্যর্থ%s" % (": " + msg if msg else "।"))
    return {
        "url": body["secure_url"], "public_id": body["public_id"],
        "width": body.get("width"), "height": body.get("height"),
        "bytes": body.get("bytes"), "format": body.get("format"),
    }


def delete_image(public_id):
    """Delete from Cloudinary. 'not found' counts as success (already gone)."""
    c = _cfg()
    if not is_configured():
        raise CloudinaryError("Cloudinary সেট করা নেই, তাই ছবি মোছা যায়নি।")
    params = {"public_id": public_id, "timestamp": str(int(time.time()))}
    payload = dict(params, api_key=c["key"], signature=_sign(params, c["secret"]))
    try:
        resp = requests.post(f"{API_BASE}/{c['cloud']}/image/destroy", data=payload, timeout=TIMEOUT_SECONDS)
        body = resp.json()
    except (requests.RequestException, ValueError):
        raise CloudinaryError("Cloudinary থেকে ছবি মোছা যায়নি। পরে আবার চেষ্টা করুন।")
    if resp.status_code != 200 or body.get("result") not in ("ok", "not found"):
        raise CloudinaryError("Cloudinary থেকে ছবি মোছা যায়নি।")
    return True


def thumb_url(url, width=320):
    """Cloudinary on-the-fly small version for grids (original stays untouched)."""
    marker = "/image/upload/"
    if url and marker in url and "res.cloudinary.com" in url:
        return url.replace(marker, f"{marker}c_limit,w_{int(width)},q_auto,f_auto/", 1)
    return url
