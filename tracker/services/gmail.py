"""Helpers for Gmail OAuth credentials and message ingestion."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.utils import timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from tracker import models

logger = logging.getLogger(__name__)


class MissingCredentialsError(Exception):
    """Raised when Gmail credentials are missing and no interactive flow is allowed."""


@dataclass
class SyncResult:
    fetched: int = 0
    created: int = 0
    skipped: int = 0
    last_history_id: Optional[str] = None


class GmailCredentialManager:
    """Persist and refresh Gmail OAuth credentials using EmailAccount records."""

    def __init__(
        self,
        user_email: Optional[str] = None,
        scopes: Optional[List[str]] = None,
        user=None,
        account: Optional[models.EmailAccount] = None,
    ):
        self.scopes = scopes or settings.GMAIL_SCOPES
        self.account = account
        email = user_email or getattr(account, "email_address", None) or settings.GMAIL_USER_EMAIL
        if not email:
            raise ImproperlyConfigured("GMAIL_USER_EMAIL is not configured and no email was provided.")
        self.user_email = email
        self.user = user or getattr(account, "user", None) or self._ensure_user()
        if not self.account:
            self.account = self._ensure_account()

    def _ensure_account(self) -> models.EmailAccount:
        account = (
            models.EmailAccount.objects.filter(
                provider=models.EmailAccount.Provider.GMAIL,
                email_address__iexact=self.user_email,
            )
            .select_related("user")
            .first()
        )
        if account:
            if not account.user_id and self.user:
                account.user = self.user
                account.save(update_fields=["user"])
            return account
        return models.EmailAccount.objects.create(
            provider=models.EmailAccount.Provider.GMAIL,
            email_address=self.user_email,
            user=self.user,
            label=self.user_email,
            is_active=True,
        )

    def get_stored_credentials(self) -> Tuple[Optional[Credentials], Optional[models.EmailAccount]]:
        account = self.account or self._ensure_account()
        self.account = account
        info = account.token_json or {}
        if isinstance(info, str):
            info = json.loads(info)
        if not info:
            return None, account
        creds = Credentials.from_authorized_user_info(info, scopes=self.scopes)
        return creds, account

    def save_credentials(self, creds: Credentials) -> models.EmailAccount:
        info = json.loads(creds.to_json())
        expiry = creds.expiry
        expiry_utc = None
        if expiry:
            expiry_utc = expiry.replace(tzinfo=dt_timezone.utc) if expiry.tzinfo is None else expiry.astimezone(dt_timezone.utc)
        account = self.account or self._ensure_account()
        account.token_json = info
        account.token_expiry = expiry_utc
        account.scopes = self.scopes
        account.refresh_token = info.get("refresh_token") or account.refresh_token
        account.is_active = True
        if not account.user_id and self.user:
            account.user = self.user
        account.save(
            update_fields=["token_json", "token_expiry", "scopes", "refresh_token", "is_active", "user"]
        )
        self.account = account
        return account

    def ensure_credentials(self, allow_interactive: bool = False, port: int = 0) -> Credentials:
        creds, _ = self.get_stored_credentials()
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self.save_credentials(creds)
            return creds
        if allow_interactive:
            return self.run_local_authorization(port=port)
        raise MissingCredentialsError(
            "No valid Gmail credentials found. Run `python manage.py gmail_auth --email your@email` first."
        )

    def run_local_authorization(self, port: int = 0) -> Credentials:
        secrets_path = settings.GOOGLE_OAUTH_CLIENT_SECRET_PATH
        if not secrets_path:
            raise ImproperlyConfigured("GOOGLE_OAUTH_CLIENT_SECRET_PATH is not configured.")
        path = Path(secrets_path)
        if not path.exists():
            raise ImproperlyConfigured(f"Client secrets file not found at: {path}")
        flow = InstalledAppFlow.from_client_secrets_file(str(path), scopes=self.scopes)
        creds = flow.run_local_server(port=port)
        self.save_credentials(creds)
        logger.info("Stored Gmail credentials for %s", self.user_email)
        return creds

    @staticmethod
    def build_service(credentials: Credentials):
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def _ensure_user(self):
        User = get_user_model()
        user = User.objects.filter(email__iexact=self.user_email).first()
        if user:
            return user
        username_field = getattr(User, "USERNAME_FIELD", "username")
        username_value = self.user_email or f"gmail_{uuid4().hex[:6]}"
        kwargs = {"email": self.user_email}
        if username_field != "email":
            kwargs[username_field] = username_value
        else:
            kwargs[username_field] = username_value
        user = User.objects.create_user(password=None, **kwargs)
        return user


def _decode_body(data: Optional[str]) -> str:
    if not data:
        return ""
    padding = "=" * (-len(data) % 4)
    try:
        decoded = base64.urlsafe_b64decode(data + padding)
    except Exception:  # pragma: no cover - defensive decoding
        logger.exception("Failed to decode Gmail body payload")
        return ""
    return decoded.decode("utf-8", errors="ignore")


def _extract_body(payload: Dict[str, Any]) -> str:
    text_body = ""
    html_body = ""
    parts = [payload]
    while parts:
        part = parts.pop(0)
        mime = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data")
        if body_data:
            decoded = _decode_body(body_data)
            if mime == "text/plain" and not text_body:
                text_body = decoded
            elif mime == "text/html" and not html_body:
                html_body = decoded
            elif not text_body:
                text_body = decoded
        for child in part.get("parts", []) or []:
            parts.append(child)
    return html_body or text_body


def _header_value(headers: List[Dict[str, str]], name: str) -> str:
    for header in headers or []:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


class GmailIngestionService:
    """Fetch Gmail messages for a specific EmailAccount and persist them as EmailMessage rows."""

    def __init__(
        self,
        service,
        account: models.EmailAccount,
        query: str,
        label: str = "primary",
        max_messages: int = 50,
    ):
        self.service = service
        self.account = account
        self.user_email = account.email_address
        self.user = account.user
        self.query = query
        self.label = label
        self.max_messages = max_messages
        self._state: Optional[models.MailSyncState] = None
        self._last_internal_date: Optional[datetime] = None

    def sync(self) -> SyncResult:
        """Attempt Gmail history sync first; fall back to search-based fetch when required."""

        history_id = self._current_history_id()
        if history_id:
            try:
                logger.info(
                    "Starting Gmail history sync for %s at historyId %s",
                    self.user_email,
                    history_id,
                )
                result = self._sync_from_history(history_id)
                self._finalize_sync(result)
                return result
            except HttpError as exc:  # pragma: no cover - network exception
                status = getattr(getattr(exc, "resp", None), "status", None)
                if status == 404:
                    logger.warning(
                        "HistoryId %s expired for %s; falling back to full search sync.",
                        history_id,
                        self.user_email,
                    )
                else:
                    logger.exception("Gmail history sync failed: %s", exc)
                    self._mark_sync_failure()
                    raise
            except Exception:  # pragma: no cover - defensive catch
                logger.exception("Unexpected Gmail history sync failure")
                self._mark_sync_failure()
                raise

        logger.info("Starting Gmail full sync for %s with query '%s'", self.user_email, self.query)
        result = self._sync_via_query()
        self._finalize_sync(result)
        return result

    def _current_history_id(self) -> Optional[str]:
        self._state = models.MailSyncState.latest_for_account(self.account, self.label or "primary")
        if self._state:
            return self._state.last_history_id()
        return None

    def _sync_from_history(self, start_history_id: str) -> SyncResult:
        result = SyncResult()
        self._last_internal_date = None
        latest_history = start_history_id
        candidate_ids: List[str] = []
        seen_ids: Set[str] = set()
        page_token = None
        while len(candidate_ids) < self.max_messages:
            response = (
                self.service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=start_history_id,
                    historyTypes=["messageAdded"],
                    pageToken=page_token,
                    maxResults=500,
                )
                .execute()
            )
            history_entries = response.get("history", [])
            if not history_entries:
                break
            for entry in history_entries:
                latest_history = entry.get("id") or latest_history
                for added in entry.get("messagesAdded", []):
                    message_meta = added.get("message") or {}
                    message_id = message_meta.get("id")
                    if message_id and message_id not in seen_ids:
                        seen_ids.add(message_id)
                        candidate_ids.append(message_id)
                        if len(candidate_ids) >= self.max_messages:
                            break
                if len(candidate_ids) >= self.max_messages:
                    break
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        filtered_ids = self._filter_candidate_ids_by_query(candidate_ids)
        for message_id in filtered_ids:
            if result.fetched >= self.max_messages:
                break
            message = (
                self.service.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
            stored = self._store_message(message)
            if stored:
                result.created += 1
            else:
                result.skipped += 1
            result.fetched += 1
            latest_history = message.get("historyId") or latest_history

        result.last_history_id = str(latest_history) if latest_history else None
        return result

    def _filter_candidate_ids_by_query(self, candidate_ids: List[str]) -> List[str]:
        if not candidate_ids:
            return []
        if not self.query:
            return candidate_ids
        target = set(candidate_ids)
        matched: List[str] = []
        matched_ids: Set[str] = set()
        page_token = None
        while True:
            response = (
                self.service.users()
                .messages()
                .list(
                    userId="me",
                    q=self.query,
                    maxResults=500,
                    pageToken=page_token,
                    includeSpamTrash=False,
                )
                .execute()
            )
            messages = response.get("messages", [])
            if not messages:
                break
            for msg in messages:
                msg_id = msg.get("id")
                if msg_id in target and msg_id not in matched_ids:
                    matched_ids.add(msg_id)
                    matched.append(msg_id)
                    if len(matched_ids) == len(target):
                        break
            if len(matched_ids) == len(target):
                break
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return matched

    def _sync_via_query(self) -> SyncResult:
        result = SyncResult()
        self._last_internal_date = None
        page_token = None
        processed = 0
        latest_history = None
        try:
            while processed < self.max_messages:
                batch_size = min(100, self.max_messages - processed)
                response = (
                    self.service.users()
                    .messages()
                    .list(
                        userId="me",
                        q=self.query,
                        maxResults=batch_size,
                        pageToken=page_token,
                        includeSpamTrash=False,
                    )
                    .execute()
                )
                messages = response.get("messages", [])
                if not messages:
                    break
                for msg_meta in messages:
                    message = (
                        self.service.users()
                        .messages()
                        .get(userId="me", id=msg_meta["id"], format="full")
                        .execute()
                    )
                    stored = self._store_message(message)
                    if stored:
                        result.created += 1
                    else:
                        result.skipped += 1
                    result.fetched += 1
                    processed += 1
                    latest_history = message.get("historyId") or latest_history
                    if processed >= self.max_messages:
                        break
                page_token = response.get("nextPageToken")
                if not page_token:
                    break
        except HttpError as exc:  # pragma: no cover - network exception
            logger.exception("Gmail API error: %s", exc)
            self._mark_sync_failure()
            raise
        except Exception:  # pragma: no cover - defensive catch
            logger.exception("Unexpected Gmail sync failure")
            self._mark_sync_failure()
            raise
        result.last_history_id = str(latest_history) if latest_history else None
        return result

    def _finalize_sync(self, result: SyncResult) -> None:
        self._update_sync_state(result, result.last_history_id)

    def _update_sync_state(self, result: SyncResult, latest_history: Optional[str]) -> None:
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
            checkpoint = state.checkpoint if isinstance(state.checkpoint, dict) else {}
            if latest_history:
                checkpoint["history_id"] = str(latest_history)
                checkpoint["history_updated_at"] = timezone.now().isoformat()
                result.last_history_id = str(latest_history)
            if self._last_internal_date:
                checkpoint["last_internal_date"] = self._last_internal_date.isoformat()
            checkpoint["last_batch_size"] = result.fetched
            state.checkpoint = checkpoint
            state.last_synced_at = timezone.now()
            state.fetched_messages += result.fetched
            state.retry_count = 0
            state.save()

    def _mark_sync_failure(self) -> None:
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
            state.retry_count += 1
            state.save(update_fields=["user", "provider", "query", "retry_count", "updated_at"])

    def _store_message(self, message: Dict[str, Any]) -> bool:
        payload = message.get("payload", {})
        headers = payload.get("headers", [])
        subject = _header_value(headers, "Subject")
        sender = _header_value(headers, "From")
        internet_message_id = _header_value(headers, "Message-ID")
        internal_ts = message.get("internalDate")
        internal_date = None
        if internal_ts:
            internal_date = datetime.fromtimestamp(int(internal_ts) / 1000, tz=dt_timezone.utc)
            if not self._last_internal_date or internal_date > self._last_internal_date:
                self._last_internal_date = internal_date
        raw_body = _extract_body(payload)
        defaults = {
            "account": self.account,
            "provider": self.account.provider,
            "mailbox_email": self.user_email,
            "thread_id": message.get("threadId", ""),
            "history_id": message.get("historyId", ""),
            "external_message_id": message.get("id", ""),
            "internet_message_id": internet_message_id,
            "subject": subject,
            "sender": sender,
            "snippet": message.get("snippet", ""),
            "internal_date": internal_date,
            "raw_payload": payload,
            "raw_body": raw_body,
            "user": self.user,
        }
        obj, created = models.EmailMessage.objects.update_or_create(
            gmail_message_id=message["id"],
            defaults=defaults,
        )
        return created
