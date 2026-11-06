# -*- coding: utf-8 -*-
"""Cloudinary image storage - নতুন করে লেখা (server side only).

শুধু ``requests`` লাগে, নতুন কোনো Python package লাগে না। API secret কখনো
browser-এ যায় না; শুধু এই ফাইলে signature বানাতে ব্যবহার হয়।

.env-এ দুইভাবে দেওয়া যায় (যেকোনো একটি):

  (ক) আলাদা আলাদা:
      CLOUDINARY_CLOUD_NAME=k1hfcl8v          <- Dashboard-এর "Cloud name" (key-র নাম নয়!)
      CLOUDINARY_API_KEY=123456789012345
      CLOUDINARY_API_SECRET=xxxxxxxxxxxx

  (খ) একটি লাইনে (Cloudinary Dashboard যেভাবে দেয়):
      CLOUDINARY_URL=cloudinary://<api_key>:<api_secret>@<cloud_name>

ঐচ্ছিক:
      CLOUDINARY_FOLDER=arnab-math-class      (default)
      BANK_IMAGE_MAX_MB=5                     (প্রতিটি ছবি, ১-১০)
"""
import hashlib
import os
import re
import time
from urllib.parse import unquote, urlparse

import requests

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
API_BASE = "https://api.cloudinary.com/v1_1"
TIMEOUT_SECONDS = 30
_CLOUD_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ImageStoreError(Exception):
    """শিক্ষকের জন্য বাংলা বার্তাসহ error।"""


# ------------------------------------------------------------------ config
def config():
    cloud = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip().strip("'\"")
    key = os.environ.get("CLOUDINARY_API_KEY", "").strip().strip("'\"")
    secret = os.environ.get("CLOUDINARY_API_SECRET", "").strip().strip("'\"")
    url = os.environ.get("CLOUDINARY_URL", "").strip().strip("'\"")
    if url and not (cloud and key and secret):
        try:
            p = urlparse(url)
            if p.scheme == "cloudinary":
                cloud = cloud or (p.hostname or "")
                key = key or unquote(p.username or "")
                secret = secret or unquote(p.password or "")
        except ValueError:
            pass
    folder = os.environ.get("CLOUDINARY_FOLDER", "arnab-math-class").strip().strip("/")
    return {"cloud": cloud, "key": key, "secret": secret, "folder": folder}


def config_problem():
    """None যদি সব ঠিক থাকে, নইলে বাংলায় কী ভুল তার বার্তা (আপলোডের আগেই ধরা পড়ে)।"""
    c = config()
    if not (c["cloud"] and c["key"] and c["secret"]):
        return ("Cloudinary is not configured. Set CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, "
                "CLOUDINARY_API_SECRET in .env, then reload the web app.")
    if not _CLOUD_RE.match(c["cloud"]):
        return ("CLOUDINARY_CLOUD_NAME is invalid ('%s'). Use the 'Cloud name' shown in the Cloudinary dashboard "
                "(it contains no spaces, e.g. k1hfcl8v); it is not the API key name." % c["cloud"])
    if not c["key"].isdigit():
        return "CLOUDINARY_API_KEY must contain digits only. Copy the API key from the dashboard again."
    return None


def is_configured():
    return config_problem() is None


def max_bytes():
    try:
        mb = float(os.environ.get("BANK_IMAGE_MAX_MB", "5"))
    except ValueError:
        mb = 5
    return int(min(10, max(1, mb)) * 1024 * 1024)


# -------------------------------------------------------------- validation
def detect_type(head):
    """ফাইলের প্রথম byte দেখে আসল ধরন (নাম/extension বিশ্বাস করা হয় না)।"""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


def validate(filename, data):
    name = filename or ""
    ext = name.rsplit(".", 1)[1].lower() if "." in name else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise ImageStoreError("'%s': only JPG, PNG, WEBP or GIF images are allowed." % name)
    if not data:
        raise ImageStoreError("'%s': the file is empty." % name)
    if len(data) > max_bytes():
        raise ImageStoreError("'%s': image size must not exceed %d MB." % (name, max_bytes() // (1024 * 1024)))
    kind = detect_type(data[:16])
    if kind is None:
        raise ImageStoreError("'%s': this is not a valid image file." % name)
    return kind


# ---------------------------------------------------------------- requests
def _sign(params, secret):
    to_sign = "&".join("%s=%s" % (k, params[k]) for k in sorted(params) if params[k] not in (None, ""))
    return hashlib.sha1((to_sign + secret).encode("utf-8")).hexdigest()


def _friendly(msg):
    """Cloudinary-র ইংরেজি error-কে বোঝার মতো বাংলায় আনে।"""
    m = (msg or "").lower()
    if "cloud_name" in m:
        return "Invalid cloud name. Set CLOUDINARY_CLOUD_NAME in .env to the 'Cloud name' from the dashboard."
    if "signature" in m:
        return "Invalid API Secret. Copy CLOUDINARY_API_SECRET into .env again."
    if "api_key" in m or "api key" in m:
        return "Invalid or disabled API key. Check CLOUDINARY_API_KEY in .env."
    if "disabled" in m or "blocked" in m:
        return "The Cloudinary account or key is disabled."
    return msg or ""


def _post(path, data, files=None):
    c = config()
    url = "%s/%s/%s" % (API_BASE, c["cloud"], path)
    last = None
    for _ in range(2):  # একবার retry (নেটওয়ার্ক সাময়িক সমস্যা হলে)
        try:
            resp = requests.post(url, data=data, files=files, timeout=TIMEOUT_SECONDS)
            break
        except (requests.ConnectionError, requests.Timeout) as exc:
            last = exc
    else:
        raise ImageStoreError("Could not connect to Cloudinary. Check the internet connection or the server's outbound access and try again.")
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body


def folder_for(category):
    c = config()
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", str(category or "")).strip("-_")[:60]
    return "/".join(p for p in (c["folder"], safe) if p)


def upload(filename, data, category=None):
    """ছবি Cloudinary-তে তোলে। Return: dict(url, public_id, width, height, bytes, format)।"""
    validate(filename, data)
    problem = config_problem()
    if problem:
        raise ImageStoreError(problem)
    c = config()
    params = {"timestamp": str(int(time.time()))}
    folder = folder_for(category)
    if folder:
        params["folder"] = folder
    payload = dict(params, api_key=c["key"], signature=_sign(params, c["secret"]))
    status, body = _post("image/upload", payload, files={"file": (os.path.basename(filename or "image"), data)})
    if status != 200 or "secure_url" not in body:
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        msg = _friendly(err.get("message"))
        raise ImageStoreError("Cloudinary upload failed%s" % (": " + msg if msg else "."))
    return {"url": body["secure_url"], "public_id": body["public_id"], "width": body.get("width"),
            "height": body.get("height"), "bytes": body.get("bytes"), "format": body.get("format")}


def destroy(public_id):
    """Cloudinary থেকে মোছে। 'not found' মানেও সফল (আগেই নেই)।"""
    problem = config_problem()
    if problem:
        raise ImageStoreError("Cloudinary is not configured correctly, so the image could not be deleted.")
    c = config()
    params = {"public_id": public_id, "timestamp": str(int(time.time()))}
    payload = dict(params, api_key=c["key"], signature=_sign(params, c["secret"]))
    status, body = _post("image/destroy", payload)
    if status != 200 or body.get("result") not in ("ok", "not found"):
        raise ImageStoreError("The image could not be deleted from Cloudinary. Please try again later.")
    return True


def thumb(url, width=320):
    """Grid-এর জন্য ছোট সংস্করণ (মূল ছবি অক্ষত)।"""
    marker = "/image/upload/"
    if url and marker in url and "res.cloudinary.com" in url:
        return url.replace(marker, "%sc_limit,w_%d,q_auto,f_auto/" % (marker, int(width)), 1)
    return url
