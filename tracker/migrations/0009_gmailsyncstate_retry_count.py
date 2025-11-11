from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0008_card_expense_account"),
    ]

    operations = [
        migrations.AddField(
            model_name="gmailsyncstate",
            name="retry_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
