"""Разовый перенос неоценённого бэклога в «Новую» — при запуске баллов и досок-проектов (#401).

    python scripts/tasks_backlog_to_new.py --dsn postgresql://…            # список, ничего не пишет
    python scripts/tasks_backlog_to_new.py --dsn postgresql://… --apply    # перенести
    python scripts/tasks_backlog_to_new.py --dsn-env TASKS_DB_DSN --apply  # строка подключения из окружения

Решение владельца (06.11.2026): «Новая» — то, что ещё не разобрали на совещании, бэклог —
очередь уже оценённых. Всё, что лежит в бэклоге без балла, уходит в «Новую» (туда же, где
уже стоят задачи прежней колонки «К выполнению»); суперадмины проставят баллы, и с первым
баллом задача сама вернётся в бэклог — в конец своего балла (update_task_board_state).

Остаются в бэклоге:
  - оценённые — они уже разобраны;
  - шаблоны регламентов: в бэклоге шаблон стоит на паузе (materialize_due_regulation_tasks
    берёт только шаблоны вне бэклога), а в «Новой» он ожил бы и сразу догнал пропущенные
    повторы — до трёх задач разом. Повторы регламента переносятся как обычные задачи;
  - задачи досок-проектов: все имеющиеся на момент выкладки — без проекта (досок до неё не
    было), а новые задачи досок живут уже по новому правилу — даже если скрипт запустят не сразу;
  - не `assigned`: начатое и сданное в бэклоге не лежит (старт работы снимает флаг), а если
    такое найдётся — «Новой» оно не станет.

Ранг очереди и время правки не трогаем: ранг при оценке всё равно пересчитается, а свежий
updated_at сжёг бы отметки «просмотрено» в «Ждут вас» у исполнителей. Telegram не уходит:
это перекладка, а не «вынесли в работу». Исполнители увидят задачи в «Новой» и в «Ждут вас»
так же, как любую назначенную им задачу.
Повторный запуск безопасен: переносит только то, что снова оказалось в бэклоге без балла.
"""
import argparse
import os
import sys

# Неоценённый бэклог без проекта, кроме шаблонов регламентов. Порядок — очередь бэклога,
# как её видели до переноса: так список проще сверить с экраном.
CANDIDATES_SQL = """
    SELECT t.id, COALESCE(p.code || '-' || t.project_number, '#' || t.id) AS task_key,
           t.subject, t.priority, t.created_at, t.due_at,
           (SELECT string_agg(u.name, ', ' ORDER BY ta.position, ta.user_id)
              FROM task_assignees ta JOIN users u ON u.id = ta.user_id
             WHERE ta.task_id = t.id) AS assignees
    FROM tasks t
    LEFT JOIN task_projects p ON p.id = t.project_id
    WHERE t.is_backlog = TRUE
      AND t.status = 'assigned'
      AND t.priority_score IS NULL
      AND t.project_id IS NULL
      AND NOT (COALESCE(t.is_regulation, FALSE) AND t.regulation_parent_id IS NULL)
    ORDER BY t.backlog_rank ASC NULLS LAST, t.created_at ASC, t.id ASC
    FOR UPDATE OF t
"""

# Условия повторены: строки под замком, но запись не должна полагаться на то, что список
# собран этим же скриптом.
MOVE_SQL = """
    UPDATE tasks t
    SET is_backlog = FALSE
    WHERE t.id = ANY(%s::int[])
      AND t.is_backlog = TRUE
      AND t.status = 'assigned'
      AND t.priority_score IS NULL
      AND t.project_id IS NULL
      AND NOT (COALESCE(t.is_regulation, FALSE) AND t.regulation_parent_id IS NULL)
"""

BACKLOG_TOTAL_SQL = "SELECT COUNT(*) FROM tasks WHERE is_backlog = TRUE"


def select_candidates(cursor):
    """Неоценённый бэклог без проекта и без шаблонов регламентов — под замком строк."""
    cursor.execute(CANDIDATES_SQL)
    return cursor.fetchall()


def move_to_new(cursor, task_ids):
    """Перенести в «Новую». Отдаёт число перенесённых строк."""
    if not task_ids:
        return 0
    cursor.execute(MOVE_SQL, (list(task_ids),))
    return cursor.rowcount


def _format_row(row):
    _task_id, key, subject, priority, created_at, due_at, assignees = row
    parts = [key, (subject or '').strip() or 'Без темы', f'исполнители: {assignees or "—"}']
    if priority and priority != 'normal':
        parts.append({'urgent': 'срочная', 'critical': 'критичная'}.get(priority, priority))
    parts.append(f'создана {str(created_at)[:16]}')
    if due_at:
        parts.append(f'срок {str(due_at)[:16]}')
    return ' · '.join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument('--dsn', help='строка подключения к базе (стенд)')
    where.add_argument('--dsn-env', help='имя переменной окружения со строкой подключения')
    parser.add_argument('--apply', action='store_true', help='записать; без флага — только список')
    args = parser.parse_args(argv)

    dsn = args.dsn or os.environ.get(args.dsn_env or '', '')
    if not dsn:
        raise SystemExit(f'Переменная {args.dsn_env} пуста — строки подключения нет')

    import psycopg2
    connection = psycopg2.connect(dsn)
    try:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute("SET client_encoding TO 'UTF8'")
                rows = select_candidates(cursor)
                cursor.execute(BACKLOG_TOTAL_SQL)
                backlog_total = int(cursor.fetchone()[0] or 0)
                print(f'В бэклоге без балла и без проекта: {len(rows)} — уйдут в «Новую»')
                for row in rows:
                    print('  ' + _format_row(row))
                print(f'Останутся в бэклоге: {backlog_total - len(rows)} '
                      '(с баллом, шаблоны регламентов, задачи досок)')
                if not args.apply:
                    print('Сухой прогон: ничего не записано. Перенести — с --apply.')
                    connection.rollback()
                    return 0
                moved = move_to_new(cursor, [row[0] for row in rows])
                if moved != len(rows):
                    raise RuntimeError(f'перенесено {moved} из {len(rows)} — откатываю')
                print(f'Перенесено в «Новую»: {moved}')
    finally:
        connection.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
