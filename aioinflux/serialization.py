from typing import Iterable, Mapping, Union, Tuple

import pandas as pd
import numpy as np

# Special characters documentation:
# https://docs.influxdata.com/influxdb/v1.2/write_protocols/line_protocol_reference/#special-characters
# Although not in the official docs, new line characters are removed in order to avoid issues.
escape_key = str.maketrans({',': r'\,', ' ': r'\ ', '=': r'\=', '\n': ''})
escape_tag = str.maketrans({',': r'\,', ' ': r'\ ', '=': r'\=', '\n': ''})
escape_str = str.maketrans({'"': r'\"', '\n': ''})
escape_measurement = str.maketrans({',': r'\,', ' ': r'\ ', '\n': ''})


def parse_data(data, measurement, tag_columns, **extra_tags):
    if isinstance(data, bytes):
        return data
    elif isinstance(data, str):
        return data.encode('utf-8')
    elif isinstance(data, pd.DataFrame):
        if measurement is None:
            raise ValueError("Missing 'measurement'")
        return parse_df(data, measurement, tag_columns, **extra_tags)
    elif isinstance(data, Mapping):
        return make_line(data)
    elif isinstance(data, Iterable):
        return b'\n'.join([parse_data(i, measurement, tag_columns, **extra_tags) for i in data])
    else:
        raise ValueError('Invalid input', data)


def make_line(point, **extra_tags):
    p = dict(measurement=_parse_measurement(point),
             tags=_parse_tags(point, extra_tags),
             fields=_parse_fields(point),
             timestamp=_parse_timestamp(point))
    if p['tags']:
        line = '{measurement},{tags} {fields} {timestamp}'.format(**p)
    else:
        line = '{measurement} {fields} {timestamp}'.format(**p)
    return line.encode('utf-8')


def _parse_measurement(point):
    return point['measurement'].translate(escape_measurement)


def _parse_tags(point, extra_tags):
    output = []
    for k, v in sorted({**point['tags'], **extra_tags}.items()):
        k = k.translate(escape_key)
        v = v.translate(escape_tag)
        if not v:
            continue  # ignore blank/null string tags
        output.append('{k}={v}'.format(k=k, v=v))
    if output:
        return ','.join(output)
    else:
        return ''


def _parse_timestamp(point):
    if 'time' not in point:
        return str()
    else:
        return int(pd.Timestamp(point['time']).asm8)


def _parse_fields(point):
    output = []
    for k, v in point['fields'].items():
        k = k.translate(escape_key)
        if isinstance(v, int):
            output.append('{k}={v}i'.format(k=k, v=v))
        elif isinstance(v, bool):
            output.append('{k}={v}'.format(k=k, v=str(v).upper()))
        elif isinstance(v, str):
            output.append('{k}="{v}"'.format(k=k, v=v.translate(escape_str)))
        elif np.isnan(v) or v is None:
            continue
        else:
            output.append('{k}={v}'.format(k=k, v=v))
    return ','.join(output)


def make_df(resp) -> Union[pd.DataFrame, Iterable[Tuple[str, pd.DataFrame]]]:
    """Makes list of DataFrames from a response object"""

    def maker(series) -> pd.DataFrame:
        df = pd.DataFrame(series['values'], columns=series['columns'])
        df = df.set_index(pd.to_datetime(df['time'])).drop('time', axis=1)  # type: pd.DataFrame
        df.index = df.index.tz_localize('UTC')
        df.index.name = None
        if 'name' in series:
            df.name = series['name']
        return df

    df_list = [(series['name'], maker(series))
               for statement in resp['results']
               for series in statement['series']]
    if len(df_list) == 1:
        return df_list[0][1]
    else:
        return df_list


def parse_df(df, measurement, tag_columns=None, **extra_tags):
    # Calling t._asdict is more straightforward
    # but about 40% slower than using indexes
    def parser(df):
        for t in df.itertuples():
            tags = dict()
            fields = dict()
            for i, k in enumerate(t._fields[1:]):
                if i + 1 in tag_indexes:
                    tags[k] = t[i + 1]
                else:
                    fields[k] = t[i + 1]
            tags.update(extra_tags)
            yield dict(measurement=measurement,
                       time=t[0],
                       tags=tags,
                       fields=fields)

    df = df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError('DataFrame index is not DatetimeIndex')
    for col_name, dtype in df.dtypes.iteritems():
        if dtype == np.dtype('O'):
            df[col_name] = df[col_name].astype(str)
    if tag_columns:
        tag_indexes = [list(df.columns).index(tag) + 1 for tag in tag_columns]
    else:
        tag_indexes = list()
    lines = [make_line(p) for p in parser(df)]
    return b'\n'.join(lines)
