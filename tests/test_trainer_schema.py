"""Порядок разворота схемы раздела «Тренажёр»: таблицы → ALTER'ы → индексы.

Проверка та же, что у «Ограничителя Перезвона», и по той же причине: 17.08.2026
выкат раздела «Обращения» положил прод, потому что индекс по новому столбцу
выполнился РАНЬШЕ, чем ALTER TABLE этот столбец добавил. Весь разворот идёт под
SAVEPOINT, поэтому откатились все миграции разом. На пустой базе ошибка не
воспроизводится — проверяется именно ПОРЯДОК инструкций.

Отдельно закреплено требование раздела: сохраняться должны все замеры. Поэтому
здесь же список колонок, без которых журнал перестаёт отвечать на вопрос «что
именно тормозило и сколько это стоило».
"""

import re

from voice_trainer.schema import init_trainer_schema


class OrderCursor:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=None):
        # Комментарии режем ДО склейки: '--' действует до конца строки и в
        # однострочном виде спрятал бы за собой часть DDL.
        without_comments = ' '.join(part.split('--')[0] for part in str(sql).splitlines())
        self.statements.append(' '.join(without_comments.split()))

    def fetchone(self):
        return None

    def fetchall(self):
        return []


def statements():
    cursor = OrderCursor()
    init_trainer_schema(cursor)
    return cursor.statements


def test_every_index_comes_after_its_table():
    seen_tables = set()
    for sql in statements():
        created = re.search(r'CREATE TABLE IF NOT EXISTS (\w+)', sql, re.IGNORECASE)
        if created:
            seen_tables.add(created.group(1).lower())
            continue
        index = re.search(r'CREATE (?:UNIQUE )?INDEX IF NOT EXISTS \w+ ON (\w+)', sql, re.IGNORECASE)
        if index:
            table = index.group(1).lower()
            assert table in seen_tables, f'индекс по таблице {table} создаётся раньше самой таблицы'


def test_index_columns_exist_by_that_moment():
    """Каждый столбец из индекса к этому моменту уже объявлен — в CREATE TABLE
    или в более раннем ALTER ... ADD COLUMN. Ровно это и сломалось на проде."""
    known: dict[str, set] = {}
    for sql in statements():
        created = re.search(r'CREATE TABLE IF NOT EXISTS (\w+) \((.*)\)', sql, re.IGNORECASE | re.DOTALL)
        if created:
            table = created.group(1).lower()
            columns = set(re.findall(r'(?:^|,)\s*(\w+)\s+[A-Z]', created.group(2)))
            known[table] = {c.lower() for c in columns}
            continue
        altered = re.search(r'ALTER TABLE (\w+) ADD COLUMN IF NOT EXISTS (\w+)', sql, re.IGNORECASE)
        if altered:
            known.setdefault(altered.group(1).lower(), set()).add(altered.group(2).lower())
            continue
        index = re.search(r'CREATE (?:UNIQUE )?INDEX IF NOT EXISTS \w+\s+ON (\w+)\s*\((.*?)\)', sql, re.IGNORECASE)
        if index:
            table = index.group(1).lower()
            for column in re.findall(r'\b(\w+)\b', index.group(2)):
                token = column.lower()
                if token in ('lower', 'desc', 'asc'):
                    continue
                assert token in known.get(table, set()), (
                    f'индекс по {table}.{token} создаётся раньше, чем появляется столбец'
                )


def test_schema_is_idempotent_by_construction():
    """Init вызывается при каждом старте — все инструкции обязаны быть
    безопасными для повторного выполнения."""
    for sql in statements():
        upper = sql.upper()
        if upper.startswith('CREATE TABLE'):
            assert 'IF NOT EXISTS' in upper
        if 'CREATE INDEX' in upper or 'CREATE UNIQUE INDEX' in upper:
            assert 'IF NOT EXISTS' in upper
        if upper.startswith('INSERT INTO'):
            assert 'ON CONFLICT' in upper


def _columns_of(table):
    for sql in statements():
        created = re.search(rf'CREATE TABLE IF NOT EXISTS {table} \((.*)\)',
                            sql, re.IGNORECASE | re.DOTALL)
        if created:
            return {c.lower() for c in re.findall(r'(?:^|,)\s*(\w+)\s+[A-Z]', created.group(1))}
    raise AssertionError(f'таблица {table} не создаётся')


def test_turn_keeps_every_measurement():
    """Требование владельца: сохраняются АБСОЛЮТНО все метрики.

    Список не косметический — без любой из этих колонок журнал перестаёт
    отвечать на вопрос, какое звено тормозило на конкретной реплике.
    """
    columns = _columns_of('trainer_turns')
    for name in (
        'stt_confidence', 'stt_tokens', 'stt_audio_ms', 'endpoint_delay_ms',
        'llm_provider', 'llm_model', 'llm_first_token_ms', 'llm_total_ms',
        'llm_input_tokens', 'llm_output_tokens',
        'tts_model', 'tts_ttfb_ms', 'tts_audio_ms', 'tts_bytes',
        'voice_to_voice_ms', 'barge_in', 'sources',
    ):
        assert name in columns, f'в trainer_turns потерян замер {name}'


def test_session_keeps_cost_with_its_rates():
    """Стоимость хранится вместе со ставками, по которым посчитана: тарифы
    меняются, а вопрос «сколько стоил вон тот прогон» задаётся задним числом."""
    columns = _columns_of('trainer_sessions')
    for name in ('cost_usd', 'cost_breakdown', 'rates', 'mode',
                 'voice_to_voice_p50', 'voice_to_voice_max'):
        assert name in columns, f'в trainer_sessions потеряна колонка {name}'


def test_turns_are_unique_per_session():
    """Реплика с тем же номером не должна задваиваться при повторной отправке."""
    assert any('UQ_TRAINER_TURNS_SESSION_IDX' in sql.upper() for sql in statements())
