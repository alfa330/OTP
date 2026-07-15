"""Content-addressed identities for reproducible call evaluations.

An evaluation is a pure function of much more than a model name.  These helpers
produce canonical hashes that are safe to use as immutable cache keys and audit
identifiers.  They deliberately contain no database or network access.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


FINGERPRINT_VERSION = 2
RETRIEVAL_PIPELINE_VERSION = "hybrid-set-v2"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def content_hash(value: Any) -> str:
    payload = value if isinstance(value, str) else canonical_json(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def transcript_fingerprint(*, audio_fingerprint: str, asr_model: str,
                           asr_config: dict, transcript: str) -> str:
    return content_hash({
        "version": 1,
        "audio_fingerprint": audio_fingerprint,
        "asr_model": asr_model,
        "asr_config": asr_config,
        "transcript_hash": content_hash(transcript or ""),
    })


def build_evaluation_fingerprint(*, transcript_hash: str, model: str,
                                 model_config: dict, prompt_hash: str,
                                 output_schema_hash: str, scale_hash: str,
                                 criterion_config_hash: str,
                                 knowledge_snapshot_hash: str,
                                 retrieval_config: dict,
                                 evaluator_code_version: str | None = None) -> tuple[str, dict]:
    components = {
        "fingerprint_version": FINGERPRINT_VERSION,
        "transcript_hash": transcript_hash,
        "model": model,
        "model_config": model_config,
        "prompt_hash": prompt_hash,
        "output_schema_hash": output_schema_hash,
        "scale_hash": scale_hash,
        "criterion_config_hash": criterion_config_hash,
        "knowledge_snapshot_hash": knowledge_snapshot_hash,
        "retrieval_pipeline_version": RETRIEVAL_PIPELINE_VERSION,
        "retrieval_config": retrieval_config,
        "evaluator_code_version": evaluator_code_version or RETRIEVAL_PIPELINE_VERSION,
    }
    return content_hash(components), components
