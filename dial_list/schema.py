# -*- coding: utf-8 -*-
"""Схема раздела «Удаленный КЦ» (до 07.10.2026 — «Обзвон из телефона»). Все таблицы
с префиксом dial_list_.

Идемпотентно (CREATE ... IF NOT EXISTS), вызывается один раз при старте из
Database._init_db через init_dial_list_schema(cursor) — как у op_funnel и
oktell_guard. Порядок внутри: CREATE TABLE → ALTER → CREATE INDEX.

Модель:

    dial_list_department_settings  на отдел: включён ли режим, размер порции,
                                   правила повтора, имя компании Binotel (по нему
                                   сервер находит ключ API в своём окружении),
                                   что показывать линии оператора как номер
                                   звонящего. Секретов в таблице нет.
    dial_list_user_settings        персональное включение (NULL = как у отдела):
                                   нужно для пилота на одном-двух операторах,
                                   потому что телефон раскатывается всем сразу.
    dial_list_lead_batches         загрузки списков водителей (файл ФИО+телефон)
                                   отделом-обзвонщиком.
    dial_list_leads                база водителей ОТДЕЛА: ФИО, номер, сколько раз
                                   звонили, дозвонились ли. Номер уникален в
                                   пределах отдела: у каждого отдела-обзвонщика
                                   свои списки (решение владельца 22.09.2026).
    dial_list_portions             выдача: кому, когда, сколько строк, когда
                                   закрыта (все строки обработаны).
    dial_list_assignments          строка выдачи: лид → оператор; состояние
                                   issued/done и итог подтверждённой попытки.
    dial_list_attempts             попытка звонка: generalCallID от Binotel,
                                   события с телефона, финальная disposition и
                                   откуда она пришла (webhook / poll / api_error /
                                   timeout).
    dial_list_webhook_log          сырые POST'ы Binotel «API Call Completed» —
                                   на время внедрения, чтобы видеть, что именно
                                   присылает АТС; чистится по возрасту.
    dial_list_line_members         сотрудник ДРУГОГО отдела на линии отдела: отдел
                                   и номер линии, её учётка у Binotel. Его
                                   собственные SIP-настройки при этом не меняются.
    dial_list_offers               акции и офферы отдела (автообзвон Тез КЦ): что
                                   оператор может предложить водителю в разговоре.
    dial_list_attempt_offers       какие офферы оператор отметил в итоге звонка.
    dial_list_wa_messages          журнал и outbox отправок шаблона WhatsApp
                                   (ChatApp) водителю: строка пишется до запроса.

Профиль отдела (dial_list_department_settings.profile, 15.10.2026): 'remote_cc' —
удалённый КЦ, как было; 'tez' — автообзвон Тез КЦ на том же движке: база без ИИН,
«цель» лида — водитель вышел на линию (поездка в TEZ APP, trips_check.py), сроки
повторов и перезвонов (next_call_at, owner_*), постобработка, офферы, WhatsApp.

Почему свои таблицы настроек, а не колонки в sip_department_config:
_SIP_OPERATOR_SELECT читает строки по индексам, и каждая новая колонка там —
правка в трёх местах с риском сдвинуть соседей. Здесь настройки читает только
этот модуль.
"""

DDL = [
    """
    CREATE TABLE IF NOT EXISTS dial_list_department_settings (
        department_id INTEGER PRIMARY KEY REFERENCES departments(id) ON DELETE CASCADE,
        enabled BOOLEAN NOT NULL DEFAULT FALSE,
        portion_size SMALLINT NOT NULL DEFAULT 20,
        max_attempts SMALLINT NOT NULL DEFAULT 3,
        retry_after_hours SMALLINT NOT NULL DEFAULT 24,
        binotel_company VARCHAR(32) NOT NULL DEFAULT 'remote_cc',
        caller_id_for_employee VARCHAR(32) NOT NULL DEFAULT '',
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_user_settings (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        enabled BOOLEAN,
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at TIMESTAMP NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_lead_batches (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        file_name TEXT NOT NULL DEFAULT '',
        rows_total INTEGER NOT NULL DEFAULT 0,
        rows_new INTEGER NOT NULL DEFAULT 0,
        rows_duplicate INTEGER NOT NULL DEFAULT 0,
        rows_invalid INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_leads (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        phone_norm VARCHAR(16) NOT NULL,
        full_name TEXT NOT NULL DEFAULT '',
        first_batch_id UUID REFERENCES dial_list_lead_batches(id) ON DELETE SET NULL,
        last_batch_id UUID REFERENCES dial_list_lead_batches(id) ON DELETE SET NULL,
        upload_count INTEGER NOT NULL DEFAULT 1,
        status VARCHAR(16) NOT NULL DEFAULT 'new'
            CHECK (status IN ('new', 'in_progress', 'done', 'excluded')),
        attempts_total SMALLINT NOT NULL DEFAULT 0,
        last_attempt_at TIMESTAMP WITH TIME ZONE,
        answered_at TIMESTAMP WITH TIME ZONE,
        period DATE NOT NULL DEFAULT (date_trunc('month', CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))::date,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT uq_dial_list_leads_period_phone UNIQUE (department_id, period, phone_norm)
    )
    """,
    # Итоги звонка (запрос владельца 23.09.2026): справочник отдела, оператор
    # обязан выбрать один после каждого разговора; цвет и порядок задаёт
    # руководитель. Не удаляются, а выключаются — история на них ссылается.
    """
    CREATE TABLE IF NOT EXISTS dial_list_outcomes (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        name VARCHAR(64) NOT NULL,
        color VARCHAR(7) NOT NULL DEFAULT '#8E8E93',
        position SMALLINT NOT NULL DEFAULT 0,
        requeue BOOLEAN NOT NULL DEFAULT FALSE,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # Скрипт разговора отдела (владелец, 25.09.2026): основной текст с лёгкой
    # разметкой и быстрые вопросы с ответами — оператор читает их на телефоне
    # прямо во время звонка. Версия растёт при каждом сохранении: по ней телефон
    # понимает, что пора перечитать. Вопросы не удаляются, а выключаются.
    """
    CREATE TABLE IF NOT EXISTS dial_list_scripts (
        department_id INTEGER PRIMARY KEY REFERENCES departments(id) ON DELETE CASCADE,
        body TEXT NOT NULL DEFAULT '',
        version INTEGER NOT NULL DEFAULT 1,
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_script_questions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        position SMALLINT NOT NULL DEFAULT 0,
        question VARCHAR(200) NOT NULL,
        answer TEXT NOT NULL DEFAULT '',
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_portions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
        size SMALLINT NOT NULL,
        issued_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        closed_at TIMESTAMP WITH TIME ZONE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_assignments (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        portion_id UUID NOT NULL REFERENCES dial_list_portions(id) ON DELETE CASCADE,
        lead_id UUID NOT NULL REFERENCES dial_list_leads(id) ON DELETE CASCADE,
        operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        position SMALLINT NOT NULL,
        state VARCHAR(16) NOT NULL DEFAULT 'issued'
            CHECK (state IN ('issued', 'done')),
        result VARCHAR(16) NOT NULL DEFAULT ''
            CHECK (result IN ('', 'answered', 'busy', 'no_answer', 'other', 'failed')),
        attempts SMALLINT NOT NULL DEFAULT 0,
        done_at TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_attempts (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        assignment_id UUID NOT NULL REFERENCES dial_list_assignments(id) ON DELETE CASCADE,
        operator_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        internal_number VARCHAR(64) NOT NULL DEFAULT '',
        general_call_id VARCHAR(32),
        requested_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        api_error TEXT NOT NULL DEFAULT '',
        state VARCHAR(16) NOT NULL DEFAULT 'requested'
            CHECK (state IN ('requested', 'failed', 'leg_ringing', 'leg_answered', 'ended', 'finished')),
        disposition VARCHAR(32) NOT NULL DEFAULT '',
        billsec INTEGER NOT NULL DEFAULT 0,
        waitsec INTEGER NOT NULL DEFAULT 0,
        final_source VARCHAR(16) NOT NULL DEFAULT '',
        phone_event_at TIMESTAMP WITH TIME ZONE,
        phone_ended_at TIMESTAMP WITH TIME ZONE,
        finished_at TIMESTAMP WITH TIME ZONE,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_webhook_log (
        id BIGSERIAL PRIMARY KEY,
        received_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        remote_addr VARCHAR(64) NOT NULL DEFAULT '',
        general_call_id VARCHAR(32) NOT NULL DEFAULT '',
        matched BOOLEAN NOT NULL DEFAULT FALSE,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    # Секреты в базе не хранятся (решение владельца 23.09.2026: ключи и токены —
    # только в окружении Render). Колонки первой версии удаляем вместе со всем,
    # что в них могли успеть записать; имя компании добавляем.
    "ALTER TABLE dial_list_department_settings DROP COLUMN IF EXISTS binotel_api_key",
    "ALTER TABLE dial_list_department_settings DROP COLUMN IF EXISTS binotel_api_secret",
    "ALTER TABLE dial_list_department_settings DROP COLUMN IF EXISTS webhook_token",
    "ALTER TABLE dial_list_department_settings ADD COLUMN IF NOT EXISTS binotel_company VARCHAR(32) NOT NULL DEFAULT 'remote_cc'",
    # Журнал водителей (запрос владельца 23.09.2026): руководитель видит по каждому
    # человеку ответственного, статус и историю звонков и может вручную вернуть
    # его в список или исключить. Ручные действия — отдельной таблицей, чтобы в
    # истории было видно, кто и когда вмешался; заметка руководителя — на лиде.
    """
    CREATE TABLE IF NOT EXISTS dial_list_lead_events (
        id BIGSERIAL PRIMARY KEY,
        lead_id UUID NOT NULL REFERENCES dial_list_leads(id) ON DELETE CASCADE,
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        kind VARCHAR(16) NOT NULL
            CHECK (kind IN ('requeue', 'exclude', 'restore', 'note')),
        note TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS note TEXT NOT NULL DEFAULT ''",
    # Базы по месяцам (запрос владельца 23.09.2026): один и тот же номер может
    # повторяться в базе другого месяца, внутри месяца — нет. Уникальность
    # переезжает с (отдел, номер) на (отдел, месяц, номер); старое ограничение
    # снимаем по автоимени Postgres.
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS period DATE NOT NULL"
    " DEFAULT (date_trunc('month', CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))::date",
    "ALTER TABLE dial_list_leads DROP CONSTRAINT IF EXISTS dial_list_leads_department_id_phone_norm_key",
    """
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_dial_list_leads_period_phone') THEN
            ALTER TABLE dial_list_leads
                ADD CONSTRAINT uq_dial_list_leads_period_phone UNIQUE (department_id, period, phone_norm);
        END IF;
    END $$
    """,
    "ALTER TABLE dial_list_lead_batches ADD COLUMN IF NOT EXISTS period DATE",
    # Какой месяц сейчас обзванивается; NULL — текущий календарный.
    "ALTER TABLE dial_list_department_settings ADD COLUMN IF NOT EXISTS active_period DATE",
    # Итог и комментарий оператора по попытке; когда телефон принял плечо —
    # по этому признаку сервер понимает, что разговор был и итог обязателен.
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS outcome_id UUID REFERENCES dial_list_outcomes(id) ON DELETE SET NULL",
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS operator_comment TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS outcome_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS leg_answered_at TIMESTAMP WITH TIME ZONE",
    # Отбой оператором (решение владельца 24.09.2026): когда оператор сам нажал
    # «Завершить» (operator_hangup_at) и Binotel сообщил, что водитель не ответил,
    # попытка отменяется (cancelled) — не считается строке и лиду, итог по ней не
    # ставится. leg_sec — сколько секунд плечо было принято телефоном (для журнала).
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS operator_hangup_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS cancelled BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS leg_sec INTEGER NOT NULL DEFAULT 0",
    # Сброс на гудках (владелец, 11.10.2026): телефон сам положил плечо, потому что
    # водитель за пять гудков не взял трубку. Это недозвон, а не отбой оператора:
    # попытка в счёт, итог по ней не нужен (если водитель так и не ответил).
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS ring_timeout_at TIMESTAMP WITH TIME ZONE",
    # Что телефон услышал на плече (владелец, 12.10.2026; телефон с 3.22.34):
    #   auto_hangup    почему плечо положил сам телефон: 'ring_timeout' — гудки кончились,
    #                  'announcement' — сообщение сети «абонент недоступен» (это всегда
    #                  недозвон, что бы ни ответил Binotel); '' — телефон плечо не клал
    #                  (и все попытки до 3.22.34: у них причину знает только ring_timeout_at)
    #   talk_detected  был ли разговор: FALSE — телефон его не услышал, итог нужен, только
    #                  если Binotel подтвердит ответ водителя; TRUE — был (по Binotel или по
    #                  звуку), итог нужен сразу, и отбой оператора тогда не отмена; NULL —
    #                  телефон не сообщил (до 3.22.34) — тогда всё решается, как раньше
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS auto_hangup VARCHAR(16) NOT NULL DEFAULT ''",
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS talk_detected BOOLEAN",
    # Замер точности распознавания сообщения сети (ревью 13.10.2026: по речи его не отличить от
    # мелодии вместо гудков, режим announcement_mode выбирает владелец по этим числам):
    #   announcement_suspected  распознавание по речи сработало на плече хоть раз (в любом режиме);
    #                           NULL — телефон не сообщил (до этой сборки)
    #   announcement_by         кто решил, что это сообщение сети, у auto_hangup 'announcement':
    #                           'auto' / 'sit' — телефон (по речи / по тону SIT), 'operator' —
    #                           оператор по подсказке; NULL — не сообщил. На счёт попытки не влияют.
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS announcement_suspected BOOLEAN",
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS announcement_by VARCHAR(16)",
    # Типы итога (запрос владельца 29.09.2026): у итога может быть свой список
    # вложенных типов («Отказ» → «Дорого», «Уже работает в другом парке»), чтобы
    # типизировать сами итоги. Отдельная таблица, а не parent_id в
    # dial_list_outcomes: иначе каждый запрос к итогам (сохранение списком,
    # выдача телефону, фишки журнала, лимит в 30) начал бы видеть типы как
    # итоги. Цвета и «перезвонить» у типа нет — они берутся у итога. Типы не
    # удаляются, а выключаются: история попыток на них ссылается.
    """
    CREATE TABLE IF NOT EXISTS dial_list_outcome_subtypes (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        outcome_id UUID NOT NULL REFERENCES dial_list_outcomes(id) ON DELETE CASCADE,
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        name VARCHAR(64) NOT NULL,
        position SMALLINT NOT NULL DEFAULT 0,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # Тип пишется в той же попытке, что и итог; NULL — без типа (старые
    # телефоны и итоги без типов).
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS outcome_subtype_id UUID"
    " REFERENCES dial_list_outcome_subtypes(id) ON DELETE SET NULL",
    # ИИН и подписание документов (запрос владельца 29.09.2026, см. signing.py и
    # sign_check.py). ИИН обязателен в файле базы, по нему Sapar говорит, подписал
    # ли водитель документы за прошлый месяц. '' — строки, загруженные до ИИН.
    #   sign_status     общий статус пакета документов (signing.SIGN_RANK), '' — не
    #                   проверяли; CHECK нет намеренно: статусы задаёт Sapar
    #   sign_docs       номер/статус/время подписи каждого документа — для карточки
    #   sign_checked_at когда Sapar ответил последний раз (по нему «пора ли снова»)
    #   signed_at       когда водитель подписал (последняя подпись пакета); не NULL —
    #                   подписал: из пула ушёл навсегда, больше не проверяется
    #   success_*       кому засчитана успешка; success_resolved_at — решение принято
    #                   (NULL при signed_at — ждём конца звонка, начатого до подписи).
    #                   success_attempt_id без FK: лид → попытки → выдачи → лид был бы
    #                   кольцом внешних ключей с каскадами навстречу друг другу.
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS iin VARCHAR(12) NOT NULL DEFAULT ''",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS sign_status VARCHAR(16) NOT NULL DEFAULT ''",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS sign_docs JSONB NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS sign_checked_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS sign_error TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS signed_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS signed_detected_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS success_attempt_id UUID",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS success_operator_id INTEGER"
    " REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS success_resolved_at TIMESTAMP WITH TIME ZONE",
    # Строки файла без ИИН или с ошибкой в нём — не загружаются, считаются отдельно.
    "ALTER TABLE dial_list_lead_batches ADD COLUMN IF NOT EXISTS rows_bad_iin INTEGER NOT NULL DEFAULT 0",
    # Сотрудник ДРУГОГО отдела на линии (запрос владельца 07.10.2026: «добавлять на
    # линию любого сотрудника по ФИО»). Своему сотруднику отдела линия пишется в его
    # же SIP-настройки (users.sip_number + user_sip_settings); чужому так нельзя: там
    # лежит телефония его отдела — у СЗоВ users.sip_number это логин Oktell, по нему
    # работают табло и «Ограничитель», у отдела продаж — номер локальной АТС. Поэтому
    # привязка живёт отдельно и ничего в его отделе не трогает:
    #   department_id    отдел ЛИНИИ (удалённый КЦ), а не отдел человека
    #   sip_login/…      учётка линии от Binotel — как у своих, наружу не отдаётся;
    #                    при снятии с линии пароль стирается
    #   released_at      NULL — сидит на линии сейчас. Строки не удаляются: по ним
    #                    видно, кто, кого и когда сажал и снимал.
    """
    CREATE TABLE IF NOT EXISTS dial_list_line_members (
        id BIGSERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        internal_number VARCHAR(64) NOT NULL,
        sip_login VARCHAR(128) NOT NULL DEFAULT '',
        sip_password VARCHAR(255) NOT NULL DEFAULT '',
        assigned_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        assigned_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        released_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        released_at TIMESTAMP WITH TIME ZONE
    )
    """,
    # Автообзвон Тез КЦ (ТЗ руководителя Тез и решения владельца 15.10.2026): тот же движок
    # с профилем отдела. Значения профиля — 'remote_cc' | 'tez'; проверяет их код
    # (service.PROFILES), CHECK в базе не заводим: новый профиль — правка кода, а не миграция.
    # Отдел, подключённый раньше, остаётся 'remote_cc' — переключает админ в настройках.
    #   auto_dial            автоцикл в «Активном»: телефон сам звонит подряд (у tez при подключении — да)
    #   call_interval_sec    пауза между звонками автоцикла, 0..600
    #   wrapup_sec           срок постобработки: 0 — без срока, иначе 10..1800
    #   retry_schedule_min   паузы перед повторами недозвона в минутах: элемент i — перед попыткой
    #                        i+2, последний повторяется; [] — как раньше, retry_after_hours
    #   callback_own_minutes сколько перезвон/дожим ждёт своего оператора после срока, 0..1440
    #   followup_after_min   через сколько после обещанного выхода на линию «дожать», 0..1440
    #   wa_license_id        лицензия ChatApp (caWhatsApp); NULL — WhatsApp выключен
    #   wa_templates         разрешённые руководителем шаблоны: [{id, title, text, params}]
    "ALTER TABLE dial_list_department_settings ADD COLUMN IF NOT EXISTS profile VARCHAR(16) NOT NULL"
    " DEFAULT 'remote_cc'",
    "ALTER TABLE dial_list_department_settings ADD COLUMN IF NOT EXISTS auto_dial BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE dial_list_department_settings ADD COLUMN IF NOT EXISTS call_interval_sec SMALLINT NOT NULL"
    " DEFAULT 10",
    "ALTER TABLE dial_list_department_settings ADD COLUMN IF NOT EXISTS wrapup_sec SMALLINT NOT NULL DEFAULT 60",
    "ALTER TABLE dial_list_department_settings ADD COLUMN IF NOT EXISTS retry_schedule_min JSONB NOT NULL"
    " DEFAULT '[]'::jsonb",
    "ALTER TABLE dial_list_department_settings ADD COLUMN IF NOT EXISTS callback_own_minutes SMALLINT NOT NULL"
    " DEFAULT 120",
    "ALTER TABLE dial_list_department_settings ADD COLUMN IF NOT EXISTS followup_after_min SMALLINT NOT NULL"
    " DEFAULT 60",
    "ALTER TABLE dial_list_department_settings ADD COLUMN IF NOT EXISTS wa_license_id INTEGER",
    "ALTER TABLE dial_list_department_settings ADD COLUMN IF NOT EXISTS wa_templates JSONB NOT NULL"
    " DEFAULT '[]'::jsonb",
    # Сроки лида (автообзвон Тез). У удалённого КЦ next_call_at всегда NULL — пул как раньше.
    #   next_call_at         общий пул не выдаёт лида раньше этого момента (NULL — сразу)
    #   owner_operator_id    чей перезвон/дожим; owner_call_at — когда ему звонить;
    #   due_kind             почему: 'callback' (перезвон) | 'online' (обещал выйти на линию) | ''
    #   promised_online_at   последнее обещанное время выхода на линию — не чистится никогда:
    #                        по нему аналитика видит «обещал и не вышел»
    #   trip_*, month_first_order_at, last_order_before_at — ответ TEZ APP (trips_check.py);
    #                        вышел на линию — signed_at = month_first_order_at, sign_status 'online'
    #   driver_id            id водителя в TEZ APP — на будущее (API его пока не отдаёт)
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS next_call_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS owner_operator_id INTEGER"
    " REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS owner_call_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS due_kind VARCHAR(16) NOT NULL DEFAULT ''",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS promised_online_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS trip_checked_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS trip_error TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS month_first_order_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS last_order_before_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_leads ADD COLUMN IF NOT EXISTS driver_id VARCHAR(64) NOT NULL DEFAULT ''",
    # Итог с датой: 'callback' — «Перезвонить» (дата и время перезвона обязательны), 'online' —
    # «Согласен выйти на линию» (когда выйдет — обязательно). Такой итог всегда «перезвонить».
    "ALTER TABLE dial_list_outcomes ADD COLUMN IF NOT EXISTS due_kind VARCHAR(16) NOT NULL DEFAULT ''",
    # Попытка автообзвона: дата, выбранная с итогом; начал ли звонок автоцикл; статус телефона
    # при звонке ('active' | 'outbound' | '' — не сообщил); когда телефон сообщил, что срок
    # постобработки вышел (оператор ушёл в «Офлайн», решение владельца 15.10.2026).
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS due_at TIMESTAMP WITH TIME ZONE",
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS auto BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS operator_status VARCHAR(16) NOT NULL DEFAULT ''",
    "ALTER TABLE dial_list_attempts ADD COLUMN IF NOT EXISTS wrapup_expired_at TIMESTAMP WITH TIME ZONE",
    # Акции и офферы отдела: оператор отмечает в итоге, какие предложил. Не удаляются, а
    # выключаются — история попыток на них ссылается (как итоги и их типы).
    """
    CREATE TABLE IF NOT EXISTS dial_list_offers (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE CASCADE,
        title VARCHAR(120) NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        position SMALLINT NOT NULL DEFAULT 0,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dial_list_attempt_offers (
        attempt_id UUID NOT NULL REFERENCES dial_list_attempts(id) ON DELETE CASCADE,
        offer_id UUID NOT NULL REFERENCES dial_list_offers(id) ON DELETE CASCADE,
        PRIMARY KEY (attempt_id, offer_id)
    )
    """,
    # Отправки шаблона WhatsApp оператором: строка 'sending' пишется и фиксируется ДО запроса в
    # ChatApp, итог — 'sent' / 'failed'. Неоднозначный ответ (таймаут) — 'failed', повтор только
    # новым нажатием; не чаще раза в сутки на водителя. attempt_id без FK, как success_attempt_id.
    """
    CREATE TABLE IF NOT EXISTS dial_list_wa_messages (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        department_id INTEGER REFERENCES departments(id) ON DELETE CASCADE,
        lead_id UUID NOT NULL REFERENCES dial_list_leads(id) ON DELETE CASCADE,
        attempt_id UUID,
        operator_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        license_id INTEGER,
        template_id VARCHAR(64) NOT NULL DEFAULT '',
        template_title VARCHAR(120) NOT NULL DEFAULT '',
        status VARCHAR(16) NOT NULL DEFAULT 'sending' CHECK (status IN ('sending', 'sent', 'failed')),
        provider_message_id VARCHAR(128) NOT NULL DEFAULT '',
        error TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
        sent_at TIMESTAMP WITH TIME ZONE
    )
    """,
    # Индексов по отметкам проверок (sign_checked_at, trip_checked_at) нет НАМЕРЕННО.
    # Отметку пишет каждая проверка подписи и поездок — у каждого неподписавшего раз в
    # час-три, ~100 тыс. обновлений в сутки. Пока отметка стояла в индексе, обновление было
    # не-HOT: новая версия строки шла во все индексы лида и с полными образами страниц в
    # журнал (замер 15–19.10.2026: 3,3 ГБ WAL за 4 дня — треть всего WAL базы). Выборку
    # «кому пора» (два раза в час, ~13 тыс. неподписавших) Postgres и без индекса делает за
    # те же ~0,1 с: время уходит на сортировку, а не на поиск (EXPLAIN на проде 19.10).
    # Стоят до индексов: это правка таблицы, а не новый индекс.
    "DROP INDEX IF EXISTS idx_dial_list_leads_sign_due",
    "DROP INDEX IF EXISTS idx_dial_list_leads_trip_due",
    # Место на странице под HOT-обновление отметки: без него новая версия строки уходит на
    # другую страницу и снова пишет все индексы. Ставится один раз (ALTER берёт блокировку).
    """
    DO $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_class
                        WHERE oid = 'dial_list_leads'::regclass
                          AND reloptions @> ARRAY['fillfactor=80']) THEN
            ALTER TABLE dial_list_leads SET (fillfactor = 80);
        END IF;
    END $$
    """,
    # Один лид — максимум в одной ОТКРЫТОЙ выдаче: два оператора не должны
    # звонить одному водителю одновременно.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_dial_list_assignments_open_lead
        ON dial_list_assignments(lead_id) WHERE state = 'issued'
    """,
    "CREATE INDEX IF NOT EXISTS idx_dial_list_leads_pool ON dial_list_leads(department_id, status, attempts_total, last_attempt_at)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_assignments_portion ON dial_list_assignments(portion_id)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_assignments_lead ON dial_list_assignments(lead_id, state)",
    # Выдачи оператора за месяц: «Мой прогресс» и вкладки итогов на телефоне.
    "CREATE INDEX IF NOT EXISTS idx_dial_list_assignments_operator_created ON dial_list_assignments(operator_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_portions_operator_open ON dial_list_portions(operator_id, closed_at)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_dial_list_attempts_general_call
        ON dial_list_attempts(general_call_id) WHERE general_call_id IS NOT NULL
    """,
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_assignment ON dial_list_attempts(assignment_id)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_operator_state ON dial_list_attempts(operator_id, state)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_requested ON dial_list_attempts(requested_at)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_webhook_log_received ON dial_list_webhook_log(received_at)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_lead_events_lead ON dial_list_lead_events(lead_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_leads_journal ON dial_list_leads(department_id, updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_leads_name ON dial_list_leads(department_id, lower(full_name))",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_leads_period ON dial_list_leads(department_id, period)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_outcomes_department ON dial_list_outcomes(department_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_outcome ON dial_list_attempts(outcome_id)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_script_questions_department ON dial_list_script_questions(department_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_pending_outcome ON dial_list_attempts(operator_id) WHERE outcome_id IS NULL AND leg_answered_at IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_outcome_subtypes_outcome ON dial_list_outcome_subtypes(outcome_id, position)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_outcome_subtype ON dial_list_attempts(outcome_subtype_id)",
    # ИИН уникален в базе месяца отдела, как и номер: один водитель — одна строка,
    # одна успешка. В DO-блоке: дубль, если он всё же окажется в данных, не должен
    # выключить весь раздел (схема идёт под одним SAVEPOINT) — без индекса дубли
    # ловит проверка при загрузке.
    """
    DO $$
    BEGIN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_dial_list_leads_period_iin
            ON dial_list_leads(department_id, period, iin) WHERE iin <> '';
    EXCEPTION WHEN unique_violation THEN
        RAISE WARNING 'dial_list: в базах есть повторы ИИН — индекс uq_dial_list_leads_period_iin не создан';
    END $$
    """,
    # Успешки оператора по дням (сводка «Операторы», аналитика).
    "CREATE INDEX IF NOT EXISTS idx_dial_list_leads_success_operator"
    " ON dial_list_leads(success_operator_id, signed_at) WHERE success_attempt_id IS NOT NULL",
    # Подписавшие, по кому решение об успешке ещё не принято.
    "CREATE INDEX IF NOT EXISTS idx_dial_list_leads_success_pending ON dial_list_leads(signed_at)"
    " WHERE signed_at IS NOT NULL AND success_resolved_at IS NULL",
    # Сотрудник другого отдела на линии: один человек — одна линия, одна линия —
    # один человек. Только среди действующих привязок: история снятых не мешает.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_dial_list_line_members_user
        ON dial_list_line_members(user_id) WHERE released_at IS NULL
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_dial_list_line_members_line
        ON dial_list_line_members(department_id, internal_number) WHERE released_at IS NULL
    """,
    # Живые попытки (13.10.2026): один опрос call-details на компанию (live_calls) каждые
    # несколько секунд ищет их по состоянию и возрасту, а «следующий звонок оператора»
    # (superseded_at) и добор исходов — по оператору и времени звонка.
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_live ON dial_list_attempts(requested_at)"
    " WHERE state IN ('requested', 'leg_ringing', 'leg_answered', 'ended')",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempts_operator_requested"
    " ON dial_list_attempts(operator_id, requested_at)",
    # Автообзвон Тез (15.10.2026). Пул по сроку — только открытые лиды; свои сроки оператора
    # (перезвон, дожим) — по владельцу. «Кому пора проверять поездки» индекса не имеет — см.
    # выше про отметки проверок.
    "CREATE INDEX IF NOT EXISTS idx_dial_list_leads_next_call ON dial_list_leads(department_id, period, next_call_at)"
    " WHERE status IN ('new', 'in_progress')",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_leads_owner_due ON dial_list_leads(owner_operator_id, owner_call_at)"
    " WHERE owner_operator_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_wa_messages_lead ON dial_list_wa_messages(lead_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_attempt_offers_offer ON dial_list_attempt_offers(offer_id)",
    "CREATE INDEX IF NOT EXISTS idx_dial_list_offers_department ON dial_list_offers(department_id, position)",
]


def init_dial_list_schema(cursor):
    """Создать/дополнить схему. Безопасно вызывать на каждом старте."""
    for statement in DDL:
        cursor.execute(statement)
