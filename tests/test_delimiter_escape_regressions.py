"""Renderer-produced escapes must preserve both prose and adjacent formatting."""
from __future__ import annotations

import logging

import pytest
import markup

LOG = logging.getLogger(__name__)


@pytest.mark.parametrize("slashes", range(1, 7))
@pytest.mark.parametrize("style", [(True, False), (False, True), (True, True)])
def test_literal_backslashes_before_emphasis(slashes, style):
    prefix = "C:" + "\\" * slashes
    text = markup.render_markup([(prefix, False, False), ("folder", *style)])
    spans = markup.parse_markup(text)
    assert markup.plain_text(text) == prefix + "folder"
    assert [(s["text"], s["bold"], s["italic"]) for s in spans] == [
        (prefix, False, False), ("folder", *style)
    ]
    assert markup.render_spans(spans) == text
    LOG.debug("Verified escape parity: slashes=%d style=%s", slashes, style)


@pytest.mark.parametrize("literal", ["*", "**", "`", "\\*", "\\`", "a\\b", "a*b`c"])
@pytest.mark.parametrize("style", [(False, False), (True, False), (False, True), (True, True)])
def test_escaped_text_inside_one_styled_run(literal, style):
    text = markup.render_markup([(literal, *style)])
    spans = markup.parse_markup(text)
    assert markup.plain_text(text) == literal
    assert [(s["text"], s["bold"], s["italic"]) for s in spans] == [(literal, *style)]
    assert markup.render_spans(spans) == text


@pytest.mark.parametrize("slashes", range(1, 7))
def test_even_escape_prefix_does_not_hide_verbatim(slashes):
    prefix = "\\" * slashes
    text = markup.escape_markup(prefix) + "`literal*value`"
    assert markup.plain_text(text) == prefix + "literal*value"
    assert markup.verbatim_spans(text) == ["literal*value"]
    assert markup.render_spans(markup.parse_markup(text)) == text


def test_adjacent_verbatim_runs_remain_distinct():
    assert markup.verbatim_spans("`first``second`") == ["first", "second"]


def test_escaped_marker_stays_literal():
    text = markup.escape_markup("**not bold** `not code`")
    assert markup.plain_text(text) == "**not bold** `not code`"
    assert markup.emphasis_signature(text) == (0, 0, 0)


def test_escaped_symbols_do_not_inflate_emphasis_count():
    text = markup.render_markup([("C:\\path*name", True, False)])
    assert markup.emphasis_signature(text) == (1, 0, 0)
    assert markup.plain_text(text) == "C:\\path*name"
