# -*- coding: utf-8 -*-
"""Error pages: 404, 500, 413."""

from flask import flash, redirect, render_template, request, url_for

from backend.core import app


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Page not found."), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500, message="Something went wrong on our side."), 500


@app.errorhandler(413)
def too_large(e):
    flash("File is too large. Maximum size is 15 MB.", "error")
    return redirect(request.referrer or url_for("upload_note"))
