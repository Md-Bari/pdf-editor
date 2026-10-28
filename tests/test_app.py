import pymupdf as fitz
import pytest
from fastapi.testclient import TestClient

from main import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "pdf-storage"))


def make_pdf(*page_texts: str) -> bytes:
    document = fitz.open()
    for text in page_texts:
        page = document.new_page()
        page.insert_text((72, 72), text, fontsize=12)
    content = document.tobytes()
    document.close()
    return content


def test_upload_replace_and_download_keeps_other_text(client):
    response = client.post(
        "/api/files",
        files={"file": ("source.pdf", make_pdf("Hello world"), "application/pdf")},
    )
    assert response.status_code == 201
    file_id = response.json()["file_id"]

    edit = client.post(
        f"/api/files/{file_id}/replace",
        data={"find": "world", "replacement": "there"},
    )
    assert edit.status_code == 200
    result = edit.json()
    assert result["replaced_count"] == 1

    download = client.get(result["download_url"])
    assert download.status_code == 200
    with fitz.open(stream=download.content, filetype="pdf") as document:
        page_text = document[0].get_text()
    assert "Hello" in page_text
    assert "there" in page_text
    assert "world" not in page_text


def test_original_is_not_overwritten(client):
    response = client.post(
        "/api/files",
        files={"file": ("source.pdf", make_pdf("Original text"), "application/pdf")},
    )
    file_id = response.json()["file_id"]
    client.post(
        f"/api/files/{file_id}/replace",
        data={"find": "Original", "replacement": "Edited"},
    )

    original = client.get(f"/api/files/{file_id}/text")
    assert original.status_code == 200
    assert "Original text" in original.json()["pages"][0]["text"]


def test_long_replacement_preserves_original_font_size_and_face(client):
    response = client.post(
        "/api/files",
        files={"file": ("source.pdf", make_pdf("Short"), "application/pdf")},
    )
    result = client.post(
        f"/api/files/{response.json()['file_id']}/replace",
        data={"find": "Short", "replacement": "A much much much longer replacement"},
    )
    assert result.status_code == 200

    downloaded = client.get(result.json()["download_url"])
    with fitz.open(stream=downloaded.content, filetype="pdf") as document:
        page = document[0]
        spans = [
            span
            for block in page.get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line.get("spans", [])
        ]
    replacement_span = next(
        span for span in spans if span["text"] == "A much much much longer replacement"
    )
    assert replacement_span["size"] == 12
    assert replacement_span["font"] == "Helvetica"


def test_very_long_replacement_is_not_limited_and_preserves_font_size(client):
    response = client.post(
        "/api/files",
        files={"file": ("source.pdf", make_pdf("Short"), "application/pdf")},
    )
    replacement = "replacement " * 100
    result = client.post(
        f"/api/files/{response.json()['file_id']}/replace",
        data={"find": "Short", "replacement": replacement},
    )
    assert result.status_code == 200

    downloaded = client.get(result.json()["download_url"])
    with fitz.open(stream=downloaded.content, filetype="pdf") as document:
        spans = [
            span
            for block in document[0].get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line.get("spans", [])
        ]
    replacement_span = next(span for span in spans if "replacement" in span["text"])
    assert replacement_span["size"] == 12


def test_rejects_non_pdf_upload(client):
    response = client.post(
        "/api/files",
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
    )
    assert response.status_code == 400


def test_page_argument_limits_replacement_to_that_page(client):
    response = client.post(
        "/api/files",
        files={
            "file": (
                "source.pdf",
                make_pdf("Name on page one", "Name on page two"),
                "application/pdf",
            )
        },
    )
    edited = client.post(
        f"/api/files/{response.json()['file_id']}/replace",
        data={"find": "Name", "replacement": "Word", "page": "2"},
    )
    assert edited.status_code == 200

    downloaded = client.get(edited.json()["download_url"])
    with fitz.open(stream=downloaded.content, filetype="pdf") as document:
        first_page = document[0].get_text()
        second_page = document[1].get_text()
    assert "Name" in first_page
    assert "Word" in second_page
    assert "Name" not in second_page


def test_home_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Edit right on the page" in response.text


def test_page_preview_returns_an_image(client):
    response = client.post(
        "/api/files",
        files={"file": ("source.pdf", make_pdf("Preview this text"), "application/pdf")},
    )
    file_id = response.json()["file_id"]

    preview = client.get(f"/api/files/{file_id}/pages/1/preview")
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/png"
    assert preview.content.startswith(b"\x89PNG")


def test_inline_edit_targets_only_the_selected_text_span(client):
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Hello ", fontsize=12)
    page.insert_text((103, 72), "world", fontsize=12, color=(1, 0, 0))
    content = document.tobytes()
    document.close()
    response = client.post(
        "/api/files",
        files={"file": ("source.pdf", content, "application/pdf")},
    )
    file_id = response.json()["file_id"]
    text = client.get(f"/api/files/{file_id}/text?page=1").json()["pages"][0]
    selected_span = next(span for span in text["spans"] if span["text"] == "world")

    edit = client.post(
        f"/api/files/{file_id}/replace-at",
        json={"page": 1, "bbox": selected_span["bbox"], "replacement": "there"},
    )
    assert edit.status_code == 200

    downloaded = client.get(edit.json()["download_url"])
    with fitz.open(stream=downloaded.content, filetype="pdf") as document:
        page_text = document[0].get_text()
    assert "Hello" in page_text
    assert "there" in page_text
    assert "world" not in page_text


def test_inline_edit_preserves_font_size_style_and_color(client):
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Hello ", fontsize=12)
    page.insert_text(
        (103, 72), "world", fontname="tibi", fontsize=12, color=(1, 0, 0)
    )
    content = document.tobytes()
    document.close()
    response = client.post(
        "/api/files",
        files={"file": ("source.pdf", content, "application/pdf")},
    )
    file_id = response.json()["file_id"]
    text = client.get(f"/api/files/{file_id}/text?page=1").json()["pages"][0]
    selected_span = next(span for span in text["spans"] if span["text"] == "world")
    edit = client.post(
        f"/api/files/{file_id}/replace-at",
        json={
            "page": 1,
            "bbox": selected_span["bbox"],
            "replacement": "worlds",
        },
    )
    assert edit.status_code == 200

    downloaded = client.get(edit.json()["download_url"])
    with fitz.open(stream=downloaded.content, filetype="pdf") as document:
        edited_span = next(
            span
            for block in document[0].get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line.get("spans", [])
            if "worlds" in span["text"]
        )
    assert edited_span["size"] == 12
    assert edited_span["font"] == selected_span["font"]
    expected_color = (
        int(selected_span["color"][0] * 255) << 16
        | int(selected_span["color"][1] * 255) << 8
        | int(selected_span["color"][2] * 255)
    )
    assert edited_span["color"] == expected_color
