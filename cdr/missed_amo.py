# -*- coding: utf-8 -*-
"""Запись пропущенного входящего в amoCRM: сделка, тег, контакт, примечание (задача #291).

Транспорт — тот же, что у робота OLX (`olx_amo.amo_writer.AmoWriter`): POST через сессию
`amocrm.leads.AmoClient`, перелогин по 401, ошибки одним типом `AmoWriteError`. Второй
источник токена рано или поздно разъехался бы с первым, поэтому здесь наследник, а не
копия. Своё — только то, чем сделка на перезвон отличается от отклика OLX.

Что выяснено на живом аккаунте и почему код такой
--------------------------------------------------
* **Контакт привязываем существующий.** У клиента, который звонит на линию, контакт почти
  всегда уже есть: интеграция станции заводит его на первом же звонке. Робот OLX каждый
  раз создаёт новый — здесь так нельзя: оператор, открыв сделку, должен видеть историю
  клиента, а не пустую карточку-двойник. Комплексное добавление принимает
  `_embedded.contacts[0].id` (документация amoCRM, «Комплексное добавление сделок»).
* **Контакт ищем по `contacts?query=`**, а совпадение проверяем сами — по десяти цифрам
  номера в поле «Телефон»: поиск amoCRM подстрочный и отдаёт и чужие номера с тем же
  хвостом. Поиск упал — заводим новый контакт: лишний контакт лучше потерянной сделки.
* **Тег — по id, если он уже есть.** amoCRM при передаче тега по имени создаёт новый,
  когда точного совпадения нет; в аккаунте уже живут двойники вида `forma_ olx_цр`.
  Поэтому имя ищется в справочнике тегов, и только если его там нет, уходит именем —
  так тег появляется один раз, первой сделкой.
* **`metadata` не прикладываем**: в аккаунте включено «Неразобранное», и сделка с ним
  уехала бы туда вместо «Новой заявки» (то же у робота OLX).
* **Чужие сделки не трогаем.** Решение владельца 28.09.2026: сделки интеграции станции
  («<номер> - Входящий») и менеджеров остаются как есть. Примечание дописывается только
  к НАШЕЙ сделке, если клиент снова не дозвонился, пока её не взяли в работу.
"""

import logging
import re

from olx_amo.amo_writer import (AmoWriteError, AmoWriter, CONTACT_PHONE_FIELD_ID,
                                CONTACT_PHONE_WORK_ENUM_ID)

from cdr import missed, missed_config, touches as touches_mod

log = logging.getLogger(__name__)

__all__ = ['AmoWriteError', 'LeadReadError', 'MissedCallWriter', 'build_lead']

# Клиент amoCRM сообщает код ответа только текстом: «amoCRM GET <путь> -> 404: …».
# 404 и 403 — сделки для нас нет насовсем; всё остальное — временно.
_GONE_RE = re.compile(r'-> (?:403|404)\b')


class LeadReadError(Exception):
    """amoCRM сейчас не ответила про сделку. Решать по ней нельзя — звонок подождёт."""


def build_lead(phone, tag, contact_id=None):
    """Тело одной сделки для POST /api/v4/leads/complex.

    tag — {'id': …} или {'name': …} (см. MissedCallWriter.tag_ref); contact_id — id
    найденного контакта клиента или None, тогда контакт заводится вместе со сделкой."""
    number = missed.amo_phone(phone)
    if not number:
        raise AmoWriteError('у пропущенного звонка нет номера клиента')
    if contact_id:
        contact = {'id': int(contact_id)}
    else:
        contact = {
            'first_name': number,
            'custom_fields_values': [{
                'field_id': CONTACT_PHONE_FIELD_ID,
                'values': [{'value': number, 'enum_id': CONTACT_PHONE_WORK_ENUM_ID}],
            }],
        }
    return {
        'name': missed.lead_name(phone),
        'pipeline_id': missed_config.PIPELINE_ID,
        'status_id': missed_config.STATUS_ID,
        'responsible_user_id': missed_config.RESPONSIBLE_USER_ID,
        '_embedded': {
            'tags': [dict(tag)],
            'contacts': [contact],
        },
    }


def _contact_phones(contact):
    """Все телефоны контакта десятью цифрами."""
    out = set()
    for field in contact.get('custom_fields_values') or []:
        if field.get('field_code') != 'PHONE' and field.get('field_id') != CONTACT_PHONE_FIELD_ID:
            continue
        for value in field.get('values') or []:
            digits = touches_mod.norm_phone(value.get('value'))
            if digits:
                out.add(digits)
    return out


class MissedCallWriter(AmoWriter):
    """Сделки на перезвон по пропущенным входящим поверх клиента amoCRM."""

    def __init__(self, client):
        super(MissedCallWriter, self).__init__(client)
        self._tag = None

    # -- чтение ------------------------------------------------------------

    def tag_ref(self):
        """Ссылка на тег для сделки: {'id': …}, если тег уже есть, иначе {'name': …}.

        Найденный id запоминается на жизнь писателя; ненайденный — нет: после первой
        сделки тег появится, и следующий цикл найдёт его уже по id."""
        if self._tag is not None:
            return self._tag
        name = missed_config.TAG_NAME
        try:
            body = self._client.get('/api/v4/leads/tags', params={'query': name, 'limit': 50})
        except Exception as exc:  # noqa: BLE001 — справочник необязателен
            log.warning('Пропущенные→amo: справочник тегов недоступен: %s', exc)
            return {'name': name}
        for tag in ((body or {}).get('_embedded') or {}).get('tags') or []:
            if str(tag.get('name') or '').strip() == name and tag.get('id'):
                self._tag = {'id': int(tag['id']), 'name': name}
                return self._tag
        return {'name': name}

    def find_contact_id(self, phone):
        """id контакта с этим номером или None. Из нескольких — последний изменённый:
        с ним и работают, остальные обычно старые двойники."""
        digits = touches_mod.norm_phone(phone)
        if not digits:
            return None
        try:
            body = self._client.get('/api/v4/contacts', params={'query': digits, 'limit': 50})
        except Exception as exc:  # noqa: BLE001 — поиск вспомогательный
            log.warning('Пропущенные→amo: поиск контакта по номеру не удался: %s', exc)
            return None
        matches = [contact for contact in ((body or {}).get('_embedded') or {}).get('contacts') or []
                   if digits in _contact_phones(contact) and contact.get('id')]
        if not matches:
            return None
        best = max(matches, key=lambda c: (int(c.get('updated_at') or 0), int(c['id'])))
        return int(best['id'])

    def lead_state(self, lead_id):
        """(воронка, этап) сделки; None — сделки нет (удалена, 404, нет доступа).

        Временный сбой — лимит запросов, который робот делит с роботом OLX и выгрузкой
        воронки, 5xx, обрыв — это НЕ «сделки нет»: он поднимает LeadReadError, и звонок
        ждёт следующего цикла. Иначе минутная икота amoCRM давала бы клиенту вторую
        сделку при живой первой в «Новой заявке»."""
        try:
            body = self._client.get('/api/v4/leads/%d' % int(lead_id))
        except Exception as exc:  # noqa: BLE001 — разбираем ниже
            if _GONE_RE.search(str(exc)):
                return None
            raise LeadReadError('сделка %s не прочиталась: %s' % (lead_id, exc))
        if not body or body.get('is_deleted'):
            return None
        return int(body.get('pipeline_id') or 0), int(body.get('status_id') or 0)

    def recent_own_lead(self, phone, since_epoch):
        """Наша сделка по этому номеру, заведённая не раньше since_epoch, или None.

        Нужна при повторе после сбоя: запрос мог дойти до amoCRM и завести сделку, а ответ
        — потеряться. Слепой повтор завёл бы вторую; поэтому сначала ищем первую — по
        контактам с этим номером и по нашему тегу у их сделок."""
        digits = touches_mod.norm_phone(phone)
        if not digits:
            return None
        body = self._client.get('/api/v4/contacts',
                                params={'query': digits, 'with': 'leads', 'limit': 50})
        lead_ids = []
        for contact in ((body or {}).get('_embedded') or {}).get('contacts') or []:
            if digits not in _contact_phones(contact):
                continue
            for lead in ((contact.get('_embedded') or {}).get('leads') or []):
                if lead.get('id'):
                    lead_ids.append(int(lead['id']))
        if not lead_ids:
            return None
        found = self._client.get('/api/v4/leads', params={
            'filter[id][]': sorted(set(lead_ids))[-50:], 'limit': 50})
        name = missed_config.TAG_NAME
        for lead in ((found or {}).get('_embedded') or {}).get('leads') or []:
            tags = {str(t.get('name') or '').strip()
                    for t in ((lead.get('_embedded') or {}).get('tags') or [])}
            if name in tags and int(lead.get('created_at') or 0) >= int(since_epoch):
                return int(lead['id'])
        return None

    # -- запись ------------------------------------------------------------

    def create_missed_lead(self, phone, contact_id=None):
        """Завести сделку на перезвон. Возвращает (lead_id, contact_id)."""
        payload = [build_lead(phone, self.tag_ref(), contact_id=contact_id)]
        body = self._post('/api/v4/leads/complex', payload)
        if not isinstance(body, list) or not body:
            raise AmoWriteError('amoCRM ответила на создание сделки неожиданным телом: %s'
                                % (str(body)[:300],))
        first = body[0] or {}
        lead_id = first.get('id')
        if not lead_id:
            raise AmoWriteError('amoCRM не вернула id созданной сделки: %s' % (str(first)[:300],))
        return int(lead_id), (int(first['contact_id']) if first.get('contact_id') else contact_id)

    def add_note(self, lead_id, text):
        """Примечание к НАШЕЙ сделке. Необязательное: сделка уже есть, и потерять её из-за
        комментария нельзя — неудача уходит в журнал, а не наверх."""
        try:
            self._post('/api/v4/leads/notes', [{
                'entity_id': int(lead_id),
                'note_type': 'common',
                'params': {'text': text},
            }])
            return True
        except AmoWriteError as exc:
            log.warning('Пропущенные→amo: примечание к сделке %s не записалось: %s', lead_id, exc)
            return False
