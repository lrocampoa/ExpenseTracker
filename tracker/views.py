from __future__ import annotations

import calendar
import json
import logging
import os
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction as db_transaction
from django.db.models import Avg, Count, Max, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import urlencode
from django.views import View
from django.views.generic import DetailView, FormView, ListView, TemplateView, UpdateView
from django.views.generic.edit import FormMixin

import msal
from google_auth_oauthlib.flow import Flow

from tracker import models
from tracker.forms import (
    CardForm,
    CardLabelForm,
    CategoryForm,
    CategoryInlineForm,
    CategoryRuleForm,
    ImportForm,
    RuleSuggestionDecisionForm,
    SubcategoryForm,
    SubcategoryInlineForm,
    TransactionFilterForm,
    TransactionUpdateForm,
)
from tracker.services import account_seeding
from tracker.services import category_seeding
from tracker.services import corrections as correction_service
from tracker.services import import_jobs as import_jobs_service
from tracker.services import parser as parser_service
from tracker.services import rule_seeding
from tracker.services import rule_suggestions
from tracker.services import rules as rules_service
from tracker.services import categorizer
from tracker.services import review as review_service
from tracker.services.gmail import GmailCredentialManager

logger = logging.getLogger(__name__)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "tracker/dashboard.html"
    RANGE_OPTIONS = {
        "last30": {"label": "Últimos 30 días", "days": 30},
        "this_month": {"label": "Mes actual", "days": None},
        "last90": {"label": "Últimos 90 días", "days": 90},
        "year_to_date": {"label": "Año en curso", "days": None, "mode": "ytd"},
    }
    LOW_CONFIDENCE_THRESHOLD = review_service.confidence_threshold()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        period = self._resolve_period()
        base_qs = (
            models.Transaction.objects.filter(user=self.request.user)
            .exclude(transaction_date__isnull=True)
            .select_related("category", "card")
        )
        expense_filter = self._resolve_expense_filter()
        if expense_filter["value"]:
            base_qs = base_qs.filter(card__expense_account=expense_filter["value"])
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
        category_insights = self._build_category_insights(period_amount_qs, previous_amount_qs)
        context.update(
            {
                "period": period,
                "hero": hero,
                "alerts": self._build_alerts(base_qs, period),
                "category_insights": category_insights,
                "category_budget_chart": self._build_category_budget_chart(category_insights),
                "spend_control": self._build_spend_control(period, hero, category_insights),
                "merchant_signals": self._build_merchant_signals(period_amount_qs, period),
                "card_health": self._build_card_health(period_qs),
                "expense_accounts": self._build_expense_accounts(
                    period_amount_qs, previous_amount_qs
                ),
                "action_queue": self._build_action_queue(base_qs),
                "uncategorized_merchants": self._build_uncategorized_merchants(period_qs),
                "automation_insight": self._build_automation_insight(period_qs),
                "llm_usage": self._build_llm_usage(period),
                "sync_health": self._build_sync_health(),
                "monthly_chart": self._build_monthly_chart(expense_filter["value"]),
                "manual_review": self._build_manual_review(period, expense_filter["value"]),
                "expense_filter": expense_filter,
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
        total_days = days
        period_end = end
        if option.get("days"):
            total_days = option["days"]
            period_end = start + timedelta(days=total_days)
        elif range_key == "this_month":
            _, month_days = calendar.monthrange(start.year, start.month)
            total_days = month_days or days
            period_end = start + timedelta(days=total_days)
        elif range_key == "year_to_date":
            year = start.year
            total_days = 366 if calendar.isleap(year) else 365
            period_end = start.replace(year=year + 1, month=1, day=1)
        return {
            "label": option["label"],
            "start": start,
            "end": end,
            "days": days,
            "total_days": total_days,
            "period_end": period_end,
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

    def _resolve_expense_filter(self):
        names = self._expense_account_names()
        selected = (self.request.GET.get("expense_account") or "").strip()
        if selected and selected not in names:
            selected = ""
        return {
            "value": selected,
            "label": selected or "Todas las cuentas",
            "options": [{"label": name, "value": name} for name in names],
            "is_active": bool(selected),
        }

    def _expense_account_names(self):
        account_seeding.ensure_default_accounts(self.request.user)
        user_accounts = set(
            models.ExpenseAccount.objects.filter(user=self.request.user).values_list("name", flat=True)
        )
        card_accounts = set(
            models.Card.objects.filter(user=self.request.user)
            .exclude(expense_account="")
            .values_list("expense_account", flat=True)
        )
        return sorted(name for name in (user_accounts | card_accounts) if name)

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
        review = base_qs.filter(needs_review=True).count()
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

    def _build_category_insights(self, current_qs, previous_qs):
        categories = list(
            current_qs.values("category_id", "category__name", "category__budget_limit")
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
            budget_limit = item.get("category__budget_limit")
            item["budget_limit"] = budget_limit
            if budget_limit not in (None, Decimal("0")):
                item["budget_used_pct"] = (
                    (item["total"] / budget_limit * Decimal("100")) if budget_limit else None
                )
                item["budget_remaining"] = budget_limit - item["total"]
                item["budget_remaining_abs"] = abs(item["budget_remaining"])
                item["is_budget_over"] = item["budget_remaining"] < 0
                fill_pct = item["budget_used_pct"] or Decimal("0")
                if fill_pct < 0:
                    fill_pct = Decimal("0")
                item["budget_fill_pct"] = float(min(fill_pct, Decimal("100")))
            else:
                item["budget_used_pct"] = None
                item["budget_remaining"] = None
                item["budget_remaining_abs"] = None
                item["is_budget_over"] = False
                item["budget_fill_pct"] = 0
        total_spend = sum((item["total"] for item in categories), Decimal("0"))
        for item in categories:
            item["share"] = (
                (item["total"] / total_spend * Decimal("100")) if total_spend else Decimal("0")
            )
        return categories

    def _build_category_budget_chart(self, category_rows, limit: int = 6):
        chart_rows = []
        for item in category_rows:
            budget = item.get("budget_limit")
            if not budget or budget in (None, Decimal("0")):
                continue
            used_pct = item.get("budget_used_pct") or Decimal("0")
            chart_rows.append(
                {
                    "label": item["label"],
                    "used_pct": used_pct,
                    "fill_pct": float(min(max(used_pct, Decimal("0")), Decimal("150"))),
                    "spent": item["total"],
                    "budget": budget,
                    "is_over": item.get("is_budget_over", False),
                }
            )
        chart_rows.sort(key=lambda row: row["used_pct"], reverse=True)
        return chart_rows[:limit]

    def _build_spend_control(self, period, hero, category_rows):
        total_budget = sum(
            (item.get("budget_limit") or Decimal("0")) for item in category_rows if item.get("budget_limit")
        )
        spent = hero.get("total_spend") or Decimal("0")
        days_total = max(period.get("total_days") or period.get("days") or 0, 1)
        elapsed_days = days_total
        days_remaining = 0
        now = timezone.now()
        if period.get("start"):
            elapsed_days = min(
                max((now - period["start"]).days + 1, 1),
                days_total,
            )
            days_remaining = max(days_total - elapsed_days, 0)
        if not total_budget or total_budget == Decimal("0"):
            return {
                "has_budget": False,
                "spent": spent,
                "total_budget": Decimal("0"),
                "burn_pct": 0,
                "remaining_budget": None,
                "projected_total": None,
                "status_delta": None,
                "status_delta_abs": None,
                "status": None,
                "run_rate": None,
                "ideal_rate": None,
                "elapsed_days": elapsed_days,
                "days_total": days_total,
                "days_remaining": days_remaining,
                "daily_allowance": None,
            }
        run_rate = spent / Decimal(elapsed_days) if elapsed_days else Decimal("0")
        ideal_rate = total_budget / Decimal(days_total) if days_total else None
        projected_total = run_rate * Decimal(days_total)
        remaining_budget = total_budget - spent
        daily_allowance = (
            (remaining_budget / Decimal(days_remaining))
            if days_remaining and remaining_budget > Decimal("0")
            else None
        )
        burn_pct = float(
            min(
                max((spent / total_budget) * Decimal("100"), Decimal("0")),
                Decimal("180"),
            )
        )
        status_delta = projected_total - total_budget
        status = "risk" if status_delta > 0 else "on_track"
        return {
            "has_budget": True,
            "spent": spent,
            "total_budget": total_budget,
            "burn_pct": burn_pct,
            "remaining_budget": remaining_budget,
            "projected_total": projected_total,
            "status_delta": status_delta,
            "status_delta_abs": abs(status_delta),
            "status": status,
            "run_rate": run_rate,
            "ideal_rate": ideal_rate,
            "elapsed_days": elapsed_days,
            "days_total": days_total,
            "days_remaining": days_remaining,
            "daily_allowance": daily_allowance,
        }

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

    def _filter_expense_account_transactions(self, qs):
        return (
            qs.exclude(card__isnull=True)
            .exclude(card__expense_account__isnull=True)
            .exclude(card__expense_account__exact="")
        )

    def _build_expense_accounts(self, current_qs, previous_qs, limit: int = 6):
        account_qs = self._filter_expense_account_transactions(current_qs)
        aggregates = list(
            account_qs.values("card__expense_account")
            .annotate(
                total=Sum("amount"),
                count=Count("id"),
                card_count=Count("card_id", distinct=True),
            )
            .order_by("-total")
        )
        if not aggregates:
            return {"has_data": False, "rows": []}

        previous_map = {
            row["card__expense_account"]: row["total"]
            for row in self._filter_expense_account_transactions(previous_qs)
            .values("card__expense_account")
            .annotate(total=Sum("amount"))
        }
        card_segments_raw = list(
            account_qs.values("card__expense_account", "card__label", "card__last4")
            .annotate(total=Sum("amount"))
        )
        card_segments_map = {}
        for entry in card_segments_raw:
            account_name = entry["card__expense_account"]
            if not account_name:
                continue
            card_segments_map.setdefault(account_name, []).append(entry)
        for segments in card_segments_map.values():
            segments.sort(key=lambda segment: segment["total"], reverse=True)

        palette = [
            "#e4002b",
            "#fb923c",
            "#facc15",
            "#0ea5e9",
            "#22c55e",
            "#8b5cf6",
            "#ec4899",
            "#14b8a6",
        ]
        grand_total = sum((row["total"] for row in aggregates), Decimal("0"))
        max_total = aggregates[0]["total"] if aggregates else Decimal("0")
        rows = []
        visible_total = Decimal("0")
        for idx, entry in enumerate(aggregates[:limit]):
            label = entry["card__expense_account"]
            total = entry["total"] or Decimal("0")
            previous_total = previous_map.get(label, Decimal("0"))
            delta = total - previous_total
            delta_pct = (
                (delta / previous_total * Decimal("100"))
                if previous_total not in (None, Decimal("0"))
                else None
            )
            share_pct = (
                (total / grand_total * Decimal("100")) if grand_total else Decimal("0")
            )
            bar_pct = (
                float(total / max_total * Decimal("100")) if max_total else 0.0
            )

            segments = []
            for seg_idx, segment in enumerate(card_segments_map.get(label, [])[:3]):
                seg_total = segment["total"] or Decimal("0")
                seg_share = (
                    (seg_total / total * Decimal("100")) if total else Decimal("0")
                )
                segments.append(
                    {
                        "label": segment.get("card__label") or "",
                        "last4": segment.get("card__last4") or "",
                        "amount": seg_total,
                        "share_pct": seg_share,
                        "width_pct": float(seg_share),
                        "color": palette[(idx + seg_idx) % len(palette)],
                    }
                )

            rows.append(
                {
                    "label": label,
                    "total": total,
                    "count": entry["count"],
                    "card_count": entry["card_count"],
                    "share_pct": share_pct,
                    "bar_pct": bar_pct,
                    "previous_total": previous_total,
                    "delta": delta,
                    "delta_pct": delta_pct,
                    "segments": segments,
                    "color": palette[idx % len(palette)],
                }
            )
            visible_total += total

        gradient_segments = []
        cursor = Decimal("0")
        for row in rows:
            start = cursor
            cursor += row["share_pct"]
            gradient_segments.append(
                f"{row['color']} {float(start):.2f}% {float(cursor):.2f}%"
            )
        if grand_total and visible_total < grand_total:
            gradient_segments.append(
                f"#d4d4d8 {float(cursor):.2f}% 100%"
            )
        pie_style = (
            f"background: conic-gradient({', '.join(gradient_segments)});"
            if gradient_segments
            else ""
        )
        return {
            "has_data": True,
            "rows": rows,
            "grand_total": grand_total,
            "total_accounts": len(aggregates),
            "visible_accounts": len(rows),
            "others_total": grand_total - visible_total,
            "pie_style": pie_style,
        }

    def _build_action_queue(self, base_qs):
        high_amount_threshold = self._decimal_from_setting(
            "DASHBOARD_HIGH_AMOUNT_THRESHOLD", Decimal("250000")
        )
        attention = (
            base_qs.filter(
                Q(category__isnull=True)
                | Q(needs_review=True)
                | Q(amount__gte=high_amount_threshold)
            )
            .order_by("-transaction_date")
            .select_related("category", "card")[:8]
        )
        return attention

    def _build_uncategorized_merchants(self, qs, limit: int = 5):
        base = (
            qs.filter(category__isnull=True)
            .exclude(merchant_name__isnull=True)
            .exclude(merchant_name__exact="")
        )
        aggregates = list(
            base.values("merchant_name")
            .annotate(
                total=Sum("amount"),
                count=Count("id"),
                last_tx=Max("transaction_date"),
                currency_code=Max("currency_code"),
            )
            .order_by("-count", "-total")[:limit]
        )
        if not aggregates:
            return []
        merchant_names = [row["merchant_name"] for row in aggregates]
        cards = (
            base.filter(merchant_name__in=merchant_names)
            .exclude(card_last4__isnull=True)
            .exclude(card_last4__exact="")
            .values("merchant_name", "card_last4")
            .annotate(card_count=Count("id"))
        )
        card_lookup = {}
        for entry in cards:
            card_lookup.setdefault(entry["merchant_name"], []).append(entry)
        for entries in card_lookup.values():
            entries.sort(key=lambda item: item["card_count"], reverse=True)
        rules_url = reverse("tracker:rules")
        top_merchants = []
        for row in aggregates:
            merchant = row["merchant_name"]
            card_last4 = ""
            if merchant in card_lookup and card_lookup[merchant]:
                card_last4 = card_lookup[merchant][0]["card_last4"]
            params = {"prefill_match_value": merchant}
            if card_last4:
                params["prefill_card_last4"] = card_last4
            query = urlencode(params)
            rule_url = f"{rules_url}?{query}#new-rule"
            top_merchants.append(
                {
                    "merchant_name": merchant,
                    "count": row["count"],
                    "total": row["total"] or Decimal("0"),
                    "last_tx": row["last_tx"],
                    "card_last4": card_last4,
                    "currency_code": row.get("currency_code") or "",
                    "rule_url": rule_url,
                }
            )
        return top_merchants

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
        states_qs = (
            models.MailSyncState.objects.filter(user=self.request.user)
            .select_related("account")
            .order_by("account__email_address", "label")
        )
        states = []
        stale_labels = []
        for state in states_qs:
            is_stale = not state.last_synced_at or state.last_synced_at < cutoff
            history_id = ""
            checkpoint = state.checkpoint or {}
            if isinstance(checkpoint, dict):
                history_id = checkpoint.get("history_id") or ""
            state_dict = {
                "label": state.label,
                "provider": state.provider,
                "account_email": getattr(state.account, "email_address", ""),
                "history_id": history_id,
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

    def _build_monthly_chart(self, expense_account: str | None = None, months: int = 9):
        end = timezone.now()
        start = (end - timedelta(days=months * 31)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        qs = (
            models.Transaction.objects.filter(user=self.request.user, transaction_date__gte=start)
            .exclude(amount__isnull=True)
        )
        if expense_account:
            qs = qs.filter(card__expense_account=expense_account)
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

    def _build_manual_review(self, period, expense_account: str | None = None):
        corrections_qs = (
            models.TransactionCorrection.objects.filter(
                transaction__user=self.request.user,
                created_at__gte=period["start"],
                created_at__lte=period["end"],
            )
            .select_related("transaction", "new_category", "user")
            .order_by("-created_at")
        )
        if expense_account:
            corrections_qs = corrections_qs.filter(
                transaction__card__expense_account=expense_account
            )
        recent = list(corrections_qs[:5])
        total = corrections_qs.count()
        merchants = list(
            corrections_qs.values("new_merchant_name")
            .annotate(count=Count("id"))
            .order_by("-count")[:5]
        )
        return {
            "count": total,
            "recent": recent,
            "top_merchants": merchants,
        }
    
    
class ReviewQueueView(LoginRequiredMixin, ListView):
    template_name = "tracker/review_queue.html"
    context_object_name = "transactions"
    paginate_by = 25

    def get_queryset(self):
        return (
            models.Transaction.objects.filter(user=self.request.user, needs_review=True)
            .select_related("category", "card", "email")
            .order_by("-updated_at")
        )

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")
        transaction_id = request.POST.get("transaction_id")
        if action == "resolve" and transaction_id:
            transaction = get_object_or_404(
                models.Transaction,
                pk=transaction_id,
                user=request.user,
            )
            transaction.needs_review = False
            transaction.save(update_fields=["needs_review", "updated_at"])
            messages.success(request, "Transacción marcada como revisada.")
        return redirect("tracker:review_queue")


class TransactionListView(LoginRequiredMixin, ListView):
    template_name = "tracker/transaction_list.html"
    context_object_name = "transactions"
    paginate_by = 20

    def get_queryset(self):
        qs, form = self._build_filtered_queryset(self.request.GET or None)
        self.filter_form = form
        self.filtered_queryset = qs
        return qs

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") != "reprocess":
            return self.get(request, *args, **kwargs)
        qs, form = self._build_filtered_queryset(request.POST)
        if not form.is_valid():
            messages.error(request, "No se pudieron aplicar los filtros para reprocesar.")
            return redirect(self._redirect_with_filters(form.data))
        stats = self._reprocess_transactions(qs)
        self._notify_reprocess_result(stats)
        return redirect(self._redirect_with_filters(form.data))

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

    def _build_filtered_queryset(self, data):
        qs = (
            models.Transaction.objects.select_related("category", "subcategory", "card")
            .order_by("-transaction_date", "-created_at")
        )
        if hasattr(models.Transaction, "user_id"):
            qs = qs.filter(user=self.request.user)
        form = TransactionFilterForm(data, user=self.request.user)
        if form.is_valid():
            qs = self._apply_filters(qs, form.cleaned_data)
        return qs, form

    def _apply_filters(self, qs, data):
        search = data.get("search")
        if search:
            qs = qs.filter(
                Q(merchant_name__icontains=search)
                | Q(description__icontains=search)
                | Q(reference_id__icontains=search)
            )
        if data.get("category"):
            qs = qs.filter(category=data["category"])
        if data.get("subcategory"):
            qs = qs.filter(subcategory=data["subcategory"])
        if data.get("merchant"):
            qs = qs.filter(merchant_name=data["merchant"])
        if data.get("uncategorized"):
            qs = qs.filter(category__isnull=True)
        if data.get("needs_review"):
            qs = qs.filter(needs_review=True)
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
        return qs

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

    def _reprocess_transactions(self, queryset):
        stats = {
            "total": queryset.count(),
            "processed": 0,
            "skipped_manual": 0,
            "skipped_missing_email": 0,
            "failed": 0,
        }
        for transaction in queryset.iterator():
            metadata = transaction.metadata or {}
            if metadata.get("manual_override"):
                stats["skipped_manual"] += 1
                continue
            if not transaction.email_id:
                stats["skipped_missing_email"] += 1
                continue
            try:
                parser_service.create_transaction_from_email(
                    transaction.email, existing_transaction=transaction
                )
            except Exception:  # pragma: no cover - log and continue
                stats["failed"] += 1
                logger.exception("Error reprocesando transacción %s", transaction.pk)
            else:
                stats["processed"] += 1
        return stats

    def _notify_reprocess_result(self, stats):
        total = stats["total"]
        processed = stats["processed"]
        if total == 0:
            messages.info(self.request, "No hay transacciones para reprocesar con los filtros actuales.")
            return
        if processed:
            messages.success(
                self.request,
                f"Reprocesadas {processed} de {total} transacciones con los filtros actuales.",
            )
        else:
            messages.warning(
                self.request,
                "No se reprocesó ninguna transacción. Revisa si solo hay cambios manuales.",
            )
        if stats["skipped_manual"]:
            messages.info(
                self.request,
                f"{stats['skipped_manual']} transacciones se omitieron por tener ajustes manuales.",
            )
        if stats["skipped_missing_email"]:
            messages.info(
                self.request,
                f"{stats['skipped_missing_email']} transacciones no tienen correo para reprocesar.",
            )
        if stats["failed"]:
            messages.error(
                self.request,
                f"{stats['failed']} transacciones no pudieron reprocesarse. Revisa los registros.",
            )

    def _redirect_with_filters(self, data):
        base_url = reverse("tracker:transaction_list")
        if not data:
            return base_url
        params = {}
        for key in TransactionFilterForm.base_fields.keys():
            value = data.get(key)
            if value not in (None, "", []):
                params[key] = value
        query = urlencode(params)
        return f"{base_url}?{query}" if query else base_url


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
        self._before_snapshot = correction_service.snapshot_transaction(self.object)
        action = request.POST.get("action")
        if action == "reparse":
            return self._handle_reparse()
        if action == "promote_rule":
            return self._handle_promote_rule()
        form = self.get_form()
        if form.is_valid():
            return self.form_valid(form)
        return self.form_invalid(form)

    def form_valid(self, form):
        before_snapshot = getattr(self, "_before_snapshot", None)
        self.object = form.save()
        if before_snapshot:
            correction_service.record_manual_correction(
                self.object,
                self.request.user,
                before_snapshot,
            )
        messages.success(self.request, "Transacción actualizada manualmente.")
        return super().form_valid(form)

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

    def _handle_promote_rule(self):
        try:
            result = rules_service.create_rule_from_transaction(self.object, self.request.user)
        except rules_service.RulePromotionError as exc:
            messages.error(self.request, str(exc))
        else:
            if result.created:
                messages.success(
                    self.request,
                    f"Regla creada para {result.rule.match_value or 'valor'}",
                )
            else:
                messages.info(self.request, "Ya existe una regla con este comercio y categoría.")
        return redirect(self.get_success_url())


class ImportTransactionsView(LoginRequiredMixin, FormView):
    template_name = "tracker/import.html"
    form_class = ImportForm

    def get_success_url(self):
        return reverse_lazy("tracker:transaction_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["last_transaction_date"] = self._latest_transaction_datetime()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_credentials"] = self._has_credentials()
        context["accounts"] = self._account_rows()
        context["gmail_connect_url"] = reverse("tracker:gmail_connect")
        context["outlook_connect_url"] = reverse("tracker:outlook_connect")
        context["last_transaction_date"] = self._latest_transaction_datetime()
        active_job = self._active_job()
        context["active_import_job"] = active_job
        context["recent_import_jobs"] = self._recent_jobs()
        if active_job:
            context["import_job_status_url"] = reverse(
                "tracker:import_job_status", kwargs={"pk": active_job.pk}
            )
            context["import_job_poll_interval_ms"] = 4000
        return context

    def form_valid(self, form):
        if not self._has_credentials():
            messages.error(self.request, "Necesitas conectar al menos una cuenta de correo antes de importar.")
            return redirect("tracker:import")
        active_job = self._active_job()
        if active_job and active_job.is_active:
            messages.info(self.request, "Ya hay una importación en progreso. Espera a que termine antes de iniciar otra.")
            return redirect("tracker:import")
        range_choice = form.cleaned_data["years"]
        after_date = self._resolve_after_date(range_choice, form)
        gmail_query = f"{settings.GMAIL_SEARCH_QUERY} after:{after_date.strftime('%Y/%m/%d')}"
        job = models.ImportJob.objects.create(
            user=self.request.user,
            range_choice=str(range_choice),
            after_date=after_date,
            gmail_query=gmail_query,
            outlook_query=settings.OUTLOOK_SEARCH_QUERY,
            max_messages=settings.GMAIL_MAX_MESSAGES_PER_SYNC,
        )
        db_transaction.on_commit(lambda job_id=job.pk: import_jobs_service.enqueue_job(job_id))
        messages.info(
            self.request,
            "Importación en progreso. Puedes navegar mientras terminamos de sincronizar tus correos.",
        )
        return redirect("tracker:import")

    def _has_credentials(self):
        return models.EmailAccount.objects.filter(user=self.request.user, is_active=True).exists()

    def _account_rows(self):
        accounts = list(
            models.EmailAccount.objects.filter(user=self.request.user)
            .order_by("provider", "email_address")
        )
        if not accounts:
            return []
        states = {
            state.account_id: state
            for state in models.MailSyncState.objects.filter(account__in=accounts)
        }
        rows = []
        for account in accounts:
            state = states.get(account.id)
            last_synced = state.last_synced_at if state else None
            rows.append(
                {
                    "email": account.email_address,
                    "label": account.label or account.email_address,
                    "provider": account.get_provider_display(),
                    "provider_key": account.provider,
                    "last_synced_at": last_synced,
                    "retry_count": state.retry_count if state else 0,
                }
            )
        return rows

    def _latest_transaction_datetime(self):
        if hasattr(self, "_cached_latest_transaction"):
            return self._cached_latest_transaction
        latest = (
            models.Transaction.objects.filter(user=self.request.user)
            .order_by("-transaction_date", "-created_at")
            .values_list("transaction_date", flat=True)
            .first()
        )
        self._cached_latest_transaction = latest
        return latest

    def _active_job(self):
        if hasattr(self, "_cached_active_job"):
            return self._cached_active_job
        job = (
            models.ImportJob.objects.filter(
                user=self.request.user,
                status__in=models.ImportJob.ACTIVE_STATUSES,
            )
            .order_by("-created_at")
            .first()
        )
        self._cached_active_job = job
        return job

    def _recent_jobs(self):
        return list(
            models.ImportJob.objects.filter(user=self.request.user).order_by("-created_at")[:5]
        )

    def _resolve_after_date(self, range_choice, form):
        today = timezone.now().date()
        if range_choice == ImportForm.RECENT_CHOICE:
            recent_start = form.recent_start_date
            if recent_start:
                # Gmail's `after:` filter is exclusive, so step one day back to include the last transaction day.
                return recent_start - timedelta(days=1)
            return today - timedelta(days=365)
        try:
            years = int(range_choice)
        except (TypeError, ValueError):
            years = 1
        years = max(1, min(3, years))
        return today - timedelta(days=365 * years)


class ImportJobStatusView(LoginRequiredMixin, View):
    def get(self, request, pk):
        job = get_object_or_404(models.ImportJob, pk=pk, user=request.user)
        data = {
            "id": str(job.pk),
            "status": job.status,
            "status_display": job.get_status_display(),
            "progress_percent": job.progress_percent,
            "fetched_count": job.fetched_count,
            "processed_total": job.processed_total,
            "processed_messages": job.processed_messages,
            "created_transactions": job.created_transactions,
            "error_count": job.error_count,
            "is_finished": not job.is_active,
            "started_at": self._isoformat(job.started_at),
            "finished_at": self._isoformat(job.finished_at),
            "last_progress_at": self._isoformat(job.last_progress_at),
            "error_message": job.error_message,
        }
        return JsonResponse(data)

    def _isoformat(self, value):
        if not value:
            return None
        return timezone.localtime(value).isoformat()


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


class OutlookOAuthMixin:
    scopes = settings.MS_GRAPH_SCOPES or ["https://graph.microsoft.com/Mail.Read"]

    def _build_app(self):
        client_id = settings.MS_GRAPH_CLIENT_ID
        client_secret = settings.MS_GRAPH_CLIENT_SECRET
        if not client_id or not client_secret:
            raise ImproperlyConfigured("Configura MS_GRAPH_CLIENT_ID y MS_GRAPH_CLIENT_SECRET.")
        authority = getattr(settings, "MS_GRAPH_TENANT_ID", "") or "common"
        return msal.ConfidentialClientApplication(
            client_id=client_id,
            client_credential=client_secret,
            authority=f"https://login.microsoftonline.com/{authority}",
        )

    def _redirect_uri(self, request):
        return settings.MS_GRAPH_REDIRECT_URI or request.build_absolute_uri(
            reverse("tracker:outlook_callback")
        )


class OutlookOAuthStartView(LoginRequiredMixin, OutlookOAuthMixin, View):
    def get(self, request):
        try:
            app = self._build_app()
        except ImproperlyConfigured as exc:
            messages.error(request, str(exc))
            return redirect("tracker:import")
        state = secrets.token_urlsafe(32)
        request.session["outlook_oauth_state"] = state
        auth_url = app.get_authorization_request_url(
            scopes=self.scopes,
            state=state,
            redirect_uri=self._redirect_uri(request),
            prompt="select_account",
        )
        return redirect(auth_url)


class OutlookOAuthCallbackView(LoginRequiredMixin, OutlookOAuthMixin, View):
    def get(self, request):
        expected_state = request.session.get("outlook_oauth_state")
        incoming_state = request.GET.get("state")
        if not expected_state or expected_state != incoming_state:
            messages.error(request, "Sesión inválida al conectar Outlook. Intenta de nuevo.")
            return redirect("tracker:import")
        code = request.GET.get("code")
        if not code:
            messages.error(request, "Falta el código de autorización de Outlook.")
            return redirect("tracker:import")
        try:
            app = self._build_app()
        except ImproperlyConfigured as exc:
            messages.error(request, str(exc))
            return redirect("tracker:import")
        result = app.acquire_token_by_authorization_code(
            code,
            scopes=self.scopes,
            redirect_uri=self._redirect_uri(request),
        )
        if "access_token" not in result:
            error = result.get("error_description") or "No se pudo conectar tu cuenta de Outlook."
            messages.error(request, error)
            return redirect("tracker:import")
        requested_email = (
            (result.get("id_token_claims") or {}).get("preferred_username")
            or (result.get("id_token_claims") or {}).get("email")
            or request.user.email
        )
        expires_in = int(result.get("expires_in") or 0)
        expiry = timezone.now() + timedelta(seconds=expires_in) if expires_in else None
        token_json = json.loads(json.dumps(result))
        account, _ = models.EmailAccount.objects.update_or_create(
            provider=models.EmailAccount.Provider.OUTLOOK,
            email_address=requested_email,
            defaults={
                "user": request.user,
                "label": requested_email,
                "token_json": token_json,
                "token_expiry": expiry,
                "refresh_token": result.get("refresh_token") or "",
                "scopes": self.scopes,
                "is_active": True,
            },
        )
        request.session.pop("outlook_oauth_state", None)
        messages.success(request, f"Cuenta de Outlook conectada correctamente ({account.email_address}).")
        return redirect("tracker:import")


class CardListView(LoginRequiredMixin, TemplateView):
    template_name = "tracker/card_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        account_seeding.ensure_default_accounts(self.request.user)
        expense_choices = self._expense_choices()
        error_forms = kwargs.get("error_forms") or {}
        context["card_rows"] = self._build_card_rows(
            expense_choices=expense_choices, error_forms=error_forms
        )
        context["has_cards"] = bool(context["card_rows"])
        return context

    def _expense_choices(self):
        user = self.request.user
        existing_accounts = set(
            models.ExpenseAccount.objects.filter(user=user).values_list("name", flat=True)
        )
        card_accounts = set(
            models.Card.objects.filter(user=user)
            .exclude(expense_account="")
            .values_list("expense_account", flat=True)
            .order_by("expense_account")
            .distinct()
        )
        return sorted(existing_accounts | card_accounts)

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


class CategoryRuleListView(LoginRequiredMixin, TemplateView):
    template_name = "tracker/rules.html"

    def get(self, request, *args, **kwargs):
        rule_seeding.ensure_defaults(request.user)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")
        if action == "create_rule":
            return self._handle_create_rule(request)
        if action == "run_rules":
            return self._handle_run_rules(request)
        if action == "update_rule":
            return self._handle_update_rule(request)
        if action == "delete_rule":
            return self._handle_delete_rule(request)
        if action in {"accept_suggestion", "reject_suggestion"}:
            return self._handle_suggestion(request, action)
        return redirect("tracker:rules")

    def _handle_create_rule(self, request):
        form = CategoryRuleForm(request.POST, user=request.user)
        if form.is_valid():
            rule = form.save()
            rule.origin = models.CategoryRule.Origin.MANUAL
            rule.save(update_fields=["origin", "updated_at"])
            messages.success(request, "Regla creada.")
            return redirect("tracker:rules")
        self.rule_form = form
        self.rule_create_open = True
        return self.get(request)

    def _handle_suggestion(self, request, action: str):
        suggestion_id = request.POST.get("suggestion_id")
        suggestion = get_object_or_404(
            models.RuleSuggestion,
            pk=suggestion_id,
            user=request.user,
            status=models.RuleSuggestion.Status.PENDING,
        )
        if action == "accept_suggestion":
            rule_suggestions.apply_suggestion(suggestion)
            messages.success(request, "Sugerencia aceptada y regla creada.")
        else:
            reason = request.POST.get("reason", "")
            rule_suggestions.reject_suggestion(suggestion, reason)
            messages.info(request, "Sugerencia rechazada.")
        return redirect("tracker:rules")

    def _handle_delete_rule(self, request):
        rule_id = request.POST.get("rule_id")
        if not rule_id:
            messages.error(request, "No se encontró la regla.")
            return redirect("tracker:rules")
        qs = models.CategoryRule.objects.all()
        if hasattr(models.CategoryRule, "user_id"):
            qs = qs.filter(user=request.user)
        deleted, _ = qs.filter(pk=rule_id).delete()
        if deleted:
            messages.success(request, "Regla eliminada.")
        else:
            messages.error(request, "No se pudo eliminar la regla.")
        return redirect("tracker:rules")

    def _handle_run_rules(self, request):
        qs = (
            models.Transaction.objects.filter(user=request.user)
            .select_related("category")
        )
        uncategorized = qs.filter(category__isnull=True)
        updated = 0
        for trx in uncategorized.iterator(chunk_size=200):
            result = categorizer.categorize_transaction(trx, allow_llm=False)
            if result is not None:
                updated += 1
        if updated:
            messages.success(request, f"Se actualizaron {updated} transacciones con categorías.")
        else:
            messages.info(request, "No había transacciones pendientes de categorías.")
        return redirect("tracker:rules")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["rules"] = self._rule_queryset()
        rule_form = getattr(self, "rule_form", None)
        if not rule_form:
            initial = self._prefill_rule_initial()
            if initial:
                self.rule_create_open = True
            rule_form = CategoryRuleForm(user=self.request.user, initial=initial)
        context["rule_create_form"] = rule_form
        context["rule_create_open"] = getattr(self, "rule_create_open", False)
        context["suggestions"] = self._suggestion_queryset()
        return context

    def _rule_queryset(self):
        qs = models.CategoryRule.objects.select_related("category", "subcategory")
        if hasattr(models.CategoryRule, "user_id"):
            qs = qs.filter(user=self.request.user)
        return qs.order_by("priority", "match_value")

    def _prefill_rule_initial(self) -> dict[str, str]:
        initial: dict[str, str] = {}
        match_value = (self.request.GET.get("prefill_match_value") or "").strip()
        if match_value:
            initial["match_value"] = match_value
        card_last4 = (self.request.GET.get("prefill_card_last4") or "").strip()
        if card_last4 and card_last4.isdigit():
            initial["card_last4"] = card_last4[-4:]
        return initial

    def _suggestion_queryset(self):
        qs = models.RuleSuggestion.objects.filter(
            status=models.RuleSuggestion.Status.PENDING
        ).select_related("category", "transaction")
        if hasattr(models.RuleSuggestion, "user_id"):
            qs = qs.filter(user=self.request.user)
        return qs.order_by("-created_at")

    def _handle_update_rule(self, request):
        rule_id = request.POST.get("rule_id")
        rule_qs = models.CategoryRule.objects.all()
        if hasattr(models.CategoryRule, "user_id"):
            rule_qs = rule_qs.filter(user=request.user)
        rule = get_object_or_404(rule_qs, pk=rule_id)
        form = CategoryRuleForm(request.POST, instance=rule, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Regla actualizada.")
        else:
            for error in form.errors.values():
                messages.error(request, error)
        return redirect("tracker:rules")

    def _handle_delete_rule(self, request):
        rule_id = request.POST.get("rule_id")
        rule_qs = models.CategoryRule.objects.all()
        if hasattr(models.CategoryRule, "user_id"):
            rule_qs = rule_qs.filter(user=request.user)
        rule = get_object_or_404(rule_qs, pk=rule_id)
        rule.delete()
        messages.success(request, "Regla eliminada.")
        return redirect("tracker:rules")


class CategoryManageView(LoginRequiredMixin, TemplateView):
    template_name = "tracker/category_manage.html"

    def get(self, request, *args, **kwargs):
        category_seeding.ensure_defaults(request.user)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")
        if action == "create_category":
            return self._handle_category_form(request)
        if action == "create_subcategory":
            return self._handle_subcategory_form(request)
        if action == "update_category":
            return self._handle_update_category(request)
        if action == "update_subcategory":
            return self._handle_update_subcategory(request)
        if action == "delete_category":
            return self._handle_delete_category(request)
        if action == "delete_subcategory":
            return self._handle_delete_subcategory(request)
        return redirect("tracker:categories")

    def _handle_category_form(self, request):
        form = CategoryForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Categoría creada.")
            return redirect("tracker:categories")
        self.category_form = form
        self.category_create_open = True
        return self.get(request)

    def _handle_subcategory_form(self, request):
        form = SubcategoryForm(request.POST, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Subcategoría creada.")
            return redirect("tracker:categories")
        self.subcategory_form = form
        self.subcategory_create_open = True
        return self.get(request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        categories = list(self._category_tree())
        for category in categories:
            category.inline_form = CategoryInlineForm(
                instance=category,
                user=self.request.user,
                auto_id=f"id_cat_{category.id}_%s",
            )
        subcategories = list(self._subcategory_list())
        for subcategory in subcategories:
            subcategory.inline_form = SubcategoryInlineForm(
                instance=subcategory,
                user=self.request.user,
                auto_id=f"id_sub_{subcategory.id}_%s",
            )
        context["categories"] = categories
        context["subcategories"] = subcategories
        context["category_create_form"] = getattr(
            self, "category_form", CategoryForm(user=self.request.user)
        )
        context["subcategory_create_form"] = getattr(
            self, "subcategory_form", SubcategoryForm(user=self.request.user)
        )
        context["category_create_open"] = getattr(self, "category_create_open", False)
        context["subcategory_create_open"] = getattr(self, "subcategory_create_open", False)
        return context

    def _category_tree(self):
        qs = (
            models.Category.objects.filter(user=self.request.user)
            .prefetch_related("subcategories")
            .order_by("name")
        )
        return qs

    def _subcategory_list(self):
        return (
            models.Subcategory.objects.filter(user=self.request.user)
            .select_related("category")
            .order_by("category__name", "name")
        )

    def _handle_update_category(self, request):
        category_id = request.POST.get("category_id")
        category = get_object_or_404(
            models.Category,
            pk=category_id,
            user=request.user,
        )
        form = CategoryInlineForm(request.POST, instance=category, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Categoría actualizada.")
        else:
            for error in form.errors.values():
                messages.error(request, error)
        return redirect("tracker:categories")

    def _handle_update_subcategory(self, request):
        subcategory_id = request.POST.get("subcategory_id")
        subcategory = get_object_or_404(
            models.Subcategory,
            pk=subcategory_id,
            user=request.user,
        )
        form = SubcategoryInlineForm(request.POST, instance=subcategory, user=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Subcategoría actualizada.")
        else:
            for error in form.errors.values():
                messages.error(request, error)
        return redirect("tracker:categories")

    def _handle_delete_category(self, request):
        category_id = request.POST.get("category_id")
        category = get_object_or_404(
            models.Category,
            pk=category_id,
            user=request.user,
        )
        category.delete()
        messages.success(request, "Categoría eliminada.")
        return redirect("tracker:categories")

    def _handle_delete_subcategory(self, request):
        subcategory_id = request.POST.get("subcategory_id")
        subcategory = get_object_or_404(
            models.Subcategory,
            pk=subcategory_id,
            user=request.user,
        )
        subcategory.delete()
        messages.success(request, "Subcategoría eliminada.")
        return redirect("tracker:categories")


class CategoryRuleUpdateView(LoginRequiredMixin, UpdateView):
    template_name = "tracker/rule_form.html"
    form_class = CategoryRuleForm
    model = models.CategoryRule

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_queryset(self):
        qs = super().get_queryset()
        if hasattr(models.CategoryRule, "user_id"):
            qs = qs.filter(user=self.request.user)
        return qs

    def form_valid(self, form):
        messages.success(self.request, "Regla actualizada.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("tracker:rules")
