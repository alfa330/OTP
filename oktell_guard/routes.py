"""HTTP раздела «Ограничитель Перезвона».

Blueprint, как у вики и «Обращений»: зависимости приходят аргументами, обратный
импорт из bot_schedule2 сюда был бы циклом.

Два вида роутов:

* **агентские** (`/config`, `/version`, `/heartbeat`, `/violations`) — их дёргает
  exe на машине сотрудника. Сессии портала у него нет, поэтому здесь работает
  общий токен агента (переменная окружения). Данные в этих ответах не секретные
  (адрес клиента, порог, пин сертификата), но писать в журнал должен всё же не
  кто угодно.
* **разделa** (`/settings`, `/employees`, `/report`, `/release`) — обычная
  авторизация портала плюс права: читают глава СЗоВ, глобальные админы и СВ
  СЗоВ, правят (`manage=True`) только первые двое. Разметку прав держит
  `access.py`, здесь — только расстановка `manage=` по роутам.
"""

import hashlib
import logging
import os
import secrets
import time
from datetime import date, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request

from . import access, queries, verify

AGENT_TOKEN_ENV = 'OKTELL_GUARD_AGENT_TOKEN'
# Токен публикации: им пользуется сборка, чтобы выложить новую версию сама.
# Отдельный от агентского намеренно — этот пускает к записи файла, и он не
# уезжает ни в один exe.
PUBLISH_TOKEN_ENV = 'OKTELL_GUARD_PUBLISH_TOKEN'
RELEASE_BUCKET_ENV = ('GOOGLE_CLOUD_STORAGE_BUCKET_AGENTS', 'GOOGLE_CLOUD_STORAGE_BUCKET_TASKS',
                      'GOOGLE_CLOUD_STORAGE_BUCKET')
RELEASE_PREFIX = 'oktell_guard'
# Час, а не 15 минут как у аватаров: агент может проснуться на медленной машине,
# и протухшая на полпути ссылка означала бы, что обновление не доедет.
DOWNLOAD_URL_TTL_MINUTES = 60


def release_bucket_name() -> str:
    for name in RELEASE_BUCKET_ENV:
        value = (os.getenv(name) or '').strip()
        if value:
            return value
    return ''


# ─────────────────────────────────────────────────────────────────────────────
# Защита публикации
#
# Право выложить версию — самое опасное в системе: файл уезжает на все машины
# операторов. Поэтому здесь не только ключ, но и проверки того, ЧТО публикуют,
# и защита от перебора.
# ─────────────────────────────────────────────────────────────────────────────

MIN_PUBLISH_TOKEN_LENGTH = 24
MAX_RELEASE_BYTES = 80 * 1024 * 1024
MIN_RELEASE_BYTES = 1 * 1024 * 1024
PUBLISH_FAIL_LIMIT = 5
PUBLISH_FAIL_WINDOW_S = 900

_publish_failures = {}


def token_is_strong(token) -> bool:
    """Короткий или предсказуемый ключ здесь недопустим: его подберут.

    Публикация с таким ключом не включается вовсе — молча работать
    «почти защищённо» хуже, чем не работать и сказать об этом в лог.
    """
    value = str(token or '').strip()
    if len(value) < MIN_PUBLISH_TOKEN_LENGTH or not value.isascii():
        return False
    # Одни буквы или одни цифры — это не случайная строка.
    return not value.isalpha() and not value.isdigit()


def is_windows_executable(content) -> bool:
    """Публиковать можно только Windows-программу: иначе одна перепутанная
    кнопка разошлёт операторам произвольный файл, и агенты честно поставят
    его вместо себя."""
    return bool(content) and content[:2] == b'MZ'


def is_valid_version(version) -> bool:
    """Номер версии — только цифры и точки: агенты сравнивают его численно."""
    value = str(version or '').strip()
    if not value or len(value) > 32:
        return False
    parts = value.split('.')
    return 1 < len(parts) <= 4 and all(part.isdigit() and len(part) <= 4 for part in parts)


def note_publish_failure(remote_ip, now) -> int:
    """Счётчик неудачных попыток на адрес. Возвращает их число в окне."""
    bucket = [ts for ts in _publish_failures.get(remote_ip, []) if now - ts < PUBLISH_FAIL_WINDOW_S]
    bucket.append(now)
    _publish_failures[remote_ip] = bucket
    return len(bucket)


def publish_locked(remote_ip, now) -> bool:
    bucket = [ts for ts in _publish_failures.get(remote_ip, []) if now - ts < PUBLISH_FAIL_WINDOW_S]
    return len(bucket) >= PUBLISH_FAIL_LIMIT


def build_oktell_guard_blueprint(*, db, require_api_key, build_cors_preflight_response,
                                 resolve_requester, gcs_client_factory=None,
                                 oktell_query=None):
    """oktell_query(sql) -> список строк из базы Oktell.

    Нужна для сверки: присланный выброс проверяется по истории статусов самой
    АТС. Без неё факты сохраняются со статусом «не проверено» и в отчёт не
    попадают — доверять словам программы с чужого компьютера нельзя.
    """
    bp = Blueprint('oktell_guard', __name__, url_prefix='/api/oktell_guard')

    # ── вспомогательное ─────────────────────────────────────────────────────

    def agent_authorized() -> bool:
        """Пропуск агента: общий токен сборки ИЛИ личный токен сотрудника.

        Личные обязательно: файл, скачанный из раздела, несёт в имени именно
        личный токен, и агент шлёт его. Пока проверка знала только общий,
        КАЖДЫЙ скачанный агент получал 401 и был мёртв с рождения — это и
        случилось на первой живой установке.
        """
        provided = (request.headers.get('X-Agent-Token') or '').strip()
        expected = (os.getenv(AGENT_TOKEN_ENV) or '').strip()

        if not expected:
            # Токен не задан — раздел ещё настраивают. Пускаем, но говорим об
            # этом в лог: молча работать «без охраны» хуже, чем шумно.
            logging.warning("%s не задан — агентские роуты открыты", AGENT_TOKEN_ENV)
            return True
        if not provided:
            return False
        if secrets.compare_digest(provided, expected):
            return True

        digest = hashlib.sha256(provided.encode('utf-8')).hexdigest()
        try:
            with db._get_cursor() as cursor:
                return bool(queries.user_by_token(cursor, digest))
        except Exception:
            logging.exception("Ограничитель Перезвона: не удалось проверить личный токен")
            return False

    def agent_route(rule, methods=('GET',), public=False):
        """public=True — ручка без токена.

        Такая ровно одна: /version. Если её закрыть, смена токена превратится в
        ловушку — агенты со старым токеном не смогут даже узнать, что вышла
        новая версия, и застрянут на прежней навсегда. Номер версии и ссылка на
        файл секретом не являются.
        """
        all_methods = tuple(methods) + ('OPTIONS',)

        def decorator(handler):
            @bp.route(rule, methods=list(all_methods), endpoint=handler.__name__)
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                if not public and not agent_authorized():
                    return jsonify({"error": "Агент не авторизован"}), 401
                try:
                    return handler(*args, **kwargs)
                except Exception:
                    logging.exception("Ограничитель Перезвона: ошибка в %s", rule)
                    return jsonify({"error": "Внутренняя ошибка"}), 500
            return wrapper
        return decorator

    def section_route(rule, methods=('GET',), manage=False):
        all_methods = tuple(methods) + ('OPTIONS',)

        def decorator(handler):
            @bp.route(rule, methods=list(all_methods), endpoint=handler.__name__)
            @require_api_key
            @wraps(handler)
            def wrapper(*args, **kwargs):
                if request.method == 'OPTIONS':
                    return build_cors_preflight_response()
                try:
                    requester_id, _row, error = resolve_requester()
                    if error:
                        message, status = error
                        return jsonify({"error": message}), status
                    # Контекст берём запросом, а не из строки базы: у неё
                    # позиционные поля, и обращение по имени молча давало None —
                    # раздел закрывался даже суперадмину.
                    with db._get_cursor() as cursor:
                        requester = queries.access_context(cursor, requester_id)
                    if not requester:
                        return jsonify({"error": "Пользователь не найден"}), 404
                    # Гейт здесь, а не в каждом обработчике: спрятанный пункт
                    # меню доступом не является, раздел открывается и прямым
                    # адресом.
                    if not access.can_view_section(requester):
                        return jsonify({"error": "Раздел вам не открыт"}), 403
                    if manage and not access.can_manage_settings(requester):
                        return jsonify({"error": "Недостаточно прав"}), 403
                    return handler(requester_id, requester, *args, **kwargs)
                except Exception:
                    logging.exception("Ограничитель Перезвона: ошибка в %s", rule)
                    return jsonify({"error": "Внутренняя ошибка"}), 500
            return wrapper
        return decorator

    def signed_download_url(release, filename=None):
        """Ссылка на файл в GCS. Отдаём её и агенту, и браузеру — качают они
        напрямую у Google, а не через наш единственный инстанс.

        filename задаёт имя, под которым файл сохранится: в него уезжает личный
        токен сотрудника. Сам объект в хранилище при этом один на всех.
        """
        if not release or not release.get('gcs_bucket') or not release.get('gcs_path'):
            return None
        factory = gcs_client_factory
        if factory is None:
            return None
        try:
            blob = factory().bucket(release['gcs_bucket']).blob(release['gcs_path'])
            extra = {}
            if filename:
                extra['response_disposition'] = f'attachment; filename="{filename}"'
            return blob.generate_signed_url(
                version="v4",
                expiration=timedelta(minutes=DOWNLOAD_URL_TTL_MINUTES),
                method="GET",
                **extra,
            )
        except Exception:
            logging.exception("Ограничитель Перезвона: не удалось подписать ссылку на файл")
            return None

    def check_violation(item, sip_number):
        """Сверить один присланный факт с историей Oktell.

        Недоступна история — статус «не проверено»: молча засчитывать нельзя,
        но и терять факт из-за недоступности прокси тоже неправильно.
        """
        if oktell_query is None:
            return verify.PENDING, 'сверка с Oktell не подключена'
        moment = verify._parse_time(item.get('at') or item.get('happened_at'))
        if not moment:
            return verify.PENDING, 'в присланном факте нет разбираемого времени'
        window = max(int(item.get('seconds') or 0), int(item.get('threshold_s') or 0), 60)
        try:
            rows = oktell_query(verify.build_history_sql(sip_number, moment, window))
        except Exception:
            logging.exception("Ограничитель Перезвона: история Oktell недоступна")
            return verify.PENDING, 'история Oktell в момент проверки недоступна'
        return verify.verdict(item, rows)

    # ── роуты агента ────────────────────────────────────────────────────────

    @agent_route('/config')
    def oktell_guard_agent_config():
        """Настройки для конкретного сотрудника: порог персональный, если задан."""
        sip = (request.args.get('login') or request.args.get('sip') or '').strip()
        with db._get_cursor() as cursor:
            settings = queries.get_settings(cursor)
            personal = queries.personal_rule_by_sip(cursor, sip) if sip else None
        payload = queries.agent_config_payload(settings, personal)
        payload['known_operator'] = bool(personal)
        return jsonify(payload)

    @agent_route('/version', public=True)
    def oktell_guard_agent_version():
        with db._get_cursor() as cursor:
            release = queries.current_release(cursor)
        if not release:
            return jsonify({"version": "", "url": "", "sha256": ""})
        return jsonify({
            "version": release['version'],
            "sha256": release['sha256'],
            "size": release['size_bytes'],
            "url": signed_download_url(release) or "",
        })

    @agent_route('/heartbeat', methods=('POST',))
    def oktell_guard_agent_heartbeat():
        data = request.get_json(silent=True) or {}
        browser = data.get('browser') or {}
        sip = str(data.get('operator_login') or '').strip()
        with db._get_cursor() as cursor:
            personal = queries.personal_rule_by_sip(cursor, sip) if sip else None
            queries.upsert_agent(cursor, {
                'agent_id': str(data.get('agent_id') or '')[:160] or 'unknown',
                'user_id': (personal or {}).get('user_id'),
                'sip_number': sip[:64],
                'hostname': str(data.get('hostname') or '')[:128],
                'windows_user': str(data.get('windows_user') or '')[:128],
                'agent_version': str(data.get('version') or '')[:32],
                'managed_window': bool(browser.get('managed_window')),
                'session_present': bool(browser.get('session_present')),
                'unmanaged_count': len(data.get('unmanaged_windows') or []),
            })
            # Пометка «в этот день человек работал в Oktell через наше
            # приложение». Пока это только отметка в разделе; засчитывать по
            # ней смену — отдельное решение на будущее.
            if browser.get('managed_window') and browser.get('session_present'):
                queries.mark_managed_day(cursor, (personal or {}).get('user_id'))
            settings = queries.get_settings(cursor)
        return jsonify({"ok": True, "poll_interval_s": int(settings.get('heartbeat_interval_s') or 60)})

    def reporter_by_header():
        """Кто прислал: по личному токену. Общий (вшитый в сборку) человека не
        называет — тогда отправитель остаётся неизвестным, и это видно."""
        raw = (request.headers.get('X-Agent-Token') or '').strip()
        if not raw:
            return None
        digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        with db._get_cursor() as cursor:
            return queries.user_by_token(cursor, digest)

    @agent_route('/violations', methods=('POST',))
    def oktell_guard_agent_violations():
        """Факты выбросов пачкой. Повтор той же записи игнорируется по client_key:
        агент переотправляет при обрыве связи, а в отчёте это один выброс."""
        data = request.get_json(silent=True) or {}
        items = data.get('violations') or []
        sip = str(data.get('operator_login') or '').strip()
        saved = 0
        rejected = 0
        reporter = reporter_by_header()
        with db._get_cursor() as cursor:
            personal = queries.personal_rule_by_sip(cursor, sip) if sip else None
            for item in items[:50]:
                status, note = check_violation(item, str(item.get('login') or sip))
                # Отправитель и субъект могут не совпадать законно: на одной
                # машине посменно работают разные операторы, а агента скачивал
                # кто-то один. Поэтому это не отказ, а пометка — достоверность
                # всё равно решает сверка с историей Oktell.
                if reporter and reporter.get('sip_number') and sip and reporter['sip_number'] != sip:
                    note = f"{note}; агент принадлежит {reporter['name']}, факт про номер {sip}"
                created = queries.record_violation(cursor, {
                    'user_id': (personal or {}).get('user_id'),
                    'sip_number': str(item.get('login') or sip)[:64],
                    # Время приводим к местному ЗДЕСЬ: браузер шлёт его по
                    # Гринвичу, и без перевода запись ложилась в базу на пять
                    # часов раньше — отчёт врал, а сверка ничего не находила.
                    'happened_at': verify._parse_time(item.get('at')),
                    'seconds': int(item.get('seconds') or 0),
                    'threshold_s': int(item.get('threshold_s') or 0),
                    'reason': str(item.get('reason') or 'recall_timeout')[:64],
                    'hostname': str(data.get('hostname') or '')[:128],
                    'windows_user': str(data.get('windows_user') or '')[:128],
                    'agent_version': str(data.get('version') or '')[:32],
                    'dry_run': bool(item.get('dry_run') or data.get('dry_run')),
                    'client_key': str(item.get('key') or '')[:128],
                    'verified': status,
                    'verified_note': note,
                    'reported_by': (reporter or {}).get('user_id'),
                })
                saved += 1 if created else 0
                if status == verify.REJECTED:
                    rejected += 1
        return jsonify({"ok": True, "saved": saved, "received": len(items), "rejected": rejected})

    # ── роуты раздела ───────────────────────────────────────────────────────

    @section_route('/settings')
    def oktell_guard_settings_get(requester_id, requester):
        with db._get_cursor() as cursor:
            settings = queries.get_settings(cursor)
            release = queries.current_release(cursor)
        return jsonify({
            "settings": settings,
            "release": release and {
                "version": release['version'],
                "sha256": release['sha256'],
                "size": release['size_bytes'],
                "uploaded_at": release['uploaded_at'],
                "notes": release['notes'],
            },
            "can_manage": access.can_manage_settings(requester),
        })

    @section_route('/settings', methods=('PUT', 'POST'), manage=True)
    def oktell_guard_settings_save(requester_id, requester):
        payload = request.get_json(silent=True) or {}
        with db._get_cursor() as cursor:
            settings = queries.save_settings(cursor, payload, updated_by=requester_id)
        return jsonify({"settings": settings})

    @section_route('/employees')
    def oktell_guard_employees(requester_id, requester):
        scope = access.visible_department_code(requester)
        if scope == '':
            return jsonify({"employees": []})
        since = date.today() - timedelta(days=30)
        with db._get_cursor() as cursor:
            employees = queries.list_employees(cursor, department_code=scope, since=since)
        return jsonify({"employees": employees})

    @section_route('/employees/bulk', methods=('POST',), manage=True)
    def oktell_guard_employees_bulk(requester_id, requester):
        """Массовое изменение выделенных строк.

        threshold: число — задать, 'default' — сбросить к общему, отсутствует —
        не трогать. Три состояния, потому что «не трогать» и «сбросить» — разные
        намерения, и путать их нельзя: галочка «выключить» иначе обнуляла бы
        всем персональные пороги.
        """
        payload = request.get_json(silent=True) or {}
        with db._get_cursor() as cursor:
            changed = queries.bulk_set_rules(
                cursor,
                payload.get('user_ids'),
                threshold_s=payload.get('threshold', None),
                enabled=payload.get('enabled', None),
                updated_by=requester_id,
            )
        return jsonify({"ok": True, "changed": changed})

    @section_route('/report')
    def oktell_guard_report(requester_id, requester):
        scope = access.visible_department_code(requester)
        if scope == '':
            return jsonify({"rows": []})
        today = date.today()
        date_from = request.args.get('from') or str(today - timedelta(days=13))
        date_to = request.args.get('to') or str(today)
        with db._get_cursor() as cursor:
            rows = queries.report(cursor, date_from, date_to, department_code=scope)
            rejected = queries.rejected_count(cursor, date_from, date_to, department_code=scope)
        return jsonify({"rows": rows, "from": date_from, "to": date_to, "rejected": rejected})

    @section_route('/download')
    def oktell_guard_download(requester_id, requester):
        """«Скачать агента»: ссылка + личный токен в ИМЕНИ файла.

        Файл в хранилище один на всех, а токен персональный — он уезжает в имя
        скачиваемого файла, агент читает его при первом запуске и запоминает.
        Так присланное всегда подписано конкретным человеком: подделка
        перестаёт быть анонимной, а токен можно отозвать. У нас хранится только
        отпечаток, самого значения мы не знаем.

        Ручка осталась на уровне ПРОСМОТРА и после выдачи раздела супервайзерам
        (31.08.2026), хотя формально она пишет: установщик операторам раздаёт как
        раз СВ, а сам файл и так отдаёт публичная /version — спрятать exe от СВ
        всё равно невозможно. Плата — личный токен на имя СВ, то есть ровно те же
        права, что уже есть у каждой машины с установленным агентом.
        """
        with db._get_cursor() as cursor:
            release = queries.current_release(cursor)
            if not release:
                return jsonify({"error": "Версия агента ещё не загружена"}), 404
            token = secrets.token_urlsafe(18).replace('-', '').replace('_', '')
            queries.issue_token(
                cursor, requester_id, hashlib.sha256(token.encode('utf-8')).hexdigest(),
                note='выдан при скачивании из раздела',
            )
        # Имя должно выглядеть осмысленно: случайный хвост в имени файла
        # человек читает как «вирус», а это и так первое, что он видит рядом с
        # предупреждением Windows о неизвестном издателе.
        filename = f"Oktell-Perezvon-Setup-{token}.exe"
        url = signed_download_url(release, filename=filename)
        if not url:
            return jsonify({"error": "Ссылка на файл недоступна"}), 503
        return jsonify({
            "url": url,
            "filename": filename,
            "version": release['version'],
            "sha256": release['sha256'],
        })

    def publish_authorized() -> bool:
        expected = (os.getenv(PUBLISH_TOKEN_ENV) or "").strip()
        if not expected:
            return False
        if not token_is_strong(expected):
            logging.error(
                "%s слишком слаб (нужно от %d символов, латиница вперемешку с цифрами) — публикация выключена",
                PUBLISH_TOKEN_ENV, MIN_PUBLISH_TOKEN_LENGTH,
            )
            return False
        provided = (request.headers.get("X-Publish-Token") or "").strip()
        # Сравнение постоянного времени: обычное == отвечает тем быстрее, чем
        # раньше расходятся строки, и по этой разнице ключ подбирают посимвольно.
        return secrets.compare_digest(provided, expected)

    def store_release(upload, version, notes, uploaded_by=None, content=None):
        """Положить файл в GCS и отметить версию текущей."""
        bucket_name = release_bucket_name()
        if not bucket_name or gcs_client_factory is None:
            return {"error": "Хранилище файлов не настроено"}, 503
        if content is None:
            content = upload.read()
        if not content:
            return {"error": "Пустой файл"}, 400
        digest = hashlib.sha256(content).hexdigest()
        blob_path = f"{RELEASE_PREFIX}/OktellRecallGuard-{version}.exe"
        try:
            blob = gcs_client_factory().bucket(bucket_name).blob(blob_path)
            blob.upload_from_string(content, content_type='application/vnd.microsoft.portable-executable')
        except Exception:
            logging.exception("Ограничитель Перезвона: файл версии не загрузился в хранилище")
            return {"error": "Не удалось загрузить файл в хранилище"}, 502
        with db._get_cursor() as cursor:
            queries.add_release(
                cursor,
                version=version,
                filename=getattr(upload, 'filename', None) or 'OktellRecallGuard.exe',
                sha256=digest,
                size_bytes=len(content),
                gcs_bucket=bucket_name,
                gcs_path=blob_path,
                notes=notes,
                uploaded_by=uploaded_by,
            )
        return {"ok": True, "version": version, "sha256": digest, "size": len(content)}, 200

    @bp.route('/publish', methods=['POST', 'OPTIONS'], endpoint='oktell_guard_publish')
    def oktell_guard_publish():
        """Публикация версии самой сборкой — чтобы человеку не приходилось
        ничего загружать руками. Собрал exe — он сам уехал в хранилище, и
        агенты обновились по манифесту."""
        if request.method == "OPTIONS":
            return build_cors_preflight_response()

        remote_ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()
        now = time.time()
        if publish_locked(remote_ip, now):
            logging.error("Публикация: адрес %s заблокирован после %d неудачных попыток",
                          remote_ip, PUBLISH_FAIL_LIMIT)
            return jsonify({"error": "Слишком много неудачных попыток, попробуйте позже"}), 429

        if not publish_authorized():
            failures = note_publish_failure(remote_ip, now)
            # Сам ключ в лог не попадает никогда — только факт и адрес.
            logging.error("Публикация: неверный ключ с адреса %s (попытка %d)", remote_ip, failures)
            return jsonify({"error": "Публикация не авторизована"}), 401

        upload = request.files.get("file")
        version = (request.form.get("version") or "").strip()
        notes = (request.form.get("notes") or "").strip()
        if not upload or not version:
            return jsonify({"error": "Нужны файл и номер версии"}), 400
        if not is_valid_version(version):
            return jsonify({"error": "Номер версии должен быть вида 1.2.3"}), 400

        content = upload.read()
        if not is_windows_executable(content):
            logging.error("Публикация: файл не является Windows-программой (адрес %s)", remote_ip)
            return jsonify({"error": "Это не Windows-программа"}), 400
        if not (MIN_RELEASE_BYTES <= len(content) <= MAX_RELEASE_BYTES):
            return jsonify({"error": "Неправдоподобный размер файла"}), 400

        expected_sha = (request.form.get("sha256") or "").strip().lower()
        actual_sha = hashlib.sha256(content).hexdigest()
        if expected_sha and not secrets.compare_digest(expected_sha, actual_sha):
            logging.error("Публикация: отпечаток не совпал с заявленным (адрес %s)", remote_ip)
            return jsonify({"error": "Отпечаток файла не совпал с заявленным"}), 400

        payload, status = store_release(upload, version, notes, content=content)
        if status == 200:
            logging.info("Публикация: версия %s выложена с адреса %s, отпечаток %s…",
                         version, remote_ip, actual_sha[:12])
        return jsonify(payload), status

    @section_route('/release', methods=('POST',), manage=True)
    def oktell_guard_release_upload(requester_id, requester):
        """Ручная загрузка версии — запасной путь, если публикация из сборки
        почему-то недоступна."""
        upload = request.files.get('file')
        version = (request.form.get('version') or '').strip()
        notes = (request.form.get('notes') or '').strip()
        if not upload or not version:
            return jsonify({"error": "Нужны файл и номер версии"}), 400
        payload, status = store_release(upload, version, notes, uploaded_by=requester_id)
        return jsonify(payload), status

    return bp
