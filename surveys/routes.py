"""Раздел «surveys»: ручки /api/surveys — вынесено из bot_schedule2.py скриптом scripts/extract_routes.py, 02.11.2026.

Тела ручек и помощников перенесены без изменений (только отступ кода внутри строителя).
Зависимости от процесса монолита приходят аргументами build_surveys_blueprint,
как у остальных пакетов репозитория."""
from flask import Blueprint, g, jsonify, request, send_file
from database import db
from datetime import datetime
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Alignment
from openpyxl.styles import Border
from openpyxl.styles import Font
from openpyxl.styles import PatternFill
from openpyxl.styles import Side
from openpyxl.utils import get_column_letter
import logging
import re


def _survey_test_config_from_payload(data):
    """Настройки теста из тела запроса; валидация — на стороне database.py."""
    data = data or {}
    test_config = data.get('test') if isinstance(data.get('test'), dict) else {}
    return {
        'starts_at': test_config.get('starts_at', data.get('starts_at')),
        'ends_at': test_config.get('ends_at', data.get('ends_at')),
        'single_attempt': test_config.get('single_attempt', data.get('single_attempt', True)),
        'affects_quality': test_config.get('affects_quality', data.get('affects_quality', False)),
    }

_SURVEY_ERROR_MESSAGES = {
    'SURVEY_NOT_FOUND': ("Опрос не найден", 404),
    'SURVEY_NOT_A_TEST': ("Этот опрос не является тестом", 400),
    'SURVEY_TITLE_REQUIRED': ("Укажите название", 400),
    'SURVEY_QUESTIONS_REQUIRED': ("Добавьте хотя бы один вопрос", 400),
    'SURVEY_OPERATORS_REQUIRED': ("Выберите хотя бы одного сотрудника", 400),
    'SURVEY_INVALID_TENURE_MIN': ("Некорректный стаж «от»", 400),
    'SURVEY_INVALID_TENURE_MAX': ("Некорректный стаж «до»", 400),
    'SURVEY_INVALID_TENURE_RANGE': ("Стаж «от» больше стажа «до»", 400),
    'SURVEY_INVALID_STARTS_AT': ("Некорректная дата начала теста", 400),
    'SURVEY_INVALID_ENDS_AT': ("Некорректная дата завершения теста", 400),
    'SURVEY_INVALID_SCHEDULE_RANGE': ("Завершение теста должно быть позже начала", 400),
    'SURVEY_TEST_NOT_STARTED': ("Тест ещё не начался", 409),
    'SURVEY_TEST_FINISHED': ("Время теста истекло", 409),
    'SURVEY_ALREADY_COMPLETED': ("Тест уже пройден", 409),
    'SURVEY_HAS_NO_QUESTIONS': ("В опросе нет вопросов", 400),
    'SURVEY_ANSWERS_REQUIRED': ("Не переданы ответы", 400),
    'SURVEY_EMPTY_RESPONSE': ("Ответьте хотя бы на один вопрос", 400),
    'SURVEY_REPEAT_SOURCE_NOT_FOUND': ("Опрос для повтора не найден", 404),
    'SURVEY_ARCHIVED': ("Опрос в архиве и больше не принимает ответы", 409),
}

_SURVEY_ERROR_PREFIX_MESSAGES = (
    ('SURVEY_QUESTION_TEXT_REQUIRED_', "Укажите текст вопроса", 400),
    ('SURVEY_INVALID_QUESTION_TYPE_', "Недопустимый тип вопроса", 400),
    ('SURVEY_OPTIONS_REQUIRED_', "В вопросе должно быть минимум 2 варианта", 400),
    ('SURVEY_TEST_RATING_NOT_ALLOWED_', "В тесте нельзя использовать вопросы-рейтинги", 400),
    ('SURVEY_CORRECT_OPTIONS_REQUIRED_', "У каждого вопроса теста отметьте правильный ответ", 400),
    ('SURVEY_CORRECT_OPTION_INVALID_', "Правильный ответ должен быть одним из вариантов", 400),
    ('SURVEY_SINGLE_CORRECT_OPTION_REQUIRED_', "В вопросе с одним ответом правильный вариант должен быть один", 400),
    ('SURVEY_INVALID_QUESTION_POINTS_', "Баллы за вопрос — число больше 0 и не больше 1000", 400),
    ('SURVEY_REQUIRED_QUESTION_', "Ответьте на все обязательные вопросы", 400),
    ('SURVEY_INVALID_RATING_', "Оценка должна быть от 1 до 5", 400),
    ('SURVEY_INVALID_OPTION_', "Выбран недопустимый вариант ответа", 400),
    ('SURVEY_TOO_MANY_OPTIONS_', "Выбрано больше вариантов, чем допускает вопрос", 400),
    ('SURVEY_OTHER_TEXT_TOO_LONG_', "Ответ «Другое» не должен быть длиннее 500 символов", 400),
)

_SURVEY_ERROR_NUMBERED_PREFIX_MESSAGES = (
    (
        'SURVEY_OPTION_CHANGED_',
        "Вопрос №{number} изменили, пока вы проходили опрос. Ответьте на него заново",
        "Один из вопросов изменили, пока вы проходили опрос. Ответьте на него заново",
        409,
    ),
)

def _survey_error_response(code):
    code = str(code)
    if code in _SURVEY_ERROR_MESSAGES:
        message, status = _SURVEY_ERROR_MESSAGES[code]
        return jsonify({"error": message}), status
    for prefix, message, fallback_message, status in _SURVEY_ERROR_NUMBERED_PREFIX_MESSAGES:
        if not code.startswith(prefix):
            continue
        suffix = code[len(prefix):].strip()
        if suffix.isdigit():
            return jsonify({"error": message.format(number=int(suffix))}), status
        return jsonify({"error": fallback_message}), status
    for prefix, message, status in _SURVEY_ERROR_PREFIX_MESSAGES:
        if code.startswith(prefix):
            return jsonify({"error": message}), status
    return jsonify({"error": code}), 400

def _survey_archived_scope_requested():
    """scope=archive — вкладка «Архив». Любое другое значение — активные."""
    return (request.args.get('scope') or '').strip().lower() in ('archive', 'archived')

def _survey_department_filter_id():
    """Фильтр по отделу из шапки раздела (не путать с периметром прав)."""
    raw = (request.args.get('department_id') or '').strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None

def _survey_page_payload(page_data):
    page_data = page_data or {}
    return {
        "surveys": page_data.get('items') or [],
        "total": page_data.get('total') or 0,
        "page": page_data.get('page') or 1,
        "page_size": page_data.get('page_size') or 0,
        "pages": page_data.get('pages') or 0,
    }

def _survey_export_format_dt(value):
    if value in (None, ''):
        return ''
    text = str(value).strip()
    if not text:
        return ''
    try:
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt_value = datetime.fromisoformat(text)
        return dt_value.strftime('%d.%m.%Y %H:%M')
    except Exception:
        if 'T' in text:
            return text.replace('T', ' ')[:16]
        return text

def _survey_export_answer_text(question, answer):
    if not question or not answer:
        return '—'

    qtype = str((question or {}).get('type') or '').strip().lower()
    if qtype == 'rating':
        rating_value = answer.get('rating_value')
        try:
            return str(int(rating_value))
        except Exception:
            return '—'

    selected = []
    for item in (answer.get('selected_options') or []):
        text = str(item or '').strip()
        if text and text not in selected:
            selected.append(text)

    other_text = str(answer.get('answer_text') or '').strip()
    if selected and other_text:
        return f"{', '.join(selected)}; Другое: {other_text}"
    if selected:
        return ', '.join(selected)
    if other_text:
        return f"Другое: {other_text}"
    return '—'

def _survey_export_unique_trimmed_list(values):
    source = values if isinstance(values, list) else []
    normalized = []
    for value in source:
        text = str(value or '').strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized

def _survey_export_expected_options(question, answer):
    expected_from_answer = _survey_export_unique_trimmed_list((answer or {}).get('expected_options'))
    if expected_from_answer:
        return expected_from_answer
    return _survey_export_unique_trimmed_list((question or {}).get('correct_options'))

def _survey_export_is_answer_present(question, answer):
    if not question or not answer:
        return False

    qtype = str((question or {}).get('type') or '').strip().lower()
    if qtype == 'rating':
        try:
            return answer.get('rating_value') is not None and str(answer.get('rating_value')).strip() != ''
        except Exception:
            return False

    selected = _survey_export_unique_trimmed_list((answer or {}).get('selected_options'))
    answer_text = str((answer or {}).get('answer_text') or '').strip()
    return bool(selected or answer_text)

def _survey_export_is_test_answer_correct(question, answer):
    if not question or not answer:
        return False
    if isinstance(answer.get('is_correct'), bool):
        return bool(answer.get('is_correct'))

    qtype = str((question or {}).get('type') or '').strip().lower()
    selected_options = _survey_export_unique_trimmed_list((answer or {}).get('selected_options'))
    answer_text = str((answer or {}).get('answer_text') or '').strip()
    expected_options = _survey_export_expected_options(question, answer)

    if qtype == 'single':
        return (
            len(expected_options) == 1
            and len(selected_options) == 1
            and selected_options[0] == expected_options[0]
            and not answer_text
        )
    if qtype == 'multiple':
        return (
            len(expected_options) > 0
            and len(selected_options) == len(expected_options)
            and all(option in selected_options for option in expected_options)
            and not answer_text
        )
    return False

def _survey_export_is_test_answer_partial(answer):
    """Частичный зачёт: балл есть, но вопрос закрыт не полностью."""
    answer = answer or {}
    if answer.get('is_partially_correct') is True:
        return True
    try:
        return not answer.get('is_correct') and float(answer.get('earned_points') or 0) > 0
    except Exception:
        return False

def _survey_export_answer_text_for_test(question, answer):
    base_text = _survey_export_answer_text(question, answer)
    has_answer = _survey_export_is_answer_present(question, answer)
    if not has_answer:
        return f"{base_text} [Нет ответа]"

    if _survey_export_is_test_answer_correct(question, answer):
        status_text = 'Верно'
    elif _survey_export_is_test_answer_partial(answer):
        status_text = 'Частично'
    else:
        status_text = 'Неверно'

    points_note = ''
    try:
        earned = answer.get('earned_points')
        maximum = (question or {}).get('points')
        if earned is not None and maximum is not None:
            points_note = f" {float(earned):g}/{float(maximum):g} балл."
    except Exception:
        points_note = ''

    expected_options = _survey_export_expected_options(question, answer)
    if expected_options:
        return f"{base_text} [{status_text}]{points_note} (Правильный: {', '.join(expected_options)})"
    return f"{base_text} [{status_text}]{points_note}"

def _survey_export_test_row_metrics(row):
    summary = row.get('test_summary') or {}
    total_questions = int(summary.get('total_questions') or 0)
    answered_questions = int(summary.get('answered_questions') or 0)
    correct_answers = int(summary.get('correct_answers') or 0)

    score_percent = None
    try:
        if summary.get('score_percent') is not None:
            score_percent = float(summary.get('score_percent'))
    except Exception:
        score_percent = None

    if total_questions > 0:
        correct_ratio = f"{correct_answers}/{total_questions}"
        answered_ratio = f"{answered_questions}/{total_questions}"
    else:
        correct_ratio = '—'
        answered_ratio = '—'

    def _points(key):
        try:
            value = summary.get(key)
            return float(value) if value is not None else None
        except Exception:
            return None

    earned_points = _points('earned_points')
    max_points = _points('max_points')
    if earned_points is not None and max_points is not None:
        points_ratio = f"{earned_points:g}/{max_points:g}"
    elif max_points is not None:
        points_ratio = f"—/{max_points:g}"
    else:
        points_ratio = '—'

    return {
        'score_percent': score_percent,
        'correct_ratio': correct_ratio,
        'answered_ratio': answered_ratio,
        'points_ratio': points_ratio,
        'earned_points': earned_points,
        'max_points': max_points,
        'sent_to_quality': bool(summary.get('sent_to_quality')),
        'is_auto_submitted': bool(summary.get('is_auto_submitted'))
    }

def _survey_export_answer_text_with_mode(question, answer, is_test=False):
    if is_test:
        return _survey_export_answer_text_for_test(question, answer)
    return _survey_export_answer_text(question, answer)


def build_surveys_blueprint(
    *,
    MARKETING_OBSERVER_ROLE,
    ROLE_HIERARCHY,
    _department_scope_id_for_requester,
    _get_role_level,
    _has_min_role,
    _headed_department_id,
    _is_admin_role,
    _is_global_admin_requester,
    _is_super_admin_role,
    _normalize_user_role,
    _resolve_requester,
    require_api_key,
):
    """Собирает Blueprint раздела; все зависимости от bot_schedule2 — аргументами."""
    bp = Blueprint('surveys', __name__, url_prefix='/api')

    def _has_any_role(role, allowed_roles) -> bool:
        role_norm = _normalize_user_role(role)
        if not role_norm:
            return False
        normalized_allowed = {_normalize_user_role(item) for item in (allowed_roles or [])}
        return role_norm in normalized_allowed

    def _normalize_surveys_role(role):
        return _normalize_user_role(role)

    def _surveys_route_guard():
        requester_id, requester, error = _resolve_requester()
        if error:
            message, status_code = error
            return None, None, None, jsonify({"error": message}), status_code

        role = _normalize_surveys_role(requester[3])
        headed_dept_id = _headed_department_id(requester_id)
        if headed_dept_id is not None and not _is_super_admin_role(role):
            role = 'sv'
        # Проверка ТОЧНАЯ, не по уровню: _has_any_role сверяет членство в кортеже,
        # и десятка в ROLE_HIERARCHY сюда роль не проводит. Отсюда 'marketing_manager'
        # отдельной строкой — раздел выдан ему картой разделов отдела.
        if not _has_any_role(role, ('operator', 'trainer', 'sv', 'admin', 'super_admin',
                                    MARKETING_OBSERVER_ROLE)):
            return None, None, None, jsonify({"error": "Only admin, sv, trainer, operator and marketing employees can access surveys"}), 403

        g.survey_scope_department_id = None if _is_global_admin_requester(role, requester_id) else _department_scope_id_for_requester(requester_id)
        return requester_id, requester, role, None, None

    @bp.route('/surveys', methods=['GET', 'POST', 'OPTIONS'])
    @require_api_key
    def handle_surveys():
        try:
            requester_id, requester, requester_role, guard_response, guard_status = _surveys_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            if request.method == 'GET':
                # Список — это страница ЛЁГКИХ строк: заголовок и счётчики.
                # Вопросы, ответы и статистику приносит карточка выбранного опроса
                # (GET /api/surveys/<id>), иначе один заход в раздел выкачивал бы
                # все ответы всех сотрудников по всем опросам разом.
                archived = _survey_archived_scope_requested()
                search = (request.args.get('q') or '').strip()
                page = request.args.get('page')
                page_size = request.args.get('page_size')

                if requester_role == 'operator':
                    # Архив — с СВ и выше. Оператору его не отдаём даже по прямому
                    # scope=archive: вкладки у него нет, и списка тоже быть не должно.
                    page_data = db.get_operator_surveys_page(
                        requester_id,
                        archived=False,
                        search=search,
                        page=page,
                        page_size=page_size
                    )
                    return jsonify({"status": "success", **_survey_page_payload(page_data)}), 200

                scope_department_id = getattr(g, 'survey_scope_department_id', None)
                page_data = db.get_surveys_page(
                    requester_id,
                    requester_role,
                    scope_department_id=scope_department_id,
                    department_id=_survey_department_filter_id(),
                    archived=archived,
                    search=search,
                    page=page,
                    page_size=page_size
                )
                # Состав групп отдаём вместе со списком: назначение «на группу»
                # не должно стоить фронту отдельного запроса.
                return jsonify({
                    "status": "success",
                    **_survey_page_payload(page_data),
                    "groups": db.get_survey_assignable_groups(
                        requester_id,
                        requester_role,
                        scope_department_id=scope_department_id
                    )
                }), 200

            if not (_is_admin_role(requester_role) or requester_role in ('sv', 'trainer')):
                return jsonify({"error": "Only admin, sv and trainer can create surveys"}), 403

            data = request.get_json() or {}
            title = data.get('title')
            description = data.get('description')
            assignment = data.get('assignment') or {}
            questions = data.get('questions') or []
            is_test_raw = data.get('is_test', False)
            if isinstance(is_test_raw, str):
                is_test = is_test_raw.strip().lower() in ('1', 'true', 'yes', 'on')
            else:
                is_test = bool(is_test_raw)
            repeat_from_survey_id_raw = data.get('repeat_from_survey_id')
            repeat_from_survey_id = None
            if repeat_from_survey_id_raw not in (None, ''):
                try:
                    repeat_from_survey_id = int(repeat_from_survey_id_raw)
                except Exception:
                    return jsonify({"error": "Invalid repeat_from_survey_id"}), 400
                if repeat_from_survey_id <= 0:
                    return jsonify({"error": "Invalid repeat_from_survey_id"}), 400

            operator_ids_raw = assignment.get('operator_ids') or []
            operator_ids = []
            for op_id in operator_ids_raw:
                try:
                    parsed = int(op_id)
                except Exception:
                    continue
                if parsed > 0 and parsed not in operator_ids:
                    operator_ids.append(parsed)

            operator_ids = db.filter_active_survey_operator_ids(operator_ids)
            if requester_role == 'sv':
                visible_operator_ids = set(db.get_visible_operator_ids_for_requester(
                    requester_id,
                    requester_role,
                    scope_department_id=getattr(g, 'survey_scope_department_id', None)
                ))
                operator_ids = [op_id for op_id in operator_ids if op_id in visible_operator_ids]
            if not operator_ids:
                return jsonify({"error": "At least one operator must be assigned"}), 400

            if repeat_from_survey_id is not None:
                visible_surveys = db.get_surveys_for_management(
                    requester_id,
                    requester_role,
                    scope_department_id=getattr(g, 'survey_scope_department_id', None)
                )
                visible_survey_ids = set()
                for survey_item in visible_surveys:
                    try:
                        visible_survey_ids.add(int(survey_item.get('id')))
                    except Exception:
                        continue
                if repeat_from_survey_id not in visible_survey_ids:
                    return jsonify({"error": "Source survey for repeat not found"}), 404

            created = db.create_survey(
                title=title,
                description=description,
                created_by=requester_id,
                assignment=assignment,
                questions=questions,
                operator_ids=operator_ids,
                repeat_from_survey_id=repeat_from_survey_id,
                is_test=is_test,
                test_config=_survey_test_config_from_payload(data)
            )

            return jsonify({
                "status": "success",
                "message": "Survey created successfully",
                "survey_id": created.get('id'),
                "created_at": created.get('created_at'),
                "repeat_root_id": created.get('repeat_root_id'),
                "repeat_iteration": created.get('repeat_iteration'),
                "is_test": bool(created.get('is_test'))
            }), 201

        except ValueError as value_error:
            return _survey_error_response(value_error)
        except Exception as e:
            logging.error(f"Error in handle_surveys: {e}", exc_info=True)
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/surveys/pending_count', methods=['GET', 'OPTIONS'])
    @require_api_key
    def survey_pending_count():
        """Число для бейджа раздела в сайдбаре.

    Раньше его считал фронт, выгрузив весь список опросов со всеми ответами —
    ради одной цифры при каждом заходе. Архивные опросы в счёт не идут.
    """
        try:
            requester_id, requester, requester_role, guard_response, guard_status = _surveys_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            return jsonify({
                "status": "success",
                "count": db.count_pending_surveys(
                    requester_id,
                    requester_role,
                    scope_department_id=getattr(g, 'survey_scope_department_id', None)
                )
            }), 200
        except Exception as e:
            logging.error(f"Error in survey_pending_count: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/surveys/<int:survey_id>/detail', methods=['GET', 'OPTIONS'])
    @require_api_key
    def get_survey_detail(survey_id):
        """Карточка одного опроса: вопросы, ответы и статистика."""
        try:
            requester_id, requester, requester_role, guard_response, guard_status = _surveys_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            detail = db.get_survey_detail_for_requester(
                survey_id,
                requester_id,
                requester_role,
                scope_department_id=getattr(g, 'survey_scope_department_id', None)
            )
            if not detail:
                return jsonify({"error": "Опрос не найден"}), 404

            return jsonify({
                "status": "success",
                "survey": detail.get('survey'),
                "repetitions": detail.get('repetitions') or []
            }), 200
        except ValueError as value_error:
            return _survey_error_response(value_error)
        except Exception as e:
            logging.error(f"Error in get_survey_detail: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/surveys/<int:survey_id>', methods=['PUT', 'PATCH', 'DELETE', 'OPTIONS'])
    @require_api_key
    def delete_survey(survey_id):
        try:
            requester_id, requester, requester_role, guard_response, guard_status = _surveys_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            if not (_is_admin_role(requester_role) or requester_role in ('sv', 'trainer')):
                return jsonify({"error": "Only admin, sv and trainer can manage surveys"}), 403

            if request.method in ('PUT', 'PATCH'):
                data = request.get_json() or {}
                title = data.get('title')
                description = data.get('description')
                assignment = data.get('assignment') or {}
                questions = data.get('questions') or []
                is_test_raw = data.get('is_test', False)
                if isinstance(is_test_raw, str):
                    is_test = is_test_raw.strip().lower() in ('1', 'true', 'yes', 'on')
                else:
                    is_test = bool(is_test_raw)

                operator_ids_raw = assignment.get('operator_ids') or []
                operator_ids = []
                for op_id in operator_ids_raw:
                    try:
                        parsed = int(op_id)
                    except Exception:
                        continue
                    if parsed > 0 and parsed not in operator_ids:
                        operator_ids.append(parsed)
                operator_ids = db.filter_active_survey_operator_ids(operator_ids)
                if requester_role == 'sv':
                    visible_operator_ids = set(db.get_visible_operator_ids_for_requester(
                        requester_id,
                        requester_role,
                        scope_department_id=getattr(g, 'survey_scope_department_id', None)
                    ))
                    operator_ids = [op_id for op_id in operator_ids if op_id in visible_operator_ids]
                if not operator_ids:
                    return jsonify({"error": "At least one active operator must be assigned"}), 400

                updated = db.update_survey(
                    survey_id=survey_id,
                    requester_id=requester_id,
                    requester_role=requester_role,
                    title=title,
                    description=description,
                    assignment=assignment,
                    questions=questions,
                    operator_ids=operator_ids,
                    is_test=is_test,
                    test_config=_survey_test_config_from_payload(data)
                )
                return jsonify({
                    "status": "success",
                    "message": "Survey updated",
                    "survey_id": updated.get('id'),
                    "updated_at": updated.get('updated_at')
                }), 200

            db.delete_survey(
                survey_id=survey_id,
                requester_id=requester_id,
                requester_role=requester_role
            )
            return jsonify({"status": "success", "message": "Survey deleted"}), 200
        except ValueError as value_error:
            return _survey_error_response(value_error)
        except PermissionError:
            return jsonify({"error": "Нет доступа к этому опросу"}), 403
        except Exception as e:
            logging.error(f"Error in delete_survey: {e}", exc_info=True)
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/surveys/<int:survey_id>/submit', methods=['POST', 'OPTIONS'])
    @require_api_key
    def submit_survey(survey_id):
        try:
            requester_id, requester, requester_role, guard_response, guard_status = _surveys_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            if requester_role != 'operator':
                return jsonify({"error": "Only operators can submit surveys"}), 403

            data = request.get_json() or {}
            answers = data.get('answers')

            result = db.submit_survey_response(
                survey_id=survey_id,
                operator_id=requester_id,
                answers=answers
            )

            return jsonify({
                "status": "success",
                "message": "Survey completed",
                "result": result
            }), 200
        except ValueError as value_error:
            return _survey_error_response(value_error)
        except PermissionError as permission_error:
            if str(permission_error) == 'SURVEY_NOT_ASSIGNED':
                return jsonify({"error": "Этот опрос вам не назначен"}), 403
            return jsonify({"error": str(permission_error)}), 403
        except Exception as e:
            logging.error(f"Error in submit_survey: {e}", exc_info=True)
            return jsonify({"error": f"Internal server error"}), 500

    @bp.route('/surveys/<int:survey_id>/attempt', methods=['PUT', 'OPTIONS'])
    @require_api_key
    def save_survey_attempt(survey_id):
        """
    Промежуточное сохранение начатой попытки теста. Нужно, чтобы по окончании
    окна автоотправка сохранила уже выбранные ответы, даже если оператор
    закрыл вкладку.
    """
        try:
            requester_id, requester, requester_role, guard_response, guard_status = _surveys_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            if requester_role != 'operator':
                return jsonify({"error": "Проходить тест может только оператор"}), 403

            data = request.get_json() or {}
            result = db.save_survey_attempt_draft(
                survey_id=survey_id,
                operator_id=requester_id,
                answers=data.get('answers')
            )
            return jsonify({"status": "success", "result": result}), 200
        except ValueError as value_error:
            return _survey_error_response(value_error)
        except PermissionError as permission_error:
            if str(permission_error) == 'SURVEY_NOT_ASSIGNED':
                return jsonify({"error": "Этот тест вам не назначен"}), 403
            return jsonify({"error": str(permission_error)}), 403
        except Exception as e:
            logging.error(f"Error in save_survey_attempt: {e}", exc_info=True)
            return jsonify({"error": "Internal server error"}), 500

    @bp.route('/surveys/<int:survey_id>/export_excel', methods=['GET', 'OPTIONS'])
    @require_api_key
    def export_survey_statistics_excel(survey_id):
        try:
            requester_id, requester, requester_role, guard_response, guard_status = _surveys_route_guard()
            if guard_response is not None:
                return guard_response, guard_status

            if not (_is_admin_role(requester_role) or requester_role in ('sv', 'trainer')):
                return jsonify({"error": "Only admin, sv and trainer can export survey statistics"}), 403

            surveys = db.get_surveys_for_management(
                requester_id,
                requester_role,
                scope_department_id=getattr(g, 'survey_scope_department_id', None)
            )
            selected_survey = None
            for survey_item in surveys:
                try:
                    if int(survey_item.get('id')) == int(survey_id):
                        selected_survey = survey_item
                        break
                except Exception:
                    continue

            if not selected_survey:
                return jsonify({"error": "Survey not found"}), 404

            title = str(selected_survey.get('title') or f'Опрос #{survey_id}')
            description = str(selected_survey.get('description') or '')
            statistics = selected_survey.get('statistics') or {}
            questions = list(selected_survey.get('questions') or [])
            is_test = bool(selected_survey.get('is_test'))
            question_stats = list(statistics.get('question_stats') or [])
            responses_detailed_all = list(statistics.get('responses_detailed_all_repetitions') or [])
            responses_detailed = responses_detailed_all if responses_detailed_all else list(statistics.get('responses_detailed') or [])
            repeat_info = selected_survey.get('repeat') or {}
            try:
                repeat_iteration = int(repeat_info.get('iteration') or 1)
            except Exception:
                repeat_iteration = 1
            repeat_iteration = repeat_iteration if repeat_iteration >= 1 else 1

            question_type_labels = {
                'single': 'Один вариант',
                'multiple': 'Несколько вариантов',
                'rating': 'Рейтинг 1-5'
            }
            question_stats_by_id = {}
            for stat in question_stats:
                try:
                    qid = int(stat.get('question_id'))
                except Exception:
                    continue
                question_stats_by_id[qid] = stat

            wb = Workbook()
            ws_summary = wb.active
            ws_summary.title = 'Сводка'

            ws_summary.append(['Отчет', 'Статистика по опросу'])
            ws_summary.append(['Опрос', title])
            ws_summary.append(['Описание', description])
            ws_summary.append(['Повторение', f"#{repeat_iteration}"])
            ws_summary.append(['Создан', _survey_export_format_dt(selected_survey.get('created_at'))])
            ws_summary.append(['Обновлен', _survey_export_format_dt(selected_survey.get('updated_at'))])
            ws_summary.append(['Сформирован', datetime.now().strftime('%d.%m.%Y %H:%M')])
            ws_summary.append(['Назначено', int(statistics.get('assigned_count') or 0)])
            ws_summary.append(['Пройдено', int(statistics.get('completed_count') or 0)])
            ws_summary.append(['Ожидают', int(statistics.get('pending_count') or 0)])
            ws_summary.append(['Процент прохождения, %', float(statistics.get('completion_rate') or 0)])
            if is_test:
                score_values = []
                for response_row in responses_detailed:
                    metrics = _survey_export_test_row_metrics(response_row)
                    score_percent = metrics.get('score_percent')
                    if isinstance(score_percent, (int, float)):
                        score_values.append(float(score_percent))
                ws_summary.append(['Средний балл теста, %', round(sum(score_values) / len(score_values), 1) if score_values else '—'])
                ws_summary.append(['Лучший балл теста, %', max(score_values) if score_values else '—'])
                ws_summary.append(['Минимальный балл теста, %', min(score_values) if score_values else '—'])
            ws_summary.append([])
            ws_summary.append(['Статистика по вопросам'])
            ws_summary.append([
                '№',
                'Вопрос',
                'Тип',
                'Ответили',
                'Респондентов (по вопросу)',
                'Доля ответивших, %',
                'Метрика',
                'Значение',
                'Количество',
                '% от ответивших',
                '% от респондентов'
            ])
            summary_correct_option_rows = set()

            for idx, question in enumerate(questions, start=1):
                question_id = int(question.get('id') or 0)
                qtype = str(question.get('type') or '').strip().lower()
                qtype_label = question_type_labels.get(qtype, qtype or '—')
                question_text = str(question.get('text') or f'Вопрос #{idx}')
                stat = question_stats_by_id.get(question_id)

                if not stat:
                    ws_summary.append([idx, question_text, qtype_label, 0, 0, 0, 'Нет данных', '', '', '', ''])
                    continue

                answered = int(stat.get('responses_with_answer') or 0)
                respondents = int(stat.get('question_respondents_total') or answered)
                response_rate = float(stat.get('response_rate') or 0)
                correct_options_set = set(_survey_export_unique_trimmed_list((stat or {}).get('correct_options') or question.get('correct_options')))

                if qtype == 'rating':
                    ws_summary.append([
                        idx, question_text, qtype_label, answered, respondents, response_rate,
                        'Среднее', stat.get('average_rating') if stat.get('average_rating') is not None else '—', '', '', ''
                    ])
                    ws_summary.append([
                        idx, question_text, qtype_label, answered, respondents, response_rate,
                        'Медиана', stat.get('median_rating') if stat.get('median_rating') is not None else '—', '', '', ''
                    ])
                    min_rating = stat.get('min_rating')
                    max_rating = stat.get('max_rating')
                    rating_range = '—'
                    if min_rating is not None or max_rating is not None:
                        rating_range = f"{min_rating if min_rating is not None else '—'}-{max_rating if max_rating is not None else '—'}"
                    ws_summary.append([
                        idx, question_text, qtype_label, answered, respondents, response_rate,
                        'Диапазон', rating_range, '', '', ''
                    ])
                    for bucket in (stat.get('ratings_distribution_detailed') or []):
                        ws_summary.append([
                            idx,
                            question_text,
                            qtype_label,
                            answered,
                            respondents,
                            response_rate,
                            f"Оценка {bucket.get('value')}",
                            '',
                            int(bucket.get('count') or 0),
                            float(bucket.get('percent_of_answers') or 0),
                            float(bucket.get('percent_of_respondents') or 0)
                        ])
                    continue

                options = list(stat.get('options') or [])
                if not options:
                    ws_summary.append([idx, question_text, qtype_label, answered, respondents, response_rate, 'Нет данных', '', '', '', ''])
                    continue

                for option_item in options:
                    option_text = str(option_item.get('option') or '').strip()
                    is_correct_option = is_test and option_text and option_text in correct_options_set
                    ws_summary.append([
                        idx,
                        question_text,
                        qtype_label,
                        answered,
                        respondents,
                        response_rate,
                        'Вариант (правильный)' if is_correct_option else 'Вариант',
                        option_text,
                        int(option_item.get('count') or 0),
                        float(option_item.get('percent_of_answers') or option_item.get('percent') or 0),
                        float(option_item.get('percent_of_respondents') or option_item.get('percent') or 0)
                    ])
                    if is_correct_option:
                        summary_correct_option_rows.add(ws_summary.max_row)

            ws_scores = None
            if is_test:
                ws_scores = wb.create_sheet('Баллы теста')
                ws_scores.append([
                    'Сотрудник', 'ID', 'Статус', 'Начало', 'Завершение', 'Повторение',
                    'Баллы', 'Общий балл, %', 'Итоговая оценка', 'Тип оценки',
                    'Передано в качество', 'Верно', 'Отвечено'
                ])
                for row in responses_detailed:
                    status_raw = str(row.get('status') or '').strip().lower()
                    status_label = 'Пройден' if status_raw == 'completed' else 'Назначен'
                    test_metrics = _survey_export_test_row_metrics(row)
                    score_percent = test_metrics.get('score_percent')
                    if test_metrics.get('is_auto_submitted'):
                        status_label = f"{status_label} (автоотправка)"
                    ws_scores.append([
                        str(row.get('operator_name') or f"#{row.get('operator_id') or ''}"),
                        int(row.get('operator_id') or 0),
                        status_label,
                        _survey_export_format_dt(row.get('started_at')),
                        _survey_export_format_dt(row.get('submitted_at')),
                        int(row.get('repeat_iteration') or repeat_iteration),
                        test_metrics.get('points_ratio'),
                        score_percent if score_percent is not None else '—',
                        score_percent if score_percent is not None else '—',
                        'Тестирование знаний',
                        'Да' if test_metrics.get('sent_to_quality') else 'Нет',
                        test_metrics.get('correct_ratio'),
                        test_metrics.get('answered_ratio')
                    ])

            ws_answers = wb.create_sheet('Ответы сотрудников')
            answer_headers = ['Сотрудник', 'ID', 'Статус', 'Отправлено', 'Повторение']
            if is_test:
                answer_headers.extend(['Общий балл, %', 'Верно', 'Отвечено'])
            for idx, question in enumerate(questions, start=1):
                question_text = str(question.get('text') or f'Вопрос #{idx}').replace('\n', ' ').strip()
                if len(question_text) > 100:
                    question_text = question_text[:97] + '...'
                answer_headers.append(f"Q{idx}: {question_text}")
            ws_answers.append(answer_headers)

            for row in responses_detailed:
                answers_by_question = row.get('answers_by_question') or {}
                status_raw = str(row.get('status') or '').strip().lower()
                status_label = 'Пройден' if status_raw == 'completed' else 'Назначен'
                row_values = [
                    str(row.get('operator_name') or f"#{row.get('operator_id') or ''}"),
                    int(row.get('operator_id') or 0),
                    status_label,
                    _survey_export_format_dt(row.get('submitted_at')),
                    int(row.get('repeat_iteration') or repeat_iteration)
                ]
                if is_test:
                    test_metrics = _survey_export_test_row_metrics(row)
                    row_values.extend([
                        test_metrics.get('score_percent') if test_metrics.get('score_percent') is not None else '—',
                        test_metrics.get('correct_ratio'),
                        test_metrics.get('answered_ratio')
                    ])

                for question in questions:
                    question_id = int(question.get('id') or 0)
                    answer = answers_by_question.get(str(question_id)) or answers_by_question.get(question_id)
                    row_values.append(_survey_export_answer_text_with_mode(question, answer, is_test=is_test))

                ws_answers.append(row_values)

            # Visual formatting for a cleaner, management-friendly export
            title_fill = PatternFill(fill_type='solid', start_color='1F4E78', end_color='1F4E78')
            section_fill = PatternFill(fill_type='solid', start_color='2F75B5', end_color='2F75B5')
            header_fill = PatternFill(fill_type='solid', start_color='4472C4', end_color='4472C4')
            label_fill = PatternFill(fill_type='solid', start_color='E9EFFB', end_color='E9EFFB')
            even_row_fill = PatternFill(fill_type='solid', start_color='F8FAFF', end_color='F8FAFF')
            status_done_fill = PatternFill(fill_type='solid', start_color='E6F4EA', end_color='E6F4EA')
            status_pending_fill = PatternFill(fill_type='solid', start_color='FFF4E5', end_color='FFF4E5')
            correct_option_fill = PatternFill(fill_type='solid', start_color='DCFCE7', end_color='DCFCE7')
            answer_correct_fill = PatternFill(fill_type='solid', start_color='DCFCE7', end_color='DCFCE7')
            answer_incorrect_fill = PatternFill(fill_type='solid', start_color='FEF2F2', end_color='FEF2F2')
            answer_empty_fill = PatternFill(fill_type='solid', start_color='F3F4F6', end_color='F3F4F6')

            white_bold_font = Font(color='FFFFFF', bold=True)
            dark_bold_font = Font(color='1F4E78', bold=True)
            regular_font = Font(color='1F2937')
            correct_option_font = Font(color='166534', bold=True)
            answer_correct_font = Font(color='166534')
            answer_incorrect_font = Font(color='991B1B')
            answer_empty_font = Font(color='6B7280')

            thin_side = Side(style='thin', color='D9E2F3')
            thin_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

            # ---- Summary sheet styles ----
            summary_header_row = None
            for row_idx in range(1, ws_summary.max_row + 1):
                if ws_summary.cell(row=row_idx, column=1).value == '№':
                    summary_header_row = row_idx
                    break

            summary_section_row = summary_header_row - 1 if summary_header_row else None
            if summary_section_row and summary_section_row >= 1:
                ws_summary.merge_cells(start_row=summary_section_row, start_column=1, end_row=summary_section_row, end_column=11)
                section_cell = ws_summary.cell(row=summary_section_row, column=1)
                section_cell.fill = section_fill
                section_cell.font = white_bold_font
                section_cell.alignment = Alignment(horizontal='left', vertical='center')

            # Report title row
            ws_summary.merge_cells(start_row=1, start_column=1, end_row=1, end_column=11)
            ws_summary.cell(row=1, column=1).value = 'Статистика по опросу'
            ws_summary.cell(row=1, column=1).fill = title_fill
            ws_summary.cell(row=1, column=1).font = white_bold_font
            ws_summary.cell(row=1, column=1).alignment = Alignment(horizontal='left', vertical='center')
            ws_summary.row_dimensions[1].height = 24

            # Meta rows (key-value)
            meta_last_row = (summary_section_row - 2) if summary_section_row else 1
            for row_idx in range(2, max(2, meta_last_row + 1)):
                key_cell = ws_summary.cell(row=row_idx, column=1)
                val_cell = ws_summary.cell(row=row_idx, column=2)
                key_cell.fill = label_fill
                key_cell.font = dark_bold_font
                key_cell.alignment = Alignment(horizontal='left', vertical='center')
                val_cell.font = regular_font
                val_cell.alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
                key_cell.border = thin_border
                val_cell.border = thin_border

            if summary_header_row:
                for col_idx in range(1, 12):
                    cell = ws_summary.cell(row=summary_header_row, column=col_idx)
                    cell.fill = header_fill
                    cell.font = white_bold_font
                    cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                    cell.border = thin_border

                for row_idx in range(summary_header_row + 1, ws_summary.max_row + 1):
                    for col_idx in range(1, 12):
                        cell = ws_summary.cell(row=row_idx, column=col_idx)
                        cell.font = regular_font
                        cell.border = thin_border
                        if col_idx in (2, 8):
                            cell.alignment = Alignment(horizontal='left', vertical='top', wrap_text=True)
                        elif col_idx in (1, 3, 4, 5, 6, 7, 9, 10, 11):
                            cell.alignment = Alignment(horizontal='center', vertical='center')

                        if isinstance(cell.value, (int, float)):
                            if col_idx in (6, 10, 11):
                                cell.number_format = '0.0"%"'
                            elif col_idx in (4, 5, 9):
                                cell.number_format = '0'

                for row_idx in summary_correct_option_rows:
                    if row_idx <= summary_header_row or row_idx > ws_summary.max_row:
                        continue
                    for col_idx in range(7, 12):
                        marked_cell = ws_summary.cell(row=row_idx, column=col_idx)
                        marked_cell.fill = correct_option_fill
                        if col_idx in (7, 8):
                            marked_cell.font = correct_option_font

            summary_widths = {
                1: 6,   # №
                2: 56,  # Вопрос
                3: 22,  # Тип
                4: 12,  # Ответили
                5: 14,  # Респондентов
                6: 18,  # Доля ответивших
                7: 20,  # Метрика
                8: 24,  # Значение
                9: 14,  # Количество
                10: 18, # % от ответивших
                11: 20  # % от респондентов
            }
            for col_idx, width in summary_widths.items():
                ws_summary.column_dimensions[get_column_letter(col_idx)].width = width

            if summary_header_row:
                ws_summary.auto_filter.ref = f"A{summary_header_row}:K{ws_summary.max_row}"
                ws_summary.freeze_panes = f"A{summary_header_row + 1}"
                ws_summary.row_dimensions[summary_header_row].height = 30

            # ---- Answers sheet styles ----
            answers_question_start_col = 9 if is_test else 6
            last_answers_col = ws_answers.max_column
            last_answers_row = ws_answers.max_row

            for col_idx in range(1, last_answers_col + 1):
                header_cell = ws_answers.cell(row=1, column=col_idx)
                header_cell.fill = header_fill
                header_cell.font = white_bold_font
                header_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                header_cell.border = thin_border
            ws_answers.row_dimensions[1].height = 28

            for row_idx in range(2, last_answers_row + 1):
                for col_idx in range(1, last_answers_col + 1):
                    cell = ws_answers.cell(row=row_idx, column=col_idx)
                    cell.font = regular_font
                    cell.border = thin_border
                    if row_idx % 2 == 0:
                        cell.fill = even_row_fill

                    if col_idx >= answers_question_start_col:
                        cell.alignment = Alignment(horizontal='left', vertical='top', wrap_text=True)
                    elif col_idx in (2, 3, 4, 5, 6, 7, 8):
                        cell.alignment = Alignment(horizontal='center', vertical='center')
                    else:
                        cell.alignment = Alignment(horizontal='left', vertical='center')

                    if isinstance(cell.value, (int, float)) and (
                        (is_test and col_idx == 6)
                    ):
                        cell.number_format = '0.0"%"'

                    if is_test and col_idx >= answers_question_start_col:
                        answer_text = str(cell.value or '')
                        if '[Верно]' in answer_text:
                            cell.fill = answer_correct_fill
                            cell.font = answer_correct_font
                        elif '[Неверно]' in answer_text or '[Частично]' in answer_text:
                            # Частичный зачёт — тоже не закрытый вопрос: подсвечиваем
                            # как незачёт, а сам балл виден рядом в тексте ячейки.
                            cell.fill = answer_incorrect_fill
                            cell.font = answer_incorrect_font
                        elif '[Нет ответа]' in answer_text:
                            cell.fill = answer_empty_fill
                            cell.font = answer_empty_font

                status_cell = ws_answers.cell(row=row_idx, column=3)
                status_text = str(status_cell.value or '').strip().lower()
                if status_text == 'пройден':
                    status_cell.fill = status_done_fill
                else:
                    status_cell.fill = status_pending_fill
                status_cell.font = Font(color='1F2937', bold=True)

            ws_answers.column_dimensions['A'].width = 28
            ws_answers.column_dimensions['B'].width = 10
            ws_answers.column_dimensions['C'].width = 14
            ws_answers.column_dimensions['D'].width = 20
            ws_answers.column_dimensions['E'].width = 12
            if is_test:
                ws_answers.column_dimensions['F'].width = 14
                ws_answers.column_dimensions['G'].width = 12
                ws_answers.column_dimensions['H'].width = 12
            for col_idx in range(answers_question_start_col, last_answers_col + 1):
                ws_answers.column_dimensions[get_column_letter(col_idx)].width = 38

            ws_answers.freeze_panes = 'I2' if is_test else 'F2'
            ws_answers.auto_filter.ref = f"A1:{get_column_letter(last_answers_col)}{max(1, last_answers_row)}"

            # ---- Test scores sheet styles ----
            if ws_scores is not None:
                last_scores_col = ws_scores.max_column
                last_scores_row = ws_scores.max_row

                for col_idx in range(1, last_scores_col + 1):
                    header_cell = ws_scores.cell(row=1, column=col_idx)
                    header_cell.fill = header_fill
                    header_cell.font = white_bold_font
                    header_cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                    header_cell.border = thin_border
                ws_scores.row_dimensions[1].height = 28

                for row_idx in range(2, last_scores_row + 1):
                    for col_idx in range(1, last_scores_col + 1):
                        cell = ws_scores.cell(row=row_idx, column=col_idx)
                        cell.font = regular_font
                        cell.border = thin_border
                        if row_idx % 2 == 0:
                            cell.fill = even_row_fill

                        if col_idx in (1,):
                            cell.alignment = Alignment(horizontal='left', vertical='center')
                        else:
                            cell.alignment = Alignment(horizontal='center', vertical='center')

                        # H — «Общий балл, %», I — «Итоговая оценка» (та же шкала 0–100)
                        if col_idx in (8, 9) and isinstance(cell.value, (int, float)):
                            cell.number_format = '0.0"%"'

                    status_cell = ws_scores.cell(row=row_idx, column=3)
                    status_text = str(status_cell.value or '').strip().lower()
                    if status_text.startswith('пройден'):
                        status_cell.fill = status_done_fill
                    else:
                        status_cell.fill = status_pending_fill
                    status_cell.font = Font(color='1F2937', bold=True)

                for column_letter, width in (
                    ('A', 28), ('B', 10), ('C', 20), ('D', 18), ('E', 18), ('F', 12),
                    ('G', 14), ('H', 14), ('I', 16), ('J', 20), ('K', 18), ('L', 12), ('M', 12)
                ):
                    ws_scores.column_dimensions[column_letter].width = width
                ws_scores.freeze_panes = 'C2'
                ws_scores.auto_filter.ref = f"A1:{get_column_letter(last_scores_col)}{max(1, last_scores_row)}"

            output = BytesIO()
            wb.save(output)
            output.seek(0)

            safe_title = re.sub(r'[^A-Za-zА-Яа-я0-9 _-]+', '', title).strip().replace(' ', '_')
            if not safe_title:
                safe_title = f'survey_{survey_id}'
            filename = f"{safe_title[:64]}_stats.xlsx"

            return send_file(
                output,
                as_attachment=True,
                download_name=filename,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            )
        except Exception as e:
            logging.error(f"Error in export_survey_statistics_excel: {e}", exc_info=True)
            return jsonify({"error": f"Internal server error"}), 500

    return bp
