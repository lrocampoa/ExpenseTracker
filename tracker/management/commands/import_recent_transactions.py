from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from django.core.management import BaseCommand, CommandError, call_command
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from tracker import models


class Command(BaseCommand):
    help = "Incrementally import only the most recent, missing transactions for targeted mailboxes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--provider",
            choices=[models.EmailAccount.Provider.GMAIL, models.EmailAccount.Provider.OUTLOOK],
            help="Restrict mailbox sync to a single provider.",
        )
        parser.add_argument(
            "--account-email",
            help="Only sync/process the mailbox with this email address.",
        )
        parser.add_argument(
            "--user-email",
            help="Scope the import to a specific ExpenseTracker user.",
        )
        parser.add_argument(
            "--since",
            help="ISO-8601 timestamp; only process emails/transactions newer than this value.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help="Maximum number of emails/transactions to process.",
        )
        parser.add_argument(
            "--max-messages",
            type=int,
            default=None,
            help="Override max messages per mailbox sync (defaults to settings value).",
        )
        parser.add_argument(
            "--skip-sync",
            action="store_true",
            help="Skip the mailbox sync step (expects emails already stored).",
        )
        parser.add_argument(
            "--skip-process",
            action="store_true",
            help="Skip turning new EmailMessage rows into transactions.",
        )
        parser.add_argument(
            "--skip-categorize",
            action="store_true",
            help="Skip running categorization after parsing.",
        )
        parser.add_argument(
            "--allow-llm",
            action="store_true",
            help="Allow categorization to invoke the LLM fallback during this import.",
        )

    def handle(self, *args, **options):
        provider = options.get("provider")
        account_email = options.get("account_email")
        user_email = options.get("user_email")
        limit = options.get("limit")
        max_messages = options.get("max_messages")
        since_dt = self._resolve_since(options.get("since"), account_email, user_email)
        since_iso = since_dt.isoformat() if since_dt else None
        run_start = timezone.now()
        scope = {"account_email": account_email, "user_email": user_email}

        if not options["skip_sync"]:
            sync_kwargs: Dict[str, Any] = {}
            if provider:
                sync_kwargs["provider"] = provider
            if account_email:
                sync_kwargs["account_email"] = account_email
            if user_email:
                sync_kwargs["user_email"] = user_email
            if max_messages:
                sync_kwargs["max_messages"] = max_messages
            call_command("sync_mailboxes", **sync_kwargs)

        if not options["skip_process"]:
            process_kwargs = {"limit": limit}
            if user_email:
                process_kwargs["user_email"] = user_email
            if account_email:
                process_kwargs["account_email"] = account_email
            if since_iso:
                process_kwargs["since"] = since_iso
            call_command("process_emails", **process_kwargs)

        if not options["skip_categorize"]:
            categorize_kwargs = {"limit": limit}
            if user_email:
                categorize_kwargs["user_email"] = user_email
            if account_email:
                categorize_kwargs["account_email"] = account_email
            if since_iso:
                categorize_kwargs["since"] = since_iso
            if options.get("allow_llm"):
                categorize_kwargs["llm"] = True
            call_command("categorize_transactions", **categorize_kwargs)

        self._emit_summary(scope, run_start)

    def _resolve_since(
        self,
        since_option: Optional[str],
        account_email: Optional[str],
        user_email: Optional[str],
    ) -> Optional[datetime]:
        if since_option:
            parsed = parse_datetime(since_option)
            if not parsed:
                raise CommandError("--since must be an ISO-8601 datetime string.")
            return parsed
        if not account_email:
            return None
        derived = self._derive_since_from_data(account_email, user_email)
        if derived:
            self.stdout.write(
                self.style.NOTICE(f"No --since provided; using last transaction timestamp {derived.isoformat()}.")
            )
        return derived

    def _derive_since_from_data(self, account_email: str, user_email: Optional[str]) -> Optional[datetime]:
        transaction_qs = models.Transaction.objects.filter(email__account__email_address__iexact=account_email)
        if user_email and hasattr(models.Transaction, "user_id"):
            transaction_qs = transaction_qs.filter(user__email__iexact=user_email)
        latest_trx = transaction_qs.order_by("-transaction_date", "-created_at").values_list("transaction_date", flat=True).first()
        if latest_trx:
            return latest_trx
        email_qs = models.EmailMessage.objects.filter(account__email_address__iexact=account_email)
        if user_email and hasattr(models.EmailMessage, "user_id"):
            email_qs = email_qs.filter(user__email__iexact=user_email)
        return email_qs.order_by("-internal_date", "-created_at").values_list("internal_date", flat=True).first()

    def _emit_summary(self, scope: Dict[str, Optional[str]], since_time: datetime) -> None:
        emails_qs = self._scoped_emails(scope).filter(created_at__gte=since_time)
        processed_qs = self._scoped_emails(scope).filter(processed_at__gte=since_time)
        transactions_qs = self._scoped_transactions(scope)
        created_transactions = transactions_qs.filter(created_at__gte=since_time).count()
        touched_transactions = transactions_qs.filter(updated_at__gte=since_time).count()
        self.stdout.write(
            self.style.SUCCESS(
                "Import summary → emails fetched {created}, parsed {processed}, "
                "transactions created {trx_created}, touched {trx_touched}.".format(
                    created=emails_qs.count(),
                    processed=processed_qs.count(),
                    trx_created=created_transactions,
                    trx_touched=touched_transactions,
                )
            )
        )

    def _scoped_emails(self, scope: Dict[str, Optional[str]]):
        qs = models.EmailMessage.objects.all()
        account_email = scope.get("account_email")
        user_email = scope.get("user_email")
        if account_email:
            qs = qs.filter(account__email_address__iexact=account_email)
        if user_email and hasattr(models.EmailMessage, "user_id"):
            qs = qs.filter(user__email__iexact=user_email)
        return qs

    def _scoped_transactions(self, scope: Dict[str, Optional[str]]):
        qs = models.Transaction.objects.all()
        account_email = scope.get("account_email")
        user_email = scope.get("user_email")
        if account_email:
            qs = qs.filter(email__account__email_address__iexact=account_email)
        if user_email and hasattr(models.Transaction, "user_id"):
            qs = qs.filter(user__email__iexact=user_email)
        return qs
