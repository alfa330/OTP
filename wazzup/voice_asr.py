"""Voice message -> text: Gemini on Vertex, one request per message.

Only the recognition lives here (download, duration, request, answer, guards);
the queue, the worker and the routes are wazzup/voice.py.

Bench of 29.10.2026: 154 real voice messages of the section (26.8 min; Kazakh,
Russian and both in one phrase; median 8 s), six Gemini models on Vertex, each
transcript scored blind against the recording by two judges that listen
(gemini-4-argon, gemini-2.5-pro), 108–112 messages each:

    model                  argon  pro   Kazakh(argon)  $ per 5000 messages
    gemini-3.8-flash        9.00  8.12  9.00           8.4 (one listen)
    gemini-3.1-flash-lite   8.36  7.00  8.03           1.8
    gemini-3.5-transcribe   8.21  7.01  7.56           5.0
    gemini-2.5-flash        7.41  6.09  6.21           2.7
    gemini-3.5-flash-lite   7.21  5.63  6.53           1.9
    gemini-2.5-flash-lite   5.64  4.18  3.94           0.8

3.8 Flash wins 42:15 against the runner-up; at ~150 voice messages a day its
price is a few dollars a month, so quality decides. Gemini on Vertex only — the
service account of the project, never another gateway.

What the bench taught that the code keeps:
* every model may write a whole driver request on silence, wind or a click
  (3.8 Flash on 6 of 10 such recordings); the prompt forbids it and the answer
  carries a speech flag, but only a second listen catches it — an invention
  differs from listen to listen, a heard phrase does not (see transcribe);
* an answer impossibly long for the recording is refused rather than shown;
* a concrete example in the prompt (a plate, a phone) gets copied into the
  transcript of an empty recording — the rules name the formats, not values.
"""
import base64
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit

import requests

# Chosen on the bench of 29.10.2026; the env switch is for an owner's decision,
# not for experiments in production (every value here is a paid model).
MODEL = (os.getenv('WAZZUP_VOICE_MODEL') or 'gemini-3.8-flash').strip()
REGION = 'global'
# Part of what a transcript was made with: stored next to it as `model`.
PROMPT_VERSION = 1

MAX_FILE = 20 * 1024 * 1024
# A file of this length still goes inline in one request; longer is skipped.
MAX_SECONDS = 15 * 60
DOWNLOAD_SECONDS = 30
# 9 of 10 answers come within 11 s, but some requests hang for minutes (4 % on
# the bench); such a request is cut and asked again at once. The answer of a
# long recording takes longer to write, so the limit grows with the audio.
REQUEST_SECONDS = 20
REQUEST_SECONDS_PER_AUDIO_SECOND = 0.5
MAX_REQUEST_SECONDS = 180
# Gemini on Vertex bills 25 tokens per second of audio; the answer of a minute
# of speech is ~250 tokens, so this ceiling covers the longest file accepted.
MAX_OUTPUT_TOKENS = 8192

SYSTEM = """Ты — система дословной расшифровки голосовых сообщений из рабочего WhatsApp-чата
службы такси в Казахстане. Голосовые присылают водители (иногда — клиенты); говорят по-казахски,
по-русски или смешивая языки в одной фразе. Оператор будет ЧИТАТЬ расшифровку вместо прослушивания.

Правила:
1. Пиши ДОСЛОВНО и на языке оригинала: казахскую речь — казахской кириллицей (ә, ғ, қ, ң, ө, ұ, ү, һ, і),
   русскую — по-русски. НИКОГДА не переводи и не пересказывай, сохраняй смешение языков как сказано.
2. Расставь знаки препинания и заглавные буквы, чтобы текст легко читался. Междометия-заминки
   («э-э», «ммм») можно опустить; все остальные слова оставь, даже если фраза построена неправильно.
3. Числа, суммы, номера телефонов, госномера, даты и время пиши цифрами.
4. Неразборчивое место помечай [неразборчиво]. Не додумывай и не добавляй того, чего не было сказано.
5. Если в записи нет речи (тишина, шум, гул, музыка, случайное нажатие) или нельзя разобрать
   ни одного слова — speech=false и пустой text. Никогда не сочиняй текст для такой записи.
6. language — основной язык: "kk", "ru", "mixed" (оба языка) или "other"."""

USER = "Расшифруй это голосовое сообщение."

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "speech": {"type": "BOOLEAN"},
        "language": {"type": "STRING", "enum": ["kk", "ru", "mixed", "other"]},
        "text": {"type": "STRING"},
    },
    "required": ["speech", "language", "text"],
    "propertyOrdering": ["speech", "language", "text"],
}

# USD per 1M tokens on Vertex, online, global endpoint (Cloud Billing SKU catalog,
# 29.10.2026): audio input, text input, output (thinking is billed as output).
PRICES = {
    'gemini-3.5-flash-lite': (0.30, 0.30, 2.50),
    'gemini-3.1-flash-lite': (0.50, 0.25, 1.50),
    'gemini-2.5-flash-lite': (0.30, 0.10, 0.40),
    'gemini-2.5-flash': (1.00, 0.30, 2.50),
    'gemini-3.8-flash': (1.50, 1.50, 7.50),
}

# Normal speech is 2–3 words a second; a transcript far above that for its
# recording was not heard but written (a model filling silence or looping).
MAX_WORDS_PER_SECOND = 6.0
MIN_WORDS_TO_JUDGE_RATE = 8
# Recordings up to this long are heard twice (see transcribe), and the two
# listens must share at least this much of their words.
CHECK_SECONDS = 20.0
MIN_AGREEMENT = 0.5


class VoiceError(Exception):
    """kind: 'retry' (try again later), 'unavailable' (the file is gone),
    'skipped' (will never be transcribed: too long, too big, not audio)."""

    def __init__(self, message, kind='retry', result=None):
        super().__init__(message)
        self.kind = kind
        # What a paid request that still failed cost (usage, model, latency).
        self.result = result


def allowed_url(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and parsed.hostname == 'store.wazzup24.com'
                and parsed.port in (None, 443) and not parsed.username and not parsed.password)
    except (ValueError, TypeError):
        return False


def download(url, *, session_factory=requests.Session, clock=time.monotonic):
    """(bytes, content type) of a stored voice message. Only Wazzup's media host,
    redirects checked hop by hop, a size and a time budget."""
    started = clock()
    with session_factory() as session:
        session.trust_env = False  # never attach netrc credentials to media URLs
        for _ in range(4):
            remaining = DOWNLOAD_SECONDS - (clock() - started)
            if remaining <= 0:
                raise VoiceError('файл загружается слишком долго')
            if not allowed_url(url):
                raise VoiceError('адрес файла не из хранилища Wazzup', 'skipped')
            with session.get(url, stream=True, allow_redirects=False,
                             headers={'Accept-Encoding': 'identity'},
                             timeout=(min(5, remaining), min(15, remaining))) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get('Location', ''))
                    continue
                if response.status_code in (403, 404, 410):
                    raise VoiceError(f'Wazzup не отдаёт файл (HTTP {response.status_code})', 'unavailable')
                if response.status_code != 200:
                    raise VoiceError(f'хранилище Wazzup ответило HTTP {response.status_code}')
                try:
                    length = int(response.headers.get('Content-Length') or 0)
                except (TypeError, ValueError):
                    length = 0
                if length > MAX_FILE:
                    raise VoiceError('файл больше 20 МБ', 'skipped')
                data = bytearray()
                for chunk in response.iter_content(64 * 1024):
                    if clock() - started > DOWNLOAD_SECONDS:
                        raise VoiceError('файл загружается слишком долго')
                    if len(data) + len(chunk) > MAX_FILE:
                        raise VoiceError('файл больше 20 МБ', 'skipped')
                    data.extend(chunk)
                content_type = (response.headers.get('Content-Type') or '').split(';')[0].strip().lower()
                return bytes(data), content_type
    raise VoiceError('слишком много перенаправлений')


_MP3_BITRATES = {
    '1': (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    '2': (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_MP3_RATES = {'1': (44100, 48000, 32000), '2': (22050, 24000, 16000), '2.5': (11025, 12000, 8000)}


def mp3_duration(raw):
    """Seconds of an MPEG Layer III file by walking its frames, or None.

    Wazzup hands every voice message over as MP3 (64 kbit/s), whatever the
    messenger recorded. The length decides what is sent at all and how many
    words the transcript can plausibly hold."""
    pos = 0
    if raw[:3] == b'ID3' and len(raw) >= 10:
        size = (raw[6] << 21) | (raw[7] << 14) | (raw[8] << 7) | raw[9]
        pos = 10 + size + (10 if raw[5] & 0x10 else 0)
    total, frames, n = 0.0, 0, len(raw)
    while pos + 4 <= n:
        b1, b2 = raw[pos + 1], raw[pos + 2]
        if raw[pos] != 0xFF or (b1 & 0xE0) != 0xE0:
            pos += 1
            continue
        version_bits, layer_bits = (b1 >> 3) & 3, (b1 >> 1) & 3
        bitrate_index, rate_index = (b2 >> 4) & 0xF, (b2 >> 2) & 3
        if version_bits == 1 or layer_bits != 1 or bitrate_index in (0, 15) or rate_index == 3:
            pos += 1
            continue
        version = {3: '1', 2: '2', 0: '2.5'}[version_bits]
        bitrate = _MP3_BITRATES['1' if version == '1' else '2'][bitrate_index] * 1000
        rate = _MP3_RATES[version][rate_index]
        length = (144 if version == '1' else 72) * bitrate // rate + ((b2 >> 1) & 1)
        if length <= 4:
            pos += 1
            continue
        total += (1152 if version == '1' else 576) / rate
        frames += 1
        pos += length
    return round(total, 3) if frames else None


_MAGIC_MIME = ((b'ID3', 'audio/mpeg'), (b'OggS', 'audio/ogg'), (b'RIFF', 'audio/wav'),
               (b'fLaC', 'audio/flac'), (b'#!AMR', 'audio/amr'))


def audio_mime(raw, content_type=''):
    """MIME type for the request: by the file's signature first (the store
    answers audio/mpeg for voice messages, but trust the bytes)."""
    for magic, mime in _MAGIC_MIME:
        if raw.startswith(magic):
            return mime
    if len(raw) > 2 and raw[0] == 0xFF and (raw[1] & 0xE0) == 0xE0:
        return 'audio/mpeg'
    if raw[4:8] == b'ftyp':
        return 'audio/mp4'
    if content_type.startswith('audio/'):
        return content_type
    raise VoiceError(f'не аудио ({content_type or "неизвестный тип"})', 'skipped')


def request_body(raw, mime):
    return {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [
            {"inlineData": {"mimeType": mime, "data": base64.b64encode(raw).decode('ascii')}},
            {"text": USER}]}],
        # Reasoning over a recording adds nothing to a verbatim transcript and is
        # billed as output; temperature 0 keeps a retry close to the first answer.
        "generationConfig": {"temperature": 0, "maxOutputTokens": MAX_OUTPUT_TOKENS,
                             "responseMimeType": "application/json", "responseSchema": SCHEMA,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }


_FENCE_RE = re.compile(r'^\s*```(?:json)?\s*(.*?)\s*```\s*$', re.S)
_WORD_RE = re.compile(r'\w+', re.U)
_CONTROL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
# Agreement compares this much of each transcript: a recording heard twice is at
# most CHECK_SECONDS long (~300 characters); the cap keeps an unexpectedly long
# answer from costing the web process seconds of CPU.
AGREEMENT_CHARS = 1500


def parse_answer(answer):
    """{'speech', 'language', 'text'} from a generateContent answer, or VoiceError."""
    candidate = (answer.get('candidates') or [{}])[0]
    finish = candidate.get('finishReason')
    text = ''.join(part.get('text', '') for part in (candidate.get('content') or {}).get('parts', [])
                   if not part.get('thought'))
    if finish == 'MAX_TOKENS':
        raise VoiceError('ответ модели оборван лимитом')
    fenced = _FENCE_RE.match(text)
    try:
        # strict=False: a raw control character inside the text is not a reason to
        # pay for the answer again; it is removed below.
        data = json.loads(fenced.group(1) if fenced else text, strict=False)
    except ValueError:
        raise VoiceError(f'ответ модели не JSON (finishReason={finish})') from None
    if not isinstance(data, dict):
        raise VoiceError('ответ модели не объект')
    # Control characters (NUL among them) go: Postgres refuses NUL in text, and a
    # transcript that cannot be stored would only be paid for again.
    body = ' '.join(_CONTROL_RE.sub(' ', str(data.get('text') or '')).split())
    language = data.get('language') if data.get('language') in ('kk', 'ru', 'mixed', 'other') else None
    return {'speech': bool(data.get('speech')) and bool(body), 'language': language, 'text': body}


def implausible(text, duration):
    """Why the transcript cannot be what was said, or None.

    More words than the recording can hold is text the model wrote, not heard;
    a phrase repeated over and over is a decoding loop."""
    words = _WORD_RE.findall(text.lower())
    if duration and len(words) >= MIN_WORDS_TO_JUDGE_RATE and len(words) / max(duration, 1.0) > MAX_WORDS_PER_SECOND:
        return f'{len(words)} слов на {duration:.1f} с записи'
    if len(words) >= 40:
        grams = [' '.join(words[i:i + 4]) for i in range(len(words) - 3)]
        top = max(grams.count(g) for g in set(grams))
        if top >= 6 and top * 4 > len(words) * 0.5:
            return 'ответ зациклен'
    return None


def usage_tokens(usage):
    """(audio, text, output, thinking) input/output tokens of a usage block."""
    usage = usage or {}
    details = {d.get('modality'): int(d.get('tokenCount') or 0) for d in usage.get('promptTokensDetails') or []}
    return (details.get('AUDIO', 0), details.get('TEXT', 0),
            int(usage.get('candidatesTokenCount') or 0), int(usage.get('thoughtsTokenCount') or 0))


def estimate_cost(model, usage):
    """USD by the Vertex price list; None for a model without known prices."""
    prices = PRICES.get(model)
    if prices is None or not usage:
        return None
    audio, text, output, thinking = usage_tokens(usage)
    return round((audio * prices[0] + text * prices[1] + (output + thinking) * prices[2]) / 1_000_000, 8)


def vertex_configured():
    """The project's service account is there (Render env; locally the dev file)."""
    try:
        from call_qa import config
        return bool(config.env('GOOGLE_APPLICATION_CREDENTIALS_CONTENT'))
    except Exception:
        return False


def request_seconds(duration):
    return min(MAX_REQUEST_SECONDS, REQUEST_SECONDS + REQUEST_SECONDS_PER_AUDIO_SECOND * (duration or 60))


def _generate(model, body, timeout=REQUEST_SECONDS):
    from call_qa import providers
    import httpx
    try:
        try:
            return providers.vertex_generate(model, body, timeout=timeout, tries=2, region=REGION)
        except httpx.TimeoutException:
            # A hung request is asked again at once; the answer usually comes in seconds.
            return providers.vertex_generate(model, body, timeout=timeout, tries=2, region=REGION)
    except httpx.TimeoutException:
        raise VoiceError(f'Vertex не ответил за {timeout:.0f} с') from None
    except providers.VertexError as error:
        # A model that refuses to switch reasoning off answers 400 to the field.
        if (error.status == 400 and 'thinking' in str(error.detail).lower()
                and body['generationConfig'].pop('thinkingConfig', None) is not None):
            return _generate(model, body, timeout)
        if error.status in (400, 403, 404):
            raise VoiceError(f'Vertex отказал (HTTP {error.status})') from None
        raise VoiceError(f'Vertex недоступен (HTTP {error.status})') from None


def agreement(first, second):
    """How much two transcripts of one recording say the same: 1 − edit distance
    over the longer one, by letters of their words (1.0 — the same, 0 — nothing
    in common). By letters, not words: «Ассалаумағалейкум» and «…күм» are one
    greeting. On the bench real pairs never fell below 0.68, invented ones never
    rose above 0.34."""
    a, b = (' '.join(_WORD_RE.findall(text.lower().replace('ё', 'е')))[:AGREEMENT_CHARS]
            for text in (first, second))
    if len(a) < len(b):
        a, b = b, a
    if not a:
        return 1.0
    row = list(range(len(b) + 1))
    for i, word in enumerate(a, 1):
        previous, row[0] = row[0], i
        for j, other in enumerate(b, 1):
            previous, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, previous + (word != other))
    return max(0.0, 1 - row[len(b)] / len(a))


def _listen(model, raw, mime, generate, clock):
    """One paid request: (what it cost, parsed answer). A broken answer raises
    VoiceError carrying its cost."""
    started = clock()
    answer = generate(model, request_body(raw, mime))
    usage = answer.get('usageMetadata') or {}
    paid = {'model_version': answer.get('modelVersion'), 'usage': usage,
            'latency_ms': round((clock() - started) * 1000), 'cost': estimate_cost(model, usage)}
    try:
        return paid, parse_answer(answer)
    except VoiceError as error:
        error.result = dict(paid, model=model, tokens=usage_tokens(usage))
        raise


def _merge_paid(model, duration, listens):
    """What every listen of the message cost, as one record (tokens and cost summed,
    each raw usage block kept)."""
    tokens = [usage_tokens(paid['usage']) for paid in listens]
    costs = [paid['cost'] for paid in listens]
    return {'duration': duration, 'model': model, 'model_version': listens[0]['model_version'],
            'usage': listens[0]['usage'] if len(listens) == 1 else {'listens': [p['usage'] for p in listens]},
            'tokens': tuple(sum(t[i] for t in tokens) for i in range(4)),
            'latency_ms': max(paid['latency_ms'] for paid in listens),
            'cost': None if None in costs else round(sum(costs), 8)}


def _listen_twice(model, raw, mime, generate, clock, duration, first=None):
    """Both listens of a short recording, in parallel — or only the second, when
    the first is already made. A failed one raises VoiceError carrying the cost
    of both."""
    outcomes, calls = [], []
    if first is None:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_listen, model, raw, mime, generate, clock) for _ in range(2)]
        calls = [future.result for future in futures]
    else:
        outcomes.append(first)
        calls = [lambda: _listen(model, raw, mime, generate, clock)]
    for call in calls:
        try:
            outcomes.append(call())
        except VoiceError as error:
            outcomes.append(error)
    failed = next((o for o in outcomes if not isinstance(o, tuple)), None)
    if failed is not None:
        spent = [p for p in (o[0] if isinstance(o, tuple) else o.result for o in outcomes) if p]
        failed.result = _merge_paid(model, duration, spent) if spent else None
        raise failed
    return outcomes


def transcribe(raw, content_type='', *, model=None, generate=None, clock=time.perf_counter):
    """Recognize one stored voice message.

    A recording up to CHECK_SECONDS long is heard twice, in parallel: its text is
    shown only when both listens heard the same words. An invented phrase differs
    from listen to listen, a heard one does not (bench 29.10.2026). Longer
    recordings are heard once — they are speech, and a model writing over silence
    does it on short ones. A file whose length cannot be read from its frames (not
    MP3 — Wazzup hands voice over as MP3) is heard once first; its length then
    comes from the audio tokens and decides whether to hear it again.

    Returns {'status': 'ready'|'no_speech', 'text', 'language', 'duration', 'model',
    'model_version', 'usage', 'tokens', 'latency_ms', 'cost', 'error'}; raises
    VoiceError (with what was already paid in .result)."""
    model = model or MODEL
    mime = audio_mime(raw, content_type)
    duration = mp3_duration(raw) if mime == 'audio/mpeg' else None
    if duration is not None and duration > MAX_SECONDS:
        raise VoiceError(f'запись длиннее {MAX_SECONDS // 60} минут', 'skipped')
    if generate is None:
        timeout = request_seconds(duration)
        generate = lambda model, body: _generate(model, body, timeout)  # noqa: E731
    if duration is not None and duration <= CHECK_SECONDS:
        outcomes = _listen_twice(model, raw, mime, generate, clock, duration)
    else:
        outcomes = [_listen(model, raw, mime, generate, clock)]
        if duration is None:
            # Gemini bills 25 tokens per second of audio.
            audio_tokens = usage_tokens(outcomes[0][0]['usage'])[0]
            duration = round(audio_tokens / 25.0, 3) if audio_tokens else None
            if duration is None or duration <= CHECK_SECONDS:
                outcomes = _listen_twice(model, raw, mime, generate, clock, duration, first=outcomes[0])
    twice = len(outcomes) == 2
    paid = _merge_paid(model, duration, [outcome[0] for outcome in outcomes])
    heard = []
    for _, result in outcomes:
        reason = implausible(result['text'], duration) if result['speech'] else None
        if reason and not twice:
            raise VoiceError(f'расшифровка не похожа на запись: {reason}', result=paid)
        heard.append(result if result['speech'] and not reason else None)
    first = outcomes[0][1]
    if all(heard) and (not twice or agreement(heard[0]['text'], heard[1]['text']) >= MIN_AGREEMENT):
        return dict(paid, status='ready', text=first['text'], language=first['language'], error=None)
    note = None
    if any(heard):
        # One listen heard words the other did not, or both heard different words:
        # nothing an operator could rely on.
        note = 'два прослушивания не совпали'
    return dict(paid, status='no_speech', text=None, language=first['language'], error=note)
