"""Read project-scoped structured logs without a local deployment or web session."""
from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import urlencode
from .contract import validate_name
from .platform_credentials import Credentials
from .platform_client import PlatformClient


def query_parameters(args):
    if not 1 <= args.tail <= 200:
        raise ValueError('application log --tail must be 1..200')
    q = {'limit': args.tail}
    for key in ('user_id', 'device_id', 'model', 'request_id', 'trace_id', 'event', 'app_version', 'q'):
        value = getattr(args, key, None)
        if value:
            if len(value) > 256: raise ValueError(f'{key} exceeds 256 characters')
            q[key] = value
    if args.level: q['level'] = args.level.upper()
    if args.log_origin: q['source'] = args.log_origin
    if args.before:
        if args.before < 0: raise ValueError('--before must be positive')
        q['before'] = args.before
    for key in ('since', 'until'):
        value = getattr(args, key, None)
        if not value: continue
        match = re.fullmatch(r'(\d+)([smhd])', value)
        if match and key == 'since':
            seconds = int(match[1]) * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[match[2]]
            if seconds > 365 * 86400: raise ValueError('--since duration is too large')
            timestamp = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        else:
            try: timestamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except ValueError: raise ValueError(f'--{key} requires an RFC3339 timestamp (or --since 30m)') from None
            if timestamp.tzinfo is None: raise ValueError(f'--{key} requires timezone')
        q[key] = timestamp.isoformat()
    return q


def dispatch(args):
    validate_name(args.application)
    if not args.env: raise ValueError('--env is required for application logs')
    validate_name(args.env, 'environment', 32)
    if args.service: raise ValueError('--service applies to runtime logs, use --log-origin for application logs')
    client = PlatformClient(Credentials.load(args.client_config))
    path = f'/api/v1/projects/{args.application}/environments/{args.env}/logging/events?'
    result = client.json('GET', path + urlencode(query_parameters(args)), limit=8*1024*1024)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for item in result['items']:
            # Escape control characters from untrusted business logs in terminal output.
            fields = [item.get(k, '') for k in ('occurred_at','level','source','event','message')]
            print(' '.join(json.dumps(v, ensure_ascii=False) for v in fields))
        if result.get('next_cursor'): print(f"Next page: --before {result['next_cursor']}")
