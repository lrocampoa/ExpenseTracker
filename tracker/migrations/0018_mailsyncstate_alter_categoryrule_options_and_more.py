import json

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def create_initial_mail_accounts(apps, schema_editor):
    EmailAccount = apps.get_model("tracker", "EmailAccount")
    MailSyncState = apps.get_model("tracker", "MailSyncState")
    GmailCredential = apps.get_model("tracker", "GmailCredential")
    GmailSyncState = apps.get_model("tracker", "GmailSyncState")

    account_cache = {}

    def _token_data(raw):
        if not raw:
            return {}
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw)
        except Exception:
            return {}

    for cred in GmailCredential.objects.all():
        token_data = _token_data(cred.token_json)
        refresh_token = token_data.get("refresh_token") or ""
        account, _ = EmailAccount.objects.update_or_create(
            provider="gmail",
            email_address=cred.user_email,
            defaults={
                "user_id": cred.user_id,
                "display_name": "",
                "label": cred.user_email or "",
                "token_json": token_data,
                "scopes": cred.scopes or [],
                "token_expiry": cred.token_expiry,
                "refresh_token": refresh_token,
                "is_active": cred.is_active,
                "metadata": {"source": "gmail_migration"},
            },
        )
        account_cache[(account.provider, account.email_address.lower())] = account

    for state in GmailSyncState.objects.all():
        email = (state.user_email or "").lower()
        cache_key = ("gmail", email)
        account = account_cache.get(cache_key)
        if email and not account:
            account, _ = EmailAccount.objects.get_or_create(
                provider="gmail",
                email_address=state.user_email,
                defaults={
                    "user_id": state.user_id,
                    "label": state.user_email,
                    "is_active": True,
                },
            )
            account_cache[cache_key] = account
        user_id = state.user_id or (account.user_id if account else None)
        checkpoint = {}
        if state.history_id:
            checkpoint["history_id"] = state.history_id
        MailSyncState.objects.update_or_create(
            account=account,
            label=state.label,
            defaults={
                "user_id": user_id,
                "provider": "gmail",
                "query": state.query,
                "last_synced_at": state.last_synced_at,
                "fetched_messages": state.fetched_messages,
                "retry_count": state.retry_count,
                "checkpoint": checkpoint,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ('tracker', '0017_categoryrule_subcategory'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='MailSyncState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('provider', models.CharField(choices=[('gmail', 'Gmail / Google Workspace'), ('outlook', 'Outlook / Hotmail / Office 365')], default='gmail', max_length=32)),
                ('label', models.CharField(default='primary', max_length=64)),
                ('query', models.CharField(blank=True, max_length=500)),
                ('last_synced_at', models.DateTimeField(blank=True, null=True)),
                ('fetched_messages', models.PositiveIntegerField(default=0)),
                ('retry_count', models.PositiveIntegerField(default=0)),
                ('checkpoint', models.JSONField(blank=True, default=dict, help_text='Provider-specific cursor state.')),
            ],
            options={
                'ordering': ('-created_at',),
                'abstract': False,
            },
        ),
        migrations.AlterModelOptions(
            name='categoryrule',
            options={'ordering': ('priority', 'match_value')},
        ),
        migrations.AddField(
            model_name='emailmessage',
            name='external_message_id',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='emailmessage',
            name='internet_message_id',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='emailmessage',
            name='mailbox_email',
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name='emailmessage',
            name='provider',
            field=models.CharField(choices=[('gmail', 'Gmail / Google Workspace'), ('outlook', 'Outlook / Hotmail / Office 365')], default='gmail', max_length=32),
        ),
        migrations.AlterField(
            model_name='emailmessage',
            name='gmail_message_id',
            field=models.CharField(blank=True, max_length=128, null=True, unique=True),
        ),
        migrations.CreateModel(
            name='EmailAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('provider', models.CharField(choices=[('gmail', 'Gmail / Google Workspace'), ('outlook', 'Outlook / Hotmail / Office 365')], max_length=32)),
                ('email_address', models.EmailField(max_length=254)),
                ('display_name', models.CharField(blank=True, max_length=255)),
                ('label', models.CharField(blank=True, help_text='Friendly name shown in settings.', max_length=64)),
                ('token_json', models.JSONField(blank=True, default=dict)),
                ('scopes', models.JSONField(blank=True, default=list)),
                ('token_expiry', models.DateTimeField(blank=True, null=True)),
                ('refresh_token', models.CharField(blank=True, max_length=512)),
                ('is_active', models.BooleanField(default=True)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='email_accounts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ('-created_at',),
                'abstract': False,
            },
        ),
        migrations.AddField(
            model_name='emailmessage',
            name='account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='emails', to='tracker.emailaccount'),
        ),
        migrations.AddConstraint(
            model_name='emailmessage',
            constraint=models.UniqueConstraint(condition=models.Q(('external_message_id', ''), _negated=True), fields=('provider', 'external_message_id'), name='unique_external_message_per_provider'),
        ),
        migrations.AddField(
            model_name='mailsyncstate',
            name='account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='sync_states', to='tracker.emailaccount'),
        ),
        migrations.AddField(
            model_name='mailsyncstate',
            name='user',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='mail_sync_states', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddConstraint(
            model_name='emailaccount',
            constraint=models.UniqueConstraint(fields=('provider', 'email_address'), name='unique_provider_email_account'),
        ),
        migrations.AddConstraint(
            model_name='mailsyncstate',
            constraint=models.UniqueConstraint(fields=('account', 'label'), name='unique_mail_sync_label_per_account'),
        ),
        migrations.RunPython(code=create_initial_mail_accounts, reverse_code=migrations.RunPython.noop),
    ]
