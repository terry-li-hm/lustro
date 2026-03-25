"""Tests for endosome sorting: cargo fate assignment and log filtering."""

from __future__ import annotations

import pytest

from lustro.sorting import (
    FATE_DEGRADE,
    FATE_STORE,
    FATE_TRANSCYTOSE,
    filter_for_log,
    sort_by_fate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cargo(score) -> dict:
    """Build a minimal article dict with the given score (int or str)."""
    return {"title": f"Article score={score}", "score": str(score), "link": ""}


# ---------------------------------------------------------------------------
# sort_by_fate
# ---------------------------------------------------------------------------

class TestSortByFate:
    def test_transcytose_fate_at_threshold_high(self):
        cargo = [_cargo(7)]
        compartments = sort_by_fate(cargo)
        assert compartments[FATE_TRANSCYTOSE] == cargo
        assert compartments[FATE_STORE] == []
        assert compartments[FATE_DEGRADE] == []

    def test_transcytose_fate_above_threshold_high(self):
        cargo = [_cargo(10)]
        compartments = sort_by_fate(cargo)
        assert compartments[FATE_TRANSCYTOSE] == cargo

    def test_store_fate_at_threshold_low(self):
        cargo = [_cargo(4)]
        compartments = sort_by_fate(cargo)
        assert compartments[FATE_STORE] == cargo
        assert compartments[FATE_TRANSCYTOSE] == []
        assert compartments[FATE_DEGRADE] == []

    def test_store_fate_between_thresholds(self):
        cargo = [_cargo(5), _cargo(6)]
        compartments = sort_by_fate(cargo)
        assert len(compartments[FATE_STORE]) == 2
        assert compartments[FATE_TRANSCYTOSE] == []
        assert compartments[FATE_DEGRADE] == []

    def test_degrade_fate_below_threshold_low(self):
        cargo = [_cargo(3)]
        compartments = sort_by_fate(cargo)
        assert compartments[FATE_DEGRADE] == cargo
        assert compartments[FATE_TRANSCYTOSE] == []
        assert compartments[FATE_STORE] == []

    def test_degrade_fate_at_score_one(self):
        cargo = [_cargo(1)]
        compartments = sort_by_fate(cargo)
        assert compartments[FATE_DEGRADE] == cargo

    def test_mixed_cargo_all_three_fates(self):
        high = _cargo(8)
        mid = _cargo(5)
        low = _cargo(2)
        compartments = sort_by_fate([high, mid, low])
        assert compartments[FATE_TRANSCYTOSE] == [high]
        assert compartments[FATE_STORE] == [mid]
        assert compartments[FATE_DEGRADE] == [low]

    def test_empty_cargo(self):
        compartments = sort_by_fate([])
        assert compartments[FATE_TRANSCYTOSE] == []
        assert compartments[FATE_STORE] == []
        assert compartments[FATE_DEGRADE] == []

    def test_all_keys_always_present(self):
        compartments = sort_by_fate([_cargo(9)])
        assert set(compartments.keys()) == {FATE_TRANSCYTOSE, FATE_STORE, FATE_DEGRADE}

    def test_score_as_integer(self):
        """Score may be stored as int rather than string."""
        cargo = {"title": "int score", "score": 8, "link": ""}
        compartments = sort_by_fate([cargo])
        assert compartments[FATE_TRANSCYTOSE] == [cargo]

    def test_score_missing_defaults_to_degrade(self):
        """Items with no score key should degrade (treated as score 0)."""
        cargo = {"title": "no score", "link": ""}
        compartments = sort_by_fate([cargo])
        assert compartments[FATE_DEGRADE] == [cargo]

    def test_score_invalid_string_defaults_to_degrade(self):
        cargo = {"title": "bad score", "score": "not-a-number", "link": ""}
        compartments = sort_by_fate([cargo])
        assert compartments[FATE_DEGRADE] == [cargo]

    def test_custom_thresholds(self):
        """Custom thresholds override the defaults."""
        cargo = [_cargo(6), _cargo(3), _cargo(1)]
        compartments = sort_by_fate(cargo, threshold_high=6, threshold_low=3)
        assert compartments[FATE_TRANSCYTOSE] == [_cargo(6)]
        assert compartments[FATE_STORE] == [_cargo(3)]
        assert compartments[FATE_DEGRADE] == [_cargo(1)]

    def test_boundary_one_below_threshold_low(self):
        cargo = [_cargo(3)]  # one below default threshold_low=4
        compartments = sort_by_fate(cargo)
        assert compartments[FATE_DEGRADE] == cargo


# ---------------------------------------------------------------------------
# filter_for_log
# ---------------------------------------------------------------------------

class TestFilterForLog:
    def test_drops_degrade_cargo(self):
        items = [_cargo(8), _cargo(5), _cargo(2)]
        survivors = filter_for_log(items)
        assert len(survivors) == 2
        scores = [int(s["score"]) for s in survivors]
        assert 2 not in scores

    def test_transcytose_items_come_first(self):
        """Transcytose items must precede store items in the returned list."""
        store = _cargo(5)
        transcytose = _cargo(9)
        survivors = filter_for_log([store, transcytose])
        assert survivors[0] == transcytose
        assert survivors[1] == store

    def test_all_degrade_returns_empty(self):
        items = [_cargo(1), _cargo(2), _cargo(3)]
        assert filter_for_log(items) == []

    def test_all_survive(self):
        items = [_cargo(7), _cargo(8), _cargo(5)]
        assert len(filter_for_log(items)) == 3

    def test_empty_input(self):
        assert filter_for_log([]) == []


# ---------------------------------------------------------------------------
# Integration: [★] marker still appears in log output for transcytose cargo
# ---------------------------------------------------------------------------

class TestTranscytoseMarkerInLog:
    def test_star_marker_for_transcytose_score(self):
        """format_markdown should still emit [*] for items that survive with score>=7."""
        from lustro.log import format_markdown

        results = {
            "TestSource": [
                {
                    "title": "High signal",
                    "date": "2026-03-25",
                    "summary": "Very relevant",
                    "link": "https://example.com/1",
                    "score": "8",
                    "banking_angle": "N/A",
                }
            ]
        }
        md = format_markdown(results, "2026-03-25")
        assert "[★]" in md

    def test_no_star_marker_for_store_score(self):
        """Store-fate items (score 4-6) should not get the [*] marker."""
        from lustro.log import format_markdown

        results = {
            "TestSource": [
                {
                    "title": "Moderate signal",
                    "date": "2026-03-25",
                    "summary": "Somewhat relevant",
                    "link": "https://example.com/2",
                    "score": "5",
                    "banking_angle": "N/A",
                }
            ]
        }
        md = format_markdown(results, "2026-03-25")
        assert "[★]" not in md
