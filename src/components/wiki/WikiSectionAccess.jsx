import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    Building2, ChevronDown, Globe, Loader2, Plus, Trash2, TriangleAlert,
} from 'lucide-react';
import {
    iosCard, iosGroupLabel, iosInput, iosBtnPrimary, iosBtnSecondary, iosBtnGhost,
    IosBadge, IosModal, IosToggle,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { sectionAncestors } from './sectionPicker';
import useStableCallback from './useStableCallback';

/* Доступ к разделу — прямо из строки раздела во вкладке «Структура».
 *
 * Отдельной вкладки «Доступы» больше нет, и это не перестановка ради вида.
 * Там раздел выбирался селектом из плоского списка, где у СЗоВ и у ОП свои
 * одноимённые «Руководитель», «Супервайзер», «Оператор»: правило регулярно
 * уезжало в чужую ветку, а замечали это, только когда раздел переставали
 * видеть нужные люди. Здесь раздел выбран тем, что человек нажал на его строку.
 *
 * ── Две части формы ───────────────────────────────────────────────────────
 * Наверху — должности: «Оператор», «Супервайзер», «Руководитель группы».
 * Это ровно то, что настраивают каждый день, и оно не требует знать слово
 * «субъект». Внизу, свёрнутое, — точечные правила (человек, группа,
 * направление, роль вики): нужны редко, но без них модель прав беднее.
 *
 * ── Почему у должности два разных смысла ──────────────────────────────────
 * Если раздел лежит внутри ветки отдела (у неё заполнен «Отдел ветки»), правило
 * пишется на ОТДЕЛ с порогом должности: «СЗоВ, не ниже супервайзера». Голая
 * роль 'sv' пробила бы границу отдела — супервайзер продаж увидел бы ОТП.
 * Если ветки отдела над разделом нет, писать не на что, кроме самой роли, —
 * и тогда правило действует по всей компании, о чём форма прямо предупреждает.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

/* Должности, на которые выдают доступ. Порог «не ниже»: правило действует, если
   уровень человека не меньше указанного (шкала ROLE_LEVELS, wiki/access.py).
   Отсюда бесплатно выходит «видит своё и всё, что ниже себя».

   У самой нижней строки порог NULL, а не 10. Разница не косметическая:
   role_level_of отдаёт 0 для роли, которой нет в шкале, и порог 10 отрезал бы
   таких людей от раздела, открытого «всему отделу». */
const ROLE_ROWS = [
    { key: 'operator', label: 'Оператор', hint: 'и все, кто выше — весь отдел',
      level: null, role: 'operator' },
    { key: 'trainer', label: 'Тренер', hint: 'и выше', level: 20, role: 'trainer' },
    { key: 'sv', label: 'Супервайзер', hint: 'и выше', level: 30, role: 'sv' },
    { key: 'head', label: 'Руководитель группы', hint: 'и выше', level: 40, role: 'admin' },
];

const PERMISSIONS = [
    { key: 'can_read', label: 'Читать', note: 'видит раздел и его статьи' },
    { key: 'can_create', label: 'Создавать', note: 'заводит новые статьи' },
    { key: 'can_edit', label: 'Править', note: 'меняет текст существующих' },
    { key: 'can_publish', label: 'Публиковать', note: 'выпускает черновик' },
    { key: 'can_approve', label: 'Согласовывать', note: 'подтверждает чужую правку' },
    { key: 'can_delete', label: 'Удалять', note: 'убирает статьи', danger: true },
];

/* Готовые наборы прав — то, что выбирают в 9 случаях из 10. Тонкая настройка
   остаётся рядом, но начинать с шести тумблеров незачем. */
const PRESETS = [
    { key: 'none', label: 'Нет', permissions: {} },
    { key: 'read', label: 'Чтение', permissions: { can_read: true } },
    { key: 'write', label: 'Правка',
      permissions: { can_read: true, can_create: true, can_edit: true } },
    { key: 'full', label: 'Полный',
      permissions: { can_read: true, can_create: true, can_edit: true,
                     can_publish: true, can_approve: true, can_delete: true } },
];

const NO_PERMISSIONS = Object.fromEntries(PERMISSIONS.map((p) => [p.key, false]));

const permissionsOf = (rule) => Object.fromEntries(
    PERMISSIONS.map((p) => [p.key, !!rule?.[p.key]]));

const anyPermission = (permissions) => PERMISSIONS.some((p) => permissions[p.key]);

/** Какой пресет описывает набор прав целиком. null — набор собран вручную. */
const presetOf = (permissions) => PRESETS.find(
    (preset) => PERMISSIONS.every(
        (p) => !!preset.permissions[p.key] === !!permissions[p.key]),
)?.key || null;

/** Право на запись без чтения бессмысленно — сервер всё равно включит чтение. */
const withRead = (permissions) => (
    anyPermission(permissions) ? { ...permissions, can_read: true } : permissions);

const SUBJECT_KINDS = [
    { value: 'user', label: 'Конкретный человек' },
    { value: 'group', label: 'Группа' },
    { value: 'direction', label: 'Направление' },
    { value: 'department', label: 'Отдел' },
    // Адресуется НАЗНАЧЕНИЮ, а не человеку: правило переезжает вместе со сменой
    // главы, и переставлять его руками не нужно.
    { value: 'department_head', label: 'Глава отдела' },
    { value: 'wiki_role', label: 'Роль в вики' },
    { value: 'otp_role', label: 'Роль в системе' },
];

const SUBJECT_KIND_LABEL = Object.fromEntries(SUBJECT_KINDS.map((k) => [k.value, k.label]));

const ROLE_LEVEL_LABEL = {
    10: 'от оператора', 20: 'от тренера', 30: 'от СВ',
    40: 'от руководителя', 50: 'супер-админ',
};

/** Ветка отдела над разделом: он сам или ближайший предок с отделом. */
export function branchDepartment(sections, sectionId) {
    const path = sectionAncestors(sections, sectionId);
    for (let i = path.length - 1; i >= 0; i -= 1) {
        if (path[i].department_id) {
            return {
                id: path[i].department_id,
                name: path[i].department_name || `отдел #${path[i].department_id}`,
                sectionName: path[i].name,
                own: path[i].id === Number(sectionId),
            };
        }
    }
    return null;
}

/** Правило матрицы должностей? Такие рисуются строками, остальные — списком ниже. */
const matrixKeyOf = (rule, department) => {
    if (department) {
        if (rule.subject_type !== 'department'
            || Number(rule.subject_id) !== Number(department.id)) return null;
        return ROLE_ROWS.find(
            (r) => (r.level ?? null) === (rule.min_role_level ?? null))?.key || null;
    }
    if (rule.subject_type !== 'otp_role' || rule.min_role_level != null) return null;
    return ROLE_ROWS.find((r) => r.role === rule.subject_role)?.key || null;
};

// ── Строка должности ────────────────────────────────────────────────────────
const RoleRow = ({ row, draft, expanded, onToggleExpand, onChange }) => {
    const preset = presetOf(draft.permissions);
    const granted = PERMISSIONS.filter((p) => draft.permissions[p.key]);

    return (
        <div className={expanded ? 'bg-slate-50/70' : ''}>
            <button
                type="button"
                onClick={onToggleExpand}
                className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-50"
            >
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                        <span className="text-[14px] font-medium text-slate-900">{row.label}</span>
                        <span className="text-[11.5px] text-slate-400">{row.hint}</span>
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1">
                        {granted.length === 0 ? (
                            <span className="text-[12px] text-slate-400">доступа нет</span>
                        ) : granted.map((p) => (
                            <IosBadge key={p.key} tone={p.danger ? 'amber' : 'blue'}>{p.label}</IosBadge>
                        ))}
                        {granted.length > 0 && draft.grant_subsections && (
                            <IosBadge tone="slate">+ подразделы</IosBadge>
                        )}
                    </div>
                </div>
                <ChevronDown
                    size={16}
                    className={`shrink-0 text-slate-300 transition-transform ${expanded ? 'rotate-180' : ''}`}
                />
            </button>

            {expanded && (
                <div className="space-y-3 px-4 pb-4">
                    <div className="flex gap-1 rounded-xl bg-slate-200/70 p-1">
                        {PRESETS.map((p) => (
                            <button
                                key={p.key}
                                type="button"
                                onClick={() => onChange({
                                    ...draft,
                                    permissions: { ...NO_PERMISSIONS, ...p.permissions },
                                })}
                                className={`flex-1 whitespace-nowrap rounded-lg px-2 py-1.5 text-[12.5px] font-medium transition ${
                                    preset === p.key
                                        ? 'bg-white text-slate-900 shadow-sm'
                                        : 'text-slate-500 hover:text-slate-700'
                                }`}
                            >
                                {p.label}
                            </button>
                        ))}
                    </div>

                    <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                        {PERMISSIONS.map((p) => (
                            <div key={p.key} className="flex items-center justify-between gap-3 px-3.5 py-2.5">
                                <div className="min-w-0">
                                    <div className={`text-[13.5px] ${p.danger ? 'text-amber-700' : 'text-slate-800'}`}>
                                        {p.label}
                                    </div>
                                    <div className="text-[11.5px] text-slate-400">{p.note}</div>
                                </div>
                                <IosToggle
                                    checked={!!draft.permissions[p.key]}
                                    // Снять чтение, оставив правку, нельзя: сервер всё
                                    // равно вернёт его обратно, и тумблер соврал бы.
                                    disabled={p.key === 'can_read' && PERMISSIONS.some(
                                        (x) => x.key !== 'can_read' && draft.permissions[x.key])}
                                    onChange={(v) => onChange({
                                        ...draft,
                                        permissions: withRead({ ...draft.permissions, [p.key]: v }),
                                    })}
                                />
                            </div>
                        ))}
                    </div>

                    <div className={`${iosCard} flex items-start justify-between gap-3 p-3.5`}>
                        <div className="min-w-0">
                            <div className="text-[13.5px] font-medium text-slate-900">
                                Вместе с подразделами
                            </div>
                            <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-500">
                                Те же права во всех вложенных разделах, включая созданные позже.
                            </p>
                        </div>
                        <IosToggle
                            checked={!!draft.grant_subsections}
                            onChange={(v) => onChange({ ...draft, grant_subsections: v })}
                        />
                    </div>
                </div>
            )}
        </div>
    );
};

export default function WikiSectionAccess({ base, headers, showToast, section, sections,
                                            onClose, reload }) {
    const toast = useStableCallback(showToast);

    const [rules, setRules] = useState([]);
    const [catalog, setCatalog] = useState({});
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [matrix, setMatrix] = useState({});
    const [expanded, setExpanded] = useState(null);
    const [showExtra, setShowExtra] = useState(false);
    const [draft, setDraft] = useState(null);

    const sectionId = section?.id;
    const department = useMemo(
        () => (sectionId ? branchDepartment(sections, sectionId) : null),
        [sections, sectionId]);

    const path = useMemo(
        () => sectionAncestors(sections, sectionId).map((s) => s.name).join(' › '),
        [sections, sectionId]);

    const loadRules = useCallback(() => {
        if (!sectionId) return;
        setLoading(true);
        axios.get(`${base}/access/section-rules`, { headers, params: { section_id: sectionId } })
            .then((r) => setRules(r.data?.items || []))
            .catch((e) => toast(errText(e, 'Не удалось загрузить правила'), 'error'))
            .finally(() => setLoading(false));
    }, [base, headers, sectionId, toast]);

    useEffect(() => { loadRules(); }, [loadRules]);

    useEffect(() => {
        axios.get(`${base}/access/subjects`, { headers })
            .then((r) => setCatalog(r.data || {}))
            .catch(() => setCatalog({}));
    }, [base, headers]);

    /* Матрица должностей — производная от загруженных правил, но состояние
       собственное: человек правит несколько строк и сохраняет разом. */
    useEffect(() => {
        const next = {};
        ROLE_ROWS.forEach((row) => {
            const rule = rules.find((r) => matrixKeyOf(r, department) === row.key);
            next[row.key] = {
                permissions: rule ? permissionsOf(rule) : { ...NO_PERMISSIONS },
                // Новое правило по умолчанию НЕ уходит вглубь: глубокое правило
                // на родителе сливает соседние ветки отделов в одну.
                grant_subsections: rule ? !!rule.grant_subsections : false,
                ruleId: rule?.id || null,
            };
        });
        setMatrix(next);
    }, [rules, department]);

    const extraRules = useMemo(
        () => rules.filter((r) => !matrixKeyOf(r, department)), [rules, department]);

    const dirty = useMemo(() => ROLE_ROWS.some((row) => {
        const state = matrix[row.key];
        if (!state) return false;
        const rule = rules.find((r) => r.id === state.ruleId);
        const before = rule ? permissionsOf(rule) : { ...NO_PERMISSIONS };
        const deepBefore = rule ? !!rule.grant_subsections : false;
        return PERMISSIONS.some((p) => before[p.key] !== state.permissions[p.key])
            || (anyPermission(state.permissions) && deepBefore !== state.grant_subsections);
    }), [matrix, rules]);

    const ruleBody = (row) => (department
        ? { subject_type: 'department', subject_id: department.id,
            min_role_level: row.level }
        : { subject_type: 'otp_role', subject_role: row.role, min_role_level: null });

    const saveMatrix = () => {
        const jobs = [];
        ROLE_ROWS.forEach((row) => {
            const state = matrix[row.key];
            if (!state) return;
            const rule = rules.find((r) => r.id === state.ruleId);
            const before = rule ? permissionsOf(rule) : { ...NO_PERMISSIONS };
            const changed = PERMISSIONS.some((p) => before[p.key] !== state.permissions[p.key])
                || (rule && !!rule.grant_subsections !== state.grant_subsections);
            if (!changed) return;

            if (!anyPermission(state.permissions)) {
                // Права сняты все до одного — правила больше нет, а не «есть,
                // но пустое»: пустое правило всё равно открывало бы раздел.
                if (rule) jobs.push(axios.delete(`${base}/access/section-rules/${rule.id}`, { headers }));
                return;
            }
            jobs.push(axios.post(`${base}/access/section-rules`, {
                section_id: sectionId,
                ...ruleBody(row),
                ...state.permissions,
                grant_subsections: state.grant_subsections,
            }, { headers }));
        });

        if (!jobs.length) { onClose?.(); return; }
        setBusy(true);
        Promise.all(jobs)
            // Закрываем только на успехе: после отказа лист обязан остаться
            // открытым с несохранёнными переключателями, иначе правка молча
            // пропадёт вместе с окном.
            .then(() => { toast('Доступ сохранён', 'success'); reload?.(); onClose?.(); })
            .catch((e) => { toast(errText(e, 'Не удалось сохранить доступ'), 'error'); loadRules(); })
            .finally(() => setBusy(false));
    };

    const saveExtra = () => {
        setBusy(true);
        axios.post(`${base}/access/section-rules`, {
            section_id: sectionId,
            subject_type: draft.subject_type,
            subject_id: draft.subject_type === 'otp_role' ? null : Number(draft.subject_id) || null,
            subject_role: draft.subject_type === 'otp_role' ? draft.subject_role : null,
            min_role_level: draft.min_role_level === '' ? null : Number(draft.min_role_level),
            ...draft.permissions,
            grant_subsections: draft.grant_subsections,
        }, { headers })
            .then(() => { toast('Правило сохранено', 'success'); setDraft(null); loadRules(); reload?.(); })
            .catch((e) => toast(errText(e, 'Не удалось сохранить правило'), 'error'))
            .finally(() => setBusy(false));
    };

    const removeRule = (rule) => {
        setBusy(true);
        axios.delete(`${base}/access/section-rules/${rule.id}`, { headers })
            .then(() => { toast('Правило удалено', 'success'); loadRules(); reload?.(); })
            .catch((e) => toast(errText(e, 'Не удалось удалить'), 'error'))
            .finally(() => setBusy(false));
    };

    const subjectOptions = useMemo(() => {
        const kind = draft?.subject_type;
        if (!kind || kind === 'otp_role' || kind === 'user') return [];
        const source = kind === 'department_head' ? 'department' : kind;
        return (catalog[source] || []).map((item) => ({
            value: String(item.id), label: item.name,
        }));
    }, [catalog, draft?.subject_type]);

    const isPublic = section?.visibility_scope === 'public';

    return (
        <IosModal
            open={!!section}
            onClose={onClose}
            title="Доступ к разделу"
            subtitle={path || section?.name}
            maxWidth="max-w-xl"
            footer={(
                <>
                    <button type="button" className={iosBtnSecondary} onClick={onClose}>
                        {dirty ? 'Отмена' : 'Закрыть'}
                    </button>
                    <button type="button" className={iosBtnPrimary} disabled={busy || !dirty}
                            onClick={saveMatrix}>
                        {busy && <Loader2 size={14} className="animate-spin" />} Сохранить
                    </button>
                </>
            )}
        >
            <div className="space-y-5">
                {/* Чей это раздел: ветка отдела задаётся в форме раздела, здесь
                    она только показана — иначе непонятно, почему строки должностей
                    означают «в СЗоВ», а не «во всей компании». */}
                <div className={`${iosCard} flex flex-wrap items-center gap-2 px-4 py-3`}>
                    {department ? (
                        <>
                            <Building2 size={16} className="shrink-0 text-indigo-500" />
                            <div className="min-w-0 flex-1">
                                <div className="text-[13.5px] font-medium text-slate-900">
                                    Отдел ветки: {department.name}
                                </div>
                                <div className="mt-0.5 text-[11.5px] text-slate-500">
                                    {department.own
                                        ? 'Задан у этого раздела'
                                        : `Унаследован от «${department.sectionName}»`}
                                    {' · '}должности ниже работают внутри этого отдела
                                </div>
                            </div>
                        </>
                    ) : (
                        <>
                            <TriangleAlert size={16} className="shrink-0 text-amber-500" />
                            <div className="min-w-0 flex-1">
                                <div className="text-[13.5px] font-medium text-slate-900">
                                    Отдел ветки не задан
                                </div>
                                <div className="mt-0.5 text-[11.5px] leading-relaxed text-slate-500">
                                    Права ниже получат сотрудники <b>всей компании</b> с такой
                                    должностью. Чтобы удержать границу отдела, укажите отдел
                                    у этого раздела или у ветки над ним — в форме «Изменить».
                                </div>
                            </div>
                        </>
                    )}
                </div>

                {isPublic && (
                    <div className="flex items-start gap-2 rounded-2xl bg-emerald-50 px-4 py-3 text-[12.5px] leading-relaxed text-emerald-800">
                        <Globe size={15} className="mt-0.5 shrink-0" />
                        <span>
                            Раздел публичный: читают его все сотрудники независимо от правил.
                            Настройки ниже нужны только для прав на запись.
                        </span>
                    </div>
                )}

                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>Кому открыт раздел</div>
                    <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                        {loading ? (
                            <div className="flex items-center justify-center gap-2 py-10 text-slate-400">
                                <Loader2 size={16} className="animate-spin" />
                                <span className="text-[13px]">Загружаем…</span>
                            </div>
                        ) : ROLE_ROWS.map((row) => (
                            matrix[row.key] ? (
                                <RoleRow
                                    key={row.key}
                                    row={row}
                                    draft={matrix[row.key]}
                                    expanded={expanded === row.key}
                                    onToggleExpand={() => setExpanded(expanded === row.key ? null : row.key)}
                                    onChange={(next) => setMatrix({ ...matrix, [row.key]: next })}
                                />
                            ) : null
                        ))}
                    </div>
                    <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">
                        Порог «не ниже»: доступ, выданный оператору, автоматически есть и у
                        супервайзера, и у руководителя — руководитель видит всё, что видит
                        подчинённый.
                    </p>
                </section>

                {/* Точечные правила свёрнуты: нужны редко, а места занимают столько
                    же, сколько главная часть формы. */}
                <section className="space-y-1.5">
                    <button
                        type="button"
                        onClick={() => setShowExtra((v) => !v)}
                        className="flex w-full items-center gap-2 px-1"
                    >
                        <span className={iosGroupLabel}>Точечные правила</span>
                        {extraRules.length > 0 && (
                            <IosBadge tone="blue">{extraRules.length}</IosBadge>
                        )}
                        <ChevronDown
                            size={14}
                            className={`ml-auto text-slate-400 transition-transform ${showExtra ? 'rotate-180' : ''}`}
                        />
                    </button>

                    {showExtra && (
                        <>
                            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                {extraRules.length === 0 && (
                                    <div className="px-4 py-6 text-center text-[12.5px] leading-relaxed text-slate-400">
                                        Правил на отдельного человека, группу или направление нет.
                                    </div>
                                )}
                                {extraRules.map((rule) => (
                                    <div key={rule.id} className="flex flex-wrap items-center gap-2 px-4 py-3">
                                        <div className="min-w-0 flex-1">
                                            <div className="flex flex-wrap items-center gap-1.5">
                                                <IosBadge tone="slate">
                                                    {SUBJECT_KIND_LABEL[rule.subject_type] || rule.subject_type}
                                                    {rule.min_role_level
                                                        ? ` · ${ROLE_LEVEL_LABEL[rule.min_role_level] || rule.min_role_level}`
                                                        : ''}
                                                </IosBadge>
                                                <span className="truncate text-[13.5px] font-medium text-slate-900">
                                                    {rule.subject_label || rule.subject_role || `#${rule.subject_id}`}
                                                </span>
                                            </div>
                                            <div className="mt-1 flex flex-wrap gap-1">
                                                {PERMISSIONS.filter((p) => rule[p.key]).map((p) => (
                                                    <IosBadge key={p.key} tone={p.danger ? 'amber' : 'blue'}>
                                                        {p.label}
                                                    </IosBadge>
                                                ))}
                                                {rule.grant_subsections && (
                                                    <IosBadge tone="slate">+ подразделы</IosBadge>
                                                )}
                                            </div>
                                        </div>
                                        <button
                                            type="button"
                                            disabled={busy}
                                            onClick={() => removeRule(rule)}
                                            className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-rose-50 hover:text-rose-600 disabled:opacity-40"
                                            aria-label="Удалить правило"
                                        >
                                            <Trash2 size={14} />
                                        </button>
                                    </div>
                                ))}
                            </div>
                            <button
                                type="button"
                                className={iosBtnGhost}
                                onClick={() => setDraft({
                                    subject_type: 'user', subject_id: '', subject_role: 'operator',
                                    min_role_level: '', grant_subsections: false,
                                    permissions: { ...NO_PERMISSIONS, can_read: true },
                                })}
                            >
                                <Plus size={14} /> Добавить правило
                            </button>
                        </>
                    )}
                </section>
            </div>

            {/* ── Точечное правило ── */}
            <IosModal
                open={!!draft}
                onClose={() => setDraft(null)}
                title="Точечное правило"
                subtitle={section?.name}
                footer={(
                    <>
                        <button type="button" className={iosBtnSecondary} onClick={() => setDraft(null)}>
                            Отмена
                        </button>
                        <button
                            type="button"
                            className={iosBtnPrimary}
                            disabled={busy || (draft?.subject_type !== 'otp_role' && !draft?.subject_id)}
                            onClick={saveExtra}
                        >
                            {busy && <Loader2 size={14} className="animate-spin" />} Сохранить
                        </button>
                    </>
                )}
            >
                {draft && (
                    <div className="space-y-3.5">
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Кому</label>
                            <CustomSelect
                                variant="ios"
                                value={draft.subject_type}
                                onChange={(v) => setDraft({ ...draft, subject_type: v, subject_id: '' })}
                                options={SUBJECT_KINDS}
                                ariaLabel="Тип субъекта"
                            />
                        </div>

                        {draft.subject_type === 'user' && (
                            <div>
                                <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">
                                    ID сотрудника
                                </label>
                                <input
                                    className={iosInput}
                                    inputMode="numeric"
                                    value={draft.subject_id}
                                    onChange={(e) => setDraft({ ...draft, subject_id: e.target.value })}
                                    placeholder="Например: 42"
                                />
                            </div>
                        )}

                        {draft.subject_type === 'otp_role' && (
                            <div>
                                <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Роль</label>
                                <CustomSelect
                                    variant="ios"
                                    value={draft.subject_role}
                                    onChange={(v) => setDraft({ ...draft, subject_role: v })}
                                    options={(catalog.otp_role || []).map((r) => ({
                                        value: String(r.id), label: r.name,
                                    }))}
                                    ariaLabel="Роль в системе"
                                />
                                <p className="mt-1 px-1 text-[11.5px] leading-relaxed text-amber-700">
                                    Роль не знает границ отдела: правило подействует во всей компании.
                                </p>
                            </div>
                        )}

                        {!['otp_role', 'user'].includes(draft.subject_type) && (
                            <div>
                                <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">
                                    {SUBJECT_KIND_LABEL[draft.subject_type]}
                                </label>
                                <CustomSelect
                                    variant="ios"
                                    value={draft.subject_id}
                                    onChange={(v) => setDraft({ ...draft, subject_id: v })}
                                    options={subjectOptions}
                                    searchable
                                    ariaLabel="Субъект правила"
                                />
                            </div>
                        )}

                        <div className="space-y-1.5">
                            <div className={iosGroupLabel}>Что разрешено</div>
                            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                {PERMISSIONS.map((p) => (
                                    <div key={p.key} className="flex items-center justify-between gap-3 px-3.5 py-2.5">
                                        <span className={`text-[13.5px] ${p.danger ? 'text-amber-700' : 'text-slate-800'}`}>
                                            {p.label}
                                        </span>
                                        <IosToggle
                                            checked={!!draft.permissions[p.key]}
                                            disabled={p.key === 'can_read' && PERMISSIONS.some(
                                                (x) => x.key !== 'can_read' && draft.permissions[x.key])}
                                            onChange={(v) => setDraft({
                                                ...draft,
                                                permissions: withRead({ ...draft.permissions, [p.key]: v }),
                                            })}
                                        />
                                    </div>
                                ))}
                            </div>
                        </div>

                        <div className={`${iosCard} flex items-start justify-between gap-3 p-3.5`}>
                            <div className="min-w-0">
                                <div className="text-[13.5px] font-medium text-slate-900">
                                    Вместе с подразделами
                                </div>
                                <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-500">
                                    Те же права во всех вложенных разделах, включая созданные позже.
                                </p>
                            </div>
                            <IosToggle
                                checked={!!draft.grant_subsections}
                                onChange={(v) => setDraft({ ...draft, grant_subsections: v })}
                            />
                        </div>
                    </div>
                )}
            </IosModal>
        </IosModal>
    );
}
