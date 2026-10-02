# -*- coding: utf-8 -*-
"""Скорость принятия лида в работу — показатель «Табло ОП» (владелец, 02.10.2026).

Определение владельца: «время поступления лида в amoCRM и момент попытки дозвона по основе».
По словам:

  * лид — сделка воронки «Отдел продаж» amoCRM, созданная в сутки табло. Сделки берутся из
    снимка «Воронки ОП» (`op_funnel_leads`, источник amo): инкремент обновляет его раз в
    3 минуты вместе с телефонами контактов. Второй поход в amoCRM за теми же сделками ради
    табло был бы дублем запросов и второй копией правил;
  * поступление — создание сделки (`created_at`, время Алматы, как у касаний);
  * попытка дозвона — исходящий звонок, в котором станция набрала клиента. К сделке звонок
    относится по правилу режима «Сделки» раздела «Касания» (`cdr.leads.assign_touches`): по
    телефону, в окне от двух минут до создания сделки до следующей сделки того же номера, —
    чтобы табло и раздел видели у сделки одни и те же звонки;
  * по основе — набирал сотрудник группы «Основа» (модель op_osnova). Звонок ЯР или «Потока»
    по тому же номеру — работа их собственной базы, а не взятие сделки «Основы»;
  * средняя — среднее арифметическое по сделкам, где попытка уже была, усечённое до секунды,
    как остальные средние табло.

Момент набора у заявки автообзвона
----------------------------------
У ручного исходящего начало касания и есть набор: время в имени файла записи `out-…` совпало с
ним у всех 3641 ручного звонка 29.09–01.10.2026 (расхождение 0–2 с). У заявки автообзвона
(исходящий с очередью) начало касания — момент ЗАЯВКИ: она ждёт в очереди свободного оператора,
и клиента набирают, когда его телефон взял заявку, — в те же сутки на 3 с позже в медиане, на
26–88 с у каждой десятой и до 12 минут в худшем случае. Поэтому момент набора берётся из имени
файла записи плеча к клиенту: станция начинает её ровно с набора. Заявку без такого файла станция
клиенту не набирала вовсе (72–110 в сутки, все — «Сброс без разговора»), попыткой она не считается.
Кроме заявки с разговором: разговор исходящего склейка берёт только с плеча к клиенту
(`cdr.touches._outcome_legs`), значит, клиента набрали, просто запись не написалась. Её момент —
начало заявки: ошибка на ожидание в очереди, в медиане 3 с.

Список сделок отстаёт от amoCRM на минуты
-----------------------------------------
Инкремент ходит раз в 3 минуты (до 02.10.2026 — раз в 15). Пока новая сделка не доехала, набор
по ней достаётся более ранней сделке того же номера (её окно ещё не закрыто) — до ближайшего
инкремента. Обрезать окна моментом выгрузки пробовали на 29.09–01.10.2026, ещё при 15 минутах:
отклонение от точной цифры не уменьшилось (у худших моментов 48/395/203 с против 37/176/169 с),
потому что откладываются и все свежие наборы по известным сделкам. Поэтому окна идут до
«сейчас», как у режима «Сделки».

Чего в среднем нет
------------------
Сделки, где клиент раньше первой попытки сам дозвонился и поговорил с оператором: её взяли в
работу входящим звонком, а исходящий после разговора — уже следующий шаг. Так выглядят сделки,
которые станция заводит на входящий звонок («8XXXXXXXXXX - Входящий»): без этого правила
повторный звонок такому клиенту через несколько часов ложился бы в среднее часами.

Сделки без попытки — считать нечего. Таких в сутках больше трети (28.09–01.10.2026: 356–410 из
936–1067): заведённые входящим звонком, чаты WhatsApp, аренда и чужие направления в той же
воронке, дубли номера. Поэтому их число табло не показывает — оно читалось бы как «не взяли».

За час — по часу первого набора
-------------------------------
Отбивке нужен и последний полный час (владелец, 02.10.2026: «в показателях за час тоже»). Сделка
относится к часу, в который её ВЗЯЛИ в работу, а не к часу прихода: так каждая взятая сделка
попадает ровно в один часовой отчёт со своим полным ожиданием, а часы в сумме дают итог дня. По
часу прихода сделка, которая к отбивке ещё ждёт, не попала бы ни в один часовой отчёт — и час,
когда лиды копились без звонков, выглядел бы благополучным.

Модуль чистый: ни базы, ни Flask — на вход списки словарей, на выход словарь показателя.
"""

import re
from datetime import datetime, timedelta

from cdr import leads as cdr_leads, touches as touches_mod

# Откуда сделки: снимок «Воронки ОП», направление «Основа», источник amoCRM.
LEAD_DIRECTION = 'op_osnova'
LEAD_SOURCE = 'amo'
# Чей набор — попытка «по основе»: модель расчёта группы, а не её имя (в имени — ФИО супервайзера).
OSNOVA_MODEL = 'op_osnova'

# Файл записи исходящего: out-[префикс*]<клиент>-<внутренний>-ГГГГММДД-ЧЧММСС-<uniqueid>.
_DIAL_RECORDING_RE = re.compile(r'out-(?:\d+\*)?\+?(\d{9,15})-\d{3,4}-(\d{8})-(\d{6})-')
# Допуск между часами строки CDR и именем файла: на живых данных 0–2 с.
DIAL_RECORDING_SLACK = timedelta(seconds=2)


def _moment(value):
    """Время касания или сделки → наивное время Алматы; мусор — None."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value or '').strip()[:19].replace('T', ' ')
    try:
        return datetime.strptime(text, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def _recording_dial(touch, started):
    """Начало плеча к клиенту по имени файла записи — только если файл этого звонка: тот же
    клиент и время внутри звонка. Иначе None."""
    matched = _DIAL_RECORDING_RE.search(str(touch.get('recording_url') or ''))
    if not matched or (touches_mod.norm_phone(matched.group(1))
                       != touches_mod.norm_phone(touch.get('phone'))):
        return None
    try:
        dialed = datetime.strptime(matched.group(2) + matched.group(3), '%Y%m%d%H%M%S')
    except ValueError:
        return None
    end = started + timedelta(seconds=int(touch.get('dial_seconds') or 0))
    if not started - DIAL_RECORDING_SLACK <= dialed <= end + DIAL_RECORDING_SLACK:
        return None
    return max(dialed, started)


def dial_moment(touch):
    """Когда станция набрала клиента в этом звонке; None — это не попытка дозвона.

    Ручной исходящий — начало касания; заявка автообзвона — начало плеча к клиенту по файлу
    записи, а без файла клиента не набирали, если только с ним не поговорили (см. докстринг
    модуля)."""
    if touch.get('call_type') != touches_mod.TYPE_OUT:
        return None
    started = _moment(touch.get('started_at'))
    if started is None:
        return None
    dialed = _recording_dial(touch, started)
    if dialed is not None:
        return dialed
    if str(touch.get('queue') or '').strip() and int(touch.get('talk_seconds') or 0) <= 0:
        return None
    return started


def osnova_scope(people, memberships):
    """(внутренние номера «Основы», id её групп) по составу табло на сутки.

    Номера — чей набор засчитывается попыткой; группы — при каком фильтре экрана показатель
    имеет смысл: у ЯР и «Потока» своих сделок amoCRM нет."""
    exts, group_ids = set(), set()
    for person in people or []:
        membership = (memberships or {}).get(person.get('id')) or {}
        if membership.get('model') != OSNOVA_MODEL:
            continue
        if membership.get('group_id') is not None:
            group_ids.add(int(membership['group_id']))
        ext = str(person.get('sip_number') or '').strip()
        if ext:
            exts.add(ext)
    return exts, sorted(group_ids)


def _talked_incoming(touch):
    return (touch.get('call_type') == touches_mod.TYPE_IN
            and int(touch.get('talk_seconds') or 0) > 0)


def take_speed(leads, touches, operator_exts, now, grace=cdr_leads.GRACE):
    """{'deals', 'taken', 'avg_seconds', 'hourly'} — принятие в работу сделок суток.

    leads — [{lead_key, created_at, phones, phone}] из снимка воронки; touches — исходящие и
    принятые входящие суток по их телефонам ({started_at, phone, ext, call_type, talk_seconds,
    dial_seconds, queue, recording_url}); operator_exts — внутренние номера «Основы»; now —
    правая граница окна последней сделки номера. `deals` — сделок суток, `taken` — из них
    в среднем (попытка была), `avg_seconds` — среднее или None, если попыток ещё не было;
    `hourly` — 24 часа [{hour, taken, avg_seconds}] по часу первого набора (докстринг модуля)."""
    prepared = []
    for lead in leads or []:
        moment = _moment(lead.get('created_at'))
        if moment is None:
            continue
        prepared.append({'key': str(lead.get('lead_key')), 'moment': moment,
                         'phones': cdr_leads.lead_phones(lead)})
    assigned = cdr_leads.assign_touches(prepared, list(touches or []), window_to=now, grace=grace)
    exts = {str(ext) for ext in (operator_exts or ()) if ext}
    total = taken = 0
    by_hour = [[0, 0] for _ in range(24)]   # [секунд, сделок] по часу первого набора
    for lead in prepared:
        own = assigned.get(lead['key']) or []
        dials = [moment for moment in (dial_moment(touch) for touch in own
                                       if str(touch.get('ext') or '') in exts)
                 if moment is not None and moment <= now]
        if not dials:
            continue
        first = min(dials)
        if any(_talked_incoming(touch) and (_moment(touch.get('started_at')) or first) < first
               for touch in own):
            continue
        # Набор в допуске до создания сделки (её завели уже во время звонка) — взяли сразу.
        seconds = max(0, int((first - lead['moment']).total_seconds()))
        total += seconds
        taken += 1
        # Набор из хвоста прошлых суток (сделка 00:01, звонок 23:59) — первый час этих суток.
        day_start = datetime.combine(lead['moment'].date(), datetime.min.time())
        bucket = by_hour[max(first, day_start).hour]
        bucket[0] += seconds
        bucket[1] += 1
    return {'deals': len(prepared), 'taken': taken,
            'avg_seconds': total // taken if taken else None,
            'hourly': [{'hour': hour, 'taken': count, 'avg_seconds': seconds // count if count else None}
                       for hour, (seconds, count) in enumerate(by_hour)]}
