from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from . import models


@admin.register(models.Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    ordering = ("name",)

    def save_model(self, request, obj, form, change):
        if hasattr(obj, "user") and not obj.user:
            obj.user = request.user
        super().save_model(request, obj, form, change)


@admin.register(models.Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ("label", "last4", "bank_name", "is_active", "updated_at")
    list_filter = ("is_active", "bank_name")
    search_fields = ("label", "last4", "bank_name")

    def save_model(self, request, obj, form, change):
        if hasattr(obj, "user") and not obj.user:
            obj.user = request.user
        super().save_model(request, obj, form, change)


@admin.register(models.EmailMessage)
class EmailMessageAdmin(admin.ModelAdmin):
    list_display = ("subject", "sender", "internal_date", "processed_at", "parse_attempts")
    search_fields = ("subject", "sender", "gmail_message_id")
    ordering = ("-internal_date",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(models.Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "merchant_name",
        "amount",
        "currency_code",
        "transaction_date",
        "parse_status",
        "category",
        "category_source",
        "email_message_link",
    )
    list_filter = ("parse_status", "currency_code", "category")
    search_fields = ("merchant_name", "reference_id", "card_last4")
    autocomplete_fields = ("email", "card", "category")
    readonly_fields = ("email_message_link",)

    def email_message_link(self, obj):
        if obj.email_id:
            url = reverse("admin:tracker_emailmessage_change", args=[obj.email_id])
            label = obj.gmail_message_id or obj.email_id
            return format_html('<a href="{}">{}</a>', url, label)
        return "-"

    email_message_link.short_description = "Email Record"


@admin.register(models.LLMDecisionLog)
class LLMDecisionLogAdmin(admin.ModelAdmin):
    list_display = ("decision_type", "model_name", "email", "transaction", "created_at")
    list_filter = ("decision_type", "model_name")
    search_fields = ("model_name",)
    autocomplete_fields = ("email", "transaction")
    actions = ("promote_to_rule",)

    def promote_to_rule(self, request, queryset):
        created = 0
        for log in queryset:
            cat_id = (log.metadata or {}).get("category_id")
            merchant = log.transaction.merchant_name if log.transaction else ""
            if not cat_id or not merchant:
                continue
            try:
                category = models.Category.objects.get(id=cat_id)
            except models.Category.DoesNotExist:
                continue
            models.CategoryRule.objects.get_or_create(
                name=f"LLM:{merchant}",
                category=category,
                defaults={
                    "match_field": models.CategoryRule.MatchField.MERCHANT,
                    "match_type": models.CategoryRule.MatchType.CONTAINS,
                    "match_value": merchant[:255],
                    "priority": 50,
                    "confidence": 0.75,
                },
            )
            created += 1
        self.message_user(request, f"Se promovieron {created} reglas.")

    promote_to_rule.short_description = "Crear regla desde decisión LLM"


@admin.register(models.GmailCredential)
class GmailCredentialAdmin(admin.ModelAdmin):
    list_display = ("user_email", "token_expiry", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("user_email",)


@admin.register(models.GmailSyncState)
class GmailSyncStateAdmin(admin.ModelAdmin):
    list_display = (
        "label",
        "user_email",
        "history_id",
        "last_synced_at",
        "fetched_messages",
        "retry_count",
    )
    search_fields = ("label", "user_email", "history_id")


@admin.register(models.CategoryRule)
class CategoryRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "match_field", "match_type", "priority", "is_active")
    list_filter = ("match_field", "match_type", "is_active", "category")
    search_fields = ("name", "match_value", "notes")
    autocomplete_fields = ("category",)

    def save_model(self, request, obj, form, change):
        if hasattr(obj, "user") and not obj.user:
            obj.user = request.user
        super().save_model(request, obj, form, change)
