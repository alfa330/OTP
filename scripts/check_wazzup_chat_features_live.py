"""Post-deploy reads only: templates, unread snapshot, and pilot access controls.

No sends, template writes, read acknowledgements, or raw user content in output.
"""
import argparse
import json

from check_wazzup_pilot_live import DEFAULT_BASE, authenticate, config, read_json


def run():
    result = {}
    with authenticate(DEFAULT_BASE, config(), 'ADMIN') as session:
        data, elapsed = read_json(session, DEFAULT_BASE, '/api/wazzup/pilot/templates', params={'account': 'op'})
        result['templates'] = dict(http=200, first_ms=elapsed,
            wazzup=sum(item['source'] == 'wazzup' for item in data['items']),
            icore=sum(item['source'] == 'icore' for item in data['items']),
            warnings=len(data['sourceWarnings']), all_supported=all(item['supported'] for item in data['items']))
        _, elapsed = read_json(session, DEFAULT_BASE, '/api/wazzup/pilot/templates', params={'account': 'op'})
        result['templates']['warm_ms'] = elapsed
        data, elapsed = read_json(session, DEFAULT_BASE, '/api/wazzup/pilot/unread', params={'account': 'op'})
        result['unread'] = dict(http=200, first_page_ms=elapsed, items=len(data['items']),
            next_page=bool(data['next']), valid=all(item['unreadCount'] > 0 and item['unreadVersion'] > 0 for item in data['items']))
    with authenticate(DEFAULT_BASE, config(), 'TEST') as session:
        result['other_user_denied'] = all(session.get(DEFAULT_BASE + '/api/wazzup/pilot/' + path, timeout=30).status_code == 403
                                          for path in ('templates', 'unread'))
    result['passed'] = result['other_user_denied'] and result['unread']['valid'] and result['templates']['wazzup'] > 0
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    if not parser.parse_args().run:
        parser.error('Pass --run after deployment')
    try:
        result = run()
    except Exception as error:
        result = dict(passed=False, error_type=type(error).__name__)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['passed'] else 1)
