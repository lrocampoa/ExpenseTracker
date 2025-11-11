"""Helpers for Gmail OAuth credentials and message ingestion."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
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
    """Persist and refresh Gmail OAuth credentials."""

    def __init__(self, user_email: Optional[str] = None, scopes: Optional[List[str]] = None, user=None):
        self.user_email = user_email or settings.GMAIL_USER_EMAIL
        if not self.user_email:
            raise ImproperlyConfigured("GMAIL_USER_EMAIL is not configured and no email was provided.")
        self.scopes = scopes or settings.GMAIL_SCOPES
        self.user = user or self._ensure_user()

    def _load_db_record(self) -> Optional[models.GmailCredential]:
        qs = models.GmailCredential.objects.filter(user_email=self.user_email, is_active=True)
        if self.user and hasattr(models.GmailCredential, "user_id"):
            qs = qs.filter(user=self.user)
        return qs.first()

    def get_stored_credentials(self) -> Tuple[Optional[Credentials], Optional[models.GmailCredential]]:
        record = self._load_db_record()
        if not record:
            return None, None
        info = record.token_json or {}
        if isinstance(info, str):
            info = json.loads(info)
        creds = Credentials.from_authorized_user_info(info, scopes=self.scopes)
        return creds, record

    def save_credentials(self, creds: Credentials) -> models.GmailCredential:
        info = json.loads(creds.to_json())
        expiry = creds.expiry
        expiry_utc = None
        if expiry:
            expiry_utc = expiry.replace(tzinfo=dt_timezone.utc) if expiry.tzinfo is None else expiry.astimezone(dt_timezone.utc)
        defaults = {
            "token_json": info,
            "scopes": self.scopes,
            "token_expiry": expiry_utc,
            "is_active": True,
            "user": self.user,
        }
        record, _ = models.GmailCredential.objects.update_or_create(
            user_email=self.user_email,
            defaults=defaults,
        )
        return record

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
    """Fetch Gmail messages and persist them as EmailMessage rows."""

    def __init__(
        self,
        service,
        user_email: str,
        query: str,
        label: str = "primary",
        max_messages: int = 50,
        user=None,
    ):
        self.service = service
        self.user_email = user_email
        self.query = query
        self.label = label
        self.max_messages = max_messages
        self.user = user

    def sync(self) -> SyncResult:
        result = SyncResult()
        page_token = None
        processed = 0
        latest_history = None
        logger.info("Starting Gmail sync for %s with query '%s'", self.user_email, self.query)
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
        self._update_sync_state(result, latest_history)
        return result

    def _update_sync_state(self, result: SyncResult, latest_history: Optional[str]) -> None:
        with transaction.atomic():
            state, _ = models.GmailSyncState.objects.select_for_update().get_or_create(
                label=self.label,
                defaults={
                    "user_email": self.user_email,
                    "query": self.query,
                    "user": self.user,
                },
            )
            state.user_email = self.user_email
            state.query = self.query
            if hasattr(state, "user_id"):
                state.user = self.user
            if latest_history:
                state.history_id = str(latest_history)
                result.last_history_id = str(latest_history)
            state.last_synced_at = timezone.now()
            state.fetched_messages += result.fetched
            state.retry_count = 0
            state.save()

    def _mark_sync_failure(self) -> None:
        with transaction.atomic():
            state, _ = models.GmailSyncState.objects.select_for_update().get_or_create(
                label=self.label,
                defaults={
                    "user_email": self.user_email,
                    "query": self.query,
                    "user": self.user,
                },
            )
            state.user_email = self.user_email
            state.query = self.query
            if hasattr(state, "user_id"):
                state.user = self.user
            state.retry_count += 1
            state.save(update_fields=["user_email", "query", "user", "retry_count", "updated_at"])

    def _store_message(self, message: Dict[str, Any]) -> bool:
        payload = message.get("payload", {})
        headers = payload.get("headers", [])
        subject = _header_value(headers, "Subject")
        sender = _header_value(headers, "From")
        internal_ts = message.get("internalDate")
        internal_date = None
        if internal_ts:
            internal_date = datetime.fromtimestamp(int(internal_ts) / 1000, tz=dt_timezone.utc)
        raw_body = _extract_body(payload)
        defaults = {
            "thread_id": message.get("threadId", ""),
            "history_id": message.get("historyId", ""),
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
