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


@admin.register(models.Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ("label", "last4", "bank_name", "is_active", "updated_at")
    list_filter = ("is_active", "bank_name")
    search_fields = ("label", "last4", "bank_name")


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


@admin.register(models.GmailCredential)
class GmailCredentialAdmin(admin.ModelAdmin):
    list_display = ("user_email", "token_expiry", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("user_email",)


@admin.register(models.GmailSyncState)
class GmailSyncStateAdmin(admin.ModelAdmin):
    list_display = ("label", "user_email", "history_id", "last_synced_at", "fetched_messages")
    search_fields = ("label", "user_email", "history_id")


@admin.register(models.CategoryRule)
class CategoryRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "match_field", "match_type", "priority", "is_active")
    list_filter = ("match_field", "match_type", "is_active", "category")
    search_fields = ("name", "match_value", "notes")
    autocomplete_fields = ("category",)
