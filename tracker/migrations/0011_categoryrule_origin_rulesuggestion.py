from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("tracker", "0010_transactioncorrection"),
    ]

    operations = [
        migrations.AddField(
            model_name="categoryrule",
            name="origin",
            field=models.CharField(
                choices=[
                    ("manual", "Manual"),
                    ("promoted", "Manual Promotion"),
                    ("suggested", "Accepted Suggestion"),
                    ("seeded", "Default Seed"),
                ],
                default="manual",
                max_length=32,
            ),
        ),
        migrations.CreateModel(
            name="RuleSuggestion",
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
                ("merchant_name", models.CharField(max_length=255)),
                ("card_last4", models.CharField(blank=True, max_length=4)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("accepted", "Accepted"),
                            ("rejected", "Rejected"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("reason", models.CharField(blank=True, max_length=255)),
                (
                    "category",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rule_suggestions",
                        to="tracker.category",
                    ),
                ),
                (
                    "correction",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="rule_suggestions",
                        to="tracker.transactioncorrection",
                    ),
                ),
                (
                    "transaction",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="rule_suggestions",
                        to="tracker.transaction",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rule_suggestions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
            },
        ),
        migrations.AddConstraint(
            model_name="rulesuggestion",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "pending")),
                fields=("user", "merchant_name", "category", "card_last4", "status"),
                name="unique_pending_suggestion",
            ),
        ),
    ]
