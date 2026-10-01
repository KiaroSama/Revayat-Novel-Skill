"""Source normalization is a prose transform, not a literal-content rewrite."""
import pytest
import bookir as ir


@pytest.mark.parametrize("literal", ["a  b", "\tx\ty", " a\n  b ", "a\u200db", "a\ufeffb", "a\u00a0b"])
@pytest.mark.parametrize("wrapper", ["", "*", "**", "***"])
def test_protected_content_survives_source_block_and_note_creation(literal, wrapper):
    text = f"  prefix  {wrapper}`{literal}`{wrapper}  tail  "
    clean = ir.normalise_source(text)
    assert ir.verbatim_spans(clean) == [literal]
    assert ir.verbatim_spans(ir.make_block("paragraph", 1, text=text)["text"]) == [literal]
    assert ir.verbatim_spans(ir.make_footnote(1, anchor_block="b00001", text=text)["text"]) == [literal]
    assert clean.startswith("prefix ") and clean.endswith(" tail")
    assert ir.normalise_source(clean) == clean


def test_prose_noise_is_still_cleaned_without_inventing_markup():
    assert ir.normalise_source("  a  b\u200dc\\*d  ") == "a bc\\*d"
    assert ir.normalise_source("lone  ` marker") == "lone ` marker"
