# PDF Text Editor

A small FastAPI application for uploading a PDF, editing text directly in a rendered page preview, and downloading the edited PDF. Click a text span in the page to edit it inline; saving refreshes the preview. Each edit creates a separate stored PDF, and the uploaded original is not overwritten.

Text outside the selected span is retained. The selected text is redacted and reinserted using the same detected font style, font size, and color (mapped to a similar standard PDF font). Replacement text can be any length and does not shrink, so longer text can extend into nearby page content. This is intended for text-based PDFs and does not provide exact font embedding or full desktop-publishing layout control. Scanned pages require OCR before their text can be edited.

## Run locally

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>. Files are stored in `storage/` by default. Set `PDF_STORAGE_DIR` to use a different persistent folder.

## API

- `POST /api/files` — multipart form upload field `file`; returns the file ID and page count.
- `GET /api/files/{file_id}/pages/{page}/preview` — render a page preview as PNG.
- `GET /api/files/{file_id}/text?page=1` — extract page text and editable text-span coordinates; omit `page` to retrieve all pages.
- `POST /api/files/{file_id}/replace-at` — JSON body with `page`, `bbox`, and `replacement`; edits one selected text span.
- `POST /api/files/{file_id}/replace` — multipart fields `find`, `replacement`, and optional one-based `page`; returns the edited file ID and a direct download URL.
- `GET /api/files/{file_id}/download` — download an edited PDF.

Uploads are limited to 25 MB and 200 pages. Password-protected PDFs are not supported. Replacement characters must be supported by a standard PDF font. The original font size and style are preserved for replacements of any length.
