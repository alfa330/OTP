"""Обязательное ознакомление с регламентами.

Владелец выбрал формальный документооборот, а не мягкое напоминание. Отсюда
устройство:

  * ключ назначения включает ВЕРСИЮ статьи. Вышла новая версия — назначение
    перевыпускается, старое остаётся в истории со своим результатом. Иначе
    отчёт «кто ознакомлен» врал бы: человек читал одну редакцию, а действует
    другая;
  * три отдельные метки времени. «Открыл», «дочитал» и «подтвердил» — разные
    события, и для документооборота их надо различать. Подтверждение без
    прочтения ничего не стоит;
  * снимки отдела, группы, роли и супервайзера на момент назначения. Через год
    отчёт должен показывать, кем человек был тогда, а не кем стал.

В оригинале снимки хранили должность и менеджера; у нас справочника должностей
нет, поэтому пишем то, что есть и поддерживается актуальным.
"""

import re

# Блоки, которые нужно раскрыть, чтобы подтвердить ознакомление. Разметка
# приходит из редактора; по дампу прода таких блоков в контенте уже 35.
_REQUIRED_BLOCK = re.compile(r'data-required-for-ack\s*=\s*"(?:true|1)"', re.I)


def count_required_blocks(html):
    """Сколько блоков статьи обязательны к раскрытию."""
    return len(_REQUIRED_BLOCK.findall(str(html or '')))


def current_version(cursor, article_id):
    cursor.execute(
        'SELECT COALESCE(max(version_number), 1) FROM wiki_article_versions WHERE article_id = %s',
        (article_id,),
    )
    return cursor.fetchone()[0]


def assign(cursor, *, article_id, user_ids, assigned_by, due_at=None):
    """Назначить ознакомление. Идемпотентно по (статья, версия, человек).

    Снимок оргданных берётся ОДНИМ запросом на всех: назначение на отдел из
    сотни человек не должно превращаться в сотню обращений к базе.
    """
    if not user_ids:
        return 0

    version = current_version(cursor, article_id)
    cursor.execute(
        """
        INSERT INTO wiki_ack_assignments (
            article_id, article_version, user_id, assigned_by, due_at,
            blocks_total, status,
            snapshot_department_id, snapshot_department_name,
            snapshot_group_id, snapshot_group_name,
            snapshot_role, snapshot_supervisor_id, snapshot_supervisor_name)
        SELECT %(article)s, %(version)s, u.id, %(by)s, %(due)s,
               (SELECT count(*) FROM regexp_matches(
                    COALESCE(a.content, ''), 'data-required-for-ack\\s*=\\s*"(?:true|1)"', 'gi')),
               'not_open',
               u.department_id, d.name,
               g.id, g.name,
               u.role, u.supervisor_id, s.name
          FROM users u
          CROSS JOIN wiki_articles a
          LEFT JOIN departments d ON d.id = u.department_id
          LEFT JOIN users s ON s.id = u.supervisor_id
          LEFT JOIN LATERAL (
                SELECT gr.id, gr.name
                  FROM group_operator_memberships gom
                  JOIN groups gr ON gr.id = gom.group_id
                 WHERE gom.operator_id = u.id
                   AND gom.start_date <= CURRENT_DATE
                   AND (gom.end_date IS NULL OR gom.end_date >= CURRENT_DATE)
                 LIMIT 1
          ) g ON TRUE
         WHERE u.id = ANY(%(users)s) AND a.id = %(article)s
        ON CONFLICT (article_id, article_version, user_id) DO NOTHING
        """,
        {'article': article_id, 'version': version, 'by': assigned_by,
         'due': due_at, 'users': list(user_ids)},
    )
    return cursor.rowcount


def mark_opened(cursor, article_id, user_id):
    """Человек открыл статью. Не то же самое, что прочитал."""
    cursor.execute(
        """
        UPDATE wiki_ack_assignments
           SET first_viewed_at = COALESCE(first_viewed_at,
                                          (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')),
               status = CASE WHEN status = 'not_open' THEN 'in_progress' ELSE status END,
               updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
         WHERE article_id = %s AND user_id = %s
           AND status IN ('not_open', 'in_progress')
        """,
        (article_id, user_id),
    )
    return cursor.rowcount > 0


def mark_read(cursor, article_id, user_id, blocks_opened):
    """Дочитал до конца и раскрыл обязательные блоки.

    Отметку ставит только сервер и только по факту: клиент сообщает, сколько
    блоков раскрыто, а условие «дочитал» проверяется здесь же сверкой с
    blocks_total. В оригинале это условие считалось на клиенте по прокрутке
    ОКНА — а окно в нашем портале не скроллится, и отметка ставилась бы в
    момент открытия статьи.
    """
    cursor.execute(
        """
        UPDATE wiki_ack_assignments
           SET blocks_opened = GREATEST(blocks_opened, %s),
               read_completed_at = CASE
                   WHEN GREATEST(blocks_opened, %s) >= blocks_total
                   THEN COALESCE(read_completed_at,
                                 (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
                   ELSE read_completed_at END,
               status = CASE
                   WHEN GREATEST(blocks_opened, %s) >= blocks_total
                        AND status IN ('not_open', 'in_progress')
                   THEN 'read_completed' ELSE status END,
               updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
         WHERE article_id = %s AND user_id = %s
           AND status IN ('not_open', 'in_progress', 'read_completed')
        RETURNING status, blocks_opened, blocks_total, read_completed_at IS NOT NULL
        """,
        (blocks_opened, blocks_opened, blocks_opened, article_id, user_id),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(zip(('status', 'blocks_opened', 'blocks_total', 'read_completed'), row))


def acknowledge(cursor, article_id, user_id):
    """Подтверждение. Возможно ТОЛЬКО после отметки «дочитал».

    Это и есть весь смысл формального ознакомления: подтверждение без
    прочтения не имеет силы, поэтому условие стоит в самом UPDATE, а не в
    интерфейсе, где его можно обойти.
    """
    cursor.execute(
        """
        UPDATE wiki_ack_assignments
           SET acknowledged_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
               status = 'acknowledged',
               completed_in_time = CASE
                   WHEN due_at IS NULL THEN TRUE
                   ELSE (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') <= due_at END,
               overdue_days = CASE
                   WHEN due_at IS NULL THEN 0
                   ELSE GREATEST(0, EXTRACT(DAY FROM
                        (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty') - due_at)::int) END,
               updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
         WHERE article_id = %s AND user_id = %s
           AND read_completed_at IS NOT NULL
           AND acknowledged_at IS NULL
        RETURNING id
        """,
        (article_id, user_id),
    )
    return cursor.fetchone() is not None


def supersede_older_versions(cursor, article_id):
    """Вышла новая версия — прежние незакрытые назначения помечаются устаревшими.

    Уже подтверждённые НЕ трогаем: они остаются свидетельством, что человек
    ознакомился именно с той редакцией.
    """
    version = current_version(cursor, article_id)
    cursor.execute(
        """
        UPDATE wiki_ack_assignments
           SET status = 'superseded',
               updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
         WHERE article_id = %s AND article_version < %s
           AND status NOT IN ('acknowledged', 'superseded', 'cancelled')
        """,
        (article_id, version),
    )
    return cursor.rowcount


_MY_KEYS = ('article_id', 'article_version', 'slug', 'title', 'status', 'due_at',
            'blocks_total', 'blocks_opened', 'first_viewed_at',
            'read_completed_at', 'acknowledged_at')


def my_assignments(cursor, user_id, visible_ids):
    """Что человек обязан прочитать — в границах его периметра."""
    if not visible_ids:
        return []
    cursor.execute(
        """
        SELECT k.article_id, k.article_version, a.slug, a.title, k.status, k.due_at,
               k.blocks_total, k.blocks_opened, k.first_viewed_at,
               k.read_completed_at, k.acknowledged_at
          FROM wiki_ack_assignments k
          JOIN wiki_articles a ON a.id = k.article_id
         WHERE k.user_id = %s AND k.article_id = ANY(%s)
           AND k.status NOT IN ('superseded', 'cancelled')
         ORDER BY (k.acknowledged_at IS NOT NULL), k.due_at NULLS LAST, a.title
        """,
        (user_id, list(visible_ids)),
    )
    return [dict(zip(_MY_KEYS, row)) for row in cursor.fetchall()]


def assignment_for(cursor, article_id, user_id):
    cursor.execute(
        """
        SELECT article_id, article_version, status, due_at, blocks_total,
               blocks_opened, first_viewed_at, read_completed_at, acknowledged_at
          FROM wiki_ack_assignments
         WHERE article_id = %s AND user_id = %s
           AND status NOT IN ('superseded', 'cancelled')
         ORDER BY article_version DESC LIMIT 1
        """,
        (article_id, user_id),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(zip(('article_id', 'article_version', 'status', 'due_at', 'blocks_total',
                     'blocks_opened', 'first_viewed_at', 'read_completed_at',
                     'acknowledged_at'), row))


_REPORT_KEYS = ('user_id', 'user_name', 'department_name', 'group_name', 'role',
                'supervisor_name', 'status', 'due_at', 'first_viewed_at',
                'read_completed_at', 'acknowledged_at', 'completed_in_time', 'overdue_days')


def report(cursor, article_id):
    """Кто ознакомился, а кто нет. Показывает оргданные НА МОМЕНТ назначения."""
    cursor.execute(
        """
        SELECT k.user_id, u.name, k.snapshot_department_name, k.snapshot_group_name,
               k.snapshot_role, k.snapshot_supervisor_name, k.status, k.due_at,
               k.first_viewed_at, k.read_completed_at, k.acknowledged_at,
               k.completed_in_time, k.overdue_days
          FROM wiki_ack_assignments k
          LEFT JOIN users u ON u.id = k.user_id
         WHERE k.article_id = %s AND k.status <> 'superseded'
         ORDER BY (k.acknowledged_at IS NOT NULL), u.name
        """,
        (article_id,),
    )
    return [dict(zip(_REPORT_KEYS, row)) for row in cursor.fetchall()]


def summary(cursor, article_id):
    cursor.execute(
        """
        SELECT count(*),
               count(*) FILTER (WHERE status = 'acknowledged'),
               count(*) FILTER (WHERE status IN ('not_open', 'in_progress')),
               count(*) FILTER (WHERE due_at IS NOT NULL
                                  AND acknowledged_at IS NULL
                                  AND due_at < (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
          FROM wiki_ack_assignments
         WHERE article_id = %s AND status <> 'superseded'
        """,
        (article_id,),
    )
    total, done, pending, overdue = cursor.fetchone() or (0, 0, 0, 0)
    return {'total': total, 'acknowledged': done, 'pending': pending, 'overdue': overdue}
