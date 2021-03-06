from django.contrib.auth.models import User
from rest_framework import serializers
from rest_framework.fields import CharField, IntegerField
from rest_framework.reverse import reverse

from forecast_app.models import Project, Target, TimeZero, ForecastModel, Forecast
from forecast_app.models.job import Job
from forecast_app.models.project import Unit
from forecast_app.views import forecast_models_owned_by_user, projects_and_roles_for_user
from utils.project_truth import oracle_model_for_project
from utils.utilities import YYYY_MM_DD_DATE_FORMAT


class UnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = Unit
        fields = ('id', 'url', 'name',)
        extra_kwargs = {
            'url': {'view_name': 'api-unit-detail'},
        }


class TargetSerializer(serializers.ModelSerializer):
    type = serializers.SerializerMethodField()


    class Meta:
        model = Target

        # always include these fields:
        fields = ('id', 'url', 'name', 'type', 'description', 'is_step_ahead',)

        # optionally/dynamically include these fields:
        # fields = ('step_ahead_increment', 'unit', 'range', 'cats')

        extra_kwargs = {
            'url': {'view_name': 'api-target-detail'},
        }


    def get_type(self, target):
        return target.type_as_str()


    def to_representation(self, target):
        # clear and re-cache the `self.fields` @cached_property for possible re-use by ListSerializer (many=True).
        # (recall that a single Serializer instance is re-used to generate all data in the ListSerializer queryset, but
        # we need to re-generate fields each time due to their being dynamic). implementation per
        # https://stackoverflow.com/questions/50290390/list-serializer-with-dynamic-fields-in-django-rest-framework
        try:
            del self.fields
        except AttributeError:
            pass
        self.fields

        self.add_optional_fields(target)
        return super().to_representation(target)


    def add_optional_fields(self, target):
        # dynamically add optional fields - see https://www.django-rest-framework.org/api-guide/serializers/#dynamically-modifying-fields .
        # note: we exclude 'niceties' like allow_null, help_text, required, style, etc.

        # first clear all optional contexts for possible re-use by ListSerializer (many=True)
        if 'range' in self.context:
            del self.context['range']
        if 'cats' in self.context:
            del self.context['cats']

        # add step_ahead_increment
        if target.is_step_ahead and (target.step_ahead_increment is not None):
            self.fields['step_ahead_increment'] = IntegerField()

        # add unit
        if target.unit is not None:
            self.fields['unit'] = CharField()

        # add range
        data_type = target.data_types()[0]  # the first is the preferred one
        target_ranges_qs = target.ranges  # target.value_i, target.value_f
        if target_ranges_qs.count() != 0:  # s/b exactly 2
            value_column = 'value_i' if data_type == Target.INTEGER_DATA_TYPE else 'value_f'
            target_ranges = target_ranges_qs.values_list(value_column, flat=True)
            target_ranges = sorted(target_ranges)
            self.context['range'] = [target_ranges[0], target_ranges[1]]
            self.fields['range'] = serializers.SerializerMethodField('get_range')

        # add cats
        cats_values = target.cats_values()
        if cats_values and (target.type != Target.BINARY_TARGET_TYPE):  # skip implicit binary -  added automatically
            if data_type == Target.DATE_DATA_TYPE:
                cats_values = [cat_date.strftime(YYYY_MM_DD_DATE_FORMAT) for cat_date in cats_values]
            self.context['cats'] = sorted(cats_values)
        elif target.type in [Target.NOMINAL_TARGET_TYPE, Target.DATE_TARGET_TYPE]:
            # handle the case of required cats list that must have come in but was empty
            self.context['cats'] = []
        if 'cats' in self.context:
            self.fields['cats'] = serializers.SerializerMethodField('get_cats')


    def get_range(self, target):
        return self.context['range']


    def get_cats(self, target):
        return self.context['cats']


class TimeZeroSerializer(serializers.HyperlinkedModelSerializer):
    # customize to use our standard format
    timezero_date = serializers.DateField(format=YYYY_MM_DD_DATE_FORMAT, input_formats=[YYYY_MM_DD_DATE_FORMAT])
    data_version_date = serializers.DateField(format=YYYY_MM_DD_DATE_FORMAT, input_formats=[YYYY_MM_DD_DATE_FORMAT])


    class Meta:
        model = TimeZero

        # always include these fields:
        fields = ('id', 'url', 'timezero_date', 'data_version_date', 'is_season_start',)

        # optionally/dynamically include these fields:
        # fields = ('season_name')

        extra_kwargs = {
            'url': {'view_name': 'api-timezero-detail'},
        }


    def to_representation(self, timezero):
        # clear and re-cache the `self.fields` @cached_property for possible re-use by ListSerializer (many=True).
        # (recall that a single Serializer instance is re-used to generate all data in the ListSerializer queryset, but
        # we need to re-generate fields each time due to their being dynamic). implementation per
        # https://stackoverflow.com/questions/50290390/list-serializer-with-dynamic-fields-in-django-rest-framework
        try:
            del self.fields
        except AttributeError:
            pass
        self.fields

        self.add_optional_fields(timezero)
        return super().to_representation(timezero)


    def add_optional_fields(self, timezero):
        # dynamically add optional fields - see https://www.django-rest-framework.org/api-guide/serializers/#dynamically-modifying-fields .
        # note: we exclude 'niceties' like allow_null, help_text, required, style, etc.

        # add season_name
        if timezero.is_season_start:
            self.fields['season_name'] = CharField()


class ProjectSerializer(serializers.HyperlinkedModelSerializer):
    time_interval_type = serializers.SerializerMethodField()
    truth = serializers.SerializerMethodField()

    models = serializers.SerializerMethodField()  # HyperlinkedRelatedField did not allow excluding non-oracle models
    units = serializers.HyperlinkedRelatedField(view_name='api-unit-detail', many=True, read_only=True)
    targets = serializers.HyperlinkedRelatedField(view_name='api-target-detail', many=True, read_only=True)
    timezeros = serializers.HyperlinkedRelatedField(view_name='api-timezero-detail', many=True, read_only=True)


    class Meta:
        model = Project
        fields = ('id', 'url', 'owner', 'is_public', 'name', 'description', 'home_url', 'logo_url', 'core_data',
                  'time_interval_type', 'visualization_y_label', 'truth', 'model_owners', 'models', 'units', 'targets',
                  'timezeros',)
        extra_kwargs = {
            'url': {'view_name': 'api-project-detail'},
            'owner': {'view_name': 'api-user-detail'},
            'model_owners': {'view_name': 'api-user-detail'},
        }


    def get_models(self, project):
        # per [Possibility to filter HyperlinkedIdentityField with many=True with queryset](https://github.com/encode/django-rest-framework/issues/3932)
        request = self.context['request']
        models = []
        for forecast_model in project.models.filter(is_oracle=False):
            models.append(reverse('api-model-detail', args=[forecast_model.pk], request=request))
        return models


    def get_time_interval_type(self, project):
        return project.time_interval_type_as_str()


    def get_truth(self, project):
        request = self.context['request']
        return reverse('api-truth-detail', args=[project.pk], request=request)


class TruthSerializer(serializers.ModelSerializer):
    project = serializers.SerializerMethodField()
    source = serializers.SerializerMethodField()
    created_at = serializers.SerializerMethodField()
    issued_at = serializers.SerializerMethodField()


    class Meta:
        model = Project
        fields = ('id', 'url', 'project', 'source', 'created_at', 'issued_at',)
        extra_kwargs = {
            'url': {'view_name': 'api-truth-detail'},
        }


    def get_project(self, project):
        request = self.context['request']
        return reverse('api-project-detail', args=[project.pk], request=request)


    def get_source(self, project):
        oracle_model = oracle_model_for_project(project)
        last_truth_forecast = oracle_model.forecasts.last() if oracle_model else None
        return last_truth_forecast.source if last_truth_forecast else None


    def get_created_at(self, project):
        oracle_model = oracle_model_for_project(project)
        last_truth_forecast = oracle_model.forecasts.last() if oracle_model else None
        return last_truth_forecast.created_at.isoformat() if last_truth_forecast else None


    def get_issued_at(self, project):
        oracle_model = oracle_model_for_project(project)
        last_truth_forecast = oracle_model.forecasts.last() if oracle_model else None
        return last_truth_forecast.issued_at.isoformat() if last_truth_forecast else None


class UserSerializer(serializers.ModelSerializer):
    owned_models = serializers.SerializerMethodField()
    projects_and_roles = serializers.SerializerMethodField()


    class Meta:
        model = User
        fields = ('id', 'url', 'username', 'owned_models', 'projects_and_roles',)
        extra_kwargs = {
            'url': {'view_name': 'api-user-detail'},
        }


    def get_owned_models(self, user):
        request = self.context['request']
        return [reverse('api-model-detail', args=[forecast_model.pk], request=request) for forecast_model in
                forecast_models_owned_by_user(user)]


    def get_projects_and_roles(self, user):
        request = self.context['request']
        return [{'project': reverse('api-project-detail', args=[project.pk], request=request),
                 'is_project_owner': role == 'Project Owner',
                 'is_model_owner': role == 'Model Owner'}
                for project, role in projects_and_roles_for_user(user)]


class JobSerializer(serializers.ModelSerializer):
    user = serializers.HyperlinkedRelatedField(view_name='api-user-detail', read_only=True)
    input_json = serializers.JSONField()  # per https://github.com/dmkoch/django-jsonfield/issues/188
    output_json = serializers.JSONField()  # ""


    class Meta:
        model = Job
        fields = ('id', 'url', 'status', 'user', 'created_at', 'updated_at', 'failure_message',
                  'input_json', 'output_json',)
        extra_kwargs = {
            'url': {'view_name': 'api-job-detail'},
        }


class ForecastModelSerializer(serializers.ModelSerializer):
    owner = serializers.HyperlinkedRelatedField(view_name='api-user-detail', read_only=True)
    project = serializers.HyperlinkedRelatedField(view_name='api-project-detail', read_only=True)
    forecasts = serializers.HyperlinkedRelatedField(view_name='api-forecast-detail', many=True, read_only=True)


    class Meta:
        model = ForecastModel
        fields = ('id', 'url', 'project', 'owner', 'name', 'abbreviation', 'team_name', 'description',
                  'contributors', 'license', 'notes', 'citation', 'methods', 'home_url', 'aux_data_url',
                  'forecasts',)
        extra_kwargs = {
            'url': {'view_name': 'api-model-detail'},
        }


class ForecastSerializer(serializers.ModelSerializer):
    forecast_model = serializers.HyperlinkedRelatedField(view_name='api-model-detail', read_only=True)
    time_zero = TimeZeroSerializer()
    forecast_data = serializers.SerializerMethodField()


    class Meta:
        model = Forecast
        fields = ('id', 'url', 'forecast_model', 'source', 'time_zero', 'created_at', 'issued_at', 'notes',
                  'forecast_data',)
        extra_kwargs = {
            'url': {'view_name': 'api-forecast-detail'},
        }


    def get_forecast_data(self, forecast):
        request = self.context['request']
        return reverse('api-forecast-data', args=[forecast.pk], request=request)
