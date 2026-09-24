"""Measured fitting must advance past sparse boundaries without raising limits."""
from __future__ import annotations

import pytest
import segments


@pytest.mark.parametrize("extra_overhead", [3, 6, 9, 15])
def test_fitting_shrinks_to_an_observed_cut_not_an_unused_allowance(extra_overhead):
    """Sparse sentence ends must not spend all attempts on the same partition."""
    text = "彼女は窓を開けた。雨が降っていた。それでも外へ出た。" * 200
    budget = 1346

    def render(group):
        payload = "".join(value for _, _, value in group)
        # Real segments can carry a longer ID and a nonzero source-length field.
        return "x" * (257 + (extra_overhead if payload else 0)) + payload

    units = segments.fit_units([("b00001", "para", text)], render, budget)
    assert len(units) > 1
    assert "".join(value for _, _, value in units) == text
    assert all(len(render([unit])) <= budget for unit in units)
