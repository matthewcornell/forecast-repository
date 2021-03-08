# Generated by Django 3.1.7 on 2021-02-25 13:51

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('forecast_app', '0013_remove_scores'),
    ]

    operations = [
        migrations.CreateModel(
            name='PredictionElement',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('pred_class', models.IntegerField(choices=[(0, 'bin'), (1, 'named'), (2, 'point'), (3, 'sample'), (4, 'quantile')])),
                ('is_retract', models.BooleanField(default=False)),
                ('data_hash', models.CharField(max_length=32)),
                ('forecast', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pred_eles', to='forecast_app.forecast')),
                ('target', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='forecast_app.target')),
                ('unit', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='forecast_app.unit')),
            ],
        ),
        migrations.CreateModel(
            name='PredictionData',
            fields=[
                ('pred_ele', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, primary_key=True, related_name='pred_data', serialize=False, to='forecast_app.predictionelement')),
                ('data', models.JSONField()),
            ],
        ),
    ]
