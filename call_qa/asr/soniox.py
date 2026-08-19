"""Soniox ASR: запись → транскрипт с диаризацией, языком и confidence по токенам.
Боевой клиент (проверен на бенче 20 звонков ОП)."""
from __future__ import annotations
import time
import requests

from .. import config

H = lambda: {"Authorization": f"Bearer {config.env('SONIOX_API_KEY')}"}


def transcribe_file(path: str, *, langs=None, diarize=True, timeout_s=300) -> list[dict]:
    """Только токены — для вызывающих, которым метаданные не нужны."""
    return transcribe_file_full(path, langs=langs, diarize=diarize, timeout_s=timeout_s)["tokens"]


def transcribe_file_full(path: str, *, langs=None, diarize=True, timeout_s=300) -> dict:
    """Токены + ВСЁ, что отдаёт вендор: {"tokens": [...], "meta": {...}}.

    Мы удаляем запись на стороне Soniox сразу после получения (гигиена ПДн), поэтому
    второго шанса забрать данные нет: всё, за что заплачено, снимаем здесь и сохраняем.
    Ключевое в meta — audio_duration_ms: это биллинговая длительность самого вендора,
    точнее любых наших оценок по размеру файла.
    """
    base, h = config.SONIOX_BASE, H()
    with open(path, "rb") as fh:
        up = requests.post(f"{base}/v1/files", headers=h, files={"file": fh}, timeout=120).json()
    fid = up["id"]
    body = {
        "model": config.SONIOX_MODEL,
        "file_id": fid,
        "language_hints": langs or config.SONIOX_LANGS,
        "enable_language_identification": True,
        "enable_speaker_diarization": diarize,
    }
    tid = requests.post(f"{base}/v1/transcriptions", headers=h, json=body, timeout=60).json()["id"]
    t0 = time.time()
    while True:
        st = requests.get(f"{base}/v1/transcriptions/{tid}", headers=h, timeout=60).json()
        if st.get("status") == "completed":
            break
        if st.get("status") == "error":
            raise RuntimeError(f"soniox: {st.get('error_message')}")
        if time.time() - t0 > timeout_s:
            raise TimeoutError("soniox poll timeout")
        time.sleep(2)
    tr = requests.get(f"{base}/v1/transcriptions/{tid}/transcript", headers=h, timeout=60).json()
    for u in (f"{base}/v1/transcriptions/{tid}", f"{base}/v1/files/{fid}"):
        try:
            requests.delete(u, headers=h, timeout=30)
        except Exception:
            pass
    meta = {
        "transcription_id": tid,
        "audio_duration_ms": st.get("audio_duration_ms"),      # биллинговая длительность вендора
        "model": st.get("model") or config.SONIOX_MODEL,
        "language_hints": st.get("language_hints") or (langs or config.SONIOX_LANGS),
        "diarization": st.get("enable_speaker_diarization"),
        "language_identification": st.get("enable_language_identification"),
        "audio_event_detection": st.get("enable_audio_event_detection"),
        "filename": st.get("filename") or up.get("filename"),
        "file_size_bytes": up.get("size"),
        "created_at": st.get("created_at"),
        "vendor_text": tr.get("text"),                          # собственная сборка вендора
    }
    return {"tokens": [_with_timing(t) for t in tr.get("tokens", [])], "meta": meta}


def _with_timing(tok: dict) -> dict:
    """Soniox называет границы токена start_ms/end_ms, остальной код ждёт *_time_ms.

    Из-за расхождения имён тайминги молча терялись при сохранении: у транскриптов
    в кэше duration_ms оставался нулём, а у реплик не было позиции в записи.
    Держим оба имени: старые потребители не ломаются, новые получают тайминги.
    """
    for src, dst in (("start_ms", "start_time_ms"), ("end_ms", "end_time_ms")):
        if tok.get(dst) is None and tok.get(src) is not None:
            tok[dst] = tok[src]
    return tok


def assemble(toks: list[dict], meta: dict | None = None) -> dict:
    """Из токенов собирает диаризованный текст, языковой состав и места неуверенности.

    Реплики и спаны неуверенности несут границы в миллисекундах — по ним можно
    открыть нужную секунду записи и нарезать обучающие отрезки.
    """
    lines, cur, buf = [], None, []
    confs, langc = [], {}
    events = []

    def flush():
        if not buf:
            return
        lines.append({
            "speaker": cur,
            "text": "".join(t.get("text", "") for t in buf).strip(),
            "start_time_ms": next((t.get("start_time_ms") for t in buf if t.get("start_time_ms") is not None), None),
            "end_time_ms": next((t.get("end_time_ms") for t in reversed(buf) if t.get("end_time_ms") is not None), None),
        })

    for t in toks:
        sp, c, lg = t.get("speaker"), t.get("confidence"), t.get("language")
        if t.get("is_audio_event"):
            events.append({"text": t.get("text"), "start_time_ms": t.get("start_time_ms"),
                           "end_time_ms": t.get("end_time_ms")})
        if lg:
            langc[lg] = langc.get(lg, 0) + 1
        if c is not None:
            confs.append(c)
        if sp != cur and buf:
            flush()
            buf = []
        cur = sp
        buf.append(t)
    flush()
    total = sum(langc.values()) or 1
    ends = [t.get("end_time_ms") for t in toks if t.get("end_time_ms") is not None]
    out = {
        "lines": lines,                                   # [{speaker, text, start_time_ms, end_time_ms}]
        "text": "\n".join(f"[S{l['speaker']}] {l['text']}" for l in lines),
        "languages": {k: round(100 * v / total) for k, v in sorted(langc.items(), key=lambda x: -x[1])},
        "mean_conf": round(sum(confs) / len(confs), 3) if confs else None,
        "low_conf_spans": _spans(toks),                   # фрагменты для ревью / «не штрафовать»
        "n_speakers": len({t.get("speaker") for t in toks if t.get("speaker") is not None}),
        # длительность: биллинговая от вендора, иначе последний токен
        "duration_ms": (meta or {}).get("audio_duration_ms") or (max(ends) if ends else None),
        "audio_events": events,
    }
    if meta:
        out["asr_meta"] = meta
    return out


def _spans(toks: list[dict]) -> list[dict]:
    spans, run = [], []
    for t in toks:
        c = t.get("confidence")
        if c is not None and c < config.ASR_CONF_HARD:
            run.append(t)
        elif run:
            spans.append(_finish(run)); run = []
    if run:
        spans.append(_finish(run))
    return sorted(spans, key=lambda s: s["min_conf"])


def _finish(run):
    cs = [t.get("confidence") for t in run if t.get("confidence") is not None]
    return {"text": "".join(t.get("text", "") for t in run).strip(),
            "min_conf": round(min(cs), 2) if cs else None, "n": len(run),
            "start_time_ms": next((t.get("start_time_ms") for t in run if t.get("start_time_ms") is not None), None),
            "end_time_ms": next((t.get("end_time_ms") for t in reversed(run) if t.get("end_time_ms") is not None), None)}
