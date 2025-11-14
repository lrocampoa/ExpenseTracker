from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils.dateparse import parse_datetime

from tracker import models
from tracker.services.categorizer import categorize_transaction


class Command(BaseCommand):
    help = "Apply category rules to transactions that lack a category."

    def add_arguments(self, parser):
        parser.add_argument(
            "--all",
            action="store_true",
            help="Categorize all transactions, not just uncategorized ones.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help="Maximum transactions to process in this run.",
        )
        parser.add_argument(
            "--llm",
            action="store_true",
            help="Allow LLM fallback even if disabled in settings.",
        )
        parser.add_argument(
            "--user-email",
            help="Categorize solo transacciones de este usuario.",
        )
        parser.add_argument(
            "--account-email",
            help="Limit categorization to transactions tied to this mailbox email.",
        )
        parser.add_argument(
            "--since",
            help="Only categorize transactions updated after this ISO-8601 timestamp.",
        )

    def handle(self, *args, **options):
        qs = models.Transaction.objects.order_by("-transaction_date", "-created_at")
        if not options["all"]:
            qs = qs.filter(category__isnull=True)
        user_email = options.get("user_email")
        if user_email and hasattr(models.Transaction, "user_id"):
            qs = qs.filter(user__email__iexact=user_email)
        account_email = options.get("account_email")
        if account_email:
            qs = qs.filter(email__account__email_address__iexact=account_email)
        since_value = options.get("since")
        if since_value:
            since_dt = parse_datetime(since_value)
            if not since_dt:
                raise CommandError("--since must be an ISO-8601 datetime string.")
            qs = qs.filter(Q(transaction_date__gte=since_dt) | Q(updated_at__gte=since_dt))
        limit = options["limit"]
        processed = 0
        categorized = 0
        allow_llm = options.get("llm")
        for trx in qs[:limit]:
            result = categorize_transaction(trx, allow_llm=allow_llm)
            processed += 1
            if result:
                categorized += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {processed} transactions ({categorized} updated)."
            )
        )
