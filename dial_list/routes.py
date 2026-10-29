# -*- coding: utf-8 -*-
"""HTTP раздела «Удаленный КЦ» — до 07.10.2026 «Обзвон из телефона» (Flask Blueprint).

Зависимости приходят аргументами фабрики, а не импортом `bot_schedule2`: тот
сам подключает этот модуль (как op_funnel/routes.py, oktell_guard/routes.py).

Ручки телефона (bearer оператора, как /api/operator/sip_settings). Телефон с 3.22.34 в каждом
запросе называет версию заголовком X-ICore-Phone-Version. Автосброс на гудках (простой таймер,
13.10.2026) — только телефону 3.22.35 и новее (service.phone_has_ring_timer); 3.22.33 (заголовка
нет) и 3.22.34 получают ring_timeout_sec 0, а на событие ring_timeout — сразу hangup: false, без
базы и без Binotel:
    GET  /api/operator/dial_list                             текущая порция и состояние (ring_timeout_sec —
                                                             через сколько секунд после ответа плеча телефон
                                                             спрашивает, класть ли его (гудки отдела × 5 + 9),
                                                             active_attempt.lead_id — с кем идёт разговор;
                                                             запросу без заголовка версии active_attempt —
                                                             и с кончившимся плечом (ended), как раньше;
                                                             speech_guard, announcement_ms, announcement_sit,
                                                             announcement_mode — для 3.22.34, всегда
                                                             «выключено»: разбор звука снят 13.10.2026;
                                                             last_error_kind строки пачки — почему не удался
                                                             её последний звонок; у строк пачки и у
                                                             active_attempt — last_comment, last_comment_label,
                                                             last_comment_outcome: последний комментарий
                                                             этого оператора по водителю; last_attempt.
                                                             auto_hangup — почему плечо положил сам телефон)
    GET  /api/operator/dial_list/progress                    «Мой прогресс»: счётчики за месяц и сегодня
                                                             (outcomes[].subtypes — счётчики по типам итога,
                                                             conversion — конверсия за базу месяца, salary —
                                                             оплата по конверсии за ту же базу (14.10.2026):
                                                             диалоги × цена диалога по тарифу, из чего она
                                                             сложилась — обзвонено, не подняли, подписали,
                                                             следующая ступень, тариф, — и today: свои диалоги
                                                             и подписавшие за сегодня)
    GET  /api/operator/dial_list/rating                      «Рейтинг» дня (14.10.2026): с 21:00 до 10:00 по
                                                             Алматы — места операторов отдела по заработку
                                                             дня (у коллег — диалоги, подписавшие, конверсия;
                                                             сумма — только своя), до 21:00 — отсчёт до итогов
                                                             (seconds_to_open) и только свои числа дня
    GET  /api/operator/dial_list/worked[?q=]                 вкладки итогов: отработанные за месяц
                                                             (items[].subtype_name — тип итога,
                                                             items[].can_call_again / call_again_block — можно
                                                             ли «Позвонить ещё раз» и почему нет, signed —
                                                             подписал, can_recall — «Перезвонить» телефонов до
                                                             3.22.33 включительно; q — поиск по ФИО: count
                                                             вкладок — найденные, в ответе q — нормализованный;
                                                             items[].last_comment, last_comment_label,
                                                             last_comment_outcome — как в состоянии, comment —
                                                             как был; items[].auto_hangup — «announcement»:
                                                             сообщение сети, подпись «Абонент недоступен»)
    GET  /api/operator/dial_list/script                      скрипт разговора отдела (текст + вопросы)
    GET  /api/operator/dial_list/attempts/<attempt_id>/live  набираем / разговор / финал по Binotel — из общего
                                                             опроса компании (один запрос call-details на всех
                                                             не чаще раза в 3 с); ANSWER, пока плечо идёт, —
                                                             talking, а не финал (13.10.2026)
    POST /api/operator/dial_list/next                        выдать следующую порцию (подписавших сервер
                                                             перед выдачей отсеивает сам); строка, исход которой
                                                             ещё едет от АТС (плечо кончилось), «Ещё» не держит
                                                             и переезжает в новую выдачу
    POST /api/operator/dial_list/items/<assignment_id>/call  позвонить по строке (attempt.lead_id — с кем;
                                                             attempt.last_comment, last_comment_label,
                                                             last_comment_outcome — как в состоянии). Держит
                                                             только звонок, плечо которого идёт; кончившееся
                                                             (ended) — нет, но тому же водителю — после исхода.
                                                             Отказ АТС — только кодом, без текста Binotel; в
                                                             ответе error_kind: line | network | pbx_busy |
                                                             rate | pbx (13.10.2026; кроме pbx — водителю не
                                                             в счёт)
    POST /api/operator/dial_list/leads/<lead_id>/recall      «Позвонить ещё раз» своему водителю (последним ему
                                                             звонил этот оператор) → assignment_id + состояние
    POST /api/operator/dial_list/leads/<lead_id>/sign_check  подписал ли водитель — Sapar сейчас, и во время
                                                             звонка: только статус и время проверки (sign),
                                                             row_closed, text; без ИИН, номера и документов
    POST /api/operator/dial_list/attempts/<attempt_id>/phone_event
                                                             {event: ringing|answered|ended|no_leg|operator_hangup|
                                                              ring_timeout, by_operator?, leg_sec?, ring_timeout?,
                                                              ring_sec?, auto_hangup?, talk_detected?,
                                                              announcement_suspected?, announcement_by?} → в ответе
                                                             outcome_required / cancelled — нужен ли итог после
                                                             разговора; на ring_timeout — ещё hangup (класть ли
                                                             плечо; данных Binotel нет или «набирает» старше 3 с —
                                                             false), talking, final (с hangup — Binotel
                                                             подтвердил «не дозвонились», попытка закрыта) и
                                                             data_age_ms (возраст данных Binotel, если решали по
                                                             ним), а решать не по чему, пока плечо идёт, —
                                                             retry_after_sec (спросить снова). У ended с 3.22.34: auto_hangup —
                                                             ring_timeout|announcement, почему плечо положил сам
                                                             телефон (сообщение сети — всегда недозвон, итог не
                                                             нужен); talk_detected — был ли разговор: true —
                                                             итог нужен сразу, и после отбоя оператора, — кроме
                                                             финального «не дозвонились» Binotel (занято, не
                                                             ответил, отмена, канал недоступен, перегрузка);
                                                             false — итог нужен, только если ответ водителя
                                                             подтвердит Binotel (до исхода outcome_required:
                                                             null); «ключа нет» — как раньше. Анализ звука не
                                                             работал — ключ не слать. announcement_suspected —
                                                             распознавание по речи сработало хоть раз (true/
                                                             false), announcement_by — auto|sit|operator, кто
                                                             решил, что это сообщение сети (с auto_hangup:
                                                             announcement; operator — отбой по подсказке, тоже
                                                             недозвон, не отмена): для замера, на счёт не влияют
    POST /api/operator/dial_list/attempts/<attempt_id>/outcome
                                                             {outcome_id, subtype_id?, comment} — итог разговора
                                                             (обязателен перед следующим звонком). subtype_id —
                                                             тип итога: ключа нет (телефон до типов) — без типа;
                                                             ключ есть — у итога с включёнными типами обязателен

Автообзвон Тез КЦ (15.10.2026) — тот же движок, отдел с профилем tez (state.profile): звонит
сам телефон, пока оператор в «Активном»; в «Исходе» — только вручную; в остальных статусах —
никак. Цель лида — выход на линию по TEZ APP (signed_at, sign_status 'online'), а не подписание:
    POST /api/operator/dial_list/items/<assignment_id>/call  тело {auto, status: active|outbound|other}: другой
                                                             статус — 409 error_kind status; водитель уже на
                                                             линии — 409 error_kind goal (строка снята)
    POST /api/operator/dial_list/auto/next                   {status} → next {assignment_id, lead_id, full_name,
                                                             kind: queue|callback|online} или null с reason
                                                             wait|empty и retry_after_sec; + состояние
    POST /api/operator/dial_list/attempts/<attempt_id>/outcome
                                                             ещё due_at (ISO; обязателен у итога с due_kind) и
                                                             offer_ids (офферы, предложенные водителю)
    POST /api/operator/dial_list/attempts/<attempt_id>/phone_event
                                                             ещё event wrapup_expired — время на итог вышло,
                                                             телефон перевёл оператора в «Офлайн»
    POST /api/operator/dial_list/leads/<lead_id>/whatsapp    {template_id, attempt_id?} — шаблон WhatsApp своему
                                                             водителю (ChatApp); не чаще раза в сутки водителю

Ручки руководителя. Круг свой, не от «Настроек SIP»: админ, глава и СВ СЗоВ видят
все отделы раздела, глава отдела — свои отделы, если они в периметре (Binotel,
заведённые настройки обзвона или код из DIAL_LIST_DEPARTMENT_CODES). Админ — как в меню:
роль admin без главенства или суперадмин; глава отдела с базовой ролью admin — руководитель
только своего отдела (с 15.10.2026). Глава отдела из DIAL_LIST_GROUP_REPORT_HEAD_CODES («Регионы»
фронт-офисов) — только две ручки: список отделов (с report_groups) и детализация звонков своих групп:
    GET     /api/dial_list/departments                       отделы раздела в моей зоне (?profile=remote_cc —
                                                             по умолчанию — | tez: автообзвон Тез; СВ Тез КЦ
                                                             видит свой отдел); главе «Регионов» — только id
                                                             и названия + report_groups
    POST    /api/dial_list/departments/<id>/enroll           подключить {profile?} — только админ
    GET/PUT /api/dial_list/departments/<id>/settings         настройки отдела (у Тез — ещё auto_dial,
                                                             call_interval_sec, wrapup_sec, retry_schedule_min,
                                                             callback_own_minutes, followup_after_min; profile
                                                             меняет только админ)
    GET/PUT /api/dial_list/departments/<id>/offers           офферы автообзвона Тез {items: [...]}
    GET/PUT /api/dial_list/departments/<id>/whatsapp         лицензия и разрешённые шаблоны WhatsApp (Тез)
    POST    /api/dial_list/departments/<id>/trips/check      внеочередная проверка поездок в TEZ APP (Тез)
    GET/PUT /api/dial_list/operators/<user_id>/settings      {enabled: true|false|null}
    GET     /api/dial_list/departments/<id>/lines            линии Binotel, кто на них сидит, кого можно
                                                             посадить (candidates — сотрудники других
                                                             отделов: только главе и СВ СЗоВ и суперадмину)
    POST    /api/dial_list/departments/<id>/lines/assign     {user_id, internal_number}; сотрудника
                                                             другого отдела — только они же, иначе 403
    POST    /api/dial_list/departments/<id>/lines/release    {user_id}
    POST    /api/dial_list/departments/<id>/leads/upload     файл ФИО + телефон + ИИН (+period=YYYY-MM);
                                                             ИИН обязателен, после загрузки — проверка подписания;
                                                             у Тез — ФИО + телефон, проверка поездок и тот же
                                                             файл в «Базу лидов ОП TEZ» (mirror)
    GET     /api/dial_list/departments/<id>/leads/summary    сколько загружено/в пуле/подписали (?period=)
    GET     /api/dial_list/departments/<id>/analytics        дашборд базы месяца: воронка, дни, операторы,
                                                             проверка подписания (?period=)
    GET     /api/dial_list/departments/<id>/announcement_stats  точность распознавания сообщения сети по речи
                                                             за ?days= (14): подозрительные плечи против
                                                             финалов Binotel — безопасен ли режим auto
    GET/PUT /api/dial_list/departments/<id>/sign_report/chats  группы для картинки «Подписание водителей по
                                                             городам» (10:00 и 19:00): available — где есть
                                                             бот, selected — выбор отдела с итогом последней
                                                             отправки; PUT {chat_ids}
    GET     /api/dial_list/departments/<id>/sign_report/preview  та же картинка на сейчас (PNG)
    POST    /api/dial_list/departments/<id>/leads/cities     список водителей месяца с городами (СМЗ-ИП):
                                                             multipart file (колонки «ИИН» и «Город», ФИО — по
                                                             желанию; без шапки ИИН — колонка, где ИИН больше
                                                             всего), period=YYYY-MM. Задаёт состав отчёта о
                                                             подписании, ставит города, водителей не из базы
                                                             добавляет без номера (только для отчёта); счёт строк
    GET/PUT /api/dial_list/departments/<id>/outcomes         справочник итогов звонка; у итога subtypes —
                                                             его типы (в PUT ключа нет — типы не трогаются)
    GET/PUT /api/dial_list/departments/<id>/script           скрипт разговора и быстрые вопросы
    POST    /api/dial_list/departments/<id>/script/ai        ИИ: создать скрипт (generate) / оформить текст (polish)
    GET     /api/dial_list/departments/<id>/leads            журнал водителей (фильтры:
                                                             q, stage, operator_id, batch_id, outcome_id,
                                                             outcome_subtype_id — тип итога или none
                                                             (только вместе с outcome_id),
                                                             date_from/date_to — дни звонков, period,
                                                             sign — состояние подписания документов,
                                                             sort, limit, offset)
    GET     /api/dial_list/leads/<lead_id>                   карточка: попытки (attempts[].auto_hangup — плечо
                                                             положил сам телефон: ring_timeout — на гудках,
                                                             announcement — на сообщении сети; это недозвон),
                                                             действия, загрузки, документы
    POST    /api/dial_list/leads/<lead_id>/sign_check        проверить подписание в Sapar сейчас
    POST    /api/dial_list/leads/<lead_id>/requeue           вернуть в список  {note} (подписавшего — нельзя)
    POST    /api/dial_list/leads/<lead_id>/exclude           исключить         {note}
    PUT     /api/dial_list/leads/<lead_id>/note              заметка руководителя
    GET     /api/dial_list/attempts/<attempt_id>/recording   ссылка на запись разговора (Binotel)
    POST    /api/dial_list/departments/<id>/repair/cut-calls починка 12.10.2026: водители, чьи разговоры
                                                             оборвал автосброс, {since: ISO, apply: false|true,
                                                             target: operator|pool} — список либо возврат в
                                                             очередь того же оператора (нет его в отделе —
                                                             в общий список); без номеров и ИИН
    GET     /api/dial_list/departments/<id>/calls/export     детализация звонков за ?date_from=&date_to=
                                                             (YYYY-MM-DD, дни по Алматы, не больше 31) — xlsx:
                                                             звонки, итоги операторов, контекст; номер — маской,
                                                             ИИН нет; звонков нет — 404 с кодом
                                                             DIAL_LIST_CALLS_EXPORT_EMPTY; главе «Регионов» —
                                                             звонки тех, кто в день звонка был в её группах
    GET     /api/dial_list/overview?date=YYYY-MM-DD[&department_id=]
    GET     /api/dial_list/departments/<id>/informer         автоинформатор (15.10.2026): робот на шлюзе
                                                             (в сети, линии) и рассылки отдела
    POST    /api/dial_list/departments/<id>/informer/campaigns  новая рассылка: multipart name, audio (WAV
                                                             24 кГц моно, оригинал — склеивает браузер:
                                                             сначала казахская, потом русская; телефонную
                                                             8 кГц для робота сводит сервер), parts (JSON границ
                                                             частей), recipients (.csv/.xlsx номеров),
                                                             window_start/_end, max_per_minute (1–600; не
                                                             задан — сколько тянут линии робота),
                                                             max_attempts, retry_after_min
    GET     /api/dial_list/departments/<id>/informer/voices  голоса озвучки (Soniox), темпы, предел текста
    POST    /api/dial_list/departments/<id>/informer/tts     {lang: kz|ru, text, voice, speed} → WAV 24 кГц
    POST    /api/dial_list/departments/<id>/informer/ai      ИИ для текста: generate {brief} → {kz, ru};
                                                             polish {text, lang}; translate {text, source, target}
    GET     /api/dial_list/departments/<id>/informer/campaigns/<cid>        аналитика и водители (маска номера)
    POST    /api/dial_list/departments/<id>/informer/campaigns/<cid>/<act>  start | pause | finish
    GET     /api/dial_list/departments/<id>/informer/campaigns/<cid>/audio  запись рассылки (оригинал)
    GET     /api/dial_list/departments/<id>/informer/campaigns/<cid>/export xlsx: водители, звонки, контекст

Binotel (без авторизации, токен в адресе И, по умолчанию, IP сервера Binotel — адрес, который
дописал справа в X-Forwarded-For прокси Render; отказ — WARNING в журнале сервера с адресом,
всей цепочкой и причиной, без токена):
    GET/POST /api/dial_list/webhook/binotel?token=…          «API Call Completed»: форма callDetails[…]
                                                             (как шлёт Binotel) или JSON {callDetails: {…}}

Номер телефона водителя ни одна ручка оператора не отдаёт — это весь смысл
раздела; проверяется тестом.
"""
import json
import logging
import os
import uuid
from datetime import date, datetime
from io import BytesIO

from flask import Blueprint, jsonify, request, send_file

from . import calls_export as calls_export_report
from .service import (PHONE_VERSION_HEADER, PROFILE_REMOTE_CC, PROFILE_TEZ, PROFILES, SUBTYPE_NOT_SENT,
                      DialListError, DialListService, parse_period, pilot_allows, webhook_remote_addr)

log = logging.getLogger(__name__)

LEADS_MAX_FILE_SIZE_MB = 10
LEADS_MAX_FILE_SIZE_BYTES = LEADS_MAX_FILE_SIZE_MB * 1024 * 1024
XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def build_dial_list_blueprint(*, db, require_api_key, build_cors_preflight_response,
                              resolve_requester, is_admin_role, headed_department_ids,
                              is_super_admin_role=None, is_supervisor_role=None,
                              department_code_of=None, service=None, tez_leads_mirror=None,
                              is_global_admin=None, excel_text_warning=None, informer_service=None):
    """resolve_requester() -> (requester_id, requester_row, error|None);
    is_admin_role(role) -> bool; headed_department_ids(requester_id) -> iterable[int];
    is_global_admin(requester_id, role) -> bool — администратор всей компании, а не глава отдела
    с ролью «админ» (у глав роль админская, а зона — свой отдел): только ему перевод отдела между
    разделами (профиль) и подключение к автообзвону Тез; не передан — как is_admin_role;
    is_super_admin_role(role) -> bool — кому, кроме главы СЗоВ, можно сажать на линию
    сотрудника другого отдела (не передан — суперадминов для раздела нет);
    is_supervisor_role(role) -> bool и department_code_of(user_id) -> код отдела —
    по ним СВ СЗоВ получает раздел целиком, а СВ Тез КЦ — свой отдел (не переданы — СВ
    раздела не получают);
    tez_leads_mirror(department_id, requester_id, file_name, raw_bytes, file_ext, period) -> dict —
    тот же файл базы автообзвона Тез ещё и в «Базу лидов ОП TEZ» (владелец, 15.10.2026: одна
    загрузка на обе); не передан — только в раздел;
    excel_text_warning(stream, sqref, sheet_path) — гасит в детализации звонков и выгрузке
    автоинформатора зелёный уголок «Число сохранено как текст» (функция монолита; не передана —
    файл с уголком);
    informer_service — informer.service.InformerService: вкладка «Автоинформатор» (не передан —
    её ручки отвечают 503)."""
    bp = Blueprint('dial_list', __name__)
    svc = service or DialListService(db)
    bp.service = svc  # для тестов и для /api/operator/sip_settings

    # ── вспомогательное ─────────────────────────────────────────────────────
    def _preflight():
        if request.method == 'OPTIONS':
            return build_cors_preflight_response()
        return None

    def _operator():
        requester_id, requester, error = resolve_requester()
        if error:
            message, status = error
            raise DialListError(message, status)
        return int(requester_id), requester

    def _supervisor_department_code(requester_id, role):
        """Код отдела, где числится запросивший, — только у супервайзера (правило —
        svc.full_access_supervisor); у остальных None и без лишнего запроса."""
        if is_supervisor_role is None or department_code_of is None or not is_supervisor_role(role):
            return None
        return department_code_of(requester_id)

    def _section_admin(role, headed):
        """Админ всей компании для раздела — как в меню (isAdminLikeRole в App.jsx): роль admin без
        главенства или суперадмин. Глава отдела с базовой ролью admin — руководитель СВОЕГО отдела:
        до 15.10.2026 сервер считал его админом раздела, и глава «Регионов» через «Детализацию»
        получила бы весь отдел, а не свою группу."""
        if not is_admin_role(role):
            return False
        return not headed or bool(is_super_admin_role and is_super_admin_role(role))

    def _manager(department_id=None):
        """Руководитель раздела и его зона (None — все отделы раздела)."""
        requester_id, requester = _operator()
        role = requester[3]
        # Логин — восьмое поле строки get_user (u.login); по нему работает пилот.
        login = requester[7] if len(requester) > 7 else None
        headed = list(headed_department_ids(requester_id) or [])
        # supervisor_id — у СВ: отдел, которому раздел открыт своим отделом (Тез КЦ, 15.10.2026).
        supervisor_code = _supervisor_department_code(requester_id, role)
        extra = {"supervisor_id": requester_id} if supervisor_code is not None else {}
        scope = svc.manager_scope(_section_admin(role, headed), headed, login=login,
                                  supervisor_department_code=supervisor_code, **extra)
        if scope is not None and not scope:
            raise DialListError("Раздел «Удаленный КЦ» вам недоступен", 403)
        if department_id is not None and scope is not None and int(department_id) not in set(scope):
            raise DialListError("Это не ваш отдел", 403)
        return requester_id, scope

    def _global_admin():
        """Администратор всей компании (is_global_admin). Глава отдела с ролью «админ» им не
        считается: перевести чужой отдел в другой раздел он не должен (ревью 15.10.2026)."""
        requester_id, requester = _operator()
        role = requester[3]
        if is_global_admin is not None:
            return bool(is_global_admin(requester_id, role))
        return bool(is_admin_role(role))

    def _report_groups_or_raise(refusal):
        """Отказ _manager — последняя проверка: глава отдела из DIAL_LIST_GROUP_REPORT_HEAD_CODES
        выгружает отчёты своей группы (svc.report_groups). Нет такого права — прежний отказ."""
        if refusal.status != 403:
            raise refusal
        requester_id, requester = _operator()
        # Пилот раздела (DIAL_LIST_PILOT_LOGINS) действует и здесь, как в manager_scope.
        if not pilot_allows(requester[7] if len(requester) > 7 else None):
            raise refusal
        groups = svc.report_groups(headed_department_ids(requester_id))
        if not groups:
            raise refusal
        return groups

    def _seats_anyone():
        """Может ли запросивший сажать на линию сотрудника ДРУГОГО отдела и снимать
        его: суперадмин, глава или СВ СЗоВ (svc.can_seat_anyone). Зовётся после
        _manager — сам по себе доступа к разделу не даёт."""
        requester_id, requester = _operator()
        role = requester[3]
        is_super = bool(is_super_admin_role and is_super_admin_role(role))
        return svc.can_seat_anyone(is_super, headed_department_ids(requester_id),
                                   supervisor_department_code=_supervisor_department_code(requester_id, role))

    def _phone_version():
        """Версия телефона из заголовка запроса (X-ICore-Phone-Version) — как пришла; None —
        заголовка нет: это выпущенный 3.22.33. Разбирает и решает сервис (phone_guards_speech):
        автосброс на гудках получает только телефон с защитой по речи."""
        return request.headers.get(PHONE_VERSION_HEADER)

    def _error(exc):
        body = {"error": str(exc)}
        # Вид ошибки — телефону (у отказа звонка: line | network | pbx_busy | rate | pbx).
        if getattr(exc, "kind", None):
            body["error_kind"] = exc.kind
        return jsonify(body), exc.status

    def _guard(fn):
        """Единая обработка ошибок: DialListError → её статус, остальное → 500."""
        def wrapper(*args, **kwargs):
            pre = _preflight()
            if pre is not None:
                return pre
            try:
                return fn(*args, **kwargs)
            except DialListError as exc:
                return _error(exc)
            except Exception:
                log.exception("dial_list: %s", request.path)
                return jsonify({"error": "Internal server error"}), 500
        wrapper.__name__ = fn.__name__
        return wrapper

    # ── телефон оператора ───────────────────────────────────────────────────
    @bp.route('/api/operator/dial_list', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_state():
        user_id, _ = _operator()
        return jsonify({"status": "success", **svc.get_state(user_id, phone_version=_phone_version())}), 200

    @bp.route('/api/operator/dial_list/progress', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_progress():
        """«Мой прогресс» на телефоне: счётчики оператора за месяц и за сегодня, без ФИО и номеров;
        salary — оплата по конверсии за базу месяца и из чего она сложилась."""
        user_id, _ = _operator()
        return jsonify({"status": "success", **svc.operator_progress(user_id)}), 200

    @bp.route('/api/operator/dial_list/rating', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_rating():
        """«Рейтинг» на телефоне (владелец, 14.10.2026): места операторов отдела за день по
        заработку — с 21:00 до 10:00 по Алматы, до того отсчёт и только свои числа. Суммы
        коллег сервер не отдаёт. Параметров нет: день выбирает сервер по своим часам."""
        user_id, _ = _operator()
        return jsonify({"status": "success", **svc.operator_rating(user_id)}), 200

    @bp.route('/api/operator/dial_list/worked', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_worked():
        """Вкладки итогов на телефоне: отработанные за месяц водители по итогам оператора;
        ?q= — поиск по ФИО во всех вкладках сразу."""
        user_id, _ = _operator()
        return jsonify({"status": "success", **svc.operator_worked(user_id, q=request.args.get('q', ''))}), 200

    @bp.route('/api/operator/dial_list/script', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_script():
        """Скрипт разговора отдела для телефона: текст и включённые вопросы с ответами."""
        user_id, _ = _operator()
        return jsonify({"status": "success", **svc.operator_script(user_id)}), 200

    @bp.route('/api/operator/dial_list/attempts/<attempt_id>/live', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_attempt_live(attempt_id):
        """Живое состояние попытки для карточки звонка: набираем / разговор / финал (по Binotel)."""
        user_id, _ = _operator()
        return jsonify({"status": "success", **svc.attempt_live(user_id, attempt_id)}), 200

    @bp.route('/api/operator/dial_list/next', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_next():
        # Ответ — то же состояние, что у GET: версия телефона нужна и здесь (ring_timeout_sec).
        user_id, _ = _operator()
        return jsonify({"status": "success",
                        **svc.issue_next_portion(user_id, phone_version=_phone_version())}), 200

    @bp.route('/api/operator/dial_list/items/<assignment_id>/call', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_call(assignment_id):
        user_id, _ = _operator()
        payload = request.get_json(silent=True) or {}
        # Автообзвон Тез (15.10.2026): auto — звонок поставил автоцикл телефона, status — статус
        # оператора в телефоне (active | outbound | other). Телефон удалённого КЦ тела не шлёт —
        # и вызов сервиса у него прежний.
        extra = {}
        if 'auto' in payload or 'status' in payload:
            extra = {"auto": payload.get('auto') is True, "operator_status": payload.get('status')}
        return jsonify({"status": "success", "attempt": svc.start_call(user_id, assignment_id, **extra)}), 200

    @bp.route('/api/operator/dial_list/auto/next', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_auto_next():
        """Автоцикл Тез: кому звонить следующим ({status} — статус оператора в телефоне). Ответ —
        состояние, как у GET, и next {assignment_id, lead_id, full_name, kind} либо next: null с
        reason wait | empty и retry_after_sec — когда спросить снова."""
        user_id, _ = _operator()
        payload = request.get_json(silent=True) or {}
        return jsonify({"status": "success", **svc.auto_next(user_id, status=payload.get('status'),
                                                             phone_version=_phone_version())}), 200

    @bp.route('/api/operator/dial_list/leads/<lead_id>/whatsapp', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_whatsapp(lead_id):
        """Шаблон WhatsApp своему водителю из карточки звонка ({template_id, attempt_id?}). Номер
        водителя телефону не уходит и тут: его знает только сервер, который и шлёт сообщение."""
        user_id, _ = _operator()
        payload = request.get_json(silent=True) or {}
        return jsonify({"status": "success", **svc.send_whatsapp(user_id, lead_id, payload.get('template_id'),
                                                                 attempt_id=payload.get('attempt_id'))}), 200

    @bp.route('/api/operator/dial_list/leads/<lead_id>/recall', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_recall(lead_id):
        """«Позвонить ещё раз»: свой водитель (последним ему звонил этот оператор) снова в
        списке оператора; в ответе — состояние и assignment_id строки, по которой телефон звонит."""
        user_id, _ = _operator()
        return jsonify({"status": "success",
                        **svc.recall_lead(user_id, lead_id, phone_version=_phone_version())}), 200

    @bp.route('/api/operator/dial_list/leads/<lead_id>/sign_check', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_sign_check(lead_id):
        """«Проверить на подписание» — и посреди разговора: статус подписания водителя без ИИН,
        номера и документов; подписал — его строка у оператора снимается со списка. Ручка
        руководителя (lead_sign_check) отдаёт карточку — телефону она не годится."""
        user_id, _ = _operator()
        return jsonify({"status": "success", **svc.operator_sign_check(user_id, lead_id)}), 200

    @bp.route('/api/operator/dial_list/attempts/<attempt_id>/phone_event', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_phone_event(attempt_id):
        user_id, _ = _operator()
        payload = request.get_json(silent=True) or {}
        # by_operator/leg_sec — у события ended: оператор ли положил трубку и сколько
        # секунд плечо было принято телефоном (см. service.phone_event). ring_timeout — у
        # ended: плечо положил сам телефон на гудках; ring_sec — у события ring_timeout:
        # сколько секунд гудков телефон отсчитал. auto_hangup и talk_detected — у ended
        # телефона с 3.22.34: почему плечо положил сам телефон и был ли разговор по звуку;
        # отсутствие ключа talk_detected (None) и false сервис различает — передаём как есть.
        # announcement_suspected и announcement_by (ревью 13.10.2026) — замер распознавания сообщения
        # сети: тоже как пришли, разбирает сервис.
        result = svc.phone_event(user_id, attempt_id, payload.get('event'), payload.get('at'),
                                 by_operator=bool(payload.get('by_operator')), leg_sec=payload.get('leg_sec', 0),
                                 ring_timeout=bool(payload.get('ring_timeout')), ring_sec=payload.get('ring_sec', 0),
                                 auto_hangup=payload.get('auto_hangup'), talk_detected=payload.get('talk_detected'),
                                 phone_version=_phone_version(),
                                 announcement_suspected=payload.get('announcement_suspected'),
                                 announcement_by=payload.get('announcement_by'))
        return jsonify({"status": "success", **result}), 200

    @bp.route('/api/operator/dial_list/attempts/<attempt_id>/outcome', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_outcome(attempt_id):
        user_id, _ = _operator()
        payload = request.get_json(silent=True) or {}
        # Ключ subtype_id шлёт только телефон с типами итога; его отсутствие (старый
        # телефон) и null (тип не выбран) сервис различает — пропуск ключа передаём как есть.
        # due_at и offer_ids (автообзвон Тез) — так же: ключа нет — телефон их не знает.
        extra = {key: payload[key] for key in ('due_at', 'offer_ids') if key in payload}
        result = svc.set_attempt_outcome(user_id, attempt_id, payload.get('outcome_id'), payload.get('comment', ''),
                                         subtype_id=payload.get('subtype_id', SUBTYPE_NOT_SENT), **extra)
        return jsonify({"status": "success", **result}), 200

    # ── руководитель ────────────────────────────────────────────────────────
    @bp.route('/api/dial_list/departments', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def departments():
        """Подключённые отделы в зоне запросившего; админу — ещё и кандидаты на
        подключение (отделы на Binotel, которых в разделе пока нет).

        ?profile=remote_cc (по умолчанию) | tez — отделы одного раздела сайта: «Удаленный КЦ» и
        «Автообзвон Тез» (15.10.2026) живут на одном движке, но списки отделов у них свои.

        Глава «Регионов» (отчёты своей группы) — только в «Удаленном КЦ»: отделы раздела с id и
        названием и report_groups — по ним сайт открывает раздел одной «Детализацией», без вкладок."""
        try:
            _requester_id, scope = _manager()
        except DialListError as refusal:
            if (request.args.get('profile') or PROFILE_REMOTE_CC).strip().lower() != PROFILE_REMOTE_CC:
                raise
            groups = _report_groups_or_raise(refusal)
            return jsonify({
                "status": "success",
                "profile": PROFILE_REMOTE_CC,
                "departments": [{"department_id": d["department_id"], "department_name": d["department_name"],
                                 "profile": PROFILE_REMOTE_CC}
                                for d in svc.list_departments(None, profile=PROFILE_REMOTE_CC)],
                "candidates": [],
                "report_groups": [{"id": g["id"], "name": g["name"], "active": g["active"]} for g in groups],
            }), 200
        profile = (request.args.get('profile') or PROFILE_REMOTE_CC).strip().lower()
        if profile not in PROFILES:
            raise DialListError("profile: remote_cc или tez")
        candidates = []
        if scope is None:
            if profile != PROFILE_TEZ:
                candidates = svc.candidate_departments()
            elif _global_admin():
                # Кандидаты автообзвона — и отделы удалённого КЦ без данных: перевод отдела между
                # разделами — только администратору компании, не главе с ролью «админ».
                candidates = svc.candidate_departments(profile=PROFILE_TEZ)
        return jsonify({
            "status": "success",
            "profile": profile,
            "departments": svc.list_departments(scope, profile=profile),
            "candidates": candidates,
        }), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/enroll', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def department_enroll(department_id):
        """Подключить отдел к разделу ({profile: remote_cc | tez}, по умолчанию remote_cc). Только
        админ: глава видит лишь уже подключённые."""
        requester_id, scope = _manager()
        if scope is not None:
            raise DialListError("Подключить отдел к обзвону может администратор", 403)
        payload = request.get_json(silent=True) or {}
        # К автообзвону Тез подключение может и перевести отдел удалённого КЦ (без данных) —
        # только администратору компании (_global_admin), как и смена профиля в настройках.
        if str(payload.get('profile') or '').strip().lower() == PROFILE_TEZ and not _global_admin():
            raise DialListError("Подключить отдел к автообзвону может администратор", 403)
        extra = {"profile": payload['profile']} if payload.get('profile') else {}
        settings = svc.enroll_department(department_id, changed_by=requester_id, **extra)
        return jsonify({"status": "success", "settings": settings}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/unenroll', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def department_unenroll(department_id):
        """Отключить отдел от раздела (пока по нему нет данных). Только админ."""
        _requester_id, scope = _manager()
        if scope is not None:
            raise DialListError("Отключить отдел может администратор", 403)
        return jsonify({"status": "success", **svc.unenroll_department(department_id)}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/users', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def department_users(department_id):
        """Операторы раздела у отдела с линией и персональным включением обзвона.

        former — кто обзванивал базу отдела, но в нынешнем составе его нет (сняли с
        линии, уволили, перевели): для фильтра журнала, чтобы их звонки можно было
        отобрать так же, как звонки тех, кто на линии сейчас."""
        _manager(department_id)
        users = svc.department_users(department_id)
        return jsonify({
            "status": "success", "users": users,
            "former": svc.former_operators(department_id, exclude_ids=[u["id"] for u in users]),
        }), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/settings', methods=['GET', 'PUT', 'OPTIONS'])
    @require_api_key
    @_guard
    def department_settings(department_id):
        requester_id, scope = _manager(department_id)
        if request.method == 'GET':
            return jsonify({"status": "success", "settings": svc.department_settings(department_id)}), 200
        payload = request.get_json(silent=True) or {}
        # Профиль переводит отдел из раздела в раздел — только администратору компании: глава
        # отдела с ролью «админ» (scope None) перевёл бы чужой отдел (ревью 15.10.2026).
        if payload.get('profile') is not None:
            current = svc.department_profile(department_id)
            if str(payload.get('profile')).strip().lower() != current and (scope is not None or not _global_admin()):
                raise DialListError("Профиль отдела меняет администратор", 403)
        settings = svc.save_department_settings(department_id, payload, changed_by=requester_id)
        return jsonify({"status": "success", "settings": settings}), 200

    @bp.route('/api/dial_list/operators/<int:user_id>/settings', methods=['GET', 'PUT', 'OPTIONS'])
    @require_api_key
    @_guard
    def operator_settings(user_id):
        # Отдел — тот, по которому человек работает в разделе: у сидящего на линии
        # другого отдела это отдел ЛИНИИ, и «как у отдела» значит «как у отдела линии».
        target = svc.dial_operator(user_id)
        if not target:
            raise DialListError("Сотрудник не найден", 404)
        requester_id, _scope = _manager(target.get('department_id'))
        department_id = target.get('department_id')
        dept = svc.department_settings(department_id) if department_id is not None else None
        department_enabled = bool(dept and dept["configured"] and dept["enabled"])
        if request.method == 'GET':
            personal = svc.user_setting(user_id)
            return jsonify({
                "status": "success", "user_id": int(user_id), "enabled": personal,
                "department_enabled": department_enabled,
                "effective": personal if personal is not None else department_enabled,
            }), 200
        payload = request.get_json(silent=True) or {}
        enabled = payload.get('enabled', None)
        if enabled is not None and not isinstance(enabled, bool):
            raise DialListError("enabled: true, false или null (как у отдела)")
        saved = svc.save_user_setting(user_id, enabled, changed_by=requester_id)
        saved["department_enabled"] = department_enabled
        saved["effective"] = enabled if enabled is not None else department_enabled
        return jsonify({"status": "success", **saved}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/lines', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def lines(department_id):
        """Линии Binotel компании отдела и сотрудники для назначения. Без секретов.

        users — сотрудники отдела и те, кто уже сидит на его линиях. candidates —
        сотрудники ДРУГИХ отделов для выбора по ФИО: только главе и СВ СЗоВ и
        суперадмину (can_seat_anyone), остальным список пуст, а сервер чужого не посадит."""
        _manager(department_id)
        anyone = _seats_anyone()
        return jsonify({
            "status": "success",
            "sip_server": svc.department_sip_server(department_id),
            "lines": svc.list_lines(department_id),
            "users": svc.department_users(department_id),
            "can_seat_anyone": anyone,
            "candidates": svc.line_candidates(department_id) if anyone else [],
        }), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/lines/assign', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def lines_assign(department_id):
        requester_id, _scope = _manager(department_id)
        payload = request.get_json(silent=True) or {}
        try:
            user_id = int(payload.get('user_id'))
        except (TypeError, ValueError):
            raise DialListError("user_id: число")
        result = svc.assign_line(department_id, user_id, payload.get('internal_number'),
                                 changed_by=requester_id, seat_anyone=_seats_anyone())
        return jsonify({"status": "success", **result}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/lines/release', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def lines_release(department_id):
        requester_id, _scope = _manager(department_id)
        payload = request.get_json(silent=True) or {}
        try:
            user_id = int(payload.get('user_id'))
        except (TypeError, ValueError):
            raise DialListError("user_id: число")
        return jsonify({"status": "success", **svc.release_line(
            department_id, user_id, changed_by=requester_id, seat_anyone=_seats_anyone())}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/leads/upload', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def leads_upload(department_id):
        requester_id, _scope = _manager(department_id)
        file_storage = request.files.get('file')
        if not file_storage:
            raise DialListError("file is required")
        file_name = os.path.basename(str(file_storage.filename or 'leads.xlsx'))
        file_ext = os.path.splitext(file_name)[1].lower()
        if file_ext not in ('.csv', '.xlsx', '.xlsm'):
            raise DialListError("Поддерживаются только .csv, .xlsx и .xlsm")
        raw_bytes = file_storage.read(LEADS_MAX_FILE_SIZE_BYTES + 1)
        if not raw_bytes:
            raise DialListError("Файл пустой")
        if len(raw_bytes) > LEADS_MAX_FILE_SIZE_BYTES:
            raise DialListError(f"Файл слишком большой. Лимит: {LEADS_MAX_FILE_SIZE_MB} MB", 413)
        # База какого месяца: поле формы period=YYYY-MM; пусто — обзваниваемый месяц.
        period = parse_period(request.form.get('period'))
        # Автообзвон Тез (15.10.2026): ФИО + телефон, ИИН не нужен — цель лида не подписание в
        # Sapar, а выход на линию по TEZ APP.
        tez = svc.department_profile(department_id) == PROFILE_TEZ
        # Общий разбор файла «fio + phone» и общая нормализация номера — одна
        # точка правды для всей телефонии проекта. ИИН в базе обзвона обязателен
        # (владелец, 29.09.2026): по нему проверяется подписание документов.
        from common.leads_file import parse_leads_file
        try:
            if tez:
                rows = parse_leads_file(raw_bytes, file_ext, with_iin=False)
            else:
                # Город — необязательная колонка: по ней строится отчёт о подписании по городам.
                rows = parse_leads_file(raw_bytes, file_ext, with_iin=True, with_city=True)
        except ValueError as exc:
            raise DialListError(str(exc))
        counts = svc.import_leads(department_id, requester_id, file_name, rows,
                                  period=period, require_iin=not tez)
        fresh = bool(counts.get("rows_new") or counts.get("rows_duplicate"))
        if not tez:
            # Свежую базу проверяем сразу, не дожидаясь получасового прогона: подписавшие
            # ещё до загрузки не должны уйти операторам. Проверка идёт в своём потоке.
            if fresh:
                svc.request_sign_check("загрузка базы")
            return jsonify({"status": "success", **counts}), 200
        # Тез: кто уже ездит — тоже сразу (свой поток), и тот же файл — в «Базу лидов ОП TEZ»,
        # где считаются успешки и зарплата (одна загрузка на обе, владелец 15.10.2026). Её сбой
        # загрузку в раздел не отменяет: руководитель увидит mirror.error и загрузит файл там.
        trip_check = ""
        if fresh:
            svc.request_trip_check("загрузка базы")
            trip_check = "queued"
        mirror = None
        if tez_leads_mirror is not None:
            period_date = date.fromisoformat(counts["period"])
            try:
                mirror = tez_leads_mirror(department_id, requester_id, file_name, raw_bytes, file_ext, period_date)
            except Exception:
                log.exception("dial_list: файл базы Тез не попал в «Базу лидов ОП TEZ» (отдел %s)", department_id)
                mirror = {"error": "В «Базу лидов ОП TEZ» файл не попал — загрузите его там вручную"}
        return jsonify({"status": "success", **counts, "mirror": mirror, "trip_check": trip_check}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/leads/cities', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def leads_cities(department_id):
        """Список водителей месяца с городами (file: .xlsx/.csv с колонками «ИИН» и «Город», ФИО — по желанию;
        period=YYYY-MM, пусто — обзваниваемый месяц) для отчёта «Подписание водителей по городам»: задаёт
        состав отчёта, ставит города, водителей не из базы добавляет без номера (svc.import_cities)."""
        _manager(department_id)
        file_storage = request.files.get('file')
        if not file_storage:
            raise DialListError("file is required")
        file_name = os.path.basename(str(file_storage.filename or 'cities.xlsx'))
        file_ext = os.path.splitext(file_name)[1].lower()
        if file_ext not in ('.csv', '.xlsx', '.xlsm'):
            raise DialListError("Поддерживаются только .csv, .xlsx и .xlsm")
        raw_bytes = file_storage.read(LEADS_MAX_FILE_SIZE_BYTES + 1)
        if not raw_bytes:
            raise DialListError("Файл пустой")
        if len(raw_bytes) > LEADS_MAX_FILE_SIZE_BYTES:
            raise DialListError(f"Файл слишком большой. Лимит: {LEADS_MAX_FILE_SIZE_MB} MB", 413)
        period = parse_period(request.form.get('period'))
        from common.leads_file import parse_city_list
        try:
            rows = parse_city_list(raw_bytes, file_ext)
        except ValueError as exc:
            raise DialListError(str(exc))
        return jsonify({"status": "success", **svc.import_cities(department_id, rows, period=period)}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/leads/summary', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def leads_summary(department_id):
        _manager(department_id)
        period = parse_period(request.args.get('period'))
        return jsonify({"status": "success", **svc.leads_summary(department_id, period=period)}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/analytics', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def analytics(department_id):
        """Вкладка «Аналитика»: показатели базы месяца (?period=YYYY-MM)."""
        _manager(department_id)
        period = parse_period(request.args.get('period'))
        return jsonify({"status": "success", **svc.analytics(department_id, period=period)}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/sign_report/chats', methods=['GET', 'PUT', 'OPTIONS'])
    @require_api_key
    @_guard
    def sign_report_chats(department_id):
        """Группы для отчёта «Подписание водителей по городам» (в 10:00 и 19:00 по Алматы): выбор
        отдела и все группы, куда добавлен бот. PUT {"chat_ids": [...]} — заменить выбор."""
        requester_id, _scope = _manager(department_id)
        if request.method == 'GET':
            return jsonify({"status": "success", **svc.sign_report_chats(department_id)}), 200
        payload = request.get_json(silent=True) or {}
        return jsonify({"status": "success", **svc.save_sign_report_chats(
            department_id, payload.get('chat_ids'), changed_by=requester_id)}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/sign_report/preview', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def sign_report_preview(department_id):
        """Картинка отчёта на сейчас (PNG) — то, что уйдёт в группы."""
        _manager(department_id)
        response = send_file(BytesIO(svc.sign_report_png(department_id)), mimetype='image/png')
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.route('/api/dial_list/departments/<int:department_id>/announcement_stats', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def announcement_stats(department_id):
        """Точность распознавания сообщения сети по речи (?days=, по умолчанию 14): по этим числам
        владелец решает, безопасен ли режим auto (service.announcement_stats). Только числа."""
        _manager(department_id)
        return jsonify({"status": "success",
                        **svc.announcement_stats(department_id, days=request.args.get('days'))}), 200

    # ── итоги звонка ────────────────────────────────────────────────────────
    @bp.route('/api/dial_list/departments/<int:department_id>/outcomes', methods=['GET', 'PUT', 'OPTIONS'])
    @require_api_key
    @_guard
    def outcomes(department_id):
        requester_id, _scope = _manager(department_id)
        if request.method == 'PUT':
            payload = request.get_json(silent=True) or {}
            items = payload.get('items')
            return jsonify({"status": "success",
                            "outcomes": svc.save_outcomes(department_id, items, changed_by=requester_id)}), 200
        return jsonify({"status": "success", "outcomes": svc.outcomes(department_id)}), 200

    # ── автообзвон Тез: офферы, WhatsApp, поездки (15.10.2026) ─────────────────
    def _tez_department(department_id):
        """Руководитель отдела автообзвона Тез: у отдела другого профиля этих настроек нет."""
        requester_id, scope = _manager(department_id)
        if svc.department_profile(department_id) != PROFILE_TEZ:
            raise DialListError("Это настройка автообзвона Тез", 409)
        return requester_id, scope

    @bp.route('/api/dial_list/departments/<int:department_id>/offers', methods=['GET', 'PUT', 'OPTIONS'])
    @require_api_key
    @_guard
    def offers(department_id):
        """Офферы, которые оператор отмечает в карточке звонка: {items: [{id?, title, description,
        is_active}]} — список целиком, как итоги (кого нет — выключается, история на них ссылается)."""
        requester_id, _scope = _tez_department(department_id)
        if request.method == 'PUT':
            payload = request.get_json(silent=True) or {}
            return jsonify({"status": "success",
                            **svc.save_offers(department_id, payload.get('items'), changed_by=requester_id)}), 200
        return jsonify({"status": "success", **svc.offers(department_id)}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/whatsapp', methods=['GET', 'PUT', 'OPTIONS'])
    @require_api_key
    @_guard
    def whatsapp(department_id):
        """Шаблоны WhatsApp (ChatApp, WABA), которые оператор шлёт из карточки: GET — лицензия,
        лицензии и шаблоны ChatApp (?refresh=1 — мимо кэша), разрешённые; PUT {license_id,
        allowed: [{id, title, text, params}]}. Токенов и ключей ChatApp в ответе нет."""
        requester_id, _scope = _tez_department(department_id)
        # Ключи настроек — в корне ответа (error — текст сбоя ChatApp, а не отказ ручки).
        if request.method == 'PUT':
            payload = request.get_json(silent=True) or {}
            return jsonify({"status": "success",
                            **svc.save_whatsapp(department_id, payload, changed_by=requester_id)}), 200
        refresh = (request.args.get('refresh') or '').strip().lower() in ('1', 'true', 'yes')
        return jsonify({"status": "success", **svc.whatsapp_settings(department_id, refresh=refresh)}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/trips/check', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def trips_check(department_id):
        """«Проверить поездки сейчас»: внеочередной прогон проверки TEZ APP в своём потоке (кому пора —
        как в получасовом). started: false — прогон уже идёт, после него пройдёт ещё раз."""
        _tez_department(department_id)
        started = svc.request_trip_check("руководитель")
        return jsonify({"status": "success", "started": bool(started), "state": svc.trip_checker.state()}), 200

    # ── скрипт разговора ────────────────────────────────────────────────────
    @bp.route('/api/dial_list/departments/<int:department_id>/script', methods=['GET', 'PUT', 'OPTIONS'])
    @require_api_key
    @_guard
    def script(department_id):
        """Основной текст с лёгкой разметкой и быстрые вопросы с ответами (редактор руководителя)."""
        requester_id, _scope = _manager(department_id)
        if request.method == 'PUT':
            payload = request.get_json(silent=True) or {}
            return jsonify({"status": "success",
                            "script": svc.save_script(department_id, payload, changed_by=requester_id)}), 200
        return jsonify({"status": "success", "script": svc.script(department_id)}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/script/ai', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def script_ai(department_id):
        """ИИ в редакторе: {mode: generate, brief} → {body, questions}; {mode: polish, text, target} → {text}.
        Ничего не сохраняет — руководитель смотрит результат и применяет сам."""
        _manager(department_id)
        payload = request.get_json(silent=True) or {}
        return jsonify({"status": "success", "result": svc.script_ai(department_id, payload)}), 200

    # ── журнал водителей ────────────────────────────────────────────────────
    # Номер водителя и здесь не отдаётся (только маска): руководитель удалённого
    # КЦ — тоже удалёнщик, а файл с номерами у загрузившего и так есть.
    def _uuid_or_404(value, what):
        try:
            return str(uuid.UUID(str(value)))
        except (ValueError, AttributeError, TypeError):
            raise DialListError(f"{what} не найден", 404)

    @bp.route('/api/dial_list/departments/<int:department_id>/leads', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def leads_journal(department_id):
        _manager(department_id)
        args = request.args
        for key in ('date_from', 'date_to'):
            if args.get(key):
                try:
                    datetime.strptime(args.get(key), '%Y-%m-%d')
                except ValueError:
                    raise DialListError(f"{key}: ожидается YYYY-MM-DD")
        batch_id = args.get('batch_id') or None
        if batch_id:
            batch_id = _uuid_or_404(batch_id, "Файл загрузки")
        outcome_id = args.get('outcome_id') or None
        if outcome_id:
            outcome_id = _uuid_or_404(outcome_id, "Итог")
        # Тип итога — только вместе с итогом; 'none' — «без типа».
        outcome_subtype_id = (args.get('outcome_subtype_id') or '').strip() or None
        if not outcome_id:
            outcome_subtype_id = None
        elif outcome_subtype_id:
            outcome_subtype_id = ('none' if outcome_subtype_id.lower() == 'none'
                                  else _uuid_or_404(outcome_subtype_id, "Тип итога"))
        return jsonify({"status": "success", **svc.leads_journal(
            department_id, q=args.get('q', ''), stage=args.get('stage', ''),
            operator_id=args.get('operator_id') or None, batch_id=batch_id,
            date_from=args.get('date_from') or None, date_to=args.get('date_to') or None,
            sort=args.get('sort', 'activity'), limit=args.get('limit', 50), offset=args.get('offset', 0),
            period=parse_period(args.get('period'), allow_all=True), outcome_id=outcome_id,
            outcome_subtype_id=outcome_subtype_id, sign=args.get('sign', ''),
        )}), 200

    @bp.route('/api/dial_list/leads/<lead_id>', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def lead_card(lead_id):
        lead_id = _uuid_or_404(lead_id, "Водитель")
        _manager(svc.lead_department(lead_id))
        return jsonify({"status": "success", "lead": svc.lead_card(lead_id)}), 200

    @bp.route('/api/dial_list/leads/<lead_id>/requeue', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def lead_requeue(lead_id):
        lead_id = _uuid_or_404(lead_id, "Водитель")
        requester_id, _scope = _manager(svc.lead_department(lead_id))
        payload = request.get_json(silent=True) or {}
        return jsonify({"status": "success",
                        "lead": svc.requeue_lead(lead_id, requester_id, note=payload.get('note', ''))}), 200

    @bp.route('/api/dial_list/leads/<lead_id>/exclude', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def lead_exclude(lead_id):
        lead_id = _uuid_or_404(lead_id, "Водитель")
        requester_id, _scope = _manager(svc.lead_department(lead_id))
        payload = request.get_json(silent=True) or {}
        return jsonify({"status": "success",
                        "lead": svc.exclude_lead(lead_id, requester_id, note=payload.get('note', ''))}), 200

    @bp.route('/api/dial_list/leads/<lead_id>/sign_check', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def lead_sign_check(lead_id):
        """«Проверить сейчас»: подписание документов водителя в Sapar, сразу."""
        lead_id = _uuid_or_404(lead_id, "Водитель")
        _manager(svc.lead_department(lead_id))
        return jsonify({"status": "success", "lead": svc.check_lead_signature(lead_id)}), 200

    @bp.route('/api/dial_list/leads/<lead_id>/note', methods=['PUT', 'OPTIONS'])
    @require_api_key
    @_guard
    def lead_note(lead_id):
        lead_id = _uuid_or_404(lead_id, "Водитель")
        requester_id, _scope = _manager(svc.lead_department(lead_id))
        payload = request.get_json(silent=True) or {}
        return jsonify({"status": "success",
                        "lead": svc.set_lead_note(lead_id, requester_id, payload.get('note', ''))}), 200

    @bp.route('/api/dial_list/attempts/<attempt_id>/recording', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def attempt_recording(attempt_id):
        attempt_id = _uuid_or_404(attempt_id, "Попытка")
        _manager(svc.attempt_department(attempt_id))
        return jsonify({"status": "success", **svc.attempt_recording(attempt_id)}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/repair/cut-calls', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def repair_cut_calls(department_id):
        """Починка 12.10.2026: водители, чьи разговоры оборвал автосброс, — список (apply: false)
        или возврат в очередь того же оператора / в общий список (apply: true, target).
        {since: ISO, apply: false|true, target: operator|pool}. Ни номеров, ни ИИН в ответе."""
        requester_id, _scope = _manager(department_id)
        payload = request.get_json(silent=True) or {}
        apply = payload.get('apply', False)
        if not isinstance(apply, bool):
            raise DialListError("apply: true или false")
        return jsonify({"status": "success", **svc.repair_cut_calls(
            department_id, payload.get('since'), apply=apply, target=payload.get('target') or 'operator',
            actor_id=requester_id)}), 200

    # ── детализация звонков ─────────────────────────────────────────────────
    @bp.route('/api/dial_list/departments/<int:department_id>/calls/export', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def calls_export(department_id):
        """Детализация звонков отдела за дни ?date_from=&date_to= (YYYY-MM-DD, по Алматы) — xlsx
        (dial_list/calls_export.py). Номер водителя — маской, как в журнале; ИИН и ID водителя —
        полностью (владелец, 29.10.2026). Звонков за период нет — 404 с кодом: сайт скажет это
        словами, а не отдаст пустой файл.

        Глава «Регионов» (DIAL_LIST_GROUP_REPORT_HEAD_CODES) — только звонки людей своих групп, и
        только в отделах раздела."""
        groups = None
        try:
            _manager(department_id)
        except DialListError as refusal:
            groups = _report_groups_or_raise(refusal)
            remote_cc = svc.list_departments(None, profile=PROFILE_REMOTE_CC)
            if int(department_id) not in {d["department_id"] for d in remote_cc}:
                raise DialListError("Это не отдел раздела «Удаленный КЦ»", 403)
        _requester_id, requester = _operator()
        try:
            day_from, day_to = calls_export_report.parse_days(request.args.get('date_from'),
                                                              request.args.get('date_to'))
        except ValueError as exc:
            raise DialListError(str(exc))
        stream, written = svc.calls_export(department_id, day_from, day_to,
                                           generated_by=(requester[2] if len(requester) > 2 else '') or '',
                                           text_warning_patch=excel_text_warning, groups=groups)
        if not written:
            return jsonify({"error": "За выбранный период звонков не было",
                            "code": "DIAL_LIST_CALLS_EXPORT_EMPTY"}), 404
        log.info("dial_list: детализация звонков отдела %s за %s..%s%s — %d звонков", department_id,
                 day_from, day_to, '' if groups is None else f" (группы {[g['id'] for g in groups]})", written)
        return send_file(stream, mimetype=XLSX_MIME, as_attachment=True,
                         download_name=calls_export_report.report_filename(day_from, day_to))

    @bp.route('/api/dial_list/overview', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def overview():
        _requester_id, scope = _manager()
        raw_day = request.args.get('date')
        try:
            day = datetime.strptime(raw_day, '%Y-%m-%d').date() if raw_day else date.today()
        except ValueError:
            raise DialListError("date: ожидается YYYY-MM-DD")
        department_ids = scope
        wanted = request.args.get('department_id')
        tez = False
        if wanted:
            try:
                wanted_id = int(wanted)
            except ValueError:
                raise DialListError("department_id: число")
            if scope is not None and wanted_id not in set(scope):
                raise DialListError("Это не ваш отдел", 403)
            department_ids = [wanted_id]
            # Отдел автообзвона Тез — ещё звонки автоцикла, согласия, перезвоны и статус сейчас.
            tez = svc.department_profile(wanted_id) == PROFILE_TEZ
        operators = svc.overview(department_ids, day, tez=True) if tez else svc.overview(department_ids, day)
        return jsonify({"status": "success", "date": day.isoformat(), "operators": operators}), 200

    # ── Binotel ─────────────────────────────────────────────────────────────
    @bp.route('/api/dial_list/webhook/binotel', methods=['GET', 'POST'])
    def binotel_webhook():
        # Адрес для списка серверов Binotel — тот, что дописал справа в X-Forwarded-For прокси Render
        # (ревью 13.10.2026), а не первый в цепочке, как у остальных ручек проекта: первый пишет сам
        # клиент, и с токеном из утёкшей ссылки любой назвался бы Binotel (service.webhook_remote_addr).
        forwarded = request.headers.get('X-Forwarded-For', '')
        remote_addr = webhook_remote_addr(forwarded, request.remote_addr)
        token = request.args.get('token', '')
        if request.method == 'GET':
            # Binotel и человек проверяют адрес открытием: отвечаем коротко и без данных.
            return "ok", 200
        try:
            allowed, reason = svc.webhook_verdict(token, remote_addr)
            if not allowed:
                # Почему отклонён и откуда — точно, со всей цепочкой прокси: по журналу видно, с
                # каких адресов на самом деле приходит Binotel. Токен в журнал не пишем никогда.
                log.warning("dial_list: вебхук Binotel отклонён — %s; адрес %s (X-Forwarded-For: %r, "
                            "соединение: %s)", reason, remote_addr or '(пусто)', forwarded[:200],
                            request.remote_addr or '(пусто)')
                return jsonify({"error": "Forbidden"}), 403
            # Binotel шлёт форму (callDetails[…]); JSON — тоже понимаем (service.webhook_fields), и с
            # чужим Content-Type: разбирать тело заставляем сами (force).
            form = request.form if request.form else (request.get_json(silent=True, force=True) or {})
            result = svc.handle_webhook(form, remote_addr)
        except Exception:
            log.exception("dial_list: ошибка обработки вебхука")
            # Binotel ретраит только на не-200; свои ошибки чиним по журналу.
            return "error", 500
        return jsonify({"status": "success", **result}), 200

    # ── автоинформатор (15.10.2026) ─────────────────────────────────────────
    # Робот звонит водителю и проигрывает запись (пакет informer). Рассылками распоряжается
    # руководитель раздела (_manager); робот — линия удалённого КЦ, поэтому и рассылки — только у
    # отделов этого профиля.
    def _informer(department_id):
        if informer_service is None:
            raise DialListError("Автоинформатор не подключён на сервере", 503)
        if svc.department_profile(department_id) != PROFILE_REMOTE_CC:
            raise DialListError("Автоинформатор есть только у удалённого КЦ", 403)
        return informer_service

    def _informer_do(call):
        from informer.service import InformerError
        try:
            return call()
        except InformerError as exc:
            raise DialListError(str(exc), exc.status)

    @bp.route('/api/dial_list/departments/<int:department_id>/informer', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def informer_overview(department_id):
        _manager(department_id)
        inf = _informer(department_id)
        robot = _informer_do(inf.robot_status)
        campaigns = _informer_do(lambda: inf.list_campaigns(department_id))
        return jsonify({"status": "success", "robot": robot, "campaigns": campaigns}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/informer/campaigns', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def informer_create(department_id):
        requester_id, _scope = _manager(department_id)
        inf = _informer(department_id)
        audio = request.files.get('audio')
        listing = request.files.get('recipients')
        if not audio:
            raise DialListError("Нет записи рассылки")
        if not listing:
            raise DialListError("Нет списка водителей")
        from informer import recipients as informer_recipients
        from informer import wav as informer_wav
        wav_bytes = audio.read(informer_wav.MAX_UPLOAD_BYTES + 1)
        if len(wav_bytes) > informer_wav.MAX_UPLOAD_BYTES:
            raise DialListError("Запись длиннее пяти минут", 413)
        list_name = os.path.basename(str(listing.filename or 'drivers.xlsx'))
        list_ext = os.path.splitext(list_name)[1].lower()
        list_bytes = listing.read(LEADS_MAX_FILE_SIZE_BYTES + 1)
        if len(list_bytes) > LEADS_MAX_FILE_SIZE_BYTES:
            raise DialListError(f"Список слишком большой. Лимит: {LEADS_MAX_FILE_SIZE_MB} MB", 413)
        try:
            parsed = informer_recipients.parse(list_bytes, list_ext)
            parts = json.loads(request.form.get('parts') or '[]')
        except ValueError as exc:
            raise DialListError(str(exc))
        form = request.form
        try:
            script = json.loads(form.get('script') or '{}')
        except ValueError:
            script = {}
        campaign = _informer_do(lambda: inf.create_campaign(
            department_id, requester_id, name=form.get('name'), wav_bytes=wav_bytes, parts=parts,
            recipients=parsed, file_name=list_name, window_start=form.get('window_start'),
            window_end=form.get('window_end'), max_per_minute=form.get('max_per_minute'),
            max_attempts=form.get('max_attempts'), retry_after_min=form.get('retry_after_min'),
            script=script))
        return jsonify({"status": "success", "campaign": campaign,
                        "recipients": {"rows": len(parsed["rows"]), "invalid": parsed["invalid"],
                                       "duplicates": parsed["duplicates"]}}), 200

    # Озвучка текста (Soniox) и ИИ для текста — до создания рассылки, ничего не сохраняют.
    @bp.route('/api/dial_list/departments/<int:department_id>/informer/voices', methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def informer_voices(department_id):
        _manager(department_id)
        _informer(department_id)
        from informer import tts as informer_tts
        return jsonify({"status": "success", "voices": informer_tts.VOICES, "default": informer_tts.DEFAULT_VOICE,
                        "speeds": list(informer_tts.SPEEDS), "default_speed": informer_tts.DEFAULT_SPEED,
                        "text_max": informer_tts.TEXT_MAX}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/informer/tts', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def informer_tts_speak(department_id):
        """{lang: kz|ru, text, voice, speed} → WAV 8 кГц моно — та же запись, что потом сыграет робот."""
        _manager(department_id)
        _informer(department_id)
        from informer import tts as informer_tts
        payload = request.get_json(silent=True) or {}
        try:
            audio = informer_tts.synthesize(payload.get('text'), payload.get('lang'), payload.get('voice'),
                                            payload.get('speed') or informer_tts.DEFAULT_SPEED)
        except informer_tts.TTSError as exc:
            raise DialListError(str(exc), exc.status)
        return send_file(BytesIO(audio), mimetype='audio/wav', as_attachment=False, download_name='speech.wav')

    @bp.route('/api/dial_list/departments/<int:department_id>/informer/ai', methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def informer_ai(department_id):
        """{mode: generate, brief} → {kz, ru}; {mode: polish, text, lang} → {text};
        {mode: translate, text, source, target} → {text}. Языки — kz | ru."""
        _manager(department_id)
        _informer(department_id)
        from informer import ai as informer_ai_text
        payload = request.get_json(silent=True) or {}
        mode = payload.get('mode')
        try:
            if mode == 'generate':
                result = informer_ai_text.generate(payload.get('brief'))
            elif mode == 'polish':
                result = informer_ai_text.polish(payload.get('text'), payload.get('lang'))
            elif mode == 'translate':
                result = informer_ai_text.translate(payload.get('text'), payload.get('source'), payload.get('target'))
            else:
                raise DialListError("mode: generate | polish | translate")
        except informer_ai_text.ScriptAIError as exc:
            raise DialListError(str(exc), exc.status)
        return jsonify({"status": "success", "result": result}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/informer/campaigns/<int:campaign_id>',
              methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def informer_campaign(department_id, campaign_id):
        _manager(department_id)
        inf = _informer(department_id)
        detail = _informer_do(lambda: inf.campaign_detail(department_id, campaign_id))
        return jsonify({"status": "success", "campaign": detail}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/informer/campaigns/<int:campaign_id>/<action>',
              methods=['POST', 'OPTIONS'])
    @require_api_key
    @_guard
    def informer_campaign_action(department_id, campaign_id, action):
        _manager(department_id)
        inf = _informer(department_id)
        detail = _informer_do(lambda: inf.campaign_action(department_id, campaign_id, action))
        return jsonify({"status": "success", "campaign": detail}), 200

    @bp.route('/api/dial_list/departments/<int:department_id>/informer/campaigns/<int:campaign_id>/audio',
              methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def informer_campaign_audio(department_id, campaign_id):
        _manager(department_id)
        inf = _informer(department_id)
        data = _informer_do(lambda: inf.campaign_audio(department_id, campaign_id))
        return send_file(BytesIO(data), mimetype='audio/wav', as_attachment=False,
                         download_name=f'informer_{campaign_id}.wav')

    @bp.route('/api/dial_list/departments/<int:department_id>/informer/campaigns/<int:campaign_id>/export',
              methods=['GET', 'OPTIONS'])
    @require_api_key
    @_guard
    def informer_campaign_export(department_id, campaign_id):
        _manager(department_id)
        inf = _informer(department_id)
        from informer import export as informer_export
        detail, targets, calls = _informer_do(lambda: inf.export_rows(department_id, campaign_id))
        stream = informer_export.build(detail, targets, calls, text_warning_patch=excel_text_warning)
        return send_file(stream, mimetype=XLSX_MIME, as_attachment=True,
                         download_name=f'informer_{campaign_id}.xlsx')

    return bp
