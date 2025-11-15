"""Background import job helpers."""

from __future__ import annotations

import logging
import threading
from typing import List, Optional

from django.conf import settings
from django.core.management import call_command
from django.db import close_old_connections

from tracker import models
from tracker.services import parser as parser_service

logger = logging.getLogger(__name__)


def enqueue_job(job_id):
    """Launch the import job runner on a background thread."""

    thread = threading.Thread(target=_run_job_in_thread, args=(job_id,), daemon=True)
    thread.start()
    return thread


def run_job(job_id) -> Optional[models.ImportJob]:
    """Run a job synchronously (used by management commands/tests)."""

    runner = _ImportJobRunner(job_id)
    return runner.run()


def _run_job_in_thread(job_id):
    """Utility for async execution ensuring DB connections are fresh."""

    close_old_connections()
    runner = _ImportJobRunner(job_id)
    runner.run()


class _ImportJobRunner:
    def __init__(self, job_id):
        self.job_id = job_id

    def run(self) -> Optional[models.ImportJob]:
        try:
            job = models.ImportJob.objects.select_related("user").get(pk=self.job_id)
        except models.ImportJob.DoesNotExist:
            logger.warning("Import job %s no longer exists", self.job_id)
            return None
        if not job.is_active:
            logger.info("Import job %s already finished with status %s", job.pk, job.status)
            return job
        job.mark_syncing()
        try:
            call_command(
                "sync_mailboxes",
                user_email=job.user.email,
                max=job.max_messages,
                gmail_query=job.gmail_query or settings.GMAIL_SEARCH_QUERY,
                outlook_query=job.outlook_query or settings.OUTLOOK_SEARCH_QUERY,
            )
        except Exception as exc:  # pragma: no cover - network heavy
            logger.exception("Mailbox sync failed for import job %s", job.pk)
            job.mark_failed(str(exc))
            return job

        pending = self._pending_emails(job)
        job.mark_processing(len(pending))
        self._process_emails(job, pending)
        if job.status in models.ImportJob.ACTIVE_STATUSES:
            job.mark_completed()
        return job

    def _pending_emails(self, job: models.ImportJob) -> List[models.EmailMessage]:
        qs = models.EmailMessage.objects.filter(
            user=job.user,
            processed_at__isnull=True,
        ).order_by("-internal_date", "-created_at")
        return list(qs[: job.max_messages])

    def _process_emails(self, job: models.ImportJob, emails: List[models.EmailMessage]):
        for email in emails:
            try:
                created = parser_service.create_transaction_from_email(email)
            except Exception:  # pragma: no cover - defensive logging
                logger.exception("Error parsing email %s during import job %s", email.pk, job.pk)
                job.increment_processed(created=False, errored=True)
            else:
                job.increment_processed(created=bool(created), errored=False)
