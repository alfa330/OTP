# -*- coding: utf-8 -*-
"""Цепочка провайдеров для ответа помощника.

Порядок и состав — не вкус, а замеры на реальных кусках вики (10.08.2026, по три
попытки на модель, четыре кейса: точный факт, перефразировка, казахский, отказ).
Каждая исключённая модель исключена по конкретной причине.

ПОРЯДОК (первым — самый точный, дальше бесплатный резерв):
  1. vertex:gemini-3-flash-preview       1,6-4,1 с. ЕДИНСТВЕННАЯ ПЛАТНАЯ, включена
     владельцем: качество ответа важнее секунды задержки. Считает тот же проект
     Google Cloud, что уже платит за бакеты и эмбеддинги, — постоплатой.
  2. groq:llama-3.3-70b-versatile        0,5-1,1 с. Потолок МИНУТНЫЙ: 12 000
     токенов/мин на всю организацию, то есть ~5 вопросов в минуту.
  3. gemini:gemini-3.5-flash-lite        1,0-2,0 с, «мышления» нет вовсе.
  4. gemini:gemini-2.5-flash             1,1-3,6 с, мышление гасится и это работает.
  5. cloudflare:llama-3.3-70b-fp8-fast   1,8-9,6 с. Потолок СУТОЧНЫЙ: 10 000
     нейронов/день, замерено 75-100 нейронов на вопрос → ~100-130 вопросов.
  6. cloudflare:mistral-small-3.1-24b    2,8-9,7 с, дешевле по нейронам.
Бесплатные остаются резервом и покрывают друг друга по типу лимита: Groq держит
минутный всплеск, Cloudflare добавляет суточный объём. Вернуться к бесплатной
цепочке — переменной WIKI_AI_CHAIN, код менять не нужно.

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

import json
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
    ('groq', 'llama-3.3-70b-versatile'),
    ('gemini', 'gemini-3.5-flash-lite'),
    ('gemini', 'gemini-2.5-flash'),
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


def available_chain():
    """Цепочка, урезанная до провайдеров, у которых есть ключи."""
    keys = {
        'groq': bool(os.getenv('GROQ_API_KEY')),
        'gemini': bool(os.getenv('GEMINI_API_KEY')),
        'cloudflare': bool(os.getenv('CLOUDFLARE_WORKER_AI_KEY')
                           and os.getenv('CLOUDFLARE_ACCOUNT_ID')),
        'openrouter': bool(os.getenv('OPEN_ROUTER_API_KEY')),
        # Vertex ходит сервисным аккаунтом — тем же, что подписывает ссылки на
        # файлы вики и считает эмбеддинги. Отдельного ключа у него нет.
        'vertex': bool(os.getenv('GOOGLE_APPLICATION_CREDENTIALS_CONTENT')),
    }
    return tuple((p, m) for p, m in _chain() if keys.get(p))


# ── адаптеры ────────────────────────────────────────────────────────────────

def _post(url, payload, headers, params=None):
    import httpx

    started = time.time()
    response = httpx.post(url, json=payload, headers=headers, params=params,
                          timeout=TIMEOUT)
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


def _openai_shape(url, key, model, system, user, extra_headers=None, history=()):
    payload = {'model': model, 'temperature': 0.1, 'max_tokens': MAX_TOKENS,
               'messages': _messages(system, user, history)}
    headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
    headers.update(extra_headers or {})
    body, elapsed = _post(url, payload, headers)
    choices = body.get('choices') or []
    if not choices:
        raise ProviderError('пустой ответ без choices')
    message = choices[0].get('message') or {}
    return {'text': message.get('content') or '',
            'finish': choices[0].get('finish_reason'),
            'usage': body.get('usage') or {}, 'elapsed': elapsed}


def _call_groq(model, system, user, history=()):
    return _openai_shape('https://api.groq.com/openai/v1/chat/completions',
                         os.environ['GROQ_API_KEY'], model, system, user,
                         history=history)


def _call_openrouter(model, system, user, history=()):
    return _openai_shape('https://openrouter.ai/api/v1/chat/completions',
                         os.environ['OPEN_ROUTER_API_KEY'], model, system, user,
                         extra_headers={'X-Title': 'OTP wiki assistant'},
                         history=history)


def _call_cloudflare(model, system, user, history=()):
    """Cloudflare отдаёт ТРИ формы ответа — знать надо все.

    Парсер на одну форму даёт ложный «пустой ответ»: на этом я уже ошибся и
    отрапортовал, что модели не отвечают, хотя ответ был в другом поле.
    """
    account = os.environ['CLOUDFLARE_ACCOUNT_ID'].strip()
    url = f'https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}'
    payload = {'temperature': 0.1, 'max_tokens': MAX_TOKENS,
               'messages': _messages(system, user, history)}
    headers = {'Authorization': 'Bearer '
                                + os.environ['CLOUDFLARE_WORKER_AI_KEY'].strip(),
               'Content-Type': 'application/json'}
    body, elapsed = _post(url, payload, headers)
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


def _call_gemini(model, system, user, history=()):
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
        'generationConfig': {'temperature': 0.1, 'maxOutputTokens': MAX_TOKENS},
    }
    last_error = None
    for suppress_thinking in (True, False):
        payload = json.loads(json.dumps(base))
        if suppress_thinking:
            payload['generationConfig']['thinkingConfig'] = {'thinkingBudget': 0}
        try:
            body, elapsed = _post(url, payload, {'Content-Type': 'application/json'},
                                  params={'key': os.environ['GEMINI_API_KEY']})
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


def _call_vertex(model, system, user, history=()):
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
        'generationConfig': {'temperature': 0.1, 'maxOutputTokens': MAX_TOKENS,
                             'thinkingConfig': {'thinkingBudget': 0}},
    }
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    try:
        body, elapsed = _post(url, payload, headers)
    except ProviderError as error:
        if error.status != 400:
            raise
        payload['generationConfig'].pop('thinkingConfig', None)
        body, elapsed = _post(url, payload, headers)

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


_ADAPTERS = {'groq': _call_groq, 'gemini': _call_gemini,
             'cloudflare': _call_cloudflare, 'openrouter': _call_openrouter,
             'vertex': _call_vertex}


def generate(system, user, *, chain=None, history=()):
    """Пройти цепочку до первого содержательного ответа.

    Возвращает (текст, метаданные). Пустой ответ — это ОШИБКА провайдера, а не
    результат: модели с рассуждениями возвращают HTTP 200 с пустым content, и
    принять такое за ответ значило бы показать оператору пустой пузырь.
    """
    chain = tuple(chain) if chain else available_chain()
    if not chain:
        raise ProviderError('не настроен ни один провайдер ИИ '
                            '(GROQ_API_KEY / GEMINI_API_KEY / CLOUDFLARE_*)',
                            retryable=False)

    attempts = []
    for provider, model in chain:
        adapter = _ADAPTERS.get(provider)
        if adapter is None:
            attempts.append({'provider': provider, 'model': model,
                             'error': 'неизвестный провайдер'})
            continue
        try:
            result = adapter(model, system, user, history)
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

    raise ProviderError('все провайдеры цепочки отказали: '
                        + json.dumps(attempts, ensure_ascii=False)[:500])
