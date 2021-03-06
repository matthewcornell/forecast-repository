# Generated by Django 3.1.7 on 2021-04-08 22:21

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('forecast_app', '0014_predictionelement_1_of_2'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='nameddistribution',
            name='forecast',
        ),
        migrations.RemoveField(
            model_name='nameddistribution',
            name='target',
        ),
        migrations.RemoveField(
            model_name='nameddistribution',
            name='unit',
        ),
        migrations.RemoveField(
            model_name='pointprediction',
            name='forecast',
        ),
        migrations.RemoveField(
            model_name='pointprediction',
            name='target',
        ),
        migrations.RemoveField(
            model_name='pointprediction',
            name='unit',
        ),
        migrations.RemoveField(
            model_name='quantiledistribution',
            name='forecast',
        ),
        migrations.RemoveField(
            model_name='quantiledistribution',
            name='target',
        ),
        migrations.RemoveField(
            model_name='quantiledistribution',
            name='unit',
        ),
        migrations.RemoveField(
            model_name='sampledistribution',
            name='forecast',
        ),
        migrations.RemoveField(
            model_name='sampledistribution',
            name='target',
        ),
        migrations.RemoveField(
            model_name='sampledistribution',
            name='unit',
        ),
        migrations.DeleteModel(
            name='BinDistribution',
        ),
        migrations.DeleteModel(
            name='NamedDistribution',
        ),
        migrations.DeleteModel(
            name='PointPrediction',
        ),
        migrations.DeleteModel(
            name='QuantileDistribution',
        ),
        migrations.DeleteModel(
            name='SampleDistribution',
        ),
    ]
