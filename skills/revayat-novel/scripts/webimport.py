"""Ordered saved/web chapters into a preserved source snapshot and Book IR."""

import argparse
from html import escape
from io import BytesIO
import json
from pathlib import Path, PureWindowsPath
import re
from urllib.parse import unquote, urldefrag, urljoin, urlsplit
import uuid
import warnings
from xml.etree import ElementTree as ET
import zipfile

from PIL import Image

import bookir as ir
import bookwrite
from read_epub import read_epub
import reviewstate
import webfetch
from webhtml import chapter_html

SCHEMA = "revayat-novel/web-input@1"
SNAPSHOT = "revayat-novel/web-snapshot@1"
HTML_LIMIT = 8 * 1024 * 1024
IMAGE_LIMIT = 32 * 1024 * 1024
TOTAL_LIMIT = 256 * 1024 * 1024


def read_manifest(path):
    if Path(path).stat().st_size > HTML_LIMIT:
        raise reviewstate.Refused("source-limit", "chapter manifest exceeds its byte limit")
    data = reviewstate.object_file(path)
    reviewstate.require(data.get("schema") == SCHEMA, "unsupported web chapter manifest schema")
    for field in ("title", "source_language"):
        reviewstate.require(isinstance(data.get(field), str) and bool(data[field].strip()), f"missing {field}")
    chapters = data.get("chapters")
    hosts = data.get("allowed_image_hosts", [])
    reviewstate.require(isinstance(hosts, list) and all(isinstance(host, str) and host and not any(char in host for char in "/:@") for host in hosts), "invalid allowed image hosts")
    reviewstate.require(isinstance(chapters, list) and 0 < len(chapters) <= 1000, "chapters must contain 1..1000 ordered entries")
    seen, locations = set(), set()
    for chapter in chapters:
        reviewstate.require(isinstance(chapter, dict), "chapter must be an object")
        identity = chapter.get("id")
        reviewstate.require(isinstance(identity, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", identity) and identity not in seen, "chapter ids must be unique safe names")
        seen.add(identity)
        for field in ("title", "content_selector"):
            reviewstate.require(isinstance(chapter.get(field), str) and bool(chapter[field].strip()), f"chapter requires {field}")
        reviewstate.require(sum(key in chapter for key in ("path", "url")) == 1, "chapter requires exactly one path or url")
        value = chapter.get("path", chapter.get("url"))
        reviewstate.require(isinstance(value, str) and bool(value.strip()), "empty chapter location")
        if "path" in chapter:
            reviewstate.require(not Path(value).is_absolute() and not PureWindowsPath(value).anchor,
                                "local chapter paths must be relative to the manifest")
        location = str((Path(path).resolve().parent / value).resolve()) if "path" in chapter else urldefrag(value).url
        identity_key = (location, chapter["content_selector"])
        reviewstate.require(identity_key not in locations, "duplicate chapter source and selector")
        locations.add(identity_key)
    return data


def local_file(root, name, limit):
    target = (root / name).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("local chapter or image escapes the manifest directory")
    with target.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("source file exceeds its byte limit")
    return raw, target


def epub_bytes(data, chapters, assets):
    namespace = "http://www.idpf.org/2007/opf"
    package = ET.Element("package", xmlns=namespace, version="2.0", attrib={"unique-identifier": "book-id"})
    metadata = ET.SubElement(package, "metadata", {"xmlns:dc": "http://purl.org/dc/elements/1.1/"})
    ET.SubElement(metadata, "dc:title").text = data["title"]
    ET.SubElement(metadata, "dc:language").text = data["source_language"]
    ET.SubElement(metadata, "dc:identifier", id="book-id").text = "revayat:" + ir.sha256_bytes(json.dumps(data, sort_keys=True).encode("utf-8"))
    manifest = ET.SubElement(package, "manifest")
    spine = ET.SubElement(package, "spine")
    members = {"mimetype": b"application/epub+zip", "META-INF/container.xml":
        b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'}
    for identity, content in chapters:
        href = f"chapters/{identity}.xhtml"
        ET.SubElement(manifest, "item", id="chapter-" + identity, href=href, attrib={"media-type": "application/xhtml+xml"})
        ET.SubElement(spine, "itemref", idref="chapter-" + identity)
        members[href] = content
    for index, (name, (raw, mime)) in enumerate(assets.items()):
        ET.SubElement(manifest, "item", id=f"asset-{index}", href="assets/" + name, attrib={"media-type": mime})
        members["assets/" + name] = raw
    members["content.opf"] = ET.tostring(package, encoding="utf-8", xml_declaration=True)
    result = BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        for name, raw in members.items():
            archive.writestr(zipfile.ZipInfo(name), raw)
    return result.getvalue()


def reuse_snapshot(data, manifest_path, out):
    snapshot = reviewstate.object_file(out / "web-source.json")
    reviewstate.require(snapshot.get("schema") == SNAPSHOT and snapshot.get("complete") is True,
                        "web snapshot is incomplete or has an unknown schema")
    if snapshot.get("input") != data:
        raise reviewstate.Refused("source-changed", "chapter input changed; use a new workspace without overwriting the translation")
    book = ir.load_book(out / "book.json")
    if book["source"].get("web_snapshot_sha256") != ir.sha256_bytes(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")):
        raise reviewstate.Refused("source-changed", "acquisition record changed; existing translation was preserved")
    for field in ("files", "local_sources", "chapters"):
        reviewstate.require(isinstance(snapshot.get(field), list), f"snapshot requires {field}")
    for field, root in (("files", out), ("local_sources", manifest_path.parent)):
        for record in snapshot[field]:
            reviewstate.require(isinstance(record, dict) and isinstance(record.get("path"), str)
                                and isinstance(record.get("sha256"), str), "invalid snapshot file identity")
            path = (root / record["path"]).resolve()
            if not path.is_relative_to(root):
                raise reviewstate.Refused("unsafe-source", "snapshot path escapes its source directory")
            try:
                current = ir.sha256_file(path)
            except OSError:
                current = ""
            if current != record["sha256"]:
                raise reviewstate.Refused("source-changed", "a source or snapshot asset changed or disappeared; existing translation was preserved")
    reviewstate.require(isinstance(snapshot.get("epub"), str), "snapshot requires EPUB identity")
    epub = (out / snapshot["epub"]).resolve()
    if (not epub.is_relative_to(out) or book["source"].get("path") != str(epub)
            or book["source"].get("sha256") != snapshot.get("epub_sha256")
            or book["source"].get("web_chapters") != snapshot["chapters"]
            or ir.sha256_file(epub) != snapshot.get("epub_sha256")):
        raise reviewstate.Refused("source-changed", "book and acquisition snapshot no longer identify the same source")
    return {"ok": True, "book": str(out / "book.json"), "chapters": len(data["chapters"]),
            "source": str(epub), "reused": True, "remote_freshness": "cached-snapshot-only"}


def import_book(manifest_path, out, *, resume=False):
    manifest_path, out = Path(manifest_path).resolve(), Path(out).resolve()
    data = read_manifest(manifest_path)
    book_path = out / "book.json"
    if book_path.exists():
        if resume:
            return reuse_snapshot(data, manifest_path, out)
        raise reviewstate.Refused("existing-book", "this destination already contains a book; preserve it and choose a new workspace")
    out.mkdir(parents=True, exist_ok=True)
    if not (out / "assets").resolve().is_relative_to(out):
        raise reviewstate.Refused("unsafe-source", "output assets directory escapes the selected workspace")
    snapshot_dir = out / ("source-web-" + uuid.uuid4().hex)
    snapshot_dir.mkdir()
    originals = snapshot_dir / "originals"
    originals.mkdir()
    chapters, assets, records, files, local_sources = [], {}, [], [], []
    total = 0
    root = manifest_path.parent

    def retain(raw):
        nonlocal total
        total += len(raw)
        if total > TOTAL_LIMIT:
            raise ValueError("chapter acquisition exceeds the total byte limit")

    for item in data["chapters"]:
        original = originals / (item["id"] + ".html")
        if "url" in item:
            host = urlsplit(item["url"]).hostname
            raw, metadata = webfetch.download(item["url"], original, limit=HTML_LIMIT,
                allowed_hosts={host.encode("idna").decode("ascii").lower()} if host else set(),
                media_types={"text/html", "application/xhtml+xml", "text/plain"})
            source = metadata["url"]
            plain_text = metadata["media_type"] == "text/plain"
        else:
            raw, source = local_file(root, item["path"], HTML_LIMIT)
            plain_text = source.suffix.lower() == ".txt"
            original.write_bytes(raw)
            local_sources.append({"path": str(source.relative_to(root)), "sha256": ir.sha256_bytes(raw)})
        retain(raw)
        files.append({"path": str(original.relative_to(out)), "sha256": ir.sha256_bytes(raw)})
        records.append({"id": item["id"], "path": str(source), "original": str(original.relative_to(out)), "sha256": ir.sha256_bytes(raw)})

        def asset(href):
            clean = urldefrag(href).url
            if "url" in item:
                address = urljoin(source, clean)
                hosts = {urlsplit(source).hostname, *data.get("allowed_image_hosts", [])}
                image_path = originals / ("image-" + uuid.uuid4().hex)
                blob, _ = webfetch.download(address, image_path, limit=IMAGE_LIMIT,
                    allowed_hosts={host.encode("idna").decode("ascii").lower() for host in hosts},
                    media_types={"image/png", "image/jpeg", "image/gif"})
                files.append({"path": str(image_path.relative_to(out)), "sha256": ir.sha256_bytes(blob)})
            else:
                if urlsplit(clean).scheme or urlsplit(clean).netloc:
                    raise ValueError("saved chapters require local images beneath the manifest directory")
                blob, local_image = local_file(root, str((source.parent / unquote(clean)).relative_to(root)), IMAGE_LIMIT)
                local_sources.append({"path": str(local_image.relative_to(root)), "sha256": ir.sha256_bytes(blob)})
            retain(blob)
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(BytesIO(blob)) as picture:
                        kind = picture.format
                        if picture.width * picture.height > ir.RENDER_MAX_PIXELS:
                            raise ValueError("source image exceeds the native pixel limit")
                        picture.verify()
            except (Image.DecompressionBombWarning, Image.DecompressionBombError, SyntaxError) as error:
                raise ValueError("source image is invalid or exceeds safe decoding limits") from error
            formats = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"), "GIF": ("gif", "image/gif")}
            if kind not in formats:
                raise ValueError("image format is not supported without a reviewed derivative")
            extension, mime = formats[kind]
            name = ir.sha256_bytes(blob) + "." + extension
            assets[name] = (blob, mime)
            return "../assets/" + name

        content = raw
        if plain_text:
            text = raw.decode("utf-8-sig")
            content = ("<html><body>" + "".join("<p>" + escape(part) + "</p>" for part in
                       re.split(r"\r?\n\s*\r?\n", text) if part.strip()) + "</body></html>").encode("utf-8")
        chapters.append((item["id"], chapter_html(content, item["content_selector"], item["title"], asset)))
    epub = snapshot_dir / "source.epub"
    epub.write_bytes(epub_bytes(data, chapters, assets))
    book = read_epub(str(epub), out / "assets", lang_source=data["source_language"])
    book["source"]["web_chapters"] = records
    files.append({"path": str(epub.relative_to(out)), "sha256": ir.sha256_file(epub)})
    files.extend({"path": "assets/" + block["asset"], "sha256": block["sha256"]}
                 for block in book["blocks"] if block["type"] == "image")
    snapshot = {"schema": SNAPSHOT, "input": data, "chapters": records,
                "files": files, "local_sources": local_sources,
                "epub": str(epub.relative_to(out)), "epub_sha256": ir.sha256_file(epub), "complete": True}
    book["source"]["web_snapshot_sha256"] = ir.sha256_bytes(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    with bookwrite.transaction(book_path, actor="web-import", expect="") as tx:
        tx.book.update(book)
        tx.side(out / "web-source.json", json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n")
    return {"ok": True, "book": str(book_path), "chapters": len(chapters), "source": str(epub), "reused": False}


@reviewstate.cli
def main(argv=None):
    ir.use_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    result = import_book(args.manifest, args.out, resume=args.resume)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
