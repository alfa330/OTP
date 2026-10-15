import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import {
    iosCard, iosInput, iosGroupLabel, iosBtnPrimary, iosBtnSecondary, iosBtnGhost, IosBadge, IosHint, IosToggle,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import useStableCallback from '../wiki/useStableCallback';
import { TEZ_STATUS_STYLE } from '../monitoring/tezWallboardShared';
import {
    OFFERS_MAX, OFFER_DESCRIPTION_MAX, OFFER_TITLE_MAX, WA_PARAM_MAX, WA_SUBSTITUTIONS, WA_TEMPLATES_MAX,
    liveStatusMeta, offersSnapshot, previewTemplate, serializeOffers, serializeWaAllowed, templateBlockReason,
    templateVariableCount, validateOffers, validateWaAllowed, waSnapshot,
} from './tezAutodial';

/*
 * Панели раздела «Автообзвон Тез» (15.10.2026), которых у «Удаленного КЦ» нет:
 * строка оператора с живым статусом, «Акции и офферы», «WhatsApp». Остальное раздел
 * берёт у удалённого КЦ с параметром profile='tez' (DialListView и соседи).
 *
 * Правила владельца: пояснения — под «i», на экране числа и названия; цвет — только
 * со смыслом; showToast нестабилен — только через useStableCallback, в зависимости
 * загрузки его не класть (иначе раздел перезагружается на каждый тост).
 */

const readError = async (resp) => {
    const data = await resp.json().catch(() => ({}));
    return data?.error || `HTTP ${resp.status}`;
};

const initials = (name) => String(name || '')
    .split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('') || '•';

const fmtTalk = (sec) => {
    const s = Math.max(0, Number(sec) || 0);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h) return `${h} ч ${m} мин`;
    if (m) return `${m} мин`;
    return s ? `${s} с` : '—';
};

const pct = (part, total) => (total ? Math.round((part / total) * 100) : null);

/* ─── оператор за день ─────────────────────────────────────────────────── */

/** Живой статус оператора чипом — цветами табло Тез (TEZ_STATUS_STYLE): «Активный»
    там и здесь горит одинаково. Ключа нет (сервер его ещё не присылает) — чипа нет. */
export const LiveStatusChip = ({ status }) => {
    const meta = liveStatusMeta(status);
    if (!meta) return null;
    const style = TEZ_STATUS_STYLE[meta.wallboardKey] || TEZ_STATUS_STYLE.other;
    return (
        <span
            className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${style.chip}`}
            title={meta.hint ? `${meta.label}: ${meta.hint}` : meta.label}
        >
            {meta.label}
        </span>
    );
};

/**
 * Строка оператора во вкладке «Операторы»: статус сейчас, «обработано из выданных»,
 * звонки (из них автообзвоном), дозвоны, согласия, перезвоны, вышли на линию и
 * просрочки постобработки за день (GET /api/dial_list/overview, контракт §5).
 * Цвет: зелёный — цель (вышли на линию), янтарный — просрочки постобработки,
 * красный — отказы АТС; прочие числа нейтральны.
 */
export const TezOperatorRow = ({ row }) => {
    const progress = row.issued ? Math.min(100, Math.round((row.done / row.issued) * 100)) : 0;
    const answered = pct(row.answered, row.attempts);
    const hasAuto = row.auto_attempts !== undefined && row.auto_attempts !== null;
    const auto = Number(row.auto_attempts) || 0;
    const manual = Math.max(0, (Number(row.attempts) || 0) - auto);
    const callsTitle = hasAuto ? `Звонков за день: автообзвоном ${auto}, вручную ${manual}` : 'Звонков за день';
    // На узком экране бейджи не помещаются — те же числа одной строкой.
    const compact = [
        `${row.attempts} звон.${hasAuto && auto ? ` (авто ${auto})` : ''}`,
        `дозв. ${row.answered}`,
        row.agreements ? `согл. ${row.agreements}` : '',
        row.successes ? `вышли ${row.successes}` : '',
        row.wrapup_expired ? `просрочки ${row.wrapup_expired}` : '',
    ].filter(Boolean).join(' · ');
    return (
        <div className="flex items-center gap-3 px-4 py-3">
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-slate-100 text-[12px] font-semibold text-slate-600">
                {initials(row.operator_name)}
            </div>
            <div className="min-w-0 flex-1">
                <div className="flex min-w-0 items-center gap-2">
                    <div className="truncate text-[13.5px] font-medium text-slate-800">{row.operator_name || `#${row.operator_id}`}</div>
                    <LiveStatusChip status={row.live_status} />
                </div>
                <div className="mt-1 flex items-center gap-2">
                    <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                        <div className="h-full rounded-full bg-blue-500 transition-all" style={{ width: `${progress}%` }} />
                    </div>
                    <div className="w-20 shrink-0 text-right text-[11.5px] tabular-nums text-slate-500">
                        {row.done} из {row.issued}
                    </div>
                </div>
                <div className="mt-1 truncate text-[11.5px] tabular-nums text-slate-500 sm:hidden">{compact}</div>
            </div>
            <div className="hidden max-w-[60%] shrink-0 flex-wrap items-center justify-end gap-1.5 sm:flex">
                <IosBadge tone="blue" title={callsTitle}>
                    {row.attempts} звон.{hasAuto ? ` · авто ${auto}` : ''}
                </IosBadge>
                <IosBadge tone={answered == null ? 'slate' : answered >= 50 ? 'green' : 'amber'} title="Дозвонились">
                    {row.answered}{answered != null ? ` · ${answered}%` : ''}
                </IosBadge>
                <IosBadge tone="slate" title="Разговоры">{fmtTalk(row.talk_sec)}</IosBadge>
                {row.agreements > 0 && (
                    <IosBadge tone="slate" title="Согласились выйти на линию: итог с датой выхода">согласия {row.agreements}</IosBadge>
                )}
                {row.callbacks > 0 && (
                    <IosBadge tone="slate" title="Назначили перезвон с датой">перезвоны {row.callbacks}</IosBadge>
                )}
                {row.successes > 0 && (
                    <IosBadge tone="green" title="Вышли на линию в этот день после разговора с оператором — успешки">
                        вышли {row.successes}
                    </IosBadge>
                )}
                {row.wrapup_expired > 0 && (
                    <IosBadge tone="amber" title="Не успел с итогом за время постобработки — телефон ставил «Офлайн»">
                        просрочки {row.wrapup_expired}
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

/* ─── акции и офферы ───────────────────────────────────────────────────── */

let tempSeq = 0;
const tempId = () => `new-${Date.now()}-${tempSeq++}`;

/**
 * «Акции и офферы» отдела (GET/PUT …/offers, контракт §5). Оператор в окне итога
 * отмечает, какие офферы предложил водителю, — в «Аналитике» видно, какие из них
 * приводят к согласию и к выходу на линию. Список уходит целиком, как у итогов:
 * отсутствующие сервер выключает; офферы не удаляются, а выключаются — на них
 * ссылается история звонков.
 */
export const TezOffersPanel = ({ apiBaseUrl, authHeaders, departmentId, canEdit = true, showToast }) => {
    const toast = useStableCallback((msg, kind = 'success') => showToast?.(msg, kind));
    const [items, setItems] = useState(null);
    const [saved, setSaved] = useState(null);
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);
    const seq = useRef(0);

    const accept = (data) => {
        const list = (Array.isArray(data?.items) ? data.items : Array.isArray(data?.offers) ? data.offers : [])
            .map((o) => ({ ...o, description: o.description || '' }));
        setItems(list);
        setSaved(offersSnapshot(list));
    };

    const load = useCallback(async () => {
        const my = ++seq.current;
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/offers`, { credentials: 'include', headers: authHeaders() });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json();
            if (my !== seq.current) return;
            accept(data);
            setError('');
        } catch (e) {
            if (my === seq.current) setError(e.message || 'Не удалось загрузить офферы');
        }
    }, [apiBaseUrl, authHeaders, departmentId]);

    useEffect(() => { load(); }, [load]);

    const snapshot = useMemo(() => (items ? offersSnapshot(items) : null), [items]);
    const dirty = items && snapshot !== saved;

    const patch = (idx, changes) => setItems((list) => list.map((o, i) => (i === idx ? { ...o, ...changes } : o)));
    const move = (idx, dir) => setItems((list) => {
        const next = [...list];
        const j = idx + dir;
        if (j < 0 || j >= next.length) return list;
        [next[idx], next[j]] = [next[j], next[idx]];
        return next;
    });
    const add = () => setItems((list) => [...(list || []), { id: tempId(), title: '', description: '', is_active: true }]);
    const remove = (idx) => setItems((list) => list.filter((_, i) => i !== idx));

    const save = async () => {
        if (!items) return;
        const payload = serializeOffers(items);
        const problem = validateOffers(payload);
        if (problem) { toast(problem, 'error'); return; }
        setSaving(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/offers`, {
                method: 'PUT', credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ items: payload }),
            });
            if (!resp.ok) throw new Error(await readError(resp));
            const data = await resp.json().catch(() => ({}));
            // Новые офферы получают id только на сервере — без них следующее
            // сохранение вставило бы их второй раз. Ответа со списком нет — перечитываем.
            if (Array.isArray(data?.items) || Array.isArray(data?.offers)) accept(data); else await load();
            toast('Офферы сохранены. Телефоны подхватят список при следующем обновлении', 'success');
        } catch (e) {
            toast(e.message || 'Не удалось сохранить офферы', 'error');
        } finally {
            setSaving(false);
        }
    };

    const activeCount = (items || []).filter((o) => o.is_active !== false).length;

    return (
        <section className="space-y-1.5">
            <div className="flex items-center justify-between gap-3 px-1">
                <div className="flex items-center gap-1.5">
                    <span className={iosGroupLabel}>Акции и офферы</span>
                    <IosHint text="В окне итога оператор отмечает, какие офферы предложил водителю; в «Аналитике» видно, какие из них приводят к согласию и к выходу на линию. Выключенный оффер пропадает из телефона, но остаётся в истории звонков." />
                </div>
                {items && <span className="text-[11.5px] text-slate-500">Включено: {activeCount}</span>}
            </div>
            <div className={`${iosCard} overflow-hidden`}>
                {error ? (
                    <div className="px-4 py-4 text-[13px] text-rose-600">
                        {error}
                        <button type="button" onClick={load} className="ml-2 font-medium text-blue-600">Повторить</button>
                    </div>
                ) : items === null ? (
                    <div className="px-4 py-4 text-[13px] text-slate-500"><FaIcon className="fas fa-spinner fa-spin" /> Загрузка…</div>
                ) : (
                    <>
                        <ul className="divide-y divide-slate-100">
                            {items.map((o, idx) => {
                                const active = o.is_active !== false;
                                return (
                                    <li key={o.id} className={`space-y-2 px-3 py-2.5 ${active ? '' : 'bg-slate-50/60'}`}>
                                        <div className="flex flex-wrap items-center gap-3 sm:flex-nowrap">
                                            <input
                                                value={o.title}
                                                onChange={(e) => patch(idx, { title: e.target.value })}
                                                disabled={!canEdit}
                                                maxLength={OFFER_TITLE_MAX}
                                                placeholder="Например: Бонус 5000 ₸ за первые 10 заказов"
                                                aria-label="Название оффера"
                                                className={`${iosInput} min-w-[160px] flex-1 py-2 text-[13.5px] ${active ? '' : 'text-slate-400 line-through'}`}
                                            />
                                            <label className="flex shrink-0 items-center gap-2 text-[12px] text-slate-600">
                                                <IosToggle checked={active} disabled={!canEdit} onChange={(v) => patch(idx, { is_active: v })} />
                                                {active ? 'включён' : 'выключен'}
                                            </label>
                                            {canEdit && (
                                                <div className="flex shrink-0 items-center gap-0.5">
                                                    <button type="button" onClick={() => move(idx, -1)} disabled={idx === 0} className={`${iosBtnGhost} px-2 py-1.5`} aria-label="Выше">
                                                        <FaIcon className="fas fa-chevron-up" style={{ width: 11, height: 11 }} />
                                                    </button>
                                                    <button type="button" onClick={() => move(idx, 1)} disabled={idx === items.length - 1} className={`${iosBtnGhost} px-2 py-1.5`} aria-label="Ниже">
                                                        <FaIcon className="fas fa-chevron-down" style={{ width: 11, height: 11 }} />
                                                    </button>
                                                    {String(o.id).startsWith('new-') && (
                                                        <button type="button" onClick={() => remove(idx)} className={`${iosBtnGhost} px-2 py-1.5 text-rose-500`} aria-label="Убрать">
                                                            <FaIcon className="fas fa-xmark" style={{ width: 11, height: 11 }} />
                                                        </button>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                        <textarea
                                            value={o.description}
                                            onChange={(e) => patch(idx, { description: e.target.value })}
                                            disabled={!canEdit}
                                            rows={2}
                                            maxLength={OFFER_DESCRIPTION_MAX}
                                            placeholder="Условия для оператора — необязательно"
                                            aria-label="Описание оффера"
                                            className={`${iosInput} resize-y py-2 text-[13px]`}
                                        />
                                    </li>
                                );
                            })}
                            {items.length === 0 && (
                                <li className="px-4 py-5 text-center text-[13px] text-slate-500">Офферов пока нет</li>
                            )}
                        </ul>
                        {canEdit && (
                            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 px-3 py-2.5">
                                <button type="button" onClick={add} disabled={items.length >= OFFERS_MAX} className={iosBtnSecondary}>
                                    <FaIcon className="fas fa-plus" style={{ width: 11, height: 11 }} /> Добавить оффер
                                </button>
                                <div className="flex items-center gap-2">
                                    {dirty && <span className="text-[12px] text-amber-600">Есть несохранённые изменения</span>}
                                    <button type="button" onClick={save} disabled={!dirty || saving} className={iosBtnPrimary}>
                                        <FaIcon className={saving ? 'fas fa-spinner fa-spin' : 'fas fa-check'} />
                                        Сохранить
                                    </button>
                                </div>
                            </div>
                        )}
                    </>
                )}
            </div>
        </section>
    );
};

/* ─── WhatsApp ──────────────────────────────────────────────────────────── */

const LICENSE_OFF = '';

/* Параметры одного разрешённого шаблона: поле на каждую переменную {{n}} и текст,
   каким его увидит водитель (подстановки — образцом). */
const TemplateParams = ({ template, count, onChange, canEdit }) => {
    const params = Array.from({ length: count }, (_, i) => template.params?.[i] ?? '');
    const set = (i, value) => onChange(params.map((p, j) => (j === i ? value : p)));
    return (
        <div className="space-y-2">
            {count > 0 && (
                <div className="grid gap-2 sm:grid-cols-2">
                    {params.map((value, i) => (
                        // eslint-disable-next-line react/no-array-index-key
                        <label key={i} className="flex min-w-0 items-center gap-2">
                            <span className="w-10 shrink-0 font-mono text-[12px] text-slate-500">{`{{${i + 1}}}`}</span>
                            <input
                                value={value}
                                onChange={(e) => set(i, e.target.value)}
                                disabled={!canEdit}
                                maxLength={WA_PARAM_MAX}
                                placeholder={i === 0 ? '{first_name}' : 'текст или подстановка'}
                                className={`${iosInput} min-w-0 py-1.5 text-[13px]`}
                            />
                        </label>
                    ))}
                </div>
            )}
            <div className="whitespace-pre-wrap break-words rounded-xl bg-emerald-50/60 px-3 py-2 text-[12.5px] leading-snug text-slate-700 ring-1 ring-emerald-100">
                {previewTemplate(template.text, params)}
            </div>
        </div>
    );
};

/**
 * «WhatsApp» отдела (GET/PUT …/whatsapp, контракт §3.7 и §5): лицензия ChatApp и
 * шаблоны, которые оператор может отправить водителю из карточки звонка. Список
 * шаблонов — из ChatApp (сервер держит его 10 минут); разрешить можно только
 * утверждённые текстовые. У каждого разрешённого — параметры {{1}}, {{2}}… с
 * подстановками {name}, {first_name}, {operator}. Номер водителя не уходит ни в
 * телефон, ни на сайт: сообщение отправляет сервер.
 */
export const TezWhatsAppPanel = ({ apiBaseUrl, authHeaders, departmentId, canEdit = true, showToast }) => {
    const toast = useStableCallback((msg, kind = 'success') => showToast?.(msg, kind));
    const [data, setData] = useState(null);
    const [licenseId, setLicenseId] = useState(LICENSE_OFF);
    const [allowed, setAllowed] = useState([]);
    const [saved, setSaved] = useState(null);
    const [loadError, setLoadError] = useState('');
    const [saving, setSaving] = useState(false);
    const seq = useRef(0);

    const accept = (body) => {
        const lic = body?.license_id === null || body?.license_id === undefined ? LICENSE_OFF : String(body.license_id);
        const list = (Array.isArray(body?.allowed) ? body.allowed : []).map((t) => ({
            id: String(t.id ?? ''), title: t.title || '', text: t.text || '', params: Array.isArray(t.params) ? t.params : [],
        }));
        setData(body || {});
        setLicenseId(lic);
        setAllowed(list);
        setSaved(waSnapshot(lic === LICENSE_OFF ? null : Number(lic), list));
    };

    const load = useCallback(async () => {
        const my = ++seq.current;
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/whatsapp`, { credentials: 'include', headers: authHeaders() });
            if (!resp.ok) throw new Error(await readError(resp));
            const body = await resp.json();
            if (my !== seq.current) return;
            accept(body);
            setLoadError('');
        } catch (e) {
            if (my === seq.current) setLoadError(e.message || 'Не удалось загрузить настройки WhatsApp');
        }
    }, [apiBaseUrl, authHeaders, departmentId]);

    useEffect(() => { load(); }, [load]);

    const templates = useMemo(() => (Array.isArray(data?.templates) ? data.templates : []), [data]);
    const byId = useMemo(() => Object.fromEntries(templates.map((t) => [String(t.id), t])), [templates]);
    const paramsCount = (t) => {
        const known = byId[t.id]?.params_count;
        return Number.isInteger(known) ? known : templateVariableCount(t.text);
    };
    const licenseValue = licenseId === LICENSE_OFF ? null : Number(licenseId);
    const dirty = data && waSnapshot(licenseValue, allowed) !== saved;
    // Список шаблонов — той лицензии, что сохранена на сервере: у каждой лицензии ChatApp свои.
    // Выбрали другую — её шаблоны появятся после сохранения, а шаблоны прежней не годятся.
    const savedLicense = data?.license_id === null || data?.license_id === undefined ? LICENSE_OFF : String(data.license_id);
    const licenseSwitched = licenseId !== LICENSE_OFF && licenseId !== savedLicense;
    const changeLicense = (value) => {
        const next = String(value ?? LICENSE_OFF);
        setLicenseId(next);
        // «Выключен» шаблоны не трогает (сервер хранит их выключенными); своя лицензия — как было;
        // другая — без шаблонов: прежние ей не принадлежат.
        if (next === savedLicense) {
            setAllowed((Array.isArray(data?.allowed) ? data.allowed : []).map((t) => ({
                id: String(t.id ?? ''), title: t.title || '', text: t.text || '', params: Array.isArray(t.params) ? t.params : [],
            })));
        } else if (next !== LICENSE_OFF) {
            setAllowed([]);
        }
    };

    const licenses = Array.isArray(data?.licenses) ? data.licenses : [];
    const licenseOptions = [
        { value: LICENSE_OFF, label: 'Выключен' },
        ...licenses.map((l) => ({ value: String(l.id), label: l.name ? `${l.name} · ${l.id}` : `Лицензия ${l.id}` })),
        // Лицензия из базы, которой нет в ответе ChatApp (ChatApp не ответил): не теряем её.
        ...(licenseId !== LICENSE_OFF && !licenses.some((l) => String(l.id) === licenseId)
            ? [{ value: licenseId, label: `Лицензия ${licenseId}` }] : []),
    ];

    const isAllowed = (id) => allowed.some((t) => t.id === String(id));
    const toggle = (t, on) => setAllowed((list) => {
        const id = String(t.id);
        if (!on) return list.filter((x) => x.id !== id);
        if (list.some((x) => x.id === id) || list.length >= WA_TEMPLATES_MAX) return list;
        return [...list, { id, title: t.title || '', text: t.text || '', params: Array.from({ length: paramsCount(t) }, () => '') }];
    });
    const setParams = (id, params) => setAllowed((list) => list.map((x) => (x.id === id ? { ...x, params } : x)));

    const save = async () => {
        // Параметров ровно столько, сколько переменных у шаблона: лишние ChatApp не примет.
        const trimmed = allowed.map((t) => ({ ...t, params: Array.from({ length: paramsCount(t) }, (_, i) => t.params?.[i] ?? '') }));
        const problem = validateWaAllowed(serializeWaAllowed(trimmed), Object.fromEntries(trimmed.map((t) => [t.id, paramsCount(t)])));
        if (problem) { toast(problem, 'error'); return; }
        setSaving(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/dial_list/departments/${departmentId}/whatsapp`, {
                method: 'PUT', credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ license_id: licenseValue, allowed: serializeWaAllowed(trimmed) }),
            });
            if (!resp.ok) throw new Error(await readError(resp));
            const body = await resp.json().catch(() => ({}));
            // Ответ со списком шаблонов — как GET; без него перечитываем.
            if (Array.isArray(body?.allowed) && Array.isArray(body?.templates)) accept(body); else await load();
            toast(licenseValue == null ? 'WhatsApp выключен' : 'WhatsApp сохранён. Телефоны подхватят шаблоны при следующем обновлении', 'success');
        } catch (e) {
            toast(e.message || 'Не удалось сохранить WhatsApp', 'error');
        } finally {
            setSaving(false);
        }
    };

    // Разрешённые, которых нет в ответе ChatApp (шаблон удалили или ChatApp не ответил).
    const orphans = allowed.filter((t) => !byId[t.id]);
    const substitutions = WA_SUBSTITUTIONS.map((s) => `${s.key} — ${s.label}`).join('; ');

    return (
        <section className="space-y-1.5">
            <div className="flex items-center justify-between gap-3 px-1">
                <div className="flex items-center gap-1.5">
                    <span className={iosGroupLabel}>WhatsApp</span>
                    <IosHint text="Оператор отправляет водителю сообщение по разрешённому шаблону кнопкой «WhatsApp» в карточке звонка. Отправляет сервер через ChatApp; номер водителя не уходит ни в телефон, ни на сайт. Одному водителю — не чаще раза в сутки." />
                </div>
                {data && <span className="text-[11.5px] text-slate-500">Разрешено: {allowed.length} из {WA_TEMPLATES_MAX}</span>}
            </div>
            <div className={`${iosCard} overflow-hidden`}>
                {loadError ? (
                    <div className="px-4 py-4 text-[13px] text-rose-600">
                        {loadError}
                        <button type="button" onClick={load} className="ml-2 font-medium text-blue-600">Повторить</button>
                    </div>
                ) : !data ? (
                    <div className="px-4 py-4 text-[13px] text-slate-500"><FaIcon className="fas fa-spinner fa-spin" /> Загрузка…</div>
                ) : (
                    <>
                        <div className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                            <div className="flex items-center gap-1.5 text-[13.5px] text-slate-800">
                                Лицензия ChatApp
                                <IosHint text="Через какую лицензию (номер WhatsApp Тез) уходят сообщения. «Выключен» — кнопки «WhatsApp» у операторов нет." />
                            </div>
                            <CustomSelect
                                className="w-full sm:w-72"
                                variant="ios"
                                value={licenseId}
                                onChange={changeLicense}
                                options={licenseOptions}
                                disabled={!canEdit}
                                ariaLabel="Лицензия ChatApp"
                            />
                        </div>
                        {data.error && (
                            <div className="border-t border-slate-100 bg-amber-50 px-4 py-2.5 text-[12.5px] text-amber-800">
                                ChatApp: {data.error}
                            </div>
                        )}
                        <div className="flex items-center gap-1.5 border-t border-slate-100 px-4 pb-1 pt-3">
                            <span className="text-[12px] font-medium text-slate-500">Шаблоны</span>
                            <IosHint text={`Разрешить можно только утверждённые текстовые шаблоны. В параметрах {{1}}, {{2}}… можно подставить: ${substitutions}. Под параметрами — текст, каким его получит водитель (подстановки — образцом).`} />
                        </div>
                        <ul className="divide-y divide-slate-100">
                            {licenseSwitched && (
                                <li className="px-4 py-5 text-center text-[13px] text-slate-500">
                                    Сохраните лицензию — после этого здесь появятся её шаблоны
                                </li>
                            )}
                            {!licenseSwitched && templates.length === 0 && !orphans.length && (
                                <li className="px-4 py-5 text-center text-[13px] text-slate-500">
                                    {data.error ? 'Список шаблонов ChatApp сейчас недоступен'
                                        : savedLicense === LICENSE_OFF ? 'Выберите лицензию и сохраните — появятся её шаблоны'
                                            : 'В ChatApp нет шаблонов'}
                                </li>
                            )}
                            {!licenseSwitched && templates.map((t) => {
                                const id = String(t.id);
                                const blocked = templateBlockReason(t);
                                const on = isAllowed(id);
                                const entry = allowed.find((x) => x.id === id);
                                return (
                                    <li key={id} className={`space-y-2 px-4 py-3 ${blocked && !on ? 'opacity-60' : ''}`}>
                                        <div className="flex items-start gap-3">
                                            <div className="min-w-0 flex-1">
                                                <div className="flex flex-wrap items-center gap-1.5">
                                                    <span className="truncate text-[13.5px] font-medium text-slate-800">{t.title || id}</span>
                                                    {t.language && <span className="text-[11.5px] uppercase text-slate-400">{t.language}</span>}
                                                    {blocked && <IosBadge tone="slate">{blocked}</IosBadge>}
                                                </div>
                                                {!on && <div className="mt-0.5 line-clamp-2 text-[12.5px] text-slate-500">{t.text}</div>}
                                            </div>
                                            <IosToggle
                                                checked={on}
                                                disabled={!canEdit || (!on && (Boolean(blocked) || allowed.length >= WA_TEMPLATES_MAX))}
                                                onChange={(v) => toggle(t, v)}
                                            />
                                        </div>
                                        {on && entry && (
                                            <TemplateParams template={entry} count={paramsCount(entry)} canEdit={canEdit} onChange={(params) => setParams(id, params)} />
                                        )}
                                    </li>
                                );
                            })}
                            {orphans.map((t) => (
                                <li key={t.id} className="space-y-2 px-4 py-3">
                                    <div className="flex items-start gap-3">
                                        <div className="min-w-0 flex-1">
                                            <div className="flex flex-wrap items-center gap-1.5">
                                                <span className="truncate text-[13.5px] font-medium text-slate-800">{t.title || t.id}</span>
                                                <IosBadge tone="amber">нет в ответе ChatApp</IosBadge>
                                            </div>
                                        </div>
                                        {canEdit && (
                                            <button type="button" onClick={() => toggle(t, false)} className={`${iosBtnGhost} px-2 py-1 text-rose-600`}>
                                                Убрать
                                            </button>
                                        )}
                                    </div>
                                    <TemplateParams template={t} count={paramsCount(t)} canEdit={canEdit} onChange={(params) => setParams(t.id, params)} />
                                </li>
                            ))}
                        </ul>
                        {canEdit && (
                            <div className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 px-3 py-2.5">
                                {dirty && <span className="text-[12px] text-amber-600">Есть несохранённые изменения</span>}
                                <button type="button" onClick={save} disabled={!dirty || saving} className={iosBtnPrimary}>
                                    <FaIcon className={saving ? 'fas fa-spinner fa-spin' : 'fas fa-check'} />
                                    Сохранить
                                </button>
                            </div>
                        )}
                    </>
                )}
            </div>
        </section>
    );
};
