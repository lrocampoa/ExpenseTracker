"""Seed default rules for new users."""

from __future__ import annotations

from tracker import models
from tracker.services import category_seeding

DEFAULT_RULES = [
    {
        "name": "Recibos de internet",
        "match_value": "CLARO",
        "category_code": "utilidades",
    },
    {
        "name": "Pago electricidad",
        "match_value": "CNFL",
        "category_code": "utilidades",
    },
    {
        "name": "Gasolineras BAC",
        "match_value": "SERVICENTRO",
        "category_code": "transporte",
    },
    {
        "name": "Uber",
        "match_value": "UBER",
        "category_code": "transporte",
    },
    {
        "name": "Amazon compras",
        "match_value": "AMAZON",
        "category_code": "ocio",
    },
    {
        "name": "Walmart supermercados",
        "match_value": "WALMART",
        "category_code": "supermercado",
    },
]


def ensure_defaults(user) -> None:
    categories = category_seeding.ensure_defaults(user)
    if models.CategoryRule.objects.filter(user=user).exists():
        return
    for rule_def in DEFAULT_RULES:
        category = categories.get(rule_def["category_code"])
        if not category:
            continue
        models.CategoryRule.objects.get_or_create(
            user=user,
            category=category,
            match_field=models.CategoryRule.MatchField.MERCHANT,
            match_type=models.CategoryRule.MatchType.CONTAINS,
            match_value=rule_def["match_value"],
            defaults={
                "priority": 120,
                "notes": f"Seed {rule_def['name']}",
                "origin": models.CategoryRule.Origin.SEEDED,
            },
        )
