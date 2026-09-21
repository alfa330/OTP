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

Как мост доказывает порталу, что он мост
----------------------------------------
Подписью Ed25519 на каждом запросе (`cdr_bridge/signing.py`, проверка —
`cdr/agent_auth.py`). Закрытый ключ лежит только на шлюзе; портал знает
открытый. Общий токен остался запасным путём на время переезда: если ключа
нет, мост шлёт токен, как раньше.

Чему мост НЕ верит
------------------
Порталу — не безусловно. Задание исполняется, только если оно похоже на сутки:
окно не шире 25 часов и начинается в полночь названного дня. Взломанный портал
не сможет через мост заставить станцию отдать год одним запросом. Переменным
окружения вроде HTTPS_PROXY — тоже: наружу мост ходит только через прокси,
названный явно, и проверяет сертификат портала по суженному набору корней,
если он задан.

Ключ подписи
------------
    python -m cdr_bridge.agent --keygen /etc/otp-gateway/agent.key
        создаёт ключ файлом с правами 600 и печатает открытую часть с
        идентификатором — их и вносят на портал в CDR_AGENT_KEYS.

Настройки — переменные окружения (или .env.codex.local при локальной отладке):
    CDR_BRIDGE_PORTAL     https://…            адрес портала
    CDR_AGENT_KEY_FILE    /etc/otp-gateway/agent.key   закрытый ключ подписи
    CDR_AGENT_PRIVATE_KEY (вместо файла)       тот же ключ прямо в окружении
    CDR_AGENT_TOKEN       (запасной путь)      общий токен, если ключа ещё нет
    CDR_STATION_URL       http://192.168.17.44:8000
    CDR_STATION_LOGIN     (необязательно)      если станция закроет чтение CDR
    CDR_STATION_PASSWORD  (необязательно)
    CDR_PORTAL_PROXY      (необязательно)      единственный выход наружу
    CDR_PORTAL_CA_BUNDLE  (необязательно)      файл с корнями, которым верим
    CDR_HEARTBEAT_FILE    (необязательно)      куда писать отметку живости
    CDR_PBXDB_HOST        (необязательно)      база станции: журнал очередей на чтение
    CDR_PBXDB_USER / _PASSWORD / _PORT / _NAME  учётка с одним правом SELECT

Журнал очередей (`cdr_bridge/pbxdb.py`) даёт касаниям точные вход в очередь, момент
ответа, ожидание и сторону отбоя — то, чего в HTTP-выдаче станции нет с 09.09.2026.
Не настроен или не ответил — касания едут как раньше, просто без этих полей.
"""

import argparse
import json
import logging
import base64
import os
import platform
import socket
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cdr import queue_facts as queue_facts_mod, touches as touches_mod  # noqa: E402
from cdr_bridge import live, pbxdb, signing  # noqa: E402
from cdr_bridge.station import Station, StationError  # noqa: E402

VERSION = '1.2.0'

# Прокси записей на шлюзе: http://127.0.0.1:8082/rec/<относительный путь файла>.
RECORDS_DEFAULT = 'http://127.0.0.1:8082'
RECORDS_TIMEOUT = (10, 120)
# Потолок файла записи — тот же, что на портале (cdr.routes.MAX_AUDIO_BYTES).
MAX_RECORD_BYTES = 30 * 1024 * 1024
_AUDIO_TYPES = {'.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.gsm': 'audio/gsm'}

# Сколько может длиться окно чтения, которое портал присылает мосту: сутки плюс
# часовой хвост (cdr.sync.window_for). Всё, что шире, — не наше задание.
MAX_JOB_WINDOW = timedelta(hours=25)

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
        # Ключ подписи: файл предпочтительнее переменной — окружение процесса
        # видно в /proc и в docker inspect, файл с правами 600 — только владельцу.
        'key_file': value('CDR_AGENT_KEY_FILE'),
        'private_key': value('CDR_AGENT_PRIVATE_KEY'),
        # Выход наружу — только через явно названный прокси. HTTPS_PROXY и
        # подобные мост не читает вовсе (см. Bridge._build_session).
        'proxy': value('CDR_PORTAL_PROXY'),
        # Файл с корневыми сертификатами, которыми реально подписан портал, —
        # вместо всего системного набора. Подменный сертификат от любого другого
        # центра тогда не пройдёт.
        'ca_bundle': value('CDR_PORTAL_CA_BUNDLE'),
        # Пусто — пульс не пишется вовсе: при локальной отладке сторожа нет.
        'heartbeat_file': value('CDR_HEARTBEAT_FILE'),
        # Живой хвост сегодняшних суток (cdr_bridge/live.py): раз в столько секунд мост
        # досылает порталу изменившиеся касания дня. 0 — выключен.
        'live_interval': value('CDR_LIVE_INTERVAL_SECONDS', '20'),
        # Прокси записей разговоров на этой же машине (nginx перед файловым сервером,
        # отдаёт только GET по относительному пути файла). Напрямую к серверу записей
        # мосту нельзя — фаервол пускает его только на loopback.
        'records': value('CDR_RECORDS_URL', RECORDS_DEFAULT).rstrip('/'),
        # Журнал очередей станции: точные вход в очередь, ожидание, разговор и сторона
        # отбоя (`cdr_bridge/pbxdb.py`). Учётка — только на чтение. Пусто — мост работает
        # как раньше, а табло считает ожидание по длине автоинформатора.
        # FREEPBX_MYSQL_* — те же значения в доступах проекта, чтобы при локальной
        # отладке не переписывать их второй раз под другими именами.
        'pbxdb_host': value('CDR_PBXDB_HOST') or value('FREEPBX_MYSQL_HOST'),
        'pbxdb_port': value('CDR_PBXDB_PORT') or value('FREEPBX_MYSQL_PORT', '3306'),
        'pbxdb_user': value('CDR_PBXDB_USER') or value('FREEPBX_MYSQL_USER'),
        'pbxdb_password': value('CDR_PBXDB_PASSWORD') or value('FREEPBX_MYSQL_PASSWORD'),
        'pbxdb_name': value('CDR_PBXDB_NAME') or value('FREEPBX_MYSQL_DB', 'asteriskcdrdb'),
    }
    config.update({k: v for k, v in (argv_overrides or {}).items() if v})
    return config


def job_window(job):
    """Проверка задания ДО похода на станцию. Возвращает (начало, конец).

    Портал — доверенная сторона, но не безусловно: если его взломали, первое,
    что сделают через мост, — заставят станцию отдать всё за год одним окном.
    Она этого не переживёт: 25.08.2026 ей хватило трёх вызовов. Поэтому мост
    исполняет только то, что похоже на сутки: окно не шире суток с часовым
    хвостом, начало — полночь названного дня. Иначе ValueError, и на станцию
    запрос не уходит.
    """
    try:
        day = datetime.strptime(str(job.get('day') or ''), '%Y-%m-%d').date()
        start = datetime.strptime(str(job.get('from_dt') or ''), '%Y-%m-%dT%H:%M:%S')
        end = datetime.strptime(str(job.get('to_dt') or ''), '%Y-%m-%dT%H:%M:%S')
    except ValueError:
        raise ValueError('задание не разбирается: day=%r from_dt=%r to_dt=%r'
                         % (job.get('day'), job.get('from_dt'), job.get('to_dt')))
    if start.date() != day or start.time() != datetime.min.time():
        raise ValueError('окно начинается в %s, а не в полночь суток %s' % (start, day))
    if not timedelta(0) < end - start <= MAX_JOB_WINDOW:
        raise ValueError('окно %s — %s шире суток с хвостом' % (start, end))
    return start, end


class Bridge:
    def __init__(self, config, station=None, session=None, pbxdb_source=None):
        self.config = config
        self.portal = config['portal']
        self.token = config.get('token') or ''
        self.session = session or self._build_session(config)
        self.signer = self._build_signer(config)
        self.station = station or Station(config['station'], config['login'],
                                          config['password'])
        # Журнал очередей станции. Выключен настройками или без драйвера — мост
        # ведёт себя ровно как до появления точных фактов.
        self.pbxdb = pbxdb_source if pbxdb_source is not None else pbxdb.from_config(config)
        self.records_session = self._build_records_session()
        self.agent_id = '%s-%d' % (socket.gethostname()[:60], os.getpid())
        try:
            live_interval = int(str(config.get('live_interval') or '0').strip() or 0)
        except ValueError:
            live_interval = 0
        self.live = (live.LiveTail(self._post, self.station, live_interval, pbxdb=self.pbxdb)
                     if live_interval > 0 else None)

    @staticmethod
    def _build_session(config):
        """Сессия к порталу.

        trust_env=False намеренно: иначе однажды заведённая на машине переменная
        HTTPS_PROXY молча перенаправила бы канал неизвестно куда. Прокси — только
        тот, что назван в настройках, и только он. Набор доверенных корней тоже
        можно сузить до тех, которыми подписан портал.
        """
        session = requests.Session()
        session.trust_env = False
        if config.get('proxy'):
            session.proxies = {'http': config['proxy'], 'https': config['proxy']}
        if config.get('ca_bundle'):
            session.verify = config['ca_bundle']
        return session

    @staticmethod
    def _build_signer(config):
        """Ключ подписи из файла или из окружения; нет ни того ни другого — None,
        и мост ходит по старому пути с токеном. ValueError — ключ не годится."""
        text = ''
        if config.get('key_file'):
            text = signing.read_key_file(config['key_file'])
        elif config.get('private_key'):
            text = config['private_key']
        if not text:
            return None
        return signing.Signer(signing.load_private_key(text))

    @property
    def auth_label(self):
        return ('ключ %s' % self.signer.key_id) if self.signer else 'общий токен'

    # ── связь с порталом ─────────────────────────────────────────────────────

    def _post(self, path, payload):
        url_path = '/api/cdr/agent/%s' % path
        # Тело сериализуем сами и отправляем байтами: подписывается хеш ровно
        # тех байтов, что уйдут в сеть, а не то, что requests соберёт по-своему.
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if self.signer is not None:
            headers.update(self.signer.headers('POST', url_path, body))
        else:
            headers['X-Agent-Token'] = self.token
        response = self.session.post(self.portal + url_path, data=body,
                                     timeout=PORTAL_TIMEOUT, headers=headers)
        if response.status_code == 401:
            raise RuntimeError(
                'Портал не принял подпись: ключ %s не внесён в CDR_AGENT_KEYS или '
                'разошлись часы' % self.signer.key_id if self.signer else
                'Портал не принял токен: проверьте CDR_AGENT_TOKEN')
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
        day = str(job.get('day') or '?')
        started = time.time()
        try:
            window_start, window_end = job_window(job)
        except ValueError as exc:
            # На станцию не идём: такое задание — либо ошибка портала, либо
            # чужая рука на нём. В обоих случаях исполнять нельзя.
            log.error('Сутки %s: задание отвергнуто — %s', day, exc)
            self._report_failure(day, 'мост отверг задание: %s' % exc)
            return False
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
        own = self._attach_queue_facts([t for t in built if t['started_at'][:10] == day],
                                       window_start, window_end, day)
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
                # Точные поля журнала очередей станции; нет журнала — пусто, и портал
                # оставит эти колонки незаполненными (см. cdr/queue_facts.py).
                'queued_at': t.get('queued_at') or '',
                'wait_seconds': t.get('wait_seconds'),
                'talk_measured_seconds': t.get('talk_measured_seconds'),
                'hangup_side': t.get('hangup_side') or '',
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

    def _attach_queue_facts(self, touches, start, end, day):
        """Дописать касаниям точные вход в очередь, ожидание, разговор и сторону отбоя.

        Журнал очередей — источник вспомогательный: его отказ не должен стоить нам суток.
        Не прочитался — сутки уезжают как раньше, а табло считает ожидание по длине
        автоинформатора."""
        if self.pbxdb is None or not self.pbxdb.enabled:
            return touches
        try:
            facts = self.pbxdb.facts(start, end)
        except Exception as exc:  # noqa: BLE001
            log.warning('Сутки %s: журнал очередей не прочитался: %s', day, exc)
            return touches
        return queue_facts_mod.attach(touches, facts)

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
        for job in jobs:
            self.do_day(job)
        # Записи разговоров для журнала оценок — тем же проходом, после суток: они
        # нужны людям через минуты, а не через сутки, но сутки важнее.
        had_audio = self.do_audio_jobs()
        return bool(jobs) or had_audio

    # ── записи разговоров для журнала оценок ─────────────────────────────────

    @staticmethod
    def _build_records_session():
        """Сессия к прокси записей на этой же машине: без прокси наружу и без
        чужих переменных окружения — как у сессии к порталу."""
        session = requests.Session()
        session.trust_env = False
        return session

    def records_url(self, recording_url):
        """Ссылка станции (http://<файловый сервер>/recordings/…) → прокси на шлюзе
        (http://127.0.0.1:8082/rec/recordings/…). Хост из ссылки отбрасывается
        намеренно: куда ходить за файлом, решает конфигурация шлюза, а не станция."""
        path = urlsplit(str(recording_url or '')).path.lstrip('/')
        lowered = path.lower()
        if not path or '..' in path or not lowered.endswith(tuple(_AUDIO_TYPES)):
            raise ValueError('ссылка на запись не годится: %r' % (recording_url,))
        return '%s/rec/%s' % (self.config.get('records') or RECORDS_DEFAULT, path)

    def do_audio_jobs(self):
        """Заказы записей (cdr_audio_jobs): скачать через прокси, отправить порталу.
        Возвращает True, если заказы были. Наружу не бросает: запись — не сутки."""
        try:
            answer = self._post('audio_poll', {'agent_id': self.agent_id})
        except Exception as exc:  # noqa: BLE001
            log.warning('Записи: портал не ответил на опрос заказов: %s', exc)
            return False
        jobs = answer.get('jobs') or []
        delivered = 0
        for job in jobs:
            try:
                delivered += 1 if self.do_audio(job) else 0
            except Exception as exc:  # noqa: BLE001
                log.error('Запись %s: заказ не выполнен: %s', job.get('linkedid'), exc)
        # «Работа была» — только доставленные файлы: отказ не повод идти за следующим
        # заказом без паузы, иначе попытки сгорают за секунды на одной причине.
        return delivered > 0

    def _report_audio(self, job_id, status, error):
        self._post('audio', {'job_id': job_id, 'status': status, 'error': str(error)[:400]})

    def do_audio(self, job):
        """Один заказ: файл с прокси записей → base64 → портал. 404 у сервера записей —
        финальный «missing»: файла нет, повтор его не создаст."""
        job_id = job.get('id')
        linkedid = job.get('linkedid')
        try:
            url = self.records_url(job.get('recording_url'))
        except ValueError as exc:
            self._report_audio(job_id, 'error', exc)
            return False
        try:
            response = self.records_session.get(url, timeout=RECORDS_TIMEOUT)
        except requests.exceptions.RequestException as exc:
            self._report_audio(job_id, 'error', 'сервер записей: %s' % exc)
            return False
        if response.status_code == 404:
            self._report_audio(job_id, 'missing', 'файла нет на сервере записей')
            return False
        if response.status_code != 200:
            self._report_audio(job_id, 'error', 'сервер записей ответил %d' % response.status_code)
            return False
        body = response.content or b''
        if not body or len(body) > MAX_RECORD_BYTES:
            self._report_audio(job_id, 'error', 'размер файла %d байт вне допустимого' % len(body))
            return False
        content_type = (response.headers.get('Content-Type') or '').split(';')[0].strip().lower()
        if not content_type or content_type == 'application/octet-stream':
            content_type = _AUDIO_TYPES.get(os.path.splitext(url)[1].lower(), 'audio/wav')
        self._post('audio', {
            'job_id': job_id, 'status': 'ok', 'content_type': content_type,
            'bytes': len(body), 'audio_b64': base64.b64encode(body).decode('ascii'),
        })
        log.info('Запись %s: %d байт отправлено порталу', linkedid, len(body))
        return True

    def beat(self):
        """Отметка живости для сторожа снаружи (healthcheck контейнера, службы).

        Ставится ТОЛЬКО после прохода, который целиком удался. Это принципиально:
        «процесс запущен» — не признак здоровья. Мост, который прекрасно ходит к
        порталу, но не может прочитать станцию, обязан считаться больным, иначе
        плашка зелёная, а данных нет. Отсутствие работы здоровьем не считается:
        пустой ответ портала — это тоже удачный проход.

        Ошибка записи не должна ронять мост: пульс — диагностика, а не работа.
        """
        path = self.config.get('heartbeat_file')
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('%d %s\n' % (int(time.time()), self.agent_id))
        except OSError as exc:
            log.warning('Пульс не записался в %s: %s', path, exc)

    def run(self):
        log.info('Мост «Касания» %s запущен. Портал: %s (%s), станция: %s, id: %s',
                 VERSION, self.portal, self.auth_label, self.config['station'],
                 self.agent_id)
        error_sleep = ERROR_SLEEP_SECONDS
        next_poll = 0.0
        while True:
            try:
                now = time.time()
                if now >= next_poll:
                    had_work = self.tick()
                    self.beat()
                    error_sleep = ERROR_SLEEP_SECONDS
                    # Была работа — сразу за следующей: очередь может быть длинной,
                    # и ждать минуту между сутками значило бы растянуть месяц на час.
                    next_poll = now if had_work else now + IDLE_SLEEP_SECONDS
                # Живой хвост идёт между заданиями, по своему расписанию, и мост не роняет:
                # его ошибки остаются внутри maybe_step.
                if self.live is not None:
                    self.live.maybe_step()
                wait = next_poll - time.time()
                if self.live is not None:
                    wait = min(wait, self.live.seconds_until_due())
                time.sleep(max(0.0, min(wait, IDLE_SLEEP_SECONDS)))
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


def inside_git_checkout(path):
    """Лежит ли путь внутри рабочей копии git — то есть там, откуда файл может
    уехать в репозиторий одним неосторожным `git add -A`. Репозиторий проекта
    публичный, поэтому закрытому ключу там не место ни под каким именем."""
    current = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.exists(os.path.join(current, '.git')):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def keygen(target):
    """Новый ключ подписи. Закрытая часть — в файл с правами 600 (или на экран,
    если файла не просили), открытая с идентификатором — всегда на экран: их
    вносят на портал в CDR_AGENT_KEYS."""
    if target != '-' and inside_git_checkout(target):
        print('Отказ: %s лежит внутри репозитория git. Закрытый ключ хранят вне клона — '
              'например, в /etc/otp-gateway/agent.key или C:\\ProgramData\\otp\\agent.key.'
              % target)
        return 2
    private_b64, public_b64, kid = signing.generate()
    if target == '-':
        print('Закрытый ключ (CDR_AGENT_PRIVATE_KEY, никому не показывать):')
        print('    %s' % private_b64)
    else:
        signing.write_key_file(target, private_b64)
        print('Закрытый ключ записан в %s (права 600)' % target)
    print('Открытый ключ — добавить на портале в CDR_AGENT_KEYS через запятую:')
    print('    %s' % public_b64)
    print('Идентификатор ключа (так его покажет состояние моста на портале): %s' % kid)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Мост «Касания»: CDR → портал')
    parser.add_argument('--once', action='store_true',
                        help='один проход и выход — для проверки установки')
    parser.add_argument('--check', action='store_true',
                        help='только проверить связь со станцией и порталом')
    parser.add_argument('--portal', help='адрес портала (перекрывает окружение)')
    parser.add_argument('--station', help='адрес станции (перекрывает окружение)')
    parser.add_argument('--keygen', nargs='?', const='-', metavar='ФАЙЛ',
                        help='создать ключ подписи (в файл с правами 600 или, без '
                             'аргумента, на экран), напечатать открытую часть и выйти')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)-7s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')

    if args.keygen:
        return keygen(args.keygen)

    config = load_config({'portal': args.portal, 'station': args.station})
    if not config['portal']:
        print('Не задано: CDR_BRIDGE_PORTAL')
        return 2
    if not (config['key_file'] or config['private_key'] or config['token']):
        print('Не задан ни ключ подписи (CDR_AGENT_KEY_FILE), ни токен (CDR_AGENT_TOKEN)')
        return 2

    try:
        bridge = Bridge(config)
    except ValueError as exc:
        print('Ключ подписи не годится: %s' % exc)
        return 2

    if args.check:
        ok = True
        print('вход     %s' % bridge.auth_label)
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
