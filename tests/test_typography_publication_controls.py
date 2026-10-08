"""Typography repairs prose without deleting authored edge controls."""
import falint


def test_typography_retains_authored_edges_while_repairing_prose():
    value = "\n\t  يک متن با كاف  \t\n"
    fixed = falint.fix_text(value)
    assert fixed.startswith("\n\t  ")
    assert fixed.endswith("  \t\n")
    assert "یک متن با کاف" in fixed
    assert falint.fix_text(fixed) == fixed
