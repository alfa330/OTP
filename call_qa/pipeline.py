"""Оркестратор дневного прогона: записи → ASR → оценка → хранение → ревью.
Каркас: интеграционные точки (скачивание из GCS, запись в БД, Batch API) помечены TODO."""
from __future__ import annotations
import os
import tempfile

from . import config
from .asr import soniox
from .evaluation import criteria as criteria_mod
from .evaluation import evaluator
from .review import queue


def _download_gcs(audio_path: str, dest: str):
    """audio_path = 'bucket/blob' → файл dest. Тем же сервис-аккаунтом."""
    from google.oauth2 import service_account
    from google.cloud import storage
    sa = config.google_sa_info()
    creds = service_account.Credentials.from_service_account_info(sa)
    bucket, blob = audio_path.split("/", 1)
    storage.Client(project=sa["project_id"], credentials=creds).bucket(bucket).blob(blob).download_to_filename(dest)


def evaluate_call(call: dict, direction: dict) -> dict:
    """Один звонок: скачать → ASR → оценить. call = {id, audio_path, direction_id}."""
    with tempfile.TemporaryDirectory() as td:
        dst = os.path.join(td, "audio.mp3")
        _download_gcs(call["audio_path"], dst)
        toks = soniox.transcribe_file(dst)
    tr = soniox.assemble(toks)
    ev = evaluator.evaluate(tr["text"], direction, asr_low_spans=tr["low_conf_spans"])
    review = queue.needs_review(ev, direction, tr["mean_conf"])
    return {"call_id": call["id"], "asr": tr, "evaluation": ev, "needs_review": review}


def run_daily(day: str | None = None):
    """Дневной прогон по ОП. PROD: брать звонки за день, гнать через Batch API, писать в БД."""
    # TODO: выбрать вчерашние звонки ОП (calls с audio_path по OP_DIRECTION_IDS) из БД
    # TODO: для масштаба — Soniox + Claude Batch API (ночью), запись в ai_evaluation_meta (DB_RW)
    # TODO: needs_review=True → положить в очередь ревью (существующие review-таблицы)
    raise NotImplementedError("Этап 3: дневной batch — подключается после Этапа 1 (оценщик) и Этапа 2 (RAG)")


if __name__ == "__main__":
    # Ручная проверка одного звонка (после добавления ANTHROPIC_API_KEY):
    #   direction = criteria_mod.load_direction(73)
    #   print(evaluate_call({"id": 6434, "audio_path": "my-app-audio-uploads/uploads/<uuid>.mp3",
    #                        "direction_id": 73}, direction))
    pass
