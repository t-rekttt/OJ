# Generated by Django 2.2.24 on 2021-09-10 15:40

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0155_contest_announcement'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contest',
            name='scoreboard_visibility',
            field=models.CharField(choices=[('V', 'Visible'), ('H', 'Always hidden'), ('C', 'Hidden for duration of contest'), ('P', 'Hidden for duration of participation')], default='V', help_text='Scoreboard visibility through the duration of the contest', max_length=1, verbose_name='scoreboard visibility'),
        ),
    ]