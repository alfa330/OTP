# -*- coding: utf-8 -*-
"""Запись разговора для карточки звонка.

Владелец, 13.11.2026: карточка, которую оператор не успел заполнить даже за время
постобработки (ему уже поступил следующий звонок), остаётся в черновиках «для
последующего заполнения вместе с аудио» — оператор слушает, о чём был разговор, и
дозаполняет причину.

Запись лежит у АТС отдела. Телефон знает звонок по SIP Call-ID, АТС — по своему номеру
звонка, общего ключа у них нет. Поэтому звонок ищется по тому, что известно обеим
сторонам: внутренний номер оператора, номер собеседника и время ответа. Найденный номер
звонка АТС запоминается в карточке (client_cards.record_call_id): список звонков у
Binotel — «нагружаемый» метод с бюджетом около пяти запросов в минуту на весь кабинет,
а ссылка на запись по известному номеру звонка — лёгкий.

Источник записей у отдела свой (у отделов разные АТС): {код отдела: источник}. Сейчас он
один — Binotel кабинета Тез. У отдела без источника записи в карточке нет.
"""

import logging
import re

from . import rules

log = logging.getLogger(__name__)

# Карточка помнит, когда оператор ответил (отметка сервера по событию телефона); АТС —
# когда звонок начался и сколько ждал ответа. Эти моменты описывают одно и то же, но
# расходятся на секунды: событие телефона идёт до сервера, а АТС считает ожидание по-своему.
# Допуск с запасом: два отвеченных разговора одного оператора с одним номером, начатые в
# пределах этих минут, редкость — и тогда берётся ближайший.
MATCH_WINDOW_SEC = 300
# Звонок у АТС начинается раньше, чем карточка: входящий сначала стоит в очереди. Окно
# запроса списка звонков оператора — с этим запасом до начала карточки и после её конца.
LOOKUP_BEFORE_SEC = 30 * 60
LOOKUP_AFTER_SEC = 5 * 60
# Сколько оператор ждёт ответа АТС: список звонков идёт в общей очереди кабинета.
LOOKUP_MAX_WAIT_SEC = 10
URL_MAX_WAIT_SEC = 8


class RecordError(Exception):
    """Запись получить не удалось. message — человеку, code — экрану, status — HTTP."""

    def __init__(self, message, code='RECORD_ERROR', status=502):
        super().__init__(message)
        self.code = code
        self.status = status


def _digits(value):
    return re.sub(r'\D', '', str(value or ''))


def to_unix(moment):
    """Наивное время Алматы (как в таблицах раздела) → unix-время, которым считает АТС."""
    return int(moment.replace(tzinfo=rules.ALMATY).timestamp())


def match_call(calls, *, phone, answered_ts, window=MATCH_WINDOW_SEC):
    """Звонок АТС, которому соответствует карточка: тот же собеседник, разговор был, ответ —
    ближе всего к моменту ответа в карточке и не дальше window секунд. None — такого нет.

    calls — звонки оператора в виде binotel.client (external_number, start_time, waitsec,
    billsec, general_call_id). Номер сверяется по последним десяти цифрам: код страны АТС
    и портал пишут по-разному.
    """
    suffix = _digits(phone)[-10:]
    if len(suffix) < 10:
        return None
    best, best_delta = None, None
    for call in calls or ():
        if not _digits(call.get('external_number')).endswith(suffix) or not call.get('general_call_id'):
            continue
        if int(call.get('billsec') or 0) <= 0:
            continue
        answered = int(call.get('start_time') or 0) + int(call.get('waitsec') or 0)
        delta = abs(answered - int(answered_ts))
        if delta > window:
            continue
        if best is None or delta < best_delta:
            best, best_delta = call, delta
    return best


class BinotelRecords:
    """Записи разговоров отдела у Binotel.

    client_factory() — клиент API кабинета (binotel.client.BinotelApiClient) или None, если
    ключей кабинета в окружении нет.
    """

    def __init__(self, client_factory):
        self._client_factory = client_factory

    def _client(self):
        client = self._client_factory()
        if client is None:
            raise RecordError('Связь с АТС не настроена — запись недоступна', 'RECORD_UNAVAILABLE', 503)
        return client

    def find(self, *, sip, phone, started_at, answered_at, ended_at):
        """Номер звонка АТС для карточки или None, если АТС такого звонка (пока) не знает."""
        from binotel.client import RateLimited
        client = self._client()
        try:
            calls = client.list_calls_by_internal_number(
                sip, to_unix(started_at) - LOOKUP_BEFORE_SEC, to_unix(ended_at) + LOOKUP_AFTER_SEC,
                max_wait=LOOKUP_MAX_WAIT_SEC)
        except RateLimited:
            raise RecordError('АТС сейчас занята. Попробуйте через минуту', 'RECORD_BUSY', 503)
        except Exception as error:  # noqa: BLE001 — сеть, отказ АТС: оператору — понятный текст
            # Отказ АТС — рядовое событие, а не сбой кода: строка в журнале без трассировки.
            log.warning('client_cards: список звонков Binotel не получен (sip=%s): %s', sip, str(error)[:200])
            raise RecordError('АТС не ответила. Попробуйте позже', 'RECORD_UNAVAILABLE', 502)
        call = match_call(calls, phone=phone, answered_ts=to_unix(answered_at))
        return str(call['general_call_id']) if call else None

    def url(self, call_id):
        """Ссылка на запись (подписанная, живёт недолго) или None, если запись ещё не готова."""
        from binotel.client import RateLimited
        client = self._client()
        try:
            return client.get_call_record_url(call_id, max_wait=URL_MAX_WAIT_SEC)
        except RateLimited:
            raise RecordError('АТС сейчас занята. Попробуйте через минуту', 'RECORD_BUSY', 503)
        except Exception as error:  # noqa: BLE001
            log.warning('client_cards: запись Binotel %s не получена: %s', call_id, str(error)[:200])
            raise RecordError('АТС не отдала запись. Попробуйте позже', 'RECORD_UNAVAILABLE', 502)


def _tez_binotel_client():
    """Клиент API кабинета Binotel Тез; None — ключей кабинета нет (стенд, тесты)."""
    from binotel import client as binotel
    config = binotel.get_config(company=binotel.COMPANY_TEZ)
    return binotel.BinotelApiClient.from_config(config) if binotel.api_ready(config) else None


def default_sources():
    """Источники записей по кодам отделов: {код: источник}."""
    return {'tez': BinotelRecords(_tez_binotel_client)}
