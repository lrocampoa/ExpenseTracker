from __future__ import annotations

import os
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Avg, Count, Max, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import urlencode
from django.views import View
from django.views.generic import DetailView, FormView, ListView, TemplateView, UpdateView
from django.views.generic.edit import FormMixin

from google_auth_oauthlib.flow import Flow

from tracker import models
from tracker.forms import CardForm, CardLabelForm, ImportForm, TransactionFilterForm, TransactionUpdateForm
from tracker.services import parser as parser_service
from tracker.services.gmail import (
    GmailCredentialManager,
    GmailIngestionService,
    MissingCredentialsError,
)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "tracker/dashboard.html"
    RANGE_OPTIONS = {
        "last30": {"label": "Últimos 30 días", "days": 30},
        "this_month": {"label": "Mes actual", "days": None},
        "last90": {"label": "Últimos 90 días", "days": 90},
        "year_to_date": {"label": "Año en curso", "days": None, "mode": "ytd"},
    }
    LOW_CONFIDENCE_THRESHOLD = 0.6

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        period = self._resolve_period()
        base_qs = (
            models.Transaction.objects.filter(user=self.request.user)
            .exclude(transaction_date__isnull=True)
            .select_related("category", "card")
        )
        period_qs = base_qs.filter(
            transaction_date__gte=period["start"], transaction_date__lte=period["end"]
        )
        period_amount_qs = period_qs.exclude(amount__isnull=True)
        previous_amount_qs = base_qs.filter(
            transaction_date__gte=period["previous_start"],
            transaction_date__lte=period["previous_end"],
        ).exclude(amount__isnull=True)
        prev_total = previous_amount_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        hero = self._build_hero(period_amount_qs, period, prev_total)
        context.update(
            {
                "period": period,
                "hero": hero,
                "alerts": self._build_alerts(base_qs, period),
                "spending_trend": self._build_trend(period_amount_qs),
                "category_insights": self._build_category_insights(
                    period_amount_qs, previous_amount_qs
                ),
                "merchant_signals": self._build_merchant_signals(period_amount_qs, period),
                "card_health": self._build_card_health(period_qs),
                "action_queue": self._build_action_queue(base_qs),
                "automation_insight": self._build_automation_insight(period_qs),
                "llm_usage": self._build_llm_usage(period),
                "sync_health": self._build_sync_health(),
                "monthly_chart": self._build_monthly_chart(),
            }
        )
        return context

    def _resolve_period(self):
        range_key = self.request.GET.get("range", "last30")
        end = timezone.now()
        option = self.RANGE_OPTIONS.get(range_key, self.RANGE_OPTIONS["last30"])
        if option.get("mode") == "ytd":
            start = end.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        elif option["days"] is None and range_key == "this_month":
            start = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            days = option.get("days") or 30
            start = end - timedelta(days=days)
        delta = end - start
        previous_end = start
        previous_start = previous_end - delta
        days = max(delta.days, 1)
        return {
            "label": option["label"],
            "start": start,
            "end": end,
            "days": days,
            "range_key": range_key,
            "previous_start": previous_start,
            "previous_end": previous_end,
            "available_ranges": [
                {
                    "key": key,
                    "label": data["label"],
                    "active": key == range_key,
                    "url": self._range_url(key),
                }
                for key, data in self.RANGE_OPTIONS.items()
            ],
        }

    def _range_url(self, key: str) -> str:
        params = self.request.GET.copy()
        params["range"] = key
        return f"?{params.urlencode()}"

    def _build_hero(self, qs, period, prev_total):
        total = qs.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        txn_count = qs.count()
        avg_daily = total / Decimal(max(period["days"], 1))
        avg_ticket = total / Decimal(txn_count) if txn_count else Decimal("0")
        active_cards = qs.exclude(card__isnull=True).values("card_id").distinct().count()
        pct_change = None
        if prev_total and prev_total != Decimal("0"):
            pct_change = ((total - prev_total) / prev_total) * Decimal("100")
        currency_totals = list(
            qs.values("currency_code")
            .annotate(total=Sum("amount"))
            .order_by("-total")
        )
        return {
            "total_spend": total,
            "previous_total": prev_total,
            "pct_change": pct_change,
            "avg_daily": avg_daily,
            "transaction_count": txn_count,
            "avg_ticket": avg_ticket,
            "active_cards": active_cards,
            "currency_totals": currency_totals,
        }

    def _build_alerts(self, base_qs, period):
        alerts = []
        uncategorized = base_qs.filter(category__isnull=True).count()
        if uncategorized:
            alerts.append(
                {"label": "Sin categoría", "value": uncategorized, "severity": "warn"}
            )
        failed = base_qs.filter(parse_status=models.Transaction.ParseStatus.FAILED).count()
        if failed:
            alerts.append({"label": "Errores de parseo", "value": failed, "severity": "error"})
        review = base_qs.filter(
            Q(parse_confidence__lt=self.LOW_CONFIDENCE_THRESHOLD)
            | Q(category_confidence__lt=self.LOW_CONFIDENCE_THRESHOLD)
        ).count()
        if review:
            alerts.append({"label": "Pendientes de revisión", "value": review, "severity": "info"})
        llm_usage = (
            models.LLMDecisionLog.objects.filter(
                user=self.request.user,
                created_at__gte=period["start"],
                created_at__lte=period["end"],
            ).aggregate(cost=Sum("cost_usd"))
        )
        cost = llm_usage.get("cost") or Decimal("0")
        llm_budget = self._decimal_from_setting("LLM_DAILY_BUDGET_USD", Decimal("1.50"))
        if cost and cost > 0:
            alerts.append(
                {
                    "label": "Costo LLM periodo",
                    "value": f"${cost:.2f}",
                    "severity": "info"
                    if cost <= llm_budget
                    else "warn",
                }
            )
        return alerts

    def _build_trend(self, qs):
        trend = (
            qs.annotate(day=TruncDate("transaction_date"))
            .values("day", "currency_code")
            .annotate(total=Sum("amount"), count=Count("id"))
            .order_by("day")
        )
        return [
            {
                "day": entry["day"],
                "currency_code": entry["currency_code"],
                "total": entry["total"],
                "count": entry["count"],
            }
            for entry in trend
        ]

    def _build_category_insights(self, current_qs, previous_qs):
        categories = list(
            current_qs.values("category_id", "category__name")
            .annotate(total=Sum("amount"), count=Count("id"))
            .order_by("-total")
        )
        previous_map = {
            row["category_id"] or "none": row["total"]
            for row in previous_qs.values("category_id").annotate(total=Sum("amount"))
        }
        for item in categories:
            key = item["category_id"] or "none"
            prev_total = previous_map.get(key, Decimal("0"))
            item["delta"] = item["total"] - prev_total
            item["delta_pct"] = (
                ((item["total"] - prev_total) / prev_total * Decimal("100"))
                if prev_total not in (None, 0, Decimal("0"))
                else None
            )
            item["label"] = item["category__name"] or "Sin categoría"
        total_spend = sum((item["total"] for item in categories), Decimal("0"))
        for item in categories:
            item["share"] = (
                (item["total"] / total_spend * Decimal("100")) if total_spend else Decimal("0")
            )
        return categories

    def _build_merchant_signals(self, qs, period):
        top_merchants = list(
            qs.exclude(merchant_name__exact="")
            .values("merchant_name")
            .annotate(total=Sum("amount"), count=Count("id"))
            .order_by("-total")[:8]
        )
        historic_merchants = set(
            models.Transaction.objects.filter(
                user=self.request.user,
                transaction_date__lt=period["start"],
            )
            .exclude(merchant_name__exact="")
            .values_list("merchant_name", flat=True)
        )
        new_merchants = (
            qs.exclude(merchant_name__exact="")
            .exclude(merchant_name__in=historic_merchants)
            .values("merchant_name")
            .distinct()
            .count()
        )
        suspected_subscriptions = list(
            qs.exclude(merchant_name__exact="")
            .exclude(amount__isnull=True)
            .values("merchant_name", "amount")
            .annotate(count=Count("id"))
            .filter(count__gte=3)
            .order_by("-count")[:5]
        )
        return {
            "top_merchants": top_merchants,
            "new_merchants": new_merchants,
            "suspected_subscriptions": suspected_subscriptions,
        }

    def _build_card_health(self, qs):
        card_stats = list(
            qs.exclude(card__isnull=True)
            .values("card__label", "card__last4", "card_id")
            .annotate(total=Sum("amount"), count=Count("id"), avg=Avg("amount"))
            .order_by("-total")
        )
        idle_cards = (
            models.Card.objects.filter(user=self.request.user, is_active=True)
            .exclude(transactions__transaction_date__gte=timezone.now() - timedelta(days=45))
            .annotate(last_tx=Max("transactions__transaction_date"))
            .distinct()
        )
        return {
            "card_stats": card_stats,
            "idle_cards": idle_cards,
        }

    def _build_action_queue(self, base_qs):
        high_amount_threshold = self._decimal_from_setting(
            "DASHBOARD_HIGH_AMOUNT_THRESHOLD", Decimal("250000")
        )
        attention = (
            base_qs.filter(
                Q(category__isnull=True)
                | Q(parse_confidence__lt=self.LOW_CONFIDENCE_THRESHOLD)
                | Q(category_confidence__lt=self.LOW_CONFIDENCE_THRESHOLD)
                | Q(amount__gte=high_amount_threshold)
            )
            .order_by("-transaction_date")
            .select_related("category", "card")[:8]
        )
        return attention

    def _build_automation_insight(self, qs):
        breakdown = list(
            qs.values("category_source")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        return breakdown

    def _build_llm_usage(self, period):
        logs = models.LLMDecisionLog.objects.filter(
            user=self.request.user,
            created_at__gte=period["start"],
            created_at__lte=period["end"],
        )
        aggregates = logs.aggregate(
            total_cost=Sum("cost_usd"),
            tokens_prompt=Sum("tokens_prompt"),
            tokens_completion=Sum("tokens_completion"),
        )
        decision_breakdown = list(
            logs.values("decision_type").annotate(count=Count("id")).order_by("-count")
        )
        return {
            "summary": aggregates,
            "decision_breakdown": decision_breakdown,
        }

    def _build_sync_health(self):
        stale_minutes = getattr(settings, "DASHBOARD_SYNC_STALE_MINUTES", 30)
        cutoff = timezone.now() - timedelta(minutes=stale_minutes)
        states_qs = models.GmailSyncState.objects.filter(user=self.request.user).order_by("label")
        states = []
        stale_labels = []
        for state in states_qs:
            is_stale = not state.last_synced_at or state.last_synced_at < cutoff
            state_dict = {
                "label": state.label,
                "history_id": state.history_id,
                "last_synced_at": state.last_synced_at,
                "fetched_messages": state.fetched_messages,
                "retry_count": getattr(state, "retry_count", 0),
                "query": state.query,
                "is_stale": is_stale,
            }
            states.append(state_dict)
            if is_stale:
                stale_labels.append(state.label)
        pending_emails = models.EmailMessage.objects.filter(
            user=self.request.user, processed_at__isnull=True
        ).count()
        return {
            "states": states,
            "pending_emails": pending_emails,
            "stale_labels": stale_labels,
            "stale_minutes": stale_minutes,
        }

    def _decimal_from_setting(self, setting_name: str, default: Decimal) -> Decimal:
        raw_value = getattr(settings, setting_name, default)
        if raw_value is None:
            return default
        if isinstance(raw_value, Decimal):
            return raw_value
        try:
            return Decimal(str(raw_value))
        except (TypeError, ValueError, ArithmeticError):
            return default

    def _build_monthly_chart(self, months: int = 9):
        end = timezone.now()
        start = (end - timedelta(days=months * 31)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        qs = (
            models.Transaction.objects.filter(user=self.request.user, transaction_date__gte=start)
            .exclude(amount__isnull=True)
        )
        aggregates = (
            qs.annotate(month=TruncMonth("transaction_date"))
            .values("month")
            .annotate(total=Sum("amount"))
            .order_by("month")
        )
        totals = {}
        for entry in aggregates:
            month = entry["month"]
            if month:
                totals[month.date().replace(day=1)] = entry["total"]

        labels = []
        values = []
        current_month = start.date().replace(day=1)
        end_month = end.date().replace(day=1)
        while current_month <= end_month:
            labels.append(current_month.strftime("%b %y"))
            values.append(totals.get(current_month, Decimal("0")))
            current_month = self._next_month(current_month)

        max_value = max(values) if values else Decimal("0")
        max_value = max_value or Decimal("1")
        steps = max(len(values) - 1, 1)
        x_step = 100 / steps if steps else 100
        points = []
        markers = []
        for idx, (label, value) in enumerate(zip(labels, values)):
            x = idx * x_step if len(values) > 1 else 50
            normalized = (value / max_value) if max_value else Decimal("0")
            y = 100 - float(normalized) * 100
            points.append(f"{x:.2f},{y:.2f}")
            markers.append(
                {
                    "x": f"{x:.2f}",
                    "y": f"{y:.2f}",
                    "label": label,
                    "value": value,
                }
            )
        area_points = ""
        if points:
            first_x = markers[0]["x"]
            last_x = markers[-1]["x"]
            area_points = f"{first_x},100 " + " ".join(points) + f" {last_x},100"

        avg_value = sum(values, Decimal("0")) / Decimal(len(values)) if values else Decimal("0")
        last_value = values[-1] if values else Decimal("0")
        prev_value = values[-2] if len(values) >= 2 else None
        mom_change = (
            ((last_value - prev_value) / prev_value * Decimal("100"))
            if prev_value not in (None, Decimal("0"))
            else None
        )
        best_value = max(values) if values else Decimal("0")
        best_index = values.index(best_value) if values else 0
        best_label = labels[best_index] if labels else ""

        return {
            "labels": labels,
            "values": values,
            "points": " ".join(points) if points else "",
            "area_points": area_points,
            "markers": markers,
            "has_data": bool(points),
            "grid_lines": [20, 40, 60, 80],
            "insights": {
                "last_label": labels[-1] if labels else "",
                "last_value": last_value,
                "avg_value": avg_value,
                "mom_change": mom_change,
                "best_label": best_label,
                "best_value": best_value,
            },
        }

    def _next_month(self, date_obj):
        if date_obj.month == 12:
            return date_obj.replace(year=date_obj.year + 1, month=1, day=1)
        return date_obj.replace(month=date_obj.month + 1, day=1)



class TransactionListView(LoginRequiredMixin, ListView):
    template_name = "tracker/transaction_list.html"
    context_object_name = "transactions"
    paginate_by = 20

    def get_queryset(self):
        qs = (
            models.Transaction.objects.select_related("category", "card")
            .order_by("-transaction_date", "-created_at")
        )
        if hasattr(models.Transaction, "user_id"):
            qs = qs.filter(user=self.request.user)
        self.filter_form = TransactionFilterForm(self.request.GET or None, user=self.request.user)
        if self.filter_form.is_valid():
            data = self.filter_form.cleaned_data
            search = data.get("search")
            if search:
                qs = qs.filter(
                    Q(merchant_name__icontains=search)
                    | Q(description__icontains=search)
                    | Q(reference_id__icontains=search)
                )
            if data.get("category"):
                qs = qs.filter(category=data["category"])
            if data.get("card_last4"):
                qs = qs.filter(card_last4=data["card_last4"])
            if data.get("date_from"):
                qs = qs.filter(transaction_date__date__gte=data["date_from"])
            if data.get("date_to"):
                qs = qs.filter(transaction_date__date__lte=data["date_to"])
            if data.get("min_amount") is not None:
                qs = qs.filter(amount__gte=data["min_amount"])
            if data.get("max_amount") is not None:
                qs = qs.filter(amount__lte=data["max_amount"])
        self.filtered_queryset = qs
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = getattr(self, "filter_form", TransactionFilterForm(user=self.request.user))
        context["result_count"] = self.filtered_queryset.count()
        totals = (
            self.filtered_queryset.values("currency_code")
            .annotate(total=Sum("amount"))
            .order_by("currency_code")
        )
        context["currency_totals"] = totals
        context["querystring"] = self._build_querystring()
        context["quick_months"] = self._month_shortcuts()
        return context

    def _build_querystring(self) -> str:
        params = self.request.GET.copy()
        params.pop("page", None)
        encoded = urlencode({k: v for k, v in params.items() if v})
        return f"&{encoded}" if encoded else ""

    def _month_shortcuts(self):
        today = timezone.now().date().replace(day=1)
        months = []
        for offset in range(5):
            month_date = (today - timezone.timedelta(days=offset * 30)).replace(day=1)
            first_day = month_date
            next_month = (first_day + timezone.timedelta(days=32)).replace(day=1)
            last_day = next_month - timezone.timedelta(days=1)
            params = self.request.GET.copy()
            params["date_from"] = first_day.isoformat()
            params["date_to"] = last_day.isoformat()
            params.pop("page", None)
            months.append(
                {
                    "label": first_day.strftime("%b %Y"),
                    "query": urlencode({k: v for k, v in params.items() if v}),
                }
            )
        return months


class TransactionDetailView(LoginRequiredMixin, FormMixin, DetailView):
    template_name = "tracker/transaction_detail.html"
    model = models.Transaction
    form_class = TransactionUpdateForm
    context_object_name = "transaction"

    def get_success_url(self):
        return reverse_lazy("tracker:transaction_detail", kwargs={"pk": self.object.pk})

    def get_queryset(self):
        qs = super().get_queryset().select_related("email", "category", "card")
        if hasattr(models.Transaction, "user_id"):
            qs = qs.filter(user=self.request.user)
        return qs

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.get_object()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if "form" not in context:
            context["form"] = self.get_form()
        context["show_form"] = self.request.GET.get("edit") == "1" or self.request.method == "POST"
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.POST.get("action") == "reparse":
            return self._handle_reparse()
        form = self.get_form()
        if form.is_valid():
            form.save()
            return self.form_valid(form)
        return self.form_invalid(form)

    def _handle_reparse(self):
        if not self.object.email:
            messages.error(self.request, "Esta transacción no tiene correo asociado.")
            return redirect(self.get_success_url())
        parser_service.create_transaction_from_email(
            self.object.email, existing_transaction=self.object
        )
        self.object.refresh_from_db()
        messages.success(self.request, "Transacción reprocesada desde el correo.")
        return redirect(self.get_success_url())


class ImportTransactionsView(LoginRequiredMixin, FormView):
    template_name = "tracker/import.html"
    form_class = ImportForm

    def get_success_url(self):
        return reverse_lazy("tracker:transaction_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_credentials"] = self._has_credentials()
        return context

    def form_valid(self, form):
        if not self._has_credentials():
            messages.error(self.request, "Necesitas conectar Gmail antes de importar.")
            return redirect("tracker:import")
        years = int(form.cleaned_data["years"])
        years = max(1, min(3, years))
        after_date = timezone.now().date() - timedelta(days=365 * years)
        manager = GmailCredentialManager(user_email=self.request.user.email, user=self.request.user)
        try:
            creds = manager.ensure_credentials()
        except MissingCredentialsError:
            messages.error(self.request, "Conecta tu Gmail nuevamente.")
            return redirect("tracker:import")

        service = GmailCredentialManager.build_service(creds)
        query = f"{settings.GMAIL_SEARCH_QUERY} after:{after_date.strftime('%Y/%m/%d')}"
        ingestion = GmailIngestionService(
            service=service,
            user_email=self.request.user.email,
            query=query,
            label=f"user-{self.request.user.id}",
            max_messages=settings.GMAIL_MAX_MESSAGES_PER_SYNC,
            user=self.request.user,
        )
        result = ingestion.sync()
        processed = self._process_pending_emails()
        messages.success(
            self.request,
            f"Importación completada. Correos nuevos: {result.created}. Transacciones procesadas: {processed}.",
        )
        return super().form_valid(form)

    def _process_pending_emails(self):
        pending = models.EmailMessage.objects.filter(
            user=self.request.user,
            processed_at__isnull=True,
        ).order_by("-internal_date")[: settings.GMAIL_MAX_MESSAGES_PER_SYNC]
        count = 0
        for email in pending:
            if parser_service.create_transaction_from_email(email):
                count += 1
        return count

    def _has_credentials(self):
        return models.GmailCredential.objects.filter(
            user=self.request.user,
            is_active=True,
            user_email__iexact=self.request.user.email,
        ).exists()


class GmailOAuthStartView(LoginRequiredMixin, View):
    def get(self, request):
        flow = Flow.from_client_secrets_file(
            settings.GOOGLE_OAUTH_CLIENT_SECRET_PATH,
            scopes=settings.GMAIL_SCOPES,
            redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI,
        )
        if settings.DEBUG:
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
            os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        request.session["gmail_oauth_state"] = state
        return redirect(authorization_url)


class GmailOAuthCallbackView(LoginRequiredMixin, View):
    def get(self, request):
        state = request.session.get("gmail_oauth_state")
        flow = Flow.from_client_secrets_file(
            settings.GOOGLE_OAUTH_CLIENT_SECRET_PATH,
            scopes=settings.GMAIL_SCOPES,
            state=state,
            redirect_uri=settings.GOOGLE_OAUTH_REDIRECT_URI,
        )
        if settings.DEBUG:
            os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
            os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
        flow.fetch_token(authorization_response=request.build_absolute_uri())
        manager = GmailCredentialManager(user_email=request.user.email, user=request.user)
        manager.save_credentials(flow.credentials)
        messages.success(request, "Cuenta de Gmail conectada correctamente.")
        return redirect("tracker:import")


class CardListView(LoginRequiredMixin, TemplateView):
    template_name = "tracker/card_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        expense_choices = self._expense_choices()
        error_forms = kwargs.get("error_forms") or {}
        context["card_rows"] = self._build_card_rows(
            expense_choices=expense_choices, error_forms=error_forms
        )
        context["has_cards"] = bool(context["card_rows"])
        return context

    def _expense_choices(self):
        user = self.request.user
        accounts = (
            models.Card.objects.filter(user=user)
            .exclude(expense_account="")
            .values_list("expense_account", flat=True)
            .order_by("expense_account")
            .distinct()
        )
        return list(accounts)

    def _build_card_rows(self, expense_choices, error_forms=None):
        user = self.request.user
        cards = models.Card.objects.filter(user=user).order_by("label")
        card_map = {card.last4: card for card in cards}
        transaction_last4 = (
            models.Transaction.objects.filter(user=user)
            .exclude(card_last4="")
            .values_list("card_last4", flat=True)
            .distinct()
        )
        all_last4 = sorted(set(card_map.keys()) | set(transaction_last4))
        rows = []
        error_forms = error_forms or {}
        for last4 in all_last4:
            card = card_map.get(last4)
            if last4 in error_forms:
                form = error_forms[last4]
            else:
                initial = {
                    "card_id": card.pk if card else "",
                    "last4": last4,
                    "label": card.label if card else "",
                    "expense_account": card.expense_account if card else "",
                }
                form = CardLabelForm(
                    initial=initial, user=user, expense_choices=expense_choices
                )
            rows.append({"last4": last4, "card": card, "form": form})
        return rows

    def post(self, request, *args, **kwargs):
        expense_choices = self._expense_choices()
        form = CardLabelForm(
            request.POST, user=request.user, expense_choices=expense_choices
        )
        if form.is_valid():
            card = form.save()
            messages.success(
                request, f"Información guardada para la tarjeta **** {card.last4}."
            )
            return redirect("tracker:cards")
        error_forms = {}
        last4 = request.POST.get("last4")
        if last4:
            error_forms[last4] = form
        context = self.get_context_data(error_forms=error_forms)
        return self.render_to_response(context)


class CardUpdateView(LoginRequiredMixin, UpdateView):
    template_name = "tracker/card_form.html"
    form_class = CardForm
    model = models.Card

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_queryset(self):
        qs = super().get_queryset()
        if hasattr(models.Card, "user_id"):
            qs = qs.filter(user=self.request.user)
        return qs

    def form_valid(self, form):
        messages.success(self.request, "Tarjeta actualizada.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("tracker:cards")
