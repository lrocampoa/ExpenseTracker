import logging
from typing import Optional

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from allauth.socialaccount.models import SocialLogin, SocialToken
from allauth.socialaccount.signals import social_account_added, social_account_updated
from google.oauth2.credentials import Credentials

from tracker.services import account_seeding, rule_seeding
from tracker.services.gmail import GmailCredentialManager

logger = logging.getLogger(__name__)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def seed_rules_for_new_user(sender, instance, created, **kwargs):
    if created:
        rule_seeding.ensure_defaults(instance)
        account_seeding.ensure_default_accounts(instance)


def _credentials_from_social_token(token: SocialToken) -> Optional[Credentials]:
    if not token or not token.token:
        return None
    client_id = ""
    client_secret = ""
    if token.app_id and token.app:
        client_id = token.app.client_id or ""
        client_secret = token.app.secret or ""
    provider_cfg = settings.SOCIALACCOUNT_PROVIDERS.get("google", {})
    app_cfg = provider_cfg.get("APP", {})
    client_id = client_id or app_cfg.get("client_id") or ""
    client_secret = client_secret or app_cfg.get("secret") or ""
    if not client_id or not client_secret:
        logger.warning("Missing Google client credentials; skipped Gmail token persistence.")
        return None
    creds = Credentials(
        token=token.token,
        refresh_token=token.token_secret or None,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=settings.GMAIL_SCOPES,
    )
    if token.expires_at:
        expires_at = token.expires_at
        if timezone.is_naive(expires_at):
            expires_at = timezone.make_aware(expires_at, timezone.utc)
        creds.expiry = expires_at
    return creds


def _store_gmail_credentials_from_sociallogin(sociallogin: SocialLogin):
    account = getattr(sociallogin, "account", None)
    if not account or account.provider != "google" or not account.user_id:
        return
    token = getattr(sociallogin, "token", None)
    if token is None:
        token = (
            SocialToken.objects.filter(account=account)
            .select_related("app")
            .order_by("-expires_at")
            .first()
        )
    creds = _credentials_from_social_token(token)
    if not creds or not creds.refresh_token:
        logger.warning("Google login for %s missing refresh token; Gmail sync not enabled.", account.user.email)
        return
    manager = GmailCredentialManager(user_email=account.user.email, user=account.user)
    manager.save_credentials(creds)


@receiver(social_account_added)
def sync_gmail_credentials_on_connect(sender, sociallogin, **kwargs):
    _store_gmail_credentials_from_sociallogin(sociallogin)


@receiver(social_account_updated)
def sync_gmail_credentials_on_update(sender, sociallogin, **kwargs):
    _store_gmail_credentials_from_sociallogin(sociallogin)
