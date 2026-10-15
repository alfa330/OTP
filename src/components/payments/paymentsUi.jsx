import React, { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Check, ChevronDown, ChevronUp, Columns3, Copy, CreditCard, Download, Eye, EyeOff, FileText, Loader2, Paperclip, RefreshCcw,
    ShoppingCart, Trash2, UsersRound, X,
} from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import IosDatePicker from '../ui/DatePicker';
import { iosBtnGhost, iosBtnSecondary, iosCard, iosGroupLabel, iosInput, IosSegmented, prefersReducedMotion } from '../ui/ios';
import { foldFollowStep, foldMotion } from '../../utils/foldMotion';
import { windowResizeMotion } from '../../utils/windowResize';
import InfoHint from '../common/InfoHint';
import useIsMobileShell from '../common/useIsMobileShell';
import {
    ATTACHMENT_LABELS, REQUEST_FORMS, approvalSummary, cardNumberLabel, dueChip, edgeFade, fileSizeLabel, fmtMoney, initialsOf,
    issueKeys, issueStep, pageRange, parseAmount, peopleParts, plural, requestState, requestType, requisitesText, stateMeta,
    toneEdge, tonePill, toneRow,
} from './paymentsMeta';

/*
 * Мелкие примитивы раздела «Оплата счетов», общие для формы, карточки и
 * справочников. Всё построено на примитивах ui/ios — своего визуального языка
 * раздел не заводит.
 */

/* Колонка раздела — одна на всё: шапка, вкладки, поиск, списки и доска стоят с
   одними и теми же краями (просьба владельца 13.10.2026: доска, выходившая из
   узкой колонки шапки на весь экран, «выглядит вообще не симметрично»). Колонка
   растёт вместе с экраном, чтобы на большом мониторе помещались все колонки
   доски; предел — восемь колонок бухгалтерии обычной ширины (8 × 268 px с
   промежутками и полями), дальше раздел стоит по центру.

   Поля на компьютере — 28 px, а не 20: в установленном портале и в полноэкранном
   режиме у области портала своих полей нет (styles.css, блок display-mode), а
   кнопка сворачивания меню выступает в неё на 16 px — при полях в 20 px заголовок
   раздела стоял к ней вплотную. */
export const SECTION_COLUMN = 'mx-auto w-full max-w-[2270px] px-3 sm:px-5 lg:px-7';

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

/* ── Чего не хватает: поле в красной рамке ─────────────────────────────────────
   Нажали «Отправить» (или кнопку этапа), а обязательное не заполнено, — поле
   обводится красным, под ним красным написано, чего в нём нет, и окно подводит
   прокрутку к первому такому полю (просьба владельца 14.10.2026). Рамка — та же,
   что у неверной строки позиции: ring-2 ring-rose-300. */
export const INVALID_RING = 'ring-2 ring-rose-300';

/* У поля с пунктом элементы управления внутри — AmountInput, FormSelect, FormDate,
   Choice, FilePicker, FormInput — обводятся сами; явное `invalid` у элемента
   старше (так списки позиций и поставщиков красят только нужную строку). В
   контексте — и id строки «чего не хватает»: поле ввода ссылается на неё
   (aria-describedby), и читалка произносит причину вместе с полем. */
const NO_ISSUE = { bad: false, id: undefined };
const FieldIssueContext = React.createContext(NO_ISSUE);
export const useFieldIssue = (invalid) => {
    const inField = React.useContext(FieldIssueContext);
    return invalid ?? inField.bad;
};
const useFieldIssueId = (bad) => {
    const inField = React.useContext(FieldIssueContext);
    return bad && inField.bad ? inField.id : undefined;
};

/* Подпись поля. Пояснение — под «i» у метки, а не строкой под полем (решение
   владельца 27.08.2026): текст нужен один раз, когда человек не понял поле.
   `as="div"` — для полей с несколькими элементами управления внутри (сегменты,
   файлы): <label> вокруг нескольких кнопок отдавал бы щелчок по подписи первой.
   Строка подписи одной высоты с «i» и без него (`min-h-5` — высота кнопки
   InfoHint): иначе в ряду из двух полей то, что с подсказкой, стояло на
   несколько пикселей ниже соседнего.

   `anchor` — ключ поля для пунктов «чего не хватает» (paymentsMeta.purchaseIssues):
   по нему стрелки находят поле на экране. `issue` — что в поле не так; пока его
   нет, строки под полем нет вовсе. `issueFirst` — строка сразу под подписью, над
   содержимым: у высокого поля (список поставщиков, позиции) строка под списком
   уходила за нижний край, когда к полю подводили.

   Провайдер стоит ВСЕГДА, меняется только значение: оберни его только у поля с
   пунктом — и содержимое на этом месте дерева меняло бы тип, React пересоздавал
   бы поле ввода, и первая же набранная буква, снявшая пункт, выбивала бы человека
   из поля (разбор 14.10.2026: «Бумага А4» превращалась в «Б»). */
export const Field = ({ label, hint, required = false, optionalMark = true, children, className = '', as = 'label', anchor, issue = '',
    issueFirst = false }) => {
    const Tag = as;
    const id = useId();
    const issueContext = useMemo(() => ({ bad: true, id }), [id]);
    const line = issue ? <IssueLine id={id} text={issue} /> : null;
    return (
        <Tag className={`block space-y-1.5 ${className}`} data-issue-anchor={anchor} data-issue={issue ? 'true' : undefined}>
            {/* Без подписи — только место для строки «чего не хватает» (файл счёта под полями счёта). */}
            {label && (
                <span data-field-label className="flex min-h-5 items-center gap-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                    {label}
                    {!required && optionalMark && (
                        <span className="font-normal normal-case tracking-normal text-slate-400">необязательно</span>
                    )}
                    <FieldHint hint={hint} />
                </span>
            )}
            {issueFirst && line}
            <FieldIssueContext.Provider value={issue ? issueContext : NO_ISSUE}>{children}</FieldIssueContext.Provider>
            {!issueFirst && line}
        </Tag>
    );
};

/* Строка «чего не хватает» под полем. Появляется мягко — как раскрытие строки. */
export const IssueLine = ({ text, id }) => (
    <span id={id} className="block px-1 text-[12.5px] leading-snug text-rose-600 motion-safe:animate-reveal" data-issue-text>{text}</span>
);

/* Поле, к которому подвели, коротко «вспыхивает» мягкой красной волной вокруг себя:
   глаз находит его, даже когда рядом на экране другие красные поля. При «Уменьшить
   движение» волны нет — поле на полторы секунды обведено неподвижной каймой, которая
   потом гаснет: без неё не видно, к какому из красных полей подвели. */
const ISSUE_FLASH = [
    { boxShadow: '0 0 0 0 rgba(244, 63, 94, 0.30)', borderRadius: '14px' },
    { boxShadow: '0 0 0 8px rgba(244, 63, 94, 0.12)', borderRadius: '14px', offset: 0.4 },
    { boxShadow: '0 0 0 12px rgba(244, 63, 94, 0)', borderRadius: '14px' },
];
const ISSUE_MARK = [
    { boxShadow: '0 0 0 5px rgba(244, 63, 94, 0.22)', borderRadius: '14px' },
    { boxShadow: '0 0 0 5px rgba(244, 63, 94, 0.22)', borderRadius: '14px', offset: 0.8 },
    { boxShadow: '0 0 0 5px rgba(244, 63, 94, 0)', borderRadius: '14px' },
];

const scrollParent = (node) => {
    for (let el = node?.parentElement; el; el = el.parentElement) {
        if (/(auto|scroll)/.test(getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight) return el;
    }
    return null;
};

/* Что в поле получает фокус, когда к нему подвели с клавиатуры: первый элемент управления
   самого поля — не «i» подсказки в подписи (она стоит в разметке раньше поля). */
const FOCUSABLE = 'input:not([type="file"]):not([disabled]), textarea:not([disabled]), button:not([disabled]), [role="tab"]';
const fieldControl = (el) => [...el.querySelectorAll(FOCUSABLE)].find((node) => !node.closest('[data-field-label]')) || null;

/* Подвести прокрутку к полю: невысокое — в середину окна, высокое (список
   поставщиков) — началом к верху, иначе его начало ушло бы за край. `focus` —
   кнопку нажали с клавиатуры: фокус переходит в само поле (мышью и пальцем — нет:
   на телефоне фокус в поле открыл бы клавиатуру поверх формы). */
export const revealAnchor = (el, { focus = false } = {}) => {
    if (!el) return;
    const reduce = prefersReducedMotion();
    const scroller = scrollParent(el);
    const tall = scroller ? el.offsetHeight > scroller.clientHeight * 0.6 : false;
    el.scrollIntoView({ block: tall ? 'start' : 'center', behavior: reduce ? 'auto' : 'smooth' });
    if (typeof el.animate === 'function') {
        el.animate(reduce ? ISSUE_MARK : ISSUE_FLASH, reduce ? { duration: 1600, easing: 'linear' } : { duration: 900, easing: 'ease-out' });
    }
    if (focus) fieldControl(el)?.focus({ preventScroll: true });
};

/* Нажатие пришло с клавиатуры (Enter, пробел): у щелчка мышью и касания detail ≥ 1. */
export const fromKeyboard = (event) => Boolean(event && event.detail === 0);

/* Переход по полям, где чего-то не хватает. `rootRef` — форма (поля ищутся в ней по
   data-issue-anchor), `keys` — поля с пунктами (paymentsMeta.issueKeys). `first()` —
   к первому по экрану: так форма отвечает на «Отправить»; `step(1)` и `step(-1)` — к
   следующему и предыдущему, по кругу. Переход идёт после отрисовки: по нажатию
   «Отправить» поля ещё не красные, а скрытый блок (счёт) ещё не раскрыт. Пункт,
   чьего поля на экране нет, стрелки пропускают: шаг, после которого ничего не
   происходит, выглядел бы поломкой. */
export const useIssueNav = (rootRef, keys) => {
    const currentRef = useRef(null);
    const pendingRef = useRef(null);
    const [tick, setTick] = useState(0);
    const keysRef = useRef(keys);
    keysRef.current = keys;
    useIsoLayoutEffect(() => {
        const pending = pendingRef.current;
        pendingRef.current = null;
        const root = rootRef.current;
        if (!pending || !root) return;
        const anchorOf = (key) => root.querySelector(`[data-issue-anchor="${key}"]`);
        const screen = [...root.querySelectorAll('[data-issue-anchor]')].map((el) => el.getAttribute('data-issue-anchor'));
        const sorted = issueKeys(keysRef.current.map((key) => ({ key })), screen).filter((key) => anchorOf(key));
        const target = pending.how === 'first' ? sorted[0] : issueStep(sorted, currentRef.current, pending.how, screen);
        if (!target) return;
        currentRef.current = target;
        revealAnchor(anchorOf(target), { focus: pending.focus });
    }, [tick]);
    const go = useCallback((how, { focus = false } = {}) => { pendingRef.current = { how, focus }; setTick((value) => value + 1); }, []);
    return useMemo(() => ({ first: (options) => go('first', options), step: (how, options) => go(how, options) }), [go]);
};

const ISSUE_NAV_BUTTON = 'grid h-7 w-7 place-items-center rounded-lg text-rose-600 transition hover:bg-rose-100 active:scale-95';

/* «Не хватает: 3 ⌃ ⌄» — в нижней панели окна, когда незаполненных полей больше
   одного: стрелки переводят от поля к полю, как «следующее совпадение» в поиске
   по странице. Одно поле — переключатель не нужен: к нему уже подвели. На
   телефоне — отдельной строкой во всю ширину над кнопками. */
export const IssueNav = ({ count, onStep }) => {
    if (!(count > 1)) return null;
    return (
        <div
            data-issue-nav
            role="group"
            aria-label="Поля, где не хватает данных"
            className="inline-flex items-center gap-0.5 rounded-xl bg-rose-50 py-0.5 pl-3 pr-0.5 text-[12.5px] font-medium text-rose-700 ring-1 ring-rose-200 motion-safe:animate-reveal max-sm:order-first max-sm:w-full max-sm:justify-between"
        >
            <span className="mr-1 tabular-nums" aria-live="polite">Не хватает: {count}</span>
            <span className="flex items-center">
                <button type="button" className={ISSUE_NAV_BUTTON} onClick={() => onStep(-1)} aria-label="К предыдущему полю, где не хватает данных" title="Предыдущее">
                    <ChevronUp size={16} />
                </button>
                <button type="button" className={ISSUE_NAV_BUTTON} onClick={() => onStep(1)} aria-label="К следующему полю, где не хватает данных" title="Следующее">
                    <ChevronDown size={16} />
                </button>
            </span>
        </div>
    );
};

/* Выбор из двух-трёх вариантов — сегментами: все варианты видны сразу, щелчок
   один. Так выбираются «С НДС / Без НДС», «Нужна / Не нужна», «ТОО / ИП»
   (решение владельца 06.10.2026: такие варианты выбирают, а не пишут словами и
   не отмечают галочкой). Значение `null` — «ещё не выбрано»: ни один сегмент не
   подсвечен, и молчаливого «нет» по умолчанию не бывает. */
export const Choice = ({ value, onChange, options, ariaLabel, stretch = false, disabled = false, invalid }) => {
    const bad = useFieldIssue(invalid);
    return (
        <div className={`${stretch ? '' : 'inline-flex max-w-full'} ${disabled ? 'pointer-events-none opacity-60' : ''}${bad ? ` rounded-[10px] ${INVALID_RING}` : ''}`}>
            <IosSegmented value={value} options={options} onChange={onChange} ariaLabel={ariaLabel} stretch={stretch} />
        </div>
    );
};

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

/* Кружок человека, как на карточках «Задач»: фото из профиля, а у кого его нет —
   инициалы на сером. Подразделение — значком, а не буквой.

   Фото не загрузилось (ссылка подписана на сутки и на давно открытой доске могла
   истечь) — вместо сломанной картинки встают те же инициалы. Запоминается сама
   негодная ссылка, а не «сломано»: придёт свежая — фото вернётся.
   Размер у картинки свой, в пикселях, а не «100 %»: кружок — сетка, и процент от её
   строки считается по самой картинке — высокий снимок вставал 20×33 и держался в
   кружке только обрезкой по краю. С размером снимок любой формы заполняет кружок
   и обрезается по нему (object-cover).
   draggable={false}: карточку доски тащат за любое место, а картинка по умолчанию
   тащится сама — вместо карточки в руке оказалась бы аватарка. */
export const Face = ({ person, size = 20 }) => {
    const url = person?.role ? '' : String(person?.avatar_url || '').trim();
    const [brokenUrl, setBrokenUrl] = useState('');
    const photo = Boolean(url) && url !== brokenUrl;
    let inner = initialsOf(person?.name);
    if (person?.role) inner = <UsersRound size={Math.round(size * 0.55)} strokeWidth={2} aria-hidden="true" />;
    else if (photo) {
        inner = (
            <img
                src={url}
                alt=""
                className="block object-cover"
                style={{ width: size, height: size }}
                loading="lazy"
                decoding="async"
                draggable={false}
                onError={() => setBrokenUrl(url)}
            />
        );
    }
    return (
        <span
            title={person?.title || person?.name || ''}
            data-face={photo ? 'photo' : (person?.role ? 'group' : 'initials')}
            className="inline-grid shrink-0 place-items-center overflow-hidden rounded-full bg-slate-200 font-semibold text-slate-600"
            style={{ width: size, height: size, fontSize: Math.max(9.5, Math.round(size * 0.36 * 10) / 10) }}
        >
            {inner}
        </span>
    );
};

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
   слева направо, по цвету.

   oneLine — этапы не переносятся, а листаются вбок, и лента сама стоит на текущем
   этапе. Так сводка заявки рисуется на телефоне: она закреплена над страницами
   окна, и три строки этапов отнимали бы у них треть экрана. Этап, уходящий за край
   ленты, гаснет, а не обрывается посреди карточки (edgeFade — как край доски). */
const LIFECYCLE_EDGE = 20;
const NO_EDGES = { left: false, right: false };

export const Lifecycle = ({ stages = [], stopped = false, oneLine = false }) => {
    const shown = stages.filter((stage) => stage.state !== 'skipped');
    const listRef = useRef(null);
    const [edges, setEdges] = useState(NO_EDGES);
    const currentCode = shown.find((stage) => stage.state === 'current')?.code || '';
    /* Оба шага — до первой отрисовки: лента открывается уже на текущем этапе и с погашенным
       краем, а не доезжает кадром позже. */
    useIsoLayoutEffect(() => {
        const list = listRef.current;
        const current = list?.querySelector('[aria-current="step"]');
        if (!oneLine || !list || !current || list.scrollWidth <= list.clientWidth) return;
        const listBox = list.getBoundingClientRect();
        const box = current.getBoundingClientRect();
        list.scrollLeft += (box.left + box.width / 2) - (listBox.left + listBox.width / 2);
    }, [oneLine, currentCode, shown.length]);
    // За каким краем ленты есть этапы: читается после того, как лента встала на текущий.
    useIsoLayoutEffect(() => {
        const list = listRef.current;
        if (!oneLine || !list) return undefined;
        const read = () => {
            const left = list.scrollLeft > 1;
            const right = list.scrollLeft + list.clientWidth < list.scrollWidth - 1;
            setEdges((prev) => (prev.left === left && prev.right === right ? prev : { left, right }));
        };
        read();
        list.addEventListener('scroll', read, { passive: true });
        const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(read) : null;
        observer?.observe(list);
        return () => {
            list.removeEventListener('scroll', read);
            observer?.disconnect();
        };
    }, [oneLine, currentCode, shown.length]);
    /* !flex-nowrap: телефонная оболочка сама переносит ряды с gap-* (mobile-shell.css) —
       здесь лента обязана остаться одной строкой. */
    return (
        <ol
            ref={listRef}
            className={`flex items-center gap-1.5 ${oneLine ? '!flex-nowrap overflow-x-auto [scrollbar-width:none]' : 'flex-wrap'}`}
            style={oneLine ? edgeFade(edges.left, edges.right, LIFECYCLE_EDGE) : undefined}
        >
            {shown.map((stage) => {
                const done = stage.state === 'done';
                const current = stage.state === 'current';
                const halted = current && stopped;
                return (
                    <li key={stage.code} className="flex shrink-0" aria-current={current ? 'step' : undefined}>
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
export const AmountInput = ({ value, onChange, placeholder = '0', className = '', disabled = false, ariaLabel, invalid }) => {
    const [text, setText] = useState(() => (value === '' || value === null || value === undefined ? '' : fmtMoney(value, { currency: false })));
    const [focused, setFocused] = useState(false);
    const inputRef = useRef(null);
    const bad = useFieldIssue(invalid);
    const describedBy = useFieldIssueId(bad);
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
            aria-invalid={bad || undefined}
            aria-describedby={describedBy}
            disabled={disabled}
            className={`${iosInput} tabular-nums ${className}${bad ? ` ${INVALID_RING}` : ''}`}
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
        className={`${iosInput} tabular-nums ${invalid ? INVALID_RING : ''} ${className}`}
        value={value}
        onFocus={(event) => event.target.select()}
        onChange={(event) => onChange(event.target.value.replace(/[^\d.,]/g, ''))}
    />
);

/* Список в форме, рядом с полями ввода: тот же рост (41 px), отступ и кегль, что у
   iosInput. Сам CustomSelect по умолчанию рассчитан на панель фильтров (35 px,
   12.5 px) — в одном ряду формы такие поля стояли вразнобой. */
export const FORM_SELECT_TEXT = '!px-3.5 !py-2.5 text-[14px] text-slate-900';
export const FormSelect = ({ invalid, ...props }) => {
    const bad = useFieldIssue(invalid);
    return <CustomSelect variant="ios" textClassName={FORM_SELECT_TEXT} invalid={bad} {...props} />;
};

/* Поля ввода формы: iosInput и красная рамка, когда в поле не хватает значения. */
export const FormInput = ({ invalid, className = '', ...props }) => {
    const bad = useFieldIssue(invalid);
    const describedBy = useFieldIssueId(bad);
    return <input className={`${iosInput}${className ? ` ${className}` : ''}${bad ? ` ${INVALID_RING}` : ''}`} aria-invalid={bad || undefined} aria-describedby={describedBy} {...props} />;
};

export const FormText = ({ invalid, className = '', ...props }) => {
    const bad = useFieldIssue(invalid);
    const describedBy = useFieldIssueId(bad);
    return <textarea className={`${iosInput} resize-y${className ? ` ${className}` : ''}${bad ? ` ${INVALID_RING}` : ''}`} aria-invalid={bad || undefined} aria-describedby={describedBy} {...props} />;
};

/* Дата в форме: тот же вид, что у полей ввода (DATE_TRIGGER), и пустое значение можно. */
export const FormDate = ({ invalid, triggerClassName = '', ...props }) => {
    const bad = useFieldIssue(invalid);
    return (
        <IosDatePicker
            allowEmpty
            placeholder="Дата"
            {...props}
            triggerClassName={`${DATE_TRIGGER}${triggerClassName ? ` ${triggerClassName}` : ''}${bad ? ` ${INVALID_RING}` : ''}`}
        />
    );
};

/* Выбранные люди в кнопке списка: первое имя обрезается по ширине, «и ещё N» видно всегда —
   иначе на телефоне второй человек пропадал из виду вместе со счётчиком (разбор 15.10.2026). */
export const PeopleValue = ({ ids, users }) => {
    const { first, rest, total } = peopleParts(ids, users);
    if (!first) return total ? `Выбрано: ${total}` : '';
    return (
        <span className="flex min-w-0 items-baseline" data-people-value>
            <span className="min-w-0 truncate">{first}</span>
            {rest > 0 && <span className="shrink-0 whitespace-pre"> и ещё {rest}</span>}
        </span>
    );
};

/* Выбор сотрудника — CustomSelect с поиском; варианты собираются один раз.
   `compact` — мелкий вид для строки, где рядом стоят такие же мелкие поля.
   `multiple` — несколько человек: значение — массив номеров СТРОКАМИ (так их отдаёт
   CustomSelect); `renderValue` — что написать в кнопке, `maxSelected` — потолок. */
export const UserSelect = ({ users, value, onChange, placeholder = 'Выберите сотрудника', exclude = [], ariaLabel, multiple = false, compact = false, invalid,
    renderValue = null, maxSelected = 0 }) => {
    const bad = useFieldIssue(invalid);
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
            renderValue={renderValue}
            maxSelected={maxSelected}
            invalid={bad}
        />
    );
};

/* ── Файлы ─────────────────────────────────────────────────────────────────── */

const ACCEPT = '.pdf,.jpg,.jpeg,.png,.webp,.heic,.doc,.docx,.xls,.xlsx,.csv,.txt,.zip';

/* Очередь файлов ДО отправки: имя, размер, вид документа. Вид спрашивается у
   каждого файла, потому что от него зависит проверка шага («счёт приложен?»). */
export const FilePicker = ({ files, onChange, kinds, disabled = false, label = 'Приложить файлы', invalid }) => {
    const inputRef = useRef(null);
    const bad = useFieldIssue(invalid);
    const describedBy = useFieldIssueId(bad);
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
            {/* Нужного файла нет — кнопка в красной рамке: файл и есть то, чего не хватает. */}
            <button
                type="button"
                disabled={disabled}
                aria-invalid={bad || undefined}
                aria-describedby={describedBy}
                className={`${iosBtnGhost} -ml-2${bad ? ` !text-rose-600 ${INVALID_RING}` : ''}`}
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

/* ── Списки записей: «Заявки», «Имущество», «Справочники» ──────────────────────
   Просьба владельца 13.10.2026: эти вкладки «нужно переделать, чтобы разделение было
   более заметным и понятным на первый взгляд». Записи стояли сплошной таблицей в одной
   белой карточке: строки разделяла едва видная линия, деление на состояния было мелкой
   серой полосой. Образец — списки «Задач»: плитки со счётчиками, каждая запись — своя
   строка-карточка с зазором, группы под подписью с линией. */

/* Плитки-срезы: «Все / Ждут меня / В работе…» у заявок, статусы у имущества. Плитка —
   это и число, и отбор: выбранная залита, как выбранная метка в «Задачах». Цвет у числа —
   только у состояний со смыслом (тот же, что у строк и меток этого состояния).

   На компьютере плитки делят ширину поровну и переносятся на вторую строку, если не
   помещаются. На телефоне восемь плиток в два столбца заняли бы пол-экрана — там ряд
   один и листается вбок: обрезанная плитка сама говорит, что есть ещё. Раскладка
   компьютера задана стилем, а не классом `grid-cols-[…]`: такие сетки телефонная
   оболочка схлопывает в столбик (а включается она и на широком экране малой высоты). */
const TILE_NUMBER = { overdue: 'text-rose-600', blocked: 'text-amber-600', done: 'text-emerald-600', rejected: 'text-slate-400' };
// Плитка не уже 148 px (иначе «Требуют уточнения» обрезается) и не шире 224: три плитки во
// всю ширину экрана выглядели бы тремя пустыми полосами, а выбранная — чёрной плашкой.
// Предел ширины задан всему ряду, а не колонке: с пределом у колонки сетка считает, сколько
// плиток встанет в строку, по нему же — и восемь плиток, которым хватает места, шли в два ряда.
const TILE_MIN = 148;
const TILE_MAX = 224;
const TILE_GAP = 8;
const tilesWrap = (count) => ({
    gridTemplateColumns: `repeat(auto-fit, minmax(${TILE_MIN}px, 1fr))`,
    maxWidth: Math.max(1, count) * TILE_MAX + (Math.max(1, count) - 1) * TILE_GAP,
});
const TILES_STRIP = { gridAutoFlow: 'column', gridAutoColumns: `minmax(${TILE_MIN}px, 1fr)` };

export const StatTiles = ({ items = [], value, onChange, ariaLabel }) => {
    const phone = useIsMobileShell();
    const stripRef = useRef(null);
    // Выбранная плитка остаётся на виду, когда ряд листается.
    useEffect(() => {
        const strip = stripRef.current;
        const active = strip?.querySelector('[aria-pressed="true"]');
        if (!strip || !active || strip.scrollWidth <= strip.clientWidth) return;
        const box = strip.getBoundingClientRect();
        const tile = active.getBoundingClientRect();
        if (tile.left < box.left || tile.right > box.right) strip.scrollLeft += (tile.left + tile.width / 2) - (box.left + box.width / 2);
    }, [value, items.length, phone]);
    return (
        <div
            ref={stripRef}
            role="group"
            aria-label={ariaLabel}
            data-stat-tiles={phone ? 'strip' : 'wrap'}
            className={`grid gap-2 ${phone ? 'overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden' : ''}`}
            style={phone ? TILES_STRIP : tilesWrap(items.length)}
        >
            {items.map((item) => {
                const active = item.key === value;
                const pill = item.tone ? tonePill(item.tone) : null;
                return (
                    <button
                        key={item.key}
                        type="button"
                        data-state-filter={item.key}
                        aria-pressed={active}
                        onClick={() => onChange?.(item.key)}
                        className={`min-w-0 rounded-xl border px-3 py-2.5 text-left transition active:scale-[0.99] ${active
                            ? 'border-slate-900 bg-slate-900'
                            : 'border-slate-200 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.06)] hover:bg-slate-50'}`}
                    >
                        {/* Не flex: ряды с gap телефонная оболочка переносит, и подпись ушла бы под точку. */}
                        <span className={`block truncate text-[12px] font-medium ${active ? 'text-slate-300' : 'text-slate-500'}`}>
                            {pill && <span className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle ${pill.dot}`} aria-hidden="true" />}
                            {item.label}
                        </span>
                        <span className={`mt-1 block text-[22px] font-semibold leading-none tabular-nums ${active ? 'text-white' : ((item.count > 0 && TILE_NUMBER[item.tone]) || 'text-slate-900')}`}>
                            {item.count ?? '—'}
                        </span>
                    </button>
                );
            })}
        </div>
    );
};

/* Заготовка ряда плиток, пока счётчики не пришли: те же размеры, серыми полосами вместо
   подписи и числа. Читалке о загрузке говорит одна подпись. */
export const StatTilesSkeleton = ({ label, count = 4 }) => {
    const phone = useIsMobileShell();
    return (
        <div role="status" aria-busy="true" aria-label={label} data-stat-tiles="skeleton" className={`grid gap-2 ${phone ? 'overflow-hidden' : ''}`} style={phone ? TILES_STRIP : tilesWrap(count)}>
            {Array.from({ length: count }, (_, index) => (
                <div key={index} className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
                    <span className="flex h-[18px] items-center"><Bone className="h-2 w-16" /></span>
                    <span className="mt-1 flex h-[22px] items-center"><Bone className="h-3 w-9" /></span>
                </div>
            ))}
        </div>
    );
};

/* Таблица, у которой карточкой нарисована СТРОКА: рамка у ячеек, скругление у крайних,
   между строками зазор. Колонки при этом остаются выровненными (и настраиваемыми — у
   реестра заявок), а подписи колонок стоят над списком, без своей полосы. Цвет рамки и
   фона — на самих ячейках: тёмный слой перекрашивает утилиты на элементе, а не вложенные
   селекторы. Подсветка под курсором — яркостью строки: годится и белой, и цветной. */
export const CARD_TABLE = 'w-full table-fixed border-separate border-spacing-x-0 border-spacing-y-1.5 text-[13.5px]';
export const CARD_TH = 'truncate px-3.5 pb-0.5 pt-1 text-left text-[11px] font-semibold uppercase tracking-wider text-slate-500';
export const CARD_ROW = 'group cursor-pointer transition hover:brightness-[0.97]';
const CARD_CELL = 'border-y px-3.5 py-2.5 first:rounded-l-xl first:border-l last:rounded-r-xl last:border-r';
/** Классы ячейки строки-карточки. `tone` — состояние записи, которое красит строку целиком
 *  (просрочена, ждёт уточнения, закрыта, отменена): фон и рамка в тон — те же, что у метки. */
export const cardCell = (tone = null) => `${CARD_CELL} ${toneEdge(tone) || 'border-slate-200'} ${toneRow(tone) || 'bg-white'}`;

/* Помещается ли в блок содержимое шириной `minWidth`: [ref блока, да / нет / null до замера].
   Меряется сам блок, а не окно: сколько ему достанется, решают меню портала (свёрнутое или
   раскрытое) и соседние колонки. Так таблица справочника встаёт только туда, где видна
   целиком, — иначе записи идут карточками, а не листаются вбок. Замер — до первой
   отрисовки: раскладка не перескакивает. */
export const useFits = (minWidth) => {
    const ref = useRef(null);
    const [width, setWidth] = useState(null);
    useIsoLayoutEffect(() => {
        const node = ref.current;
        if (!node) return undefined;
        const read = () => setWidth(node.clientWidth);
        read();
        if (typeof ResizeObserver !== 'function') return undefined;
        const observer = new ResizeObserver(read);
        observer.observe(node);
        return () => observer.disconnect();
    }, []);
    return [ref, width === null ? null : width >= minWidth];
};

/* Подпись группы с линией — как разделитель дней в списках «Задач». В таблице стоит своей
   строкой (`colSpan`), в обычном списке — блоком. */
export const GroupDivider = ({ children, count }) => (
    <div className="flex items-center gap-2 px-1 pb-0.5 pt-2">
        <span className="shrink-0 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{children}</span>
        {count !== undefined && count !== null && <span className="shrink-0 text-[11px] font-semibold tabular-nums text-slate-400">{count}</span>}
        <span className="h-px min-w-4 grow basis-0 bg-slate-200" aria-hidden="true" />
    </div>
);

/* Заготовка окна, пока запись грузится. Раньше окно открывалось коротким
   «Загружаем…» и на ответе сервера разом вырастало в несколько раз (жалоба
   владельца 12.10.2026: «мини карточка загрузка и резко она увеличивается»).
   Теперь оно сразу стоит в свой рост: те же карточки и строки на тех же местах,
   что у настоящей записи, только серыми полосами — как заготовки «Задач» и
   профиля. Строка повторяет геометрию Row, раздел — SectionTitle с карточкой,
   сводка — шапку карточки заявки: настоящее содержимое встаёт на место
   заготовки без сдвига. Читалке о загрузке говорит одна подпись, а не полосы. */
/* Тон полос — как у заготовок карточек на досках (bg-slate-200/70): более
   бледный bg-slate-100 в тёмной теме почти сливался с карточкой. */
export const Bone = ({ className = '', inline = false }) => (
    <span aria-hidden="true" className={`${inline ? 'inline-block align-middle' : 'block'} animate-pulse rounded-full bg-slate-200/70 motion-reduce:animate-none ${className}`} />
);

const BONE_WIDTHS = ['w-3/5', 'w-2/5', 'w-1/2', 'w-1/3', 'w-2/3', 'w-1/4'];
const BONE_STAGES = ['w-28', 'w-24', 'w-16', 'w-20', 'w-36', 'w-20'];

const SkeletonRows = ({ rows, wide }) => (
    <div className={`${iosCard} px-4 py-2`}>
        {Array.from({ length: rows }, (_, index) => (
            <div key={index} className="flex gap-3 py-1.5">
                <div className={`${wide ? 'w-[168px]' : 'w-[140px]'} flex h-5 shrink-0 items-center`}><Bone className="h-2.5 w-24" /></div>
                <div className="flex h-5 min-w-0 flex-1 items-center"><Bone className={`h-2.5 ${BONE_WIDTHS[index % BONE_WIDTHS.length]}`} /></div>
            </div>
        ))}
    </div>
);

/* sections — сколько строк в каждом разделе: число либо { rows, title: false }
   для карточки без заголовка. summary — сводка наверху, как у заявки. folds —
   сколько свёрнутых разделов стоит внизу одной карточкой (FoldGroup). */
export const WindowSkeleton = ({ label, summary = false, sections = [], wide = false, folds = 0 }) => (
    <div className="space-y-4" role="status" aria-busy="true" aria-label={label}>
        {summary && (
            <div className={`${iosCard} p-4`}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0 grow basis-0">
                        <div className="flex h-[22px] items-center"><Bone className="h-3.5 w-3/5" /></div>
                        <div className="mt-0.5 flex h-5 items-center"><Bone className="h-2.5 w-2/5" /></div>
                    </div>
                    <div className="flex h-[30px] items-center"><Bone className="h-5 w-28" /></div>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                    {BONE_STAGES.map((width, index) => <Bone key={index} className={`h-[22px] ${width}`} />)}
                </div>
                <div className="mt-2.5 flex h-[22px] items-center"><Bone className="h-[22px] w-44" /></div>
            </div>
        )}
        {sections.map((section, index) => {
            const { rows, title = true } = typeof section === 'number' ? { rows: section } : section;
            if (!title) return <SkeletonRows key={index} rows={rows} wide={wide} />;
            return (
                <section key={index} className="space-y-1.5">
                    <div className="flex h-4 items-center px-1"><Bone className="h-2 w-28" /></div>
                    <SkeletonRows rows={rows} wide={wide} />
                </section>
            );
        })}
        {folds > 0 && (
            <div className={`${iosCard} divide-y divide-slate-100`}>
                {Array.from({ length: folds }, (_, index) => (
                    <div key={index} className="flex items-center gap-3 px-4 py-3">
                        <div className="flex h-5 shrink-0 items-center"><Bone className="h-2.5 w-24" /></div>
                        <div className="flex h-5 min-w-0 grow basis-0 items-center justify-end"><Bone className={`h-2.5 ${BONE_WIDTHS[(index + 2) % BONE_WIDTHS.length]}`} /></div>
                    </div>
                ))}
            </div>
        )}
    </div>
);

/* Разделы окна «по запросу» (CARD_FOLDS в paymentsMeta.js): ход по этапам,
   документы, история. Одна карточка, строка на раздел — как группа в
   «Настройках» iOS и как сделка в карточке разговора «ИИ-оценки»
   (call_qa/DealBadge.jsx): название, суть одной строкой, шеврон. Раздел
   раскрывается на месте, под своей строкой; свёрнутому суть видна и так. */
export const FoldGroup = ({ children }) => (
    <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>{children}</div>
);

export const useIsoLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

// Что прокручивает раздел: тело окна (на телефоне — экран).
const scrollHost = (node) => {
    let host = node?.parentElement || null;
    while (host && host !== document.body) {
        if (/(auto|scroll)/.test(getComputedStyle(host).overflowY)) return host;
        host = host.parentElement;
    }
    return null;
};

/* РАСКРЫТИЕ — ПО ВЫСОТЕ, как в списках iOS и macOS (просьба владельца 13.10.2026:
   «открытие табов — ход заявки, документы и историю — сделать более плавным,
   аккуратно и удобно»). Раньше содержимое возникало целиком, строки под ним
   перескакивали вниз, рамка окна догоняла их отдельным движением, а при
   сворачивании всё исчезало за кадр и рамка съезжалась по пустому месту.

   Теперь тело раздела растёт от нуля до своего роста (и обратно), и всё, что под
   ним, едет вместе с ним — строки, подвал, рамка окна: у окна рост «по
   содержимому», отдельного движения рамке не нужно. Содержимое проявляется по
   ходу раскрытия и гаснет по ходу сворачивания; суть в строке уступает ему
   место и возвращается так же плавно; шеврон поворачивается тем же ходом.
   Расчёт — utils/foldMotion.js.

   Три вещи, без которых движение дёргается:
   — тело остаётся в разметке, пока сворачивается (`kept`): иначе сворачивать нечего;
   — перехваченное движение (нажали ещё раз на полпути) продолжается с того роста
     и той прозрачности, на которых его застали, а не с края;
   — раздел у нижнего края окна раскрывался бы за край: пока он растёт, окно едет
     следом, кадр в кадр, — но только пока название раздела остаётся на виду, и
     отпускает, если человек сам взялся за прокрутку.

   sizeKey — содержимое раскрытого раздела сменило рост разом («Вся история»):
   тело доезжает до нового роста тем же ходом, что рамка окна.

   При «Уменьшить движение» в системе — как раньше, сразу. */
export const Fold = ({ id, title, summary, open, onToggle, sizeKey, children }) => {
    const bodyId = useId();
    const sectionRef = useRef(null);
    const bodyRef = useRef(null);
    const [kept, setKept] = useState(open);
    if (open && !kept) setKept(true);
    const seenOpen = useRef(open);
    const running = useRef(null);

    useIsoLayoutEffect(() => {
        if (seenOpen.current === open) return;
        seenOpen.current = open;
        const body = bodyRef.current;
        const section = sectionRef.current;
        if (!body || !section) {
            if (!open) setKept(false);
            return;
        }
        const previous = running.current;
        const startHeight = previous || !open ? body.getBoundingClientRect().height : 0;
        const startOpacity = previous ? Number(getComputedStyle(body).opacity) : undefined;
        if (previous) {
            running.current = null;
            previous.cancel();
        }
        // Движение снято — у тела снова свой рост: это и есть рост раскрытого раздела.
        const motion = prefersReducedMotion() || typeof body.animate !== 'function'
            ? null
            : foldMotion(startHeight, open ? body.offsetHeight : 0, startOpacity);
        if (!motion) {
            body.style.overflow = '';
            if (open) section.scrollIntoView({ block: 'nearest' });
            else setKept(false);
            return;
        }
        body.style.overflow = 'hidden';
        const animation = body.animate(motion.frames, { duration: motion.duration, easing: motion.easing, fill: open ? 'none' : 'forwards' });
        running.current = animation;

        const host = open ? scrollHost(section) : null;
        let frame = 0;
        let following = Boolean(host);
        const letGo = () => { following = false; };
        const step = () => {
            const view = host.getBoundingClientRect();
            const box = section.getBoundingClientRect();
            const shift = foldFollowStep(box.top, box.bottom, view.top, view.bottom);
            if (shift) host.scrollTop += shift;
        };
        const follow = () => {
            if (running.current !== animation || !following) return;
            step();
            frame = requestAnimationFrame(follow);
        };
        if (host) {
            host.addEventListener('wheel', letGo, { passive: true });
            host.addEventListener('touchstart', letGo, { passive: true });
            frame = requestAnimationFrame(follow);
        }
        const release = () => {
            cancelAnimationFrame(frame);
            if (!host) return;
            host.removeEventListener('wheel', letGo);
            host.removeEventListener('touchstart', letGo);
        };
        animation.onfinish = () => {
            release();
            if (running.current !== animation) return;
            running.current = null;
            body.style.overflow = '';
            // Последний кадр: раздел встал в полный рост — доводим окно до него.
            if (host && following) step();
            if (!open) setKept(false);
        };
        animation.oncancel = release;
    }, [open]);

    const sizeSeen = useRef(sizeKey);
    const sizeFrom = useRef(null);
    if (sizeSeen.current !== sizeKey) {
        sizeSeen.current = sizeKey;
        sizeFrom.current = open && bodyRef.current && !running.current ? bodyRef.current.offsetHeight : null;
    }
    useIsoLayoutEffect(() => {
        const from = sizeFrom.current;
        sizeFrom.current = null;
        const body = bodyRef.current;
        if (!from || !body || !open || running.current || prefersReducedMotion() || typeof body.animate !== 'function') return;
        const motion = windowResizeMotion(from, body.offsetHeight);
        if (!motion) return;
        body.style.overflow = 'hidden';
        const animation = body.animate(motion.frames, { duration: motion.duration, easing: motion.easing });
        running.current = animation;
        animation.onfinish = () => {
            if (running.current !== animation) return;
            running.current = null;
            body.style.overflow = '';
        };
    }, [sizeKey]);

    useEffect(() => () => { if (running.current) running.current.cancel(); }, []);

    return (
        <section ref={sectionRef} data-fold={id} className="scroll-mb-3 scroll-mt-3">
            <h3 className="m-0">
                <button
                    type="button"
                    onClick={onToggle}
                    aria-expanded={open}
                    aria-controls={bodyId}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left outline-none transition-colors hover:bg-slate-50 active:bg-slate-100 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400"
                >
                    <span className="shrink-0 text-[13.5px] font-medium text-slate-900">{title}</span>
                    {/* Суть свёрнутого раздела. Раскрытому она не нужна — содержимое на виду, — но
                        уходит и возвращается плавно, а не морганием; читалке раскрытого не читается. */}
                    <span
                        aria-hidden={open ? true : undefined}
                        className={`min-w-0 grow basis-0 truncate text-right text-[12.5px] text-slate-500 transition-opacity duration-200 motion-reduce:transition-none ${open ? 'opacity-0' : 'opacity-100'}`}
                    >
                        {summary}
                    </span>
                    <ChevronDown
                        size={15}
                        aria-hidden="true"
                        className={`shrink-0 text-slate-400 transition-transform duration-300 ease-[cubic-bezier(0.4,0,0.2,1)] motion-reduce:transition-none ${open ? 'rotate-180' : ''}`}
                    />
                </button>
            </h3>
            {kept && (
                <div
                    ref={bodyRef}
                    id={bodyId}
                    data-fold-body
                    className="border-t border-slate-100"
                    aria-hidden={open ? undefined : true}
                    {...(open ? {} : { inert: '' })}
                >
                    {children}
                </div>
            )}
        </section>
    );
};

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

/* Заявку вернули инициатору — кто и зачем. Стоит и на странице заявки (причина видна
   сразу, как только её открыли), и в форме задачи (под рукой, когда отвечают). */
export const ReturnNotice = ({ request, subtask }) => {
    const asked = request?.clarify_reason;
    if (!asked) return null;
    return (
        <NoticeBox text="">
            <span className="font-medium">
                {asked === 'rework' ? 'Вернули на доработку' : (subtask?.clarify_label || 'Требуется уточнение')}
                {request.clarify_by_name ? ` — ${request.clarify_by_name}` : ''}.
            </span>
            {request.clarify_comment ? ` ${request.clarify_comment}` : ''}
        </NoticeBox>
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
