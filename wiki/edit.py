"""SQL правки статей: создание, обновление, версии, правила уровня статьи."""

import re

from . import links as wiki_links
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


def link_content_articles(cursor, article_id, content, *, editor_id=None):
    """Пересобирает связи «эта статья ссылается на ту». Возвращает (добавлено, снято).

    Зачем таблица, если тело статьи и так есть. Прямые ссылки («Связанные
    материалы») из таблицы НЕ читаются — их собирает витрина прямо из тела,
    поэтому блок физически не может разойтись с текстом. Таблица нужна ровно
    одной стороне — ОБРАТНОЙ: чтобы ответить «кто ссылается на меня», иначе
    пришлось бы прочитать и разобрать тела всех статей портала на каждое
    открытие статьи.

    Периметра здесь нет НАМЕРЕННО. Связь — объективный факт текста, а не мнение
    того, кто нажал «Сохранить». Стой периметр на записи, один и тот же текст,
    сохранённый супервайзером и администратором вики, дал бы разный набор строк,
    а пересохранение более узким человеком МОЛЧА СНОСИЛО БЫ связи, записанные
    широким. Кому какую связь показывать, решает чтение (wiki_articles.backlinks
    фильтрует по visible_ids).

    Идемпотентно: повторное сохранение того же текста возвращает (0, 0).
    """
    slugs = wiki_links.article_slugs(content)

    targets = []
    if slugs:
        # Самоссылку отсекаем здесь (id <> %s), а не при разборе: слаг статьи в
        # собственном теле — обычное дело у оглавления, но «статья связана сама
        # с собой» не значит ничего ни в одном из двух блоков.
        cursor.execute(
            'SELECT id FROM wiki_articles WHERE slug = ANY(%s::text[]) AND id <> %s',
            (list(slugs), article_id),
        )
        targets = [row[0] for row in cursor.fetchall()]

    # Снимаем связи, которых в тексте больше нет. Ручные (is_manual) не трогаем:
    # колонка заведена под подборку, которую ведёт человек, и пересборка по
    # тексту не имеет права её стирать. Пустой список целей — законный случай
    # («убрали все ссылки»), и `<> ALL('{}')` честно истинно для всех строк.
    cursor.execute(
        'DELETE FROM wiki_article_links '
        ' WHERE source_id = %s AND NOT is_manual AND target_id <> ALL(%s::int[])',
        (article_id, targets),
    )
    removed = cursor.rowcount or 0

    added = 0
    if targets:
        # ON CONFLICT DO NOTHING, а НЕ DO UPDATE. Во-первых, DO UPDATE на паре,
        # уже помеченной ручной, молча снял бы этот признак. Во-вторых, он
        # падает с «cannot affect row a second time», если одна цель попадёт в
        # список дважды, — а в проде внутри одного тела 42 повторных ссылки.
        # Повторы снимает article_slugs, но полагаться на две защиты дешевле,
        # чем на одну: сохранение статьи не имеет права падать из-за связей.
        cursor.execute(
            """
            INSERT INTO wiki_article_links (source_id, target_id, created_by)
            SELECT %s, t, %s FROM unnest(%s::int[]) AS t
            ON CONFLICT (source_id, target_id) DO NOTHING
            """,
            (article_id, editor_id, targets),
        )
        added = cursor.rowcount or 0

    return added, removed


def _next_version(cursor, article_id):
    cursor.execute(
        'SELECT COALESCE(max(version_number), 0) + 1 FROM wiki_article_versions WHERE article_id = %s',
        (article_id,),
    )
    return cursor.fetchone()[0]


def create_article(cursor, *, slug, title, summary, content, article_type,
                   section_ids, tags, author_id, visibility_mode='inherit',
                   strict_mode=False, ai_opt_out=False, copy_protected=False,
                   historical=False, space_ids=None):
    clean = sanitize_html(content)
    cursor.execute(
        """
        INSERT INTO wiki_articles (slug, title, summary, content, content_plain,
                                   article_type, status, visibility_mode, strict_mode,
                                   ai_opt_out, copy_protected, historical,
                                   author_id, updated_by, owner_user_id)
        VALUES (%s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (slug, title, summary, clean, to_plain_text(clean), article_type,
         visibility_mode, strict_mode, ai_opt_out, copy_protected, historical,
         author_id, author_id, author_id),
    )
    article_id = cursor.fetchone()[0]
    set_sections(cursor, article_id, section_ids, space_ids)
    # Варианты написания считаем ОДИН раз при сохранении (внутри set_tags).
    # В оригинале они вычислялись на каждый поисковый запрос и превращались
    # в четыре обращения к движку.
    set_tags(cursor, article_id, tags)
    link_content_files(cursor, article_id, clean)
    link_content_articles(cursor, article_id, clean, editor_id=author_id)
    snapshot_version(cursor, article_id, editor_id=author_id, session_id=None,
                     comment='Создание статьи')
    return article_id


_UPDATABLE = ('title', 'summary', 'article_type', 'status',
              'visibility_mode', 'strict_mode', 'ai_opt_out', 'copy_protected',
              'historical', 'owner_user_id', 'review_due_at', 'cross_department')


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

    clean = None
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
    if changed and clean is not None:
        # Разбираем ОЧИЩЕННОЕ тело, а не присланное. В базу ложится clean, и
        # производные данные обязаны описывать именно его: санитайзер экранирует
        # амперсанд ('&' → '&amp;') и может выбросить ссылку целиком. Разбор
        # сырого текста дал бы связи на то, чего в сохранённой статье уже нет.
        link_content_files(cursor, article_id, clean)
        link_content_articles(cursor, article_id, clean, editor_id=editor_id)
    if changed:
        refresh_aliases(cursor, article_id)
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


# Шапки версий — без тела. Тело самой большой статьи прода весит 90 КБ, и
# десять таких строк ради списка редакций тянуть в память незачем: список
# сравнивает состояния, а для сравнения хватает отпечатка.
_VERSION_HEAD_KEYS = ('id', 'version_number', 'title', 'summary', 'status',
                      'content_hash', 'content_len', 'change_comment',
                      'editor_id', 'editor_name', 'created_at',
                      'restored_from_version_id')


def version_headers(cursor, article_id):
    """Строки истории по возрастанию номера — сырьё для wiki/history.py."""
    cursor.execute(
        """
        SELECT v.id, v.version_number, v.title, v.summary, v.status,
               md5(COALESCE(v.content, '')), length(COALESCE(v.content, '')),
               v.change_comment, v.editor_id, u.name, v.created_at,
               v.restored_from_version_id
          FROM wiki_article_versions v
          LEFT JOIN users u ON u.id = v.editor_id
         WHERE v.article_id = %s
         ORDER BY v.version_number
        """,
        (article_id,),
    )
    return [dict(zip(_VERSION_HEAD_KEYS, row)) for row in cursor.fetchall()]


_CURRENT_STATE_KEYS = ('title', 'summary', 'status', 'content_hash', 'content_len',
                       'updated_by', 'updated_by_name', 'updated_at')


def current_state(cursor, article_id):
    """Текущее состояние статьи в том же виде, что и шапка версии.

    Отдельный запрос, а не поле из get_article: там нет имени последнего
    редактора, а сравнивать состояния надо по отпечатку тела, а не по самому
    телу.
    """
    cursor.execute(
        """
        SELECT a.title, a.summary, a.status,
               md5(COALESCE(a.content, '')), length(COALESCE(a.content, '')),
               a.updated_by, u.name, a.updated_at
          FROM wiki_articles a
          LEFT JOIN users u ON u.id = a.updated_by
         WHERE a.id = %s
        """,
        (article_id,),
    )
    row = cursor.fetchone()
    return dict(zip(_CURRENT_STATE_KEYS, row)) if row else None


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

    # Формулировка важна дважды. Во-первых, комментарий строки описывает ПРАВКУ,
    # а в истории версий он подписывает редакцию, которую эта правка создала
    # (wiki/history.py), — «Перед восстановлением версии №1» на самой
    # восстановленной редакции читалось бы задом наперёд. Во-вторых, номера
    # версии здесь нет намеренно: version_number считает СОХРАНЕНИЯ, а не
    # редакции, человеку он нигде не показан, и «версия №5» в подписи означала
    # бы не то, что он видит в списке. Куда именно вернули, записано точно —
    # в restored_from_version_id, и экран подписывает это датой той редакции.
    # Прежних записей с другим текстом нет: до появления экрана истории
    # восстановлением не пользовались ни разу.
    snapshot_version(cursor, article_id, editor_id=editor_id, session_id=session_id,
                     comment='Восстановление прежней редакции')

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
    # …и по той же причине — файлы и связи. Восстановление было ЕДИНСТВЕННЫМ
    # путём, где тело статьи меняется, а производные от него данные остаются от
    # прошлой редакции. Для картинок это живой дефект и сейчас: откат к версии с
    # непривязанным файлом оставлял его видимым только загрузившему. Для связей
    # это значило бы, что «Сюда ссылаются» у чужой статьи показывает ссылку из
    # текста, который человек только что откатил.
    link_content_files(cursor, article_id, version['content'])
    link_content_articles(cursor, article_id, version['content'], editor_id=editor_id)
    return True


# Общий отдел — дом для статьи, которой не выбрали раздел. Ищем по слагу
# раздела и по названию отдела: слаг задаётся переносом и правкам названия не
# подвержен, название — запасной путь для базы, собранной руками.
_FALLBACK_SECTION_SLUG = 'obschiy-sotrudnik'
# «Общий отдел» после переезда пространств — верхний РАЗДЕЛ, а не
# пространство: запасной поиск идёт по имени родителя.
_FALLBACK_BRANCH_NAME = 'Общий отдел'


def default_section_id(cursor, space_ids=None):
    """Раздел «Общий отдел → Общий сотрудник» — куда падает статья без раздела.

    Статья вообще без раздела — ловушка, а не свобода: в режиме «наследовать»
    ей не от чего наследовать права, и её не видит НИКТО, кроме автора. Такую
    статью нельзя было ни найти в оглавлении, ни отыскать поиском, ни положить
    в раздел — на проде так залипли три штуки. Раньше от этого спасал
    переключатель «Всё содержимое» у администратора доступов; теперь спасать не
    от чего — раздел проставляется сам.

    space_ids ОГРАНИЧИВАЕТ поиск пространствами, доступными автору, и это не
    оптимизация, а граница: без неё статья автора из чужого пространства падала
    бы в «Общий сотрудник» пространства iGroup — то есть ровно за ту границу,
    ради которой пространства и заведены. Ничего не нашлось — возвращаем None:
    статья без раздела чинится руками, а тихо уехавшая к другому клиенту — нет.

    Возвращает None и когда общего раздела в базе нет. Сами его не создаём:
    молча заведённый публичный раздел раздал бы права шире, чем кто-либо
    просил, а это то самое, что отзывается труднее всего.
    """
    cursor.execute(
        """
        SELECT s.id
          FROM wiki_sections s
          JOIN wiki_spaces sp ON sp.id = s.space_id
          LEFT JOIN wiki_sections parent ON parent.id = s.parent_section_id
         WHERE s.status = 'active' AND sp.status = 'active'
           AND (%(spaces)s::int[] IS NULL OR s.space_id = ANY(%(spaces)s::int[]))
           AND (s.slug = %(slug)s OR parent.name = %(branch)s)
         ORDER BY (s.slug = %(slug)s) DESC, (parent.name = %(branch)s) DESC,
                  s.position, s.id
         LIMIT 1
        """,
        {'slug': _FALLBACK_SECTION_SLUG, 'branch': _FALLBACK_BRANCH_NAME,
         'spaces': list(space_ids) if space_ids else None},
    )
    row = cursor.fetchone()
    return row[0] if row else None


def set_sections(cursor, article_id, section_ids, space_ids=None):
    wanted = {int(s) for s in (section_ids or []) if s}
    if not wanted:
        fallback = default_section_id(cursor, space_ids)
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
        SELECT summary, content, content_plain, article_type, ai_opt_out, copy_protected
          FROM wiki_articles WHERE id = %s
        """,
        (source_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    summary, content, content_plain, article_type, ai_opt_out, copy_protected = row

    # Защита от копирования переезжает в копию ВМЕСТЕ с текстом — в отличие от
    # visibility_mode и strict_mode, которые сбрасываются нарочно. Разница в
    # том, что те два — про доступ к документу, и решает их новый владелец в
    # своём отделе; защита же — свойство самого текста, и копия — это тот же
    # текст. Сбрасывай её здесь — и «перенести к себе» стало бы обходным путём
    # вокруг запрета, доступным каждому, кто вправе завести статью.
    cursor.execute(
        """
        INSERT INTO wiki_articles (slug, title, summary, content, content_plain,
                                   article_type, status, visibility_mode, strict_mode,
                                   ai_opt_out, copy_protected,
                                   author_id, updated_by, owner_user_id,
                                   source_article_id)
        VALUES (%s, %s, %s, %s, %s, %s, 'draft', 'inherit', FALSE, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (slug, title, summary, content, content_plain, article_type,
         ai_opt_out, copy_protected, author_id, author_id, author_id, source_id),
    )
    article_id = cursor.fetchone()[0]
    set_sections(cursor, article_id, [section_id])
    # Файлы перепривязываются к копии: без этого картинки остались бы видны
    # только тому, кто их когда-то загрузил в исходную статью.
    link_content_files(cursor, article_id, content)
    # Тело копируется байт в байт, значит копия наследует и все ссылки на другие
    # статьи. Свой набор связей ей нужен собственный: без него у общей цели в
    # «Сюда ссылаются» осталась бы только исходная статья, а копия ссылалась бы
    # на неё молча.
    link_content_articles(cursor, article_id, content, editor_id=author_id)
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
