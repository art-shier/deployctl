"""Static deployment types and Linux destination validation."""
import posixpath


def validate_deployment_type(value=None):
    if value is None or value == '': return 'docker'
    if value not in ('docker', 'static'): raise ValueError('invalid project deployment type')
    return value


def validate_target_dir(value):
    if not isinstance(value, str): raise ValueError('target directory must be a Linux absolute path')
    try: size = len(value.encode('utf-8'))
    except UnicodeError: raise ValueError('invalid target directory encoding') from None
    if (not value.startswith('/') or value == '/' or posixpath.normpath(value) != value
            or value.startswith('//') or '\\' in value or size > 4096
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in '\u2028\u2029' for c in value)):
        raise ValueError('target directory must be a normalized Linux absolute path other than /')
    return value
