from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0193_problem_enable_code_quality'),
    ]

    operations = [
        migrations.AddField(
            model_name='contest',
            name='enable_code_quality',
            field=models.BooleanField(default=False, help_text=('Score the code quality of accepted submissions '
                                                                "made in this contest, overriding the problem's "
                                                                'setting. Scores stay hidden until the contest ends. '
                                                                'Does not affect points.'),
                                      verbose_name='score code quality'),
        ),
    ]
