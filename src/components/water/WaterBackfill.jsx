import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Check, Loader2, X } from 'lucide-react';
import { IosModal, IosSegmented, IosToggle } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { IosTimePicker } from '../ui/TimePicker';
import InfoHint, { InfoHintLines } from '../common/InfoHint';
import { extractAccountId, fmtDateTime } from '../parcels/parcelMeta';
import { DriverCard } from './WaterIssue';
import {
    BACKFILL_FIELD_ORDER, BACKFILL_KINDS, BACKFILL_NEEDS_MOMENT, backfillBasis, backfillHeadline, backfillKindHints,
    backfillMoment, backfillProblems, blocksWord, kindLabel, localNow, officeIssuers,
} from './waterMeta';
import { ModalSubtitle, ModalTitle, T, WaterStepper, wBtnPrimary, wIcon, wInput, wLabel, wSegments } from './waterUi';

/*
 * «Выдача задним числом» — блок воды, который выдали у стойки, но не отметили
 * в системе. Владелец 23.11.2026: «супер-админам добавлять такие блоки задним
 * числом, в случае если офисник забыл нажать кнопку, а водитель уже выполняет
 * заказы» — сперва приветственный, в тот же день и за активность («да, сделай»).
 *
 * Поток тот же, что у «Выдачи»: вставил ссылку или ID — водитель подтянулся из
 * CRM сам. Дальше — вид выдачи, где, когда и кто выдал. Решение («можно» или
 * причины) видно под видом выдачи и пересчитывается, когда выбрано время: блоку
 * за активность от него считаются дни с прошлой выдачи и поездки. Сервер
 * проверяет всё заново при записи.
 *
 * Кнопка нажимается всегда: чего не хватает, краснеет у самого поля, и окно
 * подводит к первому такому полю. До первой попытки красного нет.
 */

const EMPTY = { link: '', kind: null, officeId: null, day: '', time: '', issuerId: null, comment: '' };

const FIELD = 'block space-y-1.5 2xl:space-y-2';
const ISSUE = `px-1 text-[12px] text-rose-600 ${T.note}`;
const INVALID = 'ring-2 ring-rose-300';

/* Дата и время — того же роста и вида, что поле ввода раздела (wInput). */
const DATE_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 text-[14px] '
    + 'tabular-nums text-slate-900 transition hover:bg-slate-200/70 focus:outline-none focus:ring-2 '
    + 'focus:ring-blue-500/70 [&>span]:flex-1 [&>span]:text-left 2xl:px-4 2xl:py-3 2xl:text-[16px]';
const TIME_INPUT = 'w-full rounded-xl border-0 bg-slate-100 px-6 py-2.5 text-center text-[14px] tabular-nums '
    + 'text-slate-900 placeholder-slate-400 transition hover:bg-slate-200/70 focus:bg-white focus:outline-none '
    + 'focus:ring-2 focus:ring-blue-500/70 2xl:py-3 2xl:text-[16px]';

/* Отказ сервера — к тому полю, о котором он: время и вода тогда — под
   «Когда выдали», выдавший — под «Кто выдал», право — под видом выдачи. */
const SERVER_FIELD = {
    WATER_KIND_REQUIRED: 'kind',
    WATER_KIND_INVALID: 'kind',
    WATER_MOMENT_REQUIRED: 'time',
    WATER_MOMENT_FUTURE: 'time',
    WATER_MOMENT_BEFORE_OFFICE: 'time',
    WATER_STOCK_SHORT: 'time',
    WATER_ISSUER_REQUIRED: 'issuer',
    WATER_ISSUER_UNKNOWN: 'issuer',
    WATER_OFFICE_REQUIRED: 'office',
    WATER_OFFICE_NOT_FOUND: 'office',
    WATER_NOT_ELIGIBLE: 'kind',
    WATER_WELCOME_TAKEN: 'kind',
    WATER_BLOCKS_TOO_MANY: 'kind',
    WATER_VERDICT_CHANGED: 'kind',
};

/* «i» держится за последним словом подписи и переносится вместе с ним. */
const Label = ({ text, hint, lines }) => {
    if (!hint && !lines) return <span className={wLabel}>{text}</span>;
    const words = text.split(' ');
    const last = words.pop();
    return (
        <span className={wLabel}>
            {words.length > 0 && `${words.join(' ')} `}
            <span className="whitespace-nowrap">
                {last}
                {lines ? (
                    <InfoHint side="left" className="ml-1.5 align-middle"><InfoHintLines lines={lines} /></InfoHint>
                ) : (
                    <InfoHint side="left" text={hint} className="ml-1.5 align-middle" />
                )}
            </span>
        </span>
    );
};

const WaterBackfill = ({ open, onClose, apiBaseUrl, headers, offices, settings, onDone, showToast }) => {
    const [form, setForm] = useState(EMPTY);
    const [result, setResult] = useState(null);
    const [checking, setChecking] = useState(false);
    const [checkError, setCheckError] = useState('');
    // null — грузится, 'error' — не загрузилось, иначе состояние «Доступа по офисам».
    const [access, setAccess] = useState(null);
    const [fkChecked, setFkChecked] = useState(false);
    const [blocks, setBlocks] = useState(1);
    const [tried, setTried] = useState(false);
    const [saving, setSaving] = useState(false);
    const [server, setServer] = useState(null);
    // Перепроверить водителя заново: сервер ответил «условия изменились».
    const [recheck, setRecheck] = useState(0);
    const ticket = useRef(0);
    const checkedAccount = useRef(null);
    const anchors = useRef({});
    const inputRef = useRef(null);

    /* Каждое открытие — с чистого листа и со свежим списком выдающих: доступ
       по офисам могли поменять, пока окно было закрыто. */
    useEffect(() => {
        if (!open) return undefined;
        ticket.current += 1;
        checkedAccount.current = null;
        setForm(EMPTY);
        setResult(null);
        setCheckError('');
        setChecking(false);
        setFkChecked(false);
        setBlocks(1);
        setTried(false);
        setServer(null);
        setAccess(null);
        let cancelled = false;
        axios.get(`${apiBaseUrl}/api/water/access`, { headers: headers() })
            .then((response) => { if (!cancelled) setAccess(response.data?.access || { offices: [], people: [] }); })
            .catch(() => { if (!cancelled) setAccess('error'); });
        return () => { cancelled = true; };
    }, [open, apiBaseUrl, headers]);

    const accountId = useMemo(() => extractAccountId(form.link), [form.link]);
    const moment = backfillMoment(form.day, form.time);

    /* Водитель подтягивается сам, как только строка разобралась в ID, и
       перепроверяется, когда выбрано время: от него зависят дни и поездки блока
       за активность. Пока идёт перепроверка, карточка остаётся на месте. */
    useEffect(() => {
        if (!open) return undefined;
        if (!accountId) { setResult(null); setCheckError(''); setChecking(false); return undefined; }
        const id = ticket.current + 1;
        ticket.current = id;
        const timer = setTimeout(async () => {
            setChecking(true);
            setCheckError('');
            try {
                const response = await axios.post(`${apiBaseUrl}/api/water/backfill/check`,
                    { link: accountId, issued_at: moment || undefined }, { headers: headers() });
                if (ticket.current !== id) return;
                // Отметка ФК — только тому водителю, для которого её ставили.
                if (checkedAccount.current !== response.data?.driver?.account_id) setFkChecked(false);
                checkedAccount.current = response.data?.driver?.account_id || null;
                setResult(response.data);
            } catch (requestError) {
                if (ticket.current !== id) return;
                setResult(null);
                setCheckError(requestError?.response?.data?.error || 'Не удалось найти водителя');
            } finally {
                if (ticket.current === id) setChecking(false);
            }
        }, 300);
        return () => clearTimeout(timer);
    }, [open, accountId, moment, recheck, apiBaseUrl, headers]);

    const set = (key, value) => {
        setServer(null);
        setForm((prev) => {
            const next = { ...prev, [key]: value };
            // Выдавший из другого офиса в новом не выдаёт — выбор сбрасывается.
            if (key === 'officeId' && access && access !== 'error'
                && !officeIssuers(access, value).some((person) => person.id === prev.issuerId)) {
                next.issuerId = null;
            }
            return next;
        });
    };

    const activeOffices = (Array.isArray(offices) ? offices : []).filter((office) => office.is_active);
    const issuers = access && access !== 'error' ? officeIssuers(access, form.officeId) : [];
    const driver = result?.driver || null;
    const current = Boolean(driver && accountId === driver.account_id);
    const verdict = current && form.kind ? result?.verdicts?.[form.kind] || null : null;
    const needsFk = Boolean(form.kind === 'welcome' && verdict?.allowed && verdict.fk === 'unknown');
    const blocksMax = verdict?.blocks_max || 0;
    const now = localNow();

    useEffect(() => { setBlocks(Math.max(1, blocksMax || 1)); }, [form.kind, blocksMax]);

    const problems = backfillProblems({
        // Без времени блоку за активность судить не по чему — это не отказ
        // водителю, а незаполненное время (его поле и покраснеет).
        driver: current ? (!verdict || verdict.needs_moment || Boolean(verdict.allowed)) : null,
        kind: form.kind,
        officeId: form.officeId,
        day: form.day,
        time: form.time,
        issuerId: form.issuerId,
        needsFk,
        fkChecked,
        now,
    });
    // «Внести нельзя» — плашкой с причинами под видом выдачи, а не строкой у поля водителя.
    if (problems.driver && current) {
        problems.kind = problems.kind || problems.driver;
        delete problems.driver;
    }
    const shownProblem = (key) => {
        if (server?.field === key) return server.text;
        return tried ? problems[key] : '';
    };

    const submit = async () => {
        if (saving) return;
        setTried(true);
        const first = BACKFILL_FIELD_ORDER.find((key) => problems[key]);
        if (first) {
            anchors.current[first]?.scrollIntoView({ block: 'center', behavior: 'smooth' });
            return;
        }
        setSaving(true);
        setServer(null);
        try {
            const response = await axios.post(`${apiBaseUrl}/api/water/backfill`, {
                kind: form.kind,
                link: driver.account_id,
                water_office_id: form.officeId,
                issued_at: moment,
                issued_by: form.issuerId,
                fk_confirmed: needsFk ? fkChecked : undefined,
                comment: form.comment.trim() || undefined,
                blocks: blocksMax > 1 ? blocks : undefined,
            }, { headers: headers() });
            const data = response.data || {};
            const recount = data.recount;
            showToast?.(`Внесено: ${kindLabel(data.issue?.kind).toLowerCase()} · ${blocksWord(data.issue?.blocks || 1)} · ${
                fmtDateTime(data.issue?.created_at)}${
                recount ? ` — остаток не изменился: блок уже учёл пересчёт ${fmtDateTime(recount.created_at)}` : ''}`,
            'success');
            onDone?.(data.issue, data.office);
            onClose();
        } catch (requestError) {
            const data = requestError?.response?.data || {};
            const field = SERVER_FIELD[data.code] || 'general';
            // Свежее решение сервера — на плашку; тогда и строка у поля не нужна.
            if (data.verdict && form.kind) {
                setResult((prev) => (prev ? { ...prev, verdicts: { ...prev.verdicts, [form.kind]: data.verdict } } : prev));
            }
            setServer(data.verdict ? null : { field, text: data.error || 'Не удалось внести выдачу' });
            if (data.code === 'WATER_VERDICT_CHANGED') setRecheck((value) => value + 1);
            anchors.current[field]?.scrollIntoView({ block: 'center', behavior: 'smooth' });
        } finally {
            setSaving(false);
        }
    };

    const clearDriver = () => {
        ticket.current += 1;
        set('link', '');
        setResult(null);
        setCheckError('');
        setChecking(false);
        inputRef.current?.focus();
    };

    const officeOptions = activeOffices.map((office) => ({
        value: office.id, label: `${office.city} · ${office.name}`, meta: blocksWord(office.stock),
    }));
    const issuerOptions = issuers.map((person) => ({ value: person.id, label: person.name }));
    const anchor = (key) => (node) => { anchors.current[key] = node; };
    const driverProblem = shownProblem('driver');
    const kindProblem = shownProblem('kind');
    const timeProblem = shownProblem('day') || shownProblem('time');
    // Офис выбран, а выдавать в нём некому — сказать, где это правится.
    const nobodyIssues = Boolean(form.officeId && access && access !== 'error' && !issuerOptions.length);
    // Без офиса список выдающих выключен и сам говорит «Сначала выберите офис»,
    // а красный уже стоит у офиса: строка под списком повторила бы то же.
    const issuerProblem = nobodyIssues && tried
        ? 'В этом офисе никто не выдаёт воду — доступ выдаётся в «Настройках» → «Доступ по офисам»'
        : (form.officeId ? shownProblem('issuer') : '');
    const headline = backfillHeadline(verdict);
    const basis = backfillBasis(verdict);
    // Плашка уже называет причины отказа — строка под ней их не повторяет.
    const kindLine = kindProblem && !(verdict && !verdict.needs_moment && !verdict.allowed && server?.field !== 'kind')
        ? kindProblem : '';

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={<ModalTitle>Выдача задним числом</ModalTitle>}
            subtitle={<ModalSubtitle>Блок воды, который выдали, но не отметили</ModalSubtitle>}
            maxWidth="max-w-lg 2xl:max-w-[640px]"
            footer={(
                <button type="button" className={`${wBtnPrimary} 2xl:!px-8`} onClick={submit} disabled={saving}>
                    {saving ? <Loader2 size={15} className={`animate-spin ${wIcon}`} /> : <Check size={15} className={wIcon} />}
                    Внести выдачу
                </button>
            )}
        >
            <div className="space-y-4 2xl:space-y-5">
                <div ref={anchor('driver')} className={FIELD}>
                    <span className={wLabel}>Водитель</span>
                    <div className="relative">
                        <input
                            ref={inputRef}
                            className={`${wInput} pr-10 2xl:pr-12 ${driverProblem ? INVALID : ''}`}
                            placeholder="Ссылка на водителя во Флите или его ID"
                            value={form.link}
                            onChange={(event) => set('link', event.target.value)}
                            aria-invalid={Boolean(driverProblem) || undefined}
                            aria-label="Водитель"
                        />
                        {(form.link || checking) && (
                            <button type="button" onClick={clearDriver} aria-label="Очистить"
                                    className="absolute right-2 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-200 hover:text-slate-600 2xl:right-2.5 2xl:h-8 2xl:w-8">
                                {checking ? <Loader2 size={15} className={`animate-spin ${wIcon}`} /> : <X size={15} className={wIcon} />}
                            </button>
                        )}
                    </div>
                    {form.link.trim() && !accountId && (
                        <p className={`px-1 text-[12px] text-slate-500 ${T.note}`}>
                            Не похоже на ссылку на водителя — нужен адрес карточки во Флите или ID из 32 символов
                        </p>
                    )}
                    {checkError && <p className={ISSUE}>{checkError}</p>}
                    {driverProblem && <p className={ISSUE}>{driverProblem}</p>}
                </div>

                {current && (
                    <div className="space-y-3 rounded-2xl bg-white p-4 ring-1 ring-slate-200/70 2xl:space-y-4 2xl:p-5">
                        <DriverCard driver={driver} programTariffs={settings?.tariffs || []} />
                        {result.history?.length > 0 && (
                            <div className="space-y-1">
                                <div className={wLabel}>Выдачи этому водителю</div>
                                <ul className={`divide-y divide-slate-100 text-[12.5px] ${T.sub}`}>
                                    {result.history.slice(0, 3).map((item) => (
                                        <li key={item.id} className="flex flex-wrap justify-between gap-x-3 py-1.5">
                                            <span className="text-slate-700">
                                                {fmtDateTime(item.created_at)} · {kindLabel(item.kind).toLowerCase()} · {blocksWord(item.blocks)}
                                            </span>
                                            <span className="text-slate-400">{item.office_name}</span>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}
                    </div>
                )}

                <div ref={anchor('kind')} className={FIELD}>
                    <Label text="Вид выдачи" lines={backfillKindHints(settings)} />
                    <div className={kindProblem && !form.kind ? `rounded-[10px] ${INVALID}` : ''}>
                        <IosSegmented value={form.kind} options={BACKFILL_KINDS} stretch ariaLabel="Вид выдачи"
                                      className={wSegments} onChange={(value) => set('kind', value)} />
                    </div>
                    {verdict && verdict.needs_moment && (
                        <div className={`rounded-xl bg-slate-50 px-3.5 py-3 text-[13px] text-slate-600 2xl:rounded-2xl 2xl:px-5 2xl:py-4 ${T.body}`}>
                            {BACKFILL_NEEDS_MOMENT}
                        </div>
                    )}
                    {headline && (
                        <div className={`rounded-xl px-3.5 py-3 2xl:rounded-2xl 2xl:px-5 2xl:py-4 ${verdict.allowed ? 'bg-emerald-50' : 'bg-slate-50'}`}>
                            <div className={`text-[14px] font-semibold 2xl:text-[16px] ${verdict.allowed ? 'text-emerald-700' : 'text-slate-900'}`}>
                                {headline}
                            </div>
                            {basis && <div className={`mt-0.5 text-[12.5px] text-emerald-700/80 ${T.sub}`}>{basis}</div>}
                            {!verdict.allowed && (
                                <ul className={`mt-1 space-y-0.5 text-[13px] text-slate-600 ${T.body}`}>
                                    {verdict.reasons.map((reason) => <li key={reason}>{reason}</li>)}
                                </ul>
                            )}
                        </div>
                    )}
                    {kindLine && <p className={ISSUE}>{kindLine}</p>}
                </div>

                {needsFk && (
                    <div ref={anchor('fk')} className="space-y-1.5">
                        <label className={`flex cursor-pointer items-center justify-between gap-3 rounded-xl bg-slate-50 px-3.5 py-2.5 2xl:rounded-2xl 2xl:px-5 2xl:py-3.5 ${shownProblem('fk') ? INVALID : ''}`}>
                            <span className="min-w-0">
                                <span className={`block text-[13.5px] text-slate-800 ${T.body}`}>ФК пройден — проверил во Флите</span>
                                <span className={`block text-[11.5px] text-slate-500 ${T.note}`}>В CRM статуса фотоконтроля нет</span>
                            </span>
                            <IosToggle checked={fkChecked} onChange={(value) => { setServer(null); setFkChecked(value); }} />
                        </label>
                        {shownProblem('fk') && <p className={ISSUE}>{shownProblem('fk')}</p>}
                    </div>
                )}

                <div ref={anchor('office')} className={FIELD}>
                    <span className={wLabel}>Офис</span>
                    <CustomSelect
                        value={form.officeId}
                        onChange={(value) => set('officeId', value || null)}
                        options={officeOptions}
                        placeholder="Где выдали воду"
                        variant="ios"
                        roomy
                        ariaLabel="Офис"
                        invalid={Boolean(shownProblem('office'))}
                    />
                    {shownProblem('office') && <p className={ISSUE}>{shownProblem('office')}</p>}
                </div>

                <div ref={(node) => { anchors.current.day = node; anchors.current.time = node; }} className={FIELD}>
                    <Label text="Когда выдали"
                           hint="Когда воду выдали у стойки. С этого момента в офисе на блок меньше, а водителю с него идут неделя и поездки до следующей выдачи." />
                    <div className="flex gap-2 2xl:gap-3">
                        <IosDatePicker
                            value={form.day}
                            onChange={(value) => set('day', value || '')}
                            max={now.day}
                            placeholder="Дата"
                            className="min-w-0 flex-1"
                            triggerClassName={`${DATE_TRIGGER} ${shownProblem('day') ? INVALID : ''}`}
                            ariaLabel="Дата выдачи"
                        />
                        <IosTimePicker
                            value={form.time}
                            onChange={(value) => set('time', value || '')}
                            max={form.day === now.day ? now.time : undefined}
                            defaultTime="12:00"
                            step={5}
                            placeholder="Время"
                            className="w-[112px] shrink-0 2xl:w-[128px]"
                            inputClassName={`${TIME_INPUT} ${shownProblem('time') ? INVALID : ''}`}
                            ariaLabel="Время выдачи"
                        />
                    </div>
                    {timeProblem && <p className={ISSUE}>{timeProblem}</p>}
                </div>

                <div ref={anchor('issuer')} className={FIELD}>
                    <Label text="Кто выдал"
                           hint="Офисник, который выдал воду: в журнале выдача будет записана на него, а вы — как внёсший её задним числом." />
                    {access === 'error' ? (
                        <p className={ISSUE}>Не удалось загрузить сотрудников — закройте окно и откройте заново</p>
                    ) : (
                        <CustomSelect
                            value={form.issuerId}
                            onChange={(value) => set('issuerId', value || null)}
                            options={issuerOptions}
                            placeholder={!form.officeId ? 'Сначала выберите офис'
                                : access === null ? 'Загружаем сотрудников…'
                                    : issuerOptions.length ? 'Выберите сотрудника' : 'В этом офисе никто не выдаёт воду'}
                            disabled={!form.officeId || access === null || !issuerOptions.length}
                            variant="ios"
                            roomy
                            ariaLabel="Кто выдал"
                            invalid={Boolean(issuerProblem)}
                        />
                    )}
                    {issuerProblem && <p className={ISSUE}>{issuerProblem}</p>}
                </div>

                <label className={FIELD}>
                    <span className={wLabel}>Комментарий</span>
                    <input className={wInput} value={form.comment} maxLength={500}
                           onChange={(event) => set('comment', event.target.value)}
                           placeholder="Необязательно. Например: забыли нажать «Выдать»" />
                </label>

                {blocksMax > 1 && (
                    <div className="flex items-center justify-between gap-3">
                        <span className={wLabel}>Блоков</span>
                        <WaterStepper label="Блоков" downDisabled={blocks <= 1} upDisabled={blocks >= blocksMax}
                                      onStep={(delta) => setBlocks((value) => Math.min(blocksMax, Math.max(1, value + delta)))}>
                            <span className="min-w-[2.5rem] text-center text-[15px] font-semibold tabular-nums text-slate-900 2xl:text-[18px]">{blocks}</span>
                        </WaterStepper>
                    </div>
                )}

                {server?.field === 'general' && (
                    <div ref={anchor('general')} className={`rounded-xl bg-rose-50 px-3.5 py-3 text-[13px] text-rose-700 ${T.body}`}>
                        {server.text}
                    </div>
                )}
            </div>
        </IosModal>
    );
};

export default WaterBackfill;
