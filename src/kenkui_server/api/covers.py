"""Bounded EPUB cover reads and immutable replacement of source cover metadata."""

from __future__ import annotations

import io
import posixpath
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED, ZipFile

from defusedxml.ElementTree import fromstring  # type: ignore[import-untyped]

MAX_COVER_BYTES = 8 * 1024 * 1024
OPF = "http://www.idpf.org/2007/opf"


def image_type(payload: bytes) -> str:
    if 32 <= len(payload) <= MAX_COVER_BYTES:
        if payload.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if payload.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
    raise ValueError("Choose a PNG or JPEG cover up to 8 MB.")


def _resolve(base: str, href: str) -> str:
    url = urlsplit(href)
    if url.scheme or url.netloc or url.query or url.fragment:
        raise ValueError("Invalid EPUB resource path.")
    path = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(url.path)))
    if path.startswith("/") or ".." in PurePosixPath(path).parts or "\\" in path:
        raise ValueError("Invalid EPUB resource path.")
    return path


def _package(archive: ZipFile) -> tuple[str, ET.Element]:
    entries = archive.infolist()
    if len(entries) > 10_000 or sum(i.file_size for i in entries) > 128 * 1024 * 1024:
        raise ValueError("EPUB archive is too large.")
    names: set[str] = set()
    for info in entries:
        name = info.filename
        if (
            name in names
            or name.startswith("/")
            or ".." in PurePosixPath(name).parts
            or "\\" in name
            or info.flag_bits & 1
            or info.file_size > 32 * 1024 * 1024
            or info.file_size > max(1, info.compress_size) * 100
        ):
            raise ValueError("Invalid EPUB archive.")
        names.add(name)
    container = fromstring(archive.read("META-INF/container.xml"))
    rootfile = next(e for e in container.iter() if e.tag.rsplit("}", 1)[-1] == "rootfile")
    path = _resolve("", rootfile.attrib["full-path"])
    return path, fromstring(archive.read(path))


def read_cover(source: bytes) -> tuple[bytes, str] | None:
    with ZipFile(io.BytesIO(source)) as archive:
        path, package = _package(archive)
        ids = {e.get("content") for e in package.iter() if e.get("name") == "cover"}
        for item in package.iter():
            if item.tag.rsplit("}", 1)[-1] != "item":
                continue
            if "cover-image" in item.get("properties", "").split() or item.get("id") in ids:
                member = _resolve(path, item.attrib["href"])
                if archive.getinfo(member).file_size > MAX_COVER_BYTES:
                    raise ValueError("Cover is too large.")
                payload = archive.read(member)
                return payload, image_type(payload)
    return None


def replace_cover(source: bytes, payload: bytes) -> bytes:
    media_type = image_type(payload)
    with ZipFile(io.BytesIO(source)) as archive:
        path, package = _package(archive)
        manifest = package.find(f"{{{OPF}}}manifest")
        metadata = package.find(f"{{{OPF}}}metadata")
        if manifest is None or metadata is None:
            raise ValueError("Invalid EPUB package.")
        for item in manifest:
            properties = item.get("properties", "").split()
            if "cover-image" in properties:
                item.set("properties", " ".join(p for p in properties if p != "cover-image"))
        for item in list(metadata):
            if item.get("name") == "cover":
                metadata.remove(item)
        identifier = f"kenkui-cover-{uuid4().hex}"
        filename = identifier + (".png" if media_type == "image/png" else ".jpg")
        ET.SubElement(
            manifest,
            f"{{{OPF}}}item",
            {
                "id": identifier,
                "href": filename,
                "media-type": media_type,
                "properties": "cover-image",
            },
        )
        ET.SubElement(metadata, f"{{{OPF}}}meta", {"name": "cover", "content": identifier})
        output = io.BytesIO()
        with ZipFile(output, "w") as target:
            # EPUB requires an uncompressed first mimetype member.
            target.writestr("mimetype", b"application/epub+zip", compress_type=ZIP_STORED)
            for member in archive.infolist():
                if member.filename == "mimetype":
                    continue
                target.writestr(
                    member,
                    ET.tostring(package, encoding="utf-8", xml_declaration=True)
                    if member.filename == path
                    else archive.read(member.filename),
                )
            target.writestr(_resolve(path, filename), payload)
        return output.getvalue()
