from django.conf import settings
from django.db import models


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
    code = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta(TimeStampedModel.Meta):
        verbose_name_plural = "Categories"

    def __str__(self) -> str:
        return self.name


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


class EmailMessage(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="emails",
        null=True,
        blank=True,
    )
    gmail_message_id = models.CharField(max_length=128, unique=True)
    thread_id = models.CharField(max_length=128, blank=True)
    history_id = models.CharField(max_length=64, blank=True)
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

    name = models.CharField(max_length=128)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="rules")
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
    min_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    priority = models.PositiveIntegerField(default=100)
    confidence = models.FloatField(default=0.8)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ("priority", "-confidence", "name")

    def __str__(self) -> str:
        return f"{self.name} -> {self.category.name}"
