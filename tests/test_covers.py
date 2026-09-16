import base64
import io
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_STORED, ZipFile

import pytest
from fastapi.testclient import TestClient

from kenkui_server.api.covers import read_cover, replace_cover
from kenkui_server.app import create_app

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)


def epub() -> bytes:
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            "<rootfiles>"
            '<rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles>"
            "</container>",
        )
        archive.writestr(
            "OPS/book.opf",
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:title>Test book</dc:title>"
            "<dc:creator>Author</dc:creator>"
            "</metadata>"
            "<manifest>"
            '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
            "</manifest>"
            "<spine>"
            '<itemref idref="chapter"/>'
            "</spine>"
            "</package>",
        )
        archive.writestr(
            "OPS/chapter.xhtml",
            '<html xmlns="http://www.w3.org/1999/xhtml">'
            "<head>"
            "<title>Chapter one</title>"
            "</head>"
            "<body>"
            "<h1>Chapter one</h1>"
            "<p>A chapter with text to read aloud.</p>"
            "</body>"
            "</html>",
        )
    return output.getvalue()


def test_cover_upload_creates_new_source_without_changing_chapters(tmp_path: Path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        original = client.post(
            "/v1/assets", content=epub(), headers={"Content-Type": "application/epub+zip"}
        ).json()["id"]
        before = client.get(f"/v1/assets/{original}/book").json()
        assert client.get(f"/v1/assets/{original}/cover").status_code == 404
        response = client.post(
            f"/v1/assets/{original}/cover", content=PNG, headers={"Content-Type": "image/png"}
        )
        assert response.status_code == 201, response.text
        new = response.json()["id"]
        assert new != original
        after = client.get(f"/v1/assets/{new}/book").json()
        assert before["chapters"] == after["chapters"]
        assert before["title"] == after["title"]
        assert client.get(f"/v1/assets/{original}/cover").status_code == 404
        image = client.get(f"/v1/assets/{new}/cover")
        assert image.content == PNG
        assert image.headers["content-type"] == "image/png"
        assert image.headers["cache-control"] == "private, no-store"
        assert image.headers["x-content-type-options"] == "nosniff"


def test_replacement_reads_the_new_declared_cover():
    once = replace_cover(epub(), PNG)
    second = PNG + b"new"
    assert read_cover(replace_cover(once, second)) == (second, "image/png")


def test_cover_rejects_non_image_and_oversized_data(tmp_path):
    with TestClient(create_app(data_dir=tmp_path)) as client:
        original = client.post(
            "/v1/assets", content=epub(), headers={"Content-Type": "application/epub+zip"}
        ).json()["id"]
        assert (
            client.post(
                f"/v1/assets/{original}/cover",
                content=b"<svg/>",
                headers={"Content-Type": "image/svg+xml"},
            ).status_code
            == 415
        )
        assert (
            client.post(
                f"/v1/assets/{original}/cover",
                content=b"not png",
                headers={"Content-Type": "image/png"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/v1/assets/{original}/cover",
                content=b"x" * (8 * 1024 * 1024 + 1),
                headers={"Content-Type": "image/png"},
            ).status_code
            == 413
        )


def test_cover_rejects_traversal_and_xml_entities():
    malicious = io.BytesIO()
    with ZipFile(malicious, "w") as archive:
        archive.writestr("../outside", "no")
    with pytest.raises(ValueError):
        replace_cover(malicious.getvalue(), PNG)
    malicious = io.BytesIO()
    with ZipFile(malicious, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<!DOCTYPE root [<!ENTITY x SYSTEM "file:///etc/passwd">]><root>&x;</root>',
        )
    from defusedxml.common import DefusedXmlException

    with pytest.raises(DefusedXmlException):
        replace_cover(malicious.getvalue(), PNG)


def test_cover_ownership_checked_before_read_or_write(tmp_path):
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        original = client.post(
            "/v1/assets", content=epub(), headers={"Content-Type": "application/epub+zip"}
        ).json()["id"]
        app.state.hosted_auth = SimpleNamespace(
            asset_owner_resolver=lambda _: "owner",
            backend=SimpleNamespace(
                authenticate=lambda token: SimpleNamespace(user_id=token),
                authorize=lambda user, owner: user == owner,
            ),
        )
        assert client.get(f"/v1/assets/{original}/cover").status_code == 401
        assert (
            client.get(
                f"/v1/assets/{original}/cover", headers={"Authorization": "Bearer other"}
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/v1/assets/{original}/cover",
                content=PNG,
                headers={"Authorization": "Bearer other", "Content-Type": "image/png"},
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/v1/assets/{original}/cover", headers={"Authorization": "Bearer owner"}
            ).status_code
            == 404
        )
