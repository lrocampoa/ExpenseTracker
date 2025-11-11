from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from tracker.services import account_seeding, rule_seeding


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def seed_rules_for_new_user(sender, instance, created, **kwargs):
    if created:
        rule_seeding.ensure_defaults(instance)
        account_seeding.ensure_default_accounts(instance)
