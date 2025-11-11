"""Parsing utilities for Bac Credomatic notification emails."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, Optional

from django.utils import timezone

from bs4 import BeautifulSoup

from tracker import models
from tracker.services.categorizer import categorize_transaction

CARD_LAST4_REGEXES = [
    re.compile(r"\*{2,}\s*(?P<card>\d{4})"),
    re.compile(r"terminaci[oó]n\s+(?P<card>\d{4})", re.IGNORECASE),
    re.compile(r"tarjeta\s+(?:terminada\s+en\s+)?(?P<card>\d{4})", re.IGNORECASE),
]

AMOUNT_REGEXES = [
    re.compile(
        r"monto:?|\bpor\b", re.IGNORECASE
    ),  # placeholder to trigger label_map usage first
]

DATE_FORMATS = [
    "%d/%m/%Y %H:%M",
    "%d-%m-%Y %H:%M",
    "%b %d, %Y, %H:%M",
    "%b %d, %Y %H:%M",
]

REFERENCE_REGEXES = [
    re.compile(r"Referencia:?\s*(?P<ref>[\w-]+)", re.IGNORECASE),
    re.compile(r"Autorizaci[oó]n:?\s*(?P<ref>[\w-]+)", re.IGNORECASE),
    re.compile(r"N[úu]mero\s+de\s+referencia:?\s*(?P<ref>[\w-]+)", re.IGNORECASE),
]

MERCHANT_REGEXES = [
    re.compile(
        r"Comercio:?\s*(?P<merchant>[A-ZÁÉÍÓÚÑ0-9 /'.-]+)", re.IGNORECASE
    ),
    re.compile(
        r"en\s+(?P<merchant>[A-ZÁÉÍÓÚÑ0-9 /'.-]+?)(?:\.|,|\n|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"hacia\s+(?P<merchant>[A-ZÁÉÍÓÚÑ0-9 /'.-]+?)(?:\.|,|\n|$)",
        re.IGNORECASE,
    ),
]

SPANISH_MONTHS = {
    "ene": "jan",
    "feb": "feb",
    "mar": "mar",
    "abr": "apr",
    "may": "may",
    "jun": "jun",
    "jul": "jul",
    "ago": "aug",
    "sept": "sep",
    "set": "sep",
    "oct": "oct",
    "nov": "nov",
    "dic": "dec",
}


@dataclass
class ParsedTransaction:
    amount: Decimal
    currency: str
    card_last4: str
    merchant_name: str
    transaction_date: dt.datetime
    reference_id: str


class BacParser:
    def parse(self, email: models.EmailMessage) -> Optional[ParsedTransaction]:
        soup = BeautifulSoup(email.raw_body or "", "html.parser")
        label_map = self._build_label_map(soup)
        body = "\n".join(
            filter(None, [email.subject, email.snippet, soup.get_text(" ", strip=True)])
        ).strip()

        card_last4 = self._extract_card_last4(label_map, body)
        reference = (
            label_map.get("referencia")
            or label_map.get("autorización")
            or label_map.get("autorizacion")
            or self._extract_first_match(body, REFERENCE_REGEXES, "ref")
        )

        amount, currency = self._extract_amount(label_map, body)
        merchant = (
            label_map.get("comercio")
            or label_map.get("comercio favorito")
            or self._extract_first_match(body, MERCHANT_REGEXES, "merchant")
            or ""
        )
        merchant = merchant.strip(" .")
        transaction_date = self._extract_date(label_map, body, email)

        if not card_last4 or not reference:
            return None

        return ParsedTransaction(
            amount=amount,
            currency=currency,
            card_last4=card_last4,
            merchant_name=merchant,
            transaction_date=transaction_date,
            reference_id=reference,
        )

    def _build_label_map(self, soup: BeautifulSoup) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for row in soup.find_all("tr"):
            texts = [text.strip() for text in row.stripped_strings if text.strip()]
            if len(texts) >= 2:
                label = texts[0].rstrip(":").lower()
                value = texts[1]
                if label not in mapping:
                    mapping[label] = value
        return mapping

    def _extract_first_match(self, body: str, patterns, group: str) -> str:
        for pattern in patterns:
            match = pattern.search(body)
            if match and match.group(group):
                return match.group(group)
        return ""

    def _extract_amount(self, label_map: Dict[str, str], body: str) -> tuple[Decimal, str]:
        label_value = (
            label_map.get("monto")
            or label_map.get("monto total")
            or label_map.get("monto a pagar")
        )
        if label_value:
            parsed = self._parse_amount_text(label_value)
            if parsed:
                return parsed

        amount_match = re.search(
            r"(₡|\$|CRC|USD)\s?([\d.,]+)", body, flags=re.IGNORECASE
        )
        if amount_match:
            symbol_or_code = amount_match.group(1)
            amount_raw = amount_match.group(2)
            currency = (
                self._currency_from_symbol(symbol_or_code)
                or self._currency_from_code(symbol_or_code.upper())
                or "CRC"
            )
            try:
                amount = Decimal(self._normalize_amount(amount_raw))
                return amount.quantize(Decimal("0.01")), currency
            except (InvalidOperation, TypeError):
                pass

        return Decimal("0.00"), "CRC"

    def _parse_amount_text(self, text: str) -> Optional[tuple[Decimal, str]]:
        currency = "CRC"
        upper = text.upper()
        if "USD" in upper or "US$" in upper or "$" in text:
            currency = "USD"
        if "CRC" in upper or "₡" in text:
            currency = "CRC"

        digits_match = re.search(r"([\d.,]+)", text)
        if not digits_match:
            return None
        try:
            amount = Decimal(self._normalize_amount(digits_match.group(1)))
        except (InvalidOperation, TypeError):
            return None
        return amount.quantize(Decimal("0.01")), currency

    def _normalize_amount(self, amount: str) -> str:
        value = (amount or "").replace(" ", "")
        if "," in value and "." in value:
            if value.rfind(",") > value.rfind("."):
                value = value.replace(".", "").replace(",", ".")
            else:
                value = value.replace(",", "")
        elif "," in value:
            value = value.replace(",", ".")
        return value

    def _currency_from_symbol(self, symbol: str) -> Optional[str]:
        if symbol == "₡":
            return "CRC"
        if symbol == "$":
            return "USD"
        return None

    def _currency_from_code(self, code: str) -> Optional[str]:
        if code in {"USD", "US$"}:
            return "USD"
        if code == "CRC":
            return "CRC"
        return None

    def _extract_date(
        self, label_map: Dict[str, str], body: str, email: models.EmailMessage
    ) -> dt.datetime:
        candidates = [
            label_map.get("fecha"),
            self._extract_first_match(
                body, [re.compile(r"Fecha:?\s*(?P<date>.+)", re.IGNORECASE)], "date"
            ),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            parsed = self._parse_date_string(candidate)
            if parsed:
                return timezone.make_aware(parsed)
        return email.internal_date or timezone.now()

    def _parse_date_string(self, value: str) -> Optional[dt.datetime]:
        cleaned = value.strip()
        lowered = cleaned.lower()
        for es, en in SPANISH_MONTHS.items():
            lowered = re.sub(rf"\b{es}\b", en, lowered)
        normalized = lowered.title()
        for fmt in DATE_FORMATS:
            try:
                return dt.datetime.strptime(normalized, fmt)
            except ValueError:
                continue
        return None

    def _extract_card_last4(self, label_map: Dict[str, str], body: str) -> str:
        potential_values = [
            label_map.get("terminación"),
            label_map.get("terminacion"),
            label_map.get("tarjeta"),
        ]
        for value in potential_values:
            if not value:
                continue
            digits = re.search(r"(\d{4})", value)
            if digits:
                return digits.group(1)
        return self._extract_first_match(body, CARD_LAST4_REGEXES, "card")


def create_transaction_from_email(
    email: models.EmailMessage, existing_transaction: Optional[models.Transaction] = None
) -> Optional[models.Transaction]:
    parser = BacParser()
    parsed = parser.parse(email)
    if not parsed:
        email.parse_attempts += 1
        email.save(update_fields=["parse_attempts", "updated_at"])
        return None

    card = models.Card.objects.filter(last4=parsed.card_last4).first()

    if existing_transaction:
        transaction = existing_transaction
        if parsed.reference_id:
            models.Transaction.objects.filter(
                email=email, reference_id=parsed.reference_id
            ).exclude(pk=transaction.pk).delete()
        transaction.reference_id = parsed.reference_id
        transaction.amount = parsed.amount
        transaction.currency_code = parsed.currency
        transaction.card_last4 = parsed.card_last4
        transaction.merchant_name = parsed.merchant_name
        transaction.transaction_date = parsed.transaction_date
        transaction.parse_status = models.Transaction.ParseStatus.PARSED
        if card:
            transaction.card = card
        if hasattr(transaction, "user_id") and not transaction.user and getattr(email, "user", None):
            transaction.user = email.user
        transaction.save(
            update_fields=[
                "reference_id",
                "amount",
                "currency_code",
                "card_last4",
                "merchant_name",
                "transaction_date",
                "parse_status",
                "card",
                "user",
                "updated_at",
            ]
        )
    else:
        transaction, _ = models.Transaction.objects.update_or_create(
            email=email,
            reference_id=parsed.reference_id,
            defaults={
                "amount": parsed.amount,
                "currency_code": parsed.currency,
                "card_last4": parsed.card_last4,
                "merchant_name": parsed.merchant_name,
                "transaction_date": parsed.transaction_date,
                "parse_status": models.Transaction.ParseStatus.PARSED,
                "user": getattr(email, "user", None),
            },
        )

        if card and transaction.card_id != card.id:
            transaction.card = card
            transaction.save(update_fields=["card"])

    email.processed_at = timezone.now()
    email.parse_attempts += 1
    email.save(update_fields=["processed_at", "parse_attempts", "updated_at"])

    if hasattr(transaction, "user_id") and not transaction.user and getattr(email, "user", None):
        transaction.user = email.user
        transaction.save(update_fields=["user", "updated_at"])

    categorize_transaction(transaction)

    return transaction
