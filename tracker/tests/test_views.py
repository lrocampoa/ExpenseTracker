from datetime import datetime, timedelta, timezone as dt_timezone
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
        category_kwargs = {"code": "food", "defaults": {"name": "Alimentación"}}
        if hasattr(models.Category, "user_id"):
            category_kwargs["user"] = self.user
        self.category, _ = models.Category.objects.get_or_create(**category_kwargs)
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

    @mock.patch("tracker.views.parser_service.create_transaction_from_email")
    def test_reprocess_action_invokes_parser_and_skips_manual(self, mock_reprocess):
        email_kwargs = {
            "gmail_message_id": "abc124",
            "subject": "Compra 2",
            "sender": "test@example.com",
            "raw_body": "",
        }
        if hasattr(models.EmailMessage, "user_id"):
            email_kwargs["user"] = self.user
        second_email = models.EmailMessage.objects.create(**email_kwargs)
        manual_transaction_kwargs = {
            "email": second_email,
            "merchant_name": "Manual",
            "amount": Decimal("5.00"),
            "currency_code": "CRC",
            "transaction_date": timezone.now(),
            "reference_id": "ref-2",
            "metadata": {"manual_override": {"fields": ["category"]}},
        }
        if hasattr(models.Transaction, "user_id"):
            manual_transaction_kwargs["user"] = self.user
        models.Transaction.objects.create(**manual_transaction_kwargs)
        url = reverse("tracker:transaction_list")
        response = self.client.post(url, {"action": "reprocess"}, follow=True)
        self.assertEqual(response.status_code, 200)
        mock_reprocess.assert_called_once_with(self.transaction.email, existing_transaction=self.transaction)
        self.assertContains(response, "Reprocesadas 1 de 2 transacciones")
        self.assertContains(response, "se omitieron por tener ajustes manuales")

    @mock.patch("tracker.views.parser_service.create_transaction_from_email")
    def test_reprocess_action_preserves_filters_on_redirect(self, mock_reprocess):
        url = reverse("tracker:transaction_list")
        response = self.client.post(url, {"action": "reprocess", "search": "Test"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("search=Test", response["Location"])
        mock_reprocess.assert_called_once()

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
        corrections = models.TransactionCorrection.objects.filter(transaction=self.transaction)
        self.assertEqual(corrections.count(), 1)
        correction = corrections.first()
        self.assertEqual(correction.previous_merchant_name, "Test Merchant")
        self.assertEqual(correction.new_merchant_name, "Nuevo Comercio")
        self.assertEqual(correction.previous_category, self.category)
        self.assertEqual(correction.new_category, self.category)
        self.assertIn("manual_override", self.transaction.metadata)
        self.assertEqual(self.transaction.category_source, "manual")
        self.assertEqual(self.transaction.category_confidence, 1.0)
        self.assertTrue(
            models.RuleSuggestion.objects.filter(
                user=self.user, merchant_name="Nuevo Comercio", status="pending"
            ).exists()
        )

    def test_detail_view_no_change_does_not_create_correction(self):
        url = reverse("tracker:transaction_detail", args=[self.transaction.pk])
        self.transaction.transaction_date = self.transaction.transaction_date.replace(second=0, microsecond=0)
        self.transaction.save(update_fields=["transaction_date"])
        response = self.client.post(
            url,
            {
                "merchant_name": "Test Merchant",
                "description": self.transaction.description,
                "amount": "12.50",
                "currency_code": "CRC",
                "transaction_date": self.transaction.transaction_date.strftime("%Y-%m-%dT%H:%M"),
                "category": self.category.pk,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            models.TransactionCorrection.objects.filter(transaction=self.transaction).exists()
        )

    def test_dashboard_manual_corrections_context(self):
        correction = models.TransactionCorrection.objects.create(
            transaction=self.transaction,
            user=self.user,
            previous_category=self.category,
            new_category=self.category,
            previous_merchant_name="Viejo",
            new_merchant_name="Nuevo",
            changed_fields=["merchant_name"],
        )
        url = reverse("tracker:dashboard")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Correcciones manuales")
        context = response.context
        self.assertIn("manual_review", context)
        review = context["manual_review"]
        self.assertEqual(review["count"], 1)
        self.assertEqual(review["recent"][0], correction)

    def test_dashboard_expense_accounts_context(self):
        card = models.Card.objects.create(
            user=self.user,
            label="Tarjeta Azul",
            last4="1111",
            expense_account="Viajes",
        )
        now = timezone.now()
        models.Transaction.objects.create(
            email=self.email,
            user=self.user,
            card=card,
            amount=Decimal("200.00"),
            currency_code="CRC",
            merchant_name="Viajes CR",
            transaction_date=now - timedelta(days=5),
            reference_id="ref-current",
            card_last4="1111",
        )
        models.Transaction.objects.create(
            email=self.email,
            user=self.user,
            card=card,
            amount=Decimal("50.00"),
            currency_code="CRC",
            merchant_name="Viajes CR",
            transaction_date=now - timedelta(days=35),
            reference_id="ref-previous",
            card_last4="1111",
        )
        response = self.client.get(reverse("tracker:dashboard"))
        self.assertEqual(response.status_code, 200)
        expense_accounts = response.context["expense_accounts"]
        self.assertTrue(expense_accounts["has_data"])
        row = expense_accounts["rows"][0]
        self.assertEqual(row["label"], "Viajes")
        self.assertEqual(row["total"], Decimal("200.00"))
        self.assertEqual(row["previous_total"], Decimal("50.00"))
        self.assertGreater(row["bar_pct"], 0)
        self.assertEqual(row["segments"][0]["last4"], "1111")

    def test_dashboard_expense_filter_limits_totals(self):
        self.category.budget_limit = Decimal("500.00")
        self.category.save(update_fields=["budget_limit"])
        viajes_card = models.Card.objects.create(
            user=self.user, label="Viajes", last4="2222", expense_account="Viajes"
        )
        hogar_card = models.Card.objects.create(
            user=self.user, label="Hogar", last4="3333", expense_account="Casa"
        )
        now = timezone.now()
        models.Transaction.objects.create(
            email=self.email,
            user=self.user,
            card=viajes_card,
            category=self.category,
            amount=Decimal("120.00"),
            currency_code="CRC",
            merchant_name="Hotel",
            transaction_date=now - timedelta(days=3),
            reference_id="viajes-current",
            card_last4="2222",
        )
        models.Transaction.objects.create(
            email=self.email,
            user=self.user,
            card=hogar_card,
            category=self.category,
            amount=Decimal("80.00"),
            currency_code="CRC",
            merchant_name="Compras Casa",
            transaction_date=now - timedelta(days=4),
            reference_id="hogar-current",
            card_last4="3333",
        )
        url = reverse("tracker:dashboard") + "?expense_account=Viajes"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        expense_filter = response.context["expense_filter"]
        self.assertTrue(expense_filter["is_active"])
        self.assertEqual(expense_filter["label"], "Viajes")
        hero = response.context["hero"]
        self.assertEqual(hero["total_spend"], Decimal("120.00"))
        category_rows = response.context["category_insights"]
        self.assertEqual(category_rows[0]["total"], Decimal("120.00"))
        self.assertEqual(category_rows[0]["budget_remaining"], Decimal("380.00"))
        self.assertContains(response, "Presupuesto global")

    def test_dashboard_category_budget_chart_context(self):
        other_category = models.Category.objects.create(
            user=self.user,
            code="transporte-extra",
            name="Transporte",
            budget_limit=Decimal("200.00"),
        )
        self.category.budget_limit = Decimal("100.00")
        self.category.save(update_fields=["budget_limit"])
        now = timezone.now()
        for cat, amount in ((self.category, Decimal("80.00")), (other_category, Decimal("150.00"))):
            models.Transaction.objects.create(
                email=self.email,
                user=self.user,
                category=cat,
                amount=amount,
                currency_code="CRC",
                transaction_date=now - timedelta(days=2),
                reference_id=f"ref-{cat.code}",
            )
        response = self.client.get(reverse("tracker:dashboard"))
        self.assertEqual(response.status_code, 200)
        chart = response.context["category_budget_chart"]
        self.assertEqual(len(chart), 2)
        self.assertGreater(chart[0]["used_pct"], chart[1]["used_pct"])

    def test_dashboard_spend_control_requires_budget(self):
        response = self.client.get(reverse("tracker:dashboard"))
        self.assertEqual(response.status_code, 200)
        control = response.context["spend_control"]
        self.assertFalse(control["has_budget"])
        self.assertIsNone(control["daily_allowance"])
        self.assertIsNone(control["status"])

    def test_dashboard_spend_control_with_budget(self):
        self.category.budget_limit = Decimal("500.00")
        self.category.save(update_fields=["budget_limit"])
        now = timezone.now()
        models.Transaction.objects.create(
            email=self.email,
            user=self.user,
            category=self.category,
            amount=Decimal("250.00"),
            currency_code="CRC",
            transaction_date=now - timedelta(days=3),
            reference_id="control-budget",
        )
        response = self.client.get(reverse("tracker:dashboard"))
        self.assertEqual(response.status_code, 200)
        control = response.context["spend_control"]
        self.assertTrue(control["has_budget"])
        self.assertEqual(control["total_budget"], Decimal("500.00"))
        self.assertEqual(control["spent"], response.context["hero"]["total_spend"])
        self.assertEqual(control["status"], "on_track")
        self.assertLess(control["projected_total"], control["total_budget"])

    @mock.patch("tracker.views.timezone.now")
    def test_dashboard_spend_control_days_remaining_for_month(self, mock_now):
        fixed_now = datetime(2024, 5, 10, tzinfo=dt_timezone.utc)
        mock_now.return_value = fixed_now
        self.category.budget_limit = Decimal("210.00")
        self.category.save(update_fields=["budget_limit"])
        self.transaction.transaction_date = fixed_now - timedelta(days=60)
        self.transaction.save(update_fields=["transaction_date"])
        tx_kwargs = {
            "email": self.email,
            "category": self.category,
            "amount": Decimal("42.00"),
            "currency_code": "CRC",
            "transaction_date": fixed_now - timedelta(days=1),
            "reference_id": "monthly-budget",
        }
        if hasattr(models.Transaction, "user_id"):
            tx_kwargs["user"] = self.user
        models.Transaction.objects.create(**tx_kwargs)
        response = self.client.get(f"{reverse('tracker:dashboard')}?range=this_month")
        self.assertEqual(response.status_code, 200)
        control = response.context["spend_control"]
        self.assertEqual(control["days_total"], 31)
        self.assertEqual(control["days_remaining"], 21)
        self.assertEqual(control["daily_allowance"], Decimal("8"))

    def test_promote_rule_creates_category_rule(self):
        self.transaction.card_last4 = "7777"
        self.transaction.save(update_fields=["card_last4"])
        url = reverse("tracker:transaction_detail", args=[self.transaction.pk])
        response = self.client.post(url, {"action": "promote_rule"}, follow=True)
        self.assertEqual(response.status_code, 200)
        rule = models.CategoryRule.objects.get(
            user=self.user,
            match_value="Test Merchant",
        )
        self.assertEqual(rule.category, self.category)
        self.assertEqual(rule.card_last4, "7777")
        self.assertEqual(rule.match_field, models.CategoryRule.MatchField.MERCHANT)

    def test_promote_rule_requires_category(self):
        self.transaction.category = None
        self.transaction.save(update_fields=["category"])
        url = reverse("tracker:transaction_detail", args=[self.transaction.pk])
        response = self.client.post(url, {"action": "promote_rule"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            models.CategoryRule.objects.filter(
                user=self.user,
                match_value="Test Merchant",
            ).exists()
        )

    def test_rules_view_accept_suggestion(self):
        suggestion = models.RuleSuggestion.objects.create(
            user=self.user,
            merchant_name="Nuevo",
            category=self.category,
            card_last4="",
            transaction=self.transaction,
        )
        url = reverse("tracker:rules")
        response = self.client.post(
            url,
            {
                "action": "accept_suggestion",
                "suggestion_id": suggestion.pk,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, "accepted")
        self.assertTrue(
            models.CategoryRule.objects.filter(
                user=self.user,
                match_value="Nuevo",
                origin=models.CategoryRule.Origin.SUGGESTED,
            ).exists()
        )

    def test_rules_view_create_rule(self):
        url = reverse("tracker:rules")
        response = self.client.post(
            url,
            {
                "action": "create_rule",
                "category": self.category.pk,
                "match_field": models.CategoryRule.MatchField.MERCHANT,
                "match_type": models.CategoryRule.MatchType.CONTAINS,
                "match_value": "Test Merchant",
                "priority": 90,
                "is_active": True,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            models.CategoryRule.objects.filter(
                user=self.user,
                match_value="Test Merchant",
            ).exists()
        )

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

    def test_import_post_without_credentials(self):
        url = reverse("tracker:import")
        response = self.client.post(url, {"years": 1}, follow=True)
        self.assertContains(response, "Necesitas conectar al menos una cuenta de correo")
        self.assertFalse(models.ImportJob.objects.exists())

    @override_settings(LLM_CATEGORIZATION_ENABLED=False)
    @mock.patch(
        "tracker.views.db_transaction.on_commit",
        side_effect=lambda func, *args, **kwargs: func(),
    )
    @mock.patch("tracker.views.import_jobs_service.enqueue_job")
    def test_import_enqueues_async_job(self, mock_enqueue_job, mock_on_commit):
        models.EmailAccount.objects.create(
            user=self.user,
            provider=models.EmailAccount.Provider.GMAIL,
            email_address=self.user.email,
            token_json={"token": "abc"},
            scopes=["test"],
            is_active=True,
        )
        import_path = reverse("tracker:import")
        response = self.client.post(import_path, {"years": 1}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Importación en progreso")
        job = models.ImportJob.objects.get(user=self.user)
        mock_enqueue_job.assert_called_once_with(job.pk)
        mock_on_commit.assert_called()
        self.assertIn("after:", job.gmail_query)

    def test_import_job_status_endpoint(self):
        job = models.ImportJob.objects.create(
            user=self.user,
            gmail_query="from:test",
            outlook_query="from:test",
            max_messages=10,
        )
        url = reverse("tracker:import_job_status", args=[job.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["id"], str(job.pk))
        self.assertEqual(payload["status"], job.status)

    def test_import_job_status_rejects_other_users(self):
        other = get_user_model().objects.create_user(
            username="other", email="other@example.com", password="12345"
        )
        job = models.ImportJob.objects.create(
            user=other,
            gmail_query="from:test",
            outlook_query="from:test",
            max_messages=10,
        )
        url = reverse("tracker:import_job_status", args=[job.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

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
        self.assertTrue(
            models.ExpenseAccount.objects.filter(user=self.user, name="Gastos Hogar").exists()
        )

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
        self.assertTrue(
            models.ExpenseAccount.objects.filter(user=self.user, name="Viajes").exists()
        )

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

    def test_default_expense_accounts_seeded(self):
        models.ExpenseAccount.objects.filter(user=self.user).delete()
        url = reverse("tracker:cards")
        self.client.get(url)
        for name in ["Personal", "Familiar", "Ahorros"]:
            self.assertTrue(
                models.ExpenseAccount.objects.filter(user=self.user, name=name).exists()
            )

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

    def test_category_manage_creates_category(self):
        url = reverse("tracker:categories")
        response = self.client.post(
            url,
            {
                "action": "create_category",
                "name": "Salud",
                "code": "salud",
                "description": "Gastos médicos",
                "budget_limit": "75000",
                "is_active": True,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            models.Category.objects.filter(user=self.user, code="salud").exists()
        )

    def test_category_manage_creates_subcategory(self):
        category = models.Category.objects.create(user=self.user, code="viajes", name="Viajes")
        url = reverse("tracker:categories")
        response = self.client.post(
            url,
            {
                "action": "create_subcategory",
                "category": category.pk,
                "name": "Hospedaje",
                "code": "hospedaje",
                "budget_limit": "50000",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            models.Subcategory.objects.filter(user=self.user, category=category, code="hospedaje").exists()
        )

    def test_dashboard_sync_health_flags_stale_states(self):
        account = models.EmailAccount.objects.create(
            user=self.user,
            provider=models.EmailAccount.Provider.GMAIL,
            email_address=self.user.email,
            token_json={"token": "abc"},
        )
        models.MailSyncState.objects.create(
            user=self.user,
            account=account,
            provider=account.provider,
            label="primary",
            checkpoint={"history_id": "abc"},
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

    @override_settings(MS_GRAPH_CLIENT_ID="cid", MS_GRAPH_CLIENT_SECRET="secret")
    @mock.patch("tracker.views.msal.ConfidentialClientApplication")
    def test_outlook_oauth_start_redirects(self, mock_app):
        instance = mock_app.return_value
        instance.get_authorization_request_url.return_value = "https://login.microsoftonline.com/auth"
        response = self.client.get(reverse("tracker:outlook_connect"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("outlook_oauth_state", self.client.session)

    @override_settings(MS_GRAPH_CLIENT_ID="cid", MS_GRAPH_CLIENT_SECRET="secret")
    @mock.patch("tracker.views.msal.ConfidentialClientApplication")
    def test_outlook_oauth_callback_creates_account(self, mock_app):
        instance = mock_app.return_value
        instance.acquire_token_by_authorization_code.return_value = {
            "access_token": "abc",
            "refresh_token": "refresh",
            "expires_in": 3600,
            "id_token_claims": {"preferred_username": "outlook@example.com"},
        }
        session = self.client.session
        session["outlook_oauth_state"] = "abc123"
        session.save()
        response = self.client.get(
            reverse("tracker:outlook_callback"),
            {"code": "authcode", "state": "abc123"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            models.EmailAccount.objects.filter(
                provider=models.EmailAccount.Provider.OUTLOOK, email_address="outlook@example.com"
            ).exists()
        )

    def test_outlook_oauth_callback_invalid_state(self):
        response = self.client.get(
            reverse("tracker:outlook_callback"),
            {"code": "authcode", "state": "mismatch"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sesión inválida al conectar Outlook")
