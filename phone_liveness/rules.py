# -*- coding: utf-8 -*-
"""Правила пульса телефона — чистые функции: ни базы, ни сети.

Строка оператора (её собирает store.load_rows):
    operator_id, key (последнее живое событие статуса), note, event_at,
    pulse — последняя активность его сессий ТЕЛЕФОНА (None — таких сессий нет),
    seen  — последняя активность ЛЮБОЙ его сессии: телефон, вкладка сайта (None — нет).
Все моменты — наивное время Алматы, как в самих событиях.

Живое событие статуса шлёт не только телефон: чат-менеджеры отдела продаж ставят статус из
рабочего места в браузере. Их статус телефону не принадлежит, и закрывать его по молчанию
телефона нельзя — отсюда phone_owned.
"""
from datetime import timedelta

OFF_KEY = 'выключен'
# Пометки событий, которые пишет сервер: по ним закрытие отличается от выхода самого оператора.
CLOSE_NOTE = 'нет связи с телефоном'
RESTORE_NOTE = 'связь восстановлена'
# client_event_id серверных событий: повтор обхода не создаёт второго события о том же молчании.
CLOSE_ID_PREFIX = 'liveness-off-'
RESTORE_ID_PREFIX = 'liveness-on-'
PHONE_USER_AGENT = 'iCORE Phone'

# Сколько телефон должен молчать, чтобы статус закрыли (решение владельца 16.11.2026: 30 минут).
SILENCE_SECONDS = 30 * 60
# Статус закрывается моментом «последняя связь + столько»: телефон выходит на связь раз в
# десять минут, и умер он где-то между последней связью и следующей несостоявшейся.
CLOSE_LAG_SECONDS = 10 * 60
# Телефон вернулся — ждём столько, прежде чем продолжить статус: первым делом он досылает
# события, накопленные без связи, и статус мог смениться в них.
RESTORE_SETTLE_SECONDS = 60
# Самый свежий пульс среди ВСЕХ телефонов должен быть не старше: иначе молчат не телефоны, а
# сам сервер перестал отмечать активность сессий — закрывать по такой картине нельзя никого.
FRESH_PULSE_SECONDS = 10 * 60
# После старта сервера столько секунд никого не закрываем: за время перезапуска (деплой, сбой)
# запросы телефонов не доходили, и каждому нужно до десяти минут, чтобы выйти на связь снова.
WARMUP_SECONDS = 15 * 60
# Больше стольких закрытий разом и одновременно больше половины открытых статусов — похоже на
# сбой, а не на людей: обход пропускаем и пишем ошибку.
MASS_MIN = 5
MASS_SHARE = 0.5
# Дальше стольких дней назад последнее событие не ищем.
LOOKBACK_DAYS = 7
# Статус считается поставленным телефоном, если сессия телефона была активна не раньше чем за
# столько секунд до события: активность сессии отмечается не чаще раза в пять минут.
PHONE_EVENT_SLACK_SECONDS = 6 * 60


def _text(value):
    return str(value or '').strip()


def closed_by_server(row):
    """Последнее событие — наше закрытие, а не выход самого оператора."""
    return _text(row.get('key')).lower() == OFF_KEY and _text(row.get('note')) == CLOSE_NOTE


def phone_owned(row):
    """Статус поставлен телефоном: его сессия была жива, когда пришло событие.

    Статус из браузера (рабочее место «Чатов ОП») сюда не проходит: у такого человека сессии
    телефона нет вовсе или она замолчала задолго до события."""
    event_at, pulse = row.get('event_at'), row.get('pulse')
    if event_at is None or pulse is None:
        return False
    return pulse >= event_at - timedelta(seconds=PHONE_EVENT_SLACK_SECONDS)


def last_sign(row):
    """Последний признак жизни человека: событие статуса или активность любой его сессии.

    Любой, а не только телефонной: человек, у которого упал телефон, но открыт сайт, на месте —
    закрывать его статус сервер не должен, он увидит мёртвый телефон сам."""
    signs = [value for value in (row.get('event_at'), row.get('pulse'), row.get('seen')) if value is not None]
    return max(signs) if signs else None


def silent_seconds(row, now):
    sign = last_sign(row)
    if sign is None:
        return None
    return (now - sign).total_seconds()


def close_moment(row, now):
    """Каким моментом закрыть статус: последняя связь + CLOSE_LAG, но не раньше самого события
    и не позже «сейчас»."""
    moment = last_sign(row) + timedelta(seconds=CLOSE_LAG_SECONDS)
    floor = row['event_at'] + timedelta(seconds=1)
    return min(now, max(moment, floor))


def close_event_id(row):
    """Ключ закрытия — по последнему признаку жизни: одно молчание закрывается один раз."""
    return CLOSE_ID_PREFIX + last_sign(row).strftime('%Y%m%d%H%M%S')


def restore_moment(row):
    """Каким моментом продолжить статус: через секунду после закрытия, а не когда вернулась связь.

    Телефон, который вернулся БЕЗ нового события статуса, всё это время работал — пропадала
    только связь с сервером (интернет в офисе, сбой сети). Оператор за это не отвечает, и время
    между закрытием и возвратом ему не срезаем. Телефон, который действительно выключали,
    при запуске сам присылает стартовый статус — до возврата дело не доходит."""
    return row['event_at'] + timedelta(seconds=1)


def restore_event_id(row):
    """Ключ возврата — по закрытию, которое он отменяет: одно закрытие возвращается один раз."""
    return RESTORE_ID_PREFIX + row['event_at'].strftime('%Y%m%d%H%M%S')


def wants_close(row, now, silence=SILENCE_SECONDS):
    key = _text(row.get('key')).lower()
    if not key or key == OFF_KEY or not phone_owned(row):
        return False
    silent = silent_seconds(row, now)
    return silent is not None and silent >= silence


def wants_restore(row, now):
    """Телефон снова на связи после нашего закрытия, и прошла минута на досылку его событий.

    Только телефон: человек, открывший сайт из дома, статус умершего телефона не возвращает."""
    if not closed_by_server(row):
        return False
    pulse = row.get('pulse')
    if pulse is None or pulse <= row['event_at']:
        return False
    return (now - pulse).total_seconds() >= RESTORE_SETTLE_SECONDS


def plan(rows, now, freshest_pulse, uptime_seconds, silence=SILENCE_SECONDS):
    """Что сделать за обход: {'close': [строки], 'restore': [строки], 'held': причина или None}.

    Возврат статуса не ждёт ничего: он опирается на новый признак жизни. Закрытие держат три
    сторожа — сервер только что запустился, сам пульс не обновляется, молчат слишком многие."""
    rows = list(rows or [])
    restore = [row for row in rows if wants_restore(row, now)]
    candidates = [row for row in rows if wants_close(row, now, silence)]
    held = None
    if candidates:
        open_count = sum(1 for row in rows if _text(row.get('key')).lower() != OFF_KEY)
        if uptime_seconds < WARMUP_SECONDS:
            held = 'warmup'
        elif freshest_pulse is None or (now - freshest_pulse).total_seconds() > FRESH_PULSE_SECONDS:
            held = 'pulse_stale'
        elif len(candidates) > MASS_MIN and len(candidates) > MASS_SHARE * open_count:
            held = 'mass_silence'
    return {'close': [] if held else candidates, 'restore': restore, 'held': held,
            'candidates': len(candidates)}
