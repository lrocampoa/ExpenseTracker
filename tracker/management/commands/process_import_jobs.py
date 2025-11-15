import logging

from django.core.management.base import BaseCommand

from tracker import models
from tracker.services import import_jobs as import_jobs_service

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process pending manual import jobs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=0,
            help="Stop after processing this many jobs (0 = all).",
        )

    def handle(self, *args, **options):
        max_jobs = options.get("max_jobs") or 0
        processed = 0
        qs = models.ImportJob.objects.filter(status=models.ImportJob.Status.QUEUED).order_by("created_at")
        if not qs.exists():
            self.stdout.write("No queued jobs found.")
            return
        for job in qs:
            if max_jobs and processed >= max_jobs:
                break
            self.stdout.write(f"Processing job {job.pk} for {job.user.email}")
            import_jobs_service.run_job(job.pk)
            processed += 1
        self.stdout.write(self.style.SUCCESS(f"Processed {processed} job(s)."))
