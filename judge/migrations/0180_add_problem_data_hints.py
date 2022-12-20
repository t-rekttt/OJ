# Generated by Django 3.2.16 on 2023-01-19 10:05

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0179_add_ranking_access_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='problemdata',
            name='nobigmath',
            field=models.BooleanField(blank=True, null=True, verbose_name='disable bigInteger / bigDecimal'),
        ),
        migrations.AddField(
            model_name='problemdata',
            name='unicode',
            field=models.BooleanField(blank=True, null=True, verbose_name='enable unicode'),
        ),
        migrations.AlterField(
            model_name='blogpost',
            name='og_image',
            field=models.CharField(blank=True, default='', max_length=150, verbose_name='OpenGraph image'),
        ),
        migrations.AlterField(
            model_name='comment',
            name='hidden',
            field=models.BooleanField(default=0, verbose_name='hidden'),
        ),
        migrations.AlterField(
            model_name='submission',
            name='result',
            field=models.CharField(blank=True, choices=[('AC', 'Accepted'), ('WA', 'Wrong Answer'), ('TLE', 'Time Limit Exceeded'), ('MLE', 'Memory Limit Exceeded'), ('OLE', 'Output Limit Exceeded'), ('IR', 'Invalid Return'), ('RTE', 'Runtime Error'), ('CE', 'Compile Error'), ('IE', 'Internal Error'), ('SC', 'Short Circuited'), ('AB', 'Aborted')], db_index=True, default=None, max_length=3, null=True, verbose_name='result'),
        ),
        migrations.AlterField(
            model_name='submissiontestcase',
            name='status',
            field=models.CharField(choices=[('AC', 'Accepted'), ('WA', 'Wrong Answer'), ('TLE', 'Time Limit Exceeded'), ('MLE', 'Memory Limit Exceeded'), ('OLE', 'Output Limit Exceeded'), ('IR', 'Invalid Return'), ('RTE', 'Runtime Error'), ('CE', 'Compile Error'), ('IE', 'Internal Error'), ('SC', 'Short Circuited'), ('AB', 'Aborted')], max_length=3, verbose_name='status flag'),
        ),
        migrations.AlterField(
            model_name='ticketmessage',
            name='user',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ticket_messages', to='judge.profile', verbose_name='user'),
        ),
    ]
