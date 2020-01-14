import datetime
import logging
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from forecast_app.api_views import csv_response_for_project_truth_data
from forecast_app.models import Project, TimeZero, Target, Score
from forecast_app.models.forecast_model import ForecastModel
from forecast_app.views import ProjectDetailView, _location_to_actual_points, _location_to_actual_max_val
from utils.cdc import load_cdc_csv_forecast_file, make_cdc_locations_and_targets
from utils.make_thai_moph_project import create_thai_locations_and_targets
from utils.utilities import get_or_create_super_po_mo_users
from utils.project import create_project_from_json


logging.getLogger().setLevel(logging.ERROR)


class ProjectTestCase(TestCase):
    """
    """


    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create()
        cls.time_zero = TimeZero.objects.create(project=cls.project, timezero_date=datetime.date(2017, 1, 1))
        make_cdc_locations_and_targets(cls.project)

        cls.forecast_model = ForecastModel.objects.create(project=cls.project, name='fm1')
        cls.forecast = load_cdc_csv_forecast_file(xx, cls.forecast_model, Path(
            'forecast_app/tests/model_error/ensemble/EW1-KoTstable-2017-01-17.csv'), cls.time_zero)


    def test_load_truth_data(self):
        self.project.load_truth_data(Path('forecast_app/tests/truth_data/truths-ok.csv'))
        self.assertEqual(7, self.project.truth_data_qs().count())
        self.assertTrue(self.project.is_truth_data_loaded())
        self.assertEqual('truths-ok.csv', self.project.truth_csv_filename)

        self.project.delete_truth_data()
        self.assertFalse(self.project.is_truth_data_loaded())
        self.assertFalse(self.project.truth_csv_filename)

        # csv references non-existent TimeZero in Project: should not raise error
        self.project.load_truth_data(Path('forecast_app/tests/truth_data/truths-bad-timezero.csv'),
                                     'truths-bad-timezero.csv')

        # csv references non-existent location in Project: should not raise error
        self.project.load_truth_data(Path('forecast_app/tests/truth_data/truths-bad-location.csv'),
                                     'truths-bad-location.csv')

        # csv references non-existent target in Project: should not raise error
        self.project.load_truth_data(Path('forecast_app/tests/truth_data/truths-bad-target.csv'),
                                     'truths-bad-target.csv')

        project2 = Project.objects.create()
        make_cdc_locations_and_targets(project2)
        self.assertEqual(0, project2.truth_data_qs().count())
        self.assertFalse(project2.is_truth_data_loaded())

        TimeZero.objects.create(project=project2, timezero_date=datetime.date(2017, 1, 1))
        project2.load_truth_data(Path('forecast_app/tests/truth_data/truths-ok.csv'))
        self.assertEqual(7, project2.truth_data_qs().count())

        # test get_truth_data_preview()
        exp_truth_preview = [
            (datetime.date(2017, 1, 1), 'US National', '1 wk ahead', 0.73102),
            (datetime.date(2017, 1, 1), 'US National', '2 wk ahead', 0.688338),
            (datetime.date(2017, 1, 1), 'US National', '3 wk ahead', 0.732049),
            (datetime.date(2017, 1, 1), 'US National', '4 wk ahead', 0.911641),
            (datetime.date(2017, 1, 1), 'US National', 'Season peak percentage', None),
            (datetime.date(2017, 1, 1), 'US National', 'Season peak week', None),
            (datetime.date(2017, 1, 1), 'US National', 'Season onset', 201747.0)
        ]
        self.assertEqual(exp_truth_preview, project2.get_truth_data_preview())


    def test_truth_date_format(self):
        self.project.load_truth_data(Path('forecast_app/tests/truth_data/truths-ok.csv'))
        response = csv_response_for_project_truth_data(self.project)
        exp_content = ['timezero,location,target,value',
                       '20170101,US National,1 wk ahead,0.73102',
                       '20170101,US National,2 wk ahead,0.688338',
                       '20170101,US National,3 wk ahead,0.732049',
                       '20170101,US National,4 wk ahead,0.911641',
                       '20170101,US National,Season peak percentage,',
                       '20170101,US National,Season peak week,',
                       '20170101,US National,Season onset,201747.0',
                       '']
        act_content = response.content.decode("utf-8").split('\r\n')
        self.assertEqual(exp_content, act_content)


    def test_timezeros_unique(self):
        project = Project.objects.create()
        with self.assertRaises(ValidationError) as context:
            timezeros = [TimeZero.objects.create(project=project, timezero_date=datetime.date(2017, 1, 1)),
                         TimeZero.objects.create(project=project, timezero_date=datetime.date(2017, 1, 1))]
            project.timezeros.add(*timezeros)
            project.save()
        self.assertIn("found duplicate TimeZero.timezero_date", str(context.exception))


    def test_get_num_rows(self):
        time_zero2 = TimeZero.objects.create(project=self.project, timezero_date=datetime.date(2017, 1, 2))
        load_cdc_csv_forecast_file(xx, self.forecast_model,
                                   Path('forecast_app/tests/model_error/ensemble/EW1-KoTstable-2017-01-17.csv'),
                                   time_zero2)
        self.assertEqual(self.project.get_num_forecast_rows_all_models(), 8019 * 2)
        self.assertEqual(self.project.get_num_forecast_rows_all_models_estimated(),
                         8019 * 2)  # exact b/c uniform forecasts


    def test_score_csv_file_cache(self):
        # verify post_save worked
        self.assertIsNotNone(self.project.score_csv_file_cache)

        # test CSV file gets created
        self.project.load_truth_data(Path('utils/ensemble-truth-table-script/truths-2016-2017-reichlab.csv'))
        Score.ensure_all_scores_exist()
        score = Score.objects.filter(abbreviation='abs_error').first()
        score.update_score_for_model(self.forecast_model)

        # NB: this test assumes delete was called before upload
        with patch('utils.cloud_file.delete_file') as delete_file_mock, \
                patch('utils.cloud_file.upload_file') as upload_file_mock:
            self.project.score_csv_file_cache.update_score_csv_file_cache()

            args = delete_file_mock.call_args[0]  # delete_file(the_object)
            delete_file_mock.assert_called_once()
            self.assertEqual(self.project.score_csv_file_cache, args[0])

            args = upload_file_mock.call_args[0]  # upload_file(the_object, data_file)
            upload_file_mock.assert_called_once()
            self.assertEqual(self.project.score_csv_file_cache, args[0])
            self.assertGreater(len(args[1]), 0)


    def test_row_count_cache(self):
        self.assertIsNotNone(self.project.row_count_cache)  # verify post_save worked
        # assume last_update default works
        self.assertIsNone(self.project.row_count_cache.row_count)

        self.project.row_count_cache.update_row_count_cache()
        # NB: we assume last_update default works
        self.assertEqual(self.project.get_num_forecast_rows_all_models(), self.project.row_count_cache.row_count)


    def test_summary_counts(self):
        self.assertEqual((1, 1, 8019), self.project.get_summary_counts())  # num_models, num_forecasts, num_rows


    def test_timezero_seasons(self):
        _, _, po_user, _, _, _ = get_or_create_super_po_mo_users(is_create_super=True)
        project2 = create_project_from_json(Path('forecast_app/tests/projects/cdc-project.json'), po_user)

        # 2015-01-01 <no season>  time_zero1    not within
        # 2015-02-01 <no season>  time_zero2    not within
        # 2016-02-01 season1      time_zero3  start
        # 2017-01-01   ""         time_zero4    within
        # 2017-02-01 season2      time_zero5  start
        # 2018-01-01 season3      time_zero6  start
        time_zero1 = TimeZero.objects.create(project=project2, timezero_date=datetime.date(2015, 1, 1),
                                             is_season_start=False)  # no season for this TZ. explicit arg
        time_zero2 = TimeZero.objects.create(project=project2, timezero_date=datetime.date(2015, 2, 1),
                                             is_season_start=False)  # ""
        time_zero3 = TimeZero.objects.create(project=project2, timezero_date=datetime.date(2016, 2, 1),
                                             is_season_start=True, season_name='season1')  # start season1. 2 TZs
        time_zero4 = TimeZero.objects.create(project=project2, timezero_date=datetime.date(2017, 1, 1)
                                             )  # in season1. default args
        time_zero5 = TimeZero.objects.create(project=project2, timezero_date=datetime.date(2017, 2, 1),
                                             is_season_start=True, season_name='season2')  # start season2. 1 TZ
        time_zero6 = TimeZero.objects.create(project=project2, timezero_date=datetime.date(2018, 1, 1),
                                             is_season_start=True, season_name='season3')  # start season3. 1 TZ

        # test Project.timezeros_num_forecasts() b/c it's convenient here
        self.assertEqual(
            [(time_zero1, 0), (time_zero2, 0), (time_zero3, 0), (time_zero4, 0), (time_zero5, 0), (time_zero6, 0)],
            ProjectDetailView.timezeros_num_forecasts(project2))

        # above create() calls test valid TimeZero season values

        # test invalid TimeZero season values
        with self.assertRaises(ValidationError) as context:
            TimeZero.objects.create(project=project2, timezero_date=datetime.date(2017, 1, 1),
                                    is_season_start=True, season_name=None)  # season start, no season name (passed)
        self.assertIn('passed is_season_start with no season_name', str(context.exception))

        with self.assertRaises(ValidationError) as context:
            TimeZero.objects.create(project=project2, timezero_date=datetime.date(2017, 1, 1),
                                    is_season_start=True)  # season start, no season name (default)
        self.assertIn('passed is_season_start with no season_name', str(context.exception))

        with self.assertRaises(ValidationError) as context:
            TimeZero.objects.create(project=project2, timezero_date=datetime.date(2017, 1, 1),
                                    is_season_start=False, season_name='season4')  # no season start, season name
        self.assertIn('passed season_name but not is_season_start', str(context.exception))

        # test seasons()
        self.assertEqual(['season1', 'season2', 'season3'], sorted(project2.seasons()))

        # test start_end_dates_for_season()
        self.assertEqual((time_zero3.timezero_date, time_zero4.timezero_date),
                         project2.start_end_dates_for_season('season1'))  # two TZs
        self.assertEqual((time_zero5.timezero_date, time_zero5.timezero_date),
                         project2.start_end_dates_for_season('season2'))  # only one TZ -> start == end
        self.assertEqual((time_zero6.timezero_date, time_zero6.timezero_date),
                         project2.start_end_dates_for_season('season3'))  # ""

        # test timezeros_in_season()
        with self.assertRaises(RuntimeError) as context:
            project2.timezeros_in_season('not a valid season')
        self.assertIn('invalid season_name', str(context.exception))

        self.assertEqual([time_zero3, time_zero4], project2.timezeros_in_season('season1'))
        self.assertEqual([time_zero5], project2.timezeros_in_season('season2'))
        self.assertEqual([time_zero6], project2.timezeros_in_season('season3'))

        # test timezeros_in_season() w/no season, but followed by some seasons
        self.assertEqual([time_zero1, time_zero2], project2.timezeros_in_season(None))

        # test timezeros_in_season() w/no season, followed by no seasons, i.e., no seasons at all in the project
        project3 = Project.objects.create()
        time_zero7 = TimeZero.objects.create(project=project3, timezero_date=datetime.date(2015, 1, 1))
        self.assertEqual([time_zero7], project3.timezeros_in_season(None))

        # test start_end_dates_for_season()
        self.assertEqual((time_zero7.timezero_date, time_zero7.timezero_date),
                         project3.start_end_dates_for_season(None))

        # test location_to_max_val()
        forecast_model = ForecastModel.objects.create(project=project2)
        load_cdc_csv_forecast_file(xx, forecast_model,
                                   Path('forecast_app/tests/model_error/ensemble/EW1-KoTstable-2017-01-17.csv'),
                                   time_zero3)
        exp_location_to_max_val = {'HHS Region 1': 2.06145600601835, 'HHS Region 10': 2.89940153907353,
                                   'HHS Region 2': 4.99776594895244, 'HHS Region 3': 2.99944727598047,
                                   'HHS Region 4': 2.62168214634388, 'HHS Region 5': 2.19233072084465,
                                   'HHS Region 6': 4.41926018901693, 'HHS Region 7': 2.79371802884364,
                                   'HHS Region 8': 1.69920709944699, 'HHS Region 9': 3.10232205135854,
                                   'US National': 3.00101461253164}
        act_location_to_max_val = project2.location_to_max_val('season1', project2.step_ahead_targets())
        self.assertEqual(exp_location_to_max_val, act_location_to_max_val)

        # test timezero_to_season_name()
        exp_timezero_to_season_name = {
            time_zero1: None,
            time_zero2: None,
            time_zero3: 'season1',
            time_zero4: 'season1',
            time_zero5: 'season2',
            time_zero6: 'season3',
        }
        self.assertEqual(exp_timezero_to_season_name, project2.timezero_to_season_name())

        # test season_name_containing_timezero(). test both cases: first timezero starts a season or not
        timezero_to_exp_season_name = {time_zero1: None,
                                       time_zero2: None,
                                       time_zero3: 'season1',
                                       time_zero4: 'season1',
                                       time_zero5: 'season2',
                                       time_zero6: 'season3'}
        for timezero, exp_season_name in timezero_to_exp_season_name.items():
            self.assertEqual(exp_season_name, project2.season_name_containing_timezero(timezero))

        del (timezero_to_exp_season_name[time_zero1])
        del (timezero_to_exp_season_name[time_zero2])
        time_zero1.delete()
        time_zero2.delete()
        for timezero, exp_season_name in timezero_to_exp_season_name.items():
            self.assertEqual(exp_season_name, project2.season_name_containing_timezero(timezero))


    def test_target_step_ahead_validation(self):
        project2 = Project.objects.create()

        # no is_step_ahead, no step_ahead_increment: valid
        target = Target.objects.create(project=project2, name="Test target", description="d",
                                       point_value_type=Target.POINT_FLOAT)
        self.assertFalse(target.is_step_ahead)

        # yes is_step_ahead, yes step_ahead_increment: valid
        target = Target.objects.create(project=project2, name="Test target", description="d",
                                       is_step_ahead=True, step_ahead_increment=1, point_value_type=Target.POINT_FLOAT)
        self.assertTrue(target.is_step_ahead)
        self.assertEqual(1, target.step_ahead_increment)

        # yes is_step_ahead, no step_ahead_increment: invalid
        # no is_step_ahead, yes step_ahead_increment: invalid
        # NB: we can't test these b/c step_ahead_increment can be zero


    def test_target_date_validation(self):
        project2 = Project.objects.create()

        # yes is_date, no is_step_ahead: valid
        Target.objects.create(project=project2, name="t", description="d", is_date=True, is_step_ahead=False,
                              point_value_type=Target.POINT_FLOAT)

        # no is_date, yes is_step_ahead: valid
        Target.objects.create(project=project2, name="t", description="d", is_date=False, is_step_ahead=True,
                              point_value_type=Target.POINT_FLOAT)

        # yes is_date, yes is_step_ahead: invalid
        with self.assertRaises(ValidationError) as context:
            Target.objects.create(project=project2, name="t", description="d", is_date=True, is_step_ahead=True,
                                  point_value_type=Target.POINT_FLOAT)
        self.assertIn('passed is_date and is_step_ahead', str(context.exception))


    def test_visualization_targets(self):
        self.assertEqual(['1 wk ahead', '2 wk ahead', '3 wk ahead', '4 wk ahead'],
                         [target.name for target in self.project.step_ahead_targets()])


    def test_reference_target_for_actual_values(self):
        self.assertEqual(Target.objects.filter(project=self.project, name='1 wk ahead').first(),
                         self.project.reference_target_for_actual_values())

        project = Project.objects.create()
        make_cdc_locations_and_targets(project)
        Target.objects.filter(project=project, name='1 wk ahead').delete()
        self.assertEqual(Target.objects.filter(project=project, name='2 wk ahead').first(),
                         project.reference_target_for_actual_values())

        project = Project.objects.create()
        create_thai_locations_and_targets(project)
        self.assertEqual(Target.objects.filter(project=project, name='1_biweek_ahead').first(),
                         project.reference_target_for_actual_values())

        project = Project.objects.create()  # no Targets
        self.assertIsNone(project.reference_target_for_actual_values())


    def test_actual_values(self):
        project = Project.objects.create()
        make_cdc_locations_and_targets(project)

        # create TimeZeros only for the first few in truths-2017-2018-reichlab.csv (other truth will be skipped)
        TimeZero.objects.create(project=project, timezero_date=datetime.date(2017, 7, 23))
        TimeZero.objects.create(project=project, timezero_date=datetime.date(2017, 7, 30))
        TimeZero.objects.create(project=project, timezero_date=datetime.date(2017, 8, 6))

        project.load_truth_data(Path('utils/ensemble-truth-table-script/truths-2017-2018-reichlab.csv'))
        exp_loc_tz_date_to_actual_vals = {
            'HHS Region 1': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): [0.303222],
                datetime.date(2017, 8, 6): [0.286054]},
            'HHS Region 10': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): [0.364459],
                datetime.date(2017, 8, 6): [0.240377]},
            'HHS Region 2': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): [1.32634],
                datetime.date(2017, 8, 6): [1.34713]},
            'HHS Region 3': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): [0.797999],
                datetime.date(2017, 8, 6): [0.586092]},
            'HHS Region 4': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): [0.476357],
                datetime.date(2017, 8, 6): [0.483647]},
            'HHS Region 5': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): [0.602327],
                datetime.date(2017, 8, 6): [0.612967]},
            'HHS Region 6': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): [1.15229],
                datetime.date(2017, 8, 6): [0.96867]},
            'HHS Region 7': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): [0.174172],
                datetime.date(2017, 8, 6): [0.115888]},
            'HHS Region 8': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): [0.33984],
                datetime.date(2017, 8, 6): [0.359646]},
            'HHS Region 9': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): [0.892872],
                datetime.date(2017, 8, 6): [0.912778]},
            'US National': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): [0.73102],
                datetime.date(2017, 8, 6): [0.688338]},
        }
        self.assertEqual(exp_loc_tz_date_to_actual_vals, project.location_timezero_date_to_actual_vals(None))

        # test _location_to_actual_points()
        exp_location_to_actual_points = {'HHS Region 1': [None, 0.303222, 0.286054],
                                         'HHS Region 10': [None, 0.364459, 0.240377],
                                         'HHS Region 2': [None, 1.32634, 1.34713],
                                         'HHS Region 3': [None, 0.797999, 0.586092],
                                         'HHS Region 4': [None, 0.476357, 0.483647],
                                         'HHS Region 5': [None, 0.602327, 0.612967],
                                         'HHS Region 6': [None, 1.15229, 0.96867],
                                         'HHS Region 7': [None, 0.174172, 0.115888],
                                         'HHS Region 8': [None, 0.33984, 0.359646],
                                         'HHS Region 9': [None, 0.892872, 0.912778],
                                         'US National': [None, 0.73102, 0.688338]}
        self.assertEqual(exp_location_to_actual_points, _location_to_actual_points(exp_loc_tz_date_to_actual_vals))

        # test _location_to_actual_max_val()
        exp_location_to_actual_max_val = {'HHS Region 1': 0.303222, 'HHS Region 10': 0.364459, 'HHS Region 2': 1.34713,
                                          'HHS Region 3': 0.797999, 'HHS Region 4': 0.483647, 'HHS Region 5': 0.612967,
                                          'HHS Region 6': 1.15229, 'HHS Region 7': 0.174172, 'HHS Region 8': 0.359646,
                                          'HHS Region 9': 0.912778, 'US National': 0.73102}
        self.assertEqual(exp_location_to_actual_max_val, _location_to_actual_max_val(exp_loc_tz_date_to_actual_vals))

        del exp_loc_tz_date_to_actual_vals['HHS Region 1'][datetime.date(2017, 7, 30)]  # leave only None
        del exp_loc_tz_date_to_actual_vals['HHS Region 1'][datetime.date(2017, 8, 6)]  # ""
        exp_location_to_actual_max_val = {'HHS Region 1': None, 'HHS Region 10': 0.364459, 'HHS Region 2': 1.34713,
                                          'HHS Region 3': 0.797999, 'HHS Region 4': 0.483647, 'HHS Region 5': 0.612967,
                                          'HHS Region 6': 1.15229, 'HHS Region 7': 0.174172, 'HHS Region 8': 0.359646,
                                          'HHS Region 9': 0.912778, 'US National': 0.73102}
        self.assertEqual(exp_location_to_actual_max_val, _location_to_actual_max_val(exp_loc_tz_date_to_actual_vals))

        # test 2 step ahead target first one not available
        project.targets.get(name='1 wk ahead').delete()  # recall: TruthData.target: on_delete=models.CASCADE
        exp_loc_tz_date_to_actual_vals = {
            'HHS Region 1': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): None,
                datetime.date(2017, 8, 6): [0.286054]},
            'HHS Region 10': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): None,
                datetime.date(2017, 8, 6): [0.240377]},
            'HHS Region 2': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): None,
                datetime.date(2017, 8, 6): [1.34713]},
            'HHS Region 3': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): None,
                datetime.date(2017, 8, 6): [0.586092]},
            'HHS Region 4': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): None,
                datetime.date(2017, 8, 6): [0.483647]},
            'HHS Region 5': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): None,
                datetime.date(2017, 8, 6): [0.612967]},
            'HHS Region 6': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): None,
                datetime.date(2017, 8, 6): [0.96867]},
            'HHS Region 7': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): None,
                datetime.date(2017, 8, 6): [0.115888]},
            'HHS Region 8': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): None,
                datetime.date(2017, 8, 6): [0.359646]},
            'HHS Region 9': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): None,
                datetime.date(2017, 8, 6): [0.912778]},
            'US National': {
                datetime.date(2017, 7, 23): None,
                datetime.date(2017, 7, 30): None,
                datetime.date(2017, 8, 6): [0.688338]}
        }
        self.assertEqual(exp_loc_tz_date_to_actual_vals, project.location_timezero_date_to_actual_vals(None))

        # test no step ahead targets available
        project.targets.all().delete()
        self.assertEqual({}, project.location_timezero_date_to_actual_vals(None))


    def test_loc_target_tz_date_to_truth(self):
        # at this point self.project.timezeros.all() = <QuerySet [(1, datetime.date(2017, 1, 1), None, False, None)]>,
        # so add remaining TimeZeros so that truths are not skipped when loading mean-abs-error-truths-dups.csv
        TimeZero.objects.create(project=self.project, timezero_date=datetime.date(2016, 12, 18))
        TimeZero.objects.create(project=self.project, timezero_date=datetime.date(2016, 12, 25))
        # we omit 20170108

        self.project.delete_truth_data()
        self.project.load_truth_data(Path('forecast_app/tests/truth_data/mean-abs-error-truths-dups.csv'))

        exp_loc_target_tz_date_to_truth = {
            'HHS Region 1': {
                '1 wk ahead': {
                    datetime.date(2017, 1, 1): [1.52411],
                    datetime.date(2016, 12, 18): [1.41861],
                    datetime.date(2016, 12, 25): [1.57644],
                },
                '2 wk ahead': {
                    datetime.date(2017, 1, 1): [1.73987],
                    datetime.date(2016, 12, 18): [1.57644],
                    datetime.date(2016, 12, 25): [1.52411],
                },
                '3 wk ahead': {
                    datetime.date(2017, 1, 1): [2.06524],
                    datetime.date(2016, 12, 18): [1.52411],
                    datetime.date(2016, 12, 25): [1.73987],
                },
                '4 wk ahead': {
                    datetime.date(2017, 1, 1): [2.51375],
                    datetime.date(2016, 12, 18): [1.73987],
                    datetime.date(2016, 12, 25): [2.06524],
                }},
            'US National': {
                '1 wk ahead': {
                    datetime.date(2017, 1, 1): [3.08492],
                    datetime.date(2016, 12, 18): [3.36496, 9.0],  # NB two!
                    datetime.date(2016, 12, 25): [3.0963],
                },
                '2 wk ahead': {
                    datetime.date(2017, 1, 1): [3.51496],
                    datetime.date(2016, 12, 18): [3.0963],
                    datetime.date(2016, 12, 25): [3.08492],
                },
                '3 wk ahead': {
                    datetime.date(2017, 1, 1): [3.8035],
                    datetime.date(2016, 12, 18): [3.08492],
                    datetime.date(2016, 12, 25): [3.51496],
                },
                '4 wk ahead': {
                    datetime.date(2017, 1, 1): [4.45059],
                    datetime.date(2016, 12, 18): [3.51496],
                    datetime.date(2016, 12, 25): [3.8035],
                }
            }
        }
        _conv_loc_target_tz_date_to_truth_to_default_dict(exp_loc_target_tz_date_to_truth)
        self.assertEqual(exp_loc_target_tz_date_to_truth,
                         self.project.location_target_name_tz_date_to_truth())  # target__id


    def test_location_timezero_date_to_actual_vals_multi_season(self):
        # test multiple seasons
        project = Project.objects.create()
        make_cdc_locations_and_targets(project)

        # create TimeZeros only for the first few in truths-2017-2018-reichlab.csv (other truth will be skipped),
        # separated into two small seasons
        TimeZero.objects.create(project=project, timezero_date=datetime.date(2017, 7, 23),
                                is_season_start=True, season_name='season1')
        TimeZero.objects.create(project=project, timezero_date=datetime.date(2017, 7, 30))
        TimeZero.objects.create(project=project, timezero_date=datetime.date(2017, 8, 6),
                                is_season_start=True, season_name='season2')
        TimeZero.objects.create(project=project, timezero_date=datetime.date(2017, 8, 13))
        project.load_truth_data(Path('utils/ensemble-truth-table-script/truths-2017-2018-reichlab.csv'))

        # test location_target_name_tz_date_to_truth() with above multiple seasons - done in this method b/c we've
        # set up some seasons :-)
        self.assertEqual(_exp_loc_tz_date_to_actual_vals_season_1a(),
                         project.location_target_name_tz_date_to_truth('season1'))  # target__id

        # test location_timezero_date_to_actual_vals() with above multiple seasons
        self.assertEqual(_exp_loc_tz_date_to_actual_vals_season_1b(),
                         project.location_timezero_date_to_actual_vals('season1'))
        self.assertEqual(_exp_loc_tz_date_to_actual_vals_season_2b(),
                         project.location_timezero_date_to_actual_vals('season2'))


    def test_0_step_target(self):
        project = Project.objects.create()
        make_cdc_locations_and_targets(project)

        # create TimeZeros only for the first few in truths-2017-2018-reichlab.csv (other truth will be skipped)
        TimeZero.objects.create(project=project, timezero_date=datetime.date(2017, 7, 23))
        TimeZero.objects.create(project=project, timezero_date=datetime.date(2017, 7, 30))
        TimeZero.objects.create(project=project, timezero_date=datetime.date(2017, 8, 6))

        project.load_truth_data(Path('utils/ensemble-truth-table-script/truths-2017-2018-reichlab.csv'))

        # change '1 wk ahead' to '0 wk ahead' in Target and truth data. also tests that target names are not used
        # (ids or step_ahead_increment should be used)
        target = project.targets.get(name='1 wk ahead')
        target.name = '0 wk ahead'
        target.step_ahead_increment = 0
        target.save()

        exp_loc_tz_date_to_actual_vals = {
            'HHS Region 1': {datetime.date(2017, 7, 23): [0.303222],
                             datetime.date(2017, 7, 30): [0.286054],
                             datetime.date(2017, 8, 6): [0.341359]},
            'HHS Region 10': {datetime.date(2017, 7, 23): [0.364459],
                              datetime.date(2017, 7, 30): [0.240377],
                              datetime.date(2017, 8, 6): [0.126923]},
            'HHS Region 2': {datetime.date(2017, 7, 23): [1.32634],
                             datetime.date(2017, 7, 30): [1.34713],
                             datetime.date(2017, 8, 6): [1.15738]},
            'HHS Region 3': {datetime.date(2017, 7, 23): [0.797999],
                             datetime.date(2017, 7, 30): [0.586092],
                             datetime.date(2017, 8, 6): [0.611163]},
            'HHS Region 4': {datetime.date(2017, 7, 23): [0.476357],
                             datetime.date(2017, 7, 30): [0.483647],
                             datetime.date(2017, 8, 6): [0.674289]},
            'HHS Region 5': {datetime.date(2017, 7, 23): [0.602327],
                             datetime.date(2017, 7, 30): [0.612967],
                             datetime.date(2017, 8, 6): [0.637141]},
            'HHS Region 6': {datetime.date(2017, 7, 23): [1.15229],
                             datetime.date(2017, 7, 30): [0.96867],
                             datetime.date(2017, 8, 6): [1.02289]},
            'HHS Region 7': {datetime.date(2017, 7, 23): [0.174172],
                             datetime.date(2017, 7, 30): [0.115888],
                             datetime.date(2017, 8, 6): [0.112074]},
            'HHS Region 8': {datetime.date(2017, 7, 23): [0.33984],
                             datetime.date(2017, 7, 30): [0.359646],
                             datetime.date(2017, 8, 6): [0.326402]},
            'HHS Region 9': {datetime.date(2017, 7, 23): [0.892872],
                             datetime.date(2017, 7, 30): [0.912778],
                             datetime.date(2017, 8, 6): [1.012]},
            'US National': {datetime.date(2017, 7, 23): [0.73102],
                            datetime.date(2017, 7, 30): [0.688338],
                            datetime.date(2017, 8, 6): [0.732049]}
        }
        self.assertEqual(exp_loc_tz_date_to_actual_vals, project.location_timezero_date_to_actual_vals(None))


    def test_timezeros_num_forecasts(self):
        self.assertEqual([(self.time_zero, 1)], ProjectDetailView.timezeros_num_forecasts(self.project))


# converts innermost dicts to defaultdicts, which are what location_target_name_tz_date_to_truth() returns
def _conv_loc_target_tz_date_to_truth_to_default_dict(loc_target_tz_date_to_truth):
    for location, target_tz_dict in loc_target_tz_date_to_truth.items():
        for target_name, tz_date_truth in target_tz_dict.items():
            loc_target_tz_date_to_truth[location][target_name] = defaultdict(list, tz_date_truth)


def _exp_loc_tz_date_to_actual_vals_season_1a():
    return {
        'HHS Region 1': {'1 wk ahead': {datetime.date(2017, 7, 23): [0.303222],
                                        datetime.date(2017, 7, 30): [0.286054]},
                         '2 wk ahead': {datetime.date(2017, 7, 23): [0.286054],
                                        datetime.date(2017, 7, 30): [0.341359]},
                         '3 wk ahead': {datetime.date(2017, 7, 23): [0.341359],
                                        datetime.date(2017, 7, 30): [0.325429]},
                         '4 wk ahead': {datetime.date(2017, 7, 23): [0.325429],
                                        datetime.date(2017, 7, 30): [0.339203]},
                         'Season onset': {datetime.date(2017, 7, 23): [20171119.0],
                                          datetime.date(2017, 7, 30): [20171119.0]},
                         'Season peak percentage': {datetime.date(2017, 7, 23): [None],
                                                    datetime.date(2017, 7, 30): [None]},
                         'Season peak week': {datetime.date(2017, 7, 23): [None],
                                              datetime.date(2017, 7, 30): [None]}}
        ,
        'HHS Region 10': {'1 wk ahead': {datetime.date(2017, 7, 23): [0.364459],
                                         datetime.date(2017, 7, 30): [0.240377]},
                          '2 wk ahead': {datetime.date(2017, 7, 23): [0.240377],
                                         datetime.date(2017, 7, 30): [0.126923]},
                          '3 wk ahead': {datetime.date(2017, 7, 23): [0.126923],
                                         datetime.date(2017, 7, 30): [0.241729]},
                          '4 wk ahead': {datetime.date(2017, 7, 23): [0.241729],
                                         datetime.date(2017, 7, 30): [0.293072]},
                          'Season onset': {datetime.date(2017, 7, 23): [20171217.0],
                                           datetime.date(2017, 7, 30): [20171217.0]},
                          'Season peak percentage': {datetime.date(2017, 7, 23): [None],
                                                     datetime.date(2017, 7, 30): [None]},
                          'Season peak week': {datetime.date(2017, 7, 23): [None],
                                               datetime.date(2017, 7, 30): [None]}}
        ,
        'HHS Region 2': {'1 wk ahead': {datetime.date(2017, 7, 23): [1.32634],
                                        datetime.date(2017, 7, 30): [1.34713]},
                         '2 wk ahead': {datetime.date(2017, 7, 23): [1.34713],
                                        datetime.date(2017, 7, 30): [1.15738]},
                         '3 wk ahead': {datetime.date(2017, 7, 23): [1.15738],
                                        datetime.date(2017, 7, 30): [1.41483]},
                         '4 wk ahead': {datetime.date(2017, 7, 23): [1.41483],
                                        datetime.date(2017, 7, 30): [1.32425]},
                         'Season onset': {datetime.date(2017, 7, 23): [20171203.0],
                                          datetime.date(2017, 7, 30): [20171203.0]},
                         'Season peak percentage': {datetime.date(2017, 7, 23): [None],
                                                    datetime.date(2017, 7, 30): [None]},
                         'Season peak week': {datetime.date(2017, 7, 23): [None],
                                              datetime.date(2017, 7, 30): [None]}}
        ,
        'HHS Region 3': {'1 wk ahead': {datetime.date(2017, 7, 23): [0.797999],
                                        datetime.date(2017, 7, 30): [0.586092]},
                         '2 wk ahead': {datetime.date(2017, 7, 23): [0.586092],
                                        datetime.date(2017, 7, 30): [0.611163]},
                         '3 wk ahead': {datetime.date(2017, 7, 23): [0.611163],
                                        datetime.date(2017, 7, 30): [0.623141]},
                         '4 wk ahead': {datetime.date(2017, 7, 23): [0.623141],
                                        datetime.date(2017, 7, 30): [0.781271]},
                         'Season onset': {datetime.date(2017, 7, 23): [20171217.0],
                                          datetime.date(2017, 7, 30): [20171217.0]},
                         'Season peak percentage': {datetime.date(2017, 7, 23): [None],
                                                    datetime.date(2017, 7, 30): [None]},
                         'Season peak week': {datetime.date(2017, 7, 23): [None],
                                              datetime.date(2017, 7, 30): [None]}}
        ,
        'HHS Region 4': {'1 wk ahead': {datetime.date(2017, 7, 23): [0.476357],
                                        datetime.date(2017, 7, 30): [0.483647]},
                         '2 wk ahead': {datetime.date(2017, 7, 23): [0.483647],
                                        datetime.date(2017, 7, 30): [0.674289]},
                         '3 wk ahead': {datetime.date(2017, 7, 23): [0.674289],
                                        datetime.date(2017, 7, 30): [0.782429]},
                         '4 wk ahead': {datetime.date(2017, 7, 23): [0.782429],
                                        datetime.date(2017, 7, 30): [1.11294]},
                         'Season onset': {datetime.date(2017, 7, 23): [20171105.0],
                                          datetime.date(2017, 7, 30): [20171105.0]},
                         'Season peak percentage': {datetime.date(2017, 7, 23): [None],
                                                    datetime.date(2017, 7, 30): [None]},
                         'Season peak week': {datetime.date(2017, 7, 23): [None],
                                              datetime.date(2017, 7, 30): [None]}}
        ,
        'HHS Region 5': {'1 wk ahead': {datetime.date(2017, 7, 23): [0.602327],
                                        datetime.date(2017, 7, 30): [0.612967]},
                         '2 wk ahead': {datetime.date(2017, 7, 23): [0.612967],
                                        datetime.date(2017, 7, 30): [0.637141]},
                         '3 wk ahead': {datetime.date(2017, 7, 23): [0.637141],
                                        datetime.date(2017, 7, 30): [0.627954]},
                         '4 wk ahead': {datetime.date(2017, 7, 23): [0.627954],
                                        datetime.date(2017, 7, 30): [0.724628]},
                         'Season onset': {datetime.date(2017, 7, 23): [20171203.0],
                                          datetime.date(2017, 7, 30): [20171203.0]},
                         'Season peak percentage': {datetime.date(2017, 7, 23): [None],
                                                    datetime.date(2017, 7, 30): [None]},
                         'Season peak week': {datetime.date(2017, 7, 23): [None],
                                              datetime.date(2017, 7, 30): [None]}}
        ,
        'HHS Region 6': {'1 wk ahead': {datetime.date(2017, 7, 23): [1.15229],
                                        datetime.date(2017, 7, 30): [0.96867]},
                         '2 wk ahead': {datetime.date(2017, 7, 23): [0.96867],
                                        datetime.date(2017, 7, 30): [1.02289]},
                         '3 wk ahead': {datetime.date(2017, 7, 23): [1.02289],
                                        datetime.date(2017, 7, 30): [1.66769]},
                         '4 wk ahead': {datetime.date(2017, 7, 23): [1.66769],
                                        datetime.date(2017, 7, 30): [1.74834]},
                         'Season onset': {datetime.date(2017, 7, 23): [20171126.0],
                                          datetime.date(2017, 7, 30): [20171126.0]},
                         'Season peak percentage': {datetime.date(2017, 7, 23): [None],
                                                    datetime.date(2017, 7, 30): [None]},
                         'Season peak week': {datetime.date(2017, 7, 23): [None],
                                              datetime.date(2017, 7, 30): [None]}}
        ,
        'HHS Region 7': {'1 wk ahead': {datetime.date(2017, 7, 23): [0.174172],
                                        datetime.date(2017, 7, 30): [0.115888]},
                         '2 wk ahead': {datetime.date(2017, 7, 23): [0.115888],
                                        datetime.date(2017, 7, 30): [0.112074]},
                         '3 wk ahead': {datetime.date(2017, 7, 23): [0.112074],
                                        datetime.date(2017, 7, 30): [0.233776]},
                         '4 wk ahead': {datetime.date(2017, 7, 23): [0.233776],
                                        datetime.date(2017, 7, 30): [0.142496]},
                         'Season onset': {datetime.date(2017, 7, 23): [20171203.0],
                                          datetime.date(2017, 7, 30): [20171203.0]},
                         'Season peak percentage': {datetime.date(2017, 7, 23): [None],
                                                    datetime.date(2017, 7, 30): [None]},
                         'Season peak week': {datetime.date(2017, 7, 23): [None],
                                              datetime.date(2017, 7, 30): [None]}}
        ,
        'HHS Region 8': {'1 wk ahead': {datetime.date(2017, 7, 23): [0.33984],
                                        datetime.date(2017, 7, 30): [0.359646]},
                         '2 wk ahead': {datetime.date(2017, 7, 23): [0.359646],
                                        datetime.date(2017, 7, 30): [0.326402]},
                         '3 wk ahead': {datetime.date(2017, 7, 23): [0.326402],
                                        datetime.date(2017, 7, 30): [0.419146]},
                         '4 wk ahead': {datetime.date(2017, 7, 23): [0.419146],
                                        datetime.date(2017, 7, 30): [0.714684]},
                         'Season onset': {datetime.date(2017, 7, 23): [20171210.0],
                                          datetime.date(2017, 7, 30): [20171210.0]},
                         'Season peak percentage': {datetime.date(2017, 7, 23): [None],
                                                    datetime.date(2017, 7, 30): [None]},
                         'Season peak week': {datetime.date(2017, 7, 23): [None],
                                              datetime.date(2017, 7, 30): [None]}}
        ,
        'HHS Region 9': {'1 wk ahead': {datetime.date(2017, 7, 23): [0.892872],
                                        datetime.date(2017, 7, 30): [0.912778]},
                         '2 wk ahead': {datetime.date(2017, 7, 23): [0.912778],
                                        datetime.date(2017, 7, 30): [1.012]},
                         '3 wk ahead': {datetime.date(2017, 7, 23): [1.012],
                                        datetime.date(2017, 7, 30): [1.26206]},
                         '4 wk ahead': {datetime.date(2017, 7, 23): [1.26206],
                                        datetime.date(2017, 7, 30): [1.28077]},
                         'Season onset': {datetime.date(2017, 7, 23): [20171203.0],
                                          datetime.date(2017, 7, 30): [20171203.0]},
                         'Season peak percentage': {datetime.date(2017, 7, 23): [None],
                                                    datetime.date(2017, 7, 30): [None]},
                         'Season peak week': {datetime.date(2017, 7, 23): [None],
                                              datetime.date(2017, 7, 30): [None]}}
        ,
        'US National': {'1 wk ahead': {datetime.date(2017, 7, 23): [0.73102],
                                       datetime.date(2017, 7, 30): [0.688338]},
                        '2 wk ahead': {datetime.date(2017, 7, 23): [0.688338],
                                       datetime.date(2017, 7, 30): [0.732049]},
                        '3 wk ahead': {datetime.date(2017, 7, 23): [0.732049],
                                       datetime.date(2017, 7, 30): [0.911641]},
                        '4 wk ahead': {datetime.date(2017, 7, 23): [0.911641],
                                       datetime.date(2017, 7, 30): [1.02105]},
                        'Season onset': {datetime.date(2017, 7, 23): [20171119.0],
                                         datetime.date(2017, 7, 30): [20171119.0]},
                        'Season peak percentage': {datetime.date(2017, 7, 23): [None],
                                                   datetime.date(2017, 7, 30): [None]},
                        'Season peak week': {datetime.date(2017, 7, 23): [None],
                                             datetime.date(2017, 7, 30): [None]}}
    }


def _exp_loc_tz_date_to_actual_vals_season_1b():
    return {
        'HHS Region 1': {
            datetime.date(2017, 7, 23): None,
            datetime.date(2017, 7, 30): [0.303222],
        },
        'HHS Region 10': {
            datetime.date(2017, 7, 23): None,
            datetime.date(2017, 7, 30): [0.364459],
        },
        'HHS Region 2': {
            datetime.date(2017, 7, 23): None,
            datetime.date(2017, 7, 30): [1.32634],
        },
        'HHS Region 3': {
            datetime.date(2017, 7, 23): None,
            datetime.date(2017, 7, 30): [0.797999],
        },
        'HHS Region 4': {
            datetime.date(2017, 7, 23): None,
            datetime.date(2017, 7, 30): [0.476357],
        },
        'HHS Region 5': {
            datetime.date(2017, 7, 23): None,
            datetime.date(2017, 7, 30): [0.602327],
        },
        'HHS Region 6': {
            datetime.date(2017, 7, 23): None,
            datetime.date(2017, 7, 30): [1.15229],
        },
        'HHS Region 7': {
            datetime.date(2017, 7, 23): None,
            datetime.date(2017, 7, 30): [0.174172],
        },
        'HHS Region 8': {
            datetime.date(2017, 7, 23): None,
            datetime.date(2017, 7, 30): [0.33984],
        },
        'HHS Region 9': {
            datetime.date(2017, 7, 23): None,
            datetime.date(2017, 7, 30): [0.892872],
        },
        'US National': {
            datetime.date(2017, 7, 23): None,
            datetime.date(2017, 7, 30): [0.73102],
        },
    }


def _exp_loc_tz_date_to_actual_vals_season_2b():
    return {
        'HHS Region 1': {
            datetime.date(2017, 8, 6): [0.286054],
            datetime.date(2017, 8, 13): [0.341359],
        },
        'HHS Region 10': {
            datetime.date(2017, 8, 6): [0.240377],
            datetime.date(2017, 8, 13): [0.126923],
        },
        'HHS Region 2': {
            datetime.date(2017, 8, 6): [1.34713],
            datetime.date(2017, 8, 13): [1.15738],
        },
        'HHS Region 3': {
            datetime.date(2017, 8, 6): [0.586092],
            datetime.date(2017, 8, 13): [0.611163],
        },
        'HHS Region 4': {
            datetime.date(2017, 8, 6): [0.483647],
            datetime.date(2017, 8, 13): [0.674289],
        },
        'HHS Region 5': {
            datetime.date(2017, 8, 6): [0.612967],
            datetime.date(2017, 8, 13): [0.637141],
        },
        'HHS Region 6': {
            datetime.date(2017, 8, 6): [0.96867],
            datetime.date(2017, 8, 13): [1.02289],
        },
        'HHS Region 7': {
            datetime.date(2017, 8, 6): [0.115888],
            datetime.date(2017, 8, 13): [0.112074],
        },
        'HHS Region 8': {
            datetime.date(2017, 8, 6): [0.359646],
            datetime.date(2017, 8, 13): [0.326402],
        },
        'HHS Region 9': {
            datetime.date(2017, 8, 6): [0.912778],
            datetime.date(2017, 8, 13): [1.012],
        },
        'US National': {
            datetime.date(2017, 8, 6): [0.688338],
            datetime.date(2017, 8, 13): [0.732049],
        },
    }
