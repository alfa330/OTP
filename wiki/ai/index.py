# -*- coding: utf-8 -*-
"""Индекс кусков статей: пересборка по изменению текста.

Признак «статья изменилась» — sha256 её текста, а НЕ updated_at и не
version_number. Причины конкретные, обе проверены в коде правки:
  * PATCH, меняющий только теги или разделы, до UPDATE самой статьи не доходит
    (wiki/edit.py выходит раньше) — updated_at не сдвинется, а текст мог;
  * version_number растёт даже на пустом PATCH (снимок версии снимается до
    проверки изменений) — по нему пересборка шла бы впустую.

Хеш считается по content + content_plain: первое — источник нарезки, второе —
запасной текст для статей-инструментов с пустым телом.
"""

import hashlib

from .chunker import chunk_article

_SELECT_ARTICLE = """
SELECT a.status, coalesce(a.content, ''), coalesce(a.content_plain, ''),
       (a.status = 'published' AND NOT a.strict_mode AND NOT a.ai_opt_out
        AND NOT EXISTS (SELECT 1
                          FROM wiki_article_sections s
                          JOIN wiki_sections sec ON sec.id = s.section_id
                         WHERE s.article_id = a.id AND sec.ai_opt_out)) AS eligible
  FROM wiki_articles a WHERE a.id = %(article_id)s
"""

_SELECT_HASH = """
SELECT content_hash FROM wiki_ai_article_index WHERE article_id = %(article_id)s
"""

_DELETE_CHUNKS = "DELETE FROM wiki_ai_chunks WHERE article_id = %(article_id)s"

_INSERT_CHUNK = """
INSERT INTO wiki_ai_chunks
       (article_id, chunk_idx, heading_path, text, requires_ack, char_len, text_hash)
VALUES (%(article_id)s, %(chunk_idx)s, %(heading_path)s, %(text)s,
        %(requires_ack)s, %(char_len)s, %(text_hash)s)
"""

_UPSERT_INDEX = """
INSERT INTO wiki_ai_article_index (article_id, content_hash, chunk_count, indexed_at)
VALUES (%(article_id)s, %(content_hash)s, %(chunk_count)s,
        (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
ON CONFLICT (article_id) DO UPDATE
   SET content_hash = EXCLUDED.content_hash,
       chunk_count  = EXCLUDED.chunk_count,
       indexed_at   = EXCLUDED.indexed_at
"""

_DELETE_INDEX = "DELETE FROM wiki_ai_article_index WHERE article_id = %(article_id)s"


def text_hash(*parts):
    """sha256 по нормализованному тексту. Своя реализация, а не импорт из call_qa.

    В call_qa.evaluation.fingerprint есть готовый content_hash, но тащить сюда
    весь пакет call_qa (со своим config и провайдерами) ради трёх строк — значит
    связать две подсистемы. Раздел «Вики» должен подниматься независимо.
    """
    digest = hashlib.sha256()
    for part in parts:
        digest.update(' '.join(str(part or '').split()).encode('utf-8'))
        digest.update(b'\x1f')
    return digest.hexdigest()


def reindex_article(cursor, article_id, *, force=False):
    """Пересобрать куски одной статьи. Возвращает словарь с итогом.

    action: 'missing' | 'removed' | 'unchanged' | 'indexed'.
    'removed' — статья перестала быть опубликованной: куски удаляются, иначе
    снятая с публикации статья продолжала бы кормить ответы помощника.
    """
    params = {'article_id': int(article_id)}
    cursor.execute(_SELECT_ARTICLE, params)
    row = cursor.fetchone()
    if not row:
        return {'article_id': article_id, 'action': 'missing', 'chunks': 0}

    status, html, plain, eligible = row
    # ПРИГОДНОСТЬ, а не просто статус. Раньше здесь стояло `status != 'published'`,
    # и этого мало: рубильник ai_opt_out и строгий режим отсекались только на
    # выдаче (wiki/perimeter.py), то есть текст такой статьи всё равно нарезался
    # в куски и уходил в ЭМБЕДДИНГИ — во внешний сервис. А рубильник обещает
    # ровно обратное. Обещание, которое нарушается там, где этого не видно,
    # хуже отсутствующего, поэтому проверка теперь стоит и на входе в индекс.
    if not eligible:
        cursor.execute(_DELETE_CHUNKS, params)
        removed = cursor.rowcount or 0
        cursor.execute(_DELETE_INDEX, params)
        return {'article_id': article_id, 'action': 'removed', 'chunks': 0,
                'deleted': removed, 'status': status}

    fresh_hash = text_hash(html, plain)
    if not force:
        cursor.execute(_SELECT_HASH, params)
        known = cursor.fetchone()
        if known and known[0] == fresh_hash:
            return {'article_id': article_id, 'action': 'unchanged', 'chunks': 0}

    chunks = chunk_article(html, plain)
    cursor.execute(_DELETE_CHUNKS, params)
    for index, chunk in enumerate(chunks):
        cursor.execute(_INSERT_CHUNK, {
            'article_id': int(article_id),
            'chunk_idx': index,
            'heading_path': chunk['heading_path'],
            'text': chunk['text'],
            'requires_ack': bool(chunk['requires_ack']),
            'char_len': len(chunk['text']),
            'text_hash': text_hash(chunk['text']),
        })
    cursor.execute(_UPSERT_INDEX, {'article_id': int(article_id),
                                   'content_hash': fresh_hash,
                                   'chunk_count': len(chunks)})
    return {'article_id': article_id, 'action': 'indexed', 'chunks': len(chunks)}


def reindex_all(cursor, *, force=False):
    """Пересобрать всё, что помощнику вообще позволено читать.

    Берём опубликованные плюс всё, у чего куски уже есть в wiki_ai_chunks:
    периметр помощника всё равно отбрасывает черновики и архив
    (wiki/perimeter.py), и держать их куски в индексе значило бы платить за
    текст, который никогда не попадёт в ответ. Второе слагаемое нужно для
    уборки: статья, у которой сняли публикацию или выключили поддержку ИИ,
    обязана уйти из индекса, а по одному лишь списку опубликованных её уже не
    найти.
    """
    # Берём ВСЕ статьи, а не только опубликованные: reindex_article сам решает,
    # индексировать или вычистить, и статья, у которой только что выключили
    # рубильник, обязана из индекса уйти.
    cursor.execute("""SELECT id FROM wiki_articles
                       WHERE status = 'published'
                          OR id IN (SELECT article_id FROM wiki_ai_chunks)
                       ORDER BY id""")
    published = [row[0] for row in cursor.fetchall()]

    summary = {'indexed': 0, 'unchanged': 0, 'removed': 0, 'chunks': 0}
    for article_id in published:
        result = reindex_article(cursor, article_id, force=force)
        summary[result['action']] = summary.get(result['action'], 0) + 1
        summary['chunks'] += result['chunks']

    cursor.execute(
        """DELETE FROM wiki_ai_chunks
            WHERE article_id NOT IN (SELECT id FROM wiki_articles
                                      WHERE status = 'published')"""
    )
    summary['orphan_chunks_deleted'] = cursor.rowcount or 0
    cursor.execute(
        """DELETE FROM wiki_ai_article_index
            WHERE article_id NOT IN (SELECT id FROM wiki_articles
                                      WHERE status = 'published')"""
    )
    return summary


def index_status(cursor):
    """Состояние индекса — для эндпоинта /ai/status и отладки."""
    cursor.execute(
        """
        SELECT (SELECT count(*) FROM wiki_ai_chunks),
               (SELECT count(*) FROM wiki_ai_article_index),
               (SELECT count(*) FROM wiki_articles WHERE status = 'published'),
               (SELECT count(*) FROM wiki_ai_chunks WHERE requires_ack),
               (SELECT max(indexed_at) FROM wiki_ai_article_index)
        """
    )
    chunks, indexed, published, ack, last = cursor.fetchone()
    return {'chunks': chunks, 'articles_indexed': indexed,
            'articles_published': published, 'chunks_requiring_ack': ack,
            'stale_articles': max(published - indexed, 0),
            'last_indexed_at': last.isoformat() if last else None}
