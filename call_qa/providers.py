"""Провайдеры LLM для оценки: Anthropic (Claude), Vertex (Gemini) и Z.ai (GLM).

Провайдер выбирается ПО ИМЕНИ МОДЕЛИ, а не отдельным флагом: модель уже входит в
`evaluation_fingerprint`, лежит в `ai_review_cache` и в `ai_evaluation_runs.model`,
поэтому второй независимый переключатель означал бы два источника правды и оценки,
подписанные не тем провайдером. Достаточно задать `AI_QA_MODEL_BULK=glm-5.3-flash`.

Штатный провайдер с 28.08.2026 — Z.ai; его особенности собраны в разделе «Z.ai (GLM)»
внизу файла, и читать их надо до любой правки: у этой модели нельзя выключить
«мышление», нет строгой JSON-схемы и нет пакетного режима.

Почему Vertex, а не ключ AI Studio: у сервисного аккаунта нет постоянного ключа
(короткий OAuth-токен в заголовке), расход попадает в общий счёт Google Cloud проекта,
и на ключе AI Studio нужные модели просто недоступны — см. wiki/ai/providers.py.

Замеры 24.08.2026 на 24 звонках Основа ОП (тот же промпт и та же схема, что у Claude),
из которых выросли значения по умолчанию:

* `gemini-3.7-flash` — лучшее совпадение штрафующих вердиктов с Opus 4.8 (84,8 %),
  1 603 выходных токена, 24 с на звонок. Поэтому он и стоит умолчанием.
* «Мышление» тарифицируется как ВЫХОД и у `gemini-3.1-pro` не гасится вовсе
  (3 510 токенов сверх ответа, 60 с) — поэтому гашение включено по умолчанию, а
  откат на запрос без параметра обязателен: на части моделей он отдаёт 400.
* Неявный кеш промпта СРАБАТЫВАЕТ ЧЕРЕЗ РАЗ (3 попадания из 9, включая промах на двух
  одинаковых запросах подряд). Планировать на него нельзя. Явный `cachedContents`
  даёт попадание 4 155 токенов из ~5 250 на КАЖДОМ запросе — поэтому системный блок
  кешируется явно, а при любой осечке запрос повторяется целиком.
* Структурный вывод соблюдается: 18 критериев из 18 во всех 120 прогонах. Но схему
  надо чистить: Vertex не принимает `additionalProperties`.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time

import httpx

from . import config

ANTHROPIC = "anthropic"
VERTEX = "vertex"
ZAI = "zai"

# Префикс имени модели → провайдер. Порядок проверки не важен: префиксы не пересекаются.
_BY_PREFIX = (("gemini", VERTEX), ("glm", ZAI))

# Формы ответа Vertex, на которых имеет смысл повторить: 429 — общая квота проекта на
# модель (ловил на плотном прогоне), 499/500/503 — обрывы на стороне Google.
# У Z.ai те же коды плюс собственные 1302 (превышен лимит) и 1305 (сервис перегружен),
# оба приходят по HTTP 429.
_RETRYABLE = (429, 499, 500, 502, 503, 504)


def provider_for(model: str) -> str:
    """Провайдер по имени модели. Всё, что не Gemini и не GLM, считается Anthropic —
    так старые записи (`model='claude-opus-4-8+claude-opus-4-8'`) остаются валидными."""
    name = str(model or "").lower()
    for prefix, provider in _BY_PREFIX:
        if name.startswith(prefix):
            return provider
    return ANTHROPIC


def provider_for_tag(model_tag: str) -> str:
    """Провайдер по составному тегу `bulk+hard`, который лежит в `CLAUDE_MODEL`.

    Смешанная пара подписывается ANTHROPIC: это не штатный режим, и безопаснее
    подписать прогон прежним провайдером, чем объявить его целиком чужим.
    """
    parts = [p for p in str(model_tag or "").split("+") if p]
    if not parts:
        return ANTHROPIC
    first = provider_for(parts[0])
    return first if all(provider_for(p) == first for p in parts) else ANTHROPIC


# ── авторизация и соединение ────────────────────────────────────────────────

class _Vertex:
    """Один сервисный аккаунт и одно TLS-соединение на процесс.

    Клиент постоянный не для красоты: рукопожатие к Vertex стоит около 470 мс
    (замер 22.08.2026: 626 мс по новому соединению против 155 мс по готовому), и на
    интерактивной оценке эта задержка видна оператору. httpx.Client потокобезопасен.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._creds = None
        self._authreq = None
        self._project = None
        self._client = None
        self._caches: dict[str, dict] = {}

    def _ensure_creds(self):
        if self._creds is not None:
            return
        from google.oauth2 import service_account
        import google.auth.transport.requests as gtr

        sa = config.google_sa_info()
        if not sa:
            raise RuntimeError("нет GOOGLE_APPLICATION_CREDENTIALS_CONTENT для Vertex")
        self._creds = service_account.Credentials.from_service_account_info(
            sa, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self._authreq = gtr.Request()
        self._project = sa["project_id"]

    def token(self) -> tuple[str, str]:
        with self._lock:
            self._ensure_creds()
            if not self._creds.valid:
                self._creds.refresh(self._authreq)
            return self._creds.token, self._project

    def http(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(
                    timeout=config.VERTEX_TIMEOUT,
                    limits=httpx.Limits(max_keepalive_connections=8, max_connections=16))
            return self._client

    def headers(self) -> dict:
        token, _ = self.token()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def base(self, region: str) -> str:
        host = ("aiplatform.googleapis.com" if region == "global"
                else f"{region}-aiplatform.googleapis.com")
        _, project = self.token()
        return f"https://{host}/v1beta1/projects/{project}/locations/{region}"


_VERTEX = _Vertex()


def _post(url: str, payload: dict, *, timeout: float, tries: int) -> dict:
    """POST с бэкоффом по повторяемым кодам. Без него плотный прогон теряет запросы:
    на бенче 24.08 без повторов до половины заявок падало в 429 «Resource exhausted»."""
    last = None
    for attempt in range(max(1, tries)):
        response = _VERTEX.http().post(url, json=payload, headers=_VERTEX.headers(),
                                       timeout=timeout)
        if response.status_code == 200:
            return response.json()
        last = response
        if response.status_code in _RETRYABLE and attempt + 1 < tries:
            time.sleep(config.VERTEX_RETRY_BASE_S * (attempt + 1))
            continue
        break
    raise VertexError(last.status_code if last is not None else 0,
                      last.text[:400] if last is not None else "нет ответа")


class VertexError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"Vertex HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


# ── схема ответа ────────────────────────────────────────────────────────────

_SCHEMA_KEYS = ("type", "properties", "required", "items", "enum", "description",
                "nullable", "format", "minimum", "maximum", "propertyOrdering")


def gemini_schema(schema: dict) -> dict:
    """JSON-схема в том виде, который принимает Vertex.

    `additionalProperties` (он же в нашей схеме стоит `False` на каждом объекте)
    Vertex отвергает с 400. Выкидываем именно неизвестные ключи, а не «всё, кроме
    type/properties»: `enum` у вердикта и `required` — это и есть та часть контракта,
    ради которой структурный вывод затевался.
    """
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key not in _SCHEMA_KEYS:
            continue
        if key == "properties" and isinstance(value, dict):
            out[key] = {k: gemini_schema(v) for k, v in value.items()}
        elif key == "items":
            out[key] = gemini_schema(value)
        else:
            out[key] = value
    return out


# ── явный кеш системного блока ──────────────────────────────────────────────

def _cache_key(model: str, region: str, system: str) -> str:
    digest = hashlib.sha256(system.encode("utf-8")).hexdigest()
    return f"{region}:{model}:{digest}"


def _cached_content_name(model: str, region: str, system: str, ttl_seconds: int) -> str | None:
    """Имя `cachedContents` для системного блока, создавая его при необходимости.

    Возвращает None при любой неудаче: кеш — это оптимизация цены, а не условие
    работы. Промах в худшем случае стоит полной ставки за 4 155 токенов, отказ
    оценивать звонок стоит несравнимо дороже.
    """
    key = _cache_key(model, region, system)
    entry = _VERTEX._caches.get(key)
    now = time.time()
    if entry and entry["expires_at"] > now + 60:
        return entry["name"]
    _, project = _VERTEX.token()
    payload = {
        "model": f"projects/{project}/locations/{region}/publishers/google/models/{model}",
        "systemInstruction": {"parts": [{"text": system}]},
        "ttl": f"{int(ttl_seconds)}s",
    }
    try:
        body = _post(f"{_VERTEX.base(region)}/cachedContents", payload,
                     timeout=60.0, tries=2)
    except Exception:
        return None
    name = body.get("name")
    if not name:
        return None
    _VERTEX._caches[key] = {"name": name, "expires_at": now + ttl_seconds}
    return name


def _forget_cache(name: str) -> None:
    for key, entry in list(_VERTEX._caches.items()):
        if entry.get("name") == name:
            _VERTEX._caches.pop(key, None)


# ── контракт llm.py ─────────────────────────────────────────────────────────

def build_body(*, model, system, user, schema, max_tokens=8000, cache_system=False,
               cache_ttl=None, effort=None, thinking=None) -> dict:
    """Тело запроса для любого не-Anthropic провайдера. Кто именно — решает имя модели.

    Anthropic сюда не попадает: его тело собирает сам llm.build_body, потому что
    именно его форма и есть общий контракт (батч, манифест, разбор ответа).
    """
    builder = _zai_build_body if provider_for(model) == ZAI else _vertex_build_body
    return builder(model=model, system=system, user=user, schema=schema,
                   max_tokens=max_tokens, cache_system=cache_system,
                   cache_ttl=cache_ttl, effort=effort, thinking=thinking)


def post_body(body: dict, *, timeout=120.0, include_meta=False) -> dict:
    """Отправка тела, собранного build_body(). Адресат берётся из маркера `_provider`,
    а не из текущей конфигурации: замороженный в манифесте батча запрос обязан уйти
    тому же провайдеру, который его собрал, даже если умолчание с тех пор сменили."""
    sender = _zai_post_body if body.get("_provider") == ZAI else _vertex_post_body
    return sender(body, timeout=timeout, include_meta=include_meta)


def _vertex_build_body(*, model, system, user, schema, max_tokens=8000, cache_system=False,
                       cache_ttl=None, effort=None, thinking=None) -> dict:
    """Тело запроса Vertex в той же форме вызова, что и у Anthropic.

    Помечено `_provider`, чтобы `llm.post_body` знал, куда его отправлять, и чтобы
    замороженный в манифесте батча запрос нельзя было случайно отправить не тому
    провайдеру. `_system`/`_cache_ttl` хранятся отдельно от payload: системный блок
    уезжает либо в `system_instruction`, либо в явный кеш — решается в момент отправки,
    когда известно, жив ли кеш.
    """
    if not isinstance(user, str):
        raise NotImplementedError(
            "Vertex-провайдер принимает только текстовое сообщение; "
            "вложения чатов остаются на Claude (call_qa/media.py)")
    payload = {
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": config.VERTEX_TEMPERATURE,
            "maxOutputTokens": int(max_tokens),
            "responseMimeType": "application/json",
            "responseSchema": gemini_schema(schema),
        },
    }
    budget = config.VERTEX_THINKING_BUDGET if thinking is None else thinking
    if budget is not None:
        payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": int(budget)}
    return {
        "_provider": VERTEX,
        "model": model,
        "region": config.VERTEX_LLM_REGION,
        "_system": system,
        "_cache_system": bool(cache_system) and config.VERTEX_EXPLICIT_CACHE,
        "_cache_ttl_s": _ttl_seconds(cache_ttl),
        "payload": payload,
    }


def _ttl_seconds(cache_ttl) -> int:
    """'1h'/'5m' Anthropic → секунды Vertex. Пусто = час: явный кеш живёт временем,
    а не числом обращений, и часовая запись у Vertex не дороже пятиминутной
    (в отличие от Anthropic, где час стоит 2× против 1,25×)."""
    raw = str(cache_ttl or "").strip().lower()
    if raw.endswith("m") and raw[:-1].isdigit():
        return max(60, int(raw[:-1]) * 60)
    if raw.endswith("s") and raw[:-1].isdigit():
        return max(60, int(raw[:-1]))
    if raw.endswith("h") and raw[:-1].isdigit():
        return int(raw[:-1]) * 3600
    return config.VERTEX_CACHE_TTL_S


def _extract_text(body: dict) -> str:
    candidates = body.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(part.get("text", "") for part in parts if part.get("text"))


def _usage(body: dict) -> dict:
    """usageMetadata Vertex → имена Anthropic, которые уже читают runtime_store и батч.

    Токены «мышления» приплюсованы к выходу намеренно: они так и тарифицируются, и
    без этого смета по журналу прогонов занижала бы счёт втрое на моделях,
    у которых гашение не срабатывает.
    """
    u = body.get("usageMetadata") or {}
    cached = int(u.get("cachedContentTokenCount") or 0)
    prompt = int(u.get("promptTokenCount") or 0)
    thoughts = int(u.get("thoughtsTokenCount") or 0)
    return {
        "input_tokens": max(0, prompt - cached),
        "output_tokens": int(u.get("candidatesTokenCount") or 0) + thoughts,
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": 0,
        "thoughts_tokens": thoughts,
    }


def _vertex_post_body(body: dict, *, timeout=120.0, include_meta=False) -> dict:
    """Отправляет тело, собранное build_body(), и разбирает JSON-ответ."""
    model = body["model"]
    region = body.get("region") or config.VERTEX_LLM_REGION
    url = f"{_VERTEX.base(region)}/publishers/google/models/{model}:generateContent"
    started = time.perf_counter()

    payload = json.loads(json.dumps(body["payload"]))
    cache_name = None
    if body.get("_cache_system"):
        cache_name = _cached_content_name(model, region, body["_system"],
                                          body.get("_cache_ttl_s") or config.VERTEX_CACHE_TTL_S)
    if cache_name:
        payload["cachedContent"] = cache_name
    else:
        payload["system_instruction"] = {"parts": [{"text": body["_system"]}]}

    try:
        answer = _post(url, payload, timeout=timeout, tries=config.VERTEX_TRIES)
    except VertexError as exc:
        if cache_name and exc.status in (400, 403, 404):
            # Кеш протух или удалён на стороне Google — повторяем без него, а не
            # роняем оценку. Именно этот отказ иначе выглядел бы как «модель сломалась».
            _forget_cache(cache_name)
            payload.pop("cachedContent", None)
            payload["system_instruction"] = {"parts": [{"text": body["_system"]}]}
            answer = _post(url, payload, timeout=timeout, tries=config.VERTEX_TRIES)
        elif exc.status == 400 and "thinkingConfig" in payload.get("generationConfig", {}):
            # Часть моделей не принимает гашение «мышления» — приём тот же, что в
            # ai_feed_back_service._gemini_generate_once и wiki/ai/providers.
            payload["generationConfig"].pop("thinkingConfig", None)
            answer = _post(url, payload, timeout=timeout, tries=config.VERTEX_TRIES)
        else:
            raise

    text = _extract_text(answer)
    finish = (answer.get("candidates") or [{}])[0].get("finishReason")
    if not text:
        # Пустой content при finishReason=MAX_TOKENS — это НЕУДАЧА, а не пустая оценка:
        # молча отдав {} наверх, мы бы записали звонку зачёт по всем критериям.
        raise VertexError(200, f"пустой ответ, finishReason={finish}")
    parsed = json.loads(text)
    if include_meta:
        parsed["_llm_meta"] = {
            "request_id": answer.get("responseId"),
            "model": answer.get("modelVersion") or model,
            "stop_reason": finish,
            "usage": _usage(answer),
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "provider": VERTEX,
            "cached_system": bool(cache_name),
        }
    return parsed


# ── Z.ai (GLM) ──────────────────────────────────────────────────────────────
#
# Третий провайдер. Всё, что о нём надо знать перед правкой, — четыре факта,
# проверенные запросами 28.08.2026 на 140 звонках Основа ОП:
#
# 1. «Мышление» ОТКЛЮЧИТЬ НЕЛЬЗЯ. `thinking={'type':'disabled'}` → HTTP 400, код 1210:
#    «This model always engages in thinking and cannot be disabled; please use low,
#    high, or max». Ступеней ровно три, `medium`/`minimal`/`none` отвергаются тем же
#    кодом. Умолчание вендора — `max`, и оно ХУДШЕЕ: 388 с и 18 626 выходных токенов
#    на звонок против 89 с и 3 956 у `high`, при этом качество ниже (полнота 47 %
#    против 55 %). Чем дольше эта модель думает, тем мягче становится. Поэтому
#    уровень задаётся всегда и явно, из ZAI_REASONING_EFFORT.
# 2. Строгой JSON Schema НЕТ. `response_format={'type':'json_schema','strict':True}`
#    принимается, но не соблюдается: ответ приезжает обёрнутым в ```json, разбор
#    падает, и это стоит ~180 с впустую. Работает только `json_object`, а сама схема
#    описана в системном промпте — оценщик так и делает.
# 3. Пакетного режима для этой модели НЕТ: `/files` с purpose=batch отвечает 400 со
#    списком поддерживаемых моделей, самая свежая там `glm-5.1`. Ночной прогон идёт
#    тем же локальным путём, что у Vertex (batch_eval._run_locally).
# 4. Кеш промпта АВТОМАТИЧЕСКИЙ и бесплатный: попадание в 90 % запросов, в среднем
#    4 015 токенов из 5 511. Ничего настраивать не нужно, `cache_system` игнорируется.
#
# Токены «мышления» уже включены в completion_tokens (замер: 49 всего, из них 27
# reasoning), поэтому в выход они попадают сами — прибавлять их отдельно нельзя,
# иначе счёт удвоится.

_ZAI_LOCK = threading.Lock()
_ZAI_CLIENT: httpx.Client | None = None


class ZaiError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"Z.ai HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def _zai_http() -> httpx.Client:
    """Одно TLS-соединение на процесс — по той же причине, что и у Vertex."""
    global _ZAI_CLIENT
    with _ZAI_LOCK:
        if _ZAI_CLIENT is None:
            _ZAI_CLIENT = httpx.Client(
                timeout=config.ZAI_TIMEOUT,
                limits=httpx.Limits(max_keepalive_connections=8, max_connections=16))
        return _ZAI_CLIENT


def _zai_build_body(*, model, system, user, schema, max_tokens=8000, cache_system=False,
                    cache_ttl=None, effort=None, thinking=None) -> dict:
    """Тело запроса Z.ai в той же форме вызова, что и у остальных.

    `schema` в запрос НЕ уходит (см. факт 2 выше) — она уже описана в системном
    промпте оценщика. `cache_system` и `cache_ttl` игнорируются: кеш автоматический.
    `thinking` здесь означает уровень рассуждений, а не бюджет токенов, как у Vertex.

    `user` — строка либо список content-блоков в форме Anthropic (её собирает
    call_qa/media.py). Блоки переводятся в форму Z.ai; см. _zai_content.
    """
    return {
        "_provider": ZAI,
        "model": model,
        "payload": {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": _zai_content(user)}],
            "max_tokens": int(max_tokens),
            "temperature": config.ZAI_TEMPERATURE,
            "reasoning_effort": _zai_effort(thinking if thinking is not None else effort),
            "response_format": {"type": "json_object"},
        },
    }


# Вложение в форме Anthropic → блок Z.ai. Проверено запросами 28.08.2026 на живом
# API: картинка распознана за 5,5 с (444 токена), пятисекундное видео — за 6 с
# (1 581 токен, модель перечислила все пять кадров), PDF — за 11 с.
#
# Три подводных камня, каждый стоил бы отладки:
#   * `image_url` обязан быть ОБЪЕКТОМ `{"url": ...}`. Строкой вместо объекта Z.ai
#     отвечает 400 «image_url format error» (код 1214).
#   * PDF нельзя слать как `image_url` — это 400 «图片输入格式/解析错误». Для файлов
#     свой тип блока, `file_url`.
#   * Видео — третий тип, `video_url`; на `image_url` оно тоже не пройдёт.
_ZAI_BLOCK = {"image": ("image_url", "image/jpeg"),
              "document": ("file_url", "application/pdf"),
              "video": ("video_url", "video/mp4")}


def _zai_content(user):
    """Содержимое сообщения: строка отдаётся как есть, список блоков переводится."""
    if isinstance(user, str):
        return user
    out = []
    for block in user:
        kind = str((block or {}).get("type") or "")
        if kind == "text":
            out.append({"type": "text", "text": block.get("text") or ""})
            continue
        names = _ZAI_BLOCK.get(kind)
        if names is None:
            raise NotImplementedError(f"Z.ai-провайдер не знает блок «{kind}»")
        field, default_mime = names
        source = block.get("source") or {}
        if source.get("type") != "base64":
            raise NotImplementedError(
                f"Z.ai-провайдер принимает вложения только как base64, а не «{source.get('type')}»")
        mime = source.get("media_type") or default_mime
        out.append({"type": field, field: {"url": f"data:{mime};base64,{source.get('data') or ''}"}})
    return out


_ZAI_EFFORTS = ("low", "high", "max")


def _zai_effort(value) -> str:
    """Уровень рассуждений: только low/high/max, иначе умолчание из конфигурации.

    Проверка не формальная. `thinking` у Vertex — это ЧИСЛО (бюджет токенов), а у
    media.py — СЛОВАРЬ `{'type': 'disabled'}`; без фильтра сюда приехала бы строка
    вроде «{'type': 'disabled'}», Z.ai ответил бы 400 кодом 1210, и выглядело бы это
    как поломка модели, а не как чужой параметр не в том поле.
    """
    level = str(value).strip().lower() if value is not None else ""
    return level if level in _ZAI_EFFORTS else config.ZAI_REASONING_EFFORT


def _zai_usage(body: dict) -> dict:
    """usage Z.ai → имена Anthropic, которые уже читают runtime_store и батч."""
    u = body.get("usage") or {}
    cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    prompt = int(u.get("prompt_tokens") or 0)
    thoughts = int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
    return {
        "input_tokens": max(0, prompt - cached),
        # completion_tokens УЖЕ включает токены рассуждений — не прибавлять.
        "output_tokens": int(u.get("completion_tokens") or 0),
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": 0,
        "thoughts_tokens": thoughts,
    }


def _zai_post_body(body: dict, *, timeout=120.0, include_meta=False) -> dict:
    """Отправляет тело, собранное build_body(), и разбирает JSON-ответ."""
    key = config.zai_key()
    if not key:
        raise RuntimeError("нет ключа Z.ai (ZAI_API_KEY)")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = body["payload"]
    # Таймаут вызывающего — это НИЖНЯЯ граница, а не верхняя. Интерактивная оценка
    # зашивает 120 с (evaluator.py:230); они калибровались под Claude и Gemini, где
    # звонок считается за 16-45 с. У GLM на reasoning_effort=high медиана 91 с, p90
    # 116 с, максимум на 140 звонках 155 с — с чужим лимитом карточка отваливалась бы
    # по таймауту на каждом десятом длинном разговоре, и выглядело бы это как отказ
    # провайдера. Поднимаем до ZAI_TIMEOUT, урезать себя ниже — нельзя.
    timeout = max(float(timeout or 0), config.ZAI_TIMEOUT)
    started = time.perf_counter()

    last = None
    answer = None
    for attempt in range(max(1, config.ZAI_TRIES)):
        response = _zai_http().post(config.ZAI_URL, json=payload, headers=headers,
                                    timeout=timeout)
        if response.status_code == 200:
            answer = response.json()
            break
        last = response
        if response.status_code in _RETRYABLE and attempt + 1 < config.ZAI_TRIES:
            time.sleep(config.ZAI_RETRY_BASE_S * (attempt + 1))
            continue
        break
    if answer is None:
        raise ZaiError(last.status_code if last is not None else 0,
                       last.text[:400] if last is not None else "нет ответа")

    choice = (answer.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = (message.get("content") or "").strip()
    finish = choice.get("finish_reason")
    if not text:
        # Пустой content — это НЕУДАЧА, а не пустая оценка: при reasoning_effort=max
        # модель отдаёт HTTP 200 с нулём символов, потратив весь лимит на рассуждения.
        # Молча вернув {}, мы записали бы звонку зачёт по всем критериям.
        raise ZaiError(200, f"пустой ответ, finish_reason={finish}, "
                            f"рассуждений {len(message.get('reasoning_content') or '')} симв")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ZaiError(200, f"ответ не JSON ({exc}): {text[:200]}") from exc
    if include_meta:
        parsed["_llm_meta"] = {
            "request_id": answer.get("id"),
            "model": answer.get("model") or body["model"],
            "stop_reason": finish,
            "usage": _zai_usage(answer),
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "provider": ZAI,
            "reasoning_effort": payload.get("reasoning_effort"),
        }
    return parsed
