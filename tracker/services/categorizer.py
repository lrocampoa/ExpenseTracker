"""Categorization helpers for transactions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.db import transaction as db_transaction

from tracker import models


@dataclass
class CategorizationResult:
    category: models.Category
    confidence: float
    source: str
    rule_id: Optional[int] = None


class RuleEngine:
    def evaluate(self, trx: models.Transaction) -> Optional[CategorizationResult]:
        rules = (
            models.CategoryRule.objects.select_related("category")
            .filter(is_active=True)
            .order_by("priority", "-confidence", "name")
        )
        for rule in rules:
            if self._matches_rule(rule, trx):
                return CategorizationResult(
                    category=rule.category,
                    confidence=rule.confidence,
                    source=f"rule:{rule.id}",
                    rule_id=rule.id,
                )
        return None

    def _matches_rule(self, rule: models.CategoryRule, trx: models.Transaction) -> bool:
        if rule.card_last4 and trx.card_last4 and rule.card_last4 != trx.card_last4:
            return False
        if rule.min_amount and trx.amount and trx.amount < rule.min_amount:
            return False
        if rule.max_amount and trx.amount and trx.amount > rule.max_amount:
            return False

        if rule.match_type == models.CategoryRule.MatchType.ALWAYS:
            return True

        value = self._resolve_field(rule.match_field, trx)
        if not value:
            return False

        comparison_value = value.lower()
        match_value = (rule.match_value or "").lower()

        if not match_value and rule.match_type != models.CategoryRule.MatchType.ALWAYS:
            return False

        if rule.match_type == models.CategoryRule.MatchType.CONTAINS:
            return match_value in comparison_value
        if rule.match_type == models.CategoryRule.MatchType.STARTS_WITH:
            return comparison_value.startswith(match_value)
        if rule.match_type == models.CategoryRule.MatchType.ENDS_WITH:
            return comparison_value.endswith(match_value)
        if rule.match_type == models.CategoryRule.MatchType.EXACT:
            return comparison_value == match_value
        if rule.match_type == models.CategoryRule.MatchType.REGEX:
            try:
                return re.search(rule.match_value, value, re.IGNORECASE) is not None
            except re.error:
                return False
        return False

    def _resolve_field(self, match_field: str, trx: models.Transaction) -> str:
        if match_field == models.CategoryRule.MatchField.MERCHANT:
            return trx.merchant_name or ""
        if match_field == models.CategoryRule.MatchField.DESCRIPTION:
            return trx.description or ""
        if match_field == models.CategoryRule.MatchField.CARD_LAST4:
            return trx.card_last4 or ""
        if match_field == models.CategoryRule.MatchField.ANY_TEXT:
            return " ".join(filter(None, [trx.merchant_name, trx.description]))
        return ""


def categorize_transaction(trx: models.Transaction, allow_llm: Optional[bool] = None) -> Optional[CategorizationResult]:
    if allow_llm is None:
        allow_llm = settings.LLM_CATEGORIZATION_ENABLED
    engine = RuleEngine()
    result = engine.evaluate(trx)
    if result:
        _apply_result(trx, result)
        return result
    if allow_llm:
        from tracker.services import llm

        llm_result = llm.categorize_with_llm(trx)
        if llm_result:
            _apply_result(trx, llm_result)
            return llm_result
    return None


def _apply_result(trx: models.Transaction, result: CategorizationResult) -> None:
    with db_transaction.atomic():
        trx.category = result.category
        trx.category_confidence = result.confidence
        trx.category_source = result.source
        metadata = trx.metadata or {}
        metadata.setdefault("categorization", {})
        metadata["categorization"].update(
            {
                "rule_id": result.rule_id,
                "source": result.source,
            }
        )
        trx.metadata = metadata
        trx.save(update_fields=[
            "category",
            "category_confidence",
            "category_source",
            "metadata",
            "updated_at",
        ])
