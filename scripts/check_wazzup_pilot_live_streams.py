"""Authorized 30-second production SSE observation; no vendor send requests.

Six streams reuse one alfa330 login, watching only naturally arriving events.
Server clock is estimated from a read-only refresh of a synthetic absent chat.
Only aggregate timings/counts are printed; identifiers and content stay in RAM.
Run after deployment readiness: python scripts/check_wazzup_pilot_live_streams.py --run
"""
import argparse
import json
import math
import threading
import time
from datetime import datetime

import requests

from check_wazzup_pilot_live import DEFAULT_BASE, SmokeError, authenticate, config, read_json


def summary(values):
    if not values:
        return {'samples': 0}
    values = sorted(values)
    def pct(fraction):
        return round(values[max(0, math.ceil(len(values) * fraction) - 1)], 2)
    return {'samples': len(values), 'p50_ms': pct(.5), 'p95_ms': pct(.95),
            'max_ms': round(values[-1], 2), 'min_ms': round(values[0], 2)}


def server_clock(session):
    estimates = []
    for _ in range(3):
        started = time.time() * 1000
        payload, _ = read_json(session, DEFAULT_BASE, '/api/wazzup/pilot/refresh', method='POST', json={
            'account': 'op', 'channelId': '__icore_observation_absent_channel__',
            'chatId': '__icore_observation_absent_chat__', 'messageIds': []})
        ended = time.time() * 1000
        server_ms = datetime.fromisoformat(payload['serverTime']).timestamp() * 1000
        estimates.append((ended - started, server_ms - (started + ended) / 2))
    rtt, offset = min(estimates)
    return offset, {'estimate_ms_server_minus_client': round(offset, 2),
                    'uncertainty_at_least_ms': round(rtt / 2, 2), 'calibration_rtt_ms': round(rtt, 2)}


def run(args):
    stop, count_events = threading.Event(), threading.Event()
    output = {'scope': 'Production natural events only; no message send attempts',
              'clients': 6, 'observation_seconds': args.seconds}
    streams = [{'http_status': None, 'connected': False, 'database_ready': False,
                'heartbeat_frames': 0, 'change_frames': 0, 'reload_frames': 0,
                'unavailable_frames': 0} for _ in range(6)]
    opened = [threading.Event() for _ in range(6)]
    event_sets, latencies = [set() for _ in range(6)], [[] for _ in range(6)]
    probe_times = []
    with authenticate(DEFAULT_BASE, config(), 'ADMIN') as admin:
        capabilities, _ = read_json(admin, DEFAULT_BASE, '/api/wazzup/pilot', params={'account': 'op'})
        if not capabilities.get('enabled'):
            raise SmokeError('ALFA330_PILOT_NOT_ENABLED')
        clock_offset, output['clock_calibration'] = server_clock(admin)
        headers = dict(admin.headers)

        def stream(index):
            result, session = streams[index], requests.Session()
            session.headers.update(headers)
            try:
                with session.get(DEFAULT_BASE + '/api/wazzup/pilot/stream?account=op',
                                 headers={'Accept': 'text/event-stream'}, stream=True,
                                 timeout=(15, 30)) as response:
                    result['http_status'] = response.status_code
                    if response.status_code != 200:
                        opened[index].set()
                        return
                    event = ''
                    for line in response.iter_lines(chunk_size=1, decode_unicode=True):
                        if stop.is_set():
                            return
                        if line.startswith('event: '):
                            event = line[7:]
                        elif line.startswith(': heartbeat'):
                            result['heartbeat_frames'] += 1
                        elif line.startswith('data: '):
                            payload = json.loads(line[6:])
                            if event == 'connected':
                                result['connected'] = True
                                result['database_ready'] = bool(payload.get('ready'))
                                opened[index].set()
                            elif event == 'reload':
                                result['reload_frames'] += 1
                                result['database_ready'] = bool(payload.get('ready'))
                            elif event == 'unavailable':
                                result['unavailable_frames'] += 1
                                result['database_ready'] = False
                            elif event == 'change' and count_events.is_set():
                                result['change_frames'] += 1
                                received_ms = time.time() * 1000 + clock_offset
                                for change in payload.get('changes') or []:
                                    event_key = (change.get('messageId'), change.get('emittedAt'))
                                    event_sets[index].add(event_key)
                                    if isinstance(change.get('emittedAt'), (int, float)):
                                        latencies[index].append(received_ms - change['emittedAt'])
                        elif not line:
                            event = ''
            except Exception as error:
                if not stop.is_set():
                    result['error_type'] = type(error).__name__
                    opened[index].set()
            finally:
                session.close()

        threads = [threading.Thread(target=stream, args=(i,), daemon=True) for i in range(6)]
        for thread in threads:
            thread.start()
        try:
            deadline = time.monotonic() + 20
            for signal in opened:
                signal.wait(max(0, deadline - time.monotonic()))
            count_events.set()
            end = time.monotonic() + args.seconds
            while time.monotonic() < end:
                try:
                    _, elapsed = read_json(admin, DEFAULT_BASE, '/api/wazzup/pilot', params={'account': 'op'})
                    probe_times.append(elapsed)
                except Exception as error:
                    output['probe_error_type'] = type(error).__name__
                stop.wait(min(2, max(0, end - time.monotonic())))
        finally:
            count_events.clear()
            stop.set()
            # Idle readers finish at the next heartbeat (20s); this also closes sockets.
            deadline = time.monotonic() + 25
            for thread in threads:
                thread.join(max(0, deadline - time.monotonic()))
            output['reader_threads_still_open'] = sum(thread.is_alive() for thread in threads)

    output['streams'] = [dict(result, unique_events=len(event_sets[index]),
                              emitted_to_reader_ms=summary(latencies[index]))
                         for index, result in enumerate(streams)]
    output['natural_events_unique_union'] = len(set().union(*event_sets))
    output['natural_events_seen_by_all_six'] = len(set.intersection(*event_sets))
    output['emitted_to_reader_ms'] = summary([value for values in latencies for value in values])
    output['ordinary_authenticated_get_ms'] = summary(probe_times)
    output['latency_limitations'] = (
        'Clock-adjusted estimate with RTT/2 uncertainty; includes DB commit/LISTEN and public network, '
        'excludes Wazzup-to-webhook delay and browser refresh/render. No events means unmeasured latency.')
    output['passed'] = (all(result['http_status'] == 200 and result['connected']
                            and result['database_ready'] and not result.get('error_type')
                            and not result['unavailable_frames'] for result in streams)
                        and not output['reader_threads_still_open'] and not output.get('probe_error_type'))
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--seconds', type=int, choices=range(20, 46), default=30)
    args = parser.parse_args()
    if not args.run:
        parser.error('Pass --run only after deployment readiness')
    try:
        result = run(args)
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result['passed'] else 1)
    except SmokeError as error:
        print(json.dumps({'error': str(error), 'passed': False}))
        raise SystemExit(1)
    except Exception as error:
        print(json.dumps({'error_type': type(error).__name__, 'passed': False}))
        raise SystemExit(1)
