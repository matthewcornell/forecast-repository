import csv
import datetime
import io
import json
import logging
from collections import defaultdict

from django.db import connection
from django.db import transaction

from forecast_app.models import Project, Location, Target
from forecast_app.models.project import POSTGRES_NULL_VALUE, TRUTH_CSV_HEADER
from utils.utilities import YYYY_MM_DD_DATE_FORMAT
from utils.utilities import parse_value


logger = logging.getLogger(__name__)


#
# delete_project_iteratively()
#

@transaction.atomic
def delete_project_iteratively(project):
    """
    An alternative to Project.delete(), deletes the passed Project, but unlike that function, does so by iterating over
    objects that refer to the project before deleting the project itproject. This apparently reduces the memory usage
    enough to allow the below Heroku deletion. See [Deleting projects on Heroku production fails](https://github.com/reichlab/forecast-repository/issues/91).
    """
    logger.info(f"* delete_project_iteratively(): deleting models and forecasts")
    for forecast_model in project.models.iterator():
        logger.info(f"- {forecast_model.pk}")
        for forecast in forecast_model.forecasts.iterator():
            logger.info(f"  = {forecast.pk}")
            forecast.delete()
        forecast_model.delete()

    logger.info(f"delete_project_iteratively(): deleting locations")
    for location in project.locations.iterator():
        logger.info(f"- {location.pk}")
        location.delete()

    logger.info(f"delete_project_iteratively(): deleting targets")
    for target in project.targets.iterator():
        logger.info(f"- {target.pk}")
        target.delete()

    logger.info(f"delete_project_iteratively(): deleting timezeros")
    for timezero in project.timezeros.iterator():
        logger.info(f"- {timezero.pk}")
        timezero.delete()

    logger.info(f"delete_project_iteratively(): deleting remainder")
    project.delete()  # deletes remaining references: RowCountCache, ScoreCsvFileCache
    logger.info(f"delete_project_iteratively(): done")


#
# config_dict_from_project()
#

# todo xx integrate with API serialization!
def config_dict_from_project(project):
    """
    The twin of `create_project_from_json()`, returns a configuration dict for project as passed to that function.
    """
    return {'name': project.name, 'is_public': project.is_public, 'description': project.description,
            'home_url': project.home_url, 'logo_url': project.logo_url, 'core_data': project.core_data,
            'time_interval_type': project.time_interval_type_as_str(),
            'visualization_y_label': project.visualization_y_label,
            'locations': [{'name': location.name} for location in project.locations.all()],
            'targets': [_target_dict_for_target(target) for target in project.targets.all()],
            'timezeros': [{'timezero_date': timezero.timezero_date.strftime(YYYY_MM_DD_DATE_FORMAT),
                           'data_version_date':
                               timezero.data_version_date.strftime(YYYY_MM_DD_DATE_FORMAT)
                               if timezero.data_version_date else None,
                           'is_season_start': timezero.is_season_start,
                           'season_name': timezero.season_name}
                          for timezero in project.timezeros.all()]}


# todo xx integrate with API serialization!
def _target_dict_for_target(target):
    if target.type is None:
        raise RuntimeError(f"target has no type: {target}")

    data_type = Target.data_type(target.type)
    type_int_to_name = {type_int: type_name for type_int, type_name in Target.TARGET_TYPE_CHOICES}

    # start with required fields
    target_dict = {'type': type_int_to_name[target.type],
                   'name': target.name,
                   'description': target.description,
                   'is_step_ahead': target.is_step_ahead}  # required keys

    # add optional fields, including 'list' ones. rather than basing whether they are available on target type (for
    # example, 'continuous' targets /might/ have a range), we check for whether the optional field (including 'list'
    # ones) is present. the exception is step_ahead_increment, for which we check is_step_ahead

    # add is_step_ahead
    if target.is_step_ahead and (target.step_ahead_increment is not None):
        target_dict['step_ahead_increment'] = target.step_ahead_increment

    # add unit
    if target.unit is not None:
        target_dict['unit'] = target.unit

    # add range
    target_ranges_qs = target.ranges  # target.value_i, target.value_f
    if target_ranges_qs.count() != 0:  # s/b exactly 2
        target_ranges = target_ranges_qs.values_list('value_i', flat=True) \
            if data_type == Target.INTEGER_DATA_TYPE \
            else target_ranges_qs.values_list('value_f', flat=True)
        target_ranges = sorted(target_ranges)
        target_dict['range'] = [target_ranges[0], target_ranges[1]]

    # add cats
    if target.cats.count() != 0:
        if data_type == Target.INTEGER_DATA_TYPE:
            target_cats = target.cats.values_list('cat_i', flat=True)
        elif data_type == Target.FLOAT_DATA_TYPE:
            target_cats = target.cats.values_list('cat_f', flat=True)
        elif data_type == Target.TEXT_DATA_TYPE:
            target_cats = target.cats.values_list('cat_t', flat=True)
        elif data_type == Target.DATE_DATA_TYPE:
            target_cats = [cat_date.strftime(YYYY_MM_DD_DATE_FORMAT)
                           for cat_date in target.cats.values_list('cat_d', flat=True)]
        else:
            raise RuntimeError(f"invalid data_type={data_type} ({type_int_to_name[target.type]})")

        target_dict['cats'] = sorted(target_cats)
    elif target.type in [Target.NOMINAL_TARGET_TYPE, Target.DATE_TARGET_TYPE]:
        # handle the case of required cats list that must have come in but was empty
        target_dict['cats'] = []
    return target_dict


#
# create_project_from_json()
#

@transaction.atomic
def create_project_from_json(proj_config_file_path_or_dict, owner):
    """
    Top-level function that creates a Project based on the json configuration file at json_file_path. Errors if one with
    that name already exists. Does not set Project.model_owners, create TimeZeros, load truth data, create Models, or
    load forecasts.

    :param proj_config_file_path_or_dict: either a Path to project config json file OR a dict as loaded from a file.
        See https://docs.zoltardata.com/fileformats/#project-creation-configuration-json for details, and
        cdc-project.json for an example.
    :param owner: the new Project's owner (a User)
    :param is_validate: True if the input json should be validated. passed in case a project requires less stringent
        validation
    :return: the new Project
    """
    logger.info(f"* create_project_from_json(): started. proj_config_file_path_or_dict="
                f"{proj_config_file_path_or_dict}, owner={owner}")
    if isinstance(proj_config_file_path_or_dict, dict):
        project_dict = proj_config_file_path_or_dict
    else:
        with open(proj_config_file_path_or_dict) as fp:
            project_dict = json.load(fp)

    # validate project_dict
    actual_keys = set(project_dict.keys())
    expected_keys = {'name', 'is_public', 'description', 'home_url', 'logo_url', 'core_data', 'time_interval_type',
                     'visualization_y_label', 'locations', 'targets', 'timezeros'}
    if actual_keys != expected_keys:
        raise RuntimeError(f"Wrong keys in project_dict. difference={expected_keys ^ actual_keys}. "
                           f"expected={expected_keys}, actual={actual_keys}")

    # error if project already exists
    name = project_dict['name']
    project = Project.objects.filter(name=name).first()  # None if doesn't exist
    if project:
        raise RuntimeError(f"found existing project. name={name}, project={project}")

    project = create_project(project_dict, owner)
    logger.info(f"- created Project: {project}")

    locations = validate_and_create_locations(project, project_dict)
    logger.info(f"- created {len(locations)} Locations: {locations}")

    targets = validate_and_create_targets(project, project_dict)
    logger.info(f"- created {len(targets)} Targets: {targets}")

    timezeros = validate_and_create_timezeros(project, project_dict)
    logger.info(f"- created {len(timezeros)} TimeZeros: {timezeros}")

    logger.info(f"* create_project_from_json(): done!")
    return project


def validate_and_create_locations(project, project_dict):
    try:
        return [Location.objects.create(project=project, name=location_dict['name'])
                for location_dict in project_dict['locations']]
    except KeyError:
        raise RuntimeError(f"one of the location_dicts had no 'name' field. locations={project_dict['locations']}")


def validate_and_create_timezeros(project, project_dict):
    from forecast_app.api_views import validate_and_create_timezero  # avoid circular imports


    return [validate_and_create_timezero(project, timezero_config) for timezero_config in project_dict['timezeros']]


# todo xx integrate with API serialization!
def validate_and_create_targets(project, project_dict):
    targets = []
    type_name_to_type_int = {type_name: type_int for type_int, type_name in Target.TARGET_TYPE_CHOICES}
    for target_dict in project_dict['targets']:
        type_name = _validate_target_dict(target_dict, type_name_to_type_int)  # raises RuntimeError if invalid

        # valid! create the Target and then supporting 'list' instances: TargetCat, TargetLwr, TargetDate,
        # and TargetRange. atomic so that Targets succeed only if others do too
        with transaction.atomic():
            model_init = {'project': project,
                          'type': type_name_to_type_int[type_name],
                          'name': target_dict['name'],
                          'description': target_dict['description'],
                          'is_step_ahead': target_dict['is_step_ahead']}  # required keys

            # add is_step_ahead
            if target_dict['is_step_ahead']:
                model_init['step_ahead_increment'] = target_dict['step_ahead_increment']

            # add unit
            if 'unit' in target_dict:
                model_init['unit'] = target_dict['unit']

            # instantiate the new Target
            target = Target.objects.create(**model_init)
            targets.append(target)

            # add range
            if ('range' in target_dict) and target_dict['range']:  # create two TargetRanges
                target.set_range(target_dict['range'][0], target_dict['range'][1])

            # add cats
            if ('cats' in target_dict) and target_dict['cats']:  # create TargetCats and TargetLwrs
                target.set_cats(target_dict['cats'])
    return targets


def _validate_target_dict(target_dict, type_name_to_type_int):
    # check for keys required by all target types. optional keys are tested below
    all_keys = set(target_dict.keys())
    tested_keys = all_keys - {'unit', 'step_ahead_increment', 'range', 'cats'}  # optional keys
    expected_keys = {'name', 'description', 'type', 'is_step_ahead'}
    if tested_keys != expected_keys:
        raise RuntimeError(f"Wrong required keys in target_dict. difference={expected_keys ^ tested_keys}. "
                           f"expected_keys={expected_keys}, tested_keys={tested_keys}. target_dict={target_dict}")
    # validate type
    type_name = target_dict['type']
    valid_target_types = [type_name for type_int, type_name in Target.TARGET_TYPE_CHOICES]
    if type_name not in valid_target_types:
        raise RuntimeError(f"Invalid type_name={type_name}. valid_target_types={valid_target_types} . "
                           f"target_dict={target_dict}")

    # validate is_step_ahead. field default if not passed is None
    if target_dict['is_step_ahead'] is None:
        raise RuntimeError(f"is_step_ahead not found but is required")

    # check for step_ahead_increment required if is_step_ahead
    if target_dict['is_step_ahead'] and ('step_ahead_increment' not in target_dict):
        raise RuntimeError(f"step_ahead_increment not found but is required when is_step_ahead is passed. "
                           f"target_dict={target_dict}")

    # check required, optional, and invalid keys by target type. 3 cases: 'unit', 'range', 'cats'
    type_int = type_name_to_type_int[type_name]

    # 1) test optional 'unit'. three cases a-c follow

    # 1a) required but not passed: ['continuous', 'discrete', 'date']
    if ('unit' not in all_keys) and \
            (type_int in [Target.CONTINUOUS_TARGET_TYPE, Target.DISCRETE_TARGET_TYPE, Target.DATE_TARGET_TYPE]):
        raise RuntimeError(f"'unit' not passed but is required for type_name={type_name}")

    # 1b) optional: ok to pass or not pass: []: no need to validate

    # 1c) invalid but passed: ['nominal', 'binary']
    if ('unit' in all_keys) and \
            (type_int in [Target.NOMINAL_TARGET_TYPE, Target.BINARY_TARGET_TYPE]):
        raise RuntimeError(f"'unit' passed but is invalid for type_name={type_name}")

    # test that unit, if passed to a Target.DATE_TARGET_TYPE, is valid
    if ('unit' in all_keys) and (type_int == Target.DATE_TARGET_TYPE) and \
            (target_dict['unit'] not in Target.DATE_UNITS):
        raise RuntimeError(f"'unit' passed for date target but was not valid. unit={target_dict['unit']!r}, "
                           f"valid_date_units={Target.DATE_UNITS!r}")

    # 2) test optional 'range'. three cases a-c follow

    # 2a) required but not passed: []: no need to validate

    # 2b) optional: ok to pass or not pass: ['continuous', 'discrete']: no need to validate

    # 2c) invalid but passed: ['nominal', 'binary', 'date']
    if ('range' in all_keys) and (
            type_int in [Target.NOMINAL_TARGET_TYPE, Target.BINARY_TARGET_TYPE, Target.DATE_TARGET_TYPE]):
        raise RuntimeError(f"'range' passed but is invalid for type_name={type_name}")

    # 3) test optional 'cats'. three cases a-c follow

    # 3a) required but not passed: ['nominal', 'date']
    if ('cats' not in all_keys) and \
            (type_int in [Target.NOMINAL_TARGET_TYPE, Target.DATE_TARGET_TYPE]):
        raise RuntimeError(f"'cats' not passed but is required for type_name={type_name}")

    # 3b) optional: ok to pass or not pass: ['continuous', 'discrete']: no need to validate

    # 3c) invalid but passed: ['binary']
    if ('cats' in all_keys) and (type_int == Target.BINARY_TARGET_TYPE):
        raise RuntimeError(f"'cats' passed but is invalid for type_name={type_name}")

    # validate 'range' if passed. values can be either ints or floats, and must match the target's data type
    data_type = Target.data_type(type_int)  # python type
    if 'range' in target_dict:
        for range_str in target_dict['range']:
            try:
                data_type(range_str)  # try parsing as an int or float
            except ValueError as ve:
                raise RuntimeError(f"range type did not match data_type. range_str={range_str!r}, "
                                   f"data_type={data_type}, error: {ve}")

        if len(target_dict['range']) != 2:
            raise RuntimeError(f"range did not contain exactly two items: {target_dict['range']}")

    # validate 'cats' if passed. values can strings, ints, or floats, and must match the target's data type. strings
    # can be either dates in YYYY_MM_DD_DATE_FORMAT form or just plain strings.
    if 'cats' in target_dict:
        for cat_str in target_dict['cats']:
            try:
                if type_int == Target.DATE_TARGET_TYPE:
                    datetime.datetime.strptime(cat_str, YYYY_MM_DD_DATE_FORMAT).date()  # try parsing as a date
                else:
                    data_type(cat_str)  # try parsing as a string, int, or float
            except ValueError as ve:
                raise RuntimeError(f"could not convert cat to data_type. cat_str={cat_str!r}, "
                                   f"data_type={data_type}, error: {ve}")
    return type_name


def create_project(project_dict, owner):
    # validate time_interval_type - one of: 'week', 'biweek', or 'month'
    time_interval_type_input = project_dict['time_interval_type'].lower()
    time_interval_type = None
    for db_value, human_readable_value in Project.TIME_INTERVAL_TYPE_CHOICES:
        if human_readable_value.lower() == time_interval_type_input:
            time_interval_type = db_value

    if time_interval_type is None:
        time_interval_type_choices = [choice[1] for choice in Project.TIME_INTERVAL_TYPE_CHOICES]
        raise RuntimeError(f"invalid 'time_interval_type': {time_interval_type_input}. must be one of: "
                           f"{time_interval_type_choices}")

    project = Project.objects.create(
        owner=owner,
        is_public=project_dict['is_public'],
        name=project_dict['name'],
        time_interval_type=time_interval_type,
        visualization_y_label=(project_dict['visualization_y_label']),
        description=project_dict['description'],
        home_url=project_dict['home_url'],  # required
        logo_url=project_dict['logo_url'] if 'logo_url' in project_dict else None,
        core_data=project_dict['core_data'] if 'core_data' in project_dict else None,
    )
    project.save()
    return project


#
# load_truth_data()
#

@transaction.atomic
def load_truth_data(project, truth_file_path_or_fp, file_name=None):
    """
    Loads the data in truth_file_path (see below for file format docs). Like load_csv_data(), uses direct SQL for
    performance, using a fast Postgres-specific routine if connected to it. Note that this method should be called
    after all TimeZeros are created b/c truth data is validated against them. Notes:

    - TimeZeros "" b/c truth timezeros are validated against project ones
    - One csv file/project, which includes timezeros across all seasons.
    - Columns: timezero, location, target, value . NB: There is no season information (see below). timezeros are
      formatted “yyyymmdd”. A header must be included.
    - Missing timezeros: If the program generating the csv file does not have information for a particular project
      timezero, then it should not generate a value for it. (The alternative would be to require the program to
      generate placeholder values for missing dates.)
    - Non-numeric values: Some targets will have no value, such as season onset when a baseline is not met. In those
      cases, the value should be “NA”, per
      https://predict.cdc.gov/api/v1/attachments/flusight/flu_challenge_2016-17_update.docx.
    - For date-based onset or peak targets, values must be dates in the same format as timezeros, rather than
        project-specific time intervals such as an epidemic week.
    - Validation:
        - Every timezero in the csv file must have a matching one in the project. Note that the inverse is not
          necessarily true, such as in the case above of missing timezeros.
        - Every location in the csv file must a matching one in the Project.
        - Ditto for every target.

    :param truth_file_path_or_fp: Path to csv file with the truth data, one line per timezero|location|target
        combination, OR an already-open file-like object
    :param file_name: name to use for the file
    """
    logger.debug(f"load_truth_data(): entered. truth_file_path_or_fp={truth_file_path_or_fp}, "
                 f"file_name={file_name}")
    if not project.pk:
        raise RuntimeError("instance is not saved the the database, so can't insert data: {!r}".format(project))

    logger.debug(f"load_truth_data(): calling delete_truth_data()")
    project.delete_truth_data()

    logger.debug(f"load_truth_data(): calling _load_truth_data()")
    # https://stackoverflow.com/questions/1661262/check-if-object-is-file-like-in-python
    if isinstance(truth_file_path_or_fp, io.IOBase):
        num_rows = _load_truth_data(project, truth_file_path_or_fp)
    else:
        with open(str(truth_file_path_or_fp)) as cdc_csv_file_fp:
            num_rows = _load_truth_data(project, cdc_csv_file_fp)

    # done
    logger.debug(f"load_truth_data(): saving. num_rows: {num_rows}")
    project.truth_csv_filename = file_name or truth_file_path_or_fp.name
    project.save()
    project._update_model_score_changes()
    logger.debug(f"load_truth_data(): done")


def _load_truth_data(project, cdc_csv_file_fp):
    from forecast_app.models import TruthData  # avoid circular imports


    with connection.cursor() as cursor:
        rows = _load_truth_data_rows(project, cdc_csv_file_fp)  # validates, and replaces value to the five typed values
        if not rows:
            return 0

        truth_data_table_name = TruthData._meta.db_table
        columns = [TruthData._meta.get_field('time_zero').column,
                   TruthData._meta.get_field('location').column,
                   TruthData._meta.get_field('target').column,
                   'value_i', 'value_f', 'value_t', 'value_d', 'value_b']  # only one of value_* is non-None
        if connection.vendor == 'postgresql':
            string_io = io.StringIO()
            csv_writer = csv.writer(string_io, delimiter=',')
            for timezero, location_id, target_id, value_i, value_f, value_t, value_d, value_b in rows:
                # note that we translate None -> POSTGRES_NULL_VALUE for the nullable column
                csv_writer.writerow([timezero, location_id, target_id,
                                     value_i if value_i is not None else POSTGRES_NULL_VALUE,
                                     value_f if value_f is not None else POSTGRES_NULL_VALUE,
                                     value_t if value_t is not None else POSTGRES_NULL_VALUE,
                                     value_d if value_d is not None else POSTGRES_NULL_VALUE,
                                     value_b if value_b is not None else POSTGRES_NULL_VALUE])
            string_io.seek(0)
            cursor.copy_from(string_io, truth_data_table_name, columns=columns, sep=',', null=POSTGRES_NULL_VALUE)
        else:  # 'sqlite', etc.
            sql = """
                INSERT INTO {truth_data_table_name} ({column_names})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """.format(truth_data_table_name=truth_data_table_name, column_names=(', '.join(columns)))
            cursor.executemany(sql, rows)
    return len(rows)


def _load_truth_data_rows(project, csv_file_fp):
    """
    Similar to _cleaned_rows_from_cdc_csv_file(), loads, validates, and cleans the rows in csv_file_fp. Replaces value
    with the five typed values.
    """
    csv_reader = csv.reader(csv_file_fp, delimiter=',')

    # validate header
    try:
        orig_header = next(csv_reader)
    except StopIteration:
        raise RuntimeError("empty file")

    header = orig_header
    header = [h.lower() for h in [i.replace('"', '') for i in header]]
    if header != TRUTH_CSV_HEADER:
        raise RuntimeError("invalid header: {}".format(', '.join(orig_header)))

    # collect the rows. first we load them all into memory (processing and validating them as we go)
    location_names_to_pks = {location.name: location.id for location in project.locations.all()}
    target_name_to_object = {target.name: target for target in project.targets.all()}
    rows = []
    timezero_to_missing_count = defaultdict(int)  # to minimize warnings
    location_to_missing_count = defaultdict(int)
    target_to_missing_count = defaultdict(int)
    for row in csv_reader:
        if len(row) != 4:
            raise RuntimeError("Invalid row (wasn't 4 columns): {!r}".format(row))

        timezero_date, location_name, target_name, value = row

        # validate timezero_date
        # todo cache: time_zero_for_timezero_date() results - expensive?
        time_zero = project.time_zero_for_timezero_date(
            datetime.datetime.strptime(timezero_date, YYYY_MM_DD_DATE_FORMAT))
        if not time_zero:
            timezero_to_missing_count[timezero_date] += 1
            continue

        # validate location and target
        if location_name not in location_names_to_pks:
            location_to_missing_count[location_name] += 1
            continue

        if target_name not in target_name_to_object:
            target_to_missing_count[target_name] += 1
            continue

        # replace value with the five typed values - similar to _replace_value_with_five_types()
        target = target_name_to_object[target_name]
        data_type = Target.data_type(target.type)
        value = parse_value(value)  # parse_value() handles non-numeric cases like 'NA' and 'none'
        value_i = value if data_type == Target.INTEGER_DATA_TYPE else None
        value_f = value if data_type == Target.FLOAT_DATA_TYPE else None
        value_t = value if data_type == Target.TEXT_DATA_TYPE else None
        value_d = value if data_type == Target.DATE_DATA_TYPE else None
        value_b = value if data_type == Target.BOOLEAN_DATA_TYPE else None

        rows.append((time_zero.pk, location_names_to_pks[location_name], target_name_to_object[target_name].pk,
                     value_i, value_f, value_t, value_d, value_b))

    # report warnings
    for time_zero, count in timezero_to_missing_count.items():
        logger.warning("_load_truth_data_rows(): timezero not found in project: {}: {} row(s)"
                       .format(time_zero, count))
    for location_name, count in location_to_missing_count.items():
        logger.warning("_load_truth_data_rows(): Location not found in project: {!r}: {} row(s)"
                       .format(location_name, count))
    for target_name, count in target_to_missing_count.items():
        logger.warning("_load_truth_data_rows(): Target not found in project: {!r}: {} row(s)"
                       .format(target_name, count))

    # done
    return rows
