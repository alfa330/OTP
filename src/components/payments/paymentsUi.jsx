import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Check, Columns3, Copy, CreditCard, Download, Eye, EyeOff, FileText, Loader2, Paperclip, RefreshCcw, ShoppingCart, Trash2,
    UsersRound, X,
} from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import { iosBtnGhost, iosBtnSecondary, iosGroupLabel, iosInput, IosSegmented } from '../ui/ios';
import InfoHint from '../common/InfoHint';
import {
    ATTACHMENT_LABELS, REQUEST_FORMS, approvalSummary, cardNumberLabel, dueChip, fileSizeLabel, fmtMoney, initialsOf,
    pageRange, parseAmount, plural, requestState, requestType, requisitesText, stateMeta, tonePill,
} from './paymentsMeta';

/*
 * Мелкие примитивы раздела «Оплата счетов», общие для формы, карточки и
 * справочников. Всё построено на примитивах ui/ios — своего визуального языка
 * раздел не заводит.
 */

/* Кнопка-поле выбора даты в формах раздела: тот же вид, что у полей ввода. */
export const DATE_TRIGGER = 'flex w-full items-center gap-2 rounded-xl bg-slate-100 px-3.5 py-2.5 '
    + 'text-[14px] tabular-nums text-slate-900 border-0 transition hover:bg-slate-200/70 '
    + 'focus:outline-none focus:ring-2 focus:ring-blue-500/70 [&>span]:flex-1 [&>span]:text-left';

/* Пояснение к выбору под «i»: фраза о том, зачем поле, и по строке на вариант.
   Данные — из HINTS в paymentsMeta.js: { intro, options: [[вариант, смысл]], outro }. */
const ChoiceHint = ({ intro, options = [], outro }) => (
    <div className="space-y-1.5">
        {intro && <div>{intro}</div>}
        {options.length > 0 && (
            <div className="space-y-1">
                {options.map(([name, meaning]) => (
                    <div key={name}>
                        <span className="font-semibold text-slate-800">{name}</span> — {meaning}
                    </div>
                ))}
            </div>
        )}
        {outro && <div className="text-slate-500">{outro}</div>}
    </div>
);

/* «i» у подписи: строка, готовый узел или описание из HINTS.
   Пузырь раскрывается вправо от значка (`side="left"`): подпись стоит у левого
   края поля, и раскрытый влево пузырь вылезал за окно формы. */
export const FieldHint = ({ hint }) => {
    if (!hint) return null;
    if (typeof hint === 'string') return <InfoHint side="left" text={hint} />;
    if (React.isValidElement(hint)) return <InfoHint side="left">{hint}</InfoHint>;
    return <InfoHint side="left"><ChoiceHint {...hint} /></InfoHint>;
};

/* Подпись поля. Пояснение — под «i» у метки, а не строкой под полем (решение
   владельца 27.08.2026): текст нужен один раз, когда человек не понял поле.
   `as="div"` — для полей с несколькими элементами управления внутри (сегменты,
   файлы): <label> вокруг нескольких кнопок отдавал бы щелчок по подписи первой.
   Строка подписи одной высоты с «i» и без него (`min-h-5` — высота кнопки
   InfoHint): иначе в ряду из двух полей то, что с подсказкой, стояло на
   несколько пикселей ниже соседнего. */
export const Field = ({ label, hint, required = false, optionalMark = true, children, className = '', as = 'label' }) => {
    const Tag = as;
    return (
        <Tag className={`block space-y-1.5 ${className}`}>
            <span className="flex min-h-5 items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                {label}
                {!required && optionalMark && (
                    <span className="font-normal normal-case tracking-normal text-slate-400">необязательно</span>
                )}
                <FieldHint hint={hint} />
            </span>
            {children}
        </Tag>
    );
};

/* Выбор из двух-трёх вариантов — сегментами: все варианты видны сразу, щелчок
   один. Так выбираются «С НДС / Без НДС», «Нужна / Не нужна», «ТОО / ИП»
   (решение владельца 06.10.2026: такие варианты выбирают, а не пишут словами и
   не отмечают галочкой). Значение `null` — «ещё не выбрано»: ни один сегмент не
   подсвечен, и молчаливого «нет» по умолчанию не бывает. */
export const Choice = ({ value, onChange, options, ariaLabel, stretch = false, disabled = false }) => (
    <div className={`${stretch ? '' : 'inline-flex max-w-full'} ${disabled ? 'pointer-events-none opacity-60' : ''}`}>
        <IosSegmented value={value} options={options} onChange={onChange} ariaLabel={ariaLabel} stretch={stretch} />
    </div>
);

/* ── Заявка в списке: карточка доски, строка «Моих задач», карточка реестра ──
   Детали — те же, что у карточек раздела «Задачи» (TaskBoardWorkspace.jsx):
   метка-чип с тонким кольцом, флажок срока, кружки с инициалами. Цвет у чипа —
   только когда он что-то сообщает (срок подходит, прошёл). */

const CHIP_TONE = {
    overdue: 'bg-rose-50 text-rose-600 ring-rose-100',
    soon: 'bg-amber-50 text-amber-700 ring-amber-100',
    normal: 'bg-slate-100 text-slate-500 ring-transparent',
    done: 'bg-slate-100 text-slate-400 ring-transparent',
};

export const MetaChip = ({ tone = 'normal', icon: Icon, title, children }) => (
    <span title={title} className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-[2px] text-[11px] font-medium tabular-nums ring-1 ${CHIP_TONE[tone] || CHIP_TONE.normal}`}>
        {Icon && <Icon size={10} strokeWidth={2.25} aria-hidden="true" />}
        {children}
    </span>
);

/* Флажок срока — тот же рисунок, что у «Задач». */
const FlagIcon = () => (
    <svg width="10" height="10" viewBox="0 0 12 12" fill="none" aria-hidden="true">
        <path d="M3 10.5V2m0 0h6l-1.3 2L9 6H3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
);

/* Срок заявки; у оплаченной — день оплаты. Нет срока — нет и метки. */
export const DueChip = ({ request, soonDays }) => {
    const chip = dueChip(request, soonDays ? { soonDays } : undefined);
    if (!chip) return null;
    return (
        <MetaChip tone={chip.tone} title={chip.title}>
            {chip.icon === 'check' ? <Check size={10} strokeWidth={2.5} aria-hidden="true" /> : <FlagIcon />}
            {chip.label}
        </MetaChip>
    );
};

/* Метка типа заявки — цвет и значок те же, что у полосы на карточке. Значки — как у
   выбора типа в «Создать заявку»: тележка — закуп, стрелки по кругу — регулярный. */
const TYPE_ICONS = { purchase: ShoppingCart, card: CreditCard, regular: RefreshCcw };

export const TypePill = ({ request }) => {
    const type = requestType(request);
    const Icon = TYPE_ICONS[type.key];
    return (
        <span className={`inline-flex min-w-0 items-center gap-1 rounded-md px-1.5 py-[2px] text-[11px] font-semibold ${type.pill}`} title={`Тип заявки: ${type.label}`}>
            <Icon size={11} strokeWidth={2.25} className="shrink-0" aria-hidden="true" />
            <span className="truncate">{type.label}</span>
        </span>
    );
};

/* Рамка карточки заявки — на доске, в окне колонки и в реестре на телефоне:
   сплошная граница с лёгкой тенью и цветная полоса типа у левого края, внутри
   карточки, как метка события в «Календаре». `type` — из requestType(). */
export const requestCardFrame = (type) => 'relative rounded-xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.06)] '
    + 'before:pointer-events-none before:absolute before:bottom-2.5 before:left-1.5 before:top-2.5 before:w-[3px] before:rounded-full '
    + type.bar;

/* Кружок человека: инициалы на сером, как на карточках «Задач». Подразделение —
   значком, а не буквой. */
export const Face = ({ person, size = 20 }) => (
    <span
        title={person?.title || person?.name || ''}
        className="inline-grid shrink-0 place-items-center overflow-hidden rounded-full bg-slate-200 font-semibold text-slate-600"
        style={{ width: size, height: size, fontSize: Math.max(9.5, Math.round(size * 0.36 * 10) / 10) }}
    >
        {person?.role ? <UsersRound size={Math.round(size * 0.55)} strokeWidth={2} aria-hidden="true" /> : initialsOf(person?.name)}
    </span>
);

const FlowArrow = () => (
    <svg width="9" height="9" viewBox="0 0 10 10" fill="none" aria-hidden="true" className="shrink-0 text-slate-300">
        <path d="M1.6 5h6.4M5.8 2.6 8.2 5 5.8 7.4" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
);

/* «Инициатор → у кого этап». Имена — в подсказках (и для читалки — в aria-label). */
export const CardFaces = ({ faces }) => {
    if (!faces?.from && !faces?.to) return null;
    const label = [faces.from?.title, faces.to?.title].filter(Boolean).join(' → ');
    return (
        <span className="flex shrink-0 items-center gap-0.5" title={label} aria-label={label}>
            {faces.from && <Face person={faces.from} />}
            {faces.from && faces.to && <FlowArrow />}
            {faces.to && <Face person={faces.to} />}
        </span>
    );
};

/* Листалка списка — как у раздела «Задачи»: «Показаны 1–20 из 75» и стрелки
   страниц. Одна страница — просто «12 заявок», без стрелок; `hideSingle` убирает
   и её там, где число уже стоит рядом (счётчик вкладки, полоса-легенда). */
const PAGER_BUTTON = 'grid h-7 w-7 place-items-center rounded-lg text-[13px] text-slate-500 transition hover:bg-slate-200/70 '
    + 'hover:text-slate-800 disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent';

export const Pager = ({ page, pageSize, total, loading = false, onPage, forms = REQUEST_FORMS, hideSingle = false }) => {
    if (!total) return null;
    const range = pageRange(page, pageSize, total);
    const single = range.totalPages <= 1;
    if (single && hideSingle) return null;
    return (
        <div className="flex flex-wrap items-center justify-between gap-2 px-1" data-pager>
            <span className="text-[11.5px] text-slate-400">
                {single
                    ? plural(total, forms)
                    : <>Показаны <b className="font-semibold tabular-nums text-slate-500">{range.from}–{range.to}</b> из <b className="font-semibold tabular-nums text-slate-500">{total}</b></>}
                {loading && <span className="ml-2 text-slate-300">обновляю…</span>}
            </span>
            {!single && (
                <span className="flex items-center gap-1">
                    <button type="button" disabled={range.page <= 1 || loading} onClick={() => onPage(range.page - 1)} className={PAGER_BUTTON} aria-label="Предыдущая страница">‹</button>
                    {/* Ширина — стилем: класс min-w-[…] оболочка телефона обнуляет. */}
                    <span className="text-center text-[11.5px] tabular-nums text-slate-500" style={{ minWidth: 54 }}>{range.page} / {range.totalPages}</span>
                    <button type="button" disabled={range.page >= range.totalPages || loading} onClick={() => onPage(range.page + 1)} className={PAGER_BUTTON} aria-label="Следующая страница">›</button>
                </span>
            )}
        </div>
    );
};

/* Строка «подпись — значение» в карточке. Пустое значение строку не рисует —
   включая `false`, которое отдаёт `{value && …}`. */
export const Row = ({ label, children, wide = false }) => {
    if (children === null || children === undefined || children === '' || children === false) return null;
    return (
        <div className="flex gap-3 py-1.5">
            <div className={`${wide ? 'w-[168px]' : 'w-[140px]'} shrink-0 text-[12.5px] text-slate-500`}>{label}</div>
            <div className="min-w-0 flex-1 whitespace-pre-line break-words text-[13.5px] text-slate-900">{children}</div>
        </div>
    );
};

export const StatePill = ({ request, className = '' }) => {
    const state = requestState(request);
    const meta = stateMeta(state);
    if (!meta.tone) return null;
    const pill = tonePill(meta.tone);
    return (
        <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[12px] font-medium ${pill.fill} ${className}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${pill.dot}`} />
            {meta.label}
        </span>
    );
};

/* Метка с точкой — статус справочной записи, закрывающих документов, имущества. */
export const TonePill = ({ tone = 'neutral', children, className = '' }) => {
    const pill = tonePill(tone);
    return (
        <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-[11.5px] font-medium ${pill.fill} ${className}`}>
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${pill.dot}`} />
            {children}
        </span>
    );
};

/* Этапы заявки одной строкой (ТЗ, п. 2): пройденные — зелёные, текущий — синий,
   будущие — серые. Этап, которого у заявки нет (учёт имущества у услуги), не
   рисуется вовсе: серая точка «пропущено» только отвлекала бы.
   Чёрточек между этапами нет: семь названий в строку не помещаются, и после
   переноса чёрточка повисала в начале второй строки. Порядок читается и так —
   слева направо, по цвету. */
export const Lifecycle = ({ stages = [], stopped = false }) => {
    const shown = stages.filter((stage) => stage.state !== 'skipped');
    return (
        <ol className="flex flex-wrap items-center gap-1.5">
            {shown.map((stage) => {
                const done = stage.state === 'done';
                const current = stage.state === 'current';
                const halted = current && stopped;
                return (
                    <li key={stage.code} className="flex">
                        <span
                            className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-[12px] ${
                                halted ? 'bg-slate-200 text-slate-600'
                                    : current ? 'bg-blue-600 font-medium text-white'
                                        : done ? 'bg-emerald-50 text-emerald-800'
                                            : 'bg-slate-100 text-slate-500'}`}
                        >
                            {done && <Check size={11} strokeWidth={3} className="text-emerald-600" />}
                            {stage.label}
                        </span>
                    </li>
                );
            })}
        </ol>
    );
};

/* Поле суммы: печатается как обычный текст, на потере фокуса приводится к
   формату «1 234,50». Число уходит наружу числом. */
export const AmountInput = ({ value, onChange, placeholder = '0', className = '', disabled = false, ariaLabel }) => {
    const [text, setText] = useState(() => (value === '' || value === null || value === undefined ? '' : fmtMoney(value, { currency: false })));
    const [focused, setFocused] = useState(false);
    const inputRef = useRef(null);
    const shown = focused ? text : (value === '' || value === null || value === undefined || value === 0 ? '' : fmtMoney(value, { currency: false }));
    /* При входе в поле сумма выделяется целиком: набранное заменяет прежнее
       значение, а не дописывается к нему. Выделяем в layout-эффекте — сразу
       после того, как в поле встал «сырой» текст и до первой нажатой клавиши;
       отложенное выделение (кадром позже) срабатывало посреди быстрого ввода и
       съедало уже набранные цифры. */
    useLayoutEffect(() => {
        if (focused) inputRef.current?.select();
    }, [focused]);
    return (
        <input
            ref={inputRef}
            type="text"
            inputMode="decimal"
            aria-label={ariaLabel}
            disabled={disabled}
            className={`${iosInput} tabular-nums ${className}`}
            placeholder={placeholder}
            value={shown}
            onFocus={() => { setFocused(true); setText(value ? String(value).replace('.', ',') : ''); }}
            onChange={(event) => { setText(event.target.value); onChange(parseAmount(event.target.value)); }}
            onBlur={() => setFocused(false)}
        />
    );
};

/* Количество: число, можно дробное («2,5»). При входе в поле значение
   выделяется целиком — иначе «4» дописывалось к подставленной «1» и выходило
   «14» (так и случалось на проверке). */
export const QuantityInput = ({ value, onChange, className = '', ariaLabel = 'Количество', invalid = false }) => (
    <input
        type="text"
        inputMode="decimal"
        aria-label={ariaLabel}
        aria-invalid={invalid || undefined}
        className={`${iosInput} tabular-nums ${invalid ? 'ring-2 ring-rose-300' : ''} ${className}`}
        value={value}
        onFocus={(event) => event.target.select()}
        onChange={(event) => onChange(event.target.value.replace(/[^\d.,]/g, ''))}
    />
);

/* Список в форме, рядом с полями ввода: тот же рост (41 px), отступ и кегль, что у
   iosInput. Сам CustomSelect по умолчанию рассчитан на панель фильтров (35 px,
   12.5 px) — в одном ряду формы такие поля стояли вразнобой. */
export const FORM_SELECT_TEXT = '!px-3.5 !py-2.5 text-[14px] text-slate-900';
export const FormSelect = (props) => <CustomSelect variant="ios" textClassName={FORM_SELECT_TEXT} {...props} />;

/* Выбор сотрудника — CustomSelect с поиском; варианты собираются один раз.
   `compact` — мелкий вид для строки, где рядом стоят такие же мелкие поля. */
export const UserSelect = ({ users, value, onChange, placeholder = 'Выберите сотрудника', exclude = [], ariaLabel, multiple = false, compact = false }) => {
    const options = useMemo(() => (users || [])
        .filter((user) => !exclude.includes(user.id))
        .map((user) => ({
            value: user.id,
            label: user.department_name ? `${user.name} · ${user.department_name}` : user.name,
        })), [users, exclude]);
    return (
        <CustomSelect
            value={value}
            onChange={onChange}
            options={options}
            placeholder={placeholder}
            variant="ios"
            textClassName={compact ? '' : FORM_SELECT_TEXT}
            searchable
            ariaLabel={ariaLabel || placeholder}
            multiple={multiple}
        />
    );
};

/* ── Файлы ─────────────────────────────────────────────────────────────────── */

const ACCEPT = '.pdf,.jpg,.jpeg,.png,.webp,.heic,.doc,.docx,.xls,.xlsx,.csv,.txt,.zip';

/* Очередь файлов ДО отправки: имя, размер, вид документа. Вид спрашивается у
   каждого файла, потому что от него зависит проверка шага («счёт приложен?»). */
export const FilePicker = ({ files, onChange, kinds, disabled = false, label = 'Приложить файлы' }) => {
    const inputRef = useRef(null);
    const defaultKind = kinds?.[0]?.value || 'other';
    const add = useCallback((list) => {
        const next = Array.from(list || []).map((file) => ({
            key: `${file.name}-${file.size}-${file.lastModified}-${Math.random().toString(36).slice(2, 7)}`,
            file,
            kind: defaultKind,
        }));
        if (next.length) onChange([...(files || []), ...next]);
    }, [defaultKind, files, onChange]);
    return (
        <div className="space-y-2">
            {(files || []).map((entry) => (
                // На телефоне список «Вид документа» уходит второй строкой: рядом с ним от
                // имени файла оставалось бы пять букв, и два файла было бы не различить.
                <div key={entry.key} className="flex flex-wrap items-center gap-2 rounded-xl bg-slate-100 px-3 py-2">
                    <FileText size={15} className="shrink-0 text-slate-400" />
                    <div className="min-w-0 grow basis-0">
                        <div className="truncate text-[13px] text-slate-800">{entry.file.name}</div>
                        <div className="text-[11.5px] text-slate-500">{fileSizeLabel(entry.file.size)}</div>
                    </div>
                    {kinds && kinds.length > 1 && (
                        <div className="order-last w-full shrink-0 sm:order-none sm:w-[190px]">
                            <CustomSelect
                                value={entry.kind}
                                onChange={(kind) => onChange(files.map((item) => (item.key === entry.key ? { ...item, kind } : item)))}
                                options={kinds}
                                variant="ios"
                                ariaLabel="Вид документа"
                            />
                        </div>
                    )}
                    <button
                        type="button"
                        aria-label="Убрать файл"
                        className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-700"
                        onClick={() => onChange(files.filter((item) => item.key !== entry.key))}
                    >
                        <X size={14} />
                    </button>
                </div>
            ))}
            <input
                ref={inputRef}
                type="file"
                multiple
                accept={ACCEPT}
                className="hidden"
                disabled={disabled}
                onChange={(event) => { add(event.target.files); event.target.value = ''; }}
            />
            <button
                type="button"
                disabled={disabled}
                className={`${iosBtnGhost} -ml-2`}
                onClick={() => inputRef.current?.click()}
            >
                <Paperclip size={14} />
                {label}
            </button>
        </div>
    );
};

export const downloadAttachment = async ({ apiBaseUrl, headers, attachment, inline = false }) => {
    const response = await axios.get(
        `${apiBaseUrl}/api/payments/attachments/${attachment.id}/download${inline ? '?inline=1' : ''}`,
        { headers: headers(), responseType: 'blob' },
    );
    const url = URL.createObjectURL(response.data);
    if (inline) {
        window.open(url, '_blank', 'noopener');
        setTimeout(() => URL.revokeObjectURL(url), 60000);
        return;
    }
    const link = document.createElement('a');
    link.href = url;
    link.download = attachment.file_name || 'file';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
};

const isViewable = (attachment) => /^(image\/|application\/pdf)/.test(attachment?.content_type || '');

/* Список сохранённых файлов заявки: вид, имя, кто и когда, скачать / снять. */
export const AttachmentList = ({ attachments, apiBaseUrl, headers, canRemove, onRemove, showToast, showStep = false }) => {
    const [busy, setBusy] = useState(null);
    if (!attachments || !attachments.length) return null;
    const open = async (attachment, inline) => {
        setBusy(attachment.id);
        try {
            await downloadAttachment({ apiBaseUrl, headers, attachment, inline });
        } catch (error) {
            showToast?.(error?.response?.data?.error || 'Не удалось скачать файл', 'error');
        } finally {
            setBusy(null);
        }
    };
    return (
        <div className="space-y-1">
            {attachments.map((attachment) => (
                <div key={attachment.id} className="flex items-center gap-2.5 rounded-xl px-2 py-1.5 transition hover:bg-slate-50">
                    <FileText size={15} className="shrink-0 text-slate-400" />
                    <button
                        type="button"
                        className="min-w-0 flex-1 text-left"
                        onClick={() => open(attachment, isViewable(attachment))}
                        title={isViewable(attachment) ? 'Открыть' : 'Скачать'}
                    >
                        <div className="truncate text-[13px] text-slate-900 underline decoration-transparent underline-offset-2 transition hover:decoration-slate-300">
                            {attachment.file_name}
                        </div>
                        <div className="truncate text-[11.5px] text-slate-500">
                            {ATTACHMENT_LABELS[attachment.kind] || attachment.kind}
                            {showStep && attachment.stage_label ? ` · ${attachment.stage_label}` : ''}
                            {' · '}{fileSizeLabel(attachment.file_size)}
                            {attachment.uploaded_by_name ? ` · ${attachment.uploaded_by_name}` : ''}
                        </div>
                    </button>
                    <button
                        type="button"
                        aria-label="Скачать"
                        className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                        disabled={busy === attachment.id}
                        onClick={() => open(attachment, false)}
                    >
                        {busy === attachment.id ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
                    </button>
                    {canRemove?.(attachment) && (
                        <button
                            type="button"
                            aria-label="Снять файл"
                            className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-rose-50 hover:text-rose-600"
                            onClick={() => onRemove?.(attachment)}
                        >
                            <Trash2 size={14} />
                        </button>
                    )}
                </div>
            ))}
        </div>
    );
};

/* Документы «под рукой» на шаге: короткие кнопки-файлы, чтобы тому, кто
   согласует или платит, не листать карточку до раздела «Документы». Это не
   второй список файлов, а только то, что нужно для решения на ЭТОМ шаге (счёт —
   тому, кто его проверяет; реестр поставщиков — тому, кто подтверждает закуп).
   Щелчок открывает PDF и картинку, остальное скачивает. */
export const FileChips = ({ attachments, apiBaseUrl, headers, showToast, label }) => {
    const [busy, setBusy] = useState(null);
    if (!attachments || !attachments.length) return null;
    const open = async (attachment) => {
        setBusy(attachment.id);
        try {
            await downloadAttachment({ apiBaseUrl, headers, attachment, inline: isViewable(attachment) });
        } catch (error) {
            showToast?.(error?.response?.data?.error || 'Не удалось открыть файл', 'error');
        } finally {
            setBusy(null);
        }
    };
    return (
        <div className="flex flex-wrap items-center gap-1.5">
            {label && <span className="text-[12.5px] text-slate-500">{label}</span>}
            {attachments.map((attachment) => (
                <button
                    key={attachment.id}
                    type="button"
                    onClick={() => open(attachment)}
                    title={`${ATTACHMENT_LABELS[attachment.kind] || 'Файл'}: ${attachment.file_name}`}
                    className="inline-flex max-w-[280px] items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-[12.5px] text-slate-700 transition hover:bg-slate-200 active:scale-[0.98]"
                >
                    {busy === attachment.id
                        ? <Loader2 size={12} className="shrink-0 animate-spin text-slate-400" />
                        : <FileText size={12} className="shrink-0 text-slate-400" />}
                    <span className="truncate">{attachment.file_name}</span>
                </button>
            ))}
        </div>
    );
};

/* «Скопировать»: реквизиты из шага 5 инициатор пересылает поставщику — одним
   щелчком, а не выделением текста мышью. */
const ICON_ACTION = 'inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md transition hover:bg-blue-50';

/* `iconOnly` — одним значком, подпись в подсказке: для мест, где слову тесно. */
export const CopyButton = ({ text, label = 'Скопировать', iconOnly = false }) => {
    const [done, setDone] = useState(false);
    const copy = async () => {
        try {
            await navigator.clipboard.writeText(String(text || ''));
            setDone(true);
            setTimeout(() => setDone(false), 1600);
        } catch { /* буфер недоступен — текст можно выделить руками */ }
    };
    if (iconOnly) {
        return (
            <button type="button" onClick={copy} title={done ? 'Скопировано' : label} aria-label={label} className={`${ICON_ACTION} ${done ? 'text-emerald-600' : 'text-blue-600'}`}>
                {done ? <Check size={13} /> : <Copy size={13} />}
            </button>
        );
    }
    return (
        <button type="button" onClick={copy} className={`${iosBtnGhost} -mr-2 ${done ? '!text-emerald-600' : ''}`}>
            {done ? <Check size={13} /> : <Copy size={13} />}
            {done ? 'Скопировано' : label}
        </button>
    );
};

/* Основание согласования под «i» (ТЗ, п. 6): маршрут выбирает система, а
   человек видит, что она проверила по порядку и почему не подошло остальное. */
export const ApprovalHint = ({ basis }) => {
    const summary = approvalSummary(basis);
    if (!summary || !summary.trace.length) return null;
    return (
        <InfoHint title="Как выбран согласующий">
            <div className="space-y-1.5 text-[12px] leading-snug">
                {summary.trace.map((step, index) => (
                    <div key={index} className={step.ok ? 'text-slate-700' : 'text-slate-500'}>
                        {step.ok ? '✓' : '—'} {step.text}
                    </div>
                ))}
                <div className="border-t border-slate-200 pt-1.5 text-slate-500">
                    Этап руководителя: {summary.managerStep ? 'есть' : 'не нужен'}{summary.managerReason ? ` (${summary.managerReason})` : ''}.
                </div>
                {summary.conflict && <div className="text-amber-700">Несколько лимитов дают право разным людям — применён приоритетный.</div>}
            </div>
        </InfoHint>
    );
};

/* Реквизиты компании-плательщика с кнопкой «Скопировать» (ТЗ, п. 13.3):
   инициатор пересылает их поставщику, чтобы тот выставил счёт. */
export const RequisitesBox = ({ entity, title = 'Реквизиты компании' }) => {
    const text = requisitesText(entity);
    if (!text) return null;
    return (
        <div className="rounded-xl bg-slate-50 px-3 py-2">
            <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">{title}</span>
                <CopyButton text={text} label="Скопировать реквизиты" />
            </div>
            <div className="whitespace-pre-line break-words text-[13px] leading-relaxed text-slate-800">{text}</div>
        </div>
    );
};

/* Номер карты: маска «•••• 1234» и, для тех, кому положено, — «показать»
   (ТЗ, п. 5.2: полный номер доступен по правам). Полный номер раздел запрашивает
   отдельно и держит только в этом компоненте: закрыл карточку — номера нет.
   `compact` — для карточки доски: показанный номер занимает строку почти целиком,
   поэтому «Скрыть» и «Скопировать» там стоят значками, а не словами (слова
   переносились на вторую строку). */
export const CardNumber = ({ mask, canReveal = false, load, showToast, compact = false }) => {
    const [full, setFull] = useState('');
    const [busy, setBusy] = useState(false);
    useEffect(() => { setFull(''); }, [mask]);
    const reveal = async () => {
        if (full) { setFull(''); return; }
        setBusy(true);
        try {
            const data = await load();
            setFull(cardNumberLabel(data?.number));
        } catch (error) {
            showToast?.(error?.response?.data?.error || 'Не удалось получить номер карты', 'error');
        } finally {
            setBusy(false);
        }
    };
    if (!mask) return null;
    const icons = compact && Boolean(full);
    return (
        <span className={`inline-flex items-center gap-y-1 ${icons ? 'gap-x-1' : 'flex-wrap gap-x-2'}`}>
            <span className={`tabular-nums text-slate-900 ${icons ? 'mr-1' : ''}`}>{full || mask}</span>
            {canReveal && (
                <button
                    type="button"
                    onClick={(event) => { event.stopPropagation(); reveal(); }}
                    disabled={busy}
                    title={icons ? 'Скрыть номер' : undefined}
                    aria-label={icons ? 'Скрыть номер' : undefined}
                    className={icons
                        ? `${ICON_ACTION} text-blue-600`
                        : 'inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] font-medium text-blue-600 transition hover:bg-blue-50'}
                >
                    {busy ? <Loader2 size={12} className="animate-spin" /> : (full ? <EyeOff size={icons ? 13 : 12} /> : <Eye size={12} />)}
                    {!icons && (full ? 'Скрыть' : 'Показать номер')}
                </button>
            )}
            {full && <CopyButton text={full.replace(/\s/g, '')} label={compact ? 'Скопировать номер' : 'Скопировать'} iconOnly={compact} />}
        </span>
    );
};

/* «Колонки» реестра — меню с галочками, как «Вид» в Finder: отмеченное видно
   в таблице. Обязательные колонки отмечены и не снимаются. В Excel уходят все
   колонки независимо от этого выбора — об этом строка внизу меню. */
export const ColumnsMenu = ({ columns, value, onChange, onReset }) => {
    const [open, setOpen] = useState(false);
    const wrapRef = useRef(null);
    useEffect(() => {
        if (!open) return undefined;
        const onDoc = (event) => { if (!wrapRef.current?.contains(event.target)) setOpen(false); };
        const onKey = (event) => { if (event.key === 'Escape') setOpen(false); };
        document.addEventListener('mousedown', onDoc);
        document.addEventListener('keydown', onKey);
        return () => {
            document.removeEventListener('mousedown', onDoc);
            document.removeEventListener('keydown', onKey);
        };
    }, [open]);
    const changed = columns.some((column) => Boolean(column.shown) !== value.includes(column.key));
    return (
        <div ref={wrapRef} className="relative shrink-0">
            <button
                type="button"
                aria-haspopup="menu"
                aria-expanded={open}
                onClick={() => setOpen((prev) => !prev)}
                className={`${iosBtnSecondary} ${open ? '!bg-slate-200 !text-slate-800' : ''}`}
                title="Какие колонки показывать в таблице"
            >
                <Columns3 size={15} />
                <span className="hidden sm:inline">Колонки</span>
            </button>
            {open && (
                <div role="menu" className="absolute right-0 top-full z-40 mt-1.5 w-[252px] rounded-2xl bg-white p-1.5 shadow-[0_14px_40px_rgba(15,23,42,0.16)] ring-1 ring-slate-200/80 motion-safe:animate-popover-in">
                    <div className="max-h-[min(460px,60vh)] overflow-y-auto">
                        {columns.map((column) => {
                            const checked = value.includes(column.key);
                            return (
                                <button
                                    key={column.key}
                                    type="button"
                                    role="menuitemcheckbox"
                                    aria-checked={checked}
                                    disabled={column.fixed}
                                    onClick={() => onChange(checked ? value.filter((key) => key !== column.key) : [...value, column.key])}
                                    className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] text-slate-800 transition hover:bg-slate-100 disabled:cursor-default disabled:text-slate-400 disabled:hover:bg-transparent"
                                >
                                    <span className="grid w-4 shrink-0 place-items-center">
                                        {checked && <Check size={14} strokeWidth={2.5} className={column.fixed ? 'text-slate-300' : 'text-blue-600'} />}
                                    </span>
                                    {column.label}
                                </button>
                            );
                        })}
                    </div>
                    <div className="mt-1 flex items-center justify-between gap-2 border-t border-slate-100 px-2 pb-0.5 pt-2">
                        <span className="text-[11.5px] leading-tight text-slate-400">В Excel — все колонки</span>
                        {changed && (
                            <button type="button" onClick={onReset} className="rounded-md px-1.5 py-0.5 text-[12px] font-medium text-blue-600 transition hover:bg-blue-50">Как было</button>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
};

export const SectionTitle = ({ children, right = null }) => (
    <div className="flex items-end justify-between gap-2">
        <div className={iosGroupLabel}>{children}</div>
        {right}
    </div>
);

export const ErrorBox = ({ text }) => (text ? (
    <div className="rounded-2xl bg-rose-50 px-4 py-3 text-[13px] leading-relaxed text-rose-700 ring-1 ring-rose-200">{text}</div>
) : null);

export const NoticeBox = ({ text, tone = 'amber', children }) => {
    const cls = tone === 'amber'
        ? 'bg-amber-50 text-amber-800 ring-amber-200'
        : tone === 'blue'
            ? 'bg-blue-50 text-blue-800 ring-blue-100'
            : 'bg-slate-50 text-slate-700 ring-slate-200';
    return (
        <div className={`rounded-2xl px-4 py-3 text-[13px] leading-relaxed ring-1 ${cls}`}>
            {text}
            {children}
        </div>
    );
};

/* Файлы в форму: сами файлы, их виды и — для коммерческих предложений —
   порядковый номер варианта поставщика, к которому относится файл. */
const appendPayloadFiles = (formData, files) => {
    (files || []).forEach((entry) => formData.append('files', entry.file, entry.file.name));
    formData.append('kinds', JSON.stringify((files || []).map((entry) => entry.kind || 'other')));
    formData.append('offers', JSON.stringify((files || []).map((entry) => (
        Number.isInteger(entry.offerIndex) ? entry.offerIndex : null))));
};

/* multipart с полем payload — так сервер читает данные рядом с файлами. */
export const multipart = (payload, files = []) => {
    const form = new FormData();
    form.append('payload', JSON.stringify(payload || {}));
    appendPayloadFiles(form, files);
    return form;
};

export const errorText = (error, fallback) => {
    const data = error?.response?.data;
    if (data?.errors?.length) return data.errors.join('. ');
    if (data?.missing?.length) return data.missing.join('. ');
    return data?.error || fallback;
};
