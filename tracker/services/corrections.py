"""Helpers to track manual transaction corrections."""

from __future__ import annotations

from typing import Dict, Iterable, Optional

from django.utils import timezone

from tracker import models
from tracker.services import rule_suggestions

TRACKED_FIELDS: Iterable[str] = (
    "merchant_name",
    "description",
    "amount",
    "currency_code",
    "transaction_date",
    "category_id",
    "subcategory_id",
)


def snapshot_transaction(trx: models.Transaction) -> Dict[str, Optional[object]]:
    return {
        "merchant_name": trx.merchant_name,
        "description": trx.description,
        "amount": trx.amount,
        "currency_code": trx.currency_code,
        "transaction_date": trx.transaction_date,
        "category_id": trx.category_id,
        "subcategory_id": trx.subcategory_id,
    }


def record_manual_correction(
    trx: models.Transaction,
    user,
    before_snapshot: Dict[str, Optional[object]],
) -> Optional[models.TransactionCorrection]:
    after_snapshot = snapshot_transaction(trx)
    changed_fields = [
        field for field in TRACKED_FIELDS if before_snapshot.get(field) != after_snapshot.get(field)
    ]
    if not changed_fields:
        return None
    correction = models.TransactionCorrection.objects.create(
        transaction=trx,
        user=user,
        previous_category_id=before_snapshot.get("category_id"),
        new_category=trx.category,
        previous_subcategory_id=before_snapshot.get("subcategory_id"),
        new_subcategory=trx.subcategory,
        previous_merchant_name=before_snapshot.get("merchant_name") or "",
        new_merchant_name=after_snapshot.get("merchant_name") or "",
        previous_description=before_snapshot.get("description") or "",
        new_description=after_snapshot.get("description") or "",
        previous_amount=before_snapshot.get("amount"),
        new_amount=after_snapshot.get("amount"),
        previous_currency_code=before_snapshot.get("currency_code") or "",
        new_currency_code=after_snapshot.get("currency_code") or "",
        previous_transaction_date=before_snapshot.get("transaction_date"),
        new_transaction_date=after_snapshot.get("transaction_date"),
        changed_fields=changed_fields,
    )
    _mark_transaction_manual(trx, user, correction, changed_fields)
    rule_suggestions.create_from_correction(correction)
    return correction


def _mark_transaction_manual(
    trx: models.Transaction,
    user,
    correction: models.TransactionCorrection,
    changed_fields,
) -> None:
    metadata = trx.metadata or {}
    metadata["manual_override"] = {
        "correction_id": correction.id,
        "user_id": getattr(user, "id", None),
        "at": timezone.now().isoformat(),
        "fields": changed_fields,
    }
    trx.metadata = metadata
    trx.category_source = "manual"
    trx.category_confidence = 1.0
    trx.save(
        update_fields=[
            "metadata",
            "category_source",
            "category_confidence",
            "updated_at",
        ]
    )
