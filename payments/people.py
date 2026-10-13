"""Фото людей на карточке доски: чьё лицо показать. Чистые функции.

На карточке два лица, как на карточках «Задач»: инициатор и человек этапа — тот, у
кого этап сейчас, либо тот, кто его закрыл. Фото берётся из профиля сотрудника
(`users.avatar_bucket`, `users.avatar_blob_path`); ссылку на него подписывает монолит —
подписчик приходит в Blueprint аргументом (routes.py), здесь его нет. Ссылки уходят
одним списком на ответ (`photos`: номер человека → ссылка), карточка называет номера.

Фото — те же сведения о человеке, что и фамилия, поэтому уходит только туда, где
видна фамилия (privacy.py, п. 9 ТЗ: «инициатор не должен знать, кто именно внутри
бухгалтерии или финансового отдела выполняет платёж»). Задаче подразделения
сотрудника не назначают, а тому, кому имена подразделений не положены, лицо этапа
подразделения не отдаётся вовсе — на карточке останется значок подразделения.
"""

from . import privacy

_NOBODY = {'assignee': None, 'done_by': None}


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def stage_people(focus, *, hide_people=False):
    """Люди этапа, чьи лица можно показать: {'assignee': id | None, 'done_by': id | None}."""
    if not focus:
        return dict(_NOBODY)
    if hide_people and focus.get('kind') in privacy.DEPARTMENT_KINDS:
        return dict(_NOBODY)
    return {'assignee': _int(focus.get('assignee_id')), 'done_by': _int(focus.get('done_by'))}


def face_ids(request_row, focus, *, hide_people=False):
    """Чьи фото нужны карточке — чтобы прочитать их одной выборкой на всю доску."""
    ids = {_int((request_row or {}).get('initiator_id'))}
    ids.update(stage_people(focus, hide_people=hide_people).values())
    ids.discard(None)
    return ids


def card_photos(request_row, focus, photos, *, hide_people=False):
    """Чьи фото стоят на карточке: {'initiator': id | None, 'assignee': …, 'done_by': …}.

    Номер человека — ключ к ссылке в общем списке ответа (`photos`): одно лицо бывает на
    десятках карточек, и подписанную ссылку длиной под килобайт незачем повторять в каждой.
    Номер отдаётся, только когда фото есть (`photos` — у кого оно нашлось): без фото карточка
    рисует инициалы, и номер человека ей не нужен.
    """
    stage = stage_people(focus, hide_people=hide_people)
    known = photos or {}

    def key(user_id):
        return user_id if user_id is not None and user_id in known else None

    return {
        'initiator': key(_int((request_row or {}).get('initiator_id'))),
        'assignee': key(stage['assignee']),
        'done_by': key(stage['done_by']),
    }
