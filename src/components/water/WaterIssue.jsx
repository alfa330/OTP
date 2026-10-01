import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Check, Loader2, Minus, Plus, RotateCcw, X } from 'lucide-react';
import { IosToggle, iosBtnGhost, iosBtnPrimary, iosCard, iosGroupLabel, iosInput } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { driverAccountUrl, extractAccountId, fmtDate, fmtDateTime, fmtPhone } from '../parcels/parcelMeta';
import {
    blocksWord, defaultOfficeId, kindLabel, nextIssueLine, readLastOffice, rememberOffice,
    stockStatus, tariffLabel, verdictBasis, verdictHeadline,
} from './waterMeta';

/*
 * «Выдача» — экран у стойки фронт-офиса.
 *
 * Поток один и короткий (ответ постановщика: «офисник просто вставляет ID
 * водителя и привязывает к нему выдачу»): вставил ссылку или ID → система сама
 * подтянула водителя из CRM и сказала, положена ли вода → одна кнопка.
 * Поиск стартует сам, как только строка разбирается в ID, — отдельное
 * «Найти» после вставки было бы лишним шагом (тот же приём, что в «Посылках»).
 *
 * Колл-центр видит тот же экран без офиса и без кнопки: проверяет право,
 * пока водитель на линии.
 */

const WORK_STATUS = {
    working: null,
    not_working: 'Не работает',
    fired: 'Уволен',
};

/* Заказы недели на тарифах программы из настроек — «из них Business 12,
   Premier (Ultima) 3». Тариф программы, которого нет у машины и по которому не
   было заказов, не показываем: «Premier 0» у машины без Premier — шум. Нет
   разбивки от CRM — нет и строки: «нет данных» не равно «ноль». */
const programWeekOrders = (orders, carTariffs, programTariffs) => {
    const byTariff = orders.week_by_tariff;
    if (!byTariff || typeof byTariff !== 'object' || orders.week == null) return [];
    return programTariffs
        .map((code) => [code, Number(byTariff[code]) || 0])
        .filter(([code, count]) => count > 0 || carTariffs.includes(code));
};

const DriverCard = ({ driver, programTariffs }) => {
    const account = driverAccountUrl({ driver_account_id: driver.account_id, driver_park_id: driver.park_id });
    const statusNote = driver.fired ? 'Уволен из парка'
        : driver.is_blocked ? 'Заблокирован в парке'
            : WORK_STATUS[driver.work_status] ?? null;
    const orders = driver.orders || {};
    // Список CRM обрезан на 500 заказах — числа «не меньше», отсюда «+».
    // Пробелы внутри пары неразрывные: на телефоне «Business» не уезжает от
    // своего числа на другую строку, перенос — только после запятой.
    const more = orders.week_truncated ? '+' : '';
    const programWeek = programWeekOrders(orders, driver.tariffs || [], programTariffs)
        .map(([code, count]) => `${tariffLabel(code)} ${count}${more}`.replace(/ /g, ' '))
        .join(', ');
    return (
        <div className="space-y-2">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                {account ? (
                    <a href={account} target="_blank" rel="noopener noreferrer"
                       className="text-[16px] font-semibold text-slate-900 underline decoration-transparent underline-offset-2 transition hover:decoration-inherit">
                        {driver.name || 'Без имени'}
                    </a>
                ) : (
                    <span className="text-[16px] font-semibold text-slate-900">{driver.name || 'Без имени'}</span>
                )}
                {driver.phone && (
                    <a href={`tel:${driver.phone}`} className="tabular-nums text-[13px] text-slate-500">
                        {fmtPhone(driver.phone)}
                    </a>
                )}
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[12.5px] text-slate-500">
                {driver.park && <span>{driver.park}</span>}
                {driver.registered_at && <span>Регистрация {fmtDate(driver.registered_at)}</span>}
                <span className="tabular-nums">
                    Заказов: {orders.total ?? '—'} всего · {orders.week ?? '—'} за 7 дней
                    {programWeek && `, из них ${programWeek}`}
                </span>
                {statusNote && <span className="font-medium text-rose-600">{statusNote}</span>}
            </div>
            {driver.tariffs?.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                    {driver.tariffs.map((code) => (
                        <span
                            key={code}
                            className={`rounded-full px-2 py-0.5 text-[11.5px] font-medium ${
                                programTariffs.includes(code)
                                    ? 'bg-slate-900 text-white'
                                    : 'bg-slate-100 text-slate-500'
                            }`}
                        >
                            {tariffLabel(code)}
                        </span>
                    ))}
                </div>
            )}
        </div>
    );
};

const Stepper = ({ value, max, onChange }) => (
    <div className="inline-flex items-center rounded-xl bg-slate-100 p-0.5">
        <button type="button" aria-label="Меньше" disabled={value <= 1}
                onClick={() => onChange(Math.max(1, value - 1))}
                className="grid h-9 w-9 place-items-center rounded-[10px] text-slate-600 transition hover:bg-white disabled:opacity-30">
            <Minus size={15} />
        </button>
        <span className="min-w-[2.5rem] text-center text-[15px] font-semibold tabular-nums text-slate-900">{value}</span>
        <button type="button" aria-label="Больше" disabled={value >= max}
                onClick={() => onChange(Math.min(max, value + 1))}
                className="grid h-9 w-9 place-items-center rounded-[10px] text-slate-600 transition hover:bg-white disabled:opacity-30">
            <Plus size={15} />
        </button>
    </div>
);

const WaterIssue = ({ apiBaseUrl, headers, capabilities, settings, offices, onOfficeUpdated, showToast }) => {
    const canIssue = Boolean(capabilities?.can_issue);
    // offices: null — ещё грузятся, 'error' — не загрузились, иначе список.
    const officesReady = Array.isArray(offices);
    const activeOffices = useMemo(
        () => (officesReady ? offices : []).filter((office) => office.is_active),
        [offices, officesReady],
    );

    const [officeId, setOfficeId] = useState(null);
    useEffect(() => {
        if (!canIssue) return;
        setOfficeId((current) => (current && activeOffices.some((office) => office.id === current)
            ? current
            : defaultOfficeId(activeOffices, capabilities?.default_city, readLastOffice())));
    }, [activeOffices, canIssue, capabilities?.default_city]);
    const office = activeOffices.find((item) => item.id === officeId) || null;

    const [link, setLink] = useState('');
    const accountId = useMemo(() => extractAccountId(link), [link]);
    const [result, setResult] = useState(null);
    const [error, setError] = useState('');
    const [checking, setChecking] = useState(false);
    const [issuing, setIssuing] = useState(false);
    const [issued, setIssued] = useState(null);
    const [blocks, setBlocks] = useState(1);
    // Отметка «ФК пройден» — только на тот водитель/вердикт, что на экране.
    const [fkChecked, setFkChecked] = useState(false);
    const inputRef = useRef(null);
    const ticket = useRef(0);

    /* Курсор — в поле ID, как только его можно заполнять: у фронт-офиса поле
       заперто до выбора офиса, и autoFocus на запертом поле не срабатывал. */
    const inputEnabled = !canIssue || Boolean(officeId);
    useEffect(() => {
        if (inputEnabled) inputRef.current?.focus();
    }, [inputEnabled]);

    const toastRef = useRef(showToast);
    useEffect(() => { toastRef.current = showToast; }, [showToast]);

    const check = useCallback(async (value, water_office_id) => {
        const id = ticket.current + 1;
        ticket.current = id;
        setChecking(true);
        setError('');
        try {
            const response = await axios.post(`${apiBaseUrl}/api/water/check`,
                { link: value, water_office_id: water_office_id || null }, { headers: headers() });
            if (ticket.current !== id) return;
            setResult(response.data);
            setBlocks(Math.max(1, response.data?.verdict?.blocks_max || 1));
            setFkChecked(false);
        } catch (requestError) {
            if (ticket.current !== id) return;
            setResult(null);
            setError(requestError?.response?.data?.error || 'Не удалось проверить водителя');
        } finally {
            if (ticket.current === id) setChecking(false);
        }
    }, [apiBaseUrl, headers]);

    /* Проверка сама — как только строка стала ID, и заново при смене офиса:
       от офиса зависит, хватит ли воды. */
    useEffect(() => {
        setIssued(null);
        if (!accountId) { setResult(null); setError(''); return undefined; }
        if (canIssue && !officeId) return undefined;
        const timer = setTimeout(() => check(accountId, canIssue ? officeId : null), 250);
        return () => clearTimeout(timer);
    }, [accountId, officeId, canIssue, check]);

    const clear = () => {
        ticket.current += 1;
        setLink('');
        setResult(null);
        setIssued(null);
        setError('');
        setChecking(false);
        inputRef.current?.focus();
    };

    const verdict = result?.verdict || null;
    const driver = result?.driver || null;
    const blocksMax = verdict?.blocks_max || 0;
    /* ФК нового водителя CRM чаще всего не знает (поле заполняется только после
       ручного обновления карточки). Тогда пройденный ФК подтверждает сотрудник,
       проверив во Флите, — без отметки кнопка не нажимается, сервер отметку
       перепроверяет и пишет в журнал, кто подтвердил. */
    const needsFk = Boolean(verdict?.allowed && verdict.kind === 'welcome' && verdict.fk === 'unknown');

    /* Выдача — водителю, чья карточка НА ЭКРАНЕ, а не тому, что сейчас в поле:
       пока ждём ответа (CRM, Telegram о закупке), человек мог уже вставить
       следующего. Ответ, пришедший после смены водителя, карточку не трогает —
       только тост и остаток офиса. Поле и крестик на это время заперты. */
    const issue = async () => {
        if (!verdict?.allowed || !office || issuing || !driver) return;
        const target = driver.account_id;
        const myTicket = ticket.current;
        setIssuing(true);
        try {
            const response = await axios.post(`${apiBaseUrl}/api/water/issues`, {
                link: target, water_office_id: office.id, blocks, kind: verdict.kind,
                fk_confirmed: needsFk ? fkChecked : undefined,
            }, { headers: headers() });
            const data = response.data || {};
            if (data.office) onOfficeUpdated?.(data.office);
            toastRef.current?.(`Выдано: ${blocksWord(data.issue?.blocks || blocks)}`, 'success');
            if (ticket.current !== myTicket) return;
            setIssued(data.issue || null);
            setResult((prev) => (prev ? { ...prev, verdict: data.verdict || prev.verdict,
                history: data.issue ? [data.issue, ...(prev.history || [])].slice(0, 5) : prev.history } : prev));
        } catch (requestError) {
            const data = requestError?.response?.data || {};
            toastRef.current?.(data.error || 'Не удалось выдать воду', 'error');
            if (ticket.current !== myTicket) return;
            // Сервер перепроверил и отказал — показываем его свежий вердикт; нет
            // его в ответе (гонка на приветственном, сеть) — проверяем заново,
            // чтобы на экране не осталась зелёная плашка с живой кнопкой.
            if (data.verdict) {
                setResult((prev) => (prev ? { ...prev, verdict: data.verdict } : prev));
                setBlocks(Math.max(1, data.verdict.blocks_max || 1));
            } else {
                check(target, office.id);
            }
        } finally {
            setIssuing(false);
        }
    };

    const officeOptions = activeOffices.map((item) => ({
        value: item.id,
        label: `${item.city} · ${item.name}`,
        meta: blocksWord(item.stock),
    }));

    return (
        <div className="max-w-2xl space-y-3">
            {canIssue && (
                <div className="space-y-1.5">
                    <span className={iosGroupLabel}>Офис</span>
                    {!officesReady ? (
                        <div className={`${iosCard} px-4 py-3 text-[13px] ${offices === 'error' ? 'text-rose-600' : 'text-slate-500'}`}>
                            {offices === 'error' ? 'Не удалось загрузить офисы — обновите страницу' : 'Загружаем офисы…'}
                        </div>
                    ) : activeOffices.length ? (
                        <CustomSelect
                            value={officeId}
                            onChange={(value) => { setOfficeId(value); rememberOffice(value); }}
                            options={officeOptions}
                            placeholder="Выберите офис"
                            variant="ios"
                            ariaLabel="Офис"
                        />
                    ) : (
                        <div className={`${iosCard} px-4 py-3 text-[13px] text-slate-500`}>
                            Офисы ещё не заведены в учёт.{capabilities?.can_manage
                                ? ' Добавьте их во вкладке «Остатки».'
                                : ' Их заводит региональный руководитель.'}
                        </div>
                    )}
                </div>
            )}

            <div className="space-y-1.5">
                <span className={iosGroupLabel}>Водитель</span>
                <div className="relative">
                    <input
                        ref={inputRef}
                        className={`${iosInput} pr-10`}
                        placeholder="Ссылка на водителя во Флите или его ID"
                        value={link}
                        onChange={(event) => setLink(event.target.value)}
                        disabled={(canIssue && !officeId) || issuing}
                    />
                    {(link || checking) && (
                        <button type="button" onClick={clear} aria-label="Очистить" disabled={issuing}
                                className="absolute right-2 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-200 hover:text-slate-600">
                            {checking ? <Loader2 size={15} className="animate-spin" /> : <X size={15} />}
                        </button>
                    )}
                </div>
                {link.trim() && !accountId && (
                    <p className="px-1 text-[12px] text-slate-500">
                        Не похоже на ссылку на водителя — нужен адрес карточки во Флите или ID из 32 символов
                    </p>
                )}
            </div>

            {error && (
                <div className={`${iosCard} flex items-center justify-between gap-3 px-4 py-3`}>
                    <span className="text-[13px] text-rose-600">{error}</span>
                    <button type="button" className={iosBtnGhost}
                            onClick={() => check(accountId, canIssue ? officeId : null)}>
                        <RotateCcw size={14} /> Ещё раз
                    </button>
                </div>
            )}

            {driver && verdict && (
                <div className={`${iosCard} space-y-4 p-4`}>
                    <DriverCard driver={driver} programTariffs={settings?.tariffs || []} />

                    <div className={`rounded-xl px-3.5 py-3 ${verdict.allowed || issued ? 'bg-emerald-50' : 'bg-slate-50'}`}>
                        <div className={`text-[14px] font-semibold ${verdict.allowed || issued ? 'text-emerald-700' : 'text-slate-900'}`}>
                            {issued ? `Выдано: ${blocksWord(issued.blocks)} · ${kindLabel(issued.kind).toLowerCase()}` : verdictHeadline(verdict)}
                        </div>
                        {!issued && verdict.allowed && (
                            <div className="mt-0.5 text-[12.5px] text-emerald-700/80">{verdictBasis(verdict)}</div>
                        )}
                        {!verdict.allowed && !issued && (
                            <ul className="mt-1 space-y-0.5 text-[13px] text-slate-600">
                                {verdict.reasons.map((reason) => <li key={reason}>{reason}</li>)}
                            </ul>
                        )}
                        {issued && nextIssueLine(verdict) && (
                            <div className="mt-0.5 text-[12.5px] text-emerald-700/80">{nextIssueLine(verdict)}</div>
                        )}
                    </div>

                    {result.history?.length > 0 && (
                        <div className="space-y-1">
                            <div className={iosGroupLabel}>Выдачи этому водителю</div>
                            <ul className="divide-y divide-slate-100 text-[12.5px]">
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

                    {needsFk && !issued && (canIssue ? (
                        <label className="flex cursor-pointer items-center justify-between gap-3 rounded-xl bg-slate-50 px-3.5 py-2.5">
                            <span className="min-w-0">
                                <span className="block text-[13.5px] text-slate-800">ФК пройден — проверил во Флите</span>
                                <span className="block text-[11.5px] text-slate-500">В CRM статуса фотоконтроля нет</span>
                            </span>
                            <IosToggle checked={fkChecked} onChange={setFkChecked} />
                        </label>
                    ) : (
                        <p className="text-[12.5px] text-slate-500">Статуса ФК в CRM нет — его проверят в офисе при выдаче</p>
                    ))}

                    {canIssue && verdict.allowed && !issued && (
                        <div className="flex flex-wrap items-center gap-3">
                            {blocksMax > 1 && <Stepper value={blocks} max={blocksMax} onChange={setBlocks} />}
                            <button type="button" className={`${iosBtnPrimary} flex-1 sm:flex-none`}
                                    onClick={issue}
                                    disabled={issuing || checking || !office || accountId !== driver.account_id
                                        || (needsFk && !fkChecked)}>
                                {issuing ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}
                                {`Выдать ${blocksWord(blocks)}`}
                            </button>
                            {office && (
                                <span className={`text-[12px] ${office.status === 'enough'
                                    ? 'text-slate-400' : stockStatus(office.status).text}`}>
                                    В офисе {blocksWord(office.stock)}
                                </span>
                            )}
                        </div>
                    )}

                    {issued && (
                        <button type="button" className={iosBtnGhost} onClick={clear}>
                            Следующий водитель
                        </button>
                    )}
                </div>
            )}
        </div>
    );
};

export default WaterIssue;
