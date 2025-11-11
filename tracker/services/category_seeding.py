"""Default category + subcategory seeding helpers."""

from __future__ import annotations

from decimal import Decimal

from tracker import models

CATEGORY_STRUCTURE = [
    {
        "code": "utilidades",
        "name": "Utilidades",
        "budget": Decimal("250000"),
        "subcategories": [
            {"code": "internet", "name": "Internet", "budget": Decimal("50000")},
            {"code": "agua", "name": "Agua", "budget": Decimal("30000")},
            {"code": "luz", "name": "Electricidad", "budget": Decimal("60000")},
            {"code": "condo", "name": "Cuota condominal", "budget": Decimal("70000")},
            {"code": "alquiler", "name": "Alquiler", "budget": Decimal("120000")},
        ],
    },
    {
        "code": "transporte",
        "name": "Transporte",
        "budget": Decimal("180000"),
        "subcategories": [
            {"code": "gasolina", "name": "Gasolina", "budget": Decimal("80000")},
            {"code": "uber", "name": "Uber / Taxi", "budget": Decimal("40000")},
            {"code": "parqueo", "name": "Parqueo / Peajes", "budget": Decimal("30000")},
            {"code": "mantenimiento", "name": "Mantenimiento", "budget": Decimal("30000")},
        ],
    },
    {
        "code": "supermercado",
        "name": "Supermercado",
        "budget": Decimal("200000"),
        "subcategories": [
            {"code": "despensa", "name": "Despensa", "budget": Decimal("150000")},
            {"code": "limpieza", "name": "Limpieza", "budget": Decimal("30000")},
            {"code": "mascotas", "name": "Mascotas", "budget": Decimal("20000")},
        ],
    },
    {
        "code": "ocio",
        "name": "Ocio & Compras",
        "budget": Decimal("150000"),
        "subcategories": [
            {"code": "restaurantes", "name": "Restaurantes", "budget": Decimal("70000")},
            {"code": "suscripciones", "name": "Suscripciones", "budget": Decimal("20000")},
            {"code": "ropa", "name": "Ropa", "budget": Decimal("30000")},
            {"code": "viajes", "name": "Viajes", "budget": Decimal("30000")},
        ],
    },
]


def ensure_defaults(user):
    categories = {}
    for cat_def in CATEGORY_STRUCTURE:
        category, created = models.Category.objects.get_or_create(
            user=user,
            code=cat_def["code"],
            defaults={
                "name": cat_def["name"],
                "description": cat_def["name"],
                "budget_limit": cat_def["budget"],
                "is_active": True,
            },
        )
        if not created:
            if cat_def["budget"] is not None and category.budget_limit != cat_def["budget"]:
                category.budget_limit = cat_def["budget"]
                category.save(update_fields=["budget_limit", "updated_at"])
        categories[cat_def["code"]] = category
        for sub_def in cat_def.get("subcategories", []):
            subcategory, sub_created = models.Subcategory.objects.get_or_create(
                user=user,
                category=category,
                code=sub_def["code"],
                defaults={
                    "name": sub_def["name"],
                    "budget_limit": sub_def.get("budget"),
                },
            )
            if not sub_created and sub_def.get("budget") and subcategory.budget_limit != sub_def.get("budget"):
                subcategory.budget_limit = sub_def.get("budget")
                subcategory.save(update_fields=["budget_limit", "updated_at"])
    return categories
