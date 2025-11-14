"""Helpers for parse confidence scoring and review flags."""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from django.conf import settings


def confidence_threshold() -> float:
    """Return the global confidence threshold for review flags."""

    value = getattr(settings, "REVIEW_CONFIDENCE_THRESHOLD", 0.6)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.6


def should_flag(parse_confidence: Optional[float], category_confidence: Optional[float]) -> bool:
    """Decide whether a transaction should be marked for review."""

    threshold = confidence_threshold()
    parse_issue = parse_confidence is None or parse_confidence < threshold
    category_issue = (
        category_confidence is not None and category_confidence < threshold
    )
    return parse_issue or category_issue


def score_parse_confidence(
    *,
    amount: Optional[Decimal],
    merchant_name: str,
    transaction_date,
    reference_id: str,
    raw_body: Optional[str],
    card_detected: bool,
) -> float:
    """Heuristic scoring to estimate confidence of the parsed transaction."""

    score = 0.95

    def penalize(condition: bool, penalty: float) -> None:
        nonlocal score
        if condition:
            score -= penalty

    penalize(amount is None or amount <= Decimal("0.00"), 0.35)
    penalize(not merchant_name or len(merchant_name.strip()) < 4, 0.2)
    penalize(not reference_id, 0.2)
    penalize(transaction_date is None, 0.1)
    penalize(not card_detected, 0.05)
    penalize(not raw_body or len((raw_body or "").strip()) < 80, 0.05)

    if amount is not None and amount >= Decimal("5000000"):
        penalize(True, 0.05)

    return float(max(0.05, min(score, 0.99)))
