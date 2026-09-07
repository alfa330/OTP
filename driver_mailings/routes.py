"""HTTP раздела «Рассылки» (задача #166).

Blueprint собирается фабрикой, зависимости приходят аргументами — обратный
импорт из bot_schedule2 был бы циклом (так же устроены вики, «Обращения»,
«Ограничитель Перезвона» и «Провайдер ЭДО»).

ПОЧЕМУ ОТПРАВКА СИНХРОННАЯ, БЕЗ МАШИНЕРИИ ФОНОВЫХ ЗАДАНИЙ.
«Провайдер ЭДО» отвечает 202-м и живёт минутами, потому что обходит 86 парков
по десятку раз. Здесь работы на пять запросов: рассылка разрешена в пяти
диспетчерских из девяноста, и каждая отправка — один POST на 0,3–0,5 секунды.
Заводить ради этого карточку задания, пульс, подхват после деплоя и опрос
прогресса значило бы построить механизм сложнее самой задачи. Синхронный ответ
укладывается в таймаут waitress (120 с) с запасом в два порядка.

ЧТО ЗДЕСЬ ДЕЙСТВИТЕЛЬНО СЛОЖНО — три вещи, и все три про кабинет:

1. **Кабинет не возвращает id отправленной рассылки.** Успех — 204 без тела.
   Без id нельзя ни показать прочтения, ни отозвать, поэтому перед отправкой
   снимается снимок журнала парка, а после — берётся запись, которой в снимке не
   было (client.journal_ids + client.resolve_mailing_id). Искать по времени
   нельзя: у только что созданной рассылки поля `sent_at` в журнале НЕТ вовсе,
   кабинет проставляет его позже. Не нашли — рассылка всё равно считается
   отправленной: она ушла, и делать вид, что нет, было бы враньём.

2. **Опрос девяноста парков дорог.** Узнать, где рассылка разрешена, можно
   только спросив каждый парк. Это 90 запросов, поэтому ответ кэшируется в
   driver_mailing_parks и обновляется раз в сутки либо кнопкой.

3. **Прочтения считает кабинет, и по одной рассылке за раз спрашивать их
   нельзя**: страница журнала на 20 рассылок в пяти парках стоила бы сотню
   запросов. Вместо этого на каждый парк берётся его журнал страницами, пока не
   покроем самую старую рассылку страницы, — обычно это один запрос на парк.

ПОЧЕМУ СЕТЬ НИКОГДА НЕ ДЁРГАЕТСЯ ПОД ОТКРЫТЫМ КУРСОРОМ.
Соединений в пуле немного, а поход в кабинет — это сотни миллисекунд. Поэтому
порядок везде один: короткая транзакция на запись, закрыли, сходили в кабинет,
снова короткая транзакция. Ни одного `with db._get_cursor()` вокруг запроса
наружу.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, jsonify, request

from fleet_edm import queries as fleet_session
from fleet_edm.client import FleetError, FleetSessionExpired

from . import access, catalog, queries
from .client import MailingRefused, MailingsClient

# Сколько живёт список «где разрешена рассылка». Права на рассылку меняет Яндекс,
# и меняет редко: за сутки промахнуться можно разве что в день подключения новой
# диспетчерской, а кнопка «Обновить список» рядом.
PARKS_TTL_SECONDS = 24 * 60 * 60

# Опрос парков и сбор справочников — параллельно, но скромно. Четыре потока —
# стартовое значение самого клиента кабинета (замерено: ~440 запросов в минуту
# при неизменной медиане ответа), выше начинается очередь на их стороне.
SCAN_WORKERS = 4

# Справочники отбора живут в памяти процесса недолго: человек в форме щёлкает
# фильтрами десятки раз, и каждый щелчок стоил бы пяти запросов в кабинет.
# Десять минут — заведомо меньше, чем срок жизни процесса между деплоями, так
# что «протухший навсегда» кэш здесь невозможен.
REFS_TTL_SECONDS = 600

# Сколько страниц журнала кабинета готовы пролистать в одном парке, добирая
# прочтения. Страница — 50 рассылок; четыре страницы это две сотни, глубже
# нашей собственной страницы журнала не бывает.
STATS_MAX_PAGES = 4
STATS_PAGE_LIMIT = 50

# Насколько шире окна поиска смотрим «уже занятые» идентификаторы. Кабинет не
# возвращает id отправленной рассылки, и мы ищем её в журнале по заголовку. Если
# в этот же парк сегодня уже уходила рассылка с тем же заголовком, её id обязан
# попасть в «занятые» — иначе вторая отправка заберёт себе первую, и «Отозвать»
# ударит по чужой. Сутки — с запасом: за сутки в парк разрешено 30 рассылок.
CLAIM_LOOKBACK = timedelta(days=1)

# Потолок страницы журнала — чтобы один запрос не потянул за собой обход всего
# кабинета.
JOURNAL_MAX_LIMIT = 100

# Насколько далеко может разойтись наше время отправки и время записи в кабинете
# при «лечении» строк без связи. Кабинет проставляет `sent_at` не в момент
# создания рассылки, а когда действительно разошлёт, и на большом парке это
# минуты; отозванная запись вместо `sent_at` показывает `deleted_at`, который
# ещё позже. Час — с запасом на оба случая и всё ещё несравнимо меньше, чем
# промежуток между двумя рассылками с одинаковым заголовком.
HEAL_WINDOW_SECONDS = 3600


def _parse_stamp(value):
    """Время из базы или из ответа кабинета — в единый datetime с зоной.

    Кабинет отдаёт строку ISO («2026-09-04T12:57:13.770911+00:00»), база —
    готовый datetime. Сравнивать их строковыми видами нельзя: у одного зона
    записана, у другого нет, и сравнение молча даёт неверный ответ вместо
    ошибки. Наивное время считаем UTC — иначе сравнение с зоной падает.
    """
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        stamp = value
    else:
        text = str(value).strip().replace('Z', '+00:00')
        try:
            stamp = datetime.fromisoformat(text)
        except ValueError:
            return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _sent_total(detail):
    """Скольким людям рассылка действительно ушла.

    Считаем по целям в статусе `sent`, а не по общему `recipients_estimate`
    карточки: если в одном парке кабинет отказал по суточному лимиту, его
    водители сообщения не получили, и класть их в «ушло» нельзя — по этому
    числу человек решает, отправлять ли ещё раз.
    """
    return sum(int(target.get('recipients_estimate') or 0)
               for target in ((detail or {}).get('targets') or [])
               if target.get('status') == 'sent')


def build_driver_mailings_blueprint(*, db, require_api_key, build_cors_preflight_response,
                                    resolve_requester):
    bp = Blueprint('driver_mailings', __name__, url_prefix='/api/driver_mailings')

    # Кэш справочников: (парки, сегмент) → (когда, что). Живёт в процессе, а не
    # в базе: он производный от кабинета и восстанавливается одним запросом.
    _refs_cache = {}
    _refs_lock = threading.Lock()

    # ── доступ ───────────────────────────────────────────────────────────────

    def section_route(rule, methods=('GET',), sending=False):
        all_methods = tuple(methods) + ('OPTIONS',)

        def decorator(handler):
            @bp.route(rule, methods=list(all_methods), endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, _row, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status
                    with db._get_cursor() as cursor:
                        requester = queries.access_context(cursor, requester_id)
                    if not requester:
                        return jsonify({"error": "Пользователь не найден"}), 404
                    # Гейт здесь, а не в обработчиках: спрятанный пункт меню
                    # доступом не является, раздел открывается и прямым адресом.
                    if not access.can_view_section(requester):
                        return jsonify({"error": "Раздел «Рассылки» вам не открыт",
                                        "code": "MAILINGS_SECTION_CLOSED"}), 403
                    if sending and not access.can_send(requester):
                        return jsonify({"error": "Отправлять рассылки вам не разрешено",
                                        "code": "MAILINGS_READ_ONLY"}), 403
                    return handler(requester_id, requester, *args, **kwargs)
                except FleetSessionExpired as error:
                    # Единственная ошибка, которую чинит не разработчик, а живой
                    # человек в браузере, — поэтому она говорит прямым текстом.
                    logging.warning('Рассылки: сессия кабинета недействительна (%s)', error)
                    return jsonify({
                        "error": "Связь с кабинетом диспетчерской прервалась. "
                                 "Её восстанавливают в разделе «Провайдер ЭДО».",
                        "code": "SESSION_EXPIRED",
                    }), 503
                except FleetError as error:
                    logging.warning('Рассылки: кабинет отказал в %s (%s)', rule, error)
                    return jsonify({"error": "Кабинет диспетчерской ответил ошибкой",
                                    "code": "FLEET_ERROR"}), 502
                except Exception:
                    logging.exception("Рассылки: ошибка в %s", rule)
                    return jsonify({"error": "Внутренняя ошибка"}), 500
            return wrapper
        return decorator

    # ── кабинет ──────────────────────────────────────────────────────────────

    def make_client():
        """Клиент кабинета на общей сессии портала.

        Сессия одна на все разделы и живёт в «Провайдере ЭДО»: учётная запись
        кабинета одна, и второе хранилище тех же кук означало бы две даты
        обновления и молчаливые 401 в половине портала. Обновлять её отсюда
        нельзя — только читать.
        """
        with db._get_cursor() as cursor:
            session = fleet_session.load_session(cursor)
        if not session or not session.get('cookies'):
            raise FleetSessionExpired(
                'Связь с кабинетом диспетчерской не настроена. '
                'Её настраивают в разделе «Провайдер ЭДО».')
        return MailingsClient(session['cookies'], session.get('user_agent'))

    def scan_parks(client):
        """Опросить все диспетчерские аккаунта и понять, где разрешена рассылка.

        Девяносто запросов в четыре потока — около десяти секунд. Делается это
        редко: результат кладётся в кэш на сутки.
        """
        parks = client.parks()
        rows = []

        def probe(park):
            park_id = str(park.get('id') or '')
            row = {
                'park_id': park_id,
                'name': str(park.get('name') or '').strip(),
                'city': str(park.get('city') or '').strip(),
                'is_enabled': False,
                'status': 'no_permissions',
            }
            if not park_id:
                return None
            payload = client.mailing_limits(park_id)
            if payload is None:
                # 403 — у аккаунта нет прав на рассылку в этом парке. Строку всё
                # равно сохраняем: иначе «проверен и запрещён» не отличить от
                # «не проверяли», и раздел не сможет объяснить, почему из 90
                # диспетчерских в списке пять.
                return row
            row.update(client.pro_limits(payload))
            return row

        with ThreadPoolExecutor(max_workers=SCAN_WORKERS,
                                thread_name_prefix='mailings-scan') as pool:
            for row in pool.map(probe, parks):
                if row:
                    rows.append(row)
        return rows

    def refresh_parks(client=None):
        client = client or make_client()
        rows = scan_parks(client)
        with db._get_cursor() as cursor:
            queries.save_parks_cache(cursor, rows)
        return rows

    def parks_for_view(refresh_if_stale=True):
        """Список диспетчерских для интерфейса: из кэша, обновив его при нужде."""
        with db._get_cursor() as cursor:
            rows = queries.parks_cache(cursor)
            age = queries.parks_cache_age(cursor)
        stale = age is None or age > PARKS_TTL_SECONDS
        if stale and refresh_if_stale:
            try:
                refresh_parks()
            except FleetSessionExpired:
                # Кэш есть — покажем его и скажем про связь отдельной строкой.
                # Совсем пустой кэш при мёртвой сессии — законный пустой список.
                if not rows:
                    raise
            with db._get_cursor() as cursor:
                rows = queries.parks_cache(cursor)
        return rows

    def enabled_parks(rows=None):
        rows = rows if rows is not None else parks_for_view()
        return [row for row in rows if row.get('is_enabled')]

    # ── справочники отбора ───────────────────────────────────────────────────

    def collect_refs(client, park_ids, segment):
        """Справочники отбора, объединённые по выбранным диспетчерским.

        Объединение, а не пересечение: рассылка уходит в каждый парк со своим
        фильтром, и значение, которого нет в одном парке, просто никого там не
        найдёт. Пересечение же молча спрятало бы от человека половину категорий
        только потому, что во втором парке их не заводили.
        """
        segments = {}
        groups = set()
        professions = {}
        cities = {}
        categories = {}
        amenities = {}
        statuses = {}

        def gather(park_id):
            out = {}
            try:
                out['filters'] = client.available_filters(park_id)
                out['groups'] = client.available_groups(park_id)
                out['professions'] = client.professions(park_id)
                out['references'] = client.references(park_id)
                if segment in catalog.SEGMENTS_WITH_CITY:
                    out['cities'] = client.working_cities(park_id, segment)
            except FleetSessionExpired:
                raise
            except FleetError as error:
                # Один недоступный парк не должен лишать человека справочников
                # остальных: он увидит меньше значений, но форма будет рабочей.
                logging.warning('Рассылки: справочники парка %s недоступны (%s)', park_id, error)
            return out

        with ThreadPoolExecutor(max_workers=SCAN_WORKERS,
                                thread_name_prefix='mailings-refs') as pool:
            collected = list(pool.map(gather, park_ids))

        for out in collected:
            tree = ((out.get('filters') or {}).get('segments_and_subsegments')) or {}
            for key, value in tree.items():
                node = segments.setdefault(key, {
                    'id': key,
                    'name': catalog.SEGMENT_LABELS.get(key, key),
                    'subsegments': {},
                })
                for sub in (value or {}).get('subsegments') or []:
                    sub_id = str(sub.get('id') or '').strip()
                    if sub_id:
                        node['subsegments'][sub_id] = str(sub.get('name') or sub_id).strip()
            groups.update(out.get('groups') or [])
            for item in out.get('professions') or []:
                professions[item['id']] = item['name']
            refs = out.get('references') or {}
            for item in catalog.clean_categories(refs.get('order_categories')):
                categories[item['id']] = item['name']
            for item in catalog.clean_amenities(refs.get('car_services')):
                amenities[item['id']] = item['name']
            for item in catalog.clean_statuses(refs.get('driver_order_statuses')):
                statuses[item['id']] = item['name']
            for item in out.get('cities') or []:
                cities[item['id']] = item['name']

        def listed(mapping):
            return [{'id': key, 'name': value}
                    for key, value in sorted(mapping.items(), key=lambda pair: pair[1])]

        ordered_segments = [
            {
                'id': key,
                'name': segments[key]['name'],
                'subsegments': [{'id': sub, 'name': name}
                                for sub, name in segments[key]['subsegments'].items()],
            }
            for key in catalog.SEGMENTS if key in segments
        ]

        return {
            'segments': ordered_segments,
            'groups': catalog.groups_for(groups),
            'professions': listed(professions),
            'statuses': catalog.clean_statuses(
                [{'id': key, 'name': value} for key, value in statuses.items()]),
            'categories': listed(categories),
            'amenities': listed(amenities),
            'cities': listed(cities),
        }

    def refs_for(park_ids, segment):
        key = (','.join(sorted(park_ids)), segment or '')
        now = time.time()
        with _refs_lock:
            cached = _refs_cache.get(key)
            if cached and now - cached[0] < REFS_TTL_SECONDS:
                return cached[1]
        value = collect_refs(make_client(), park_ids, segment)
        with _refs_lock:
            _refs_cache[key] = (now, value)
            # Ключей мало (наборы парков наперечёт), но чистим, чтобы кэш не рос
            # бесконечно при переборе комбинаций.
            if len(_refs_cache) > 64:
                oldest = sorted(_refs_cache.items(), key=lambda pair: pair[1][0])[:32]
                for stale_key, _ in oldest:
                    _refs_cache.pop(stale_key, None)
        return value

    # ── прочтения из кабинета ────────────────────────────────────────────────

    def attach_read_stats(items):
        """Дописать целям прочтения из журнала кабинета.

        Спрашиваем не по одной рассылке, а страницами журнала на каждый парк:
        двадцать рассылок в пяти парках — это сто обращений по одной и пять-шесть
        страницами. Идём вглубь, пока не покроем самую старую рассылку страницы.

        Ошибка здесь НЕ роняет журнал: прочтения — приятная добавка, а список
        отправленного человек должен видеть всегда, в том числе когда связь с
        кабинетом оборвалась.
        """
        wanted = {}
        oldest = {}
        # Цели, у которых связи с кабинетом нет: их будем лечить по заголовку.
        unlinked = {}
        for item in items:
            for target in item.get('targets') or []:
                park_id = target.get('park_id')
                if not park_id:
                    continue
                stamp = _parse_stamp(target.get('sent_at'))
                ident = target.get('fleet_mailing_id')
                if not ident:
                    if target.get('status') == 'sent' and stamp:
                        unlinked.setdefault(park_id, []).append((item, target, stamp))
                        if park_id not in oldest or stamp < oldest[park_id]:
                            oldest[park_id] = stamp
                    continue
                wanted.setdefault(park_id, set()).add(str(ident))
                if stamp and (park_id not in oldest or stamp < oldest[park_id]):
                    oldest[park_id] = stamp
        if not wanted and not unlinked:
            return items
        for park_id in unlinked:
            wanted.setdefault(park_id, set())

        try:
            client = make_client()
        except FleetError as error:
            logging.warning('Рассылки: прочтения не добраны (%s)', error)
            return items

        def collect(park_id):
            found = {}
            seen = []
            needed = set(wanted[park_id])
            heal = bool(unlinked.get(park_id))
            edge = oldest.get(park_id)
            cursor = None
            for _ in range(STATS_MAX_PAGES):
                try:
                    page, cursor = client.list_mailings(
                        park_id, limit=STATS_PAGE_LIMIT, cursor=cursor)
                except FleetError as error:
                    logging.warning('Рассылки: журнал парка %s недоступен (%s)', park_id, error)
                    break
                for row in page:
                    ident = str(row.get('id') or '')
                    if heal:
                        seen.append(row)
                    if ident in needed:
                        found[ident] = row
                        needed.discard(ident)
                if (not needed and not heal) or not cursor or not page:
                    break
                # Дошли до рассылок старше самой старой нашей — дальше искать
                # нечего. Сравниваем РАЗОБРАННОЕ время: кабинет отдаёт
                # «2026-09-04T12:57:13.770911+00:00», а у нас в базе datetime, и
                # сравнение их строковых видов не срабатывало никогда — цикл
                # каждый раз выбирал все четыре страницы.
                last = _parse_stamp(page[-1].get('sent_at') or page[-1].get('deleted_at'))
                if edge is not None and last is not None and last < edge:
                    break
            return park_id, found, seen

        stats = {}
        pages = {}
        with ThreadPoolExecutor(max_workers=SCAN_WORKERS,
                                thread_name_prefix='mailings-stats') as pool:
            for park_id, found, seen in pool.map(collect, list(wanted)):
                stats[park_id] = found
                pages[park_id] = seen

        # Лечим записи без связи с кабинетом. Такие остались от первой версии,
        # которая искала id по времени и потому не находила его никогда: у только
        # что созданной рассылки кабинет `sent_at` ещё не проставил. Раз журнал
        # парка уже выкачан, найти пропажу по заголовку стоит ноль запросов.
        #
        # Совпадения заголовка мало: тем же заголовком мог отправить человек
        # прямо из кабинета. Поэтому берём только запись, время которой (отправки
        # либо отзыва) рядом с нашим, и только не занятую другой нашей целью.
        for park_id, rows in unlinked.items():
            journal_rows = pages.get(park_id) or []
            taken = set(wanted.get(park_id) or set())
            for item, target, stamp in rows:
                title = str(item.get('title') or '').strip()
                for row in journal_rows:
                    ident = str(row.get('id') or '')
                    if not ident or ident in taken:
                        continue
                    if str(row.get('preview') or '').strip() != title:
                        continue
                    row_stamp = _parse_stamp(row.get('sent_at') or row.get('deleted_at'))
                    if row_stamp is not None and abs((row_stamp - stamp).total_seconds()) > HEAL_WINDOW_SECONDS:
                        continue
                    target['fleet_mailing_id'] = ident
                    stats.setdefault(park_id, {})[ident] = row
                    taken.add(ident)
                    with db._get_cursor() as cursor:
                        queries.mark_target_sent(cursor, item['id'], park_id,
                                                 fleet_mailing_id=ident)
                    logging.info('Рассылки: связали запись %s (парк %s) с рассылкой %s',
                                 item['id'], park_id, ident)
                    break

        revoked_outside = []
        for item in items:
            sent_total = 0
            read_total = 0
            for target in item.get('targets') or []:
                row = stats.get(target.get('park_id'), {}).get(str(target.get('fleet_mailing_id') or ''))
                if not row:
                    continue
                target['sent_to_number'] = row.get('sent_to_number')
                target['read_by_number'] = row.get('read_by_number')
                target['read_percent'] = row.get('read_percent')
                target['fleet_status'] = row.get('status')
                target['deleted_at'] = row.get('deleted_at')
                # Отозвать рассылку можно и мимо портала — прямо в кабинете, и
                # так уже делали. Наш журнал обязан это показывать, иначе он
                # утверждает «Ушла» про сообщение, которого у водителей нет.
                #
                # Записываем В БАЗУ, а не только в ответ: иначе при недоступном
                # кабинете журнал снова показал бы «Ушла», и сводный статус
                # карточки навсегда расходился бы со строками внутри неё.
                if str(row.get('status') or '').startswith('deleted_') \
                        and target.get('status') == 'sent':
                    target['status'] = 'revoked'
                    target['revoked_in_cabinet'] = True
                    revoked_outside.append((item, target.get('park_id')))
                sent_total += int(row.get('sent_to_number') or 0)
                read_total += int(row.get('read_by_number') or 0)
            item['sent_total'] = sent_total
            item['read_total'] = read_total

        # Отзывы, сделанные мимо портала, доносим до базы и пересчитываем сводный
        # статус карточки. Без этого в списке стояло бы «Отправлена», а внутри
        # карточки — «Отозвана» по каждой диспетчерской: одна запись, два разных
        # ответа на один вопрос.
        for item, park_id in revoked_outside:
            with db._get_cursor() as cursor:
                queries.mark_target_revoked(cursor, item['id'], park_id)
                item['status'] = queries.finish_mailing(cursor, item['id'])

        return items

    # ── ручки ────────────────────────────────────────────────────────────────

    @section_route('/overview')
    def driver_mailings_overview(requester_id, requester):
        rows = parks_for_view()
        with db._get_cursor() as cursor:
            session = fleet_session.session_status(cursor)
            template_rows = queries.templates(cursor)
        allowed = enabled_parks(rows)
        # Пределы берём из первой разрешённой диспетчерской: они одинаковы во
        # всех (проверено), а разными быть в принципе могут — тогда честнее
        # показывать самый строгий, чтобы текст прошёл везде.
        limits = {
            'max_title': min([row.get('max_title') or 120 for row in allowed] or [120]),
            'max_message': min([row.get('max_message') or 1500 for row in allowed] or [1500]),
            'revoke_seconds': min([row.get('revoke_seconds') or 300 for row in allowed] or [300]),
            'per_day': min([row.get('per_day') or 30 for row in allowed] or [30]),
        }
        return jsonify({
            'status': 'success',
            'session': {
                'configured': bool(session.get('configured')),
                'account': session.get('account'),
                'updated_at': session.get('updated_at'),
                'last_ok_at': session.get('last_ok_at'),
                'last_error': session.get('last_error'),
            },
            'parks': [{'id': row['park_id'], 'name': row.get('name'),
                       'city': row.get('city')} for row in allowed],
            'parks_total': len(rows),
            'limits': limits,
            'templates': template_rows,
            'capabilities': access.capabilities(requester),
        })

    @section_route('/parks/refresh', methods=('POST',), sending=True)
    def driver_mailings_refresh_parks(requester_id, requester):
        rows = refresh_parks()
        allowed = [row for row in rows if row.get('is_enabled')]
        return jsonify({
            'status': 'success',
            'checked': len(rows),
            'enabled': len(allowed),
        })

    @section_route('/filters')
    def driver_mailings_filters(requester_id, requester):
        park_ids = [value for value in (request.args.get('park_ids') or '').split(',') if value]
        segment = (request.args.get('segment') or '').strip() or None
        if not park_ids:
            return jsonify({'segments': [], 'groups': [], 'professions': [],
                            'statuses': [], 'categories': [], 'amenities': [], 'cities': []})
        allowed = {row['park_id'] for row in enabled_parks()}
        park_ids = [park_id for park_id in park_ids if park_id in allowed]
        if not park_ids:
            return jsonify({"error": "В этих диспетчерских рассылка не разрешена",
                            "code": "PARK_NOT_ALLOWED"}), 400
        return jsonify(refs_for(park_ids, segment))

    @section_route('/recipients/count', methods=('POST',))
    def driver_mailings_count(requester_id, requester):
        payload = request.get_json(silent=True) or {}
        park_ids = [str(value) for value in (payload.get('park_ids') or []) if value]
        filters = catalog.normalize_filters(payload.get('filters'))
        allowed = {row['park_id']: row for row in enabled_parks()}
        park_ids = [park_id for park_id in park_ids if park_id in allowed]
        if not park_ids:
            return jsonify({'total': 0, 'by_park': []})

        client = make_client()

        def one(park_id):
            row = {'park_id': park_id, 'park_name': allowed[park_id].get('name'), 'count': 0}
            try:
                row['count'] = client.recipients_count(park_id, filters)
            except FleetSessionExpired:
                raise
            except FleetError as error:
                logging.warning('Рассылки: не посчитали получателей в %s (%s)', park_id, error)
                row['error'] = 'не удалось посчитать'
            return row

        with ThreadPoolExecutor(max_workers=SCAN_WORKERS,
                                thread_name_prefix='mailings-count') as pool:
            by_park = list(pool.map(one, park_ids))
        return jsonify({
            'total': sum(int(row.get('count') or 0) for row in by_park),
            'by_park': by_park,
        })

    @section_route('/send', methods=('POST',), sending=True)
    def driver_mailings_send(requester_id, requester):
        payload = request.get_json(silent=True) or {}
        idempotency_key = str(payload.get('idempotency_key') or '').strip()
        if not idempotency_key:
            return jsonify({"error": "Пустой ключ отправки", "code": "NO_TOKEN"}), 400

        # Повторное нажатие: карточка с этим токеном уже есть — отдаём её как
        # есть и в кабинет не идём. Токен рождается один раз на попытку.
        with db._get_cursor() as cursor:
            existing = queries.find_mailing_by_key(cursor, idempotency_key)
        if existing:
            with db._get_cursor() as cursor:
                detail = queries.mailing_detail(cursor, existing['id'])
            # sent_total считаем и здесь: без него повтор рапортовал бы «ушло 0
            # водителей», и человек отправил бы заново — то есть защита от
            # двойной отправки сама бы к ней и подтолкнула.
            return jsonify({
                'status': detail.get('status'),
                'mailing_id': detail.get('id'),
                'repeated': True,
                'sent_total': _sent_total(detail),
                'targets': detail.get('targets') or [],
            })

        title = str(payload.get('title') or '').strip()
        message_kk = str(payload.get('message_kk') or '').strip()
        message_ru = str(payload.get('message_ru') or '').strip()
        if message_kk or message_ru:
            # Склейка двух языков живёт на сервере, чтобы кабинет и журнал видели
            # ровно один и тот же текст: собери его клиент — и «Повторить» из
            # журнала однажды разошлось бы с тем, что получил водитель.
            parts = [part for part in (message_kk, message_ru) if part]
            message = catalog.BILINGUAL_SEPARATOR.join(parts)
        else:
            message = str(payload.get('message') or '').strip()

        filters = catalog.normalize_filters(payload.get('filters'))
        park_rows = {row['park_id']: row for row in enabled_parks()}
        # dict.fromkeys, а не set: порядок диспетчерских виден человеку в окне
        # подтверждения, а дубль в списке означал бы два POST в один парк, то
        # есть две настоящие рассылки одним и тем же людям.
        park_ids = list(dict.fromkeys(
            str(value) for value in (payload.get('park_ids') or [])
            if str(value) in park_rows))

        if not title:
            return jsonify({"error": "Заголовок обязателен", "code": "NO_TITLE"}), 400
        if not message:
            return jsonify({"error": "Текст рассылки обязателен", "code": "NO_MESSAGE"}), 400
        if not park_ids:
            return jsonify({"error": "Выберите хотя бы одну диспетчерскую",
                            "code": "NO_PARKS"}), 400

        max_title = min([park_rows[p].get('max_title') or 120 for p in park_ids])
        max_message = min([park_rows[p].get('max_message') or 1500 for p in park_ids])
        if len(title) > max_title:
            return jsonify({"error": "Заголовок длиннее {} символов".format(max_title),
                            "code": "TITLE_TOO_LONG"}), 400
        if len(message) > max_message:
            return jsonify({"error": "Текст длиннее {} символов".format(max_message),
                            "code": "MESSAGE_TOO_LONG"}), 400

        client = make_client()

        # Охват на момент отправки: он попадёт в журнал и в отчёт. Считаем ДО
        # отправки, потому что после неё состав получателей уже изменится.
        estimates = {}
        for park_id in park_ids:
            try:
                estimates[park_id] = client.recipients_count(park_id, filters)
            except FleetError:
                estimates[park_id] = None

        with db._get_cursor() as cursor:
            mailing_id = queries.create_mailing(
                cursor,
                created_by=requester_id,
                created_by_name=(requester or {}).get('name'),
                title=title, message=message,
                message_kk=message_kk or None, message_ru=message_ru or None,
                filters=filters,
                recipients_estimate=sum(v for v in estimates.values() if v) or None,
                idempotency_key=idempotency_key,
            )
            for park_id in park_ids:
                queries.add_target(
                    cursor, mailing_id,
                    park_id=park_id,
                    park_name=park_rows[park_id].get('name'),
                    park_city=park_rows[park_id].get('city'),
                    recipients_estimate=estimates.get(park_id),
                )

        started = datetime.now(timezone.utc) - timedelta(seconds=30)
        started_iso = started.isoformat()

        def claim_fleet_id(park_id, before_ids):
            """Найти в журнале кабинета id только что отправленной рассылки.

            before_ids — снимок журнала ДО отправки: id, которого там не было, и
            есть наш. Это главный признак, потому что у только что созданной
            рассылки кабинет ещё не проставил время (проверено на живой отправке
            07.09.2026), и искать её по времени бесполезно.

            Окно «уже занятых» берём ШИРЕ окна поиска (started минус запас): если
            в этот же парк сегодня уже уходила рассылка с тем же заголовком, её id
            обязан попасть в занятые. Совпади окна — вторая отправка забрала бы
            id первой, и «Отозвать» ударил бы по чужой рассылке.
            """
            with db._get_cursor() as cursor:
                claimed = queries.claimed_fleet_ids(
                    cursor, park_id, since=started - CLAIM_LOOKBACK)
            return client.resolve_mailing_id(park_id, title, since_iso=started_iso,
                                             skip_ids=claimed, before_ids=before_ids)

        # Отправляем по одной диспетчерской и после КАЖДОЙ пишем результат в базу.
        # Последовательно, а не пачкой: кабинет считает лимиты на аккаунт, и пять
        # одновременных отправок — верный способ получить отказ по темпу на
        # ровном месте. Пять запросов подряд стоят пары секунд.
        #
        # try/finally на весь цикл: без него исключение посреди отправки оставляло
        # бы карточку навсегда в статусе «Отправляется», а не дошедшие парки — в
        # «В очереди». Человек видел бы вечно идущую рассылку и отправил заново.
        try:
            for park_id in park_ids:
                token = '{}-{}'.format(idempotency_key, park_id)
                # Снимок журнала ДО отправки — по нему потом узнаём свою рассылку.
                # Один запрос на парк; без него id найти нечем.
                before_ids = client.journal_ids(park_id)
                try:
                    client.send_mailing(park_id, title=title, message=message,
                                        filters=filters, idempotency_token=token)
                except MailingRefused as error:
                    # Отказ кабинета — рассылка НЕ ушла, искать её в журнале
                    # незачем.
                    with db._get_cursor() as cursor:
                        queries.mark_target_failed(cursor, mailing_id, park_id,
                                                   error=str(error), error_code=error.code)
                    continue
                except FleetSessionExpired:
                    with db._get_cursor() as cursor:
                        queries.mark_target_failed(
                            cursor, mailing_id, park_id,
                            error='Связь с кабинетом прервалась',
                            error_code='session_expired')
                    raise
                except FleetError as error:
                    # А вот здесь рассылка МОГЛА уйти: обрыв на ответе, таймаут,
                    # 500 после доставки. Прежде чем записать отказ, ищем её в
                    # журнале кабинета — нашли, значит ушла, и записать «ошибку»
                    # было бы враньём, из-за которого её ещё и отозвать нельзя.
                    logging.warning('Рассылки: отправка в парк %s не удалась (%s)',
                                    park_id, error)
                    recovered = None
                    try:
                        recovered = claim_fleet_id(park_id, before_ids)
                    except FleetError:
                        recovered = None
                    with db._get_cursor() as cursor:
                        if recovered:
                            queries.mark_target_sent(cursor, mailing_id, park_id,
                                                     fleet_mailing_id=recovered)
                        else:
                            queries.mark_target_failed(cursor, mailing_id, park_id,
                                                       error='Кабинет ответил ошибкой',
                                                       error_code='fleet_error')
                    continue

                # Ушло. Ищем id в журнале кабинета — он нужен для прочтений и
                # отзыва. Не нашли — всё равно отмечаем отправку: рассылка у людей.
                fleet_id = claim_fleet_id(park_id, before_ids)
                with db._get_cursor() as cursor:
                    queries.mark_target_sent(cursor, mailing_id, park_id,
                                             fleet_mailing_id=fleet_id)
        finally:
            with db._get_cursor() as cursor:
                # Сначала закрываем недошедшие цели, потом считаем сводный
                # статус: иначе прерванная отправка оставила бы половину строк
                # в «В очереди», а карточку — в «Отправляется», и человек не
                # понял бы, ушло ли хоть что-нибудь.
                queries.abandon_pending_targets(cursor, mailing_id)
                queries.finish_mailing(cursor, mailing_id)

        with db._get_cursor() as cursor:
            status = queries.finish_mailing(cursor, mailing_id)
            detail = queries.mailing_detail(cursor, mailing_id)

        return jsonify({
            'status': status,
            'mailing_id': mailing_id,
            'sent_total': _sent_total(detail),
            'targets': detail.get('targets') or [],
        })

    @section_route('/journal')
    def driver_mailings_journal(requester_id, requester):
        try:
            limit = min(int(request.args.get('limit') or 20), JOURNAL_MAX_LIMIT)
            offset = max(int(request.args.get('offset') or 0), 0)
        except (TypeError, ValueError):
            limit, offset = 20, 0
        with db._get_cursor() as cursor:
            items, total = queries.journal(cursor, limit=limit, offset=offset)
        return jsonify({'status': 'success', 'items': attach_read_stats(items), 'total': total})

    @section_route('/journal/<int:mailing_id>')
    def driver_mailings_journal_item(requester_id, requester, mailing_id):
        with db._get_cursor() as cursor:
            detail = queries.mailing_detail(cursor, mailing_id)
        if not detail:
            return jsonify({"error": "Рассылка не найдена"}), 404
        return jsonify({'status': 'success', 'item': attach_read_stats([detail])[0]})

    @section_route('/journal/<int:mailing_id>/revoke', methods=('POST',), sending=True)
    def driver_mailings_revoke(requester_id, requester, mailing_id):
        with db._get_cursor() as cursor:
            detail = queries.mailing_detail(cursor, mailing_id)
        if not detail:
            return jsonify({"error": "Рассылка не найдена"}), 404

        client = make_client()
        revoked = 0
        errors = []
        for target in detail.get('targets') or []:
            if target.get('status') != 'sent' or not target.get('fleet_mailing_id'):
                continue
            try:
                client.revoke_mailing(target['park_id'], target['fleet_mailing_id'])
            except FleetSessionExpired:
                raise
            except FleetError as error:
                # Чаще всего это «окно закрылось». Кабинет решает, отзывать или
                # нет, и его отказ по одной диспетчерской не отменяет отзыв в
                # остальных.
                logging.warning('Рассылки: отзыв в парке %s не прошёл (%s)',
                                target.get('park_id'), error)
                errors.append(target.get('park_name') or target.get('park_id'))
                continue
            with db._get_cursor() as cursor:
                queries.mark_target_revoked(cursor, mailing_id, target['park_id'])
            revoked += 1

        with db._get_cursor() as cursor:
            status = queries.finish_mailing(cursor, mailing_id)
        return jsonify({'status': status, 'revoked': revoked, 'failed': errors})

    # ── шаблоны ──────────────────────────────────────────────────────────────

    @section_route('/templates', methods=('GET', 'POST'))
    def driver_mailings_templates(requester_id, requester):
        if request.method == 'GET':
            with db._get_cursor() as cursor:
                return jsonify({'status': 'success', 'items': queries.templates(cursor)})

        if not access.can_manage_templates(requester):
            return jsonify({"error": "Шаблоны меняют те же, кто отправляет"}), 403
        payload = request.get_json(silent=True) or {}
        name = str(payload.get('name') or '').strip()
        if not name:
            return jsonify({"error": "У шаблона должно быть название"}), 400
        with db._get_cursor() as cursor:
            item = queries.create_template(
                cursor,
                name=name[:80],
                title=str(payload.get('title') or ''),
                message=str(payload.get('message') or ''),
                message_kk=str(payload.get('message_kk') or '') or None,
                message_ru=str(payload.get('message_ru') or '') or None,
                filters=catalog.normalize_filters(payload.get('filters')),
                park_ids=[str(value) for value in (payload.get('park_ids') or [])],
                created_by=requester_id,
                created_by_name=(requester or {}).get('name'),
            )
        return jsonify({'status': 'success', 'item': item}), 201

    @section_route('/templates/<int:template_id>', methods=('PATCH', 'DELETE'))
    def driver_mailings_template_item(requester_id, requester, template_id):
        if not access.can_manage_templates(requester):
            return jsonify({"error": "Шаблоны меняют те же, кто отправляет"}), 403
        if request.method == 'DELETE':
            with db._get_cursor() as cursor:
                removed = queries.delete_template(cursor, template_id)
            if not removed:
                return jsonify({"error": "Шаблон не найден"}), 404
            return jsonify({'status': 'success'})

        payload = request.get_json(silent=True) or {}
        fields = {}
        for key in ('name', 'title', 'message', 'message_kk', 'message_ru'):
            if key in payload:
                fields[key] = str(payload.get(key) or '')
        if 'filters' in payload:
            fields['filters'] = catalog.normalize_filters(payload.get('filters'))
        if 'park_ids' in payload:
            fields['park_ids'] = [str(value) for value in (payload.get('park_ids') or [])]
        with db._get_cursor() as cursor:
            item = queries.update_template(cursor, template_id, **fields)
        if not item:
            return jsonify({"error": "Шаблон не найден"}), 404
        return jsonify({'status': 'success', 'item': item})

    return bp
