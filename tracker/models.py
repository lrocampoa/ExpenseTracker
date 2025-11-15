from typing import Optional
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


class TimeStampedModel(models.Model):
    """Abstract base with created/updated timestamps."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ("-created_at",)


class SpendingGroup(TimeStampedModel):
    class GroupType(models.TextChoices):
        FAMILY = ("family", "Family")
        ROOMMATES = ("roommates", "Roommates")
        BUSINESS = ("business", "Business/Partners")
        OTHER = ("other", "Other")

    name = models.CharField(max_length=128)
    slug = models.SlugField(max_length=150, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="created_spending_groups",
    )
    currency_code = models.CharField(max_length=12, blank=True)
    group_type = models.CharField(max_length=32, choices=GroupType.choices, default=GroupType.FAMILY)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class GroupMembership(TimeStampedModel):
    class Role(models.TextChoices):
        ADMIN = ("admin", "Admin")
        MEMBER = ("member", "Member")

    class Status(models.TextChoices):
        INVITED = ("invited", "Invited")
        ACTIVE = ("active", "Active")
        LEFT = ("left", "Left")

    group = models.ForeignKey(
        SpendingGroup,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="group_memberships",
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.INVITED)
    budget_share_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sent_group_invitations",
    )
    joined_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        constraints = [
            models.UniqueConstraint(fields=("group", "user"), name="unique_members_per_group"),
        ]

    def activate(self):
        self.status = self.Status.ACTIVE
        self.joined_at = timezone.now()
        self.save(update_fields=["status", "joined_at", "updated_at"])

    def __str__(self) -> str:
        return f"{self.user} -> {self.group}"


class Category(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="categories",
        null=True,
        blank=True,
    )
    group = models.ForeignKey(
        SpendingGroup,
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
                condition=Q(user__isnull=False),
                name="unique_category_code_per_user",
            ),
            models.UniqueConstraint(
                fields=("group", "code"),
                condition=Q(group__isnull=False),
                name="unique_category_code_per_group",
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
    group = models.ForeignKey(
        SpendingGroup,
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


class CategorySuggestion(TimeStampedModel):
    class SuggestionType(models.TextChoices):
        CATEGORY = ("category", "Category")
        SUBCATEGORY = ("subcategory", "Subcategory")

    class Status(models.TextChoices):
        PENDING = ("pending", "Pending")
        APPROVED = ("approved", "Approved")
        REJECTED = ("rejected", "Rejected")

    group = models.ForeignKey(
        SpendingGroup,
        on_delete=models.CASCADE,
        related_name="category_suggestions",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="category_suggestions",
    )
    suggestion_type = models.CharField(max_length=16, choices=SuggestionType.choices)
    name = models.CharField(max_length=128)
    parent_category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="suggested_children",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_category_suggestions",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.get_suggestion_type_display()} · {self.name}"


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
        null=True,
        blank=True,
    )
    group = models.ForeignKey(
        SpendingGroup,
        on_delete=models.CASCADE,
        related_name="expense_accounts",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=128)
    is_default = models.BooleanField(default=False)
    share_history_from = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Share transactions occurring on or after this timestamp with the selected group. "
            "Leave empty to share the full history."
        ),
    )

    class Meta(TimeStampedModel.Meta):
        unique_together = ("user", "name")
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=("group", "name"),
                condition=Q(group__isnull=False),
                name="unique_expense_account_per_group",
            )
        ]

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


class ImportJob(TimeStampedModel):
    class Status(models.TextChoices):
        QUEUED = ("queued", "En cola")
        SYNCING = ("syncing", "Sincronizando correos")
        PROCESSING = ("processing", "Procesando transacciones")
        COMPLETED = ("completed", "Completado")
        FAILED = ("failed", "Con errores")

    ACTIVE_STATUSES = {Status.QUEUED, Status.SYNCING, Status.PROCESSING}

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="import_jobs",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    range_choice = models.CharField(max_length=16, blank=True)
    after_date = models.DateField(null=True, blank=True)
    gmail_query = models.TextField(blank=True)
    outlook_query = models.TextField(blank=True)
    max_messages = models.PositiveIntegerField(default=50)
    fetched_count = models.PositiveIntegerField(default=0)
    processed_total = models.PositiveIntegerField(default=0)
    processed_messages = models.PositiveIntegerField(default=0)
    created_transactions = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_progress_at = models.DateTimeField(null=True, blank=True)

    class Meta(TimeStampedModel.Meta):
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"ImportJob {self.pk} ({self.get_status_display()})"

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE_STATUSES

    @property
    def progress_percent(self) -> int:
        if self.status == self.Status.QUEUED:
            return 5
        if self.status == self.Status.SYNCING:
            return 25
        if self.status == self.Status.PROCESSING:
            if self.processed_total:
                return min(
                    99,
                    max(30, int((self.processed_messages / self.processed_total) * 70 + 30)),
                )
            return 35
        return 100

    def mark_syncing(self):
        now = timezone.now()
        updates = {
            "status": self.Status.SYNCING,
            "started_at": self.started_at or now,
            "last_progress_at": now,
            "error_message": "",
        }
        self._apply_updates(updates)

    def mark_processing(self, total_messages: int):
        now = timezone.now()
        updates = {
            "status": self.Status.PROCESSING,
            "processed_total": total_messages,
            "fetched_count": max(self.fetched_count, total_messages),
            "last_progress_at": now,
            "processed_messages": 0,
            "created_transactions": 0,
            "error_count": 0,
        }
        self._apply_updates(updates)

    def mark_completed(self):
        now = timezone.now()
        updates = {
            "status": self.Status.COMPLETED,
            "finished_at": now,
            "last_progress_at": now,
        }
        self._apply_updates(updates)

    def mark_failed(self, message: str):
        now = timezone.now()
        updates = {
            "status": self.Status.FAILED,
            "finished_at": now,
            "last_progress_at": now,
            "error_message": message[:2000],
        }
        self._apply_updates(updates)

    def increment_processed(self, created: bool = False, errored: bool = False):
        updates = {
            "processed_messages": F("processed_messages") + 1,
            "last_progress_at": timezone.now(),
        }
        if created:
            updates["created_transactions"] = F("created_transactions") + 1
        if errored:
            updates["error_count"] = F("error_count") + 1
        self.__class__.objects.filter(pk=self.pk).update(**updates)
        self.refresh_from_db(
            fields=[
                "processed_messages",
                "created_transactions",
                "error_count",
                "last_progress_at",
            ]
        )

    def _apply_updates(self, updates: dict):
        for field, value in updates.items():
            setattr(self, field, value)
        self.save(update_fields=list(updates.keys()) + ["updated_at"])


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
    group = models.ForeignKey(
        SpendingGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
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
    needs_review = models.BooleanField(default=False, db_index=True)

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

    @property
    def source_email_address(self) -> str:
        """Return a friendly email address for the source mailbox."""

        if not self.email:
            return ""

        if self.email.mailbox_email:
            return self.email.mailbox_email

        account = self.email.account
        if account and account.email_address:
            return account.email_address

        return ""


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
