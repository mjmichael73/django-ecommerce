# Generated by Django 5.0.3 on 2024-04-13 13:32

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('coupons', '0001_initial'),
    ]

    operations = [
        migrations.RenameField(
            model_name='coupon',
            old_name='valid_form',
            new_name='valid_from',
        ),
    ]
