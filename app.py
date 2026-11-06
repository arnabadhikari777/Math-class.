# -*- coding: utf-8 -*-
"""Arnab Math Class - entry point.

* Local:          python app.py
* PythonAnywhere: the WSGI file does  ``from app import app as application``
* Gunicorn:       gunicorn app:app

All real code lives in the backend/ package (see docs/ARCHITECTURE.md).
"""
from backend.bootstrap import app
from backend.config import DEBUG_MODE

if __name__ == "__main__":
    app.run(debug=DEBUG_MODE, host="0.0.0.0", port=5000)
