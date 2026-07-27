"""Субъект оценки: звонок или эпизод переписки Wazzup.

Раздел ИИ-оценки начинался со звонков, и весь конвейер (immutable-транскрипт →
fingerprint → оценка по текущей шкале → очередь ревью → разборы) на самом деле
не зависит от того, откуда взялся текст. Здесь собрано всё, что отличает
субъекты друг от друга, чтобы api.py дальше работал с одной формой данных:

* ``load(kind, id)`` — кто оператор, какое направление, когда это было;
* ``eligibility(subject)`` — можно ли вообще честно оценить этот субъект;
* ``resolve_transcript(subject)`` — текст + строки для карточки.

Про направление эпизода: у ``wazzup_episodes`` направления нет. Оно берётся из
оператора, которому эпизод атрибутирован (``users.direction_id``) — так же, как
это делает человеческая оценка «Случайного чата». Направления Верификаторов не
захардкожены: они вычисляются по коду отдела + маркеру в названии, поэтому
переименование или новое направление подхватываются сами, а оценка всегда идёт
по ТЕКУЩЕЙ мониторинговой шкале этого направления (см. criteria.load_direction).
"""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from . import config
from . import media as media_mod
from .evaluation.fingerprint import content_hash

ALMATY = ZoneInfo("Asia/Almaty")

# Провайдер/модель «источника записи» для эпизода чата: в ai_transcript_cache
# это поля происхождения текста, а не факты ASR (у чата нет аудио).
WZ_SOURCE_PROVIDER = "wazzup-episode"
WZ_SOURCE_MODEL = "episode-transcript-v1"

# Причины, по которым эпизод нельзя оценить (уходят в UI как есть).
REASON_NO_OPERATOR = "no_operator"
REASON_SHARE = "operator_share"
REASON_FEW_MESSAGES = "few_operator_messages"
REASON_KIND = "not_dialog"
REASON_DIRECTION = "direction_not_eligible"
REASON_NO_DIRECTION = "operator_without_direction"


class SubjectNotFound(ValueError):
    pass


class SubjectNotEvaluable(ValueError):
    """Субъект найден, но честно оценить его нельзя (см. .reason/.detail)."""

    def __init__(self, message, *, reason=None, detail=None):
        super().__init__(message)
        self.reason = reason
        self.detail = detail or {}


def normalise_kind(kind) -> str:
    value = str(kind or config.SUBJECT_CALL).strip() or config.SUBJECT_CALL
    if value not in config.SUBJECT_KINDS:
        raise ValueError(f"неизвестный тип субъекта оценки: {value}")
    return value


# ── направления ──────────────────────────────────────────────────────────────

def op_direction_family(cur) -> list[int]:
    """Все направления отдела продаж (см. config.op_direction_id_family)."""
    return config.op_direction_id_family(cur)


def wz_direction_family(cur) -> list[int]:
    """Направления Верификаторов (эпизоды чатов оцениваются только у них).

    Правило совпадает с кнопкой «Случайный чат»: код отдела + маркер в названии
    направления. Архивные версии шкалы включаются, чтобы старая оценка
    оставалась в скоупе после правки критериев."""
    try:
        cur.execute(
            """SELECT d.id
                 FROM directions d
                 LEFT JOIN departments dep ON dep.id = d.department_id
                WHERE lower(COALESCE(dep.code, '')) = %s
                  AND position(%s in lower(COALESCE(d.name, ''))) > 0""",
            (config.WZ_DEPARTMENT_CODE, config.WZ_DIRECTION_MARKER))
        ids = [int(r[0]) for r in cur.fetchall()]
    except Exception:
        logging.exception("ai-qa: не удалось вычислить направления Верификаторов")
        return []
    return ids


# ── загрузка субъекта ────────────────────────────────────────────────────────

def load(subject_kind, subject_id: int) -> dict:
    """Единая форма субъекта для конвейера оценки."""
    kind = normalise_kind(subject_kind)
    if kind == config.SUBJECT_CALL:
        return _load_call(int(subject_id))
    return _load_wz_episode(int(subject_id))


def _load_call(call_id: int) -> dict:
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(
            """SELECT c.id, c.direction_id, d.name, u.name,
                      TO_CHAR(c.created_at,'DD.MM.YYYY, HH24:MI'), c.score, c.audio_path
                 FROM calls c
                 LEFT JOIN directions d ON c.direction_id = d.id
                 LEFT JOIN users u ON u.id = c.operator_id
                WHERE c.id = %s""", (call_id,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not row:
        raise SubjectNotFound("звонок не найден")
    if not row[6]:
        # Обычный ValueError, а не SubjectNotEvaluable: маршрут отдаёт на это 404,
        # как и раньше — «нет записи» это отсутствие данных, а не отказ по существу.
        raise ValueError("у звонка нет записи")
    return {"kind": config.SUBJECT_CALL, "id": int(row[0]), "direction_id": row[1],
            "direction": row[2], "operator": row[3] or "—", "datetime": row[4],
            "human_score": row[5], "audio_path": row[6]}


def _load_wz_episode(episode_id: int) -> dict:
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(
            """SELECT e.id, e.channel_id, e.chat_id, e.chat_type, e.contact_name,
                      e.contact_phone, e.started_at, e.ended_at, e.messages_count,
                      e.inbound_count, e.outbound_count, e.human_outbound_count,
                      e.kind, e.operator_user_id, u.name, e.operator_share,
                      e.authors, e.force_closed, e.transcript, e.context_tail,
                      u.direction_id, d.name
                 FROM wazzup_episodes e
                 LEFT JOIN users u ON u.id = e.operator_user_id
                 LEFT JOIN directions d ON d.id = u.direction_id
                WHERE e.id = %s""", (episode_id,))
        row = cur.fetchone()
        eligible_directions = wz_direction_family(cur) if row else []
        cur.close()
    finally:
        conn.close()
    if not row:
        raise SubjectNotFound("эпизод чата не найден")
    started, ended = row[6], row[7]
    subject = {
        "kind": config.SUBJECT_WZ_EPISODE, "id": int(row[0]),
        "channel_id": row[1], "chat_id": row[2], "chat_type": row[3],
        "contact_name": row[4], "contact_phone": row[5],
        "started_at": started, "ended_at": ended,
        "messages_count": int(row[8] or 0), "inbound_count": int(row[9] or 0),
        "outbound_count": int(row[10] or 0), "human_outbound_count": int(row[11] or 0),
        "episode_kind": row[12], "operator_user_id": row[13],
        "operator": row[14] or "—", "operator_share": row[15],
        "authors": row[16] or [], "force_closed": bool(row[17]),
        "raw_transcript": row[18] or "", "context_tail": row[19],
        "direction_id": row[20], "direction": row[21],
        "eligible_direction_ids": eligible_directions,
        "datetime": (ended.astimezone(ALMATY).strftime("%d.%m.%Y, %H:%M")
                     if ended is not None else "—"),
        "human_score": None,
    }
    return subject


# ── можно ли оценивать ───────────────────────────────────────────────────────

def eligibility(subject: dict) -> dict:
    """Проверка «оценка будет честной», без обращения к моделям.

    Для эпизода чата главное — атрибуция: в одном эпизоде могут отвечать
    несколько операторов, и тогда оценка «в одни руки» приписывает одному
    человеку работу другого. Порог доли ответов доминирующего оператора —
    config.WZ_MIN_OPERATOR_SHARE (по умолчанию 90%)."""
    if subject["kind"] == config.SUBJECT_CALL:
        return {"ok": True, "reason": None, "detail": {}}

    share = subject.get("operator_share")
    human_out = int(subject.get("human_outbound_count") or 0)
    detail = {
        "operator_share": share,
        "operator_share_pct": (round(float(share) * 100) if share is not None else None),
        "min_operator_share_pct": round(config.WZ_MIN_OPERATOR_SHARE * 100),
        "human_outbound_count": human_out,
        "operator_messages": (int(round(float(share) * human_out))
                              if share is not None else None),
        "authors": [a for a in (subject.get("authors") or []) if not a.get("is_bot")],
    }
    if subject.get("episode_kind") != "dialog":
        return {"ok": False, "reason": REASON_KIND, "detail": detail,
                "message": "эпизод не является диалогом (нет ответа оператора)"}
    if not subject.get("operator_user_id"):
        return {"ok": False, "reason": REASON_NO_OPERATOR, "detail": detail,
                "message": "автор ответов не привязан к сотруднику — оценивать некого"}
    if subject.get("direction_id") is None:
        return {"ok": False, "reason": REASON_NO_DIRECTION, "detail": detail,
                "message": "у оператора не указано направление — нет мониторинговой шкалы"}
    eligible = {int(x) for x in (subject.get("eligible_direction_ids") or [])}
    if eligible and int(subject["direction_id"]) not in eligible:
        return {"ok": False, "reason": REASON_DIRECTION, "detail": detail,
                "message": ("направление «%s» не относится к Верификаторам — "
                            "чаты оцениваются только по их шкале"
                            % (subject.get("direction") or subject["direction_id"]))}
    if share is None or float(share) + 1e-9 < config.WZ_MIN_OPERATOR_SHARE:
        pct = detail["operator_share_pct"]
        return {"ok": False, "reason": REASON_SHARE, "detail": detail,
                "message": ("в эпизоде отвечали несколько операторов: у оператора "
                            f"{pct if pct is not None else 0}% ответов из "
                            f"{human_out} (нужно не меньше "
                            f"{detail['min_operator_share_pct']}%) — "
                            "оценить одного человека нельзя")}
    if human_out < config.WZ_MIN_OPERATOR_MESSAGES:
        return {"ok": False, "reason": REASON_FEW_MESSAGES, "detail": detail,
                "message": (f"в эпизоде всего {human_out} ответ(а) оператора — "
                            "недостаточно для оценки по шкале")}
    return {"ok": True, "reason": None, "detail": detail}


def require_evaluable(subject: dict) -> dict:
    verdict = eligibility(subject)
    if not verdict["ok"]:
        raise SubjectNotEvaluable(verdict["message"], reason=verdict["reason"],
                                  detail=verdict["detail"])
    return verdict


# ── транскрипт эпизода ───────────────────────────────────────────────────────

def fetch_episode_messages(subject: dict) -> list[dict]:
    """Сообщения эпизода вместе с ссылками на вложения.

    wazzup_messages живут 45 дней, эпизод — бессрочно; пустой список означает
    «сырые сообщения уже удалены ретеншном», а не «сообщений не было»."""
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(
            """SELECT m.message_id, m.dt, m.is_echo, m.type, m.text, m.content_uri,
                      m.author_name, m.author_id, COALESCE(map.is_bot, FALSE),
                      m.is_deleted, map.user_id, u.name, m.channel_id, m.chat_id
                 FROM wazzup_messages m
                 LEFT JOIN wazzup_operator_map map ON map.author_id = m.author_id
                 LEFT JOIN users u ON u.id = map.user_id
                WHERE m.channel_id = %s AND m.chat_id = %s
                  AND m.dt >= %s AND m.dt <= %s
                ORDER BY m.dt, m.message_id""",
            (str(subject["channel_id"]), str(subject["chat_id"]),
             subject["started_at"], subject["ended_at"]))
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    return [{"message_id": r[0], "dt": r[1], "is_echo": r[2], "type": r[3],
             "text": r[4], "content_uri": r[5], "author_name": r[6], "author_id": r[7],
             "is_bot": r[8], "is_deleted": r[9], "user_id": r[10],
             "matched_name": r[11], "channel_id": r[12], "chat_id": r[13]}
            for r in rows]


_MEDIA_RU = {"image": "фото", "video": "видео", "audio": "голосовое",
             "document": "документ", "geo": "геолокация", "vcard": "контакт",
             "missing_call": "пропущенный звонок", "unsupported": "вложение"}


def _speaker_of(msg: dict, target_user_id) -> tuple[str, str]:
    """(speaker для карточки, подпись для транскрипта модели)."""
    author = msg.get("matched_name") or msg.get("author_name") or "?"
    if not msg.get("is_echo"):
        return "client", "Клиент"
    if msg.get("is_bot"):
        return "bot", f"Рассылка ({msg.get('author_name') or 'бот'})"
    template = " [шаблон]" if msg.get("type") == "wapi_template" else ""
    if target_user_id is not None and msg.get("user_id") == target_user_id:
        return "operator", f"Оператор ({author}){template}"
    # Чужой сотрудник в том же чате: помечаем явно, иначе модель припишет его
    # слова оцениваемому оператору (порог 90% допускает до 10% таких строк).
    return "other_operator", f"Другой сотрудник ({author}){template}"


def build_wz_transcript(subject: dict, messages: list[dict],
                        annotations: dict[str, dict]) -> dict:
    """Строит текст для модели и строки для карточки из одного прохода.

    Текст и строки обязаны совпадать: цитата разбора проверяется по тексту
    карточки, а оценка идёт по тексту модели — расхождение сделало бы
    подтверждение цитаты невозможным."""
    target = subject.get("operator_user_id")
    lines, text_lines = [], []
    for msg in messages:
        stamp = msg["dt"].astimezone(ALMATY).strftime("%d.%m %H:%M")
        speaker, who = _speaker_of(msg, target)
        parts = []
        if msg.get("is_deleted"):
            parts.append("[удалено]")
        media_kind = media_mod._kind_of(msg.get("type"))
        media_label = _MEDIA_RU.get(str(msg.get("type") or ""))
        if media_label:
            if msg.get("content_uri") and media_mod.annotatable(msg.get("type")):
                parts.append(media_mod.annotation_text(
                    media_kind, annotations.get(str(msg.get("message_id")))))
            else:
                parts.append(f"[{media_label}]")
        if msg.get("text"):
            parts.append(msg["text"])
        if not parts:
            parts.append(f"[{msg.get('type') or 'сообщение'}]")
        body = " ".join(parts)
        text_lines.append(f"[{stamp}] {who}: {body}")
        line = {"speaker": speaker, "seg": [{"t": f"{who}: {body}"}], "ts": stamp,
                "author": msg.get("matched_name") or msg.get("author_name"),
                "message_id": str(msg.get("message_id"))}
        if msg.get("content_uri") and media_label:
            line["media"] = {"kind": media_kind if media_kind != "unknown" else "file",
                             "label": media_label, "url": msg["content_uri"]}
        lines.append(line)

    header = _transcript_header(subject)
    return {"text": "\n".join([header, ""] + text_lines) if text_lines else header,
            "lines": lines}


def _transcript_header(subject: dict) -> str:
    share = subject.get("operator_share")
    pct = round(float(share) * 100) if share is not None else None
    bits = [f"ЧАТ WhatsApp (Wazzup), эпизод #{subject['id']}",
            f"ОЦЕНИВАЕМЫЙ ОПЕРАТОР: {subject.get('operator') or '—'}"]
    if pct is not None:
        bits.append(f"его доля ответов в эпизоде: {pct}%")
    bits.append(f"клиент: {subject.get('contact_name') or subject.get('contact_phone') or '—'}")
    bits.append("Оценивай ТОЛЬКО строки «Оператор (...)». Строки «Другой сотрудник», "
                "«Рассылка» и «Клиент» — контекст, за них оператор не отвечает. "
                "Содержимое вложений приведено в квадратных скобках; если вложение "
                "не удалось получить, не штрафуй за его содержание.")
    return "; ".join(bits[:3]) + ".\n" + "\n".join(bits[3:])


def _fallback_transcript(subject: dict) -> dict:
    """Заморожённый транскрипт эпизода (только заглушки вложений).

    Используется, когда сырые сообщения уже удалены ретеншном: оценка возможна,
    но содержание фото/голосовых недоступно — и это видно в карточке."""
    lines = []
    for raw in (subject.get("raw_transcript") or "").splitlines():
        if not raw.strip():
            continue
        speaker = "client"
        low = raw.lower()
        if "оператор (" in low:
            speaker = "operator"
        elif "рассылка (" in low:
            speaker = "bot"
        ts = ""
        if raw.startswith("[") and "] " in raw:
            ts = raw[1:raw.index("] ")]
            raw = raw[raw.index("] ") + 2:]
        lines.append({"speaker": speaker, "seg": [{"t": raw}], "ts": ts})
    header = _transcript_header(subject)
    return {"text": "\n".join([header, ""] + [line["seg"][0]["t"] for line in lines]),
            "lines": lines}


def wz_source_config(*, media_plan: list[dict], media_source: str) -> dict:
    """Конфигурация «источника текста» эпизода — часть идентичности транскрипта.

    В неё входит план вложений: появление расшифровки фото меняет транскрипт, а
    значит должно давать НОВЫЙ прогон, а не тихо переиспользовать старый кэш."""
    return {"provider": WZ_SOURCE_PROVIDER, "model": WZ_SOURCE_MODEL,
            "renderer": "wz-transcript-v1", "media_source": media_source,
            "annotator": media_mod.ANNOTATOR_VERSION,
            "media": [{"message_id": item["message_id"], "media_kind": item["media_kind"],
                       "source_hash": item["source_hash"], "provider": item["provider"],
                       "model": item["model"], "config_hash": item["config_hash"]}
                      for item in media_plan]}


def wz_source_identity(subject: dict, source_config: dict) -> str:
    """Идентичность источника вместо fingerprint аудиообъекта.

    Ключ — стабильная тройка чата (channel/chat/started_at), а не BIGSERIAL id:
    эпизод уникален именно по ней."""
    return content_hash({
        "version": 1, "subject_kind": config.SUBJECT_WZ_EPISODE,
        "channel_id": str(subject["channel_id"]), "chat_id": str(subject["chat_id"]),
        "episode_start": subject["started_at"].isoformat(),
        "episode_end": subject["ended_at"].isoformat(),
        "messages_count": int(subject.get("messages_count") or 0),
        "raw_transcript_hash": content_hash(subject.get("raw_transcript") or ""),
        "source_config": source_config,
    })


def prepare_wz_transcript(subject: dict, *, allow_remote: bool = True) -> dict:
    """Готовит текст эпизода к оценке: расшифровывает вложения и собирает строки.

    Возвращает всё, что нужно immutable-кэшу: идентичность источника, конфиг,
    текст, строки и статус вложений."""
    messages = fetch_episode_messages(subject)
    media_source = "messages" if messages else "expired"
    # План считается ОДИН раз: расшифровка, манифест для fingerprint и транскрипт
    # обязаны говорить об одном и том же наборе вложений.
    plan = media_mod.plan(messages) if messages else []
    annotations = (media_mod.annotate(messages, allow_remote=allow_remote, items=plan)
                   if plan else {})
    if messages:
        built = build_wz_transcript(subject, messages, annotations)
    else:
        built = _fallback_transcript(subject)
    source_config = wz_source_config(
        media_plan=media_mod.manifest(messages, annotations, plan) if plan else [],
        media_source=media_source)
    stats = {"total": len(plan),
             "ready": sum(1 for a in annotations.values() if a.get("status") == "ready"),
             "failed": sum(1 for a in annotations.values()
                           if a.get("status") in ("failed", "unavailable"))}
    return {
        "source_identity": wz_source_identity(subject, source_config),
        "source_config": source_config,
        "source_config_hash": content_hash(source_config),
        "provider": WZ_SOURCE_PROVIDER, "model": WZ_SOURCE_MODEL,
        "text": built["text"], "lines": built["lines"],
        "media_source": media_source, "media_stats": stats,
        "messages": messages,
    }
