from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tracker", "0009_gmailsyncstate_retry_count"),
    ]

    operations = [
        migrations.CreateModel(
            name="TransactionCorrection",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("previous_merchant_name", models.CharField(blank=True, max_length=255)),
                ("new_merchant_name", models.CharField(blank=True, max_length=255)),
                ("previous_description", models.TextField(blank=True)),
                ("new_description", models.TextField(blank=True)),
                (
                    "previous_amount",
                    models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
                ),
                (
                    "new_amount",
                    models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
                ),
                ("previous_currency_code", models.CharField(blank=True, max_length=12)),
                ("new_currency_code", models.CharField(blank=True, max_length=12)),
                (
                    "previous_transaction_date",
                    models.DateTimeField(blank=True, null=True),
                ),
                ("new_transaction_date", models.DateTimeField(blank=True, null=True)),
                ("changed_fields", models.JSONField(blank=True, default=list)),
                (
                    "new_category",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="tracker.category",
                    ),
                ),
                (
                    "previous_category",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="tracker.category",
                    ),
                ),
                (
                    "transaction",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="corrections",
                        to="tracker.transaction",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="transaction_corrections",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
            },
        ),
    ]
