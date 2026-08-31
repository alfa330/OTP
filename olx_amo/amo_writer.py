# -*- coding: utf-8 -*-
"""Запись лида в amoCRM: сделка + контакт + тег + ответственный.

Все идентификаторы ниже сняты с БОЕВОГО аккаунта igroupkz (account_id 30241468)
чтением API 31.08.2026, а не взяты из документации. Это важно: одноимённые
сущности в аккаунте не уникальны, и «найти по имени» — прямой путь положить
сделку не туда.

Что выяснено на живом аккаунте и почему код такой
--------------------------------------------------

* **Этап «Новая заявка» есть в ДВУХ воронках** — в «Отделе продаж» (5524684 /
  48846277) и в «Тестовой воронке» (11084618 / 87053946). Поэтому этап задаётся
  парой чисел, и обе константы стоят рядом.

* **Теги прикладываем по id, а не по имени.** В справочнике тегов сделок рядом
  с нужными лежат мусорные двойники: `forma_ olx_цр` (с пробелом), `forma_olx_`,
  `forma_Olx_`, `forma_olx`, `forms_olx`. amoCRM при передаче тега по имени
  создаёт новый тег, если точного совпадения нет, — и очередной двойник
  появился бы уже от нашей руки, а отчётность по источникам разъехалась бы.

* **«Раб. тел.» — не отдельное поле.** Это значение enum `WORK` (1277159)
  внутри стандартного поля «Телефон» (field_id 892223, тип multitext). Отдельного
  поля с таким названием в аккаунте нет вовсе.

* **Ответственный «Администратор» — 8303491.** Ручка `/api/v4/users` нашей
  учётке закрыта (403 «Admin access only»), id получен через старую
  `/api/v2/account?with=users` и независимо подтверждён журналом событий уже
  созданных сделок с тегами `forma_olx_*`.

* **Блок `metadata` в комплексном добавлении не прикладываем.** В аккаунте
  включено «Неразобранное» (`is_unsorted_on = true`), и сделка с `metadata`
  уехала бы туда вместо этапа «Новая заявка».

* **Фильтра по тегам в API нет.** `filter[tags][]` не ошибка — он молча
  игнорируется и отдаёт неотфильтрованный список. Поэтому проверка «была ли уже
  сделка по этому номеру» идёт через `contacts?query=<номер>`, а основной
  дедупликацией остаётся собственный журнал: параметр `query` в документации
  amoCRM уже помечен как кандидат на удаление.
"""

import logging
import os

log = logging.getLogger(__name__)

# ── Идентификаторы боевого аккаунта ──────────────────────────────────────────
# Все вынесены в окружение с боевым значением по умолчанию: аккаунт живой, поля
# в нём иногда пересоздают, и подмена одного числа не должна требовать релиза.

PIPELINE_ID = int(os.getenv('OLX_AMO_PIPELINE_ID') or 5524684)          # «Отдел продаж»
STATUS_ID = int(os.getenv('OLX_AMO_STATUS_ID') or 48846277)            # «Новая заявка»
RESPONSIBLE_USER_ID = int(os.getenv('OLX_AMO_RESPONSIBLE_ID') or 8303491)  # «Администратор»

CONTACT_PHONE_FIELD_ID = int(os.getenv('OLX_AMO_PHONE_FIELD_ID') or 892223)
CONTACT_PHONE_WORK_ENUM_ID = int(os.getenv('OLX_AMO_PHONE_WORK_ENUM_ID') or 1277159)

# Теги сделок по кабинетам — id из справочника аккаунта. Имя рядом только для
# читаемости диффа и сообщений об ошибке; в запрос уходит исключительно id.
TAG_IDS = {
    'cr':      (717413, 'forma_olx_цр'),
    'adal':    (717421, 'forma_olx_adal'),
    'amanat':  (717419, 'forma_olx_amanat'),
    'itaxi':   (717425, 'forma_olx_itaxi'),
    'global':  (717417, 'forma_olx_global'),
    'jana':    (717423, 'forma_olx_jana'),
    'tenge':   (717427, 'forma_olx_tenge'),
    'noltaxi': (717411, 'forma_olx_noltaxi'),
    'arenda':  (718303, 'forma_arenda_olx'),
}

# Сколько сделок за один запрос принимает комплексное добавление.
COMPLEX_BATCH_LIMIT = 50


class AmoWriteError(Exception):
    """Не удалось записать в amoCRM. Обращение остаётся в очереди на повтор."""

    def __init__(self, message, status=None, payload=None):
        super(AmoWriteError, self).__init__(message)
        self.status = status
        self.payload = payload


def tag_for(cabinet_code):
    """(id, имя) тега кабинета. Без тега сделку не создаём — ТЗ запрещает прямо."""
    pair = TAG_IDS.get(cabinet_code)
    if not pair:
        raise AmoWriteError('для кабинета %r не задан тег сделки в amoCRM' % (cabinet_code,))
    return pair


def build_lead(phone, cabinet_code, contact_name=None, needs_manual_review=False,
               note=None):
    """Собрать тело сделки для POST /api/v4/leads/complex.

    Название сделки — ровно номер в формате 77XXXXXXXXX, как требует раздел 2.7
    ТЗ. В аккаунте встречаются и сделки вида «77XXXXXXXXX Имя Фамилия», но это
    результат ручного заведения; постановка требует номер, и мы её и выполняем.

    `needs_manual_review` НЕ меняет ни название, ни этап: помеченная сделка
    должна лежать в общей воронке, иначе её не увидят. Сама пометка приезжает
    отдельным примечанием уже после создания (см. `add_note`) — в комплексном
    добавлении `_embedded` официально принимает только contacts, companies, tags
    и metadata, и лишний ключ отвергается вместе со всей сделкой.
    """
    tag_id, tag_name = tag_for(cabinet_code)

    contact = {
        'custom_fields_values': [{
            'field_id': CONTACT_PHONE_FIELD_ID,
            'values': [{
                'value': phone,
                'enum_id': CONTACT_PHONE_WORK_ENUM_ID,
            }],
        }],
    }
    if contact_name:
        contact['first_name'] = contact_name

    lead = {
        'name': phone,
        'pipeline_id': PIPELINE_ID,
        'status_id': STATUS_ID,
        'responsible_user_id': RESPONSIBLE_USER_ID,
        '_embedded': {
            'tags': [{'id': tag_id, 'name': tag_name}],
            'contacts': [contact],
        },
    }

    return lead


MANUAL_REVIEW_NOTE = ('Номер из отклика OLX не удалось привести к формату '
                      '77XXXXXXXXX — нужна ручная проверка')


class AmoWriter(object):
    """Запись сделок в amoCRM поверх готового клиента из amo_leads.py.

    Клиент оттуда умеет только читать (`get`), поэтому POST добавлен здесь, а не
    переписан там: `amo_leads` обслуживает отбивку по источникам, у неё своя
    ответственность, и трогать её ради робота OLX незачем.
    """

    def __init__(self, client):
        self._client = client

    # -- транспорт ---------------------------------------------------------

    def _post(self, path, payload):
        """POST через ту же сессию, которой `AmoClient` читает.

        Сессию не подменяем и заголовок авторизации не собираем сами: его уже
        держит и обновляет `AmoClient._login`, и второй источник токена рано или
        поздно разъехался бы с первым. Отсюда же берётся перелогин по 401 —
        поведение ровно то же, что у чтения.
        """
        import time

        client = self._client
        # Токен по логину/паролю живёт ~40 минут; `get` проверяет срок так же.
        if time.time() > getattr(client, 'expires_at', 0):
            client._login()

        url = client.base + path
        for attempt in (1, 2):
            try:
                response = client.session.post(url, json=payload, timeout=60)
            except Exception as exc:        # noqa: BLE001 — наверх уходит своим типом
                raise AmoWriteError('amoCRM недоступна: %s' % (exc,))

            if response.status_code == 401 and attempt == 1:
                client._login()
                continue

            try:
                body = response.json()
            except ValueError:
                body = {'raw': response.text[:500]}

            if response.status_code >= 400:
                raise AmoWriteError(
                    'amoCRM отклонила запись (%s): %s'
                    % (response.status_code, str(body)[:400]),
                    status=response.status_code, payload=body)
            return body

        raise AmoWriteError('amoCRM не приняла запись даже после перелогина')

    # -- операции ----------------------------------------------------------

    def create_lead(self, phone, cabinet_code, contact_name=None,
                    needs_manual_review=False, note=None):
        """Завести сделку с контактом. Возвращает (lead_id, contact_id).

        Комплексное добавление создаёт сделку и контакт одним запросом — это и
        быстрее (SLA в минуту), и безопаснее: при раздельном создании контакт мог
        бы остаться без сделки, если второй запрос упадёт.
        """
        payload = [build_lead(phone, cabinet_code, contact_name=contact_name,
                              needs_manual_review=needs_manual_review)]
        body = self._post('/api/v4/leads/complex', payload)

        if not isinstance(body, list) or not body:
            raise AmoWriteError('amoCRM ответила на создание сделки неожиданным телом: %s'
                                % (str(body)[:300],))
        first = body[0] or {}
        lead_id = first.get('id')
        if not lead_id:
            raise AmoWriteError('amoCRM не вернула id созданной сделки: %s'
                                % (str(first)[:300],))

        remark = note or (MANUAL_REVIEW_NOTE if needs_manual_review else None)
        if remark:
            self.add_note(lead_id, remark)
        return lead_id, first.get('contact_id')

    def add_note(self, lead_id, text):
        """Примечание к сделке. Необязательное действие — сделка уже создана.

        Падение примечания НЕ роняет обработку: обращение доехало, сделка есть,
        а потерять её из-за комментария было бы прямым нарушением требования
        «не терять обращение». Неудача уходит в лог и остаётся видимой там.
        """
        try:
            self._post('/api/v4/leads/notes', [{
                'entity_id': int(lead_id),
                'note_type': 'common',
                'params': {'text': text},
            }])
            return True
        except AmoWriteError as exc:
            log.warning('OLX→amo: примечание к сделке %s не записалось: %s', lead_id, exc)
            return False

    def find_contacts_by_phone(self, phone):
        """Контакты с таким номером — вторая линия защиты от дублей.

        Основная — собственный журнал робота: `query` в документации amoCRM уже
        объявлен кандидатом на удаление, а фильтр по значению поля «Телефон»
        отвечает 400 «Invalid filter for current account». Полагаться на эту
        проверку как на единственную нельзя, и здесь она именно вспомогательная.
        """
        try:
            body = self._client.get('/api/v4/contacts',
                                    params={'query': phone, 'with': 'leads'})
        except Exception as exc:            # noqa: BLE001 — вспомогательная проверка
            log.warning('OLX→amo: поиск дублей по номеру не удался: %s', exc)
            return []
        if not body:
            return []
        return ((body.get('_embedded') or {}).get('contacts') or [])
