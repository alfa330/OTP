# -*- coding: utf-8 -*-
"""Векторы кусков: получение у провайдера и хранение с адресацией по тексту.

Провайдер НЕ дублируется. call_qa/embeddings/provider.py уже умеет главное —
раздельные роли query и document (у многоязычной модели Vertex это разные
task_type, и путать их значит молча терять качество) и проверку размерности на
каждой границе. Импорт лёгкий: все __init__.py пакета call_qa пусты, а его
config при импорте только читает окружение. Импорт всё равно ленивый, внутри
функции: раздел «Вики» должен подниматься, даже если провайдер сломан или ключа
нет — тогда помощник работает на одной лексике, а не падает весь раздел.

ХРАНЕНИЕ АДРЕСУЕТСЯ ТЕКСТОМ, а не куском. Таблица ключуется хешем текста, и
куски присоединяются к ней по wiki_ai_chunks.text_hash. Три следствия, каждое
измеримое:
  * пересборка индекса (DELETE+INSERT кусков) не сжигает векторы: правка одного
    абзаца в статье из 28 кусков пересчитывает один кусок, а не 28;
  * одинаковые куски разных статей считаются один раз — в корпусе есть
    посимвольные дубли (архивные копии статей 1 и 2);
  * смена провайдера или модели не портит старое: ключ включает provider,
    model и dim, поэтому рядом спокойно лежат векторы двух контрактов.
"""

import functools
import os
import time

_BATCH = 10          # Vertex отдаёт 429 на плотных батчах: замерено на 80-м и
_PAUSE = 1.2         # 90-м куске из 132 при батче 25 без паузы
_RETRIES = 6

# РЕГИОН СВОЙ, не общий с call_qa. Прод стоит во Франкфурте, а call_qa по
# умолчанию считает векторы в asia-southeast1 — каждый вопрос помощника летал в
# Сингапур и обратно. Общую переменную VERTEX_REGION трогать НЕЛЬЗЯ: она входит
# в config_hash контракта, а на этот хеш ключуется база знаний разбора звонков
# (call_qa/rag) — смена региона там означала бы «векторов нет, считай заново».
#
# Здесь она безопасна по двум причинам, обе проверены 22.08.2026:
#   * контракт вики (provider_contract ниже) состоит из provider/model/dim и
#     региона НЕ содержит — ключ хранения не меняется;
#   * europe-west3 отдаёт БИТ-В-БИТ те же векторы, что asia-southeast1: косинус
#     к уже посчитанному индексу 1,00000000 на русском и на казахском. То есть
#     старые векторы и новые сравнимы, пересчитывать ничего не надо.
_QUERY_REGION = os.getenv('WIKI_EMBED_REGION', 'europe-west3')


def provider_contract():
    """Кто и чем считает векторы. Без обращения к сети."""
    from call_qa import config as qa_config

    return {
        'provider': str(qa_config.EMBEDDINGS_PROVIDER),
        'model': str(qa_config.VERTEX_EMBED_MODEL
                     if qa_config.EMBEDDINGS_PROVIDER == 'vertex'
                     else qa_config.SELFHOST_EMBED_MODEL),
        'dim': int(qa_config.EMBED_DIM),
    }


@functools.lru_cache(maxsize=1)
def _provider():
    """Провайдер векторов для вики — с регионом поближе к серверу (см. шапку).

    Кеш обязателен: без него на каждый вопрос заново читался бы сервисный
    аккаунт и заново открывалось соединение, то есть ровно то, ради чего эта
    правка и делалась.
    """
    from call_qa import config as qa_config
    from call_qa.embeddings.provider import VertexEmbeddings, get_provider

    if (qa_config.EMBEDDINGS_PROVIDER != 'vertex'
            or not _QUERY_REGION
            or _QUERY_REGION == qa_config.VERTEX_REGION):
        return get_provider()
    provider = VertexEmbeddings(region=_QUERY_REGION)
    if int(provider.dim) != int(qa_config.EMBED_DIM):
        # Та же проверка, что у общего провайдера: размерность обязана совпасть
        # с индексом, иначе поиск молча сравнивает несравнимое.
        raise RuntimeError('размерность вектора не совпала с индексом')
    return provider


def _with_backoff(call, texts):
    """Повтор с удвоением паузы на 429. Иначе индексация рвётся на середине."""
    import httpx

    delay = 2.0
    for attempt in range(_RETRIES):
        try:
            return call(texts)
        except httpx.HTTPStatusError as error:
            too_many = error.response is not None and error.response.status_code == 429
            if not too_many or attempt == _RETRIES - 1:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError('недостижимо')


def embed_documents(texts):
    provider = _provider()
    return _with_backoff(provider.embed_document, list(texts))


def embed_query(text):
    provider = _provider()
    return _with_backoff(provider.embed_query, [str(text)])[0]


_PENDING_SQL = """
SELECT c.id, c.text_hash, c.heading_path, c.text
  FROM wiki_ai_chunks c
 WHERE NOT EXISTS (SELECT 1 FROM wiki_ai_embeddings e
                    WHERE e.text_hash = c.text_hash
                      AND e.embed_provider = %(provider)s
                      AND e.embed_model = %(model)s
                      AND e.embed_dim = %(dim)s)
 ORDER BY c.article_id, c.chunk_idx
 LIMIT %(limit)s
"""

_INSERT_SQL = """
INSERT INTO wiki_ai_embeddings
       (text_hash, embed_provider, embed_model, embed_dim, embedding)
VALUES (%(text_hash)s, %(provider)s, %(model)s, %(dim)s, %(embedding)s)
ON CONFLICT (text_hash, embed_provider, embed_model, embed_dim) DO NOTHING
"""

_COUNTS_SQL = """
SELECT (SELECT count(*) FROM wiki_ai_chunks),
       (SELECT count(DISTINCT c.text_hash)
          FROM wiki_ai_chunks c
          JOIN wiki_ai_embeddings e ON e.text_hash = c.text_hash
         WHERE e.embed_provider = %(provider)s
           AND e.embed_model = %(model)s
           AND e.embed_dim = %(dim)s),
       (SELECT count(DISTINCT text_hash) FROM wiki_ai_chunks)
"""


def _as_vector(values):
    """Список float → литерал pgvector. Свой, чтобы не тянуть адаптер типа."""
    return '[' + ','.join(repr(float(v)) for v in values) + ']'


def embedding_status(cursor):
    """Сколько кусков уже покрыто векторами текущего контракта."""
    contract = provider_contract()
    cursor.execute(_COUNTS_SQL, contract)
    chunks, embedded_texts, distinct_texts = cursor.fetchone()
    return {'chunks': chunks, 'distinct_texts': distinct_texts,
            'embedded_texts': embedded_texts,
            'pending_texts': max((distinct_texts or 0) - (embedded_texts or 0), 0),
            'contract': contract}


def embed_missing(cursor, limit=50):
    """Досчитать векторы для кусков, у которых их ещё нет.

    Порциями и отдельным вызовом, а НЕ внутри пересборки индекса: обработчик
    роута держит соединение из пула всё время работы (wiki/routes.py открывает
    курсор снаружи обработчика), а пул на 40 соединений делится с SSE аукциона и
    колокола. Индексация 200 кусков с паузами против 429 — это минута с лишним, и
    держать на неё соединение значит рисковать ЧУЖИМИ разделами.
    """
    contract = provider_contract()
    params = dict(contract)
    params['limit'] = max(int(limit), 1)
    cursor.execute(_PENDING_SQL, params)
    rows = cursor.fetchall()
    if not rows:
        return {'embedded': 0, 'pending_after': 0, 'contract': contract}

    # Один текст может встречаться в нескольких куках — считаем его один раз.
    by_hash = {}
    for _chunk_id, text_hash, heading_path, text in rows:
        if text_hash not in by_hash:
            prefix = f'{heading_path}. ' if heading_path else ''
            by_hash[text_hash] = prefix + text

    hashes = list(by_hash)
    embedded = 0
    for start in range(0, len(hashes), _BATCH):
        group = hashes[start:start + _BATCH]
        vectors = embed_documents([by_hash[h] for h in group])
        for text_hash, vector in zip(group, vectors):
            cursor.execute(_INSERT_SQL, {
                'text_hash': text_hash, 'embedding': _as_vector(vector),
                **contract,
            })
            embedded += 1
        if start + _BATCH < len(hashes):
            time.sleep(_PAUSE)

    status = embedding_status(cursor)
    return {'embedded': embedded, 'pending_after': status['pending_texts'],
            'contract': contract}
