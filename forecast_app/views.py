from django.http import JsonResponse
from django.shortcuts import render
from django.views.generic import DetailView

from forecast_app.models import Project, ForecastModel, Forecast
from utils.utilities import mean_abs_error_rows_for_project


def index(request):
    projects = Project.objects.all()
    return render(
        request,
        'index.html',
        context={'projects': projects},
    )


def about(request):
    return render(request, 'about.html')


def project_visualizations(request, pk):
    """
    View function to render various visualizations for a particular project.

    :param request:
    :param pk:
    :return:
    """
    # todo xx pull season_start_year and location from somewhere, probably form elements on the page
    season_start_year = 2016
    location = 'US National'

    project = Project.objects.get(pk=pk)
    mean_abs_error_rows = mean_abs_error_rows_for_project(project, season_start_year, location)
    return render(
        request,
        'project_visualizations.html',
        context={'project': project,
                 'season_start_year': season_start_year,
                 'location': location,
                 'mean_abs_error_rows': mean_abs_error_rows},
    )


class ProjectDetailView(DetailView):
    model = Project


class ForecastModelDetailView(DetailView):
    model = ForecastModel

    def get_context_data(self, **kwargs):
        context = super(ForecastModelDetailView, self).get_context_data(**kwargs)
        forecast_model = self.get_object()

        # pass a dict that maps Project TimeZeros to corresponding Forecasts this ForecastModel, or None if not found
        timezero_to_forecast = {}
        for time_zero in forecast_model.project.timezero_set.all():
            timezero_to_forecast[time_zero] = forecast_model.forecast_for_time_zero(time_zero)
        context['timezero_to_forecast'] = timezero_to_forecast

        return context


class ForecastDetailView(DetailView):
    model = Forecast


def json_download(request, pk):
    """
    :param request:
    :param pk: a Forecast pk
    :return: JSON version of the passed Forecast's data
    """
    forecast = Forecast.objects.get(pk=pk)
    location_target_dict = forecast.get_location_target_dict()
    response = JsonResponse(location_target_dict)
    response['Content-Disposition'] = 'attachment; filename="{data_filename}.json"'.format(
        data_filename=forecast.data_filename)
    return response
