"""Helpers for creating and resolving rule suggestions."""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from tracker import models
from tracker.services import rules as rule_service


class SuggestionError(Exception):
    pass


@dataclass
class SuggestionResult:
    suggestion: models.RuleSuggestion
    created: bool


def create_from_correction(correction: models.TransactionCorrection) -> SuggestionResult | None:
    trx = correction.transaction
    if not trx or not trx.category or not trx.merchant_name:
        return None
    defaults = {
        "transaction": trx,
        "correction": correction,
        "card_last4": trx.card_last4 or "",
    }
    if hasattr(models.RuleSuggestion, "user_id") and correction.user_id:
        defaults["user_id"] = correction.user_id
    suggestion, created = models.RuleSuggestion.objects.get_or_create(
        user=getattr(correction, "user", None),
        merchant_name=trx.merchant_name,
        category=trx.category,
        card_last4=trx.card_last4 or "",
        status=models.RuleSuggestion.Status.PENDING,
        defaults=defaults,
    )
    return SuggestionResult(suggestion=suggestion, created=created)


def apply_suggestion(suggestion: models.RuleSuggestion) -> models.CategoryRule:
    if suggestion.status != models.RuleSuggestion.Status.PENDING:
        raise SuggestionError("La sugerencia ya fue procesada.")
    base_trx = suggestion.transaction or (suggestion.correction.transaction if suggestion.correction else None)
    if not base_trx:
        raise SuggestionError("No se encontró la transacción para esta sugerencia.")
    original_values = (
        base_trx.merchant_name,
        base_trx.category,
        base_trx.card_last4,
    )
    base_trx.merchant_name = suggestion.merchant_name
    base_trx.category = suggestion.category
    if suggestion.card_last4:
        base_trx.card_last4 = suggestion.card_last4
    result = rule_service.create_rule_from_transaction(
        base_trx,
        suggestion.user,
        include_card_last4=bool(suggestion.card_last4),
        origin=models.CategoryRule.Origin.SUGGESTED,
    )
    (
        base_trx.merchant_name,
        base_trx.category,
        base_trx.card_last4,
    ) = original_values
    suggestion.status = models.RuleSuggestion.Status.ACCEPTED
    suggestion.reason = f"Regla {result.rule.id}"
    suggestion.save(update_fields=["status", "reason", "updated_at"])
    return result.rule


def reject_suggestion(suggestion: models.RuleSuggestion, reason: str = "") -> None:
    if suggestion.status != models.RuleSuggestion.Status.PENDING:
        raise SuggestionError("La sugerencia ya fue procesada.")
    suggestion.status = models.RuleSuggestion.Status.REJECTED
    suggestion.reason = reason[:250]
    suggestion.save(update_fields=["status", "reason", "updated_at"])
