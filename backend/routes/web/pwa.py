# -*- coding: utf-8 -*-
"""PWA files served from the site root (manifest, service worker)."""

from flask import send_from_directory

from backend.config import STATIC_FOLDER
from backend.core import app


@app.route("/manifest.json")
def manifest():
    return send_from_directory(STATIC_FOLDER, "manifest.json")


@app.route("/sw.js")
def service_worker():
    return send_from_directory(STATIC_FOLDER, "sw.js")
