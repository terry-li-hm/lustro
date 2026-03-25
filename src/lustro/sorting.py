"""Endosome sorting: route scored cargo to one of three fates.

Biological analogy
------------------
Items entering the endosome are sorted by receptor-ligand affinity (score):

  transcytose — high-affinity cargo crosses the cell (score >= threshold_high).
                Forward to the surface; mark visually in the news log.
  store        — moderate-affinity cargo is retained in the lumen (score >= threshold_low).
                Write to the news log; worth keeping in vault.
  degrade      — low-affinity cargo is handed to the lysosome (score < threshold_low).
                Silently dropped; not persisted.
"""

from __future__ import annotations


FATE_TRANSCYTOSE = "transcytose"
FATE_STORE = "store"
FATE_DEGRADE = "degrade"


def _cargo_score(item: dict) -> int:
    """Extract the numeric relevance score from a cargo item (article dict)."""
    raw = item.get("score", 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def sort_by_fate(
    items: list[dict],
    threshold_high: int = 7,
    threshold_low: int = 4,
) -> dict[str, list[dict]]:
    """Sort endosomal cargo into three fate compartments by receptor affinity (score).

    Args:
        items: List of scored article dicts. Each must have a ``score`` key
               (string or int) produced by the relevance-scoring pipeline.
        threshold_high: Minimum score for transcytosis (forward/notify). Default 7.
        threshold_low:  Minimum score for storage (keep in vault). Default 4.
                        Items below this threshold are degraded (dropped).

    Returns:
        Dict with keys ``"transcytose"``, ``"store"``, and ``"degrade"``, each
        mapping to the list of cargo items assigned to that fate compartment.
    """
    compartments: dict[str, list[dict]] = {
        FATE_TRANSCYTOSE: [],
        FATE_STORE: [],
        FATE_DEGRADE: [],
    }

    for cargo in items:
        affinity = _cargo_score(cargo)
        if affinity >= threshold_high:
            compartments[FATE_TRANSCYTOSE].append(cargo)
        elif affinity >= threshold_low:
            compartments[FATE_STORE].append(cargo)
        else:
            # Hand to lysosome: cargo is silently degraded, not persisted
            compartments[FATE_DEGRADE].append(cargo)

    return compartments


def filter_for_log(
    items: list[dict],
    threshold_high: int = 7,
    threshold_low: int = 4,
) -> list[dict]:
    """Return only cargo that survives degradation (transcytose + store fates).

    Convenience wrapper used by the fetch pipeline to strip low-signal items
    before handing the remainder to format_markdown / append_to_log.

    Args:
        items: Scored article dicts from a single source.
        threshold_high: Score floor for transcytosis.
        threshold_low:  Score floor for storage; items below are degraded.

    Returns:
        Items with fate ``"transcytose"`` or ``"store"``, in original order.
    """
    compartments = sort_by_fate(items, threshold_high=threshold_high, threshold_low=threshold_low)
    # Preserve original ordering: transcytose items first, then store items
    return compartments[FATE_TRANSCYTOSE] + compartments[FATE_STORE]
