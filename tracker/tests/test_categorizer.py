from decimal import Decimal

from django.test import TestCase, override_settings

from tracker import models
from tracker.services.categorizer import categorize_transaction


class CategorizerTests(TestCase):
    def setUp(self):
        self.category_food = models.Category.objects.create(code="food", name="Food")
        self.category_transfer = models.Category.objects.create(code="transfer", name="Transfers")
        models.CategoryRule.objects.create(
            name="Uber rule",
            category=self.category_food,
            match_field=models.CategoryRule.MatchField.MERCHANT,
            match_type=models.CategoryRule.MatchType.CONTAINS,
            match_value="uber",
            priority=10,
            confidence=0.9,
        )
        models.CategoryRule.objects.create(
            name="SINPE regex",
            category=self.category_transfer,
            match_field=models.CategoryRule.MatchField.DESCRIPTION,
            match_type=models.CategoryRule.MatchType.REGEX,
            match_value=r"SINPE",
            priority=20,
            confidence=0.95,
        )

    def test_contains_rule_applied(self):
        email = models.EmailMessage.objects.create(gmail_message_id="cat1")
        trx = models.Transaction.objects.create(
            email=email,
            merchant_name="Uber Eats",
            description="",
            amount=Decimal("5200"),
            currency_code="CRC",
            reference_id="ref1",
            parse_status=models.Transaction.ParseStatus.PARSED,
        )

        result = categorize_transaction(trx)
        self.assertIsNotNone(result)
        trx.refresh_from_db()
        self.assertEqual(trx.category, self.category_food)
        self.assertEqual(trx.category_source, f"rule:{self.category_food.rules.first().id}")

    def test_regex_rule_applied_to_description(self):
        email = models.EmailMessage.objects.create(gmail_message_id="cat2")
        trx = models.Transaction.objects.create(
            email=email,
            merchant_name="Banco",
            description="Transferencia SINPE",
            amount=Decimal("120.00"),
            currency_code="USD",
            reference_id="ref2",
            parse_status=models.Transaction.ParseStatus.PARSED,
        )

        result = categorize_transaction(trx)
        self.assertIsNotNone(result)
        trx.refresh_from_db()
        self.assertEqual(trx.category, self.category_transfer)

    @override_settings(LLM_CATEGORIZATION_ENABLED=False)
    def test_no_rule_no_llm_returns_none(self):
        email = models.EmailMessage.objects.create(gmail_message_id="cat3")
        trx = models.Transaction.objects.create(
            email=email,
            merchant_name="Unknown",
            description="",
            amount=Decimal("10"),
            currency_code="CRC",
            reference_id="ref3",
            parse_status=models.Transaction.ParseStatus.PARSED,
        )

        result = categorize_transaction(trx)
        self.assertIsNone(result)
