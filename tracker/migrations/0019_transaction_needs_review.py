from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracker", "0018_mailsyncstate_alter_categoryrule_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="transaction",
            name="needs_review",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
