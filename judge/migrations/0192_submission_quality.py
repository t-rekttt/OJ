import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0191_submission_index_cleanup'),
    ]

    operations = [
        migrations.CreateModel(
            name='SubmissionQuality',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('naming', models.FloatField(verbose_name='naming')),
                ('structure', models.FloatField(verbose_name='structure')),
                ('clarity', models.FloatField(verbose_name='clarity')),
                ('hygiene', models.FloatField(verbose_name='hygiene')),
                ('confidence', models.JSONField(verbose_name='confidence per dimension')),
                ('source_hash', models.CharField(db_index=True, max_length=64, verbose_name='source SHA-256')),
                ('scorer_model', models.CharField(max_length=64, verbose_name='scoring model')),
                ('rubric_version', models.PositiveSmallIntegerField(verbose_name='rubric version')),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='created')),
                ('submission', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                                    related_name='quality', to='judge.submission',
                                                    verbose_name='submission')),
            ],
            options={
                'verbose_name': 'submission code quality',
                'verbose_name_plural': 'submission code qualities',
            },
        ),
    ]
