# -*- coding: utf-8 -*-
"""HTTPS redirect, CSRF protection and security headers."""

import hmac
import secrets

from flask import flash, jsonify, redirect, request, session, url_for

from backend.config import DEBUG_MODE, FORCE_HTTPS
from backend.core import app


def get_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = get_csrf_token


def _is_local_host():
    host = (request.host or "").split(":")[0].lower()
    return host in ("localhost", "127.0.0.1", "[::1]", "::1")


@app.before_request
def enforce_https():
    if FORCE_HTTPS and not DEBUG_MODE and not request.is_secure and not _is_local_host():
        return redirect(request.url.replace("http://", "https://", 1), code=301)


@app.before_request
def csrf_protect():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if request.endpoint == "static":
        return None
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken") or ""
    expected = session.get("_csrf_token") or ""
    if expected and sent and hmac.compare_digest(sent, expected):
        return None
    if request.path.startswith("/api/") or request.is_json:
        return jsonify({"ok": False, "error": "CSRF token missing or invalid."}), 400
    flash("Your session expired or the form was invalid. Please try again.", "error")
    return redirect(request.referrer or url_for("index"))


@app.after_request
def add_security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if FORCE_HTTPS and request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return resp
