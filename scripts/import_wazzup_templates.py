"""Import an authorized browser export of ordinary Wazzup templates into iCORE.

Keep real exports under ignored outputs/, never in Git. The input is a complete
browser-captured catalogue: {workspace: '6757-7677', total: N, data: [...]}.
This tool neither reads browser credentials nor calls Wazzup private endpoints.
Preview is the default; --apply performs the already-reviewed import.
"""
import argparse
import html
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_wazzup_pilot_live import DEFAULT_BASE, SmokeError, authenticate, config
from wazzup.accounts import ACCOUNTS
from wazzup.templates import MAX_IMPORT_BYTES, MAX_IMPORTED, normalize_import


def browser_export_payload(document, *, apply=False):
    """Require a complete single-account export; never silently drop a row."""
    if not isinstance(document, dict) or document.get('workspace') != ACCOUNTS['op']['workspace']:
        raise ValueError('Expected the Verifiers Wazzup workspace export')
    rows, total = document.get('data'), document.get('total')
    if (not isinstance(rows, list) or type(total) is not int
            or total != len(rows) or not 1 <= total <= MAX_IMPORTED):
        raise ValueError('Incomplete or invalid browser export')
    items = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f'Invalid template row {index}')
        try:
            external_id = str(uuid.UUID(row['guid']))
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValueError(f'Invalid template ID in row {index}') from None
        if not isinstance(row.get('name'), str) or not isinstance(row.get('text'), str):
            raise ValueError(f'Invalid title or text in row {index}')
        if not isinstance(row.get('files'), list):
            raise ValueError(f'Missing attachment metadata in row {index}')
        items.append(dict(externalId=external_id, title=html.unescape(row['name']),
                          text=html.unescape(row['text']), files=row['files']))
    payload = dict(account='op', dryRun=not apply, items=items)
    normalize_import(payload)
    return payload


def run(args):
    source = Path(args.file)
    if source.stat().st_size > MAX_IMPORT_BYTES:
        raise ValueError('Export exceeds the import size limit')
    payload = browser_export_payload(json.loads(source.read_text(encoding='utf-8-sig')), apply=args.apply)
    with authenticate(DEFAULT_BASE, config(), 'ADMIN') as session:
        response = session.post(DEFAULT_BASE + '/api/wazzup/pilot/templates/import',
                                json=payload, timeout=45)
        if response.status_code != 200:
            # No upstream body: it may contain private data or diagnostics.
            raise SmokeError('IMPORT_HTTP_' + str(response.status_code))
        result = response.json()
        if result.get('dryRun') is not payload['dryRun'] or result.get('total') != len(payload['items']):
            raise SmokeError('IMPORT_RESPONSE_MISMATCH')
        return {key: result.get(key) for key in
                ('status', 'dryRun', 'total', 'created', 'updated', 'unchanged', 'unsupported')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', help='Complete browser export JSON (store it outside Git)')
    parser.add_argument('--apply', action='store_true', help='Write the templates; otherwise preview only')
    args = parser.parse_args()
    try:
        print(json.dumps(run(args), ensure_ascii=False))
    except (ValueError, SmokeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:
        print('IMPORT_FAILED_' + type(error).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
