from django.core.management.base import BaseCommand

from tracker.services.gmail import GmailCredentialManager


class Command(BaseCommand):
    help = "Run the OAuth flow to store Gmail credentials in the database."

    def add_arguments(self, parser):
        parser.add_argument("--email", dest="email", help="Gmail address to authorize")
        parser.add_argument(
            "--port",
            dest="port",
            default=0,
            type=int,
            help="Port for the local OAuth callback server (0 picks a random port).",
        )

    def handle(self, *args, **options):
        email = options.get("email")
        port = options.get("port", 0)
        manager = GmailCredentialManager(user_email=email)
        creds = manager.run_local_authorization(port=port)
        self.stdout.write(
            self.style.SUCCESS(
                f"Stored Gmail credentials for {manager.user_email}. Token expires at {creds.expiry}."
            )
        )
