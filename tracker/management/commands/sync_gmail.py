from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from tracker.services.gmail import (
    GmailCredentialManager,
    GmailIngestionService,
    MissingCredentialsError,
)


class Command(BaseCommand):
    help = "Fetch Gmail messages that match the configured query and store them in the database."

    def add_arguments(self, parser):
        parser.add_argument("--email", help="Gmail account to sync")
        parser.add_argument("--query", help="Gmail search query (defaults to env GMAIL_SEARCH_QUERY)")
        parser.add_argument(
            "--label",
            default="primary",
            help="Sync state label so multiple queries can keep separate checkpoints.",
        )
        parser.add_argument(
            "--max",
            dest="max_messages",
            type=int,
            default=settings.GMAIL_MAX_MESSAGES_PER_SYNC,
            help="Maximum messages to fetch in this run.",
        )
        parser.add_argument(
            "--interactive",
            action="store_true",
            help="Allow launching the OAuth flow if credentials are missing.",
        )

    def handle(self, *args, **options):
        user_email = options.get("email") or settings.GMAIL_USER_EMAIL
        query = options.get("query") or settings.GMAIL_SEARCH_QUERY
        label = options.get("label") or "primary"
        max_messages = options.get("max_messages") or settings.GMAIL_MAX_MESSAGES_PER_SYNC
        interactive = options.get("interactive", False)

        if not user_email:
            raise CommandError("Provide --email or set GMAIL_USER_EMAIL in the environment.")

        manager = GmailCredentialManager(user_email=user_email)
        try:
            creds = manager.ensure_credentials(allow_interactive=interactive)
        except MissingCredentialsError as exc:
            raise CommandError(str(exc))

        service = GmailCredentialManager.build_service(creds)
        ingestion = GmailIngestionService(
            service=service,
            user_email=manager.user_email,
            query=query,
            label=label,
            max_messages=max_messages,
            user=getattr(manager, "user", None),
        )
        result = ingestion.sync()
        self.stdout.write(
            self.style.SUCCESS(
                "Fetched {fetched} messages ({created} new, {skipped} existing).".format(
                    fetched=result.fetched,
                    created=result.created,
                    skipped=result.skipped,
                )
            )
        )
        if result.last_history_id:
            self.stdout.write(f"Latest historyId: {result.last_history_id}")
