"""The finished `.docx`, checked as a package rather than as a promise.

`qa check` reads `book.json`; this reads the file that was actually written. The
two answer different questions, and only this one can catch a build that dropped a
picture, renumbered a footnote or produced a document Word will not open.

**Package integrity, not whole-document review.** What a page *looks* like is
`render-qa`'s question and needs eyes; what the package *contains* is this one's
and is decidable. Keeping them apart is why this file can be strict.

Everything here used to be lexical — regexes over the raw XML, a substring test
for a content type, a count of `<wp:extent>` tags — and measured against real
mutations that answered wrongly in both directions:

* removing every footnote reference **and** body passed, because the note checks
  only ran `if refs:` and nothing compared the book's own note inventory;
* substituting a note's text passed, because bodies were matched by id and never
  by content;
* deleting the footnotes *relationship* passed, because nothing resolved the
  relationship graph — only the content-type string was searched for;
* duplicating a reference passed, because the references were collected into a
  set, which is exactly the shape that loses a duplicate;
* distorting a picture's extent passed, because extents were counted, not read;
* a truncated `word/document.xml` passed, because a regex over broken XML finds
  fewer matches and nothing said the part would not parse;
* and a package using a different, legal namespace prefix for the relationship
  elements was **rejected**.

`opc` now does the OPC work — namespace-aware parsing, resolved content types,
normalised relationship targets, bounded archive — and this module asks the
questions, against the IR's own expectations.
"""

from __future__ import annotations

import math
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import bookir as ir
import opc
from findings import ERROR, WARNING, Report

#: Legacy lexical helpers remain for import compatibility. Package validation
#: reads bookmark and hyperlink elements by namespace, not by the chosen prefix.
_BOOKMARK = re.compile(r'<w:bookmarkStart[^>]*w:name="([^"]+)"')
_ANCHOR = re.compile(r'<w:hyperlink[^>]*w:anchor="([^"]+)"')
_BLIP = re.compile(r'<a:blip[^>]*r:embed="([^"]+)"')

#: English metric units per point, and how far a picture's drawn aspect ratio may
#: drift from the source's before it is a distortion rather than rounding. The
#: builder converts points to EMU through python-docx, so a point of rounding is
#: expected; 2% is far tighter than any visible squash.
EMU_PER_PT = 12700
ASPECT_TOLERANCE = 0.02
#: How far the drawn width may exceed the section's text width. The builder fits a
#: picture to the text block, preserving aspect (`build_docx._image_size`), so
#: anything wider than the page's own text measure was not produced by it.
WIDTH_TOLERANCE = 1.02


def _package_image_order(archive: zipfile.ZipFile, document: str) -> list[str]:
    """The SHA-256 of every picture, in the order the document shows them.

    python-docx stores one media part per *distinct* image, so two identical
    pictures share a part and ``word/media/`` cannot describe order at all. The
    ``<a:blip r:embed>`` sequence in ``document.xml`` can, once each relationship
    is followed back to the bytes it points at.

    Kept on the raw text for the callers that already had it; the checks below go
    through `opc` so a relationship written with another prefix resolves.
    """
    try:
        package = opc.Package(archive)
        targets = {identifier: record["part"] for identifier, record
                   in package.relationships("word/document.xml").items()}
    except opc.Damaged:
        return []

    digests: dict[str, str] = {}
    order: list[str] = []
    for relationship_id in _BLIP.findall(document):
        name = targets.get(relationship_id, "")
        if not name:
            continue
        if name not in digests:
            try:
                digests[name] = ir.sha256_bytes(archive.read(name))
            except KeyError:            # an external or missing part
                digests[name] = ""
        if digests[name]:
            order.append(digests[name])
    return order


def _expected_notes(book: dict[str, Any]) -> list[str]:
    """The note bodies the document must place, in reading order, with repeats.

    The **IR's** inventory, which is the thing the package has to be checked
    against: the builder writes one Word footnote per marker it meets while
    walking the blocks, so the sequence of note texts is the correspondence —
    not the numeric ids, which Word allocates and renumbers as it pleases.
    """
    bodies = {note["id"]: str(note.get("target") or note.get("text") or "")
              for note in book.get("footnotes") or []}
    wanted: list[str] = []
    for block in ir.iter_text_blocks(book):
        for ref in ir.footnote_refs(block.get("target") or ""):
            if ref in bodies:
                wanted.append(bodies[ref])
    return wanted


def _check_footnotes(package: opc.Package, book: dict[str, Any] | None,
                     report: Report) -> None:
    """Every note the book places is in the package, once, with its own text."""
    document = package.xml("word/document.xml")
    references = opc.footnote_references(document)
    parts = package.related("word/document.xml", "footnotes")
    expected = _expected_notes(book) if book is not None else []

    report.count("footnote_references", len(references))
    if expected and not references:
        # The strongest form of the failure, and the one that used to pass: the
        # whole check was behind `if refs:`, so a document with every note
        # stripped out had nothing asked of it.
        report.add(ERROR, "footnotes-lost", "word/document.xml",
                   f"the book places {len(expected)} footnote(s) and the document "
                   f"has no reference at all — rebuild from book.json")
        return
    if not references:
        return

    if not parts:
        report.add(ERROR, "footnotes-relationship-missing", "word/document.xml",
                   f"{len(references)} reference(s) and no footnotes "
                   f"relationship: Word resolves the notes through the "
                   f"relationship, so it will not find them")
        return
    name = parts[0]
    if name not in package.names:
        report.add(ERROR, "footnotes-part-missing", name,
                   "the footnotes relationship points at a part that is not in "
                   "the package")
        return
    declared = package.content_types().get(name, "")
    if not declared.endswith("footnotes+xml"):
        report.add(ERROR, "footnotes-content-type", name,
                   f"declared as {declared or '(nothing)'!r}; Word opens a part by "
                   f"its declared content type, so the notes are unreachable")

    notes_root = package.xml(name)
    if any(any(char in (node.text or "") for char in "\r\n\t")
           for node in notes_root.iter(opc.qname("w", "t"))):
        report.add(ERROR, "footnote-control-text", name,
                   "note layout controls are raw text rather than native Word breaks/tabs; rebuild from book.json")
    bodies = opc.footnote_bodies(notes_root)
    for identifier, times in sorted(Counter(references).items()):
        if identifier not in bodies:
            report.add(ERROR, "footnote-body-missing", str(identifier),
                       "a reference whose note has no body in the footnotes part")
        elif times > 1:
            # Multiplicity, which a set could not see. Two markers on one note
            # means one of the two sentences is footnoted by text written for the
            # other.
            report.add(ERROR, "footnote-reference-duplicated", str(identifier),
                       f"referred to {times} times; each note belongs to one "
                       f"sentence")
    orphans = sorted(set(bodies) - set(references))
    if orphans:
        report.add(ERROR, "footnote-body-orphaned", ", ".join(orphans[:6]),
                   "a note body nothing in the document refers to; it prints "
                   "under a page that does not point at it")

    if book is None:
        return
    # The content, compared in reading order. A substituted body is invisible to
    # every id-based check — the ids line up perfectly and the note says something
    # the translator never wrote.
    placed = [bodies.get(identifier, "") for identifier in references]
    wanted = [ir.plain_text(text).replace("\r\n", "\n").replace("\r", "\n") for text in expected]
    if placed != wanted:
        report.add(ERROR, "footnote-text-mismatch", "word/footnotes.xml",
                   f"the notes in the document are not the notes in the book: "
                   f"{len(expected)} expected, {len(placed)} placed, first "
                   f"difference at "
                   f"{_first_difference(placed, wanted)} — rebuild from book.json")


def _normalised(text: str) -> str:
    """Word's own text, comparable with the book's: whitespace and markup aside."""
    return " ".join(ir.plain_text(text or "").split())


def _first_difference(placed: list[str], expected: list[str]) -> str:
    for position, (left, right) in enumerate(zip(placed, expected), start=1):
        if left != right:
            return f"note {position}"
    return f"note {min(len(placed), len(expected)) + 1}"


def _check_link_controls(package: opc.Package, book: dict[str, Any] | None,
                         report: Report) -> None:
    document = package.xml("word/document.xml")
    relationships = package.relationships("word/document.xml")
    labels: dict[str, set[str]] = {}
    for block in (book or {}).get("blocks", []):
        for link in block.get("links") or []:
            labels.setdefault(link.get("href") or "", set()).add((link.get("text") or ""))
    for link in document.iter(opc.qname("w", "hyperlink")):
        if any(any(char in (node.text or "") for char in "\r\n\t")
               for node in link.iter(opc.qname("w", "t"))):
            report.add(ERROR, "link-control-text", "word/document.xml",
                       "link layout controls are raw text; rebuild from book.json")
        anchor = link.get(opc.qname("w", "anchor"))
        identifier = link.get(opc.qname("r", "id"))
        href = "#" + anchor if anchor else relationships.get(identifier, {}).get("target", "")
        expected = labels.get(href, set())
        if any("\n" in text or "\t" in text or "\r" in text for text in expected):
            if opc.text_of(link) not in {text.replace("\r\n", "\n").replace("\r", "\n") for text in expected}:
                report.add(ERROR, "link-text-mismatch", "word/document.xml",
                           "link text or layout controls differ from the book; rebuild from book.json")


def _text_width_pt(book: dict[str, Any] | None) -> float:
    """The measure the builder fits a picture to, from the same fields it reads."""
    page = (book or {}).get("page") or ir.default_page_setup()
    return max(72.0, page["width_pt"] - page["margin_inner_pt"]
               - page["margin_outer_pt"])


def _check_image_geometry(package: opc.Package, book: dict[str, Any] | None,
                          report: Report) -> None:
    """A picture must be drawn at the shape the book says, fitted as documented.

    Extents used to be counted and never read, so a picture stretched to twice its
    height passed: the bytes are untouched and the count is unchanged. The rule
    checked here is the builder's own (`build_docx._image_size`): the source
    aspect is preserved, and a picture wider than the text measure is scaled down
    to it.
    """
    if book is None:
        return
    placed = opc.drawing_extents(package.xml("word/document.xml"))
    report.count("pictures_drawn", len(placed))
    if not placed:
        return
    targets = {identifier: record["part"] for identifier, record
               in package.relationships("word/document.xml").items()}
    by_digest: dict[str, dict[str, Any]] = {}
    for block in book.get("blocks") or []:
        if block.get("type") == "image" and block.get("sha256"):
            by_digest.setdefault(block["sha256"], block)

    limit = _text_width_pt(book) * WIDTH_TOLERANCE
    pictures = [node for node in package.xml('word/document.xml').iter()
                if node.tag in {opc.qname('wp', 'inline'), opc.qname('wp', 'anchor')}]
    for position, (relationship_id, cx, cy) in enumerate(placed, start=1):
        if position <= len(pictures):
            transform = next(pictures[position - 1].iter(opc.qname('a', 'xfrm')), None)
            if transform is not None:
                try:
                    angle = int(transform.get('rot', '0')) / 60000 * math.pi / 180
                    cx, cy = (abs(cx * math.cos(angle)) + abs(cy * math.sin(angle)),
                              abs(cx * math.sin(angle)) + abs(cy * math.cos(angle)))
                except ValueError:
                    report.add(ERROR, 'picture-size-invalid', f'picture {position}', 'native rotation is invalid')
                    continue
        where = f"picture {position}"
        if cx <= 0 or cy <= 0:
            report.add(ERROR, "picture-size-invalid", where,
                       f"drawn at {cx}x{cy} EMU, which Word cannot render")
            continue
        if cx / EMU_PER_PT > limit:
            report.add(ERROR, "picture-too-wide", where,
                       f"drawn {cx / EMU_PER_PT:.0f}pt wide on a text measure of "
                       f"{_text_width_pt(book):.0f}pt; the builder fits a picture "
                       f"to the text block, so this was widened afterwards")
        name = targets.get(relationship_id, "")
        try:
            digest = ir.sha256_bytes(package.read(name)) if name else ""
        except opc.Damaged:
            digest = ""
        source = by_digest.get(digest)
        if source is None:
            continue
        wanted = _source_aspect(source)
        if wanted is None:
            continue
        drawn = cx / cy
        if abs(drawn - wanted) / wanted > ASPECT_TOLERANCE:
            report.add(ERROR, "picture-aspect", where,
                       f"drawn at {drawn:.3f} and the source is {wanted:.3f}: the "
                       f"illustration is squashed, which preserving the aspect "
                       f"ratio cannot produce")


def _check_image_transforms(package, book, report):
    """Native rotation/reflection is occurrence identity, not the media bytes."""
    if book is None:
        return
    pictures = [node for node in package.xml('word/document.xml').iter()
                if node.tag in {opc.qname('wp', 'inline'), opc.qname('wp', 'anchor')}]
    expected = [b for b in book.get('blocks') or [] if b['type'] == 'image']
    for index, (block, picture) in enumerate(zip(expected, pictures), 1):
        matrix = block.get('transform')
        if matrix is None:
            continue
        try:
            a, b, c, d, e, f = map(float, matrix)
            width, height = math.hypot(a, b), math.hypot(c, d)
            if not all(math.isfinite(v) for v in (a, b, c, d, e, f)) or width <= 0 or height <= 0 or abs(a*c + b*d) > width*height*1e-6:
                raise ValueError('unsupported affine')
            xfrm = next(picture.iter(opc.qname('a', 'xfrm')))
            angle = int(xfrm.get('rot', '0')) / 60000 * math.pi / 180
            flip_h = -1 if xfrm.get('flipH', '0') in {'1', 'true'} else 1
            flip_v = -1 if xfrm.get('flipV', '0') in {'1', 'true'} else 1
            native = xfrm.find(opc.qname('a', 'ext'))
            w, h = int(native.get('cx')) / EMU_PER_PT, int(native.get('cy')) / EMU_PER_PT
            observed = (math.cos(angle)*w*flip_h, math.sin(angle)*w*flip_h,
                        -math.sin(angle)*h*flip_v, math.cos(angle)*h*flip_v)
            scale = min(1.0, _text_width_pt(book) / float(block['width_pt']))
            if any(abs(v - wanted * scale) > 0.05 for v, wanted in zip(observed, (a, b, c, d))):
                raise ValueError('native image orientation disagrees')
            extent = picture.find(opc.qname('wp', 'extent'))
            host_w, host_h = int(extent.get('cx')) / EMU_PER_PT, int(extent.get('cy')) / EMU_PER_PT
            if abs(host_w - w) > 0.05 or abs(host_h - h) > 0.05:
                raise ValueError('native inline host does not match unrotated picture')
            effect = picture.find(opc.qname('wp', 'effectExtent'))
            dx = max(0.0, (abs(observed[0]) + abs(observed[2]) - host_w) / 2)
            dy = max(0.0, (abs(observed[1]) + abs(observed[3]) - host_h) / 2)
            if any(int(effect.get(edge, '0') if effect is not None else '0') / EMU_PER_PT + 0.01 < value
                   for edge, value in [('l', dx), ('r', dx), ('t', dy), ('b', dy)]):
                raise ValueError('native inline rotation overflow is not reserved')
        except (ValueError, TypeError, AttributeError, StopIteration):
            report.add(ERROR, 'image-transform-mismatch', f'picture {index}',
                       'native image rotation, reflection or pre-rotation dimensions differ from source occurrence')


def _source_aspect(block: dict[str, Any]) -> float | None:
    for width_field, height_field in (("width_pt", "height_pt"),
                                      ("pixel_width", "pixel_height")):
        width, height = block.get(width_field), block.get(height_field)
        if width and height:
            return float(width) / float(height)
    return None


def _check_image_order(archive: zipfile.ZipFile, document: str,
                       book: dict[str, Any], report: Report) -> None:
    """The pictures must be where the book puts them, not merely present.

    Counting was the only check, and a count cannot see an illustration that
    moved: the picture is still in the file, the caption underneath it now
    belongs to a different one.
    """
    expected = [block["sha256"] for block in book.get("blocks", [])
                if block["type"] == "image" and block.get("sha256")]
    if not expected:
        return
    actual = _package_image_order(archive, document)
    report.count("pictures_placed", len(actual))
    # No early return on an empty `actual`. "The document shows no pictures at
    # all" is the *strongest* form of the failure this function exists to catch,
    # and treating it as nothing to compare is how deleting the only `<w:drawing>`
    # — media part left in place, so the file count is unchanged — passed.

    # Three distinct conditions, and the old check could see only the third.
    # Comparing the *intersection* of the two sides excluded exactly the pictures
    # that were wrong: a substituted image's bytes are in neither set, so it
    # dropped out of its own comparison, and a removed placement dropped out of
    # its. Measured: replacing `word/media/image1.png` with a different image of
    # the same length passed, and deleting the `<w:drawing>` while keeping the
    # media part passed.
    placed = set(actual)
    wanted = set(expected)
    for position, digest in enumerate(expected, start=1):
        if digest not in placed:
            report.add(ERROR, "image-missing-placement", f"picture {position}",
                       "the book places this illustration and the document does "
                       "not show it — the media part may still be in the package, "
                       "which is why counting the files cannot see this")
    for position, digest in enumerate(actual, start=1):
        if digest not in wanted:
            report.add(ERROR, "image-unexpected", f"placed picture {position}",
                       "this illustration's bytes are not any picture the book "
                       "expects — it was substituted or edited after the build; "
                       "rebuild from book.json")

    # Order is asked of the pictures present on both sides, because a missing or
    # substituted one is already named above and would otherwise shift every
    # position after it.
    shared = wanted & placed
    in_book = [digest for digest in expected if digest in shared]
    in_package = [digest for digest in actual if digest in shared]
    if in_book != in_package:
        first = next((position for position, pair in enumerate(zip(in_book, in_package))
                      if pair[0] != pair[1]), min(len(in_book), len(in_package)))
        report.add(ERROR, "image-order", f"picture {first + 1}",
                   "the pictures are not in the book's order — a caption now "
                   "sits under the wrong illustration; rebuild from book.json "
                   "instead of editing the document")


def _check_bookmarks(names: list[str], book: dict[str, Any] | None,
                     report: Report) -> None:
    """Bookmarks are what the table of contents and every internal link land on."""
    report.count("bookmarks", len(names))
    for name, times in sorted(Counter(names).items()):
        if times > 1:
            report.add(ERROR, "bookmark-duplicate", name,
                       f"opened {times} times; Word sends every link to the "
                       f"first, so the contents jump to the wrong chapter — "
                       f"rebuild instead of editing the document")

    if book is None:
        return
    headings = sum(1 for block in book.get("blocks", []) if block["type"] == "heading")
    report.count("headings_in_book", headings)
    if headings and not names:
        report.add(ERROR, "bookmarks-missing", "word/document.xml",
                   f"{headings} headings and no bookmarks; the table of contents "
                   f"has nothing to link to — rebuild")


def _check_cell_termination(package: opc.Package, report: Report) -> None:
    """A valid cell ends in a paragraph even when its last authored item is a table."""
    for index, cell in enumerate(package.xml("word/document.xml").iter(opc.qname("w", "tc")), start=1):
        if len(cell) == 0 or cell[-1].tag != opc.qname("w", "p"):
            report.add(ERROR, "table-cell-terminal-paragraph", f"cell {index}",
                       "table cell has no final native paragraph; rebuild without deleting its required terminator")


def _check_authored_structure(package, book, report):
    """Compare declared native topology and zero-unit layout with actual XML."""
    if book is None:
        return
    import bookstructure
    problems = bookstructure.validate(book)
    if problems:
        report.add(ERROR, 'book-structure-invalid', 'book', '; '.join(problems))
        return
    root = package.xml('word/document.xml')
    body = root.find('w:body', opc.NS)
    if body is None:
        return
    actual, layouts, sequence = [], [], []
    relationships = package.relationships('word/document.xml')
    skipped = set()
    children = list(body)
    prefix = 0
    for field, style_id in [('title', 'Title'), ('author', 'Subtitle')]:
        text = (book.get('meta') or {}).get(field + '_target') or (book.get('meta') or {}).get(field) or ''
        if not text.strip() or prefix >= len(children):
            continue
        node = children[prefix]
        style = node.find('w:pPr/w:pStyle', opc.NS)
        if opc.text_of(node) == ir.plain_text(text) and style is not None and style.get(opc.qname('w', 'val')) in {style_id, 'Normal'}:
            skipped.add(node)
            prefix += 1
    for index, node in enumerate(children):
        if any((part.text or '').strip().startswith('TOC ') for part in node.iter(opc.qname('w', 'instrText'))):
            skipped.add(node)
            if index == prefix + 1:
                skipped.add(children[prefix])

    def walk(owner, parent=None, row=None, cell=None):
        for node in owner:
            if node in skipped:
                continue
            if node.tag == opc.qname('w', 'p'):
                text = opc.text_of(node)
                excluded = (node.find('w:pPr/w:sectPr', opc.NS) is not None or
                            any(n.tag == opc.qname('w', 'drawing') or
                                n.tag in {opc.qname('w', 'fldChar'), opc.qname('w', 'instrText'),
                                          opc.qname('w', 'footnoteReference'), opc.qname('w', 'endnoteReference')} or
                                n.tag == opc.qname('w', 'br') and n.get(opc.qname('w', 'type'), 'textWrapping') != 'textWrapping'
                                for n in node.iter()))
                terminal = parent is not None and node is owner[-1] and not text and not excluded
                if not text.strip() and not excluded and not terminal:
                    layouts.append((parent, row, cell, text))
                    sequence.append(('layout', parent, row, cell, text))
                elif text.strip():
                    sequence.append(('text', parent, row, cell, text))
                for blip in node.iter(opc.qname('a', 'blip')):
                    target = relationships.get(blip.get(opc.qname('r', 'embed')), {}).get('part')
                    sequence.append(('image', parent, row, cell,
                                     ir.sha256_bytes(package.read(target)) if target else 'absent'))
            elif node.tag == opc.qname('w', 'tbl'):
                index = len(actual)
                sequence.append(('table', parent, row, cell, index))
                rows = node.findall('w:tr', opc.NS)
                columns = len(node.findall('w:tblGrid/w:gridCol', opc.NS))
                record = {'rows': len(rows), 'columns': columns, 'parent': parent,
                          'parent_row': row, 'parent_cell': cell, 'cells': []}
                actual.append(record)
                opened = {}
                for r, tr in enumerate(rows, 1):
                    before = tr.find('w:trPr/w:gridBefore', opc.NS)
                    c = int(before.get(opc.qname('w', 'val'), '0')) + 1 if before is not None else 1
                    following = {}
                    for tc in tr.findall('w:tc', opc.NS):
                        span = tc.find('w:tcPr/w:gridSpan', opc.NS)
                        width = int(span.get(opc.qname('w', 'val'), '1')) if span is not None else 1
                        merge = tc.find('w:tcPr/w:vMerge', opc.NS)
                        if merge is not None and merge.get(opc.qname('w', 'val')) != 'restart':
                            if c not in opened or opened[c]['col_span'] != width:
                                raise ValueError('invalid native vertical table continuation')
                            opened[c]['row_span'] += 1
                            following[c] = opened[c]
                        else:
                            spec = {'row': r, 'cell': c, 'row_span': 1, 'col_span': width}
                            record['cells'].append(spec)
                            if merge is not None:
                                following[c] = spec
                            walk(tc, index, r, c)
                        c += width
                    opened = following

    try:
        walk(body)
    except (ValueError, TypeError):
        report.add(ERROR, 'table-structure-invalid', 'word/document.xml', 'native table grid is invalid')
        return
    if 'tables' in book:
        identities = {r['id']: i for i, r in enumerate(book['tables'])}
        expected = [{'rows': r['rows'], 'columns': r['columns'],
                     'cells': [{key: c[key] for key in bookstructure.CELL_FIELDS} for c in r['cells']],
                     'parent': identities.get(r.get('parent_table')),
                     'parent_row': r.get('parent_row'), 'parent_cell': r.get('parent_cell')}
                    for r in book['tables']]
        if expected != actual:
            report.add(ERROR, 'table-structure-mismatch', 'word/document.xml',
                       'native table grids, merges, order or immediate cell ownership differ from source inventory')
    wanted_layouts = [(next((i for i, r in enumerate(book.get('tables') or []) if r['id'] == b.get('table')), None),
                       b.get('row'), b.get('cell'), b['controls'])
                      for b in book.get('blocks') or [] if b['type'] == 'layout']
    # Only each cell's final required empty paragraph is excluded above.
    if wanted_layouts != layouts:
        report.add(ERROR, 'layout-structure-mismatch', 'word/document.xml',
                   'authored empty/control-only paragraph sequence differs from the book')
    identities = {r['id']: i for i, r in enumerate(book.get('tables') or [])}
    expected_sequence = []
    for block in book.get('blocks') or []:
        kind = block['type']
        owner = identities.get(block.get('table'))
        row, cell = block.get('row'), block.get('cell')
        if kind == 'table':
            expected_sequence.append(('table', identities.get(block.get('parent_table')),
                                      block.get('parent_row'), block.get('parent_cell'), owner))
        elif kind == 'layout':
            expected_sequence.append(('layout', owner, row, cell, block['controls']))
        elif kind in ir.TEXT_TYPES or kind == 'separator':
            text = '❖' if kind == 'separator' else ir.plain_text(block.get('target') or block.get('text') or '').replace('\r\n', '\n').replace('\r', '\n')
            if text.strip():
                expected_sequence.append(('text', owner, row, cell, text))
        elif kind == 'image':
            expected_sequence.append(('image', owner, row, cell, block.get('sha256')))
            caption = ir.plain_text(block.get('target_alt') or block.get('alt') or '')
            if caption.strip() and ('text', owner, row, cell, caption) in sequence:
                expected_sequence.append(('text', owner, row, cell, caption))
    if 'tables' not in book:
        # Legacy grids lack source placement events. Keep their existing package
        # contracts; the new exact sequence claim needs authoritative topology.
        sequence = [event for event in sequence if event[0] != 'table']
        sequence = [(kind, None, None, None, value) for kind, _, _, _, value in sequence]
        expected_sequence = [(kind, None, None, None, value) for kind, _, _, _, value in expected_sequence]
    if expected_sequence != sequence:
        report.add(ERROR, 'body-structure-order-mismatch', 'word/document.xml',
                   'interleaved native body prose, illustrations, tables or layout differ from book order')


#: The parts a Word package cannot open without, and the root each must have.
REQUIRED = (
    ("[Content_Types].xml", ("ct", "Types")),
    ("word/document.xml", ("w", "document")),
    ("word/_rels/document.xml.rels", ("pr", "Relationships")),
)


def check_docx(path: Path, book: dict[str, Any] | None = None) -> Report:
    report = Report()
    try:
        package = opc.open_package(Path(path))
    except opc.Damaged as damaged:
        report.add(ERROR, damaged.code if damaged.code != "docx-unreadable"
                   else "docx-unreadable", str(path), damaged.detail)
        return report

    with package.archive:
        names = set(package.names)
        # Every required part, present *and* parsing, with the root the format
        # says. A truncated part used to pass every check below it: a regex over
        # broken XML finds fewer matches, and nothing said the matches were
        # missing rather than absent.
        for required, (prefix, tag) in REQUIRED:
            if required not in names:
                report.add(ERROR, "docx-invalid", str(path),
                           f"no {required}: this is not a Word package Word will "
                           f"open, whatever else is in it")
                continue
            try:
                root = package.xml(required)
            except opc.Damaged as damaged:
                report.add(ERROR, damaged.code, required, damaged.detail)
                continue
            if root.tag != opc.qname(prefix, tag):
                report.add(ERROR, "part-root-wrong", required,
                           f"root element is {root.tag!r}, not {tag}")
        if report.summary()["errors"]:
            return report

        try:
            document = package.read("word/document.xml").decode("utf-8", "replace")
            _check_footnotes(package, book, report)
            _check_link_controls(package, book, report)
            _check_cell_termination(package, report)
            if book is not None and ('tables' in book or any(b['type'] == 'layout' for b in book.get('blocks') or [])):
                _check_authored_structure(package, book, report)
            _check_image_geometry(package, book, report)
            _check_image_transforms(package, book, report)
        except opc.Damaged as damaged:
            report.add(ERROR, damaged.code, str(path), damaged.detail)
            return report

        document_root = package.xml("word/document.xml")
        bookmark_names = [node.get(opc.qname("w", "name"), "")
                          for node in document_root.iter(opc.qname("w", "bookmarkStart"))]
        bookmarks = set(bookmark_names)
        anchors = {node.get(opc.qname("w", "anchor"))
                   for node in document_root.iter(opc.qname("w", "hyperlink"))}
        for anchor in sorted(anchors - {None, ""}):
            if anchor not in bookmarks:
                report.add(ERROR, "dead-link", anchor,
                           "internal link has no matching bookmark")
        _check_bookmarks(bookmark_names, book, report)

        media = [name for name in names if name.startswith("word/media/")]
        bidi = next(package.xml("word/document.xml").iter(opc.qname("w", "bidi")),
                    None)
        if bidi is None:
            report.add(WARNING, "no-rtl", str(path),
                       "no w:bidi element found — the document is not "
                       "right-to-left")

        if book is not None:
            expected = sum(1 for block in book["blocks"]
                           if block["type"] == "image")
            unique = len({block["sha256"] for block in book["blocks"]
                          if block["type"] == "image" and block.get("sha256")})
            if media and unique and len(media) < unique:
                report.add(ERROR, "images-lost", str(path),
                           f"{unique} unique images expected, {len(media)} in package")
            elif not media and expected:
                report.add(ERROR, "images-lost", str(path),
                           f"{expected} images expected, none in package")
            _check_image_order(package.archive, document, book, report)

    return report
