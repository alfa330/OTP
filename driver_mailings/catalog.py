"""Справочники раздела «Рассылки» и сборка фильтра получателей (задача #166).

ЗАЧЕМ ОТДЕЛЬНЫЙ МОДУЛЬ. Здесь лежит вся «правда о кабинете», которую нельзя
вывести из его же ответов: какие ключи фильтра он понимает, что из присланных им
справочников показывать нельзя, как называются по-русски группы (кабинет отдаёт
только машинные ключи) и что означает код отказа. Всё измерено живьём 07.09.2026
на fleet.yandex.kz, а не взято из документации — её на эти ручки нет.

ГЛАВНАЯ ГРАБЛЯ, РАДИ КОТОРОЙ ВСЁ ЭТО СУЩЕСТВУЕТ. Неизвестный ключ фильтра
кабинет не считает ошибкой: он молча его выбрасывает и отвечает 200 с ПОЛНЫМ
охватом парка (проверено: `{"blah":"nope"}` дал те же 1144 получателя, что и
пустой фильтр). Здесь это не абстрактная неаккуратность, а рассылка полутора
тысячам живых водителей вместо десятка выбранных, причём отозвать её можно
только пять минут. Поэтому фильтр НИКОГДА не переносится из запроса как есть:
`normalize_filters` собирает его заново по белому списку.

ЧТО ЕЩЁ ВАЖНО ЗНАТЬ ПРО ФИЛЬТР:
* `subsegments` без `segment` дают ноль получателей и ни слова об ошибке.
* `city_ids` кабинет принимает всегда, но в своём интерфейсе показывает только
  для сегментов «Активные» и «Отток» — повторяем это поведение, иначе оператор
  выберет город при сегменте «Архив» и получит охват, которого не ожидал.
* В `city_ids` едут НАЗВАНИЯ городов («Алматы»), а не идентификаторы, несмотря
  на имя ключа.
"""

# ── сегменты ────────────────────────────────────────────────────────────────

SEGMENT_LABELS = {
    'candidate': 'Новые',
    'active': 'Активные',
    'churn': 'Отток',
    'archive': 'Архив',
}

# Сегмент — единственное значение, а не список: кабинет принимает ровно один.
SEGMENTS = tuple(SEGMENT_LABELS)

# Город кабинет показывает только у этих сегментов (см. ловушку в шапке).
SEGMENTS_WITH_CITY = ('active', 'churn')

# ── группы (бейджи водителей) ───────────────────────────────────────────────
#
# Категорию выбирают первым списком, конкретную группу — вторым: плоский список
# из тридцати машинных ключей нечитаем, а сам кабинет делит их ровно так же.

GROUP_CATEGORIES = (
    ('has_restrictions', 'Ограничения'),
    ('has_warnings', 'Предупреждения'),
    ('has_opportunities', 'Возможности'),
)

GROUPS_BY_CATEGORY = {
    'has_restrictions': (
        'has_not_verified_inn', 'has_thermobag_photocheck_not_passed',
        'is_car_photocheck_not_passed', 'is_car_photocheck_not_passed_depriority',
        'has_contract_issue', 'has_contract_issue_depriority',
        'is_selfemployed_income_threshold_exceeded', 'carrier_license_not_confirmed_depriority',
        'attestation_depriority_status', 'has_osago_restriction',
        'no_license_taxi_uzb', 'no_license_taxi_uzb_restriction'),
    'has_warnings': (
        'has_violation_warning', 'is_available_and_has_low_balance',
        'has_not_all_payment_details', 'is_car_photocheck_not_passed_warning',
        'has_contract_issue_warning', 'has_expiring_closing_documents',
        'has_attestation_issues', 'is_selfemployed_income_threshold_approaching',
        'carrier_license_not_confirmed_warning', 'has_osago_warning',
        'no_license_taxi_uzb', 'no_license_taxi_uzb_warning', 'no_edm_provider'),
    'has_opportunities': (
        'has_unprocessed_rental_request', 'unblocked_without_trips',
        'want_to_change_car', 'has_fuec_issues', 'platform_newbie'),
}

# Русские подписи. Кабинет отдаёт по парку только машинные ключи
# (`available_groups`), переводов в ответе нет вовсе — они зашиты в его фронтенд,
# откуда и списаны. Ключа нет в словаре — группу не показываем: так же поступает
# и сам кабинет с неизвестными ему значениями.
GROUP_LABELS = {
    'has_not_verified_inn': 'Не верифицирован ИНН',
    'has_thermobag_photocheck_not_passed': 'Фотоконтроль термокороба',
    'is_car_photocheck_not_passed': 'Нет выписки из реестра ТС',
    'is_car_photocheck_not_passed_depriority': 'Нет выписки из реестра ТС',
    'is_car_photocheck_not_passed_warning': 'Нет выписки из реестра ТС',
    'has_contract_issue': 'Подтвердить занятость',
    'has_contract_issue_depriority': 'Подтвердить занятость',
    'has_contract_issue_warning': 'Подтвердить занятость',
    'is_selfemployed_income_threshold_exceeded': 'Лимит доходов исчерпан',
    'is_selfemployed_income_threshold_approaching': 'Лимит доходов на исходе',
    'carrier_license_not_confirmed_depriority': 'Подтвердить разрешение перевозчика',
    'carrier_license_not_confirmed_warning': 'Подтвердить разрешение перевозчика',
    'attestation_depriority_status': 'Не сдана аттестация',
    'has_attestation_issues': 'Не сдана аттестация',
    'has_osago_restriction': 'Не подтверждено ОСАГО для такси',
    'has_osago_warning': 'Не подтверждено ОСАГО для такси',
    'no_license_taxi_uzb': 'Нет лицензии на такси',
    'no_license_taxi_uzb_restriction': 'Нет лицензии на такси',
    'no_license_taxi_uzb_warning': 'Нет лицензии на такси',
    'has_violation_warning': 'Нарушения',
    'is_available_and_has_low_balance': 'Низкий баланс',
    'has_not_all_payment_details': 'Нет платёжных реквизитов',
    'has_expiring_closing_documents': 'Не сданы закрывающие документы',
    'no_edm_provider': 'Не выбран провайдер ЭДО',
    'has_unprocessed_rental_request': 'Отклик на парковый автомобиль',
    'unblocked_without_trips': 'Не поехал после разблокировки',
    'platform_newbie': 'Новички: меньше месяца в сервисе',
    # want_to_change_car и has_fuec_issues кабинет не переводит и в своём
    # интерфейсе не показывает — у нас их тоже нет.
}

# Всё, что мы вообще согласны отправить в ключе `group`. Ограничение намеренно
# статическое: значение сюда приходит из выпадающего списка, собранного как
# пересечение available_groups парка с GROUPS_BY_CATEGORY, и любое другое
# значение означает не «Яндекс завёл новую группу», а самодельный запрос мимо
# интерфейса. Появится новая группа — заводим её здесь вместе с подписью.
KNOWN_GROUPS = frozenset(
    key for keys in GROUPS_BY_CATEGORY.values() for key in keys
)

def groups_for(available):
    """Список групп для выпадающего списка: пересечение того, что есть в парке,
    с тем, что мы умеем назвать по-русски.

    Порядок — как в GROUP_CATEGORIES и внутри категории как в GROUPS_BY_CATEGORY,
    а не как ответил кабинет: набор групп у него меняется от парка к парку, и
    список, переставляющийся местами при смене диспетчерской, читается как
    другой список.

    Ключ, которого нет в GROUP_LABELS, пропускается молча — ровно так поступает
    и сам кабинет: у `want_to_change_car` и `has_fuec_issues` перевода нет, и в
    его интерфейсе их тоже не видно.
    """
    have = {str(key).strip() for key in (available or []) if str(key).strip()}
    out = []
    for category, category_label in GROUP_CATEGORIES:
        for key in GROUPS_BY_CATEGORY.get(category, ()):
            if key in have and key in GROUP_LABELS:
                out.append({
                    'category': category,
                    'category_label': category_label,
                    'key': key,
                    'label': GROUP_LABELS[key],
                })
    return out


# Разделитель двуязычной рассылки — ТРИ НИЖНИХ ПОДЧЁРКИВАНИЯ.
#
# Приложение Pro рисует черту между языками именно по `___`: это разметка, и
# приложение превращает её в горизонтальную линию. Первая версия ставила десять
# символов «─» — в поле ввода они выглядят чертой, но водителю приезжают обычным
# текстом, и вместо линии он видит ряд палочек во всю ширину (сверено со
# скриншотами из приложения 07.09.2026).
#
# Пустые строки вокруг обязательны: без них Pro считает подчёркивания
# продолжением абзаца и черту не рисует. Тот же разделитель ставит и разбирает
# фронт (src/components/driver_mailings/mailingText.js) — значение обязано
# совпадать байт в байт, иначе «Повторить» не разделит текст обратно на языки.
BILINGUAL_SEPARATOR = '\n\n___\n\n'


# ── чистка справочников кабинета ────────────────────────────────────────────

# Статусы водителя. Из справочника `driver_order_statuses` кабинет показывает не
# всё: составные «на заказе + свободен» и «на заказе + занят» в фильтре рассылки
# не участвуют, а `free` дублирует `online`.
STATUS_DROP = ('in_order_free', 'in_order_busy', 'free')

# «На заказе» уезжает как `on_order`, хотя в справочнике он `in_order`. Это не
# наша вольность: с `in_order` кабинет отвечает 400 (проверено 07.09.2026), а его
# собственный интерфейс шлёт именно `on_order`. Справочник и фильтр здесь живут
# в разных словарях, и переименование — единственный способ их свести.
STATUS_RENAME = {'in_order': 'on_order'}

# Значения, которые кабинет принимает в `contractor_statuses` после чистки.
ALLOWED_STATUSES = ('offline', 'busy', 'on_order', 'online')

# `pool` (совместные поездки) и `none` — служебные значения `order_categories`,
# тарифами не являются и в фильтре смысла не имеют.
CATEGORY_DROP = ('pool', 'none')

# `none` в `car_services` означает «опций нет» — как условие фильтра бессмысленно.
AMENITY_DROP = ('none',)


def _clean_reference(items, *, drop=(), rename=None, allow=None):
    """Справочник кабинета → список `{id, name}` для выпадающего списка.

    Выбрасывает служебные значения, применяет переименования и, если задан
    белый список, оставляет только то, что кабинет реально примет в фильтре.
    Дубликаты после переименования схлопываются: `in_order` и уже пришедший
    `on_order` — одна и та же строка интерфейса.
    """
    drop = set(drop or ())
    rename = dict(rename or {})
    result, seen = [], set()
    for item in (items or []):
        if isinstance(item, dict):
            code = str(item.get('id') or item.get('code') or '').strip()
            name = str(item.get('name') or '').strip()
        else:
            code, name = str(item or '').strip(), ''
        if not code or code in drop:
            continue
        code = rename.get(code, code)
        if allow is not None and code not in allow:
            continue
        if code in seen:
            continue
        seen.add(code)
        result.append({'id': code, 'name': name or code})
    return result


def clean_statuses(items):
    """Статусы водителя для интерфейса: без составных «на заказе» и без `free`,
    с `in_order`, переименованным в `on_order` (иначе кабинет отдаст 400)."""
    return _clean_reference(items, drop=STATUS_DROP, rename=STATUS_RENAME,
                            allow=set(ALLOWED_STATUSES))


def clean_categories(items):
    """Тарифы (`order_categories`) без служебных `pool` и `none`."""
    return _clean_reference(items, drop=CATEGORY_DROP)


def clean_amenities(items):
    """Опции автомобиля (`car_services`) без `none`."""
    return _clean_reference(items, drop=AMENITY_DROP)


# ── фильтр получателей ──────────────────────────────────────────────────────

# Белый список ключей фильтра. Всё, чего здесь нет, до кабинета не доедет.
#
# work_rule_ids (условия работы) кабинет тоже понимает, но в первой версии
# раздела его нет по решению владельца, и в белый список он не внесён
# сознательно: ключ, который никто не проверяет глазами в интерфейсе, — прямая
# дорога к неожиданному охвату.
FILTER_KEYS = (
    'segment',
    'subsegments',
    'group',
    'city_ids',
    'profession_ids',
    'contractor_statuses',
    'car_categories',
    'car_amenities',
)

# Ключи-списки: значения приводятся к строкам, пустые выбрасываются, порядок
# сохраняется (он виден человеку в окне подтверждения).
_LIST_KEYS = ('subsegments', 'city_ids', 'profession_ids',
              'contractor_statuses', 'car_categories', 'car_amenities')


def _string_list(value):
    """Список непустых строк без повторов, порядок как пришёл. Одиночную строку
    принимаем тоже: фронт для мультиселекта с единственным выбранным значением
    иногда шлёт скаляр."""
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        items = [value]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = [value]
    result, seen = [], set()
    for item in items:
        text = str(item or '').strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def normalize_filters(raw):
    """Собрать фильтр получателей заново — только из белого списка ключей.

    ЭТО НЕ ВАЛИДАЦИЯ, А ГРАНИЦА БЕЗОПАСНОСТИ. Неизвестный ключ кабинет не
    отвергает: он молча его игнорирует и отвечает ПОЛНЫМ охватом парка (измерено:
    `{"blah":"nope"}` → 1144 получателя, ровно как пустой фильтр). То есть
    опечатка в имени ключа или «прокинем фильтр как есть, там разберутся»
    означает не ошибку 400, а рассылку всем водителям диспетчерской, которую
    можно отозвать лишь в ближайшие пять минут. Поэтому наружу уходит только то,
    что собрано здесь поимённо.

    Заодно повторяем три правила самого кабинета, каждое из которых иначе
    отрабатывает молча и неправильно:
    * `subsegments` без `segment` → кабинет вернёт ноль получателей и промолчит,
      поэтому подсегменты без сегмента отбрасываем;
    * город кабинет учитывает при любом сегменте, но показывает только у
      «Активных» и «Оттока» — при других сегментах город не отправляем, чтобы
      охват совпадал с тем, что человек видел на экране;
    * статус «На заказе» переименовываем в `on_order`: `in_order` даёт 400.

    Пустой список значений — это не «ничего не выбрано из этого справочника», а
    отсутствие условия, поэтому такой ключ просто не попадает в результат.
    """
    source = raw if isinstance(raw, dict) else {}
    filters = {}

    segment = str(source.get('segment') or '').strip()
    if segment in SEGMENTS:
        filters['segment'] = segment

    group = str(source.get('group') or '').strip()
    if group in KNOWN_GROUPS:
        filters['group'] = group

    for key in _LIST_KEYS:
        values = _string_list(source.get(key))
        if not values:
            continue
        if key == 'contractor_statuses':
            values = [STATUS_RENAME.get(v, v) for v in values]
            values = [v for v in values if v in ALLOWED_STATUSES]
            # Порядок сохраняем, но после переименования возможны дубликаты.
            values = list(dict.fromkeys(values))
        elif key == 'car_categories':
            values = [v for v in values if v not in CATEGORY_DROP]
        elif key == 'car_amenities':
            values = [v for v in values if v not in AMENITY_DROP]
        elif key == 'subsegments' and 'segment' not in filters:
            continue
        elif key == 'city_ids' and filters.get('segment') not in SEGMENTS_WITH_CITY:
            continue
        if values:
            filters[key] = values

    return filters


# ── человеческие подписи выбранного ─────────────────────────────────────────

# Подписи справочников приходят живыми из кабинета (свои переводы завели бы
# вторую правду), поэтому describe_filters принимает их отдельным аргументом.
# Порядок строк — как на экране, чтобы окно подтверждения читалось сверху вниз
# так же, как заполнялась форма.
_DESCRIBE_ORDER = (
    ('segment', 'Сегмент'),
    ('subsegments', 'Подсегменты'),
    ('group', 'Группа'),
    ('city_ids', 'Города'),
    ('profession_ids', 'Профессии'),
    ('contractor_statuses', 'Статусы'),
    ('car_categories', 'Тарифы'),
    ('car_amenities', 'Опции авто'),
)

# Из какого справочника берётся подпись для каждого ключа фильтра.
_DESCRIBE_REFS = {
    'subsegments': 'subsegments',
    'city_ids': 'cities',
    'profession_ids': 'professions',
    'contractor_statuses': 'statuses',
    'car_categories': 'categories',
    'car_amenities': 'amenities',
}


def _label_map(refs, name):
    """Справочник в виде `{код: подпись}`.

    Принимаем оба вида, потому что по дороге от кабинета до окна подтверждения
    справочник успевает побывать и списком `[{id, name}]` (как отдаёт кабинет и
    как уходит на фронт), и уже готовым словарём (как удобно в тестах).
    """
    raw = (refs or {}).get(name)
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    result = {}
    for item in (raw or []):
        if isinstance(item, dict):
            code = str(item.get('id') or item.get('code') or '').strip()
            if code:
                result[code] = str(item.get('name') or '').strip() or code
    return result


def describe_filters(filters, refs=None):
    """Подписи выбранных условий для окна подтверждения и журнала.

    Возвращает список строк вида «Сегмент: Активные», «Города: Алматы, Астана».
    Человек подтверждает отправку по этому списку, поэтому пустой фильтр не
    оставляем пустым местом: он честно называется полным охватом — это самая
    дорогая ошибка раздела, и молчать о ней нельзя.

    Кода, которому не нашлось подписи, не прячем: показываем машинный ключ. Это
    некрасиво, но честно — исчезнувшее из списка условие человек бы не заметил.
    """
    filters = filters if isinstance(filters, dict) else {}
    if not filters:
        return ['Без фильтров: все водители диспетчерской']

    lines = []
    for key, caption in _DESCRIBE_ORDER:
        value = filters.get(key)
        if not value:
            continue
        if key == 'segment':
            lines.append('{}: {}'.format(caption, SEGMENT_LABELS.get(value, value)))
            continue
        if key == 'group':
            lines.append('{}: {}'.format(caption, GROUP_LABELS.get(value, value)))
            continue
        labels = _label_map(refs, _DESCRIBE_REFS[key])
        names = [labels.get(code, code) for code in _string_list(value)]
        if names:
            lines.append('{}: {}'.format(caption, ', '.join(names)))
    return lines


# ── отказы кабинета ─────────────────────────────────────────────────────────

# Кабинет отвечает на отказ JSON с полем `code` и без единого слова для
# человека — тексты наши. Список закрытый: всё незнакомое попадает в общий
# случай вместе с самим кодом, чтобы по обращению в поддержку было понятно, что
# именно ответил кабинет.
MAILING_ERRORS = {
    'limit_drivers': 'Слишком много получателей',
    'limit_time': 'Превышен лимит рассылок за период',
    'mailing_not_allowed': 'Рассылка не разрешена',
    'bad_request': 'Кабинет не принял запрос',
    # Не из таблицы отказов, а из общего 403: так кабинет отвечает в 85 парках
    # из 90. На отправке это тот же отказ, и общее «не смог создать рассылку»
    # увело бы разбор не туда — причина ровно одна и она известна.
    'no_permissions': 'В этой диспетчерской рассылка не разрешена',
}

MAILING_ERROR_DEFAULT = 'Кабинет не смог создать рассылку'


def error_message(code, max_recipients=None):
    """Код отказа кабинета → текст для человека.

    `max_recipients` подставляется в сообщение о слишком большом охвате: само
    ограничение кабинет в отказе не называет, оно известно только из
    `mailings/limits`, поэтому число приходит снаружи и может отсутствовать.
    """
    code = str(code or '').strip()
    if code == 'limit_drivers':
        if max_recipients:
            return 'Слишком много получателей (максимум {})'.format(max_recipients)
        return MAILING_ERRORS['limit_drivers']
    if code in MAILING_ERRORS:
        return MAILING_ERRORS[code]
    if code:
        return '{} (код: {})'.format(MAILING_ERROR_DEFAULT, code)
    return MAILING_ERROR_DEFAULT
