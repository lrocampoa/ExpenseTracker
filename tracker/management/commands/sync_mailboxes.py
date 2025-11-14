import logging
import re
from typing import Dict, List, Optional, Tuple

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand

from tracker import models
from tracker.services.gmail import (
    GmailCredentialManager,
    GmailIngestionService,
    MissingCredentialsError,
)
from tracker.services.outlook import (
    MissingOutlookCredentialsError,
    OutlookCredentialManager,
    OutlookIngestionService,
)

logger = logging.getLogger(__name__)
EMAIL_RE = re.compile(r"([\w+\.-]+@[\w\.-]+)")


class Command(BaseCommand):
    help = "Sync all active email accounts (Gmail + Outlook) and store messages in the database."

    def add_arguments(self, parser):
        parser.add_argument("--provider", choices=["gmail", "outlook"], help="Filter by provider.")
        parser.add_argument(
            "--account-email",
            help="Sync only the mailbox with this email address (case insensitive).",
        )
        parser.add_argument(
            "--user-email",
            help="Sync only accounts belonging to this ExpenseTracker user.",
        )
        parser.add_argument(
            "--max",
            dest="max_messages",
            type=int,
            default=settings.GMAIL_MAX_MESSAGES_PER_SYNC,
            help="Maximum messages to fetch per account.",
        )
        parser.add_argument(
            "--gmail-query",
            help="Override the Gmail search query (defaults to settings.GMAIL_SEARCH_QUERY).",
        )
        parser.add_argument(
            "--outlook-query",
            help="Override the Outlook search string (defaults to settings.OUTLOOK_SEARCH_QUERY).",
        )

    def handle(self, *args, **options):
        provider = options.get("provider")
        account_email = options.get("account_email")
        user_email = options.get("user_email")
        max_messages = options.get("max_messages") or settings.GMAIL_MAX_MESSAGES_PER_SYNC
        gmail_query = options.get("gmail_query") or settings.GMAIL_SEARCH_QUERY
        outlook_query = options.get("outlook_query") or settings.OUTLOOK_SEARCH_QUERY

        qs = models.EmailAccount.objects.filter(is_active=True)
        if provider:
            qs = qs.filter(provider=provider)
        if account_email:
            qs = qs.filter(email_address__iexact=account_email)
        if user_email and hasattr(models.EmailAccount, "user_id"):
            qs = qs.filter(user__email__iexact=user_email)

        accounts = list(qs.order_by("provider", "email_address"))
        if not accounts:
            self.stdout.write("No matching email accounts were found.")
            return

        for account in accounts:
            if account.provider == models.EmailAccount.Provider.GMAIL:
                self._sync_gmail_account(account, gmail_query, max_messages)
            elif account.provider == models.EmailAccount.Provider.OUTLOOK:
                self._sync_outlook_account(account, outlook_query, max_messages)

    def _sync_gmail_account(self, account, query, max_messages):
        manager = GmailCredentialManager(user_email=account.email_address, user=account.user, account=account)
        try:
            creds = manager.ensure_credentials()
        except MissingCredentialsError as exc:
            self.stderr.write(f"[Gmail] {account.email_address}: {exc}")
            return
        service = GmailCredentialManager.build_service(creds)
        label = account.label or "primary"
        ingestion = GmailIngestionService(
            service=service,
            account=account,
            query=query,
            label=label,
            max_messages=max_messages,
        )
        result = ingestion.sync()
        self.stdout.write(
            self.style.SUCCESS(
                f"[Gmail] {account.email_address}: fetched {result.fetched} (new {result.created}, skipped {result.skipped})."
            )
        )

    def _sync_outlook_account(self, account, query, max_messages):
        try:
            manager = OutlookCredentialManager(account=account)
            token_response = manager.ensure_credentials()
        except (MissingOutlookCredentialsError, ValueError) as exc:
            self.stderr.write(f"[Outlook] {account.email_address}: {exc}")
            return

        access_token = token_response.get("access_token")
        messages, delta_link = self._fetch_outlook_messages(account, access_token, max_messages, query)
        ingestion = OutlookIngestionService(account=account, max_messages=max_messages, label=account.label or "primary")
        result = ingestion.sync(messages=messages, delta_link=delta_link)
        self.stdout.write(
            self.style.SUCCESS(
                f"[Outlook] {account.email_address}: fetched {result.fetched} (new {result.created}, skipped {result.skipped})."
            )
        )

    def _fetch_outlook_messages(
        self,
        account: models.EmailAccount,
        access_token: str,
        max_messages: int,
        query: Optional[str],
    ) -> Tuple[List[dict], Optional[str]]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "ConsistencyLevel": "eventual",
        }
        remaining = max_messages
        state = (
            models.MailSyncState.objects.filter(account=account, label=account.label or "primary")
            .order_by("-updated_at")
            .first()
        )
        checkpoint = state.checkpoint if state and isinstance(state.checkpoint, dict) else {}
        delta_link = checkpoint.get("delta_link")
        if delta_link:
            url = delta_link
            params = None
        else:
            url = "https://graph.microsoft.com/v1.0/me/messages/delta"
            params = {
                "$top": min(remaining, 50),
                "$select": "id,internetMessageId,subject,bodyPreview,body,receivedDateTime,conversationId,from",
            }
            filter_query = self._build_outlook_filter(query)
            if filter_query:
                params["$filter"] = filter_query
        messages: List[dict] = []
        new_delta_link = delta_link
        try:
            with httpx.Client(timeout=30) as client:
                while url and remaining > 0:
                    response = client.get(url, headers=headers, params=params if params else None)
                    response.raise_for_status()
                    payload = response.json()
                    batch = payload.get("value", [])
                    messages.extend(batch)
                    remaining = max_messages - len(messages)
                    url = payload.get("@odata.nextLink")
                    new_delta_link = payload.get("@odata.deltaLink") or new_delta_link
                    params = None
                    if remaining <= 0:
                        break
        except httpx.HTTPError as exc:
            logger.warning("Outlook fetch failed for %s: %s", account.email_address, exc)
            return [], new_delta_link
        return messages[:max_messages], new_delta_link

    def _build_outlook_filter(self, query: Optional[str]) -> Optional[str]:
        if not query:
            return None
        addresses = self._extract_addresses(query)
        if not addresses:
            return None
        clauses = [f"from/emailAddress/address eq '{address}'" for address in addresses]
        return " or ".join(clauses)

    def _extract_addresses(self, query: str) -> List[str]:
        raw_matches = EMAIL_RE.findall(query or "")
        cleaned: Dict[str, str] = {}
        for match in raw_matches:
            address = match.strip().strip("'\"").strip("()").lower()
            if address:
                cleaned[address] = address
        return list(cleaned.values())
