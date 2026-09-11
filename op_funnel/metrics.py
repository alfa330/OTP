# -*- coding: utf-8 -*-
"""Математика воронки ОП. Чистая: ни базы, ни сети, ни Flask.

Здесь живут ВСЕ правила подсчёта — и экран, и выгрузка в Excel, и суточный
агрегат считают одним кодом. Разъехавшиеся формулы в двух местах — это главный
способ получить два разных ответа на один вопрос, и в этом проекте на таком уже
горели (правила «ждут вас» лежат в четырёх копиях, и их сторожит отдельный тест).

Откуда взялись правила
----------------------
Не выдуманы: сняты с рабочих файлов супервайзеров, приложенных к задачам, —
формула за формулой.

**«Поток»** (задача #303, лист «Общий», ячейки S33/T33/V33):

    Дозвон   = статус звонка «Дозвон»
             + пустой статус звонка, если статус диалога заполнен и это не
               «Конечный недозвон» и не «Неверный номер»
    Недозвон = статус звонка из набора недозвонных
    Исход    = Недозвон + Дозвон          (обработано лидов)
    Согласия = статус диалога из {приглашен, вышел на линию, согласился перейти,
               согласился выйти}

Пустой статус звонка при заполненном диалоге — это не дырка в данных, а
«оператор поговорил, но статус звонка не проставил». Excel это учитывает
осознанно, и мы повторяем: иначе у части операторов дозвон занижается вдвое.

**«Яндекс Регистрация» / платный найм** (задача #301, раздел 2 ТЗ): у лида один
статус, и он же называет корзину причины. Поэтому разбивка «отказы / нецелевые /
недозвоны» берётся из СТАТУСА, а причина внутри корзины — из `closed_reason`.
Угадывать корзину по коду причины не нужно и нельзя: «Нет авто» бывает и
нецелевым, и отказом — решает статус лида.

**«Основа ОП»** (задача #302, лист «Общий», колонки AC..AQ): корзина зашита в
саму причину отказа amoCRM — «Нет авто (не цел)», «Неправильный номер
(недозвон)», «Высокая комиссия (отказ)». Дозвон здесь НЕ из телефонии, а
выводится из исходов: `Дозвон = Отказы + Нецелевые + Согласия`, а «Исходящие
звонки» в файле равны «Обработано лидов». То есть телефония для этого таба не
нужна вовсе.

**«Верификатор»** (задача #305, лист «Воронка»): воронки обзвона нет, есть
нагрузка и качество: `Обработано = Чаты WZ + Тикеты`, `Чаты/час = Обработано /
Часы`, `% факт = Чаты/час / таргет`, `Разница от таргета = таргет / факт − 1`.

Единый знаменатель «% дозвона»
------------------------------
ТЗ #301 (пункт 3.1) требовало зафиксировать ОДНУ формулу до разработки: в группе
ЯР считали от суммы дозвон+недозвон, в «Потоке» — от всех обработанных лидов, и
цифры между командами были несравнимы. Решение владельца от 11.09.2026:

    % дозвона = Дозвон / Обработано лидов

во всех направлениях. Смысл: знаменатель — вся взятая в работу база, поэтому
показатель честно падает, когда оператор до части лидов просто не дошёл.
Вариант «от суммы попыток» этого не показывал: можно было взять 200 лидов,
обзвонить 50 и иметь отличный процент.
"""

from datetime import date, timedelta

# ── Пространства значений источников ─────────────────────────────────────────
# Пришли из живых замеров за 01.08–11.09.2026, а не из документации: партнёрская
# ручка СХЛОПЫВАЕТ детализацию, которую видно в выгрузке с экрана СРМ. В ответе
# приходит «Недозвон», а в файле супервайзера были «Недозвон 1/2/3», «Гудок не
# идет», «сброс», «чс», «уволен». Поэтому набор ниже — объединение обоих: код
# обязан понимать и то, что отдаёт ручка, и то, что лежит в старых выгрузках.

_STREAM_CALL_NOT_REACHED = frozenset((
    'недозвон', 'недозвон 1', 'недозвон 2', 'недозвон 3', 'недозвон 4',
    'гудок не идет', 'гудок не идёт', 'чс', 'неверный номер', 'неправильный номер',
    'уволен', 'сброс', 'перезвон назначен',
))
_STREAM_CALL_REACHED = frozenset(('дозвон',))
# Статусы «лид ещё не в работе». Считать их обработанными нельзя: «Новый» — это
# лид, до которого никто не дошёл, и он не должен портить конверсию оператора.
_STREAM_CALL_UNTOUCHED = frozenset(('новый', 'в работе'))

# Диалоги, которые при ПУСТОМ статусе звонка означают, что разговора не было.
_STREAM_DIALOG_NOT_REACHED = frozenset(('конечный недозвон', 'неверный номер'))

_STREAM_DIALOG_AGREED = frozenset((
    'приглашен', 'приглашён', 'вышел на линию', 'согласился перейти', 'согласился выйти',
))
_STREAM_DIALOG_SUCCESS = frozenset(('вышел на линию',))
_STREAM_DIALOG_REJECT = frozenset(('отказ',))
_STREAM_DIALOG_CALLBACK = frozenset(('перезвон назначен',))

# ── Платный найм: статус лида = корзина ──────────────────────────────────────
# Значение → (достижимость, исход диалога, корзина причины).
_PAID_HIRE_STATUS = {
    'вышел на линию':   ('dozvon', 'success', ''),
    'приглашен':        ('dozvon', 'agree', ''),
    'приглашён':        ('dozvon', 'agree', ''),
    'отказ':            ('dozvon', 'reject', 'otkaz'),
    'нецелевой':        ('dozvon', 'untargeted', 'netsel'),
    # Перезвон назначен — это состоявшийся контакт: договорились о времени.
    # В живых данных у таких лидов лежат комментарии вида «взял 3 лицо, сказал
    # перезвонить через 2-3 часа», то есть трубку сняли.
    'перезвон назначен': ('dozvon', 'callback', ''),
    'недозвон':         ('nedozvon', 'none', 'nedozvon'),
    'новый':            ('new', 'none', ''),
    'в работе':         ('new', 'none', ''),
    'без статуса':      ('new', 'none', ''),
}

# Коды причин платного найма, у которых в СРМ нет русской подписи: ручка отдаёт
# их «как есть», и на экране они выглядели бы как not_available. Подписи наши.
PAID_HIRE_REASON_TITLES = {
    'not_available': 'Недоступен',
    'call_reset': 'Сброс',
    'barrier_not_passed': 'Не прошёл барьер 5 звонков',
    'invalid_number': 'Неправильный номер',
    'blacklist': 'Аккаунт в блоке (ЧС)',
    'error_reg': 'Ошибочная регистрация',
    'no_data': 'Нет необходимых данных',
    'competitor': 'Работает с ДТ (конкурент)',
    'no_auto': 'Нет авто',
    'deleted_account': 'Аккаунт на удалении',
    'park_com_dislike': 'Не устраивает комиссия парка',
    'service_com_dislike': 'Не устраивает комиссия сервиса',
    'third_person': '3-е лицо',
    'bad_rights': 'Права не подходят',
}

# Эти четыре кода означают «разговора не было», и по ним статус лида в СРМ
# действительно «Недозвон» — набор нужен, чтобы не отнести их в отказы, если
# статус почему-то пришёл другой.
PAID_HIRE_NOT_REACHED_CODES = frozenset((
    'not_available', 'call_reset', 'barrier_not_passed', 'invalid_number',
))

# ── Поток: причины отказа свободным текстом ──────────────────────────────────
# За 01.08–11.09.2026 в поле приехало 40 разных написаний, включая «не интеросно»,
# «своих не предаю дид» и «акк в блоке на полгода». Свод ниже — сид справочника
# op_funnel_reason_dict: он приводит опечатки к одной причине и называет корзину.
# Дальше справочник правит человек в разделе, без релиза.
STREAM_REASON_SEED = (
    # (код, подпись, корзина, синонимы в нижнем регистре)
    ('reset', 'Сброс', 'nedozvon', ('сброс',)),
    ('invalid_number', 'Неправильный номер', 'nedozvon',
     ('не правильный номер', 'неправильный номер', 'неверный номер')),
    ('silent', 'Молчит', 'nedozvon', ('молчит',)),
    ('not_yandex', 'Не работает с Яндекс', 'otkaz', ('не работает с яндекс',)),
    ('competitor', 'Работа с ДТ', 'otkaz', ('работа с дт', 'работает с дт')),
    ('no_auto', 'Нет авто', 'netsel', ('нет авто',)),
    ('car_repair', 'Авто на ремонте', 'otkaz', ('авто на ремонте',)),
    ('entrepreneur', 'ИП', 'otkaz', ('ип',)),
    ('blacklist', 'ЧС', 'netsel', ('чс', 'акк заблокирован', 'акк в блоке на полгода')),
    ('health', 'Проблемы со здоровьем', 'otkaz', ('проблемы со здоровьем',)),
    ('no_tariff', 'Тариф в городе отсутствует', 'otkaz',
     ('тариф в данном городе отсутствует', 'тариф в городе отсутствует')),
    ('invited_offline', 'Приглашён на ВВ', '', ('приглашен на вв', 'приглашён на вв')),
    ('not_interested', 'Не интересно', 'otkaz',
     ('не интересно', 'не интеросно', 'неинтересно', 'не интересует', 'пока не интересует',
      'не нужно', 'не интересует работа с таксопарком')),
    ('other_job', 'Есть основная работа', 'otkaz',
     ('работа', 'основная работа', 'есть основная работа', 'времени нет учеба', 'нет времени')),
    ('rare_online', 'Редко выходит на линию', 'otkaz',
     ('редко выходит на линию', 'не выходит часто', 'редко выполняет заказы',
      'не планирует выходить')),
    ('commission', 'Высокая комиссия', 'otkaz', ('высокая комиссия', 'процент много')),
    ('documents', 'Проблемы с документами', 'netsel', ('проблемы с документами', 'внж')),
    ('not_in_city', 'Не в городе', 'otkaz',
     ('не в городе', 'не в городе работает на вахте', 'не будет в городе до весны')),
    ('later', 'Вернётся позже', 'otkaz',
     ('через месяц может только', 'до нового года не планирует')),
    ('unspecified', 'Не уточнял', '', ('не уточнял',)),
)

# Корзина по умолчанию для незнакомой причины «Потока». Отказ, а не пустота:
# причину пишут в момент отказа, и молча терять её из разбивки хуже, чем
# положить в самую вероятную корзину — человек переложит её в справочнике.
STREAM_DEFAULT_BUCKET = 'otkaz'

# ── amoCRM: корзина зашита в подпись причины ─────────────────────────────────
_AMO_BUCKET_BY_SUFFIX = (
    ('(недозвон)', 'nedozvon'),
    ('(не цел)', 'netsel'),
    ('(отказ)', 'otkaz'),
)

# Корзина BUCKET_MOVED — «лид не отказал, его увели».
#
# В amoCRM причиной закрытия работают не только отказы: в справочнике из 27
# причин семь штук — это НАЗВАНИЯ ЭТАПОВ И ВОРОНОК («Диалоги», «YaPROREG»,
# «Дожим приглашенные», «Звонки (не закрытые)», «Отправлен в офис (аренда)»).
# Ими помечают лид, ушедший в другой процесс.
#
# Сводные листы супервайзера их НЕ СЧИТАЮТ: в «Причинах отказов» ровно
# одиннадцать колонок, и ни одной из этих там нет. Считать их отказами — значит
# завысить отказы вдвое: на 01.09 у одного оператора получалось 14 отказов
# вместо 7 (проверено 11.09.2026 на файле задачи #302).
BUCKET_MOVED = 'moved'

# Причины отказа amoCRM без суффикса-подсказки, разложенные по корзинам ровно
# так, как они разложены в сводных листах файла задачи #302. Суффикс в подписи
# («(отказ)», «(не цел)», «(недозвон)») сильнее этого списка и проверяется первым;
# список нужен там, где подписи ничего не говорят.
_AMO_REASON_BUCKETS = {
    # «Причины отказов 2», одиннадцать колонок.
    'высокая комиссия': 'otkaz',
    'консультация по ип получена': 'otkaz',
    'консультация получена': 'otkaz',
    'нас нет в данном городе': 'otkaz',
    'не устраивает акция': 'otkaz',
    'нет необходимых данных': 'otkaz',
    'нет необходимых документов': 'otkaz',
    'работает с дт': 'otkaz',
    'без причины': 'otkaz',
    'перезвон фокус группы': 'otkaz',
    'зн не подходит под условиям аренды': 'otkaz',
    'зн не подходит по условиям (аренда)': 'otkaz',
    'отказ клиента (аренда)': 'otkaz',
    'отказ клиента(аренда)': 'otkaz',
    # «Причины нецелевых 2», семь колонок.
    'вопрос для тех поддержки': 'netsel',
    'не оставлял заявку': 'netsel',
    'не работает с яндекс': 'netsel',
    'нет 18 лет': 'netsel',
    'нет авто': 'netsel',
    'пассажир': 'netsel',
    'третье лицо': 'netsel',
    # «Причины недозвонов 2», пять колонок.
    'сброс': 'nedozvon',
    'перезвон': 'nedozvon',
    'недозвон': 'nedozvon',
    'не прошел барьер 5-х звонков': 'nedozvon',
    'неправильный номер': 'nedozvon',
    # Увели в другой процесс — в разбивку не идут.
    'диалоги': BUCKET_MOVED,
    'yapro reg': BUCKET_MOVED,
    'yaproreg': BUCKET_MOVED,
    'дожим приглашенные': BUCKET_MOVED,
    'дожим приглашённые': BUCKET_MOVED,
    'звонки (не закрытые)': BUCKET_MOVED,
    'звонки': BUCKET_MOVED,
    'отправлен в офис (аренда)': BUCKET_MOVED,
    'отправлен в офис (аренда электро)': BUCKET_MOVED,
}

# Этапы воронки 5524684 «Отдел продаж» (снято живьём 11.09.2026).
_AMO_STAGE_NOT_REACHED = frozenset((
    'недозвон 1', 'недозвон 2', 'недозвон 3', 'недозвон 4',
))
_AMO_STAGE_AGREED = frozenset((
    'приглашен на регистрацию', 'приглашён на регистрацию', 'дожим приглашенные',
    'дожим приглашённые', 'отправлен в офис (аренда)',
))
# 142 в ЭТОЙ воронке называется «ПРОШЕЛ РЕГИСТРАЦИЮ», а в четырёх остальных —
# «Успешно реализовано». Текущая ночная выгрузка собирает справочник этапов по
# всем воронкам подряд и перетирает имя, поэтому в базе лежит чужая подпись.
# Считаем успех и по имени, и по номеру статуса — что придёт, то и сработает.
_AMO_STAGE_SUCCESS = frozenset((
    'прошел регистрацию', 'прошёл регистрацию', 'успешно реализовано',
))
AMO_SUCCESS_STATUS_ID = 142
AMO_LOST_STATUS_ID = 143
AMO_SALES_PIPELINE_ID = 5524684
_AMO_STAGE_UNTOUCHED = frozenset((
    'неразобранное', 'новая заявка', 'принято в работу', 'звонки', 'диалоги',
))
_AMO_STAGE_CALLBACK = frozenset(('перезвон',))


def _low(value):
    """Ключ сравнения: без регистра, без краевых пробелов, без двойных внутри.

    Пробелы внутри сжимаются не ради красоты: в выгрузках встречается «Санақ
    Ерсұлтан  » с двумя пробелами на конце и « Касимбаев Алибек» с пробелом
    впереди, и без этого один человек стал бы тремя.
    """
    return ' '.join(str(value or '').strip().lower().split())


def _is_blank_status(value):
    """Пустой статус источника. В выгрузках пустота приходит четырьмя видами:
    None, '', '-' и строка из пробелов."""
    text = _low(value)
    return text in ('', '-', 'none', '—')


def normalize_reason_key(value):
    """Ключ причины из свободного текста. Только для поиска по синонимам —
    подпись на экране берётся из справочника, а не отсюда."""
    text = _low(value)
    if not text:
        return ''
    return ''.join(ch for ch in text if ch.isalnum() or ch == ' ').strip()


def build_reason_index(seed=STREAM_REASON_SEED):
    """синоним → (код, подпись, корзина). Сид для справочника и для разбора."""
    index = {}
    for code, title, bucket, aliases in seed:
        for alias in (code,) + tuple(aliases):
            index[normalize_reason_key(alias)] = (code, title, bucket)
        index[normalize_reason_key(title)] = (code, title, bucket)
    return index


_STREAM_REASON_INDEX = build_reason_index()


# ── Разбор одного лида ───────────────────────────────────────────────────────
# Все три разборщика возвращают один и тот же словарь, чтобы sync.py не знал,
# из какого источника пришла строка.

def _outcome(reach='new', dialog='none', bucket='', reason_code='', reason_title=''):
    return {
        'reach_outcome': reach,
        'dialog_outcome': dialog,
        'reason_bucket': bucket,
        'reason_code': reason_code,
        'reason_title': reason_title,
    }


def classify_stream_lead(call_status, dialog_status, reject_reason, reason_index=None):
    """«Поток»: два статуса плюс свободная причина отказа.

    Порядок проверок повторяет формулу Excel буквально, включая ветку «статус
    звонка пуст, а диалог есть» — см. шапку модуля.
    """
    index = reason_index if reason_index is not None else _STREAM_REASON_INDEX
    call = _low(call_status)
    dialog = _low(dialog_status)

    if call in _STREAM_CALL_REACHED:
        reach = 'dozvon'
    elif _is_blank_status(call_status):
        # Разговор был, если диалог заполнен и он не про «не дозвонились».
        if dialog and dialog not in _STREAM_DIALOG_NOT_REACHED:
            reach = 'dozvon'
        else:
            # Статус звонка не проставлен, а диалог говорит «не дозвонились».
            # Excel такие лиды ИСКЛЮЧАЕТ и из дозвона, и из «Исхода»: формула
            # вычитает их из дозвона и не добавляет к недозвону. Повторяем
            # буквально — иначе не выполняется критерий приёмки «цифры совпадают
            # с посчитанными вручную». Сверено на 140 строках за 01–07.09.2026:
            # с этой веткой расхождений ноль, без неё «Исход» завышался на 38
            # лидов из 5787 (0,7 %) — ровно на эти 37 «Неверный номер» и один
            # «Конечный недозвон».
            reach = 'new'
    elif call in _STREAM_CALL_NOT_REACHED:
        reach = 'nedozvon'
    elif call in _STREAM_CALL_UNTOUCHED:
        reach = 'new'
    else:
        # Незнакомый статус. «Новый», а не «дозвон»: приписать оператору
        # несуществующий разговор хуже, чем недосчитать.
        reach = 'new'

    if dialog in _STREAM_DIALOG_SUCCESS:
        outcome = 'success'
    elif dialog in _STREAM_DIALOG_AGREED:
        outcome = 'agree'
    elif dialog in _STREAM_DIALOG_REJECT:
        outcome = 'reject'
    elif dialog in _STREAM_DIALOG_CALLBACK or call in _STREAM_DIALOG_CALLBACK:
        outcome = 'callback'
    else:
        outcome = 'none'

    reason_code = reason_title = bucket = ''
    key = normalize_reason_key(reject_reason)
    if key:
        found = index.get(key)
        if found:
            reason_code, reason_title, bucket = found
        else:
            # Причина есть, а в справочнике её нет — заводим по сырому тексту.
            # Так новая причина появляется в разбивке в тот же день, а человек
            # потом переложит её в нужную корзину.
            reason_code = key[:64]
            reason_title = str(reject_reason).strip()[:200]
            bucket = STREAM_DEFAULT_BUCKET
    elif outcome == 'reject':
        # Отказ без причины — это тоже строка разбивки, иначе сумма причин не
        # сойдётся с числом отказов, и колонка выглядит сломанной.
        reason_code, reason_title, bucket = 'no_reason', 'Без причины', 'otkaz'

    # Причина недозвона не делает лид дозвоном и наоборот: корзина причины и
    # достижимость независимы, кроме одного случая — «Сброс» у дозвонившегося.
    if bucket == 'nedozvon' and reach == 'dozvon' and outcome in ('none', 'callback'):
        reach = 'nedozvon'

    return _outcome(reach, outcome, bucket, reason_code, reason_title)


def classify_paid_hire_lead(status, closed_reason_code, closed_reason, closed_sub_reason=''):
    """«Яндекс Регистрация»: один статус, он же называет корзину причины."""
    reach, outcome, bucket = _PAID_HIRE_STATUS.get(_low(status), ('new', 'none', ''))

    code = _low(closed_reason_code).replace(' ', '_')[:64]
    if not code:
        code = normalize_reason_key(closed_reason)[:64]
    title = PAID_HIRE_REASON_TITLES.get(code) or (str(closed_reason or '').strip()[:200])

    if code in PAID_HIRE_NOT_REACHED_CODES:
        # Код говорит «разговора не было». Верим коду, а не статусу: статус
        # оператор мог не переставить, а код проставляется выбором причины из
        # списка. Исход диалога при этом НЕ гасим: «Перезвон назначен» с кодом
        # not_available — это живой случай («взял 3 лицо, сказал перезвонить
        # через 2-3 часа»), и в разбивке диалогов он должен остаться перезвоном,
        # как в файле супервайзера, где «Перезвон» стоит и среди причин
        # недозвона, и отдельной колонкой воронки диалогов.
        reach, bucket = 'nedozvon', 'nedozvon'

    if bucket and not code:
        code, title = 'no_reason', 'Без причины'
    if code and not bucket:
        # Причина у лида есть, а корзины по статусу нет (например, «Приглашен» с
        # проставленной причиной) — в разбивку такой лид не идёт.
        code = title = ''

    sub = str(closed_sub_reason or '').strip()
    if sub and title:
        title = '%s — %s' % (title, sub[:80])

    return _outcome(reach, outcome, bucket, code, title)


_AMO_LOST_STAGE_PREFIX = 'закрыто и не реализовано'


def amo_split_stage(stage):
    """Разделить «Закрыто и не реализовано (Нет авто (не цел))» на этап и причину.

    Выгрузка amoCRM приклеивает причину закрытия к названию этапа в скобках —
    отдельной колонки с причиной в файле супервайзера почти всегда нет. Берём
    от ПЕРВОЙ открывающей скобки до ПОСЛЕДНЕЙ закрывающей: у причины внутри
    бывают свои скобки («Не прошел барьер 5-х звонков (недозвон)»).
    """
    text = str(stage or '').strip()
    low = _low(text)
    if not low.startswith(_AMO_LOST_STAGE_PREFIX):
        return text, ''
    rest = text[len(_AMO_LOST_STAGE_PREFIX):].strip()
    if rest.startswith('(') and rest.endswith(')'):
        return text[:len(_AMO_LOST_STAGE_PREFIX)].strip(), rest[1:-1].strip()
    return text, ''


def amo_reason_bucket(loss_reason):
    """Корзина причины amoCRM. Суффикс подписи сильнее списка: «Нет авто (не
    цел)» → netsel, даже если «нет авто» в списке значится иначе."""
    text = _low(loss_reason)
    if not text:
        return ''
    for suffix, bucket in _AMO_BUCKET_BY_SUFFIX:
        if suffix in text:
            return bucket
    if text in _AMO_REASON_BUCKETS:
        return _AMO_REASON_BUCKETS[text]
    # Причина без подсказки и не из списка — это НЕ отказ. Помечаем «увели»:
    # в разбивку такой лид не идёт и отказы не завышает (см. BUCKET_MOVED).
    return BUCKET_MOVED


def classify_amo_lead(stage, loss_reason=None, status_id=None):
    """«Основа ОП»: этап воронки 5524684 плюс причина закрытия.

    `loss_reason` необязателен: если его не передали, причина достаётся из самого
    названия этапа — именно так она и приезжает в выгрузке.
    """
    base_stage, glued_reason = amo_split_stage(stage)
    reason = loss_reason if (loss_reason not in (None, '')) else glued_reason
    # Причина, равная самому этапу, причиной не является: так бывает, когда в
    # выгрузке колонку причины заполнили копией этапа.
    if _low(reason) == _low(stage):
        reason = glued_reason

    stage_key = _low(base_stage)

    if status_id == AMO_SUCCESS_STATUS_ID or stage_key in _AMO_STAGE_SUCCESS:
        return _outcome('dozvon', 'success', '', '', '')

    if stage_key in _AMO_STAGE_AGREED:
        return _outcome('dozvon', 'agree', '', '', '')

    if stage_key in _AMO_STAGE_NOT_REACHED:
        return _outcome('nedozvon', 'none', 'nedozvon',
                        normalize_reason_key(base_stage)[:64] or 'nedozvon',
                        base_stage[:200])

    if stage_key in _AMO_STAGE_CALLBACK:
        return _outcome('dozvon', 'callback', '', '', '')

    if stage_key in _AMO_STAGE_UNTOUCHED:
        return _outcome('new', 'none', '', '', '')

    # Остались закрытые лиды (этап 143). Что это было — говорит причина.
    bucket = amo_reason_bucket(reason)
    code = normalize_reason_key(reason)[:64]
    title = str(reason or '').strip()[:200]

    if bucket == BUCKET_MOVED or not bucket:
        # Лид увели в другой процесс: ни отказ, ни нецелевой, ни недозвон.
        # В «обработано» он попадёт (оператор его трогал), в разбивку — нет.
        return _outcome('new', 'none', BUCKET_MOVED, code or 'moved',
                        title or 'Закрыт без причины')

    if bucket == 'nedozvon':
        return _outcome('nedozvon', 'none', bucket, code or 'nedozvon',
                        title or 'Недозвон')
    outcome = 'untargeted' if bucket == 'netsel' else 'reject'
    return _outcome('dozvon', outcome, bucket, code or 'no_reason',
                    title or 'Без причины')


# ── Суточный агрегат ─────────────────────────────────────────────────────────

DAILY_COUNTERS = (
    'handled', 'reached', 'not_reached', 'agreed', 'succeeded',
    'rejected', 'untargeted', 'callbacks', 'inbound',
    # Все строки источника за сутки, включая те, до которых не дошли, и те, что
    # увели в другой процесс. Нужен, потому что «обработано» у направлений
    # считается ПО-РАЗНОМУ (см. handled_for_source).
    'leads_total', 'moved',
)

# Как считается «обработано лидов» у каждого источника.
#
# Разница настоящая, а не оплошность файлов. В «Потоке» и «Платном найме»
# выгрузка отдаёт ВСЮ базу, включая лиды со статусом «Новый», до которых никто
# не дошёл, — их в «обработано» быть не должно, поэтому там «Исход = дозвон +
# недозвон». Выгрузка amoCRM устроена иначе: в неё попадает только то, что
# оператор трогал, и «обработано» там — это просто число строк. Проверено на
# файлах задач #303 и #302 (11.09.2026): у «Основы» число строк совпадает с
# колонкой «Обработаны лиды» вплоть до оператора, а «дозвон + недозвон» — нет.
HANDLED_AS_TOTAL_SOURCES = frozenset(('amo',))


def handled_for_source(acc, source):
    """«Обработано лидов» по правилам источника."""
    if source in HANDLED_AS_TOTAL_SOURCES:
        return int(acc.get('leads_total') or 0)
    return int(acc.get('reached') or 0) + int(acc.get('not_reached') or 0)


def empty_daily():
    return {name: 0 for name in DAILY_COUNTERS}


def add_lead(acc, outcome, is_inbound=False):
    """Досчитать один разобранный лид в суточный агрегат.

    `handled` считается как «дозвон + недозвон» и НЕ включает лиды, до которых
    не дошли, — это и есть «Исход» из файла супервайзера.
    """
    reach = outcome.get('reach_outcome') or 'new'
    dialog = outcome.get('dialog_outcome') or 'none'

    acc['leads_total'] += 1
    if is_inbound:
        acc['inbound'] += 1
    if (outcome.get('reason_bucket') or '') == BUCKET_MOVED:
        acc['moved'] += 1

    if reach == 'dozvon':
        acc['reached'] += 1
        acc['handled'] += 1
    elif reach == 'nedozvon':
        acc['not_reached'] += 1
        acc['handled'] += 1

    if dialog == 'success':
        # Успех — это и согласие тоже: человек согласился и вышел. Иначе
        # «% согласий от дозвона» провалился бы у лучших операторов.
        acc['succeeded'] += 1
        acc['agreed'] += 1
    elif dialog == 'agree':
        acc['agreed'] += 1
    elif dialog == 'reject':
        acc['rejected'] += 1
    elif dialog == 'untargeted':
        acc['untargeted'] += 1
    elif dialog == 'callback':
        acc['callbacks'] += 1

    return acc


def aggregate_leads(leads):
    """leads: итерируемое из словарей с полями исхода. Возвращает агрегат."""
    acc = empty_daily()
    for lead in leads:
        add_lead(acc, lead, bool(lead.get('is_inbound')))
    return acc


# ── Производные показатели ───────────────────────────────────────────────────

def _ratio(numerator, denominator):
    """Доля или None. None, а не ноль: «нет данных» и «ноль процентов» — разные
    вещи, и на экране они выглядят по-разному («—» против «0 %»)."""
    try:
        top = float(numerator or 0)
        bottom = float(denominator or 0)
    except (TypeError, ValueError):
        return None
    if bottom <= 0:
        return None
    return top / bottom


def derive_rates(row):
    """Конверсии по одной строке (оператор за период или итог команды).

    Знаменатели зафиксированы решением владельца от 11.09.2026, см. шапку.
    """
    handled = row.get('handled') or 0
    reached = row.get('reached') or 0
    agreed = row.get('agreed') or 0
    hours = float(row.get('work_hours') or 0)

    return {
        # % дозвона — единый стандарт: от всей взятой в работу базы.
        'reach_rate': _ratio(reached, handled),
        # Второй знаменатель оставлен ОТДЕЛЬНЫМ показателем, а не заменой: он
        # отвечает на другой вопрос — «какого качества были попытки».
        'attempt_rate': _ratio(reached, reached + (row.get('not_reached') or 0)),
        'agree_rate': _ratio(agreed, reached),
        'success_rate': _ratio(row.get('succeeded'), agreed),
        'success_of_reached': _ratio(row.get('succeeded'), reached),
        'reject_rate': _ratio(row.get('rejected'), reached),
        'untargeted_rate': _ratio(row.get('untargeted'), reached),
        'closed_rate': _ratio((row.get('rejected') or 0) + (row.get('untargeted') or 0), handled),
        'leads_per_hour': _ratio(handled, hours),
        'plan_reached_rate': _ratio(reached, row.get('plan_reached')),
        'plan_agreed_rate': _ratio(agreed, row.get('plan_agreed')),
        # Верификаторы: нагрузка и отклонение от таргета.
        'chats_per_hour': _ratio((row.get('chats') or 0) + (row.get('tickets') or 0), hours),
    }


def verificator_rates(row, targets):
    """Показатели Верификатора против таргетов смены (лист «Воронка», #305).

    «Разница от таргета» в файле считается как `таргет / факт − 1`, то есть
    положительная разница — это ХОРОШО (ответили быстрее таргета). Оставляем
    ровно так: супервайзер читает эти числа каждый день и привык к их знаку.
    """
    handled = (row.get('chats') or 0) + (row.get('tickets') or 0)
    hours = float(row.get('work_hours') or 0)
    per_hour = _ratio(handled, hours)

    chats_target = targets.get('chats_per_hour')
    reply_target = targets.get('reply_seconds')

    reply = row.get('chat_reply_seconds')
    ticket = row.get('ticket_handle_seconds')

    return {
        'handled': handled,
        'chats_per_hour': per_hour,
        'chats_plan_rate': _ratio(per_hour, chats_target) if per_hour is not None else None,
        'reply_gap': (_ratio(reply_target, reply) - 1) if _ratio(reply_target, reply) is not None else None,
        'ticket_gap': (_ratio(reply_target, ticket) - 1) if _ratio(reply_target, ticket) is not None else None,
        'quality_rate': _ratio(row.get('quality_score'), targets.get('quality')),
    }


def plan_for_day(work_hours, targets, reached=None):
    """План на сутки. Ноль часов — ноль плана, а не пустота: иначе «выполнение» у
    невыходившего человека считалось бы делением на ноль.

    План у направлений задаётся ТРЕМЯ разными способами, и брать только первый
    нельзя — у «Основы» и «Яндекс Регистрации» норм в час нет вовсе, и план
    выходил бы нулевым всегда, а колонка «% плана» — вечным прочерком:

    * **норма в час** («Поток»: 20 дозвонов и 5 согласий) — часы × норму;
    * **месячный план на ставку** («Основа», «Верификатор») — та же формула, что
      в калькуляторе зарплат: `план на FTE ÷ норму часов × отработанные часы`
      (`calculateOsnovaMonthlyPlan` в `src/utils/salaryFormula.js`). Коэффициенты
      обязаны совпадать с калькулятором — расходиться им нельзя;
    * **целевая конверсия** («Яндекс Регистрация», 0,5) — план согласий считается
      от фактических дозвонов, а не от часов: там план на человека не ставят,
      требуют долю.
    """
    hours = float(work_hours or 0)
    norm_reached = float(targets.get('reached_per_hour') or 0)
    norm_agreed = float(targets.get('agreed_per_hour') or 0)

    plan_reached = hours * norm_reached
    plan_agreed = hours * norm_agreed

    if not norm_agreed:
        per_fte = float(targets.get('plan_per_fte') or 0)
        norm_hours = float(targets.get('norm_hours_fte') or 0)
        if per_fte and norm_hours:
            plan_agreed = per_fte / norm_hours * hours
        elif targets.get('target_conversion') and reached is not None:
            plan_agreed = float(reached) * float(targets['target_conversion'])

    return {
        'plan_reached': round(plan_reached, 2),
        'plan_agreed': round(plan_agreed, 2),
    }


def monthly_plan(rate, targets, worked_hours=None, norm_hours=None, newbie=False):
    """Месячный план продаж на человека: план на 1 FTE × ставку.

    Та же формула, что в калькуляторах зарплат (`src/utils/salaryFormula.js`):
    новичку план ×0,8, у ночной смены своя строка таргета. Считаем здесь, а не
    берём из калькулятора, потому что у воронки план нужен подневно, а
    калькулятор месячный, — но КОЭФФИЦИЕНТЫ одни и те же, и расходиться им нельзя.
    """
    per_fte = float(targets.get('plan_per_fte') or 0)
    plan = per_fte * float(rate or 0)
    if newbie:
        plan *= 0.8
    if norm_hours and worked_hours is not None:
        # Отработал меньше нормы — план пропорционально меньше. Так считает и
        # калькулятор: иначе человек, вышедший на полмесяца, заведомо в минусе.
        share = _ratio(worked_hours, norm_hours)
        if share is not None:
            plan = per_fte * float(rate or 0) * min(share, 1.0) * (0.8 if newbie else 1.0)
    return round(plan, 1)


# ── Цветовая индикация ───────────────────────────────────────────────────────

def color_bucket(rate, green_from=1.0, amber_from=0.8):
    """Куда покрасить % выполнения плана. Возвращает '', 'green', 'amber', 'red'.

    Пустая строка — это «не красим»: нейтральное состояние цветом не помечается
    (требование владельца — цвет только там, где несёт смысл). Нет данных тоже
    не красим: серый прочерк честнее зелёного нуля.
    """
    if rate is None:
        return ''
    try:
        value = float(rate)
    except (TypeError, ValueError):
        return ''
    if value >= float(green_from):
        return 'green'
    if value >= float(amber_from):
        return 'amber'
    return 'red'


# ── Флаги аномалий ───────────────────────────────────────────────────────────
# Раздел 6.7 ТЗ #301. Все три случая взяты из живых отчётов, а не придуманы.

ANOMALY_ZERO_HOURS = 'zero_hours_with_leads'
ANOMALY_VOLUME_JUMP = 'volume_jump'
ANOMALY_BELOW_TARGET = 'below_target_streak'

# Во сколько раз объём должен вырасти, чтобы это считалось подозрительным.
VOLUME_JUMP_FACTOR = 2.0
# Ниже этого объёма скачок ничего не значит: с 2 лидов до 5 — это не дубль базы.
VOLUME_JUMP_MIN_BASE = 10
# Сколько суток подряд ниже порога считаем поводом сказать супервайзеру.
BELOW_TARGET_DAYS = 2


def detect_anomalies(rows, amber_from=0.8, days_in_streak=BELOW_TARGET_DAYS):
    """rows: суточные строки одного направления, каждая с user_id, work_day,
    work_hours, handled, plan_reached, reached. Возвращает список находок.

    Считается по подневным строкам, а не по итогу периода: «0 часов при 40
    обработанных лидах» видно только в конкретных сутках.
    """
    by_user = {}
    for row in rows:
        user_id = row.get('user_id')
        # Строка «не сопоставлен» — это не человек, а пробел в сопоставлении.
        # У неё часов нет по определению (часы считаются по сотрудникам), и флаг
        # «0 часов при N лидах» загорался бы на ней каждый день, приучая
        # супервайзера не смотреть на аномалии вовсе. Про сам пробел раздел
        # сообщает отдельно — счётчиком несопоставленных в шапке.
        if not user_id:
            continue
        by_user.setdefault(user_id, []).append(row)

    found = []
    for user_id, user_rows in by_user.items():
        user_rows = sorted(user_rows, key=lambda r: r.get('work_day'))
        streak = 0
        previous_handled = None

        for row in user_rows:
            handled = int(row.get('handled') or 0)
            hours = float(row.get('work_hours') or 0)

            # 1. Часы нулевые, а работа есть — разрыв в данных, а не подвиг.
            if handled > 0 and hours <= 0:
                found.append({
                    'kind': ANOMALY_ZERO_HOURS,
                    'user_id': user_id,
                    'work_day': row.get('work_day'),
                    'detail': {'handled': handled},
                })

            # 2. Объём подскочил кратно — похоже на дубль в базе.
            if (previous_handled is not None and previous_handled >= VOLUME_JUMP_MIN_BASE
                    and handled >= previous_handled * VOLUME_JUMP_FACTOR):
                found.append({
                    'kind': ANOMALY_VOLUME_JUMP,
                    'user_id': user_id,
                    'work_day': row.get('work_day'),
                    'detail': {'was': previous_handled, 'became': handled},
                })
            previous_handled = handled

            # 3. Несколько суток подряд ниже порога.
            rate = _ratio(row.get('reached'), row.get('plan_reached'))
            if rate is not None and rate < float(amber_from):
                streak += 1
                if streak == int(days_in_streak):
                    found.append({
                        'kind': ANOMALY_BELOW_TARGET,
                        'user_id': user_id,
                        'work_day': row.get('work_day'),
                        'detail': {'days': int(days_in_streak), 'rate': round(rate, 3)},
                    })
            elif rate is not None:
                streak = 0

    return found


# ── Сравнение периодов ───────────────────────────────────────────────────────

COMPARABLE_METRICS = (
    'handled', 'reached', 'agreed', 'succeeded', 'rejected', 'untargeted',
    'work_hours', 'reach_rate', 'agree_rate', 'success_rate', 'leads_per_hour',
    'plan_reached_rate',
)

# Показатели, у которых рост — это плохо. Стрелка та же, а цвет обратный.
LOWER_IS_BETTER = frozenset(('rejected', 'untargeted', 'untargeted_rate', 'reject_rate'))


def previous_period(day_from, day_to):
    """Предыдущий период той же длины, встык. Для сравнения «эта неделя против
    прошлой» из раздела 5 ТЗ."""
    length = (day_to - day_from).days + 1
    end = day_from - timedelta(days=1)
    return end - timedelta(days=length - 1), end


def compare(current, base, metrics=COMPARABLE_METRICS):
    """Дельты между двумя сводками. None там, где сравнивать нечего."""
    out = {}
    for name in metrics:
        now = current.get(name)
        was = base.get(name)
        if now is None or was is None:
            out[name] = {'now': now, 'was': was, 'delta': None, 'direction': ''}
            continue
        delta = float(now) - float(was)
        if abs(delta) < 1e-9:
            direction = 'flat'
        elif (delta > 0) != (name in LOWER_IS_BETTER):
            direction = 'up'
        else:
            direction = 'down'
        out[name] = {'now': now, 'was': was, 'delta': delta, 'direction': direction}
    return out


def month_bounds(day):
    """Границы месяца, в который попал день. Нужны месячному плану."""
    first = day.replace(day=1)
    if first.month == 12:
        return first, date(first.year, 12, 31)
    return first, date(first.year, first.month + 1, 1) - timedelta(days=1)
