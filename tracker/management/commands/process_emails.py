from django.core.management.base import BaseCommand

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

    def handle(self, *args, **options):
        limit = options["limit"]
        process_all = options["all"]
        queryset = models.EmailMessage.objects.order_by("-internal_date")
        if not process_all:
            queryset = queryset.filter(processed_at__isnull=True)
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
