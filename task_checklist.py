# -*- coding: utf-8 -*-
"""Чек-лист задачи при правке: какие пункты оставить, какие добавить, какие убрать.

ЗАЧЕМ (HR-2, 24.11.2026). Правка задачи удаляла все пункты чек-листа и вставляла
их заново. Поэтому любая правка (добавили исполнителя, поправили описание) снимала
все отметки, а у пунктов менялись id. Галочка из вкладки, открытой до правки,
уходила на уже удалённый пункт и падала «пункт не найден», а после обновления
страницы отмеченного не было. Чек-лист — общее рабочее поле задачи: пункты
отмечают исполнители по ходу работы, и правка постановщика не должна это стирать.

ПРАВИЛО. Пункт из правки узнаётся среди нынешних пунктов задачи:
  1. по id — форма шлёт id пунктов, пришедших с сервера. Переименованный пункт
     остаётся тем же пунктом со своей отметкой;
  2. иначе по названию без учёта регистра. Так старая вкладка (форма без id) и
     скрипты, которые шлют чек-лист строками, отметок не сбрасывают.
Узнанный пункт сохраняет отметку, кто и когда её поставил, и итог; меняются только
название, обязательность и место. Неузнанный — новый пункт, без отметки. Нынешние
пункты, которых в правке нет, убираются: пустой список снимает весь чек-лист.
"""


def title_key(title):
    """Название пункта для сравнения: без пробелов по краям и без учёта регистра."""
    return str(title or '').strip().casefold()


def _positive_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def plan_sync(current_items, incoming_items):
    """Как привести чек-лист задачи к правке, не теряя отметок.

    current_items — пункты задачи из базы: [{id, title, position, is_required}];
    incoming_items — чек-лист из правки в порядке показа: [{title, is_required, id?}]
    (уже без пустых названий и повторов). Возвращает словарь:
      keep    — узнанные пункты: [{id, title, position, is_required, content_changed, moved}],
                где position — новое место, content_changed — поменялись название
                или обязательность, moved — номер места в базе другой;
      insert  — новые пункты: [{title, position, is_required}];
      delete  — id пунктов, которых в правке нет;
      changed — правка меняет чек-лист хоть в чём-то.
    """
    current = sorted(
        (dict(item) for item in (current_items or [])),
        key=lambda item: (int(item.get('position') or 0), int(item['id'])),
    )
    incoming = list(incoming_items or [])
    by_id = {int(item['id']): item for item in current}

    claimed = {}
    used = set()
    # Сначала по id: пункт, который форма знает в лицо, не должен достаться
    # другому пункту с тем же названием раньше, чем до него дойдёт очередь.
    for index, item in enumerate(incoming):
        item_id = _positive_int(item.get('id'))
        if item_id is not None and item_id in by_id and item_id not in used:
            claimed[index] = item_id
            used.add(item_id)
    # Потом по названию — среди ещё не узнанных.
    by_title = {}
    for item in current:
        item_id = int(item['id'])
        if item_id not in used:
            by_title.setdefault(title_key(item.get('title')), item_id)
    for index, item in enumerate(incoming):
        if index in claimed:
            continue
        item_id = by_title.pop(title_key(item.get('title')), None)
        if item_id is not None:
            claimed[index] = item_id
            used.add(item_id)

    keep = []
    insert = []
    for position, item in enumerate(incoming):
        title = str(item.get('title') or '').strip()
        is_required = bool(item.get('is_required', True))
        if position in claimed:
            row = by_id[claimed[position]]
            keep.append({
                "id": claimed[position],
                "title": title,
                "position": position,
                "is_required": is_required,
                "content_changed": (
                    str(row.get('title') or '') != title
                    or bool(row.get('is_required')) != is_required
                ),
                "moved": int(row.get('position') or 0) != position,
            })
        else:
            insert.append({"title": title, "position": position, "is_required": is_required})
    delete = [int(item['id']) for item in current if int(item['id']) not in used]
    # Порядок сравниваем последовательностью, а не номерами мест: строка с
    # пропуском в номерах при том же порядке — не правка чек-листа.
    order_changed = [item['id'] for item in keep] != [int(item['id']) for item in current if int(item['id']) in used]
    changed = bool(insert or delete or order_changed or any(item['content_changed'] for item in keep))
    return {"keep": keep, "insert": insert, "delete": delete, "changed": changed}
