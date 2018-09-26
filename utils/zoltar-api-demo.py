import os
import time

import click
import requests


# ---- ZOLTAR_HOST ----

ZOLTAR_HOST = 'https://reichlab-forecast-repository.herokuapp.com/'


#
# ---- REST and other utility functions ----
#

def get_resource(uri, token):
    response = requests.get(uri, headers={'Accept': 'application/json; indent=4',
                                          'Authorization': 'JWT {}'.format(token)})
    return response.json()


def delete_resource(uri, token):
    response = requests.delete(uri, headers={'Accept': 'application/json; indent=4',
                                             'Authorization': 'JWT {}'.format(token)})
    if response.status_code != 204:  # 204 No Content
        print('delete_resource(): unexpected status code: '.format(response.status_code))


def get_token(host, username, password):
    response = requests.post(host + '/api-token-auth/', {'username': username, 'password': password})
    return response.json()['token']


def upload_forecast(model_uri, token, timezero_date, file):
    # timezero_date format: yyyy-mm-dd
    # NB: date formats are currently inconsistent - yyyy-mm-dd vs. yyyymmdd (expected by POST). will be fixed
    timezero_date = timezero_date[:4] + timezero_date[5:7] + timezero_date[8:]  # remove '-'
    response = requests.post(model_uri + 'forecasts/',
                             headers={'Authorization': 'JWT {}'.format(token)},
                             data={'timezero_date': timezero_date},
                             files={'data_file': open(file, 'rb')})
    return response.json()  # UploadFileJobSerializer


def get_projects(host, token):
    return get_resource(host + '/api/projects/', token)


def get_project(host, token, project_pk):
    return get_resource(host + '/api/project/{}/'.format(project_pk), token)


def get_upload_file_job(host, token, upload_file_pk):
    return get_resource(host + '/api/uploadfilejob/{}/'.format(upload_file_pk), token)


def get_project_from_obj_list(project_list, name):
    for project in project_list:
        if project['name'] == name:
            return project

    return None


def get_forecast_from_obj(model, timezero_date):  # timezero_date format: yyyy-mm-dd
    for forecast in model['forecasts']:
        if forecast['timezero_date'] == timezero_date:
            return forecast

    return None


def get_forecast(host, token, forecast_pk):
    return get_resource(host + '/api/forecast/{}/'.format(forecast_pk), token)


# ---- the app ----

@click.command()
@click.argument('forecast_csv_file', type=click.Path(file_okay=True, exists=True))
def demo_zoltar_api_app(forecast_csv_file):
    """
    This app demonstrates some of Zoltar's API features. It assumes the projects defined in make_minimal_projects.py
    have been loaded, and that an account with the appropriate authorizations is identified in the below environment
    variables.

    Inputs:
    - DEV_USERNAME environment variable: username of account in server
    - DEV_PASSWORD environment variable: password for ""
    - forecast_csv_file app argument: path of the forecast *.cdc.csv file to load into the private project's
    """
    #
    # authenticate the dev user
    #
    username = os.environ.get('DEV_USERNAME')
    mo1_token = get_token(ZOLTAR_HOST, username, os.environ.get('DEV_PASSWORD'))
    print('- token', username, mo1_token)

    #
    # print all projects
    #
    projects = get_projects(ZOLTAR_HOST, mo1_token)
    print('- projects', projects)
    # example:
    # [{'id': 1,
    #   'url': 'http://localhost:8000/api/project/1/',
    #   'owner': 'http://localhost:8000/api/user/2/',
    #   'is_public': True,
    #   'name': 'public project',
    #   'description': '',
    #   'home_url': '',
    #   'core_data': '',
    #   'config_dict': {'visualization-y-label': 'Weighted ILI (%)'},
    #   'template': 'http://localhost:8000/api/project/1/template/',
    #   'truth': 'http://localhost:8000/api/project/1/truth/',
    #   'model_owners': ['http://localhost:8000/api/user/3/'],
    #   'models': ['http://localhost:8000/api/model/1/', 'http://localhost:8000/api/model/2/'],
    #   'targets': [{'name': 'Season onset', 'description': "The onset of the season is defined as ..."}, ...],
    #   'timezeros': [{'timezero_date': '2017-01-01', 'data_version_date': None}, ...]}]

    #
    # print one project's details. first get the project id from the previously-requested list of projects
    #
    # project = get_project_from_obj_list(projects, 'public project')
    project = get_project_from_obj_list(projects, 'private project')
    print('- project', get_project(ZOLTAR_HOST, mo1_token, project['id']))

    #
    # upload a forecast to the first model found, first printing the model's forecasts and then deleting the existing
    # one if found
    #
    model_uri = project['models'][0]
    model = get_resource(model_uri, mo1_token)
    print('- model (with forecasts)', model)
    # example:
    # {'id': 3,
    #  'url': 'http://localhost:8000/api/model/3/',
    #  'project': 'http://localhost:8000/api/project/2/',
    #  'owner': 'http://localhost:8000/api/user/3/',
    #  'name': 'Test ForecastModel1',
    #  'description': 'a ForecastModel for testing',
    #  'home_url': 'http://example.com',
    #  'aux_data_url': None,
    #  'forecasts': [{'timezero_date': '2017-01-17', 'data_version_date': None,
    #                 'forecast': None},
    #                {'timezero_date': '2017-01-24', 'data_version_date': None,
    #                 'forecast': 'http://localhost:8000/api/forecast/3/'}]}

    #
    # delete existing Forecast, if any
    #
    timezero_date = '2017-01-17'  # NB: date formats are currently inconsistent - yyyy-mm-dd vs. yyyymmdd. will be fixed
    forecast_for_tz_date = get_forecast_from_obj(model, timezero_date)
    forecast_uri = forecast_for_tz_date['forecast']
    print('- forecast_for_tz_date', forecast_for_tz_date)
    # example:
    # {'timezero_date': '2017-01-17', 'data_version_date': None, 'forecast': None}
    # {'timezero_date': '2017-01-24', 'data_version_date': None, 'forecast': 'http://localhost:8000/api/forecast/3/'}

    if forecast_uri:
        print('  = deleting existing forecast', forecast_uri)
        delete_resource(forecast_uri, mo1_token)
    else:
        print('  = no existing forecast')

    #
    # upload a new forecast
    #
    # from UploadFileJob:
    status_int_to_name = {0: 'PENDING', 1: 'S3_FILE_UPLOADED', 2: 'QUEUED', 3: 'S3_FILE_DOWNLOADED', 4: 'SUCCESS'}
    upload_file_job = upload_forecast(model_uri, mo1_token, timezero_date, forecast_csv_file)
    print('- upload_file_job', status_int_to_name[upload_file_job['status']], upload_file_job)
    # example:
    # {'id': 50,
    #  'url': 'http://localhost:8000/api/uploadfilejob/50/',
    #  'status': 2,
    #  'user': 'http://localhost:8000/api/user/3/',
    #  'created_at': '2018-09-05T09:18:21.346093-04:00',
    #  'updated_at': '2018-09-05T09:18:22.164622-04:00',
    #  'failure_message': '',
    #  'filename': 'EW1-KoTsarima-2017-01-17-small.csv',
    #  'input_json': "{'forecast_model_pk': 3, 'timezero_pk': 4}",
    #  'output_json': None}

    #
    # get the updated status via polling (busy wait every 1 second)
    #
    print('- polling for status change. upload_file_job pk:', upload_file_job['id'])
    while True:
        upload_file_job = get_upload_file_job(ZOLTAR_HOST, mo1_token, upload_file_job['id'])
        status = status_int_to_name[upload_file_job['status']]
        print('  =', status)
        if status == 'FAILED':
            print('  x failed')
            break
        if status == 'SUCCESS':
            break
        time.sleep(1)

    #
    # print the model's forecasts again - see the new one?
    #
    model = get_resource(model_uri, mo1_token)
    print('- updated model forecasts', model['forecasts'])

    #
    # get the new forecast from the upload_file_job by parsing the generic 'output_json' field
    #
    new_forecast_pk = upload_file_job['output_json']['forecast_pk']
    new_forecast = get_forecast(ZOLTAR_HOST, mo1_token, new_forecast_pk)
    print('- new forecast', new_forecast_pk, new_forecast)

    #
    # GET its data (default format is JSON)
    #
    data_uri = new_forecast['forecast_data']
    data_json = get_resource(data_uri, mo1_token)
    print('- data_json', data_json)

    # GET the data as CSV
    # - todo fix api_views.forecast_data() to use proper accept type rather than 'format' query parameter
    response = requests.get(data_uri,
                            headers={'Authorization': 'JWT {}'.format(mo1_token)},
                            params={'format': 'csv'})
    data_csv = response.content
    print('- data_csv', data_csv)


if __name__ == '__main__':
    demo_zoltar_api_app()