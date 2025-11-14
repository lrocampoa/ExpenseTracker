from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from . import models


@admin.register(models.SpendingGroup)
class SpendingGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "group_type", "is_active", "created_by", "updated_at")
    list_filter = ("group_type", "is_active")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("created_by",)


@admin.register(models.GroupMembership)
class GroupMembershipAdmin(admin.ModelAdmin):
    list_display = ("group", "user", "role", "status", "budget_share_percent", "updated_at")
    list_filter = ("role", "status")
    search_fields = ("group__name", "user__email")
    autocomplete_fields = ("group", "user", "invited_by")


@admin.register(models.Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "budget_limit", "is_active", "group", "user", "updated_at")
    list_filter = ("is_active", "group")
    search_fields = ("name", "code")
    ordering = ("name",)

    def save_model(self, request, obj, form, change):
        if hasattr(obj, "user") and not obj.user and not obj.group_id:
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
        "subcategory",
        "category_source",
        "needs_review",
        "group",
        "email_message_link",
    )
    list_filter = ("parse_status", "currency_code", "category", "subcategory", "needs_review", "group")
    search_fields = ("merchant_name", "reference_id", "card_last4")
    autocomplete_fields = ("email", "card", "category", "subcategory")
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


@admin.register(models.EmailAccount)
class EmailAccountAdmin(admin.ModelAdmin):
    list_display = ("email_address", "provider", "user", "is_active", "updated_at")
    list_filter = ("provider", "is_active")
    search_fields = ("email_address", "user__email", "label")
    autocomplete_fields = ("user",)


@admin.register(models.MailSyncState)
class MailSyncStateAdmin(admin.ModelAdmin):
    list_display = ("label", "provider", "account", "last_synced_at", "fetched_messages", "retry_count")
    list_filter = ("provider",)
    search_fields = ("label", "account__email_address")
    autocomplete_fields = ("account", "user")


@admin.register(models.CategoryRule)
class CategoryRuleAdmin(admin.ModelAdmin):
    list_display = ("match_value", "category", "subcategory", "match_field", "match_type", "priority", "is_active", "origin")
    list_filter = ("match_field", "match_type", "is_active", "category", "subcategory", "origin")
    search_fields = ("match_value", "notes")
    autocomplete_fields = ("category", "subcategory")

    def save_model(self, request, obj, form, change):
        if hasattr(obj, "user") and not obj.user:
            obj.user = request.user
        super().save_model(request, obj, form, change)


@admin.register(models.TransactionCorrection)
class TransactionCorrectionAdmin(admin.ModelAdmin):
    list_display = (
        "transaction",
        "user",
        "previous_category",
        "new_category",
        "previous_subcategory",
        "new_subcategory",
        "created_at",
    )
    list_filter = ("changed_fields", "new_category")
    search_fields = (
        "transaction__merchant_name",
        "previous_merchant_name",
        "new_merchant_name",
    )
    readonly_fields = (
        "previous_merchant_name",
        "new_merchant_name",
        "previous_description",
        "new_description",
        "previous_amount",
        "new_amount",
        "previous_currency_code",
        "new_currency_code",
        "previous_transaction_date",
        "new_transaction_date",
        "changed_fields",
        "previous_subcategory",
        "new_subcategory",
    )


@admin.register(models.Subcategory)
class SubcategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "group", "budget_limit", "updated_at")
    list_filter = ("category", "group")
    search_fields = ("name", "code", "category__name")


@admin.register(models.RuleSuggestion)
class RuleSuggestionAdmin(admin.ModelAdmin):
    list_display = ("merchant_name", "category", "card_last4", "status", "user", "created_at")
    list_filter = ("status", "category")
    search_fields = ("merchant_name", "category__name", "user__email")


@admin.register(models.CategorySuggestion)
class CategorySuggestionAdmin(admin.ModelAdmin):
    list_display = ("name", "suggestion_type", "group", "status", "requested_by", "reviewed_by", "updated_at")
    list_filter = ("suggestion_type", "status", "group")
    search_fields = ("name", "group__name", "requested_by__email")
    autocomplete_fields = ("group", "requested_by", "parent_category", "reviewed_by")


@admin.register(models.ExpenseAccount)
class ExpenseAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "is_default", "created_at")
    list_filter = ("is_default",)
    search_fields = ("name", "user__email")
