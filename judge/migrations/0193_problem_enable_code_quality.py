from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0192_submission_quality'),
    ]

    operations = [
        migrations.AddField(
            model_name='problem',
            name='enable_code_quality',
            field=models.BooleanField(default=False, help_text='Score the code quality of accepted submissions. '
                                                               'Informational only; does not affect points.',
                                      verbose_name='score code quality'),
        ),
    ]
