# -*- coding: utf-8 -*-
"""Справочники раздела «Жалобы»: на кого жалуются, почему, чем кончилась проверка.

Всё здесь — буква ТЗ задачи #297 (вложение «жалобы.docx», постановщик
Полатжанкызы Жанис). Списки не редактируются мышкой в проде по той же причине,
что сценарии обращений (crm/scenarios.py): это согласованный регламент, и
менять его должен тот, кто меняет ТЗ, через ревью. На каждый список стоит тест,
сверяющий его с ТЗ дословно.

Модуль чистый — ни базы, ни Flask, ни сети. Поэтому правила «что обязательно»,
«уходит ли жалоба в группу» и «когда жалоба считается закрытой» проверяются
юнит-тестами без окружения (tests/test_complaints_catalog.py).

Коды — латиницей и навсегда: по ним строится аналитика за месяцы назад, и
переименованная подпись не должна рвать историю. Подпись можно поправить, код —
нельзя.
"""

# ─────────────────────────────────────────────────────────────────────────────
# На кого или на что жалоба — первый уровень выбора (ТЗ, в этом порядке)
# ─────────────────────────────────────────────────────────────────────────────

TARGET_CALL_CENTER = 'call_center'
TARGET_CAR_RENTAL = 'car_rental'
TARGET_FRONT_OFFICE = 'front_office'
TARGET_TAXI_PARK = 'taxi_park'
TARGET_YANDEX = 'yandex'

# Уходит ли жалоба в Telegram-группу «Жалобы КЦ, регионы, таксопарк».
#
# ТЗ разводит два вида: «Жалоба зафиксирована» — сохраняется в iCORE и идёт в
# аналитику, и «Требует обработки» — дополнительно уходит через бота в группу.
#
#   never     — Яндекс. «В Telegram-группу не передаём обращения „для
#               Яндекса“ … достаточно зафиксировать в iCore для аналитики».
#   always    — все остальные: сотрудники КЦ, фронт-офис, аренда авто и
#               таксопарк. У жалобы на сотрудника ТЗ требует «обязательно
#               довести процесс до обратной связи», жалобу на аренду без группы
#               проверить некому. Таксопарк был «по выбору оператора», но
#               владелец 29.09.2026: «у оператора не должно быть возможности не
#               отправлять в группу» — выбора у оператора нет ни у одной цели.
PROCESS_ALWAYS = 'always'
PROCESS_NEVER = 'never'

# Чем уточняется адресат жалобы («конкретного сотрудника / подразделение, если
# удалось определить»). У каждой цели своё «подразделение».
UNIT_DEPARTMENT = 'department'   # подразделение КЦ — отдел портала
UNIT_OFFICE = 'office'           # офис фронт-офиса — справочник офисов вики
UNIT_PARK = 'park'               # таксопарк — справочник парков вики

OTHER = 'other'  # «Другое» — последний пункт каждого списка причин в ТЗ


def _reasons(*pairs):
    return [{'code': code, 'title': title} for code, title in pairs]


TARGETS = [
    {
        'code': TARGET_CALL_CENTER,
        'title': 'Оператор колл-центра',
        'hint': 'Жалоба на сотрудника КЦ: техподдержка, отдел продаж или другое подразделение',
        # «Если выбрана жалоба на оператора колл-центра, необходимо
        # дополнительно определить направление» — поэтому подразделение здесь
        # обязательно, а сотрудник нет: «если оператор не смог определить
        # сотрудника, обращение всё равно должно быть создано».
        'unit': UNIT_DEPARTMENT,
        'unit_required': True,
        'employee': True,
        'processing': PROCESS_ALWAYS,
        'reasons': _reasons(
            ('rude', 'Грубость / некорректное общение'),
            ('not_solved', 'Не решили вопрос'),
            ('wrong_info', 'Предоставили неверную информацию'),
            ('promo', 'Не добавили в акцию / некорректно обработали вопрос по акции'),
            ('slow', 'Долго не отвечали'),
            ('no_answer', 'Не ответили на звонок / сообщение'),
            (OTHER, 'Другое'),
        ),
    },
    {
        'code': TARGET_CAR_RENTAL,
        'title': 'Аренда авто',
        'hint': 'Не отвечают, отказали в помощи, условия аренды, вопрос по автомобилю',
        'unit': None,
        'unit_required': False,
        'employee': False,
        'processing': PROCESS_ALWAYS,
        'reasons': _reasons(
            ('no_calls', 'Не отвечают на звонки'),
            ('no_whatsapp', 'Не отвечают в WhatsApp'),
            ('refused', 'Отказали в помощи'),
            ('car_issue', 'Вопрос по автомобилю не решён'),
            ('terms', 'Условия аренды'),
            (OTHER, 'Другое'),
        ),
    },
    {
        'code': TARGET_FRONT_OFFICE,
        'title': 'Фронт-офис',
        'hint': 'Жалоба на офис парка или его сотрудника',
        # Сотрудника фронт-офиса тоже можно определить, и работа с ним идёт по
        # той же цепочке, что у КЦ: ТЗ говорит о «жалобах на сотрудников», не
        # ограничивая их колл-центром.
        'unit': UNIT_OFFICE,
        'unit_required': False,
        'employee': True,
        'employee_department_code': 'front_office',
        'processing': PROCESS_ALWAYS,
        'reasons': _reasons(
            ('rude', 'Грубое общение'),
            ('refused', 'Отказали в помощи / консультации'),
            ('not_solved', 'Вопрос не решили'),
            ('waiting', 'Долгое ожидание'),
            (OTHER, 'Другое'),
        ),
    },
    {
        'code': TARGET_TAXI_PARK,
        'title': 'Таксопарк',
        'hint': 'Комиссия, условия работы, акции, выплаты, обслуживание',
        'unit': UNIT_PARK,
        'unit_required': False,
        'employee': False,
        'processing': PROCESS_ALWAYS,
        'reasons': _reasons(
            ('commission', 'Комиссия'),
            ('terms', 'Условия работы'),
            ('promo', 'Акции / бонусы'),
            ('payouts', 'Выплаты'),
            ('service', 'Обслуживание'),
            ('dissatisfied', 'Неудовлетворённость условиями парка'),
            (OTHER, 'Другое'),
        ),
    },
    {
        'code': TARGET_YANDEX,
        'title': 'Яндекс',
        'hint': 'Тарифы, заказы, комиссии, приложение — фиксируем для аналитики',
        'unit': None,
        'unit_required': False,
        'employee': False,
        'processing': PROCESS_NEVER,
        'reasons': _reasons(
            ('tariffs', 'Тарифы'),
            ('order_price', 'Стоимость заказов'),
            ('fees', 'Комиссии / удержания'),
            ('app', 'Работа приложения'),
            ('dispatch', 'Распределение заказов'),
            ('blocks', 'Блокировки / ограничения'),
            (OTHER, 'Другое'),
        ),
    },
]

BY_CODE = {item['code']: item for item in TARGETS}


def target(code):
    return BY_CODE.get(str(code or ''))


def reason(target_code, reason_code):
    """Причина внутри цели. Коды причин повторяются между целями («rude» у КЦ
    и у фронт-офиса), поэтому без цели код причины ничего не значит."""
    for item in (target(target_code) or {}).get('reasons') or []:
        if item['code'] == reason_code:
            return item
    return None


def reason_title(target_code, reason_code):
    found = reason(target_code, reason_code)
    return found['title'] if found else (reason_code or '')


def target_title(code):
    return (target(code) or {}).get('title') or (code or '')


# ─────────────────────────────────────────────────────────────────────────────
# Подразделения колл-центра
#
# «Направление» из ТЗ («техподдержка; отдел продаж; при необходимости другие
# подразделения КЦ») — это отдел портала, а не свободная подпись: по отделу
# выбирается сотрудник, по нему же — кто отработает жалобу. Список отделов
# живой (таблица departments), здесь только коды КЦ и то, как их назвать
# оператору: «СЗоВ — Служба заботы о водителях» он знает как техподдержку.
# Порядок — порядок ТЗ: сначала техподдержка, потом продажи, потом прочие.
# ─────────────────────────────────────────────────────────────────────────────

# ТЭЗ КЦ в жалобах нет вовсе (владелец, 29.09.2026: «у никого ТЭЗ КЦ не должно
# быть»): ни в выборе подразделения у оператора, ни среди разбирающих отделов.
CALL_CENTER_DEPARTMENT_CODES = ('szov', 'op', 'remote_cc',
                                'request_processing_department')

CALL_CENTER_DEPARTMENT_LABELS = {
    'szov': 'Техподдержка (СЗоВ)',
    'op': 'Отдел продаж',
}

FRONT_OFFICE_DEPARTMENT_CODE = 'front_office'

# Отделы, чьих сотрудников может коснуться жалоба. Их супервайзеры и главы
# входят в раздел как разбирающие — у них своя очередь «К разбору».
HANDLER_DEPARTMENT_CODES = CALL_CENTER_DEPARTMENT_CODES + (FRONT_OFFICE_DEPARTMENT_CODE,)


def department_label(code, name):
    return CALL_CENTER_DEPARTMENT_LABELS.get(str(code or ''), name or code or '')


# ─────────────────────────────────────────────────────────────────────────────
# Итог проверки (ТЗ, в этом порядке)
# ─────────────────────────────────────────────────────────────────────────────

RESULTS = [
    {'code': 'confirmed', 'title': 'Жалоба подтверждена'},
    {'code': 'not_confirmed', 'title': 'Жалоба не подтверждена'},
    {'code': 'partial', 'title': 'Информация частично подтверждена'},
    {'code': 'explained', 'title': 'Предоставлено разъяснение'},
    {'code': 'solved', 'title': 'Вопрос решён'},
    {'code': 'transferred', 'title': 'Передано ответственному подразделению'},
    {'code': 'out_of_scope', 'title': 'Не в зоне влияния'},
    {'code': 'no_data', 'title': 'Недостаточно данных для проверки'},
]

RESULT_BY_CODE = {item['code']: item for item in RESULTS}

# «Подтверждённые / неподтверждённые» — фильтр аналитики из ТЗ. Частичное
# подтверждение считается подтверждением: факт, на который жаловался водитель,
# хотя бы отчасти был. Остальные итоги (разъяснение, передано, вне зоны) — не
# про подтверждение вовсе, и в эту пару не входят.
CONFIRMED_RESULTS = ('confirmed', 'partial')
NOT_CONFIRMED_RESULTS = ('not_confirmed',)


def result_title(code):
    return (RESULT_BY_CODE.get(str(code or '')) or {}).get('title') or ''


# ─────────────────────────────────────────────────────────────────────────────
# Работа с сотрудником
#
# «Далее супервайзер фиксирует результат работы» — пять вариантов ТЗ. Запись
# работы ведётся журналом: жалоба может потребовать сначала обратную связь, а
# через неделю тренинг, и оба факта должны остаться.
#
# Два варианта из пяти создают запись в разделе «Тренинги» — ровно так ТЗ и
# просит («после нажатия „Провести обратную связь / тренинг“ система создаёт
# соответствующую запись в тренингах»): обратная связь и тренинг — это время,
# проведённое с сотрудником, и оно идёт в его часы так же, как ОС по оценке
# звонка. Разбор, «обучение не требуется» и «другие меры» — не занятие, и
# выдумывать им время в журнале тренингов незачем.
# ─────────────────────────────────────────────────────────────────────────────

ACTION_FEEDBACK = 'feedback'
ACTION_REVIEW = 'review'
ACTION_TRAINING_ASSIGNED = 'training_assigned'
ACTION_TRAINING = 'training'
ACTION_NO_TRAINING = 'no_training'
ACTION_OTHER = 'other'

# «назначен / проведён тренинг» в ТЗ — один пункт, но два разных факта: после
# «назначен» работа с сотрудником НЕ закончена (статус «требуется тренинг»),
# после «проведён» — закончена. Поэтому здесь два варианта.
#
# training     — создаёт запись в «Тренингах» (нужны дата и время занятия);
# ask_training — у записи есть галочка «Нужен тренинг»: разобрали, дали ОС, но
#                этого мало — работа остаётся открытой со статусом «требуется
#                тренинг»;
# closes       — запись завершает работу с сотрудником (если не отмечено «нужен
#                тренинг»).
WORK_ACTIONS = [
    {'code': ACTION_FEEDBACK, 'title': 'Обратная связь проведена',
     'training': True, 'ask_training': True, 'closes': True,
     'default_reason': 'Обратная связь'},
    {'code': ACTION_REVIEW, 'title': 'Проведён дополнительный разбор ситуации',
     'training': False, 'ask_training': True, 'closes': True},
    {'code': ACTION_TRAINING_ASSIGNED, 'title': 'Назначен тренинг',
     'training': False, 'ask_training': False, 'closes': False},
    {'code': ACTION_TRAINING, 'title': 'Проведён тренинг',
     'training': True, 'ask_training': False, 'closes': True,
     'default_reason': 'Тренинг по качеству. Разбор ошибок'},
    {'code': ACTION_NO_TRAINING, 'title': 'Дополнительное обучение не требуется',
     'training': False, 'ask_training': False, 'closes': True},
    {'code': ACTION_OTHER, 'title': 'Приняты другие меры',
     'training': False, 'ask_training': False, 'closes': True},
]

WORK_ACTION_BY_CODE = {item['code']: item for item in WORK_ACTIONS}

# Что снимает требование тренинга: сам проведённый тренинг и прямое «обучение
# не требуется». Остальное его не касается.
TRAINING_CLEARING_ACTIONS = (ACTION_TRAINING, ACTION_NO_TRAINING)

# Чем можно закрыть работу, когда сотрудника так и не определили: объяснить,
# почему дальше работать не с кем, — «указать, почему дополнительная работа не
# требуется» (ТЗ). ОС и тренинг без сотрудника провести нельзя.
UNASSIGNED_ACTIONS = (ACTION_REVIEW, ACTION_NO_TRAINING, ACTION_OTHER)

# Вид занятия в журнале «Тренинги». Только литералы из trainings_reason_check —
# другое значение база не примет. Набор короче полного: «Собрание», «Тех. сбой»
# и «Мониторинг» к работе по жалобе отношения не имеют.
TRAINING_REASONS = (
    'Обратная связь',
    'Дисциплинарный тренинг',
    'Тренинг по качеству. Разбор ошибок',
    'Тренинг по продукту',
    'Мотивационная беседа',
    'Другое',
)


def work_action_title(code):
    return (WORK_ACTION_BY_CODE.get(str(code or '')) or {}).get('title') or ''


def next_work_flags(flags, action_code, need_training=False):
    """Факты цепочки после ещё одной записи о работе с сотрудником.

    flags — {'feedback_done', 'training_required', 'training_done'}. Возвращает
    те же три ключа плюс 'closed' — завершена ли работа с сотрудником.

    Правило одно: запись, которая «закрывает» (ОС, разбор, проведённый тренинг,
    «обучение не требуется», другие меры), завершает работу, если после неё
    не остаётся требования тренинга. «Требуется тренинг» ставят две вещи —
    «Назначен тренинг» и галочка «Нужен тренинг» у ОС или разбора; снимают —
    ТОЛЬКО проведённый тренинг или прямое «обучение не требуется»
    (TRAINING_CLEARING_ACTIONS).
    """
    spec = WORK_ACTION_BY_CODE.get(str(action_code or ''))
    if not spec:
        raise ValueError('unknown work action: %r' % (action_code,))
    flags = flags or {}
    feedback_done = bool(flags.get('feedback_done')) or action_code == ACTION_FEEDBACK
    training_done = bool(flags.get('training_done')) or action_code == ACTION_TRAINING
    training_required = bool(flags.get('training_required'))
    if action_code == ACTION_TRAINING_ASSIGNED:
        training_required = True
    elif spec['ask_training'] and need_training:
        training_required = True
    elif action_code in TRAINING_CLEARING_ACTIONS:
        # Назначенный тренинг снимают ровно две записи. ОС, разбор или
        # «другие меры» после «назначен тренинг» его не отменяют: иначе
        # работа закрылась бы без проведённого тренинга (сверка с ТЗ 29.09.2026).
        training_required = False
    return {
        'feedback_done': feedback_done,
        'training_required': training_required,
        'training_done': training_done,
        'closed': bool(spec['closes']) and not training_required,
    }


def work_summary(*, feedback_done, training_done, complaint_closed, employee_known=True):
    """Отбивка в группу о проведённой работе — без внутренних деталей.

    ТЗ даёт образец: «Жалоба обработана. Сотрудник определён. Обратная связь
    проведена. Тренинг зафиксирован». Что именно сказали сотруднику и чему
    учили, сюда не попадает никогда: «внутренние детали обратной связи и
    обучения сотрудника не должны уходить оператору для передачи водителю».
    """
    parts = ['Жалоба обработана' if complaint_closed else 'Работа с сотрудником завершена',
             'Сотрудник определён' if employee_known else 'Сотрудник не определён']
    if feedback_done:
        parts.append('Обратная связь проведена')
    if training_done:
        parts.append('Тренинг зафиксирован')
    return '. '.join(parts) + '.'


# Состояние работы с сотрудником — то, по чему строится очередь «К разбору» и
# фильтр аналитики. Семь статусов ТЗ («сотрудник не определён … работа с
# сотрудником завершена») — не взаимоисключающие состояния, а факты цепочки;
# храним состояние, а факты (ОС проведена, тренинг проведён) — флагами рядом.
WORK_UNASSIGNED = 'unassigned'   # сотрудник не определён
WORK_PENDING = 'pending'         # определён, работа не завершена
WORK_DONE = 'done'               # работа с сотрудником завершена


def work_state_for(target_code, employee_id, work_closed):
    """Состояние работы по факту. None — у цели нет сотрудника вовсе."""
    if not (target(target_code) or {}).get('employee'):
        return None
    if not employee_id:
        return WORK_UNASSIGNED
    return WORK_DONE if work_closed else WORK_PENDING


def processing_mode(target_code):
    return (target(target_code) or {}).get('processing') or PROCESS_ALWAYS


def requires_processing(target_code):
    """Уходит ли жалоба в группу. Решает цель, а не оператор: присланный
    клиентом requires_processing сервер не слушает (владелец, 29.09.2026)."""
    return processing_mode(target_code) != PROCESS_NEVER


def is_closed(*, requires_processing, result_code, work_state):
    """Отработана ли жалоба целиком.

    Два условия, и оба из ТЗ:
      * у жалобы, которая уходила на разбор, зафиксирован итог проверки
        («по обращению должен фиксироваться итоговый результат обработки»);
      * жалоба на сотрудника «не может считаться полностью закрытой до тех пор,
        пока супервайзер не завершил обязательную работу с сотрудником либо не
        указал, почему дополнительная работа не требуется».

    Сотрудник так и не определён — работа с ним держит жалобу, только если она
    ПОДТВЕРДИЛАСЬ: подтверждённая жалоба на сотрудника без сотрудника — это
    работа, которую никто не провёл. Закрыть её можно, определив сотрудника или
    записав, почему работать не с кем (UNASSIGNED_ACTIONS: work_state станет
    done). Не подтвердилась, «недостаточно данных», «не в зоне влияния» —
    разбирать некого, итог это и объясняет.

    Жалоба, которую только зафиксировали (Яндекс, часть жалоб на парк), итога
    не ждёт: её никто не проверяет — она для аналитики.
    """
    if work_state == WORK_PENDING:
        return False
    if requires_processing and not result_code:
        return False
    if work_state == WORK_UNASSIGNED and result_code in CONFIRMED_RESULTS:
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Поля жалобы и их проверка
# ─────────────────────────────────────────────────────────────────────────────

# Пределы длины — те же, что у колонок в schema.py, чтобы ошибка была понятной
# фразой, а не отказом базы.
LIMITS = {
    'driver_name': 255,
    'driver_phone': 64,
    'driver_ref': 64,
    'city': 120,
    'description': 4000,
    'employee_note': 255,
}


def _text(value, limit):
    return ' '.join(str(value or '').split())[:limit]


def _longtext(value):
    return str(value or '').replace('\r\n', '\n').strip()


def clean_complaint(data):
    """Тело запроса «новая жалоба» → (чистые поля, {поле: ошибка}).

    Проверяет только то, что можно проверить без базы: состав и обязательность.
    Существование отдела, сотрудника, офиса и парка сверяет слой запросов —
    здесь модуль чистый.
    """
    data = data or {}
    errors = {}
    code = str(data.get('target') or '').strip()
    spec = target(code)
    if not spec:
        return None, {'target': 'Выберите, на кого или на что жалоба'}

    reason_code = str(data.get('reason') or '').strip()
    if not reason(code, reason_code):
        errors['reason'] = 'Выберите причину жалобы'

    clean = {
        'target': code,
        'reason': reason_code,
        'driver_name': _text(data.get('driver_name'), LIMITS['driver_name']),
        'driver_phone': _text(data.get('driver_phone'), LIMITS['driver_phone']),
        'driver_ref': _text(data.get('driver_ref'), LIMITS['driver_ref']) or None,
        'city': _text(data.get('city'), LIMITS['city']),
        # Описание не обрезается молча: вставленный хвост переписки иначе
        # пропал бы без предупреждения. Длинное — ошибка с понятной фразой.
        'description': _longtext(data.get('description')),
        'event_at': str(data.get('event_at') or '').strip().replace(' ', 'T') or None,
        'unit_id': _int_or_none(data.get('unit_id')),
        'employee_id': _int_or_none(data.get('employee_id')) if spec['employee'] else None,
        'requires_processing': requires_processing(code),
    }
    if not spec['unit']:
        clean['unit_id'] = None

    # «При заполнении жалобы оператор указывает основные данные»: ФИО, телефон,
    # город и краткое описание — без них проверять нечего. ID / ВУ — «при
    # наличии», дата события — «если это необходимо для проверки».
    for key, message in (('driver_name', 'Укажите ФИО водителя'),
                         ('driver_phone', 'Укажите номер телефона'),
                         ('city', 'Выберите город'),
                         ('description', 'Опишите ситуацию')):
        if not clean[key]:
            errors[key] = message
    if spec['unit_required'] and not clean['unit_id']:
        errors['unit_id'] = 'Выберите подразделение колл-центра'
    if len(clean['description']) > LIMITS['description']:
        errors['description'] = 'Описание длиннее %d символов — сократите' % LIMITS['description']
    # «Дату / примерное время события»: время необязательно, и то, что его не
    # указали, храним явно. Иначе дата без времени легла бы полуночью, и в
    # карточке и выгрузке появилось бы «00:00», которого никто не называл.
    clean['event_time_known'] = False
    if clean['event_at']:
        parsed = _parse_event(clean['event_at'])
        if parsed is None:
            errors['event_at'] = 'Укажите дату и время события'
        else:
            clean['event_at'], clean['event_time_known'] = parsed
    return clean, errors


def _parse_event(value):
    """'2026-09-28' или '2026-09-28T14:30' → (значение для базы, указано ли время).

    None — не дата или несуществующая дата (30 февраля): такую база отвергла бы
    ошибкой 500 вместо понятной фразы."""
    from datetime import datetime as _dt

    text = str(value or '').strip()
    for pattern, has_time in (('%Y-%m-%dT%H:%M', True), ('%Y-%m-%dT%H:%M:%S', True),
                              ('%Y-%m-%d', False)):
        try:
            moment = _dt.strptime(text, pattern)
        except ValueError:
            continue
        return moment.strftime('%Y-%m-%d %H:%M:%S'), has_time
    return None


def _int_or_none(value):
    if value in (None, '', 'null'):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def public_meta():
    """Справочники для интерфейса одним ответом: цели с причинами, итоги,
    варианты работы с сотрудником и виды занятия для «Тренингов»."""
    return {
        'targets': [{
            'code': item['code'],
            'title': item['title'],
            'hint': item['hint'],
            'unit': item['unit'],
            'unit_required': item['unit_required'],
            'employee': item['employee'],
            'processing': item['processing'],
            'reasons': item['reasons'],
        } for item in TARGETS],
        'results': RESULTS,
        'work_actions': WORK_ACTIONS,
        'training_reasons': list(TRAINING_REASONS),
    }
