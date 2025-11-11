from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0013_category_budget_subcategory_transaction_subcategory"),
    ]

    operations = [
        migrations.AddField(
            model_name="transactioncorrection",
            name="new_subcategory",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="tracker.subcategory",
            ),
        ),
        migrations.AddField(
            model_name="transactioncorrection",
            name="previous_subcategory",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="tracker.subcategory",
            ),
        ),
    ]
