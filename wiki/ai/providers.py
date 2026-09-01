# -*- coding: utf-8 -*-
"""Цепочки провайдеров вики.

ЦЕПОЧЕК ДВЕ, и это не дублирование. Ниже описана цепочка ЧАТА (available_chain,
generate): помощник отвечает на вопрос своими словами, там ценится точность
формулировки. У РЕДАКТОРА СТАТЕЙ своя — editor_chain, generate_article: он
переносит документ в статью, там ценится полнота, и модели ведут себя на этих
задачах по-разному вплоть до вдвое. Почему именно так — у _EDITOR_CHAIN, с
замерами.

Порядок и состав — не вкус, а замеры на реальных кусках вики (10.08.2026, по три
попытки на модель, четыре кейса: точный факт, перефразировка, казахский, отказ).
Каждая исключённая модель исключена по конкретной причине.

ПОРЯДОК (первым — самый точный, за ним платный резерв, дальше бесплатный):
  1. vertex:gemini-3-flash-preview       1,6-4,1 с. Считает тот же проект Google
     Cloud, что уже платит за бакеты и эмбеддинги, — постоплатой.
  2. zai:glm-5.3-flash                   1,3-2,8 с в чате, 55 с на сборке статьи.
     ВТОРАЯ ПЛАТНАЯ, добавлена 01.09.2026 после разбора отказа (см. ниже).
  3. gemini:gemini-3.5-flash-lite        1,0-2,0 с, «мышления» нет вовсе.
  4. cloudflare:llama-3.3-70b-fp8-fast   1,8-9,6 с. Потолок СУТОЧНЫЙ: 10 000
     нейронов/день, замерено 75-100 нейронов на вопрос → ~100-130 вопросов.
  5. cloudflare:mistral-small-3.1-24b    2,8-9,7 с, дешевле по нейронам.
  6. groq:openai/gpt-oss-20b             последний резерв ЧАТА, и только чата:
     потолок Groq 8 000 токенов/мин на организацию, а сборка статьи бронирует
     9 000 токенов вывода — она отваливается арифметикой, HTTP 413 «Limit 8000,
     Requested 9073», ещё до всякой нагрузки.
Вернуться к прежней бесплатной цепочке — переменной WIKI_AI_CHAIN, код менять не
нужно. Она же аварийный переключатель: WIKI_AI_CHAIN='zai:glm-5.3-flash'.

ПОЧЕМУ ВТОРЫМ СТОИТ ПЛАТНЫЙ (разбор отказа 01.09.2026). В редактор загрузили
документ Word на 23 КБ и получили 503 «все провайдеры цепочки отказали». Прогон
всех адаптеров боевыми ключами по очереди на настоящем документе показал, что
резерва у вики не было ВООБЩЕ — отказало каждое звено, и каждое по своей причине:
  vertex:gemini-3-flash-preview   HTTP 429 «Resource exhausted. Please try again
                                  later» — тот самый сбой. Он ПЛАВАЮЩИЙ: через
                                  час тот же запрос прошёл за 1-3 с. Именно
                                  поэтому дело не в Vertex, а в отсутствии
                                  резерва: секундная просадка у первого звена
                                  роняла всю кнопку;
  groq:llama-3.3-70b-versatile    HTTP 404, модель снята вендором (в /v1/models
                                  Groq её больше нет) — УБРАНА из цепочки;
  gemini:gemini-3.5-flash-lite    HTTP 429 «prepayment credits are depleted»,
                                  на ключе AI Studio кончилась предоплата;
  gemini:gemini-2.5-flash         HTTP 404 «no longer available to new users»,
                                  зовёт на gemini-3.6-flash — а она исключена
                                  ниже по замерам. УБРАНА из цепочки;
  cloudflare:*                    ключей CLOUDFLARE_* на боевом сервисе нет
                                  вовсе, available_chain() выбрасывает оба звена
                                  ещё до попытки; локально они на документе
                                  отваливаются по таймауту (408 за 120 с);
  groq:openai/gpt-oss-20b         HTTP 413 по потолку TPM, см. выше.
То есть бесплатный резерв, набранный замерами 10.08.2026, за три недели истёк
целиком, и вика жила на одном Vertex. Резерв обязан быть ПЛАТНЫМ: бесплатные
модели снимают и обнуляют без предупреждения, а узнаём мы об этом от редактора,
у которого упала кнопка. GLM на том же документе — 55,3 с, статья разобрана, все
маркеры таблиц на месте, около $0,002 за статью.

ИСКЛЮЧЕНЫ:
  * openrouter/free (авторутер) — на русский вопрос ответил исковерканным
    казахским, а на вопрос без ответа в вике вернул строку «User Safety: safe»
    вместо ответа. Артефакт классификатора в поле ответа;
  * nvidia/nemotron-nano-9b-v2:free — ЛОЖНЫЙ ОТКАЗ: на вопрос, ответ на который
    был в переданном контексте, ответил «в доступных вам статьях этого нет».
    Худший режим отказа из возможных;
  * любые gemma-4 — 9,9-56 с и СМЕСЬ языков (36 % казахских слов при 10 русских
    маркерах в одном ответе);
  * groq:qwen/qwen3.6-27b — сыпет <think> в текст ответа и съедает весь лимит
    вывода, не дойдя до сути;
  * gemini-3.6-flash — мышление не гасится (672-1381 токен несмотря на
    thinkingBudget=0) и 429 «exceeded your current quota» на 4-м запросе.
OpenRouter доступен, но по умолчанию ВЫКЛЮЧЕН: единственная годная там модель
(nemotron-3-ultra-550b) отвечает 2,7-13,7 с и не держит правило языка. Включается
переменной WIKI_AI_CHAIN.
"""

import functools
import json
import logging
import os
import re
import time

# Порядок по умолчанию. Переопределяется WIKI_AI_CHAIN='groq:модель,gemini:модель'.
_DEFAULT_CHAIN = (
    # ПЛАТНАЯ и первая. Счёт идёт в тот же проект Google Cloud, которым уже
    # оплачиваются бакеты и эмбеддинги — постоплатой, без отдельного биллинга.
    # Замер на боевых вопросах (гашение мышления обязательно, см. _call_vertex):
    # 1,6-4,1 с, вход ~1630 токенов, выход 22-290. Отвечает заметно лучше
    # бесплатных: на «а для новичков» перечислила именно новичковые акции с
    # парками и сроками, на «Офис Астана» дала оба офиса с адресами и графиком,
    # тогда как бесплатная переспрашивала.
    # gemini-3.1-pro в цепочку НЕ взята: с погашенным мышлением она осторожничает
    # до бесполезности — на том же вопросе про новичков ответила «нет информации»,
    # хотя информация есть. Дороже втрое и хуже.
    ('vertex', 'gemini-3-flash-preview'),
    # ВТОРАЯ ПЛАТНАЯ и единственный настоящий резерв: см. «ПОЧЕМУ ВТОРЫМ СТОИТ
    # ПЛАТНЫЙ» в шапке. Из всей цепочки только она вывозит сборку статьи —
    # документ 45 КБ, 9 000 токенов вывода, 55,3 с, finish='stop'. Ключ на проде
    # уже есть: тем же ZAI_API_KEY считаются «Оценки ИИ».
    ('zai', 'glm-5.3-flash'),
    ('gemini', 'gemini-3.5-flash-lite'),
    ('cloudflare', '@cf/meta/llama-3.3-70b-instruct-fp8-fast'),
    ('cloudflare', '@cf/mistralai/mistral-small-3.1-24b-instruct'),
    ('groq', 'openai/gpt-oss-20b'),
)

# Моделям с рассуждениями нужен запас, иначе content приходит ПУСТЫМ при
# finish_reason='length': запрос успешен, HTTP 200, ответа нет. Наступал на это.
MAX_TOKENS = int(os.getenv('WIKI_AI_MAX_TOKENS', '2500'))
TIMEOUT = float(os.getenv('WIKI_AI_TIMEOUT', '60'))

# Служебные блоки рассуждений, утекающие в текст ответа у части моделей.
_THINK_BLOCK = re.compile(
    r'<(think|thinking|reasoning)\b[^>]*>.*?</\1>', re.S | re.I)
_ORPHAN_OPEN = re.compile(r'<(think|thinking|reasoning)\b[^>]*>.*\Z', re.S | re.I)
_LEADING_META = re.compile(r'^\s*(?:User Safety:\s*\w+|Here\'s a thinking process:)\s*',
                           re.I)


class ProviderError(RuntimeError):
    """Провайдер не смог ответить — можно пробовать следующего в цепочке."""

    def __init__(self, message, *, status=None, retryable=True):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def _exhausted(what, attempts):
    """Цепочка кончилась: записать причины В ЛОГ и вернуть ошибку для человека.

    Логирование здесь не «на всякий случай». 01.09.2026 редактор получил 503 на
    документе Word, и разобрать инцидент было НЕЧЕМ: log_action вызывается только
    после успеха, обработчик ловит ProviderError и превращает её в 503, а в этом
    модуле не было ни одной строки логирования — в логах Render по всем приметам
    отказа ноль совпадений. Причина у каждого звена своя (404 снятой модели, 429
    кончившейся предоплаты, 413 по потолку токенов), и без записи её видит только
    тот, кто повторит запрос руками.

    В лог уходит ВЕСЬ перечень попыток, в сообщение человеку — обрезанный: его
    роут отдаёт полем detail, а там своя граница в 300 знаков.
    """
    full = json.dumps(attempts, ensure_ascii=False)
    logging.warning('wiki ИИ: %s — %s', what, full)
    return ProviderError('%s: %s' % (what, full[:500]))


def normalize_answer(text):
    """Убрать служебные блоки и разметку из текста ответа.

    Нужен не «на всякий случай»: qwen3.6-27b и часть бесплатных моделей пишут
    рассуждения прямо в ответ, а авторутер OpenRouter однажды вернул
    «User Safety: safe» вместо текста. Оператор такого видеть не должен.

    Markdown здесь НЕ трогаем. Сначала я его сглаживал, потому что рендерера в
    проекте не было и звёздочки доходили до оператора символами. Теперь ответ
    рисуется полноценно (src/components/ui/markdown.jsx: marked + DOMPurify),
    включая таблицы — а таблицы в вики главный формат справочных данных: город,
    цена, срок, парк. Сглаживание такую таблицу разрушало бы ровно там, где она
    нужнее всего.
    """
    cleaned = _THINK_BLOCK.sub('', str(text or ''))
    cleaned = _ORPHAN_OPEN.sub('', cleaned)
    cleaned = _LEADING_META.sub('', cleaned)
    return cleaned.strip()


def _chain():
    raw = (os.getenv('WIKI_AI_CHAIN') or '').strip()
    if raw:
        out = []
        for item in raw.split(','):
            provider, _, model = item.strip().partition(':')
            if provider and model:
                out.append((provider.strip().lower(), model.strip()))
        if out:
            return tuple(out)
    return _DEFAULT_CHAIN


def _with_keys(chain):
    """Отбросить звенья, для которых не задан ключ."""
    keys = {
        'groq': bool(os.getenv('GROQ_API_KEY')),
        'gemini': bool(os.getenv('GEMINI_API_KEY')),
        'cloudflare': bool(os.getenv('CLOUDFLARE_WORKER_AI_KEY')
                           and os.getenv('CLOUDFLARE_ACCOUNT_ID')),
        'openrouter': bool(os.getenv('OPEN_ROUTER_API_KEY')),
        # Vertex ходит сервисным аккаунтом — тем же, что подписывает ссылки на
        # файлы вики и считает эмбеддинги. Отдельного ключа у него нет.
        'vertex': bool(os.getenv('GOOGLE_APPLICATION_CREDENTIALS_CONTENT')),
        'zai': bool(os.getenv('ZAI_API_KEY')),
    }
    return tuple((p, m) for p, m in chain if keys.get(p))


def available_chain():
    """Цепочка ЧАТА, урезанная до провайдеров, у которых есть ключи."""
    return _with_keys(_chain())


# ── Цепочка РЕДАКТОРА СТАТЕЙ ────────────────────────────────────────────────
#
# Отдельная от чата, и различие между ними не вкус, а разные задачи. Чат отвечает
# на вопрос своими словами: там ценится точность формулировки, и первым по
# замерам 10.08.2026 стоит Vertex. Редактор ПЕРЕНОСИТ документ в статью: там
# ценится полнота, и Vertex на этом проваливается.
#
# Замер 01.09.2026 на двух настоящих документах, по два прогона на модель.
# Доля переноса = знаки текста статьи / знаки текста документа:
#
#   документ «Стоимость ИИ-оценки звонков» (8 328 знаков, 3 таблицы)
#     vertex:gemini-3-flash-preview   0,466 и 0,474   1 432 и 1 438 вых. токенов
#     zai:glm-5.3-flash               0,992 и 1,009   2 661 и 2 807
#   документ «Смета расшифровки звонков Основа ОП» (7 693 знака, 6 таблиц)
#     vertex:gemini-3-flash-preview   0,651 и 0,649   1 485 и 1 402
#     zai:glm-5.3-flash               0,992 и 1,037   2 081 и 2 335
#
# Vertex останавливается САМ на ~1 400-1 500 выходных токенах, каким бы ни был
# документ: finish='STOP', а не обрыв по потолку (потолок 9 000, см.
# authoring.MAX_OUTPUT_TOKENS). То есть это не лимит и не наша обработка —
# пошаговый разбор конвейера показал, что после модели не теряется ничего, кроме
# строк «НАЗВАНИЕ:/КРАТКО:», которые и должны уйти. Модель просто пересказывает,
# хотя промпт прямым текстом это запрещает. Правка промпта на GLM ничего не
# меняла (четыре варианта, доля 0,98-1,02 у всех) — лечить надо было выбором
# модели.
#
# Решение владельца 01.09.2026: «если gemini нормально не справляется с этой
# задачей, то лучше к редактору статей полностью использовать glm».
#
# Vertex оставлен ВТОРЫМ, а не выброшен: сжатая статья с предупреждением
# «Текста в статье заметно меньше» (authoring.structure_warnings) — это всё же
# заготовка, которую редактор поправит, а отказ оставил бы его ни с чем.
_EDITOR_CHAIN = (
    ('zai', 'glm-5.3-flash'),
    ('vertex', 'gemini-3-flash-preview'),
)


def editor_chain():
    """Цепочка сборки и правки статьи. Переопределяется WIKI_AI_EDITOR_CHAIN."""
    raw = (os.getenv('WIKI_AI_EDITOR_CHAIN') or '').strip()
    if raw:
        out = []
        for item in raw.split(','):
            provider, _, model = item.strip().partition(':')
            if provider and model:
                out.append((provider.strip().lower(), model.strip()))
        if out:
            return _with_keys(tuple(out))
    return _with_keys(_EDITOR_CHAIN)


# ── адаптеры ────────────────────────────────────────────────────────────────

def _gemini_headers():
    """Ключ Gemini — ЗАГОЛОВКОМ, а не '?key=' в адресе.

    httpx логирует полный URL на уровне INFO, поэтому query-параметр с ключом
    попадал в логи Render открытым текстом (обнаружено 22.08.2026: записи с
    ?key=AIza... лежали там с 20.08). Заголовок в лог не пишется.
    """
    return {'Content-Type': 'application/json',
            'x-goog-api-key': os.environ['GEMINI_API_KEY']}


@functools.lru_cache(maxsize=1)
def _http():
    """Один клиент на процесс: TLS-рукопожатие не оплачивается каждым вопросом.

    Замер 22.08.2026: до Vertex по новому соединению 626 мс, по готовому 155 мс.
    Помощник делает такой запрос на каждый вопрос, и рукопожатие сидело прямо в
    паузе перед ответом. httpx.Client потокобезопасен.
    """
    import httpx

    return httpx.Client(timeout=TIMEOUT,
                        limits=httpx.Limits(max_keepalive_connections=8,
                                            max_connections=16))


def _post(url, payload, headers, params=None, timeout=None):
    """timeout=None — общий срок (WIKI_AI_TIMEOUT). Значение переопределяют там,
    где ждать долго нельзя: голосовому наставнику минута ожидания бессмысленна,
    ему лучше через несколько секунд уйти на следующую модель в цепочке."""
    started = time.time()
    response = _http().post(url, json=payload, headers=headers, params=params,
                            timeout=timeout or TIMEOUT)
    elapsed = time.time() - started
    if response.status_code != 200:
        detail = response.text[:300]
        raise ProviderError(f'HTTP {response.status_code}: {detail}',
                            status=response.status_code)
    return response.json(), elapsed


def _messages(system, user, history):
    out = [{'role': 'system', 'content': system}]
    for turn in history or ():
        role = 'assistant' if turn.get('role') == 'assistant' else 'user'
        text = str(turn.get('text') or '').strip()
        if text:
            out.append({'role': role, 'content': text})
    out.append({'role': 'user', 'content': user})
    return out


def _openai_shape(url, key, model, system, user, extra_headers=None, history=(),
                  max_tokens=None, timeout=None, extra_payload=None):
    """Общая форма для всех OpenAI-совместимых: Groq, OpenRouter, Z.ai.

    extra_payload нужен ровно одному провайдеру — Z.ai требует reasoning_effort
    (см. _call_zai). Отдельная копия этой функции под него разошлась бы с
    оригиналом на первой правке: здесь и разбор choices, и «пустой ответ без
    choices», и подъём HTTP-ошибки в ProviderError.
    """
    payload = {'model': model, 'temperature': 0.1,
               'max_tokens': max_tokens or MAX_TOKENS,
               'messages': _messages(system, user, history)}
    payload.update(extra_payload or {})
    headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
    headers.update(extra_headers or {})
    body, elapsed = _post(url, payload, headers, timeout=timeout)
    choices = body.get('choices') or []
    if not choices:
        raise ProviderError('пустой ответ без choices')
    message = choices[0].get('message') or {}
    return {'text': message.get('content') or '',
            'finish': choices[0].get('finish_reason'),
            'usage': body.get('usage') or {}, 'elapsed': elapsed}


def _call_groq(model, system, user, history=(), max_tokens=None, timeout=None):
    return _openai_shape('https://api.groq.com/openai/v1/chat/completions',
                         os.environ['GROQ_API_KEY'], model, system, user,
                         history=history, max_tokens=max_tokens, timeout=timeout)


def _call_openrouter(model, system, user, history=(), max_tokens=None, timeout=None):
    return _openai_shape('https://openrouter.ai/api/v1/chat/completions',
                         os.environ['OPEN_ROUTER_API_KEY'], model, system, user,
                         extra_headers={'X-Title': 'OTP wiki assistant'},
                         history=history, max_tokens=max_tokens, timeout=timeout)


def _call_cloudflare(model, system, user, history=(), max_tokens=None, timeout=None):
    """Cloudflare отдаёт ТРИ формы ответа — знать надо все.

    Парсер на одну форму даёт ложный «пустой ответ»: на этом я уже ошибся и
    отрапортовал, что модели не отвечают, хотя ответ был в другом поле.
    """
    account = os.environ['CLOUDFLARE_ACCOUNT_ID'].strip()
    url = f'https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}'
    payload = {'temperature': 0.1, 'max_tokens': max_tokens or MAX_TOKENS,
               'messages': _messages(system, user, history)}
    headers = {'Authorization': 'Bearer '
                                + os.environ['CLOUDFLARE_WORKER_AI_KEY'].strip(),
               'Content-Type': 'application/json'}
    body, elapsed = _post(url, payload, headers, timeout=timeout)
    result = body.get('result') or {}

    text, finish = '', None
    if isinstance(result.get('response'), str) and result['response'].strip():
        text = result['response']
    else:
        choices = result.get('choices') or []
        if choices:
            text = (choices[0].get('message') or {}).get('content') or ''
            finish = choices[0].get('finish_reason')
        if not str(text).strip():
            for item in result.get('output') or []:
                for part in item.get('content') or []:
                    if part.get('type') == 'output_text' and part.get('text'):
                        text = part['text']
                        break
    return {'text': text, 'finish': finish,
            'usage': result.get('usage') or {}, 'elapsed': elapsed}


def _call_gemini(model, system, user, history=(), max_tokens=None, timeout=None):
    """Gemini с гашением «мышления» и обязательным откатом на 400.

    На моделях 3.x параметр thinkingConfig отдаёт 400 (он изменился), поэтому
    повтор без него — не подстраховка, а рабочая ветка. Приём тот же, что в
    ai_feed_back_service._gemini_generate_once.
    """
    url = ('https://generativelanguage.googleapis.com/v1beta/models/'
           + model + ':generateContent')
    # У Gemini роль модели называется 'model', а не 'assistant'.
    contents = []
    for turn in history or ():
        text = str(turn.get('text') or '').strip()
        if text:
            role = 'model' if turn.get('role') == 'assistant' else 'user'
            contents.append({'role': role, 'parts': [{'text': text}]})
    contents.append({'role': 'user', 'parts': [{'text': user}]})
    base = {
        'system_instruction': {'parts': [{'text': system}]},
        'contents': contents,
        'generationConfig': {'temperature': 0.1,
                             'maxOutputTokens': max_tokens or MAX_TOKENS},
    }
    last_error = None
    for suppress_thinking in (True, False):
        payload = json.loads(json.dumps(base))
        if suppress_thinking:
            payload['generationConfig']['thinkingConfig'] = {'thinkingBudget': 0}
        try:
            body, elapsed = _post(url, payload, _gemini_headers())
        except ProviderError as error:
            last_error = error
            if error.status == 400 and suppress_thinking:
                continue          # параметр не принят этой моделью — без него
            raise
        candidates = body.get('candidates') or []
        text, finish = '', None
        if candidates:
            finish = candidates[0].get('finishReason')
            for part in ((candidates[0].get('content') or {}).get('parts') or []):
                if part.get('text'):
                    text += part['text']
        usage = body.get('usageMetadata') or {}
        return {'text': text, 'finish': finish,
                'usage': {'prompt_tokens': usage.get('promptTokenCount'),
                          'completion_tokens': usage.get('candidatesTokenCount'),
                          'thoughts_tokens': usage.get('thoughtsTokenCount')},
                'elapsed': elapsed,
                'thinking_suppressed': suppress_thinking}
    raise last_error or ProviderError('gemini не ответил')


# ── Vertex: платные Gemini на счёте, которым уже оплачиваются бакеты ─────────
#
# Отдельный провайдер, а не режим 'gemini', потому что различий три: авторизация
# сервисным аккаунтом вместо ключа, свой адрес и отсутствие бесплатной квоты.
#
# Смысл в биллинге. Ключ AI Studio живёт на бесплатном тарифе, и самая умная
# модель на нём просто недоступна: gemini-3.1-pro-preview отдавал 429 на ПЕРВОМ
# запросе. Через Vertex тот же сервисный аккаунт, что подписывает ссылки на файлы
# и считает эмбеддинги, открывает все модели, а расход попадает в общий счёт
# Google Cloud проекта — постоплатой, вместе с хранилищем. Отдельный биллинг
# заводить не нужно.
VERTEX_REGION = os.getenv('WIKI_AI_VERTEX_REGION', 'global')

_vertex_credentials = None


def _vertex_token():
    """Токен сервисного аккаунта. Обновляется сам, поэтому кредентиалы кешируем."""
    global _vertex_credentials

    if _vertex_credentials is None:
        from google.oauth2 import service_account

        from call_qa import config as qa_config

        info = qa_config.google_sa_info()
        if not info:
            raise ProviderError('нет GOOGLE_APPLICATION_CREDENTIALS_CONTENT',
                                retryable=False)
        _vertex_credentials = service_account.Credentials.from_service_account_info(
            info, scopes=['https://www.googleapis.com/auth/cloud-platform'])
    if not _vertex_credentials.valid:
        import google.auth.transport.requests as gtr

        _vertex_credentials.refresh(gtr.Request())
    return _vertex_credentials.token, _vertex_credentials.project_id


def _call_vertex(model, system, user, history=(), max_tokens=None, timeout=None):
    token, project = _vertex_token()
    region = VERTEX_REGION
    host = ('aiplatform.googleapis.com' if region == 'global'
            else f'{region}-aiplatform.googleapis.com')
    url = (f'https://{host}/v1/projects/{project}/locations/{region}'
           f'/publishers/google/models/{model}:generateContent')

    contents = []
    for turn in history or ():
        text = str(turn.get('text') or '').strip()
        if text:
            role = 'model' if turn.get('role') == 'assistant' else 'user'
            contents.append({'role': role, 'parts': [{'text': text}]})
    contents.append({'role': 'user', 'parts': [{'text': user}]})

    # Гашение «мышления» здесь РАБОТАЕТ, в отличие от ключа AI Studio, где модели
    # 3.x отвергают thinkingBudget с 400. Замер на Vertex: gemini-3.1-pro без
    # гашения 7,9 с и 666 токенов мышления, с гашением 2,7 с и ноль; на реальных
    # вопросах без него модель выжирала весь потолок вывода (2496 из 2500) и
    # отвечала 17 секунд. Мышление тарифицируется как выход, поэтому это разом и
    # скорость, и цена. Откат на запрос без параметра — на случай модели, которая
    # его не примет.
    payload = {
        'system_instruction': {'parts': [{'text': system}]},
        'contents': contents,
        'generationConfig': {'temperature': 0.1,
                             'maxOutputTokens': max_tokens or MAX_TOKENS,
                             'thinkingConfig': {'thinkingBudget': 0}},
    }
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    try:
        body, elapsed = _post(url, payload, headers, timeout=timeout)
    except ProviderError as error:
        if error.status != 400:
            raise
        payload['generationConfig'].pop('thinkingConfig', None)
        body, elapsed = _post(url, payload, headers, timeout=timeout)

    candidates = body.get('candidates') or []
    text, finish = '', None
    if candidates:
        finish = candidates[0].get('finishReason')
        for part in ((candidates[0].get('content') or {}).get('parts') or []):
            if part.get('text'):
                text += part['text']
    usage = body.get('usageMetadata') or {}
    return {'text': text, 'finish': finish, 'elapsed': elapsed,
            'usage': {'prompt_tokens': usage.get('promptTokenCount'),
                      'completion_tokens': usage.get('candidatesTokenCount'),
                      'thoughts_tokens': usage.get('thoughtsTokenCount')}}


# ── Z.ai (GLM): резерв, который в отличие от прочих ТЯНЕТ сборку статьи ──────
#
# Второй в цепочке. Форма запроса OpenAI-совместимая, поэтому своего HTTP-пути у
# него нет — только _openai_shape с одним добавленным полем. Отличий от прочих
# ровно три, и все три обязательные (замеры 01.09.2026 на настоящем SYSTEM_PROMPT
# вики, документ 45 КБ, max_tokens=9000):
#
# 1. «Мышление» ОТКЛЮЧИТЬ НЕЛЬЗЯ, и поле надо посылать ВСЕГДА.
#    thinking={'type':'disabled'} и reasoning_effort='medium' → HTTP 400, код 1210
#    «This model always engages in thinking and cannot be disabled; please use
#    low, high, or max». Ступеней ровно три. Не поставить поле — это НЕ
#    нейтральный выбор: включается умолчание вендора, и тот же запрос идёт 23,0 с
#    с 846 токенами мышления вместо 8,3 с и нуля.
#    Умолчание здесь low, а не 'high' из ZAI_REASONING_EFFORT «Оценок ИИ»:
#    переиспользовать ту переменную значило бы отдать вике настройку ночного
#    прогона звонков. На документе 45 КБ high даёт полную статью (28 205 знаков,
#    93,4 с), но съедает 8 174 токена вывода из 9 000 — следующий документ уедет
#    в обрыв. low — 55,3 с и 5 415 токенов, статья сжата в прозе (сработал порог
#    structure_warnings «текста заметно меньше»), но все 292 числа документа на
#    месте и все таблицы вернулись. Сжатие названо предупреждением и видно
#    редактору, а обрыв молча теряет хвост вместе с таблицами — поэтому low.
# 2. response_format НЕ ставим. Вика ждёт не JSON, а конверт «НАЗВАНИЕ: / КРАТКО:
#    / СТАТЬЯ:» (SYSTEM_PROMPT и _envelope в wiki/ai/authoring.py). Проверено:
#    json_object вендор просто игнорирует — ответ всё равно приходит конвертом.
#    Поле, которое ничего не гарантирует, в запросе шум. В «Оценках ИИ» оно
#    остаётся: там от модели действительно ждут JSON.
# 3. Свой срок ожидания, и он зависит от объёма задачи. Общая минута
#    WIKI_AI_TIMEOUT убивает сборку статьи (55-196 с), а держать пять минут в
#    чате нельзя — там ответ приходит за 1,3-2,8 с. Порог по max_tokens: сборка
#    просит 9 000 (authoring.MAX_OUTPUT_TOKENS), чат — 2 500.
#
# Контекст модели документу не помеха: живьём принято 389 793 входных токена
# одним запросом. Ограничитель другой — потолок ВЫВОДА в 9 000 токенов.
# Цена: около $0,002 за статью, кеш системного промпта включается сам и бесплатен.
ZAI_URL = os.getenv('ZAI_URL', 'https://api.z.ai/api/paas/v4/chat/completions')
ZAI_EFFORT = (os.getenv('WIKI_AI_ZAI_EFFORT') or 'low').strip().lower()
ZAI_TIMEOUT = float(os.getenv('WIKI_AI_ZAI_TIMEOUT', '300'))
_ZAI_EFFORTS = ('low', 'high', 'max')


def _zai_effort():
    return ZAI_EFFORT if ZAI_EFFORT in _ZAI_EFFORTS else 'low'


def _zai_floor(max_tokens):
    """Нижняя граница ожидания: минута — чату, пять минут — сборке статьи."""
    return ZAI_TIMEOUT if (max_tokens or MAX_TOKENS) > MAX_TOKENS else TIMEOUT


def _zai_usage(usage):
    """usage Z.ai → имена, которые уже читают routes_ai и truncation_warning.

    Токены мышления уже входят в completion_tokens, поэтому прибавлять их
    отдельно нельзя — счёт удвоится.
    """
    return {'prompt_tokens': usage.get('prompt_tokens'),
            'completion_tokens': usage.get('completion_tokens'),
            'thoughts_tokens': (usage.get('completion_tokens_details') or {})
            .get('reasoning_tokens'),
            'cached_tokens': (usage.get('prompt_tokens_details') or {})
            .get('cached_tokens')}


def _call_zai(model, system, user, history=(), max_tokens=None, timeout=None):
    result = _openai_shape(
        ZAI_URL, os.environ['ZAI_API_KEY'].strip(), model, system, user,
        history=history, max_tokens=max_tokens,
        # Переданный срок — это ПОТОЛОК вызывающего, но не ниже нашего порога:
        # наставник тренажёра просит 12 с и получит их, а сборка статьи не должна
        # умереть на общей минуте.
        timeout=max(float(timeout or 0), _zai_floor(max_tokens)),
        extra_payload={'reasoning_effort': _zai_effort()})
    result['usage'] = _zai_usage(result['usage'])
    return result


_ADAPTERS = {'groq': _call_groq, 'gemini': _call_gemini,
             'cloudflare': _call_cloudflare, 'openrouter': _call_openrouter,
             'vertex': _call_vertex, 'zai': _call_zai}


# ── Документ целиком: файл уходит в модель как есть ─────────────────────────
#
# Нужно ради ТАБЛИЦ. В PDF таблица — это не структура, а координаты: pypdf
# выдаёт из неё поток слов, и разложить их обратно по колонкам из текста уже
# нельзя. Скан вообще не даёт текстового слоя (importer на таком отказывает
# прямым текстом). Gemini читает PDF и картинку постранично с разметкой, то есть
# видит саму сетку — это единственный способ сдержать обещание «всегда корректно
# понимает структуру документа».
#
# Из цепочки здесь годятся vertex, gemini и zai: Groq и Cloudflare принимают лишь
# текст, и подсунуть им файл значит получить 400 на каждой попытке.
_FILE_MIME = {
    'application/pdf', 'image/png', 'image/jpeg', 'image/webp', 'image/heic',
    'image/heif',
}
_FILE_CAPABLE = ('vertex', 'gemini', 'zai')


def file_capable_chain(chain=None):
    """Цепочка, урезанная до провайдеров, принимающих файл."""
    return tuple((p, m) for p, m in (chain or available_chain())
                 if p in _FILE_CAPABLE)


def _file_parts(system, user, blob, mime):
    import base64

    return {
        'system_instruction': {'parts': [{'text': system}]},
        'contents': [{'role': 'user', 'parts': [
            {'inline_data': {'mime_type': mime,
                             'data': base64.b64encode(blob).decode('ascii')}},
            {'text': user},
        ]}],
    }


def _read_gemini_body(body, elapsed):
    candidates = body.get('candidates') or []
    text, finish = '', None
    if candidates:
        finish = candidates[0].get('finishReason')
        for part in ((candidates[0].get('content') or {}).get('parts') or []):
            if part.get('text'):
                text += part['text']
    usage = body.get('usageMetadata') or {}
    return {'text': text, 'finish': finish, 'elapsed': elapsed,
            'usage': {'prompt_tokens': usage.get('promptTokenCount'),
                      'completion_tokens': usage.get('candidatesTokenCount'),
                      'thoughts_tokens': usage.get('thoughtsTokenCount')}}


def _call_vertex_file(model, system, user, *, blob, mime, max_tokens=None, timeout=None):
    token, project = _vertex_token()
    region = VERTEX_REGION
    host = ('aiplatform.googleapis.com' if region == 'global'
            else f'{region}-aiplatform.googleapis.com')
    url = (f'https://{host}/v1/projects/{project}/locations/{region}'
           f'/publishers/google/models/{model}:generateContent')
    payload = _file_parts(system, user, blob, mime)
    payload['generationConfig'] = {
        'temperature': 0.1, 'maxOutputTokens': max_tokens or MAX_TOKENS,
        'thinkingConfig': {'thinkingBudget': 0},
    }
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    try:
        body, elapsed = _post(url, payload, headers, timeout=timeout)
    except ProviderError as error:
        if error.status != 400:
            raise
        payload['generationConfig'].pop('thinkingConfig', None)
        body, elapsed = _post(url, payload, headers, timeout=timeout)
    return _read_gemini_body(body, elapsed)


def _call_gemini_file(model, system, user, *, blob, mime, max_tokens=None, timeout=None):
    url = ('https://generativelanguage.googleapis.com/v1beta/models/'
           + model + ':generateContent')
    payload = _file_parts(system, user, blob, mime)
    payload['generationConfig'] = {'temperature': 0.1,
                                   'maxOutputTokens': max_tokens or MAX_TOKENS}
    body, elapsed = _post(url, payload, _gemini_headers())
    return _read_gemini_body(body, elapsed)


# PDF нельзя слать блоком image_url — Z.ai отвечает 400 «ошибка формата/разбора
# изображения». У файлов свой блок, у картинок свой; вместе они покрывают весь
# _VISION_MIME роута импорта (pdf, png, jpg, jpeg, webp).
_ZAI_FILE_FIELD = {'application/pdf': 'file_url'}


def _call_zai_file(model, system, user, *, blob, mime, max_tokens=None, timeout=None):
    """Файл в Z.ai. Замеры 01.09.2026: PDF 8,7 с, PNG/JPEG/WEBP 5,3-8,9 с.

    Таблицу со страницы модель собирает сама и числа переносит дословно —
    проверено на тарифной таблице с тремя суммами. Цена страницы PDF считается
    не размером файла, а разметкой: ~7 600 входных токенов на страницу.

    image/heic и image/heif из _FILE_MIME НЕ проверены — их нечем было собрать.
    Роут импорта их и не принимает, но если понадобятся, надо мерить отдельно.
    """
    import base64

    field = _ZAI_FILE_FIELD.get(mime, 'image_url')
    result = _openai_shape(
        ZAI_URL, os.environ['ZAI_API_KEY'].strip(), model, system,
        [{'type': field, field: {'url': 'data:%s;base64,%s'
                                 % (mime, base64.b64encode(blob).decode('ascii'))}},
         {'type': 'text', 'text': user}],
        max_tokens=max_tokens, timeout=max(float(timeout or 0), _zai_floor(max_tokens)),
        extra_payload={'reasoning_effort': _zai_effort()})
    result['usage'] = _zai_usage(result['usage'])
    return result


_FILE_ADAPTERS = {'vertex': _call_vertex_file, 'gemini': _call_gemini_file,
                  'zai': _call_zai_file}


def generate_document(system, user, *, blob, mime, chain=None, max_tokens=None):
    """Ответ по ФАЙЛУ: сам файл уходит в модель, текст из него не извлекается.

    Отдельная функция, а не флаг у generate: цепочка здесь другая (текстовые
    провайдеры файл не примут), и молчаливое падение на них выглядело бы как
    «ИИ недоступен», хотя недоступен ровно один способ вызова.
    """
    mime = str(mime or '').split(';')[0].strip().lower()
    if mime not in _FILE_MIME:
        raise ProviderError('этот тип файла модель не читает: %s' % (mime or '—'),
                            retryable=False)

    chain = file_capable_chain(chain)
    if not chain:
        raise ProviderError('нет провайдера, умеющего читать файл '
                            '(нужен Vertex, ключ Z.ai или ключ Gemini)',
                            retryable=False)

    attempts = []
    for provider, model in chain:
        adapter = _FILE_ADAPTERS.get(provider)
        if adapter is None:
            continue
        try:
            result = adapter(model, system, user, blob=blob, mime=mime,
                             max_tokens=max_tokens)
        except Exception as error:                    # noqa: BLE001
            attempts.append({'provider': provider, 'model': model,
                             'error': str(error)[:200]})
            continue
        text = normalize_answer(result.get('text'))
        if not text:
            attempts.append({'provider': provider, 'model': model,
                             'error': 'пустой ответ',
                             'finish': result.get('finish')})
            continue
        return text, {'provider': provider, 'model': model,
                      'elapsed': round(result.get('elapsed') or 0, 3),
                      'usage': result.get('usage') or {},
                      'finish': result.get('finish'), 'attempts': attempts}

    raise _exhausted('файл не прочитал ни один провайдер', attempts)


def generate(system, user, *, chain=None, history=(), max_tokens=None, timeout=None):
    """Пройти цепочку до первого содержательного ответа.

    Возвращает (текст, метаданные). Пустой ответ — это ОШИБКА провайдера, а не
    результат: модели с рассуждениями возвращают HTTP 200 с пустым content, и
    принять такое за ответ значило бы показать оператору пустой пузырь.
    """
    chain = tuple(chain) if chain else available_chain()
    if not chain:
        raise ProviderError('не настроен ни один провайдер ИИ (ZAI_API_KEY / '
                            'GOOGLE_APPLICATION_CREDENTIALS_CONTENT / '
                            'GEMINI_API_KEY / GROQ_API_KEY / CLOUDFLARE_*)',
                            retryable=False)

    attempts = []
    for provider, model in chain:
        adapter = _ADAPTERS.get(provider)
        if adapter is None:
            attempts.append({'provider': provider, 'model': model,
                             'error': 'неизвестный провайдер'})
            continue
        try:
            # timeout передаём ТОЛЬКО когда он задан: адаптер — это функция с
            # известной сигнатурой, и добавлять ей аргумент без нужды значит
            # ломать всякую свою реализацию, которой этот аргумент не нужен.
            extra = {'timeout': timeout} if timeout else {}
            result = adapter(model, system, user, history,
                             max_tokens=max_tokens, **extra)
        except Exception as error:                    # noqa: BLE001
            attempts.append({'provider': provider, 'model': model,
                             'error': str(error)[:200]})
            continue

        text = normalize_answer(result.get('text'))
        if not text:
            attempts.append({'provider': provider, 'model': model,
                             'error': 'пустой ответ',
                             'finish': result.get('finish')})
            continue
        return text, {'provider': provider, 'model': model,
                      'elapsed': round(result.get('elapsed') or 0, 3),
                      'usage': result.get('usage') or {},
                      'finish': result.get('finish'),
                      'attempts': attempts}

    raise _exhausted('все провайдеры цепочки отказали', attempts)


# ── Двери редактора статей ──────────────────────────────────────────────────
#
# Тонкие обёртки, а не флаг у generate: цепочка редактора отличается от цепочки
# чата, и вызывающему не следует помнить об этом при каждом вызове. Забыть
# передать chain — значит молча собрать статью моделью, которая её вдвое сожмёт,
# и заметить это только по предупреждению в редакторе.
#
# Подпись совпадает с generate/generate_document: они подставляются как
# generate_fn и generate_file_fn в authoring.compose и revise.*.

def generate_article(system, user, **kwargs):
    """Сборка или правка статьи по тексту. Цепочка — editor_chain()."""
    kwargs.setdefault('chain', editor_chain())
    return generate(system, user, **kwargs)


def generate_article_document(system, user, *, blob, mime, **kwargs):
    """То же по файлу (PDF, скан, фото), который модель читает сама."""
    kwargs.setdefault('chain', editor_chain())
    return generate_document(system, user, blob=blob, mime=mime, **kwargs)
