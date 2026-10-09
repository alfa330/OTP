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
#   always    — сотрудники КЦ, фронт-офис, аренда авто и таксопарк: уходит
#               сразу. У жалобы на сотрудника ТЗ требует «обязательно довести
#               процесс до обратной связи», жалобу на аренду без группы
#               проверить некому. Таксопарк был «по выбору оператора», но
#               владелец 29.09.2026: «у оператора не должно быть возможности не
#               отправлять в группу» — выбора у оператора нет ни у одной цели.
#   review    — Яндекс. Сначала жалобу проверяет супервайзер (с 09.10.2026 —
#               любой СВ отдела оператора, а не только его группы) и сам
#               решает: стоит она внимания — «Отправить в группу», нет —
#               «Решено» с итогом (владелец, 30.09.2026). До этого жалобы на
#               Яндекс только фиксировались для аналитики и не проверялись
#               никем; у таких старых жалоб review_state пуст, и они остаются
#               «зафиксированными».
PROCESS_ALWAYS = 'always'
PROCESS_REVIEW = 'review'

# Проверка супервайзером (у целей с processing = review).
#   pending   — ждёт супервайзера: жалоба открыта и стоит в «К разбору»;
#   sent      — супервайзер отправил её в группу, дальше обычный путь;
#   resolved  — супервайзер решил её сам, итог записан, в группу не уходила.
REVIEW_PENDING = 'pending'
REVIEW_SENT = 'sent'
REVIEW_RESOLVED = 'resolved'

# Итог, который ставит «Решено». Сам итог супервайзер пишет словами (он и есть
# «прописать итог»), а код нужен аналитике — «Вопрос решён» и есть «Решено».
REVIEW_RESOLVED_RESULT = 'solved'

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
        'hint': 'Тарифы, заказы, комиссии, приложение — сначала проверит супервайзер',
        'unit': None,
        'unit_required': False,
        'employee': False,
        'processing': PROCESS_REVIEW,
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
# Три действия — три кнопки под блоком «Работа с сотрудником» (владелец,
# 30.09.2026, вместо одной «Записать работу» с шестью вариантами ТЗ):
#
#   Назначить тренинг     — день и время. В этот день супервайзерам отдела
#                           сотрудника и самому сотруднику приходит уведомление
#                           (колокол, источник training_plans). Работа остаётся
#                           открытой: тренинг ещё не проведён.
#   Проведён тренинг      — дата, начало и конец. Занятие сразу ложится в
#                           «Тренинги» и идёт в часы сотрудника, как ОС по
#                           оценке звонка. Закрывает работу.
#   Приняты другие меры   — комментарий, что сделано. Закрывает работу.
#
# Запись работы по-прежнему ведётся журналом: сначала «назначен», потом
# «проведён», и оба факта остаются. Остальные три варианта ТЗ (ОС, разбор,
# «обучение не требуется») новыми записями больше не создаются, но в журнале
# их подписи нужны — ими записана работа до 30.09.2026.
# ─────────────────────────────────────────────────────────────────────────────

ACTION_FEEDBACK = 'feedback'
ACTION_REVIEW = 'review'
ACTION_TRAINING_ASSIGNED = 'training_assigned'
ACTION_TRAINING = 'training'
ACTION_NO_TRAINING = 'no_training'
ACTION_OTHER = 'other'

# title        — подпись записи в журнале (что сделано);
# button       — подпись кнопки (что сделать); есть только у действующих;
# training     — создаёт запись в «Тренингах» (нужны дата и время занятия);
# plan         — нужен день и время, на которые тренинг назначен;
# ask_training — у записи была галочка «Нужен тренинг» (только старые записи);
# closes       — запись завершает работу с сотрудником, если после неё не
#                осталось требования тренинга.
WORK_ACTIONS = [
    {'code': ACTION_TRAINING_ASSIGNED, 'title': 'Назначен тренинг',
     'button': 'Назначить тренинг',
     'training': False, 'plan': True, 'ask_training': False, 'closes': False},
    {'code': ACTION_TRAINING, 'title': 'Проведён тренинг',
     'button': 'Проведён тренинг',
     'training': True, 'plan': False, 'ask_training': False, 'closes': True,
     'default_reason': 'Тренинг по качеству. Разбор ошибок'},
    {'code': ACTION_OTHER, 'title': 'Приняты другие меры',
     'button': 'Приняты другие меры',
     'training': False, 'plan': False, 'ask_training': False, 'closes': True},
    # Выведены 30.09.2026 — только для подписей старых записей журнала.
    {'code': ACTION_FEEDBACK, 'title': 'Обратная связь проведена',
     'training': True, 'plan': False, 'ask_training': True, 'closes': True,
     'default_reason': 'Обратная связь'},
    {'code': ACTION_REVIEW, 'title': 'Проведён дополнительный разбор ситуации',
     'training': False, 'plan': False, 'ask_training': True, 'closes': True},
    {'code': ACTION_NO_TRAINING, 'title': 'Дополнительное обучение не требуется',
     'training': False, 'plan': False, 'ask_training': False, 'closes': True},
]

WORK_ACTION_BY_CODE = {item['code']: item for item in WORK_ACTIONS}

# Чем можно записать работу сейчас — ровно три кнопки. Сервер прочие коды
# отвергает: иначе выведенное из интерфейса «обучение не требуется» осталось бы
# лазейкой закрыть работу в обход трёх кнопок.
ACTIVE_WORK_ACTIONS = (ACTION_TRAINING_ASSIGNED, ACTION_TRAINING, ACTION_OTHER)

# Что снимает требование тренинга: проведённый тренинг, «приняты другие меры»
# и (в старых записях) «обучение не требуется».
#
# «Другие меры» снимают назначенный тренинг с 30.09.2026. Пока кнопок было
# шесть, отменить назначенный тренинг можно было записью «обучение не
# требуется», а «другие меры» его не трогали. Теперь кнопки три, и без этого
# назначенный, но так и не проведённый тренинг (сотрудник ушёл, решили иначе)
# держал бы жалобу открытой навсегда: закрыть её было бы нечем, кроме записи
# о тренинге, которого не было. Окно «Приняты другие меры» при назначенном
# тренинге прямо говорит, что тренинг будет снят.
TRAINING_CLEARING_ACTIONS = (ACTION_TRAINING, ACTION_NO_TRAINING, ACTION_OTHER)

# Чем можно закрыть работу, когда сотрудника так и не определили: объяснить,
# почему дальше работать не с кем, — «указать, почему дополнительная работа не
# требуется» (ТЗ). Тренинг без сотрудника не назначить и не провести, поэтому
# из действующих кнопок остаются только «другие меры».
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

    Правило одно: запись, которая «закрывает» (проведённый тренинг, другие
    меры, а в старых записях ОС, разбор и «обучение не требуется»), завершает
    работу, если после неё не остаётся требования тренинга. «Требуется
    тренинг» ставит «Назначен тренинг» (и галочка «Нужен тренинг» у старых
    записей ОС и разбора); снимают — TRAINING_CLEARING_ACTIONS.
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
        # ОС и разбор после «назначен тренинг» его не отменяют (сверка с ТЗ
        # 29.09.2026) — снимают только записи из TRAINING_CLEARING_ACTIONS.
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
    """Уходит ли жалоба в группу СРАЗУ при создании. Решает цель, а не
    оператор: присланный клиентом requires_processing сервер не слушает
    (владелец, 29.09.2026). Жалоба на проверке супервайзера уходит в группу
    позже — его кнопкой, и тогда флаг ставит сервис (service.review_send)."""
    return processing_mode(target_code) == PROCESS_ALWAYS


def requires_review(target_code):
    """Проверяет ли жалобу супервайзер до группы (Яндекс, с 30.09.2026)."""
    return processing_mode(target_code) == PROCESS_REVIEW


def is_closed(*, requires_processing, result_code, work_state, review_state=None):
    """Отработана ли жалоба целиком.

    Условия, и все из ТЗ или от владельца:
      * у жалобы, которая уходила на разбор, зафиксирован итог проверки
        («по обращению должен фиксироваться итоговый результат обработки»);
      * жалоба на сотрудника «не может считаться полностью закрытой до тех пор,
        пока супервайзер не завершил обязательную работу с сотрудником либо не
        указал, почему дополнительная работа не требуется»;
      * жалоба на проверке у супервайзера (Яндекс) открыта, пока он не решил:
        в группу или «Решено». Отправленная дальше идёт обычным путём и ждёт
        итога, решённая закрыта его итогом.

    Сотрудник так и не определён — работа с ним держит жалобу, только если она
    ПОДТВЕРДИЛАСЬ: подтверждённая жалоба на сотрудника без сотрудника — это
    работа, которую никто не провёл. Закрыть её можно, определив сотрудника или
    записав, почему работать не с кем (UNASSIGNED_ACTIONS: work_state станет
    done). Не подтвердилась, «недостаточно данных», «не в зоне влияния» —
    разбирать некого, итог это и объясняет.

    Жалоба, которую только зафиксировали (так до 30.09.2026 сохранялись жалобы
    на Яндекс), итога не ждёт: её никто не проверяет — она для аналитики.
    """
    if review_state == REVIEW_PENDING:
        return False
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
        'review_state': REVIEW_PENDING if requires_review(code) else None,
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
    варианты работы с сотрудником (все — для подписей журнала, кнопками —
    только действующие) и виды занятия для «Тренингов»."""
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
        'work_buttons': [WORK_ACTION_BY_CODE[code] for code in ACTIVE_WORK_ACTIONS],
        'training_reasons': list(TRAINING_REASONS),
    }
