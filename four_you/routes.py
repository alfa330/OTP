"""Раздел «four_you»: ручки /api/four_you — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_four_you_blueprint,
как у остальных пакетов репозитория."""
from flask import Blueprint, jsonify, request
from PIL import Image
from PIL import ImageOps
from database import db
from datetime import datetime
from datetime import timedelta
from google.cloud import storage as gcs_storage
from io import BytesIO
from werkzeug.utils import secure_filename
import json
import logging
import os
import re
import threading
import time
import uuid


FOUR_YOU_MAX_FILES_PER_UPLOAD = int(os.getenv('FOUR_YOU_MAX_FILES_PER_UPLOAD', '20'))

FOUR_YOU_MAX_FILE_SIZE_BYTES = int(os.getenv('FOUR_YOU_MAX_FILE_SIZE_BYTES', str(15 * 1024 * 1024)))

FOUR_YOU_UPLOAD_FOLDER = (os.getenv('FOUR_YOU_UPLOAD_FOLDER') or 'FourYou/').strip()

FOUR_YOU_ANN_MAX_BYTES = int(os.getenv('FOUR_YOU_ANN_MAX_BYTES', str(512 * 1024)))

FOUR_YOU_ANN_FONTS = frozenset({'inter', 'script', 'serif', 'mono', 'display', 'round'})

FOUR_YOU_ANN_BACKGROUNDS = frozenset({'none', 'hearts', 'aurora', 'bokeh', 'stars', 'sunset'})

def _four_you_bucket_name():
    return (
        (os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET_FOUR_YOU') or '').strip()
        or (os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET_TASKS') or '').strip()
        or (os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET') or '').strip()
    )

def _four_you_blob_paths():
    folder = FOUR_YOU_UPLOAD_FOLDER.strip('/')
    prefix = f"{folder}/" if folder else ""
    date_prefix = datetime.utcnow().strftime('%Y/%m/%d')
    random_id = uuid.uuid4().hex
    base_path = f"{prefix}{date_prefix}/{random_id}"
    return f"{base_path}_preview.webp", f"{base_path}_display.webp"

_FOUR_YOU_HEX_RE = re.compile(r'^#[0-9a-fA-F]{3,8}$')

def _four_you_num(value, lo, hi, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(lo, min(hi, number))

def _four_you_color(value, fallback='#ffffff'):
    text = str(value or '').strip()
    return text if _FOUR_YOU_HEX_RE.match(text) else fallback

def _sanitize_four_you_annotations(raw):
    """Защита хранимой/расшариваемой разметки: типы, диапазоны координат 0..1,
    белые списки шрифтов/фонов и лимиты количества. Текст не парсится как HTML —
    фронт рендерит его как текст (React экранирует), здесь лишь ограничиваем длину."""
    if not isinstance(raw, dict):
        return {'strokes': [], 'stickers': [], 'texts': [], 'comments': [], 'background': 'none'}

    strokes = []
    for stroke in (raw.get('strokes') or [])[:200]:
        if not isinstance(stroke, dict):
            continue
        points = []
        for point in (stroke.get('points') or [])[:1200]:
            if isinstance(point, (list, tuple)) and len(point) >= 2:
                points.append([
                    round(_four_you_num(point[0], 0, 1, 0), 4),
                    round(_four_you_num(point[1], 0, 1, 0), 4),
                ])
        if points:
            strokes.append({
                'color': _four_you_color(stroke.get('color')),
                # width нормализована к ширине фото (доля 0..1), чтобы линия масштабировалась.
                'width': round(_four_you_num(stroke.get('width'), 0.0008, 0.15, 0.006), 5),
                'points': points,
            })

    stickers = []
    for sticker in (raw.get('stickers') or [])[:120]:
        if not isinstance(sticker, dict):
            continue
        emoji = str(sticker.get('emoji') or '')[:12]
        if not emoji.strip():
            continue
        stickers.append({
            'emoji': emoji,
            'x': round(_four_you_num(sticker.get('x'), -0.2, 1.2, 0.5), 4),
            'y': round(_four_you_num(sticker.get('y'), -0.2, 1.2, 0.5), 4),
            'size': round(_four_you_num(sticker.get('size'), 0.02, 0.6, 0.12), 4),
            'rot': round(_four_you_num(sticker.get('rot'), -360, 360, 0), 2),
        })

    texts = []
    for item in (raw.get('texts') or [])[:60]:
        if not isinstance(item, dict):
            continue
        text_value = str(item.get('text') or '')[:200]
        if not text_value.strip():
            continue
        font = str(item.get('font') or 'inter')
        texts.append({
            'text': text_value,
            'x': round(_four_you_num(item.get('x'), -0.2, 1.2, 0.5), 4),
            'y': round(_four_you_num(item.get('y'), -0.2, 1.2, 0.5), 4),
            'size': round(_four_you_num(item.get('size'), 0.02, 0.4, 0.06), 4),
            'rot': round(_four_you_num(item.get('rot'), -360, 360, 0), 2),
            'font': font if font in FOUR_YOU_ANN_FONTS else 'inter',
            'color': _four_you_color(item.get('color')),
        })

    comments = []
    for comment in (raw.get('comments') or [])[:300]:
        if not isinstance(comment, dict):
            continue
        comment_text = str(comment.get('text') or '')[:600]
        if not comment_text.strip():
            continue
        try:
            author = int(comment.get('by') or 0)
        except (TypeError, ValueError):
            author = 0
        comments.append({
            'by': author,
            'text': comment_text,
            'at': str(comment.get('at') or '')[:40],
        })

    background = str(raw.get('background') or 'none')
    return {
        'strokes': strokes,
        'stickers': stickers,
        'texts': texts,
        'comments': comments,
        'background': background if background in FOUR_YOU_ANN_BACKGROUNDS else 'none',
    }


def build_four_you_blueprint(
    *,
    AVATAR_SIGNED_URL_CACHE,
    AVATAR_SIGNED_URL_CACHE_LOCK,
    AVATAR_SIGNED_URL_CACHE_MAX_ITEMS,
    AVATAR_SIGNED_URL_CACHE_MIN_REMAINING_SECONDS,
    AVATAR_SIGNED_URL_TTL_SECONDS,
    FOUR_YOU_ADMIN_USER_ID,
    FOUR_YOU_MAX_IMAGE_PIXELS,
    FOUR_YOU_SECTION_OPEN,
    _build_avatar_signed_url,
    _four_you_access_for_requester,
    _four_you_convert_image_variants,
    _get_authenticated_requester,
    _normalize_user_role,
    get_gcs_client,
    require_auth,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""
    bp = Blueprint('four_you', __name__, url_prefix='/api')

    def _four_you_route_guard(require_upload=False):
        requester_id, requester, auth_error = _get_authenticated_requester()
        if auth_error:
            message, status_code = auth_error
            return None, None, jsonify({"error": message}), status_code
        can_view, can_upload = _four_you_access_for_requester(requester_id, requester)
        if not can_view or (require_upload and not can_upload):
            return None, None, jsonify({"error": "Access denied"}), 403
        return requester_id, can_upload, None, None

    def _four_you_image_payload(image_row):
        return {
            "id": image_row.get("id"),
            "preview_url": _build_avatar_signed_url(
                image_row.get("preview_bucket"),
                image_row.get("preview_blob_path")
            ),
            "display_url": _build_avatar_signed_url(
                image_row.get("display_bucket"),
                image_row.get("display_blob_path")
            ),
            "width": int(image_row.get("width") or 0),
            "height": int(image_row.get("height") or 0),
            "created_at": image_row.get("created_at"),
            "annotations": image_row.get("annotations") or {},
            "annotations_updated_at": image_row.get("annotations_updated_at"),
        }

    def _delete_four_you_blobs(image_row):
        refs = {
            (str(image_row.get("preview_bucket") or '').strip(), str(image_row.get("preview_blob_path") or '').strip()),
            (str(image_row.get("display_bucket") or '').strip(), str(image_row.get("display_blob_path") or '').strip()),
        }
        client = None
        for bucket_name, blob_path in refs:
            if not bucket_name or not blob_path:
                continue
            try:
                if client is None:
                    client = get_gcs_client()
                client.bucket(bucket_name).blob(blob_path).delete()
            except Exception:
                logging.warning("Failed to delete 4 You blob %s/%s", bucket_name, blob_path, exc_info=True)

    @bp.route('/four_you/images', methods=['GET', 'POST', 'OPTIONS'])
    @require_auth
    def four_you_images():
        requester_id, can_upload, guard_response, guard_status = _four_you_route_guard(
            require_upload=request.method == 'POST'
        )
        if guard_response is not None:
            return guard_response, guard_status

        if request.method == 'GET':
            rows = db.list_four_you_images()
            response = jsonify({
                "status": "success",
                "can_upload": bool(can_upload),
                "images": [_four_you_image_payload(row) for row in rows],
            })
            response.headers['Cache-Control'] = 'private, max-age=30'
            return response, 200

        files = [item for item in request.files.getlist('images') if item and item.filename]
        if not files:
            single_file = request.files.get('image')
            files = [single_file] if single_file and single_file.filename else []
        if not files:
            return jsonify({"error": "Выберите изображения"}), 400
        if len(files) > FOUR_YOU_MAX_FILES_PER_UPLOAD:
            return jsonify({"error": f"За один раз можно загрузить не более {FOUR_YOU_MAX_FILES_PER_UPLOAD} изображений"}), 400

        bucket_name = _four_you_bucket_name()
        if not bucket_name:
            return jsonify({"error": "Хранилище 4 You не настроено"}), 500

        created_ids = []
        uploaded_rows = []
        try:
            client = get_gcs_client()
            bucket = client.bucket(bucket_name)
            order_seed = time.time_ns()

            for index, file_storage in enumerate(files):
                raw_bytes = file_storage.read() or b''
                if not raw_bytes:
                    raise ValueError(f"Файл «{file_storage.filename}» пуст")
                if len(raw_bytes) > FOUR_YOU_MAX_FILE_SIZE_BYTES:
                    max_mb = FOUR_YOU_MAX_FILE_SIZE_BYTES // (1024 * 1024)
                    raise ValueError(f"Файл «{file_storage.filename}» превышает лимит {max_mb} МБ")
                content_type = str(file_storage.mimetype or '').strip().lower()
                if not content_type.startswith('image/'):
                    raise ValueError(f"Файл «{file_storage.filename}» не является изображением")

                preview_bytes, display_bytes, width, height = _four_you_convert_image_variants(raw_bytes)
                preview_path, display_path = _four_you_blob_paths()
                uploaded_row = {
                    "preview_bucket": bucket_name,
                    "preview_blob_path": preview_path,
                    "display_bucket": bucket_name,
                    "display_blob_path": display_path,
                }
                uploaded_rows.append(uploaded_row)

                preview_blob = bucket.blob(preview_path)
                preview_blob.cache_control = 'private, max-age=31536000, immutable'
                preview_blob.upload_from_string(preview_bytes, content_type='image/webp')

                display_blob = bucket.blob(display_path)
                display_blob.cache_control = 'private, max-age=31536000, immutable'
                display_blob.upload_from_string(display_bytes, content_type='image/webp')

                original_name = (secure_filename(file_storage.filename) or f'image-{index + 1}')[:255]
                image_id = db.create_four_you_image({
                    **uploaded_row,
                    "original_name": original_name,
                    "width": width,
                    "height": height,
                    "preview_file_size": len(preview_bytes),
                    "display_file_size": len(display_bytes),
                    "uploaded_by": requester_id,
                    "sort_order": order_seed + index,
                })
                if not image_id:
                    raise RuntimeError("Не удалось сохранить изображение")
                created_ids.append(image_id)

            rows = db.list_four_you_images()
            return jsonify({
                "status": "success",
                "can_upload": True,
                "images": [_four_you_image_payload(row) for row in rows],
            }), 201
        except Exception as error:
            for image_id in created_ids:
                try:
                    db.delete_four_you_image(image_id)
                except Exception:
                    logging.warning("Failed to roll back 4 You image metadata %s", image_id, exc_info=True)
            for uploaded_row in uploaded_rows:
                _delete_four_you_blobs(uploaded_row)
            if isinstance(error, ValueError):
                return jsonify({"error": str(error)}), 400
            logging.error("4 You upload failed: %s", error, exc_info=True)
            return jsonify({"error": "Не удалось загрузить изображения"}), 500

    @bp.route('/four_you/images/<image_id>', methods=['DELETE', 'OPTIONS'])
    @require_auth
    def delete_four_you_image(image_id):
        _, _, guard_response, guard_status = _four_you_route_guard(require_upload=True)
        if guard_response is not None:
            return guard_response, guard_status
        try:
            normalized_image_id = str(uuid.UUID(str(image_id)))
        except (TypeError, ValueError):
            return jsonify({"error": "Некорректный идентификатор изображения"}), 400

        deleted_row = db.delete_four_you_image(normalized_image_id)
        if not deleted_row:
            return jsonify({"error": "Изображение не найдено"}), 404
        _delete_four_you_blobs(deleted_row)
        return jsonify({"status": "success", "deleted_id": normalized_image_id}), 200

    @bp.route('/four_you/images/delete_batch', methods=['POST', 'OPTIONS'])
    @require_auth
    def delete_four_you_images_batch():
        _, _, guard_response, guard_status = _four_you_route_guard(require_upload=True)
        if guard_response is not None:
            return guard_response, guard_status

        payload = request.get_json(silent=True) or {}
        raw_ids = payload.get('ids')
        if not isinstance(raw_ids, list) or not raw_ids:
            return jsonify({"error": "Не выбраны изображения"}), 400
        if len(raw_ids) > 500:
            return jsonify({"error": "За один раз можно удалить не более 500 изображений"}), 400

        normalized_ids = []
        seen = set()
        for raw_id in raw_ids:
            try:
                normalized = str(uuid.UUID(str(raw_id)))
            except (TypeError, ValueError):
                return jsonify({"error": "Некорректный идентификатор изображения"}), 400
            if normalized not in seen:
                seen.add(normalized)
                normalized_ids.append(normalized)

        deleted_rows = db.delete_four_you_images(normalized_ids)
        for deleted_row in deleted_rows:
            _delete_four_you_blobs(deleted_row)

        rows = db.list_four_you_images()
        return jsonify({
            "status": "success",
            "deleted_ids": [row["id"] for row in deleted_rows],
            "deleted_count": len(deleted_rows),
            "can_upload": True,
            "images": [_four_you_image_payload(row) for row in rows],
        }), 200

    @bp.route('/four_you/images/<image_id>/annotations', methods=['PUT', 'POST', 'OPTIONS'])
    @require_auth
    def save_four_you_annotations(image_id):
        # Разметку (рисунок/стикеры/текст/комментарии/фон) могут менять пользователи с доступом к 4 You.
        # (require_upload=False ⇒ достаточно доступа к просмотру). Загрузка/удаление фото — только Руслан.
        requester_id, _, guard_response, guard_status = _four_you_route_guard()
        if guard_response is not None:
            return guard_response, guard_status
        try:
            normalized_image_id = str(uuid.UUID(str(image_id)))
        except (TypeError, ValueError):
            return jsonify({"error": "Некорректный идентификатор изображения"}), 400

        payload = request.get_json(silent=True) or {}
        raw = payload.get('annotations') if isinstance(payload.get('annotations'), dict) else payload
        sanitized = _sanitize_four_you_annotations(raw)
        sanitized['updated_by'] = int(requester_id or 0)
        try:
            serialized_len = len(json.dumps(sanitized, ensure_ascii=False).encode('utf-8'))
        except Exception:
            serialized_len = 0
        if serialized_len > FOUR_YOU_ANN_MAX_BYTES:
            return jsonify({"error": "Слишком большой объём разметки, упростите рисунок"}), 413

        saved = db.set_four_you_annotations(normalized_image_id, sanitized)
        if saved is None:
            return jsonify({"error": "Изображение не найдено"}), 404
        return jsonify({"status": "success", "id": normalized_image_id, **saved}), 200

    @bp.route('/four_you/annotations/poll', methods=['GET', 'OPTIONS'])
    @require_auth
    def four_you_annotations_poll():
        # Лёгкий поллинг разметки для near-real-time синхронизации между двумя пользователями.
        _, _, guard_response, guard_status = _four_you_route_guard()
        if guard_response is not None:
            return guard_response, guard_status
        since = (request.args.get('since') or '').strip() or None
        items = db.list_four_you_annotations_changed_since(since)
        return jsonify({"status": "success", "items": items}), 200

    @bp.route('/four_you/seen', methods=['POST', 'OPTIONS'])
    @require_auth
    def four_you_seen():
        # Зритель открыл раздел 4 You — гасим бейдж новых фото.
        requester_id, _, guard_response, guard_status = _four_you_route_guard()
        if guard_response is not None:
            return guard_response, guard_status
        db.mark_four_you_seen(requester_id)
        return jsonify({"status": "success"}), 200

    @bp.route('/four_you/unread_count', methods=['GET', 'OPTIONS'])
    @require_auth
    def four_you_unread_count():
        # Кол-во новых фото 4 You для бейджа в сайдбаре (как у «Ивентов»).
        requester_id, _, guard_response, guard_status = _four_you_route_guard()
        if guard_response is not None:
            return guard_response, guard_status
        count = db.count_unread_four_you_images(requester_id)
        response = jsonify({"status": "success", "count": int(count)})
        response.headers['Cache-Control'] = 'no-store'
        return response, 200

    return bp
