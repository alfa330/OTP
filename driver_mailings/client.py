"""Клиент кабинета Fleet для рассылок водителям (задача #166).

ПОЧЕМУ ПОДКЛАСС, А НЕ СВОЙ HTTP. Сессия у портала одна на всех: те же куки
живого логина из `fleet_edm_session`, которые обновляет раздел «Провайдер ЭДО».
Значит и обращаться к кабинету надо тем же способом — с тем же набором
заголовков, тем же распознаванием «разлогинило» (200 с HTML страницы входа) и
той же реакцией на 429. Дублировать это здесь означало бы завести вторую правду
о кабинете, которая начнёт расходиться с первой при первом же изменении на
стороне Яндекса. Поэтому весь транспорт — из `fleet_edm.client.FleetClient`, а
здесь только пути и разбор ответов рассылочных ручек.

ЧТО ПРО ЭТИ РУЧКИ ИЗВЕСТНО (всё проверено живьём 07.09.2026, документации нет):
* Парк выбирается ТОЛЬКО заголовком `x-park-id`, как и везде в кабинете.
* Рассылка разрешена не везде: из 90 диспетчерских аккаунта — в пяти. Остальные
  85 отвечают 403 `no_permissions` на `limits` и на подсчёт получателей. Это
  нормальный ответ, а не сбой связи (см. `mailing_limits`).
* Отправка отвечает 204 БЕЗ ТЕЛА и, следовательно, без идентификатора рассылки.
  Чтобы потом показать прочтения и уметь отозвать, id приходится доставать
  отдельно, сопоставлением по журналу — см. `resolve_mailing_id`.
* Отзыв возможен только 300 секунд после отправки (`delete_limit.seconds` в
  лимитах). Позже кабинет откажет, и это не наша ошибка.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from fleet_edm.client import FleetClient, FleetError, FleetSessionExpired  # noqa: F401

from . import catalog

# ── пути кабинета ───────────────────────────────────────────────────────────
#
# Версии в путях разные и вперемешку (v1 и v2 у соседних ручек одной и той же
# рассылки) — это не опечатка, так их отдаёт сам кабинет. Все проверены живьём
# 07.09.2026 на fleet.yandex.kz.

PATH_LIMITS = '/api/fleet/communications/v2/mailings/limits'
PATH_AVAILABLE_FILTERS = '/api/fleet/communications/v1/available-filters'
PATH_AVAILABLE_GROUPS = '/api/fleet/contractor-profiles-manager/v1/available-groups'
PATH_PROFESSIONS = '/api/fleet/contractor-profiles-manager/v1/existing-professions'
PATH_WORKING_CITIES = '/api/fleet/contractor-profiles-manager/v1/working-cities'
PATH_REFERENCES = '/api/fleet/router/v1/references/list'
PATH_WORK_RULES = '/api/fleet/driver-work-rules/v1/work-rules'
PATH_RECIPIENTS_COUNT = '/api/fleet/fleet-communications/v1/mailing-recipients/count'

# Одна и та же ручка `communications/v2/mailings`: POST — отправить,
# GET с ?id=<uuid> — прочитать одну рассылку.
PATH_MAILINGS = '/api/fleet/communications/v2/mailings'

# А журнал и отзыв живут в v1, причём отзыв — DELETE по тому же адресу.
PATH_MAILINGS_LIST = '/api/fleet/communications/v1/mailings/list'
PATH_MAILING_REVOKE = '/api/fleet/communications/v1/mailings'

# Справочники, которые кабинет отдаёт одним запросом. Больше нам ничего из этой
# ручки не нужно, а лишние имена она встречает 400.
REFERENCE_NAMES = ('car_services', 'driver_order_statuses', 'order_categories')

# Тип рассылки всегда `pro` (приложение Yandex/Yango Pro): WhatsApp у нас
# выключен на стороне Яндекса (`status: mailing_not_enable`), выбора в
# интерфейсе нет и заводить его незачем.
MAILING_TYPE = 'pro'

# Сколько последних рассылок парка смотрим, разыскивая только что отправленную.
# Своя рассылка в этот момент — самая свежая в парке, так что первой страницы
# заведомо хватает; листать журнал ради этого не нужно.
RESOLVE_PAGE_LIMIT = 50

# Допуск на расхождение часов между нами и кабинетом при сопоставлении. Строгое
# «sent_at не раньше начала отправки» ломается от секундного расхождения часов, а
# цена промаха несимметрична: лишняя минута окна почти ничем не грозит (свои
# уже связанные рассылки отсекаются по skip_ids), а не найденный id означает
# запись в журнале без связи с кабинетом и без возможности отозвать.
SINCE_TOLERANCE = timedelta(seconds=60)

# Код отказа в теле ответа. Базовый _request прячет тело неуспешного ответа
# внутрь текста FleetError, а код нам нужен значением — вынимаем его обратно.
_CODE_RE = re.compile(r"['\"]code['\"]\s*:\s*['\"]([A-Za-z0-9_]+)['\"]")


class MailingRefused(FleetError):
    """Кабинет отказался создавать рассылку и назвал причину.

    Отдельный класс, потому что это не сбой связи и не протухшая сессия:
    повторять запрос бессмысленно, а человеку нужно показать конкретную причину
    («слишком много получателей», «превышен лимит за период») — с ней он сам
    решит, что делать. Код кабинета сохраняем как есть: он уходит в
    `driver_mailing_targets.error_code` и по нему потом разбирают инциденты.
    """

    def __init__(self, code, message=''):
        self.code = str(code or '').strip()
        self.message = message or catalog.error_message(self.code)
        super().__init__(self.message)


def _parse_ts(value):
    """Метка времени кабинета в datetime с зоной. Без зоны считаем UTC: кабинет
    отдаёт время со смещением, а голая строка приходит только из наших же
    тестов и из старых записей."""
    text = str(value or '').strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def _refusal_code(text):
    """Код отказа из текста ошибки базового клиента, если он там есть."""
    match = _CODE_RE.search(str(text or ''))
    return match.group(1) if match else ''


class MailingsClient(FleetClient):
    """Рассылочные ручки кабинета поверх общего транспорта FleetClient."""

    # ── доступность и справочники ────────────────────────────────────────────

    def mailing_limits(self, park_id):
        """Лимиты рассылки в парке, либо None, если рассылка здесь не разрешена.

        None — ЭТО НОРМАЛЬНЫЙ ОТВЕТ, а не сбой: из 90 диспетчерских аккаунта
        рассылка разрешена в пяти, остальные 85 отвечают 403 `no_permissions`.
        Поэтому 403 здесь не поднимается наверх ошибкой — иначе обход парков при
        входе в раздел выглядел бы как восемьдесят пять поломок подряд, и
        настоящую поломку в этом шуме никто бы не заметил. Всё остальное
        (401, 500, обрыв связи) по-прежнему кидается: это уже про связь, а не
        про права.

        Возвращает ответ кабинета целиком: `{'pro': {...}, 'whatsapp': {...}}`.
        """
        try:
            return self._request('GET', PATH_LIMITS, park_id=park_id, attempts=3)
        except FleetSessionExpired:
            raise
        except FleetError as error:
            if '403' in str(error):
                return None
            raise

    @staticmethod
    def pro_limits(payload):
        """Ограничения рассылки типа `pro` из ответа `mailing_limits` — плоско,
        под колонки `driver_mailing_parks`.

        Разбор здесь, а не у вызывающего, потому что вложенность ответа
        (`pro.restriction.delete_limit.seconds`) — свойство кабинета, и знать о
        ней должен один модуль. Пустой ответ (парк без прав) даёт выключенные
        лимиты, а не исключение: для кэша парков это законное состояние.
        """
        pro = ((payload or {}).get(MAILING_TYPE)) or {}
        restriction = pro.get('restriction') or {}
        delete_limit = restriction.get('delete_limit') or {}
        time_limit = restriction.get('time_limit') or {}
        return {
            'status': str(pro.get('status') or '').strip(),
            'is_enabled': bool(restriction.get('is_enabled')),
            'max_title': restriction.get('max_title_length'),
            'max_message': restriction.get('max_message_length'),
            'per_day': time_limit.get('day'),
            'revoke_seconds': delete_limit.get('seconds'),
        }

    def available_filters(self, park_id):
        """Сегменты и подсегменты парка — ветка `pro` ответа.

        Подписи подсегментов приходят отсюда живыми, своих не заводим: их набор
        меняет Яндекс, и второй список у нас неминуемо отстал бы.
        """
        payload = self._request('GET', PATH_AVAILABLE_FILTERS, park_id=park_id)
        return dict((payload or {}).get(MAILING_TYPE) or {})

    def available_groups(self, park_id):
        """Машинные ключи групп (бейджей), доступных в этом парке. Русские
        подписи — в catalog.GROUP_LABELS: кабинет переводов не отдаёт."""
        payload = self._request('GET', PATH_AVAILABLE_GROUPS, park_id=park_id)
        return [str(x).strip() for x in ((payload or {}).get('available_groups') or []) if x]

    def professions(self, park_id):
        """Профессии парка: `[{'id': 'taxi/driver', 'name': 'Водитель такси'}]`.

        Ответ приходит голым массивом, но обёрнутый вид тоже принимаем: соседние
        ручки кабинета отдают именно его, и цена страховки — одна строка.
        """
        payload = self._request('GET', PATH_PROFESSIONS, park_id=park_id)
        items = payload if isinstance(payload, list) else ((payload or {}).get('professions') or [])
        return [
            {'id': str(item.get('id') or '').strip(),
             'name': str(item.get('name') or '').strip()}
            for item in items
            if isinstance(item, dict) and str(item.get('id') or '').strip()
        ]

    def working_cities(self, park_id, segment):
        """Города работы для сегмента (`active`, `churn`, `candidates`).

        Сегмент обязателен и едет параметром строки запроса. В фильтр рассылки
        потом уходит НАЗВАНИЕ города, а не идентификатор — см. ловушку в catalog.
        """
        payload = self._request('GET', PATH_WORKING_CITIES, park_id=park_id,
                                params={'segment': str(segment or '').strip()})
        return [
            {'id': str(item.get('id') or '').strip(),
             'name': str(item.get('name') or '').strip()}
            for item in ((payload or {}).get('cities') or [])
            if isinstance(item, dict)
        ]

    def references(self, park_id):
        """Три справочника одним запросом: опции авто, статусы водителя, тарифы.

        Возвращает `{'car_services': [...], 'driver_order_statuses': [...],
        'order_categories': [...]}` — по массиву `{id, name}` на имя. Кабинет
        отдаёт их отдельными полями ответа; обёртку `{'references': [...]}`
        разбираем тоже, чтобы смена формы ответа не уронила весь раздел.

        Чистить справочники здесь нельзя: правила чистки (что выбросить, что
        переименовать) — часть договорённости о фильтре, и живут они в catalog
        рядом с самим фильтром.
        """
        payload = self._request('POST', PATH_REFERENCES, park_id=park_id,
                                body={'references': list(REFERENCE_NAMES)})
        payload = payload or {}
        result = {name: list(payload.get(name) or []) for name in REFERENCE_NAMES}
        for item in (payload.get('references') or []):
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or item.get('id') or '').strip()
            if name in result and not result[name]:
                result[name] = list(item.get('values') or item.get('items') or [])
        return result

    def work_rules(self, park_id):
        """Условия работы парка (неархивные).

        В первой версии раздела фильтр по ним не собирается (решение владельца),
        но ручка нужна журналу: у живых рассылок кабинета в фильтрах встречаются
        `work_rule_ids`, и показать их человеку иначе нечем — в рассылке лежат
        одни uuid.
        """
        payload = self._request('GET', PATH_WORK_RULES, park_id=park_id,
                                params={'is_archived': 'false'})
        return list((payload or {}).get('work_rules') or [])

    # ── охват и отправка ─────────────────────────────────────────────────────

    def recipients_count(self, park_id, filters):
        """Сколько водителей парка попадёт под фильтр.

        Фильтр прогоняется через `catalog.normalize_filters` ещё раз, хотя это
        уже сделано на входе в ручку портала. Дублирование намеренное: кабинет
        на неизвестный ключ отвечает не ошибкой, а ПОЛНЫМ охватом парка, так что
        единственная забытая нормализация превращается не в 400, а в рассылку
        всем. Дешевле пересобрать словарь второй раз.
        """
        body = {
            'type': 'with_filters',
            'mailing_type': MAILING_TYPE,
            'filters': catalog.normalize_filters(filters),
        }
        payload = self._request('POST', PATH_RECIPIENTS_COUNT, park_id=park_id,
                                body=body, attempts=3)
        try:
            return int((payload or {}).get('count') or 0)
        except (TypeError, ValueError):
            return 0

    def send_mailing(self, park_id, *, title, message, filters, idempotency_token):
        """Отправить рассылку в один парк. Ничего не возвращает.

        Удача — это 204 без тела: идентификатора рассылки кабинет не отдаёт
        (его потом разыскивает `resolve_mailing_id`). Отказ — ответ С телом, где
        лежит поле `code`; он поднимается как MailingRefused, потому что
        повторять его бессмысленно, а человеку нужна причина.

        Повторы оставлены (три попытки) осознанно: их страхует
        `x-idempotency-token` — один токен на одну попытку отправки в один парк.
        Именно ради этого кабинет его и принимает: без токена повтор после
        оборванного соединения был бы второй рассылкой тем же людям, а с ним
        кабинет узнаёт запрос и не создаёт дубль.
        """
        body = {
            'type': MAILING_TYPE,
            'title': str(title or ''),
            'message': str(message or ''),
            'recipients': {'filters': catalog.normalize_filters(filters)},
        }
        try:
            payload = self._request(
                'POST', PATH_MAILINGS, park_id=park_id, body=body, attempts=3,
                extra_headers={'x-idempotency-token': str(idempotency_token or '')},
                allow_empty=True,
            )
        except FleetSessionExpired:
            raise
        except FleetError as error:
            code = _refusal_code(error)
            if code:
                raise MailingRefused(code) from error
            raise
        if payload is None:
            return None
        # Тело на «успешном» коде — тоже отказ: кабинет так отвечает на часть
        # ограничений, не меняя статус. Код достаём только из словаря: список
        # или строка в теле означают, что кабинет ответил чем-то незнакомым, и
        # падать на .get() здесь нельзя — вызывающий ждёт либо тишины, либо
        # MailingRefused, а исключение TypeError он разберёт как «ошибка сети»
        # и запишет отправку неотправленной.
        code = str(payload.get('code') or '').strip() if isinstance(payload, dict) else ''
        raise MailingRefused(code)

    # ── журнал кабинета ──────────────────────────────────────────────────────

    def list_mailings(self, park_id, limit=RESOLVE_PAGE_LIMIT, cursor=None):
        """Страница журнала рассылок парка. Возвращает (записи, курсор)."""
        body = {'limit': int(limit or RESOLVE_PAGE_LIMIT)}
        if cursor:
            body['cursor'] = cursor
        payload = self._request('POST', PATH_MAILINGS_LIST, park_id=park_id,
                                body=body, attempts=3) or {}
        return list(payload.get('mailings') or []), payload.get('cursor')

    def mailing_detail(self, park_id, mailing_id):
        """Одна рассылка целиком: шаблон и сводка (в ней — прочтения).

        Идентификатор едет параметром строки запроса, а не в теле: тела эта
        ручка не ждёт.
        """
        return self._request('GET', PATH_MAILINGS, park_id=park_id,
                             params={'id': str(mailing_id)}, attempts=3)

    def revoke_mailing(self, park_id, mailing_id):
        """Отозвать рассылку. Успех — 204 без тела.

        Работает только в течение `delete_limit.seconds` (300 с) после отправки;
        позже кабинет откажет, и это его решение, а не наша ошибка — сообщение
        показываем как есть.
        """
        self._request('DELETE', PATH_MAILING_REVOKE, park_id=park_id,
                      params={'id': str(mailing_id)}, attempts=3, allow_empty=True)
        return None

    def resolve_mailing_id(self, park_id, title, since_iso, skip_ids=None):
        """Найти в журнале кабинета рассылку, которую мы только что отправили.

        Обходной путь, потому что прямого нет: успешная отправка отвечает 204 без
        тела, идентификатора в ней не бывает, а без него нельзя ни показать
        прочтения, ни отозвать. Признаков сопоставления три, и все нужны:

        * `preview == title` — у типа `pro` превью буквально совпадает с
          заголовком (проверено на всех 12 живых рассылках);
        * `sent_at` не раньше начала нашей отправки (с допуском SINCE_TOLERANCE
          на расхождение часов) — иначе подберём прошлогоднюю рассылку с тем же
          заголовком, а «Отозвать» ударит по чужой;
        * идентификатор не занят другой нашей записью (`skip_ids`) — иначе при
          двух одинаковых рассылках подряд обе сошлись бы на одном id.

        Не нашли — возвращаем None. Это законный исход: журнал честно скажет, что
        связать с кабинетом не удалось, и отзыв для такой строки будет недоступен.
        Выдумывать связь опаснее, чем её не иметь.
        """
        wanted = str(title or '').strip()
        since = _parse_ts(since_iso)
        skip = {str(x) for x in (skip_ids or []) if x}
        try:
            items, _ = self.list_mailings(park_id, limit=RESOLVE_PAGE_LIMIT)
        except FleetError as error:
            # Рассылка уже ушла, и потерять её из-за неудачного поиска id нельзя:
            # отправка обязана считаться удачной. Пишем в лог и возвращаем None.
            logging.warning('Рассылки: не удалось найти id в парке %s (%s)',
                            park_id, error)
            return None

        best_id, best_stamp = None, None
        for item in items:
            if not isinstance(item, dict):
                continue
            ident = str(item.get('id') or item.get('mailing_id') or '').strip()
            if not ident or ident in skip:
                continue
            preview = str(item.get('preview') or item.get('title') or '').strip()
            if preview != wanted:
                continue
            stamp = _parse_ts(item.get('sent_at') or item.get('created_at'))
            if since is not None:
                # Без разобранного времени проверить окно нечем, а брать
                # наугад — значит рискнуть отзывом чужой рассылки.
                if stamp is None or stamp < since - SINCE_TOLERANCE:
                    continue
            if best_id is None:
                # Журнал приходит от свежих к старым, поэтому первое совпадение
                # без разобранного времени — всё же самое свежее из них.
                best_id, best_stamp = ident, stamp
            elif stamp is not None and (best_stamp is None or stamp > best_stamp):
                best_id, best_stamp = ident, stamp
        return best_id
