"""Synthetic apps and mocked cloud commands; never accesses provider accounts."""
import argparse
from datetime import datetime, timezone
import json
import sys


def events(environment='dev', timestamp=None, service='auth'):
    prefixes = {'dev': ('DEV', 41), 'preview': ('PREVIEW', 173), 'prod': ('PROD', 911)}
    prefix, duration = prefixes[environment]
    stamp = timestamp or datetime.now(timezone.utc).isoformat()
    return [dict(event_id=f'{prefix}-{service}-{number}', timestamp=stamp, service=service,
                 level='error', duration_ms=duration + number,
                 message=f'Session renewal refused for request {prefix}-{number}; LIB_AUTH_{prefix}_409',
                 email=f'{prefix.lower()}-{number}@example.test', phone=f'+15550100{number:03}',
                 error_code=f'LIB_AUTH_{prefix}_409', request_id=f'{prefix}-{number}')
            for number in range(1, {'dev': 2, 'preview': 3, 'prod': 4}[environment] + 1)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('app', choices=['auth', 'payments', 'ndjson', 'cloud-mock', 'inventory'])
    parser.add_argument('--environment', choices=['dev', 'preview', 'prod'], default='dev')
    parser.add_argument('--since')
    parser.add_argument('--until')
    parser.add_argument('--run-id', default='')
    parser.add_argument('--case', choices=['ok', 'auth', 'rate', 'malformed', 'cap', 'naive'], default='ok')
    args, _ = parser.parse_known_args()  # Official provider flags are deliberately ignored by mocks.
    if args.app == 'cloud-mock' and args.case in {'auth', 'rate'}:
        print('401 unauthorized synthetic-provider-canary' if args.case == 'auth'
              else '429 retry-after: 17 synthetic-provider-canary', file=sys.stderr)
        return 1
    if args.case == 'malformed':
        print('{invalid synthetic json')
        return 0
    if args.app == 'payments':
        for stream, number in [(sys.stdout, 1), (sys.stderr, 2)]:
            print(json.dumps(dict(event_id=f'PAY-{number}' + ('-' + args.run_id if args.run_id else ''), timestamp=datetime.now(timezone.utc).isoformat(),
                                  service='payments', level='error', duration_ms=100 * number,
                                  message=f'Payment authorization refused PAY-{number}', error_code='PAY_DECLINED_42')),
                  file=stream, flush=True)
        return 7
    stamp = args.since or datetime.now(timezone.utc).isoformat()
    if args.case == 'naive':
        stamp = '2026-10-04T12:00:00'
    rows = events(args.environment, stamp, 'inventory' if args.app == 'inventory' else 'auth')
    if args.case == 'cap':
        rows = [dict(rows[0], event_id=f'cap-{number}') for number in range(401)]
    for row in rows:
        print(json.dumps(row), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
