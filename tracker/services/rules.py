"""Helpers to promote transactions into deterministic category rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from django.db import transaction

from tracker import models


class RulePromotionError(Exception):
    """Raised when a transaction cannot be promoted to a rule."""


@dataclass
class RulePromotionResult:
    rule: models.CategoryRule
    created: bool


def create_rule_from_transaction(
    trx: models.Transaction,
    user,
    include_card_last4: bool = True,
    origin: str | None = None,
) -> RulePromotionResult:
    merchant = (trx.merchant_name or "").strip()
    if not merchant:
        raise RulePromotionError("La transacción no tiene comercio definido.")
    if not trx.category:
        raise RulePromotionError("Asigna una categoría antes de crear la regla.")
    rule_kwargs = {
        "match_field": models.CategoryRule.MatchField.MERCHANT,
        "match_type": models.CategoryRule.MatchType.CONTAINS,
        "match_value": merchant,
        "category": trx.category,
    }
    if include_card_last4 and trx.card_last4:
        rule_kwargs["card_last4"] = trx.card_last4
    if hasattr(models.CategoryRule, "user_id") and user:
        rule_kwargs["user"] = user
    filters = {
        "category": rule_kwargs["category"],
        "match_field": rule_kwargs["match_field"],
        "match_type": rule_kwargs["match_type"],
        "match_value": rule_kwargs["match_value"],
    }
    if include_card_last4 and trx.card_last4:
        filters["card_last4"] = trx.card_last4
    if hasattr(models.CategoryRule, "user_id") and user:
        filters["user"] = user
    if origin is None:
        origin = models.CategoryRule.Origin.PROMOTED
    with transaction.atomic():
        rule, created = models.CategoryRule.objects.get_or_create(
            **filters,
            defaults={
                "priority": 80,
                "notes": f"Creada desde transacción {trx.id}",
                "origin": origin,
            },
        )
        if created:
            for field, value in rule_kwargs.items():
                setattr(rule, field, value)
            rule.save()
        if not created:
            updated = False
            for field, value in rule_kwargs.items():
                if getattr(rule, field) != value:
                    setattr(rule, field, value)
                    updated = True
            if rule.origin != origin:
                rule.origin = origin
                updated = True
            if updated:
                rule.save()
    return RulePromotionResult(rule=rule, created=created)
