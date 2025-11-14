from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils.dateparse import parse_datetime

from tracker import models
from tracker.services import parser


class Command(BaseCommand):
    help = "Parse unprocessed EmailMessage rows into Transaction records."

    def add_arguments(self, parser_arg):
        parser_arg.add_argument(
            "--limit",
            type=int,
            default=100,
            help="Maximum emails to process in this run.",
        )
        parser_arg.add_argument(
            "--all",
            action="store_true",
            help="Process all emails regardless of parsed state.",
        )
        parser_arg.add_argument(
            "--user-email",
            help="Process emails belonging to this user (by email).",
        )
        parser_arg.add_argument(
            "--account-email",
            help="Process emails tied to this mailbox email address.",
        )
        parser_arg.add_argument(
            "--since",
            help="Only process emails with internal_date/created_at >= this ISO-8601 timestamp.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        process_all = options["all"]
        queryset = models.EmailMessage.objects.order_by("-internal_date")
        if not process_all:
            queryset = queryset.filter(processed_at__isnull=True)
        user_email = options.get("user_email")
        if user_email and hasattr(models.EmailMessage, "user_id"):
            queryset = queryset.filter(user__email__iexact=user_email)
        account_email = options.get("account_email")
        if account_email:
            queryset = queryset.filter(account__email_address__iexact=account_email)
        since_value = options.get("since")
        if since_value:
            since_dt = parse_datetime(since_value)
            if not since_dt:
                raise CommandError("--since must be an ISO-8601 datetime string.")
            queryset = queryset.filter(Q(internal_date__gte=since_dt) | Q(created_at__gte=since_dt))
        processed = 0
        created = 0
        for email in queryset[:limit]:
            transaction = parser.create_transaction_from_email(email)
            processed += 1
            if transaction:
                created += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {processed} emails ({created} transactions created/updated)."
            )
        )
