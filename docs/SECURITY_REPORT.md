# SECURITY_REPORT

**Fixed**: impersonation isolation + audit + expiry · session regeneration (login, logout, impersonation) · POST+CSRF logout and OTP resend · same-site-only redirects · push subscribe/unsubscribe ownership, endpoint/host/key validation, SSRF guard on send, expired-subscription cleanup · CSV formula injection · PDF upload (signature, size, uuid names, orphan cleanup, shared-file-safe delete) · image delete protection (id **and** URL, exam snapshots included) · cascade data-loss paths removed · server-side exam timer.

**Headers** (every response): `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Cross-Origin-Opener-Policy`, HSTS when HTTPS.
CSP: `default-src 'self'`; scripts/styles `'self' 'unsafe-inline'` + cdnjs (+ Google Fonts CSS/fonts); images `'self' data: blob: res.cloudinary.com`; `connect-src 'self'`; `object-src 'none'`; `base-uri 'self'`; `form-action 'self'`; `frame-ancestors 'self'`. Inventory done on the templates: inline `<script>`/`<style>`/`onclick` are used everywhere, so `'unsafe-inline'` stays; moving them to `/static` + nonces is the next step.

**Push host allow-list** (`backend/services/push_validation.py`): fcm.googleapis.com, android.googleapis.com, push.services.mozilla.com, push.apple.com, notify.windows.com. If a browser vendor's push host is ever rejected, add its suffix there.

**Known remaining risks**: `'unsafe-inline'` in CSP; CSV leading `'` is shown literally by Excel (OWASP-recommended trade-off); no structured logging; live Cloudinary/push paths untested here.
