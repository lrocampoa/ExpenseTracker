from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tracker import models, views


class TransactionViewsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="tester", email="user@example.com", password="pass1234")
        self.client.force_login(self.user)
        self.factory = RequestFactory()
        self.category = models.Category.objects.create(code="food", name="Alimentación")
        email_kwargs = {
            "gmail_message_id": "abc123",
            "subject": "Compra",
            "sender": "test@example.com",
            "raw_body": "",
        }
        if hasattr(models.EmailMessage, "user_id"):
            email_kwargs["user"] = self.user
        self.email = models.EmailMessage.objects.create(**email_kwargs)
        transaction_kwargs = {
            "email": self.email,
            "category": self.category,
            "merchant_name": "Test Merchant",
            "amount": Decimal("12.50"),
            "currency_code": "CRC",
            "transaction_date": timezone.now(),
            "reference_id": "ref-1",
            "parse_status": models.Transaction.ParseStatus.PARSED,
        }
        if hasattr(models.Transaction, "user_id"):
            transaction_kwargs["user"] = self.user
        self.transaction = models.Transaction.objects.create(**transaction_kwargs)

    def test_list_view_renders(self):
        url = reverse("tracker:transaction_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Merchant")

    def test_detail_view_inline_update(self):
        url = reverse("tracker:transaction_detail", args=[self.transaction.pk])
        response = self.client.post(
            url,
            {
                "merchant_name": "Nuevo Comercio",
                "description": "actualizado",
                "amount": "25.00",
                "currency_code": "CRC",
                "transaction_date": self.transaction.transaction_date.strftime("%Y-%m-%dT%H:%M"),
                "category": self.category.pk,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.merchant_name, "Nuevo Comercio")

    @override_settings(LLM_CATEGORIZATION_ENABLED=False)
    def test_reparse_action(self):
        html = """
        <table>
          <tr><td>Comercio:</td><td>Tienda TEST</td></tr>
          <tr><td>Monto:</td><td>CRC 1500.00</td></tr>
          <tr><td>Autorización:</td><td>XYZ123</td></tr>
          <tr><td>Fecha:</td><td>Nov 8, 2025, 13:00</td></tr>
          <tr><td>**** **** **** 9999</td></tr>
        </table>
        """
        self.email.raw_body = html
        self.email.save()
        self.transaction.merchant_name = ""
        self.transaction.amount = Decimal("0")
        self.transaction.reference_id = ""
        self.transaction.card_last4 = ""
        self.transaction.save()
        url = reverse("tracker:transaction_detail", args=[self.transaction.pk])
        response = self.client.post(url, {"action": "reparse"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.merchant_name, "Tienda TEST")
        self.assertEqual(self.transaction.reference_id, "XYZ123")

    @override_settings(LLM_CATEGORIZATION_ENABLED=False)
    def test_reparse_deletes_duplicate(self):
        dup_kwargs = {
            "email": self.email,
            "merchant_name": "Otro",
            "amount": Decimal("5"),
            "currency_code": "CRC",
            "reference_id": "XYZ123",
            "parse_status": models.Transaction.ParseStatus.PARSED,
        }
        if hasattr(models.Transaction, "user_id"):
            dup_kwargs["user"] = self.user
        duplicate = models.Transaction.objects.create(**dup_kwargs)
        html = """
        <table>
          <tr><td>Comercio:</td><td>Tienda TEST</td></tr>
          <tr><td>Monto:</td><td>CRC 1500.00</td></tr>
          <tr><td>Autorización:</td><td>XYZ123</td></tr>
          <tr><td>Fecha:</td><td>Nov 8, 2025, 13:00</td></tr>
          <tr><td>**** **** **** 9999</td></tr>
        </table>
        """
        self.email.raw_body = html
        self.email.save()
        self.transaction.reference_id = ""
        self.transaction.save()
        url = reverse("tracker:transaction_detail", args=[self.transaction.pk])
        self.client.post(url, {"action": "reparse"}, follow=True)
        self.assertFalse(
            models.Transaction.objects.filter(pk=duplicate.pk).exists()
        )
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.reference_id, "XYZ123")

    def test_list_view_filter_by_search(self):
        url = reverse("tracker:transaction_list")
        response = self.client.get(url, {"search": "merchant"})
        self.assertContains(response, "Test Merchant")

    def test_detail_view_renders(self):
        url = reverse("tracker:transaction_detail", args=[self.transaction.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Merchant")

    def test_import_page_without_credentials(self):
        url = reverse("tracker:import")
        response = self.client.get(url)
        self.assertContains(response, "Conectar Gmail")

    def test_import_post_without_credentials(self):
        url = reverse("tracker:import")
        response = self.client.post(url, {"years": 1}, follow=True)
        self.assertContains(response, "Necesitas conectar Gmail")

    @override_settings(LLM_CATEGORIZATION_ENABLED=False)
    def test_import_runs_ingestion_and_processing(self):
        models.GmailCredential.objects.create(
            user=self.user,
            user_email=self.user.email,
            token_json={"token": "abc"},
            scopes=["test"],
            is_active=True,
        )
        email = models.EmailMessage.objects.create(
            gmail_message_id="newmsg",
            subject="Notificación",
            sender="notificacion@baccr.com",
            raw_body="<table><tr><td>Comercio:</td><td>Test Store</td></tr><tr><td>Monto:</td><td>CRC 100.00</td></tr><tr><td>Autorización:</td><td>CODE1</td></tr><tr><td>Fecha:</td><td>09/11/2025 10:00</td></tr><tr><td>**** 1234</td></tr></table>",
            user=self.user,
        )
        import_path = reverse("tracker:import")
        with mock.patch(
            "tracker.views.GmailCredentialManager.ensure_credentials", return_value=object()
        ), mock.patch("tracker.views.GmailCredentialManager.build_service"), mock.patch(
            "tracker.views.GmailIngestionService.sync"
        ) as sync_mock:
            sync_mock.return_value.created = 0
            response = self.client.post(import_path, {"years": 1}, follow=True)
        self.assertEqual(response.status_code, 200)
        email.refresh_from_db()
        self.assertIsNotNone(email.processed_at)

    def test_card_list_includes_transaction_last4(self):
        self.transaction.card_last4 = "4321"
        self.transaction.save()
        url = reverse("tracker:cards")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "**** 4321")
        self.assertContains(response, 'name="label"')

    def test_label_card_from_list_creates_record(self):
        self.transaction.card_last4 = "9999"
        self.transaction.save()
        url = reverse("tracker:cards")
        response = self.client.post(
            url,
            {
                "card_id": "",
                "last4": "9999",
                "label": "Mi tarjeta",
                "expense_account": "__new__",
                "new_expense_account": "Gastos Hogar",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        card = models.Card.objects.get(user=self.user, last4="9999")
        self.assertEqual(card.label, "Mi tarjeta")
        self.assertEqual(card.expense_account, "Gastos Hogar")

    def test_label_card_updates_existing_record(self):
        card = models.Card.objects.create(
            user=self.user,
            label="Principal",
            last4="2222",
            expense_account="Casa",
        )
        url = reverse("tracker:cards")
        response = self.client.post(
            url,
            {
                "card_id": card.pk,
                "last4": "2222",
                "label": "Principal editada",
                "expense_account": "__new__",
                "new_expense_account": "Viajes",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        card.refresh_from_db()
        self.assertEqual(card.label, "Principal editada")
        self.assertEqual(card.expense_account, "Viajes")

    def test_label_card_selects_existing_account(self):
        existing = models.Card.objects.create(
            user=self.user,
            label="Secundaria",
            last4="3333",
            expense_account="Compras",
        )
        url = reverse("tracker:cards")
        response = self.client.post(
            url,
            {
                "card_id": existing.pk,
                "last4": "3333",
                "label": "Secundaria",
                "expense_account": "Compras",
                "new_expense_account": "",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        existing.refresh_from_db()
        self.assertEqual(existing.expense_account, "Compras")

    def test_edit_card_view(self):
        card = models.Card.objects.create(
            user=self.user,
            label="Principal",
            last4="2222",
            bank_name="BAC",
        )
        url = reverse("tracker:card_edit", args=[card.pk])
        response = self.client.post(
            url,
            {
                "label": "Principal editada",
                "last4": "2222",
                "bank_name": "Scotia",
                "expense_account": "Viajes",
                "is_active": False,
                "notes": "",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        card.refresh_from_db()
        self.assertEqual(card.label, "Principal editada")
        self.assertEqual(card.expense_account, "Viajes")
        self.assertFalse(card.is_active)

    def test_dashboard_sync_health_flags_stale_states(self):
        models.GmailSyncState.objects.create(
            user=self.user,
            label="primary",
            user_email=self.user.email,
            history_id="abc",
            last_synced_at=timezone.now() - timedelta(hours=2),
            fetched_messages=10,
            retry_count=3,
            query="from:test@example.com",
        )
        request = self.factory.get("/")
        request.user = self.user
        view = views.DashboardView()
        view.request = request
        with override_settings(DASHBOARD_SYNC_STALE_MINUTES=30):
            sync_health = view._build_sync_health()
        self.assertEqual(sync_health["states"][0]["retry_count"], 3)
        self.assertTrue(sync_health["states"][0]["is_stale"])
        self.assertEqual(sync_health["stale_labels"], ["primary"])
