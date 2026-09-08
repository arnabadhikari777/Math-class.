# Arnab Math Class

Online notes, timed MCQ tests, and teacher admin panel.

## Highlights
- **Top utility bar** with Teacher Login + Notes/Download shortcut
- **Mandatory mobile** on registration (displayed; OTP only via email)
- **Teacher admin**: search students, admit without OTP, verify, block, delete, login-as student, export CSV
- **Contact Sir** form (teacher mobile never shown)
- Contact inbox, activity log, announcements, leaderboards, answer review, dark mode, PWA

## Run
```bash
cd "Math Class"
pip install -r requirements.txt
export SECRET_KEY="random"
export TEACHER_PASSWORD="strong"
python app.py
```
Teacher default: username `Arnab sir`, password `2026` (change via env).
