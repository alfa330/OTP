"""Раздел «user_profile»: ручки /api/user — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_user_profile_blueprint,
как у остальных пакетов репозитория."""
from flask import Blueprint, g, jsonify, request
from database import db
from datetime import datetime
from datetime import timedelta
from google.cloud import storage as gcs_storage
from werkzeug.utils import secure_filename
import logging
import os
import re
import requests
import threading
import time
import uuid

from .avatar_image import CONTENT_TYPE as AVATAR_CONTENT_TYPE, AvatarImageError, avatar_to_webp


AVATAR_MAX_UPLOAD_BYTES = int(os.getenv('AVATAR_MAX_UPLOAD_BYTES', str(2 * 1024 * 1024)))

AVATAR_MAX_ORIGINAL_UPLOAD_BYTES = int(os.getenv('AVATAR_MAX_ORIGINAL_UPLOAD_BYTES', str(10 * 1024 * 1024)))

AVATAR_UPLOAD_FOLDER = (os.getenv('AVATAR_UPLOAD_FOLDER') or 'AvatarUploads/').strip()

AVATAR_THUMBNAIL_SUFFIX = (os.getenv('AVATAR_THUMBNAIL_SUFFIX') or '128').strip() or '128'

# Фото профиля после avatar_to_webp — всегда WebP, и расширение в бакете
# берётся из него, а не из имени, которое прислал браузер.
AVATAR_FILE_NAME = 'avatar.webp'

def _sanitize_avatar_extension(source_filename, fallback='.webp'):
    safe_name = secure_filename(source_filename or "")
    extension = os.path.splitext(safe_name)[1].lower()
    if not extension or not re.match(r'^\.[a-z0-9]{1,10}$', extension):
        return fallback
    return extension

def _build_avatar_blob_paths(user_id, thumbnail_filename, original_filename=None):
    upload_folder = AVATAR_UPLOAD_FOLDER.strip('/')
    upload_prefix = f"{upload_folder}/" if upload_folder else ""
    thumbnail_ext = _sanitize_avatar_extension(thumbnail_filename, '.webp')
    original_ext = _sanitize_avatar_extension(original_filename or thumbnail_filename, thumbnail_ext)
    date_prefix = datetime.utcnow().strftime('%Y/%m/%d')
    random_id = uuid.uuid4().hex
    base_prefix = f"{upload_prefix}avatars/{int(user_id)}/{date_prefix}/{random_id}"
    return {
        "thumbnail_path": f"{base_prefix}_{AVATAR_THUMBNAIL_SUFFIX}{thumbnail_ext}",
        "original_path": f"{base_prefix}_original{original_ext}"
    }


def build_user_profile_blueprint(
    *,
    ALLOWED_ORIGINS,
    API_TOKEN,
    AVATAR_SIGNED_URL_CACHE,
    AVATAR_SIGNED_URL_CACHE_LOCK,
    AVATAR_SIGNED_URL_CACHE_MAX_ITEMS,
    AVATAR_SIGNED_URL_CACHE_MIN_REMAINING_SECONDS,
    AVATAR_SIGNED_URL_TTL_SECONDS,
    ROLE_HIERARCHY,
    _build_avatar_signed_url,
    _build_cors_preflight_response,
    _get_authenticated_requester,
    _get_role_level,
    _has_min_role,
    _headed_department_id,
    _is_admin_role,
    _is_allowed_origin,
    _is_employee_accounting_manager,
    _is_super_admin_role,
    _is_supervisor_role,
    _load_target_user_with_scope,
    _normalize_origin,
    _normalize_user_role,
    _requester_can_access_target_user,
    get_gcs_client,
    require_api_key,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""
    bp = Blueprint('user_profile', __name__, url_prefix='/api')

    def _delete_avatar_blobs(bucket_name, *blob_paths):
        bucket_name = (bucket_name or '').strip()
        unique_paths = []
        for item in blob_paths:
            path = (item or '').strip()
            if path and path not in unique_paths:
                unique_paths.append(path)
        if not bucket_name or not unique_paths:
            return
        try:
            client = get_gcs_client()
            bucket = client.bucket(bucket_name)
        except Exception:
            return
        for blob_path in unique_paths:
            try:
                bucket.blob(blob_path).delete()
            except Exception:
                pass

    def _resolve_avatar_target_user(requester, requester_id, target_user_id):
        target_user = db.get_user(id=target_user_id)
        if not target_user:
            return None, ("User not found", 404)
        requester_role = _normalize_user_role(requester[3])
        if requester_id == target_user[0]:
            return target_user, None
        # Кадровик ведёт карточки всей компании — фотография её часть.
        if _is_employee_accounting_manager(requester_id):
            return target_user, None
        headed_dept_id = _headed_department_id(requester_id)
        if headed_dept_id is not None and not _is_super_admin_role(requester_role):
            if _requester_can_access_target_user(
                requester,
                requester_id,
                target_user,
                allow_self=False,
                supervisor_target_roles=('operator', 'trainee', 'trainer', 'sv')
            ):
                return target_user, None
            return None, ("You do not have access to manage this avatar", 403)
        if _is_admin_role(requester_role):
            return target_user, None
        if _is_supervisor_role(requester_role) and _normalize_user_role(target_user[3]) == 'operator' and target_user[6] == requester_id:
            return target_user, None
        return None, ("You do not have access to manage this avatar", 403)

    @bp.route('/user/profile', methods=['GET'])
    @require_api_key
    def get_user_profile():
        try:
            user_id = request.args.get('user_id')
            if not user_id:
                logging.warning("Missing user_id parameter in profile request")
                return jsonify({"error": "Missing user_id parameter"}), 400

            try:
                user_id = int(user_id)
            except ValueError:
                logging.error(f"Invalid user_id format: {user_id}")
                return jsonify({"error": "Invalid user_id format"}), 400

            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            user, scope_error = _load_target_user_with_scope(
                requester,
                requester_id,
                user_id,
                allow_self=True,
                supervisor_target_roles=('operator', 'trainee'),
                not_found_message="User not found",
                forbidden_message="Unauthorized to view this profile"
            )
            if scope_error:
                message, status_code = scope_error
                logging.warning("Blocked profile access: requester=%s target=%s error=%s", requester_id, user_id, message)
                return jsonify({"error": message}), status_code

            supervisor_name = None
            if user[6]:
                supervisor = db.get_user(id=user[6])
                supervisor_name = supervisor[2] if supervisor else None

            hire_date = user[5].strftime('%Y-%m-%d') if user[5] else None
            requester_role = _normalize_user_role(requester[3])
            can_view_contact_handles = _is_admin_role(requester_role) or requester_id == int(user[0])

            profile_data = {
                "id": user[0],
                "name": user[2] or "Unknown",
                "role": user[3] or "Unknown",
                "direction": user[4],  # Now returns direction name from joined directions table
                "hire_date": hire_date,
                "supervisor_name": supervisor_name,
                "telegram_id": user[1] if can_view_contact_handles else None,
                "scores_table_url": user[9] if can_view_contact_handles else None,
                "hours_table_url": user[8] if can_view_contact_handles else None,
                "status": user[11],  # Adjust index based on query
                "rate": float(user[12]) if user[12] else 1.0,
                "avatar_url": _build_avatar_signed_url(user[15], user[16]),
                "avatar_updated_at": user[19].isoformat() if len(user) > 19 and user[19] else None
            }
            # Должность и отдел — карточка бэк-офиса показывает их вместо
            # супервайзера и ставки. Мягко: сбой запроса не должен ронять профиль.
            try:
                profile_data.update(db.get_user_hr_card(user[0]))
            except Exception:
                profile_data.setdefault("job_title", None)
                profile_data.setdefault("department_name", None)
            # Дни длительного Б/С, которые глава решил не считать в стаж (задача
            # #358, п. 8): на них «Стаж» в профиле и стаж в оценке зарплаты
            # начинаются позже даты найма. Сбой — стаж просто по найму, как раньше.
            try:
                profile_data["tenure_excluded_days"] = db.get_tenure_excluded_days(user[0])
            except Exception:
                logging.warning("Не удалось посчитать дни вне стажа для %s", user[0], exc_info=True)
                profile_data["tenure_excluded_days"] = 0

            logging.info(f"Profile data fetched successfully for user_id: {user_id}")
            return jsonify({"status": "success", "profile": profile_data}), 200
        except Exception as e:
            logging.error(f"Error fetching user profile for user_id {user_id}: {e}", exc_info=True)
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/user/avatar', methods=['POST', 'DELETE', 'OPTIONS'])
    @require_api_key
    def manage_user_avatar():
        try:
            if request.method == 'OPTIONS':
                return _build_cors_preflight_response()

            requester_id_raw = request.headers.get('X-User-Id') or getattr(g, 'user_id', None)
            if not requester_id_raw:
                return jsonify({"error": "Missing X-User-Id header"}), 400
            try:
                requester_id = int(requester_id_raw)
            except (TypeError, ValueError):
                return jsonify({"error": "Invalid X-User-Id"}), 400

            requester = db.get_user(id=requester_id)
            if not requester:
                return jsonify({"error": "Requester not found"}), 404

            json_payload = request.get_json(silent=True) or {}
            target_user_id_raw = request.form.get('target_user_id') if request.method == 'POST' else json_payload.get('target_user_id')
            if target_user_id_raw in (None, ''):
                target_user_id = requester_id
            else:
                try:
                    target_user_id = int(target_user_id_raw)
                except (TypeError, ValueError):
                    return jsonify({"error": "Invalid target_user_id"}), 400

            target_user, access_error = _resolve_avatar_target_user(requester, requester_id, target_user_id)
            if access_error:
                message, status_code = access_error
                return jsonify({"error": message}), status_code

            remove_flag_raw = request.form.get('remove') if request.method == 'POST' else json_payload.get('remove')
            remove_flag = request.method == 'DELETE' or str(remove_flag_raw or '').strip().lower() in ('1', 'true', 'yes')
            previous_avatar = db.get_user_avatar_storage(target_user_id) or {}

            if remove_flag:
                if not db.clear_user_avatar(target_user_id):
                    return jsonify({"error": "Failed to clear avatar"}), 500

                prev_bucket = (previous_avatar.get('avatar_bucket') or '').strip()
                prev_blob_path = (previous_avatar.get('avatar_blob_path') or '').strip()
                prev_original_blob_path = (previous_avatar.get('avatar_original_blob_path') or '').strip()
                _delete_avatar_blobs(prev_bucket, prev_blob_path, prev_original_blob_path)

                updated_avatar = db.get_user_avatar_storage(target_user_id) or {}
                return jsonify({
                    "status": "success",
                    "message": "Avatar removed",
                    "target_user_id": target_user[0],
                    "avatar_url": None,
                    "avatar_updated_at": updated_avatar.get("avatar_updated_at")
                }), 200

            file_storage = request.files.get('avatar')
            if not file_storage or not file_storage.filename:
                return jsonify({"error": "Missing avatar file"}), 400

            avatar_bytes = file_storage.read() or b''
            if not avatar_bytes:
                return jsonify({"error": "Uploaded avatar is empty"}), 400
            if len(avatar_bytes) > AVATAR_MAX_UPLOAD_BYTES:
                return jsonify({"error": f"Avatar exceeds {AVATAR_MAX_UPLOAD_BYTES // 1024} KB limit"}), 400

            content_type = (file_storage.mimetype or '').strip().lower()
            if not content_type.startswith('image/'):
                return jsonify({"error": "Only image files are allowed"}), 400
            # Подписи браузера не верим: Safari кладёт в «.webp» PNG. Фото
            # профиля хранится только в WebP — см. user_profile/avatar_image.py.
            try:
                avatar_bytes = avatar_to_webp(avatar_bytes)
            except AvatarImageError as error:
                return jsonify({"error": str(error)}), 400
            content_type = AVATAR_CONTENT_TYPE

            original_file_storage = request.files.get('avatar_original')
            original_bytes = avatar_bytes
            original_content_type = content_type
            original_filename = AVATAR_FILE_NAME
            if original_file_storage and original_file_storage.filename:
                candidate_original_bytes = original_file_storage.read() or b''
                if not candidate_original_bytes:
                    return jsonify({"error": "Uploaded original avatar is empty"}), 400
                if len(candidate_original_bytes) > AVATAR_MAX_ORIGINAL_UPLOAD_BYTES:
                    return jsonify({"error": f"Original avatar exceeds {AVATAR_MAX_ORIGINAL_UPLOAD_BYTES // 1024} KB limit"}), 400
                candidate_original_content_type = (original_file_storage.mimetype or '').strip().lower()
                if not candidate_original_content_type.startswith('image/'):
                    return jsonify({"error": "Only image files are allowed for original avatar"}), 400
                original_bytes = candidate_original_bytes
                original_content_type = candidate_original_content_type
                original_filename = original_file_storage.filename

            bucket_name = (
                os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET_AVATARS')
                or os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET_TASKS')
                or os.getenv('GOOGLE_CLOUD_STORAGE_BUCKET')
                or ''
            ).strip()
            if not bucket_name:
                return jsonify({"error": "Avatar bucket is not configured"}), 500

            avatar_blob_paths = _build_avatar_blob_paths(target_user_id, AVATAR_FILE_NAME, original_filename)
            blob_path = avatar_blob_paths['thumbnail_path']
            original_blob_path = avatar_blob_paths['original_path']
            gcs_client = get_gcs_client()
            gcs_bucket = gcs_client.bucket(bucket_name)
            blob = gcs_bucket.blob(blob_path)
            blob.cache_control = "public, max-age=31536000, immutable"
            blob.upload_from_string(avatar_bytes, content_type=content_type)
            original_blob = gcs_bucket.blob(original_blob_path)
            original_blob.cache_control = "private, max-age=0, no-cache"
            original_blob.upload_from_string(original_bytes, content_type=original_content_type)

            saved_avatar = db.set_user_avatar(
                user_id=target_user_id,
                bucket_name=bucket_name,
                blob_path=blob_path,
                original_blob_path=original_blob_path,
                content_type=content_type,
                file_size=len(avatar_bytes)
            )
            if not saved_avatar:
                _delete_avatar_blobs(bucket_name, blob_path, original_blob_path)
                return jsonify({"error": "Failed to save avatar metadata"}), 500

            prev_bucket = (previous_avatar.get('avatar_bucket') or '').strip()
            prev_blob_path = (previous_avatar.get('avatar_blob_path') or '').strip()
            prev_original_blob_path = (previous_avatar.get('avatar_original_blob_path') or '').strip()
            is_same_thumbnail = prev_bucket == bucket_name and prev_blob_path == blob_path
            is_same_original = prev_bucket == bucket_name and prev_original_blob_path == original_blob_path
            if prev_bucket and (not is_same_thumbnail or not is_same_original):
                _delete_avatar_blobs(prev_bucket, prev_blob_path, prev_original_blob_path)

            return jsonify({
                "status": "success",
                "message": "Avatar updated",
                "target_user_id": target_user[0],
                "avatar_url": _build_avatar_signed_url(saved_avatar.get("avatar_bucket"), saved_avatar.get("avatar_blob_path")),
                "avatar_updated_at": saved_avatar.get("avatar_updated_at"),
                "avatar_file_size": saved_avatar.get("avatar_file_size"),
                "avatar_original_file_size": len(original_bytes)
            }), 200
        except Exception as e:
            logging.error(f"Error in /api/user/avatar: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/user/history', methods=['GET'])
    @require_api_key
    def get_user_history():
        try:
            user_id = request.args.get('user_id')
            if not user_id:
                return jsonify({"error": "Missing user_id parameter"}), 400
            user_id = int(user_id)

            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            target_user = db.get_user(id=user_id)
            if not target_user:
                return jsonify({"error": "User not found"}), 404

            # Permissions: global admin can view any user, a department head can
            # view non-admin employees in the headed department, and a regular SV
            # keeps the legacy direct-report access.
            requester_role = _normalize_user_role(requester[3])
            headed_dept_id = _headed_department_id(requester_id)
            # Кадровик читает историю любого сотрудника: раздел «Учет сотрудников»
            # открыт ему по всей компании, а история — то же чтение, только по
            # одному человеку. Ветка стоит первой и для ГЛАВЫ отдела кадров тоже:
            # иначе он видел бы в списке всю фирму, а «Историю» открывал лишь своим.
            if _is_employee_accounting_manager(requester_id):
                if _is_admin_role(_normalize_user_role(target_user[3])) and not _is_admin_role(requester_role):
                    return jsonify({"error": "Unauthorized to view this user's history"}), 403
            elif headed_dept_id is not None and not _is_super_admin_role(requester_role):
                if not _requester_can_access_target_user(
                    requester,
                    requester_id,
                    target_user,
                    allow_self=True,
                    supervisor_target_roles=('operator', 'trainee', 'trainer', 'sv')
                ):
                    return jsonify({"error": "Unauthorized to view this user's history"}), 403
            elif (not _is_admin_role(requester_role)) and (not _is_supervisor_role(requester_role) or target_user[6] != requester_id):
                return jsonify({"error": "Unauthorized to view this user's history"}), 403

            history = db.get_user_history(user_id)
            return jsonify({"status": "success", "history": history}), 200
        except Exception as e:
            logging.error(f"Error fetching user history: {e}")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/user/change_password', methods=['POST'])
    @require_api_key
    def change_password():
        try:
            data = request.get_json()
            required_fields = ['user_id', 'new_password']
            if not data or not all(field in data for field in required_fields):
                return jsonify({"error": "Missing required fields (user_id, new_password)"}), 400

            user_id = int(data['user_id'])
            new_password = data['new_password']
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            if not new_password or len(new_password) < 6:
                return jsonify({"error": "New password must be at least 6 characters long"}), 400

            target_user = db.get_user(id=user_id)

            if not target_user:
                return jsonify({"error": "User not found"}), 404

            # Authorization checks. A formally assigned department head may manage
            # any non-admin employee in that department regardless of the head's
            # base role; a regular SV — operators of their own department.
            requester_role = _normalize_user_role(requester[3])
            if requester_id != user_id:
                headed_dept_id = _headed_department_id(requester_id)
                # Кадровик меняет учётные данные кому угодно, включая админов:
                # владелец 22.09.2026 на прямой вопрос ответил «без исключений».
                # Ветка первая — глава отдела кадров тоже кадровик, и ветка
                # headed_dept_id ниже заперла бы его своим отделом.
                if _is_employee_accounting_manager(requester_id):
                    pass
                elif headed_dept_id is not None and not _is_super_admin_role(requester_role):
                    if not _requester_can_access_target_user(
                        requester,
                        requester_id,
                        target_user,
                        allow_self=False,
                        supervisor_target_roles=('operator', 'trainee', 'trainer', 'sv')
                    ):
                        return jsonify({"error": "Unauthorized to change this user's password"}), 403
                elif requester_role == 'operator':
                    return jsonify({"error": "Operators can only change their own password"}), 403
                elif requester_role == 'sv':
                    # Задача #380: СВ меняет пароль любому оператору своего отдела, а не только
                    # тем, у кого он записан руководителем. Граница — та же, что у остальных
                    # правок СВ в карточке сотрудника.
                    if not _requester_can_access_target_user(
                        requester,
                        requester_id,
                        target_user,
                        allow_self=False,
                        supervisor_target_roles=('operator',)
                    ):
                        return jsonify({"error": "Супервайзер меняет пароль только операторам своего отдела"}), 403
                elif not _is_admin_role(requester_role):
                    return jsonify({"error": "Unauthorized to change this user's password"}), 403

            # Update password
            success = db.update_user_password(user_id, new_password)
            if not success:
                return jsonify({"error": "Failed to update password"}), 500

            # Notify user via Telegram
            if target_user[1]:  # Check if telegram_id exists
                telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
                payload = {
                    "chat_id": target_user[1],
                    "text": "Ваш пароль был успешно изменён. Если это были не вы, срочно обратитесь к администратору.",
                    "parse_mode": "HTML"
                }
                response = requests.post(telegram_url, json=payload, timeout=10)
                if response.status_code != 200:
                    error_detail = response.json().get('description', 'Unknown error')
                    logging.error(f"Telegram API error: {error_detail}")

            return jsonify({"status": "success", "message": f"Password updated for user {target_user[2]}"})

        except Exception as e:
            logging.error(f"Error changing password: {e}")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/user/change_login', methods=['POST'])
    @require_api_key
    def change_login():
        try:
            data = request.get_json()
            required_fields = ['user_id', 'new_login']
            if not data or not all(field in data for field in required_fields):
                return jsonify({"error": "Missing required fields (user_id, new_login)"}), 400

            user_id = int(data['user_id'])
            new_login = data['new_login']
            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            if not new_login or len(new_login) < 4:
                return jsonify({"error": "New login must be at least 4 characters long"}), 400

            # Check if new login is already taken
            if db.get_user_by_login(new_login):
                return jsonify({"error": "Login is already in use"}), 400

            # Fetch target user; requester comes only from the authenticated JWT context.
            target_user = db.get_user(id=user_id)

            if not target_user:
                return jsonify({"error": "User not found"}), 404

            # Authorization checks. A formally assigned department head may manage
            # any non-admin employee in that department regardless of the head's
            # base role; a regular SV — operators of their own department.
            requester_role = _normalize_user_role(requester[3])
            if requester_id != user_id:
                headed_dept_id = _headed_department_id(requester_id)
                # Кадровик меняет учётные данные кому угодно, включая админов:
                # владелец 22.09.2026 на прямой вопрос ответил «без исключений».
                # Ветка первая — глава отдела кадров тоже кадровик, и ветка
                # headed_dept_id ниже заперла бы его своим отделом.
                if _is_employee_accounting_manager(requester_id):
                    pass
                elif headed_dept_id is not None and not _is_super_admin_role(requester_role):
                    if not _requester_can_access_target_user(
                        requester,
                        requester_id,
                        target_user,
                        allow_self=False,
                        supervisor_target_roles=('operator', 'trainee', 'trainer', 'sv')
                    ):
                        return jsonify({"error": "Unauthorized to change this user's login"}), 403
                elif requester_role == 'operator':
                    return jsonify({"error": "Operators can only change their own login"}), 403
                elif requester_role == 'sv':
                    # Задача #380: как и пароль — любому оператору своего отдела.
                    if not _requester_can_access_target_user(
                        requester,
                        requester_id,
                        target_user,
                        allow_self=False,
                        supervisor_target_roles=('operator',)
                    ):
                        return jsonify({"error": "Супервайзер меняет логин только операторам своего отдела"}), 403
                elif not _is_admin_role(requester_role):
                    return jsonify({"error": "Unauthorized to change this user's login"}), 403

            # Update login
            success = db.update_operator_login(user_id, target_user[6] if requester_role in ('sv', 'admin', 'super_admin') else None, new_login)
            if not success:
                return jsonify({"error": "Failed to update login"}), 500

            # Notify user via Telegram
            if target_user[1]:
                telegram_url = f"https://api.telegram.org/bot{API_TOKEN}/sendMessage"
                payload = {
                    "chat_id": target_user[1],
                    "text": f"Ваш логин был успешно изменён. Новый логин: <b>{new_login}</b>",
                    "parse_mode": "HTML"
                }
                response = requests.post(telegram_url, json=payload, timeout=10)
                if response.status_code != 200:
                    error_detail = response.json().get('description', 'Unknown error')
                    logging.error(f"Telegram API error: {error_detail}")

            return jsonify({"status": "success", "message": f"Login updated for user {target_user[2]}"})

        except Exception as e:
            logging.error(f"Error changing login: {e}")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/user/hours', methods=['GET'])
    @require_api_key
    def get_user_hours():
        try:
            operator_id = request.args.get('operator_id')
            month = request.args.get('month')
            if not operator_id:
                return jsonify({"error": "Missing operator_id parameter"}), 400
            if not month:
                month = datetime.now().strftime('%Y-%m')

            requester_id, requester, auth_error = _get_authenticated_requester()
            if auth_error:
                message, status_code = auth_error
                return jsonify({"error": message}), status_code

            target_user, scope_error = _load_target_user_with_scope(
                requester,
                requester_id,
                operator_id,
                allow_self=True,
                supervisor_target_roles=('operator',),
                not_found_message="Operator not found",
                forbidden_message="Unauthorized to view this operator's hours"
            )
            if scope_error:
                message, status_code = scope_error
                return jsonify({"error": message}), status_code

            operator_id = int(target_user[0])
            hours_summary = db.get_hours_summary(operator_id=operator_id, month=month)

            if not hours_summary:
                return jsonify({"status": "success", "hours": None}), 200

            # Assuming get_hours_summary returns a list, take the first item
            hours_data = hours_summary[0]

            return jsonify({"status": "success", "hours": hours_data}), 200
        except Exception as e:
            logging.error(f"Error fetching hours data: {e}")
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/user/toggle_active', methods=['POST'])
    @require_api_key
    def toggle_user_active():
        try:
            data = request.get_json()
            if not data or 'status' not in data:
                return jsonify({"error": "Missing status field"}), 400

            new_status = data['status']
            allowed_statuses = {"active", "break", "training", "inactive", "tech","iesigning"}
            if new_status not in allowed_statuses:
                return jsonify({"error": f"Invalid status. Allowed: {', '.join(allowed_statuses)}"}), 400

            user_id = int(request.headers.get('X-User-Id'))
            user = db.get_user(id=user_id)
            if not user:
                return jsonify({"error": "Unauthurized"}), 403

            # здесь предполагаем, что user[10] хранит BOOLEAN (is_active)
            current_active = user[10]
            new_active_bool = True if new_status == "active" else False

            # если статус active ↔ True/False не меняется — нет смысла обновлять
            if current_active == new_active_bool:
                # но дополнительно можно проверить, не был ли изменён текстовый статус (например break/training)
                last_log_status = db.get_last_activity_status(user_id)  # нужна функция в db
                if last_log_status == new_status:
                    return jsonify({"status": "unchanged", "message": "Status is already set to the requested value"})

            # обновляем users.is_active (bool)
            success = db.set_user_active(user_id, new_status)
            if not success:
                return jsonify({"error": "Failed to update user active flag"}), 500

            # записываем строковый статус в логи
            log_success = db.log_activity(user_id, new_status)
            if not log_success:
                logging.warning("User active flag updated but logging failed")
                return jsonify({
                    "status": "partial_success",
                    "message": "User active flag updated, but logging failed"
                }), 500

            return jsonify({"status": "success", "message": f"Status updated to '{new_status}'"}), 200

        except Exception as e:
            logging.error(f"Error toggling status: {e}")
            return jsonify({"error": f"Internal server error"}), 500

    return bp
