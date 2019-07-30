import datetime
import shutil
import tempfile
import unittest
from pathlib import Path

from django.test import TestCase

from forecast_app.models import Project, TimeZero
from forecast_app.models.forecast import Forecast
from forecast_app.models.forecast_model import ForecastModel
from utils.cdc import load_cdc_csv_forecast_file, CDC_CSV_HEADER, load_cdc_csv_forecasts_from_dir
from utils.make_cdc_flu_contests_project import make_cdc_locations_and_targets, CDC_CONFIG_DICT


class ForecastTestCase(TestCase):
    """
    """


    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(config_dict=CDC_CONFIG_DICT)
        make_cdc_locations_and_targets(cls.project)

        cls.forecast_model = ForecastModel.objects.create(project=cls.project)
        cls.time_zero = TimeZero.objects.create(project=cls.project, timezero_date=datetime.date(2017, 1, 1))
        cls.forecast = load_cdc_csv_forecast_file(cls.forecast_model, Path(
            'forecast_app/tests/model_error/ensemble/EW1-KoTstable-2017-01-17.csv'), cls.time_zero)


    def test_load_forecast_created_at_field(self):
        project2 = Project.objects.create(config_dict=CDC_CONFIG_DICT)
        make_cdc_locations_and_targets(project2)
        time_zero = TimeZero.objects.create(project=project2, timezero_date=datetime.date.today())
        forecast_model2 = ForecastModel.objects.create(project=project2)
        forecast2 = load_cdc_csv_forecast_file(forecast_model2,
                                               Path('forecast_app/tests/EW1-KoTsarima-2017-01-17-small.csv'),
                                               time_zero)
        self.assertIsNotNone(forecast2.created_at)


    def test_load_forecast(self):
        self.assertEqual(1, len(self.forecast_model.forecasts.all()))

        self.assertIsInstance(self.forecast, Forecast)
        self.assertEqual('EW1-KoTstable-2017-01-17.csv', self.forecast.csv_filename)
        self.assertEqual(8019, self.forecast.get_num_rows())  # excluding header

        # spot-check a few point rows
        exp_points = [('US National', 'Season onset', None, None, '50.0012056690978'),  # value_t
                      ('US National', 'Season peak week', None, None, '4.96302456525203'),
                      ('US National', 'Season peak percentage', None, 3.30854920241938, None),  # value_f
                      ('US National', '1 wk ahead', None, 3.00101461253164, None),
                      ('US National', '2 wk ahead', None, 2.72809349594878, None),
                      ('US National', '3 wk ahead', None, 2.5332588357381, None),
                      ('US National', '4 wk ahead', None, 2.42985946508278, None)]
        act_points_qs = self.forecast.point_prediction_qs() \
            .filter(location__name='US National') \
            .order_by('location__id', 'target__id') \
            .values_list('location__name', 'target__name', 'value_i', 'value_f', 'value_t')
        self.assertEqual(exp_points, list(act_points_qs))

        # test empty file
        with self.assertRaises(RuntimeError) as context:
            load_cdc_csv_forecast_file(self.forecast_model,
                                       Path('forecast_app/tests/EW1-bad_file_no_header-2017-01-17.csv'),
                                       self.time_zero)
        self.assertIn('empty file', str(context.exception))

        # test a bad data file header
        with self.assertRaises(RuntimeError) as context:
            load_cdc_csv_forecast_file(self.forecast_model,
                                       Path('forecast_app/tests/EW1-bad_file_header-2017-01-17.csv'),
                                       self.time_zero)
        self.assertIn('invalid header', str(context.exception))

        # test load_forecast() with timezero not in the project
        project2 = Project.objects.create(config_dict=CDC_CONFIG_DICT)  # no TimeZeros
        make_cdc_locations_and_targets(project2)

        forecast_model2 = ForecastModel.objects.create(project=project2)
        with self.assertRaises(RuntimeError) as context:
            load_cdc_csv_forecast_file(  # TimeZero doesn't matter b/c project has none
                forecast_model2,
                Path('forecast_app/tests/model_error/ensemble/EW1-KoTstable-2017-01-17.csv'), self.time_zero)
        self.assertIn("time_zero was not in project", str(context.exception))


    def test_load_forecasts_from_dir(self):
        project2 = Project.objects.create(config_dict=CDC_CONFIG_DICT)
        make_cdc_locations_and_targets(project2)
        TimeZero.objects.create(project=project2,
                                timezero_date=datetime.date(2016, 10, 23),  # 20161023-KoTstable-20161109.cdc.csv
                                data_version_date=None)
        TimeZero.objects.create(project=project2,
                                timezero_date=datetime.date(2016, 10, 30),  # 20161030-KoTstable-20161114.cdc.csv
                                data_version_date=None)
        TimeZero.objects.create(project=project2,
                                timezero_date=datetime.date(2016, 11, 6),  # 20161106-KoTstable-20161121.cdc.csv
                                data_version_date=None)
        forecast_model2 = ForecastModel.objects.create(project=project2)

        # copy the two files from 'forecast_app/tests/load_forecasts' to a temp dir, run the loader, and then copy a
        # third file over to test that it skips already-loaded ones
        with tempfile.TemporaryDirectory() as temp_dir_name:
            temp_dir = Path(temp_dir_name)
            test_file_dir = Path('forecast_app/tests/load_forecasts')
            shutil.copy(str(test_file_dir / '20161023-KoTstable-20161109.cdc.csv'), str(temp_dir))
            shutil.copy(str(test_file_dir / '20161030-KoTstable-20161114.cdc.csv'), str(temp_dir))

            forecasts = load_cdc_csv_forecasts_from_dir(forecast_model2, temp_dir)
            self.assertEqual(2, len(forecasts))
            self.assertEqual(2, len(forecast_model2.forecasts.all()))

            # copy third file and test only new loaded
            shutil.copy(str(test_file_dir / 'third-file/20161106-KoTstable-20161121.cdc.csv'), str(temp_dir))
            forecasts = load_cdc_csv_forecasts_from_dir(forecast_model2, temp_dir)
            self.assertEqual(1, len(forecasts))


    def test_forecast_data_validation(self):
        with self.assertRaises(RuntimeError) as context:
            load_cdc_csv_forecast_file(self.forecast_model,
                                       Path('forecast_app/tests/EW1-bin-doesnt-sum-to-one-2017-01-17.csv'),
                                       self.time_zero)
        self.assertIn("Bin did not sum to 1.0", str(context.exception))

        with self.assertRaises(RuntimeError) as context:
            load_cdc_csv_forecast_file(self.forecast_model,
                                       Path('forecast_app/tests/EW1-bad-point-na-2017-01-17.csv'),
                                       self.time_zero)
        self.assertIn("Point value was non-numeric", str(context.exception))

        # via https://stackoverflow.com/questions/647900/python-test-that-succeeds-when-exception-is-not-raised/4711722#4711722
        with self.assertRaises(Exception):
            try:
                load_cdc_csv_forecast_file(self.forecast_model,
                                           Path('forecast_app/tests/EW1-ok-point-na-2017-01-17.csv'),
                                           self.time_zero)  # date-based Point row w/NA value is OK
            except:
                pass
            else:
                raise Exception

        # todo xx test other validations ala old Forecast.validate_forecast_data():
        #   v raise RuntimeError("Bin did not sum to 1.0.
        #   v raise RuntimeError("Point value was non-numeric
        #   - raise RuntimeError("Locations did not match template
        #   - raise RuntimeError("Targets did not match template
        #   ? raise RuntimeError("Bins did not match template
        self.fail()  # todo xx


    @unittest.skip
    def test_forecast_data_validation_additional(self):
        # test points lie within the range of point values in the template. see Nick's comment
        # ( https://github.com/reichlab/forecast-repository/issues/18#issuecomment-335654340 ):
        # The thought was that for each target we could look at all of the values in the point rows for that target and
        # would end up with a vector of numbers. The minimum and maximum of those numbers would define the acceptable
        # range for the point values in the files themselves. E.g., if the predictions for target K should never be
        # negative, then whoever made the template file would explicitly place a zero in at least one of the target K
        # point rows. And none of those rows would have negative values. Is this too "cute" of a way to set the max/min
        # ranges for testing? Alternatively, we could hard-code them as part of the project.
        self.fail()  # todo

        # see @josh's comment ( https://reichlab.slack.com/archives/C57HNDFN0/p1507744847000350 ):
        # how important is the left handedness of the bins? by that i mean that bin_start_incl and bin_end_notincl
        # versus bin_start_notincl and bin_end_incl
        self.fail()  # todo


    def test_forecast_data_and_accessors(self):
        # test points
        point_prediction_qs = self.forecast.point_prediction_qs() \
            .order_by('location__id', 'target__id') \
            .values_list('location__name', 'target__name', 'value_i', 'value_f', 'value_t')
        self.assertEqual(77, point_prediction_qs.count())
        exp_points = [('US National', 'Season onset', None, None, '50.0012056690978'),  # value_t
                      ('US National', 'Season peak week', None, None, '4.96302456525203'),
                      ('US National', 'Season peak percentage', None, 3.30854920241938, None),  # value_f
                      ('US National', '1 wk ahead', None, 3.00101461253164, None),
                      ('US National', '2 wk ahead', None, 2.72809349594878, None),
                      ('US National', '3 wk ahead', None, 2.5332588357381, None),
                      ('US National', '4 wk ahead', None, 2.42985946508278, None)]
        self.assertEqual(exp_points, list(point_prediction_qs.filter(location__name='US National')))

        # test binlwr
        binlwr_distribution_qs = self.forecast.binlwr_distribution_qs() \
            .order_by('location__id', 'target__id', 'lwr') \
            .values_list('location__name', 'target__name', 'lwr', 'prob')
        self.assertEqual(7205, binlwr_distribution_qs.count())
        self.assertEqual(('HHS Region 1', 'Season peak percentage', 0.0, 2.77466929119912e-05),
                         binlwr_distribution_qs[0])

        #  test bincat
        bincat_distribution_qs = self.forecast.bincat_distribution_qs() \
            .order_by('location__id', 'target__id', 'cat') \
            .values_list('location__name', 'target__name', 'cat', 'prob')
        self.assertEqual(737, bincat_distribution_qs.count())
        self.assertEqual(('HHS Region 1', 'Season onset', '1', 2.37797107673309e-05), bincat_distribution_qs[0])
        self.assertEqual(('HHS Region 1', 'Season onset', 'None', 0.0227300694570138), bincat_distribution_qs[33])


    def test_forecast_delete(self):
        # add a second forecast, check its associated ForecastData rows were added, delete it, and test that the data was
        # deleted (via CASCADE)
        self.assertEqual(1, self.forecast_model.forecasts.count())  # from setUpTestData()
        self.assertEqual(8019, self.forecast.get_num_rows())  # ""

        forecast2 = load_cdc_csv_forecast_file(self.forecast_model,
                                               Path('forecast_app/tests/EW1-KoTsarima-2017-01-17.csv'),
                                               self.time_zero)
        self.assertEqual(2, self.forecast_model.forecasts.count())  # includes new
        self.assertEqual(5237, forecast2.get_num_rows())  # 8019 rows - 2782 bin=0 rows 5237
        self.assertEqual(8019, self.forecast.get_num_rows())  # didn't change

        forecast2.delete()
        self.assertEqual(0, forecast2.get_num_rows())


    def test_forecast_for_time_zero(self):
        time_zero = TimeZero.objects.create(project=self.project,
                                            timezero_date=datetime.date.today(),
                                            data_version_date=None)
        self.assertEqual(None, self.forecast_model.forecast_for_time_zero(time_zero))

        forecast2 = load_cdc_csv_forecast_file(self.forecast_model,
                                               Path('forecast_app/tests/EW1-KoTsarima-2017-01-17.csv'), time_zero)
        self.assertEqual(forecast2, self.forecast_model.forecast_for_time_zero(time_zero))

        forecast2.delete()


    def test_download_forecast_data(self):
        response = csv_response_for_model_with_cdc_data(self.forecast)
        split_content = response.content.decode("utf-8").split('\r\n')
        self.assertEqual(len(split_content), 8021)  # 8019 data rows + 1 header + 1 EOF
        self.assertEqual(split_content[0], ','.join(CDC_CSV_HEADER))

        # relies on order, which is OK - see get_data_rows(): `order_by('id')`:
        self.assertEqual(split_content[1], 'US National,Season onset,point,week,,,50.0012056690978')
        self.assertEqual(split_content[2], 'US National,Season onset,bin,week,40.0,41.0,1.95984004521967e-05')
