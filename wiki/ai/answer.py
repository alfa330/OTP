# -*- coding: utf-8 -*-
"""Ответ помощника: промпт, гейт уточнения, дословная сверка источников.

Три решения здесь опираются на замеры, а не на общие соображения.

1. ПРАВИЛО ЯЗЫКА обязано явно разрешать перевод контекста. Формулировка «отвечай
   на языке вопроса» слишком слаба: все фрагменты статей русские, и модель
   тянется за языком контекста — на казахский вопрос отвечала по-русски. С
   добавлением «фрагменты могут быть на русском: переводи их содержание» все
   проверенные модели отвечают беглым казахским.

2. ОТКАЗ ДЕЛАЕТ ПОРОГ, А НЕ ПРОМПТ. На вопросе «сколько мне отпускных положено»
   лучшая близость 0,661: куски в контекст не попадают вовсе, и модель отвечает
   отказом просто потому, что ей нечего пересказывать. Это надёжнее любых
   уговоров, поэтому STRICT_FLOOR — часть механики ответа, а не настройка поиска.

3. УТОЧНЯЮЩИЙ ВОПРОС ПРОМПТОМ НЕ ДОСТИГАЕТСЯ. Замер: на заведомо мутное «что с
   машиной» обе проверенные модели вместо вопроса выдали ответ про мойку кузова,
   зацепившись за кусок с близостью 0,725 — правило №4 в промпте не сработало ни
   у одной. Поэтому неоднозначность определяется КОДОМ (should_clarify) до вызова
   модели, а промпт лишь дублирует правило.
"""

import re

# Порог, ниже которого кусок в контекст не кладём. Измерен: см. пункт 2 шапки.
STRICT_FLOOR = 0.72

# Неоднозначность: короткий запрос + невысокая лучшая близость + разброс попаданий
# по несвязанным статьям. Числа с замера «что с машиной»: 3 слова, 0,725, 2 статьи.
CLARIFY_MAX_WORDS = 3
CLARIFY_SIMILARITY = 0.78
CLARIFY_MIN_ARTICLES = 2

_WORD = re.compile(r'[^\W\d_]{3,}', re.UNICODE)
_SOURCES_HEADER = re.compile(r'^\s*ИСТОЧНИКИ\s*:\s*$', re.I | re.M)
_SOURCE_LINE = re.compile(r'^\s*\[(\d+)\]\s*(.+?)\s*$', re.M)

SYSTEM_PROMPT = """Ты — справочный помощник корпоративной вики таксопарка. Отвечаешь операторам колл-центра.

ЖЁСТКИЕ ПРАВИЛА
1. Отвечай ТОЛЬКО по приведённым фрагментам статей. Ничего не додумывай и не
   добавляй из общих знаний.
2. Если во фрагментах ответа нет — ответь одной фразой «В доступных вам статьях
   этого нет» и укажи, к кому обратиться. Не пытайся ответить приблизительно.
3. Коротко и по делу: без вступлений, без пересказа вопроса, без воды и без
   выводов в конце. Сразу суть. Списком, если пунктов несколько.
4. Если вопрос неоднозначен и от уточнения зависит ответ — задай ОДИН уточняющий
   вопрос вместо ответа.
5. ЯЗЫК ОТВЕТА обязан совпадать с языком вопроса. Вопрос на казахском — весь
   ответ на казахском, включая пункты списка. Фрагменты статей могут быть на
   русском: переводи их содержание, но не переходи на русский язык в ответе.
6. Числа, суммы, сроки и названия переноси ДОСЛОВНО из фрагментов. Не округляй и
   не пересчитывай.
7. Если спрашивают КОНКРЕТНОЕ число, сумму, срок или адрес, а во фрагментах его
   нет — первой фразой скажи прямо, что этой величины в доступных статьях нет, и
   только потом добавь, что известно рядом и к кому обратиться. Не выдавай
   смежный факт за ответ на вопрос.

ФОРМАТ ОТВЕТА
Сначала сам ответ. Затем с новой строки ровно так:
ИСТОЧНИКИ:
[номер фрагмента] дословная цитата, подтверждающая ответ

Цитату бери ТОЛЬКО из блока ТЕКСТ соответствующего фрагмента — не из строки с
названием статьи и раздела. Цитата обязана встречаться в тексте слово в слово,
её проверяет программа; перевод цитаты недопустим, даже если отвечаешь на другом
языке. Указывай только те фрагменты, из которых реально взял факты.
Если ответа нет или ты задаёшь уточняющий вопрос — блок ИСТОЧНИКИ не пиши."""


def _squash(text):
    """Нормализация для дословной сверки: как в проверке цитат разбора чатов."""
    return ' '.join(str(text or '').split()).lower()


def meaningful_words(question):
    return _WORD.findall(str(question or '').lower())


def should_clarify(question, chunks):
    """Нужен ли уточняющий вопрос — решает код, а не модель (см. шапку).

    Возвращает (нужно, причина). Причина уходит в журнал: без неё потом не
    понять, почему помощник переспросил.
    """
    words = meaningful_words(question)
    if len(words) > CLARIFY_MAX_WORDS:
        return False, None
    if not chunks:
        return False, None
    top = max((chunk.get('similarity') or 0) for chunk in chunks)
    if top >= CLARIFY_SIMILARITY:
        return False, None
    articles = {chunk['article_id'] for chunk in chunks}
    if len(articles) < CLARIFY_MIN_ARTICLES:
        return False, None
    return True, (f'коротко ({len(words)} сл.), лучшая близость {top:.3f} < '
                  f'{CLARIFY_SIMILARITY}, попадания в {len(articles)} разных статей')


def usable_chunks(chunks, floor=STRICT_FLOOR):
    """Куски, годные для ответа: близость выше порога либо найдены лексикой.

    Лексическое совпадение пропускаем без порога близости намеренно: точный
    термин или номер — сам себе доказательство, а близости у него может не быть
    вовсе (вектор промахивается на редких словах, лексика для этого и нужна).
    """
    out = []
    for chunk in chunks:
        similarity = chunk.get('similarity')
        lexical = 0 in (chunk.get('found_by') or [])
        if lexical or (similarity is not None and similarity >= floor):
            out.append(chunk)
    return out


def build_user_prompt(question, chunks):
    """Пронумерованные фрагменты + вопрос. Номера — ключ к сверке цитат.

    Метка фрагмента отделена от тела строкой «ТЕКСТ:» намеренно. В первом варианте
    название статьи и путь заголовков шли той же строкой, что текст, и модель на
    вопросе про самозанятого процитировала ЗАГОЛОВОК фрагмента: цитата не
    сверилась, и защита превратила верный ответ в отказ. Помощник, отрицающий то,
    что знает, дороже любой галлюцинации — он теряет доверие целиком.
    """
    blocks = []
    for number, chunk in enumerate(chunks, start=1):
        heading = chunk.get('heading_path') or ''
        title = chunk.get('title') or ''
        label = f'Статья «{title}»'
        if heading:
            label += f', раздел «{heading}»'
        blocks.append(f'[{number}] {label}\nТЕКСТ:\n{chunk["text"]}')
    context = '\n\n'.join(blocks) if blocks else '(подходящих фрагментов не найдено)'
    return f'ФРАГМЕНТЫ СТАТЕЙ:\n{context}\n\nВОПРОС: {question}'


def split_sources(text):
    """Отделить блок ИСТОЧНИКИ от тела ответа."""
    match = _SOURCES_HEADER.search(str(text or ''))
    if not match:
        return str(text or '').strip(), []
    body = text[:match.start()].strip()
    tail = text[match.end():]
    claims = [(int(number), quote.strip())
              for number, quote in _SOURCE_LINE.findall(tail)]
    return body, claims


def verify_sources(claims, chunks):
    """Сверить каждую цитату с текстом её фрагмента.

    ПОШТУЧНО, а не «всё или ничего». Готовый валидатор цитат из разбора чатов
    переиспользовать нельзя именно поэтому: он на первом несовпадении отвергает
    весь ответ, а нам нужно помечать негодным один источник и оставлять
    остальные. Плюс он живёт в bot_schedule2 — импорт оттуда дал бы цикл.
    """
    verified = []
    for number, quote in claims:
        if not 1 <= number <= len(chunks):
            verified.append({'number': number, 'quote': quote, 'ok': False,
                             'reason': 'нет такого фрагмента'})
            continue
        chunk = chunks[number - 1]
        if len(_squash(quote)) < 12:
            verified.append({'number': number, 'quote': quote, 'ok': False,
                             'reason': 'цитата слишком короткая'})
            continue
        needle = _squash(quote)
        ok = needle in _squash(chunk['text'])
        # Защита в глубину: цитата из заголовка фрагмента считается подтверждённой
        # СЛАБО. Она не доказывает факт, но и не должна обнулять верный ответ —
        # именно так однажды и вышло, пока метка фрагмента шла строкой с текстом.
        weak = False
        if not ok:
            label = _squash(f"{chunk.get('title') or ''} {chunk.get('heading_path') or ''}")
            if label and needle in label:
                ok, weak = True, True
        verified.append({'number': number, 'quote': quote, 'ok': ok, 'weak': weak,
                         'reason': ('цитата из заголовка, а не из текста' if weak
                                    else None if ok
                                    else 'цитата не найдена во фрагменте'),
                         'chunk_id': chunk['chunk_id'],
                         'article_id': chunk['article_id'],
                         'title': chunk.get('title'),
                         'slug': chunk.get('slug'),
                         'heading_path': chunk.get('heading_path'),
                         'requires_ack': bool(chunk.get('requires_ack'))})
    return verified


NO_ANSWER_TEXT = ('В доступных вам статьях этого нет. Уточните у супервайзера '
                  'или в профильном отделе.')

_ACK_NOTE = ('Этот пункт входит в обязательное ознакомление по статье «{title}» — '
             'подтвердите ознакомление в самой статье.')


def ack_notice(sources):
    """Приписка про обязательное ознакомление — решение владельца 10.08.2026.

    Помощник не обходит процедуру, а загоняет в неё: отвечает по сути и отправляет
    подтвердить. Запись о подтверждении он не трогает, поэтому отчёт «кто
    ознакомлен» остаётся точным.
    """
    titles = []
    for source in sources:
        if source.get('ok') and source.get('requires_ack'):
            title = source.get('title') or ''
            if title and title not in titles:
                titles.append(title)
    return [_ACK_NOTE.format(title=title) for title in titles]


def compose(question, chunks, generate_fn):
    """Собрать ответ. generate_fn(system, user) -> (текст, метаданные).

    Возвращает словарь с kind: 'answer' | 'no_answer' | 'clarify'.
    """
    usable = usable_chunks(chunks)

    if not usable:
        return {'kind': 'no_answer', 'text': NO_ANSWER_TEXT, 'sources': [],
                'notes': [], 'meta': {'reason': 'нет кусков выше порога'}}

    clarify, reason = should_clarify(question, usable)
    if clarify:
        return {'kind': 'clarify',
                'text': ('Уточните вопрос: он допускает разные ответы. Про что '
                         'именно спрашиваете?'),
                'sources': [], 'notes': [],
                'meta': {'reason': reason}}

    text, meta = generate_fn(SYSTEM_PROMPT, build_user_prompt(question, usable))
    body, claims = split_sources(text)
    sources = verify_sources(claims, usable)
    verified = [source for source in sources if source['ok']]

    looks_like_refusal = 'в доступных вам статьях этого нет' in body.lower()
    if not verified and not looks_like_refusal:
        # Ответ без ни одного подтверждённого источника до оператора не доходит:
        # именно так ловится «User Safety: safe» и пересказ общих знаний.
        return {'kind': 'no_answer', 'text': NO_ANSWER_TEXT, 'sources': sources,
                'notes': [], 'meta': dict(meta, rejected='нет подтверждённых цитат',
                                          raw_preview=body[:300])}

    return {'kind': 'no_answer' if looks_like_refusal else 'answer',
            'text': body or NO_ANSWER_TEXT,
            'sources': sources, 'notes': ack_notice(sources), 'meta': meta}
