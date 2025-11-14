import json
from datetime import timedelta

import msal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tracker import models


class Command(BaseCommand):
    help = "Run a device-code OAuth flow to connect an Outlook/Hotmail mailbox via Microsoft Graph."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user-email",
            dest="user_email",
            help="Optional ExpenseTracker user email to associate with the mailbox.",
        )
        parser.add_argument(
            "--email",
            dest="mailbox_email",
            help="Mailbox address (used if Microsoft response omits preferred_username).",
        )

    def handle(self, *args, **options):
        client_id = settings.MS_GRAPH_CLIENT_ID
        tenant = settings.MS_GRAPH_TENANT_ID or "common"
        if not client_id:
            raise CommandError("MS_GRAPH_CLIENT_ID must be configured before running this command.")
        authority = f"https://login.microsoftonline.com/{tenant}"
        scopes = list(settings.MS_GRAPH_SCOPES or [])
        for scope in ("offline_access", "openid", "email"):
            if scope not in scopes:
                scopes.append(scope)
        app = msal.PublicClientApplication(client_id=client_id, authority=authority)
        flow = app.initiate_device_flow(scopes=scopes)
        if "user_code" not in flow:
            raise CommandError(f"Unable to initiate device flow: {flow}")
        self.stdout.write(flow["message"])
        self.stdout.write("")
        self.stdout.write("Waiting for completion...")
        result = app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise CommandError(result.get("error_description") or str(result))

        mailbox_email = (
            options.get("mailbox_email")
            or (result.get("id_token_claims") or {}).get("preferred_username")
            or (result.get("id_token_claims") or {}).get("email")
        )
        if not mailbox_email:
            raise CommandError("Mailbox email could not be determined; re-run with --email.")

        user = None
        user_email = options.get("user_email")
        if user_email:
            User = get_user_model()
            user = User.objects.filter(email__iexact=user_email).first()
            if not user:
                raise CommandError(f"User with email {user_email} not found.")

        expires_in = int(result.get("expires_in") or 0)
        expiry = timezone.now() + timedelta(seconds=expires_in) if expires_in else None
        token_json = json.loads(json.dumps(result))
        account, _ = models.EmailAccount.objects.update_or_create(
            provider=models.EmailAccount.Provider.OUTLOOK,
            email_address=mailbox_email,
            defaults={
                "user": user,
                "label": mailbox_email,
                "token_json": token_json,
                "token_expiry": expiry,
                "refresh_token": result.get("refresh_token") or "",
                "scopes": scopes,
                "is_active": True,
            },
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Stored Outlook credentials for {mailbox_email} (user: {account.user or 'sin asignar'})."
            )
        )
