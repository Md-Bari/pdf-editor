from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import pymupdf as fitz

MAX_FILE_SIZE = 25 * 1024 * 1024
MAX_PAGES = 200


class PdfInputError(ValueError):
    """The uploaded PDF or requested edit cannot be processed safely."""


def _new_id() -> str:
    return uuid.uuid4().hex


def _safe_id(file_id: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", file_id):
        raise FileNotFoundError("PDF not found")
    return file_id


def _write_metadata(folder: Path, metadata: dict[str, Any]) -> None:
    (folder / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
    )


def _read_metadata(folder: Path) -> dict[str, Any]:
    try:
        return json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FileNotFoundError("PDF not found") from exc


def store_uploaded_pdf(storage_dir: Path, filename: str, content: bytes) -> dict[str, Any]:
    if not content:
        raise PdfInputError("The uploaded file is empty.")
    if len(content) > MAX_FILE_SIZE:
        raise PdfInputError("PDF files must be 25 MB or smaller.")
    if not content.startswith(b"%PDF-"):
        raise PdfInputError("The uploaded file is not a valid PDF.")

    try:
        with fitz.open(stream=content, filetype="pdf") as document:
            if document.needs_pass:
                raise PdfInputError("Password-protected PDFs are not supported.")
            page_count = document.page_count
            if page_count == 0:
                raise PdfInputError("The PDF has no pages.")
            if page_count > MAX_PAGES:
                raise PdfInputError(f"PDF files may contain at most {MAX_PAGES} pages.")
    except fitz.FileDataError as exc:
        raise PdfInputError("The uploaded file is not a readable PDF.") from exc

    file_id = _new_id()
    folder = storage_dir / file_id
    folder.mkdir(parents=True, exist_ok=False)
    original_name = Path(filename or "document.pdf").name
    if not original_name.lower().endswith(".pdf"):
        original_name += ".pdf"
    (folder / "source.pdf").write_bytes(content)
    _write_metadata(
        folder,
        {
            "id": file_id,
            "kind": "source",
            "filename": original_name,
            "page_count": page_count,
        },
    )
    return {
        "file_id": file_id,
        "filename": original_name,
        "page_count": page_count,
    }


def get_pdf_metadata(storage_dir: Path, file_id: str) -> dict[str, Any]:
    return _read_metadata(storage_dir / _safe_id(file_id))


def get_pdf_text(storage_dir: Path, file_id: str, page_number: int | None) -> list[dict[str, Any]]:
    folder = storage_dir / _safe_id(file_id)
    metadata = _read_metadata(folder)
    pdf_path = folder / ("source.pdf" if metadata["kind"] == "source" else "edited.pdf")
    with fitz.open(pdf_path) as document:
        if page_number is not None and not 1 <= page_number <= document.page_count:
            raise PdfInputError(f"Page number must be between 1 and {document.page_count}.")
        pages = range(document.page_count) if page_number is None else [page_number - 1]
        page_data = []
        for index in pages:
            page = document[index]
            spans = []
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if not span["text"].strip():
                            continue
                        bbox = fitz.Rect(span["bbox"]) * page.rotation_matrix
                        color_value = span.get("color", 0)
                        spans.append(
                            {
                                "text": span["text"],
                                "bbox": [bbox.x0, bbox.y0, bbox.x1, bbox.y1],
                                "edit_bbox": list(span["bbox"]),
                                "font": span.get("font", ""),
                                "font_size": float(span["size"]),
                                "color": [
                                    ((color_value >> 16) & 255) / 255,
                                    ((color_value >> 8) & 255) / 255,
                                    (color_value & 255) / 255,
                                ],
                            }
                        )
            page_data.append(
                {
                    "page_number": index + 1,
                    "text": page.get_text(),
                    "width": page.rect.width,
                    "height": page.rect.height,
                    "spans": spans,
                }
            )
        return page_data


def get_pdf_page_preview(storage_dir: Path, file_id: str, page_number: int) -> bytes:
    folder = storage_dir / _safe_id(file_id)
    metadata = _read_metadata(folder)
    pdf_path = folder / ("source.pdf" if metadata["kind"] == "source" else "edited.pdf")
    with fitz.open(pdf_path) as document:
        if not 1 <= page_number <= document.page_count:
            raise PdfInputError(f"Page number must be between 1 and {document.page_count}.")
        pixmap = document[page_number - 1].get_pixmap(
            matrix=fitz.Matrix(2, 2), alpha=False
        )
        return pixmap.tobytes("png")


def _font_for_span(span: dict[str, Any]) -> str:
    font_name = span.get("font", "").lower()
    bold = "bold" in font_name or "black" in font_name or "demi" in font_name
    italic = "italic" in font_name or "oblique" in font_name
    if "courier" in font_name or "mono" in font_name:
        family = "co"
    elif "times" in font_name or "serif" in font_name:
        family = "ti"
    else:
        family = "he"
    return {
        ("he", False, False): "helv",
        ("he", True, False): "hebo",
        ("he", False, True): "heit",
        ("he", True, True): "hebi",
        ("ti", False, False): "tiro",
        ("ti", True, False): "tibo",
        ("ti", False, True): "tiit",
        ("ti", True, True): "tibi",
        ("co", False, False): "cour",
        ("co", True, False): "cobo",
        ("co", False, True): "coit",
        ("co", True, True): "cobi",
    }[(family, bold, italic)]


def _style_for_rect(
    page: fitz.Page, rect: fitz.Rect
) -> tuple[str, float, tuple[float, float, float], float]:
    blocks = page.get_text("dict")["blocks"]
    best_span: dict[str, Any] | None = None
    best_overlap = 0.0
    for block in blocks:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                span_rect = fitz.Rect(span["bbox"])
                overlap = span_rect & rect
                area = overlap.get_area()
                if area > best_overlap:
                    best_overlap = area
                    best_span = span

    if best_span is None:
        raise PdfInputError("Could not determine the style of the selected text.")
    color_value = best_span.get("color", 0)
    color = (
        ((color_value >> 16) & 255) / 255,
        ((color_value >> 8) & 255) / 255,
        (color_value & 255) / 255,
    )
    return (
        _font_for_span(best_span),
        float(best_span["size"]),
        color,
        float(best_span["origin"][1]),
    )


def _validate_glyphs(text: str, font_name: str) -> None:
    font = fitz.Font(fontname=font_name)
    if any(not font.has_glyph(ord(char)) for char in text):
        raise PdfInputError(
            "Replacement includes characters that the matching standard PDF font cannot display."
        )


def replace_pdf_text(
    storage_dir: Path,
    file_id: str,
    find_text: str,
    replacement_text: str,
    page_number: int | None = None,
) -> dict[str, Any]:
    if not find_text.strip():
        raise PdfInputError("Text to find cannot be empty.")
    if "\n" in find_text or "\r" in find_text:
        raise PdfInputError("Text to find must be on one line.")
    if "\n" in replacement_text or "\r" in replacement_text:
        raise PdfInputError("Replacement text must be on one line.")

    source_folder = storage_dir / _safe_id(file_id)
    source_metadata = _read_metadata(source_folder)
    source_path = source_folder / (
        "source.pdf" if source_metadata["kind"] == "source" else "edited.pdf"
    )

    output_id = _new_id()
    output_folder = storage_dir / output_id
    output_folder.mkdir(parents=True, exist_ok=False)
    output_path = output_folder / "edited.pdf"

    try:
        with fitz.open(source_path) as document:
            if page_number is not None and not 1 <= page_number <= document.page_count:
                raise PdfInputError(
                    f"Page number must be between 1 and {document.page_count}."
                )

            selected_pages = (
                range(document.page_count)
                if page_number is None
                else [page_number - 1]
            )
            matches: list[
                tuple[int, fitz.Rect, str, float, tuple[float, float, float], float]
            ] = []
            for index in selected_pages:
                page = document[index]
                for rect in page.search_for(find_text):
                    font_name, font_size, color, baseline = _style_for_rect(page, rect)
                    _validate_glyphs(replacement_text, font_name)
                    matches.append((index, rect, font_name, font_size, color, baseline))

            if not matches:
                raise PdfInputError("The selected text was not found in the PDF.")

            for index, rect, _, _, _, _ in matches:
                document[index].add_redact_annot(rect, fill=(1, 1, 1))
            for index in selected_pages:
                document[index].apply_redactions()

            for index, rect, font_name, font_size, color, baseline in matches:
                document[index].insert_text(
                    (rect.x0, baseline),
                    replacement_text,
                    fontname=font_name,
                    fontsize=font_size,
                    color=color,
                )

            document.save(output_path, garbage=4, deflate=True)

        _write_metadata(
            output_folder,
            {
                "id": output_id,
                "kind": "edited",
                "filename": source_metadata["filename"],
                "page_count": source_metadata["page_count"],
                "replaced_count": len(matches),
                "source_id": file_id,
            },
        )
    except Exception:
        if output_path.exists():
            output_path.unlink()
        metadata_path = output_folder / "metadata.json"
        if metadata_path.exists():
            metadata_path.unlink()
        output_folder.rmdir()
        raise

    return {
        "file_id": output_id,
        "filename": source_metadata["filename"],
        "replaced_count": len(matches),
        "download_url": f"/api/files/{output_id}/download",
    }


def replace_pdf_text_at(
    storage_dir: Path,
    file_id: str,
    page_number: int,
    bbox: tuple[float, float, float, float],
    replacement_text: str,
) -> dict[str, Any]:
    if "\n" in replacement_text or "\r" in replacement_text:
        raise PdfInputError("Replacement text must be on one line.")

    source_folder = storage_dir / _safe_id(file_id)
    source_metadata = _read_metadata(source_folder)
    source_path = source_folder / (
        "source.pdf" if source_metadata["kind"] == "source" else "edited.pdf"
    )
    output_id = _new_id()
    output_folder = storage_dir / output_id
    output_folder.mkdir(parents=True, exist_ok=False)
    output_path = output_folder / "edited.pdf"

    try:
        with fitz.open(source_path) as document:
            if not 1 <= page_number <= document.page_count:
                raise PdfInputError(
                    f"Page number must be between 1 and {document.page_count}."
                )
            page = document[page_number - 1]
            selected_rect = fitz.Rect(bbox) * page.derotation_matrix
            if selected_rect.is_empty or selected_rect.is_infinite:
                raise PdfInputError("Select a text item on the PDF page.")
            font_name, font_size, color, baseline = _style_for_rect(
                page, selected_rect
            )
            _validate_glyphs(replacement_text, font_name)

            page.add_redact_annot(selected_rect, fill=(1, 1, 1))
            page.apply_redactions()
            if replacement_text:
                page.insert_text(
                    (selected_rect.x0, baseline),
                    replacement_text,
                    fontname=font_name,
                    fontsize=font_size,
                    color=color,
                )
            document.save(output_path, garbage=4, deflate=True)

        _write_metadata(
            output_folder,
            {
                "id": output_id,
                "kind": "edited",
                "filename": source_metadata["filename"],
                "page_count": source_metadata["page_count"],
                "replaced_count": 1,
                "source_id": file_id,
            },
        )
    except Exception:
        if output_path.exists():
            output_path.unlink()
        metadata_path = output_folder / "metadata.json"
        if metadata_path.exists():
            metadata_path.unlink()
        output_folder.rmdir()
        raise

    return {
        "file_id": output_id,
        "filename": source_metadata["filename"],
        "replaced_count": 1,
        "download_url": f"/api/files/{output_id}/download",
    }


def get_download_path(storage_dir: Path, file_id: str) -> tuple[Path, str]:
    folder = storage_dir / _safe_id(file_id)
    metadata = _read_metadata(folder)
    if metadata.get("kind") != "edited":
        raise FileNotFoundError("Edited PDF not found")
    path = folder / "edited.pdf"
    if not path.is_file():
        raise FileNotFoundError("Edited PDF not found")
    return path, metadata["filename"]
