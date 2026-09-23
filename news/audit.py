# -*- coding: utf-8 -*-
"""Действия с новостями — в общий журнал вики.

Просьба владельца (23.09.2026): «в журнал тоже отправлять изменения, чтобы при
удалении можно было увидеть, кто это сделал; сделать корректное разделение и не
перемешивать Таксопарки и Тез». До этого у новости был только журнал ПРОЧТЕНИЙ, и
он удаляется вместе с ней — то есть после удаления не оставалось ни самой
новости, ни следа того, кто её убрал.

ПОЧЕМУ ЖУРНАЛ ВИКИ, А НЕ СВОЙ. Вкладка «Новости» живёт внутри вики, и «Журнал»
рядом с ней — тот, куда уже смотрят за правками статей, разделов и доступов.
Второй журнал на соседней вкладке заставлял бы искать одно событие в двух
местах.

ПРОСТРАНСТВО — ВСЕГДА ЯВНО. Журнал вики держит строгую границу
(wiki/structure.py: _audit_filters): запись без пространства не видна ни в
«Таксопарках», ни в «Тез». Формула пространства вики считает его по объекту, но
таблицу новостей она НЕ знает намеренно — пакеты разворачиваются раздельно, и
ссылка на news_posts из общей формулы уронила бы запись в журнал ЛЮБОГО
действия вики, сорвись у новостей миграция. Поэтому пространство кладётся в
details (вторая ступень формулы) здесь, одной функцией на все двери.

ЖУРНАЛ НЕ ВПРАВЕ СОРВАТЬ ДЕЙСТВИЕ. Запись идёт под своей точкой сохранения:
не развернулась таблица журнала, не хватает колонки — откатывается только
запись, а новость сохраняется, выпускается или снимается как обычно. Пакет
news/ вынесен из вики ровно затем, чтобы поломка вики не трогала новости.
"""

import logging

# Действия с новостями. Подписи — src/components/wiki/auditEvents.js
# (ACTION_META), группа — wiki/structure.py (AUDIT_GROUPS['news']); совпадение
# трёх списков сверяет tests/test_news.py, как для действий самой вики.
AUDIT_ACTIONS = (
    'news.create',      # сохранена новая новость (черновиком или сразу с выпуском)
    'news.update',      # правка — с перечнем изменённых полей
    'news.publish',     # вышла к людям: кнопкой или по расписанию
    'news.schedule',    # взведён отложенный запуск
    'news.unschedule',  # запланированный запуск отменён
    'news.archive',     # снята с показа
    'news.delete',      # удалена насовсем — вместе с журналом прочтений
)

# Поля, которые сравниваются при правке. Ключи — те, что в карточке
# (queries.get_post), подписи — во фронте (auditEvents.js: FIELD_TITLE).
# Тест, фотографии и расписание сравниваются отдельно: у карточки их нет
# колонками, и сравнивает их роут.
_COMPARED = ('title', 'body', 'kind', 'pass_score_percent', 'confirm_delay_seconds',
             'expires_at', 'channel', 'trainer_key', 'pass_required',
             'read_limit_seconds', 'quiz_limit_seconds')


def _rules(post):
    """Адресаты новости как сравнимое множество."""
    return sorted((rule.get('subject_type'), rule.get('subject_id'), rule.get('subject_role'))
                  for rule in (post or {}).get('audience') or ())


def _schedule(post):
    post = post or {}
    return (post.get('publish_mode'), post.get('scheduled_at'),
            post.get('spread_minutes'), post.get('wave_interval_minutes'))


def changed_fields(before, after):
    """Что изменила правка: список ключей в порядке формы. Чистая функция.

    Сравниваем КАРТОЧКИ до и после, а не присланное формой: форма присылает все
    поля разом, и «поля: заголовок, текст, адресаты…» на каждое сохранение
    читалось бы как «поменяли всё», хотя поправили одну опечатку.
    """
    fields = [key for key in _COMPARED
              if (before or {}).get(key) != (after or {}).get(key)]
    if _rules(before) != _rules(after):
        fields.append('audience')
    if _schedule(before) != _schedule(after):
        fields.append('schedule')
    return fields


def record(cursor, *, actor_id, action, post, fallback_space_id=None, details=None):
    """Записать действие с новостью в журнал вики. Ничего не возвращает.

    actor_id=None — действие системы (выпуск по расписанию): журнал покажет его
    без автора, а не припишет тому, кто нажал «Опубликовать» накануне.

    fallback_space_id — пространство вкладки, из которой действуют. Нужно
    только «ничьей» новости (space_id IS NULL): иначе запись о ней не попала бы
    ни в один журнал. У новости с пространством оно главнее вкладки — запись
    принадлежит объявлению, а не тому, куда человек сейчас смотрит.
    """
    if action not in AUDIT_ACTIONS:
        raise ValueError('Неизвестное действие журнала новостей: %s' % action)
    space_id = (post or {}).get('space_id') or fallback_space_id
    payload = {'title': (post or {}).get('title') or ''}
    if space_id:
        payload['space_id'] = int(space_id)
    payload.update(details or {})

    cursor.execute('SAVEPOINT news_audit')
    try:
        # Импорт — здесь, а не в шапке: журнал вики нужен только пишущим
        # дверям, а окно «Новость дня» грузит пакет news на каждом входе.
        from wiki import queries as wiki_queries
        wiki_queries.log_action(
            cursor, actor_id=actor_id, action=action, entity_type='news',
            entity_id=(post or {}).get('id'), details=payload, space_id=space_id)
        cursor.execute('RELEASE SAVEPOINT news_audit')
    except Exception:  # noqa: BLE001 — журнал не вправе сорвать само действие
        cursor.execute('ROLLBACK TO SAVEPOINT news_audit')
        logging.warning('news: запись «%s» в журнал вики не удалась', action,
                        exc_info=True)
