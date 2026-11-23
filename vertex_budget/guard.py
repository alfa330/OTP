"""Дневной лимит расхода Vertex AI (владелец, 23.11.2026: «Надо поставить дневной лимит 5$,
чтобы такого больше не происходило вообще»).

Откуда взялось. 18–20.11.2026 вторая машина разработки проверяла ударения в роликах моделью
gemini-3.1-pro-preview тем же ключом проекта, что у прода: 6,8 млн токенов выхода (почти всё —
«мышление»), около 81 $. В консоли Google — 54,90 $ за 18.11 и 32,36 $ за 19.11 при обычном
расходе около 1 $ в день.

Почему сторож, а не настройка Google. Бюджет Cloud Billing расход не останавливает, только шлёт
письма, а платёжный аккаунт проекта сервисному аккаунту недоступен. Квоты Vertex меряют запросы
и токены в минуту, а не деньги за день. Остаётся выключатель самого Vertex AI API: выключенный
API отказывает всем, у кого ключ проекта, — проду, машинам разработчиков, скриптам.

Как работает — раз в минуту, своим потоком, в процессе с ботом и планировщиком:
* расход дня = токены по моделям из Cloud Monitoring (token_count, вход и выход) × тариф
  (prices.py). День — как в счёте Google, по тихоокеанскому времени: сброс в 13:00 по Алматы
  (летом в 12:00). Так в консоли Google за день не набегает больше лимита.
* метрики доезжают с опозданием на 1–4 минуты, поэтому к расходу дня прибавляется расход
  последних 10 минут — запас на недоехавшее. Прогон по 45 дням до 22.11.2026: в обычные дни
  расход с запасом не выше 3,5 $ — лимит не срабатывает; 18.11 и 19.11 сторож выключил бы
  Vertex через ~10 минут всплеска, и дни закрылись бы на ≈ 7 $ вместо 55 $ и 33 $. Остаток
  сверх лимита — запросы, запущенные пачкой до выключения: их Google считает, даже когда
  метрика их ещё не показала. Запас «резче» (по последним 3–4 минутам) этот остаток почти не
  уменьшал, а обычные дни подводил к 4,6 $ — не взят.
* расход с запасом дошёл до лимита — Vertex AI API выключается, владельцу уходит сообщение в
  Telegram. Выключенный сторожем Vertex стоит до конца дня, даже если запас потом опал.
  Выключить не вышло — владелец получает и об этом (раз в день), иначе лимит отказал бы молча.
* в новый день сторож включает Vertex обратно, но только если выключал сам: кто выключил,
  видно в журнале аудита Google. Выключил человек в консоли — сторож его не трогает.
* лимит исчерпан, а человек включил Vertex в консоли — это ручное снятие лимита до конца дня,
  сторож его не перебивает. Включил сервисный аккаунт (скрипт, агент) — выключает снова.

Лимит — VERTEX_DAILY_LIMIT_USD (по умолчанию 5), выключатель сторожа —
VERTEX_BUDGET_GUARD_ENABLED=false. Без ключа проекта сторож не стартует; ключ без прав (дев-стенд
с otp-dev, ответ 403) — одна строка в журнал, и сторож останавливается до перезапуска. Прочие
отказы Google пишутся в журнал не чаще раза в час.
"""

import json
import logging
import os
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import prices

logger = logging.getLogger(__name__)

SERVICE = 'aiplatform.googleapis.com'
TOKEN_METRIC = 'aiplatform.googleapis.com/publisher/online_serving/token_count'
BILLING_TZ = ZoneInfo('America/Los_Angeles')   # день в счёте Google
ALMATY_TZ = timezone(timedelta(hours=5))

ENV_LIMIT = 'VERTEX_DAILY_LIMIT_USD'
ENV_ENABLED = 'VERTEX_BUDGET_GUARD_ENABLED'
DEFAULT_LIMIT_USD = 5.0

INTERVAL_SECONDS = 60
FIRST_RUN_DELAY_SECONDS = 60
RECENT_SECONDS = 600
DISABLE_LOOKBACK = timedelta(days=30)
HTTP_TIMEOUT = 30
ERROR_LOG_EVERY_SECONDS = 3600

_MONITORING_URL = 'https://monitoring.googleapis.com/v3/projects/{project}/timeSeries'
_SERVICE_URL = 'https://serviceusage.googleapis.com/v1/projects/{project}/services/' + SERVICE
_LOGGING_URL = 'https://logging.googleapis.com/v2/entries:list'


class GuardError(Exception):
    """Отказ Google — текст для журнала и код ответа (403 — нет прав: ключ сменится только с
    перезапуском, ждать незачем)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def limit_usd(environ=None):
    environ = os.environ if environ is None else environ
    raw = str(environ.get(ENV_LIMIT) or '').strip().replace(',', '.')
    try:
        value = float(raw) if raw else DEFAULT_LIMIT_USD
    except ValueError:
        return DEFAULT_LIMIT_USD
    return value if value > 0 else DEFAULT_LIMIT_USD


def is_enabled(environ=None):
    environ = os.environ if environ is None else environ
    return str(environ.get(ENV_ENABLED, 'true')).strip().lower() not in ('0', 'false', 'off', 'no')


def billing_day(now):
    """(начало дня счёта Google, начало следующего) — оба в UTC.

    Сутки складываются по стенным часам Калифорнии, поэтому в день перевода часов их 23 или 25."""
    local = now.astimezone(BILLING_TZ)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def _iso(moment):
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _parse_time(value):
    """Время Google («…T05:59:00.123456789Z») — наносекунды Python не разбирает, режем до микро."""
    text = re.sub(r'(\.\d{6})\d+', r'\1', str(value or '').strip()).replace('Z', '+00:00')
    return datetime.fromisoformat(text)


def day_cost(tokens):
    """{(модель, вид): токены} → (сумма $, {модель: $}, незнакомые модели)."""
    per_model = defaultdict(float)
    unknown = set()
    for (model, kind), count in tokens.items():
        if not prices.price_for(model)[1]:
            unknown.add(model)
        per_model[model] += prices.cost_usd(model, kind, count)
    return sum(per_model.values()), dict(per_model), unknown


class GoogleCloud:
    """Обращения к Google ключом проекта: метрики, состояние Vertex AI API, журнал аудита."""

    def __init__(self, session, project, email):
        self.session = session
        self.project = project
        self.email = email

    @classmethod
    def from_env(cls):
        from call_qa import config as qa_config

        info = qa_config.google_sa_info()
        if not info or not info.get('project_id'):
            return None
        import google.auth.transport.requests as google_requests
        from google.oauth2 import service_account

        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=['https://www.googleapis.com/auth/cloud-platform'])
        return cls(google_requests.AuthorizedSession(credentials), info['project_id'],
                   info.get('client_email') or '')

    @staticmethod
    def _body(response, what):
        if response.status_code >= 400:
            raise GuardError('%s: HTTP %s %s' % (what, response.status_code,
                                                  ' '.join((response.text or '').split())[:300]),
                             status=response.status_code)
        return response.json() if response.content else {}

    def usage(self, start, end, recent_seconds=RECENT_SECONDS):
        """Токены по (модель, вид) за [start, end] и отдельно — за последние recent_seconds."""
        window = int((end - start).total_seconds())
        if window < 60:
            return {}, {}
        align = max(60, min(recent_seconds, window))
        params = [
            ('filter', 'metric.type="%s"' % TOKEN_METRIC),
            ('interval.startTime', _iso(start)),
            ('interval.endTime', _iso(end)),
            ('aggregation.alignmentPeriod', '%ds' % align),
            ('aggregation.perSeriesAligner', 'ALIGN_SUM'),
            ('aggregation.crossSeriesReducer', 'REDUCE_SUM'),
            ('aggregation.groupByFields', 'resource.labels.model_user_id'),
            ('aggregation.groupByFields', 'metric.labels.type'),
        ]
        last_bucket_end = end.replace(microsecond=0) - timedelta(seconds=1)
        totals, recent = defaultdict(int), defaultdict(int)
        page_token = None
        while True:
            page = params + ([('pageToken', page_token)] if page_token else [])
            body = self._body(self.session.get(_MONITORING_URL.format(project=self.project),
                                               params=page, timeout=HTTP_TIMEOUT), 'метрики Vertex')
            for series in body.get('timeSeries') or []:
                model = ((series.get('resource') or {}).get('labels') or {}).get('model_user_id') or '?'
                kind = ((series.get('metric') or {}).get('labels') or {}).get('type') or 'output'
                for point in series.get('points') or []:
                    value = int((point.get('value') or {}).get('int64Value') or 0)
                    totals[(model, kind)] += value
                    if _parse_time(point['interval']['endTime']) >= last_bucket_end:
                        recent[(model, kind)] += value
            page_token = body.get('nextPageToken')
            if not page_token:
                return dict(totals), dict(recent)

    def service_state(self):
        body = self._body(self.session.get(_SERVICE_URL.format(project=self.project),
                                           timeout=HTTP_TIMEOUT), 'состояние Vertex AI API')
        return str(body.get('state') or '').upper()

    def set_service(self, enabled):
        verb = 'enable' if enabled else 'disable'
        # Зависимые сервисы не выключаем: включить их обратно сторожу было бы нечем.
        payload = {} if enabled else {'disableDependentServices': False, 'checkIfServiceHasUsage': 'SKIP'}
        self._body(self.session.post(_SERVICE_URL.format(project=self.project) + ':' + verb,
                                     json=payload, timeout=HTTP_TIMEOUT), '%s Vertex AI API' % verb)

    def last_toggle(self, enabled, since):
        """(кто, когда) — последнее включение или выключение Vertex AI API после since, по журналу
        аудита Google (он пишется всегда и бесплатно). None — не было."""
        method = 'EnableService' if enabled else 'DisableService'
        query = ('logName="projects/%s/logs/cloudaudit.googleapis.com%%2Factivity" '
                 'AND protoPayload.serviceName="serviceusage.googleapis.com" '
                 'AND protoPayload.methodName:"%s" AND timestamp>="%s"' % (self.project, method, _iso(since)))
        body = self._body(self.session.post(_LOGGING_URL, json={
            'resourceNames': ['projects/%s' % self.project], 'filter': query,
            'orderBy': 'timestamp desc', 'pageSize': 50}, timeout=HTTP_TIMEOUT), 'журнал аудита')
        for entry in body.get('entries') or []:
            payload = entry.get('protoPayload') or {}
            target = str(payload.get('resourceName') or '') + json.dumps(payload.get('request') or {})
            if SERVICE in target:
                who = (payload.get('authenticationInfo') or {}).get('principalEmail') or ''
                return who, _parse_time(entry.get('timestamp'))
        return None


def _is_person(email):
    return bool(email) and not str(email).lower().endswith('.gserviceaccount.com')


def _money(value):
    return ('%.2f' % value).replace('.', ',') + ' $'


def _reset_words(next_start, now):
    reset = next_start.astimezone(ALMATY_TZ)
    day = 'сегодня' if reset.date() == now.astimezone(ALMATY_TZ).date() else 'завтра'
    return '%s в %s по Алматы' % (day, reset.strftime('%H:%M'))


def disabled_text(spent, recent, limit, per_model, next_start, now, again=False):
    if again:
        first = ('Vertex AI снова выключен: его включили ключом проекта (скрипт или агент), '
                 'а дневной лимит %s исчерпан.' % _money(limit))
    else:
        first = 'Vertex AI выключен: дневной лимит %s исчерпан.' % _money(limit)
    spent_line = 'Потрачено за день ≈ %s' % _money(spent)
    if recent >= 0.01:
        spent_line += ', из них за последние 10 минут ≈ %s' % _money(recent)
    lines = [first, spent_line + '.']
    top = [(model, cost) for model, cost in sorted(per_model.items(), key=lambda item: -item[1])
           if cost >= 0.01][:2]
    if top:
        lines.append('Больше всего: %s.' % ', '.join('%s — %s' % (model, _money(cost)) for model, cost in top))
    lines.append('Включится сам %s, когда у Google начнётся новый день.' % _reset_words(next_start, now))
    lines.append('Снять лимит до конца дня — включить Vertex AI API в консоли Google Cloud.')
    return '\n'.join(lines)


def enabled_text(limit):
    return 'Vertex AI снова включён: у Google начался новый день, лимит %s обнулился.' % _money(limit)


def failed_text(spent, limit, error):
    return ('Дневной лимит Vertex AI %s исчерпан (потрачено ≈ %s), но выключить Vertex AI API не '
            'вышло: %s. Расход не остановлен — выключи Vertex AI API в консоли Google Cloud.'
            % (_money(limit), _money(spent), str(error)[:200]))


def enable_failed_text(error):
    return ('Новый день, но включить Vertex AI обратно не вышло: %s. ИИ в портале стоит — включи '
            'Vertex AI API в консоли Google Cloud.' % str(error)[:200])


_unknown_logged = set()
_override_logged_day = None
_failure_notified_day = None
_enable_failure_day = None


def _notify(notify, text):
    if notify is None:
        return
    try:
        notify(text)
    except Exception:  # noqa: BLE001
        logger.exception('Vertex AI, дневной лимит: сообщение владельцу не ушло')


def run_once(cloud, *, now=None, limit=None, notify=None):
    """Один проход сторожа: 'disabled', 'enabled', 'override' (человек снял лимит) или None."""
    global _override_logged_day, _failure_notified_day, _enable_failure_day
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    limit = limit_usd() if limit is None else limit
    day_start, next_start = billing_day(now)
    totals, recent_tokens = cloud.usage(day_start, now)
    spent, per_model, unknown = day_cost(totals)
    recent = day_cost(recent_tokens)[0]
    for model in sorted(unknown - _unknown_logged):
        _unknown_logged.add(model)
        logger.warning('Vertex AI, дневной лимит: тарифа модели %s нет — считаю по цене Pro, '
                       'допиши её в vertex_budget/prices.py', model)
    over = spent + recent >= limit
    state = cloud.service_state()

    if state == 'ENABLED':
        if not over:
            return None
        # Журнал аудита не читается — выключаем всё равно: лимит важнее ручного снятия.
        try:
            enabled = cloud.last_toggle(True, day_start)
            # «Снова»: сегодня Vertex уже выключали, а потом его включил ключ проекта. Утреннее
            # включение самим сторожем выключения в этот день не имеет — это обычный случай.
            disabled_today = cloud.last_toggle(False, day_start) if enabled else None
        except GuardError as error:
            logger.error('Vertex AI, дневной лимит: журнал аудита не прочитан (%s) — выключаю без '
                         'проверки ручного снятия', error)
            enabled = disabled_today = None
        if enabled and _is_person(enabled[0]):
            if _override_logged_day != day_start:
                _override_logged_day = day_start
                logger.warning('Vertex AI, дневной лимит: %s из %s, но Vertex включил человек — '
                               'лимит снят до конца дня', _money(spent), _money(limit))
            return 'override'
        again = bool(disabled_today) and disabled_today[1] <= enabled[1]
        try:
            cloud.set_service(False)
        except GuardError as error:
            if _failure_notified_day != day_start:
                _failure_notified_day = day_start
                _notify(notify, failed_text(spent, limit, error))
            raise
        logger.warning('Vertex AI, дневной лимит: %s за день (+%s за 10 минут) при лимите %s — '
                       'Vertex AI API выключен до %s', _money(spent), _money(recent), _money(limit),
                       _reset_words(next_start, now))
        _notify(notify, disabled_text(spent, recent, limit, per_model, next_start, now, again=again))
        return 'disabled'

    if state == 'DISABLED' and not over:
        try:
            disabled = cloud.last_toggle(False, now - DISABLE_LOOKBACK)
            # Включаем только своё и только в новый день: выключенное сегодня стоит до сброса.
            if not disabled or disabled[0] != cloud.email or disabled[1] >= day_start:
                return None
            cloud.set_service(True)
        except GuardError as error:
            if _enable_failure_day != day_start:
                _enable_failure_day = day_start
                _notify(notify, enable_failed_text(error))
            raise
        logger.info('Vertex AI, дневной лимит: новый день — Vertex AI API включён')
        _notify(notify, enabled_text(limit))
        return 'enabled'
    return None


_error_logged_at = {}


def _log_error(key, message, exc_info=False):
    """Отказ, повторяющийся каждый проход, в журнале — раз в час."""
    moment = time.monotonic()
    if moment - _error_logged_at.get(key, -ERROR_LOG_EVERY_SECONDS) >= ERROR_LOG_EVERY_SECONDS:
        _error_logged_at[key] = moment
        logger.error('Vertex AI, дневной лимит: %s', message, exc_info=exc_info)


def _loop(cloud, notify, interval, first_delay):
    time.sleep(first_delay)
    while True:
        try:
            run_once(cloud, notify=notify)
        except GuardError as error:
            if error.status == 403:
                # Чужой ключ без прав (дев-стенд с otp-dev): раз в журнал — и стоп, без шума.
                logger.error('Vertex AI, дневной лимит: у ключа %s нет прав (%s) — сторож '
                             'остановлен до перезапуска', cloud.email, error)
                return
            _log_error(str(error)[:80], str(error))
        except Exception as error:  # noqa: BLE001
            _log_error(type(error).__name__, 'проход упал', exc_info=True)
        time.sleep(interval)


_worker = None
_worker_pid = None
_worker_lock = threading.Lock()


def start_worker(notify=None, *, interval=INTERVAL_SECONDS, first_delay=FIRST_RUN_DELAY_SECONDS,
                 cloud_factory=None):
    """Один поток на процесс. Без ключа проекта или с выключателем — не запускается."""
    global _worker, _worker_pid
    if not is_enabled():
        return None
    pid = os.getpid()
    with _worker_lock:
        if _worker is not None and _worker_pid == pid and _worker.is_alive():
            return _worker
        cloud = (cloud_factory or GoogleCloud.from_env)()
        if cloud is None:
            return None
        _worker = threading.Thread(target=_loop, args=(cloud, notify, interval, first_delay),
                                   name='vertex-budget-guard', daemon=True)
        _worker_pid = pid
        _worker.start()
        return _worker
