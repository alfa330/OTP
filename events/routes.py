"""Раздел «events»: ручки /api/events — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_events_blueprint,
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


EVENTS_MAX_MEDIA_PER_POST = int(os.getenv('EVENTS_MAX_MEDIA_PER_POST', '10'))

EVENTS_MAX_IMAGE_FILE_SIZE_BYTES = int(os.getenv('EVENTS_MAX_IMAGE_FILE_SIZE_BYTES', str(15 * 1024 * 1024)))

EVENTS_MAX_VIDEO_FILE_SIZE_BYTES = int(os.getenv('EVENTS_MAX_VIDEO_FILE_SIZE_BYTES', str(200 * 1024 * 1024)))

EVENTS_ALLOWED_VIDEO_MIME = frozenset({'video/mp4', 'video/webm', 'video/quicktime'})

EVENTS_UPLOAD_FOLDER = (os.getenv('EVENTS_UPLOAD_FOLDER') or 'Events/').strip()

EVENTS_BODY_MAX_CHARS = int(os.getenv('EVENTS_BODY_MAX_CHARS', '8000'))

EVENTS_TITLE_MAX_CHARS = 255

EVENTS_COMMENT_MAX_CHARS = int(os.getenv('EVENTS_COMMENT_MAX_CHARS', '2000'))

EVENTS_VIDEO_SIGNED_URL_TTL_MINUTES = int(os.getenv('EVENTS_VIDEO_SIGNED_URL_TTL_MINUTES', '240'))

def _events_bucket_name():
    return (
        (os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET_EVENTS') or '').strip()
        or (os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET_TASKS') or '').strip()
        or (os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET') or '').strip()
    )

def _events_blob_path(suffix, ext):
    folder = EVENTS_UPLOAD_FOLDER.strip('/')
    prefix = f"{folder}/" if folder else ""
    date_prefix = datetime.utcnow().strftime('%Y/%m/%d')
    random_id = uuid.uuid4().hex
    safe_ext = ext if (ext and re.match(r'^\.[a-z0-9]{1,6}$', ext)) else ''
    return f"{prefix}{date_prefix}/{random_id}_{suffix}{safe_ext}"

def _events_video_ext(filename, mime):
    ext = os.path.splitext(secure_filename(filename or ''))[1].lower()
    if re.match(r'^\.[a-z0-9]{1,5}$', ext):
        return ext
    return {'video/webm': '.webm', 'video/quicktime': '.mov'}.get(mime, '.mp4')

_EVENTS_UNSET = object()  # сентинел: «скоуп не передан» (None — валидное значение).

def _events_can_view(event_department_ids, is_global, viewer_dept):
    """Видимость поста: глобальным — всё; пост без отделов — всем; иначе отдел
    зрителя должен быть среди получателей. event_department_ids — список (или [])."""
    if is_global or not event_department_ids:
        return True
    if viewer_dept is None:
        return False
    return int(viewer_dept) in {int(d) for d in event_department_ids}


def build_events_blueprint(
    *,
    AVATAR_SIGNED_URL_CACHE,
    AVATAR_SIGNED_URL_CACHE_LOCK,
    AVATAR_SIGNED_URL_CACHE_MAX_ITEMS,
    AVATAR_SIGNED_URL_CACHE_MIN_REMAINING_SECONDS,
    AVATAR_SIGNED_URL_TTL_SECONDS,
    FOUR_YOU_MAX_IMAGE_PIXELS,
    ROLE_HIERARCHY,
    _build_avatar_signed_url,
    _department_scope_id_for_requester,
    _events_viewer_scope,
    _four_you_convert_image_variants,
    _get_authenticated_requester,
    _get_role_level,
    _has_min_role,
    _headed_department_id,
    _is_admin_role,
    _is_global_admin_requester,
    _lms_signed_url,
    _normalize_user_role,
    get_gcs_client,
    require_auth,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""
    bp = Blueprint('events', __name__, url_prefix='/api')

    def _events_can_publish(role, requester_id):
        """Публиковать ивенты могут админы, супервайзеры и главы отделов."""
        role = _normalize_user_role(role)
        return _is_admin_role(role) or role == 'sv' or _headed_department_id(requester_id) is not None

    def _events_is_moderator_for(requester_id, role, event_department_ids, viewer_scope=_EVENTS_UNSET):
        """Модератор поста: глобальный админ — везде; админ-глава/СВ/глава отдела —
    только если их отдел среди получателей поста (не для постов «всем»).

    viewer_scope можно передать заранее (на странице с множеством постов/
    комментариев), чтобы не дёргать _department_scope_id_for_requester на каждый
    элемент (его get_user_department_id не кэшируется)."""
        role = _normalize_user_role(role)
        if _is_global_admin_requester(role, requester_id):
            return True
        if _is_admin_role(role) or role == 'sv' or _headed_department_id(requester_id) is not None:
            scope = viewer_scope if viewer_scope is not _EVENTS_UNSET else _department_scope_id_for_requester(requester_id)
            if scope is None or not event_department_ids:
                return False
            return int(scope) in {int(d) for d in event_department_ids}
        return False

    def _events_resolve_target_departments(role, requester_id, requested_raw):
        """Возвращает (department_ids, error). Пустой список => виден всем отделам.
    Глобальный админ выбирает любой набор отделов или «всем» (пустой); остальные
    публикаторы жёстко привязаны к своему отделу (без рассылки «всем»)."""
        role = _normalize_user_role(role)
        # requested_raw — JSON-массив id (или 'all'/''/None/[] => «всем»).
        requested_ids = []
        if isinstance(requested_raw, list):
            candidates = requested_raw
        elif requested_raw in (None, '', 'all', 'null', '[]'):
            candidates = []
        else:
            try:
                parsed = json.loads(requested_raw)
                candidates = parsed if isinstance(parsed, list) else [parsed]
            except (TypeError, ValueError):
                candidates = [requested_raw]
        for c in candidates:
            try:
                cid = int(c)
            except (TypeError, ValueError):
                return None, "Некорректный отдел"
            if cid not in requested_ids:
                requested_ids.append(cid)

        if _is_global_admin_requester(role, requester_id):
            if not requested_ids:
                return [], None  # всем
            for cid in requested_ids:
                dept = db.get_department_by_id(cid)
                if not dept or not dept.get('is_active', True):
                    return None, "Отдел не найден"
            return requested_ids, None
        # Привязанный публикатор (СВ / глава отдела / админ-глава): только свой отдел.
        scope = _department_scope_id_for_requester(requester_id)
        if scope is None:
            return None, "У вас не задан отдел для публикации"
        return [int(scope)], None

    def _events_publish_options(role, requester_id):
        """Опции для формы создания: можно ли публиковать, можно ли «всем»,
    и список отделов-получателей, доступных публикатору."""
        if not _events_can_publish(role, requester_id):
            return {"can_publish": False, "can_target_all": False, "publish_departments": []}
        if _is_global_admin_requester(role, requester_id):
            options = [
                {"id": d.get("id"), "name": d.get("name")}
                for d in (db.get_departments() or [])
                if d.get("is_active", True)
            ]
            return {"can_publish": True, "can_target_all": True, "publish_departments": options}
        scope = _department_scope_id_for_requester(requester_id)
        dept = db.get_department_by_id(scope) if scope else None
        options = [{"id": dept.get("id"), "name": dept.get("name")}] if dept else []
        return {"can_publish": True, "can_target_all": False, "publish_departments": options}

    def _events_can_delete_post(event, requester_id, role, viewer_scope=_EVENTS_UNSET):
        author_id = event.get("author_id")
        if author_id is not None and requester_id is not None and int(author_id) == int(requester_id):
            return True
        return _events_is_moderator_for(requester_id, role, event.get("department_ids") or [], viewer_scope)

    def _events_media_payload(media):
        mtype = media.get("type")
        if mtype == 'video':
            return {
                "id": media.get("id"),
                "type": "video",
                "url": _lms_signed_url(
                    media.get("bucket"), media.get("blob_path"),
                    expires_minutes=EVENTS_VIDEO_SIGNED_URL_TTL_MINUTES,
                    response_disposition='inline',
                    response_type=media.get("mime_type") or 'video/mp4',
                ),
                "poster_url": _build_avatar_signed_url(media.get("preview_bucket"), media.get("preview_blob_path")),
                "width": int(media.get("width") or 0),
                "height": int(media.get("height") or 0),
                "duration_seconds": media.get("duration_seconds"),
                "mime_type": media.get("mime_type"),
            }
        display_url = _build_avatar_signed_url(media.get("bucket"), media.get("blob_path"))
        preview_url = _build_avatar_signed_url(media.get("preview_bucket"), media.get("preview_blob_path"))
        return {
            "id": media.get("id"),
            "type": "image",
            "url": display_url,
            "preview_url": preview_url or display_url,
            "width": int(media.get("width") or 0),
            "height": int(media.get("height") or 0),
        }

    def _events_event_payload(event, requester_id=None, role=None, viewer_scope=_EVENTS_UNSET):
        department_ids = event.get("department_ids") or []
        department_names = event.get("department_names") or []
        legacy_department_name = None
        if department_names:
            legacy_department_name = department_names[0]
            if len(department_names) > 1:
                legacy_department_name = f"{legacy_department_name} +{len(department_names) - 1}"
        return {
            "id": event.get("id"),
            "author_id": event.get("author_id"),
            "author_name": event.get("author_name"),
            "author_avatar_url": _build_avatar_signed_url(
                event.get("author_avatar_bucket"), event.get("author_avatar_blob_path")
            ),
            "department_ids": department_ids,
            "department_names": department_names,
            "department_id": department_ids[0] if department_ids else None,
            "department_name": legacy_department_name,
            "title": event.get("title"),
            "body": event.get("body"),
            "created_at": event.get("created_at"),
            "like_count": int(event.get("like_count") or 0),
            "comment_count": int(event.get("comment_count") or 0),
            "liked": bool(event.get("liked")),
            "media": [_events_media_payload(m) for m in (event.get("media") or [])],
            "can_delete": _events_can_delete_post(event, requester_id, role, viewer_scope),
            "is_pinned": bool(event.get("is_pinned")),
            # Закрепление строже удаления: свой пост автор убрать вправе, а поднять
            # его над чужими поверх всей ленты — решение уровня отдела.
            "can_pin": _events_is_moderator_for(
                requester_id, role, event.get("department_ids") or [], viewer_scope),
        }

    def _events_comment_payload(comment, requester_id=None, role=None,
                                event_author_id=None, event_department_ids=None, moderator=None):
        # moderator (bool) можно передать заранее: для всех комментов одного поста
        # модераторский статус одинаков, считаем его один раз в роуте (а не на каждый).
        can_delete = False
        owner = comment.get("user_id")
        if requester_id is not None:
            if owner is not None and int(owner) == int(requester_id):
                can_delete = True
            elif event_author_id is not None and int(event_author_id) == int(requester_id):
                can_delete = True
            elif (moderator if moderator is not None
                  else _events_is_moderator_for(requester_id, role, event_department_ids or [])):
                can_delete = True
        return {
            "id": comment.get("id"),
            "user_id": comment.get("user_id"),
            "user_name": comment.get("user_name"),
            "author_avatar_url": _build_avatar_signed_url(
                comment.get("author_avatar_bucket"), comment.get("author_avatar_blob_path")
            ),
            "body": comment.get("body"),
            "created_at": comment.get("created_at"),
            "can_delete": can_delete,
        }

    def _delete_events_blobs(blobs):
        client = None
        for bucket_name, blob_path in (blobs or []):
            bucket_name = str(bucket_name or '').strip()
            blob_path = str(blob_path or '').strip()
            if not bucket_name or not blob_path:
                continue
            try:
                if client is None:
                    client = get_gcs_client()
                client.bucket(bucket_name).blob(blob_path).delete()
            except Exception:
                logging.warning("Failed to delete event blob %s/%s", bucket_name, blob_path, exc_info=True)

    @bp.route('/events', methods=['GET', 'POST', 'OPTIONS'])
    @require_auth
    def api_events():
        requester_id, requester, auth_error = _get_authenticated_requester()
        if auth_error:
            message, status_code = auth_error
            return jsonify({"error": message}), status_code
        role = _normalize_user_role(requester[3])
        is_global, viewer_dept = _events_viewer_scope(requester_id, role)

        if request.method == 'GET':
            before_raw = request.args.get('before')
            try:
                before_id = int(before_raw) if before_raw not in (None, '', 'null') else None
            except (TypeError, ValueError):
                before_id = None
            try:
                limit = int(request.args.get('limit') or 12)
            except (TypeError, ValueError):
                limit = 12
            result = db.list_events(requester_id, viewer_dept, is_global, before_id, limit)
            # viewer_dept уже равен _department_scope_id_for_requester для скоупных —
            # передаём как viewer_scope, чтобы can_delete не дёргал БД на каждый пост.
            events_payload = [
                _events_event_payload(ev, requester_id, role, viewer_dept)
                for ev in result["events"]
            ]
            # Закреплённые приходят отдельным списком и только к первой странице —
            # иначе они ломали бы курсор по id (см. Database.list_events).
            pinned_payload = [
                _events_event_payload(ev, requester_id, role, viewer_dept)
                for ev in result.get("pinned") or []
            ]
            response = jsonify({
                "status": "success",
                "events": events_payload,
                "pinned": pinned_payload,
                "has_more": result["has_more"],
                "next_before": result["next_before"],
                **_events_publish_options(role, requester_id),
            })
            response.headers['Cache-Control'] = 'private, max-age=10'
            return response, 200

        # POST — создание поста
        if not _events_can_publish(role, requester_id):
            return jsonify({"error": "Недостаточно прав для публикации ивентов"}), 403

        title = str(request.form.get('title') or '').strip()[:EVENTS_TITLE_MAX_CHARS]
        body = str(request.form.get('body') or '').strip()[:EVENTS_BODY_MAX_CHARS]
        # department_ids — JSON-массив id ([] / 'all' => виден всем отделам).
        requested_departments = (
            request.form.get('department_ids')
            if 'department_ids' in request.form
            else request.form.get('department_id')
        )
        target_dept_ids, dept_error = _events_resolve_target_departments(
            role, requester_id, requested_departments
        )
        if dept_error:
            return jsonify({"error": dept_error}), 400

        media_files = [f for f in request.files.getlist('media') if f and f.filename]
        poster_files = [f for f in request.files.getlist('posters') if f and f.filename]
        try:
            media_meta = json.loads(request.form.get('media_meta') or '[]')
            if not isinstance(media_meta, list):
                media_meta = []
        except Exception:
            media_meta = []

        if not body and not media_files:
            return jsonify({"error": "Добавьте текст или медиа"}), 400
        if len(media_files) > EVENTS_MAX_MEDIA_PER_POST:
            return jsonify({"error": f"Не более {EVENTS_MAX_MEDIA_PER_POST} файлов в одном посте"}), 400
        if media_files and len(media_meta) != len(media_files):
            return jsonify({"error": "Несогласованные данные медиа"}), 400

        bucket_name = _events_bucket_name() if media_files else None
        if media_files and not bucket_name:
            return jsonify({"error": "Хранилище ивентов не настроено"}), 500

        # Предварительная проверка типов ДО создания поста — чтобы заведомо неверный
        # файл отклонялся 400-кой без INSERT+rollback в БД (тела не читаем здесь).
        for idx, fs in enumerate(media_files):
            meta_i = media_meta[idx] if isinstance(media_meta[idx], dict) else {}
            declared = str(meta_i.get('type') or '').strip().lower()
            ct = str(fs.mimetype or '').strip().lower()
            if declared == 'video' or ct.startswith('video/'):
                if ct not in EVENTS_ALLOWED_VIDEO_MIME:
                    return jsonify({"error": f"Формат видео «{fs.filename}» не поддерживается"}), 400
            elif not ct.startswith('image/'):
                return jsonify({"error": f"Файл «{fs.filename}» не является изображением"}), 400

        created_event_id = None
        uploaded_blobs = []
        poster_cursor = 0
        try:
            client = get_gcs_client() if media_files else None
            bucket = client.bucket(bucket_name) if client else None
            created_event_id = db.create_event(requester_id, title, body, target_dept_ids)

            for index, file_storage in enumerate(media_files):
                meta = media_meta[index] if index < len(media_meta) else {}
                meta = meta if isinstance(meta, dict) else {}
                declared_type = str(meta.get('type') or '').strip().lower()
                content_type = str(file_storage.mimetype or '').strip().lower()
                raw_bytes = file_storage.read() or b''
                if not raw_bytes:
                    raise ValueError(f"Файл «{file_storage.filename}» пуст")

                if declared_type == 'video' or content_type.startswith('video/'):
                    if len(raw_bytes) > EVENTS_MAX_VIDEO_FILE_SIZE_BYTES:
                        max_mb = EVENTS_MAX_VIDEO_FILE_SIZE_BYTES // (1024 * 1024)
                        raise ValueError(f"Видео «{file_storage.filename}» превышает лимит {max_mb} МБ")
                    if content_type not in EVENTS_ALLOWED_VIDEO_MIME:
                        raise ValueError(f"Формат видео «{file_storage.filename}» не поддерживается")
                    video_path = _events_blob_path(f"{index}_video", _events_video_ext(file_storage.filename, content_type))
                    video_blob = bucket.blob(video_path)
                    video_blob.cache_control = 'private, max-age=31536000, immutable'
                    video_blob.upload_from_string(raw_bytes, content_type=content_type)
                    uploaded_blobs.append((bucket_name, video_path))

                    poster_bucket = poster_path = None
                    poster_w = poster_h = 0
                    # Постеры в posters[] идут по порядку ТОЛЬКО для видео, у которых
                    # клиент пометил has_poster=true (захват кадра удался). Так слот
                    # постера не «съезжает», если у раннего видео постер не сделался.
                    if bool(meta.get('has_poster')) and poster_cursor < len(poster_files):
                        poster_fs = poster_files[poster_cursor]
                        poster_cursor += 1
                        poster_raw = poster_fs.read() or b''
                        if poster_raw:
                            try:
                                poster_bytes, _disp_bytes, poster_w, poster_h = _four_you_convert_image_variants(poster_raw)
                                poster_path = _events_blob_path(f"{index}_poster", '.webp')
                                poster_blob = bucket.blob(poster_path)
                                poster_blob.cache_control = 'private, max-age=31536000, immutable'
                                poster_blob.upload_from_string(poster_bytes, content_type='image/webp')
                                poster_bucket = bucket_name
                                uploaded_blobs.append((bucket_name, poster_path))
                            except Exception:
                                poster_bucket = poster_path = None
                                poster_w = poster_h = 0

                    duration = meta.get('duration')
                    try:
                        duration = round(float(duration), 2) if duration is not None else None
                    except (TypeError, ValueError):
                        duration = None
                    db.add_event_media(created_event_id, {
                        "media_type": "video",
                        "bucket": bucket_name, "blob_path": video_path,
                        "preview_bucket": poster_bucket, "preview_blob_path": poster_path,
                        "mime_type": content_type,
                        "width": poster_w or int(meta.get('width') or 0),
                        "height": poster_h or int(meta.get('height') or 0),
                        "file_size": len(raw_bytes), "duration_seconds": duration,
                        "sort_order": index,
                    })
                else:
                    if not content_type.startswith('image/'):
                        raise ValueError(f"Файл «{file_storage.filename}» не является изображением")
                    if len(raw_bytes) > EVENTS_MAX_IMAGE_FILE_SIZE_BYTES:
                        max_mb = EVENTS_MAX_IMAGE_FILE_SIZE_BYTES // (1024 * 1024)
                        raise ValueError(f"Изображение «{file_storage.filename}» превышает лимит {max_mb} МБ")
                    preview_bytes, display_bytes, width, height = _four_you_convert_image_variants(raw_bytes)
                    preview_path = _events_blob_path(f"{index}_preview", '.webp')
                    display_path = _events_blob_path(f"{index}_display", '.webp')
                    preview_blob = bucket.blob(preview_path)
                    preview_blob.cache_control = 'private, max-age=31536000, immutable'
                    preview_blob.upload_from_string(preview_bytes, content_type='image/webp')
                    display_blob = bucket.blob(display_path)
                    display_blob.cache_control = 'private, max-age=31536000, immutable'
                    display_blob.upload_from_string(display_bytes, content_type='image/webp')
                    uploaded_blobs.append((bucket_name, preview_path))
                    uploaded_blobs.append((bucket_name, display_path))
                    db.add_event_media(created_event_id, {
                        "media_type": "image",
                        "bucket": bucket_name, "blob_path": display_path,
                        "preview_bucket": bucket_name, "preview_blob_path": preview_path,
                        "mime_type": "image/webp", "width": width, "height": height,
                        "file_size": len(display_bytes), "sort_order": index,
                    })

            event = db.get_event(created_event_id, requester_id)
            return jsonify({"status": "success", "event": _events_event_payload(event, requester_id, role)}), 201
        except Exception as error:
            if created_event_id:
                try:
                    db.delete_event(created_event_id)
                except Exception:
                    logging.warning("Failed to roll back event %s", created_event_id, exc_info=True)
            _delete_events_blobs(uploaded_blobs)
            if isinstance(error, ValueError):
                return jsonify({"error": str(error)}), 400
            logging.error("Event create failed: %s", error, exc_info=True)
            return jsonify({"error": "Не удалось опубликовать ивент"}), 500

    @bp.route('/events/seen', methods=['POST', 'OPTIONS'])
    @require_auth
    def api_events_seen():
        requester_id, requester, auth_error = _get_authenticated_requester()
        if auth_error:
            message, status_code = auth_error
            return jsonify({"error": message}), status_code
        db.mark_events_seen(requester_id)
        return jsonify({"status": "success"}), 200

    @bp.route('/events/unread_count', methods=['GET', 'OPTIONS'])
    @require_auth
    def api_events_unread_count():
        requester_id, requester, auth_error = _get_authenticated_requester()
        if auth_error:
            message, status_code = auth_error
            return jsonify({"error": message}), status_code
        role = _normalize_user_role(requester[3])
        is_global, viewer_dept = _events_viewer_scope(requester_id, role)
        count = db.count_unread_events(requester_id, viewer_dept, is_global)
        response = jsonify({"status": "success", "count": int(count)})
        response.headers['Cache-Control'] = 'no-store'
        return response, 200

    @bp.route('/events/<int:event_id>/like', methods=['POST', 'OPTIONS'])
    @require_auth
    def api_event_like(event_id):
        requester_id, requester, auth_error = _get_authenticated_requester()
        if auth_error:
            message, status_code = auth_error
            return jsonify({"error": message}), status_code
        role = _normalize_user_role(requester[3])
        info = db.get_event_author_and_departments(event_id)
        if not info:
            return jsonify({"error": "Ивент не найден"}), 404
        _author_id, dept_ids = info
        is_global, viewer_dept = _events_viewer_scope(requester_id, role)
        if not _events_can_view(dept_ids, is_global, viewer_dept):
            return jsonify({"error": "Ивент не найден"}), 404
        result = db.toggle_event_like(event_id, requester_id)
        return jsonify({"status": "success", **result}), 200

    @bp.route('/events/<int:event_id>/comments', methods=['GET', 'POST', 'OPTIONS'])
    @require_auth
    def api_event_comments(event_id):
        requester_id, requester, auth_error = _get_authenticated_requester()
        if auth_error:
            message, status_code = auth_error
            return jsonify({"error": message}), status_code
        role = _normalize_user_role(requester[3])
        info = db.get_event_author_and_departments(event_id)
        if not info:
            return jsonify({"error": "Ивент не найден"}), 404
        event_author_id, dept_ids = info
        is_global, viewer_dept = _events_viewer_scope(requester_id, role)
        if not _events_can_view(dept_ids, is_global, viewer_dept):
            return jsonify({"error": "Ивент не найден"}), 404

        if request.method == 'GET':
            before_raw = request.args.get('before')
            try:
                before_id = int(before_raw) if before_raw not in (None, '', 'null') else None
            except (TypeError, ValueError):
                before_id = None
            try:
                limit = int(request.args.get('limit') or 20)
            except (TypeError, ValueError):
                limit = 20
            result = db.list_event_comments(event_id, before_id, limit)
            # Модераторский статус одинаков для всех комментов поста — считаем 1 раз.
            viewer_is_moderator = _events_is_moderator_for(requester_id, role, dept_ids, viewer_dept)
            return jsonify({
                "status": "success",
                "comments": [
                    _events_comment_payload(c, requester_id, role, event_author_id, dept_ids, viewer_is_moderator)
                    for c in result["comments"]
                ],
                "has_more": result["has_more"],
                "next_before": result["next_before"],
            }), 200

        payload = request.get_json(silent=True) or {}
        body = str(payload.get('body') or '').strip()[:EVENTS_COMMENT_MAX_CHARS]
        if not body:
            return jsonify({"error": "Введите комментарий"}), 400
        comment = db.add_event_comment(event_id, requester_id, body)
        return jsonify({
            "status": "success",
            "comment": _events_comment_payload(comment, requester_id, role, event_author_id, dept_ids),
        }), 201

    @bp.route('/events/<int:event_id>/comments/<int:comment_id>', methods=['DELETE', 'OPTIONS'])
    @require_auth
    def api_event_comment_delete(event_id, comment_id):
        requester_id, requester, auth_error = _get_authenticated_requester()
        if auth_error:
            message, status_code = auth_error
            return jsonify({"error": message}), status_code
        role = _normalize_user_role(requester[3])
        comment = db.get_event_comment(comment_id)
        if not comment or int(comment["event_id"]) != int(event_id):
            return jsonify({"error": "Комментарий не найден"}), 404
        info = db.get_event_author_and_departments(event_id)
        if not info:
            return jsonify({"error": "Ивент не найден"}), 404
        event_author_id, dept_ids = info
        owner = comment.get("user_id")
        allowed = (
            (owner is not None and int(owner) == int(requester_id))
            or (event_author_id is not None and int(event_author_id) == int(requester_id))
            or _events_is_moderator_for(requester_id, role, dept_ids)
        )
        if not allowed:
            return jsonify({"error": "Недостаточно прав"}), 403
        db.delete_event_comment(comment_id)
        return jsonify({"status": "success", "deleted_id": comment_id}), 200

    @bp.route('/events/<int:event_id>', methods=['DELETE', 'OPTIONS'])
    @require_auth
    def api_event_delete(event_id):
        requester_id, requester, auth_error = _get_authenticated_requester()
        if auth_error:
            message, status_code = auth_error
            return jsonify({"error": message}), status_code
        role = _normalize_user_role(requester[3])
        info = db.get_event_author_and_departments(event_id)
        if not info:
            return jsonify({"error": "Ивент не найден"}), 404
        author_id, dept_ids = info
        can_delete = (
            (author_id is not None and int(author_id) == int(requester_id))
            or _events_is_moderator_for(requester_id, role, dept_ids)
        )
        if not can_delete:
            return jsonify({"error": "Недостаточно прав"}), 403
        blobs = db.delete_event(event_id)
        if blobs is None:
            return jsonify({"error": "Ивент не найден"}), 404
        _delete_events_blobs(blobs)
        return jsonify({"status": "success", "deleted_id": event_id}), 200

    @bp.route('/events/<int:event_id>/pin', methods=['POST', 'OPTIONS'])
    @require_auth
    def api_event_pin(event_id):
        """Закрепить/открепить пост. Право — только модератора, не автора.

    Осознанно строже удаления: свой пост автор вправе убрать, но поднять его
    над чужими поверх всей ленты — решение уровня отдела, а не автора.
    """
        requester_id, requester, auth_error = _get_authenticated_requester()
        if auth_error:
            message, status_code = auth_error
            return jsonify({"error": message}), status_code

        info = db.get_event_author_and_departments(event_id)
        if not info:
            return jsonify({"error": "Ивент не найден"}), 404
        _author_id, dept_ids = info

        role = _normalize_user_role(requester[3])
        if not _events_is_moderator_for(requester_id, role, dept_ids):
            return jsonify({"error": "Недостаточно прав"}), 403

        payload = request.get_json(silent=True) or {}
        pinned = db.set_event_pinned(event_id, bool(payload.get('pinned', True)))
        if pinned is None:
            return jsonify({"error": "Ивент не найден"}), 404
        return jsonify({"status": "success", "id": event_id, "is_pinned": pinned}), 200

    return bp
