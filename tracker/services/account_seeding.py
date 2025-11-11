"""Default expense account helpers."""

from __future__ import annotations

from tracker import models

DEFAULT_ACCOUNTS = ["Personal", "Familiar", "Ahorros"]


def ensure_default_accounts(user):
    if not user:
        return
    for name in DEFAULT_ACCOUNTS:
        models.ExpenseAccount.objects.get_or_create(
            user=user,
            name=name,
            defaults={"is_default": True},
        )


def ensure_account(user, name):
    if not user or not name:
        return None
    name = name.strip()
    if not name:
        return None
    account, _ = models.ExpenseAccount.objects.get_or_create(
        user=user,
        name=name,
        defaults={"is_default": False},
    )
    return account
