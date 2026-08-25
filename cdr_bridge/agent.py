# -*- coding: utf-8 -*-
"""Мост «Касания»: забирает CDR со станции внутри корпоративной сети и отдаёт
касания порталу.

Зачем он вообще есть
--------------------
Станция стоит в корпоративной сети. 25.08.2026 её вывели наружу через прокси с
basic-auth; в тот же день сервис лёг, и внешний доступ закрыли. Портал живёт на
Render и до сети не дотягивается — значит, ходить должен тот, кто внутри.

Направление перевёрнуто осознанно: мост делает ТОЛЬКО исходящие запросы к
порталу. Наружу не открывается ни одного порта, согласовывать с админами нечего,
и то, что закрыли, не приходится открывать заново.

Что он делает
-------------
Раз в минуту (а когда есть работа — сразу же) спрашивает портал: какие сутки
нужны. Получив задание, читает CDR за эти сутки с часовым хвостом следующих,
склеивает строки в касания ПРЯМО ЗДЕСЬ и отправляет результат. Через интернет
едут 3,4 тысячи касаний вместо 23 тысяч сырых строк — в десять раз меньше.

Склейка берётся из `cdr.touches` — того же модуля, что живёт на портале. Второй
копии этой логики быть не должно: она сверена с эталонной выгрузкой построчно, и
разошедшиеся копии обнаружились бы не сразу, а на цифрах в отчёте.

Что он НЕ делает
----------------
Не ходит по ручкам станции, которые трогают AMI (`/freepbx/load/*`) — см.
белый список в `station.py`. Не держит открытых портов. Не хранит состояние:
очередь суток живёт на портале, поэтому перезапуск моста ничего не теряет.

Запуск
------
    python -m cdr_bridge.agent --once      один проход, для проверки
    python -m cdr_bridge.agent             рабочий цикл (служба)

Настройки — переменные окружения (или .env.codex.local при локальной отладке):
    CDR_BRIDGE_PORTAL     https://…            адрес портала
    CDR_AGENT_TOKEN       …                    общий токен, как на портале
    CDR_STATION_URL       http://192.168.17.44:8000
    CDR_STATION_LOGIN     (необязательно)      если станция закроет чтение CDR
    CDR_STATION_PASSWORD  (необязательно)
"""

import argparse
import json
import logging
import os
import platform
import socket
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr import touches as touches_mod  # noqa: E402
from cdr_bridge.station import Station, StationError  # noqa: E402

VERSION = '1.0.0'

log = logging.getLogger('cdr_bridge')

# Пауза, когда работы нет. Минута — компромисс: человек, нажавший «обновить»,
# ждёт не дольше минуты, а портал не получает опрос каждые пять секунд впустую.
IDLE_SLEEP_SECONDS = 60

# Пауза после отказа. Растёт до потолка, чтобы упавший портал или station не
# получали шторм повторов — именно ретрай-штормы клали прокси Oktell.
ERROR_SLEEP_SECONDS = 30
ERROR_SLEEP_MAX = 300

PORTAL_TIMEOUT = (10, 120)


def _env_file_values(path):
    out = {}
    try:
        with open(path, encoding='utf-8-sig') as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    out[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def load_config(argv_overrides=None):
    """Окружение важнее файла: на VM переменные ставит служба, а файл нужен
    только при локальной отладке."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dev = _env_file_values(os.path.join(root, '.env.codex.local'))

    def value(key, default=''):
        return (os.environ.get(key) or dev.get(key) or default).strip()

    config = {
        'portal': value('CDR_BRIDGE_PORTAL').rstrip('/'),
        'token': value('CDR_AGENT_TOKEN'),
        'station': value('CDR_STATION_URL', 'http://192.168.17.44:8000').rstrip('/'),
        'login': value('CDR_STATION_LOGIN'),
        'password': value('CDR_STATION_PASSWORD'),
    }
    config.update({k: v for k, v in (argv_overrides or {}).items() if v})
    return config


class Bridge:
    def __init__(self, config, station=None, session=None):
        self.config = config
        self.portal = config['portal']
        self.token = config['token']
        self.session = session or requests.Session()
        self.station = station or Station(config['station'], config['login'],
                                          config['password'])
        self.agent_id = '%s-%d' % (socket.gethostname()[:60], os.getpid())

    # ── связь с порталом ─────────────────────────────────────────────────────

    def _post(self, path, payload):
        response = self.session.post(
            '%s/api/cdr/agent/%s' % (self.portal, path),
            json=payload, timeout=PORTAL_TIMEOUT,
            headers={'X-Agent-Token': self.token,
                     'Content-Type': 'application/json'})
        if response.status_code == 401:
            raise RuntimeError('Портал не принял токен: проверьте CDR_AGENT_TOKEN')
        if response.status_code != 200:
            # Тело читаем текстом, а не json(): на 502/504 от прокси там HTML, и
            # разбор JSON бросил бы своё исключение поверх настоящей причины.
            raise RuntimeError('Портал ответил %d: %s'
                               % (response.status_code, response.text[:200]))
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError('Портал вернул не JSON: %s' % response.text[:200]) from exc

    def poll(self, error=None):
        return self._post('poll', {
            'agent_id': self.agent_id,
            'hostname': socket.gethostname(),
            'version': '%s/%s' % (VERSION, platform.python_version()),
            'station_url': self.config['station'],
            'error': error,
        })

    # ── работа ───────────────────────────────────────────────────────────────

    def send_directory(self):
        """Справочник агентов станции. Портал сам к станции не ходит, а знать,
        кто владеет номером сейчас, может только она."""
        agents = self.station.agents_map()
        result = self._post('directory', {'agents': agents})
        log.info('Справочник станции отправлен: %d номеров', result.get('agents', 0))

    def do_day(self, job):
        """Одни сутки: прочитать, склеить, отправить.

        Хвост следующих суток приходит от портала (`to_dt` на час больше конца
        суток) — правило «сутки плюс час» живёт в одном месте, на портале, чтобы
        двум его копиям было негде разойтись.
        """
        day = job['day']
        started = time.time()
        rows = []
        try:
            for row in self.station.iter_cdr(job['from_dt'], job['to_dt']):
                rows.append(row)
        except StationError as exc:
            log.warning('Сутки %s: станция отказала (%s) — %s', day, exc.code, exc)
            self._report_failure(day, '%s: %s' % (exc.code, exc))
            return False
        except Exception as exc:  # noqa: BLE001
            # Всё, что не StationError — битый JSON, кончившаяся память, ошибка
            # в разборе. Молчать нельзя: портал оставит сутки в «в работе», через
            # 15 минут выдаст их снова, и мост будет читать станцию по кругу.
            log.error('Сутки %s: не смог прочитать: %s', day, exc, exc_info=True)
            self._report_failure(day, 'чтение: %s' % exc)
            return False

        try:
            built = touches_mod.build_touches(rows)
        except Exception as exc:  # noqa: BLE001
            log.error('Сутки %s: склейка не удалась: %s', day, exc, exc_info=True)
            self._report_failure(day, 'склейка: %s' % exc)
            return False
        own = [t for t in built if t['started_at'][:10] == day]
        payload = {
            'day': day,
            'rows_fetched': len(rows),
            'touches': [{
                'linkedid': t['linkedid'], 'phone': t['phone'],
                'started_at': t['started_at'], 'answered_at': t['answered_at'],
                'ext': t['ext'], 'call_type': t['call_type'], 'result': t['result'],
                'talk_seconds': t['talk_seconds'], 'dial_seconds': t['dial_seconds'],
                'queue': t['queue'], 'recording_url': t['recording_url'],
                'legs': t['legs'],
            } for t in own],
        }
        try:
            result = self._post('day', payload)
        except Exception as exc:  # noqa: BLE001
            # Портал отверг тело (например касаний больше потолка). Повторять
            # бессмысленно — данные те же; сутки надо закрыть отказом, иначе они
            # останутся «в работе» и вернутся к нам через 15 минут навсегда.
            log.error('Сутки %s: портал не принял: %s', day, exc)
            self._report_failure(day, 'портал не принял: %s' % exc)
            return False
        log.info('Сутки %s: строк CDR %d → касаний %d, отправлено за %.1f с (%s)',
                 day, len(rows), len(own), time.time() - started,
                 'закрыты' if result.get('complete') else 'ещё дописываются')
        return True

    def _report_failure(self, day, error):
        """Сказать порталу, что сутки не вышли. Если и это не дошло — записать в
        лог и жить дальше: сутки протухнут по времени взятия и вернутся сами."""
        try:
            self._post('day', {'day': day, 'error': str(error)[:400]})
        except Exception as exc:  # noqa: BLE001
            log.error('Сутки %s: не смог сообщить порталу об отказе: %s', day, exc)

    def tick(self):
        """Один проход. Возвращает True, если работа была."""
        answer = self.poll()
        if answer.get('want_directory'):
            try:
                self.send_directory()
            except StationError as exc:
                # Без справочника касания всё равно поедут — только без ФИО.
                log.warning('Справочник станции не забрался: %s', exc)
        jobs = answer.get('jobs') or []
        if not jobs:
            return False
        for job in jobs:
            self.do_day(job)
        return True

    def run(self):
        log.info('Мост «Касания» %s запущен. Портал: %s, станция: %s, id: %s',
                 VERSION, self.portal, self.config['station'], self.agent_id)
        error_sleep = ERROR_SLEEP_SECONDS
        while True:
            try:
                had_work = self.tick()
                error_sleep = ERROR_SLEEP_SECONDS
                # Была работа — сразу за следующей: очередь может быть длинной,
                # и ждать минуту между сутками значило бы растянуть месяц на час.
                time.sleep(0 if had_work else IDLE_SLEEP_SECONDS)
            except KeyboardInterrupt:
                log.info('Остановлен с клавиатуры')
                return 0
            except Exception as exc:  # noqa: BLE001
                log.error('Проход не удался: %s', exc, exc_info=True)
                try:
                    self.poll(error=str(exc)[:400])
                except Exception:
                    # Портал недоступен — сказать ему об этом всё равно нечем.
                    pass
                time.sleep(error_sleep)
                error_sleep = min(error_sleep * 2, ERROR_SLEEP_MAX)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Мост «Касания»: CDR → портал')
    parser.add_argument('--once', action='store_true',
                        help='один проход и выход — для проверки установки')
    parser.add_argument('--check', action='store_true',
                        help='только проверить связь со станцией и порталом')
    parser.add_argument('--portal', help='адрес портала (перекрывает окружение)')
    parser.add_argument('--station', help='адрес станции (перекрывает окружение)')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)-7s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')

    config = load_config({'portal': args.portal, 'station': args.station})
    missing = [name for name, key in (('CDR_BRIDGE_PORTAL', 'portal'),
                                      ('CDR_AGENT_TOKEN', 'token')) if not config[key]]
    if missing:
        print('Не задано: %s' % ', '.join(missing))
        return 2

    bridge = Bridge(config)

    if args.check:
        ok = True
        try:
            bridge.station.health()
            print('станция  %s — отвечает' % config['station'])
        except StationError as exc:
            ok = False
            print('станция  %s — НЕ отвечает: %s' % (config['station'], exc))
        try:
            answer = bridge.poll()
            print('портал   %s — принял, заданий в очереди: %d'
                  % (config['portal'], len(answer.get('jobs') or [])))
        except Exception as exc:  # noqa: BLE001
            ok = False
            print('портал   %s — НЕ принял: %s' % (config['portal'], exc))
        return 0 if ok else 1

    if args.once:
        had_work = bridge.tick()
        print('работа была' if had_work else 'очередь пуста')
        return 0

    return bridge.run()


if __name__ == '__main__':
    sys.exit(main())
