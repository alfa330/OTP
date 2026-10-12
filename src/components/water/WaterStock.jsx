import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Loader2, Plus } from 'lucide-react';
import { IosMenu, IosModal, IosSegmented, IosToggle, iosCard } from '../ui/ios';
import InfoHint from '../common/InfoHint';
import CustomSelect from '../ui/CustomSelect';
import { IosDateRangePicker, rangeLabel } from '../ui/DateRangePicker';
import { fmtDateTime, shiftDaysBack, todayISO } from '../parcels/parcelMeta';
import CountInput from './CountInput';
import { blocksWord, fmtAvg, forecastLabel, officePlace, parseCount, plural, stockStatus } from './waterMeta';
import {
    ModalSubtitle, ModalTitle, T, wBtnPrimary, wBtnSecondary, wChip, wIcon, wInput, wLabel, wPill, wSegments,
    wTable, wTd, wTdSub, wTh, wThead,
} from './waterUi';

/*
 * «Остатки» — дашборд по офисам (ТЗ, разделы 6–7) и карточка офиса.
 *
 * Колонки — ровно из ТЗ: остаток, поступило, выдано, число выдач, средний
 * расход, прогноз закупки. Период по умолчанию — последние 30 дней: средний
 * расход за месяц сглаживает и выходные, и «пришёл автобус водителей».
 *
 * Цвет — только у тех офисов, где есть что делать (низкий остаток и закупка);
 * достаточный остаток не красится.
 */

const PERIOD_PRESETS = [
    { label: 'Неделя', range: () => ({ from: shiftDaysBack(todayISO(), 6), to: todayISO() }) },
    { label: '30 дней', range: () => ({ from: shiftDaysBack(todayISO(), 29), to: todayISO() }) },
    { label: '90 дней', range: () => ({ from: shiftDaysBack(todayISO(), 89), to: todayISO() }) },
];

const CHIP = 'flex items-center gap-2 rounded-xl bg-white px-3 py-2 text-left text-[12.5px] '
    + 'font-medium text-slate-700 ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)] '
    + `transition-all hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-blue-500/60 ${wChip}`;

const MOVEMENT_LABELS = { intake: 'Поступление', recount: 'Пересчёт' };

/* «Поступило» в остатках — поступления плюс пересчёты с этой отметкой:
   поступление вносят не тем числом или раньше, чем привезли воду, а снимают
   его пересчётом (05.10.2026). Отметка одна и та же в форме пересчёта и у уже
   сделанного пересчёта в списке. */
const INTAKE_FLAG_LABEL = 'Учесть в «Поступило»';
const INTAKE_FLAG_HINT = 'Включите, когда пересчётом исправляете поступление: внесли не то число, '
    + 'не в тот офис или раньше, чем привезли. «Поступило» изменится на разницу. Недостачу и излишек '
    + 'на полке так не отмечают. У уже сделанного пересчёта отметка меняется в списке ниже, в меню «···».';

const StatusPill = ({ status }) => {
    if (status === 'enough') return null;
    const meta = stockStatus(status);
    return (
        <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11.5px] font-medium ${wPill} ${meta.pill}`}>
            {meta.label}
        </span>
    );
};

const errorOf = (requestError, fallback) => requestError?.response?.data?.error || fallback;

const FIELD = 'block space-y-1.5 2xl:space-y-2';

/* ── Карточка офиса ─────────────────────────────────────────────────────── */

const OfficeSheet = ({
    open, office, onClose, canManage, apiBaseUrl, headers, settings, onSaved, onIntakeChanged, showToast,
}) => {
    const [movements, setMovements] = useState([]);
    const [loading, setLoading] = useState(false);
    const [mode, setMode] = useState('intake');
    const [amount, setAmount] = useState('');
    const [comment, setComment] = useState('');
    const [adjustsIntake, setAdjustsIntake] = useState(false);
    const [markingId, setMarkingId] = useState(null);
    const [low, setLow] = useState('');
    const [buy, setBuy] = useState('');
    const [saving, setSaving] = useState(false);

    const load = useCallback(() => {
        if (!office) return;
        setLoading(true);
        axios.get(`${apiBaseUrl}/api/water/offices/${office.id}/movements`, { headers: headers() })
            .then((response) => setMovements(response.data?.items || []))
            .catch(() => setMovements([]))
            .finally(() => setLoading(false));
    }, [apiBaseUrl, headers, office]);

    useEffect(() => {
        if (!open || !office) return;
        setMode('intake');
        setAmount('');
        setComment('');
        setLow(office.own_low_threshold ?? '');
        setBuy(office.own_buy_threshold ?? '');
        load();
        // Карточку заново открывают другим офисом — от него и зависим.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, office?.id]);

    // Отметка живёт один пересчёт: ушли с вкладки или закрыли карточку — снята,
    // иначе следующий пересчёт молча ушёл бы в «Поступило».
    useEffect(() => { setAdjustsIntake(false); }, [mode, open, office?.id]);

    if (!office) return null;

    const count = parseCount(amount);
    const amountValid = mode === 'intake' ? Number.isInteger(count) && count > 0 : Number.isInteger(count);
    const lowValue = parseCount(low);
    const buyValue = parseCount(buy);
    const thresholdsValid = !Number.isNaN(lowValue) && !Number.isNaN(buyValue);

    const post = async (url, body, method = 'post') => {
        setSaving(true);
        try {
            const response = await axios[method](`${apiBaseUrl}${url}`, body, { headers: headers() });
            onSaved?.(response.data?.office);
            return response.data;
        } catch (requestError) {
            showToast?.(errorOf(requestError, 'Не удалось сохранить'), 'error');
            return null;
        } finally {
            setSaving(false);
        }
    };

    const submitMovement = async () => {
        if (!amountValid) return;
        const data = mode === 'intake'
            ? await post(`/api/water/offices/${office.id}/intake`, { blocks: count, comment })
            : await post(`/api/water/offices/${office.id}/recount`,
                { stock: count, comment, adjusts_intake: adjustsIntake });
        if (data) {
            showToast?.(mode === 'intake' ? `Поступление: +${blocksWord(count)}` : 'Остаток пересчитан', 'success');
            setAmount('');
            setComment('');
            setAdjustsIntake(false);
            load();
        }
    };

    /* Отметка у уже сделанного пересчёта. Остаток не меняется — пересчитывается
       только «Поступило» в таблице остатков, её и просим перечитать. */
    const markIntake = async (item, next) => {
        if (markingId) return;
        setMarkingId(item.id);
        try {
            const response = await axios.patch(`${apiBaseUrl}/api/water/movements/${item.id}`,
                { adjusts_intake: next }, { headers: headers() });
            const saved = response.data?.movement || { adjusts_intake: next };
            setMovements((prev) => prev.map((row) => (row.id === item.id ? { ...row, ...saved } : row)));
            onIntakeChanged?.();
            showToast?.(next ? 'Пересчёт учтён в «Поступило»' : 'Пересчёт убран из «Поступило»', 'success');
        } catch (requestError) {
            showToast?.(errorOf(requestError, 'Не удалось сохранить'), 'error');
        } finally {
            setMarkingId(null);
        }
    };

    const submitThresholds = async () => {
        if (!thresholdsValid) return;
        const data = await post(`/api/water/offices/${office.id}`,
            { low_threshold: lowValue, buy_threshold: buyValue }, 'patch');
        if (data) showToast?.('Пороги сохранены', 'success');
    };

    const toggleActive = async (next) => {
        const data = await post(`/api/water/offices/${office.id}`, { is_active: next }, 'patch');
        if (data) showToast?.(next ? 'Офис снова в учёте' : 'Офис выведен из учёта', 'success');
    };

    const ownThresholds = office.own_low_threshold !== null || office.own_buy_threshold !== null;
    // Две колонки — только когда есть форма: без неё слева осталось бы одно число.
    const twoPane = canManage && office.is_active;
    const recountMenu = canManage && movements.some((item) => item.kind === 'recount');

    return (
        <IosModal open={open} onClose={onClose} title={<ModalTitle>{office.name}</ModalTitle>}
                  subtitle={<ModalSubtitle>{officePlace(office)}</ModalSubtitle>}
                  maxWidth={twoPane ? 'max-w-lg xl:max-w-[920px] 2xl:max-w-[1080px]' : 'max-w-lg 2xl:max-w-[680px]'}>
            {/* На телефоне и в узком окне — столбик: остаток, форма, движения, «В
                учёте». На широком экране (с xl) у руководителя окно в две колонки:
                слева остаток с формой и «В учёте», справа движения. Список движений
                бывает длинным (до 30 строк), и в одной колонке форма уезжала за
                край уже на пяти строках; левая колонка липкая — форма остаётся на
                месте, пока список прокручивается. Обёртка левой колонки до xl
                растворена (contents), порядок в столбике держат order-классы. */}
            <div className={`flex flex-col gap-4 2xl:gap-6 ${twoPane ? 'xl:grid xl:grid-cols-2 xl:items-start xl:gap-x-8 xl:gap-y-0 2xl:gap-x-10' : ''}`}>
                <div className={twoPane ? 'contents xl:sticky xl:top-4 xl:flex xl:flex-col xl:gap-4 2xl:gap-6' : 'contents'}>
                    <div className="order-1 flex items-end justify-between gap-3">
                        <div>
                            <div className={wLabel}>Остаток</div>
                            <div className={`text-[28px] font-semibold leading-tight tabular-nums xl:text-[32px] 2xl:text-[40px] 2xl:tracking-tight ${stockStatus(office.status).text}`}>
                                {blocksWord(office.stock)}
                            </div>
                        </div>
                        <div className={`text-right text-[12px] text-slate-500 ${T.sub}`}>
                            <div>Низкий остаток — {office.low_threshold} и меньше</div>
                            <div>Закупка — {office.buy_threshold} и меньше</div>
                            {!ownThresholds && <div className="text-slate-400">пороги общие</div>}
                        </div>
                    </div>

                    {canManage && office.is_active && (
                        <div className={`${iosCard} order-2 space-y-3 p-3.5 xl:p-4 2xl:space-y-4 2xl:p-5`}>
                            <IosSegmented
                                value={mode}
                                onChange={setMode}
                                stretch
                                className={wSegments}
                                options={[
                                    { value: 'intake', label: 'Поступление' },
                                    { value: 'recount', label: 'Пересчёт' },
                                    { value: 'thresholds', label: 'Пороги' },
                                ]}
                            />
                            {mode !== 'thresholds' ? (
                                <>
                                    <label className={FIELD}>
                                        <span className={wLabel}>
                                            {mode === 'intake' ? 'Поступило блоков' : 'Сколько блоков на самом деле'}
                                        </span>
                                        <CountInput key={mode} className={wInput} value={amount} onChange={setAmount}
                                                    placeholder={mode === 'intake' ? 'Блок — 16 бутылок по 0,5 л' : `Сейчас в учёте ${office.stock}`} />
                                    </label>
                                    <label className={FIELD}>
                                        <span className={wLabel}>
                                            {mode === 'intake' ? 'Накладная или комментарий' : 'Причина пересчёта'}
                                        </span>
                                        <input className={wInput} value={comment} maxLength={500}
                                               onChange={(event) => setComment(event.target.value)}
                                               placeholder={mode === 'intake' ? 'Необязательно' : 'Например: пересчитали полку'} />
                                    </label>
                                    {mode === 'recount' && (
                                        <div className="flex items-center justify-between gap-3 px-1">
                                            <span className={`flex items-center gap-1.5 text-[13px] text-slate-800 ${T.body}`}>
                                                {INTAKE_FLAG_LABEL}
                                                <InfoHint side="left" text={INTAKE_FLAG_HINT} />
                                            </span>
                                            <IosToggle checked={adjustsIntake} onChange={setAdjustsIntake} disabled={saving} />
                                        </div>
                                    )}
                                    <button type="button" className={`${wBtnPrimary} w-full`}
                                            disabled={saving || !amountValid || (mode === 'recount' && !comment.trim())}
                                            onClick={submitMovement}>
                                        {saving && <Loader2 size={15} className={`animate-spin ${wIcon}`} />}
                                        {mode === 'intake' ? 'Провести поступление' : 'Сохранить пересчёт'}
                                    </button>
                                </>
                            ) : (
                                <>
                                    <div className="grid grid-cols-2 gap-3 2xl:gap-4">
                                        <label className={FIELD}>
                                            <span className={wLabel}>Низкий остаток</span>
                                            <CountInput className={wInput} value={low} onChange={setLow}
                                                        placeholder={`Общий: ${settings?.low_threshold ?? '—'}`} />
                                        </label>
                                        <label className={FIELD}>
                                            <span className={wLabel}>Требуется закупка</span>
                                            <CountInput className={wInput} value={buy} onChange={setBuy}
                                                        placeholder={`Общий: ${settings?.buy_threshold ?? '—'}`} />
                                        </label>
                                    </div>
                                    <p className={`text-[11.5px] text-slate-500 ${T.note}`}>
                                        Пустое поле — действует общий порог из настроек.
                                    </p>
                                    <button type="button" className={`${wBtnPrimary} w-full`}
                                            disabled={saving || !thresholdsValid} onClick={submitThresholds}>
                                        {saving && <Loader2 size={15} className={`animate-spin ${wIcon}`} />}
                                        Сохранить пороги
                                    </button>
                                </>
                            )}
                        </div>
                    )}

                    {canManage && (
                        <div className="order-4 flex items-center justify-between gap-3 border-t border-slate-100 pt-3 2xl:pt-4">
                            <div>
                                <div className={`text-[13px] text-slate-800 ${T.body}`}>В учёте</div>
                                <div className={`text-[11.5px] text-slate-500 ${T.note}`}>
                                    Выведенный офис не виден в выдаче и остатках, журнал сохраняется
                                </div>
                            </div>
                            <IosToggle checked={office.is_active} onChange={toggleActive} disabled={saving} />
                        </div>
                    )}
                </div>

                <div className="order-3 space-y-1.5">
                    <div className={wLabel}>Поступления и пересчёты</div>
                    {loading ? (
                        <div className={`flex items-center gap-2 py-3 text-[12.5px] text-slate-500 ${T.sub}`}>
                            <Loader2 size={14} className={`animate-spin ${wIcon}`} /> Загружаем…
                        </div>
                    ) : movements.length ? (
                        <ul className="divide-y divide-slate-100">
                            {movements.map((item) => (
                                /* nowrap инлайном: оболочка телефона (mobile-shell.css) переносит
                                   ряды с gap, и у строки с длинной подписью число с меню падало
                                   под текст, к левому краю. Классом её правило не перебить. */
                                <li key={item.id} style={{ flexWrap: 'nowrap' }}
                                    className="flex items-start justify-between gap-3 py-2 text-[12.5px] xl:py-2.5 xl:text-[13.5px] 2xl:py-3 2xl:text-[15px]">
                                    <div className="min-w-0">
                                        {/* Отметка и дата не рвутся посередине: на узком экране
                                            строка переносится между ними, а не внутри даты. */}
                                        <div className="text-slate-800">
                                            {MOVEMENT_LABELS[item.kind] || item.kind}
                                            {item.kind === 'recount' && item.adjusts_intake && (
                                                <span className="text-slate-500"> · <span className="whitespace-nowrap">в «Поступило»</span></span>
                                            )}
                                            <span className="text-slate-400"> · <span className="whitespace-nowrap">{fmtDateTime(item.created_at)}</span></span>
                                        </div>
                                        <div className="truncate text-slate-500">
                                            {[item.actor_name, item.comment].filter(Boolean).join(' · ')}
                                        </div>
                                    </div>
                                    <div className="flex shrink-0 items-start gap-1">
                                        <div className="text-right tabular-nums">
                                            <div className={item.delta > 0 ? 'text-emerald-700' : 'text-slate-700'}>
                                                {item.delta > 0 ? `+${item.delta}` : item.delta}
                                            </div>
                                            <div className={`text-[11.5px] text-slate-400 ${T.note}`}>стало {item.stock_after}</div>
                                        </div>
                                        {/* Меню — у пересчётов; у поступлений на его месте пусто той же
                                            ширины, чтобы числа стояли в один столбец. */}
                                        {recountMenu && (item.kind === 'recount' ? (
                                            <IosMenu
                                                label="Пересчёт и «Поступило»"
                                                disabled={markingId !== null}
                                                items={[{
                                                    key: 'intake',
                                                    label: item.adjusts_intake ? 'Не учитывать в «Поступило»' : INTAKE_FLAG_LABEL,
                                                    onSelect: () => markIntake(item, !item.adjusts_intake),
                                                }]}
                                            />
                                        ) : <span className="w-8 shrink-0" aria-hidden="true" />)}
                                    </div>
                                </li>
                            ))}
                        </ul>
                    ) : (
                        <p className={`py-2 text-[12.5px] text-slate-500 ${T.sub}`}>Пока ничего не было</p>
                    )}
                </div>
            </div>
        </IosModal>
    );
};

/* ── Добавить офис в учёт ───────────────────────────────────────────────── */
/* Справочник приходит с сервера уже отобранным: только офисы Алматы и Астаны,
   без офисов Wolt и без офиса «Бизнес» в Астане (water/rules.py:
   is_program_office), и без тех, что в учёте. */

const AddOffice = ({ open, onClose, directory, apiBaseUrl, headers, onAdded, showToast }) => {
    const [officeId, setOfficeId] = useState(null);
    const [stock, setStock] = useState('');
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        if (open) { setOfficeId(null); setStock(''); }
    }, [open]);

    const count = parseCount(stock);
    const valid = officeId && Number.isInteger(count);

    const save = async () => {
        if (!valid || saving) return;
        setSaving(true);
        try {
            const response = await axios.post(`${apiBaseUrl}/api/water/offices`,
                { office_id: officeId, stock: count }, { headers: headers() });
            onAdded?.(response.data?.office);
            showToast?.('Офис добавлен в учёт', 'success');
            onClose();
        } catch (requestError) {
            showToast?.(errorOf(requestError, 'Не удалось добавить офис'), 'error');
        } finally {
            setSaving(false);
        }
    };

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={<ModalTitle>Офис в учёт</ModalTitle>}
            maxWidth="max-w-lg 2xl:max-w-[600px]"
            footer={(
                <button type="button" className={`${wBtnPrimary} w-full`} disabled={!valid || saving} onClick={save}>
                    {saving && <Loader2 size={15} className={`animate-spin ${wIcon}`} />}
                    Добавить
                </button>
            )}
        >
            <div className="space-y-3 2xl:space-y-4">
                <label className={FIELD}>
                    <span className={wLabel}>Офис</span>
                    <CustomSelect
                        value={officeId}
                        onChange={setOfficeId}
                        options={(directory || []).map((item) => ({
                            value: item.id, label: `${item.city} · ${item.name}`, meta: item.address || '',
                        }))}
                        placeholder={(directory || []).length ? 'Выберите офис' : 'Все офисы Алматы и Астаны уже в учёте'}
                        disabled={!(directory || []).length}
                        variant="ios"
                        roomy
                        searchable
                        ariaLabel="Офис"
                    />
                </label>
                <label className={FIELD}>
                    <span className={wLabel}>Сколько блоков сейчас в офисе</span>
                    <CountInput className={wInput} value={stock} onChange={setStock}
                                placeholder="Блок — 16 бутылок по 0,5 л" />
                </label>
            </div>
        </IosModal>
    );
};

/* ── Экран ──────────────────────────────────────────────────────────────── */

const WaterStock = ({ apiBaseUrl, headers, capabilities, settings, offices, directory, onOfficeUpdated, showToast }) => {
    const canManage = Boolean(capabilities?.can_manage);
    const [range, setRange] = useState(() => ({ from: shiftDaysBack(todayISO(), 29), to: todayISO() }));
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [openedId, setOpenedId] = useState(null);
    const [adding, setAdding] = useState(false);
    const ticket = useRef(0);

    const load = useCallback(async () => {
        const id = ticket.current + 1;
        ticket.current = id;
        setLoading(true);
        setError('');
        try {
            const params = new URLSearchParams({ date_from: range.from, date_to: range.to });
            const response = await axios.get(`${apiBaseUrl}/api/water/dashboard?${params}`, { headers: headers() });
            if (ticket.current === id) setRows(response.data?.rows || []);
        } catch (requestError) {
            if (ticket.current === id) setError(errorOf(requestError, 'Не удалось загрузить остатки'));
        } finally {
            if (ticket.current === id) setLoading(false);
        }
    }, [apiBaseUrl, headers, range]);

    useEffect(() => { load(); }, [load]);

    const opened = (offices || []).find((office) => office.id === openedId) || null;
    const inactive = useMemo(() => (offices || []).filter((office) => !office.is_active), [offices]);
    const needBuy = rows.filter((row) => row.status === 'buy').length;

    const saved = (office) => {
        if (!office) return;
        onOfficeUpdated?.(office);
        load();
    };

    return (
        /* Столбик держит gap, а не space-y: окна карточки офиса и «Офис в учёт»
           лежат детьми этого же блока, и отступ space-y доставался и им —
           затемнение начиналось ниже верхнего края экрана на его величину. */
        <div className="flex flex-col gap-3 xl:gap-4 2xl:gap-5">
            <div className="flex flex-wrap items-center gap-2 xl:gap-3 2xl:gap-4">
                <div className="w-full sm:w-auto">
                    <IosDateRangePicker
                        roomy
                        from={range.from}
                        to={range.to}
                        max={todayISO()}
                        onChange={({ from, to }) => setRange({ from: from || to, to: to || from })}
                        presets={PERIOD_PRESETS}
                        triggerClassName={CHIP}
                    />
                </div>
                {needBuy > 0 && (
                    <span className="text-[12.5px] font-medium text-rose-600 xl:text-[13.5px] 2xl:text-[15px]">
                        Требуется закупка: {needBuy} {plural(needBuy, 'офис', 'офиса', 'офисов')}
                    </span>
                )}
                {canManage && (
                    <button type="button" className={`${wBtnSecondary} ml-auto`} onClick={() => setAdding(true)}>
                        <Plus size={15} className={wIcon} /> Офис в учёт
                    </button>
                )}
            </div>

            {error && <div className={`text-[13px] text-rose-600 ${T.body}`}>{error}</div>}

            {/* Прокрутка вбок — запас: с закреплённым сайдбаром колонка раздела
                уже окна на 220 px, и крупная таблица могла бы не войти. */}
            <div className={`${iosCard} hidden overflow-x-auto md:block`}>
                <table className={wTable}>
                    <thead className={wThead}>
                        <tr>
                            <th className={wTh}>Офис</th>
                            <th className={wTh}>Остаток</th>
                            <th className={`${wTh} text-right`}>Поступило</th>
                            <th className={`${wTh} text-right`}>Выдано</th>
                            <th className={`${wTh} text-right`}>Выдач</th>
                            <th className={`${wTh} text-right`}>Расход в день</th>
                            <th className={wTh}>Прогноз закупки</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row, index) => (
                            <tr key={row.id} onClick={() => setOpenedId(row.id)}
                                className={`cursor-pointer transition hover:bg-slate-50 ${index ? 'border-t border-slate-100' : ''}`}>
                                <td className={wTd}>
                                    <div className="text-slate-900">{row.name}</div>
                                    <div className={`text-[12px] text-slate-500 ${wTdSub}`}>{officePlace(row)}</div>
                                </td>
                                <td className={wTd}>
                                    <div className="flex items-center gap-2 2xl:gap-3">
                                        <span className={`font-semibold tabular-nums 2xl:text-[18px] ${stockStatus(row.status).text}`}>{row.stock}</span>
                                        <StatusPill status={row.status} />
                                    </div>
                                </td>
                                <td className={`${wTd} text-right tabular-nums text-slate-700`}>{row.intake_blocks}</td>
                                <td className={`${wTd} text-right tabular-nums text-slate-700`}>{row.issued_blocks}</td>
                                <td className={`${wTd} text-right tabular-nums text-slate-700`}>{row.issues}</td>
                                <td className={`${wTd} text-right tabular-nums text-slate-700`}>{fmtAvg(row.avg_daily)}</td>
                                <td className={`${wTd} text-slate-600 2xl:whitespace-nowrap`}>{forecastLabel(row)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                {!rows.length && !loading && (
                    <div className={`px-4 py-10 text-center text-[13.5px] text-slate-500 2xl:py-14 ${T.body}`}>
                        {canManage ? 'Офисов в учёте пока нет — добавьте первый' : 'Офисов в учёте пока нет'}
                    </div>
                )}
            </div>

            <div className="space-y-2 md:hidden">
                {rows.map((row) => (
                    <button key={row.id} type="button" onClick={() => setOpenedId(row.id)}
                            className={`${iosCard} w-full p-3.5 text-left transition active:scale-[0.99]`}>
                        <div className="flex items-baseline justify-between gap-2">
                            <span className="truncate text-[14px] font-medium text-slate-900">{row.name}</span>
                            <span className={`shrink-0 text-[15px] font-semibold tabular-nums ${stockStatus(row.status).text}`}>
                                {blocksWord(row.stock)}
                            </span>
                        </div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[12px] text-slate-500">
                            <span>{officePlace(row)}</span>
                            <span>выдано {row.issued_blocks} · поступило {row.intake_blocks}</span>
                            <span>в день {fmtAvg(row.avg_daily)}</span>
                        </div>
                        {row.status !== 'enough' && (
                            <div className="mt-1.5 flex items-center gap-2">
                                <StatusPill status={row.status} />
                                {row.status !== 'buy' && <span className="text-[12px] text-slate-500">закупка {forecastLabel(row)}</span>}
                            </div>
                        )}
                    </button>
                ))}
                {!rows.length && !loading && (
                    <div className={`${iosCard} px-4 py-10 text-center text-[13.5px] text-slate-500`}>
                        Офисов в учёте пока нет
                    </div>
                )}
            </div>

            {loading && (
                <div className={`flex items-center justify-center gap-2 text-[13px] text-slate-500 ${T.body}`}>
                    <Loader2 size={15} className={`animate-spin ${wIcon}`} /> Загружаем остатки…
                </div>
            )}

            {!loading && rows.length > 0 && (
                <p className={`px-1 text-[11.5px] text-slate-400 ${T.note}`}>
                    За период {rangeLabel(range.from, range.to)}. Прогноз — через сколько дней при среднем расходе остаток дойдёт до порога закупки.
                </p>
            )}

            {canManage && inactive.length > 0 && (
                <div className={`px-1 text-[12.5px] text-slate-500 ${T.sub}`}>
                    Выведены из учёта:{' '}
                    {inactive.map((office, index) => (
                        <React.Fragment key={office.id}>
                            {index > 0 && ', '}
                            <button type="button" className="underline decoration-slate-300 underline-offset-2 hover:text-slate-700"
                                    onClick={() => setOpenedId(office.id)}>
                                {office.city} · {office.name}
                            </button>
                        </React.Fragment>
                    ))}
                </div>
            )}

            <OfficeSheet
                open={Boolean(opened)}
                office={opened}
                onClose={() => setOpenedId(null)}
                canManage={canManage}
                apiBaseUrl={apiBaseUrl}
                headers={headers}
                settings={settings}
                onSaved={saved}
                onIntakeChanged={load}
                showToast={showToast}
            />
            {canManage && (
                <AddOffice
                    open={adding}
                    onClose={() => setAdding(false)}
                    directory={directory}
                    apiBaseUrl={apiBaseUrl}
                    headers={headers}
                    onAdded={saved}
                    showToast={showToast}
                />
            )}
        </div>
    );
};

export default WaterStock;
