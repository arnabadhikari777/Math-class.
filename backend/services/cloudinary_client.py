# -*- coding: utf-8 -*-
"""Compatibility wrapper. The ONE Cloudinary implementation lives in backend/services/image_store.py
(used through backend/services/image_service.py). This module only keeps the old names importable
so existing code (Question Bank pages) keeps working with identical behaviour and error messages."""
import requests  # noqa: F401  (kept importable: tests patch cloudinary_client.requests.post)

from backend.services import image_store as _s

CloudinaryError = _s.ImageStoreError
ALLOWED_EXTENSIONS = _s.ALLOWED_EXTENSIONS
is_configured = _s.is_configured
max_bytes = _s.max_bytes
detect_image_type = _s.detect_type
validate_image = _s.validate
upload_image = _s.upload
delete_image = _s.destroy
thumb_url = _s.thumb
_sign = _s._sign
