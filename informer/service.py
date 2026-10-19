# -*- coding: utf-8 -*-
"""Рассылки автоинформатора: создание, очередь звонков для диспетчера, итоги и аналитика."""
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

import psycopg2.errors
from psycopg2.extras import execute_values

from dial_list.service import mask_phone

from . import rules, schema, telephone, wav

log = logging.getLogger(__name__)

# Один ключ блокировки на раздачу звонков: два параллельных опроса (перезапуск диспетчера,
# повтор после таймаута) не должны раздать один и тот же темп дважды.
POLL_LOCK_KEY = 0x1F0_2610_15
ROBOT_ONLINE_SECONDS = 90
MAX_LINE_CAPACITY = 4          # верх, которому верим от диспетчера (у Binotel сейчас 2)
DETAIL_TARGETS_LIMIT = 500
DETAILS_MAX_CHARS = 8000
POLL_SOON_SEC = 2
POLL_IDLE_SEC = 10
POLL_AMI_DOWN_SEC = 15


class InformerError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = int(status)


def _moment(value):
    """Момент от диспетчера: секунды эпохи (float) → aware datetime | None."""
    if value in (None, ''):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise InformerError('Время — секунды эпохи')
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, timezone.utc)


def _non_negative_int(value, cap):
    if value in (None, ''):
        return None
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        raise InformerError('Длительность — целые миллисекунды')
    return max(0, min(number, cap))


SCRIPT_SOURCES = ('tts', 'upload')
SCRIPT_TEXT_MAX = 1500


def clean_script(raw):
    """Что звучит в рассылке — для карточки: озвучка (голос, темп, тексты) или загруженные записи."""
    raw = raw if isinstance(raw, dict) else {}
    source = raw.get('source') if raw.get('source') in SCRIPT_SOURCES else 'upload'
    out = {"source": source}
    if source == 'tts':
        out["voice"] = str(raw.get('voice') or '')[:32]
        out["speed"] = str(raw.get('speed') or '')[:16]
        texts = raw.get('texts') if isinstance(raw.get('texts'), dict) else {}
        out["texts"] = {lang: " ".join(str(texts.get(lang) or '').split())[:SCRIPT_TEXT_MAX]
                        for lang in rules.PARTS_ORDER}
    return out


class InformerService:
    def __init__(self, db, clock=rules.now_utc):
        self.db = db
        self.clock = clock

    # ── схема ────────────────────────────────────────────────────────────────
    def ready(self):
        if not schema.ensure_schema(self.db):
            raise InformerError('Автоинформатор временно недоступен', 503)

    # ── рассылки (руководитель) ──────────────────────────────────────────────
    def create_campaign(self, department_id, created_by, *, name, wav_bytes, parts, recipients,
                        file_name='', window_start=None, window_end=None, max_per_minute=None,
                        max_attempts=None, retry_after_min=None, script=None):
        """Новая рассылка (черновик): запись, части, список водителей. Звонить начнёт «Запустить».

        Запись — оригинал 24 кГц (склеивает браузер); робот играет её телефонную версию 8 кГц, которую
        сводит сервер (informer/telephone.py). WAV 8 кГц — запись прежней страницы: оригинала у неё нет."""
        self.ready()
        name = str(name or '').strip()
        if not name:
            raise InformerError('Назовите рассылку')
        try:
            audio = wav.inspect(wav_bytes, rates=(wav.MASTER_RATE, wav.PHONE_RATE))
            clean = rules.clean_parts(parts, audio['duration_ms'])
            start = rules.parse_hhmm(window_start, rules.DEFAULT_WINDOW[0])
            end = rules.parse_hhmm(window_end, rules.DEFAULT_WINDOW[1])
            # Не задан — сколько тянут линии робота при этой записи (то же число, что стоит в поле формы).
            fits = rules.line_rate(rules.line_capacity(self.robot_status().get('lines')), audio['duration_ms'])
            per_minute = rules.clamp_int(max_per_minute, fits, 1, rules.MAX_PER_MINUTE, 'Звонков в минуту')
            attempts = rules.clamp_int(max_attempts, rules.DEFAULT_MAX_ATTEMPTS, 1,
                                       rules.MAX_ATTEMPTS_LIMIT, 'Попыток на водителя')
            retry = rules.clamp_int(retry_after_min, rules.DEFAULT_RETRY_MINUTES, 5, 24 * 60,
                                    'Повтор через, мин')
        except ValueError as exc:
            raise InformerError(str(exc))
        if start == end:
            raise InformerError('Окно рассылки пустое: начало совпадает с концом')
        rows = list((recipients or {}).get('rows') or [])
        if not rows:
            raise InformerError('В списке нет ни одного номера')
        script_json = json.dumps(clean_script(script), ensure_ascii=False)
        if audio['sample_rate'] == wav.MASTER_RATE:
            master, phone = wav_bytes, telephone.from_master(wav_bytes)
        else:
            master, phone = None, wav_bytes
        # Телефонная версия не короче оригинала (⌈n/3⌉ отсчётов), поэтому части в неё помещаются.
        phone_ms = wav.inspect(phone)['duration_ms']
        sha = hashlib.sha256(phone).hexdigest()
        with self.db._get_cursor() as cur:
            cur.execute("""
                INSERT INTO informer_campaigns
                    (department_id, name, status, audio_wav, audio_master, audio_sha256, audio_ms, parts,
                     script, window_start, window_end, max_per_minute, max_attempts, retry_after_min,
                     targets_total, file_name, created_by, created_at)
                VALUES (%s, %s, 'draft', %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (int(department_id), name[:200], phone, master, sha, phone_ms, json.dumps(clean),
                  script_json, start, end, per_minute, attempts, retry, len(rows), str(file_name or '')[:255],
                  created_by, self.clock()))
            campaign_id = cur.fetchone()[0]
            execute_values(cur, """
                INSERT INTO informer_targets (campaign_id, phone, full_name, row_number, next_call_at)
                VALUES %s ON CONFLICT (campaign_id, phone) DO NOTHING
            """, [(campaign_id, phone, full_name, row_number, self.clock())
                  for row_number, full_name, phone in rows], page_size=1000)
        return self.campaign_detail(department_id, campaign_id, targets_limit=0)

    def list_campaigns(self, department_id):
        self.ready()
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT c.id, c.name, c.status, c.audio_ms, c.parts, c.window_start, c.window_end,
                       c.max_per_minute, c.max_attempts, c.retry_after_min, c.targets_total, c.created_at,
                       c.started_at, c.finished_at, c.file_name,
                       COUNT(t.id) FILTER (WHERE t.state = 'done'),
                       COUNT(t.id) FILTER (WHERE t.state = 'given_up'),
                       COUNT(t.id) FILTER (WHERE t.state IN ('pending', 'calling')),
                       AVG(t.best_listened_ms) FILTER (WHERE t.state = 'done'),
                       c.script, c.warmup_since, c.cooldown_until
                FROM informer_campaigns c
                LEFT JOIN informer_targets t ON t.campaign_id = c.id
                WHERE c.department_id = %s
                GROUP BY c.id
                ORDER BY c.created_at DESC, c.id DESC
                LIMIT 100
            """, (int(department_id),))
            rows = cur.fetchall()
            agent_warm = self._agent_warmup(cur)
        now = self.clock()
        return [self._campaign_row(r, agent_warm, now) for r in rows]

    @staticmethod
    def _agent_warmup(cur):
        cur.execute("SELECT warmup_since FROM informer_agent_state WHERE id = 1")
        row = cur.fetchone()
        return row[0] if row else None

    @classmethod
    def _campaign_row(cls, r, agent_warm=None, now=None):
        done, given_up, left = int(r[15] or 0), int(r[16] or 0), int(r[17] or 0)
        warmup_rate = None
        if r[2] == rules.CAMPAIGN_ACTIVE and now is not None and len(r) > 20:
            effective = cls._effective_rate(r[7], r[20], agent_warm, now)
            warmup_rate = effective if effective < int(r[7]) else None
        return {
            "warmup_rate": warmup_rate,
            # Остывание после отказов сети: до какого момента рассылка молчит сама (None — не молчит).
            "cooldown_until": (r[21].isoformat() if len(r) > 21 and r[21] and now is not None and r[21] > now else None),
            "id": r[0], "name": r[1], "status": r[2], "audio_ms": r[3], "parts": r[4] or [],
            "window_start": r[5].strftime('%H:%M') if r[5] else None,
            "window_end": r[6].strftime('%H:%M') if r[6] else None,
            "max_per_minute": r[7], "max_attempts": r[8], "retry_after_min": r[9],
            "targets_total": r[10],
            "created_at": r[11].isoformat() if r[11] else None,
            "started_at": r[12].isoformat() if r[12] else None,
            "finished_at": r[13].isoformat() if r[13] else None,
            "file_name": r[14] or '',
            "answered": done, "given_up": given_up, "left": left,
            "avg_listened_ms": int(r[18]) if r[18] is not None else None,
            "script": r[19] or {},
        }

    def campaign_action(self, department_id, campaign_id, action):
        """start | pause | finish. Завершённую рассылку не перезапускают — создают новую."""
        self.ready()
        if action not in rules.CAMPAIGN_ACTIONS:
            raise InformerError('Неизвестное действие')
        allowed_from, target = rules.CAMPAIGN_ACTIONS[action]
        now = self.clock()
        with self.db._get_cursor() as cur:
            cur.execute("SELECT status FROM informer_campaigns WHERE id = %s AND department_id = %s FOR UPDATE",
                        (int(campaign_id), int(department_id)))
            row = cur.fetchone()
            if not row:
                raise InformerError('Рассылка не найдена', 404)
            if row[0] not in allowed_from:
                raise InformerError('Рассылка уже в другом состоянии — обновите страницу', 409)
            cur.execute("""
                UPDATE informer_campaigns
                SET status = %s,
                    started_at = CASE WHEN %s = 'active' THEN COALESCE(started_at, %s) ELSE started_at END,
                    warmup_since = CASE WHEN %s = 'active' THEN %s ELSE warmup_since END,
                    cooldown_until = CASE WHEN %s = 'active' THEN NULL ELSE cooldown_until END,
                    cooldown_count = CASE WHEN %s = 'active' THEN 0 ELSE cooldown_count END,
                    finished_at = CASE WHEN %s = 'done' THEN %s ELSE finished_at END
                WHERE id = %s
            """, (target, target, now, target, now, target, target, target, now, int(campaign_id)))
            if target == rules.CAMPAIGN_DONE:
                # Кому не успели позвонить — «рассылку завершили», а не «не дозвонились».
                cur.execute("""
                    UPDATE informer_targets SET state = 'stopped'
                    WHERE campaign_id = %s AND state = 'pending'
                """, (int(campaign_id),))
        return self.campaign_detail(department_id, campaign_id, targets_limit=0)

    def campaign_detail(self, department_id, campaign_id, targets_limit=DETAIL_TARGETS_LIMIT):
        """Рассылка с аналитикой: дозвон, сколько слушали, на какой части клали трубку, водители."""
        self.ready()
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT c.id, c.name, c.status, c.audio_ms, c.parts, c.window_start, c.window_end,
                       c.max_per_minute, c.max_attempts, c.retry_after_min, c.targets_total, c.created_at,
                       c.started_at, c.finished_at, c.file_name,
                       COUNT(t.id) FILTER (WHERE t.state = 'done'),
                       COUNT(t.id) FILTER (WHERE t.state = 'given_up'),
                       COUNT(t.id) FILTER (WHERE t.state IN ('pending', 'calling')),
                       AVG(t.best_listened_ms) FILTER (WHERE t.state = 'done'),
                       c.script, c.warmup_since, c.cooldown_until
                FROM informer_campaigns c
                LEFT JOIN informer_targets t ON t.campaign_id = c.id
                WHERE c.id = %s AND c.department_id = %s
                GROUP BY c.id
            """, (int(campaign_id), int(department_id)))
            row = cur.fetchone()
            if not row:
                raise InformerError('Рассылка не найдена', 404)
            campaign = self._campaign_row(row, self._agent_warmup(cur), self.clock())
            parts = campaign["parts"]
            first_end = int(parts[0]["end_ms"]) if parts else 0
            last_end = int(parts[-1]["end_ms"]) if parts else 0
            cur.execute("""
                SELECT state, COUNT(*) FROM informer_targets WHERE campaign_id = %s GROUP BY state
            """, (int(campaign_id),))
            states = {state: int(n) for state, n in cur.fetchall()}
            cur.execute("""
                SELECT COUNT(*) FILTER (WHERE best_played_to_end OR best_listened_ms >= %s),
                       COUNT(*) FILTER (WHERE NOT best_played_to_end AND COALESCE(best_listened_ms, 0) < %s),
                       COUNT(*) FILTER (WHERE NOT best_played_to_end AND best_listened_ms >= %s
                                          AND best_listened_ms < %s),
                       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY best_listened_ms)
                FROM informer_targets
                WHERE campaign_id = %s AND state = 'done'
            """, (last_end, first_end, first_end, last_end, int(campaign_id)))
            full, on_first, on_second, median = cur.fetchone()
            cur.execute("""
                SELECT COUNT(*), COUNT(*) FILTER (WHERE result = 'answered'),
                       COUNT(*) FILTER (WHERE result = 'line_busy'),
                       COUNT(*) FILTER (WHERE result = 'no_link'),
                       COUNT(*) FILTER (WHERE result = 'failed'),
                       COUNT(*) FILTER (WHERE NOT finished)
                FROM informer_calls WHERE campaign_id = %s
            """, (int(campaign_id),))
            calls_total, calls_answered, calls_line_busy, calls_no_link, calls_failed, calls_open = cur.fetchone()
            cur.execute("""
                SELECT result, COUNT(*) FROM informer_calls
                WHERE campaign_id = %s AND finished AND NOT (result = ANY(%s))
                GROUP BY result
            """, (int(campaign_id), list(rules.NOT_ATTEMPTS)))
            results = {res: int(n) for res, n in cur.fetchall()}
            # Очередь: кому ещё не звонили, кто ждёт повтора после неудачной попытки, кому звоним сейчас.
            cur.execute("""
                SELECT COUNT(*) FILTER (WHERE state = 'pending' AND attempts = 0),
                       COUNT(*) FILTER (WHERE state = 'pending' AND attempts > 0),
                       COUNT(*) FILTER (WHERE state = 'calling')
                FROM informer_targets WHERE campaign_id = %s
            """, (int(campaign_id),))
            queue_fresh, queue_retry, queue_calling = cur.fetchone()
            # Почему не дозвонились: последняя настоящая попытка каждого водителя, до которого ещё не дозвонились
            # («линия занята», «сеть не пропустила» и «сбой АТС» — не попытки, они про водителя ничего не говорят).
            cur.execute("""
                SELECT last.result, COUNT(*)
                FROM informer_targets t
                JOIN (SELECT DISTINCT ON (target_id) target_id, result FROM informer_calls
                      WHERE campaign_id = %s AND finished AND NOT (result = ANY(%s))
                      ORDER BY target_id, id DESC) last ON last.target_id = t.id
                WHERE t.campaign_id = %s AND t.state IN ('pending', 'calling', 'given_up')
                GROUP BY last.result
            """, (int(campaign_id), list(rules.NOT_ATTEMPTS), int(campaign_id)))
            unreached = {res: int(n) for res, n in cur.fetchall()}
            signing, signed_map = self._signing(cur, campaign_id, department_id)
            targets = []
            if targets_limit:
                cur.execute("""
                    SELECT id, full_name, phone, state, attempts, last_result, last_call_at,
                           best_listened_ms, best_played_to_end
                    FROM informer_targets
                    WHERE campaign_id = %s
                    ORDER BY last_call_at DESC NULLS LAST, id
                    LIMIT %s
                """, (int(campaign_id), int(targets_limit)))
                for t in cur.fetchall():
                    stage = (rules.listen_stage(t[7], parts, bool(t[8])) if t[3] == rules.STATE_DONE
                             else rules.STAGE_NONE)
                    signed_at, signed_after = signed_map.get(t[0], (None, False))
                    targets.append({
                        "id": t[0], "full_name": t[1], "phone": mask_phone(t[2]), "state": t[3],
                        "attempts": int(t[4] or 0), "last_result": t[5] or '',
                        "last_call_at": t[6].isoformat() if t[6] else None,
                        "listened_ms": t[7], "stage": stage,
                        "signed_at": signed_at.isoformat() if signed_at else None,
                        "signed_after_call": signed_after,
                    })
        answered = int(states.get(rules.STATE_DONE, 0))
        processed = answered + int(states.get(rules.STATE_GIVEN_UP, 0))
        campaign["analytics"] = {
            "targets": dict(states),
            "processed": processed,
            "answered": answered,
            "answer_rate": round(answered / processed, 4) if processed else None,
            "avg_listened_ms": campaign["avg_listened_ms"],
            "median_listened_ms": int(median) if median is not None else None,
            # Дозвонившиеся по тому, где положили трубку (лучший звонок водителя).
            "stages": {
                parts[0]["lang"] if parts else 'first': int(on_first or 0),
                parts[-1]["lang"] if parts else 'last': int(on_second or 0),
                rules.STAGE_FULL: int(full or 0),
            },
            "calls": {"total": int(calls_total or 0), "answered": int(calls_answered or 0),
                      "line_busy": int(calls_line_busy or 0), "no_link": int(calls_no_link or 0),
                      "failed": int(calls_failed or 0),
                      "open": int(calls_open or 0), "results": results},
            "queue": {"fresh": int(queue_fresh or 0), "retry": int(queue_retry or 0),
                      "calling": int(queue_calling or 0)},
            "unreached": unreached,
            "signing": signing,
        }
        campaign["targets"] = targets
        return campaign

    # Подписание — из базы «Обзвона» того же отдела (dial_list_leads): Sapar раз в три часа и перед выдачей
    # строки сообщает, подписал ли водитель документы; signed_at — время подписи по Sapar. Водитель рассылки
    # находится по номеру. «После звонка» — подпись позже первого ответа водителя роботу; «сам» — успешка
    # «Обзвона» ни одному оператору не засчитана (success_attempt_id пуст), то есть разговора с оператором
    # перед подписью не было. Владелец, 17.10.2026: «отображать, сколько человек подписали именно после
    # звонка автоинформатора».
    def _signing(self, cur, campaign_id, department_id):
        """(сводка подписаний | None — базы «Обзвона» нет, {id цели: (signed_at, после звонка)})."""
        cur.execute("SAVEPOINT informer_signing")
        try:
            cur.execute("""
                WITH first_answer AS (
                    SELECT target_id, MIN(answered_at) AS answered_at FROM informer_calls
                    WHERE campaign_id = %s AND result = 'answered' AND answered_at IS NOT NULL
                    GROUP BY target_id
                )
                SELECT COUNT(l.id),
                       COUNT(*) FILTER (WHERE l.signed_at > fa.answered_at),
                       COUNT(*) FILTER (WHERE l.signed_at > fa.answered_at AND l.success_attempt_id IS NULL),
                       COUNT(*) FILTER (WHERE l.signed_at IS NOT NULL AND l.signed_at <= fa.answered_at),
                       MAX(l.sign_checked_at)
                FROM informer_targets t
                JOIN dial_list_leads l ON l.phone_norm = t.phone AND l.department_id = %s
                LEFT JOIN first_answer fa ON fa.target_id = t.id
                WHERE t.campaign_id = %s
            """, (int(campaign_id), int(department_id), int(campaign_id)))
            matched, after_call, after_self, before_call, checked_at = cur.fetchone()
            cur.execute("""
                WITH first_answer AS (
                    SELECT target_id, MIN(answered_at) AS answered_at FROM informer_calls
                    WHERE campaign_id = %s AND result = 'answered' AND answered_at IS NOT NULL
                    GROUP BY target_id
                )
                SELECT t.id, l.signed_at, fa.answered_at
                FROM informer_targets t
                JOIN dial_list_leads l ON l.phone_norm = t.phone AND l.department_id = %s
                LEFT JOIN first_answer fa ON fa.target_id = t.id
                WHERE t.campaign_id = %s AND l.signed_at IS NOT NULL
            """, (int(campaign_id), int(department_id), int(campaign_id)))
            signed_map = {tid: (signed_at, bool(answered_at and signed_at > answered_at))
                          for tid, signed_at, answered_at in cur.fetchall()}
        except psycopg2.errors.UndefinedTable:
            cur.execute("ROLLBACK TO SAVEPOINT informer_signing")
            return None, {}
        cur.execute("RELEASE SAVEPOINT informer_signing")
        return {
            "matched": int(matched or 0), "after_call": int(after_call or 0), "after_call_self": int(after_self or 0),
            "before_call": int(before_call or 0), "checked_at": checked_at.isoformat() if checked_at else None,
        }, signed_map

    def campaign_audio(self, department_id, campaign_id):
        """Запись для руководителя: оригинал, а у рассылок прежней страницы — телефонная версия."""
        self.ready()
        with self.db._get_cursor() as cur:
            cur.execute("SELECT COALESCE(audio_master, audio_wav) FROM informer_campaigns"
                        " WHERE id = %s AND department_id = %s",
                        (int(campaign_id), int(department_id)))
            row = cur.fetchone()
        if not row:
            raise InformerError('Рассылка не найдена', 404)
        return bytes(row[0])

    def export_rows(self, department_id, campaign_id):
        """Всё для выгрузки: рассылка, водители и все звонки (номер — маской)."""
        detail = self.campaign_detail(department_id, campaign_id, targets_limit=0)
        parts = detail["parts"]
        with self.db._get_cursor() as cur:
            cur.execute("""
                SELECT id, row_number, full_name, phone, state, attempts, last_result, last_call_at,
                       best_listened_ms, best_played_to_end
                FROM informer_targets WHERE campaign_id = %s ORDER BY row_number NULLS LAST, id
            """, (int(campaign_id),))
            targets = cur.fetchall()
            cur.execute("""
                SELECT c.issued_at, c.answered_at, c.ended_at, t.full_name, t.phone, c.attempt_no, c.line,
                       c.result, c.ring_ms, c.listened_ms, c.played_to_end, c.hangup_by, c.dtmf
                FROM informer_calls c JOIN informer_targets t ON t.id = c.target_id
                WHERE c.campaign_id = %s ORDER BY c.issued_at, c.id
            """, (int(campaign_id),))
            calls = cur.fetchall()
            _signing, signed_map = self._signing(cur, campaign_id, department_id)
        target_rows = [{
            "row_number": t[1], "full_name": t[2], "phone": mask_phone(t[3]), "state": t[4],
            "attempts": int(t[5] or 0), "last_result": t[6] or '', "last_call_at": t[7],
            "listened_ms": t[8],
            "stage": (rules.listen_stage(t[8], parts, bool(t[9])) if t[4] == rules.STATE_DONE
                      else rules.STAGE_NONE),
            "signed_at": signed_map.get(t[0], (None, False))[0],
            "signed_after_call": signed_map.get(t[0], (None, False))[1],
        } for t in targets]
        call_rows = [{
            "issued_at": c[0], "answered_at": c[1], "ended_at": c[2], "full_name": c[3],
            "phone": mask_phone(c[4]), "attempt_no": c[5], "line": c[6], "result": c[7],
            "ring_ms": c[8], "listened_ms": c[9],
            "stage": (rules.listen_stage(c[9], parts, bool(c[10])) if c[7] == rules.RESULT_ANSWERED
                      else rules.STAGE_NONE),
            "hangup_by": c[11] or '', "dtmf": c[12] or '',
        } for c in calls]
        return detail, target_rows, call_rows

    def robot_status(self):
        self.ready()
        with self.db._get_cursor() as cur:
            cur.execute("SELECT agent_id, version, lines, ami_ok, error, last_seen_at FROM informer_agent_state "
                        "WHERE id = 1")
            row = cur.fetchone()
        if not row or not row[5]:
            return {"online": False, "lines": {}, "last_seen_at": None, "error": ''}
        online = (self.clock() - row[5]).total_seconds() <= ROBOT_ONLINE_SECONDS and bool(row[3])
        return {"online": online, "lines": row[2] or {}, "last_seen_at": row[5].isoformat(),
                "error": row[4] or '', "version": row[1] or ''}

    # ── диспетчер на шлюзе ───────────────────────────────────────────────────
    def agent_poll(self, payload, key_id=''):
        """Кому звонить прямо сейчас. Темп, окно, ёмкость линий и повторы решает портал; диспетчер
        только сообщает, сколько свободно на каждой линии, и звонит."""
        self.ready()
        payload = payload or {}
        lines = self._agent_lines(payload.get('lines'))
        ami_ok = payload.get('ami_ok') is True
        now = self.clock()
        # Робот в паузе из-за отказов сети или проверяет связь одним звонком — мест ≤ 1: с этого момента
        # прогрев темпа начинается заново (rules.WARMUP_STAGES).
        robot_paused = bool(lines) and sum(info["capacity"] for info in lines.values()) <= 1
        with self.db._get_cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (POLL_LOCK_KEY,))
            cur.execute("""
                INSERT INTO informer_agent_state (id, agent_id, version, key_id, lines, ami_ok, error, last_seen_at,
                                                  warmup_since)
                VALUES (1, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET agent_id = EXCLUDED.agent_id, version = EXCLUDED.version,
                    key_id = EXCLUDED.key_id, lines = EXCLUDED.lines, ami_ok = EXCLUDED.ami_ok,
                    error = EXCLUDED.error, last_seen_at = EXCLUDED.last_seen_at,
                    warmup_since = CASE WHEN %s THEN EXCLUDED.last_seen_at ELSE informer_agent_state.warmup_since END
                RETURNING warmup_since
            """, (str(payload.get('agent_id') or '')[:128], str(payload.get('version') or '')[:64],
                  str(key_id or '')[:32], json.dumps(lines), ami_ok, str(payload.get('error') or '')[:500], now,
                  now if robot_paused else None, robot_paused))
            agent_warm = cur.fetchone()[0]
            self._reclaim_stale(cur, now)
            if not ami_ok or not lines:
                return {"calls": [], "next_poll_sec": POLL_AMI_DOWN_SEC}
            free = {line: max(0, info["capacity"] - info["active"]) for line, info in lines.items()}
            # Не больше MAX_ACTIVE_CALLS звонков разом (владелец, 16.10.2026: «залпов не надо, по 3 максимум»),
            # даже если диспетчер старый и сообщил все места линий. Минутного потолка нет: темп — поле рассылки.
            room_total = min(sum(free.values()), rules.MAX_ACTIVE_CALLS - sum(info["active"] for info in lines.values()))
            if room_total <= 0:
                return {"calls": [], "next_poll_sec": POLL_SOON_SEC}
            window_start = now - timedelta(seconds=60)
            cur.execute("""
                SELECT c.id, c.window_start, c.window_end, c.max_per_minute, c.audio_sha256, c.audio_ms,
                       (SELECT COUNT(*) FROM informer_calls x WHERE x.campaign_id = c.id AND x.issued_at > %s),
                       c.warmup_since,
                       (SELECT MAX(x.issued_at) FROM informer_calls x WHERE x.campaign_id = c.id),
                       c.cooldown_until, c.cooldown_count
                FROM informer_campaigns c
                WHERE c.status = 'active'
                ORDER BY c.started_at, c.id
            """, (window_start,))
            campaigns = cur.fetchall()
            issued = []
            more_due = False
            pace_ms = 0
            for (cid, w_start, w_end, per_minute, sha, audio_ms, issued_last_minute, campaign_warm, last_issued,
                 cooldown_until, cooldown_count) in campaigns:
                if not rules.in_window(now, w_start, w_end):
                    continue
                # Остывание после отказов сети: молчим до срока, потом продолжаем сами (прогрев заново — по простою).
                if cooldown_until and now < cooldown_until:
                    continue
                # Считаются только итоги после последнего запуска руками или прошлого остывания: старые отказы
                # не должны включать остывание заново.
                boundary = max((stamp for stamp in (cooldown_until, campaign_warm) if stamp), default=None)
                cur.execute("""
                    SELECT result FROM informer_calls
                    WHERE campaign_id = %s AND finished AND result <> 'line_busy'
                      AND finished_at >= COALESCE(%s, '-infinity'::timestamptz)
                    ORDER BY finished_at DESC NULLS LAST, id DESC LIMIT %s
                """, (cid, boundary, rules.COOLDOWN_TRIP_WINDOW))
                recent = [r[0] for r in cur.fetchall()]
                if rules.network_tripped(recent):
                    count = int(cooldown_count or 0) + 1
                    until = now + timedelta(minutes=rules.cooldown_minutes(count))
                    cur.execute("UPDATE informer_campaigns SET cooldown_until = %s, cooldown_count = %s WHERE id = %s",
                                (until, count, cid))
                    log.warning("Автоинформатор: рассылка %s — сеть не пропускает звонки, остывание до %s (№%s)",
                                cid, until.isoformat(), count)
                    continue
                # Простой дольше WARMUP_IDLE_MINUTES (открылось окно дня, долгая пауза) — прогрев заново.
                idle_since = last_issued or campaign_warm
                if idle_since and (now - idle_since) >= timedelta(minutes=rules.WARMUP_IDLE_MINUTES) \
                        and (not campaign_warm or campaign_warm <= idle_since):
                    cur.execute("UPDATE informer_campaigns SET warmup_since = %s WHERE id = %s", (now, cid))
                    campaign_warm = now
                # Прогрев: 3, 7, 10, 15, 20 в минуту от запуска рассылки или от паузы робота — что позже.
                per_minute = self._effective_rate(per_minute, campaign_warm, agent_warm, now)
                room = min(room_total - len(issued), per_minute - int(issued_last_minute))
                if room <= 0:
                    more_due = True
                    continue
                cur.execute("""
                    SELECT id, phone, attempts FROM informer_targets
                    WHERE campaign_id = %s AND state = 'pending' AND next_call_at <= %s
                    ORDER BY next_call_at, id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                """, (cid, now, room + 1))
                due = cur.fetchall()
                if len(due) > room:
                    more_due = True
                    due = due[:room]
                for target_id, phone, attempts in due:
                    line = max(free, key=lambda name: (free[name], name))
                    free[line] -= 1
                    cur.execute("""
                        UPDATE informer_targets SET state = 'calling', attempts = attempts + 1, last_call_at = %s
                        WHERE id = %s
                    """, (now, target_id))
                    cur.execute("""
                        INSERT INTO informer_calls (campaign_id, target_id, attempt_no, line, agent_id, issued_at)
                        VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
                    """, (cid, target_id, int(attempts) + 1, line, str(payload.get('agent_id') or '')[:128], now))
                    call_id = cur.fetchone()[0]
                    issued.append({"call_id": call_id, "campaign_id": cid, "phone": phone, "line": line,
                                   "audio_sha256": sha, "audio_ms": audio_ms})
                if due:
                    # Шаг набора — ровно по темпу рассылки: 15 в минуту — раз в 4 с, а не пачкой за 15 с в начале
                    # минуты (16.10.2026: всплески режут и офисная сеть, и операторы связи).
                    pace_ms = max(pace_ms, 60000 // max(1, int(per_minute)))
                if room_total - len(issued) <= 0:
                    more_due = True
                    break
        return {"calls": issued, "next_poll_sec": POLL_SOON_SEC if (issued or more_due) else POLL_IDLE_SEC,
                "dial_gap_ms": pace_ms if issued else 0}

    @staticmethod
    def _effective_rate(per_minute, campaign_warm, agent_warm, now):
        """Темп рассылки с учётом прогрева: ступень по времени от запуска или от паузы робота (что позже)."""
        since = max((stamp for stamp in (campaign_warm, agent_warm) if stamp), default=None)
        stage = rules.warmup_rate((now - since).total_seconds() / 60 if since else None)
        rate = int(per_minute)
        return min(rate, stage) if stage else rate

    @staticmethod
    def _agent_lines(raw):
        """{'939': {'capacity': 2, 'active': 1}} от диспетчера → проверенный словарь."""
        out = {}
        if not isinstance(raw, dict):
            return out
        for name, info in list(raw.items())[:64]:          # в кабинете 45 линий — запас без молчаливой обрезки
            line = ''.join(ch for ch in str(name) if ch.isalnum())[:16]
            if not line or not isinstance(info, dict):
                continue
            try:
                capacity = max(0, min(int(info.get('capacity', rules.CALLS_PER_LINE)), MAX_LINE_CAPACITY))
                active = max(0, int(info.get('active', 0)))
            except (TypeError, ValueError):
                continue
            out[line] = {"capacity": capacity, "active": active}
        return out

    def _reclaim_stale(self, cur, now):
        """Звонки без итога дольше STALE_CALL_SECONDS — «итог не пришёл», водитель — в очередь."""
        # Срок — «запись + запас»: звонок с пятиминутной записью, признанный потерянным на пятой
        # минуте, вернул бы водителя в очередь, и робот позвонил бы ему второй раз поверх первого.
        cur.execute("""
            SELECT c.id FROM informer_calls c
            JOIN informer_campaigns k ON k.id = c.campaign_id
            WHERE NOT c.finished
              AND c.issued_at < %s - make_interval(secs => k.audio_ms / 1000.0 + %s)
            ORDER BY c.id
            LIMIT 200
            FOR UPDATE OF c SKIP LOCKED
        """, (now, rules.STALE_CALL_SECONDS))
        for (call_id,) in cur.fetchall():
            self._apply_result(cur, call_id, {"result": rules.RESULT_LOST}, now)

    def agent_audio(self, campaign_id):
        self.ready()
        with self.db._get_cursor() as cur:
            cur.execute("SELECT audio_sha256, audio_wav FROM informer_campaigns WHERE id = %s", (int(campaign_id),))
            row = cur.fetchone()
        if not row:
            raise InformerError('Рассылка не найдена', 404)
        return row[0], bytes(row[1])

    def agent_result(self, payload):
        """Итог звонка от диспетчера. Повтор того же итога (сеть, перезапуск) ничего не меняет."""
        self.ready()
        payload = payload or {}
        try:
            call_id = int(payload.get('call_id'))
        except (TypeError, ValueError):
            raise InformerError('call_id — число')
        result = str(payload.get('result') or '')
        if result not in rules.RESULTS or result == rules.RESULT_LOST:
            raise InformerError('Неизвестный итог звонка')
        now = self.clock()
        with self.db._get_cursor() as cur:
            applied = self._apply_result(cur, call_id, payload, now)
        return {"applied": applied}

    def _apply_result(self, cur, call_id, payload, now):
        cur.execute("""
            SELECT c.finished, c.result, c.target_id, c.campaign_id, k.max_attempts, k.retry_after_min,
                   k.audio_ms, k.status
            FROM informer_calls c JOIN informer_campaigns k ON k.id = c.campaign_id
            WHERE c.id = %s FOR UPDATE OF c
        """, (int(call_id),))
        row = cur.fetchone()
        if not row:
            raise InformerError('Звонок не найден', 404)
        finished, old_result, target_id, campaign_id, max_attempts, retry_min, audio_ms, campaign_status = row
        result = str(payload.get('result') or '')
        # Поздний настоящий итог после «итог не пришёл» принимаем: он точнее догадки.
        if finished and old_result != rules.RESULT_LOST:
            return False
        if finished and result == rules.RESULT_LOST:
            return False
        cap = int(audio_ms) + 120_000
        listened = _non_negative_int(payload.get('listened_ms'), cap) if result == rules.RESULT_ANSWERED else None
        ring = _non_negative_int(payload.get('ring_ms'), 300_000)
        played = bool(payload.get('played_to_end')) if result == rules.RESULT_ANSWERED else None
        hangup_by = str(payload.get('hangup_by') or '')
        if hangup_by not in rules.HANGUP_SIDES:
            hangup_by = ''
        dtmf = ''.join(ch for ch in str(payload.get('dtmf') or '') if ch in '0123456789*#')[:32]
        details = payload.get('details') if isinstance(payload.get('details'), dict) else {}
        details_json = json.dumps(details, ensure_ascii=False)
        if len(details_json) > DETAILS_MAX_CHARS:
            details_json = json.dumps({"_truncated": True})
        try:
            hangup_cause = int(payload['hangup_cause']) if payload.get('hangup_cause') not in (None, '') else None
        except (TypeError, ValueError):
            hangup_cause = None
        dialed_at = _moment(payload.get('dialed_at'))
        answered_at = _moment(payload.get('answered_at')) if result == rules.RESULT_ANSWERED else None
        ended_at = _moment(payload.get('ended_at'))
        cur.execute("""
            UPDATE informer_calls
            SET finished = TRUE, finished_at = %s, result = %s, dialed_at = COALESCE(%s, dialed_at),
                answered_at = %s, ended_at = %s, ring_ms = %s, listened_ms = %s, played_to_end = %s,
                hangup_by = %s, hangup_cause = %s, dtmf = %s, details = %s::jsonb
            WHERE id = %s
        """, (now, result, dialed_at, answered_at, ended_at, ring, listened, played, hangup_by, hangup_cause,
              dtmf, details_json, int(call_id)))

        cur.execute("""
            SELECT state, attempts, best_listened_ms, best_played_to_end FROM informer_targets
            WHERE id = %s FOR UPDATE
        """, (target_id,))
        state, attempts, best, best_played = cur.fetchone()
        if result == rules.RESULT_ANSWERED:
            new_state, next_at, new_attempts = rules.STATE_DONE, None, int(attempts or 0)
            best = max(int(best or 0), int(listened or 0))
            best_played = bool(best_played) or bool(played)
        elif state == rules.STATE_DONE:
            # Поздний «итог не пришёл» по водителю, до которого уже дозвонились, ничего не портит.
            new_state, next_at, new_attempts = state, None, int(attempts or 0)
        else:
            failed_before = 0
            if result == rules.RESULT_FAILED:
                # Сколько «сбоев АТС» у водителя уже было (этот звонок уже записан — без него).
                cur.execute("""
                    SELECT COUNT(*) FROM informer_calls
                    WHERE campaign_id = %s AND target_id = %s AND finished AND result = %s AND id <> %s
                """, (campaign_id, target_id, rules.RESULT_FAILED, int(call_id)))
                failed_before = int(cur.fetchone()[0] or 0)
            new_state, next_at, new_attempts = rules.after_result(result, attempts, max_attempts, retry_min, now,
                                                                  failed_before=failed_before)
            if campaign_status == rules.CAMPAIGN_DONE and new_state == rules.STATE_PENDING:
                new_state = rules.STATE_STOPPED
        cur.execute("""
            UPDATE informer_targets
            SET state = %s, attempts = %s, next_call_at = COALESCE(%s, next_call_at), last_result = %s,
                last_call_at = COALESCE(%s, %s, last_call_at), best_listened_ms = %s, best_played_to_end = %s
            WHERE id = %s
        """, (new_state, new_attempts, next_at, result, ended_at, dialed_at, best, bool(best_played), target_id))
        # Очередь кончилась — рассылка завершена сама.
        cur.execute("""
            UPDATE informer_campaigns SET status = 'done', finished_at = %s
            WHERE id = %s AND status = 'active'
              AND NOT EXISTS (SELECT 1 FROM informer_targets
                              WHERE campaign_id = %s AND state IN ('pending', 'calling'))
        """, (now, campaign_id, campaign_id))
        return True
