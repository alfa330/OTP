# -*- coding: utf-8 -*-
"""Маркетинговый отбор: шесть осей ТЗ #317 (ФТ-05…ФТ-10) — разбор и SQL.

Модуль держит ДВЕ вещи и больше ничего:

1. `normalise()` — запрос человека → проверенные значения. Неверное значение
   это 400 с текстом, а не молча снятый фильтр: список без обещанного фильтра
   выглядит рабочим, только показывает чужие строки (то же правило, что в
   `call_qa.api.normalise_list_filters`, — там оно уже стоило разбирательств).
2. `JOIN_SQL` + `predicate()` — как это ложится в запрос списка.

Зеркало на фронте — `src/components/call_qa/marketingFilters.js`; расхождение
сторожит `tests/ai_qa_marketing_filters.test.mjs`. Разойтись им нельзя: панель
рисует чипы по своим правилам, а выборку и счётчик «Показано N из M» делает
сервер по своим, и один и тот же отбор давал бы разные ответы.

Почему связь лежит таблицей, а не считается здесь
-------------------------------------------------
Сопоставить звонок сделке можно только окнами по телефону (`linker.py`), и это
перебор по всей истории номера. Делать его на каждый показ списка нельзя, а
держать в SQL — тем более: правило нетривиальное, у него есть допуск на
опережение и стыковка окон встык. Поэтому связь вычислена заранее и лежит в
`qa_subject_deals`, а здесь остаётся обычный JOIN по ключу.

Что берётся ЖИВЫМ join'ом, а не из снимка
-----------------------------------------
Этап, причина, ответственный и парк — всё это читается из `op_funnel_leads` и
словаря в момент запроса. Снимок, снятый при связывании, назавтра врал бы: в
этом и смысл ФТ-08. Исключение ровно одно — «этап на момент разговора»: он по
определению исторический и берётся из журнала `op_funnel_lead_stages`.
"""

import re

# Код оси: то, что уезжает в адресную строку, в пресет и в выгрузку.
_CODE_RE = re.compile(r'^[a-z0-9_\-]{1,64}$')

# «Не определено» / «Причина не указана» — не пустой фильтр, а отдельная корзина.
# У 60% сделок парк пуст и у 58% пуст канал (прод, сентябрь 2026): без корзины
# самая большая группа лидов просто исчезала бы из любого разреза, и сумма по
# каналам не сходилась бы с общим числом разборов.
NONE_BUCKET = 'none'

# Сколько значений принимаем в одном мультиселекте. Не защита от злого умысла, а
# защита от вставленного списка на тысячу строк: такой IN разворачивается в план
# на полэкрана и выполняется секундами.
MAX_VALUES = 50

STAGE_MODES = ('current', 'at_call')
HANDLER_MODES = ('spoke', 'crm')

# ── Как модуль прицепляется к запросу списка ────────────────────────────────
#
# Все JOIN'ы левые: субъект без сделки обязан остаться в списке (их большинство —
# в сентябре сделка нашлась у 29 звонков «Основы ОП» из 73). Фильтр по
# маркетинговой оси сам отсечёт их, когда его выставят, а без фильтра список
# остаётся прежним разделом «ИИ-оценки».
JOIN_SQL = """
                 LEFT JOIN qa_subject_deals sd
                        ON sd.subject_kind = rc.subject_kind AND sd.call_id = rc.call_id
                 LEFT JOIN op_funnel_leads l
                        ON l.direction_code = sd.direction_code AND l.source = sd.source
                       AND l.stream_type = sd.stream_type AND l.lead_key = sd.lead_key
                 LEFT JOIN qa_marketing_dict mpark
                        ON mpark.kind = 'park'
                       AND mpark.aliases ? lower(btrim(COALESCE(l.park_name, '')))
                 LEFT JOIN qa_marketing_dict mchan
                        ON mchan.kind = 'channel'
                       AND mchan.aliases ? lower(btrim(COALESCE(l.utm_source, '')))
                 LEFT JOIN amo_leads al
                        ON sd.source = 'amo' AND l.lead_key ~ '^[0-9]+$'
                       AND al.lead_id = NULLIF(l.lead_key, '')::bigint
                 LEFT JOIN op_funnel_operator_map omap
                        ON omap.source = sd.source AND omap.external_key = l.owner_raw
                 LEFT JOIN LATERAL (
                     SELECT ls.stage_raw, ls.reason_raw
                       FROM op_funnel_lead_stages ls
                      WHERE ls.source = sd.source AND ls.lead_key = sd.lead_key
                        AND ls.seen_at <= COALESCE(sd.happened_at, NOW())
                      ORDER BY ls.seen_at DESC
                      LIMIT 1
                 ) shist ON TRUE
                 LEFT JOIN LATERAL (
                     SELECT ls.stage_raw, ls.reason_raw
                       FROM op_funnel_lead_stages ls
                      WHERE ls.source = sd.source AND ls.lead_key = sd.lead_key
                      ORDER BY ls.seen_at DESC
                      LIMIT 1
                 ) slast ON TRUE
"""

# «Текущий этап» — ПОСЛЕДНЯЯ запись журнала, а снимок op_funnel_leads — только
# запасной вариант, пока журнала у сделки нет. Снимок здесь не годится в
# первоисточники: «Воронка ОП» намеренно не переписывает лиды зафиксированных
# суток (список за причиной обязан сходиться с итогом, названным на планёрке),
# и «текущий» этап из снимка у такой сделки застыл бы на дне заморозки.
# Журнал же пишется при каждой выгрузке независимо от заморозки.

# Выражения осей. Вынесены константами, потому что каждое используется трижды:
# в предикате отбора, в справочнике значений и в выгрузке.
PARK_CODE = "COALESCE(mpark.code, '')"
PARK_TITLE = "COALESCE(mpark.title, NULLIF(btrim(l.park_name), ''), '')"
CHANNEL_CODE = "COALESCE(mchan.code, '')"
CHANNEL_TITLE = "COALESCE(mchan.title, NULLIF(btrim(l.utm_source), ''), '')"
CAMPAIGN = "COALESCE(NULLIF(btrim(al.utm_campaign), ''), '')"
# btrim(NULL) = NULL, поэтому COALESCE переходит к снимку ТОЛЬКО когда строки
# журнала нет. Пустая причина в журнале — это законный ответ «сделка вышла из
# отказа», и NULLIF здесь превращал бы его обратно в устаревшую причину снимка.
STAGE_CURRENT = "COALESCE(btrim(slast.stage_raw), btrim(l.stage_raw), '')"
STAGE_AT_CALL = "COALESCE(btrim(shist.stage_raw), '')"
REASON_CURRENT = "COALESCE(btrim(slast.reason_raw), btrim(l.reason_raw), '')"
RESPONSIBLE_ID = "omap.user_id"
RESPONSIBLE_RAW = "COALESCE(NULLIF(btrim(omap.external_name), ''), COALESCE(l.owner_raw, ''))"
DEAL_ID = "COALESCE(l.lead_key, '')"
DEAL_LINKED = "(sd.call_id IS NOT NULL)"

# Этапы «Закрыто-нереализовано». ФТ-09 включает причину отказа только на них.
# Список — не догадка: это фактические имена этапов воронки 5524684 в проде.
# Хранится здесь, а не в коде фронта, потому что по нему идёт и серверная
# проверка «причина без своего этапа», и подсказка в панели.
LOST_STAGE_MARKERS = ('закрыто и не реализовано', 'закрыто-нереализовано',
                      'закрыто не реализовано')


def is_lost_stage(stage):
    """Этап из группы «Закрыто-нереализовано»? По нему ФТ-09 включает причину."""
    text = str(stage or '').strip().lower()
    return any(marker in text for marker in LOST_STAGE_MARKERS)


def _codes(raw, field):
    """Список кодов осей: 'a,b,c' или ['a','b'] → ['a','b','c'] без повторов."""
    if raw in (None, '', []):
        return []
    values = raw if isinstance(raw, (list, tuple)) else str(raw).split(',')
    out = []
    for value in values:
        code = str(value or '').strip().lower()
        if not code:
            continue
        if not _CODE_RE.match(code):
            raise ValueError(f"{field}: недопустимое значение «{value}»")
        if code not in out:
            out.append(code)
    if len(out) > MAX_VALUES:
        raise ValueError(f"{field}: за раз можно выбрать не больше {MAX_VALUES} значений")
    return out


def _labels(raw, field):
    """Список СЫРЫХ значений (этапы, причины, кампании): сравниваются как есть.

    Нормализовать их нельзя: имя этапа и текст причины — это то, что человек
    выбрал в amoCRM, и в справочнике причин за два месяца встречается «не
    интеросно» рядом с «не интересно». Сведение их к коду здесь потеряло бы
    ровно ту разницу, ради которой маркетинг и смотрит причины.
    """
    if raw in (None, '', []):
        return []
    values = raw if isinstance(raw, (list, tuple)) else str(raw).split('\x1f')
    out = []
    for value in values:
        text = str(value or '').strip()
        if not text:
            continue
        if len(text) > 300:
            raise ValueError(f"{field}: значение длиннее 300 символов")
        if text not in out:
            out.append(text)
    if len(out) > MAX_VALUES:
        raise ValueError(f"{field}: за раз можно выбрать не больше {MAX_VALUES} значений")
    return out


def _ids(raw, field):
    if raw in (None, '', []):
        return []
    values = raw if isinstance(raw, (list, tuple)) else str(raw).split(',')
    out = []
    for value in values:
        text = str(value or '').strip()
        if not text:
            continue
        try:
            number = int(text)
        except (TypeError, ValueError):
            raise ValueError(f"{field}: ожидается число, пришло «{value}»")
        if number not in out:
            out.append(number)
    if len(out) > MAX_VALUES:
        raise ValueError(f"{field}: за раз можно выбрать не больше {MAX_VALUES} значений")
    return out


def normalise(raw):
    """Маркетинговые оси из запроса → проверенные значения. ValueError → 400.

    Пустой результат ({}) значит «маркетингового отбора нет», и тогда раздел
    работает ровно как до модуля: ни одного лишнего условия в запросе.
    """
    raw = raw or {}
    out = {}

    parks = _codes(raw.get('parks'), 'parks')
    if parks:
        out['parks'] = parks
    channels = _codes(raw.get('channels'), 'channels')
    if channels:
        out['channels'] = channels

    campaigns = _labels(raw.get('campaigns'), 'campaigns')
    if campaigns:
        out['campaigns'] = campaigns

    stages = _labels(raw.get('stages'), 'stages')
    if stages:
        out['stages'] = stages

    stage_mode = str(raw.get('stage_mode') or '').strip().lower()
    if stage_mode and stage_mode not in STAGE_MODES:
        raise ValueError("stage_mode: допустимы current или at_call")
    # Режим запоминаем, только когда этапы выбраны: одинокий «на момент
    # разговора» без самих этапов — это чип ни о чём в панели.
    if stages and stage_mode == 'at_call':
        out['stage_mode'] = 'at_call'

    reasons = _labels(raw.get('reasons'), 'reasons')
    if reasons:
        # ФТ-09: причина живёт только при выбранных этапах «Закрыто-нереализовано».
        # Проверяем и на сервере, а не только гашением контрола: отбор уезжает
        # в пресет и в адресную строку, и восстановленный оттуда набор обязан
        # означать то же самое, что набранный руками.
        if not any(is_lost_stage(stage) for stage in stages):
            raise ValueError(
                "Причина отказа выбирается только вместе с этапом группы "
                "«Закрыто-нереализовано»")
        out['reasons'] = reasons

    handlers = _ids(raw.get('handler_ids'), 'handler_ids')
    if handlers:
        out['handler_ids'] = handlers
    handler_groups = _ids(raw.get('handler_group_ids'), 'handler_group_ids')
    if handler_groups:
        out['handler_group_ids'] = handler_groups

    handler_mode = str(raw.get('handler_mode') or '').strip().lower()
    if handler_mode and handler_mode not in HANDLER_MODES:
        raise ValueError("handler_mode: допустимы spoke или crm")
    if (handlers or handler_groups) and handler_mode == 'crm':
        out['handler_mode'] = 'crm'

    deal_id = str(raw.get('deal_id') or '').strip()
    if deal_id:
        if not re.match(r'^[0-9]{1,20}$', deal_id):
            raise ValueError("Номер сделки — это число")
        out['deal_id'] = deal_id

    return out


def predicate(filters, *, operator_id_sql, group_of_person):
    """(sql, params) для маркетинговых осей. Пустой отбор — пустая строка.

    `operator_id_sql` — выражение «кто говорил», уже известное разделу.
    `group_of_person(person_sql)` — как раздел считает группу человека НА ДЕНЬ
    разговора. И то и другое приходит снаружи: собственного определения
    оператора и группы у модуля быть не должно, иначе «Группа» в маркетинговом
    фильтре означала бы не то же, что «Группа» в соседнем, и две выборки
    разошлись бы на людях, сменивших группу посреди месяца.
    """
    filters = filters or {}
    if not filters:
        return "", ()
    sql = ""
    params = []

    def bucketed(expression, codes):
        """Условие по оси со своей корзиной «Не определено»."""
        real = [code for code in codes if code != NONE_BUCKET]
        clauses = []
        if real:
            clauses.append(f"{expression} = ANY(%s)")
            params.append(real)
        if NONE_BUCKET in codes:
            clauses.append(f"{expression} = ''")
        return "(" + " OR ".join(clauses) + ")"

    if filters.get('parks'):
        sql += " AND " + bucketed(PARK_CODE, filters['parks'])
    if filters.get('channels'):
        sql += " AND " + bucketed(CHANNEL_CODE, filters['channels'])

    if filters.get('campaigns'):
        sql += f" AND {CAMPAIGN} = ANY(%s)"
        params.append(filters['campaigns'])

    if filters.get('stages'):
        stage_sql = (STAGE_AT_CALL if filters.get('stage_mode') == 'at_call'
                     else STAGE_CURRENT)
        sql += f" AND {stage_sql} = ANY(%s)"
        params.append(filters['stages'])

    if filters.get('reasons'):
        sql += " AND " + bucketed(REASON_CURRENT, filters['reasons'])

    handlers = filters.get('handler_ids') or []
    groups = filters.get('handler_group_ids') or []
    if handlers or groups:
        if filters.get('handler_mode') == 'crm':
            # «Ответственный»: тот, за кем сделка закреплена в amoCRM. Имя
            # оттуда приходит строкой и сопоставляется вручную
            # (op_funnel_operator_map) — иначе один и тот же человек в двух
            # написаниях разошёлся бы на два фильтра.
            person_sql = RESPONSIBLE_ID
        else:
            person_sql = operator_id_sql
        clauses = []
        if handlers:
            clauses.append(f"{person_sql} = ANY(%s)")
            params.append(handlers)
        if groups:
            clauses.append(f"{group_of_person(person_sql)} = ANY(%s)")
            params.append(groups)
        sql += " AND (" + " OR ".join(clauses) + ")"

    if filters.get('deal_id'):
        sql += f" AND {DEAL_ID} = %s"
        params.append(filters['deal_id'])

    return sql, tuple(params)
