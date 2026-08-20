"""SQL правки статей: создание, обновление, версии, правила уровня статьи."""

import re

from .sanitize import sanitize_html, to_plain_text
from .search import refresh_aliases

# Ссылки на файлы внутри тела статьи: /api/wiki/file/<uuid>
_FILE_REF = re.compile(
    r'/api/wiki/file/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',
    re.I)


def link_content_files(cursor, article_id, content):
    """Привязывает к статье все файлы, на которые она ссылается.

    Делается ПРИ СОХРАНЕНИИ, а не при загрузке файла, и это принципиально:
    редактор загружает картинку до того, как статья существует (у новой статьи
    ещё нет id), а импорт документа — тем более. Непривязанный файл по правилам
    раздела виден только загрузившему, поэтому без этого шага картинки в статье
    открывались бы у одного человека.

    Идемпотентно: повторное сохранение просто подтверждает привязку.
    """
    ids = set(_FILE_REF.findall(str(content or '')))
    if not ids:
        return 0
    cursor.execute(
        """
        UPDATE wiki_files SET article_id = %s
         WHERE id = ANY(%s::uuid[]) AND (article_id IS NULL OR article_id = %s)
        """,
        (article_id, list(ids), article_id),
    )
    return cursor.rowcount


_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


def set_attachments(cursor, article_id, file_ids, *, uploaded_by):
    """Приложения статьи в заданном порядке. Возвращает (привязано, откреплено).

    Привязать можно ТОЛЬКО свой ещё ничей файл либо файл, уже принадлежащий этой
    статье. Проверка обязательна: идентификатор приложения — обычный UUID из
    ответа загрузки, и без неё чужой файл достаточно было бы «приложить» к своей
    статье, чтобы прочитать его в обход прав на чужую.

    Пропавшие из списка ОТКРЕПЛЯЮТСЯ, а не удаляются: файл возвращается в то же
    состояние, в каком был сразу после загрузки, — виден одному загрузившему
    (эта ветка описана в routes_articles.wiki_file). Значит опечатка в списке
    стоит одной повторной привязки, а не потерянного документа. Сносить блоб в
    GCS отсюда всё равно нечем: хранилище живёт в слое роутов.

    Порядок хранится номером, а не временем загрузки: приложения к регламенту
    нумерованные, и перезалитое «Приложение 2» не должно вставать первым.
    """
    ids, seen = [], set()
    for value in (file_ids or []):
        text = str(value or '').strip()
        if _UUID_RE.match(text) and text.lower() not in seen:
            seen.add(text.lower())
            ids.append(text)

    # Сначала снимаем лишние — иначе статья на мгновение осталась бы с двумя
    # наборами приложений, а порядковые номера столкнулись бы между собой.
    cursor.execute(
        """
        UPDATE wiki_files SET article_id = NULL, is_attachment = FALSE
         WHERE article_id = %s AND is_attachment AND NOT (id = ANY(%s::uuid[]))
        """,
        (article_id, ids),
    )
    detached = cursor.rowcount

    attached = 0
    for order, file_id in enumerate(ids):
        cursor.execute(
            """
            UPDATE wiki_files
               SET article_id = %s, is_attachment = TRUE, sort_order = %s
             WHERE id = %s::uuid
               AND (article_id = %s OR (article_id IS NULL AND uploaded_by = %s))
            """,
            (article_id, order, file_id, article_id, uploaded_by),
        )
        attached += cursor.rowcount
    return attached, detached


def _next_version(cursor, article_id):
    cursor.execute(
        'SELECT COALESCE(max(version_number), 0) + 1 FROM wiki_article_versions WHERE article_id = %s',
        (article_id,),
    )
    return cursor.fetchone()[0]


def create_article(cursor, *, slug, title, summary, content, article_type,
                   section_ids, tags, author_id, visibility_mode='inherit',
                   strict_mode=False, ai_opt_out=False):
    clean = sanitize_html(content)
    cursor.execute(
        """
        INSERT INTO wiki_articles (slug, title, summary, content, content_plain,
                                   article_type, status, visibility_mode, strict_mode,
                                   ai_opt_out, author_id, updated_by, owner_user_id)
        VALUES (%s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (slug, title, summary, clean, to_plain_text(clean), article_type,
         visibility_mode, strict_mode, ai_opt_out, author_id, author_id, author_id),
    )
    article_id = cursor.fetchone()[0]
    set_sections(cursor, article_id, section_ids)
    # Варианты написания считаем ОДИН раз при сохранении (внутри set_tags).
    # В оригинале они вычислялись на каждый поисковый запрос и превращались
    # в четыре обращения к движку.
    set_tags(cursor, article_id, tags)
    link_content_files(cursor, article_id, clean)
    snapshot_version(cursor, article_id, editor_id=author_id, session_id=None,
                     comment='Создание статьи')
    return article_id


_UPDATABLE = ('title', 'summary', 'article_type', 'status',
              'visibility_mode', 'strict_mode', 'ai_opt_out', 'owner_user_id',
              'review_due_at', 'cross_department')


def update_article(cursor, article_id, fields, *, editor_id, session_id, comment):
    """Обновление с обязательным снимком предыдущей версии.

    Снимок делается ДО записи новых значений: иначе «предыдущая версия» окажется
    равной новой, и восстановление станет бессмысленным.
    """
    snapshot_version(cursor, article_id, editor_id=editor_id,
                     session_id=session_id, comment=comment)

    sets, values = [], []
    for key in _UPDATABLE:
        if key in fields:
            sets.append(key + ' = %s')
            values.append(fields[key])

    if 'content' in fields:
        clean = sanitize_html(fields['content'])
        sets.append('content = %s')
        values.append(clean)
        sets.append('content_plain = %s')
        values.append(to_plain_text(clean))

    if fields.get('status') == 'published':
        sets.append("published_at = COALESCE(published_at, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))")

    if not sets:
        return False

    sets.append('updated_by = %s')
    values.append(editor_id)
    sets.append("updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')")
    values.append(article_id)
    cursor.execute('UPDATE wiki_articles SET ' + ', '.join(sets) + ' WHERE id = %s', values)
    changed = cursor.rowcount > 0
    if changed:
        refresh_aliases(cursor, article_id)
        if 'content' in fields:
            link_content_files(cursor, article_id, fields['content'])
    return changed


def normalize_session_id(value):
    """Приводит идентификатор сессии к тому, что примет колонка UUID.

    Ловушка: _current_session_id_from_access_token() (bot_schedule2.py:1472)
    заканчивается на `return str(payload.get("sid"))` — то есть при отсутствии
    sid в токене возвращает СТРОКУ 'None', а не None. Такая строка в колонку
    UUID не ляжет: «invalid input syntax for type uuid». Нормализуем в одном
    месте, чтобы каждый вызывающий не помнил об этом.
    """
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in ('', 'none', 'null', 'undefined'):
        return None
    return text


def snapshot_version(cursor, article_id, *, editor_id, session_id, comment):
    """Снимок текущего состояния статьи в историю."""
    session_id = normalize_session_id(session_id)

    cursor.execute(
        """
        INSERT INTO wiki_article_versions (article_id, version_number, title, summary,
                                           content, status, change_comment,
                                           editor_id, session_id)
        SELECT a.id, %(version)s, a.title, a.summary, a.content, a.status,
               %(comment)s, %(editor)s, %(session)s::uuid
          FROM wiki_articles a WHERE a.id = %(id)s
        """,
        {'id': article_id, 'version': _next_version(cursor, article_id),
         'comment': comment, 'editor': editor_id, 'session': session_id},
    )


_VERSION_KEYS = ('id', 'version_number', 'title', 'summary', 'status',
                 'change_comment', 'editor_id', 'editor_name', 'created_at')


def list_versions(cursor, article_id):
    cursor.execute(
        """
        SELECT v.id, v.version_number, v.title, v.summary, v.status,
               v.change_comment, v.editor_id, u.name, v.created_at
          FROM wiki_article_versions v
          LEFT JOIN users u ON u.id = v.editor_id
         WHERE v.article_id = %s
         ORDER BY v.version_number DESC
        """,
        (article_id,),
    )
    return [dict(zip(_VERSION_KEYS, row)) for row in cursor.fetchall()]


def get_version(cursor, article_id, version_id):
    cursor.execute(
        """
        SELECT id, version_number, title, summary, content, status, change_comment,
               editor_id, created_at
          FROM wiki_article_versions
         WHERE id = %s AND article_id = %s
        """,
        (version_id, article_id),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(zip(('id', 'version_number', 'title', 'summary', 'content',
                     'status', 'change_comment', 'editor_id', 'created_at'), row))


def restore_version(cursor, article_id, version_id, *, editor_id, session_id):
    """Восстановление — это новая версия, а не откат истории.

    История не переписывается: снимок текущего состояния делается перед
    восстановлением, поэтому «откатить откат» тоже можно.
    """
    version = get_version(cursor, article_id, version_id)
    if not version:
        return False

    snapshot_version(cursor, article_id, editor_id=editor_id, session_id=session_id,
                     comment='Перед восстановлением версии №%s' % version['version_number'])

    cursor.execute(
        """
        UPDATE wiki_articles
           SET title = %(title)s, summary = %(summary)s, content = %(content)s,
               content_plain = %(plain)s, updated_by = %(editor)s,
               updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
         WHERE id = %(id)s
        """,
        {'id': article_id, 'title': version['title'], 'summary': version['summary'],
         'content': version['content'], 'plain': to_plain_text(version['content']),
         'editor': editor_id},
    )
    cursor.execute(
        """
        UPDATE wiki_article_versions SET restored_from_version_id = %s
         WHERE article_id = %s
           AND version_number = (SELECT max(version_number) FROM wiki_article_versions
                                  WHERE article_id = %s)
        """,
        (version_id, article_id, article_id),
    )
    # Заголовок и текст только что заменились — алиасы обязаны следовать за
    # ними, иначе после отката транслит/синонимы ищут по прошлой редакции.
    refresh_aliases(cursor, article_id)
    return True


# Общий отдел — дом для статьи, которой не выбрали раздел. Ищем по слагу
# раздела и по названию отдела: слаг задаётся переносом и правкам названия не
# подвержен, название — запасной путь для базы, собранной руками.
_FALLBACK_SECTION_SLUG = 'obschiy-sotrudnik'
_FALLBACK_SPACE_NAME = 'Общий отдел'


def default_section_id(cursor):
    """Раздел «Общий отдел → Общий сотрудник» — куда падает статья без раздела.

    Статья вообще без раздела — ловушка, а не свобода: в режиме «наследовать»
    ей не от чего наследовать права, и её не видит НИКТО, кроме автора. Такую
    статью нельзя было ни найти в оглавлении, ни отыскать поиском, ни положить
    в раздел — на проде так залипли три штуки. Раньше от этого спасал
    переключатель «Всё содержимое» у администратора доступов; теперь спасать не
    от чего — раздел проставляется сам.

    Возвращает None, если общего отдела в базе нет. Сами его не создаём:
    молча заведённый публичный раздел раздал бы права шире, чем кто-либо
    просил, а это то самое, что отзывается труднее всего.
    """
    cursor.execute(
        """
        SELECT s.id
          FROM wiki_sections s
          JOIN wiki_spaces sp ON sp.id = s.space_id
         WHERE s.status = 'active' AND sp.status = 'active'
           AND (s.slug = %(slug)s OR sp.name = %(space)s)
         ORDER BY (s.slug = %(slug)s) DESC, (sp.name = %(space)s) DESC,
                  s.position, s.id
         LIMIT 1
        """,
        {'slug': _FALLBACK_SECTION_SLUG, 'space': _FALLBACK_SPACE_NAME},
    )
    row = cursor.fetchone()
    return row[0] if row else None


def set_sections(cursor, article_id, section_ids):
    wanted = {int(s) for s in (section_ids or []) if s}
    if not wanted:
        fallback = default_section_id(cursor)
        if fallback:
            wanted = {fallback}

    cursor.execute('DELETE FROM wiki_article_sections WHERE article_id = %s', (article_id,))
    for section_id in wanted:
        cursor.execute(
            'INSERT INTO wiki_article_sections (article_id, section_id) VALUES (%s, %s) '
            'ON CONFLICT DO NOTHING',
            (article_id, section_id),
        )


def attach_section(cursor, article_id, section_id):
    """Подключить статью к ещё одному разделу, ничего не отвязывая.

    Отдельно от set_sections: та ЗАМЕНЯЕТ набор разделов (сначала DELETE), и
    заимствование чужой статьи через неё оторвало бы её от раздела-источника —
    у соседнего отдела статья молча пропала бы.
    """
    cursor.execute(
        'INSERT INTO wiki_article_sections (article_id, section_id) VALUES (%s, %s) '
        'ON CONFLICT DO NOTHING',
        (article_id, section_id),
    )
    return cursor.rowcount > 0


def fork_article(cursor, source_id, *, section_id, author_id, slug, title):
    """Своя копия чужой статьи: дальше расходится независимо от источника.

    Копия создаётся ЧЕРНОВИКОМ: заимствованный регламент почти всегда правят
    под свой отдел, и публиковать его от чужого имени, не читая, нельзя.
    """
    cursor.execute(
        """
        SELECT summary, content, content_plain, article_type, ai_opt_out
          FROM wiki_articles WHERE id = %s
        """,
        (source_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    summary, content, content_plain, article_type, ai_opt_out = row

    cursor.execute(
        """
        INSERT INTO wiki_articles (slug, title, summary, content, content_plain,
                                   article_type, status, visibility_mode, strict_mode,
                                   ai_opt_out, author_id, updated_by, owner_user_id,
                                   source_article_id)
        VALUES (%s, %s, %s, %s, %s, %s, 'draft', 'inherit', FALSE, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (slug, title, summary, content, content_plain, article_type,
         ai_opt_out, author_id, author_id, author_id, source_id),
    )
    article_id = cursor.fetchone()[0]
    set_sections(cursor, article_id, [section_id])
    # Файлы перепривязываются к копии: без этого картинки остались бы видны
    # только тому, кто их когда-то загрузил в исходную статью.
    link_content_files(cursor, article_id, content)
    snapshot_version(cursor, article_id, editor_id=author_id, session_id=None,
                     comment='Копия статьи №%s' % source_id)
    return article_id


def set_tags(cursor, article_id, tags):
    cursor.execute('DELETE FROM wiki_article_tags WHERE article_id = %s', (article_id,))
    for tag in {str(t).strip()[:64] for t in (tags or []) if str(t).strip()}:
        cursor.execute(
            'INSERT INTO wiki_article_tags (article_id, tag_name) VALUES (%s, %s) '
            'ON CONFLICT DO NOTHING',
            (article_id, tag),
        )
    # Теги входят в search_aliases. Без пересчёта здесь PATCH, меняющий только
    # теги, не доходил до refresh_aliases в update_article (нет полей — ранний
    # выход), и поиск по свежему тегу не работал до следующей правки текста.
    refresh_aliases(cursor, article_id)


def delete_article(cursor, article_id):
    """Мягкое удаление: статья уходит в архив.

    Физическое удаление снесло бы каскадом версии, просмотры, назначения на
    ознакомление и избранное — восстановить это неоткуда.
    """
    cursor.execute(
        """
        UPDATE wiki_articles
           SET status = 'archived',
               updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
         WHERE id = %s
        """,
        (article_id,),
    )
    return cursor.rowcount > 0


def slug_is_free(cursor, slug, exclude_id=None):
    cursor.execute(
        'SELECT 1 FROM wiki_articles WHERE slug = %s AND (%s::int IS NULL OR id <> %s::int)',
        (slug, exclude_id, exclude_id),
    )
    return cursor.fetchone() is None


# ─────────────────────────────────────────────────────────────────────────────
# Правила уровня статьи
# ─────────────────────────────────────────────────────────────────────────────

_ARTICLE_RULE_KEYS = ('id', 'article_id', 'subject_type', 'subject_id', 'subject_role',
                      'mode', 'can_read', 'can_create', 'can_edit', 'can_delete',
                      'can_publish', 'can_approve', 'subject_label')


def list_article_rules(cursor, article_id):
    cursor.execute(
        """
        SELECT r.id, r.article_id, r.subject_type, r.subject_id, r.subject_role,
               r.mode, r.can_read, r.can_create, r.can_edit, r.can_delete,
               r.can_publish, r.can_approve,
               CASE r.subject_type
                   WHEN 'department' THEN (SELECT name FROM departments WHERE id = r.subject_id)
                   WHEN 'direction'  THEN (SELECT name FROM directions  WHERE id = r.subject_id)
                   WHEN 'group'      THEN (SELECT name FROM groups      WHERE id = r.subject_id)
                   WHEN 'wiki_role'  THEN (SELECT name FROM wiki_roles  WHERE id = r.subject_id)
                   WHEN 'user'       THEN (SELECT name FROM users       WHERE id = r.subject_id)
                   ELSE r.subject_role
               END
          FROM wiki_article_access_rules r
         WHERE r.article_id = %s
         ORDER BY r.mode DESC, r.subject_type, r.id
        """,
        (article_id,),
    )
    return [dict(zip(_ARTICLE_RULE_KEYS, row)) for row in cursor.fetchall()]


def upsert_article_rule(cursor, *, article_id, subject_type, subject_id, subject_role,
                        mode, permissions, created_by):
    cursor.execute(
        """
        INSERT INTO wiki_article_access_rules
            (article_id, subject_type, subject_id, subject_role, mode,
             can_read, can_create, can_edit, can_delete, can_publish, can_approve, created_by)
        VALUES (%(article)s, %(stype)s, %(sid)s, %(srole)s, %(mode)s,
                %(read)s, %(create)s, %(edit)s, %(delete)s, %(publish)s, %(approve)s, %(by)s)
        ON CONFLICT (article_id, subject_type,
                     COALESCE(subject_id, -1), COALESCE(subject_role, ''))
        DO UPDATE SET mode        = EXCLUDED.mode,
                      can_read    = EXCLUDED.can_read,
                      can_create  = EXCLUDED.can_create,
                      can_edit    = EXCLUDED.can_edit,
                      can_delete  = EXCLUDED.can_delete,
                      can_publish = EXCLUDED.can_publish,
                      can_approve = EXCLUDED.can_approve,
                      updated_at  = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
        RETURNING id
        """,
        {'article': article_id, 'stype': subject_type, 'sid': subject_id,
         'srole': subject_role, 'mode': mode,
         'read': permissions.get('can_read', True),
         'create': permissions.get('can_create', False),
         'edit': permissions.get('can_edit', False),
         'delete': permissions.get('can_delete', False),
         'publish': permissions.get('can_publish', False),
         'approve': permissions.get('can_approve', False),
         'by': created_by},
    )
    return cursor.fetchone()[0]


def delete_article_rule(cursor, rule_id):
    cursor.execute(
        'DELETE FROM wiki_article_access_rules WHERE id = %s RETURNING article_id',
        (rule_id,),
    )
    row = cursor.fetchone()
    return row[0] if row else None
