import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import { APPLE_FONT, iosCard, iosGroupLabel, iosBtnSecondary, IosBadge, IosHint, IosSegmented } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { DialListLeadsPanel, DialListOperatorsPanel, DialListSettingsPanel, useDialListDepartment } from '../sip/DialListSettings';
import DialListLinesPanel from './DialListLinesPanel';
import DialListJournal from './DialListJournal';
import DialListAnalytics from './DialListAnalytics';
import DialListOutcomesPanel from './DialListOutcomesPanel';
import DialListScriptPanel from './DialListScriptPanel';
import DialListCallsExport from './DialListCallsExport';
import DialListInformerPanel from './DialListInformerPanel';
import { monthLabel } from './dialListPeriods';
import { sameSectionTitle } from './linePicker';
import TezAutodialAnalytics from './TezAutodialAnalytics';
import { TezOffersPanel, TezOperatorRow, TezWhatsAppPanel } from './TezAutodialPanels';
import { PROFILE_REMOTE_CC, PROFILE_TEZ, TEZ_SECTION_TITLE, departmentsOfProfile, normalizeProfile } from './tezAutodial';

/*
 * Раздел «Удаленный КЦ» (до 07.10.2026 — «Обзвон из телефона») — экран руководителя
 * удалённого колл-центра.
 *
 * Вкладки: «Операторы» (кто сколько сделал за день), «Аналитика» (показатели базы
 * месяца: воронка, подписание документов, дни, операторы), «Линии», «База
 * водителей» (загрузка файла ФИО + телефон + ИИН), «Журнал», «Настройки» и
 * «Скрипт». Отдел выбирается в шапке; если он один — выбора нет. Там же —
 * «Детализация»: звонки отдела за период в Excel (DialListCallsExport, 15.10.2026).
 *
 * Глава «Регионов» (группа «Фронт офисов», её люди на линиях удалённого КЦ) видит раздел
 * только отчётами своей группы: сервер отдаёт ей report_groups, и экран — шапка с
 * «Детализацией» и карточка о ней, без вкладок (владелец, 15.10.2026).
 *
 * Сводки над вкладками нет (просьба владельца 29.09.2026: «сверху сводку убрать,
 * чтобы не отвлекала») — показатели живут во вкладке «Аналитика».
 *
 * Как считается: строка выдачи закрывается после первой попытки с исходом,
 * подтверждённым Binotel (вебхук или опрос по generalCallID) — телефон оператора
 * здесь ничего не «доказывает». Поэтому цифры могут догонять разговор на
 * несколько секунд.
 *
 * С 15.10.2026 тот же экран — раздел «Автообзвон Тез» (profile='tez'): тот же движок,
 * но отделы своего профиля, цель лида — выход на линию (TEZ APP), а в настройках —
 * автообзвон, постобработка, расписание недозвонов, офферы и WhatsApp. В статусе
 * «Активный» телефон оператора звонит сам, подряд; в «Исходе» — оператор сам.
 */

const TABS = [
    { value: 'operators', label: 'Операторы', icon: <FaIcon className="fas fa-users" /> },
    { value: 'analytics', label: 'Аналитика', icon: <FaIcon className="fas fa-chart-bar" /> },
    { value: 'lines', label: 'Линии', icon: <FaIcon className="fas fa-phone" /> },
    { value: 'leads', label: 'База водителей', icon: <FaIcon className="fas fa-address-book" /> },
    { value: 'journal', label: 'Журнал', icon: <FaIcon className="fas fa-clock-rotate-left" /> },
    // Автоинформатор (15.10.2026) — только у удалённого КЦ: робот звонит с его линии.
    { value: 'informer', label: 'Автоинформатор', icon: <FaIcon className="fas fa-phone-volume" /> },
    { value: 'settings', label: 'Настройки', icon: <FaIcon className="fas fa-sliders" /> },
    { value: 'script', label: 'Скрипт', icon: <FaIcon className="fas fa-file-lines" /> },
];

const todayIso = () => {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
};

const fmtTalk = (sec) => {
    const s = Math.max(0, Number(sec) || 0);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h) return `${h} ч ${m} мин`;
    if (m) return `${m} мин`;
    return s ? `${s} с` : '—';
};

const pct = (part, total) => (total ? Math.round((part / total) * 100) : null);

// Порядок отделов в шапке: сначала с включённым режимом, затем с базой, затем по
// имени. Иначе первым по алфавиту вставал отдел, к обзвону не относящийся.
const sortDepartments = (list) => [...list].sort((a, b) => (
    (Number(!!b.enabled) - Number(!!a.enabled))
    || ((b.leads_total || 0) - (a.leads_total || 0))
    || String(a.department_name || '').localeCompare(String(b.department_name || ''), 'ru')
));

// Последний открытый отдел помним в браузере: раздел открывается на нём, а не на
// первом в списке. Хранилище может быть недоступно (приватное окно) — тогда молча
// без памяти.
// У «Автообзвона Тез» память своя: отделы разделов разные.
const lastDeptKey = (tez) => (tez ? 'otp.tez_autodial.last_department' : 'otp.dial_list.last_department');
const readLastDept = (tez) => { try { return window.localStorage.getItem(lastDeptKey(tez)) || ''; } catch { return ''; } };
const writeLastDept = (tez, id) => { try { if (id) window.localStorage.setItem(lastDeptKey(tez), String(id)); } catch { /* нет хранилища */ } };

const initials = (name) => String(name || '')
    .split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('') || '•';

// Название раздела. С 07.10.2026 оно совпадает с названием самого отдела удалённого
// КЦ, поэтому плашка единственного отдела в шапке показывается, только когда имя
// отдела другое (sameSectionTitle): иначе в одной строке дважды стояло бы «Удаленный КЦ».
const SECTION_TITLE = 'Удаленный КЦ';

/* Строка оператора: имя, полоса «обработано из выданных», цифры справа. */
const OperatorRow = ({ row, showDepartment }) => {
    const progress = row.issued ? Math.min(100, Math.round((row.done / row.issued) * 100)) : 0;
    const answered = pct(row.answered, row.attempts);
    return (
        <div className="flex items-center gap-3 px-4 py-3">
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-slate-100 text-[12px] font-semibold text-slate-600">
                {initials(row.operator_name)}
            </div>
            <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                    <div className="truncate text-[13.5px] font-medium text-slate-800">{row.operator_name || `#${row.operator_id}`}</div>
                    {showDepartment && row.department_name && (
                        <span className="truncate text-[11.5px] text-slate-400">{row.department_name}</span>
                    )}
                </div>
                <div className="mt-1 flex items-center gap-2">
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                        <div className="h-full rounded-full bg-blue-500 transition-all" style={{ width: `${progress}%` }} />
                    </div>
                    <div className="w-24 shrink-0 text-right text-[11.5px] tabular-nums text-slate-500">
                        {row.done} из {row.issued}
                    </div>
                </div>
            </div>
            <div className="hidden shrink-0 items-center gap-1.5 sm:flex">
                <IosBadge tone="blue" title="Попыток">{row.attempts} звон.</IosBadge>
                {/* Дозвон — разговор от 10 с (владелец, 29.10.2026); короткий — в минутах, но не здесь. */}
                <IosBadge tone={answered == null ? 'slate' : answered >= 50 ? 'green' : 'amber'} title="Дозвонились: разговор от 10 секунд">
                    {row.answered}{answered != null ? ` · ${answered}%` : ''}
                </IosBadge>
                <IosBadge tone="slate" title="Разговоры">{fmtTalk(row.talk_sec)}</IosBadge>
                {row.successes > 0 && (
                    <IosBadge tone="green" title="Успешки: водитель подписал документы в этот день после разговора с оператором">
                        успешки {row.successes}
                    </IosBadge>
                )}
                {row.failed > 0 && <IosBadge tone="red" title="Отказы АТС">АТС {row.failed}</IosBadge>}
                {row.cancelled > 0 && (
                    <IosBadge tone="slate" title="Оператор завершил звонок до ответа водителя — попытки не засчитаны">
                        отменено {row.cancelled}
                    </IosBadge>
                )}
            </div>
        </div>
    );
};

const DialListView = ({ user, showToast, apiBaseUrl, withAccessTokenHeader, canEdit = true, profile = PROFILE_REMOTE_CC }) => {
    const sectionProfile = normalizeProfile(profile);
    const tez = sectionProfile === PROFILE_TEZ;
    const sectionTitle = tez ? TEZ_SECTION_TITLE : SECTION_TITLE;
    const authHeaders = useCallback(
        (extra = {}) => withAccessTokenHeader({ 'X-User-Id': String(user?.id ?? ''), ...extra }),
        [withAccessTokenHeader, user?.id]
    );

    const [departments, setDepartments] = useState(null); // null — ещё грузим
    const [candidates, setCandidates] = useState([]);     // отделы на Binotel, ещё не подключённые
    const [enrollPick, setEnrollPick] = useState('');
    const [enrolling, setEnrolling] = useState(false);
    const [departmentId, setDepartmentId] = useState('');
    const [tab, setTab] = useState('operators');
    // Месяц базы, который смотрим во вкладках «База водителей» и «Журнал»
    // ('' — обзваниваемый месяц отдела). Сбрасывается при смене отдела.
    const [period, setPeriod] = useState('');
    const [date, setDate] = useState(todayIso);
    // Переход из «Аналитики» в журнал с готовым отбором: { sign | stage | operatorId, nonce }.
    const [journalPreset, setJournalPreset] = useState(null);
    const presetSeq = useRef(0);
    const openJournal = useCallback((preset) => {
        presetSeq.current += 1;
        setJournalPreset({ ...preset, nonce: presetSeq.current });
        setTab('journal');
    }, []);
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    // Глава «Регионов» (владелец, 15.10.2026): сервер отдаёт ей не зону руководителя, а группы,
    // по которым она выгружает отчёты (report_groups). Тогда раздел — одна «Детализация»: ни
    // вкладок, ни их запросов (сервер на них ответил бы 403). null — обычный руководитель.
    const [reportGroups, setReportGroups] = useState(null);
    const reportsOnly = Array.isArray(reportGroups);

    // Отделы раздела — своим списком: бэкенд сам знает, какие отделы подключены
    // и какие из них в зоне запросившего. Кандидаты (на Binotel, но ещё не
    // подключённые) приходят только админу — для кнопки «Подключить отдел».
    const loadDepartments = useCallback(async (preferId = null) => {
        try {
            // Отделы своего раздела: сервер режет список по профилю сам, departmentsOfProfile —
            // страховка на время раскатки (сервер без профилей отдал бы все отделы).
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments?profile=${sectionProfile}`, { credentials: 'include', headers: authHeaders() });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            const list = sortDepartments(departmentsOfProfile(data.departments, sectionProfile));
            const cands = Array.isArray(data.candidates) ? data.candidates : [];
            setReportGroups(Array.isArray(data.report_groups) ? data.report_groups : null);
            setDepartments(list);
            setCandidates(cands);
            setEnrollPick((cur) => cur || (cands[0] ? String(cands[0].department_id) : ''));
            const has = (id) => id && list.some((d) => String(d.department_id) === String(id));
            setDepartmentId((current) => {
                if (has(preferId)) return String(preferId);
                if (has(current)) return current;
                const remembered = readLastDept(tez);
                if (has(remembered)) return remembered;
                return list[0] ? String(list[0].department_id) : '';
            });
        } catch (e) {
            setDepartments([]);
            setError(e.message || 'Не удалось загрузить отделы');
        }
    }, [apiBaseUrl, authHeaders, sectionProfile, tez]);

    useEffect(() => { loadDepartments(); }, [loadDepartments]);
    useEffect(() => { writeLastDept(tez, departmentId); setPeriod(''); }, [departmentId, tez]);

    // Кандидаты приходят только админу: по ним же понимаем, что отключать отдел можно.
    const candidatesAdmin = Array.isArray(candidates);

    const unenroll = async () => {
        if (!departmentId || enrolling) return;
        const dep = (departments || []).find((d) => String(d.department_id) === String(departmentId));
        if (!window.confirm(`Отключить отдел «${dep?.department_name || ''}» от обзвона? Операторы вернутся к обычному телефону после перезапуска.`)) return;
        setEnrolling(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/unenroll`, {
                method: 'POST', credentials: 'include', headers: authHeaders({ 'Content-Type': 'application/json' }), body: '{}',
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            showToast?.('Отдел отключён от обзвона', 'success');
            setDepartmentId('');
            await loadDepartments();
            setTab('operators');
        } catch (e) {
            showToast?.(e.message || 'Не удалось отключить отдел', 'error');
        } finally {
            setEnrolling(false);
        }
    };

    const enroll = async () => {
        if (!enrollPick || enrolling) return;
        setEnrolling(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${enrollPick}/enroll`, {
                method: 'POST', credentials: 'include', headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(tez ? { profile: PROFILE_TEZ } : {}),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            const picked = candidates.find((d) => String(d.department_id) === String(enrollPick));
            showToast?.(tez
                ? `Отдел «${picked?.department_name || ''}» подключён к автообзвону. Включите операторов во вкладке «Настройки»`
                : `Отдел «${picked?.department_name || ''}» подключён. Включите режим во вкладке «Настройки»`, 'success');
            await loadDepartments(enrollPick);
            setTab('settings');
        } catch (e) {
            showToast?.(e.message || 'Не удалось подключить отдел', 'error');
        } finally {
            setEnrolling(false);
        }
    };

    const loadOverview = useCallback(async () => {
        if (!departmentId || reportsOnly) { setRows([]); setLoading(false); return; }
        setLoading(true);
        try {
            const qs = new URLSearchParams({ date, department_id: departmentId });
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/overview?${qs.toString()}`, { credentials: 'include', headers: authHeaders() });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            setRows(Array.isArray(data.operators) ? data.operators : []);
            setError('');
        } catch (e) {
            setError(e.message || 'Не удалось загрузить сводку');
            setRows([]);
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, authHeaders, date, departmentId, reportsOnly]);

    useEffect(() => { loadOverview(); }, [loadOverview]);

    const dept = useDialListDepartment({
        apiBaseUrl, authHeaders, departmentId: (!reportsOnly && departmentId) || null, period: period === 'all' ? '' : period,
        profile: sectionProfile,
    });
    const selected = (departments || []).find((d) => String(d.department_id) === String(departmentId));
    // Название группы в подписи — действующие группы; архивные сервер учитывает, но ими не шумим.
    const activeReportGroups = (reportGroups || []).filter((g) => g.active !== false);
    const reportGroupNames = (activeReportGroups.length ? activeReportGroups : reportGroups || [])
        .map((g) => g.name).filter(Boolean).join(', ');

    const sortedRows = useMemo(() => [...rows].sort((a, b) => (b.done - a.done) || (b.attempts - a.attempts)
        || String(a.operator_name || '').localeCompare(String(b.operator_name || ''), 'ru')), [rows]);

    const enabled = !!dept.settings?.enabled;
    // У Тез режим отдела целиком всегда выключен (операторов включают лично): «включён» —
    // это автообзвон.
    const autoDial = !!dept.settings?.auto_dial;
    const isToday = date === todayIso();

    const counts = selected
        ? `Порции по ${dept.settings?.portion_size ?? selected.portion_size}, в базе ${selected.leads_total}${dept.summary ? `, доступно ${dept.summary.pool_available}` : ''}`
        : '';
    let subtitle = !selected
        ? (candidates.length ? 'Подключите отдел, чтобы начать' : 'Отделов для обзвона пока нет')
        : tez || enabled
            ? counts
            : 'Режим выключен: операторы вкладку «Обзвон» не видят';
    if (reportsOnly) subtitle = `Отчёты по вашей группе: ${reportGroupNames}`;

    return (
        <div style={{ fontFamily: APPLE_FONT }} className="space-y-4 pb-24">
            {/* Шапка */}
            <div className={`${iosCard} p-4`}>
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                        <div className="grid h-10 w-10 place-items-center rounded-2xl bg-blue-50 text-blue-600">
                            <FaIcon className="fas fa-list-check" style={{ width: 16, height: 16 }} />
                        </div>
                        <div>
                            <div className="flex items-center gap-2 text-[15px] font-semibold text-slate-900">
                                {sectionTitle}
                                {!reportsOnly && (
                                    <IosHint
                                        text={tez
                                            ? 'В статусе «Активный» телефон оператора звонит сам, подряд: после итога и паузы — следующему водителю пачки, а кому пора перезвонить — первым. В статусе «Исход» оператор звонит сам из своих списков. Номер водителя телефону не передаётся: звонок инициирует сервер через Binotel. Цель — водитель вышел на линию (заказ в TEZ APP).'
                                            : 'Оператор видит в iCORE Phone только ФИО и кнопку «Позвонить». Звонок инициирует сервер через Binotel API, номер водителя телефону не передаётся. Исход каждого звонка подтверждает Binotel, а не телефон.'}
                                    />
                                )}
                            </div>
                            <div className="text-[12.5px] text-slate-500">{subtitle}</div>
                        </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        {selected && !reportsOnly && (tez ? (
                            <IosBadge tone={autoDial ? 'green' : 'slate'} title="Автообзвон в статусе «Активный»">
                                {autoDial ? 'Автообзвон включён' : 'Автообзвон выключен'}
                            </IosBadge>
                        ) : (
                            <IosBadge tone={enabled ? 'green' : 'slate'}>{enabled ? 'Включён' : 'Выключен'}</IosBadge>
                        ))}
                        {selected && dept.settings?.period && (
                            <IosBadge tone="blue" title="Какой месяц базы сейчас обзванивается">
                                <FaIcon className="fas fa-calendar" style={{ width: 10, height: 10 }} />
                                {monthLabel(dept.settings.period)}
                            </IosBadge>
                        )}
                        {departments && departments.length > 1 && (
                            <CustomSelect
                                className="w-56"
                                variant="ios"
                                value={departmentId}
                                onChange={(v) => setDepartmentId(String(v))}
                                ariaLabel="Отдел"
                                options={departments.map((d) => ({
                                    value: String(d.department_id),
                                    label: `${d.department_name || `Отдел ${d.department_id}`}${d.enabled && !tez ? ' · включён' : ''}`,
                                }))}
                            />
                        )}
                        {departments && departments.length === 1 && !sameSectionTitle(selected?.department_name, sectionTitle) && (
                            <span className="rounded-xl bg-slate-100 px-3 py-2 text-[13px] font-medium text-slate-700">{selected?.department_name}</span>
                        )}
                        {/* Детализация звонков отдела за период — с любой вкладки. Только «Удаленный КЦ»:
                            её просили для него, а успешка у автообзвона Тез — другая (вышел на линию). */}
                        {selected && !tez && (
                            <DialListCallsExport
                                apiBaseUrl={apiBaseUrl}
                                authHeaders={authHeaders}
                                departmentId={selected.department_id}
                                showToast={showToast}
                            />
                        )}
                    </div>
                </div>

                {/* Подключение отдела: только админу и только пока есть кандидаты
                    (отделы на Binotel, которых в разделе ещё нет). */}
                {canEdit && candidates.length > 0 && (
                    <div className="mt-4 flex flex-wrap items-center gap-2 rounded-xl bg-slate-50 px-3.5 py-2.5">
                        <span className="text-[12.5px] text-slate-600">{tez ? 'Подключить отдел к автообзвону:' : 'Подключить отдел к обзвону:'}</span>
                        <CustomSelect
                            className="w-60"
                            variant="ios"
                            value={enrollPick}
                            onChange={(v) => setEnrollPick(String(v))}
                            ariaLabel="Отдел для подключения"
                            placeholder="Выберите отдел"
                            options={candidates.map((d) => ({ value: String(d.department_id), label: d.department_name || `Отдел ${d.department_id}` }))}
                        />
                        <button type="button" onClick={enroll} disabled={enrolling || !enrollPick} className={`${iosBtnSecondary} py-1.5`}>
                            <FaIcon className={enrolling ? 'fas fa-spinner fa-spin' : 'fas fa-plus'} />
                            Подключить
                        </button>
                    </div>
                )}

            </div>

            {error && <div className="rounded-xl bg-rose-50 px-4 py-3 text-[13px] text-rose-700">{error}</div>}

            {departments && departments.length === 0 && !error && (
                <div className={`${iosCard} p-8 text-center`}>
                    <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                        <FaIcon className="fas fa-building" style={{ width: 18, height: 18 }} />
                    </div>
                    <div className="mt-3 text-[14px] font-semibold text-slate-800">Нет подключённых отделов</div>
                    <div className="mt-1 text-[12.5px] text-slate-500">
                        {candidates.length
                            ? 'Выберите отдел в шапке и нажмите «Подключить».'
                            : tez
                                ? 'Отдел подключает администратор: телефония отдела должна быть на Binotel («Настройки SIP»).'
                                : 'Заведите отдел удалённого колл-центра и переключите его телефонию на Binotel в «Настройках SIP» — тогда его можно будет подключить здесь.'}
                    </div>
                </div>
            )}

            {/* Глава «Регионов»: в разделе у неё одна «Детализация» (кнопка в шапке) — карточка
                говорит, что в ней и где она, чтобы экран под шапкой не выглядел недогруженным. */}
            {selected && reportsOnly && (
                <div className={`${iosCard} p-8 text-center`}>
                    <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-blue-50 text-blue-600">
                        <FaIcon className="fas fa-file-excel" style={{ width: 18, height: 18 }} />
                    </div>
                    <div className="mt-3 text-[14px] font-semibold text-slate-800">Детализация звонков вашей группы</div>
                    <div className="mx-auto mt-1 max-w-md text-[12.5px] text-slate-500">
                        Нажмите «Детализация» и выберите дни — скачается Excel со звонками сотрудников группы на линиях
                        удалённого КЦ и итогами по каждому.
                    </div>
                </div>
            )}

            {selected && !reportsOnly && (
                <>
                    {/* Пять вкладок на телефоне в ширину не помещаются: без прокрутки
                        «Журнал» и «Настройки» уезжали за край экрана, и попасть в
                        них было нечем. На широком экране min-w-max ничего не
                        меняет — полоса и так растянута на всю ширину. */}
                    <div className="-mx-1 overflow-x-auto px-1" style={{ scrollbarWidth: 'none' }}>
                        <IosSegmented
                            value={tab}
                            onChange={setTab}
                            options={TABS
                                .filter((t) => !tez || t.value !== 'informer')
                                .map((t) => (t.value === 'leads' ? { ...t, count: dept.summary?.pool_available } : t))}
                            stretch
                            size="lg"
                            className="min-w-max"
                            ariaLabel="Разделы обзвона"
                        />
                    </div>

                    {tab === 'operators' && (
                        <section className="space-y-1.5">
                            <div className="flex items-center justify-between gap-3 px-1">
                                <div className={iosGroupLabel}>По операторам</div>
                                <div className="flex items-center gap-2">
                                    <IosDatePicker
                                        value={date}
                                        max={todayIso()}
                                        onChange={(iso) => setDate(iso || todayIso())}
                                        placeholder="Дата"
                                    />
                                    <button type="button" onClick={loadOverview} disabled={loading} className={`${iosBtnSecondary} py-1.5`}>
                                        <FaIcon className={loading ? 'fas fa-spinner fa-spin' : 'fas fa-rotate'} />
                                    </button>
                                </div>
                            </div>
                            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                {loading && rows.length === 0 ? (
                                    <div className="px-4 py-6 text-center text-[13px] text-slate-500"><FaIcon className="fas fa-spinner fa-spin" /> Загрузка…</div>
                                ) : sortedRows.length === 0 ? (
                                    <div className="px-4 py-8 text-center">
                                        <div className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                                            <FaIcon className="fas fa-phone-slash" style={{ width: 18, height: 18 }} />
                                        </div>
                                        <div className="mt-3 text-[14px] font-semibold text-slate-800">
                                            {isToday ? 'Сегодня звонков из списка ещё не было' : 'В этот день звонков из списка не было'}
                                        </div>
                                        <div className="mt-1 text-[12.5px] text-slate-500">
                                            {(tez || enabled) && (dept.summary?.pool_available ?? 0) === 0
                                                ? 'База пуста — загрузите список водителей во вкладке «База водителей».'
                                                : tez
                                                    ? 'Включите операторов во вкладке «Настройки» — в статусе «Активный» их телефоны звонят сами.'
                                                    : !enabled
                                                        ? 'Включите режим во вкладке «Настройки» и перезапустите телефоны операторов.'
                                                        : 'Операторы получают порции сами, нажимая «Ещё» в телефоне.'}
                                        </div>
                                    </div>
                                ) : sortedRows.map((r) => (tez
                                    ? <TezOperatorRow key={r.operator_id} row={r} />
                                    : <OperatorRow key={r.operator_id} row={r} showDepartment={false} />
                                ))}
                            </div>
                        </section>
                    )}

                    {tab === 'analytics' && tez && (
                        <TezAutodialAnalytics
                            apiBaseUrl={apiBaseUrl}
                            authHeaders={authHeaders}
                            departmentId={selected.department_id}
                            periods={dept.summary?.periods || []}
                            activePeriod={dept.summary?.active_period || dept.settings?.period || ''}
                            period={period}
                            onPeriodChange={setPeriod}
                            onOpenJournal={openJournal}
                        />
                    )}

                    {tab === 'analytics' && !tez && (
                        <DialListAnalytics
                            apiBaseUrl={apiBaseUrl}
                            authHeaders={authHeaders}
                            departmentId={selected.department_id}
                            periods={dept.summary?.periods || []}
                            activePeriod={dept.summary?.active_period || dept.settings?.period || ''}
                            period={period}
                            onPeriodChange={setPeriod}
                            onOpenJournal={openJournal}
                            canEdit={canEdit}
                            showToast={showToast}
                        />
                    )}

                    {tab === 'lines' && (
                        /* key: открытый выбор «Кому» — состояние панели, и при смене отдела
                           оно не должно уехать в запрос к линиям другого отдела. */
                        <DialListLinesPanel
                            key={selected.department_id}
                            apiBaseUrl={apiBaseUrl}
                            authHeaders={authHeaders}
                            departmentId={selected.department_id}
                            departmentName={selected.department_name}
                            canEdit={canEdit}
                            showToast={showToast}
                        />
                    )}

                    {tab === 'leads' && (
                        <DialListLeadsPanel
                            apiBaseUrl={apiBaseUrl}
                            authHeaders={authHeaders}
                            departmentId={selected.department_id}
                            summary={dept.summary}
                            period={period === 'all' ? '' : period}
                            onPeriodChange={setPeriod}
                            onChanged={dept.reload}
                            canEdit={canEdit}
                            showToast={showToast}
                            profile={sectionProfile}
                        />
                    )}

                    {tab === 'journal' && (
                        <DialListJournal
                            apiBaseUrl={apiBaseUrl}
                            authHeaders={authHeaders}
                            departmentId={selected.department_id}
                            batches={dept.summary?.batches || []}
                            periods={dept.summary?.periods || []}
                            activePeriod={dept.summary?.active_period || dept.settings?.period || ''}
                            period={period}
                            onPeriodChange={setPeriod}
                            onChanged={dept.reload}
                            canEdit={canEdit}
                            showToast={showToast}
                            preset={journalPreset}
                            profile={sectionProfile}
                        />
                    )}

                    {tab === 'informer' && !tez && (
                        <DialListInformerPanel
                            key={selected.department_id}
                            apiBaseUrl={apiBaseUrl}
                            authHeaders={authHeaders}
                            departmentId={selected.department_id}
                            canEdit={canEdit}
                            showToast={showToast}
                        />
                    )}

                    {tab === 'script' && (
                        <DialListScriptPanel
                            apiBaseUrl={apiBaseUrl}
                            authHeaders={authHeaders}
                            departmentId={selected.department_id}
                            canEdit={canEdit}
                            showToast={showToast}
                        />
                    )}

                    {tab === 'settings' && (
                        dept.error || dept.mismatch
                            ? (
                                <div className={`${iosCard} p-4 text-[13px] text-rose-600`}>
                                    {dept.error || `Этот отдел — в разделе «${tez ? SECTION_TITLE : TEZ_SECTION_TITLE}»: его настройки там.`}
                                </div>
                            )
                            : (
                                <div className="space-y-4">
                                    <DialListSettingsPanel
                                        apiBaseUrl={apiBaseUrl}
                                        authHeaders={authHeaders}
                                        departmentId={selected.department_id}
                                        settings={dept.settings}
                                        periods={dept.summary?.periods || []}
                                        onSaved={() => { dept.reload(); loadDepartments(selected.department_id); }}
                                        canEdit={canEdit}
                                        showToast={showToast}
                                        profile={sectionProfile}
                                    />
                                    <DialListOutcomesPanel
                                        apiBaseUrl={apiBaseUrl}
                                        authHeaders={authHeaders}
                                        departmentId={selected.department_id}
                                        canEdit={canEdit}
                                        showToast={showToast}
                                        dueKinds={tez}
                                    />
                                    {tez && (
                                        <TezOffersPanel
                                            apiBaseUrl={apiBaseUrl}
                                            authHeaders={authHeaders}
                                            departmentId={selected.department_id}
                                            canEdit={canEdit}
                                            showToast={showToast}
                                        />
                                    )}
                                    {tez && (
                                        <TezWhatsAppPanel
                                            apiBaseUrl={apiBaseUrl}
                                            authHeaders={authHeaders}
                                            departmentId={selected.department_id}
                                            canEdit={canEdit}
                                            showToast={showToast}
                                        />
                                    )}
                                    <DialListOperatorsPanel
                                        apiBaseUrl={apiBaseUrl}
                                        authHeaders={authHeaders}
                                        departmentId={selected.department_id}
                                        departmentEnabled={enabled}
                                        canEdit={canEdit}
                                        showToast={showToast}
                                        profile={sectionProfile}
                                    />
                                    {canEdit && candidatesAdmin && (
                                        <div className="flex justify-end">
                                            <button type="button" onClick={unenroll} disabled={enrolling} className={`${iosBtnSecondary} text-rose-600`}>
                                                <FaIcon className="fas fa-link-slash" />
                                                Отключить отдел от обзвона
                                            </button>
                                        </div>
                                    )}
                                </div>
                            )
                    )}
                </>
            )}
        </div>
    );
};

export default DialListView;
