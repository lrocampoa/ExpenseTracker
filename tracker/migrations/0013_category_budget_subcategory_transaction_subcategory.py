from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0012_expenseaccount"),
    ]

    operations = [
        migrations.AlterField(
            model_name="category",
            name="code",
            field=models.SlugField(max_length=64),
        ),
        migrations.AddField(
            model_name="category",
            name="budget_limit",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.CreateModel(
            name="Subcategory",
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
                ("code", models.SlugField(max_length=64)),
                ("name", models.CharField(max_length=128)),
                ("budget_limit", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                (
                    "category",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="subcategories", to="tracker.category"),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subcategories",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("name",),
            },
        ),
        migrations.AddConstraint(
            model_name="subcategory",
            constraint=models.UniqueConstraint(fields=("category", "code"), name="unique_category_subcategory"),
        ),
        migrations.AddConstraint(
            model_name="category",
            constraint=models.UniqueConstraint(fields=("user", "code"), name="unique_category_code_per_user"),
        ),
        migrations.AddField(
            model_name="transaction",
            name="subcategory",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="transactions",
                to="tracker.subcategory",
            ),
        ),
    ]
