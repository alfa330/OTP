"""Immutable knowledge lifecycle for production RAG.

Human adjudications are evidence records.  They never become live prompt
instructions directly: a reviewed case creates a versioned policy-rule draft,
an administrator indexes and activates that version, and activation publishes a
content-addressed knowledge snapshot.  Evaluation fingerprints refer to that
snapshot, so old results remain reproducible after later edits.

All functions accept an existing connection.  The caller owns the transaction;
this keeps case + rule + embedding and lifecycle + snapshot changes atomic.
"""
from __future__ import annotations

import uuid
from typing import Any

from psycopg2.extras import Json

from ..evaluation.fingerprint import canonical_json, content_hash


RULE_STATUSES = frozenset({"draft", "active", "deprecated", "quarantined"})
EVIDENCE_STATUSES = frozenset({"unverified", "verified", "no_evidence", "rejected", "missing"})
CASE_STATUSES = frozenset({"submitted", "verified", "rejected", "quarantined"})
INDEX_STATUSES = frozenset({"pending", "ready", "error"})


class KnowledgeConflict(RuntimeError):
    """Concurrent lifecycle/version change or an invalid expected state."""


class KnowledgeValidationError(ValueError):
    """A case/rule cannot safely enter the knowledge lifecycle."""


def _vector(value: list[float] | tuple[float, ...]) -> str:
    return "[" + ",".join(format(float(item), ".12g") for item in value) + "]"


def _criterion_manifest(criteria: list[dict]) -> list[dict]:
    return [{
        "criterion_id": str(item["criterion_id"]),
        "criterion_idx": int(item["idx"]),
        "criterion_name": str(item.get("name") or ""),
        "description": str(item.get("description") or item.get("value") or ""),
        "weight": item.get("weight"),
        "is_critical": bool(item.get("is_critical")),
        "deficiency": item.get("deficiency"),
        "eval_source": item.get("eval_source"),
    } for item in criteria]


def scale_revision_fingerprint(*, direction_id: int, scale_hash: str,
                               criteria_manifest: list[dict]) -> str:
    """Identity of the complete immutable scale contract.

    ``criteria.scale_hash`` describes the monitoring scale itself.  Evaluation
    source is configured separately, but it is persisted in
    ``qa_scale_revision_criteria`` and therefore must also participate in the
    revision identity.  Version 2 intentionally creates one replacement revision
    for pre-v2 rows whose content hash covered only the structural scale.
    """
    direction_id = int(direction_id)
    structural_hash = str(scale_hash or "").strip().lower()
    if len(structural_hash) != 64 or any(ch not in "0123456789abcdef"
                                         for ch in structural_hash):
        structural_hash = content_hash({
            "direction_id": direction_id,
            "criteria": criteria_manifest,
        })
    return content_hash({
        "identity_version": 2,
        "direction_id": direction_id,
        "structural_scale_hash": structural_hash,
        "criteria_manifest": criteria_manifest,
    })


def sync_scale_revision(conn, *, direction_id: int, scale_hash: str,
                        criteria: list[dict], created_by=None) -> int:
    """Register the current scale and return its immutable revision row ID."""
    direction_id = int(direction_id)
    manifest = _criterion_manifest(criteria)
    if not manifest:
        raise KnowledgeValidationError("monitoring scale has no criteria")
    if len({row["criterion_id"] for row in manifest}) != len(manifest):
        raise KnowledgeValidationError("criterion_id values must be unique inside a scale")
    revision_hash = scale_revision_fingerprint(
        direction_id=direction_id, scale_hash=scale_hash, criteria_manifest=manifest)

    with conn.cursor() as cur:
        # Serialises revision allocation across application workers.
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (71621, direction_id))
        cur.execute(
            "SELECT id FROM qa_scale_revisions WHERE direction_id=%s AND content_hash=%s",
            (direction_id, revision_hash),
        )
        existing = cur.fetchone()
        if existing:
            return int(existing[0])

        for row in manifest:
            stable_key = row["criterion_id"]
            cur.execute("SELECT direction_id FROM qa_criterion_registry WHERE criterion_id=%s",
                        (row["criterion_id"],))
            registered = cur.fetchone()
            if registered and int(registered[0]) != direction_id:
                raise KnowledgeValidationError(
                    f"criterion_id {row['criterion_id']!r} is already owned by direction {registered[0]}")
            cur.execute(
                """INSERT INTO qa_criterion_registry
                       (criterion_id, direction_id, stable_key, canonical_name, metadata)
                     VALUES (%s,%s,%s,%s,%s)
                     ON CONFLICT (criterion_id) DO UPDATE SET
                       canonical_name=EXCLUDED.canonical_name,
                       last_seen_at=now(), deprecated_at=NULL""",
                (row["criterion_id"], direction_id, stable_key,
                 row["criterion_name"], Json({})),
            )
        cur.execute(
            "SELECT COALESCE(MAX(scale_revision),0)+1 FROM qa_scale_revisions WHERE direction_id=%s",
            (direction_id,),
        )
        revision = int(cur.fetchone()[0])
        cur.execute(
            """INSERT INTO qa_scale_revisions
                   (direction_id, scale_revision, content_hash, criteria_manifest, created_by)
                 VALUES (%s,%s,%s,%s,%s) RETURNING id""",
            (direction_id, revision, revision_hash, Json(manifest), created_by),
        )
        scale_revision_id = int(cur.fetchone()[0])
        for row in manifest:
            cur.execute(
                """INSERT INTO qa_scale_revision_criteria
                       (scale_revision_id, criterion_id, criterion_idx, criterion_name,
                        description, weight, is_critical, deficiency, eval_source)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (scale_revision_id, row["criterion_id"], row["criterion_idx"],
                 row["criterion_name"], row["description"], row["weight"],
                 row["is_critical"], Json(row["deficiency"]), row["eval_source"]),
            )
        cur.execute(
            """INSERT INTO qa_knowledge_state
                   (direction_id, scale_revision_id, current_revision)
                 VALUES (%s,%s,0) ON CONFLICT DO NOTHING""",
            (direction_id, scale_revision_id),
        )
    return scale_revision_id


def _active_rule_manifest(cur, direction_id: int) -> list[dict]:
    from ..embeddings.provider import configured_contract
    contract = configured_contract()
    cur.execute(
        """SELECT r.id::text, v.id, r.criterion_id, v.rule_version,
                  v.content_hash::text, v.situation, v.rule_text, v.not_covered,
                  v.correct_verdict,e.id,m.id,m.embedding_provider,m.embedding_model,
                  m.embedding_dim,m.config_hash::text,
                  encode(digest(e.embedding::text,'sha256'),'hex')
             FROM qa_policy_rules r
             JOIN qa_policy_rule_versions v ON v.id=r.current_version_id
             JOIN qa_policy_rule_embeddings e
               ON e.rule_version_id=v.id AND e.index_status='ready' AND e.embedding IS NOT NULL
             JOIN qa_embedding_models m ON m.id=e.embedding_model_id
            WHERE r.direction_id=%s AND r.rule_status='active'
              AND m.embedding_provider=%s AND m.embedding_model=%s
              AND m.embedding_dim=%s AND m.config_hash=%s
            ORDER BY r.criterion_id, r.id""",
        (int(direction_id), contract["provider"], contract["model"],
         contract["dim"], contract["config_hash"]),
    )
    return [{
        "rule_id": row[0], "rule_version_id": int(row[1]), "criterion_id": row[2],
        "rule_version": int(row[3]), "content_hash": row[4], "situation": row[5],
        "rule_text": row[6], "not_covered": row[7], "correct_verdict": row[8],
        "embedding_id": int(row[9]), "embedding_model_id": int(row[10]),
        "embedding_provider": row[11], "embedding_model": row[12],
        "embedding_dim": int(row[13]), "embedding_config_hash": row[14],
        "embedding_hash": row[15],
    } for row in cur.fetchall()]


def create_knowledge_snapshot(conn, *, direction_id: int, scale_revision_id: int,
                              created_by=None, reason: str | None = None) -> dict:
    """Publish or reuse the snapshot for the exact set of active rule versions."""
    direction_id = int(direction_id)
    scale_revision_id = int(scale_revision_id)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (71622, direction_id))
        cur.execute(
            """SELECT content_hash::text FROM qa_scale_revisions
                WHERE id=%s AND direction_id=%s""", (scale_revision_id, direction_id))
        scale_row = cur.fetchone()
        if not scale_row:
            raise KnowledgeValidationError("scale revision does not belong to the direction")
        scale_content_hash = scale_row[0]
        manifest = _active_rule_manifest(cur, direction_id)
        compact = [{key: row[key] for key in
                    ("rule_id", "rule_version_id", "criterion_id", "rule_version", "content_hash",
                     "embedding_id", "embedding_model_id", "embedding_provider",
                     "embedding_model", "embedding_dim", "embedding_config_hash",
                     "embedding_hash")}
                   for row in manifest]
        identity_manifest = [{key: row[key] for key in
                              ("criterion_id", "content_hash", "embedding_provider", "embedding_model",
                               "embedding_dim", "embedding_config_hash", "embedding_hash")}
                             for row in manifest]
        snapshot_hash = content_hash({
            "direction_id": direction_id,
            "scale_content_hash": scale_content_hash,
            "rules": identity_manifest,
        })
        cur.execute(
            """SELECT id, knowledge_revision, content_hash::text, rule_count, created_at
                 FROM qa_knowledge_snapshots
                WHERE direction_id=%s AND scale_revision_id=%s AND content_hash=%s
                ORDER BY id DESC LIMIT 1""",
            (direction_id, scale_revision_id, snapshot_hash),
        )
        row = cur.fetchone()
        if row:
            snapshot_id, revision = int(row[0]), int(row[1])
            cur.execute(
                """INSERT INTO qa_knowledge_state
                       (direction_id,scale_revision_id,current_revision,current_snapshot_id,
                        updated_by,updated_at)
                     VALUES (%s,%s,%s,%s,%s,now())
                     ON CONFLICT (direction_id,scale_revision_id) DO UPDATE SET
                       current_revision=EXCLUDED.current_revision,
                       current_snapshot_id=EXCLUDED.current_snapshot_id,
                       updated_by=EXCLUDED.updated_by,updated_at=now()""",
                (direction_id, scale_revision_id, revision, snapshot_id, created_by),
            )
            return {"id": snapshot_id, "knowledge_revision": revision,
                    "content_hash": row[2], "rule_count": int(row[3]),
                    "created_at": row[4], "reused": True}

        # Revisions are monotonic per direction, including scale changes.
        cur.execute(
            "SELECT COALESCE(MAX(knowledge_revision),0)+1 FROM qa_knowledge_snapshots WHERE direction_id=%s",
            (direction_id,),
        )
        revision = int(cur.fetchone()[0])
        policy_pack = "\n\n".join(
            f"[{row['criterion_id']}] {row['situation']}\n{row['rule_text']}"
            + (f"\nНЕ применяется: {row['not_covered']}" if row.get("not_covered") else "")
            for row in manifest
        )
        cur.execute(
            """INSERT INTO qa_knowledge_snapshots
                   (direction_id,scale_revision_id,knowledge_revision,content_hash,
                    rule_manifest,policy_pack,rule_count,reason,created_by)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                 RETURNING id,created_at""",
            (direction_id, scale_revision_id, revision, snapshot_hash, Json(compact),
             policy_pack, len(manifest), reason, created_by),
        )
        snapshot_id, created_at = cur.fetchone()
        snapshot_id = int(snapshot_id)
        for ordinal, item in enumerate(manifest):
            cur.execute(
                """INSERT INTO qa_knowledge_snapshot_rules
                       (snapshot_id,rule_id,rule_version_id,criterion_id,ordinal,content_hash,
                        embedding_id)
                     VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (snapshot_id, item["rule_id"], item["rule_version_id"],
                 item["criterion_id"], ordinal, item["content_hash"], item["embedding_id"]),
            )
        cur.execute(
            """INSERT INTO qa_knowledge_state
                   (direction_id,scale_revision_id,current_revision,current_snapshot_id,
                    updated_by,updated_at)
                 VALUES (%s,%s,%s,%s,%s,now())
                 ON CONFLICT (direction_id,scale_revision_id) DO UPDATE SET
                   current_revision=EXCLUDED.current_revision,
                   current_snapshot_id=EXCLUDED.current_snapshot_id,
                   updated_by=EXCLUDED.updated_by,updated_at=now()""",
            (direction_id, scale_revision_id, revision, snapshot_id, created_by),
        )
    return {"id": snapshot_id, "knowledge_revision": revision,
            "content_hash": snapshot_hash, "rule_count": len(manifest),
            "created_at": created_at, "reused": False}


def ensure_knowledge_context(conn, *, direction: dict, created_by=None) -> dict:
    """Synchronise scale metadata and return a usable current snapshot."""
    scale_revision_id = sync_scale_revision(
        conn, direction_id=direction["id"], scale_hash=direction.get("scale_hash") or "",
        criteria=direction["criteria"], created_by=created_by,
    )
    with conn.cursor() as cur:
        cur.execute(
            """SELECT k.id,k.knowledge_revision,k.content_hash::text,k.rule_count,k.created_at
                 FROM qa_knowledge_state s
                 JOIN qa_knowledge_snapshots k ON k.id=s.current_snapshot_id
                WHERE s.direction_id=%s AND s.scale_revision_id=%s""",
            (int(direction["id"]), scale_revision_id),
        )
        row = cur.fetchone()
    snapshot = ({"id": int(row[0]), "knowledge_revision": int(row[1]),
                 "content_hash": row[2], "rule_count": int(row[3]), "created_at": row[4],
                 "reused": True} if row else
                create_knowledge_snapshot(conn, direction_id=direction["id"],
                                          scale_revision_id=scale_revision_id,
                                          created_by=created_by, reason="initial snapshot"))
    return {"scale_revision_id": scale_revision_id, "snapshot": snapshot}


def peek_knowledge_snapshot_hash(conn, *, direction: dict) -> str | None:
    """Read-only-двойник ensure_knowledge_context: content_hash текущего снапшота
    для актуальной шкалы направления. None — снапшот ещё не создан (его создало бы
    первое открытие/оценка). Ничего не пишет — безопасно для GET-эндпоинтов."""
    manifest = _criterion_manifest(direction["criteria"])
    revision_hash = scale_revision_fingerprint(
        direction_id=int(direction["id"]), scale_hash=direction.get("scale_hash") or "",
        criteria_manifest=manifest)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT k.content_hash::text
                 FROM qa_scale_revisions r
                 JOIN qa_knowledge_state s
                   ON s.direction_id = r.direction_id AND s.scale_revision_id = r.id
                 JOIN qa_knowledge_snapshots k ON k.id = s.current_snapshot_id
                WHERE r.direction_id = %s AND r.content_hash = %s""",
            (int(direction["id"]), revision_hash))
        row = cur.fetchone()
    return row[0] if row else None


def create_adjudication_case(conn, *, direction_id: int, criterion_id: str,
                             correct_verdict: str, evidence_excerpt: str, reason: str,
                             criterion_idx=None, criterion_name=None, scale_revision_id=None,
                             call_id=None, evaluation_run_id=None, ai_verdict=None,
                             evidence_status="unverified", evidence_start_offset=None,
                             evidence_end_offset=None, evidence_start_ms=None,
                             evidence_end_ms=None, transcript_hash=None, situation=None,
                             not_covered=None, case_status=None, supersedes_case_id=None,
                             created_by=None, verified_by=None, metadata=None,
                             legacy_adjudication_id=None) -> str:
    evidence_status = str(evidence_status)
    if evidence_status not in EVIDENCE_STATUSES:
        raise KnowledgeValidationError(f"invalid evidence_status: {evidence_status}")
    if not str(reason or "").strip():
        raise KnowledgeValidationError("adjudication reason is required")
    if evidence_status == "verified" and (not evidence_excerpt or evidence_start_offset is None or
                                           evidence_end_offset is None or not transcript_hash):
        raise KnowledgeValidationError("verified evidence requires excerpt, offsets and transcript hash")
    if evidence_status == "no_evidence" and str(evidence_excerpt or "").strip():
        raise KnowledgeValidationError("no_evidence case cannot contain an excerpt")
    case_status = case_status or ("verified" if verified_by is not None else "submitted")
    if case_status not in CASE_STATUSES:
        raise KnowledgeValidationError(f"invalid case_status: {case_status}")
    digest = content_hash({
        "direction_id": int(direction_id), "criterion_id": str(criterion_id),
        "call_id": call_id,
        "evaluation_run_id": str(evaluation_run_id) if evaluation_run_id else None,
        "correct_verdict": correct_verdict,
        "evidence_excerpt": evidence_excerpt or "", "evidence_status": evidence_status,
        "reason": str(reason).strip(), "situation": situation, "not_covered": not_covered,
        "transcript_hash": transcript_hash, "legacy_adjudication_id": legacy_adjudication_id,
    })
    case_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO qa_adjudication_cases
                   (id,direction_id,criterion_id,criterion_idx,criterion_name,scale_revision_id,
                    call_id,evaluation_run_id,ai_verdict,correct_verdict,evidence_excerpt,
                    verified_excerpt,evidence_status,evidence_start_offset,evidence_end_offset,
                    evidence_start_ms,evidence_end_ms,transcript_hash,situation,reason,not_covered,
                    case_status,content_hash,supersedes_case_id,legacy_adjudication_id,
                    created_by,verified_by,verified_at,metadata)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                         %s,%s,%s,%s,%s,%s,%s,CASE WHEN %s IS NULL THEN NULL ELSE now() END,%s)
                 ON CONFLICT DO NOTHING RETURNING id::text""",
            (case_id, int(direction_id), str(criterion_id), criterion_idx, criterion_name,
             scale_revision_id, call_id, evaluation_run_id, ai_verdict, correct_verdict,
             evidence_excerpt or "", evidence_status == "verified", evidence_status,
             evidence_start_offset, evidence_end_offset, evidence_start_ms, evidence_end_ms,
             transcript_hash, situation, str(reason).strip(), not_covered, case_status, digest,
             supersedes_case_id, legacy_adjudication_id, created_by, verified_by, verified_by,
             Json(metadata or {})),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        cur.execute(
            """SELECT id::text FROM qa_adjudication_cases
                WHERE call_id IS NOT DISTINCT FROM %s AND direction_id=%s AND criterion_id=%s
                  AND content_hash=%s ORDER BY created_at DESC LIMIT 1""",
            (call_id, int(direction_id), str(criterion_id), digest),
        )
        existing = cur.fetchone()
        if not existing:
            raise KnowledgeConflict("case insert conflicted but no matching case was found")
        return existing[0]


def create_draft_policy_rule(conn, *, case_id: str, direction_id: int,
                             criterion_id: str, situation: str | None, rule_text: str,
                             correct_verdict: str, criterion_idx=None, criterion_name=None,
                             not_covered=None, excerpt=None, verified_excerpt=False,
                             evidence_status=None, evidence_start_offset=None,
                             evidence_end_offset=None, created_by=None, metadata=None) -> dict:
    clean_rule = str(rule_text or "").strip()
    clean_situation = str(situation or excerpt or clean_rule).strip()
    if not clean_rule or not clean_situation:
        raise KnowledgeValidationError("situation and rule text cannot be empty")
    evidence_status = evidence_status or ("verified" if verified_excerpt else "no_evidence")
    digest = content_hash({
        "situation": clean_situation, "rule_text": clean_rule,
        "not_covered": not_covered, "correct_verdict": correct_verdict,
        "excerpt": excerpt or "", "evidence_status": evidence_status,
    })
    rule_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO qa_policy_rules
                   (id,direction_id,criterion_id,criterion_idx,criterion_name,rule_status,
                    created_by,updated_by,change_reason,metadata)
                 VALUES (%s,%s,%s,%s,%s,'draft',%s,%s,%s,%s)""",
            (rule_id, int(direction_id), str(criterion_id), criterion_idx, criterion_name,
             created_by, created_by, "created from verified adjudication", Json(metadata or {})),
        )
        cur.execute(
            """INSERT INTO qa_policy_rule_versions
                   (rule_id,rule_version,content_hash,situation,rule_text,not_covered,
                    correct_verdict,excerpt,verified_excerpt,evidence_status,
                    evidence_start_offset,evidence_end_offset,source_case_id,created_by,
                    change_summary,metadata)
                 VALUES (%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                 RETURNING id""",
            (rule_id, digest, clean_situation, clean_rule, not_covered, correct_verdict,
             excerpt or None, bool(verified_excerpt), evidence_status, evidence_start_offset,
             evidence_end_offset, case_id, created_by, "initial draft", Json(metadata or {})),
        )
        version_id = int(cur.fetchone()[0])
        cur.execute(
            """UPDATE qa_policy_rules SET current_version_id=%s,updated_by=%s,
                       change_reason=%s WHERE id=%s""",
            (version_id, created_by, "initial version", rule_id),
        )
        cur.execute(
            """INSERT INTO qa_policy_rule_events
                   (rule_id,rule_version_id,event_type,from_status,to_status,actor_id,reason)
                 VALUES (%s,%s,'version_created','draft','draft',%s,%s)""",
            (rule_id, version_id, created_by, "initial draft"),
        )
    return {"rule_id": rule_id, "rule_version_id": version_id, "rule_version": 1,
            "content_hash": digest, "rule_status": "draft"}


def revise_policy_rule(conn, *, rule_id: str, changes: dict, actor_id=None,
                       reason: str, expected_version_id=None,
                       publish_snapshot: bool = True) -> dict:
    """Create a new immutable version and return the rule to reviewable draft."""
    if not str(reason or "").strip():
        raise KnowledgeValidationError("change reason is required")
    allowed = {"situation", "rule_text", "reason", "not_covered", "correct_verdict",
               "excerpt", "verified_excerpt", "evidence_status",
               "evidence_start_offset", "evidence_end_offset"}
    unknown = set(changes) - allowed
    if unknown:
        raise KnowledgeValidationError(f"unsupported rule fields: {', '.join(sorted(unknown))}")
    changes = dict(changes)
    if "reason" in changes and "rule_text" not in changes:
        changes["rule_text"] = changes.pop("reason")
    with conn.cursor() as cur:
        cur.execute(
            """SELECT r.current_version_id,r.rule_status,v.rule_version,v.situation,v.rule_text,
                      v.not_covered,v.correct_verdict,v.excerpt,v.verified_excerpt,
                      v.evidence_status,v.evidence_start_offset,v.evidence_end_offset,
                      v.source_case_id,v.metadata
                 FROM qa_policy_rules r
                 JOIN qa_policy_rule_versions v ON v.id=r.current_version_id
                WHERE r.id=%s FOR UPDATE OF r""",
            (str(rule_id),),
        )
        row = cur.fetchone()
        if not row:
            raise KeyError(str(rule_id))
        if expected_version_id is not None and int(row[0]) != int(expected_version_id):
            raise KnowledgeConflict("rule version changed concurrently")
        keys = ("situation", "rule_text", "not_covered", "correct_verdict", "excerpt",
                "verified_excerpt", "evidence_status", "evidence_start_offset",
                "evidence_end_offset")
        values = dict(zip(keys, row[3:12]))
        values.update({key: value for key, value in changes.items() if key in keys})
        values["situation"] = str(values.get("situation") or "").strip()
        values["rule_text"] = str(values.get("rule_text") or "").strip()
        if not values["situation"] or not values["rule_text"]:
            raise KnowledgeValidationError("situation and rule text cannot be empty")
        digest = content_hash(values)
        next_version = int(row[2]) + 1
        cur.execute(
            """INSERT INTO qa_policy_rule_versions
                   (rule_id,rule_version,content_hash,situation,rule_text,not_covered,
                    correct_verdict,excerpt,verified_excerpt,evidence_status,
                    evidence_start_offset,evidence_end_offset,source_case_id,created_by,
                    change_summary,metadata)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                 RETURNING id""",
            (str(rule_id), next_version, digest, values["situation"], values["rule_text"],
             values.get("not_covered"), values.get("correct_verdict"), values.get("excerpt"),
             bool(values.get("verified_excerpt")), values.get("evidence_status") or "unverified",
             values.get("evidence_start_offset"), values.get("evidence_end_offset"), row[12],
             actor_id, str(reason).strip(), Json(row[13] or {})),
        )
        version_id = int(cur.fetchone()[0])
        cur.execute(
            """UPDATE qa_policy_rules SET current_version_id=%s,rule_status='draft',
                       updated_by=%s,change_reason=%s WHERE id=%s""",
            (version_id, actor_id, str(reason).strip(), str(rule_id)),
        )
        cur.execute(
            """INSERT INTO qa_policy_rule_events
                   (rule_id,rule_version_id,event_type,from_status,to_status,actor_id,reason)
                 VALUES (%s,%s,'version_created',%s,'draft',%s,%s)""",
            (str(rule_id), version_id, row[1], actor_id, str(reason).strip()),
        )
        cur.execute("SELECT direction_id FROM qa_policy_rules WHERE id=%s", (str(rule_id),))
        direction_id = int(cur.fetchone()[0])
    snapshot = None
    if publish_snapshot and row[1] == "active":
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id FROM qa_scale_revisions WHERE direction_id=%s
                    ORDER BY scale_revision DESC LIMIT 1""", (direction_id,))
            scale_row = cur.fetchone()
        if scale_row:
            snapshot = create_knowledge_snapshot(
                conn, direction_id=direction_id, scale_revision_id=int(scale_row[0]),
                created_by=actor_id, reason=str(reason).strip())
    return {"rule_id": str(rule_id), "rule_version_id": version_id,
            "rule_version": next_version, "content_hash": digest, "rule_status": "draft"}


def ensure_embedding_model(conn, *, provider: str, model: str, embedding_dim: int,
                           config: dict | None = None) -> int:
    if config is None:
        from ..embeddings.provider import configured_contract
        contract = configured_contract()
        cfg = (contract["config"] if (provider, model, int(embedding_dim)) ==
               (contract["provider"], contract["model"], int(contract["dim"])) else {})
    else:
        cfg = config
    cfg_hash = content_hash(cfg)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO qa_embedding_models
                   (embedding_provider,embedding_model,embedding_dim,config_hash,config)
                 VALUES (%s,%s,%s,%s,%s)
                 ON CONFLICT (embedding_provider,embedding_model,embedding_dim,config_hash)
                 DO UPDATE SET model_status='active'
                 RETURNING id""",
            (provider, model, int(embedding_dim), cfg_hash, Json(cfg)),
        )
        return int(cur.fetchone()[0])


def record_rule_embedding(conn, *, rule_version_id: int, provider: str, model: str,
                          embedding_dim: int, embedding: list[float], config: dict | None = None,
                          index_status: str = "ready") -> int:
    if index_status not in INDEX_STATUSES:
        raise KnowledgeValidationError(f"invalid index_status: {index_status}")
    if len(embedding) != int(embedding_dim):
        raise KnowledgeValidationError("embedding dimension does not match embedding_dim")
    model_id = ensure_embedding_model(conn, provider=provider, model=model,
                                      embedding_dim=embedding_dim, config=config)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO qa_policy_rule_embeddings
                   (rule_version_id,embedding_model_id,embedding,embedding_dim,index_status,
                    indexed_at,index_error)
                 VALUES (%s,%s,%s::vector,%s,%s,
                         CASE WHEN %s='ready' THEN now() ELSE NULL END,NULL)
                 ON CONFLICT (rule_version_id,embedding_model_id) DO UPDATE SET
                   embedding=EXCLUDED.embedding,index_status=EXCLUDED.index_status,
                   indexed_at=EXCLUDED.indexed_at,index_error=NULL,updated_at=now()
                 WHERE qa_policy_rule_embeddings.index_status <> 'ready'
                 RETURNING id""",
            (int(rule_version_id), model_id, _vector(embedding), int(embedding_dim),
             index_status, index_status),
        )
        row = cur.fetchone()
        if row:
            return int(row[0])
        cur.execute(
            """SELECT id FROM qa_policy_rule_embeddings
                WHERE rule_version_id=%s AND embedding_model_id=%s""",
            (int(rule_version_id), model_id))
        return int(cur.fetchone()[0])


def mark_rule_index_error(conn, *, rule_version_id: int, provider: str, model: str,
                          embedding_dim: int, error: str, config: dict | None = None) -> int:
    model_id = ensure_embedding_model(conn, provider=provider, model=model,
                                      embedding_dim=embedding_dim, config=config)
    message = str(error or "embedding failed")[:2000]
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO qa_policy_rule_embeddings
                   (rule_version_id,embedding_model_id,embedding_dim,index_status,index_error)
                 VALUES (%s,%s,%s,'error',%s)
                 ON CONFLICT (rule_version_id,embedding_model_id) DO UPDATE SET
                   embedding=NULL,index_status='error',index_error=EXCLUDED.index_error,
                   indexed_at=NULL,updated_at=now()
                 WHERE qa_policy_rule_embeddings.index_status <> 'ready'
                 RETURNING id""",
            (int(rule_version_id), model_id, int(embedding_dim), message),
        )
        row = cur.fetchone()
        if row:
            return int(row[0])
        cur.execute(
            """SELECT id FROM qa_policy_rule_embeddings
                WHERE rule_version_id=%s AND embedding_model_id=%s""",
            (int(rule_version_id), model_id))
        return int(cur.fetchone()[0])


def mark_rule_index_pending(conn, *, rule_version_id: int, provider: str, model: str,
                            embedding_dim: int, config: dict | None = None) -> int:
    model_id = ensure_embedding_model(conn, provider=provider, model=model,
                                      embedding_dim=embedding_dim, config=config)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO qa_policy_rule_embeddings
                   (rule_version_id,embedding_model_id,embedding_dim,index_status)
                 VALUES (%s,%s,%s,'pending')
                 ON CONFLICT (rule_version_id,embedding_model_id) DO UPDATE SET
                   embedding=NULL,index_status='pending',index_error=NULL,
                   indexed_at=NULL,updated_at=now()
                 WHERE qa_policy_rule_embeddings.index_status <> 'ready'
                 RETURNING id""",
            (int(rule_version_id), model_id, int(embedding_dim)),
        )
        row = cur.fetchone()
        if row:
            return int(row[0])
        cur.execute(
            """SELECT id FROM qa_policy_rule_embeddings
                WHERE rule_version_id=%s AND embedding_model_id=%s""",
            (int(rule_version_id), model_id))
        return int(cur.fetchone()[0])


def transition_policy_rule(conn, *, rule_id: str, to_status: str, actor_id=None,
                           reason: str, expected_status=None, version_id=None,
                           scale_revision_id=None, expected_version_id=None) -> dict:
    if to_status not in RULE_STATUSES:
        raise KnowledgeValidationError(f"invalid rule status: {to_status}")
    if not str(reason or "").strip():
        raise KnowledgeValidationError("transition reason is required")
    with conn.cursor() as cur:
        cur.execute(
            """SELECT direction_id,rule_status,current_version_id
                 FROM qa_policy_rules WHERE id=%s FOR UPDATE""",
            (str(rule_id),),
        )
        row = cur.fetchone()
        if not row:
            raise KeyError(str(rule_id))
        direction_id, current_status, current_version = int(row[0]), row[1], int(row[2])
        if expected_status is not None and current_status != expected_status:
            raise KnowledgeConflict(
                f"rule status changed concurrently: expected {expected_status}, got {current_status}")
        if expected_version_id is not None and current_version != int(expected_version_id):
            raise KnowledgeConflict(
                f"rule version changed concurrently: expected {expected_version_id}, got {current_version}")
        selected_version = int(version_id or current_version)
        if selected_version != current_version:
            raise KnowledgeConflict("lifecycle transition can only use the current rule version")
        cur.execute("SELECT 1 FROM qa_policy_rule_versions WHERE id=%s AND rule_id=%s",
                    (selected_version, str(rule_id)))
        if not cur.fetchone():
            raise KnowledgeValidationError("selected version does not belong to the rule")
        allowed = {
            "draft": {"active", "deprecated", "quarantined"},
            "active": {"draft", "deprecated", "quarantined"},
            "deprecated": {"draft", "quarantined"},
            "quarantined": {"draft", "deprecated"},
        }
        if to_status != current_status and to_status not in allowed[current_status]:
            raise KnowledgeValidationError(f"transition {current_status} -> {to_status} is not allowed")
        if to_status == "active":
            from ..embeddings.provider import configured_contract
            contract = configured_contract()
            if scale_revision_id is None:
                raise KnowledgeValidationError("activation requires scale_revision_id")
            cur.execute(
                """SELECT 1 FROM qa_policy_rule_embeddings e
                    JOIN qa_embedding_models m ON m.id=e.embedding_model_id
                    WHERE e.rule_version_id=%s AND e.index_status='ready'
                      AND e.embedding IS NOT NULL AND m.embedding_provider=%s
                      AND m.embedding_model=%s AND m.embedding_dim=%s AND m.config_hash=%s
                    LIMIT 1""",
                (selected_version, contract["provider"], contract["model"],
                 contract["dim"], contract["config_hash"]),
            )
            if not cur.fetchone():
                raise KnowledgeValidationError("rule must have a ready embedding before activation")
            cur.execute(
                """SELECT evidence_status FROM qa_policy_rule_versions WHERE id=%s AND rule_id=%s""",
                (selected_version, str(rule_id)),
            )
            version_row = cur.fetchone()
            if not version_row or version_row[0] not in ("verified", "no_evidence"):
                raise KnowledgeValidationError("rule evidence must be verified or explicitly absent")
        cur.execute(
            """UPDATE qa_policy_rules SET rule_status=%s,current_version_id=%s,
                       updated_by=%s,change_reason=%s WHERE id=%s""",
            (to_status, selected_version, actor_id, str(reason).strip(), str(rule_id)),
        )
    snapshot = None
    if to_status == "active" or current_status == "active":
        if scale_revision_id is None:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id FROM qa_scale_revisions WHERE direction_id=%s
                        ORDER BY scale_revision DESC LIMIT 1""", (direction_id,))
                scale_row = cur.fetchone()
            if not scale_row:
                raise KnowledgeValidationError("direction has no registered scale revision")
            scale_revision_id = int(scale_row[0])
        snapshot = create_knowledge_snapshot(
            conn, direction_id=direction_id, scale_revision_id=int(scale_revision_id),
            created_by=actor_id, reason=str(reason).strip(),
        )
    return {"rule_id": str(rule_id), "rule_version_id": selected_version,
            "rule_status": to_status, "knowledge_snapshot": snapshot}


def rule_document_text(*, situation: str | None, excerpt: str | None,
                       rule_text: str | None = None) -> str:
    """Text used for candidate search; exclusions/instructions are added after selection."""
    parts = [str(value).strip() for value in (situation, excerpt) if str(value or "").strip()]
    if not parts and str(rule_text or "").strip():
        parts.append(str(rule_text).strip())
    return "\n".join(parts)
