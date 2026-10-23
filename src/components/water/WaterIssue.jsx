import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import { Check, Loader2, RotateCcw, X } from 'lucide-react';
import { IosToggle, iosCard } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { driverAccountUrl, extractAccountId, fmtDate, fmtDateTime, fmtPhone } from '../parcels/parcelMeta';
import {
    blocksWord, defaultOfficeId, issueOffices, kindLabel, nextIssueLine, readLastOffice, rememberOffice,
    stockStatus, tariffLabel, verdictBasis, verdictHeadline,
} from './waterMeta';
import { T, WaterStepper, wBtnGhost, wBtnPrimary, wIcon, wInput, wLabel, wPill } from './waterUi';

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
 *
 * Офисник выбирает только из офисов, к которым ему выдан доступ (владелец
 * 23.10.2026, «Настройки» → «Доступ по офисам»). Не выдан ни один — воду он не
 * выдаёт, но водителя проверить может, как колл-центр, и экран говорит, у кого
 * просить доступ.
 */

const WORK_STATUS = {
    working: null,
    not_working: 'Не работает',
    fired: 'Уволен',
};

/* `is_blocked` в CRM — не запрет работать в парке: так помечена карточка, в
   которой сменили номер телефона («карточка заблокирована сменой номера» в
   спецификации CRM). Водитель при этом числится работающим и выполняет
   заказы, право на воду от флага не зависит — подпись только говорит, что с
   карточкой. */
const CARD_BLOCKED = 'Сменился номер — карточка в CRM заблокирована';

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

/* ФИО водителя — главная строка карточки: по ней сверяют человека у стойки. */
const NAME = 'xl:text-[17px] 2xl:text-[22px] 2xl:tracking-tight';

const DriverCard = ({ driver, programTariffs }) => {
    const account = driverAccountUrl({ driver_account_id: driver.account_id, driver_park_id: driver.park_id });
    const statusNote = driver.fired ? 'Уволен из парка'
        : driver.is_blocked ? CARD_BLOCKED
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
        <div className="space-y-2 2xl:space-y-3">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 2xl:gap-x-3">
                {account ? (
                    <a href={account} target="_blank" rel="noopener noreferrer"
                       className={`text-[16px] font-semibold text-slate-900 underline decoration-transparent underline-offset-2 transition hover:decoration-inherit ${NAME}`}>
                        {driver.name || 'Без имени'}
                    </a>
                ) : (
                    <span className={`text-[16px] font-semibold text-slate-900 ${NAME}`}>{driver.name || 'Без имени'}</span>
                )}
                {driver.phone && (
                    <a href={`tel:${driver.phone}`} className={`tabular-nums text-[13px] text-slate-500 ${T.body}`}>
                        {fmtPhone(driver.phone)}
                    </a>
                )}
            </div>
            <div className={`flex flex-wrap gap-x-3 gap-y-0.5 text-[12.5px] text-slate-500 2xl:gap-x-4 2xl:gap-y-1 ${T.sub}`}>
                {driver.park && <span>{driver.park}</span>}
                {driver.registered_at && <span>Регистрация {fmtDate(driver.registered_at)}</span>}
                <span className="tabular-nums">
                    Заказов: {orders.total ?? '—'} всего · {orders.week ?? '—'} за 7 дней
                    {programWeek && `, из них ${programWeek}`}
                </span>
                {statusNote && <span className="font-medium text-rose-600">{statusNote}</span>}
            </div>
            {driver.tariffs?.length > 0 && (
                <div className="flex flex-wrap gap-1.5 2xl:gap-2">
                    {driver.tariffs.map((code) => (
                        <span
                            key={code}
                            className={`rounded-full px-2 py-0.5 text-[11.5px] font-medium ${wPill} ${
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

/* Плашка на месте списка офисов (загрузка, сбой, «офисов нет») — ростом с поле
   рядом: 41 px на компьютере (с xl), 48 px на большом экране. */
const NOTE = `px-4 py-3 text-[13px] xl:py-2.5 2xl:px-5 2xl:py-3 ${T.body}`;

const WaterIssue = ({
    apiBaseUrl, headers, capabilities, settings, offices, onOfficeUpdated, onCapabilities, showToast,
}) => {
    const canIssue = Boolean(capabilities?.can_issue);
    // offices: null — ещё грузятся, 'error' — не загрузились, иначе список.
    const officesReady = Array.isArray(offices);
    // Офиснику — офисы, к которым выдан доступ; руководителю (null) — все.
    const allowedIds = capabilities?.issue_office_ids;
    const byGrant = Array.isArray(allowedIds);
    const activeOffices = useMemo(
        () => issueOffices((officesReady ? offices : []).filter((office) => office.is_active), allowedIds),
        [offices, officesReady, allowedIds],
    );
    /* Офисник без доступа ни к одному офису учёта воду не выдаёт — экран
       становится проверкой, как у колл-центра. Дальше «выдаёт ли этот экран»
       — officeMode, а не canIssue. */
    const noOffice = canIssue && byGrant && officesReady && activeOffices.length === 0;
    const officeMode = canIssue && !noOffice;

    const [officeId, setOfficeId] = useState(null);
    useEffect(() => {
        if (!officeMode) { setOfficeId(null); return; }
        setOfficeId((current) => (current && activeOffices.some((office) => office.id === current)
            ? current
            : defaultOfficeId(activeOffices, capabilities?.default_city, readLastOffice(), byGrant)));
    }, [activeOffices, officeMode, byGrant, capabilities?.default_city]);
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
    const inputEnabled = !officeMode || Boolean(officeId);
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
        if (officeMode && !officeId) return undefined;
        const timer = setTimeout(() => check(accountId, officeMode ? officeId : null), 250);
        return () => clearTimeout(timer);
    }, [accountId, officeId, officeMode, check]);

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
            /* Доступ к офису сняли, пока экран был открыт: свежие права пришли
               в ответе — офис уходит из списка, и проверять водителя по нему
               заново незачем. */
            if (data.capabilities) onCapabilities?.(data.capabilities);
            if (data.code === 'WATER_OFFICE_FORBIDDEN') return;
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

    /* Ряд «Офис | Водитель» — когда офис есть из чего выбирать. Пока офисов в
       учёте нет, на месте списка стоит пояснение в две строки, и рядом с полем
       в одну строку оно смотрелось бы ступенькой: тогда блоки идут друг под другом. */
    const fieldsRow = canIssue && (!officesReady || activeOffices.length > 0);
    const granted = verdict?.allowed || issued;
    const showFk = needsFk && !issued;
    const showIssue = officeMode && Boolean(verdict?.allowed) && !issued;

    return (
        <div className="max-w-2xl space-y-3 xl:space-y-4 xl:max-w-none 2xl:space-y-5">
            {/* На широком экране (с xl) офис и водитель — один ряд во всю колонку
                раздела, как строка поиска в журнале; ответ встаёт под ним. Доли
                ряда — 5 и 7 из двенадцати, а не сетка в квадратных скобках: её
                оболочка телефона ловит по подстроке «grid-cols-[» и складывает в
                одну колонку (низкое широкое окно тоже считается телефоном).
                min-w-0 у блоков обязателен: без него колонка сетки растёт под
                длинное название офиса, и список выезжает за правый край поля
                (на телефоне — на 7 px при названии в 36 знаков). */}
            <div className={`grid gap-3 xl:gap-4 2xl:gap-6 ${fieldsRow ? 'xl:grid-cols-12' : ''}`}>
                {canIssue && (
                    <div className={`min-w-0 space-y-1.5 2xl:space-y-2 ${fieldsRow ? 'xl:col-span-5' : ''}`}>
                        <span className={wLabel}>Офис</span>
                        {!officesReady ? (
                            <div className={`${iosCard} ${NOTE} ${offices === 'error' ? 'text-rose-600' : 'text-slate-500'}`}>
                                {offices === 'error' ? 'Не удалось загрузить офисы — обновите страницу' : 'Загружаем офисы…'}
                            </div>
                        ) : activeOffices.length ? (
                            <CustomSelect
                                value={officeId}
                                onChange={(value) => { setOfficeId(value); rememberOffice(value); }}
                                options={officeOptions}
                                placeholder="Выберите офис"
                                variant="ios"
                                roomy
                                ariaLabel="Офис"
                            />
                        ) : byGrant ? (
                            <div className={`${iosCard} ${NOTE} text-slate-500`}>
                                Выдавать воду вам пока не открыто ни в одном офисе — доступ выдаёт руководитель
                                фронт-офисов. Проверить водителя можно и сейчас.
                            </div>
                        ) : (
                            <div className={`${iosCard} ${NOTE} text-slate-500`}>
                                Офисы ещё не заведены в учёт.{capabilities?.can_manage
                                    ? ' Добавьте их во вкладке «Остатки».'
                                    : ' Их заводит региональный руководитель.'}
                            </div>
                        )}
                    </div>
                )}

                <div className={`min-w-0 space-y-1.5 2xl:space-y-2 ${fieldsRow ? 'xl:col-span-7' : ''}`}>
                    <span className={wLabel}>Водитель</span>
                    <div className="relative">
                        <input
                            ref={inputRef}
                            className={`${wInput} pr-10 2xl:pr-12`}
                            placeholder="Ссылка на водителя во Флите или его ID"
                            value={link}
                            onChange={(event) => setLink(event.target.value)}
                            disabled={(officeMode && !officeId) || issuing}
                        />
                        {(link || checking) && (
                            <button type="button" onClick={clear} aria-label="Очистить" disabled={issuing}
                                    className="absolute right-2 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-200 hover:text-slate-600 2xl:right-2.5 2xl:h-8 2xl:w-8">
                                {checking ? <Loader2 size={15} className={`animate-spin ${wIcon}`} /> : <X size={15} className={wIcon} />}
                            </button>
                        )}
                    </div>
                    {link.trim() && !accountId && (
                        <p className={`px-1 text-[12px] text-slate-500 ${T.note}`}>
                            Не похоже на ссылку на водителя — нужен адрес карточки во Флите или ID из 32 символов
                        </p>
                    )}
                </div>
            </div>

            {error && (
                <div className={`${iosCard} flex items-center justify-between gap-3 px-4 py-3 2xl:px-6 2xl:py-4`}>
                    <span className={`text-[13px] text-rose-600 ${T.body}`}>{error}</span>
                    <button type="button" className={wBtnGhost}
                            onClick={() => check(accountId, officeMode ? officeId : null)}>
                        <RotateCcw size={14} className={wIcon} /> Ещё раз
                    </button>
                </div>
            )}

            {driver && verdict && (
                /* На телефоне и в узком окне — столбик: водитель, ответ, прошлые
                   выдачи, действие. На широком экране (с xl) — две колонки: слева
                   «кто» (водитель и его выдачи), справа «что делать» (ответ и
                   кнопка). Колонки — обёртки, которые до xl растворены (contents),
                   а порядок в столбике держат order-классы: так ответ остаётся над
                   историей, а в колонках высоты не зависят друг от друга. */
                <div className={`${iosCard} flex flex-col gap-4 p-4 xl:p-5 xl:grid xl:grid-cols-2 xl:items-start xl:gap-x-8 xl:gap-y-0 2xl:gap-x-12 2xl:p-8`}>
                    <div className="contents xl:flex xl:flex-col xl:gap-5 2xl:gap-7">
                        <div className="order-1">
                            <DriverCard driver={driver} programTariffs={settings?.tariffs || []} />
                        </div>

                        {result.history?.length > 0 && (
                            <div className="order-3 space-y-1 2xl:space-y-1.5">
                                <div className={wLabel}>Выдачи этому водителю</div>
                                <ul className={`divide-y divide-slate-100 text-[12.5px] ${T.sub}`}>
                                    {result.history.slice(0, 3).map((item) => (
                                        <li key={item.id} className="flex flex-wrap justify-between gap-x-3 py-1.5 xl:py-2 2xl:py-2.5">
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

                    <div className="contents xl:flex xl:flex-col xl:gap-4 2xl:gap-5">
                        <div className={`order-2 rounded-xl px-3.5 py-3 xl:px-4 2xl:rounded-2xl 2xl:px-6 2xl:py-5 ${granted ? 'bg-emerald-50' : 'bg-slate-50'}`}>
                            <div className={`text-[14px] font-semibold xl:text-[15px] 2xl:text-[19px] ${granted ? 'text-emerald-700' : 'text-slate-900'}`}>
                                {issued ? `Выдано: ${blocksWord(issued.blocks)} · ${kindLabel(issued.kind).toLowerCase()}` : verdictHeadline(verdict)}
                            </div>
                            {!issued && verdict.allowed && (
                                <div className={`mt-0.5 text-[12.5px] text-emerald-700/80 2xl:mt-1 ${T.sub}`}>{verdictBasis(verdict)}</div>
                            )}
                            {!verdict.allowed && !issued && (
                                <ul className={`mt-1 space-y-0.5 text-[13px] text-slate-600 2xl:mt-2 2xl:space-y-1 ${T.body}`}>
                                    {verdict.reasons.map((reason) => <li key={reason}>{reason}</li>)}
                                </ul>
                            )}
                            {issued && nextIssueLine(verdict) && (
                                <div className={`mt-0.5 text-[12.5px] text-emerald-700/80 2xl:mt-1 ${T.sub}`}>{nextIssueLine(verdict)}</div>
                            )}
                        </div>

                        {(showFk || showIssue || issued) && (
                            <div className="order-4 space-y-4 2xl:space-y-5">
                                {showFk && (officeMode ? (
                                    <label className="flex cursor-pointer items-center justify-between gap-3 rounded-xl bg-slate-50 px-3.5 py-2.5 xl:px-4 xl:py-3 2xl:rounded-2xl 2xl:px-6 2xl:py-4">
                                        <span className="min-w-0">
                                            <span className={`block text-[13.5px] text-slate-800 ${T.body}`}>ФК пройден — проверил во Флите</span>
                                            <span className={`block text-[11.5px] text-slate-500 ${T.note}`}>В CRM статуса фотоконтроля нет</span>
                                        </span>
                                        <IosToggle checked={fkChecked} onChange={setFkChecked} />
                                    </label>
                                ) : (
                                    <p className={`text-[12.5px] text-slate-500 ${T.sub}`}>Статуса ФК в CRM нет — его проверят в офисе при выдаче</p>
                                ))}

                                {showIssue && (
                                    <div className="flex flex-wrap items-center gap-3 2xl:gap-4">
                                        {blocksMax > 1 && (
                                            <WaterStepper label="Блоков к выдаче" downDisabled={blocks <= 1} upDisabled={blocks >= blocksMax}
                                                          onStep={(delta) => setBlocks((value) => Math.min(blocksMax, Math.max(1, value + delta)))}>
                                                <span className="min-w-[2.5rem] text-center text-[15px] font-semibold tabular-nums text-slate-900 2xl:px-1.5 2xl:text-[18px]">{blocks}</span>
                                            </WaterStepper>
                                        )}
                                        <button type="button" className={`${wBtnPrimary} flex-1 sm:flex-none 2xl:!px-8`}
                                                onClick={issue}
                                                disabled={issuing || checking || !office || accountId !== driver.account_id
                                                    || (needsFk && !fkChecked)}>
                                            {issuing ? <Loader2 size={15} className={`animate-spin ${wIcon}`} /> : <Check size={15} className={wIcon} />}
                                            {`Выдать ${blocksWord(blocks)}`}
                                        </button>
                                        {office && (
                                            <span className={`text-[12px] ${T.sub} ${office.status === 'enough'
                                                ? 'text-slate-400' : stockStatus(office.status).text}`}>
                                                В офисе {blocksWord(office.stock)}
                                            </span>
                                        )}
                                    </div>
                                )}

                                {issued && (
                                    <button type="button" className={wBtnGhost} onClick={clear}>
                                        Следующий водитель
                                    </button>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
};

export default WaterIssue;
