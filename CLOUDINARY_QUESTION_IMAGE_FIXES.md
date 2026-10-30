# Cloudinary Image Library + Test Builder Fixes

## What changed
- Image uploads are stored in Cloudinary folders by category:
  `CLOUDINARY_FOLDER/<Category>` (default root: `arnab-math-class`).
- The upload request limit is now 55 MB so the advertised batch of up to 10 images
  (each limited by `BANK_IMAGE_MAX_MB`, default 5 MB) is not rejected by Flask's
  old 15 MB total request limit.
- PDF notes still have an explicit 15 MB per-file limit.
- The test question builder now lets the teacher select one existing Image Library
  image for each question while entering the question text and four options.
- The selected image is saved to `questions.image_url` and `questions.bank_image_id`.
  Existing student test pages and result pages continue using the same image URL.
- Existing Question Bank import remains available and unchanged.
- Image picker limit raised from 500 to 2,000 entries.

## Important deployment notes
1. Keep the existing production `database.db`; do not replace it with a database
   from this ZIP.
2. Keep Cloudinary credentials in the server environment / `.env` only:
   `CLOUDINARY_CLOUD_NAME`, `CLOUDINARY_API_KEY`, `CLOUDINARY_API_SECRET`.
3. `CLOUDINARY_FOLDER` and `BANK_IMAGE_MAX_MB` are optional.
4. After deploying the code, reload the PythonAnywhere web app.
5. Create a category, upload images, then open a test's Add Questions page.
   Each question now has an optional image selector. Choose an image and save.
6. Cloudinary upload itself still requires valid credentials and outbound HTTPS
   access from the hosting environment; it cannot be live-verified offline.

## Safety
- No existing student, test, question, result, or other database rows are deleted
  or reset by these code changes.
- The existing additive migration `007_question_bank` remains the mechanism that
  adds the nullable image columns on older databases.
