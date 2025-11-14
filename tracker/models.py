from typing import Optional

from django.conf import settings
from django.db import models
from django.db.models import Q


class TimeStampedModel(models.Model):
    """Abstract base with created/updated timestamps."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ("-created_at",)


class Category(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="categories",
        null=True,
        blank=True,
    )
    code = models.SlugField(max_length=64)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    budget_limit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta(TimeStampedModel.Meta):
        verbose_name_plural = "Categories"
        constraints = [
            models.UniqueConstraint(
                fields=("user", "code"),
                name="unique_category_code_per_user",
            )
        ]

    def __str__(self) -> str:
        return self.name


class Subcategory(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subcategories",
        null=True,
        blank=True,
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="subcategories",
    )
    code = models.SlugField(max_length=64)
    name = models.CharField(max_length=128)
    budget_limit = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        unique_together = ("category", "code")
        ordering = ("name",)

    def __str__(self) -> str:
        return f"{self.category.name} · {self.name}"


class Card(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cards",
        null=True,
        blank=True,
    )
    label = models.CharField(max_length=128)
    last4 = models.CharField(max_length=4, unique=True)
    bank_name = models.CharField(max_length=128, blank=True)
    network = models.CharField(max_length=32, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    expense_account = models.CharField(
        max_length=128,
        blank=True,
        help_text="Cuenta o categoría contable asociada a esta tarjeta.",
    )

    class Meta(TimeStampedModel.Meta):
        ordering = ("label",)

    def __str__(self) -> str:
        label = self.label or "Card"
        return f"{label} ••••{self.last4}"


class ExpenseAccount(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="expense_accounts",
    )
    name = models.CharField(max_length=128)
    is_default = models.BooleanField(default=False)

    class Meta(TimeStampedModel.Meta):
        unique_together = ("user", "name")
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class EmailAccount(TimeStampedModel):
    class Provider(models.TextChoices):
        GMAIL = ("gmail", "Gmail / Google Workspace")
        OUTLOOK = ("outlook", "Outlook / Hotmail / Office 365")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="email_accounts",
        null=True,
        blank=True,
    )
    provider = models.CharField(max_length=32, choices=Provider.choices)
    email_address = models.EmailField()
    display_name = models.CharField(max_length=255, blank=True)
    label = models.CharField(max_length=64, blank=True, help_text="Friendly name shown in settings.")
    token_json = models.JSONField(default=dict, blank=True)
    scopes = models.JSONField(default=list, blank=True)
    token_expiry = models.DateTimeField(null=True, blank=True)
    refresh_token = models.CharField(max_length=512, blank=True)
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta(TimeStampedModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=("provider", "email_address"),
                name="unique_provider_email_account",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_provider_display()} • {self.email_address}"


class MailSyncState(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="mail_sync_states",
        null=True,
        blank=True,
    )
    account = models.ForeignKey(
        "EmailAccount",
        on_delete=models.CASCADE,
        related_name="sync_states",
        null=True,
        blank=True,
    )
    provider = models.CharField(
        max_length=32,
        choices=EmailAccount.Provider.choices,
        default=EmailAccount.Provider.GMAIL,
    )
    label = models.CharField(max_length=64, default="primary")
    query = models.CharField(max_length=500, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    fetched_messages = models.PositiveIntegerField(default=0)
    retry_count = models.PositiveIntegerField(default=0)
    checkpoint = models.JSONField(default=dict, blank=True, help_text="Provider-specific cursor state.")

    class Meta(TimeStampedModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=("account", "label"),
                name="unique_mail_sync_label_per_account",
            )
        ]

    def __str__(self) -> str:
        label = self.label or "primary"
        provider = self.get_provider_display()
        return f"{provider} sync ({label})"

    @classmethod
    def latest_for_account(cls, account: "EmailAccount", label: Optional[str] = "primary") -> Optional["MailSyncState"]:
        """Return the newest sync state for the given account/label combination."""

        if not account:
            return None
        lookup_label = label or "primary"
        return (
            cls.objects.filter(account=account, label=lookup_label)
            .order_by("-updated_at")
            .first()
        )

    def checkpoint_dict(self) -> dict:
        """Safe helper to treat checkpoint JSONField as a dictionary."""

        return self.checkpoint if isinstance(self.checkpoint, dict) else {}

    def last_history_id(self) -> Optional[str]:
        """Convenience accessor for the stored Gmail historyId."""

        checkpoint = self.checkpoint_dict()
        history_id = checkpoint.get("history_id")
        return str(history_id) if history_id else None


class EmailMessage(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="emails",
        null=True,
        blank=True,
    )
    account = models.ForeignKey(
        "EmailAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="emails",
    )
    provider = models.CharField(
        max_length=32,
        choices=EmailAccount.Provider.choices,
        default=EmailAccount.Provider.GMAIL,
    )
    mailbox_email = models.EmailField(blank=True)
    gmail_message_id = models.CharField(max_length=128, unique=True, null=True, blank=True)
    thread_id = models.CharField(max_length=128, blank=True)
    history_id = models.CharField(max_length=64, blank=True)
    external_message_id = models.CharField(max_length=255, blank=True)
    internet_message_id = models.CharField(max_length=255, blank=True)
    subject = models.CharField(max_length=255, blank=True)
    sender = models.CharField(max_length=255, blank=True)
    snippet = models.TextField(blank=True)
    internal_date = models.DateTimeField(null=True, blank=True)
    raw_payload = models.JSONField(blank=True, null=True)
    raw_body = models.TextField(blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    parse_attempts = models.PositiveIntegerField(default=0)

    class Meta(TimeStampedModel.Meta):
        ordering = ("-internal_date", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=("provider", "external_message_id"),
                condition=~Q(external_message_id=""),
                name="unique_external_message_per_provider",
            )
        ]

    def __str__(self) -> str:
        return f"{self.subject or 'Email'} ({self.gmail_message_id})"


class Transaction(TimeStampedModel):
    class ParseStatus(models.TextChoices):
        PENDING = ("pending", "Pending")
        PARSED = ("parsed", "Parsed")
        FAILED = ("failed", "Failed")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transactions",
        null=True,
        blank=True,
    )
    email = models.ForeignKey(
        EmailMessage,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    card = models.ForeignKey(
        Card,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    subcategory = models.ForeignKey(
        "Subcategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    merchant_name = models.CharField(max_length=255, blank=True)
    transaction_date = models.DateTimeField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency_code = models.CharField(max_length=12, default="CRC")
    card_last4 = models.CharField(max_length=4, blank=True)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=255, blank=True)
    reference_id = models.CharField(max_length=64, blank=True)
    parse_status = models.CharField(
        max_length=16, choices=ParseStatus.choices, default=ParseStatus.PENDING
    )
    parse_confidence = models.FloatField(null=True, blank=True)
    category_confidence = models.FloatField(null=True, blank=True)
    category_source = models.CharField(max_length=32, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta(TimeStampedModel.Meta):
        constraints = [
            models.UniqueConstraint(
                fields=("email", "reference_id"),
                name="unique_transaction_reference_per_email",
            )
        ]

    def __str__(self) -> str:
        amount = f"{self.amount} {self.currency_code}" if self.amount is not None else "Amount N/A"
        return f"{self.merchant_name or 'Transaction'} - {amount}"

    @property
    def gmail_message_id(self) -> str:
        if self.email_id and self.email.gmail_message_id:
            return self.email.gmail_message_id
        return ""

    @property
    def gmail_message_url(self) -> str:
        message_id = self.gmail_message_id
        if not message_id:
            return ""
        return f"https://mail.google.com/mail/u/0/#all/{message_id}"


class LLMDecisionLog(TimeStampedModel):
    class DecisionType(models.TextChoices):
        PARSING = ("parsing", "Parsing")
        CATEGORIZATION = ("categorization", "Categorization")
        OTHER = ("other", "Other")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="llm_logs",
        null=True,
        blank=True,
    )
    email = models.ForeignKey(
        EmailMessage,
        on_delete=models.CASCADE,
        related_name="llm_logs",
    )
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        related_name="llm_logs",
        null=True,
        blank=True,
    )
    decision_type = models.CharField(max_length=32, choices=DecisionType.choices)
    model_name = models.CharField(max_length=100)
    prompt = models.TextField()
    response = models.TextField(blank=True)
    tokens_prompt = models.PositiveIntegerField(null=True, blank=True)
    tokens_completion = models.PositiveIntegerField(null=True, blank=True)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    cache_key = models.CharField(max_length=255, blank=True, db_index=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.model_name} {self.decision_type}"


class GmailCredential(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="gmail_credentials",
        null=True,
        blank=True,
    )
    user_email = models.EmailField(unique=True)
    token_json = models.JSONField(default=dict)
    scopes = models.JSONField(default=list, blank=True)
    token_expiry = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ("user_email",)

    def __str__(self) -> str:
        return f"Gmail Credential ({self.user_email})"


class GmailSyncState(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="gmail_sync_states",
        null=True,
        blank=True,
    )
    label = models.CharField(max_length=64, unique=True, default="primary")
    user_email = models.EmailField(blank=True)
    history_id = models.CharField(max_length=128, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    query = models.CharField(max_length=500, blank=True)
    fetched_messages = models.PositiveIntegerField(default=0)
    retry_count = models.PositiveIntegerField(default=0)

    class Meta(TimeStampedModel.Meta):
        ordering = ("label",)

    def __str__(self) -> str:
        return f"Gmail Sync ({self.label})"


class CategoryRule(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="category_rules",
        null=True,
        blank=True,
    )
    class MatchField(models.TextChoices):
        MERCHANT = ("merchant", "Merchant Name")
        DESCRIPTION = ("description", "Description")
        CARD_LAST4 = ("card_last4", "Card Last4")
        ANY_TEXT = ("any", "Any Text")

    class MatchType(models.TextChoices):
        CONTAINS = ("contains", "Contains")
        STARTS_WITH = ("starts_with", "Starts With")
        ENDS_WITH = ("ends_with", "Ends With")
        EXACT = ("exact", "Exact")
        REGEX = ("regex", "Regex")
        ALWAYS = ("always", "Always Match")

    class Origin(models.TextChoices):
        MANUAL = ("manual", "Manual")
        PROMOTED = ("promoted", "Manual Promotion")
        SUGGESTED = ("suggested", "Accepted Suggestion")
        SEEDED = ("seeded", "Default Seed")

    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="rules")
    subcategory = models.ForeignKey(
        Subcategory,
        on_delete=models.CASCADE,
        related_name="rules",
        null=True,
        blank=True,
    )
    match_value = models.CharField(max_length=255, blank=True)
    match_field = models.CharField(
        max_length=32, choices=MatchField.choices, default=MatchField.MERCHANT
    )
    match_type = models.CharField(
        max_length=32, choices=MatchType.choices, default=MatchType.CONTAINS
    )
    card_last4 = models.CharField(
        max_length=4,
        blank=True,
        help_text="Optional: restrict rule to a specific card last4.",
    )
    priority = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    origin = models.CharField(max_length=32, choices=Origin.choices, default=Origin.MANUAL)

    class Meta(TimeStampedModel.Meta):
        ordering = ("priority", "match_value")

    def __str__(self) -> str:
        label = self.match_value or "Regla"
        return f"{label} -> {self.category.name}"


class TransactionCorrection(TimeStampedModel):
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        related_name="corrections",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transaction_corrections",
    )
    previous_category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    new_category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    previous_subcategory = models.ForeignKey(
        "Subcategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    new_subcategory = models.ForeignKey(
        "Subcategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    previous_merchant_name = models.CharField(max_length=255, blank=True)
    new_merchant_name = models.CharField(max_length=255, blank=True)
    previous_description = models.TextField(blank=True)
    new_description = models.TextField(blank=True)
    previous_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    new_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    previous_currency_code = models.CharField(max_length=12, blank=True)
    new_currency_code = models.CharField(max_length=12, blank=True)
    previous_transaction_date = models.DateTimeField(null=True, blank=True)
    new_transaction_date = models.DateTimeField(null=True, blank=True)
    changed_fields = models.JSONField(default=list, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"Correction for {self.transaction_id} by {self.user_id or 'system'}"


class RuleSuggestion(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = ("pending", "Pending")
        ACCEPTED = ("accepted", "Accepted")
        REJECTED = ("rejected", "Rejected")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="rule_suggestions",
        null=True,
        blank=True,
    )
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rule_suggestions",
    )
    correction = models.ForeignKey(
        TransactionCorrection,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rule_suggestions",
    )
    merchant_name = models.CharField(max_length=255)
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="rule_suggestions",
    )
    card_last4 = models.CharField(max_length=4, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    reason = models.CharField(max_length=255, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("user", "merchant_name", "category", "card_last4", "status"),
                condition=Q(status="pending"),
                name="unique_pending_suggestion",
            )
        ]

    def __str__(self):
        return f"Suggestion {self.merchant_name} -> {self.category} ({self.status})"
