from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0016_remove_rule_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="categoryrule",
            name="subcategory",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rules",
                to="tracker.subcategory",
            ),
        ),
    ]
