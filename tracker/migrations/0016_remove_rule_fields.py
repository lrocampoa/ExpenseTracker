from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0015_remove_subcategory_unique_category_subcategory_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="categoryrule",
            name="name",
        ),
        migrations.RemoveField(
            model_name="categoryrule",
            name="min_amount",
        ),
        migrations.RemoveField(
            model_name="categoryrule",
            name="max_amount",
        ),
        migrations.RemoveField(
            model_name="categoryrule",
            name="confidence",
        ),
    ]
