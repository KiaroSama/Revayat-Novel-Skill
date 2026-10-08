"""Validated authored table and layout structure, separate from prose units."""
from __future__ import annotations
from copy import deepcopy
from typing import Any

CELL_FIELDS = ('row', 'cell', 'row_span', 'col_span')
PARENT_FIELDS = ('parent_table', 'parent_row', 'parent_cell')
EVENT_FIELDS = ('id', 'type', 'page', 'table', *CELL_FIELDS, *PARENT_FIELDS, 'controls')
MAX_GRID_CELLS = 100_000


def _positive(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def validate(book: dict[str, Any]) -> list[str]:
    """Validate optional source topology without guessing missing legacy shape."""
    problems = []
    blocks = book.get('blocks') or []
    for block in blocks:
        if block.get('type') == 'layout':
            controls = block.get('controls')
            if not isinstance(controls, str) or any(c not in ' \t\r\n' for c in controls):
                problems.append('layout structure requires exact whitespace controls')
            if block.get('text') or block.get('target'):
                problems.append('layout structure cannot carry translation prose')
    records = book.get('tables')
    if records is None:
        if any(b.get('type') == 'table' for b in blocks):
            problems.append('table event requires source table inventory')
        return problems
    if not isinstance(records, list):
        return problems + ['table inventory must be a list']
    tables, cells = {}, {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get('id'), str) or not record['id']:
            problems.append('invalid table identity')
            continue
        identity = record['id']
        if identity in tables:
            problems.append('duplicate table identity')
            continue
        tables[identity] = record
        rows, columns = record.get('rows'), record.get('columns')
        if not _positive(rows) or not _positive(columns) or rows * columns > MAX_GRID_CELLS:
            problems.append('table grid has invalid or excessive dimensions')
            continue
        if not isinstance(record.get('cells'), list):
            problems.append('table cells must be a list')
            continue
        occupied, actual = set(), {}
        for cell in record['cells']:
            if isinstance(cell, dict) and 'terminal_empty' in cell and not isinstance(cell['terminal_empty'], bool):
                problems.append('table terminal_empty must be a boolean')
            if not isinstance(cell, dict) or not all(_positive(cell.get(f)) for f in CELL_FIELDS):
                problems.append('invalid table cell coordinates')
                continue
            row, column, height, width = (cell[f] for f in CELL_FIELDS)
            if row + height - 1 > rows or column + width - 1 > columns:
                problems.append('table cell exceeds declared grid bounds')
                continue
            area = {(r, c) for r in range(row, row + height) for c in range(column, column + width)}
            if occupied & area:
                problems.append('overlapping table cells or duplicate identity')
            occupied.update(area)
            actual[(row, column)] = cell
        for row in range(1, rows + 1):
            covered = sorted(c for r, c in occupied if r == row)
            if covered and covered != list(range(covered[0], covered[-1] + 1)):
                problems.append('table row contains unsupported internal grid holes')
        cells[identity] = actual
    for identity, record in tables.items():
        parent = record.get('parent_table')
        if parent is not None:
            position = (record.get('parent_row'), record.get('parent_cell'))
            if parent not in tables or position not in cells.get(parent, {}):
                problems.append('table immediate parent cell is missing')
        elif any(f in record for f in PARENT_FIELDS[1:]):
            problems.append('table parent coordinates have no parent')
        visited, cursor = set(), identity
        while cursor in tables:
            if cursor in visited:
                problems.append('table ownership cycle')
                break
            visited.add(cursor)
            cursor = tables[cursor].get('parent_table')
    seen, order, active = set(), [], []
    for block in blocks:
        if not block.get('table'):
            active.clear()
        elif block.get('type') == 'table':
            parent = tables.get(block.get('table'), {}).get('parent_table')
            if parent is None:
                active = [block.get('table')]
            elif parent in active:
                active = active[:active.index(parent) + 1] + [block.get('table')]
            else:
                problems.append('nested table event is outside its immediate parent sequence')
        elif block.get('table') in active:
            active = active[:active.index(block['table']) + 1]
        else:
            problems.append('table content is outside its placement sequence')
        identity = block.get('table')
        if not identity:
            continue
        record = tables.get(identity)
        if record is None:
            problems.append('block names an undeclared table')
            continue
        if any(block.get(f) != record.get(f) for f in PARENT_FIELDS):
            problems.append('block table immediate ownership disagrees with inventory')
        if block.get('type') == 'table':
            if identity in seen or record.get('parent_table') and record['parent_table'] not in seen:
                problems.append('table placement event is duplicated or precedes its parent')
            seen.add(identity)
            order.append(identity)
        else:
            if identity not in seen:
                problems.append('table content precedes placement event')
            position = (block.get('row'), block.get('cell'))
            cell = cells.get(identity, {}).get(position)
            if cell is None:
                problems.append('block table cell is missing from inventory')
            elif any(block.get(f, 1) != cell[f] for f in ('row_span', 'col_span')):
                problems.append('block table span disagrees with inventory')
    if order != list(tables):
        problems.append('table placement order differs from source inventory')
    return problems


def select_tables(book: dict[str, Any], blocks) -> list[dict[str, Any]]:
    """Relevant table closure, source ordered; returns independent records."""
    records = book.get('tables') or []
    wanted = {b.get('table') for b in blocks if b.get('table')}
    changed = True
    while changed:
        previous = set(wanted)
        for record in records:
            if record['id'] in wanted and record.get('parent_table'):
                wanted.add(record['parent_table'])
            if record.get('parent_table') in wanted:
                wanted.add(record['id'])
        changed = previous != wanted
    return deepcopy([r for r in records if r['id'] in wanted])


def projection(book: dict[str, Any], blocks=None) -> dict[str, Any]:
    """Authored sequence/topology only, never translation units or approvals."""
    selected = book.get('blocks') or [] if blocks is None else blocks
    return {'tables': deepcopy(book.get('tables') or []) if blocks is None else select_tables(book, selected),
            'events': [{f: deepcopy(b[f]) for f in EVENT_FIELDS if f in b}
                       for b in selected if b.get('table') or b.get('type') == 'layout']}


def validate_book(book: dict[str, Any]) -> list[str]:
    """Structural self-check. Returns human-readable problems (empty == good)."""
    import bookir as ir
    problems: list[str] = []
    seen: set[str] = set()

    for position, block in enumerate(book.get("blocks", [])):
        where = block.get("id") or f"#{position}"
        if not block.get("id"):
            problems.append(f"block {where}: missing id")
        elif block["id"] in seen:
            problems.append(f"block {where}: duplicate id")
        else:
            seen.add(block["id"])

        if block.get("type") not in ir.BLOCK_TYPES:
            problems.append(f"block {where}: unknown type {block.get('type')!r}")
        if block.get("type") == "image" and not block.get("asset"):
            problems.append(f"block {where}: image without asset path")
        if block.get("type") == "heading":
            level = block.get("level")
            if not isinstance(level, int) or not 1 <= level <= 6:
                problems.append(f"block {where}: heading level must be 1..6, got {level!r}")

    footnote_ids: set[str] = set()
    for note in book.get("footnotes", []):
        note_id = note.get("id", "?")
        if note_id in footnote_ids:
            problems.append(f"footnote {note_id}: duplicate id")
        footnote_ids.add(note_id)
        if note.get("anchor_block") and note["anchor_block"] not in seen:
            problems.append(f"footnote {note_id}: anchor block {note['anchor_block']} not found")

    # A repeated running-head id is the "silently disappeared" shape: merge
    # resolves an id to one piece, so the second one's translation lands on the
    # first and the head it was written for prints in the source language.
    running: set[str] = set()
    for unit_id, _, _, section in ir.iter_running_pieces(book):
        if unit_id in running:
            problems.append(f"running head {unit_id}: duplicate id "
                            f"(section {section.get('index')})")
        running.add(unit_id)

    # Every token, not only the canonical ones. A `tr-01` a translator wrote is
    # legitimate in transit and a defect in a finished book, and a token naming
    # nothing is the same defect spelled differently — but scanning with the
    # canonical pattern could see neither, so the one check whose job is to
    # notice found nothing to report and the marker printed.
    for block in ir.iter_text_blocks(book):
        for side in ("text", "target"):
            value = block.get(side)
            if not value:
                continue
            # Parsed, not scanned, for the reason `footnote_refs` documents: a
            # marker inside a code span is an example of the notation. `tr-NN`
            # is still included, because one surviving into a finished book is
            # exactly the defect this check exists for.
            for ref in ir.footnote_refs(value, include_local=True):
                if ref not in footnote_ids:
                    problems.append(
                        f"block {block['id']} ({side}): unknown footnote ref {ref}")
    problems.extend(validate(book))
    return problems
