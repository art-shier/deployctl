"""Merge project build arguments and encode Docker action list input."""

import csv
import io

from .contract import validate_build_args


def resolve_build_args(defaults, overrides=''):
    result = validate_build_args(defaults)
    if not isinstance(overrides, str) or len(overrides.encode('utf-8')) > 65536:
        raise ValueError('build-args must be a KEY=value string of at most 64 KiB')
    extra = {}
    for line in overrides.split('\n'):
        if not line.strip():
            continue
        name, separator, value = line.partition('=')
        if not separator or name in extra:
            raise ValueError('build-args requires unique KEY=value lines; bare names are not supported')
        extra[name] = value
    result.update(validate_build_args(extra, 'build-args'))
    return validate_build_args(result, 'effective build-args')


def render_build_args(arguments):
    arguments = validate_build_args(arguments, 'effective build-args')
    content = io.StringIO()
    writer = csv.writer(content, quoting=csv.QUOTE_ALL, lineterminator='\n')
    for name, value in arguments.items():
        writer.writerow([name + '=' + value])
    return content.getvalue().rstrip('\n')
