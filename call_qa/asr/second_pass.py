"""Повторное распознавание слабых записей.

Soniox распознаёт каждый звонок и сам сообщает, насколько уверен. Запись, на которой
его средняя уверенность ниже порога (config.ASR_SECOND_PASS_BELOW), повторно
распознаёт Gemini, и оценка идёт по её расшифровке — см. пояснение у порога в
config.py. Первый проход при этом не выбрасывается: он остаётся в кэше, по нему
решается, слаба ли запись, и на нём же оценка остаётся при любом сбое второго.

Два прохода — это ещё и сверка. Gemini слышит чистую речь точнее (имя оператора,
город), но невнятную фразу не помечает, а додумывает, причём каждый раз по-своему: на
звонке 7060 первую реплику водителя пять прогонов прочли пятью способами, и оценка
шла следом за догадкой — 98, 67 и снова 0 с той же критической ошибкой. Поэтому
реплики, где Gemini и Soniox услышали разное, считаются ненадёжными: оценщик получает
их в блоке «неуверенные фрагменты (не штрафовать)», карточка подсвечивает. На той же
расшифровке с такой пометкой оценщик дал 98 и 95 и снял критическую ошибку: «из-за
ненадёжного распознавания нельзя установить характер вопроса».

Платный вызов Gemini делается, только когда оценка действительно будет считаться
(первая оценка звонка или «Переоценить»). Открытие карточки с готовой оценкой его не
делает: иначе просмотр старых звонков тратил бы деньги и помечал их оценки
«устаревшими» — расшифровка входит в отпечаток оценки.
"""
from __future__ import annotations

import difflib
import logging
import os
import re
import tempfile

from .. import config
from .. import providers
from .. import speaker_roles
from ..evaluation import runtime_store
from ..evaluation.fingerprint import content_hash
from . import gemini

PROVIDER = "gemini"
_WORD_RE = re.compile(r"\w+")
# Расшифровки одной записи не могут расходиться по объёму в разы: вдвое короче —
# Gemini оборвала разговор, вдвое длиннее — зациклилась. На коротком тексте (меньше
# десяти слов у Soniox) сравнивать нечего.
_MIN_WORDS_TO_COMPARE = 10
_PLAUSIBLE_RATIO = (0.5, 2.0)
# Реплика ненадёжна, если первый проход подтвердил меньше половины её слов. Короткие
# («Алло», «Иә, иә») не сверяем: в них одно разночтение — уже половина.
_DOUBT_MIN_WORDS = 4
_DOUBT_CONFIRMED_SHARE = 0.5
DOUBT_NOTE = "два распознавания услышали это место по-разному — содержание реплики ненадёжно"
# Числа в сверку не идут: Soniox пишет их цифрами, Gemini — словами, и реплика с
# номером телефона расходилась бы целиком при одинаково услышанном.
_NUMBER_WORDS = frozenset((
    "ноль нуль один одна одно два две три четыре пять шесть семь восемь девять десять "
    "одиннадцать двенадцать тринадцать четырнадцать пятнадцать шестнадцать семнадцать "
    "восемнадцать девятнадцать двадцать тридцать сорок пятьдесят шестьдесят семьдесят "
    "восемьдесят девяносто сто двести триста четыреста пятьсот шестьсот семьсот восемьсот "
    "девятьсот тысяча тысячи тысяч "
    "нөл бір екі үш төрт бес алты жеті сегіз тоғыз он жиырма отыз қырық елу алпыс жетпіс "
    "сексен тоқсан жүз мың").split())


class SecondPassRejected(RuntimeError):
    """Второй проход получен, но доверять ему нельзя — остаёмся на первом."""


def is_weak(mean_conf) -> bool:
    """Ниже порога — слабая. Порог 0 выключает второй проход: ниже нуля не бывает."""
    return mean_conf is not None and float(mean_conf) < config.ASR_SECOND_PASS_BELOW


def card_note(transcript_payload) -> dict | None:
    """Пометка карточки «распознано повторно» — по записи кэша той расшифровки,
    которая показана, а не по сегодняшним настройкам."""
    payload = transcript_payload if isinstance(transcript_payload, dict) else {}
    identity = payload.get("asr_config") if isinstance(payload.get("asr_config"), dict) else {}
    if identity.get("provider") != PROVIDER:
        return None
    first = payload.get("first_pass") if isinstance(payload.get("first_pass"), dict) else {}
    return {"engine": PROVIDER, "model": identity.get("model"),
            "first_pass_conf": first.get("mean_conf")}


def _check_plausible(second_text: str, first_text: str) -> None:
    first_words = len(_WORD_RE.findall(first_text or ""))
    if first_words < _MIN_WORDS_TO_COMPARE:
        return
    ratio = len(_WORD_RE.findall(second_text or "")) / first_words
    if not _PLAUSIBLE_RATIO[0] <= ratio <= _PLAUSIBLE_RATIO[1]:
        raise SecondPassRejected(f"объём расшифровки {ratio:.2f} от первого прохода")


def _comparable_words(text: str) -> list[str]:
    return [word for word in (match.lower() for match in _WORD_RE.findall(text or ""))
            if word not in _NUMBER_WORDS and not any(char.isdigit() for char in word)]


def _line_text(line: dict) -> str:
    return "".join(str(seg.get("t") or "") for seg in line.get("seg") or [])


def doubtful_lines(lines: list[dict], first_text: str) -> list[int]:
    """Номера реплик второго прохода, которые первый проход услышал иначе.

    Обе расшифровки выравниваются по словам целиком (порядок реплик и их нарезка у
    распознавателей разные), и у каждой реплики считается доля слов, нашедших пару.
    Метки голоса первого прохода («[S1]») в сверку не попадают сами: в них цифра."""
    other = _comparable_words(first_text)
    if not other:
        return []
    flat, owner, totals = [], [], []
    for index, line in enumerate(lines):
        words = _comparable_words(_line_text(line))
        totals.append(len(words))
        flat.extend(words)
        owner.extend([index] * len(words))
    confirmed = [0] * len(lines)
    for block in difflib.SequenceMatcher(None, flat, other, autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            confirmed[owner[block.a + offset]] += 1
    return [index for index, total in enumerate(totals)
            if total >= _DOUBT_MIN_WORDS and confirmed[index] / total < _DOUBT_CONFIRMED_SHARE]


def _mark_doubtful(lines: list[dict], first_text: str) -> tuple[list[dict], list[dict]]:
    """Реплики с пометкой ненадёжных (`u` у сегмента — карточка подсвечивает) и те же
    реплики списком для оценщика — в форме неуверенных фрагментов Soniox."""
    doubtful = set(doubtful_lines(lines, first_text))
    marked, spans = [], []
    for index, line in enumerate(lines):
        if index in doubtful:
            line = {**line, "seg": [{**seg, "u": True} for seg in line["seg"]]}
            span = {"text": _line_text(line), "note": DOUBT_NOTE}
            for key, source in (("start_time_ms", "start_ms"), ("end_time_ms", "end_ms")):
                if line.get(source) is not None:
                    span[key] = line[source]
            spans.append(span)
        marked.append(line)
    return marked, spans


def _lookup(call_id, subject_kind, audio_fingerprint, model, config_hash) -> dict | None:
    return runtime_store.get_transcript(
        call_id=call_id, audio_fingerprint_value=audio_fingerprint, asr_provider=PROVIDER,
        asr_model=model, asr_config_hash=config_hash, subject_kind=subject_kind)


def _transcribe_and_store(*, call_id, subject_kind, audio_path, audio_fingerprint, first,
                          model, identity, config_hash, download) -> dict:
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "audio" + (os.path.splitext(audio_path)[1] or ".mp3"))
        download(audio_path, path)
        size = os.path.getsize(path)
        if size > config.ASR_SECOND_PASS_MAX_BYTES:
            raise SecondPassRejected(f"запись {size} байт больше лимита запроса")
        got = gemini.transcribe_file(path, model=model)
    assembled = gemini.assemble(got["segments"])
    _check_plausible(assembled["text"], first.get("text"))
    lines, doubtful_spans = _mark_doubtful(assembled["lines"], first.get("text"))
    lines = speaker_roles.assign(lines, speaker_roles.resolve(lines))
    runtime_store.put_transcript(
        call_id=call_id, audio_fingerprint_value=audio_fingerprint, asr_provider=PROVIDER,
        asr_model=model, asr_config_hash=config_hash,
        transcript_hash=content_hash(assembled["text"]), text=assembled["text"],
        segments=lines, tokens=None,
        payload={"asr_config": identity, "asr_meta": got["meta"], "segments": got["segments"],
                 "first_pass": {"provider": "soniox", "model": config.SONIOX_MODEL,
                                "mean_conf": first.get("mean_conf"),
                                "transcript_cache_id": first.get("transcript_cache_id")}},
        languages=assembled["languages"],
        # У Gemini уверенности нет. Храним уверенность первого прохода как меру
        # качества звука: по ней «Слабый звук» по-прежнему отправляет звонок человеку.
        mean_conf=first.get("mean_conf"), low_conf_spans=doubtful_spans,
        duration_ms=first.get("duration_ms"), subject_kind=subject_kind)
    # Кэш пишется «кто первый»: при гонке двух оценок остаётся первая расшифровка, а
    # Gemini неповторяема — оцениваем то, что реально легло в кэш, а не своё.
    stored = _lookup(call_id, subject_kind, audio_fingerprint, model, config_hash)
    if not stored:
        raise SecondPassRejected("расшифровка не сохранилась в кэше")
    return stored


def resolve(*, call_id, subject_kind, audio_path, audio_fingerprint, first: dict,
            may_transcribe, download) -> dict | None:
    """Расшифровка второго прохода для слабой записи — или None: остаёмся на первой.

    first — что дал Soniox: mean_conf, text, duration_ms, transcript_cache_id.
    may_transcribe() — будет ли сейчас считаться оценка; только тогда зовём Gemini.
    Уже сделанная расшифровка берётся из кэша всегда: её отпечаток — у прогона,
    который по ней оценён. Любой сбой второго прохода оценку не роняет."""
    if not is_weak(first.get("mean_conf")):
        return None
    model = config.ASR_SECOND_PASS_MODEL
    identity = gemini.config_identity(model)
    config_hash = content_hash(identity)
    try:
        record = _lookup(call_id, subject_kind, audio_fingerprint, model, config_hash)
        if record is None:
            if not may_transcribe():
                return None
            record = _transcribe_and_store(
                call_id=call_id, subject_kind=subject_kind, audio_path=audio_path,
                audio_fingerprint=audio_fingerprint, first=first, model=model,
                identity=identity, config_hash=config_hash, download=download)
    except (gemini.GeminiAsrError, SecondPassRejected, providers.VertexError) as exc:
        logging.warning("ai-qa: повторное распознавание %s %s не удалось, остаётся Soniox: %s",
                        subject_kind, call_id, exc)
        return None
    except Exception:
        # Ошибка не модели, а кода или окружения (подпись, ключ, кэш): оценка всё равно
        # идёт на Soniox, но с трейсбеком — иначе функция тихо не работала бы в проде.
        logging.exception("ai-qa: сбой повторного распознавания %s %s, остаётся Soniox",
                          subject_kind, call_id)
        return None
    return {"asm": {"text": record["text"], "languages": record.get("languages") or {},
                    "mean_conf": record.get("mean_conf"),
                    "low_conf_spans": record.get("low_conf_spans") or []},
            "lines": record.get("segments") or [],
            "transcript_cache_id": record["id"], "transcript_hash": record["transcript_hash"],
            "source_model": model, "source_config": identity,
            "asr": card_note(record.get("payload"))}
