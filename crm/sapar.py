# -*- coding: utf-8 -*-
"""Доступ к API Sapar (tps-public.silt.kz) — только сеть, без правил.

Зачем. Половину вопросов, которые оператор задаёт водителю и потом пересылает
в рабочую группу, Sapar знает сам: сформированы ли документы за месяц по парку,
есть ли они у конкретного водителя, подписаны ли. Спрашивать про это человека и
занимать этим группу незачем — достаточно ИИН и периода.

Что здесь есть и чего нет. Здесь HTTP: адрес, токен, таймаут, разбор ответа.
Решение «что это значит для обращения» принимает crm.scenarios::sapar_verdict —
он чистый и проверяется тестами без сети. Разделение то же, что и у остального
раздела: правила отдельно от транспорта.

Ошибки наружу не летят. Sapar недоступен — обращение всё равно должно
заводиться: снимок возвращается с available=False, и мастер просто проводит
оператора по обычным вопросам. Считать при этом «документов нет» нельзя — это
противоположный по смыслу ответ, и по нему обращение закрыли бы зря.
"""

import logging
import os
import threading
import time

import requests

DEFAULT_BASE_URL = 'https://tps-public.silt.kz'
TIMEOUT = 8

# Статусы документов Яндекса (ServiceAvrStatus) и АВР парка (AvrStatus) — как их
# отдаёт Sapar. Переводим здесь: и в карточке, и в группе должно быть по-русски
# и одинаково, а не «НаПодписанииУВодителя» в одном месте и «ждёт» в другом.
STATUS_LABELS = {
    'НеСформировано': 'не сформирован',
    'НаПодписанииУЯндекса': 'на подписании у Яндекса',
    'НаПодписанииУВодителя': 'ждёт подписи водителя',
    'Подписано': 'подписан',
    'СрокПодписанияИстек': 'срок подписания истёк',
    'ИдетСохранениеФайла': 'сохраняется',
    'Rejected': 'отклонён',
    'Cancelled': 'отменён',
    'Active': 'активен',
    'Inactive': 'неактивен',
    'Signed': 'подписан',
    'Pending': 'ожидает',
    'Waiting': 'ожидает',
}

# Статусы, при которых документ считается подписанным. Множество, а не сравнение
# со строкой: у Яндекса и у парка это разные слова про одно и то же.
SIGNED = ('Подписано', 'Signed')

# ГЛАВНОЕ ПРО ЭТУ РУЧКУ. «Документы поступили водителю на подписание» — это
# непустой YandexDocuments, и ТОЛЬКО он. TaxiParkDocuments (АВР парка) приходит
# и тем, кому подписывать нечего: на выборке 21.08.2026 у 7 водителей из 25 без
# документов ручка всё равно вернула строку АВР в статусе Active или Signed.
#
# Проверено на 75 водителях за 07.2026, разбитых по ArrivalStatus самого Sapar:
# Arrived — документы Яндекса есть у всех 50, NotArrived — нет ни у одного из 25.
# То есть `ArrivalStatus == Arrived` и «непустой YandexDocuments» — одно и то же,
# а объединение двух списков дало бы «документы есть» примерно четверти тех, у
# кого их нет. Для тематики «Документы не поступили» это означало бы закрытое
# обращение по верной жалобе — поэтому решает только список Яндекса.

# Готовность документов ПО ПАРКУ за месяц одна на всех, и спрашивать её на каждое
# обращение незачем: в начале месяца её успевают спросить десятки операторов
# подряд, и ответ у всех будет один.
READY_TTL = 300
_ready_cache = {}
_ready_lock = threading.Lock()


def base_url():
    return (os.getenv('SAPAR_API_URL') or DEFAULT_BASE_URL).strip().rstrip('/')


def _token():
    return (os.getenv('SAPAR_API') or '').strip()


def configured():
    """Есть ли доступ к Sapar. Нет — раздел работает как работал."""
    return bool(_token())


def status_label(code):
    return STATUS_LABELS.get(str(code or ''), str(code or '')) or 'неизвестен'


def reset_cache():
    """Для тестов и для ручного сброса: следующий вопрос уйдёт в Sapar."""
    with _ready_lock:
        _ready_cache.clear()


def _call(path, *, body=None, params=None):
    """Возвращает (Response-часть ответа, ошибка). Исключений не бросает."""
    if not _token():
        return None, 'SAPAR_API не настроен'
    try:
        response = requests.request(
            'POST' if body is not None else 'GET', base_url() + path,
            json=body, params=params,
            headers={'Authorization': 'Bearer ' + _token(), 'Accept': 'application/json'},
            timeout=TIMEOUT,
        )
        payload = response.json()
    except Exception as error:  # noqa: BLE001
        logging.warning('sapar: %s не ответил: %s', path, error)
        return None, str(error)
    if not isinstance(payload, dict):
        return None, 'неожиданный ответ Sapar'
    # Конверт свой: HTTP 200 приходит и на отказ, настоящий код лежит внутри.
    code = payload.get('Code')
    try:
        failed = int(code) >= 400
    except (TypeError, ValueError):
        failed = False
    if failed:
        return None, payload.get('Message') or ('код %s' % code)
    return payload.get('Response'), None


def month_documents_ready(month, year):
    """Сформированы ли закрывающие документы за месяц ПО ПАРКУ. None — не знаем.

    Это первый вопрос: пока Яндекс не выгрузил документы за месяц, их нет ни у
    кого, и разбираться с конкретным водителем рано.
    """
    key = (int(month), int(year))
    now = time.time()
    with _ready_lock:
        cached = _ready_cache.get(key)
    if cached and cached[0] > now:
        return cached[1]

    payload, error = _call('/fleetsoft/are-docs-ready-to-sign',
                           params={'month': int(month), 'year': int(year)})
    if error or not isinstance(payload, dict):
        return None
    value = bool(payload.get('AreDocsReadyForSign'))
    with _ready_lock:
        _ready_cache[key] = (now + READY_TTL, value)
    return value


def _document(row, source):
    status = row.get('Status')
    return {
        'source': source,
        'status': status,
        'status_label': status_label(status),
        'signed': str(status) in SIGNED,
        'sum': row.get('Sum') if source == 'yandex' else row.get('AvrSum'),
        'driver_name': row.get('DriverFio') or row.get('Name'),
    }


def driver_snapshot(iin, month, year):
    """Что Sapar знает про водителя за период. Никогда не бросает исключение.

    Возвращает словарь:
        available       — удалось ли спросить (False = Sapar молчит или не настроен)
        month_ready     — сформированы ли документы за месяц по парку (True/False/None)
        documents       — закрывающие документы Яндекса: ими и решается всё
        park_documents  — АВР парка, СПРАВОЧНО (см. заметку про SIGNED выше)
        driver_name     — ФИО из Sapar, если Sapar его знает
        error           — текст отказа, если он был

    documents = [] при available=True означает именно «документы не поступили»,
    и на этом можно строить решения. При available=False список пуст ровно
    потому, что мы не спрашивали, и решать по нему нельзя — для того и флаг.
    """
    snapshot = {'available': False, 'month_ready': None, 'documents': [],
                'park_documents': [], 'driver_name': None, 'error': None,
                'iin': str(iin or '').strip(),
                'month': int(month), 'year': int(year)}
    if not configured():
        snapshot['error'] = 'Доступ к Sapar не настроен'
        return snapshot

    payload, error = _call('/taxipark-api/get-driver-documents-by-iin',
                           body={'DriverIin': snapshot['iin'],
                                 'Month': int(month), 'Year': int(year)})
    if error:
        snapshot['error'] = error
        return snapshot

    payload = payload or {}
    documents = [_document(row, 'yandex') for row in (payload.get('YandexDocuments') or [])]
    park = [_document(row, 'park') for row in (payload.get('TaxiParkDocuments') or [])]
    snapshot['available'] = True
    snapshot['documents'] = documents
    snapshot['park_documents'] = park
    snapshot['driver_name'] = next((d['driver_name'] for d in documents + park
                                    if d['driver_name']), None)
    # Готовность по парку спрашиваем только когда водителю ничего не поступило:
    # если документы у него есть, месяц заведомо сформирован, и второй запрос
    # ничего не добавит.
    snapshot['month_ready'] = True if documents else month_documents_ready(month, year)
    return snapshot
