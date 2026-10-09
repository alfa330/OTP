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
// работы; сотрудники видят свой профиль, «Мои смены» (без смен коллег) и
// «Опросы», стажёры — без «Опросов».
const FRONT_OFFICE_TRAINEE_VIEWS = ['profile', 'work_schedules'];
// «Опросы» у сотрудника — с 06.10.2026, вместе с разделом у главы (см. ниже):
// назначенный опрос проходят только в самом разделе, колокол ведёт туда же, и
// без этой строки опрос висел бы у человека непройденным без единого входа.
// Стажёру раздел не выдан: сервер стажёра в «Опросы» не пускает
// (_surveys_route_guard), а назначить ему опрос руководитель не может — пункт
// меню открывал бы отказ.
const FRONT_OFFICE_OPERATOR_VIEWS = [...FRONT_OFFICE_TRAINEE_VIEWS, 'surveys'];
const FRONT_OFFICE_MANAGER_VIEWS = ['manage_operators', 'groups', 'work_schedules'];
// «Задачи» выданы только главе отдела: у СВ фронт-офисов набор разделов прежний
// (в tez/op раздел есть у обеих ролей, здесь — по запросу владельца только глава).
// «QR доступ» — там же и по той же причине: сотрудники фронт-офиса открывают
// «Вики» (офисы, парки) только по подтверждению, а супервайзеров в отделе нет
// вовсе — подтверждает глава. Без строки в allowlist пункта меню у него не
// появится, и подтвердить доступ станет физически некому.
// «Опросы» — тоже только главе (решение владельца 06.10.2026: руководитель
// фронт-офисов сам назначает опросы своим сотрудникам). Права эта строка не
// выдаёт: сервер главу любого отдела пускает в раздел супервайзером в границах
// его отдела (_surveys_route_guard в bot_schedule2.py) — закрыт был пункт меню.
const FRONT_OFFICE_HEAD_VIEWS = [...FRONT_OFFICE_MANAGER_VIEWS, 'tasks', 'qr_access', 'surveys'];

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
        trainee: FRONT_OFFICE_TRAINEE_VIEWS,
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

/* Личный набор разделов: человеку показываем ТОЛЬКО перечисленное (решение
   владельца 06.10.2026: «чтобы у этого сотрудника отображался только раздел
   байга»). Ключ — users.id; ФИО в публичный репозиторий не кладём.

   Набор СТРОЖЕ карты отдела. Карта оставляет человеку общие разделы
   («Ивенты»), «Библиотеку» по роли и «Вики» по тумблеру отдела — здесь нет и
   их: «только» значит только. Поэтому departmentAllowsView спрашивает набор
   раньше UNIVERSAL_VIEWS, а «Вики» и «Библиотеку», которых карта не ведёт,
   App.jsx сверяет с набором сам (personalViewsAllow).

   Доступа набор НЕ выдаёт — он только прячет то, что человеку досталось бы
   по должности: разделы карты отдела, общие «Ивенты», «Библиотеку» и «Вики».
   «Списки Байги» открывает сервер (baiga/access.py: именной список, круг
   раздела и выдачи — флагом baiga_access в профиле); человек из набора,
   которому раздел не открыт, увидит пустой портал.

   Разделы со СВОИМ кругом доступа набор не прячет — ни выданные поимённо
   («Рассылки», «Касания»), ни выданные отделу своим предикатом («Обращения»,
   «Посылки», «Учёт воды» у СЗоВ). У сотрудника отдела аналитики таких нет.
   Переводят человека из набора в отдел, где они есть, — набор пересмотреть:
   либо снять, либо сказать владельцу, что «только» перестало быть правдой.

   Набор — для РЯДОВОГО сотрудника (оператор, стажёр, должности бэк-офиса), не
   возглавляющего отдел: это его ветка меню и его гарды в App.jsx. Сменили
   человеку роль на супервайзера, тренера или админа, назначили главой отдела —
   набор перестаёт действовать, и меню становится меню новой роли. Иначе
   нельзя: у главы «Учет сотрудников» стоит в меню безусловно, а тренерский
   гард уводил бы человека из раздела набора в «Опросы», гард отдела — обратно,
   и они гоняли бы раздел друг другу без остановки. Заодно строка, забытая
   здесь после повышения, не запрёт админа в одном разделе.

   Зеркало — PERSONAL_VIEW_ALLOWLIST и _personal_views_for в bot_schedule2.py:
   колокол не зовёт человека в разделы, которых у него нет. Тест сверяет оба
   места. */
const PERSONAL_VIEW_ALLOWLIST = {
    540: ['baiga'],
};

/* Тот же набор «только это», но выданный не человеку, а ОТДЕЛУ: рядовому
   сотруднику отдела портал показывает только перечисленное. Решение владельца
   08.10.2026 про удалённый КЦ: «у операторов отдела должны отображаться лишь
   раздел профиль, мои смены и вики, который не доступен без сканирования QR».

   Правила — те же, что у личного набора выше, слово в слово: строже карты
   отдела (нет ни «Ивентов», ни «Библиотеки»), доступа не выдаёт, разделы со
   своим кругом не прячет, действует на рядового, не возглавляющего отдел.
   Личный набор человека сильнее набора его отдела. «Скачать iCore Phone» —
   не раздел, а действие со своим предикатом (canDownloadIcorePhone в App.jsx):
   на этом телефоне отдел и работает, набор его не касается.

   'wiki' в наборе значит «не прятать», и только: сам раздел по-прежнему
   выдаёт тумблер отдела вместе с пространством вики (wikiEnabledFor), а замок
   QR держит должность — оператора спрашивают и портал
   (sensitiveSectionQrRequiredFor в App.jsx), и сервер (QR_GATED_ROLES в
   wiki/access.py). Стажёру QR не выдают вовсе, поэтому вики в его наборе
   нет: иначе раздел открывался бы ему без подтверждения.

   'profile' обязан остаться ПЕРВЫМ: firstAllowedView берёт allow[0], и это
   раздел, в который человек попадает после входа.

   Ключ верхнего уровня — departments.code, внутри — роль. Зеркало —
   DEPARTMENT_ONLY_VIEWS в bot_schedule2.py (колокол); тест сверяет оба места. */
const DEPARTMENT_ONLY_VIEWS = {
    remote_cc: {
        operator: ['profile', 'work_schedules', 'wiki'],
        trainee: ['profile', 'work_schedules'],
    },
};

/* Разделы, которые открывает не карта, а собственный флаг в App.jsx: «Вики» —
   тумблер отдела вместе с пространством. В наборе «только это» такой раздел
   значит «не прятать» (personalViewsAllow), но открыть его набор не может: на
   вопрос departmentAllowsView о нём всегда отвечает «нет», как и карта отдела,
   в которой его не бывает. Иначе гард «Этап 10» оставлял бы человека в вике,
   которую его отделу выключили. */
const OWN_FLAG_VIEWS = new Set(['wiki']);

// Рядовой — как в ветке рядового сотрудника сайдбара (RANK_AND_FILE_ROLES в
// App.jsx) и при том же условии «не глава отдела»; тест сверяет оба списка.
const PERSONAL_VIEW_BASE_ROLES = ['operator', 'trainee'];

const personalViewsApplyTo = (user) => {
    if (isDepartmentHead(user)) return false;
    const role = normalizeRole(user?.role);
    return PERSONAL_VIEW_BASE_ROLES.includes(role) || isBackOfficeEmployeeRole(role);
};

// Набор отдела по коду и роли (или undefined). Код — без пробелов по краям,
// как его читает сервер (_personal_views_for). Имя из прототипа вместо кода
// («constructor») набором не становится: массива под ним нет, а вызывающий
// принимает только массив.
const departmentOnlyViewsOf = (user) => {
    const code = normalizeDepartmentCodeValue(departmentCodeOf(user));
    const byRole = code ? DEPARTMENT_ONLY_VIEWS[code] : null;
    return byRole ? byRole[normalizeRole(user?.role)] : null;
};

// Набор «только это» пользователя — личный, а без него набор его отдела, —
// либо null (набора нет).
export const personalViewsOf = (user) => {
    if (!personalViewsApplyTo(user)) return null;
    // Number(): id в профиле бывает строкой, а имя из прототипа («constructor»)
    // превращается в NaN и ключом карты не становится.
    const personal = PERSONAL_VIEW_ALLOWLIST[Number(user?.id)];
    // Пустой набор — не «спрятать всё», а ошибка записи: набора нет.
    if (Array.isArray(personal) && personal.length) return personal;
    const allow = departmentOnlyViewsOf(user);
    return Array.isArray(allow) && allow.length ? allow : null;
};

// Не скрыт ли раздел набором «только это». Нет набора — не скрыт ничем.
export const personalViewsAllow = (user, viewKey) => {
    const allow = personalViewsOf(user);
    return !allow || allow.includes(viewKey);
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
   отдела с линией) остаётся как есть.

   Удалённый КЦ — в наборе «SIP номер» с 08.10.2026 (решение владельца: при
   заведении сотрудника — «без sip номера, если это удалённый КЦ»). Номер у
   отдела есть, но это линия Binotel, и выдают её в разделе «Удаленный КЦ» на
   вкладке «Линии» вместе с учёткой линии: номер, вписанный в карточку руками,
   учётки не получает, а линию в разделе показывает занятой.
   Зеркало набора «SIP номер» — EMPLOYEE_SIP_INPUT_HIDDEN_DEPARTMENT_CODES в
   bot_schedule2.py: сервер присланный номер при заведении не пишет. */
const EMPLOYEE_DIRECTION_HIDDEN_DEPARTMENTS = new Set(['request_processing_department']);
const EMPLOYEE_SIP_INPUT_HIDDEN_DEPARTMENTS = new Set(['request_processing_department', 'remote_cc']);
const EMPLOYEE_INTERNSHIP_HIDDEN_DEPARTMENTS = new Set(['request_processing_department']);
const EMPLOYEE_TAXIPRO_ID_HIDDEN_DEPARTMENTS = new Set(['request_processing_department']);

/* Отделы, где направление сотруднику выбирать необязательно. Отдел аналитики —
   решение владельца 06.10.2026 («именно у отдела аналитики»): у отдела нет ни
   одного направления (прод, 06.10.2026), а форма и сервер требовали выбрать его
   у каждого оператора — завести аналитика было нельзя вовсе. IT — решение
   владельца 09.10.2026: при заведении сотрудника в IT не обязательны ни группа,
   ни направление (группа — EMPLOYEE_GROUP_OPTIONAL_DEPARTMENTS ниже).

   Это не EMPLOYEE_DIRECTION_HIDDEN_DEPARTMENTS: там поля в карточке нет, и
   присланное направление сервер отбрасывает. Здесь поле остаётся — отделу могут
   завести направления позже, — и выбранное сохраняется как у любого оператора;
   снята только обязательность.
   Зеркало — EMPLOYEE_DIRECTION_OPTIONAL_DEPARTMENT_CODES в bot_schedule2.py. */
const EMPLOYEE_DIRECTION_OPTIONAL_DEPARTMENTS = new Set(['analytik', 'it']);

/* Отделы, где группу при заведении сотрудника выбирать необязательно (решение
   владельца 09.10.2026 — IT, вместе с направлением выше). Поле в карточке
   остаётся: выбранная группа сохраняется как обычно, и супервайзер с
   направлением приходят из неё. Зеркала на сервере нет: ручка add_user группу
   не требует ни у кого — обязательной её делала только карточка. */
const EMPLOYEE_GROUP_OPTIONAL_DEPARTMENTS = new Set(['it']);

const departmentCodeIn = (set) => (code) => {
    const normalized = normalizeDepartmentCodeValue(code);
    return Boolean(normalized && set.has(normalized));
};

export const departmentCodeHidesEmployeeDirection = departmentCodeIn(EMPLOYEE_DIRECTION_HIDDEN_DEPARTMENTS);
export const departmentCodeHidesEmployeeSipInput = departmentCodeIn(EMPLOYEE_SIP_INPUT_HIDDEN_DEPARTMENTS);
export const departmentCodeHidesEmployeeInternship = departmentCodeIn(EMPLOYEE_INTERNSHIP_HIDDEN_DEPARTMENTS);
export const departmentCodeHidesEmployeeTaxiproId = departmentCodeIn(EMPLOYEE_TAXIPRO_ID_HIDDEN_DEPARTMENTS);
export const departmentCodeHasOptionalEmployeeDirection = departmentCodeIn(EMPLOYEE_DIRECTION_OPTIONAL_DEPARTMENTS);
export const departmentCodeHasOptionalEmployeeGroup = departmentCodeIn(EMPLOYEE_GROUP_OPTIONAL_DEPARTMENTS);

export const departmentHidesEmployeeDirection = (user) => departmentCodeHidesEmployeeDirection(departmentCodeOf(user));
export const departmentHidesEmployeeInternship = (user) => departmentCodeHidesEmployeeInternship(departmentCodeOf(user));
export const departmentHidesEmployeeTaxiproId = (user) => departmentCodeHidesEmployeeTaxiproId(departmentCodeOf(user));

// Отделы, чья телефония — кабинет Oktell, а не наш SIP-телефон. У СЗоВ оператор
// работает в клиенте Oktell: регистрации по SIP там нет, а автодозвона, очередей
// FOP2 и автопринятия — тем более (всё это механика локальной АТС и iCORE Phone,
// на котором сам отдел не работает: программу скачивают только его глава и те,
// кого посадили на линию удалённого КЦ, — см. canDownloadIcorePhone в App.jsx).
// От нас клиенту нужна ровно одна пара «логин + пароль кабинета»,
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

/* Должность нового сотрудника после того, как в карточке выбрали отдел. Рядовая
   должность следует за отделом: в бэк-офисе это должность отдела, в отделе с
   линией — оператор. Стажёра, тренера, супервайзера и админа отдел не
   переопределяет — то же правило, что у resolveEmployeeRoleForDepartment в
   App.jsx при отправке. Нужна карточке сразу, а не только при отправке: поля
   «Группа» и «Направление» показываются по должности черновика. */
export const employeeRoleForDepartmentCode = (currentRole, code) => {
    const role = String(currentRole ?? '').trim().toLowerCase() || 'operator';
    if (role !== 'operator' && !isBackOfficeEmployeeRole(role)) return role;
    return departmentCodeEmployeeRole(code) || 'operator';
};

// Возвращает массив разрешённых разделов для пользователя, либо null (без ограничений).
const allowlistFor = (user) => {
    // Глобальные админы — без ограничений по отделу; главы отделов идут по head-набору.
    if (normalizeRole(user?.role) === 'super_admin') return null;
    if (isAdminLikeRole(user?.role) && !isDepartmentHead(user)) return null;
    // Набор «только это» заменяет карту отдела целиком, а не пересекается с ней.
    const personal = personalViewsOf(user);
    if (personal) return personal;
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
    // Набор «только это» — раньше общих разделов: в нём нет и «Ивентов».
    // Разделы со своим флагом (OWN_FLAG_VIEWS) набор не открывает.
    const personal = personalViewsOf(user);
    if (personal) return personal.includes(viewKey) && !OWN_FLAG_VIEWS.has(viewKey);
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
