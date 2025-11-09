from decimal import Decimal
from pathlib import Path

from django.test import TestCase
from django.utils import timezone

from tracker import models
from tracker.services.parser import create_transaction_from_email

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class ParserTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def test_parse_credit_card_email(self):
        email = models.EmailMessage.objects.create(
            gmail_message_id="cc",
            subject="Notificación de transacción",
            raw_body=load_fixture("bac_notificacion_credit_card.html"),
            internal_date=self.now,
        )

        transaction = create_transaction_from_email(email)

        self.assertIsNotNone(transaction)
        self.assertEqual(transaction.reference_id, "987654321")
        self.assertEqual(transaction.card_last4, "1234")
        self.assertEqual(transaction.amount, Decimal("15320.50"))
        self.assertEqual(transaction.currency_code, "CRC")
        self.assertEqual(transaction.merchant_name, "FARMACIA LA BUENA")

    def test_parse_credit_card_email_new_template(self):
        email = models.EmailMessage.objects.create(
            gmail_message_id="cc2",
            subject="Notificación de transacción",
            raw_body=load_fixture("bac_notificacion_credit_card_v2.html"),
            internal_date=self.now,
        )

        transaction = create_transaction_from_email(email)

        self.assertIsNotNone(transaction)
        self.assertEqual(transaction.reference_id, "AUTH1234")
        self.assertEqual(transaction.card_last4, "3517")
        self.assertEqual(transaction.amount, Decimal("26400.00"))
        self.assertEqual(transaction.currency_code, "CRC")
        self.assertEqual(transaction.merchant_name, "BO BAR MIXOLOGY")

    def test_parse_sinpe_email(self):
        email = models.EmailMessage.objects.create(
            gmail_message_id="sinpe",
            subject="Notificación SINPE",
            raw_body=load_fixture("bac_notificacion_sinpe.html"),
            internal_date=self.now,
        )

        transaction = create_transaction_from_email(email)

        self.assertIsNotNone(transaction)
        self.assertEqual(transaction.reference_id, "SINPE123")
        self.assertEqual(transaction.card_last4, "9876")
        self.assertEqual(transaction.amount, Decimal("120.00"))
        self.assertEqual(transaction.currency_code, "USD")
        self.assertEqual(transaction.merchant_name, "Juan Perez")

    def test_parse_requires_reference_and_card(self):
        email = models.EmailMessage.objects.create(
            gmail_message_id="missing",
            subject="Compra",
            raw_body="Compra sin referencia",
        )

        transaction = create_transaction_from_email(email)
        self.assertIsNone(transaction)
