import React, { useEffect, useId, useState } from 'react';
import axios from 'axios';
import { Check, Loader2 } from 'lucide-react';
import InfoHint from '../common/InfoHint';
import CustomSelect from '../ui/CustomSelect';
import { iosCard } from '../ui/ios';
import { fmtDateTime } from '../parcels/parcelMeta';
import { COUNT_HINT, useCountInput } from './CountInput';
import WaterAccess from './WaterAccess';
import {
    KNOWN_TARIFFS, SETTINGS_LIMITS, parseCount, settingsDirty, settingsIssues, stepCount, tariffLabel, visibleIssues,
} from './waterMeta';
import { T, WaterStepper, wBtnPrimary, wBtnSecondary, wIcon, wLabel } from './waterUi';

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
 *   — числа — степпером раздела «− 10 +» (WaterStepper, тот же, что в
 *     «Выдаче»), набрать с клавиатуры тоже можно;
 *   — пояснение — под «i» у подписи; под подписью бывает только то, что надо
 *     исправить, красным;
 *   — тарифы — ровная сетка одинаковых плиток; выбранная — мягко-голубая с
 *     галочкой, а не чёрная (владелец 16.10.2026: чёрный выбор «выглядит плохо»).
 *
 * Первой группой — «Выдача воды»: строка-кнопка «Доступ по офисам» (владелец
 * 23.10.2026, WaterAccess.jsx). Она сохраняется своим окном, а не кнопкой
 * «Сохранить» внизу, поэтому стоит вне формы условий и её замка.
 */

const PROGRAM_FIELDS = [
    { name: 'min_trips', label: 'Поездок для выдачи за активность',
      hint: 'Считаются поездки с прошлой выдачи, а в первый раз — за последние 7 дней. 0 — поездки не требуются' },
    { name: 'cooldown_days', label: 'Дней между выдачами',
      hint: 'Следующая выдача за активность — не раньше, чем через столько дней после прошлой. '
          + '0 — без ограничения по дням' },
    { name: 'welcome_blocks', label: 'Блоков в приветственной выдаче' },
    { name: 'activity_blocks', label: 'Блоков за активность' },
];

const THRESHOLD_FIELDS = [
    { name: 'low_threshold', label: 'Низкий остаток',
      hint: 'Когда в офисе остаётся столько блоков или меньше, он помечается «Низкий остаток»' },
    { name: 'buy_threshold', label: 'Требуется закупка',
      hint: 'Когда в офисе остаётся столько блоков или меньше, он помечается «Требуется закупка»' },
];

const NUMBER_NAMES = [...PROGRAM_FIELDS, ...THRESHOLD_FIELDS].map((field) => field.name);

/* Когда и кому — как в water/notify.py и queries.notify_recipients: один раз
   на пересечении порога (следующие выдачи ниже него молчат); выбранным, у кого
   есть Telegram, а нет таких — главе фронт-офисов. */
const RECIPIENTS_HINT = 'Сообщение уходит один раз — когда остаток офиса опускается до порога «Требуется закупка». '
    + 'Если никто не выбран или ни у кого из выбранных нет Telegram, его получает глава фронт-офисов';

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

/* На телефоне плиток две в ряд, и «Личный водитель» кеглем 13,5 обрезался
   многоточием на пару пикселей — там кегль 13. */
const TILE = 'flex min-h-[44px] items-center justify-between gap-1.5 rounded-xl px-3 py-2 text-left '
    + 'text-[13px] font-medium ring-1 transition active:scale-[0.97] '
    + 'xl:min-h-[48px] xl:gap-2 xl:px-3.5 xl:text-[14px] 2xl:min-h-[56px] 2xl:rounded-[14px] 2xl:px-4 2xl:text-[15px]';

/* Подпись группы, «Изменил…» и красная строка под карточкой начинаются там же,
   где текст строк. У wLabel свой px-1 — его обёртке на 4 px меньше. */
const TEXT_INSET = 'px-4 xl:px-5 2xl:px-6';
const LABEL_INSET = 'px-3 xl:px-4 2xl:px-5';

const Section = ({ title, info, issue, issueId, children }) => (
    <section className="space-y-1.5 2xl:space-y-2">
        <div className={`flex items-center gap-1.5 ${LABEL_INSET}`}>
            <span className={wLabel}>{title}</span>
            {info && <InfoHint side="left" text={info} />}
        </div>
        {children}
        {issue && <div id={issueId} className={`${TEXT_INSET} ${ISSUE}`}>{issue}</div>}
    </section>
);

/* «i» стоит сразу за последним словом подписи и переносится вместе с ним:
   отдельным флекс-элементом она на телефоне падала третьей строкой под
   подпись в две строки. Сама «i» — вне <label>: нажатие на неё открывает
   подсказку, а не ставит курсор в поле. */
const RowLabel = ({ htmlFor, label, hint, issue, issueId }) => {
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
            {issue && <div id={issueId} className={ISSUE}>{issue}</div>}
        </div>
    );
};

/* Число набирается только цифрами (CountInput), шагает степпером в пределах
   сервера. Ширина числа одна у всех — степперы стоят ровным столбиком; её
   хватает на шесть цифр (порог до 100 000). Какое поле в работе, знает экран:
   от этого зависят ошибки и соседней строки. */
const NumberRow = ({ field, value, issue, onChange, onFocusChange }) => {
    const id = useId();
    const { rejected, inputProps, setValue, clearRejected } = useCountInput(onChange);
    const [min, max] = SETTINGS_LIMITS[field.name];
    const number = parseCount(value);
    const known = Number.isInteger(number);
    const shown = rejected ? COUNT_HINT : issue;
    const invalid = Boolean(shown);
    const issueId = `${id}-issue`;
    return (
        <>
            <RowLabel htmlFor={id} label={field.label} hint={field.hint} issue={shown} issueId={issueId} />
            <WaterStepper label={field.label} invalid={invalid}
                          downDisabled={known && number <= min} upDisabled={known && number >= max}
                          onStep={(delta) => setValue(stepCount(value, delta, [min, max]))}>
                <input id={id} {...inputProps} value={value}
                       aria-invalid={invalid || undefined}
                       aria-describedby={invalid ? issueId : undefined}
                       onFocus={(event) => { onFocusChange(true); event.target.select(); }}
                       onBlur={() => { clearRejected(); onFocusChange(false); }}
                       className={`w-16 border-0 bg-transparent p-0 text-center text-[15px] font-semibold tabular-nums focus:outline-none focus:ring-0 2xl:w-20 2xl:text-[18px] ${
                           invalid ? 'text-rose-600' : 'text-slate-900'}`} />
            </WaterStepper>
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
    // Поле, в котором сейчас набирают: пока оно в работе, часть ошибок молчит.
    const [focused, setFocused] = useState(null);
    const recipientsId = useId();
    const tariffsIssueId = useId();

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
    // Пока идёт сохранение, править тоже нельзя: ответ сервера заменяет
    // черновик, и правка, сделанная в эти полсекунды, молча пропадала бы.
    const locked = !ready || saving;
    if (!view) {
        return (
            <div className={`flex items-center justify-center gap-2 py-10 text-[13px] text-slate-500 ${T.body}`}>
                <Loader2 size={15} className={`animate-spin ${wIcon}`} /> Загружаем настройки…
            </div>
        );
    }

    const set = (name, value) => {
        if (!locked) setDraft((prev) => ({ ...prev, [name]: value }));
    };
    const parsed = Object.fromEntries(NUMBER_NAMES.map((name) => [name, parseCount(view[name])]));
    const issues = settingsIssues(view);
    const invalid = Object.keys(issues).length > 0;
    const shownIssues = visibleIssues(issues, view, focused);
    const dirty = ready && settingsDirty(draft, loaded);

    // Свой код тарифа (не из справочника) остаётся плиткой и после снятия —
    // иначе его не отметить снова.
    const extra = [...new Set([...(loaded?.tariffs || []), ...(preview?.tariffs || []), ...(view.tariffs || [])])]
        .filter((code) => !KNOWN_TARIFFS.includes(code));
    const tariffs = [...KNOWN_TARIFFS, ...extra];
    const chosen = view.tariffs || [];
    const toggleTariff = (code) => set('tariffs', chosen.includes(code)
        ? chosen.filter((item) => item !== code)
        : [...chosen, code]);

    // Строки пересоздаются (generation), и поле, бывшее в работе, — тоже.
    const renew = (settings) => {
        setDraft(settings);
        setFocused(null);
        setGeneration((value) => value + 1);
    };
    const reset = () => renew(loaded);

    const save = async () => {
        if (!ready || invalid || saving) return;
        setSaving(true);
        try {
            const response = await axios.put(`${apiBaseUrl}/api/water/settings`, {
                ...parsed, tariffs: draft.tariffs, notify_user_ids: draft.notify_user_ids || [],
            }, { headers: headers() });
            const settings = response.data?.settings || draft;
            setLoaded(settings);
            renew(settings);
            onSaved?.(settings);
            showToast?.('Настройки сохранены', 'success');
        } catch (requestError) {
            showToast?.(requestError?.response?.data?.error || 'Не удалось сохранить', 'error');
        } finally {
            setSaving(false);
        }
    };

    const recipients = ready ? (draft.notify_user_ids || []) : [];
    // Никто не выбран — пишем главе фронт-офисов, но только тому, у кого есть
    // Telegram (queries.notify_recipients). До ответа — неразрывный пробел:
    // пустое поле того же роста.
    const heads = candidates.filter((person) => headIds.includes(person.id) && person.has_telegram);
    const nobody = !ready ? '\u00a0' : heads.length
        ? `Глава фронт-офисов — ${heads.map((person) => person.name).join(', ')}`
        : 'Никто — сообщение не уйдёт';
    const stamp = loaded || preview;

    const numberCell = (field, index) => (
        <div key={`${field.name}-${generation}`} className={CELL}>
            <div className={`${ROW} ${rowEdge(index)}`} style={NOWRAP}>
                <NumberRow field={field} value={view[field.name] ?? ''} issue={shownIssues[field.name]}
                           onChange={(value) => set(field.name, value)}
                           onFocusChange={(on) => setFocused((current) => (on ? field.name : (current === field.name ? null : current)))} />
            </div>
        </div>
    );

    return (
        /* Ниже xl — одна колонка, как список настроек на телефоне. С xl
           карточки во всю ширину раздела, строки в них — в две колонки.
           Доступ по офисам — над формой и вне её замка: пока условия грузятся
           или сохраняются, строка доступа работает. */
        <div className="max-w-2xl space-y-5 xl:max-w-none xl:space-y-6 2xl:space-y-7">
            <WaterAccess apiBaseUrl={apiBaseUrl} headers={headers} showToast={showToast} />
            <div className={`space-y-5 xl:space-y-6 2xl:space-y-7 ${locked ? 'pointer-events-none' : ''}`}
                 aria-busy={locked || undefined}>
                <Section title="Право на воду">
                    <div className={`${iosCard} grid xl:grid-cols-2`}>
                        {PROGRAM_FIELDS.map(numberCell)}
                    </div>
                </Section>

                <Section title="Тарифы программы" issue={issues.tariffs} issueId={tariffsIssueId}
                         info="Вода положена водителю, у машины которого есть хотя бы один из отмеченных тарифов">
                    {/* Две плитки в ряд на телефоне, три — с 640 px (телефон боком,
                        планшет: по две выходили полосами в 400 px), четыре с xl, шесть
                        на большом экране. С xl края плиток стоят там же, где текст
                        строк в карточках выше и ниже. */}
                    <div role="group" aria-label="Тарифы программы" aria-describedby={issues.tariffs ? tariffsIssueId : undefined}
                         className={`${iosCard} grid grid-cols-2 gap-2 p-3 sm:grid-cols-3 xl:grid-cols-4 xl:gap-2.5 xl:p-5 2xl:grid-cols-6 2xl:gap-3 2xl:p-6`}>
                        {tariffs.map((code) => {
                            const on = chosen.includes(code);
                            return (
                                <button key={code} type="button" onClick={() => toggleTariff(code)} aria-pressed={on} style={NOWRAP}
                                        title={tariffLabel(code)}
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
                                          hint={RECIPIENTS_HINT} />
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
        </div>
    );
};

export default WaterSettings;
