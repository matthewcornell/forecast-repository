# -*- coding: utf-8 -*-
# Generated by Django 1.11.4 on 2017-09-14 19:22
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='CDCData',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('location', models.CharField(max_length=200)),
                ('target', models.CharField(max_length=200)),
                ('row_type', models.CharField(choices=[('p', 'Point'), ('b', 'Bin')], max_length=1)),
                ('unit', models.CharField(max_length=200)),
                ('bin_start_incl', models.CharField(max_length=200, null=True)),
                ('bin_end_notincl', models.CharField(max_length=200, null=True)),
                ('value', models.CharField(max_length=200)),
            ],
        ),
        migrations.CreateModel(
            name='Forecast',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('data_filename', models.CharField(help_text="Original CSV file name of this forecast's data source", max_length=200)),
            ],
        ),
        migrations.CreateModel(
            name='ForecastModel',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('description', models.CharField(help_text='A few paragraphs describing the model. should include information on reproducing the model’s results', max_length=2000)),
                ('url', models.URLField(help_text="The model's development URL")),
                ('auxiliary_data', models.URLField(help_text='optional model-specific Zip file containing data files (e.g., CSV files) beyond Project.core_data that were used by the this model', null=True)),
            ],
        ),
        migrations.CreateModel(
            name='Project',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('description', models.CharField(help_text="A few paragraphs describing the project. Includes info about 'real-time-ness' of data, i.e., revised/unrevised", max_length=2000)),
                ('url', models.URLField(help_text="The project's site")),
                ('core_data', models.URLField(help_text='Zip file containing data files (e.g., CSV files) made made available to everyone in the challenge, including supplemental data like Google queries or weather')),
            ],
        ),
        migrations.CreateModel(
            name='Target',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('description', models.CharField(help_text='A few paragraphs describing the target', max_length=2000)),
                ('project', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='forecast_app.Project')),
            ],
        ),
        migrations.CreateModel(
            name='TimeZero',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('timezero_date', models.DateField(blank=True, help_text='A date that a target is relative to', null=True)),
                ('data_version_date', models.DateField(blank=True, help_text='the database date at which models should work with for the timezero_date', null=True)),
                ('project', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='forecast_app.Project')),
            ],
        ),
        migrations.AddField(
            model_name='forecastmodel',
            name='project',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='forecast_app.Project'),
        ),
        migrations.AddField(
            model_name='forecast',
            name='forecast_model',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='forecast_app.ForecastModel'),
        ),
        migrations.AddField(
            model_name='forecast',
            name='time_zero',
            field=models.ForeignKey(help_text='TimeZero that this forecast is in relation to', null=True, on_delete=django.db.models.deletion.CASCADE, to='forecast_app.TimeZero'),
        ),
        migrations.AddField(
            model_name='cdcdata',
            name='forecast',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='forecast_app.Forecast'),
        ),
    ]
