"""Раздел «it_tickets»: ручки /api/it_tickets — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_it_tickets_blueprint,
как у остальных пакетов репозитория."""
from flask import Blueprint, g, jsonify, request
from ai_feedback.service import generate_it_ticket_with_ai
from common import log_secrets
from database import IT_TICKET_CATALOG
from database import IT_TICKET_DEFAULT_PROFILE
from database import db
from database import resolve_it_ticket_profile
from database import resolve_it_ticket_profile_strict
from datetime import datetime
from zoneinfo import ZoneInfo
import asyncio
import html
import json
import logging
import os
import re
import requests


IT_TICKET_PRIORITY_LABELS = {
    'low': 'Низкий',
    'medium': 'Средний',
    'high': 'Высокий',
    'critical': 'Критический',
}

ICORE_TICKET_IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}

IT_TICKET_ATTACH_MAX_BYTES = 20 * 1024 * 1024  # предел Telegram для загрузки ботом

def _it_channel_public(channel, is_admin=False):
    """Урезает данные канала для не-админов (скрываем chat_id/служебное)."""
    if not channel:
        return None
    public = {
        'id': channel.get('id'),
        'title': channel.get('title') or f"Чат {channel.get('chat_id')}",
        'chat_type': channel.get('chat_type'),
        'username': channel.get('username'),
        'is_active': channel.get('is_active'),
    }
    if is_admin:
        public.update({
            'chat_id': channel.get('chat_id'),
            'source': channel.get('source'),
            'added_by': channel.get('added_by'),
            'last_seen_at': channel.get('last_seen_at'),
            'created_at': channel.get('created_at'),
        })
    return public


def build_it_tickets_blueprint(
    *,
    ALLOWED_ORIGINS,
    ROLE_HIERARCHY,
    _build_cors_preflight_response,
    _department_scope_id_for_requester,
    _get_role_level,
    _has_min_role,
    _headed_department_id,
    _is_admin_role,
    _is_allowed_origin,
    _is_supervisor_role,
    _normalize_management_role,
    _normalize_origin,
    _normalize_user_role,
    _telegram_exception_text,
    _tg_send_message,
    require_api_key,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""
    bp = Blueprint('it_tickets', __name__, url_prefix='/api')

    def _it_ticket_authorize(require_admin=False):
        """Авторизация для маршрутов IT-тикетов.

    Возвращает (requester_id, requester, role, error). error — кортеж (response, code) или None.
    """
        requester_id = getattr(g, 'user_id', None) or request.headers.get('X-User-Id')
        if not requester_id:
            return None, None, None, (jsonify({"error": "Unauthorized"}), 401)
        try:
            requester_id = int(requester_id)
        except (TypeError, ValueError):
            return None, None, None, (jsonify({"error": "Invalid user id"}), 400)
        requester = db.get_user(id=requester_id)
        if not requester:
            return None, None, None, (jsonify({"error": "User not found"}), 404)
        role = _normalize_management_role(requester[3])
        is_admin = _is_admin_role(role)
        allowed = is_admin or _is_supervisor_role(role) or (_headed_department_id(requester_id) is not None)
        if require_admin and not is_admin:
            return None, None, None, (jsonify({"error": "Only admin can manage IT-ticket channels"}), 403)
        if not allowed:
            return None, None, None, (jsonify({"error": "Forbidden"}), 403)
        return requester_id, requester, role, None

    def _resolve_it_ticket_profile_for_requester(requester_id):
        try:
            dept_id = _headed_department_id(requester_id) or _department_scope_id_for_requester(requester_id)
            if dept_id:
                dept = db.get_department_by_id(int(dept_id))
                if dept:
                    return resolve_it_ticket_profile(dept.get('code'), dept.get('name')), dept
        except Exception:
            logging.debug("IT-ticket profile resolution failed", exc_info=True)
        return IT_TICKET_DEFAULT_PROFILE, None

    def _strict_it_ticket_profile_for_requester(requester_id):
        """СТРОГИЙ профиль главы отдела для проверки прав (op/szov или None).

    В отличие от _resolve_it_ticket_profile_for_requester (который для UI-дефолта
    откатывается на 'op'), здесь без fallback: если отдел не сопоставляется однозначно
    с IT-профилем — возвращаем None, чтобы глава непрофильного отдела не получил прав
    на чужой профиль.
    """
        try:
            dept_id = _headed_department_id(requester_id) or _department_scope_id_for_requester(requester_id)
            if dept_id:
                dept = db.get_department_by_id(int(dept_id))
                if dept:
                    return resolve_it_ticket_profile_strict(dept.get('code'), dept.get('name'))
        except Exception:
            logging.debug("strict IT-ticket profile resolution failed", exc_info=True)
        return None

    def _it_ticket_can_edit_instructions(requester_id, role):
        """Инструкции к ИИ редактируют только админы и главы профильных отделов."""
        if _is_admin_role(role):
            return True
        return _strict_it_ticket_profile_for_requester(requester_id) in ('op', 'szov')

    def _it_ticket_editable_profiles(requester_id, role):
        """Какие профили инструкций может редактировать запросивший.

    Админ — общие + оба профиля. Глава отдела — ТОЛЬКО профиль своего отдела
    (строгое сопоставление; непрофильный отдел → []). Общие инструкции — только админ.
    """
        if _is_admin_role(role):
            return ['common', 'op', 'szov']
        profile = _strict_it_ticket_profile_for_requester(requester_id)
        return [profile] if profile in ('op', 'szov') else []

    def _it_ticket_pinnable_profiles(requester_id, role):
        """Кто может закреплять канал: админ — оба профиля, глава отдела — только свой (строго)."""
        if _is_admin_role(role):
            return ['op', 'szov']
        profile = _strict_it_ticket_profile_for_requester(requester_id)
        return [profile] if profile in ('op', 'szov') else []

    def _tg_get_chat(chat_id):
        token = os.getenv('BOT_TOKEN')
        if not token:
            return None, "BOT_TOKEN не настроен"
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{token}/getChat",
                params={"chat_id": chat_id},
                timeout=10,
            )
            data = resp.json()
            if not data.get("ok"):
                return None, data.get("description") or f"HTTP {resp.status_code}"
            return data.get("result") or {}, None
        except Exception as e:
            return None, _telegram_exception_text(e)

    def _tg_send_attachment(chat_id, file_storage, reply_to_message_id=None, caption=None):
        """Отправляет вложение заявки в чат. Возвращает (result, error).

    Скриншоты уходят как photo (сразу видны в чате), остальное — как document,
    чтобы не терять качество и расширение. Файл прикрепляем ОТВЕТОМ к сообщению
    заявки: подпись у медиа ограничена 1024 символами, а текст тикета длиннее.
    """
        token = os.getenv('BOT_TOKEN')
        if not token:
            return None, "BOT_TOKEN не настроен"
        name = os.path.basename(str(getattr(file_storage, 'filename', '') or 'attachment'))
        ext = os.path.splitext(name)[1].lower()
        as_photo = ext in ICORE_TICKET_IMAGE_EXT
        method = 'sendPhoto' if as_photo else 'sendDocument'
        field = 'photo' if as_photo else 'document'
        data = {"chat_id": chat_id}
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id
        if caption:
            data["caption"] = caption[:1000]
        try:
            file_storage.stream.seek(0)
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/{method}",
                data=data,
                files={field: (name, file_storage.stream, file_storage.mimetype or 'application/octet-stream')},
                timeout=60,
            )
            payload = resp.json()
            if not payload.get("ok"):
                return None, payload.get("description") or f"HTTP {resp.status_code}"
            return payload.get("result") or {}, None
        except Exception as e:
            return None, _telegram_exception_text(e)

    @bp.route('/it_tickets/catalog', methods=['GET'])
    @require_api_key
    def it_ticket_catalog():
        try:
            requester_id, requester, role, err = _it_ticket_authorize()
            if err:
                return err
            profile, dept = _resolve_it_ticket_profile_for_requester(requester_id)
            pinned_raw = db.get_it_ticket_pinned_channels()
            # Скрываем chat_id из публичного ответа — клиенту он не нужен
            pinned = {}
            for prof_key, info in (pinned_raw or {}).items():
                if not info:
                    continue
                pinned[prof_key] = {
                    'channel_id': info.get('channel_id'),
                    'channel_title': info.get('channel_title'),
                    'channel_is_active': info.get('channel_is_active'),
                    'pinned_by_name': info.get('pinned_by_name'),
                    'pinned_at': info.get('pinned_at'),
                }
            return jsonify({
                "status": "success",
                "default_profile": profile,
                "department": {"code": (dept or {}).get('code'), "name": (dept or {}).get('name')} if dept else None,
                "catalog": db.get_it_ticket_catalog(),
                "priorities": IT_TICKET_PRIORITY_LABELS,
                "pinned": pinned,
                "pinnable_profiles": _it_ticket_pinnable_profiles(requester_id, role),
                "catalog_editable_profiles": _it_ticket_pinnable_profiles(requester_id, role),
            }), 200
        except Exception as e:
            logging.error(f"Error fetching IT-ticket catalog: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/it_tickets/catalog', methods=['PUT', 'POST'])
    @require_api_key
    def it_ticket_catalog_update():
        try:
            requester_id, requester, role, err = _it_ticket_authorize()
            if err:
                return err
            # Какие профили вправе менять: админ — оба, глава отдела — только свой
            editable = _it_ticket_pinnable_profiles(requester_id, role)
            if not editable:
                return jsonify({"error": "Редактировать каталог может админ или глава отдела"}), 403

            payload = request.get_json(silent=True) or {}
            catalog = payload.get('catalog')
            if not isinstance(catalog, dict):
                return jsonify({"error": "Передайте каталог объектом"}), 400

            # Мёржим: меняем только разрешённые профили, остальные берём из текущего каталога
            current = db.get_it_ticket_catalog()
            merged = {}
            for prof in ('op', 'szov'):
                if prof in editable and isinstance(catalog.get(prof), dict):
                    merged[prof] = catalog[prof]
                else:
                    merged[prof] = current.get(prof)

            saved = db.set_it_ticket_catalog(merged, requester_id)
            return jsonify({"status": "success", "catalog": saved}), 200
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error updating IT-ticket catalog: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/it_tickets/pinned', methods=['PUT', 'POST'])
    @require_api_key
    def it_ticket_pin_channel():
        try:
            requester_id, requester, role, err = _it_ticket_authorize()
            if err:
                return err
            payload = request.get_json(silent=True) or {}
            profile = str(payload.get('profile') or '').strip().lower()
            if profile not in ('op', 'szov'):
                return jsonify({"error": "Неизвестный профиль"}), 400
            if profile not in _it_ticket_pinnable_profiles(requester_id, role):
                return jsonify({"error": "Закреплять канал может только админ или глава отдела (для своего профиля)"}), 403

            channel_id = payload.get('channel_id')
            if channel_id is not None and str(channel_id).strip() != '':
                channel = db.get_it_ticket_channel(channel_id)
                if not channel:
                    return jsonify({"error": "Канал не найден"}), 404
                if not channel.get('is_active'):
                    return jsonify({"error": "Нельзя закрепить отключённый канал"}), 400

            item = db.set_it_ticket_pinned_channel(profile, channel_id, requester_id)
            # Прячем chat_id из ответа
            if item:
                item = {
                    'profile': item.get('profile'),
                    'channel_id': item.get('channel_id'),
                    'channel_title': item.get('channel_title'),
                    'channel_is_active': item.get('channel_is_active'),
                    'pinned_by_name': item.get('pinned_by_name'),
                    'pinned_at': item.get('pinned_at'),
                }
            return jsonify({"status": "success", "item": item}), 200
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error pinning IT-ticket channel: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/it_tickets/channels', methods=['GET', 'OPTIONS'])
    @require_api_key
    def it_ticket_channels_list():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        try:
            requester_id, requester, role, err = _it_ticket_authorize()
            if err:
                return err
            is_admin = _is_admin_role(role)
            include_all = is_admin and str(request.args.get('all') or '').strip() in ('1', 'true', 'yes')
            channels = db.list_it_ticket_channels(active_only=not include_all)
            items = [_it_channel_public(ch, is_admin=is_admin) for ch in channels]
            return jsonify({"status": "success", "items": items, "can_manage": is_admin}), 200
        except Exception as e:
            logging.error(f"Error listing IT-ticket channels: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/it_tickets/channels', methods=['POST'])
    @require_api_key
    def it_ticket_channels_add():
        try:
            requester_id, requester, role, err = _it_ticket_authorize(require_admin=True)
            if err:
                return err
            payload = request.get_json(silent=True) or {}
            raw_chat_id = payload.get('chat_id')
            if raw_chat_id is None or str(raw_chat_id).strip() == '':
                return jsonify({"error": "Укажите chat_id чата/канала"}), 400
            chat_id_str = str(raw_chat_id).strip()
            try:
                chat_id = int(chat_id_str)
            except ValueError:
                return jsonify({"error": "chat_id должен быть числом (например, -1001234567890)"}), 400

            chat, chat_err = _tg_get_chat(chat_id)
            if chat is None:
                return jsonify({"error": f"Бот не видит этот чат: {chat_err}. Убедитесь, что бот добавлен в чат."}), 400

            title = chat.get('title') or chat.get('username') or chat.get('first_name')
            item = db.add_it_ticket_channel(
                chat_id=chat_id,
                title=title,
                chat_type=chat.get('type'),
                username=chat.get('username'),
                added_by=requester_id,
                source='manual',
            )
            return jsonify({"status": "success", "item": _it_channel_public(item, is_admin=True)}), 201
        except Exception as e:
            logging.error(f"Error adding IT-ticket channel: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/it_tickets/channels/<int:channel_id>', methods=['PATCH', 'DELETE', 'OPTIONS'])
    @require_api_key
    def it_ticket_channel_modify(channel_id):
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        try:
            requester_id, requester, role, err = _it_ticket_authorize(require_admin=True)
            if err:
                return err
            if request.method == 'DELETE':
                ok = db.delete_it_ticket_channel(channel_id)
                if not ok:
                    return jsonify({"error": "Канал не найден"}), 404
                return jsonify({"status": "success", "message": "Канал удалён"}), 200
            payload = request.get_json(silent=True) or {}
            is_active = payload.get('is_active')
            if is_active is None:
                return jsonify({"error": "Передайте is_active"}), 400
            item = db.set_it_ticket_channel_active(channel_id, bool(is_active))
            if not item:
                return jsonify({"error": "Канал не найден"}), 404
            return jsonify({"status": "success", "item": _it_channel_public(item, is_admin=True)}), 200
        except Exception as e:
            logging.error(f"Error modifying IT-ticket channel {channel_id}: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/it_tickets/channels/<int:channel_id>/test', methods=['POST'])
    @require_api_key
    def it_ticket_channel_test(channel_id):
        try:
            requester_id, requester, role, err = _it_ticket_authorize(require_admin=True)
            if err:
                return err
            channel = db.get_it_ticket_channel(channel_id)
            if not channel:
                return jsonify({"error": "Канал не найден"}), 404
            text = (
                "🎫 <b>Проверка канала IT-заявок</b>\n"
                f"Бот успешно отправляет сообщения в этот чат.\n"
                f"<i>Инициатор: {html.escape(str(requester[2] or ''))}</i>"
            )
            result, send_err = _tg_send_message(channel['chat_id'], text, parse_mode='HTML')
            if result is None:
                return jsonify({"error": f"Не удалось отправить тестовое сообщение: {send_err}"}), 502
            return jsonify({"status": "success", "message_id": result.get('message_id')}), 200
        except Exception as e:
            logging.error(f"Error testing IT-ticket channel {channel_id}: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/it_tickets/ai', methods=['POST'])
    @require_api_key
    def it_ticket_ai_assist():
        try:
            requester_id, requester, role, err = _it_ticket_authorize()
            if err:
                return err
            payload = request.get_json(silent=True) or {}
            mode = str(payload.get('mode') or 'draft').strip().lower()
            if mode not in ('draft', 'finalize'):
                mode = 'draft'

            profile = str(payload.get('profile') or '').strip().lower()
            if profile not in IT_TICKET_CATALOG:
                profile, _dept = _resolve_it_ticket_profile_for_requester(requester_id)

            _resolved_profile, dept = _resolve_it_ticket_profile_for_requester(requester_id)
            try:
                now_text = datetime.now(ZoneInfo('Asia/Almaty')).strftime('%Y-%m-%d %H:%M')
            except Exception:
                now_text = datetime.now().strftime('%Y-%m-%d %H:%M')

            ai_payload = {
                "profile": profile,
                "category": payload.get('category'),
                "subcategory": payload.get('subcategory'),
                "description": payload.get('description'),
                "fields": payload.get('fields') if isinstance(payload.get('fields'), dict) else {},
                "answers": payload.get('answers') if isinstance(payload.get('answers'), dict) else {},
                "mass_context": str(payload.get('mass_context') or '').strip()[:600],
                "context": {
                    "reporter_name": requester[2],
                    "role": requester[3],
                    "department": (dept or {}).get('name'),
                    "now": now_text,
                },
            }

            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(
                    generate_it_ticket_with_ai(mode, ai_payload), loop
                )
                # Запас под перебор цепочки моделей (3 × 20с + сеть)
                result = fut.result(timeout=75)
            else:
                result = asyncio.run(generate_it_ticket_with_ai(mode, ai_payload))

            if result is None:
                return jsonify({"status": "error", "error": "ai_failed"}), 502
            if isinstance(result, dict) and result.get('error'):
                return jsonify({"status": "error", "error": result.get('error')}), 200
            return jsonify({"status": "success", "result": result}), 200
        except Exception as e:
            logging.error(f"Error in IT-ticket AI assist: {e}", exc_info=True)
            return jsonify({"status": "error", "error": "Internal server error"}), 500

    @bp.route('/it_tickets/send', methods=['POST'])
    @require_api_key
    def it_ticket_send():
        try:
            requester_id, requester, role, err = _it_ticket_authorize()
            if err:
                return err

            # Со вложением заявка приходит multipart, без него — обычным JSON (как раньше).
            attachment = request.files.get('attachment')
            if attachment is not None and not str(attachment.filename or '').strip():
                attachment = None
            if attachment is not None or request.form:
                payload = dict(request.form)
                try:
                    payload['fields'] = json.loads(payload.get('fields') or '{}')
                except (TypeError, ValueError):
                    payload['fields'] = {}
            else:
                payload = request.get_json(silent=True) or {}

            if attachment is not None:
                attachment.stream.seek(0, os.SEEK_END)
                size = attachment.stream.tell()
                attachment.stream.seek(0)
                if size == 0:
                    attachment = None
                elif size > IT_TICKET_ATTACH_MAX_BYTES:
                    return jsonify({"error": "Файл больше 20 МБ — Telegram его не примет"}), 400

            body = str(payload.get('body') or '').strip()
            if not body:
                return jsonify({"error": "Пустой текст заявки"}), 400

            profile = str(payload.get('profile') or '').strip().lower()
            if profile not in IT_TICKET_CATALOG:
                profile = IT_TICKET_DEFAULT_PROFILE

            # Канал берётся из закреплённого админом/главой отдела для профиля,
            # а не выбирается отправителем — так гарантируется единый адрес заявок.
            channel = db.get_it_ticket_pinned_channel(profile)
            if not channel:
                return jsonify({"error": "Канал для отправки не закреплён. Обратитесь к администратору или главе отдела."}), 400

            category = str(payload.get('category') or '').strip()
            subcategory = str(payload.get('subcategory') or '').strip()
            priority = str(payload.get('priority') or '').strip().lower()
            if priority not in IT_TICKET_PRIORITY_LABELS:
                priority = 'medium'
            title = str(payload.get('title') or '').strip()[:480]
            fields = payload.get('fields') if isinstance(payload.get('fields'), dict) else {}

            _resolved_profile, dept = _resolve_it_ticket_profile_for_requester(requester_id)
            try:
                now_text = datetime.now(ZoneInfo('Asia/Almaty')).strftime('%d.%m.%Y %H:%M')
            except Exception:
                now_text = datetime.now().strftime('%d.%m.%Y %H:%M')

            cat_line_parts = [p for p in [category, subcategory] if p]
            cat_line = " → ".join(cat_line_parts)
            priority_label = IT_TICKET_PRIORITY_LABELS.get(priority, priority)
            priority_emoji = {'low': '🟢', 'medium': '🟡', 'high': '🟠', 'critical': '🔴'}.get(priority, '⚪')

            # Шапка: заголовок, затем приоритет/тема — с воздухом между блоками для читабельности
            header_lines = [f"🎫 <b>Заявка в IT-отдел</b>", ""]
            header_lines.append(f"{priority_emoji} <b>Приоритет:</b> {html.escape(priority_label)}")
            if cat_line:
                header_lines.append(f"🗂 <b>Тема:</b> {html.escape(cat_line)}")

            sig_parts = [str(requester[2] or '')]
            if (dept or {}).get('name'):
                sig_parts.append(dept['name'])
            sig_parts.append(now_text)
            footer = " · ".join([p for p in sig_parts if p])

            message_text = (
                "\n".join(header_lines)
                + "\n\n" + body.strip()
                + f"\n\n<i>🙍 Отправил: {html.escape(footer)}</i>"
            )
            message_text = message_text[:4050]

            result, send_err = _tg_send_message(channel['chat_id'], message_text, parse_mode='HTML')
            if result is None:
                # Повтор без HTML-разметки (на случай некорректных тегов в тексте)
                plain = re.sub(r'<[^>]+>', '', message_text)
                result, send_err = _tg_send_message(channel['chat_id'], plain, parse_mode=None)
            if result is None:
                try:
                    db.record_it_ticket(
                        profile=profile, category=category, subcategory=subcategory, priority=priority,
                        title=title, body=body, fields=fields, channel_id=channel.get('id'),
                        channel_chat_id=channel.get('chat_id'), channel_title=channel.get('title'),
                        telegram_message_id=None, status='failed',
                        created_by=requester_id, created_by_name=requester[2],
                    )
                except Exception:
                    logging.debug("Failed to record failed IT ticket", exc_info=True)
                return jsonify({"error": f"Не удалось отправить заявку в Telegram: {send_err}"}), 502

            message_id = result.get('message_id')

            # Вложение уходит ответом к заявке. Если не прошло — саму заявку уже отправили,
            # откатывать нечего: сообщаем об этом отдельно, а не роняем всю отправку.
            attach_error = None
            if attachment is not None:
                _attach_result, attach_error = _tg_send_attachment(
                    channel['chat_id'], attachment, reply_to_message_id=message_id,
                    caption='📎 Вложение к заявке',
                )
                if attach_error:
                    logging.warning("IT ticket attachment not sent: %s", attach_error)

            ticket = db.record_it_ticket(
                profile=profile, category=category, subcategory=subcategory, priority=priority,
                title=title, body=body, fields=fields, channel_id=channel.get('id'),
                channel_chat_id=channel.get('chat_id'), channel_title=channel.get('title'),
                telegram_message_id=message_id, status='sent',
                created_by=requester_id, created_by_name=requester[2],
            )
            return jsonify({
                "status": "success",
                "ticket_id": ticket.get('id'),
                "message_id": message_id,
                "channel_title": channel.get('title'),
                "attachment_error": attach_error,
            }), 201
        except Exception as e:
            logging.error(f"Error sending IT ticket: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/it_tickets', methods=['GET'])
    @require_api_key
    def it_ticket_history():
        try:
            requester_id, requester, role, err = _it_ticket_authorize()
            if err:
                return err
            try:
                limit = max(1, min(int(request.args.get('limit', 50)), 200))
            except (TypeError, ValueError):
                limit = 50
            try:
                offset = max(0, int(request.args.get('offset', 0)))
            except (TypeError, ValueError):
                offset = 0
            items = db.list_it_tickets(limit=limit, offset=offset)
            return jsonify({"status": "success", "items": items}), 200
        except Exception as e:
            logging.error(f"Error listing IT tickets: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/it_tickets/instructions', methods=['GET', 'OPTIONS'])
    @require_api_key
    def it_ticket_instructions_list():
        if request.method == 'OPTIONS':
            return _build_cors_preflight_response()
        try:
            requester_id, requester, role, err = _it_ticket_authorize()
            if err:
                return err
            if not _it_ticket_can_edit_instructions(requester_id, role):
                return jsonify({"error": "Только админ или глава отдела могут просматривать инструкции"}), 403
            editable = _it_ticket_editable_profiles(requester_id, role)
            items = db.get_it_ticket_instructions()
            return jsonify({
                "status": "success",
                "items": items,
                "editable_profiles": editable,
            }), 200
        except Exception as e:
            logging.error(f"Error listing IT-ticket instructions: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/it_tickets/instructions', methods=['PUT', 'POST'])
    @require_api_key
    def it_ticket_instructions_update():
        try:
            requester_id, requester, role, err = _it_ticket_authorize()
            if err:
                return err
            if not _it_ticket_can_edit_instructions(requester_id, role):
                return jsonify({"error": "Только админ или глава отдела могут редактировать инструкции"}), 403

            payload = request.get_json(silent=True) or {}
            profile = str(payload.get('profile') or '').strip().lower()
            if profile not in ('common', 'op', 'szov'):
                return jsonify({"error": "Неизвестный профиль инструкций"}), 400

            editable = _it_ticket_editable_profiles(requester_id, role)
            if profile not in editable:
                return jsonify({"error": "Недостаточно прав для редактирования этого профиля"}), 403

            instructions = payload.get('instructions')
            if instructions is None:
                return jsonify({"error": "Передайте текст инструкций"}), 400
            if len(str(instructions)) > 8000:
                return jsonify({"error": "Слишком длинный текст инструкций (макс. 8000 символов)"}), 400

            item = db.set_it_ticket_instructions(profile, instructions, requester_id)
            return jsonify({"status": "success", "item": item}), 200
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logging.error(f"Error updating IT-ticket instructions: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    return bp
