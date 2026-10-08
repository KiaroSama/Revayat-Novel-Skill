"""Bounded native style inheritance and script-applicable emphasis."""
from __future__ import annotations

import unicodedata

import opc


class Unresolved(ValueError):
    """Native properties cannot be established without guessing."""


def on_off(node):
    if node is None:
        return None
    value = node.get(opc.qname('w', 'val'), '1')
    if value in {'1', 'true', 'on'}:
        return True
    if value in {'0', 'false', 'off'}:
        return False
    raise Unresolved('invalid native on/off property')


def _property(owner, path):
    found = owner.findall(path, opc.NS) if owner is not None else []
    if len(found) > 1:
        raise Unresolved('duplicate native property')
    return found[0] if found else None


class Styles:
    def __init__(self, root):
        self.root = root
        self.lookup = {}
        self.defaults = {}
        for node in root.findall('w:style', opc.NS):
            identity = node.get(opc.qname('w', 'styleId'))
            if not identity or identity in self.lookup:
                raise Unresolved('invalid or duplicate native style identity')
            self.lookup[identity] = node
            if on_off_default(node.get(opc.qname('w', 'default'), '0')):
                kind = node.get(opc.qname('w', 'type'))
                if kind in self.defaults:
                    raise Unresolved('duplicate default native style')
                self.defaults[kind] = identity

    def chain(self, identity, kind):
        found, seen = [], set()
        while identity:
            if identity in seen or len(seen) >= 256:
                raise Unresolved('native style inheritance cycle or excessive depth')
            seen.add(identity)
            node = self.lookup.get(identity)
            if node is None or node.get(opc.qname('w', 'type')) != kind:
                raise Unresolved('unresolved native style reference')
            found.append(node)
            base = _property(node, 'w:basedOn')
            identity = base.get(opc.qname('w', 'val')) if base is not None else None
            if base is not None and not identity:
                raise Unresolved('empty native basedOn reference')
        return found

    def selected(self, owner, path, kind):
        selector = _property(owner, path)
        identity = selector.get(opc.qname('w', 'val')) if selector is not None else self.defaults.get(kind)
        if selector is not None and not identity:
            raise Unresolved('empty native style reference')
        return self.chain(identity, kind)

    def paragraph_bidi(self, paragraph):
        chain = self.selected(paragraph, 'w:pPr/w:pStyle', 'paragraph')
        direct = on_off(_property(paragraph, 'w:pPr/w:bidi'))
        if direct is not None:
            return direct
        for style in chain:
            value = on_off(_property(style, 'w:pPr/w:bidi'))
            if value is not None:
                return value
        default = on_off(_property(self.root, 'w:docDefaults/w:pPrDefault/w:pPr/w:bidi'))
        return False if default is None else default

    def run_property(self, run, paragraph, name, *, toggle=False):
        paragraph_chain = self.selected(paragraph, 'w:pPr/w:pStyle', 'paragraph')
        character_chain = self.selected(run, 'w:rPr/w:rStyle', 'character')
        direct = on_off(_property(run, 'w:rPr/w:' + name))
        if direct is not None:
            return direct
        default = on_off(_property(self.root, 'w:docDefaults/w:rPrDefault/w:rPr/w:' + name))
        if toggle:
            value = bool(default)
            for style in [*reversed(paragraph_chain), *reversed(character_chain)]:
                if on_off(_property(style, 'w:rPr/w:' + name)):
                    value = not value
            return value
        for style in [*character_chain, *paragraph_chain]:
            value = on_off(_property(style, 'w:rPr/w:' + name))
            if value is not None:
                return value
        return bool(default)


def on_off_default(value):
    if value in {'1', 'true', 'on'}:
        return True
    if value in {'0', 'false', 'off'}:
        return False
    raise Unresolved('invalid native default style flag')


def styles_for(run):
    part = run.part
    if not hasattr(part, 'styles'):
        part = part.package.main_document_part
    return Styles(part.styles.element)


def emphasis_spans(run, text):
    """Split a mixed source run only when its effective script emphasis differs."""
    styles = styles_for(run)
    owner = run._parent
    if not hasattr(owner, '_p'):
        owner = owner._parent
    paragraph = owner._p
    forced = (styles.run_property(run._r, paragraph, 'cs') or
              styles.run_property(run._r, paragraph, 'rtl'))
    ordinary = (styles.run_property(run._r, paragraph, 'b', toggle=True),
                styles.run_property(run._r, paragraph, 'i', toggle=True))
    complex_style = (styles.run_property(run._r, paragraph, 'bCs', toggle=True),
                     styles.run_property(run._r, paragraph, 'iCs', toggle=True))
    spans = []
    complex_chars = [unicodedata.bidirectional(char) in {'AL', 'R', 'AN'} for char in text]
    surrounding_complex = any(complex_chars) and not any(char.isalpha() and not complex_char
                                                        for char, complex_char in zip(text, complex_chars))
    for char, complex_char in zip(text, complex_chars):
        complex_char = forced or complex_char or surrounding_complex and not char.isalpha()
        bold, italic = complex_style if complex_char else ordinary
        if spans and spans[-1][1:] == (bold, italic):
            spans[-1] = (spans[-1][0] + char, bold, italic)
        else:
            spans.append((char, bold, italic))
    return spans


def run_style(run):
    """Compatibility callback for uniform notes; mixed adapters use spans."""
    spans = emphasis_spans(run, run.text)
    visible = {(bold, italic) for text, bold, italic in spans if text.strip()}
    if len(visible) > 1:
        raise Unresolved('mixed native run requires script-applicable spans')
    return next(iter(visible), (False, False))
