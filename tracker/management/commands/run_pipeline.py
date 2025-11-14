from django.core.management import BaseCommand, call_command


class Command(BaseCommand):
    help = (
        "Run the ingestion pipeline: mailbox sync, parse/process emails, and categorize transactions."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-sync",
            action="store_true",
            help="Skip Gmail sync step.",
        )
        parser.add_argument(
            "--skip-parse",
            action="store_true",
            help="Skip processing EmailMessage records into transactions.",
        )
        parser.add_argument(
            "--skip-categorize",
            action="store_true",
            help="Skip categorization step.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help="Limit for processing/categorization commands.",
        )
        parser.add_argument(
            "--user-email",
            help="Pipeline scope for a specific usuario (coincide con email).",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        user_email = options.get("user_email")
        if not options["skip_sync"]:
            self.stdout.write(self.style.NOTICE("[1/3] Syncing mailboxes..."))
            cmd_args = {}
            if user_email:
                cmd_args["user_email"] = user_email
            call_command("sync_mailboxes", **cmd_args)
        if not options["skip_parse"]:
            self.stdout.write(self.style.NOTICE("[2/3] Processing emails..."))
            cmd_args = {"limit": limit}
            if user_email:
                cmd_args["user_email"] = user_email
            call_command("process_emails", **cmd_args)
        if not options["skip_categorize"]:
            self.stdout.write(self.style.NOTICE("[3/3] Categorizing transactions..."))
            cmd_args = {"limit": limit}
            if user_email:
                cmd_args["user_email"] = user_email
            call_command("categorize_transactions", **cmd_args)
        self.stdout.write(self.style.SUCCESS("Pipeline execution complete."))
