"""Поиск разбора, который уже описывает то же самое.

Вопрос не «какие правила похожи», а «такой разбор уже был?» — его задаёт
проверяющий, исправляя ИИ, и от ответа зависят две вещи:

  * ДУБЛЬ ДЕЙСТВУЮЩЕГО правила (тот же критерий и вердикт) новым правилом не
    становится — разбор привязывается к существующему (решение владельца
    01.10.2026). Иначе копии забивают промпт: на критерий ИИ получает не больше
    трёх правил, а в 72 «Приветствие» лежало 16 копий одного и того же;
  * если такое правило уже действовало, а ИИ всё равно ошибся, — это ответ на
    вопрос владельца, «нормально ли ИИ использует разборы».

ЛОГИКА — КАК У «ТАКАЯ СТАТЬЯ УЖЕ ЕСТЬ?» В ВИКИ (wiki/ai/similar.py): смысловая
ветка плюс словесная, ответ — короткий список с уверенностью, а не выдача поиска.
Отличие одно и измеренное: сравнивается ТЕКСТ ПРАВИЛА, а не вектор поиска.

Вектор поиска правила (qa_policy_rule_embeddings) построен по «ситуации и
цитате» — под сравнение с разговором, а не с другим правилом. На размеченных
вручную 852 парах правил одного критерия (72 «Приветствие», 72 «Персонализация»,
74 «Выявление потребностей», 01.10.2026, векторы Vertex той же модели) он
дубли от разных правил не отличает: при пороге 0,94 ловит 12 % повторов и
даёт 6 % ложных. Вектор текста правила разделяет чисто:

    порог   повторы найдены   разные правила приняты за похожие
    0,94        45 %                    0 %   (максимум у разных — 0,931)
    0,90        87 %                   19 %   (это «приветствие» против
                                              «верно назвал таксопарк» —
                                              части одного критерия)
    0,86        95 %                   49 %

Второй замер — на всех 132 живых правилах — сдвинул порог «дубля» с 0,94 на
0,97. Длинные правила, переформулированные кнопкой «Сформулировать», начинаются
одинаково («Считай … выполненным (Correct), если оператор …»), и это поднимает
сходство РАЗНЫХ правил до 0,940–0,9696: «приветствие на двух языках» против
«трёх элементов приветствия», «перезвон по тому же обращению», «не было
исходных данных водителя». Ошибка в эту сторону дороже: ложный «дубль» не
создал бы правило с новым оттенком, а пропущенный — всего лишь ещё одна копия,
которую видно в карточке как «похоже».

Но и выше 0,97 смысл бывает ОБРАТНЫМ — векторы не различают модальность
(сверка с решениями владельца по спорным черновикам, 01.10.2026):
«не должен проговаривать имя» (запрет) против «не обязан проговаривать» (не
требуется) — 0,974 и 0,982; «сверил имя — достаточно» против «без известного
города — ошибка» — 0,9721. Слова различают: у этих пар общих слов 0,23–0,46, а у
настоящих копий (дословные, без «ему», перестановка слов; смысл 0,989–1,0) —
0,83–1,0. Поэтому «дубль» — это смысл >= 0,97 И та же формулировка: общих слов
>= 0,8, тот же набор «не/должен/обязан/можно…» и те же числа (замена «должен» на
«обязан» или «4%» на «5%» в длинной фразе почти не трогает долю общих слов, но
меняет правило).

Отсюда: «дубль» — смысл >= 0,97 и та же формулировка; «похоже» — смысл >= 0,90;
ниже не показываем ничего — список из пяти случайных правил на каждое
исправление обесценил бы и настоящую находку.

СЛОВЕСНАЯ ВЕТКА — ТОЛЬКО ЗАПАСНАЯ: когда сравнить по смыслу нечем (провайдер
лёг, у правила нет вектора). «Дубль» по словам — только при СОВПАДЕНИИ НАБОРА
СЛОВ, и в слова входят «не»/«ни»: доля общих слов не различает «уточнил имя»
и «даже если НЕ уточнил имя» (7 из 8 слов общие), а ошибка здесь стоит
правила — дубль действующего правила не создаёт нового. Частичное совпадение
слов даёт не больше «похоже».

ДУБЛЬ ПРИВЯЗЫВАЕТСЯ, ТОЛЬКО ЕСЛИ ИИ ЭТО ПРАВИЛО ПОЛУЧИЛ (решает вызывающий,
linkable_ids в duplicate_target). Правило ищется по своей «ситуации и цитате»:
если на этом разговоре оно до промпта не дошло, привязка оставила бы ИИ без
правила для таких разговоров навсегда — нужно новое правило из этого разговора.

Векторы текстов правил хранятся по хэшу текста (как wiki_ai_embeddings):
версии правил неизменяемы, и посчитанный раз вектор не пересчитывается ни после
выкладки, ни при следующем исправлении.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from collections import OrderedDict

from .. import config
from ..evaluation.fingerprint import content_hash

SURE = 0.97
CLOSE = 0.90
# «Дубль» по смыслу ещё и с той же формулировкой (шапка модуля): доля общих слов.
LEXICAL_DUPLICATE = 0.8
# Запасная словесная ветка: от этой доли общих слов — «похоже» (но не «дубль»).
LEXICAL_SIMILAR = 0.6
_LEXICAL_DUPLICATE_SCORE = 0.99
_LEXICAL_SIMILAR_SCORE = 0.91

# Потолок кандидатов на критерий. Живых правил на критерий сейчас до 30; потолок
# только страхует от неожиданного роста и пишет в лог, если сработал.
CANDIDATE_LIMIT = 200
SHOW_LIMIT = 3

# От двух букв: «не», «ни», «но» меняют смысл правила на обратный.
_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)

# Числа правила («4%», «3 секунды», «1,5 минуты») — тоже его суть: слова их не
# видят, а одно число среди сорока слов почти не трогает долю общих слов.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

# Слова, задающие силу правила: «не должен» — запрет, «не обязан» — не требуется.
# У дубля их набор обязан совпасть целиком.
_MODAL = frozenset({
    "не", "ни", "нет", "нельзя", "можно", "может", "могут", "вправе", "должен", "должна",
    "должно", "должны", "обязан", "обязана", "обязано", "обязаны", "обязательно",
    "необязательно", "нужно", "необходимо", "требуется", "запрещено", "разрешено",
    "допускается", "достаточно", "только",
})

# Векторы текстов проверяющего: подсказка в карточке спрашивает по каждой паузе
# в наборе, и одинаковый текст (перерисовка, возврат вердикта) не должен
# стоить нового обращения к Vertex — квота у проекта тесная.
_QUERY_CACHE: "OrderedDict[str, list[float]]" = OrderedDict()
_QUERY_CACHE_LIMIT = 256
_QUERY_CACHE_LOCK = threading.Lock()

_CANDIDATES_SQL = """
SELECT r.id::text, r.rule_status, r.criterion_id, r.criterion_idx, r.criterion_name,
       v.id, v.rule_text, v.situation, v.not_covered, v.correct_verdict, v.excerpt,
       r.created_at, u.name,
       COALESCE(m.included_count, 0), COALESCE(m.review_corrected_count, 0),
       COALESCE(m.review_confirmed_count, 0)
  FROM qa_policy_rules r
  JOIN qa_policy_rule_versions v ON v.id = r.current_version_id
  LEFT JOIN users u ON u.id = r.created_by
  LEFT JOIN qa_policy_rule_metrics m ON m.rule_id = r.id
 WHERE r.direction_id = %s AND r.criterion_id = %s
   AND r.rule_status IN ('active', 'draft', 'quarantined')
 ORDER BY r.created_at
 LIMIT %s
"""

_CANDIDATE_KEYS = ("rule_id", "rule_status", "criterion_id", "criterion_idx",
                   "criterion_name", "rule_version_id", "rule_text", "situation",
                   "not_covered", "correct_verdict", "excerpt", "created_at", "author",
                   "included_count", "corrected_after_count", "confirmed_count")


def meaning_text(rule_text) -> str:
    """Текст правила в том виде, в каком он сравнивается: пробелы схлопнуты."""
    return " ".join(str(rule_text or "").split())


def text_hash(text) -> str:
    return content_hash(meaning_text(text))


def _words(text) -> set[str]:
    return set(_WORD.findall(str(text or "").casefold().replace("ё", "е")))


def word_overlap(left, right) -> float:
    """Доля общих слов (Жаккар). Ловит дословные и почти дословные повторы."""
    a, b = _words(left), _words(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def _numbers(text) -> set[str]:
    return {value.replace(",", ".") for value in _NUMBER.findall(str(text or ""))}


def same_wording(left, right) -> bool:
    """Та же формулировка: почти те же слова, та же сила правила и те же числа."""
    a, b = _words(left), _words(right)
    return (bool(a and b) and len(a & b) / len(a | b) >= LEXICAL_DUPLICATE
            and a & _MODAL == b & _MODAL and _numbers(left) == _numbers(right))


def cosine(left, right) -> float:
    dot = sum(x * y for x, y in zip(left, right))
    norm = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(y * y for y in right))
    return dot / norm if norm else 0.0


def label(score: float) -> str | None:
    if score >= SURE:
        return "duplicate"
    if score >= CLOSE:
        return "similar"
    return None


def _model_id(cur) -> int | None:
    """Строка qa_embedding_models текущего контракта (её заводит первое правило)."""
    from ..embeddings.provider import configured_contract
    contract = configured_contract()
    cur.execute(
        """SELECT id FROM qa_embedding_models
            WHERE embedding_provider=%s AND embedding_model=%s
              AND embedding_dim=%s AND config_hash=%s""",
        (contract["provider"], contract["model"], int(contract["dim"]),
         contract["config_hash"]))
    row = cur.fetchone()
    return int(row[0]) if row else None


def _parse_vector(raw):
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        return [float(x) for x in raw]
    return [float(x) for x in str(raw).strip("[]").split(",") if x.strip()]


def _vector_literal(values) -> str:
    return "[" + ",".join(format(float(x), ".9g") for x in values) + "]"


def _stored_vectors(cur, model_id, hashes) -> dict:
    if not hashes or model_id is None:
        return {}
    cur.execute(
        """SELECT text_hash, embedding::text FROM qa_rule_text_embeddings
            WHERE embedding_model_id=%s AND text_hash = ANY(%s)""",
        (model_id, sorted(set(hashes))))
    return {row[0]: _parse_vector(row[1]) for row in cur.fetchall()}


_BATCH = 25


def embed_texts(texts: list[str], *, attempts: int = 3) -> list[list[float]]:
    """Векторы текстов (роль document — как у самих правил), пачками по 25.

    Квота Vertex на базовую модель у проекта невелика: на плотной серии запросов
    треть ответов — 429 (замер 01.10.2026). Два коротких повтора с паузой
    покрывают обычный всплеск; дольше ждать не даём — проверка идёт, пока
    человек печатает, а сохранение разбора без вектора не теряется."""
    import httpx
    from ..embeddings.provider import get_provider
    provider = get_provider()
    out = []
    for start in range(0, len(texts), _BATCH):
        part = texts[start:start + _BATCH]
        delay = 1.5
        for attempt in range(attempts):
            try:
                out.extend(provider.embed_document(part))
                break
            except httpx.HTTPStatusError as error:
                too_many = error.response is not None and error.response.status_code == 429
                if not too_many or attempt == attempts - 1:
                    raise
                time.sleep(delay)
                delay *= 2
    return out


_INSERT_VECTOR_SQL = """INSERT INTO qa_rule_text_embeddings
                            (text_hash, embedding_model_id, embedding)
                          VALUES (%s, %s, %s::vector)
                          ON CONFLICT (text_hash, embedding_model_id) DO NOTHING"""


def save_vector(cur, model_id, text, vector) -> None:
    """Вектор текста правила — в той транзакции, что держит вызывающий."""
    if model_id is None or not vector or not meaning_text(text):
        return
    cur.execute(_INSERT_VECTOR_SQL, (text_hash(text), int(model_id), _vector_literal(vector)))


def store_vectors(model_id, vectors_by_hash: dict) -> None:
    """Сохранить векторы текстов правил. Лучшее усилие: без RW — просто не кэшируем."""
    if model_id is None or not vectors_by_hash:
        return
    try:
        conn = config.connect_rw()
        try:
            with conn, conn.cursor() as cur:
                for digest, vector in vectors_by_hash.items():
                    cur.execute(_INSERT_VECTOR_SQL,
                                (digest, int(model_id), _vector_literal(vector)))
        finally:
            conn.close()
    except Exception:
        logging.info("ai-qa: векторы текстов правил не сохранены (кэш пропущен)", exc_info=True)


def summary(found: dict, *, target=None) -> list[dict]:
    """Что нашлось при сохранении — в метаданные разбора (не больше трёх).

    Разбор неизменяем, поэтому снимок «какой похожий разбор уже был и действовал
    ли он» пишется в момент сохранения: по нему потом считается, сколько раз ИИ
    ошибался там, где правило у него уже было. Цель привязки в снимке всегда."""
    return [{"rule_id": item["rule_id"], "score": item["score"], "verdict": item["verdict"],
             "status": item["rule_status"], "same_verdict": item["same_verdict"]}
            for item in shown_items(found, target=target)]


def load_candidates(cur, *, direction_id: int, criterion_id: str) -> list[dict]:
    cur.execute(_CANDIDATES_SQL, (int(direction_id), str(criterion_id), CANDIDATE_LIMIT))
    rows = [dict(zip(_CANDIDATE_KEYS, row)) for row in cur.fetchall()]
    if len(rows) >= CANDIDATE_LIMIT:
        logging.warning("ai-qa: похожие разборы — кандидатов по критерию %s больше %d, "
                        "хвост не сравнивается", criterion_id, CANDIDATE_LIMIT)
    return rows


def rank(candidates: list[dict], *, text: str, vector=None,
         vectors_by_hash: dict | None = None, correct_verdict=None,
         exclude_rule_ids=()) -> list[dict]:
    """Свести смысловую и словесную ветки в короткий список находок.

    Чистая функция (без базы и сети) — её и проверяют тесты. score — самая
    сильная из двух причин, found_by перечисляет все: иначе непонятно, почему
    правило вообще предложено."""
    excluded = {str(rule_id) for rule_id in exclude_rule_ids or ()}
    vectors_by_hash = vectors_by_hash or {}
    found = []
    for item in candidates:
        if str(item["rule_id"]) in excluded:
            continue
        reasons, score = [], 0.0
        rule_vector = vectors_by_hash.get(text_hash(item["rule_text"]))
        if vector is not None and rule_vector is not None:
            score = cosine(vector, rule_vector)
            if score >= CLOSE:
                reasons.append("смысл")
            verdict = label(score)
            if verdict == "duplicate" and not same_wording(text, item["rule_text"]):
                # Близко по смыслу, но сказано иначе — возможно, с обратной силой.
                verdict = "similar"
        else:
            # Сравнить по смыслу нечем — запасная словесная ветка (шапка модуля):
            # «дубль» только при совпадении набора слов, иначе не выше «похоже».
            overlap = word_overlap(text, item["rule_text"])
            if overlap >= 1.0 and _numbers(text) == _numbers(item["rule_text"]):
                score = _LEXICAL_DUPLICATE_SCORE
            elif overlap >= LEXICAL_SIMILAR:
                score = _LEXICAL_SIMILAR_SCORE
            if score:
                reasons.append("слова")
            verdict = label(score)
        if not verdict:
            continue
        found.append({
            **{key: item[key] for key in _CANDIDATE_KEYS if key != "created_at"},
            "created_at": item["created_at"].isoformat() if hasattr(item.get("created_at"), "isoformat")
            else item.get("created_at"),
            "score": round(score, 3), "verdict": verdict, "found_by": reasons,
            "same_verdict": (correct_verdict is None
                             or str(item["correct_verdict"]) == str(correct_verdict)),
        })
    # Дубли первыми, среди них — действующие: дубль-черновик со 100 % не должен
    # вытеснить из показа действующее правило, к которому разбор привяжется.
    found.sort(key=lambda row: (row["verdict"] != "duplicate", row["rule_status"] != "active",
                                -row["score"]))
    return found


def _cached_query_vector(text):
    with _QUERY_CACHE_LOCK:
        vector = _QUERY_CACHE.get(text)
        if vector is not None:
            _QUERY_CACHE.move_to_end(text)
        return vector


def _remember_query_vector(text, vector):
    with _QUERY_CACHE_LOCK:
        _QUERY_CACHE[text] = vector
        _QUERY_CACHE.move_to_end(text)
        while len(_QUERY_CACHE) > _QUERY_CACHE_LIMIT:
            _QUERY_CACHE.popitem(last=False)


def find_similar(*, direction_id: int, criterion_id: str, text: str,
                 correct_verdict=None, exclude_rule_ids=(), vector=None,
                 embed: bool = True) -> dict:
    """Правила того же критерия направления, повторяющие этот разбор.

    vector — уже посчитанный вектор текста (сохранение разбора считает его
    вместе с вектором поиска одним вызовом); иначе считается здесь. Сбой
    провайдера не роняет ответ: где сравнить по смыслу нечем, работает запасная
    словесная ветка, degraded=True. embed=False — к провайдеру не ходить вовсе
    (сохранение, у которого общий вызов или проверка прошлого пункта уже упали:
    повторные попытки под блокировкой звонка только растянули бы сбой).
    Возвращает {"items": [...], "degraded": bool, "candidates": n,
    "provider_failed": bool}."""
    text = meaning_text(text)
    if len(text) < 8 or not criterion_id:
        return {"items": [], "degraded": False, "candidates": 0, "provider_failed": False}
    conn = config.connect_ro()
    try:
        with conn.cursor() as cur:
            cur.execute("SET client_encoding TO 'UTF8'")
            candidates = load_candidates(cur, direction_id=direction_id,
                                         criterion_id=criterion_id)
            if not candidates:
                return {"items": [], "degraded": False, "candidates": 0,
                        "provider_failed": False}
            model_id = _model_id(cur)
            try:
                stored = _stored_vectors(cur, model_id,
                                         [text_hash(item["rule_text"]) for item in candidates])
            except Exception as exc:
                from ..evaluation.runtime_store import is_schema_compat_error
                if not is_schema_compat_error(exc):
                    raise
                # Таблицы векторов текстов ещё нет (код выложен раньше миграции):
                # векторы считаем заново, кэш не пишем — поиск при этом работает.
                stored, model_id = {}, None
    finally:
        conn.close()

    missing = {}
    for item in candidates:
        digest = text_hash(item["rule_text"])
        if digest not in stored and digest not in missing and meaning_text(item["rule_text"]):
            missing[digest] = meaning_text(item["rule_text"])
    if vector is None:
        vector = _cached_query_vector(text)
    need_query = vector is None
    texts = list(missing.values()) + ([text] if need_query else [])
    fresh = {}
    provider_failed = False
    if texts and embed:
        try:
            vectors = embed_texts(texts)
            if need_query:
                vector = vectors.pop()
                _remember_query_vector(text, vector)
            fresh = dict(zip(missing, vectors))
        except Exception:
            # Уже готовый вектор не выбрасываем: с сохранёнными векторами правил
            # он сравнится по смыслу, а без вектора — правило уйдёт в словесную ветку.
            provider_failed = True
            logging.warning("ai-qa: похожие разборы — эмбеддинг недоступен, часть сравнений "
                            "по словам", exc_info=True)
    if fresh:
        store_vectors(model_id, fresh)
    # Неполно, если хоть одно правило сравнивается по словам, а не по смыслу.
    compared = {**stored, **fresh}
    degraded = vector is None or any(
        text_hash(item["rule_text"]) not in compared for item in candidates
        if meaning_text(item["rule_text"]))
    items = rank(candidates, text=text, vector=vector, vectors_by_hash={**stored, **fresh},
                 correct_verdict=correct_verdict, exclude_rule_ids=exclude_rule_ids)
    return {"items": items, "degraded": degraded, "candidates": len(candidates),
            "provider_failed": provider_failed}


def duplicate_target(found: dict, *, correct_verdict, linkable_ids=None) -> dict | None:
    """Действующее правило, которому этот разбор — дубль (тот же вердикт).

    linkable_ids — правила, которые ИИ в этой оценке ПОЛУЧИЛ по этому критерию
    (трасса прогона). Привязывать можно только к ним: правило, не дошедшее до
    промпта на этом разговоре, привязкой не поможет — нужно новое правило из
    этого разговора (шапка модуля). None — без ограничения (для тестов и
    вызовов, где трассы нет)."""
    allowed = None if linkable_ids is None else {str(rule_id) for rule_id in linkable_ids}
    for item in (found or {}).get("items") or []:
        if (item["verdict"] == "duplicate" and item["rule_status"] == "active"
                and str(item["correct_verdict"]) == str(correct_verdict)
                and (allowed is None or str(item["rule_id"]) in allowed)):
            return item
    return None


def shown_items(found: dict, *, target=None) -> list[dict]:
    """Что показать проверяющему: цель привязки — всегда, остальное до SHOW_LIMIT.

    Иначе дубли-черновики со 100 % вытесняли бы из показа действующее правило,
    к которому разбор на самом деле привяжется."""
    items = list((found or {}).get("items") or [])
    if target is not None:
        items = [target] + [item for item in items if item["rule_id"] != target["rule_id"]]
    return items[:SHOW_LIMIT]
