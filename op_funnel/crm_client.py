# -*- coding: utf-8 -*-
"""Клиент двух партнёрских ручек СРМ, из которых живут «Поток» и «Яндекс Регистрация».

Модуль умеет ровно одно: сходить в СРМ и принести сырые словари лидов, как они
пришли. Классификация исходов, причины, `work_day` и приведение к строкам
`op_funnel_leads` — в `sources.py`. Разделение не косметическое: «тупой» клиент
проверяется подменой `requests.Session` (сессия — аргумент конструктора, дубль в
проекте уже есть — `tests/test_parcels_drivers.py::_FakeSession`), а правила
классификации проверяются вообще без сети. Смешав их, мы получили бы модуль,
который нельзя проверить ни так, ни так.

Ручки и объёмы (снято на живом API 11.09.2026, период 01.08–11.09.2026)
-----------------------------------------------------------------------
    POST /api/partners/stream-leads     stream_type=1   30 000 лидов = 60 страниц
                                        stream_type=2   23 514 лидов = 48 страниц
    POST /api/partners/paid-hire-leads                   2 605 лидов =  6 страниц

`per_page` у ручек максимум 500, а по умолчанию 100: на дефолте те же данные
приехали бы 300 страницами вместо 60 — впятеро дольше и впятеро больше шансов
оборваться на середине. Поэтому качаем по 500 и отдаём лиды генератором: 54
тысячи словарей по 15 полей одним списком — это десятки мегабайт в процессе,
который на Render делит память с ботом, а `sources.py` всё равно разбирает их
по одному.

Времена лидов (`created_at`, `taken_at`, `updated_at`) клиент не парсит и не
сдвигает — отдаёт строками источника. Пояс здесь трогать нельзя: даты СРМ
местные (Алматы), и на попытке «нормализовать» их в проекте уже горели — отчёт
rating Chat2Desk уехал на +5 часов.

Токен не наш: он общий
----------------------
Заголовок `X-Integration-Token`, и это тот же токен, которым ходят конкурс
регистраций, обзвон фронт-офиса и карточка водителя в «Посылках». Поэтому его
разрешение целиком отдано `reg_contest.get_config`: там уже лежит фолбэк на
«человеческое» имя переменной (в `.env.codex.local` ключ называется буквально
`X-Integration-Token`, а не `CRM_INTEGRATION_TOKEN`). Свой резолвер означал бы
четвёртое место, куда надо не забыть добавить ключ на Render, — и раздел молча
остался бы без данных на проде, где `.env.codex.local` не читается вовсе.

Ловушка Laravel: HTML с кодом 200
---------------------------------
На кривом запросе (перевёрнутый период, дата в виде ДД.ММ.ГГГГ, пустой
обязательный параметр) эта СРМ отдаёт не 4xx, а страницу вёрстки со статусом
200. В проекте на этом горели трижды: `reg_contest` (`trip_deadline` раньше
`registered_to`), `front_office_calls` (перевёрнутый период) и
`parcels/drivers.py` (пустой `account_id`). Отсюда два правила, и они тут
главные:

1. Что проверяется до сети — проверяется до сети: формат и порядок дат, номер
   потока. Опечатка в аргументе обязана отвечать сразу и по-русски, а не через
   минуту страницей HTML.
2. Незнакомый формат = ошибка с внятным текстом, а НЕ пустой результат. Нет
   `leads` списком — исключение; лид без `lead_id` — исключение. Молчаливый
   «ноль лидов» здесь дороже падения: `sync` запишет пустые сутки,
   `freeze_daily` их зафиксирует, и на планёрке у направления будет ноль
   обработанных лидов при живых 30 тысячах. Ровно так у «Топа по регистрациям»
   сменившийся контракт обнулил рейтинг, а синк отрапортовал «ok».

HTML-200 при этом НЕ уходит в цикл повторов, хотя в `reg_contest._post` и
`front_office_calls._post` уходит. Причина арифметическая: этот отказ
детерминированный — повтор принесёт тот же HTML, зато кривой период сожжёт три
попытки по 60 секунд, а в ночной джобе таких запросов четыре направления. Ждём
повтором только то, что от повтора может измениться: сеть и 5xx.

Пагинация без вечного круга
---------------------------
Дальше идут три проверки, каждая закрывает свой способ уехать в бесконечность
или в молчаливый недобор:

* **эхо `page`.** У соседней ручки той же СРМ (`registrations-contest`) `page` и
  `per_page` ИГНОРИРУЮТСЯ — она всегда отдаёт полный список. Если эта ручка
  однажды станет вести себя так же, мы бы крутили `last_page` страниц, получая
  одну и ту же первую, и «увидели» бы 30 тысяч лидов, из которых в базу легли бы
  500 (остальные схлопнул бы первичный ключ `op_funnel_leads`). Поэтому номер
  страницы в ответе сверяется с запрошенным.
* **эхо `per_page`.** Признак «страница короче размера» считается от ЭХА ответа,
  а не от нашего 500: ручка, вернувшаяся к дефолтным 100, иначе выглядела бы как
  «последняя страница», и две трети лидов пропали бы молча.
* **потолок `MAX_PAGES`.** Он не «на всякий случай»: упёрлись в него — значит
  либо пагинация зациклилась, либо период просят неподъёмный. И то и другое
  обрывается исключением, а не обрезанным результатом; обрезанный результат — то
  же самое молчаливое занижение, что и пустой ответ.

Обрывы посреди пагинации
------------------------
Тот же класс отказа уже измерен на amoCRM: 5 прогонов из 39 за 06–10.08.2026
упали обрывом соединения примерно на десятой странице из 86. 60 страниц по
партнёрской ручке живут столько же, поэтому повтор с ЛИНЕЙНЫМ бэкоффом
(2, 4 секунды) есть, и в него намеренно входит таймаут — в отличие от
`amo_leads._request`, который таймаут не повторяет. Разница в том, кто ждёт:
там запрос идёт под открытой вкладкой человека, здесь — ночная джоба, и потеря
всей выгрузки направления из-за одной задумавшейся страницы дороже лишней
минуты ожидания.

Паузы между страницами нет: лимита частоты партнёрских ручек нам не объявляли, а
двухсекундная пауза добавила бы к ночному прогону «Потока» две минуты на каждый
поток. Если лимит появится, он приедет как 429 — и попадёт в тот же бэкофф.
"""

import logging
import os
import re
import time
from datetime import date, datetime
from urllib.parse import urlsplit, urlunsplit

import requests

import reg_contest

log = logging.getLogger(__name__)

# Адрес не секретный и один на все партнёрские ручки — держим значением по
# умолчанию, как в `front_office_calls` и `parcels/drivers`, чтобы не заводить на
# Render переменную, которую всё равно никто не будет менять.
DEFAULT_BASE_URL = 'https://backend.yataxi.kz'
BASE_URL_ENV = 'CRM_PARTNERS_BASE_URL'

STREAM_LEADS_PATH = '/api/partners/stream-leads'
PAID_HIRE_LEADS_PATH = '/api/partners/paid-hire-leads'
LIST_TICKETS_PATH = '/api/partners/list-tickets'

# 60 секунд, а не 15 как у карточки водителя в «Посылках»: там одна карточка и
# менеджер ждёт у формы, здесь страница на 500 лидов и фоновая джоба.
DEFAULT_TIMEOUT = 60

MAX_RETRIES = 3
RETRY_PAUSE = 2.0

# Максимум ручки. Больше 500 не проверено и, судя по 422 на прочих кривых
# параметрах, просто не будет принято.
PAGE_SIZE = 500

# У обращений своя пагинация и свой потолок страницы (1000 против 500 у воронок).
TICKETS_PAGE_SIZE = 1000
# Столько обращений за период считаем разумным пределом одной выгрузки. Месяц
# направления — это сотни строк, а не сотни тысяч: больше похоже на то, что
# периметр токена расширили на весь холдинг, и такую выгрузку лучше остановить.
MAX_TICKETS = 200000

# Поля обращения, которые нужны воронке. Просить всё незачем: в полном ответе 23
# колонки, включая текст обращения целиком.
#   assignee        кто ОБРАБОТАЛ — по нему и считаются «Тикеты» оператора
#   created_by      кто завёл (сейчас это всегда «ИИ Агент», см. fetch_tickets)
#   time_to_process время обработки в секундах; time_total — весь жизненный цикл
#                   обращения (медиана 48 часов), и путать их нельзя
TICKET_SLUGS = (
    'ticket_id', 'created_date', 'created_time', 'created_by', 'assignee',
    'status', 'closed_date', 'closed_by',
    'time_to_accept', 'time_to_process', 'time_to_close', 'time_total',
    'request_from', 'park', 'city', 'request_topic', 'query_topic',
)

# 400 страниц по 500 — это 200 000 лидов, в 6,6 раза больше самого тяжёлого
# замеренного потока за шесть недель, тогда как ночная джоба ходит за 1–3 суток.
# Упёрлись в потолок — это не «много данных», это авария (см. шапку модуля).
MAX_PAGES = 400

# Номера потоков = группы портала: 1 — группа 14, 2 — группа 38 «Айткалиев Айдын
# - Поток 2». Третьего потока в СРМ нет, и выдумывать его нельзя: лиды уехали бы
# в чужую группу и в чужую зарплату.
STREAM_TYPES = (1, 2)

# Ровно ГГГГ-ММ-ДД. ДД.ММ.ГГГГ эта СРМ отвечает HTML с кодом 200 (замерено на
# `region-call-stats` 17.08.2026), поэтому формат ловим до похода в сеть.
_DAY_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')

# Коды, после которых повтор имеет смысл. 429 сюда включён на будущее: лимита нам
# не объявляли, но если объявят, ночная джоба должна пережить его сама.
_RETRY_STATUSES = frozenset((429,))


def _origin_of(value):
    """Схема+хост из полного адреса ручки. Пустая строка, если разобрать нечем."""
    try:
        parts = urlsplit(str(value or '').strip())
    except ValueError:
        return ''
    if parts.scheme in ('http', 'https') and parts.netloc:
        return urlunsplit((parts.scheme, parts.netloc, '', '', ''))
    return ''


def get_config(env_file='.env.codex.local'):
    """Адрес базы СРМ + токен. Возвращает {'base', 'token'}.

    Токен разрешает `reg_contest.get_config` — своего резолвера у раздела нет
    (см. шапку модуля). Хост берётся в таком порядке:

      1. `CRM_PARTNERS_BASE_URL` — если СРМ когда-нибудь переедет;
      2. хост из уже заведённого `CRM_API_URL` — чтобы перевод конкурса на
         стенд не оставил «Воронку ОП» смотреть в прод: это один и тот же
         backend, и разъехаться они не должны;
      3. значение по умолчанию.
    """
    crm = reg_contest.get_config(env_file) or {}
    base = os.getenv(BASE_URL_ENV) or _origin_of(crm.get('url')) or DEFAULT_BASE_URL
    return {'base': str(base).rstrip('/'), 'token': (crm.get('token') or '').strip()}


def is_configured(config=None):
    """Есть ли чем ходить. Нет токена — роут отвечает 503 с текстом, а не 500."""
    cfg = config or get_config()
    return bool(cfg.get('base') and cfg.get('token'))


def _iso_day(value, field):
    """`date`/`datetime`/строка → 'ГГГГ-ММ-ДД'. Иначе ValueError по-русски.

    `datetime` проверяется первым: он наследник `date`, и порядок здесь значим.
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or '').strip()
    if not _DAY_RE.match(text):
        raise ValueError('%s: ожидается дата ГГГГ-ММ-ДД, пришло «%s»' % (field, value))
    return text


def _period_body(day_from, day_to):
    """Тело `{from, to}` с проверкой до сети.

    Границы сравниваются строками: у ГГГГ-ММ-ДД лексикографический порядок
    совпадает с календарным, а разбирать дату обратно в `date` только ради
    сравнения незачем. Перевёрнутый период — это HTML-200 от СРМ, то есть
    невнятная ошибка разбора вместо внятной ошибки аргумента.
    """
    start = _iso_day(day_from, 'начало периода')
    end = _iso_day(day_to, 'конец периода')
    if start > end:
        raise ValueError(
            'Период перевёрнут: начало %s позже конца %s — СРМ на таком отдаёт '
            'HTML вместо данных' % (start, end))
    return {'from': start, 'to': end}


def _positive_int(value):
    """Целое больше нуля или None. Строку '3' принимаем: СРМ шлёт числа и так, и так."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _snippet(text, limit=200):
    """Начало ответа одной строкой для текста ошибки.

    HTML-страница Laravel — это 40 КБ вёрстки с переводами строк; в таком виде
    она не читается ни в логе Render, ни в поле `error` журнала прогонов.
    """
    return ' '.join(str(text or '').split())[:limit]


class CrmFunnelClient:
    """POST к двум партнёрским ручкам. Ничего не разбирает и ничего не пишет.

    Исключения — `RuntimeError` с русским текстом (и `ValueError` на кривых
    аргументах), как у `reg_contest.RegContestClient` и
    `front_office_calls.RegionCallStatsClient`. Своего класса ошибки у клиента
    нет намеренно: `sync.sync_direction` ловит отказ источника целиком и
    складывает `str(exc)` в `op_funnel_sync_runs.error` — различать сорта отказа
    ему незачем, а текст он показывает человеку как есть.
    """

    def __init__(self, base, token, timeout=DEFAULT_TIMEOUT, retries=MAX_RETRIES,
                 session=None, page_size=PAGE_SIZE):
        if not base or not token:
            raise ValueError(
                'Не задан адрес СРМ или токен X-Integration-Token — '
                'проверьте CRM_API_URL и X-Integration-Token')
        self.base = str(base).rstrip('/')
        self.token = str(token)
        self.timeout = timeout
        self.retries = max(int(retries or 1), 1)
        # Своя сессия: keep-alive экономит TLS-рукопожатие на каждой из 60
        # страниц. Она же — единственная точка подмены в тестах, поэтому
        # приходит аргументом, а не создаётся молча внутри.
        self.session = session or requests.Session()
        self.page_size = max(1, min(int(page_size or PAGE_SIZE), PAGE_SIZE))

    @classmethod
    def from_config(cls, config=None, timeout=DEFAULT_TIMEOUT, retries=MAX_RETRIES,
                    session=None):
        cfg = config or get_config()
        return cls(cfg.get('base'), cfg.get('token'), timeout=timeout,
                   retries=retries, session=session)

    # ── транспорт ───────────────────────────────────────────────────────────

    def _post(self, path, payload):
        """Один запрос с линейным бэкоффом. Возвращает разобранный объект ответа."""
        url = self.base + path
        headers = {
            'X-Integration-Token': self.token,
            'Content-Type': 'application/json',
        }
        last_error = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self.session.post(url, json=payload, headers=headers,
                                             timeout=self.timeout)
            except requests.RequestException as exc:
                # Сюда попадают и обрыв соединения, и таймаут — оба от повтора
                # могут измениться (см. шапку модуля).
                last_error = exc
                log.warning('op_funnel/crm: %s, попытка %s/%s оборвалась: %s',
                            path, attempt, self.retries, exc)
                if attempt < self.retries:
                    time.sleep(RETRY_PAUSE * attempt)
                continue

            status = int(getattr(response, 'status_code', 0) or 0)
            if status >= 500 or status in _RETRY_STATUSES:
                last_error = 'HTTP %s: %s' % (status, _snippet(response.text, 120))
                log.warning('op_funnel/crm: %s ответил %s, попытка %s/%s',
                            path, status, attempt, self.retries)
                if attempt < self.retries:
                    time.sleep(RETRY_PAUSE * attempt)
                continue

            # Дальше — отказы, которые повтор не исправит. Каждый назван своим
            # текстом: «почему цифры не обновились» должно отвечаться из журнала
            # прогонов, без чтения логов Render.
            if status == 401:
                raise RuntimeError(
                    'СРМ не приняла X-Integration-Token (401) — токен отозван или '
                    'заведён не тот')
            if status == 403:
                raise RuntimeError(
                    'СРМ закрыла доступ к %s (403) — токену не выдали эту ручку' % path)
            if status == 422:
                raise RuntimeError(
                    'СРМ отвергла параметры %s (422): %s'
                    % (path, _snippet(response.text, 300)))
            if status != 200:
                raise RuntimeError(
                    'СРМ %s ответила HTTP %s: %s'
                    % (path, status, _snippet(response.text, 300)))
            return self._decode(path, response)

        raise RuntimeError('СРМ %s недоступна после %s попыток: %s'
                           % (path, self.retries, last_error))

    def _decode(self, path, response):
        """Тело 200 → словарь. Здесь ловится HTML-200 и на этом всё держится."""
        # `headers` берём через getattr: дубль ответа в тестах проекта
        # (`_FakeResponse`) заголовков не имеет, и требовать их значило бы
        # переписывать дубль ради проверки, которая и так делается по телу.
        content_type = str((getattr(response, 'headers', None) or {}).get('Content-Type') or '')
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError(
                'СРМ %s отдала не JSON (Content-Type: %s) — похоже на HTML-страницу '
                'Laravel на кривом запросе. Начало ответа: %s'
                % (path, content_type or 'не указан', _snippet(response.text)))
        if not isinstance(data, dict):
            raise RuntimeError('СРМ %s отдала %s вместо объекта: %s'
                               % (path, type(data).__name__, _snippet(data)))
        return data

    # ── пагинация ───────────────────────────────────────────────────────────

    def _paginate(self, path, body, label, expect_stream=None):
        """Генератор сырых лидов: страница за страницей до `last_page`.

        Лид отдаётся как пришёл — ни одного переименованного поля. Дубли между
        страницами (лид переехал на соседнюю, потому что СРМ дописала новые
        строки прямо во время обхода) клиент НЕ вычищает: их снимает первичный
        ключ `op_funnel_leads (direction_code, source, stream_type, lead_key)` при
        вставке, а держать в памяти множество из 30 тысяч ключей ради того же
        результата смысла нет.
        """
        page = 1
        seen = 0
        total = None
        finished = False
        while page <= MAX_PAGES:
            payload = dict(body, page=page, per_page=self.page_size)
            data = self._post(path, payload)

            leads = data.get('leads')
            if not isinstance(leads, list):
                raise RuntimeError(
                    'СРМ (%s) отдала незнакомый формат: нет списка leads, '
                    'ключи ответа = %s' % (label, sorted(data)[:10]))

            echoed_page = _positive_int(data.get('page'))
            if echoed_page is not None and echoed_page != page:
                raise RuntimeError(
                    'СРМ (%s) игнорирует пагинацию: просили страницу %s, вернула %s. '
                    'Выгрузка остановлена, чтобы не записать одну страницу как весь '
                    'период' % (label, page, echoed_page))

            if expect_stream is not None:
                echoed_stream = _positive_int(data.get('stream_type'))
                if echoed_stream is not None and echoed_stream != expect_stream:
                    # Лиды чужого потока — это чужая группа (14 против 38) и чужие
                    # цифры в чужой зарплате. Молча принять такое нельзя.
                    raise RuntimeError(
                        'СРМ (%s) вернула лиды потока %s вместо %s'
                        % (label, echoed_stream, expect_stream))

            for lead in leads:
                if not isinstance(lead, dict):
                    raise RuntimeError('СРМ (%s) отдала лид не объектом, а %s'
                                       % (label, type(lead).__name__))
                if not str(lead.get('lead_id') or '').strip():
                    # `lead_id` — часть первичного ключа снимка. Лид без него не
                    # «лид с пропуском», а 500 лидов, схлопнутых в одну строку.
                    raise RuntimeError('СРМ (%s) отдала лид без lead_id, поля = %s'
                                       % (label, sorted(lead)[:12]))
                yield lead

            seen += len(leads)
            if total is None:
                total = _positive_int(data.get('total'))

            last_page = _positive_int(data.get('last_page'))
            # Размер страницы — из ЭХА ответа: ручка, вернувшаяся к дефолтным 100,
            # иначе выглядела бы как «последняя страница» (см. шапку модуля).
            page_size = _positive_int(data.get('per_page')) or self.page_size

            if last_page is None:
                # Контракт сменился и `last_page` пропал — не повод падать: идём,
                # пока страницы полные. Потолок ниже всё равно не даст крутиться.
                if len(leads) < page_size:
                    finished = True
                    break
            elif page >= last_page:
                finished = True
                break
            elif not leads:
                # `last_page` обещает продолжение, а страница пустая. Так выглядит
                # период, у которого лиды удалили между запросами; крутить дальше
                # нечего, но в логе это должно быть видно.
                log.warning('op_funnel/crm: %s — страница %s из %s пустая, останавливаемся',
                            label, page, last_page)
                finished = True
                break
            page += 1

        if not finished:
            raise RuntimeError(
                'СРМ (%s): уперлись в потолок %s страниц по %s лидов (получено %s). '
                'Либо ручка зациклила пагинацию, либо период слишком большой — '
                'качайте его частями' % (label, MAX_PAGES, self.page_size, seen))

        if total is not None and total != seen:
            # Не ошибка: СРМ переписывает прошлое и дописывает лиды прямо во время
            # обхода (замерено 11.09.2026 на снимках супервайзера). Но расхождение
            # обязано быть видимым — иначе «почему вчера было 1125, а сегодня 1130»
            # не на чем разбирать.
            log.info('op_funnel/crm: %s — total %s, а привезли %s (СРМ дописала лиды '
                     'во время обхода)', label, total, seen)
        log.info('op_funnel/crm: %s — %s лидов со %s стр.', label, seen, page)

    # ── ручки ───────────────────────────────────────────────────────────────

    def fetch_stream_leads(self, day_from, day_to, stream_type=1):
        """Лиды «Потока» за период. Генератор сырых словарей источника.

        Аргументы проверяются ЗДЕСЬ, а тело — генератор `_paginate`, поэтому метод
        специально не `yield from`: иначе кривая дата или номер потока вылетели бы
        не на вызове, а на первом `next()` — то есть из середины `sync`, где этот
        отказ выглядел бы как отказ источника.

        Второе следствие лени генератора, о котором должен знать `sync`: ошибка
        может прийти после того, как часть лидов уже отдана. Обход обязан быть
        внутри `try`, а прогон с такой ошибкой — считаться невыгруженным целиком:
        оборванный период — это занижение, а не «чуть меньше лидов».
        """
        stream = _positive_int(stream_type)
        if stream not in STREAM_TYPES:
            raise ValueError(
                'stream_type должен быть 1 или 2 (группы 14 и 38), пришло «%s»'
                % (stream_type,))
        body = _period_body(day_from, day_to)
        body['stream_type'] = stream
        return self._paginate(STREAM_LEADS_PATH, body,
                              'stream-leads, поток %s, %s..%s' % (stream, body['from'],
                                                                  body['to']),
                              expect_stream=stream)

    def fetch_tickets(self, day_from, day_to, slugs=None):
        """Обращения («тикеты») за период. Генератор сырых словарей.

        Ручка заведена по нашей заявке 11.09.2026 и устроена ИНАЧЕ, чем воронки:
        страницы не `page`/`last_page`, а `limit`/`offset` с общим `total`,
        потолок страницы 1000. Поэтому свой обход, а не `_paginate`.

        `slugs` — какие поля вернуть. Просить всё дороже, чем кажется: в полном
        ответе 23 колонки, включая комментарий обращения целиком. Неизвестные
        имена ручка не роняет, а перечисляет в `unknown_slugs` — мы их логируем,
        потому что молча потерянное поле означает пустую колонку в отчёте.

        **Что важно знать про периметр.** Видны только обращения, заведённые под
        учётными записями партнёра. На 11.09.2026 в этот набор входит одна
        учётка: за 01.06–11.09 пришло 215 обращений, и у ВСЕХ `created_by` =
        «ИИ Агент». Тикеты, которые верификаторы заводят сами, сюда пока не
        попадают, хотя их имена уже видны в `assignee`. То есть автоматический
        счёт заработает в полном объёме, когда в периметр токена добавят учётки
        направления; до тех пор числа занижены, и раздел это показывает.
        """
        body = _period_body(day_from, day_to)
        wanted = [str(item).strip() for item in (slugs or []) if str(item or '').strip()]
        if wanted:
            body['slugs'] = wanted

        offset = 0
        seen = 0
        total = None
        while offset <= MAX_TICKETS:
            payload = dict(body, limit=TICKETS_PAGE_SIZE, offset=offset)
            data = self._post(LIST_TICKETS_PATH, payload)

            rows = data.get('data')
            if not isinstance(rows, list):
                raise RuntimeError(
                    'СРМ (list-tickets) отдала незнакомый формат: нет списка data, '
                    'ключи ответа = %s' % (sorted(data)[:10],))

            unknown = data.get('unknown_slugs') or []
            if unknown and offset == 0:
                log.warning('op_funnel: СРМ не знает поля обращений %s — '
                            'в отчёте они останутся пустыми', unknown)

            if total is None:
                # None, а не 0: без `total` останавливаться надо по КОРОТКОЙ
                # странице, иначе «нет поля total» читалось бы как «обращений
                # ноль» и выгрузка обрывалась бы на первой же странице, отдав
                # тысячу строк из десяти тысяч.
                total = _positive_int(data.get('total'))

            for row in rows:
                if isinstance(row, dict):
                    yield row
                    seen += 1

            if not rows or len(rows) < TICKETS_PAGE_SIZE:
                break
            if total is not None and seen >= total:
                break
            # Двигаемся на фактически полученное, а не на TICKETS_PAGE_SIZE:
            # ручка вправе отдать меньше, и шаг «по запрошенному» пропустил бы
            # хвост страницы.
            offset += len(rows)
        else:
            raise RuntimeError(
                'СРМ (list-tickets) за период %s..%s отдала больше %s обращений — '
                'выгрузка остановлена, чтобы не съесть память; сузьте период'
                % (body['from'], body['to'], MAX_TICKETS))

        if total is not None and seen != total:
            # Не ошибка: СРМ дописывает обращения во время обхода.
            log.info('op_funnel: обращений привезли %s, СРМ обещала %s', seen, total)

    def fetch_paid_hire_leads(self, day_from, day_to):
        """Лиды платного найма («Яндекс Регистрация») за период. Генератор словарей.

        Период у этой ручки считается по `driver_registered_at`, а не по дате
        взятия лида в работу, — но это знание `sources.py`, клиент только передаёт
        границы. Проверка аргументов, как и у потока, происходит до сети.
        """
        body = _period_body(day_from, day_to)
        return self._paginate(PAID_HIRE_LEADS_PATH, body,
                              'paid-hire-leads, %s..%s' % (body['from'], body['to']))
