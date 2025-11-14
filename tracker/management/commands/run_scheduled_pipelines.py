import logging

from django.core.management import BaseCommand, call_command

from tracker import models

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Iterate through active users/email accounts and run the ingestion pipeline for each."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help="Limit passed to run_pipeline for processing/categorization.",
        )
        parser.add_argument(
            "--max-users",
            type=int,
            help="Optional cap on number of users processed in this run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List the users that would be processed without invoking run_pipeline.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        max_users = options.get("max_users")
        dry_run = options.get("dry_run", False)

        user_emails = self._active_user_emails(max_users=max_users)
        if not user_emails:
            self.stdout.write("No active email accounts found.")
            return

        self.stdout.write(
            f"Processing {len(user_emails)} user(s){' (dry-run)' if dry_run else ''}..."
        )
        for index, email in enumerate(user_emails, start=1):
            prefix = f"[{index}/{len(user_emails)}] {email}"
            if dry_run:
                self.stdout.write(f"{prefix} (skipped – dry run)")
                continue
            try:
                self.stdout.write(f"{prefix} → running pipeline...")
                call_command("run_pipeline", user_email=email, limit=limit)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.exception("Scheduled pipeline failed for %s", email)
                self.stderr.write(f"{prefix} failed: {exc}")
            else:
                self.stdout.write(self.style.SUCCESS(f"{prefix} ✓ complete"))

    def _active_user_emails(self, max_users=None):
        emails = []
        seen = set()
        qs = (
            models.EmailAccount.objects.filter(is_active=True)
            .select_related("user")
            .order_by("user__email")
        )
        for account in qs:
            user = account.user
            if not user or not user.email:
                continue
            if user.email in seen:
                continue
            seen.add(user.email)
            emails.append(user.email)
            if max_users and len(emails) >= max_users:
                break
        return emails
