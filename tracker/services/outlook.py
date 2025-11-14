"""Scaffolding for Microsoft Graph mail ingestion."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Any, Dict, List, Optional

import msal
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from tracker import models
from tracker.services.gmail import SyncResult

logger = logging.getLogger(__name__)


class MissingOutlookCredentialsError(Exception):
    """Raised when Microsoft Graph credentials are missing."""


def _parse_graph_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_timezone.utc)
        return dt.astimezone(dt_timezone.utc)
    except ValueError:
        logger.warning("Failed to parse Graph datetime: %s", value)
        return None


class OutlookCredentialManager:
    """Refresh and persist Microsoft Graph tokens."""

    def __init__(self, account: models.EmailAccount):
        if account.provider != models.EmailAccount.Provider.OUTLOOK:
            raise ValueError("OutlookCredentialManager requires an Outlook/Hotmail account.")
        self.account = account
        self.scopes = settings.MS_GRAPH_SCOPES or ["https://graph.microsoft.com/Mail.Read"]
        client_id = settings.MS_GRAPH_CLIENT_ID
        client_secret = settings.MS_GRAPH_CLIENT_SECRET
        tenant = settings.MS_GRAPH_TENANT_ID or "common"
        if not client_id or not client_secret:
            raise ImproperlyConfigured("MS_GRAPH_CLIENT_ID and MS_GRAPH_CLIENT_SECRET must be configured.")
        authority = f"https://login.microsoftonline.com/{tenant}"
        self.app = msal.ConfidentialClientApplication(
            client_id=client_id,
            client_credential=client_secret,
            authority=authority,
        )

    def ensure_credentials(self) -> Dict[str, Any]:
        token_data = self.account.token_json or {}
        refresh_token = self.account.refresh_token or token_data.get("refresh_token")
        if not refresh_token:
            raise MissingOutlookCredentialsError(
                "No refresh token stored for this Outlook account. Connect the account again."
            )
        result = self.app.acquire_token_by_refresh_token(refresh_token, scopes=self.scopes)
        if "access_token" not in result:
            error = result.get("error_description") or result.get("error") or "Failed to refresh Outlook token"
            raise MissingOutlookCredentialsError(error)
        self._persist_tokens(result)
        return result

    def _persist_tokens(self, token_response: Dict[str, Any]) -> None:
        expires_in = int(token_response.get("expires_in") or 0)
        expiry = timezone.now() + timedelta(seconds=expires_in) if expires_in else None
        self.account.token_json = token_response
        self.account.token_expiry = expiry
        self.account.refresh_token = token_response.get("refresh_token") or self.account.refresh_token
        self.account.scopes = self.scopes
        self.account.is_active = True
        self.account.save(
            update_fields=["token_json", "token_expiry", "refresh_token", "scopes", "is_active", "updated_at"]
        )

    @staticmethod
    def build_authority() -> str:
        tenant = settings.MS_GRAPH_TENANT_ID or "common"
        return f"https://login.microsoftonline.com/{tenant}"


class OutlookIngestionService:
    """Store Outlook/Hotmail messages in EmailMessage rows."""

    def __init__(
        self,
        account: models.EmailAccount,
        graph_client=None,
        query: Optional[str] = None,
        label: str = "primary",
        max_messages: int = 50,
    ):
        if account.provider != models.EmailAccount.Provider.OUTLOOK:
            raise ValueError("OutlookIngestionService requires an Outlook/Hotmail account.")
        self.account = account
        self.graph_client = graph_client
        self.query = query or getattr(settings, "OUTLOOK_SEARCH_QUERY", "")
        self.label = label
        self.max_messages = max_messages

    def sync(self, messages: Optional[List[Dict[str, Any]]] = None, delta_link: Optional[str] = None) -> SyncResult:
        result = SyncResult()
        if messages is None:
            logger.info(
                "Outlook Graph sync is not fully implemented; skipping fetch for %s",
                self.account.email_address,
            )
            return result
        for message in messages[: self.max_messages]:
            stored = self._store_message(message)
            if stored:
                result.created += 1
            else:
                result.skipped += 1
            result.fetched += 1
        self._update_sync_state(result, delta_link)
        return result

    def _update_sync_state(self, result: SyncResult, delta_link: Optional[str]) -> None:
        with transaction.atomic():
            state, _ = models.MailSyncState.objects.select_for_update().get_or_create(
                account=self.account,
                label=self.label or "primary",
                defaults={
                    "user": self.account.user,
                    "provider": self.account.provider,
                    "query": self.query,
                },
            )
            state.user = self.account.user
            state.provider = self.account.provider
            state.query = self.query
            checkpoint = state.checkpoint or {}
            if delta_link:
                checkpoint["delta_link"] = delta_link
                result.last_history_id = delta_link
            state.checkpoint = checkpoint
            state.last_synced_at = timezone.now()
            state.fetched_messages += result.fetched
            state.retry_count = 0
            state.save()

    def _store_message(self, message: Dict[str, Any]) -> bool:
        received = _parse_graph_datetime(message.get("receivedDateTime"))
        body_html = ""
        body_text = ""
        body = message.get("body") or {}
        content = body.get("content") or ""
        if (body.get("contentType") or "").lower() == "html":
            body_html = content
        else:
            body_text = content
        defaults = {
            "account": self.account,
            "provider": self.account.provider,
            "mailbox_email": self.account.email_address,
            "thread_id": message.get("conversationId", ""),
            "history_id": "",
            "external_message_id": message.get("id", ""),
            "internet_message_id": message.get("internetMessageId", ""),
            "subject": message.get("subject", ""),
            "sender": ((message.get("from") or {}).get("emailAddress") or {}).get("address", ""),
            "snippet": message.get("bodyPreview", ""),
            "internal_date": received,
            "raw_payload": message,
            "raw_body": body_html or body_text,
            "user": self.account.user,
        }
        obj, created = models.EmailMessage.objects.update_or_create(
            provider=self.account.provider,
            external_message_id=message.get("id", ""),
            defaults=defaults,
        )
        if body_html and not obj.raw_body:
            obj.raw_body = body_html
            obj.save(update_fields=["raw_body"])
        return created
