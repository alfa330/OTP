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
from datetime import datetime
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


def _chat_manager_direction_family(cur, department_code) -> list[int]:
    """Чатовые направления отдела — по МОДЕЛИ расчёта, а не по названию.

    У СЗоВ переписку ведёт «Чат менеджер», и модель `chat_manager` — тот же
    признак, по которому чатовое направление узнаёт весь остальной портал
    (get_directions, гейт кнопки «Случайный чат»). Название сюда не годится:
    переименование направления молча обнулило бы список."""
    try:
        cur.execute(
            """SELECT d.id
                 FROM directions d
                 LEFT JOIN departments dep ON dep.id = d.department_id
                WHERE lower(COALESCE(dep.code, '')) = %s
                  AND d.calculation_model_code = 'chat_manager'""",
            (config.normalise_department_code(department_code),))
        return [int(r[0]) for r in cur.fetchall()]
    except Exception:
        logging.exception("ai-qa: не удалось вычислить чатовые направления отдела %s",
                          department_code)
        return []


def _mapped_chat_direction_family(cur, department_code) -> list[int]:
    """Направления, для которых шкала чата задана картой (Тез КЦ).

    Техменеджеры ТЭЗ числятся на «ТП линия», а переписка оценивается по шкале
    «ТП чат»: в карте ключ — направление ОПЕРАТОРА, значение — откуда брать
    критерии. Возвращаем и то и другое вместе с архивными версиями обоих, иначе
    уже сохранённая оценка выпала бы из скоупа после правки критериев."""
    ids = set()
    for operator_direction, criteria_direction in config.CHAT_CRITERIA_DIRECTION_MAP.items():
        ids.add(int(operator_direction))
        ids.add(int(criteria_direction))
    if not ids:
        return []
    try:
        cur.execute(
            """SELECT d.id
                 FROM directions d
                 LEFT JOIN departments dep ON dep.id = d.department_id
                WHERE lower(COALESCE(dep.code, '')) = %s
                  AND (d.id = ANY(%s) OR d.canonical_id = ANY(%s))""",
            (config.normalise_department_code(department_code), list(ids), list(ids)))
        return [int(r[0]) for r in cur.fetchall()]
    except Exception:
        logging.exception("ai-qa: не удалось вычислить чатовые направления по карте (%s)",
                          department_code)
        return []


def chat_direction_family(cur, department_code) -> list[int]:
    """Направления отдела, чью переписку оценивает ИИ.

    Правило у каждого отдела своё, потому что своим оно и было ещё до раздела —
    здесь только собрано в одном месте:
      ОП       — маркер «верификатор» в названии (кнопка «Случайный чат»);
      СЗоВ     — модель расчёта `chat_manager` («Чат менеджер»);
      Тез КЦ   — карта AI_QA_CHAT_CRITERIA_MAP («ТП линия» -> шкала «ТП чат»).
    """
    code = config.normalise_department_code(department_code)
    if code == config.OP_DEPARTMENT_CODE:
        return wz_direction_family(cur)
    if code == config.TEZ_DEPARTMENT_CODE:
        return _mapped_chat_direction_family(cur, code)
    return _chat_manager_direction_family(cur, code)


def criteria_direction_id(direction_id):
    """Направление, из которого берётся мониторинговая шкала переписки.

    Обычно оно же и есть; исключение — ТЭЗ, где оператор числится на линии, а
    шкала чата лежит на отдельном направлении (config.CHAT_CRITERIA_DIRECTION_MAP)."""
    if direction_id is None:
        return None
    return config.CHAT_CRITERIA_DIRECTION_MAP.get(int(direction_id), int(direction_id))


# ── загрузка субъекта ────────────────────────────────────────────────────────

def load(subject_kind, subject_id: int) -> dict:
    """Единая форма субъекта для конвейера оценки."""
    kind = normalise_kind(subject_kind)
    try:
        loader = _LOADERS[kind]
    except KeyError:  # pragma: no cover — normalise_kind уже проверил список
        raise ValueError(f"нет загрузчика субъекта {kind!r}")
    return loader(int(subject_id))


def _load_call(call_id: int) -> dict:
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(
            """SELECT c.id, c.direction_id, d.name, u.name,
                      TO_CHAR(c.created_at,'DD.MM.YYYY, HH24:MI'), c.score, c.audio_path,
                      dep.code
                 FROM calls c
                 LEFT JOIN directions d ON c.direction_id = d.id
                 LEFT JOIN departments dep ON dep.id = d.department_id
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
            "human_score": row[5], "audio_path": row[6],
            # Отдел у звонка журнала берём у НАПРАВЛЕНИЯ (это его связь с
            # отделом), а не у оператора: оператора могли перевести, а оценка
            # осталась по шкале прежнего направления.
            "department_code": config.normalise_department_code(row[7])}


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
                      u.direction_id, d.name, dep.code
                 FROM wazzup_episodes e
                 LEFT JOIN users u ON u.id = e.operator_user_id
                 LEFT JOIN directions d ON d.id = u.direction_id
                 LEFT JOIN departments dep ON dep.id = d.department_id
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
        "department_code": config.normalise_department_code(row[22]),
        "eligible_direction_ids": eligible_directions,
        "datetime": (ended.astimezone(ALMATY).strftime("%d.%m.%Y, %H:%M")
                     if ended is not None else "—"),
        "human_score": None,
    }
    return subject


def _load_imported_call(imported_call_id: int) -> dict:
    """Звонок из АТС, которого НЕТ в журнале оценок.

    `imported_calls` наполняет кнопка «Случайный звонок» журнала (СЗоВ — Oktell,
    Тез КЦ — Binotel): запись уже лежит в GCS, а человеческой оценки может не
    быть вовсе. Направления у строки нет — как и у эпизода чата, оно берётся у
    оператора (users.direction_id)."""
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(
            """SELECT ic.id, u.direction_id, d.name, COALESCE(u.name, ic.operator_name),
                      TO_CHAR(ic.datetime_raw AT TIME ZONE 'Asia/Almaty', 'DD.MM.YYYY, HH24:MI'),
                      ic.audio_path, ic.operator_id, ic.phone_number, ic.duration_sec,
                      ic.status, dep.code, hc.score
                 FROM imported_calls ic
                 LEFT JOIN users u ON u.id = ic.operator_id
                 LEFT JOIN directions d ON d.id = u.direction_id
                 -- Отдел берём у НАПРАВЛЕНИЯ, а не у users.department_id:
                 -- отдел входит в prompt_hash, а пометку «устарела» очередь
                 -- считает по отделу направления (_direction_identity_context).
                 -- Значения расходятся на проде (у одного оператора СЗоВ
                 -- направление «Фронт офисов»), и тогда КАЖДАЯ карточка
                 -- показывалась бы устаревшей. Отдел сотрудника оставлен
                 -- запасным — на случай оператора без направления.
                 LEFT JOIN departments dep
                        ON dep.id = COALESCE(d.department_id, u.department_id)
                 LEFT JOIN LATERAL (
                     SELECT c.score FROM calls c
                      WHERE c.imported_call_id = ic.id
                        AND COALESCE(c.is_draft, FALSE) = FALSE
                      ORDER BY c.created_at DESC LIMIT 1
                 ) hc ON TRUE
                WHERE ic.id = %s""", (imported_call_id,))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not row:
        raise SubjectNotFound("звонок не найден")
    if not row[5]:
        # Как и у обычного звонка: отсутствие записи — это отсутствие данных
        # (Binotel докачивает запись асинхронно), а не отказ по существу.
        raise ValueError("у звонка нет записи")
    return {"kind": config.SUBJECT_IMPORTED_CALL, "id": int(row[0]),
            "direction_id": row[1], "direction": row[2], "operator": row[3] or "—",
            "datetime": row[4] or "—", "audio_path": row[5],
            "operator_user_id": row[6], "phone_number": row[7],
            "duration_sec": row[8], "import_status": row[9],
            "department_code": config.normalise_department_code(row[10]),
            "human_score": row[11]}


def _load_c2d_snapshot(snapshot_id: int) -> dict:
    """Переписка Chat2Desk (СЗоВ): единица — ЗАЯВКА, снятая в снапшот.

    Сырых сообщений Chat2Desk локально нет, поэтому субъект — снапшот: он уже
    скачан, живёт 180 дней и служит источником и человеческой оценке, и этой."""
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(
            """SELECT s.id, s.request_id, s.dialog_id, s.day, s.operator_id, u.name,
                      s.c2d_operator_name, s.channel_name, s.transport, s.client_name,
                      s.client_phone, s.messages, s.messages_count, s.created_at,
                      u.direction_id, d.name, dep.code
                 FROM c2d_chat_snapshots s
                 LEFT JOIN users u ON u.id = s.operator_id
                 LEFT JOIN directions d ON d.id = u.direction_id
                 -- Отдел берём у НАПРАВЛЕНИЯ, а не у users.department_id:
                 -- отдел входит в prompt_hash, а пометку «устарела» очередь
                 -- считает по отделу направления (_direction_identity_context).
                 -- Значения расходятся на проде (у одного оператора СЗоВ
                 -- направление «Фронт офисов»), и тогда КАЖДАЯ карточка
                 -- показывалась бы устаревшей. Отдел сотрудника оставлен
                 -- запасным — на случай оператора без направления.
                 LEFT JOIN departments dep
                        ON dep.id = COALESCE(d.department_id, u.department_id)
                WHERE s.id = %s AND s.source = 'chat2desk'""", (snapshot_id,))
        row = cur.fetchone()
        department_code = config.normalise_department_code(row[16]) if row else ""
        eligible = chat_direction_family(cur, department_code) if row else []
        cur.close()
    finally:
        conn.close()
    if not row:
        raise SubjectNotFound("переписка не найдена")
    messages = row[11] or []
    day = row[3]
    return {"kind": config.SUBJECT_C2D_SNAPSHOT, "id": int(row[0]),
            "request_id": row[1], "dialog_id": row[2], "day": day,
            "operator_user_id": row[4], "operator": row[5] or row[6] or "—",
            "channel_name": row[7], "transport": row[8],
            "contact_name": row[9], "contact_phone": row[10],
            "messages": messages, "messages_count": int(row[12] or len(messages)),
            "created_at": row[13],
            "direction_id": row[14], "direction": row[15],
            "department_code": department_code,
            "eligible_direction_ids": eligible,
            "datetime": day.strftime("%d.%m.%Y") if day is not None else "—",
            "human_score": None}


def _load_ca_episode(episode_id: int) -> dict:
    """Эпизод переписки ChatApp (Тез КЦ).

    Таблица построена тем же ночным билдером, что и wazzup_episodes, поэтому
    поля атрибуции те же. Отличие одно: замороженного транскрипта у эпизода нет
    (см. комментарий к DDL) — текст собирается из chatapp_messages."""
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(
            """SELECT e.id, e.license_id, e.messenger_type, e.chat_id, e.contact_name,
                      e.contact_phone, e.started_at, e.ended_at, e.messages_count,
                      e.inbound_count, e.outbound_count, e.human_outbound_count,
                      e.kind, e.operator_user_id, u.name, e.operator_share,
                      e.authors, e.force_closed, u.direction_id, d.name, dep.code
                 FROM chatapp_episodes e
                 LEFT JOIN users u ON u.id = e.operator_user_id
                 LEFT JOIN directions d ON d.id = u.direction_id
                 -- Отдел берём у НАПРАВЛЕНИЯ, а не у users.department_id:
                 -- отдел входит в prompt_hash, а пометку «устарела» очередь
                 -- считает по отделу направления (_direction_identity_context).
                 -- Значения расходятся на проде (у одного оператора СЗоВ
                 -- направление «Фронт офисов»), и тогда КАЖДАЯ карточка
                 -- показывалась бы устаревшей. Отдел сотрудника оставлен
                 -- запасным — на случай оператора без направления.
                 LEFT JOIN departments dep
                        ON dep.id = COALESCE(d.department_id, u.department_id)
                WHERE e.id = %s""", (episode_id,))
        row = cur.fetchone()
        department_code = config.normalise_department_code(row[20]) if row else ""
        eligible = chat_direction_family(cur, department_code) if row else []
        cur.close()
    finally:
        conn.close()
    if not row:
        raise SubjectNotFound("эпизод чата не найден")
    ended = row[7]
    return {"kind": config.SUBJECT_CA_EPISODE, "id": int(row[0]),
            "license_id": int(row[1]), "messenger_type": row[2], "chat_id": row[3],
            # Обобщённый ключ эпизода, тот же, что у снапшота ChatApp
            # (c2d_chat_snapshots.wz_channel_id): '<licenseId>:<messengerType>'.
            "channel_id": f"{int(row[1])}:{row[2]}",
            "contact_name": row[4], "contact_phone": row[5],
            "started_at": row[6], "ended_at": ended,
            "messages_count": int(row[8] or 0), "inbound_count": int(row[9] or 0),
            "outbound_count": int(row[10] or 0), "human_outbound_count": int(row[11] or 0),
            "episode_kind": row[12], "operator_user_id": row[13],
            "operator": row[14] or "—", "operator_share": row[15],
            "authors": row[16] or [], "force_closed": bool(row[17]),
            "raw_transcript": "", "context_tail": None,
            "direction_id": row[18], "direction": row[19],
            "department_code": department_code,
            "eligible_direction_ids": eligible,
            "datetime": (ended.astimezone(ALMATY).strftime("%d.%m.%Y, %H:%M")
                         if ended is not None else "—"),
            "human_score": None}


_LOADERS = {
    config.SUBJECT_CALL: _load_call,
    config.SUBJECT_WZ_EPISODE: _load_wz_episode,
    config.SUBJECT_IMPORTED_CALL: _load_imported_call,
    config.SUBJECT_C2D_SNAPSHOT: _load_c2d_snapshot,
    config.SUBJECT_CA_EPISODE: _load_ca_episode,
}


# ── можно ли оценивать ───────────────────────────────────────────────────────

def eligibility(subject: dict) -> dict:
    """Проверка «оценка будет честной», без обращения к моделям.

    Для эпизода чата главное — атрибуция: в одном эпизоде могут отвечать
    несколько операторов, и тогда оценка «в одни руки» приписывает одному
    человеку работу другого. Порог доли ответов доминирующего оператора —
    config.WZ_MIN_OPERATOR_SHARE (по умолчанию 90%).

    У звонка такого вопроса нет: запись принадлежит одному оператору целиком."""
    kind = subject["kind"]
    if kind in config.AUDIO_SUBJECT_KINDS:
        return {"ok": True, "reason": None, "detail": {}}
    if kind == config.SUBJECT_C2D_SNAPSHOT:
        return _c2d_eligibility(subject)
    if kind == config.SUBJECT_CA_EPISODE:
        return _episode_eligibility(
            subject, min_share=config.CA_MIN_OPERATOR_SHARE,
            min_messages=config.CA_MIN_OPERATOR_MESSAGES,
            foreign_direction_message=(
                "направление «%s» не оценивается по чатовой шкале отдела"))
    return _episode_eligibility(
        subject, min_share=config.WZ_MIN_OPERATOR_SHARE,
        min_messages=config.WZ_MIN_OPERATOR_MESSAGES,
        foreign_direction_message=(
            "направление «%s» не относится к Верификаторам — "
            "чаты оцениваются только по их шкале"))


def _episode_eligibility(subject: dict, *, min_share: float, min_messages: int,
                         foreign_direction_message: str) -> dict:
    """Общий гейт эпизодных источников (Wazzup, ChatApp) — у них одни колонки.

    Тексты отказов у Верификаторов обязаны остаться прежними: они видны в
    карточке и закреплены тестами."""
    share = subject.get("operator_share")
    human_out = int(subject.get("human_outbound_count") or 0)
    detail = {
        "operator_share": share,
        "operator_share_pct": (round(float(share) * 100) if share is not None else None),
        "min_operator_share_pct": round(min_share * 100),
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
                "message": (foreign_direction_message
                            % (subject.get("direction") or subject["direction_id"]))}
    if share is None or float(share) + 1e-9 < min_share:
        pct = detail["operator_share_pct"]
        return {"ok": False, "reason": REASON_SHARE, "detail": detail,
                "message": ("в эпизоде отвечали несколько операторов: у оператора "
                            f"{pct if pct is not None else 0}% ответов из "
                            f"{human_out} (нужно не меньше "
                            f"{detail['min_operator_share_pct']}%) — "
                            "оценить одного человека нельзя")}
    if human_out < min_messages:
        return {"ok": False, "reason": REASON_FEW_MESSAGES, "detail": detail,
                "message": (f"в эпизоде всего {human_out} ответ(а) оператора — "
                            "недостаточно для оценки по шкале")}
    return {"ok": True, "reason": None, "detail": detail}


def _c2d_eligibility(subject: dict) -> dict:
    """Гейт переписки Chat2Desk. Атрибуция здесь устроена иначе.

    У Chat2Desk нет эпизодов и нет автора у каждого сообщения — единица это
    ЗАЯВКА, закреплённая за одним оператором (тем же, по которому её оценивает
    человек). Поэтому доли ответов не существует, и вместо неё проверяется то,
    что реально может сделать оценку нечестной: РУЧНАЯ ПЕРЕДАЧА чата другому
    сотруднику посреди заявки. Автоназначение в начале («Причина —
    автоназначение чата системой») передачей не считается — оно и есть выдача
    заявки оператору.

    Проверено на проде 07.09.2026: из 1972 неоценённых заявок с ≥2 ответами
    оператора реально поделены между людьми только 131."""
    messages = subject.get("messages") or []
    operator_messages = 0
    operator_messages_before_transfer = 0
    transfers = 0
    for message in messages:
        mtype = str((message or {}).get("type") or "")
        if mtype == "to_client":
            operator_messages += 1
        elif mtype == "system" and _is_manual_transfer(message):
            transfers += 1
            operator_messages_before_transfer = operator_messages
    detail = {"operator_messages": operator_messages,
              "min_operator_messages": config.C2D_MIN_OPERATOR_MESSAGES,
              "transfers": transfers,
              "operator_messages_before_transfer": operator_messages_before_transfer,
              "messages_count": len(messages)}
    if not subject.get("operator_user_id"):
        return {"ok": False, "reason": REASON_NO_OPERATOR, "detail": detail,
                "message": "заявка не закреплена за сотрудником — оценивать некого"}
    if subject.get("direction_id") is None:
        return {"ok": False, "reason": REASON_NO_DIRECTION, "detail": detail,
                "message": "у оператора не указано направление — нет мониторинговой шкалы"}
    eligible = {int(x) for x in (subject.get("eligible_direction_ids") or [])}
    if eligible and int(subject["direction_id"]) not in eligible:
        return {"ok": False, "reason": REASON_DIRECTION, "detail": detail,
                "message": ("направление «%s» не оценивается по чатовой шкале отдела"
                            % (subject.get("direction") or subject["direction_id"]))}
    if operator_messages_before_transfer > 0:
        return {"ok": False, "reason": REASON_SHARE, "detail": detail,
                "message": ("заявку вели несколько сотрудников: до передачи чата уже было "
                            f"{operator_messages_before_transfer} ответ(а) — "
                            "оценить одного человека нельзя")}
    if operator_messages < config.C2D_MIN_OPERATOR_MESSAGES:
        return {"ok": False, "reason": REASON_FEW_MESSAGES, "detail": detail,
                "message": (f"в заявке всего {operator_messages} ответ(а) оператора — "
                            "недостаточно для оценки по шкале")}
    return {"ok": True, "reason": None, "detail": detail}


def _is_manual_transfer(message: dict) -> bool:
    """Системная строка «Чат передан от A к B» БЕЗ пометки автоназначения.

    Автоназначение — это первичная выдача заявки, а не передача работы: на проде
    таких строк 1690 из 1877, и считать их передачей означало бы отсечь почти
    весь пул."""
    text = str((message or {}).get("text") or "").strip().lower()
    if not text.startswith("чат передан"):
        return False
    return "автоназначен" not in text


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
             "missing_call": "пропущенный звонок", "unsupported": "вложение",
             "sticker": "стикер"}


# ── чужие источники приводятся к форме сообщения Wazzup ──────────────────────
# Один вид сообщения на все три источника — сознательно: план вложений, их
# расшифровка и сборка транскрипта уже написаны под эту форму, и четвёртая копия
# нормализатора разошлась бы с остальными (у человеческой стороны так и вышло —
# в снапшотах Chat2Desk нет ключа `author`, который добавили двум другим).

def _fetch_chatapp_messages(subject: dict) -> list[dict]:
    """Сообщения эпизода ChatApp в форме сообщения Wazzup.

    chatapp_messages живут ограниченное время, эпизод — бессрочно; пустой список
    означает «сырые сообщения уже удалены ретеншном», а не «сообщений не было»."""
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(
            """SELECT m.message_id, m.dt, m.side, m.type, m.subtype, m.text,
                      m.file_link, m.file_name, m.file_content_type, m.employee_id,
                      m.app_id, m.app_sender, m.client_name, m.is_deleted,
                      map.employee_name, map.user_id, COALESCE(map.is_bot, FALSE), u.name
                 FROM chatapp_messages m
                 LEFT JOIN chatapp_operator_map map ON map.employee_id = m.employee_id
                 LEFT JOIN users u ON u.id = map.user_id
                WHERE m.license_id = %s AND m.messenger_type = %s AND m.chat_id = %s
                  AND m.dt >= %s AND m.dt <= %s
                ORDER BY m.dt, m.message_id""",
            (subject["license_id"], subject["messenger_type"], subject["chat_id"],
             subject["started_at"], subject["ended_at"]))
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    out = []
    for r in rows:
        (message_id, dt, side, mtype, subtype, text, file_link, _file_name,
         content_type, employee_id, app_id, app_sender, client_name, is_deleted,
         employee_name, user_id, is_bot, user_name) = r
        if str(mtype or "") == "system":
            # Служебные строки ChatApp — это не реплики, а события платформы.
            continue
        outgoing = str(side or "") == "out"
        # «Робот» здесь — не только помеченный в карте: рассылка (template) и
        # отправка не из окна оператора тоже не работа человека.
        robot = bool(is_bot) or str(mtype or "") == "template" or (
            outgoing and (str(app_sender or "") == "system"
                          or str(app_id or "") not in ("webchat", "")))
        out.append({
            # message_id уникален только внутри чата — для общей таблицы
            # расшифровок вложений его надо развернуть в глобальный ключ.
            "message_id": f"ca:{subject['license_id']}:{subject['messenger_type']}:"
                          f"{subject['chat_id']}:{message_id}",
            "dt": dt, "is_echo": outgoing,
            "type": _chatapp_media_type(mtype, subtype, content_type),
            "text": text, "content_uri": file_link or None,
            "author_name": employee_name or (client_name if not outgoing else None),
            "author_id": str(employee_id) if employee_id is not None else None,
            "is_bot": robot, "is_deleted": bool(is_deleted),
            "user_id": user_id, "matched_name": user_name,
            "channel_id": subject["channel_id"], "chat_id": subject["chat_id"],
        })
    return out


def _chatapp_media_type(mtype: str, subtype, content_type) -> str:
    """Тип ChatApp -> тип, который понимает media._kind_of.

    У ChatApp «file» значит ровно то же, что «document» у Wazzup — «какой-то
    файл», и решает расширение ссылки (там и PDF, и docx, и zip).

    Решающим считается CONTENT-TYPE, а не заявленный тип: так же классифицирует
    человеческая сторона (_chatapp_normalize_snapshot_message), и на проде есть
    строки, где type и содержимое расходятся. Без этого рассылка (`template`) с
    картинкой не получала бы даже заглушки: media._kind_of вернул бы «unknown»,
    подписи в _MEDIA_RU для «template» нет, и вложение исчезало бы из транскрипта
    молча — модель видела бы пустую реплику вместо картинки."""
    declared = str(mtype or "").strip().lower()
    ctype = str(content_type or "").strip().lower()
    for prefix, media_type in (("image/", "image"), ("audio/", "audio"),
                               ("video/", "video")):
        if ctype.startswith(prefix):
            return media_type
    if ctype == "application/pdf":
        return "document"
    if declared == "file" or ctype:
        # Есть файл, но тип нам не знаком — пусть решает расширение ссылки.
        return "document"
    if declared in ("image", "audio", "video", "sticker"):
        return declared
    if declared == "location":
        return "geo"
    return declared or "text"


def _snapshot_messages(subject: dict, *, prefix: str, channel_id: str, chat_id: str,
                       authored: bool) -> list[dict]:
    """Сообщения СНАПШОТА переписки в форме сообщения Wazzup.

    Снапшот — единый формат ленты для всех источников (его пишет человеческая
    сторона), поэтому разбор общий. Отличий два, и оба существенные:

    * `prefix` + ключ чата — из них собирается ГЛОБАЛЬНЫЙ message_id для таблицы
      расшифровок вложений: у Chat2Desk id сообщения глобальный, у ChatApp —
      уникален только внутри чата. Префикс обязан совпадать с тем, что даёт
      «сырой» путь того же источника, иначе одно и то же вложение
      расшифровывалось бы дважды, под двумя ключами.
    * `authored` — есть ли у сообщения автор. У Chat2Desk его нет вовсе: сторона
      определяется типом (`to_client` — оператор, `from_client` — клиент), а
      заявка закреплена за одним оператором. У ChatApp автор есть, и его НАДО
      читать: порог атрибуции допускает до 10% ответов другого сотрудника, и без
      автора его реплики подписались бы оцениваемым оператором.

    `comment` — ВНУТРЕННЯЯ заметка оператора, клиент её не видит; в оценку она не
    идёт, иначе заметка «нет ответа/обед» читалась бы как реплика клиенту.
    """
    target_name = str(subject.get("operator") or "").strip()
    out = []
    for raw in (subject.get("messages") or []):
        message = raw or {}
        mtype = str(message.get("type") or "")
        if mtype in ("system", "comment"):
            continue
        created = _parse_snapshot_dt(message.get("created"))
        if created is None:
            continue
        media_type, uri = _c2d_media(message)
        outgoing = mtype != "from_client"
        author = str(message.get("author") or "").strip() if authored else ""
        # Своим считаем исходящее сообщение автора, совпавшего с оцениваемым
        # оператором; при отсутствии автора — любое исходящее (Chat2Desk).
        own = outgoing and (not authored or not author or author == target_name)
        out.append({
            "message_id": f"{prefix}{message.get('id')}",
            "dt": created, "is_echo": outgoing,
            "type": media_type, "text": message.get("text") or "",
            "content_uri": uri,
            "author_name": ((author or target_name) if outgoing
                            else (subject.get("contact_name") or None)),
            "author_id": None,
            "is_bot": mtype == "autoreply",
            "is_deleted": False,
            "user_id": subject.get("operator_user_id") if own else None,
            "matched_name": ((target_name or None) if own
                             else (author or None) if outgoing else None),
            "channel_id": channel_id,
            "chat_id": chat_id,
        })
    return out


def _c2d_snapshot_messages(subject: dict) -> list[dict]:
    """Сообщения снапшота Chat2Desk (см. _snapshot_messages)."""
    return _snapshot_messages(
        subject, prefix="c2d:",
        channel_id=str(subject.get("channel_name") or ""),
        chat_id=str(subject.get("request_id") or subject["id"]),
        authored=False)


def _c2d_media(message: dict) -> tuple[str, str | None]:
    """Вложение снапшота: у Chat2Desk оно разложено по отдельным полям."""
    for field, media_type in (("photo", "image"), ("video", "video"),
                              ("audio", "audio"), ("pdf", "document")):
        uri = message.get(field)
        if uri:
            return media_type, str(uri)
    attachments = message.get("attachments") or []
    if attachments:
        first = attachments[0]
        uri = first.get("link") or first.get("url") if isinstance(first, dict) else first
        if uri:
            return "document", str(uri)
    return "text", None


def _parse_snapshot_dt(value):
    """Время сообщения снапшота. Оно записано наивной локалью Алматы."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=ALMATY)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=ALMATY)


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
        deleted = bool(msg.get("is_deleted"))
        media_kind = media_mod._kind_of(msg.get("type"), msg.get("content_uri"))
        media_label = _MEDIA_RU.get(str(msg.get("type") or ""))
        annotatable = bool(media_label and msg.get("content_uri")
                           and media_mod.annotatable(msg.get("type"), msg.get("content_uri")))
        ann = annotations.get(str(msg.get("message_id"))) if annotatable else None
        text = msg.get("text") or ""

        # Инвариант: seg карточки == строке модели (без времени) — цитата разбора
        # проверяется по тексту карточки, оценка идёт по тексту модели. Поэтому seg
        # НЕ трогаем (аннотация вшита), а для нового вида чата добавляем ОТДЕЛЬНЫЕ поля.
        parts = []
        if deleted:
            parts.append("[удалено]")
        if media_label:
            parts.append(media_mod.annotation_text(media_kind, ann) if annotatable
                         else f"[{media_label}]")
        if text:
            parts.append(text)
        if not parts:
            parts.append(f"[{msg.get('type') or 'сообщение'}]")
        body = " ".join(parts)
        text_lines.append(f"[{stamp}] {who}: {body}")

        # Тело для вида-чата (без префикса «кто:» и без вшитой аннотации — она
        # показывается отдельным блоком под фото/аудио).
        clean_parts = []
        if deleted:
            clean_parts.append("[удалено]")
        if media_label and not annotatable:
            clean_parts.append(f"[{media_label}]")
        if text:
            clean_parts.append(text)
        line = {"speaker": speaker, "seg": [{"t": f"{who}: {body}"}], "ts": stamp,
                "body": " ".join(clean_parts),
                "created": msg["dt"].astimezone(ALMATY).isoformat(),
                "outgoing": speaker != "client",
                "author": msg.get("matched_name") or msg.get("author_name"),
                "message_id": str(msg.get("message_id"))}
        if msg.get("content_uri") and media_label:
            line["media"] = {"kind": media_kind if media_kind != "unknown" else "file",
                             "label": media_label, "url": msg["content_uri"]}
        if annotatable:
            if ann and ann.get("status") == "ready" and ann.get("annotation"):
                line["annotation"] = {"kind": media_kind, "status": "ready",
                                      "text": ann["annotation"]}
            else:
                line["annotation"] = {"kind": media_kind,
                                      "status": (ann or {}).get("status") or "missing",
                                      "error": (ann or {}).get("error")}
        lines.append(line)

    header = _transcript_header(subject)
    return {"text": "\n".join([header, ""] + text_lines) if text_lines else header,
            "lines": lines}


# Первая строка шапки транскрипта. У Верификаторов она обязана остаться
# байт-в-байт: шапка входит в текст, а значит в transcript_hash и в отпечаток.
_HEADER_TITLE = {
    config.SUBJECT_WZ_EPISODE: "ЧАТ WhatsApp (Wazzup), эпизод #{id}",
    config.SUBJECT_CA_EPISODE: "ЧАТ WhatsApp (ChatApp), эпизод #{id}",
    config.SUBJECT_C2D_SNAPSHOT: "ЧАТ (Chat2Desk), заявка #{request}",
}


def _transcript_header(subject: dict) -> str:
    share = subject.get("operator_share")
    pct = round(float(share) * 100) if share is not None else None
    title = _HEADER_TITLE.get(subject.get("kind"), _HEADER_TITLE[config.SUBJECT_WZ_EPISODE])
    bits = [title.format(id=subject["id"],
                         request=subject.get("request_id") or subject["id"]),
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


# Провайдер «источника текста» на каждый вид переписки. Строки РАЗНЫЕ не для
# красоты: provider входит и в ключ ai_transcript_cache, и в его идентичность.
_CHAT_SOURCE_PROVIDER = {
    config.SUBJECT_WZ_EPISODE: WZ_SOURCE_PROVIDER,
    config.SUBJECT_CA_EPISODE: config.CA_SOURCE_PROVIDER,
    config.SUBJECT_C2D_SNAPSHOT: config.C2D_SOURCE_PROVIDER,
}


def chat_source_provider(subject_kind) -> str:
    return _CHAT_SOURCE_PROVIDER.get(subject_kind, WZ_SOURCE_PROVIDER)


def wz_source_config(*, media_plan: list[dict], media_source: str,
                     subject_kind: str = config.SUBJECT_WZ_EPISODE) -> dict:
    """Конфигурация «источника текста» эпизода — часть идентичности транскрипта.

    В неё входит план вложений: появление расшифровки фото меняет транскрипт, а
    значит должно давать НОВЫЙ прогон, а не тихо переиспользовать старый кэш."""
    return {"provider": chat_source_provider(subject_kind), "model": WZ_SOURCE_MODEL,
            "renderer": "wz-transcript-v1", "media_source": media_source,
            "annotator": media_mod.ANNOTATOR_VERSION,
            "media": [{"message_id": item["message_id"], "media_kind": item["media_kind"],
                       "source_hash": item["source_hash"], "provider": item["provider"],
                       "model": item["model"], "config_hash": item["config_hash"]}
                      for item in media_plan]}


def wz_source_identity(subject: dict, source_config: dict) -> str:
    """Идентичность источника вместо fingerprint аудиообъекта.

    Ключ — стабильная тройка чата (channel/chat/started_at), а не BIGSERIAL id:
    эпизод уникален именно по ней. Для заявки Chat2Desk такой тройки нет, её
    ключ — request_id (он же UNIQUE у снапшота)."""
    if subject.get("kind") == config.SUBJECT_C2D_SNAPSHOT:
        return content_hash({
            "version": 1, "subject_kind": config.SUBJECT_C2D_SNAPSHOT,
            "request_id": str(subject.get("request_id") or subject["id"]),
            "snapshot_id": int(subject["id"]),
            "messages_count": int(subject.get("messages_count") or 0),
            "messages_hash": content_hash(subject.get("messages") or []),
            "source_config": source_config,
        })
    return content_hash({
        # subject_kind берём у субъекта, а не литералом: у эпизода Wazzup
        # значение то же, что и было, поэтому отпечатки прежних прогонов целы.
        "version": 1, "subject_kind": subject.get("kind", config.SUBJECT_WZ_EPISODE),
        "channel_id": str(subject["channel_id"]), "chat_id": str(subject["chat_id"]),
        "episode_start": subject["started_at"].isoformat(),
        "episode_end": subject["ended_at"].isoformat(),
        "messages_count": int(subject.get("messages_count") or 0),
        "raw_transcript_hash": content_hash(subject.get("raw_transcript") or ""),
        "source_config": source_config,
    })


def chat_messages(subject: dict) -> tuple[list[dict], str]:
    """Сообщения переписки + откуда они взяты (для media_source).

    Порядок источников у каждого вида свой:
      Wazzup   — сырые сообщения, иначе заморожённый транскрипт эпизода;
      ChatApp  — сырые сообщения, иначе снапшот (замороженного текста у эпизода
                 нет вовсе, см. DDL chatapp_episodes);
      Chat2Desk — только снапшот: сырых сообщений локально не существует.
    """
    kind = subject["kind"]
    if kind == config.SUBJECT_C2D_SNAPSHOT:
        return _c2d_snapshot_messages(subject), "snapshot"
    if kind == config.SUBJECT_CA_EPISODE:
        messages = _fetch_chatapp_messages(subject)
        if messages:
            return messages, "messages"
        return _ca_snapshot_messages(subject), "snapshot"
    return fetch_episode_messages(subject), "messages"


def _ca_snapshot_messages(subject: dict) -> list[dict]:
    """Переписка эпизода ChatApp из снапшота — запас на случай ретеншна.

    Снапшот кладёт «Случайный чат» журнала по тому же ключу эпизода
    ('<licenseId>:<messengerType>' + chat_id + начало)."""
    conn = config.connect_ro()
    try:
        cur = conn.cursor()
        cur.execute("SET client_encoding TO 'UTF8'")
        cur.execute(
            """SELECT s.messages FROM c2d_chat_snapshots s
                WHERE s.source = 'chatapp' AND s.wz_channel_id = %s
                  AND s.wz_chat_id = %s AND s.episode_start = %s
                LIMIT 1""",
            (subject["channel_id"], str(subject["chat_id"]), subject["started_at"]))
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()
    if not row or not row[0]:
        return []
    # Ключ вложений и автор — как на «сыром» пути ChatApp: иначе то же вложение
    # расшифровывалось бы вторично под ключом Chat2Desk, а реплики другого
    # сотрудника подписались бы оцениваемым оператором.
    return _snapshot_messages(
        dict(subject, messages=row[0]),
        prefix=f"ca:{subject['license_id']}:{subject['messenger_type']}:"
               f"{subject['chat_id']}:",
        channel_id=subject["channel_id"], chat_id=str(subject["chat_id"]),
        authored=True)


def prepare_wz_transcript(subject: dict, *, allow_remote: bool = True) -> dict:
    """Готовит текст переписки к оценке: расшифровывает вложения и собирает строки.

    Возвращает всё, что нужно immutable-кэшу: идентичность источника, конфиг,
    текст, строки и статус вложений."""
    kind = subject["kind"]
    messages, origin = chat_messages(subject)
    media_source = origin if messages else "expired"
    # План считается ОДИН раз: расшифровка, манифест для fingerprint и транскрипт
    # обязаны говорить об одном и том же наборе вложений.
    plan = media_mod.plan(messages) if messages else []
    annotations = (media_mod.annotate(messages, allow_remote=allow_remote, items=plan)
                   if plan else {})
    if messages:
        built = build_wz_transcript(subject, messages, annotations)
    elif kind == config.SUBJECT_WZ_EPISODE:
        built = _fallback_transcript(subject)
    else:
        # Ни сырых сообщений, ни снапшота: оценивать нечего, и молча выдать одну
        # шапку нельзя — модель оценила бы пустоту и поставила балл.
        raise SubjectNotEvaluable(
            "переписка недоступна: сообщения уже удалены ретеншном, а снапшота нет",
            reason="messages_expired",
            detail={"messages_count": int(subject.get("messages_count") or 0)})
    # Гейт считает ответы оператора по колонкам эпизода, а транскрипт собирается
    # из сообщений — и они могут расходиться (например, рассылки `template`
    # попадают в human_outbound_count, но в реплики оператора нет). Оценивать
    # переписку, где НИ ОДНОЙ строки оцениваемого оператора не осталось, нельзя:
    # модель поставила бы балл за пустоту.
    if not any(line.get("speaker") == "operator" for line in built["lines"]):
        raise SubjectNotEvaluable(
            "в переписке нет ни одной реплики оцениваемого оператора",
            reason=REASON_FEW_MESSAGES,
            detail={"messages": len(built["lines"]),
                    "human_outbound_count": subject.get("human_outbound_count")})
    provider = chat_source_provider(kind)
    source_config = wz_source_config(
        media_plan=media_mod.manifest(messages, annotations, plan) if plan else [],
        media_source=media_source, subject_kind=kind)
    stats = {"total": len(plan),
             "ready": sum(1 for a in annotations.values() if a.get("status") == "ready"),
             "failed": sum(1 for a in annotations.values()
                           if a.get("status") in ("failed", "unavailable"))}
    return {
        "source_identity": wz_source_identity(subject, source_config),
        "source_config": source_config,
        "source_config_hash": content_hash(source_config),
        "provider": provider, "model": WZ_SOURCE_MODEL,
        "text": built["text"], "lines": built["lines"],
        "media_source": media_source, "media_stats": stats,
        "messages": messages,
    }
