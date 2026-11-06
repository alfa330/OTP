# -*- coding: utf-8 -*-
"""Разовый перенос неоценённого бэклога в «Новую» (scripts/tasks_backlog_to_new.py).

Правило владельца (06.11.2026): «Новая» — ещё не разобранное на совещании, бэклог —
очередь оценённых; первый балл сам вернёт задачу в бэклог. Оценённые, шаблоны
регламентов (в бэклоге они на паузе) и задачи досок остаются на месте.
Смысл SQL проверяется на настоящем Postgres (TASK_PROJECTS_TEST_PORT — порт локальной базы,
своя схема со случайным именем, в конце удаляется); в CI эта часть пропускается.
"""
import contextlib
import importlib.util
import os
import unittest
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "tasks_backlog_to_new.py"
PORT = os.environ.get('TASK_PROJECTS_TEST_PORT')


def _load_script():
    spec = importlib.util.spec_from_file_location("tasks_backlog_to_new", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script()
TEMPLATE_GUARD = "AND NOT (COALESCE(t.is_regulation, FALSE) AND t.regulation_parent_id IS NULL)"


class SelectionRuleTests(unittest.TestCase):
    def test_only_unscored_backlog_without_project_and_templates(self):
        sql = SCRIPT.CANDIDATES_SQL
        self.assertIn("t.is_backlog = TRUE", sql)
        self.assertIn("t.status = 'assigned'", sql)
        self.assertIn("t.priority_score IS NULL", sql)
        self.assertIn("t.project_id IS NULL", sql)
        self.assertIn(TEMPLATE_GUARD, sql)
        # Строки под замком: между списком и записью задачу могли взять в работу или оценить.
        self.assertIn("FOR UPDATE OF t", sql)

    def test_move_rechecks_state_and_keeps_rank_and_updated_at(self):
        sql = SCRIPT.MOVE_SQL
        self.assertIn("SET is_backlog = FALSE", sql)
        for guard in ("AND t.is_backlog = TRUE", "AND t.status = 'assigned'", "AND t.priority_score IS NULL",
                      "AND t.project_id IS NULL", TEMPLATE_GUARD):
            self.assertIn(guard, sql)
        # Свежий updated_at сжёг бы отметки «просмотрено» в «Ждут вас».
        self.assertNotIn("updated_at", sql)
        self.assertNotIn("backlog_rank", sql)

    def test_dry_run_by_default(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertIn("parser.add_argument('--apply', action='store_true'", source)
        self.assertIn("if not args.apply:", source)
        self.assertIn("connection.rollback()", source)


SCHEMA_DDL = '''
    CREATE TABLE users (id integer PRIMARY KEY, name text NOT NULL);
    CREATE TABLE task_projects (id integer PRIMARY KEY, code text NOT NULL);
    CREATE TABLE tasks (
        id integer PRIMARY KEY, subject text, status text NOT NULL, priority text DEFAULT 'normal',
        is_backlog boolean NOT NULL DEFAULT FALSE, backlog_rank double precision,
        priority_score smallint, is_regulation boolean NOT NULL DEFAULT FALSE,
        regulation_parent_id integer, project_id integer, project_number integer,
        created_at timestamp NOT NULL, due_at timestamp, updated_at timestamp
    );
    CREATE TABLE task_assignees (task_id integer, user_id integer, position integer);
    INSERT INTO users VALUES (1, 'Исполнитель Первый'), (2, 'Исполнитель Второй');
    -- 1, 2 — бэклог без балла (2 раньше в очереди); 3 — бэклог с баллом; 4 — уже «Новая»;
    -- 5 — шаблон регламента в бэклоге (пауза); 6 — повтор регламента в бэклоге;
    -- 7 — задача доски HR в бэклоге; 8 — в работе; 9 — сданная с флагом бэклога (старые данные).
    INSERT INTO tasks (id, subject, status, is_backlog, backlog_rank, priority_score, is_regulation,
                       regulation_parent_id, created_at, updated_at) VALUES
        (1, 'Бэклог второй', 'assigned', TRUE, 2, NULL, FALSE, NULL, '2026-09-01 10:00', '2026-10-01 10:00'),
        (2, 'Бэклог первый', 'assigned', TRUE, 1, NULL, FALSE, NULL, '2026-09-05 10:00', '2026-10-01 10:00'),
        (3, 'Оценённая', 'assigned', TRUE, 3, 1, FALSE, NULL, '2026-09-02 10:00', '2026-10-01 10:00'),
        (4, 'Уже новая', 'assigned', FALSE, NULL, NULL, FALSE, NULL, '2026-09-03 10:00', '2026-10-01 10:00'),
        (5, 'Шаблон регламента', 'assigned', TRUE, 4, NULL, TRUE, NULL, '2026-09-04 10:00', '2026-10-01 10:00'),
        (6, 'Повтор регламента', 'assigned', TRUE, 5, NULL, TRUE, 5, '2026-11-04 10:00', '2026-10-01 10:00'),
        (7, 'Бэклог доски HR', 'assigned', TRUE, 6, NULL, FALSE, NULL, '2026-11-06 09:00', '2026-10-01 10:00'),
        (8, 'В работе', 'in_progress', FALSE, NULL, NULL, FALSE, NULL, '2026-09-06 10:00', '2026-10-01 10:00'),
        (9, 'Сдана из бэклога', 'completed', TRUE, 7, NULL, FALSE, NULL, '2026-09-07 10:00', '2026-10-01 10:00');
    INSERT INTO task_projects VALUES (10, 'HR');
    UPDATE tasks SET project_id = 10, project_number = 1 WHERE id = 7;
    INSERT INTO task_assignees VALUES (1, 1, 0), (2, 2, 0), (2, 1, 1);
'''


@contextlib.contextmanager
def _cursor():
    import psycopg2
    conn = psycopg2.connect(host='127.0.0.1', port=int(PORT), user='postgres', dbname='postgres', connect_timeout=3)
    schema = 'tbn_' + uuid.uuid4().hex[:10]
    cur = conn.cursor()
    try:
        cur.execute(f'CREATE SCHEMA {schema}')
        cur.execute(f'SET search_path TO {schema}')
        cur.execute(SCHEMA_DDL)
        yield cur
    finally:
        conn.rollback()
        cur.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
        conn.commit()
        conn.close()


@pytest.mark.skipif(not PORT, reason='needs local PostgreSQL (TASK_PROJECTS_TEST_PORT)')
class RealPostgresMoveTests(unittest.TestCase):
    def test_moves_only_unscored_backlog(self):
        with _cursor() as cur:
            rows = SCRIPT.select_candidates(cur)
            self.assertEqual([row[0] for row in rows], [2, 1, 6])  # очередь бэклога
            self.assertEqual(rows[0][-1], 'Исполнитель Второй, Исполнитель Первый')
            self.assertEqual(SCRIPT.move_to_new(cur, [row[0] for row in rows]), 3)
            cur.execute('SELECT id, is_backlog, backlog_rank, updated_at::text FROM tasks ORDER BY id')
            state = {task_id: (backlog, rank, updated) for task_id, backlog, rank, updated in cur.fetchall()}
            for moved in (1, 2, 6):
                self.assertFalse(state[moved][0], moved)
            for kept in (3, 5, 7, 9):
                self.assertTrue(state[kept][0], kept)
            self.assertFalse(state[4][0])
            self.assertFalse(state[8][0])
            # Ранг и время правки — как были.
            self.assertEqual(state[1][1], 2.0)
            self.assertTrue(all(updated == '2026-10-01 10:00:00' for _b, _r, updated in state.values()))
            # Повторный запуск — переносить нечего.
            self.assertEqual(SCRIPT.select_candidates(cur), [])

    def test_nothing_to_move_is_not_an_error(self):
        with _cursor() as cur:
            self.assertEqual(SCRIPT.move_to_new(cur, []), 0)
