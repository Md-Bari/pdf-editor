from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pdf_service import (
    MAX_FILE_SIZE,
    PdfInputError,
    get_download_path,
    get_pdf_page_preview,
    get_pdf_metadata,
    get_pdf_text,
    replace_pdf_text,
    replace_pdf_text_at,
    store_uploaded_pdf,
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


class ReplaceAtRequest(BaseModel):
    page: int = Field(ge=1)
    bbox: tuple[float, float, float, float]
    replacement: str


def create_app(storage_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="PDF Text Editor", version="1.0.0")
    app.state.storage_dir = storage_dir or Path(
        os.environ.get("PDF_STORAGE_DIR", BASE_DIR / "storage")
    )
    app.state.storage_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def home() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.post("/api/files", status_code=201)
    async def upload_pdf(file: UploadFile = File(...)) -> dict:
        content = await file.read(MAX_FILE_SIZE + 1)
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="PDF files must be 25 MB or smaller.")
        try:
            return store_uploaded_pdf(
                app.state.storage_dir, file.filename or "document.pdf", content
            )
        except PdfInputError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            await file.close()

    @app.get("/api/files/{file_id}/text")
    async def read_text(file_id: str, page: int | None = None) -> dict:
        try:
            metadata = get_pdf_metadata(app.state.storage_dir, file_id)
            return {
                "file_id": file_id,
                "filename": metadata["filename"],
                "pages": get_pdf_text(app.state.storage_dir, file_id, page),
            }
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PdfInputError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/files/{file_id}/pages/{page_number}/preview")
    async def preview_page(file_id: str, page_number: int):
        try:
            image = get_pdf_page_preview(
                app.state.storage_dir, file_id, page_number
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PdfInputError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return Response(content=image, media_type="image/png")

    @app.post("/api/files/{file_id}/replace")
    async def replace_text(
        file_id: str,
        find: str = Form(...),
        replacement: str = Form(...),
        page: int | None = Form(default=None),
    ) -> dict:
        try:
            return replace_pdf_text(
                app.state.storage_dir, file_id, find, replacement, page
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PdfInputError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/files/{file_id}/replace-at")
    async def replace_text_at(file_id: str, edit: ReplaceAtRequest) -> dict:
        try:
            return replace_pdf_text_at(
                app.state.storage_dir,
                file_id,
                edit.page,
                edit.bbox,
                edit.replacement,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PdfInputError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/files/{file_id}/download")
    async def download_pdf(file_id: str) -> FileResponse:
        try:
            path, filename = get_download_path(app.state.storage_dir, file_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=f"edited-{filename}",
        )

    return app


app = create_app()
