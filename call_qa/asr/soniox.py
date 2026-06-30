"""Soniox ASR: запись → транскрипт с диаризацией, языком и confidence по токенам.
Боевой клиент (проверен на бенче 20 звонков ОП)."""
from __future__ import annotations
import time
import requests

from .. import config

H = lambda: {"Authorization": f"Bearer {config.env('SONIOX_API_KEY')}"}


def transcribe_file(path: str, *, langs=None, diarize=True, timeout_s=300) -> list[dict]:
    """Возвращает список токенов: {text, speaker, language, confidence, start_time_ms, end_time_ms}.
    Удаляет файл/транскрипцию на стороне Soniox после получения (гигиена ПДн)."""
    base, h = config.SONIOX_BASE, H()
    with open(path, "rb") as fh:
        fid = requests.post(f"{base}/v1/files", headers=h, files={"file": fh}, timeout=120).json()["id"]
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
    toks = requests.get(f"{base}/v1/transcriptions/{tid}/transcript", headers=h, timeout=60).json().get("tokens", [])
    for u in (f"{base}/v1/transcriptions/{tid}", f"{base}/v1/files/{fid}"):
        try:
            requests.delete(u, headers=h, timeout=30)
        except Exception:
            pass
    return toks


def assemble(toks: list[dict]) -> dict:
    """Из токенов собирает диаризованный текст, языковой состав и места неуверенности."""
    lines, cur, buf = [], None, []
    confs, langc = [], {}
    for t in toks:
        sp, c, lg = t.get("speaker"), t.get("confidence"), t.get("language")
        if lg:
            langc[lg] = langc.get(lg, 0) + 1
        if c is not None:
            confs.append(c)
        if sp != cur and buf:
            lines.append({"speaker": cur, "text": "".join(buf).strip()})
            buf = []
        cur = sp
        buf.append(t.get("text", ""))
    if buf:
        lines.append({"speaker": cur, "text": "".join(buf).strip()})
    total = sum(langc.values()) or 1
    return {
        "lines": lines,                                   # [{speaker, text}]
        "text": "\n".join(f"[S{l['speaker']}] {l['text']}" for l in lines),
        "languages": {k: round(100 * v / total) for k, v in sorted(langc.items(), key=lambda x: -x[1])},
        "mean_conf": round(sum(confs) / len(confs), 3) if confs else None,
        "low_conf_spans": _spans(toks),                   # фрагменты для ревью / «не штрафовать»
        "n_speakers": len({t.get("speaker") for t in toks if t.get("speaker") is not None}),
    }


def _spans(toks: list[dict]) -> list[dict]:
    spans, run = [], []
    for t in toks:
        c = t.get("confidence")
        if c is not None and c < config.ASR_CONF_HARD:
            run.append((t.get("text", ""), c))
        elif run:
            spans.append(_finish(run)); run = []
    if run:
        spans.append(_finish(run))
    return sorted(spans, key=lambda s: s["min_conf"])


def _finish(run):
    cs = [c for _, c in run]
    return {"text": "".join(t for t, _ in run).strip(), "min_conf": round(min(cs), 2), "n": len(run)}
