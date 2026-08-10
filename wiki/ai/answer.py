# -*- coding: utf-8 -*-
"""Ответ помощника: промпт, гейт уточнения, источники и защита от выдумки.

ЦИТАТУ ИЗВЛЕКАЕТ СЕРВЕР, а модель указывает только номера фрагментов. Так стало
после проверки на проде: требование «процитируй дословно» оказалось ненадёжным
механизмом и, что хуже, отбрасывало ВЕРНЫЕ ответы. На вопросе «Офис Астана» поиск
нашёл нужный кусок (близость 0,834, в тексте «Город: Астана; Адрес: Проспект
Сарыарка, 31»), ответ существовал — но модель процитировала строку-метку
«Статья «Адреса офисов», раздел «…»», сверка не нашла её в тексте, и защита
выдала отказ. На соседнем вопросе та же модель цитировала корректно, то есть
механизм срабатывал через раз. Ненадёжная защита хуже отсутствующей: помощник,
отрицающий то, что знает, теряет доверие целиком.

От выдумки теперь защищает ДРУГАЯ проверка, машинная и устойчивая: числа из
ответа обязаны встречаться в переданных фрагментах. Именно в числах живёт риск —
сумма, срок, номер телефона, адрес; а пересказ смысла своими словами перестал
быть поводом придержать ответ.

Три решения ниже опираются на замеры, а не на общие соображения.

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

# Порог, ниже которого кусок в источники не идёт. Совпавшее число весит 5, общее
# слово — 1, порог 4: то есть нужен либо один совпавший факт-число, либо четыре
# общих слова. Порог подобран по мусору, который лез при более мягких правилах: к
# вопросу о сроке аренды подшивалось «Eazy Go — сервис аренды электровелосипедов»
# (одно слово «аренды»), а к длинному ответу про байгу — статья про грузчиков и
# «Баллы приоритета» (три частых слова). Источник, ничего не подтверждающий, хуже
# отсутствующего: он выглядит доказательством, не будучи им.
SUPPORT_FLOOR = 4.0

_WORD = re.compile(r'[^\W\d_]{3,}', re.UNICODE)
_SOURCES_HEADER = re.compile(r'^\s*ИСТОЧНИКИ\s*:?', re.I | re.M)
_FRAGMENT_REF = re.compile(r'\[(\d+)\]')
# Числа от двух знаков: одиночные цифры — это почти всегда нумерация списка.
_NUMBER = re.compile(r'\d[\d\s.,:/-]*\d')

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
ИСТОЧНИКИ: [номера фрагментов, из которых взяты факты]
Например: ИСТОЧНИКИ: [1] [4]

Только номера — цитату подберёт программа сама. Указывай лишь те фрагменты,
факты из которых действительно попали в ответ.
Если ответа нет или ты задаёшь уточняющий вопрос — строку ИСТОЧНИКИ не пиши."""


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
    """Отделить блок ИСТОЧНИКИ от тела ответа и вынуть номера фрагментов."""
    body = str(text or '')
    match = _SOURCES_HEADER.search(body)
    if not match:
        return body.strip(), []
    tail = body[match.end():]
    numbers = []
    for raw in _FRAGMENT_REF.findall(tail):
        value = int(raw)
        if value not in numbers:
            numbers.append(value)
    return body[:match.start()].strip(), numbers


def _content_words(text):
    return set(_WORD.findall(str(text or '').lower()))


def pick_excerpt(chunk_text, answer, *, limit=240):
    """Выбрать из куска строку, которая ближе всего к ответу.

    Цитату подбирает СЕРВЕР, а не модель, и это переделка по результатам проверки
    на проде. Требование «процитируй дословно» оказалось ненадёжным механизмом:
    на вопросе «Офис Астана» поиск нашёл нужный кусок (близость 0,834, в тексте
    буквально «Город: Астана; Адрес: Проспект Сарыарка, 31»), но модель
    процитировала строку-метку «Статья «Адреса офисов», раздел «…»», сверка её в
    тексте не нашла, и защита выбросила ВЕРНЫЙ ответ. На соседнем вопросе та же
    модель цитировала корректно — то есть механизм срабатывал через раз.

    Извлечение на сервере снимает проблему целиком: цитата дословна по
    построению, потому что взята из текста куска программой.
    """
    lines = [line.strip() for line in str(chunk_text or '').splitlines()
             if line.strip()]
    if not lines:
        return ''
    target = _content_words(answer)
    # Числа весят больше слов: именно в них факт, за которым оператор и пришёл.
    # И они работают через язык — на казахский ответ пересечение русских слов
    # нулевое, и без этого признака подбор скатывался к первой строке куска
    # («Условия:» вместо строки со сроком).
    target_numbers = {_digits(token) for token in _NUMBER.findall(str(answer or ''))
                      if len(_digits(token)) >= 2}
    best, best_score = lines[0], -1.0
    for line in lines:
        line_numbers = {_digits(token) for token in _NUMBER.findall(line)}
        score = (2.0 * len(target_numbers & line_numbers)
                 + len(target & _content_words(line))
                 # Длина решает при равенстве: содержательная строка полезнее
                 # огрызка вроде «Условия:».
                 + min(len(line), 400) / 1000.0)
        if score > best_score:
            best, best_score = line, score
    return best if len(best) <= limit else best[:limit].rstrip() + '…'


def _digits(text):
    return re.sub(r'\D', '', str(text or ''))


def ungrounded_numbers(answer, chunks, question=''):
    """Числа из ответа, которых нет ни в одном переданном фрагменте.

    Это и есть машинная защита от выдумки — вместо сверки цитат, которая
    отбрасывала верные ответы. Проверяются числа от трёх знаков: именно в них
    живёт настоящий риск (сумма, срок, номер телефона, адрес), а короткие
    совпадают со нумерацией списков и давали бы ложные срабатывания.
    """
    haystack = _digits(' '.join(chunk.get('text') or '' for chunk in chunks))
    asked = _digits(question)
    bad = []
    for token in _NUMBER.findall(str(answer or '')):
        digits = _digits(token)
        if len(digits) < 3:
            continue
        if digits in haystack or digits in asked:
            continue
        if token.strip() not in bad:
            bad.append(token.strip())
    return bad


def _support_score(chunk, answer):
    """Насколько текст куска подтверждает ответ. Числа весят больше слов."""
    text = chunk.get('text') or ''
    answer_numbers = {_digits(token) for token in _NUMBER.findall(str(answer or ''))
                      if len(_digits(token)) >= 2}
    text_numbers = {_digits(token) for token in _NUMBER.findall(text)}
    return (5.0 * len(answer_numbers & text_numbers)
            + len(_content_words(answer) & _content_words(text)))


def build_sources(cited, chunks, answer):
    """Источники ответа: выбирает СЕРВЕР по доказательству, модель лишь подсказывает.

    Номера модели — подсказка, а не истина. Замер на корпусе: на вопросе о
    минимальном сроке аренды модель верно ответила «14 дней», но сослалась на
    кусок, в котором этого срока нет — под ответом оказывалась цитата про график
    6/1, ничего не подтверждающая. Показывать такую цитату хуже, чем не показывать:
    она выглядит доказательством, не будучи им.

    Поэтому куски ранжируются по совпадению с ответом (числа весят втрое), а
    указание модели даёт лишь надбавку. Выбранный кусок, которого модель не
    называла, помечается attributed — в интерфейсе это бейдж «сопоставлено».
    """
    ranked = sorted(
        ((_support_score(chunk, answer) + (2.0 if index in cited else 0.0), index)
         for index, chunk in enumerate(chunks, start=1)),
        key=lambda item: (-item[0], item[1]))
    used = [index for score, index in ranked if score >= SUPPORT_FLOOR][:3]
    if not used and cited:
        used = [number for number in cited if 1 <= number <= len(chunks)][:1]

    sources = []
    for number in used[:5]:
        chunk = chunks[number - 1]
        sources.append({
            'number': number,
            'quote': pick_excerpt(chunk.get('text'), answer),
            'ok': True,          # цитата извлечена программой — дословна всегда
            'attributed': number not in cited,
            'chunk_id': chunk['chunk_id'],
            'chunk_text_hash': chunk.get('text_hash'),
            'article_id': chunk['article_id'],
            'title': chunk.get('title'),
            'slug': chunk.get('slug'),
            'heading_path': chunk.get('heading_path'),
            'requires_ack': bool(chunk.get('requires_ack')),
        })
    return sources


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
    body, cited = split_sources(text)

    looks_like_refusal = 'в доступных вам статьях этого нет' in body.lower()
    if looks_like_refusal or not body:
        # Отказ идёт без источников: список статей под фразой «этого нет» читается
        # как противоречие и подрывает доверие к самой фразе.
        return {'kind': 'no_answer', 'text': body or NO_ANSWER_TEXT,
                'sources': [], 'notes': [], 'meta': meta}

    invented = ungrounded_numbers(body, usable, question)
    if invented:
        # Единственное, из-за чего ответ теперь придерживается: числа, которых нет
        # ни в одном переданном фрагменте. Прежнее правило («нет подтверждённых
        # цитат») отбрасывало верные ответы — проверка на проде показала это на
        # вопросе «Офис Астана», где нужный кусок был найден и ответ существовал.
        return {'kind': 'no_answer', 'text': NO_ANSWER_TEXT, 'sources': [],
                'notes': [],
                'meta': dict(meta, rejected='числа не найдены в источниках',
                             ungrounded=invented, raw_preview=body[:300])}

    sources = build_sources(cited, usable, body)
    return {'kind': 'answer', 'text': body,
            'sources': sources, 'notes': ack_notice(sources), 'meta': meta}
