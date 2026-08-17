# -*- coding: utf-8 -*-
"""Сценарии обращений: тематики, вопросы, обязательные проверки и правила отправки.

Это сердце задачи #160. Смысл ТЗ в одной фразе: оператор НЕ пишет свободный
текст, а проходит готовый сценарий, и обращение уходит в группу только если
все обязательные проверки выполнены. Всё остальное здесь — следствие.

Почему сценарии объявлены данными, а не кодом с if'ами. Их шесть, у каждого до
шестнадцати вопросов и до шести правил блокировки, и правила ссылаются друг на
друга («документы не отображаются» переводит из тематики №2 в №1). В виде
структуры это читается рядом с ТЗ строка в строку, проверяется тестами целиком
и не требует лезть в поток управления, чтобы поправить формулировку.

Почему в Python, а не в таблице с редактором. Ровно как каталог заявок в IT
(IT_TICKET_CATALOG): правила тут не «настройка», а согласованный регламент —
менять их должен тот, кто меняет ТЗ, через ревью, а не мышкой в проде. Таблицу
под переопределение можно добавить позже, схема к этому готова.

Модуль чистый: ни базы, ни Flask, ни сети. Поэтому весь регламент проверяется
юнит-тестами без окружения (tests/test_crm_scenarios.py).
"""

import re

# ─────────────────────────────────────────────────────────────────────────────
# Типы шагов
#
# Тип решает три вещи сразу: чем спрашивать на экране, как проверить ответ и как
# он выглядит в готовом тексте для группы.
# ─────────────────────────────────────────────────────────────────────────────

IIN = 'iin'                 # ИИН водителя: ровно 12 цифр
PERIOD = 'period'           # отчётный период: месяц и год
TEXT = 'text'               # свободная строка (парк, текст ошибки)
LONGTEXT = 'longtext'       # многострочное описание
CHOICE = 'choice'           # один вариант из списка
YESNO = 'yesno'             # да / нет
YESNO_DATE = 'yesno_date'   # да / нет, при «да» — дата
DATETIME = 'datetime'       # дата и время
ATTACHMENT = 'attachment'   # вложение (скриншот / видео)

# Требование к вложению у тематики.
ATTACH_NONE = 'none'
ATTACH_IMAGE = 'image'          # только скриншот
ATTACH_IMAGE_OR_VIDEO = 'media'  # скриншот или видео/запись экрана

# Чем кончается прохождение сценария.
READY = 'ready'        # можно отправлять в группу
BLOCKED = 'blocked'    # обязательная проверка не выполнена — вернуть оператора к шагу
CLOSE = 'close'        # вопрос решился по ходу: закрыть без сообщения в группу
SWITCH = 'switch'      # это другая тематика — перевести туда
INCOMPLETE = 'incomplete'  # не хватает ответов или вложения

# Метка на обращении (не блокирует отправку, но видна и в группе, и в карточке).
FLAG_MASS_OUTAGE = 'mass_outage'
FLAG_LABELS = {FLAG_MASS_OUTAGE: 'Возможный массовый сбой'}

_IIN_RE = re.compile(r'^\d{12}$')
_PERIOD_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')  # YYYY-MM


def step(key, label, kind, **extra):
    item = {'key': key, 'label': label, 'kind': kind}
    item.update(extra)
    return item


def rule(when, outcome, message, **extra):
    """Правило «когда бот не отправляет запрос».

    when — (ключ шага, значение). Совпало → сработало.
    """
    item = {'when': when, 'outcome': outcome, 'message': message}
    item.update(extra)
    return item


# ─────────────────────────────────────────────────────────────────────────────
# Общие обязательные данные (раздел 2 ТЗ)
#
# ИИН и отчётный период повторяются почти во всех тематиках Sapar, поэтому
# объявлены один раз: разъехавшиеся формулировки одного и того же вопроса —
# первый признак, что регламент начал жить в двух местах.
# ─────────────────────────────────────────────────────────────────────────────

STEP_IIN = step('iin', 'ИИН водителя', IIN,
                hint='Ровно 12 цифр')
STEP_PERIOD = step('period', 'Отчётный период', PERIOD,
                   hint='Месяц и год. Проверьте период сами и приложите скриншот, '
                        'где видно, что комиссия парка снималась именно в нём')
STEP_PARK = step('park', 'Парк или регион', TEXT)


# ─────────────────────────────────────────────────────────────────────────────
# ТЕМАТИКИ SAPAR
# ─────────────────────────────────────────────────────────────────────────────

SAPAR_DOCS_MISSING = {
    'key': 'sapar_docs_missing',
    'queue_code': 'itaxi_sapar',
    'title': 'Документы не поступили',
    'when_to_use': 'Водитель выполнял поездки в нашем парке, но закрывающие документы '
                   'за нужный отчётный период в Sapar не отображаются.',
    'attachment': ATTACH_IMAGE,
    'attachment_hint': 'Скриншот раздела Sapar, в котором отсутствуют документы',
    'checks': [
        'За выбранный отчётный период у водителя были выполненные поездки именно в нашем парке',
        'Списывалась ли комиссия за поездки',
        'Были ли корпоративные поездки или начислялись ли бонусы (от Яндекс)',
        'Какой провайдер был подключён у водителя в отчётном периоде',
        'Менялся ли провайдер. Если менялся — уточнить дату смены',
    ],
    'steps': [
        STEP_IIN,
        STEP_PERIOD,
        STEP_PARK,
        step('trips_in_park', 'Были ли поездки в нашем парке', YESNO),
        step('commission_charged', 'Списывалась ли комиссия', YESNO),
        step('corp_or_bonus', 'Были ли корпоративные поездки или начислялись бонусы', YESNO),
        step('provider_changed', 'Менялся ли провайдер', YESNO_DATE,
             date_label='Дата смены провайдера'),
        step('relogin_done', 'Выполнен ли повторный вход в Sapar', YESNO),
        step('docs_after_relogin', 'Появились ли документы после повторного входа', YESNO,
             depends_on=('relogin_done', 'yes')),
        step('screenshot', 'Скриншот раздела, в котором нет документов', ATTACHMENT),
    ],
    'rules': [
        rule(('trips_in_park', 'no'), CLOSE,
             'Поездок в нашем парке за выбранный период не было — отсутствие документов '
             'может быть корректным. Обращение закрываем без отправки.'),
        rule(('relogin_done', 'no'), BLOCKED,
             'Попросите водителя выйти из Sapar, войти заново и обновить список документов. '
             'После этого вернитесь к вопросу.'),
        rule(('docs_after_relogin', 'yes'), CLOSE,
             'После повторного входа документы появились — вопрос решён, отправлять нечего.'),
    ],
}

SAPAR_SIGN_ERROR = {
    'key': 'sapar_sign_error',
    'queue_code': 'itaxi_sapar',
    'title': 'Не удаётся подписать документы / ошибка при подписании',
    'when_to_use': 'Документы отображаются в Sapar, но водитель не может завершить '
                   'подписание либо на одном из этапов появляется ошибка.',
    'attachment': ATTACH_IMAGE_OR_VIDEO,
    'attachment_hint': 'Скриншот или видео ошибки',
    'checks': [
        'Очистить кэш и файлы сайта Sapar в браузере',
        'Открыть Sapar через другой браузер, например Chrome вместо Яндекс Браузера',
        'Полностью закрыть и повторно запустить eGov Mobile и Sapar',
        'При возможности очистить кэш приложений и повторить попытку',
        'При необходимости проверить подписание с другого устройства',
        'Уточнить точный этап возникновения ошибки',
        'Проверить, относится ли проблема к Sapar, а не к паролю ЭЦП, SMS, биометрии '
        'или общей работе eGov Mobile',
    ],
    'steps': [
        STEP_IIN,
        STEP_PERIOD,
        STEP_PARK,
        step('docs_visible', 'Отображаются ли документы в Sapar', YESNO),
        step('error_stage', 'На каком этапе возникает ошибка', CHOICE, options=[
            'Открытие документа',
            'Переход в eGov Mobile',
            'Подтверждение подписания',
            'Возврат в Sapar',
            'Сохранение подписанного документа',
        ]),
        step('error_text', 'Дословный текст ошибки', TEXT),
        step('last_try_at', 'Дата и время последней попытки', DATETIME),
        step('device', 'Устройство и операционная система', TEXT),
        step('browser', 'Название браузера', TEXT),
        step('cache_cleared', 'Выполнена ли очистка кэша и какой результат', YESNO_DATE,
             date_label='Результат после очистки кэша', date_kind=TEXT),
        step('other_browser', 'Выполнена ли проверка через другой браузер и какой результат',
             YESNO_DATE, date_label='Результат в другом браузере', date_kind=TEXT),
        step('apps_restarted', 'Перезапущены ли eGov Mobile и Sapar', YESNO),
        # У водителя может не быть второго устройства — ТЗ отмечает это как необязательное.
        step('other_device', 'Выполнена ли проверка с другого устройства', YESNO, optional=True),
        step('error_persists', 'Сохраняется ли ошибка после всех выполненных действий', YESNO,
             hint='Очистка кэша, другой браузер, перезапуск приложений, другое устройство'),
        step('error_repeats', 'Ошибка воспроизводится повторно или возникла один раз', CHOICE,
             options=['Воспроизводится повторно', 'Возникла один раз']),
        step('multiple_drivers', 'Проблема у одного водителя или у нескольких', CHOICE,
             options=['У одного', 'У нескольких'], optional=True),
        step('sapar_related', 'Проблема относится к работе Sapar, а не к ЭЦП, SMS, '
                              'биометрии или eGov Mobile', YESNO),
        step('screenshot', 'Скриншот или видео ошибки', ATTACHMENT),
    ],
    # Порядок правил = приоритет: перебор идёт сверху вниз и останавливается на
    # первом совпавшем. «Ошибка исчезла» стоит первым пунктом и в ТЗ, и здесь:
    # если проблема решилась, незачем возвращать оператора к невыполненной
    # проверке — вопроса больше нет.
    'rules': [
        rule(('error_persists', 'no'), CLOSE,
             'Ошибка исчезла после выполненных действий — обращение закрываем без отправки.'),
        rule(('docs_visible', 'no'), SWITCH,
             'Документы не отображаются — это тематика «Документы не поступили», '
             'переводим туда.',
             switch_to='sapar_docs_missing'),
        rule(('sapar_related', 'no'), CLOSE,
             'Проблема относится к паролю ЭЦП, SMS, биометрии или общей работе eGov Mobile — '
             'в группу iTaxi Sapar такое не отправляется.'),
        rule(('cache_cleared', 'no'), BLOCKED,
             'Сначала очистите кэш и файлы сайта Sapar в браузере, затем повторите попытку.'),
        rule(('other_browser', 'no'), BLOCKED,
             'Проверьте Sapar в другом браузере (например, Chrome вместо Яндекс Браузера).'),
        rule(('apps_restarted', 'no'), BLOCKED,
             'Полностью закройте и повторно запустите eGov Mobile и Sapar, затем повторите попытку.'),
        rule(('error_repeats', 'Возникла один раз'), CLOSE,
             'Ошибка возникла один раз и больше не повторяется — обращение закрываем без отправки.'),
    ],
    'flags': [
        {'when': ('multiple_drivers', 'У нескольких'), 'flag': FLAG_MASS_OUTAGE},
    ],
}

SAPAR_PAYMENT_REQUIRED = {
    'key': 'sapar_payment_required',
    'queue_code': 'itaxi_sapar',
    'title': 'Отображается оплата за подписание документов',
    'when_to_use': 'На сайте Sapar отображается требование оплатить подписание документов, '
                   'хотя за выбранный отчётный период у водителя были поездки в нашем парке.',
    'attachment': ATTACH_IMAGE,
    'attachment_hint': 'Скриншот, на котором видно требование оплаты в Sapar',
    'checks': [
        'За выбранный отчётный период у водителя были выполненные поездки именно в нашем парке',
        'Списывалась ли комиссия за поездки',
        'Были ли корпоративные поездки или начислялись ли бонусы',
        'Требование оплаты отображается именно в Sapar на этапе работы с закрывающими документами',
        'На скриншоте видны требование оплаты и данные, позволяющие определить раздел Sapar',
    ],
    'steps': [
        STEP_IIN,
        STEP_PERIOD,
        step('park', 'Парк', TEXT),
        step('trips_in_park', 'Были ли выполненные поездки в нашем парке', YESNO),
        step('park_commission_charged', 'Списывалась ли комиссия парка', YESNO,
             hint='Именно комиссия парка'),
        step('corp_or_bonus', 'Были ли корпоративные поездки или начислялись бонусы', YESNO),
        step('payment_shown', 'Отображается ли требование оплаты в Sapar', YESNO),
        # Правило «оплата относится к другому сервису» из ТЗ иначе нечем проверить:
        # без этого вопроса оно осталось бы текстом, который никогда не срабатывает.
        step('payment_is_sapar_signing', 'Оплата относится именно к подписанию документов '
                                         'в Sapar, а не к другому сервису', YESNO),
        step('screenshot', 'Скриншот с требованием оплаты', ATTACHMENT),
    ],
    'rules': [
        rule(('trips_in_park', 'no'), CLOSE,
             'Поездок в нашем парке за выбранный период не было — требование оплаты '
             'может быть корректным.'),
        rule(('park_commission_charged', 'no'), BLOCKED,
             'Проверьте, списывалась ли комиссия парка за поездки этого периода.'),
        rule(('payment_shown', 'no'), CLOSE,
             'Требование оплаты больше не отображается — обращение закрываем без отправки.'),
        rule(('payment_is_sapar_signing', 'no'), CLOSE,
             'Оплата относится к другому сервису и не связана с подписанием документов '
             'в Sapar — такое обращение в группу не отправляется.'),
    ],
}

SAPAR_SIGN_STATUS = {
    'key': 'sapar_sign_status',
    'queue_code': 'itaxi_sapar',
    'title': 'Проверить статус подписания документов',
    'when_to_use': 'Нужно определить статус закрывающих документов: подписаны ли они водителем.',
    'attachment': ATTACH_NONE,
    'checks': [
        'Самостоятельно открыть нужный отчётный период в Sapar и убедиться, что у водителя '
        'сформировались документы',
        'Убедиться, что выбран правильный провайдер и он выбран в срок '
        '(после подписания документов 15–31 числа месяца)',
    ],
    'steps': [
        STEP_IIN,
        STEP_PERIOD,
        step('park', 'Парк', TEXT),
        step('what_to_check', 'Что необходимо проверить', LONGTEXT,
             hint='Период в Диспетчерской оператор проверяет сам: если у водителя есть '
                  'корпоративные поездки и бонусы от Яндекс — документы формируются, '
                  'если их нет — не формируются'),
    ],
    'rules': [],
    # Готовые формулировки для ответа оператору — чтобы статус объясняли одинаково.
    'status_glossary': [
        ('Документы подписаны водителем',
         'Водитель подписал документы, ожидается принятие со стороны Яндекса.'),
    ],
}

SAPAR_SERVICE_ERROR = {
    'key': 'sapar_service_error',
    'queue_code': 'itaxi_sapar',
    'title': 'Ошибка в работе Sapar',
    'when_to_use': 'Технические ошибки сайта или приложения Sapar, не связанные с подписанием '
                   'или сохранением документов. Ошибка на этапе подписания — это тематика '
                   '«Не удаётся подписать документы».',
    'attachment': ATTACH_IMAGE_OR_VIDEO,
    'attachment_hint': 'Скриншот или запись экрана с ошибкой',
    'checks': [
        'Подождать 5 минут и повторить попытку',
        'Полностью закрыть все вкладки Sapar',
        'Выполнить повторный вход в сервис',
        'Проверить работу Sapar в другом браузере',
        'Проверить интернет-соединение',
        'При возможности проверить работу с другого устройства',
        'Уточнить, возникает ли проблема у одного водителя или у нескольких',
        'Убедиться, что ошибка не связана с подписанием или сохранением документов',
    ],
    'steps': [
        step('error_type', 'Тип ошибки', CHOICE, options=[
            'Сайт не загружается',
            'Приложение показывает белый экран',
            'Ошибка после авторизации',
            'Долгая загрузка',
            'Некорректное отображение страницы',
            'Другая техническая ошибка',
        ]),
        STEP_IIN,
        step('park', 'Парк', TEXT),
        step('where', 'Где возникает ошибка', CHOICE, options=['На сайте', 'В приложении']),
        step('device', 'Устройство и операционная система', TEXT),
        step('browser', 'Название браузера', TEXT),
        step('last_try_at', 'Дата и время последней попытки', DATETIME),
        step('error_text', 'Дословный текст ошибки или описание результата', TEXT),
        step('multiple_drivers', 'Проблема у одного водителя или у нескольких', CHOICE,
             options=['У одного', 'У нескольких'], optional=True),
        step('waited_5min', 'Выполнено ли ожидание в течение 5 минут', YESNO),
        step('relogin_done', 'Выполнен ли повторный вход', YESNO),
        step('other_browser_checked', 'Проверена ли работа в другом браузере', YESNO),
        step('internet_checked', 'Проверено ли интернет-соединение', YESNO),
        step('other_device', 'Выполнена ли проверка с другого устройства', YESNO, optional=True),
        step('local_cause_excluded',
             'Локальная причина исключена: дело не в интернете, браузере или устройстве водителя',
             YESNO),
        step('signing_related', 'Ошибка возникает на этапе подписания или сохранения документов',
             YESNO),
        step('error_persists', 'Сохраняется ли ошибка после всех действий', YESNO),
        step('screenshot', 'Скриншот или запись экрана', ATTACHMENT),
    ],
    # Порядок = приоритет, и он взят из ТЗ. «Сервис заработал» там первый пункт
    # среди причин не отправлять: если всё уже работает, возвращать оператора к
    # непройденной проверке бессмысленно — раньше именно так и получалось.
    'rules': [
        rule(('error_persists', 'no'), CLOSE,
             'Сервис заработал после выполненных действий — обращение закрываем без отправки.'),
        rule(('signing_related', 'yes'), SWITCH,
             'Ошибка на этапе подписания или сохранения документов — это тематика '
             '«Не удаётся подписать документы», переводим туда.',
             switch_to='sapar_sign_error'),
        rule(('local_cause_excluded', 'no'), BLOCKED,
             'Похоже на локальную причину — интернет, браузер или устройство водителя. '
             'Устраните её, прежде чем обращаться в группу.'),
        rule(('waited_5min', 'no'), BLOCKED,
             'Подождите 5 минут и повторите попытку, затем вернитесь к вопросу.'),
        rule(('relogin_done', 'no'), BLOCKED,
             'Закройте все вкладки Sapar и выполните повторный вход, затем вернитесь к вопросу.'),
        rule(('other_browser_checked', 'no'), BLOCKED,
             'Проверьте работу Sapar в другом браузере.'),
        rule(('internet_checked', 'no'), BLOCKED,
             'Проверьте интернет-соединение у водителя: локальную причину нужно исключить '
             'до обращения в группу.'),
    ],
    'flags': [
        {'when': ('multiple_drivers', 'У нескольких'), 'flag': FLAG_MASS_OUTAGE},
    ],
}

PARCEL_LOCATION = {
    'key': 'parcel_location',
    'queue_code': 'parcels',
    'title': 'Уточнение местонахождения посылки',
    'when_to_use': 'Оператору необходимо уточнить, где находится отправленная посылка.',
    'attachment': ATTACH_NONE,
    # Автоответ из внутреннего реестра (Google-таблица) в ТЗ был, но постановщик
    # 11.08.2026 попросил не включать реестр в проверку: ссылки закрыты и бот их
    # не читает. Поэтому обращение сразу уходит ответственному за посылки.
    'checks': [
        'Уточнить номер отправителя',
        'Проверить город',
        'Уточнить дату отправки',
        'Уточнить, поступало ли получателю уведомление или звонок',
    ],
    'steps': [
        step('contact_number', 'Номер отправителя или получателя', TEXT),
        step('parcel_description', 'Описание посылки', LONGTEXT),
        step('city', 'Город, где выполнялся заказ', TEXT),
        step('order_date', 'Дата заказа', DATETIME, date_only=True),
        step('notified', 'Поступало ли получателю уведомление или звонок', YESNO, optional=True),
    ],
    'rules': [],
}

SCENARIOS = [
    SAPAR_DOCS_MISSING,
    SAPAR_SIGN_ERROR,
    SAPAR_PAYMENT_REQUIRED,
    SAPAR_SIGN_STATUS,
    SAPAR_SERVICE_ERROR,
    PARCEL_LOCATION,
]

BY_KEY = {item['key']: item for item in SCENARIOS}


def get(key):
    return BY_KEY.get(str(key or ''))


# ─────────────────────────────────────────────────────────────────────────────
# Проверка ответов
# ─────────────────────────────────────────────────────────────────────────────

def _answered(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return bool(value.get('value'))
    return True


def _value(answers, key):
    """Значение шага. Для «да/нет + уточнение» — сама да/нет часть."""
    raw = answers.get(key)
    if isinstance(raw, dict):
        return raw.get('value')
    return raw


def validate_step(item, answers):
    """Что не так с ответом на конкретный шаг. None — всё в порядке."""
    kind = item['kind']
    raw = answers.get(item['key'])
    value = _value(answers, item['key'])

    if not _answered(raw):
        return None if item.get('optional') else 'Не заполнено'

    if kind == IIN and not _IIN_RE.match(str(value).strip()):
        return 'ИИН должен состоять ровно из 12 цифр'
    if kind == PERIOD and not _PERIOD_RE.match(str(value).strip()):
        return 'Укажите месяц и год отчётного периода'
    if kind == CHOICE and str(value) not in (item.get('options') or []):
        return 'Выберите вариант из списка'
    if kind in (YESNO, YESNO_DATE) and str(value) not in ('yes', 'no'):
        return 'Ответьте «да» или «нет»'
    if kind == YESNO_DATE and str(value) == 'yes':
        extra = raw.get('detail') if isinstance(raw, dict) else None
        if not (extra or '').strip():
            return item.get('date_label') or 'Уточните подробности'
    return None


def visible_steps(scenario, answers):
    """Шаги, которые сейчас имеют смысл.

    Зависимый шаг («появились ли документы после повторного входа») не должен ни
    спрашиваться, ни требоваться, пока не выполнено условие: иначе оператор
    упирается в обязательное поле, которого в его ситуации не существует.
    """
    result = []
    for item in scenario['steps']:
        depends = item.get('depends_on')
        if depends and _value(answers, depends[0]) != depends[1]:
            continue
        result.append(item)
    return result


def evaluate(scenario_key, answers, *, has_attachment=False, checks_confirmed=False):
    """Что делать с обращением: отправлять, вернуть, закрыть или перевести.

    Порядок разбора не случаен и повторяет логику ТЗ:

    1. Сначала СМЫСЛОВЫЕ правила по уже данным ответам. «Документы появились
       после повторного входа» закрывает обращение, даже если скриншот ещё не
       приложен: требовать вложение к тому, что не будет отправлено, — впустую
       гонять человека.
    2. Потом полнота: незаполненные шаги и вложение.
    3. И только когда всё заполнено и ничто не блокирует — READY.

    Возвращает словарь; поле outcome — одно из READY/BLOCKED/CLOSE/SWITCH/INCOMPLETE.
    """
    scenario = get(scenario_key)
    if not scenario:
        return {'outcome': INCOMPLETE, 'message': 'Неизвестная тематика', 'missing': {}}

    answers = answers or {}
    steps = visible_steps(scenario, answers)

    # 1. Правила: закрыть, перевести, вернуть к проверке.
    for item in scenario.get('rules', []):
        key, expected = item['when']
        if _value(answers, key) == expected:
            found = {
                'outcome': item['outcome'],
                'message': item['message'],
                'step': key,
                'missing': {},
            }
            if item.get('switch_to'):
                found['switch_to'] = item['switch_to']
                found['switch_title'] = (get(item['switch_to']) or {}).get('title')
            return found

    # 2. Полнота.
    missing = {}
    for item in steps:
        if item['kind'] == ATTACHMENT:
            continue
        problem = validate_step(item, answers)
        if problem:
            missing[item['key']] = problem

    needs_file = scenario['attachment'] != ATTACH_NONE
    if needs_file and not has_attachment:
        missing['__attachment__'] = (
            'Приложите скриншот' if scenario['attachment'] == ATTACH_IMAGE
            else 'Приложите скриншот или видео')

    if scenario.get('checks') and not checks_confirmed:
        missing['__checks__'] = 'Подтвердите, что выполнили обязательные проверки'

    if missing:
        return {'outcome': INCOMPLETE, 'message': 'Заполнены не все обязательные данные',
                'missing': missing}

    # 3. Всё сошлось.
    flags = [f['flag'] for f in scenario.get('flags', [])
             if _value(answers, f['when'][0]) == f['when'][1]]
    return {'outcome': READY, 'message': None, 'missing': {}, 'flags': flags}


# ─────────────────────────────────────────────────────────────────────────────
# Готовый текст обращения
# ─────────────────────────────────────────────────────────────────────────────

_YESNO_WORDS = {'yes': 'да', 'no': 'нет'}


def format_answer(item, answers):
    raw = answers.get(item['key'])
    value = _value(answers, item['key'])
    if not _answered(raw):
        return '—'
    if item['kind'] in (YESNO, YESNO_DATE):
        word = _YESNO_WORDS.get(str(value), str(value))
        detail = raw.get('detail') if isinstance(raw, dict) else None
        if item['kind'] == YESNO_DATE and str(value) == 'yes' and detail:
            return '%s (%s)' % (word, detail)
        return word
    if item['kind'] == PERIOD:
        text = str(value).strip()
        if _PERIOD_RE.match(text):
            year, month = text.split('-')
            months = ('январь', 'февраль', 'март', 'апрель', 'май', 'июнь', 'июль',
                      'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь')
            return '%s %s' % (months[int(month) - 1], year)
        return text
    return str(value).strip()


def render_body(scenario_key, answers, *, flags=()):
    """Текст обращения для группы. Собирается системой и вручную не правится.

    Это прямое требование ТЗ: «Сам текст стандартной тематики вручную не
    редактируется». Отсюда и формат — не проза, а перечень «вопрос: ответ»:
    специалисту в группе нужно за секунду увидеть ИИН, период и что уже
    проверено, а не читать сочинение.
    """
    scenario = get(scenario_key)
    if not scenario:
        return ''
    lines = []
    for flag in flags or ():
        if flag in FLAG_LABELS:
            lines.append('⚠️ %s' % FLAG_LABELS[flag])
    if lines:
        lines.append('')
    for item in visible_steps(scenario, answers or {}):
        if item['kind'] == ATTACHMENT:
            continue
        value = format_answer(item, answers or {})
        if value == '—' and item.get('optional'):
            continue
        lines.append('%s: %s' % (item['label'], value))
    if scenario.get('checks'):
        lines.append('')
        lines.append('Оператор подтвердил обязательные проверки: %d из %d.'
                     % (len(scenario['checks']), len(scenario['checks'])))
    return '\n'.join(lines)


def render_subject(scenario_key, answers):
    """Короткая тема: тематика + ИИН, если он в сценарии есть.

    По ней обращение ищут и в списке, и в группе, поэтому в теме то, чем его
    реально опознают, а не первые слова описания.
    """
    scenario = get(scenario_key)
    if not scenario:
        return ''
    iin = _value(answers or {}, 'iin')
    if iin:
        return '%s · ИИН %s' % (scenario['title'], str(iin).strip())
    return scenario['title']


def public_catalog():
    """Каталог для интерфейса: всё, что нужно нарисовать мастер вопросов.

    Правила отдаются вместе со сценарием намеренно: интерфейс может подсветить
    последствие сразу при ответе, не дожидаясь ответа сервера. Решение при этом
    всё равно принимает сервер (evaluate) — клиентская подсветка это подсказка,
    а не проверка.
    """
    return [{
        'key': item['key'],
        'queue_code': item['queue_code'],
        'title': item['title'],
        'when_to_use': item['when_to_use'],
        'attachment': item['attachment'],
        'attachment_hint': item.get('attachment_hint'),
        'checks': item.get('checks', []),
        'steps': item['steps'],
        'rules': [{'when': list(r['when']), 'outcome': r['outcome'], 'message': r['message'],
                   'switch_to': r.get('switch_to')} for r in item.get('rules', [])],
        'status_glossary': [{'status': s, 'meaning': m}
                            for s, m in item.get('status_glossary', [])],
    } for item in SCENARIOS]
