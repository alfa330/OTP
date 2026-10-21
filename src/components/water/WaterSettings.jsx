import React, { useEffect, useId, useMemo, useState } from 'react';
import axios from 'axios';
import { Check, Loader2, Minus, Plus } from 'lucide-react';
import InfoHint from '../common/InfoHint';
import CustomSelect from '../ui/CustomSelect';
import { iosCard } from '../ui/ios';
import { fmtDateTime } from '../parcels/parcelMeta';
import { COUNT_HINT, useCountInput } from './CountInput';
import { KNOWN_TARIFFS, SETTINGS_LIMITS, parseCount, settingsIssues, tariffLabel } from './waterMeta';
import { T, wBtnPrimary, wBtnSecondary, wIcon, wLabel } from './waterUi';

/*
 * «Настройки» — условия программы и пороги остатка (ТЗ 3.2, 6, 7).
 *
 * «Предусмотреть возможность дальнейшего изменения количества заказов и
 * перечня доступных тарифов без переработки основной логики системы» — всё,
 * что постановщик назвал числом, здесь поле, а не константа в коде.
 *
 * Видит только региональный руководитель (глава фронт-офисов) и глобальный
 * админ; сервер закрыт тем же правилом.
 *
 * Вид — список настроек iOS/macOS (владелец 21.10.2026: «выглядит всё криво и
 * не симметрично, сделай аккуратно и удобно»). До этого группы стояли сеткой
 * из трёх колонок разной высоты: под «Тарифами» оставалась пустота, справа
 * две карточки стопкой, пояснения разной длины под полями сбивали строки, а на
 * телефоне два поля из четырёх падали под подпись к левому краю. Теперь:
 *   — группа — карточка во всю ширину раздела, в ней строки «подпись слева —
 *     значение справа» с тонкими разделителями; с xl строки идут в две равные
 *     колонки, поэтому края и середины всех карточек совпадают;
 *   — числа — степпером «− 10 +», набрать с клавиатуры тоже можно;
 *   — пояснение — под «i» у подписи; под подписью бывает только то, что надо
 *     исправить, красным;
 *   — тарифы — ровная сетка одинаковых плиток; выбранная — мягко-голубая с
 *     галочкой, а не чёрная (владелец 16.10.2026: чёрный выбор «выглядит плохо»).
 */

const PROGRAM_FIELDS = [
    { name: 'min_trips', label: 'Поездок для выдачи за активность',
      hint: 'Считаются поездки с прошлой выдачи, а в первый раз — за последние 7 дней' },
    { name: 'cooldown_days', label: 'Дней между выдачами',
      hint: 'Следующая выдача за активность — не раньше, чем через столько дней после прошлой' },
    { name: 'welcome_blocks', label: 'Блоков в приветственной выдаче' },
    { name: 'activity_blocks', label: 'Блоков за активность' },
];

const THRESHOLD_FIELDS = [
    { name: 'low_threshold', label: 'Низкий остаток',
      hint: 'Когда в офисе остаётся столько блоков или меньше, он помечается «Низкий остаток»' },
    { name: 'buy_threshold', label: 'Требуется закупка',
      hint: 'Когда в офисе остаётся столько блоков или меньше, он помечается «Требуется закупка», '
          + 'а получателям ниже уходит сообщение в Telegram' },
];

const NUMBER_NAMES = [...PROGRAM_FIELDS, ...THRESHOLD_FIELDS].map((field) => field.name);

/* Оболочка телефона переводит ряды с justify-between в перенос, и поле падало
   под подпись к левому краю (снимок 390×844 до правки). Перебивается только
   инлайном — класс flex-nowrap она сама переводит в перенос. */
const NOWRAP = { flexWrap: 'nowrap' };

/* Ячейка строки. Отступ — у ячейки, разделитель — у строки внутри: так линия
   начинается от текста, как в списках iOS, а у двух колонок с xl линии не
   сливаются в одну через всю карточку. */
const CELL = 'min-w-0 px-4 xl:px-5 2xl:px-6';
const ROW = 'flex min-h-[56px] items-center justify-between gap-3 border-slate-100 py-2.5 '
    + 'xl:min-h-[64px] 2xl:min-h-[76px] 2xl:gap-4';
const LABEL = `text-[14px] text-slate-800 ${T.body}`;
const ISSUE = `mt-0.5 text-[12px] text-rose-600 ${T.note}`;

/* Разделитель над строкой: на телефоне — у всех, кроме первой; с xl строки
   идут парами, и линии нет у первой пары. */
const rowEdge = (index) => [
    index > 0 ? 'border-t' : '',
    index === 1 ? 'xl:border-t-0' : '',
].join(' ');

const STEP = 'grid size-8 place-items-center rounded-[9px] text-slate-600 transition hover:bg-white '
    + 'active:scale-95 disabled:pointer-events-none disabled:opacity-30 xl:size-9 2xl:size-10 2xl:rounded-[11px]';

/* На телефоне плиток две в ряд, и «Личный водитель» кеглем 13,5 обрезался
   многоточием на пару пикселей — там кегль 13. */
const TILE = 'flex min-h-[44px] items-center justify-between gap-1.5 rounded-xl px-3 py-2 text-left '
    + 'text-[13px] font-medium ring-1 transition active:scale-[0.97] '
    + 'xl:min-h-[48px] xl:gap-2 xl:px-3.5 xl:text-[14px] 2xl:min-h-[56px] 2xl:rounded-[14px] 2xl:px-4 2xl:text-[15px]';

/* Подпись группы, «Изменил…» и красная строка под карточкой начинаются там же,
   где текст строк. У wLabel свой px-1 — его обёртке на 4 px меньше. */
const TEXT_INSET = 'px-4 xl:px-5 2xl:px-6';
const LABEL_INSET = 'px-3 xl:px-4 2xl:px-5';

const Section = ({ title, info, issue, children }) => (
    <section className="space-y-1.5 2xl:space-y-2">
        <div className={`flex items-center gap-1.5 ${LABEL_INSET}`}>
            <span className={wLabel}>{title}</span>
            {info && <InfoHint side="left" text={info} />}
        </div>
        {children}
        {issue && <div className={`${TEXT_INSET} ${ISSUE}`}>{issue}</div>}
    </section>
);

/* «i» стоит сразу за последним словом подписи и переносится вместе с ним:
   отдельным флекс-элементом она на телефоне падала третьей строкой под
   подпись в две строки. Сама «i» — вне <label>: нажатие на неё открывает
   подсказку, а не ставит курсор в поле. */
const RowLabel = ({ htmlFor, label, hint, issue }) => {
    const words = label.split(' ');
    const last = words.pop();
    return (
        <div className="min-w-0">
            <div className={LABEL}>
                {hint ? (
                    <>
                        {words.length > 0 && <label htmlFor={htmlFor}>{`${words.join(' ')} `}</label>}
                        <span className="whitespace-nowrap">
                            <label htmlFor={htmlFor}>{last}</label>
                            <InfoHint side="left" text={hint} className="ml-1.5 align-middle" />
                        </span>
                    </>
                ) : <label htmlFor={htmlFor}>{label}</label>}
            </div>
            {issue && <div className={ISSUE}>{issue}</div>}
        </div>
    );
};

/* Степпер как UIStepper: «−» и «+» по краям, число посередине можно набрать —
   только цифрами (CountInput). Ширина числа одна у всех, поэтому степперы
   стоят ровным столбиком. */
const NumberRow = ({ field, value, issue, onChange }) => {
    const id = useId();
    const [editing, setEditing] = useState(false);
    const { rejected, inputProps, setValue } = useCountInput(onChange);
    const [min, max] = SETTINGS_LIMITS[field.name];
    const number = parseCount(value);
    const known = Number.isInteger(number);
    const step = (delta) => setValue(known ? Math.min(max, Math.max(min, number + delta)) : min);
    // Пока поле в работе, «Укажите число» не загорается: человек стёр, чтобы набрать новое.
    const shown = rejected ? COUNT_HINT : (editing ? '' : issue);
    const invalid = Boolean(shown);
    return (
        <>
            <RowLabel htmlFor={id} label={field.label} hint={field.hint} issue={shown} />
            <div className={`inline-flex shrink-0 items-center rounded-xl bg-slate-100 p-0.5 transition ${
                invalid ? 'ring-2 ring-rose-300' : 'focus-within:ring-2 focus-within:ring-blue-500/60'}`}>
                <button type="button" className={STEP} aria-label={`${field.label}: меньше`}
                        disabled={known && number <= min} onClick={() => step(-1)}>
                    <Minus size={15} strokeWidth={2.4} className={wIcon} />
                </button>
                <input id={id} {...inputProps} value={value}
                       aria-invalid={invalid || undefined}
                       onFocus={(event) => { setEditing(true); event.target.select(); }}
                       onBlur={() => setEditing(false)}
                       className={`w-14 border-0 bg-transparent p-0 text-center text-[15px] font-semibold tabular-nums focus:outline-none focus:ring-0 xl:w-16 2xl:w-20 2xl:text-[17px] ${
                           invalid ? 'text-rose-600' : 'text-slate-900'}`} />
                <button type="button" className={STEP} aria-label={`${field.label}: больше`}
                        disabled={known && number >= max} onClick={() => step(1)}>
                    <Plus size={15} strokeWidth={2.4} className={wIcon} />
                </button>
            </div>
        </>
    );
};

const WaterSettings = ({ apiBaseUrl, headers, preview = null, onSaved, showToast }) => {
    const [loaded, setLoaded] = useState(null);
    const [draft, setDraft] = useState(null);
    const [candidates, setCandidates] = useState([]);
    const [headIds, setHeadIds] = useState([]);
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);
    // Новое поколение полей после сохранения и отмены: «Только цифры» и
    // «поле в работе» живут в самих строках и уходят вместе с черновиком.
    const [generation, setGeneration] = useState(0);
    const recipientsId = useId();

    useEffect(() => {
        axios.get(`${apiBaseUrl}/api/water/settings`, { headers: headers() })
            .then((response) => {
                const settings = response.data?.settings || {};
                setLoaded(settings);
                setDraft(settings);
                setCandidates(response.data?.candidates || []);
                setHeadIds(response.data?.head_ids || []);
            })
            .catch((requestError) => setError(requestError?.response?.data?.error || 'Не удалось загрузить настройки'));
    }, [apiBaseUrl, headers]);

    const shownTariffs = (draft || preview)?.tariffs;
    const tariffs = useMemo(() => {
        const extra = (shownTariffs || []).filter((code) => !KNOWN_TARIFFS.includes(code));
        return [...KNOWN_TARIFFS, ...extra];
    }, [shownTariffs]);

    if (error) return <div className={`text-[13px] text-rose-600 ${T.body}`}>{error}</div>;

    /* Пока едут настройки, вкладка сразу рисуется готовой — по условиям из
       /ping, которые панель уже знает: числа и тарифы те же, недостаёт только
       списка получателей. Раньше она открывалась строкой «Загружаем
       настройки…» и через 0,4 с перестраивалась в форму (замер 21.10.2026);
       такие перестройки владелец называет «микро пролаг» (13.10.2026). Править
       можно только настоящий черновик: до ответа строки не нажимаются, а поле
       получателей стоит пустым — не «никто», чтобы не показать неверного. */
    const view = draft || preview;
    const ready = Boolean(draft);
    if (!view) {
        return (
            <div className={`flex items-center justify-center gap-2 py-10 text-[13px] text-slate-500 ${T.body}`}>
                <Loader2 size={15} className={`animate-spin ${wIcon}`} /> Загружаем настройки…
            </div>
        );
    }

    const set = (name, value) => {
        if (ready) setDraft((prev) => ({ ...prev, [name]: value }));
    };
    const parsed = Object.fromEntries(NUMBER_NAMES.map((name) => [name, parseCount(view[name])]));
    const issues = settingsIssues(view);
    const invalid = Object.keys(issues).length > 0;
    // Сравниваем значения, а не строки: набранное «10» поверх 10 — не правка,
    // и тариф, снятый и отмеченный снова, — тоже.
    const sameSet = (a, b) => JSON.stringify([...(a || [])].map(String).sort())
        === JSON.stringify([...(b || [])].map(String).sort());
    const dirty = ready && (NUMBER_NAMES.some((name) => parsed[name] !== parseCount(loaded[name]))
        || !sameSet(draft.tariffs, loaded.tariffs)
        || !sameSet(draft.notify_user_ids, loaded.notify_user_ids));

    const chosen = view.tariffs || [];
    const toggleTariff = (code) => set('tariffs', chosen.includes(code)
        ? chosen.filter((item) => item !== code)
        : [...chosen, code]);

    const reset = () => {
        setDraft(loaded);
        setGeneration((value) => value + 1);
    };

    const save = async () => {
        if (!ready || invalid || saving) return;
        setSaving(true);
        try {
            const response = await axios.put(`${apiBaseUrl}/api/water/settings`, {
                ...parsed, tariffs: draft.tariffs, notify_user_ids: draft.notify_user_ids || [],
            }, { headers: headers() });
            const settings = response.data?.settings || draft;
            setLoaded(settings);
            setDraft(settings);
            setGeneration((value) => value + 1);
            onSaved?.(settings);
            showToast?.('Настройки сохранены', 'success');
        } catch (requestError) {
            showToast?.(requestError?.response?.data?.error || 'Не удалось сохранить', 'error');
        } finally {
            setSaving(false);
        }
    };

    const recipients = ready ? (draft.notify_user_ids || []) : [];
    const heads = candidates.filter((person) => headIds.includes(person.id));
    // До ответа — неразрывный пробел: пустое поле того же роста.
    const nobody = !ready ? '\u00a0' : heads.length
        ? `Глава фронт-офисов — ${heads.map((person) => person.name).join(', ')}`
        : 'Никто — сообщение не уйдёт';
    const stamp = loaded || preview;

    const numberCell = (field, index) => (
        <div key={`${field.name}-${generation}`} className={CELL}>
            <div className={`${ROW} ${rowEdge(index)}`} style={NOWRAP}>
                <NumberRow field={field} value={view[field.name] ?? ''} issue={issues[field.name]}
                           onChange={(value) => set(field.name, value)} />
            </div>
        </div>
    );

    return (
        /* Ниже xl — одна колонка, как список настроек на телефоне. С xl
           карточки во всю ширину раздела, строки в них — в две колонки. */
        <div className={`max-w-2xl space-y-5 xl:max-w-none xl:space-y-6 2xl:space-y-7 ${ready ? '' : 'pointer-events-none'}`}
             aria-busy={!ready || undefined}>
            <Section title="Право на воду">
                <div className={`${iosCard} grid xl:grid-cols-2`}>
                    {PROGRAM_FIELDS.map(numberCell)}
                </div>
            </Section>

            <Section title="Тарифы программы" issue={issues.tariffs}
                     info="Вода положена водителю, у машины которого есть хотя бы один из отмеченных тарифов">
                {/* С xl края плиток стоят там же, где текст строк в карточках выше и ниже. */}
                <div className={`${iosCard} grid grid-cols-2 gap-2 p-3 xl:grid-cols-4 xl:gap-2.5 xl:p-5 2xl:grid-cols-6 2xl:gap-3 2xl:p-6`}>
                    {tariffs.map((code) => {
                        const on = chosen.includes(code);
                        return (
                            <button key={code} type="button" onClick={() => toggleTariff(code)} aria-pressed={on} style={NOWRAP}
                                    className={`${TILE} ${on
                                        ? 'bg-blue-50 text-blue-700 ring-blue-200'
                                        : 'bg-white text-slate-700 ring-slate-200/80 hover:bg-slate-50'}`}>
                                <span className="truncate">{tariffLabel(code)}</span>
                                {on ? (
                                    <span className="grid size-5 shrink-0 place-items-center rounded-full bg-blue-600 text-white 2xl:size-6">
                                        <Check size={13} strokeWidth={3} className="2xl:size-[15px]" />
                                    </span>
                                ) : (
                                    <span className="size-5 shrink-0 rounded-full ring-1 ring-inset ring-slate-300 2xl:size-6" />
                                )}
                            </button>
                        );
                    })}
                </div>
            </Section>

            <Section title="Пороги остатка"
                     info="Общие для всех офисов. Свой порог офиса задаётся в его карточке во вкладке «Остатки»">
                <div className={`${iosCard} grid xl:grid-cols-2`}>
                    {THRESHOLD_FIELDS.map(numberCell)}
                    {/* Получатели — во всю ширину: список встаёт под правую
                        колонку, вровень с полями порогов над ним. */}
                    <div className={`${CELL} xl:col-span-2`}>
                        <div className="flex flex-col gap-2 border-t border-slate-100 py-3 xl:flex-row xl:items-center xl:justify-between xl:gap-4 xl:py-3.5 2xl:py-4">
                            <RowLabel htmlFor={recipientsId} label="Кому писать о закупке в Telegram"
                                      hint="Сообщение уходит, когда остаток офиса опускается до порога «Требуется закупка». Пока никто не выбран, его получает глава фронт-офисов. Кому не привязан Telegram, сообщение не придёт" />
                            <div className="min-w-0 xl:w-1/2 xl:pl-5 2xl:pl-6">
                                <CustomSelect
                                    id={recipientsId}
                                    multiple
                                    searchable
                                    variant="ios"
                                    roomy
                                    textClassName={`text-[14px] font-medium text-slate-800 ${T.body}`}
                                    /* CustomSelect в режиме multiple держит ключи строками —
                                       сравниваем строками, храним числами (как SessionModal). */
                                    value={recipients.map(String)}
                                    onChange={(value) => set('notify_user_ids', (value || []).map(Number))}
                                    options={candidates.map((person) => ({
                                        value: person.id,
                                        label: person.name,
                                        meta: person.has_telegram ? (person.city || '') : 'нет Telegram',
                                        muted: !person.has_telegram,
                                    }))}
                                    placeholder={nobody}
                                    renderValue={(values) => (values.length
                                        ? candidates.filter((person) => values.map(String).includes(String(person.id)))
                                            .map((person) => person.name).join(', ')
                                        : nobody)}
                                    ariaLabel="Кому писать о закупке в Telegram"
                                />
                            </div>
                        </div>
                    </div>
                </div>
            </Section>

            {/* Как в настройках macOS: кто и когда менял — слева, действие — справа. */}
            <div className="flex flex-col-reverse gap-3 pt-1 xl:flex-row xl:items-center xl:justify-between">
                <span className={`${TEXT_INSET} text-[12px] text-slate-400 ${T.sub}`}>
                    {stamp?.updated_by_name && `Изменил ${stamp.updated_by_name}, ${fmtDateTime(stamp.updated_at)}`}
                </span>
                <div className="flex gap-2 2xl:gap-3">
                    {dirty && (
                        <button type="button" className={`${wBtnSecondary} flex-1 xl:flex-none`} disabled={saving} onClick={reset}>
                            Отменить
                        </button>
                    )}
                    <button type="button" className={`${wBtnPrimary} flex-1 xl:flex-none 2xl:!px-8`} disabled={!dirty || invalid || saving} onClick={save}>
                        {saving && <Loader2 size={15} className={`animate-spin ${wIcon}`} />}
                        Сохранить
                    </button>
                </div>
            </div>
        </div>
    );
};

export default WaterSettings;
