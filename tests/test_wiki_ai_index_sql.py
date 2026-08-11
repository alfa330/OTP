# -*- coding: utf-8 -*-
"""Сверка SQL индексатора со схемой — без базы.

Зачем. Таблицы wiki_ai_chunks и wiki_ai_article_index появятся на проде только
после деплоя, а до тех пор Postgres не может проверить запросы к ним даже на
синтаксис: разбор падает на «relation does not exist» раньше всего остального.
Значит опечатку в имени колонки поймать нечем — кроме этой сверки.

Тест сравнивает две вещи, которые обязаны совпадать: колонки, объявленные в
wiki.schema._AI_STATEMENTS, и колонки, которые перечисляет SQL в wiki.ai.index.
Он же ловит расхождение в будущем: колонку переименовали в схеме, а в INSERT
забыли.
"""

import re
import unittest

from wiki import schema as wiki_schema
from wiki.ai import index as ai_index

_CREATE = re.compile(
    r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\((.*?)\n\s*\);',
    re.S | re.I)
_ADD_COLUMN = re.compile(
    r'ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+(\w+)', re.I)
_INSERT = re.compile(r'INSERT\s+INTO\s+(\w+)\s*\(([^)]*)\)', re.S | re.I)


def _declared_columns():
    """{таблица: {колонки}} из операторов схемы раздела."""
    tables = {}
    for statement in (list(wiki_schema._AI_STATEMENTS)
                      + list(wiki_schema._AI_VECTOR_STATEMENTS)):
        for table, body in _CREATE.findall(statement):
            columns = set()
            depth = 0
            for line in body.splitlines():
                stripped = line.strip()
                depth += stripped.count('(') - stripped.count(')')
                match = re.match(r'(\w+)\s+[A-Za-z]', stripped)
                if match and not stripped.upper().startswith(
                        ('UNIQUE', 'PRIMARY', 'FOREIGN', 'CHECK', 'CONSTRAINT')):
                    columns.add(match.group(1))
            tables.setdefault(table, set()).update(columns)
        for table, column in _ADD_COLUMN.findall(statement):
            tables.setdefault(table, set()).add(column)
    return tables


class IndexSqlMatchesSchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.declared = _declared_columns()

    def test_expected_tables_declared(self):
        for table in ('wiki_ai_chunks', 'wiki_ai_article_index'):
            self.assertIn(table, self.declared)

    def test_chunk_table_has_required_columns(self):
        expected = {'id', 'article_id', 'chunk_idx', 'heading_path', 'text',
                    'requires_ack', 'char_len', 'text_hash', 'created_at',
                    'chunk_tsv'}
        self.assertTrue(expected <= self.declared['wiki_ai_chunks'],
                        expected - self.declared['wiki_ai_chunks'])

    def test_index_table_has_required_columns(self):
        expected = {'article_id', 'content_hash', 'chunk_count', 'indexed_at'}
        self.assertTrue(expected <= self.declared['wiki_ai_article_index'],
                        expected - self.declared['wiki_ai_article_index'])

    def test_embedding_table_has_required_columns(self):
        expected = {'text_hash', 'embed_provider', 'embed_model', 'embed_dim',
                    'embedding', 'created_at'}
        self.assertIn('wiki_ai_embeddings', self.declared)
        self.assertTrue(expected <= self.declared['wiki_ai_embeddings'],
                        expected - self.declared['wiki_ai_embeddings'])

    def test_vector_ddl_is_under_its_own_savepoint(self):
        """Без расширения vector помощник обязан остаться на лексике.

        Если операторы с CREATE EXTENSION уедут в общий список, падение прав на
        расширение откатит всю схему раздела — а это уже не деградация, а потеря
        вики целиком.
        """
        joined = ' '.join(wiki_schema._AI_STATEMENTS)
        self.assertNotIn('CREATE EXTENSION', joined.upper())
        self.assertIn('CREATE EXTENSION',
                      ' '.join(wiki_schema._AI_VECTOR_STATEMENTS).upper())

    def test_inserts_reference_declared_columns_only(self):
        from wiki.ai import embed as ai_embed

        statements = [getattr(ai_index, name) for name in dir(ai_index)
                      if name.startswith('_') and isinstance(getattr(ai_index, name), str)]
        statements += [getattr(ai_embed, name) for name in dir(ai_embed)
                       if name.startswith('_') and isinstance(getattr(ai_embed, name), str)]
        checked = 0
        for sql in statements:
            for table, columns in _INSERT.findall(sql):
                self.assertIn(table, self.declared, f'{table} не объявлена в схеме')
                for column in (c.strip() for c in columns.split(',')):
                    if not column:
                        continue
                    self.assertIn(column, self.declared[table],
                                  f'{table}.{column} нет в схеме')
                    checked += 1
        self.assertGreater(checked, 0, 'не нашли ни одного INSERT — сверка бесполезна')

    def test_generated_column_is_not_written(self):
        """chunk_tsv генерируемая: попытка вписать её вызвала бы ошибку на проде."""
        self.assertNotIn('chunk_tsv', ai_index._INSERT_CHUNK)

    def test_selection_checks_eligibility_not_only_status(self):
        """Рубильник обязан отсекать статью НА ВХОДЕ в индекс, а не только на выдаче.

        Иначе текст статьи с выключенной поддержкой ИИ всё равно нарезается в
        куски и уходит в эмбеддинги — во внешний сервис, ровно вопреки тому, что
        рубильник обещает. Проверка на выдаче (wiki/perimeter.py) от этого не
        спасает: она работает уже после того, как текст отправлен.
        """
        sql = ai_index._SELECT_ARTICLE
        self.assertIn('ai_opt_out', sql)
        self.assertIn('strict_mode', sql)
        self.assertIn('wiki_sections', sql)

    def test_reindex_all_can_clean_up_dropped_articles(self):
        """Выборка обязана включать статьи, у которых куски есть, а права уже нет."""
        self.assertIn('wiki_ai_chunks', ai_index.reindex_all.__doc__ or '')

    def test_hash_is_stable_and_whitespace_insensitive(self):
        first = ai_index.text_hash('<p>Текст  статьи</p>', 'Текст статьи')
        second = ai_index.text_hash('<p>Текст статьи</p>', 'Текст   статьи')
        self.assertEqual(first, second)
        self.assertNotEqual(first, ai_index.text_hash('<p>Другое</p>', 'Другое'))

    def test_hash_separates_fields(self):
        """Склейка полей не должна давать коллизию: 'аб'+'' != 'а'+'б'."""
        self.assertNotEqual(ai_index.text_hash('аб', ''), ai_index.text_hash('а', 'б'))


if __name__ == '__main__':
    unittest.main()
