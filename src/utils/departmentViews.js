// Расширение в пути обязательно: этот модуль грузит напрямую Node в
// tests/back_office_department_views.test.mjs, а ESM без расширения путь не
// разрешает (Vite разрешает и так, поэтому сборка не замечает разницы).
import { isAdminLikeRole, isDepartmentHead, normalizeRole } from './roles.js';

const TEZ_OPERATOR_VIEWS = ['profile', 'evaluation', 'hours', 'work_schedules', 'surveys', 'salary'];
const TEZ_MANAGER_VIEWS = [
    'manage_operators',
    'qr_access',
    'call_evaluation',
    'call_division',
    'monitoring_scale',
    'work_schedules',
    'sv_hours',
    'tasks',
    'salary',
    'surveys',
];
const TEZ_SUPERVISOR_VIEWS = TEZ_MANAGER_VIEWS.filter((view) => view !== 'monitoring_scale');

// Операторы ОП: Зарплата, Профиль, Мои часы, Мои смены, Мои оценки, Опросы.
// «Зарплата» остаётся первой — это раздел по умолчанию (firstAllowedView).
const SALES_OPERATOR_VIEWS = ['salary', 'profile', 'hours', 'work_schedules', 'evaluation', 'surveys'];

const SALES_SUPERVISOR_VIEWS = [
    'manage_operators',
    'qr_access',
    'call_evaluation',
    'call_division',
    'ai_qa',
    'work_schedules',
    // Учёт часов открыт СВ ОП (модели направлений ОП: часы + штрафы).
    'sv_hours',
    'trainings',
    'technical_issues',
    'surveys',
    'tasks',
    'salary',
];
const SALES_HEAD_VIEWS = [
    ...SALES_SUPERVISOR_VIEWS.slice(0, 4),
    'monitoring_scale',
    ...SALES_SUPERVISOR_VIEWS.slice(4),
];

// Фронт офисы: менеджеры ведут только учёт сотрудников, свои группы и графики
// работы; сотрудники видят только свой профиль и «Мои смены» (без смен коллег).
const FRONT_OFFICE_OPERATOR_VIEWS = ['profile', 'work_schedules'];
const FRONT_OFFICE_MANAGER_VIEWS = ['manage_operators', 'groups', 'work_schedules'];
// «Задачи» выданы только главе отдела: у СВ фронт-офисов набор разделов прежний
// (в tez/op раздел есть у обеих ролей, здесь — по запросу владельца только глава).
// «QR доступ» — там же и по той же причине: сотрудники фронт-офиса открывают
// «Вики» (офисы, парки) только по подтверждению, а супервайзеров в отделе нет
// вовсе — подтверждает глава. Без строки в allowlist пункта меню у него не
// появится, и подтвердить доступ станет физически некому.
const FRONT_OFFICE_HEAD_VIEWS = [...FRONT_OFFICE_MANAGER_VIEWS, 'tasks', 'qr_access'];

// Бэк-офис (Бухгалтерия, HR): отделы без телефонии, направлений, графиков и
// оценок. Им оставлены только «Учёт сотрудников» и «Вики».
//
// «Вики» в этой карте не значится намеренно: раздел выдаётся ОТДЕЛУ тумблером
// departments.wiki_enabled вместе с пространством и гейтится wikiEnabledFor в
// App.jsx, а не allowlist'ом — вписав его сюда, мы бы завели вторую, молчаливо
// расходящуюся проверку.
//
// «QR доступ» у главы — по той же причине, что у фронт-офисов: сотрудник с
// ролью «оператор» открывает «Вики» только после подтверждения QR
// (sensitiveSectionQrRequiredFor), а подтверждает админ, супервайзер или глава
// отдела (_sensitive_access_approval_error). Супервайзеров в бэк-офисе нет —
// без этой строки пункта у главы не будет и подтвердить доступ станет некому.
//
// Роли 'trainer' в конфиге нет намеренно: у такого отдела не осталось бы ни
// одного раздела из TRAINER_ALLOWED_VIEWS, и два гарда в App.jsx — тренерский
// (выкидывает в 'surveys') и отдельский (выкидывает в 'profile') — гоняли бы
// вид друг другу без остановки.
// «Задачи» есть у ВСЕХ ролей бэк-офиса, а не только у главы и СВ: в этих
// отделах раздел — рабочий инструмент рядового, а не инструмент надзора.
// Охват у рядового ЛИЧНЫЙ: задачи, где он постановщик, поручитель или
// исполнитель, — и принимать работу за других он не может. Считает это
// бэкенд (database.TASK_PERSONAL_SCOPE_ROLES), карта лишь показывает пункт.
// 'profile' обязан остаться ПЕРВЫМ: firstAllowedView берёт allow[0], и
// раздел по умолчанию сменился бы вместе с порядком.
const BACK_OFFICE_EMPLOYEE_VIEWS = ['profile', 'tasks'];
const BACK_OFFICE_MANAGER_VIEWS = ['manage_operators', 'tasks'];
const BACK_OFFICE_HEAD_VIEWS = [...BACK_OFFICE_MANAGER_VIEWS, 'qr_access'];

// Маркетинг. Отдел устроен как бэк-офис (ни линии, ни направлений, ни групп),
// но разделы у него свои — по решению владельца 04.09.2026 рядовой маркетолог
// смотрит качество обслуживания наравне с главой своего отдела, которому
// «ИИ-оценка» и «Лиды OLX» открыты с 06.08.2026.
//
// СОБСТВЕННАЯ константа, а не BACK_OFFICE_EMPLOYEE_VIEWS: расширив общую, мы
// молча выдали бы те же шесть разделов Бухгалтерии и HR.
//
// Из десяти пунктов, названных владельцем, здесь ШЕСТЬ. Остальные четыре
// картой разделов не выдаются вовсе, и дублировать их сюда — значит завести
// вторую, молча расходящуюся проверку:
//   «Уведомление» — это колокол (NotificationsBell), у него нет view-ключа;
//   «Ивенты»      — UNIVERSAL_VIEWS ниже, раздел общий для всех ролей;
//   «Вики»        — тумблер departments.wiki_enabled плюс пространство вики;
//   «Лиды OLX»    — свой предикат canAccessOlxLeadsForUser в App.jsx.
//
// 'profile' обязан остаться ПЕРВЫМ по двум причинам. Во-первых, firstAllowedView
// берёт allow[0], и это раздел по умолчанию; из шести только 'profile' и
// 'tasks' реально отрисованы в ветке рядового, остальные четыре дали бы пустой
// экран при каждом входе. Во-вторых, «Должность», ради которой отдел и заведён
// как бэк-офисный, сам сотрудник видит именно в «Профиле» — без этой строки
// поле завели бы для человека, который его никогда не увидит.
const MARKETING_EMPLOYEE_VIEWS = [
    'profile',
    'tasks',
    'surveys',
    'lms',
    'call_evaluation',
    'call_division',
];

// ООЗ — отдел обработки запросов (код request_processing_department). Задача
// #359 от главы отдела: сотруднику открыт ТОЛЬКО раздел «Рассылки» — ни
// профиля, ни часов, ни смен. «Ивенты» остаются, как у всех ролей: это
// UNIVERSAL_VIEWS ниже, а не строка отдела.
//
// Саму кнопку отправки эта строка НЕ выдаёт: периметр «Рассылок» именной
// (driver_mailings/access.py), потому что одно нажатие уходит тысяче водителей.
// Сотрудник отдела, которого нет в именном списке, увидит пустой портал, а не
// чужую рассылку. Строка отвечает на другой вопрос — что ЕЩЁ показать
// сотруднику отдела помимо рассылок: ничего.
const REQUEST_PROCESSING_EMPLOYEE_VIEWS = ['driver_mailings'];

const VIEW_ALIASES = {
    sv_list: 'manage_operators',
    manage_users: 'manage_operators',
};

// Разделы, доступные всем ролям/отделам независимо от allowlist отдела.
// «Ивенты» — общая лента компании (пункт меню тоже рендерится для всех);
// без этого исключения guard видимости выкидывал бы сотрудников отделов с
// ограничениями (op/tez) обратно на первый разрешённый раздел (напр. зарплату).
const UNIVERSAL_VIEWS = new Set(['events']);

/*
 * Хардкод-карта «отдел → роль → разрешённые разделы» (view-ключи из App.jsx).
 *
 * Правила:
 *  - Отдел отсутствует в карте  => ограничений НЕТ (напр. СЗоВ — все видят свои
 *    разделы по роли как обычно).
 *  - Роль отсутствует в конфиге отдела => для этой роли ограничений НЕТ.
 *  - Админы / супер-админы НЕ ограничиваются.
 *  - Главы отделов используют отдельный head-набор.
 *  - Для остальных ролей спец-отдела показываем ТОЛЬКО перечисленные разделы.
 *
 * Ключ верхнего уровня — departments.code (lowercase). Внутри — роль → [view-ключи].
 */
export const DEPARTMENT_VIEW_ALLOWLIST = {
    tez: {
        operator: TEZ_OPERATOR_VIEWS,
        trainee: TEZ_OPERATOR_VIEWS,
        head: TEZ_MANAGER_VIEWS,
        sv: TEZ_SUPERVISOR_VIEWS,
    },
    op: {
        operator: SALES_OPERATOR_VIEWS,
        trainee: SALES_OPERATOR_VIEWS,
        // Супервайзеры продаж: их рабочий набор разделов
        head: SALES_HEAD_VIEWS,
        sv: SALES_SUPERVISOR_VIEWS,
    },
    front_office: {
        operator: FRONT_OFFICE_OPERATOR_VIEWS,
        trainee: FRONT_OFFICE_OPERATOR_VIEWS,
        head: FRONT_OFFICE_HEAD_VIEWS,
        sv: FRONT_OFFICE_MANAGER_VIEWS,
    },
    accounting: {
        // 'operator'/'trainee' оставлены для тех, кого успели завести до
        // появления собственной роли: без ключа ограничение снимается целиком.
        operator: BACK_OFFICE_EMPLOYEE_VIEWS,
        trainee: BACK_OFFICE_EMPLOYEE_VIEWS,
        accounting_manager: BACK_OFFICE_EMPLOYEE_VIEWS,
        head: BACK_OFFICE_HEAD_VIEWS,
        sv: BACK_OFFICE_MANAGER_VIEWS,
    },
    hr: {
        operator: BACK_OFFICE_EMPLOYEE_VIEWS,
        trainee: BACK_OFFICE_EMPLOYEE_VIEWS,
        hr_manager: BACK_OFFICE_EMPLOYEE_VIEWS,
        head: BACK_OFFICE_HEAD_VIEWS,
        sv: BACK_OFFICE_MANAGER_VIEWS,
    },
    // Маркетинг: ограничена ТОЛЬКО собственная должность отдела — решение
    // владельца 04.09.2026. Ключей 'operator'/'trainee' здесь намеренно нет,
    // в отличие от бэк-офиса: людей, заведённых в отделе до появления
    // должности, ограничение не касается, и портал у них не меняется.
    // Ключа 'head' нет по той же причине — глава отдела остаётся без
    // ограничений (роль отсутствует в конфиге => allowlistFor вернёт null).
    marketing: {
        marketing_manager: MARKETING_EMPLOYEE_VIEWS,
    },
    // ООЗ: ограничены только рядовые. Ключа 'head' нет намеренно — глава
    // отдела (админ портала) остаётся без ограничений, и её меню не меняется.
    request_processing_department: {
        operator: REQUEST_PROCESSING_EMPLOYEE_VIEWS,
        trainee: REQUEST_PROCESSING_EMPLOYEE_VIEWS,
    },
};

export const departmentCodeOf = (user) => {
    const code = user?.department_code ?? user?.departmentCode;
    return code ? String(code).toLowerCase() : null;
};

// Отделы, у СВ которых часы считаются по отметкам Clockster, а РОП ведёт их
// график и перерыв (задача #352). Зеркало SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENT_CODES
// в supervisor_hours.py — меняются вместе.
const SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENTS = new Set(['op']);

// Свой отдел пользователя — для СВ: его часы и график коллег-СВ.
export const departmentHasSupervisorHours = (user) => {
    const code = departmentCodeOf(user);
    return Boolean(code && SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENTS.has(code));
};

// Возглавляемый отдел — для РОП: карточка отдела в users у главы может быть
// другой или пустой, а права на СВ сервер даёт именно по главенству.
export const headsSupervisorHoursDepartment = (user) => {
    const codes = [];
    const plural = user?.headed_department_codes ?? user?.headedDepartmentCodes;
    if (Array.isArray(plural)) codes.push(...plural);
    codes.push(user?.headed_department_code ?? user?.headedDepartmentCode);
    return codes.some((code) => code && SUPERVISOR_CLOCKSTER_HOURS_DEPARTMENTS.has(String(code).toLowerCase()));
};

// Отделы, операторам которых нельзя видеть смены коллег по отделу/направлению:
// в «Мои смены» скрываются табы «Замены» и «Смены коллег» вместе с кнопками
// обмена; бэкенд зеркалит это запретом /work_schedules/direction и shift_swap.
const COLLEAGUE_SCHEDULES_HIDDEN_DEPARTMENTS = new Set(['front_office']);

export const departmentHidesColleagueSchedules = (user) => {
    const code = departmentCodeOf(user);
    return Boolean(code && COLLEAGUE_SCHEDULES_HIDDEN_DEPARTMENTS.has(code));
};

// Отделы с упрощённым «Учётом сотрудников» у главы: без пунктов «Супервайзеры»
// и «Тренеры» — сразу список сотрудников (manage_users), в разделе они
// называются «Сотрудники», а не «Операторы». Бэк-офис здесь по той же причине,
// что и фронт-офисы: ни супервайзеров, ни тренеров в этих отделах нет, и оба
// пункта выпадашки открывали бы заведомо пустые списки.
const SIMPLE_EMPLOYEE_ACCOUNTING_DEPARTMENTS = new Set(['front_office', 'accounting', 'hr']);

export const departmentUsesSimpleEmployeeAccounting = (user) => {
    const code = departmentCodeOf(user);
    return Boolean(code && SIMPLE_EMPLOYEE_ACCOUNTING_DEPARTMENTS.has(code));
};

const normalizeDepartmentCodeValue = (code) => String(code ?? '').trim().toLowerCase();

/* Отдел, который ведёт кадровый учёт ПО ВСЕЙ КОМПАНИИ. С 22.09.2026 «Учет
   сотрудников» открыт ему целиком: все четыре списка выпадашки («Супервайзеры»,
   «Сотрудники», «Тренеры», «Админы») и правка наравне с супер-админом.

   Решения владельца того дня, в два захода. Сперва: «нужно его открыть для Hr
   направления... просматривать данные без возможности их изменить». Следом:
   «открой доступ к редактированию и к другим вариантам сотрудников, то есть это
   админы, сотрудники и супервайзеры». На вопрос, касается ли это логинов и
   паролей админов, — «без исключений»; про «Тренеров» — «да, все четыре
   списка»; про заведение и переводы — «да, полный набор».

   Отсюда правило: ПО ЛЮДЯМ границ нет — ни по отделу, ни по должности цели.
   Периметр только про людей: других разделов портала он не открывает. Глава
   отдела кадров входит сюда наравне с рядовым — их права совпадают. Не дали
   одного: завести НОВОГО админа (выдачу админских прав владелец не называл).

   Своим отделом кадровик видел бы трёх человек, то есть себя: тот же довод, по
   которому отделу открыли «Отметки» на всю компанию (задача #273).

   Живёт ЗДЕСЬ, а не в App.jsx, потому что читается из двух мест: портал решает
   по нему, показывать ли раздел, а карточка сотрудника — можно ли выбрать
   отдел. Разъехались бы — кадровик открывал бы карточку, в которой отдел
   заперт на его собственный, и завести человека на линию не смог бы.

   Признак — ЧЛЕНСТВО В ОТДЕЛЕ, а не роль: у кадровика роль hr_manager с
   уровнем как у оператора. Зеркало на бэкенде —
   EMPLOYEE_ACCOUNTING_DEPARTMENT_CODE и _is_employee_accounting_manager
   в bot_schedule2.py. */
const EMPLOYEE_ACCOUNTING_DEPARTMENTS = new Set(['hr']);

export const departmentCodeManagesEmployeeAccounting = (code) => {
    const normalized = normalizeDepartmentCodeValue(code);
    return Boolean(normalized && EMPLOYEE_ACCOUNTING_DEPARTMENTS.has(normalized));
};

export const managesEmployeeAccounting = (user) =>
    departmentCodeManagesEmployeeAccounting(departmentCodeOf(user));

// Отделы, чьи сотрудники сидят по офисам в разных городах: в карточке
// сотрудника у них есть «Город», у остальных отделов поля нет.
const EMPLOYEE_CITY_DEPARTMENTS = new Set(['front_office']);

export const departmentCodeUsesEmployeeCity = (code) => {
    const normalized = normalizeDepartmentCodeValue(code);
    return Boolean(normalized && EMPLOYEE_CITY_DEPARTMENTS.has(normalized));
};

export const departmentUsesEmployeeCity = (user) => departmentCodeUsesEmployeeCity(departmentCodeOf(user));

// Отделы, у сотрудников которых в карточке есть «Должность». У бэк-офиса
// человека определяет именно она: направления и группы, которыми
// различают людей на линии, там не заведены вовсе (см.
// OPERATOR_FIELDS_HIDDEN_DEPARTMENTS — те же коды с другой стороны).
// ООЗ — по просьбе владельца 25.09.2026: там у сотрудника есть группа, но
// направлений нет, и должность («менеджер отдела обработки запросов»)
// указывают при заведении.
// В базе колонка называется job_title: position — ключевое слово Postgres.
const EMPLOYEE_JOB_TITLE_DEPARTMENTS = new Set(['accounting', 'hr', 'marketing', 'request_processing_department']);

export const departmentCodeUsesEmployeeJobTitle = (code) => {
    const normalized = normalizeDepartmentCodeValue(code);
    return Boolean(normalized && EMPLOYEE_JOB_TITLE_DEPARTMENTS.has(normalized));
};

export const departmentUsesEmployeeJobTitle = (user) => departmentCodeUsesEmployeeJobTitle(departmentCodeOf(user));

// Отделы, у сотрудников которых не спрашиваем «Был во фронт офисе на обучении»:
// сотрудники фронт-офисов и есть фронт офис, отметка для них бессмысленна, а
// бухгалтерия, HR и ООЗ на линию не выходят вовсе — обучать их работе в офисе
// продаж незачем (ООЗ — решение владельца 25.09.2026). Скрываем только ввод —
// уже сохранённое значение сохраняется как есть.
const FRONT_OFFICE_TRAINING_HIDDEN_DEPARTMENTS = new Set(['front_office', 'accounting', 'hr', 'request_processing_department']);

export const departmentCodeHidesFrontOfficeTraining = (code) => {
    const normalized = normalizeDepartmentCodeValue(code);
    return Boolean(normalized && FRONT_OFFICE_TRAINING_HIDDEN_DEPARTMENTS.has(normalized));
};

export const departmentHidesFrontOfficeTraining = (user) => departmentCodeHidesFrontOfficeTraining(departmentCodeOf(user));

// Отделы без операторских полей в карточке сотрудника: «Группа», «Направление»
// и «SIP номер». У бэк-офиса нет ни групп, ни направлений, ни телефонии —
// сотрудники не сидят на линии и по направлениям не делятся, а пустые
// выпадашки только просят выбрать то, чего нет.
//
// Скрываем не только ввод: с этих отделов снимается и обязательность группы и
// направления при создании сотрудника (UserEditModal.handleSave). Без этого
// глава бэк-офиса не смог бы завести человека вовсе — валидация требовала
// выбрать группу и направление, которых в отделе не существует.
//
// Уже сохранённые значения (сотрудника перевели из отдела с линией) остаются
// как есть: поле не показываем, но и не затираем.
const OPERATOR_FIELDS_HIDDEN_DEPARTMENTS = new Set(['accounting', 'hr', 'marketing']);

export const departmentCodeHidesOperatorFields = (code) => {
    const normalized = normalizeDepartmentCodeValue(code);
    return Boolean(normalized && OPERATOR_FIELDS_HIDDEN_DEPARTMENTS.has(normalized));
};

export const departmentHidesOperatorFields = (user) => departmentCodeHidesOperatorFields(departmentCodeOf(user));

// Отделы без супервайзеров: людей в них ведёт глава отдела напрямую, роль 'sv'
// там не заведена вовсе. Проверено по проду 09.09.2026: у всех 22 активных
// сотрудников фронт-офисов supervisor_id пуст, а людей с ролью sv/supervisor в
// отделе нет ни одного (они есть только в op, szov и tez). Это же зафиксировано
// выше в комментарии к FRONT_OFFICE_HEAD_VIEWS — «супервайзеров в отделе нет
// вовсе, подтверждает глава» — и в SIMPLE_EMPLOYEE_ACCOUNTING_DEPARTMENTS,
// который убирает пункт «Супервайзеры» из выпадашки сайдбара.
//
// Фронт-офисы в OPERATOR_FIELDS_HIDDEN_DEPARTMENTS НЕ входят намеренно:
// направление у них есть (21 из 22), есть группы, графики и ставка — отдел
// устроен как линия во всём, кроме супервайзеров и телефонии. Три кода
// бэк-офиса здесь перечислены хотя и лишний раз (их гасит уже
// departmentCodeHidesOperatorFields), чтобы предикат отвечал верно сам по
// себе: «есть ли в отделе супервайзеры» — вопрос об отделе, а не о том,
// в каком порядке сложились гейты в карточке.
const EMPLOYEE_SUPERVISOR_HIDDEN_DEPARTMENTS = new Set(['front_office', 'accounting', 'hr', 'marketing']);

export const departmentCodeHidesEmployeeSupervisor = (code) => {
    const normalized = normalizeDepartmentCodeValue(code);
    return Boolean(normalized && EMPLOYEE_SUPERVISOR_HIDDEN_DEPARTMENTS.has(normalized));
};

export const departmentHidesEmployeeSupervisor = (user) => departmentCodeHidesEmployeeSupervisor(departmentCodeOf(user));

// Отделы без нашей телефонии: SIP-номера сотрудникам не выдаются. У фронт-офисов
// sip_number пуст у всех 22 активных (прод, 09.09.2026) — звонят они из кабинета
// CRM yataxi, а их статистика приходит из region-call-stats, а не из Oktell
// (задача #159). Список тот же, что у супервайзеров, но набор ОТДЕЛЬНЫЙ: это
// два независимых свойства отдела, и телефонию фронт-офисам могут завести, не
// заводя супервайзеров. ООЗ телефонии тоже не имеет (владелец, 25.09.2026).
const EMPLOYEE_SIP_HIDDEN_DEPARTMENTS = new Set(['front_office', 'accounting', 'hr', 'marketing', 'request_processing_department']);

export const departmentCodeHidesEmployeeSip = (code) => {
    const normalized = normalizeDepartmentCodeValue(code);
    return Boolean(normalized && EMPLOYEE_SIP_HIDDEN_DEPARTMENTS.has(normalized));
};

export const departmentHidesEmployeeSip = (user) => departmentCodeHidesEmployeeSip(departmentCodeOf(user));

/* Поля карточки, которых нет у ООЗ (решение владельца 25.09.2026: «у ООЗ нет
   SIP номера, практики в компании, обучения во фронт офисе и ID таксипро»).
   Сотрудник ООЗ заводится оператором и зачисляется в группу — группа в
   карточке остаётся, — но на линию не выходит.

   «Направление» здесь же, хотя владелец его не называл: у отдела нет ни
   одного направления (прод, 25.09.2026), а форма и сервер требовали выбрать
   его у каждого оператора — завести сотрудника ООЗ было нельзя вовсе.

   «SIP номер» — ОТДЕЛЬНЫЙ набор от EMPLOYEE_SIP_HIDDEN_DEPARTMENTS выше: тот
   решает про колонку в списке сотрудников, а фронт-офисам ввод номера в
   карточке оставлен — владелец его не убирал, и снимать поле у чужого
   отдела заодно значило бы менять не своё. Бэк-офис не перечислен ни в
   одном из наборов: у него все операторские поля гасит
   departmentCodeHidesOperatorFields.

   Скрываем только ввод: уже сохранённое значение (человека перевели из
   отдела с линией) остаётся как есть. */
const EMPLOYEE_DIRECTION_HIDDEN_DEPARTMENTS = new Set(['request_processing_department']);
const EMPLOYEE_SIP_INPUT_HIDDEN_DEPARTMENTS = new Set(['request_processing_department']);
const EMPLOYEE_INTERNSHIP_HIDDEN_DEPARTMENTS = new Set(['request_processing_department']);
const EMPLOYEE_TAXIPRO_ID_HIDDEN_DEPARTMENTS = new Set(['request_processing_department']);

const departmentCodeIn = (set) => (code) => {
    const normalized = normalizeDepartmentCodeValue(code);
    return Boolean(normalized && set.has(normalized));
};

export const departmentCodeHidesEmployeeDirection = departmentCodeIn(EMPLOYEE_DIRECTION_HIDDEN_DEPARTMENTS);
export const departmentCodeHidesEmployeeSipInput = departmentCodeIn(EMPLOYEE_SIP_INPUT_HIDDEN_DEPARTMENTS);
export const departmentCodeHidesEmployeeInternship = departmentCodeIn(EMPLOYEE_INTERNSHIP_HIDDEN_DEPARTMENTS);
export const departmentCodeHidesEmployeeTaxiproId = departmentCodeIn(EMPLOYEE_TAXIPRO_ID_HIDDEN_DEPARTMENTS);

export const departmentHidesEmployeeDirection = (user) => departmentCodeHidesEmployeeDirection(departmentCodeOf(user));
export const departmentHidesEmployeeInternship = (user) => departmentCodeHidesEmployeeInternship(departmentCodeOf(user));
export const departmentHidesEmployeeTaxiproId = (user) => departmentCodeHidesEmployeeTaxiproId(departmentCodeOf(user));

// Отделы, чья телефония — кабинет Oktell, а не наш SIP-телефон. У СЗоВ оператор
// работает в клиенте Oktell: регистрации по SIP там нет, а автодозвона, очередей
// FOP2 и автопринятия — тем более (всё это механика локальной АТС и iCORE Phone,
// которого отделу не выдают, см. download_icore_phone в SIDEBAR_SECTION_DEPARTMENTS
// в App.jsx). От нас клиенту нужна ровно одна пара «логин + пароль кабинета»,
// которой он входит за оператора, — её и показывает карточка сотрудника в
// «Настройках SIP», без единого лишнего поля.
//
// Внутренний номер (users.sip_number) у таких отделов остаётся и дальше: в Oktell
// это логин агента (oktell_guard/queries.py) и ключ привязки звонков к оператору
// в табло и оценках. Скрытие полей его не трогает — правится он в «Учёте
// сотрудников», где заводят самого человека.
const OKTELL_CABINET_DEPARTMENTS = new Set(['szov']);

export const departmentCodeUsesOktellCabinet = (code) => {
    const normalized = normalizeDepartmentCodeValue(code);
    return Boolean(normalized && OKTELL_CABINET_DEPARTMENTS.has(normalized));
};

export const departmentUsesOktellCabinet = (user) => departmentCodeUsesOktellCabinet(departmentCodeOf(user));

// Роль, с которой заводится рядовой сотрудник отдела. 'operator' в этой
// системе означает человека НА ЛИНИИ — с направлением, группой, часами и
// оценками; бухгалтеру и кадровику она давала бы разделы и поля, которых у
// них нет. Отдела нет в карте => роль по умолчанию, её решает вызывающий.
// Зеркало — BACK_OFFICE_EMPLOYEE_ROLES в bot_schedule2.py и CHECK на
// users.role в database.py.
const BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT = {
    accounting: 'accounting_manager',
    hr: 'hr_manager',
    marketing: 'marketing_manager',
};

export const BACK_OFFICE_EMPLOYEE_ROLES = Object.freeze(
    Object.values(BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT),
);

export const departmentCodeEmployeeRole = (code) =>
    BACK_OFFICE_EMPLOYEE_ROLE_BY_DEPARTMENT[normalizeDepartmentCodeValue(code)] || null;

export const departmentEmployeeRole = (user) => departmentCodeEmployeeRole(departmentCodeOf(user));

export const isBackOfficeEmployeeRole = (role) =>
    BACK_OFFICE_EMPLOYEE_ROLES.includes(String(role ?? '').trim().toLowerCase());

// Возвращает массив разрешённых разделов для пользователя, либо null (без ограничений).
const allowlistFor = (user) => {
    // Глобальные админы — без ограничений по отделу; главы отделов идут по head-набору.
    if (normalizeRole(user?.role) === 'super_admin') return null;
    if (isAdminLikeRole(user?.role) && !isDepartmentHead(user)) return null;
    const code = departmentCodeOf(user);
    const deptCfg = code ? DEPARTMENT_VIEW_ALLOWLIST[code] : null;
    if (!deptCfg) return null;
    const role = isDepartmentHead(user) ? 'head' : normalizeRole(user?.role);
    const allow = deptCfg[role];
    return Array.isArray(allow) ? allow : null;
};

export const departmentRestrictsViews = (user) => Array.isArray(allowlistFor(user));

// Разрешён ли раздел viewKey пользователю с учётом его отдела и роли.
export const departmentAllowsView = (user, viewKey) => {
    if (UNIVERSAL_VIEWS.has(viewKey)) return true;
    const allow = allowlistFor(user);
    if (!allow) return true; // нет ограничений
    if (allow.includes(viewKey)) return true;
    const alias = VIEW_ALIASES[viewKey];
    return Boolean(alias && isDepartmentHead(user) && allow.includes(alias));
};

// Первый разрешённый раздел: сначала из переданных кандидатов, иначе — первый из allowlist.
export const firstAllowedView = (user, candidates = []) => {
    const allow = allowlistFor(user);
    for (const v of candidates) {
        if (!allow || allow.includes(v)) return v;
    }
    return allow && allow.length ? allow[0] : null;
};
