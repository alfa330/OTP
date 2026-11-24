# -*- coding: utf-8 -*-
"""Запись пропущенного входящего в CRM Syntony (SALES-4: переадресация из amoCRM).

То же, что сделка на перезвон в amoCRM (cdr/missed_amo.py), но средствами API заявок
Syntony — формой (syntony_leads/form.py). Решение «передавать или нет» общее
(cdr/missed.py), меняется только запись.

Чем Syntony отличается от amoCRM и почему здесь меньше кода
-----------------------------------------------------------
* **Воронку, ответственного и теги задаёт форма** в самой Syntony. Тега «Пропущенный
  входящий» запросом не поставить — его ставит форма, если так настроена. Запрос несёт
  телефон, комментарий, метки utm и свои поля карточки — опознавательные знаки перезвона
  (просьба владельца 24.11.2026): «Обращение: Пропущенный входящий», парк и номер линии,
  время звонка. Поля менеджер видит в карточке новой заявки; в выгрузку API они не попадают.
* **Повтор по номеру Syntony разбирает сама.** Пока у номера есть открытая заявка, новая
  не заводится — наш текст ложится в неё комментарием. Поэтому нет ни поиска «нашей
  сделки в „Новой заявке“», ни примечания к ней отдельным запросом, ни проверки «не
  дошёл ли запрос» перед повтором: повтор в худшем случае даст лишний комментарий.
  Замер 20–22.11.2026: на 84 из 118 пропущенных Syntony уже через ~17 с после начала
  звонка заводит свою заявку-звонок в парке линии — у таких клиентов наш текст станет
  комментарием в ней.
* **Номера заявки Syntony не отдаёт** (202 без тела). В журнале робота остаётся отметка
  «Syntony приняла» (`syntony_sent_at`), а не номер.

Имя клиента не передаём: его не знает и станция. Слова «Пропущенный входящий» в поле имени
Syntony показала бы как имя клиента — в карточке и в приветствии автоответа.
"""

from cdr import missed, missed_config, touches as touches_mod
from syntony_leads import form

__all__ = ['SyntonyFormError', 'SyntonyMissedWriter', 'build_fields', 'build_payload']

SyntonyFormError = form.SyntonyFormError

# Свои поля карточки заявки: название — то, что менеджер увидит в карточке. Первое поле —
# главный опознавательный знак: заявку «Пропущенный входящий» не спутать с заявкой формы сайта.
FIELD_KIND = 'Обращение'
KIND_MISSED = 'Пропущенный входящий'
FIELD_PARK = 'Парк линии'
FIELD_LINE = 'Линия'
FIELD_CALL = 'Звонок'


def form_phone(phone):
    """Десять цифр касания → +7XXXXXXXXXX. Пусто — пусто."""
    digits = touches_mod.norm_phone(phone)
    return '+7' + digits if digits else ''


def build_fields(touch, park):
    """Опознавательные поля заявки: что это за обращение, на линию какого парка звонил клиент
    и когда. Чего станция не назвала — того поля нет, прочерков не шлём."""
    fields = {FIELD_KIND: KIND_MISSED}
    if park:
        fields[FIELD_PARK] = park
    line = missed.pretty_phone(touch.get('line_number'))
    if line:
        fields[FIELD_LINE] = line
    started = missed._moment(touch.get('started_at'))
    if started is not None:
        fields[FIELD_CALL] = started.strftime('%d.%m.%Y %H:%M:%S')
    return fields


def build_payload(touch, park):
    """Что уходит в Syntony по пропущенному звонку: (телефон, комментарий, метки, поля)."""
    phone = form_phone(touch.get('phone'))
    if not phone:
        raise SyntonyFormError('у пропущенного звонка нет номера клиента', final=True)
    return (phone, missed.note_text(touch, park), dict(missed_config.SYNTONY_UTM),
            build_fields(touch, park))


class SyntonyMissedWriter(object):
    """Заявки на перезвон по пропущенным входящим через форму Syntony."""

    def __init__(self, key=None, session=None):
        self.key = key or form.form_key()
        if not self.key:
            raise SyntonyFormError('Syntony: не задан ключ формы %s' % form.ENV_FORM_KEY)
        self._session = session

    def send(self, touch, park=''):
        """Передать звонок. Не принят — SyntonyFormError (final — повтор не поможет)."""
        phone, comment, utm, fields = build_payload(touch, park)
        form.create_lead(self.key, phone, comment=comment, utm=utm, fields=fields,
                         session=self._session)
