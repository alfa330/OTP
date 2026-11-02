"""Раздел «call_calibration»: ручки /api/call_calibration — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_call_calibration_blueprint,
как у остальных пакетов репозитория."""
from flask import Blueprint, g, jsonify, request, send_file
from database import db
from datetime import datetime
from datetime import timedelta
from google.cloud import storage as gcs_storage
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Alignment
from openpyxl.styles import Border
from openpyxl.styles import Font
from openpyxl.styles import PatternFill
from openpyxl.styles import Side
from openpyxl.utils import get_column_letter
from werkzeug.utils import secure_filename
import json
import logging
import os
import re
import uuid


CALIBRATION_FORBIDDEN_ERROR = "Калибровка доступна руководителям и супервайзерам"

def _normalize_calibration_score_value(value):
    raw = str(value or '').strip().lower()
    mapping = {
        'correct': 'Correct',
        'ok': 'Correct',
        'n/a': 'N/A',
        'na': 'N/A',
        'n\\a': 'N/A',
        'incorrect': 'Incorrect',
        'deficiency': 'Deficiency',
        'error': 'Error'
    }
    return mapping.get(raw, str(value or 'Correct').strip() or 'Correct')

def _calibration_label_score(value):
    normalized = _normalize_calibration_score_value(value)
    labels = {
        'Correct': 'Корректно',
        'N/A': 'N/A',
        'Incorrect': 'Ошибка',
        'Deficiency': 'Недочет',
        'Error': 'Критич. ошибка'
    }
    return labels.get(normalized, normalized)

def _compute_total_score_from_criteria(criteria, scores):
    """Балл калибровки — формула журнала (src/call_evaluation/journalScore.js)."""
    criteria = criteria if isinstance(criteria, list) else []
    scores = scores if isinstance(scores, list) else []
    has_critical_error = False
    total = 0.0
    penalty = 0.0

    for idx, criterion in enumerate(criteria):
        criterion = criterion if isinstance(criterion, dict) else {}
        score_value = _normalize_calibration_score_value(scores[idx] if idx < len(scores) else 'Correct')
        is_critical = bool(criterion.get('isCritical'))
        if is_critical:
            if score_value == 'Error':
                has_critical_error = True
            elif score_value == 'Deficiency':
                # Недочёт критического критерия не обнуляет итог, а снимает
                # установленное в шкале число баллов (deficiency.weight).
                deficiency = criterion.get('deficiency') if isinstance(criterion.get('deficiency'), dict) else {}
                try:
                    penalty += max(0.0, float(deficiency.get('weight') or 0))
                except Exception:
                    pass
            continue
        try:
            weight = float(criterion.get('weight') or 0)
        except Exception:
            weight = 0.0
        if score_value in ('Correct', 'N/A'):
            total += weight
        elif score_value == 'Deficiency':
            deficiency = criterion.get('deficiency') if isinstance(criterion.get('deficiency'), dict) else {}
            try:
                total += float(deficiency.get('weight') or 0)
            except Exception:
                pass

    if has_critical_error:
        return 0.0, True
    return round(max(0.0, float(total) - penalty), 2), False

def _build_calibration_results(criteria, etalon_scores, etalon_comments, evaluations, admin_scores=None, admin_comments=None):
    criteria = criteria if isinstance(criteria, list) else []
    etalon_scores = etalon_scores if isinstance(etalon_scores, list) else []
    etalon_comments = etalon_comments if isinstance(etalon_comments, list) else []
    admin_scores = admin_scores if isinstance(admin_scores, list) else etalon_scores
    admin_comments = admin_comments if isinstance(admin_comments, list) else etalon_comments
    evaluations = evaluations if isinstance(evaluations, list) else []

    eval_count = len(evaluations)
    critical_mismatch = False
    rows = []

    for idx, criterion in enumerate(criteria):
        criterion = criterion if isinstance(criterion, dict) else {}
        is_critical = bool(criterion.get('isCritical'))
        admin_val = _normalize_calibration_score_value(admin_scores[idx] if idx < len(admin_scores) else 'Correct')
        etalon_val = _normalize_calibration_score_value(etalon_scores[idx] if idx < len(etalon_scores) else admin_val)
        # Percent is based on overall agreement between all participants:
        # admin + all supervisors. Etalon is displayed separately and does not
        # affect calibration percent.
        score_counts = {admin_val: 1}
        by_evaluator = []

        for evaluation in evaluations:
            ev_scores = evaluation.get('scores') if isinstance(evaluation.get('scores'), list) else []
            ev_comments = evaluation.get('criterion_comments') if isinstance(evaluation.get('criterion_comments'), list) else []
            ev_val = _normalize_calibration_score_value(ev_scores[idx] if idx < len(ev_scores) else 'Correct')
            score_counts[ev_val] = int(score_counts.get(ev_val, 0)) + 1
            is_match = ev_val == admin_val

            by_evaluator.append({
                "evaluator_id": evaluation.get('evaluator_id'),
                "score": ev_val,
                "score_label": _calibration_label_score(ev_val),
                "comment": (ev_comments[idx] if idx < len(ev_comments) else None) or None,
                "is_match": bool(is_match)
            })

        percent = None
        if eval_count > 0:
            participants_count = 1 + eval_count
            dominant_count = max(score_counts.values()) if score_counts else 0
            percent = round((dominant_count * 100.0) / float(participants_count), 1)
            if is_critical and dominant_count < participants_count:
                critical_mismatch = True
                percent = 0.0

        rows.append({
            "criterion_index": idx,
            "criterion_name": criterion.get('name') or f"Критерий {idx + 1}",
            "is_critical": is_critical,
            "percent": percent,
            "etalon": {
                "score": etalon_val,
                "score_label": _calibration_label_score(etalon_val),
                "comment": (etalon_comments[idx] if idx < len(etalon_comments) else None) or None
            },
            "admin": {
                "score": admin_val,
                "score_label": _calibration_label_score(admin_val),
                "comment": (admin_comments[idx] if idx < len(admin_comments) else None) or None
            },
            "benchmark": {
                "score": etalon_val,
                "score_label": _calibration_label_score(etalon_val),
                "comment": (etalon_comments[idx] if idx < len(etalon_comments) else None) or None
            },
            "by_evaluator": by_evaluator
        })

    overall_percent = None
    if rows and eval_count > 0:
        perc_values = [r.get('percent') for r in rows if r.get('percent') is not None]
        if perc_values:
            overall_percent = round(sum(perc_values) / float(len(perc_values)), 1)
    if critical_mismatch and overall_percent is not None:
        overall_percent = 0.0

    return {
        "criteria_rows": rows,
        "evaluated_count": eval_count,
        "critical_mismatch": critical_mismatch,
        "overall_percent": overall_percent
    }


def build_call_calibration_blueprint(
    *,
    ROLE_HIERARCHY,
    _authorize_operator_scope,
    _get_role_level,
    _has_min_role,
    _headed_department_id,
    _is_admin_role,
    _is_global_admin_requester,
    _is_supervisor_role,
    _normalize_user_role,
    _signed_audio_response_type,
    get_gcs_client,
    require_api_key,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""
    bp = Blueprint('call_calibration', __name__, url_prefix='/api')

    def _calibration_role(requester_id, requester):
        """Роль человека в калибровке звонков: 'admin', 'sv' или None (нет доступа).

    Калибровка открыта руководителям и супервайзерам (решение владельца
    27.10.2026). 'admin' — ведущий комнаты: админ или глава отдела по
    назначению с любой базовой ролью; создаёт комнаты, добавляет звонки, правит
    эталон и видит результаты без своей оценки. 'sv' — участник: входит в
    комнату, оценивает вслепую и видит результаты после своей оценки. Глава с
    базовой ролью sv остаётся участником: журнал ведёт его как супервайзера.
    Звонок, эталон и общий комментарий каждый добавляет и правит только по
    своим операторам (_authorize_operator_scope); удаляет комнаты и звонки
    только глобальный админ.
    Зеркало — canUseCalibration в src/call_evaluation/main.jsx.
    """
        role = _normalize_user_role(requester[3] if requester else None)
        if _is_supervisor_role(role):
            return 'sv'
        if _is_admin_role(role) or _headed_department_id(requester_id) is not None:
            return 'admin'
        return None

    def _build_signed_audio_url(audio_path):
        if not audio_path:
            return None
        try:
            path_parts = str(audio_path).split('/', 1)
            if len(path_parts) != 2:
                return None
            bucket_name, blob_path = path_parts
            bucket = get_gcs_client().bucket(bucket_name)
            blob = bucket.blob(blob_path)
            if not blob.exists():
                return None
            return blob.generate_signed_url(
                version="v4",
                expiration=timedelta(minutes=15),
                method="GET",
                response_type=_signed_audio_response_type(blob.name)
            )
        except Exception as e:
            logging.error("Error building signed audio URL: %s", e)
            return None

    @bp.route('/call_calibration/rooms', methods=['GET'])
    @require_api_key
    def list_call_calibration_rooms():
        try:
            requester_id = getattr(g, 'user_id', None)
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "Requester not found"}), 404
            calibration_role = _calibration_role(requester_id, requester)
            if calibration_role is None:
                return jsonify({"error": CALIBRATION_FORBIDDEN_ERROR}), 403

            month = (request.args.get('month') or '').strip()
            if month and not re.match(r'^\d{4}-\d{2}$', month):
                return jsonify({"error": "Invalid month format. Use YYYY-MM"}), 400

            with db._get_cursor() as cursor:
                params = [requester_id, requester_id]
                query = """
                SELECT
                    r.id,
                    r.month,
                    r.room_title,
                    r.created_at,
                    adm.id AS admin_id,
                    adm.name AS admin_name,
                    m.id AS member_id,
                    COALESCE(stats.calls_count, 0) AS calls_count,
                    COALESCE(stats.evaluators_count, 0) AS evaluators_count,
                    COALESCE(stats.evaluations_count, 0) AS evaluations_count,
                    COALESCE(my_stats.my_evaluated_calls, 0) AS my_evaluated_calls
                FROM calibration_rooms r
                JOIN users adm ON adm.id = r.created_by_admin_id
                LEFT JOIN calibration_room_members m
                    ON m.room_id = r.id
                   AND m.supervisor_id = %s
                LEFT JOIN (
                    SELECT
                        c.room_id,
                        COUNT(*) AS calls_count,
                        COUNT(e.id) AS evaluations_count,
                        COUNT(DISTINCT e.evaluator_id) AS evaluators_count
                    FROM calibration_room_calls c
                    LEFT JOIN calibration_room_call_evaluations e ON e.room_call_id = c.id
                    GROUP BY c.room_id
                ) stats ON stats.room_id = r.id
                LEFT JOIN (
                    SELECT
                        c.room_id,
                        COUNT(DISTINCT c.id) AS my_evaluated_calls
                    FROM calibration_room_calls c
                    JOIN calibration_room_call_evaluations e
                      ON e.room_call_id = c.id
                     AND e.evaluator_id = %s
                    GROUP BY c.room_id
                ) my_stats ON my_stats.room_id = r.id
                WHERE 1=1
            """
                if month:
                    query += " AND r.month = %s"
                    params.append(month)
                query += " ORDER BY r.created_at DESC"
                cursor.execute(query, params)
                rows = cursor.fetchall()

            rooms = []
            for row in rows:
                calls_count = int(row[7] or 0)
                my_evaluated_calls = int(row[10] or 0)
                is_creator = bool(row[4] and int(row[4]) == int(requester_id))
                rooms.append({
                    "id": row[0],
                    "month": row[1],
                    "room_title": row[2] or f"Комната #{row[0]}",
                    "created_at": row[3].strftime('%Y-%m-%d %H:%M:%S') if row[3] and hasattr(row[3], "strftime") else None,
                    "benchmark_admin": {"id": row[4], "name": row[5]},
                    "joined": True if calibration_role == 'admin' else bool(row[6] or is_creator),
                    "calls_count": calls_count,
                    "evaluated_count": int(row[8] or 0),
                    "evaluation_rows_count": int(row[9] or 0),
                    "my_evaluated_calls": my_evaluated_calls,
                    "my_evaluated": bool(calls_count > 0 and my_evaluated_calls >= calls_count),
                })

            return jsonify({"status": "success", "rooms": rooms}), 200
        except Exception as e:
            logging.exception("Error listing calibration rooms")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/call_calibration/rooms', methods=['POST'])
    @require_api_key
    def create_call_calibration_room():
        try:
            requester_id = getattr(g, 'user_id', None)
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "Requester not found"}), 404
            if _calibration_role(requester_id, requester) is None:
                return jsonify({"error": CALIBRATION_FORBIDDEN_ERROR}), 403

            data = request.form if request.form else (request.get_json(silent=True) or {})
            month = str(data.get('month') or '').strip()
            if not re.match(r'^\d{4}-\d{2}$', month):
                return jsonify({"error": "Invalid month format. Use YYYY-MM"}), 400

            room_title = str(data.get('room_title') or data.get('title') or '').strip() or None
            if room_title and len(room_title) > 255:
                return jsonify({"error": "room_title is too long (max 255)"}), 400

            with db._get_cursor() as cursor:
                cursor.execute("""
                INSERT INTO calibration_rooms (
                    created_by_admin_id,
                    month,
                    room_title,
                    created_at,
                    updated_at
                )
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id
            """, (
                    requester_id,
                    month,
                    room_title,
                ))
                room_id = cursor.fetchone()[0]

            return jsonify({"status": "success", "room_id": room_id}), 201
        except Exception as e:
            logging.exception("Error creating calibration room")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/call_calibration/rooms/<int:room_id>', methods=['DELETE', 'OPTIONS'])
    @require_api_key
    def delete_calibration_room(room_id):
        if request.method == 'OPTIONS':
            return jsonify({}), 200
        try:
            requester_id = getattr(g, 'user_id', None)
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "Requester not found"}), 404
            # Удаляет только глобальный админ — как и кнопка в журнале. Глава
            # отдела в калибровке ведущий, но общую комнату другого отдела не снесёт.
            if not _is_global_admin_requester(requester[3], requester_id):
                return jsonify({"error": "Only admin can delete calibration rooms"}), 403

            with db._get_cursor() as cursor:
                cursor.execute("SELECT id FROM calibration_rooms WHERE id = %s", (room_id,))
                if not cursor.fetchone():
                    return jsonify({"error": "Комната не найдена"}), 404
                cursor.execute("DELETE FROM calibration_rooms WHERE id = %s", (room_id,))

            return jsonify({"status": "success"}), 200
        except Exception as e:
            logging.exception("Error deleting calibration room")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/call_calibration/rooms/<int:room_id>', methods=['PATCH', 'OPTIONS'])
    @require_api_key
    def update_calibration_room(room_id):
        if request.method == 'OPTIONS':
            return jsonify({}), 200
        try:
            requester_id = getattr(g, 'user_id', None)
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "Requester not found"}), 404
            calibration_role = _calibration_role(requester_id, requester)
            if calibration_role is None:
                return jsonify({"error": CALIBRATION_FORBIDDEN_ERROR}), 403

            data = request.form if request.form else (request.get_json(silent=True) or {})
            room_title = str(data.get('room_title') or data.get('title') or '').strip()
            if len(room_title) > 255:
                return jsonify({"error": "room_title is too long (max 255)"}), 400

            with db._get_cursor() as cursor:
                cursor.execute("""
                SELECT id, created_by_admin_id
                FROM calibration_rooms
                WHERE id = %s
            """, (room_id,))
                room_row = cursor.fetchone()
                if not room_row:
                    return jsonify({"error": "Комната не найдена"}), 404

                if calibration_role == 'sv':
                    room_creator_id = int(room_row[1]) if room_row[1] is not None else None
                    if int(requester_id) != int(room_creator_id or 0):
                        cursor.execute("""
                        SELECT id
                        FROM calibration_room_members
                        WHERE room_id = %s AND supervisor_id = %s
                    """, (room_id, requester_id))
                        if not cursor.fetchone():
                            return jsonify({"error": "Join the room first"}), 403

                cursor.execute("""
                UPDATE calibration_rooms
                SET
                    room_title = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING id, room_title
            """, (room_title or None, room_id))
                updated = cursor.fetchone()
                if not updated:
                    return jsonify({"error": "Комната не найдена"}), 404

            return jsonify({
                "status": "success",
                "room": {
                    "id": updated[0],
                    "room_title": updated[1] or f"Комната #{updated[0]}"
                }
            }), 200
        except Exception as e:
            logging.exception("Error updating calibration room")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/call_calibration/rooms/<int:room_id>/calls', methods=['POST'])
    @require_api_key
    def add_call_to_calibration_room(room_id):
        try:
            requester_id = getattr(g, 'user_id', None)
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "Requester not found"}), 404
            calibration_role = _calibration_role(requester_id, requester)
            if calibration_role is None:
                return jsonify({"error": CALIBRATION_FORBIDDEN_ERROR}), 403

            data = request.form if request.form else (request.get_json(silent=True) or {})
            required_fields = ['operator_id', 'phone_number', 'appeal_date', 'direction', 'scores', 'criterion_comments']
            missing = [f for f in required_fields if f not in data]
            if missing:
                return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

            try:
                operator_id = int(data.get('operator_id'))
                direction_id = int(data.get('direction'))
            except Exception:
                return jsonify({"error": "operator_id and direction must be integers"}), 400

            month = str(data.get('month') or '').strip()
            if month and not re.match(r'^\d{4}-\d{2}$', month):
                return jsonify({"error": "Invalid month format. Use YYYY-MM"}), 400

            phone_number = str(data.get('phone_number') or '').strip()
            if len(phone_number) < 5:
                return jsonify({"error": "Invalid phone number"}), 400

            appeal_date_raw = str(data.get('appeal_date') or '').strip()
            try:
                appeal_date = datetime.fromisoformat(appeal_date_raw.replace('Z', '+00:00'))
            except Exception:
                return jsonify({"error": "Invalid appeal_date format. Use ISO datetime"}), 400

            raw_scores = data.get('scores', '[]')
            raw_comments = data.get('criterion_comments', '[]')
            try:
                scores = json.loads(raw_scores) if isinstance(raw_scores, str) else raw_scores
                criterion_comments = json.loads(raw_comments) if isinstance(raw_comments, str) else raw_comments
            except Exception:
                return jsonify({"error": "scores and criterion_comments must be valid JSON arrays"}), 400

            if not isinstance(scores, list) or not isinstance(criterion_comments, list):
                return jsonify({"error": "scores and criterion_comments must be arrays"}), 400

            operator = db.get_user(id=operator_id)
            if not operator or operator[3] != 'operator':
                return jsonify({"error": "Operator not found"}), 404
            if not _authorize_operator_scope(requester, requester_id, operator_id):
                return jsonify({"error": "Forbidden: you cannot add calls for this operator"}), 403

            with db._get_cursor() as cursor:
                cursor.execute("""
                SELECT id, month, created_by_admin_id
                FROM calibration_rooms
                WHERE id = %s
            """, (room_id,))
                room_row = cursor.fetchone()
                if not room_row:
                    return jsonify({"error": "Calibration room not found"}), 404

                room_creator_id = room_row[2]
                if calibration_role == 'sv' and int(room_creator_id or 0) != int(requester_id):
                    cursor.execute("""
                    SELECT id
                    FROM calibration_room_members
                    WHERE room_id = %s AND supervisor_id = %s
                """, (room_id, requester_id))
                    if not cursor.fetchone():
                        return jsonify({"error": "Join the room first"}), 403

                room_month = str(room_row[1] or '').strip()
                if room_month and month and room_month != month:
                    return jsonify({"error": "Call month must match room month"}), 400
                month = room_month or month
                if not month:
                    return jsonify({"error": "Calibration room month is missing"}), 400

                cursor.execute("""
                SELECT id, criteria, has_file_upload
                FROM directions
                WHERE id = %s AND is_active = TRUE
            """, (direction_id,))
                direction = cursor.fetchone()
                if not direction:
                    return jsonify({"error": "Direction not found"}), 404

                criteria = direction[1] if isinstance(direction[1], list) else []
                has_file_upload = bool(direction[2])

                normalized_scores = []
                normalized_comments = []
                for idx in range(len(criteria)):
                    src_score = scores[idx] if idx < len(scores) else 'Correct'
                    src_comment = criterion_comments[idx] if idx < len(criterion_comments) else ''
                    normalized_scores.append(_normalize_calibration_score_value(src_score))
                    normalized_comments.append(str(src_comment or ''))

                total_score, _ = _compute_total_score_from_criteria(criteria, normalized_scores)
                comment = str(data.get('comment') or '').strip()
                if not comment:
                    comment = '; '.join(
                        f"{(criteria[i] or {}).get('name', f'Критерий {i + 1}')}: {text.strip()}"
                        for i, text in enumerate(normalized_comments)
                        if str(text or '').strip()
                    )

                audio_path = None
                if has_file_upload:
                    file = request.files.get('audio_file')
                    if not file or not file.filename:
                        return jsonify({"error": "Audio file is required for this direction"}), 400
                    audio_data = file.read()
                    if not audio_data:
                        return jsonify({"error": "Audio file is empty"}), 400
                    bucket_name = os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET')
                    if not bucket_name:
                        return jsonify({"error": "GOOGLE_CLOUD_STORAGE_BUCKET is not configured"}), 500
                    upload_folder = os.getenv('UPLOAD_FOLDER', 'Uploads/')
                    filename = secure_filename(f"{uuid.uuid4()}.mp3")
                    blob_path = f"{upload_folder}{filename}"
                    gcs_client = get_gcs_client()
                    bucket = gcs_client.bucket(bucket_name)
                    blob = bucket.blob(blob_path)
                    blob.upload_from_string(audio_data, content_type='audio/mpeg')
                    audio_path = f"{bucket_name}/{blob_path}"

                cursor.execute("""
                INSERT INTO calibration_room_calls (
                    room_id,
                    created_by_admin_id,
                    operator_id,
                    month,
                    phone_number,
                    appeal_date,
                    direction_id,
                    score,
                    comment,
                    audio_path,
                    scores,
                    criterion_comments,
                    etalon_scores,
                    etalon_criterion_comments,
                    created_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                RETURNING id
            """, (
                    room_id,
                    requester_id,
                    operator_id,
                    month,
                    phone_number,
                    appeal_date,
                    direction_id,
                    total_score,
                    comment,
                    audio_path,
                    json.dumps(normalized_scores, ensure_ascii=False),
                    json.dumps(normalized_comments, ensure_ascii=False),
                    json.dumps(normalized_scores, ensure_ascii=False),
                    json.dumps(normalized_comments, ensure_ascii=False),
                ))
                room_call_id = cursor.fetchone()[0]

            return jsonify({"status": "success", "room_call_id": room_call_id, "score": total_score}), 201
        except Exception as e:
            logging.exception("Error adding call to calibration room")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/call_calibration/rooms/<int:room_id>/calls/<int:call_id>', methods=['DELETE', 'OPTIONS'])
    @require_api_key
    def delete_call_from_calibration_room(room_id, call_id):
        if request.method == 'OPTIONS':
            return jsonify({}), 200
        try:
            requester_id = getattr(g, 'user_id', None)
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "Requester not found"}), 404
            if not _is_global_admin_requester(requester[3], requester_id):
                return jsonify({"error": "Only admin can delete calls from calibration room"}), 403

            with db._get_cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM calibration_room_calls WHERE id = %s AND room_id = %s",
                    (call_id, room_id)
                )
                if not cursor.fetchone():
                    return jsonify({"error": "Звонок не найден"}), 404
                cursor.execute(
                    "DELETE FROM calibration_room_calls WHERE id = %s AND room_id = %s",
                    (call_id, room_id)
                )

            return jsonify({"status": "success"}), 200
        except Exception as e:
            logging.exception("Error deleting call from calibration room")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/call_calibration/rooms/<int:room_id>/join', methods=['POST'])
    @require_api_key
    def join_call_calibration_room(room_id):
        try:
            requester_id = getattr(g, 'user_id', None)
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "Requester not found"}), 404
            if not _is_supervisor_role(requester[3]):
                return jsonify({"error": "Only supervisors can join calibration rooms"}), 403

            with db._get_cursor() as cursor:
                cursor.execute("SELECT id FROM calibration_rooms WHERE id = %s", (room_id,))
                if not cursor.fetchone():
                    return jsonify({"error": "Calibration room not found"}), 404

                cursor.execute("""
                INSERT INTO calibration_room_members (room_id, supervisor_id)
                VALUES (%s, %s)
                ON CONFLICT (room_id, supervisor_id) DO NOTHING
            """, (room_id, requester_id))

            return jsonify({"status": "success"}), 200
        except Exception as e:
            logging.exception("Error joining calibration room")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/call_calibration/rooms/<int:room_id>', methods=['GET'])
    @require_api_key
    def get_call_calibration_room(room_id):
        try:
            requester_id = getattr(g, 'user_id', None)
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "Requester not found"}), 404
            calibration_role = _calibration_role(requester_id, requester)
            if calibration_role is None:
                return jsonify({"error": CALIBRATION_FORBIDDEN_ERROR}), 403

            requested_call_id = None
            requested_call_id_raw = request.args.get('call_id')
            if requested_call_id_raw not in (None, ''):
                try:
                    requested_call_id = int(requested_call_id_raw)
                except Exception:
                    return jsonify({"error": "Invalid call_id"}), 400

            with db._get_cursor() as cursor:
                cursor.execute("""
                SELECT
                    r.id,
                    r.month,
                    r.room_title,
                    r.created_at,
                    adm.id AS admin_id,
                    adm.name AS admin_name
                FROM calibration_rooms r
                JOIN users adm ON adm.id = r.created_by_admin_id
                WHERE r.id = %s
            """, (room_id,))
                room_row = cursor.fetchone()
                if not room_row:
                    return jsonify({"error": "Calibration room not found"}), 404

                if calibration_role == 'sv':
                    is_creator = int(room_row[4] or 0) == int(requester_id)
                    cursor.execute("""
                    SELECT id FROM calibration_room_members
                    WHERE room_id = %s AND supervisor_id = %s
                """, (room_id, requester_id))
                    joined = bool(cursor.fetchone()) or bool(is_creator)
                    if not joined:
                        return jsonify({"error": "Join the room first"}), 403
                else:
                    joined = True

                cursor.execute("""
                SELECT
                    c.id,
                    c.operator_id,
                    op.name AS operator_name,
                    c.phone_number,
                    c.appeal_date,
                    c.score,
                    c.created_at,
                    c.direction_id,
                    d.name AS direction_name,
                    (
                        SELECT COUNT(*)
                        FROM calibration_room_call_evaluations e
                        WHERE e.room_call_id = c.id
                    ) AS evaluated_count,
                    EXISTS(
                        SELECT 1
                        FROM calibration_room_call_evaluations me
                        WHERE me.room_call_id = c.id
                          AND me.evaluator_id = %s
                    ) AS my_evaluated
                FROM calibration_room_calls c
                JOIN users op ON op.id = c.operator_id
                LEFT JOIN directions d ON d.id = c.direction_id
                WHERE c.room_id = %s
                ORDER BY c.created_at DESC, c.id DESC
            """, (requester_id, room_id))
                call_rows = cursor.fetchall()

                selected_call_id = None
                available_call_ids = {int(c[0]) for c in call_rows}
                if requested_call_id is not None:
                    if requested_call_id not in available_call_ids:
                        return jsonify({"error": "Calibration call not found in room"}), 404
                    selected_call_id = requested_call_id
                elif call_rows:
                    selected_call_id = int(call_rows[0][0])

                selected_call_row = None
                if selected_call_id is not None:
                    cursor.execute("""
                    SELECT
                        c.id,
                        c.room_id,
                        c.month,
                        c.phone_number,
                        c.appeal_date,
                        c.score,
                        c.comment,
                        c.audio_path,
                        c.scores,
                        c.criterion_comments,
                        c.created_at,
                        c.updated_at,
                        op.id AS operator_id,
                        op.name AS operator_name,
                        d.id AS direction_id,
                        d.name AS direction_name,
                        d.criteria AS direction_criteria,
                        d.has_file_upload AS direction_has_file_upload,
                        c.etalon_scores,
                        c.etalon_criterion_comments,
                        c.etalon_updated_at,
                        eu.id AS etalon_updated_by,
                        eu.name AS etalon_updated_by_name,
                        c.general_comment,
                        c.general_comment_updated_at,
                        gu.id AS general_comment_updated_by_id,
                        gu.name AS general_comment_updated_by_name
                    FROM calibration_room_calls c
                    JOIN users op ON op.id = c.operator_id
                    LEFT JOIN directions d ON d.id = c.direction_id
                    LEFT JOIN users eu ON eu.id = c.etalon_updated_by
                    LEFT JOIN users gu ON gu.id = c.general_comment_updated_by
                    WHERE c.id = %s AND c.room_id = %s
                """, (selected_call_id, room_id))
                    selected_call_row = cursor.fetchone()
                    if not selected_call_row:
                        return jsonify({"error": "Calibration call not found"}), 404

                my_eval_row = None
                if selected_call_id is not None:
                    cursor.execute("""
                    SELECT id, score, comment, scores, criterion_comments, created_at, updated_at
                    FROM calibration_room_call_evaluations
                    WHERE room_call_id = %s AND evaluator_id = %s
                """, (selected_call_id, requester_id))
                    my_eval_row = cursor.fetchone()

                can_view_results = calibration_role == 'admin' or bool(my_eval_row)
                # Эталон и общий комментарий правят только по своим операторам (та же
                # граница, что в ручках правки): фронт по этому признаку не рисует
                # редактор, который отдал бы 403 — общий список комнат видят все.
                can_edit_call = bool(
                    selected_call_row
                    and _authorize_operator_scope(requester, requester_id, selected_call_row[12])
                )
                evaluation_rows = []
                evaluator_columns = []
                if selected_call_id is not None and can_view_results:
                    cursor.execute("""
                    SELECT
                        e.id,
                        e.evaluator_id,
                        u.name AS evaluator_name,
                        e.score,
                        e.comment,
                        e.scores,
                        e.criterion_comments,
                        e.created_at,
                        e.updated_at
                    FROM calibration_room_call_evaluations e
                    JOIN users u ON u.id = e.evaluator_id
                    WHERE e.room_call_id = %s
                    ORDER BY u.name
                """, (selected_call_id,))
                    fetched = cursor.fetchall()
                    for e in fetched:
                        payload = {
                            "id": e[0],
                            "evaluator_id": e[1],
                            "evaluator_name": e[2],
                            "score": float(e[3]) if e[3] is not None else 0.0,
                            "comment": e[4],
                            "scores": e[5] if isinstance(e[5], list) else [],
                            "criterion_comments": e[6] if isinstance(e[6], list) else [],
                            "created_at": e[7].strftime('%Y-%m-%d %H:%M:%S') if e[7] and hasattr(e[7], "strftime") else None,
                            "updated_at": e[8].strftime('%Y-%m-%d %H:%M:%S') if e[8] and hasattr(e[8], "strftime") else None,
                        }
                        evaluation_rows.append(payload)
                        evaluator_columns.append({"id": payload["evaluator_id"], "name": payload["evaluator_name"]})

            room_payload = {
                "id": room_row[0],
                "month": room_row[1],
                "room_title": room_row[2] or f"Комната #{room_row[0]}",
                "created_at": room_row[3].strftime('%Y-%m-%d %H:%M:%S') if room_row[3] and hasattr(room_row[3], "strftime") else None,
                "benchmark_admin": {"id": room_row[4], "name": room_row[5]}
            }

            calls_payload = []
            for call_row in call_rows:
                calls_payload.append({
                    "id": call_row[0],
                    "operator": {"id": call_row[1], "name": call_row[2]},
                    "phone_number": call_row[3],
                    "appeal_date": call_row[4].strftime('%Y-%m-%d %H:%M:%S') if call_row[4] and hasattr(call_row[4], "strftime") else None,
                    "score": float(call_row[5]) if call_row[5] is not None else 0.0,
                    "created_at": call_row[6].strftime('%Y-%m-%d %H:%M:%S') if call_row[6] and hasattr(call_row[6], "strftime") else None,
                    "direction": {"id": call_row[7], "name": call_row[8]},
                    "evaluated_count": int(call_row[9] or 0),
                    "my_evaluated": bool(call_row[10]),
                })

            selected_call_payload = None
            results = None
            if selected_call_row:
                direction_criteria = selected_call_row[16] if isinstance(selected_call_row[16], list) else []
                admin_scores = selected_call_row[8] if isinstance(selected_call_row[8], list) else []
                admin_comments = selected_call_row[9] if isinstance(selected_call_row[9], list) else []
                etalon_scores_raw = selected_call_row[18] if isinstance(selected_call_row[18], list) else []
                etalon_comments_raw = selected_call_row[19] if isinstance(selected_call_row[19], list) else []
                etalon_scores = etalon_scores_raw if etalon_scores_raw else admin_scores
                etalon_comments = etalon_comments_raw if etalon_comments_raw else admin_comments
                etalon_total, _ = _compute_total_score_from_criteria(direction_criteria, etalon_scores)
                selected_call_payload = {
                    "id": selected_call_row[0],
                    "room_id": selected_call_row[1],
                    "month": selected_call_row[2],
                    "phone_number": selected_call_row[3],
                    "appeal_date": selected_call_row[4].strftime('%Y-%m-%d %H:%M:%S') if selected_call_row[4] and hasattr(selected_call_row[4], "strftime") else None,
                    "score": float(selected_call_row[5]) if selected_call_row[5] is not None else 0.0,
                    "comment": selected_call_row[6],
                    "audio_url": _build_signed_audio_url(selected_call_row[7]),
                    "scores": admin_scores,
                    "criterion_comments": admin_comments,
                    "admin_scores": admin_scores,
                    "admin_criterion_comments": admin_comments,
                    "etalon_scores": etalon_scores,
                    "etalon_criterion_comments": etalon_comments,
                    "etalon_score": etalon_total,
                    "etalon_updated_at": selected_call_row[20].strftime('%Y-%m-%d %H:%M:%S') if selected_call_row[20] and hasattr(selected_call_row[20], "strftime") else None,
                    "etalon_updated_by": {
                        "id": selected_call_row[21],
                        "name": selected_call_row[22]
                    } if selected_call_row[21] else None,
                    "general_comment": selected_call_row[23] or '',
                    "general_comment_updated_at": selected_call_row[24].strftime('%Y-%m-%d %H:%M:%S') if selected_call_row[24] and hasattr(selected_call_row[24], "strftime") else None,
                    "general_comment_updated_by": {
                        "id": selected_call_row[25],
                        "name": selected_call_row[26]
                    } if selected_call_row[25] else None,
                    "created_at": selected_call_row[10].strftime('%Y-%m-%d %H:%M:%S') if selected_call_row[10] and hasattr(selected_call_row[10], "strftime") else None,
                    "updated_at": selected_call_row[11].strftime('%Y-%m-%d %H:%M:%S') if selected_call_row[11] and hasattr(selected_call_row[11], "strftime") else None,
                    "operator": {"id": selected_call_row[12], "name": selected_call_row[13]},
                    "direction": {
                        "id": selected_call_row[14],
                        "name": selected_call_row[15],
                        "criteria": direction_criteria,
                        "hasFileUpload": bool(selected_call_row[17]) if selected_call_row[17] is not None else True
                    }
                }
                if can_view_results:
                    results = _build_calibration_results(
                        direction_criteria,
                        etalon_scores,
                        etalon_comments,
                        evaluation_rows,
                        admin_scores=admin_scores,
                        admin_comments=admin_comments
                    )

            my_evaluation = None
            if my_eval_row:
                my_evaluation = {
                    "id": my_eval_row[0],
                    "score": float(my_eval_row[1]) if my_eval_row[1] is not None else 0.0,
                    "comment": my_eval_row[2],
                    "scores": my_eval_row[3] if isinstance(my_eval_row[3], list) else [],
                    "criterion_comments": my_eval_row[4] if isinstance(my_eval_row[4], list) else [],
                    "created_at": my_eval_row[5].strftime('%Y-%m-%d %H:%M:%S') if my_eval_row[5] and hasattr(my_eval_row[5], "strftime") else None,
                    "updated_at": my_eval_row[6].strftime('%Y-%m-%d %H:%M:%S') if my_eval_row[6] and hasattr(my_eval_row[6], "strftime") else None,
                }

            return jsonify({
                "status": "success",
                "room": room_payload,
                "calls": calls_payload,
                "selected_call_id": selected_call_id,
                "selected_call": selected_call_payload,
                "joined": joined,
                "can_evaluate": bool(calibration_role == 'sv' and selected_call_id is not None and not my_eval_row),
                "can_view_results": bool(can_view_results and selected_call_id is not None),
                "can_edit_call": can_edit_call,
                "my_evaluation": my_evaluation,
                "results": results,
                "evaluators": evaluator_columns
            }), 200
        except Exception as e:
            logging.exception("Error getting calibration room")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/call_calibration/rooms/<int:room_id>/export_excel', methods=['GET'])
    @require_api_key
    def export_call_calibration_room_excel(room_id):
        try:
            requester_id = getattr(g, 'user_id', None)
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "Requester not found"}), 404
            calibration_role = _calibration_role(requester_id, requester)
            if calibration_role is None:
                return jsonify({"error": CALIBRATION_FORBIDDEN_ERROR}), 403

            def _fmt_dt(value):
                if value is None:
                    return '—'
                if hasattr(value, 'strftime'):
                    return value.strftime('%Y-%m-%d %H:%M:%S')
                return str(value)

            with db._get_cursor() as cursor:
                cursor.execute("""
                SELECT
                    r.id,
                    r.month,
                    r.room_title,
                    r.created_at,
                    adm.id AS creator_id,
                    adm.name AS creator_name
                FROM calibration_rooms r
                JOIN users adm ON adm.id = r.created_by_admin_id
                WHERE r.id = %s
            """, (room_id,))
                room_row = cursor.fetchone()
                if not room_row:
                    return jsonify({"error": "Calibration room not found"}), 404

                if calibration_role == 'sv':
                    is_creator = int(room_row[4] or 0) == int(requester_id)
                    cursor.execute("""
                    SELECT id
                    FROM calibration_room_members
                    WHERE room_id = %s AND supervisor_id = %s
                """, (room_id, requester_id))
                    joined = bool(cursor.fetchone()) or bool(is_creator)
                    if not joined:
                        return jsonify({"error": "Join the room first"}), 403

                cursor.execute("""
                SELECT
                    c.id,
                    c.operator_id,
                    op.name AS operator_name,
                    c.phone_number,
                    c.appeal_date,
                    c.score,
                    d.name AS direction_name,
                    c.scores,
                    c.criterion_comments,
                    c.etalon_scores,
                    c.etalon_criterion_comments,
                    c.created_at,
                    c.updated_at,
                    d.criteria
                FROM calibration_room_calls c
                JOIN users op ON op.id = c.operator_id
                LEFT JOIN directions d ON d.id = c.direction_id
                WHERE c.room_id = %s
                ORDER BY c.created_at DESC, c.id DESC
            """, (room_id,))
                call_rows = cursor.fetchall()

                call_ids = [int(row[0]) for row in call_rows]
                evaluations_by_call = {}
                if call_ids:
                    cursor.execute("""
                    SELECT
                        e.room_call_id,
                        e.evaluator_id,
                        u.name AS evaluator_name,
                        e.score,
                        e.comment,
                        e.scores,
                        e.criterion_comments
                    FROM calibration_room_call_evaluations e
                    JOIN users u ON u.id = e.evaluator_id
                    WHERE e.room_call_id = ANY(%s)
                    ORDER BY e.room_call_id, u.name
                """, (call_ids,))
                    for ev in cursor.fetchall():
                        room_call_id = int(ev[0])
                        evaluations_by_call.setdefault(room_call_id, []).append({
                            "id": None,
                            "evaluator_id": ev[1],
                            "evaluator_name": ev[2],
                            "score": float(ev[3]) if ev[3] is not None else 0.0,
                            "comment": ev[4],
                            "scores": ev[5] if isinstance(ev[5], list) else [],
                            "criterion_comments": ev[6] if isinstance(ev[6], list) else [],
                        })

            wb = Workbook()
            ws_summary = wb.active
            ws_summary.title = 'Сводка'
            ws_details = wb.create_sheet('Критерии')

            header_fill = PatternFill(start_color='1F4E78', end_color='1F4E78', fill_type='solid')
            header_font = Font(color='FFFFFF', bold=True)
            wrap_alignment = Alignment(vertical='top', wrap_text=True)
            thin_border = Border(
                left=Side(style='thin', color='DDDDDD'),
                right=Side(style='thin', color='DDDDDD'),
                top=Side(style='thin', color='DDDDDD'),
                bottom=Side(style='thin', color='DDDDDD'),
            )

            ws_summary.append(['Параметр', 'Значение'])
            ws_summary.append(['Комната ID', room_row[0]])
            ws_summary.append(['Название комнаты', room_row[2] or f'Комната #{room_row[0]}'])
            ws_summary.append(['Месяц', room_row[1] or '—'])
            ws_summary.append(['Создал', room_row[5] or '—'])
            ws_summary.append(['Создано', _fmt_dt(room_row[3])])
            ws_summary.append(['Количество звонков', len(call_rows)])
            ws_summary.append([])

            summary_headers = [
                'Звонок ID',
                'Оператор',
                'Телефон',
                'Дата обращения',
                'Направление',
                f'Оценка автора ({room_row[5] or "—"})',
                'Текущий эталон',
                'Оценивших',
                'Общий % калибровки',
                'Критич. расхождение'
            ]
            ws_summary.append(summary_headers)
            summary_header_row = ws_summary.max_row

            details_headers = [
                'Звонок ID',
                'Оператор',
                'Критерий',
                'Критичный',
                '% калибровки',
                'Эталон',
                'Комментарий эталона',
                f'Оценка автора ({room_row[5] or "—"})',
                'Комментарий автора',
                'Оценки супервайзеров'
            ]
            ws_details.append(details_headers)
            details_header_row = ws_details.max_row

            for call_row in call_rows:
                call_id = int(call_row[0])
                operator_name = call_row[2] or '—'
                phone_number = call_row[3] or '—'
                appeal_date = _fmt_dt(call_row[4])
                admin_total = float(call_row[5]) if call_row[5] is not None else 0.0
                direction_name = call_row[6] or '—'
                admin_scores = call_row[7] if isinstance(call_row[7], list) else []
                admin_comments = call_row[8] if isinstance(call_row[8], list) else []
                etalon_scores_raw = call_row[9] if isinstance(call_row[9], list) else []
                etalon_comments_raw = call_row[10] if isinstance(call_row[10], list) else []
                direction_criteria = call_row[13] if isinstance(call_row[13], list) else []

                etalon_scores = etalon_scores_raw if etalon_scores_raw else admin_scores
                etalon_comments = etalon_comments_raw if etalon_comments_raw else admin_comments
                etalon_total, _ = _compute_total_score_from_criteria(direction_criteria, etalon_scores)

                evaluations = evaluations_by_call.get(call_id, [])
                eval_name_by_id = {int(ev.get('evaluator_id')): ev.get('evaluator_name') or 'Супервайзер' for ev in evaluations}
                results = _build_calibration_results(
                    direction_criteria,
                    etalon_scores,
                    etalon_comments,
                    evaluations,
                    admin_scores=admin_scores,
                    admin_comments=admin_comments
                )
                overall_percent = results.get('overall_percent')
                overall_percent_text = '—' if overall_percent is None else f"{overall_percent:.1f}%"
                critical_mismatch = bool(results.get('critical_mismatch'))

                ws_summary.append([
                    call_id,
                    operator_name,
                    phone_number,
                    appeal_date,
                    direction_name,
                    round(admin_total, 2),
                    round(float(etalon_total or 0.0), 2),
                    int(results.get('evaluated_count') or 0),
                    overall_percent_text,
                    'Да' if critical_mismatch else 'Нет'
                ])

                criteria_rows = results.get('criteria_rows') or []
                if not criteria_rows:
                    ws_details.append([
                        call_id,
                        operator_name,
                        'Нет критериев',
                        '',
                        '',
                        '',
                        '',
                        '',
                        '',
                        ''
                    ])
                else:
                    for criterion_row in criteria_rows:
                        sv_values = []
                        for cell in criterion_row.get('by_evaluator') or []:
                            evaluator_id = int(cell.get('evaluator_id') or 0)
                            evaluator_name = eval_name_by_id.get(evaluator_id) or f"SV #{evaluator_id}"
                            item = f"{evaluator_name}: {cell.get('score_label') or _calibration_label_score(cell.get('score'))}"
                            if cell.get('is_match') is not None:
                                item += ' (совпало)' if bool(cell.get('is_match')) else ' (не совпало)'
                            if cell.get('comment'):
                                item += f" | {cell.get('comment')}"
                            sv_values.append(item)

                        percent = criterion_row.get('percent')
                        percent_text = '—' if percent is None else f"{float(percent):.1f}%"
                        ws_details.append([
                            call_id,
                            operator_name,
                            criterion_row.get('criterion_name') or '',
                            'Да' if criterion_row.get('is_critical') else 'Нет',
                            percent_text,
                            criterion_row.get('etalon', {}).get('score_label') or _calibration_label_score(criterion_row.get('etalon', {}).get('score')),
                            criterion_row.get('etalon', {}).get('comment') or '',
                            criterion_row.get('admin', {}).get('score_label') or _calibration_label_score(criterion_row.get('admin', {}).get('score')),
                            criterion_row.get('admin', {}).get('comment') or '',
                            '\n'.join(sv_values) if sv_values else '—'
                        ])

                ws_details.append([
                    call_id,
                    operator_name,
                    'ИТОГ ПО ЗВОНКУ',
                    '',
                    '',
                    '',
                    '',
                    '',
                    '',
                    f"Общий %: {overall_percent_text}; Критич. расхождение: {'Да' if critical_mismatch else 'Нет'}"
                ])
                ws_details.append([])

            for col in range(1, len(summary_headers) + 1):
                cell = ws_summary.cell(row=summary_header_row, column=col)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal='center', vertical='center')
                cell.border = thin_border

            for col in range(1, len(details_headers) + 1):
                cell = ws_details.cell(row=details_header_row, column=col)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                cell.border = thin_border

            for row in ws_summary.iter_rows(min_row=summary_header_row + 1, max_row=ws_summary.max_row, min_col=1, max_col=len(summary_headers)):
                for cell in row:
                    cell.border = thin_border
                    cell.alignment = wrap_alignment

            for row in ws_details.iter_rows(min_row=details_header_row + 1, max_row=ws_details.max_row, min_col=1, max_col=len(details_headers)):
                for cell in row:
                    cell.border = thin_border
                    cell.alignment = wrap_alignment

            summary_widths = [12, 22, 18, 20, 20, 19, 14, 11, 19, 18]
            for i, width in enumerate(summary_widths, start=1):
                ws_summary.column_dimensions[get_column_letter(i)].width = width

            details_widths = [12, 22, 36, 10, 15, 15, 28, 19, 28, 54]
            for i, width in enumerate(details_widths, start=1):
                ws_details.column_dimensions[get_column_letter(i)].width = width

            ws_summary.freeze_panes = 'A10'
            ws_details.freeze_panes = 'A2'

            output = BytesIO()
            wb.save(output)
            output.seek(0)

            safe_month = re.sub(r'[^0-9-]', '', str(room_row[1] or ''))
            filename = f"calibration_room_{room_id}_{safe_month or 'export'}.xlsx"
            return send_file(
                output,
                as_attachment=True,
                download_name=filename,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
        except Exception as e:
            logging.exception("Error exporting calibration room")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/call_calibration/rooms/<int:room_id>/calls/<int:call_id>/etalon', methods=['PATCH'])
    @require_api_key
    def update_call_calibration_etalon(room_id, call_id):
        try:
            requester_id = getattr(g, 'user_id', None)
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "Requester not found"}), 404
            calibration_role = _calibration_role(requester_id, requester)
            if calibration_role is None:
                return jsonify({"error": CALIBRATION_FORBIDDEN_ERROR}), 403

            data = request.get_json() or {}
            scores = data.get('scores')
            criterion_comments = data.get('criterion_comments')
            if not isinstance(scores, list) or not isinstance(criterion_comments, list):
                return jsonify({"error": "scores and criterion_comments must be arrays"}), 400

            with db._get_cursor() as cursor:
                cursor.execute("""
                SELECT d.criteria, c.operator_id, r.created_by_admin_id
                FROM calibration_room_calls c
                JOIN calibration_rooms r ON r.id = c.room_id
                LEFT JOIN directions d ON d.id = c.direction_id
                WHERE c.id = %s AND c.room_id = %s
            """, (call_id, room_id))
                call_row = cursor.fetchone()
                if not call_row:
                    return jsonify({"error": "Calibration call not found"}), 404
                criteria = call_row[0] if isinstance(call_row[0], list) else []
                call_operator_id = int(call_row[1]) if call_row[1] is not None else None
                room_creator_id = int(call_row[2]) if call_row[2] is not None else None

                # Граница оператора — для всех, как при добавлении звонка: глава
                # отдела правит эталон только по своим операторам, глобальному
                # админу _authorize_operator_scope и так открывает всех.
                if call_operator_id is None or not _authorize_operator_scope(requester, requester_id, call_operator_id):
                    return jsonify({"error": "Forbidden: you cannot update etalon for this operator"}), 403
                if calibration_role == 'sv':
                    if int(requester_id) != room_creator_id:
                        cursor.execute("""
                        SELECT id
                        FROM calibration_room_members
                        WHERE room_id = %s AND supervisor_id = %s
                    """, (room_id, requester_id))
                        if not cursor.fetchone():
                            return jsonify({"error": "Join the room first"}), 403

                normalized_scores = []
                normalized_comments = []
                for idx in range(len(criteria)):
                    src_score = scores[idx] if idx < len(scores) else 'Correct'
                    src_comment = criterion_comments[idx] if idx < len(criterion_comments) else ''
                    normalized_scores.append(_normalize_calibration_score_value(src_score))
                    normalized_comments.append(str(src_comment or ''))

                etalon_total, _ = _compute_total_score_from_criteria(criteria, normalized_scores)

                cursor.execute("""
                UPDATE calibration_room_calls
                SET
                    etalon_scores = %s::jsonb,
                    etalon_criterion_comments = %s::jsonb,
                    etalon_updated_by = %s,
                    etalon_updated_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND room_id = %s
                RETURNING id
            """, (
                    json.dumps(normalized_scores, ensure_ascii=False),
                    json.dumps(normalized_comments, ensure_ascii=False),
                    requester_id,
                    call_id,
                    room_id
                ))
                updated = cursor.fetchone()
                if not updated:
                    return jsonify({"error": "Calibration call not found"}), 404

            return jsonify({
                "status": "success",
                "call_id": call_id,
                "etalon_score": etalon_total
            }), 200
        except Exception as e:
            logging.exception("Error updating calibration etalon")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/call_calibration/rooms/<int:room_id>/calls/<int:call_id>/general_comment', methods=['PATCH'])
    @require_api_key
    def update_call_calibration_general_comment(room_id, call_id):
        try:
            requester_id = getattr(g, 'user_id', None)
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "Requester not found"}), 404
            calibration_role = _calibration_role(requester_id, requester)
            if calibration_role is None:
                return jsonify({"error": CALIBRATION_FORBIDDEN_ERROR}), 403

            data = request.get_json() or {}
            general_comment = str(data.get('general_comment') or '').strip()

            with db._get_cursor() as cursor:
                cursor.execute("""
                SELECT c.operator_id, r.created_by_admin_id
                FROM calibration_room_calls c
                JOIN calibration_rooms r ON r.id = c.room_id
                WHERE c.id = %s AND c.room_id = %s
            """, (call_id, room_id))
                call_row = cursor.fetchone()
                if not call_row:
                    return jsonify({"error": "Calibration call not found"}), 404

                call_operator_id = int(call_row[0]) if call_row[0] is not None else None
                room_creator_id = int(call_row[1]) if call_row[1] is not None else None
                # Граница оператора — для всех, как у эталона и добавления звонка.
                if call_operator_id is None or not _authorize_operator_scope(requester, requester_id, call_operator_id):
                    return jsonify({"error": "Forbidden: you cannot update comment for this operator"}), 403
                if calibration_role == 'sv':
                    if int(requester_id) != room_creator_id:
                        cursor.execute("""
                        SELECT id FROM calibration_room_members
                        WHERE room_id = %s AND supervisor_id = %s
                    """, (room_id, requester_id))
                        if not cursor.fetchone():
                            return jsonify({"error": "Join the room first"}), 403

                cursor.execute("""
                UPDATE calibration_room_calls
                SET
                    general_comment = %s,
                    general_comment_updated_by = %s,
                    general_comment_updated_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND room_id = %s
                RETURNING id
            """, (general_comment or None, requester_id, call_id, room_id))
                if not cursor.fetchone():
                    return jsonify({"error": "Calibration call not found"}), 404

            return jsonify({"status": "success", "call_id": call_id}), 200
        except Exception as e:
            logging.exception("Error updating calibration general comment")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/call_calibration/rooms/<int:room_id>/evaluate', methods=['POST'])
    @require_api_key
    def evaluate_call_calibration_room(room_id):
        try:
            requester_id = getattr(g, 'user_id', None)
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "Requester not found"}), 404
            if not _is_supervisor_role(requester[3]):
                return jsonify({"error": "Only supervisors can submit calibration results"}), 403

            data = request.get_json() or {}
            call_id_raw = data.get('call_id')
            try:
                call_id = int(call_id_raw)
            except Exception:
                return jsonify({"error": "call_id is required and must be integer"}), 400

            scores = data.get('scores')
            criterion_comments = data.get('criterion_comments')
            comment = str(data.get('comment') or '').strip()
            if not isinstance(scores, list) or not isinstance(criterion_comments, list):
                return jsonify({"error": "scores and criterion_comments must be arrays"}), 400

            with db._get_cursor() as cursor:
                cursor.execute("""
                SELECT created_by_admin_id
                FROM calibration_rooms
                WHERE id = %s
            """, (room_id,))
                room_row = cursor.fetchone()
                if not room_row:
                    return jsonify({"error": "Calibration room not found"}), 404

                room_creator_id = int(room_row[0]) if room_row[0] is not None else None
                if int(requester_id) != int(room_creator_id or 0):
                    cursor.execute("""
                    SELECT id
                    FROM calibration_room_members
                    WHERE room_id = %s AND supervisor_id = %s
                """, (room_id, requester_id))
                    if not cursor.fetchone():
                        return jsonify({"error": "Join the room first"}), 403

                cursor.execute("""
                SELECT c.operator_id, d.criteria
                FROM calibration_room_calls c
                LEFT JOIN directions d ON d.id = c.direction_id
                WHERE c.id = %s AND c.room_id = %s
            """, (call_id, room_id))
                call_row = cursor.fetchone()
                if not call_row:
                    return jsonify({"error": "Calibration call not found"}), 404
                call_operator_id = int(call_row[0]) if call_row[0] is not None else None
                criteria = call_row[1] if isinstance(call_row[1], list) else []
                if call_operator_id is None or not _authorize_operator_scope(requester, requester_id, call_operator_id):
                    return jsonify({"error": "Forbidden: you cannot evaluate this operator"}), 403

                normalized_scores = []
                normalized_comments = []
                for idx in range(len(criteria)):
                    src_score = scores[idx] if idx < len(scores) else 'Correct'
                    src_comment = criterion_comments[idx] if idx < len(criterion_comments) else ''
                    normalized_scores.append(_normalize_calibration_score_value(src_score))
                    normalized_comments.append(str(src_comment or ''))

                total_score, _ = _compute_total_score_from_criteria(criteria, normalized_scores)
                if not comment:
                    comment = '; '.join(
                        f"{(criteria[i] or {}).get('name', f'Критерий {i + 1}')}: {text.strip()}"
                        for i, text in enumerate(normalized_comments)
                        if str(text or '').strip()
                    )

                cursor.execute("""
                INSERT INTO calibration_room_call_evaluations (
                    room_call_id,
                    evaluator_id,
                    score,
                    comment,
                    scores,
                    criterion_comments,
                    created_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (room_call_id, evaluator_id)
                DO UPDATE SET
                    score = EXCLUDED.score,
                    comment = EXCLUDED.comment,
                    scores = EXCLUDED.scores,
                    criterion_comments = EXCLUDED.criterion_comments,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
            """, (
                    call_id,
                    requester_id,
                    total_score,
                    comment,
                    json.dumps(normalized_scores, ensure_ascii=False),
                    json.dumps(normalized_comments, ensure_ascii=False),
                ))
                evaluation_id = cursor.fetchone()[0]

            return jsonify({
                "status": "success",
                "evaluation_id": evaluation_id,
                "call_id": call_id,
                "score": total_score
            }), 200
        except Exception as e:
            logging.exception("Error submitting calibration evaluation")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/call_calibration/rooms/<int:room_id>/history', methods=['GET', 'OPTIONS'])
    @require_api_key
    def get_calibration_room_history(room_id):
        if request.method == 'OPTIONS':
            return jsonify({}), 200
        try:
            requester_id = getattr(g, 'user_id', None)
            if not requester_id:
                return jsonify({"error": "Unauthorized"}), 401

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "Requester not found"}), 404
            if _calibration_role(requester_id, requester) is None:
                return jsonify({"error": CALIBRATION_FORBIDDEN_ERROR}), 403

            events = []

            with db._get_cursor() as cursor:
                # Verify room exists and requester has access
                cursor.execute("""
                SELECT r.id, r.created_at, r.room_title, u.name AS creator_name
                FROM calibration_rooms r
                JOIN users u ON u.id = r.created_by_admin_id
                WHERE r.id = %s
            """, (room_id,))
                room_row = cursor.fetchone()
                if not room_row:
                    return jsonify({"error": "Room not found"}), 404

                # Room creation
                events.append({
                    "type": "room_created",
                    "timestamp": room_row[1].strftime('%Y-%m-%d %H:%M:%S') if room_row[1] else None,
                    "actor": room_row[3],
                    "description": f"Комната создана: «{room_row[2] or f'Комната #{room_row[0]}'}»"
                })

                # Member joins
                cursor.execute("""
                SELECT m.joined_at, u.name
                FROM calibration_room_members m
                JOIN users u ON u.id = m.supervisor_id
                WHERE m.room_id = %s
                ORDER BY m.joined_at
            """, (room_id,))
                for row in cursor.fetchall():
                    events.append({
                        "type": "member_joined",
                        "timestamp": row[0].strftime('%Y-%m-%d %H:%M:%S') if row[0] else None,
                        "actor": row[1],
                        "description": f"Вошёл в комнату"
                    })

                # Calls added
                cursor.execute("""
                SELECT c.id, c.created_at, adm.name AS adder_name, op.name AS operator_name, c.phone_number
                FROM calibration_room_calls c
                JOIN users adm ON adm.id = c.created_by_admin_id
                JOIN users op ON op.id = c.operator_id
                WHERE c.room_id = %s
                ORDER BY c.created_at
            """, (room_id,))
                for row in cursor.fetchall():
                    events.append({
                        "type": "call_added",
                        "timestamp": row[1].strftime('%Y-%m-%d %H:%M:%S') if row[1] else None,
                        "actor": row[2],
                        "description": f"Добавлен звонок #{row[0]}: оператор {row[3]}, тел. {row[4]}"
                    })

                # Etalon updates
                cursor.execute("""
                SELECT c.id, c.etalon_updated_at, u.name, op.name AS operator_name
                FROM calibration_room_calls c
                JOIN users u ON u.id = c.etalon_updated_by
                JOIN users op ON op.id = c.operator_id
                WHERE c.room_id = %s AND c.etalon_updated_at IS NOT NULL
                ORDER BY c.etalon_updated_at
            """, (room_id,))
                for row in cursor.fetchall():
                    events.append({
                        "type": "etalon_updated",
                        "timestamp": row[1].strftime('%Y-%m-%d %H:%M:%S') if row[1] else None,
                        "actor": row[2],
                        "description": f"Обновлён эталон для звонка #{row[0]} (оператор {row[3]})"
                    })

                # Evaluations submitted / updated
                cursor.execute("""
                SELECT e.id, e.created_at, e.updated_at, u.name AS evaluator_name,
                       c.id AS call_id, op.name AS operator_name
                FROM calibration_room_call_evaluations e
                JOIN users u ON u.id = e.evaluator_id
                JOIN calibration_room_calls c ON c.id = e.room_call_id
                JOIN users op ON op.id = c.operator_id
                WHERE c.room_id = %s
                ORDER BY e.created_at
            """, (room_id,))
                for row in cursor.fetchall():
                    events.append({
                        "type": "evaluated",
                        "timestamp": row[1].strftime('%Y-%m-%d %H:%M:%S') if row[1] else None,
                        "actor": row[3],
                        "description": f"Оценил звонок #{row[4]} (оператор {row[5]})"
                    })
                    created_ts = row[1].strftime('%Y-%m-%d %H:%M:%S') if row[1] else None
                    updated_ts = row[2].strftime('%Y-%m-%d %H:%M:%S') if row[2] else None
                    if updated_ts and updated_ts != created_ts:
                        events.append({
                            "type": "evaluation_updated",
                            "timestamp": updated_ts,
                            "actor": row[3],
                            "description": f"Изменил оценку звонка #{row[4]} (оператор {row[5]})"
                        })

            events.sort(key=lambda x: x["timestamp"] or "")
            return jsonify({"status": "success", "events": events}), 200
        except Exception as e:
            logging.exception("Error getting calibration room history")
            return jsonify({"error": f"Internal server error"}), 500

    return bp
