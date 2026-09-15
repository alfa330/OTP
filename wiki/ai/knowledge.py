# -*- coding: utf-8 -*-
"""Ответ супервайзера → база знаний: статья, новость и тест (задача #321).

Вторая половина цепочки «Вопросов операторов» (первая — wiki/questions.py):
«…подбирается статья, куда дописать сведения, или предлагается новая → после
подтверждения готовятся правки → публикация → новость об изменении → тест по
новой информации».

ЧТО ЗДЕСЬ НЕ ПИШЕТСЯ ЗАНОВО. Правка существующей статьи — это «правка по
указанию» из редактора (revise.edit_by_instruction): там уже решены таблицы
маркерами, клетки точечными правками и обязательный список изменений, а вторая
дверь к той же модели разошлась бы с первой. Новая статья — сборка из текста
(authoring.compose) по той же причине. Новое здесь только одно — конверт
новости с тестом.

НИЧЕГО НЕ ПУБЛИКУЕТСЯ САМО. Всё, что готовит модель, возвращается супервайзеру
на экран, и публикует он: новость с тестом уходит всему отделу обязательным
окном, и цена ошибки там не в опечатке, а в том, что отдел выучит неверный ответ.

ПОЧЕМУ ТЕСТ РАЗБИРАЕТСЯ ИЗ СТРОК, А НЕ ПРОСИТСЯ JSON. Цепочка провайдеров
неоднородна (wiki/ai/providers.py), и строгий JSON у части моделей ломается на
кавычках внутри вариантов. Конверт из помеченных строк («+» — верный вариант)
переживает любую из них, а разбор всё равно заканчивается проверкой
news.access.normalize_quiz — той же, через которую проходит публикация.
"""

import html
import re

from news import access as news_access
from news.schema import QUIZ_MAX_QUESTIONS, QUIZ_MAX_OPTIONS

from . import authoring
from . import embed as ai_embed
from . import retrieve as ai_retrieve
from . import revise
from .answer import STRICT_FLOOR
from ..sanitize import sanitize_html

MAX_NEWS_TITLE = 120
NEWS_MAX_TOKENS = 1500
# Сколько статей предлагать на выбор. Больше пяти — это уже поиск, а не
# подсказка, и выбирать из длинного списка супервайзер будет дольше, чем найдёт
# статью сам.
TARGET_LIMIT = 5


def _one_line(text):
    return ' '.join(str(text or '').split())


# ─────────────────────────────────────────────────────────────────────────────
# КУДА ДОПИСАТЬ
# ─────────────────────────────────────────────────────────────────────────────

def find_targets(cursor, *, article_ids, question, answer, limit=TARGET_LIMIT):
    """Статьи, в которые по смыслу ложится ответ. (кандидаты, поиск_деградировал).

    Ищем по вопросу ВМЕСТЕ с ответом: сам вопрос помощник уже искал и не нашёл
    ничего выше порога, а ответ приносит слова, которыми тема названа в статьях.
    """
    query = _one_line('%s %s' % (question or '', answer or ''))[:1500]
    try:
        vector = ai_embed.embed_query(query)
    except Exception:                                   # noqa: BLE001
        vector = None                                   # деградация до лексики
    found = ai_retrieve.search_hybrid(cursor, article_ids=article_ids, query=query,
                                      query_vector=vector, limit=12, per_article=1)
    return pick_targets(found['rows'], limit=limit), bool(found['degraded'])


def pick_targets(rows, *, limit=TARGET_LIMIT):
    """Куски выдачи → статьи, по строке на статью, в порядке выдачи."""
    seen, targets = set(), []
    for row in rows or ():
        article_id = row.get('article_id')
        if article_id in seen:
            continue
        seen.add(article_id)
        targets.append({
            'article_id': article_id,
            'title': row.get('title') or '',
            'slug': row.get('slug') or '',
            'heading_path': row.get('heading_path') or '',
            'similarity': row.get('similarity'),
        })
        if len(targets) >= limit:
            break
    return targets


def suggest_target(candidates, *, default_section_id=None):
    """Что предложить по умолчанию: дописать уверенно подходящую статью или завести новую.

    Порог тот же, что у ответа помощника (answer.STRICT_FLOOR): ниже него кусок
    статьи не считается отвечающим на вопрос, и дописывать туда — значит класть
    сведения в статью «про соседнее». Без вектора похожести нет вовсе, и
    лексическое совпадение уверенности не даёт — тогда предлагаем новую статью,
    а кандидаты остаются в списке на выбор.
    """
    for candidate in candidates or ():
        similarity = candidate.get('similarity')
        if candidate.get('can_edit') and similarity is not None and similarity >= STRICT_FLOOR:
            return {'action': 'update', 'article_id': candidate['article_id']}
    return {'action': 'create', 'section_id': default_section_id}


# ─────────────────────────────────────────────────────────────────────────────
# ПРАВКА И СБОРКА СТАТЬИ
# ─────────────────────────────────────────────────────────────────────────────

def update_instruction(question, answer):
    """Указание для правки статьи. Формулировки — как у человека в редакторе."""
    return (
        'Дополни статью сведениями из ответа супервайзера на вопрос оператора. '
        'ВОПРОС ОПЕРАТОРА: «%s». ОТВЕТ СУПЕРВАЙЗЕРА: «%s». '
        'Размести сведения в подходящем по смыслу разделе, а если такого нет — '
        'добавь новый раздел. Пиши как справку для оператора, без упоминания '
        'вопроса, оператора и супервайзера. Если ответ противоречит тому, что уже '
        'написано в статье, ничего не заменяй — вынеси противоречие в ВОПРОСЫ.'
    ) % (_one_line(question), _one_line(answer))


def create_instruction(question):
    """Указание для новой статьи. Вопрос — КОНТЕКСТ указания, а не текст документа.

    Замер на стенде 15.09.2026 (llama-3.3): пока вопрос лежал в документе первым
    абзацем, модель переносила его в статью дословно, вопреки указанию «не
    цитируй». Сборка из документа на то и сборка — всё, что в документе,
    считается материалом.
    """
    return (
        'Документ — ответ супервайзера на вопрос оператора «%s». Собери из него '
        'короткую справочную статью для операторов: понятное название, один-два '
        'раздела с заголовками. Сам вопрос в статью не переноси и об операторе не '
        'пиши. Сведения из ответа переноси дословно, ничего не добавляй от себя.'
    ) % _one_line(question)


def draft_update(*, article, question, answer, generate_fn):
    """Правка существующей статьи: {content, changes, questions, warnings, meta}."""
    return revise.edit_by_instruction(
        current_title=article.get('title') or '',
        current_html=article.get('content') or '',
        instruction=update_instruction(question, answer),
        generate_fn=generate_fn,
        # Числа ответа — законный источник. Без него каждое число из ответа
        # супервайзера («14 дней», «500 ₸») ушло бы в предупреждение «нет в
        # статье», и настоящие выдумки потерялись бы среди ложных.
        extra_sources=answer)


def draft_create(*, question, answer, generate_fn):
    """Новая статья: {title, summary, content, warnings, meta}."""
    paragraphs = [part.strip() for part in str(answer or '').splitlines() if part.strip()]
    source_html = ''.join('<p>%s</p>' % html.escape(part) for part in paragraphs)
    return authoring.compose(
        filename='Ответ супервайзера', kind='текст',
        source_html=source_html, source_text=str(answer or ''),
        generate_fn=generate_fn, instruction=create_instruction(question))


# ─────────────────────────────────────────────────────────────────────────────
# НОВОСТЬ И ТЕСТ
# ─────────────────────────────────────────────────────────────────────────────

NEWS_SYSTEM_PROMPT = """Ты готовишь объявление для операторов колл-центра таксопарка: в базе знаний появились новые сведения, и каждый оператор отдела должен их узнать и подтвердить коротким тестом.

ФОРМАТ ОТВЕТА — ровно три части, каждая со своей строки:
ЗАГОЛОВОК: короткий заголовок новости, до 80 знаков
НОВОСТЬ:
2–4 предложения простым текстом: что теперь известно и как действовать. Только факты из ответа — без приветствия, подписи и общих фраз вроде «операторы должны быть осведомлены».
ТЕСТ:
1. Вопрос по новым сведениям?
- неверный вариант
+ верный вариант
- неверный вариант
2. Следующий вопрос?
…

ПРАВИЛА
1. В тесте 2 или 3 вопроса, в каждом 3 варианта ответа, ровно один верный — он отмечен «+», остальные «-».
2. Вопросы только о новых сведениях из ответа супервайзера, а не об общих знаниях.
3. Числа, суммы, сроки и названия — дословно из сведений. Ничего не додумывай.
4. Неверные варианты правдоподобны, но однозначно неверны по сведениям.
5. Язык — русский. Никакой разметки и никаких пояснений вне трёх частей."""


def build_news_prompt(*, question, answer, article_title, changes=()):
    # Заголовки блоков — без пояснений в скобках, а статья без названия не
    # упоминается вовсе. Замер 15.09.2026 (Cloudflare llama-3.3): при «СТАТЬЯ:
    # «без названия»» и «ОТВЕТ СУПЕРВАЙЗЕРА (новые сведения)» модель три раза из
    # трёх начинала ответ голой строкой «НОВЫЕ СВЕДЕНИЯ» вместо «ЗАГОЛОВОК:».
    parts = []
    if _one_line(article_title):
        parts.append('СТАТЬЯ: «%s»' % _one_line(article_title))
    parts += ['ВОПРОС ОПЕРАТОРА: %s' % _one_line(question),
              'ОТВЕТ СУПЕРВАЙЗЕРА:\n%s' % str(answer or '').strip()]
    lines = [_one_line(change) for change in (changes or ()) if _one_line(change)]
    if lines:
        parts.append('ЧТО ИЗМЕНИЛОСЬ В СТАТЬЕ:\n' + '\n'.join('- ' + line for line in lines[:12]))
    return '\n\n'.join(parts)


_FENCE_RE = re.compile(r'^```[a-z]*[ \t]*$', re.I | re.M)
_TITLE_RE = re.compile(r'^[ \t#]*ЗАГОЛОВОК[ \t]*:[ \t]*(.*)$', re.I | re.M)
_NEWS_RE = re.compile(r'^[ \t#]*НОВОСТЬ[ \t]*:[ \t]*(.*?)(?=^[ \t#]*ТЕСТ[ \t]*:|\Z)',
                      re.I | re.M | re.S)
_TEST_RE = re.compile(r'^[ \t#]*ТЕСТ[ \t]*:[ \t]*(.*)\Z', re.I | re.M | re.S)
_QUESTION_RE = re.compile(r'^\s*(?:вопрос\s*)?\d{1,2}\s*[.)]\s*(.+?)\s*$', re.I)
# «+» и «[x]» — верный вариант; «-», тире, «•», «*» и «[ ]» — неверный.
_OPTION_RE = re.compile(r'^\s*(\+|\[[xх]\]|\[\s?\]|[-−–—•*])\s*(.+?)\s*$', re.I)
_MARKER_LINE = re.compile(r'^[ \t#]*(?:ЗАГОЛОВОК|НОВОСТЬ|ТЕСТ)[ \t]*:', re.I)


def _is_label(line):
    """Строка-метка вроде «НОВЫЕ СВЕДЕНИЯ»: заглавными, коротко, без цифр.

    Модель повторяет такие метки из запроса вместо конверта, и в заголовок или
    текст новости они попасть не должны.
    """
    text = line.strip()
    return (bool(text) and len(text) <= 40 and text == text.upper()
            and any(char.isalpha() for char in text) and not any(char.isdigit() for char in text))


def parse_news_reply(text):
    """Ответ модели → {'title', 'body', 'quiz'}. Проверки здесь нет — только разбор.

    Вопрос с двумя «+» получает correct=None: выбрать за модель, какой из двух
    верный, значило бы угадать, а угаданный ответ в обязательном тесте хуже
    пустого — его выучит весь отдел.
    """
    raw = _FENCE_RE.sub('', str(text or '')).replace('**', '').strip()

    test_match = _TEST_RE.search(raw)
    # Всё, что до «ТЕСТ:», — заголовок и текст. Их ищем только здесь, чтобы
    # формулировка вопроса теста не приняла за конверт новости.
    head = raw[:test_match.start()] if test_match else raw

    title = ''
    title_match = _TITLE_RE.search(head)
    if title_match:
        title = _one_line(title_match.group(1))
        if not title:
            # «ЗАГОЛОВОК:» отдельной строкой — сам заголовок следующей.
            following = [line for line in head[title_match.end():].splitlines() if line.strip()]
            if following and not _MARKER_LINE.match(following[0]) and not _is_label(following[0]):
                title = _one_line(following[0])

    news_match = _NEWS_RE.search(head)
    if news_match:
        body = news_match.group(1).strip()
    else:
        # Без «НОВОСТЬ:» текстом считается проза шапки — без заголовка и меток.
        body = '\n'.join(
            line.strip() for line in head.splitlines()
            if line.strip() and not _MARKER_LINE.match(line) and not _is_label(line)
            and _one_line(line) != title)

    quiz, current, marks = [], None, []

    def close():
        if current is not None:
            current['correct'] = marks[0] if len(marks) == 1 else None

    for line in (test_match.group(1).splitlines() if test_match else ()):
        if not line.strip():
            continue
        option = _OPTION_RE.match(line)
        if option and current is not None:
            if option.group(1) == '+' or option.group(1).lower() in ('[x]', '[х]'):
                marks.append(len(current['options']))
            current['options'].append(_one_line(option.group(2)))
            continue
        question = _QUESTION_RE.match(line)
        if question:
            close()
            current, marks = {'prompt': _one_line(question.group(1)), 'options': [],
                              'correct': None}, []
            quiz.append(current)
            continue
        # Формулировка вопроса, перенесённая на следующую строку.
        if current is not None and not current['options']:
            current['prompt'] = _one_line('%s %s' % (current['prompt'], line))
    close()

    return {'title': title[:MAX_NEWS_TITLE], 'body': body, 'quiz': quiz}


def _with_title(parsed, article_title):
    """Заголовка нет, а статья известна — «Новое в статье «…»».

    Второй вызов модели ради одной строки стоит ~15 секунд на соединении из пула,
    а заголовок супервайзер всё равно видит и правит на экране. Повторный запрос
    остаётся для того, без чего новость не собрать: текста и теста.
    """
    if not parsed['title'] and _one_line(article_title):
        parsed['title'] = ('Новое в статье «%s»' % _one_line(article_title))[:MAX_NEWS_TITLE]
    return parsed


def _reply_problem(parsed):
    if not parsed['title']:
        return 'нет заголовка'
    if not parsed['body']:
        return 'нет текста новости'
    _quiz, problem = news_access.normalize_quiz(parsed['quiz'])
    return problem


def salvage_quiz(quiz):
    """То, что из черновика теста можно показать на экране для ручной правки.

    Проверку не проходит — и не должен: супервайзер доведёт его руками, а
    публикация всё равно пропустит тест только через normalize_quiz.
    """
    kept = []
    for item in (quiz or ())[:QUIZ_MAX_QUESTIONS]:
        options = [option for option in (item.get('options') or ()) if option][:QUIZ_MAX_OPTIONS]
        correct = item.get('correct')
        kept.append({
            'prompt': item.get('prompt') or '',
            'options': options,
            'correct': correct if isinstance(correct, int) and 0 <= correct < len(options) else None,
        })
    return kept


def draft_news(*, question, answer, article_title, changes, generate_fn):
    """Черновик новости с тестом: {title, body, quiz, warnings, meta}."""
    prompt = build_news_prompt(question=question, answer=answer,
                               article_title=article_title, changes=changes)
    text, meta = generate_fn(NEWS_SYSTEM_PROMPT, prompt, max_tokens=NEWS_MAX_TOKENS)
    parsed = _with_title(parse_news_reply(text), article_title)
    problem = _reply_problem(parsed)
    if problem:
        # Одна повторная попытка с названной причиной: модели сбиваются на
        # формате чаще, чем на смысле. Дальше — ручная правка на экране:
        # недособранный тест лучше круга запросов, каждый из которых занимает
        # соединение из пула.
        text, meta = generate_fn(
            NEWS_SYSTEM_PROMPT,
            prompt + '\n\nПредыдущий ответ не принят: %s. Ответь строго в формате '
                     'из трёх частей.' % problem,
            max_tokens=NEWS_MAX_TOKENS)
        parsed = _with_title(parse_news_reply(text), article_title)
        problem = _reply_problem(parsed)

    quiz, quiz_problem = news_access.normalize_quiz(parsed['quiz'])
    warnings = []
    if problem:
        warnings.append('ИИ не собрал новость по формату (%s) — допишите её вручную'
                        % problem)
    if quiz_problem:
        quiz = salvage_quiz(parsed['quiz'])

    # Сверяются заголовок, текст, формулировки и ВЕРНЫЕ варианты. Неверные — нет:
    # «300 ₸» в неверном варианте при «500 ₸» в ответе — это и есть неверный
    # вариант, а не выдумка, и предупреждение на каждом тесте с числами
    # приучило бы предупреждения не читать.
    shown = '\n'.join([parsed['title'], parsed['body']]
                      + ['%s %s' % (item['prompt'],
                                    item['options'][item['correct']]
                                    if isinstance(item.get('correct'), int) else '')
                         for item in quiz])
    invented = revise.invented_numbers(shown, question, answer, article_title,
                                       '\n'.join(str(c) for c in (changes or ())))
    if invented:
        warnings.append('Числа, которых нет в ответе супервайзера: %s — проверьте их'
                        % ', '.join(invented[:8]))
    return {'title': parsed['title'], 'body': parsed['body'], 'quiz': quiz,
            'warnings': warnings, 'meta': meta}


def news_body_html(text):
    """Текст новости из поля → абзацы.

    Разметку супервайзер здесь не пишет: это короткое объявление, а поле на
    экране — простой текст. Экранируем, а не пропускаем как HTML: иначе
    угловая скобка в ответе («<5 минут») съела бы полфразы.
    """
    paragraphs = [part.strip() for part in re.split(r'\r?\n', str(text or '')) if part.strip()]
    return sanitize_html(''.join('<p>%s</p>' % html.escape(part) for part in paragraphs))
