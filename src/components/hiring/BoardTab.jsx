import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronRight, ChevronsLeft, ChevronsRight, Phone, Plus, RefreshCw, Repeat, Search, X } from 'lucide-react';
import CustomSelect from '../ui/CustomSelect';
import IosDateRangePicker from '../ui/DateRangePicker';
import { IosModal, IosSegmented, iosBtnPrimary, iosBtnSecondary, iosCard } from '../ui/ios';
import { quickPeriods } from '../ui/periodRange';
import {
    ARCHIVE_STAGE, DATED_STAGES, POOL_STAGES, STAGE_TONES, SUBSTAGE_LABELS, SUBSTAGE_TONES, boardQuery, codeOf, columnCollapsed,
    dayKey, dayLabel, daysSince, errorOf, groupCards, groupKey, initials, isPastDay, openDaysFrom, openICorePhoneCall, shortTime,
    sourceTone, todayIso, toggleInList, visitTime, withExpanded, withExpandedDay,
} from './hiringMeta';
import { FIELD, FiltersRow, FiltersToggle, HOVER_REVEAL, ICON_BTN, Label, Loading, Pill, SourcePill, TOOLBAR_BTN, TOOLBAR_BTN_PRIMARY } from './hiringUi';

/*
 * Вкладка «Воронка» (ТЗ 4): канбан из десяти колонок ТЗ 4.1 в его порядке. Карточка идёт
 * слева направо; «Отказ» и «Отсев» — завершающие. Перетаскивание открывает карточку с уже
 * выбранным переходом — у каждого перехода свои обязательные поля (дата визита, группа,
 * причина), и бросать карточку «вслепую» нельзя. Пока карточку тянут, колонки, куда ей можно,
 * подсвечены, остальные приглушены — путь по правилам ТЗ 4.2 виден до броска.
 *
 * Десять колонок шире экрана: любую можно свернуть в узкую полоску со счётчиком (запоминается
 * в браузере), завершающие обычно так и держат; полоса прокрутки под доской тонкая и толстеет под
 * курсором. Рекрутер видит пул и свои карточки; у удалённого рекрутера в пуле нет телефонов (сервер
 * их не присылает). «Взять в работу» — маленькой кнопкой в углу карточки пула, по наведению;
 * «Позвонить» — с карточки в работе, в iCore Phone.
 *
 * «Отклики» поделены по источникам (владелец 20.11.2026: «столбец Отклики разделить на каналы с названием и
 * кол-вом», каналы слиты с источниками): у группы — цвет источника, название и сколько откликов. Группы свёрнуты,
 * пока их не раскрыли (решение владельца 20.11.2026: сначала видно все каналы с числами), раскрытые запоминаются в
 * браузере; карточки сервер присылает только раскрытым, у длинной — «Показать ещё».
 *
 * Период — общий для всех колонок, в панели рядом с «Фильтрами» (владелец 24.11.2026: «сделать пикер периода общим на
 * все столбцы и переместить наверх к фильтрам»; до этого он был только в шапке «Откликов»): по дате отклика — доска
 * показывает, где сейчас кандидаты, откликнувшиеся за этот период.
 *
 * «Приглашен в офис» и «Приглашен на обучение» поделены по дням (владелец 24.11.2026: «разбивку по датам приглашения
 * начиная от сегодня… как в откликах раскрыть и посмотреть людей, которые приглашены к этому дню»; «прошлые дни должны
 * уходить вниз»): в офисе — день визита, на обучении — старт группы. Сверху сегодня и дальше по порядку, ниже, под
 * подписью «Прошедшие дни», — прошедшие от вчерашнего назад. Дни свёрнуты и раскрываются так же, как группы «Откликов»
 * (раскрытые помнит браузер); в дне визита у карточки — время визита.
 *
 * Последняя колонка — «Не отвечает звонок/чат» (владелец 20.11.2026: «отдельный список как архив», «по умолчанию
 * завёрнутый и серый»): туда сторож уводит из «В работе» тех, кто 72 часа не отвечает ни на звонок, ни на WhatsApp.
 * Свёрнута, пока её не раскрыли (раскрытие запоминается в браузере), и приглушена серым.
 */

// Колонка растёт, когда экран широкий (на большом мониторе все десять этапов без прокрутки), но не уже 236 px.
const COLUMN_WIDTH = 'min-w-[236px] flex-1 basis-[272px]';
const COLLAPSED_KEY = 'hiring.board.collapsed';
const OPEN_GROUPS_KEY = 'hiring.board.openSources';
// Раскрытые дни «Приглашен в офис» / «Приглашен на обучение»: {этап: [дни]}.
const OPEN_DAYS_KEY = 'hiring.board.openDays';
// Раскрытый архив «Не отвечает звонок/чат»: по умолчанию свёрнут — в прежних списках свёрнутых его нет.
const ARCHIVE_OPEN_KEY = 'hiring.board.openArchive';
// Быстрые периоды — как у остальных разделов сайта: «Сегодня», «Неделя», «Месяц», «Весь период»; виды — дни и месяцы.
const PERIOD_PRESETS = quickPeriods();
const PERIOD_VIEWS = ['days', 'months'];
// Период — кнопкой панели, как «Фильтры». Обводка в покое — рамкой, а не кольцом: тёмная тема красит кольцо
// ring-slate-200/70 через !important, и синее кольцо фокуса с клавиатуры под ним пропадало бы (разбор 20.11.2026).
const PERIOD_TRIGGER = 'inline-flex h-9 shrink-0 items-center gap-1.5 rounded-xl border px-3 text-[13px] font-medium '
    + 'shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/70 '
    + '[&>span]:whitespace-nowrap';
// Отбор по периоду включён — кнопка синеет: иначе меньшие числа колонок читались бы как «кандидатов мало».
const periodTrigger = (active) => `${PERIOD_TRIGGER} ${active
    ? 'border-blue-200 bg-blue-50 text-blue-700'
    : 'border-slate-200/70 bg-white text-slate-700 hover:bg-slate-50'}`;
// Кнопка пула — свой класс в styles.css (.hiring-take-btn), а не утилиты: с мышью она проявляется с задержкой и до
// этого щелчков не берёт (быстрый щелчок в угол открывает карточку, а не берёт её), а тёмная тема перекрашивает
// bg-blue-50/text-blue-700 через !important — синеть под курсором утилиты бы перестали.
const TAKE_BTN = 'hiring-take-btn';
const EMPTY_FILTERS = { vacancy_id: '', city: '', source_id: '', recruiter_id: '' };

const readList = (key) => {
    try {
        const raw = JSON.parse(window.localStorage.getItem(key) || '[]');
        return Array.isArray(raw) ? raw : [];
    } catch (error) {
        return [];
    }
};

const readOpenDays = () => {
    try {
        return openDaysFrom(JSON.parse(window.localStorage.getItem(OPEN_DAYS_KEY) || '{}'));
    } catch (error) {
        return openDaysFrom(null);
    }
};

const saveStored = (key, value) => {
    try {
        window.localStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
        // Приватное окно: свёрнутое просто не запомнится.
    }
};

/* showSource — плашка источника на карточке; в группах «Откликов» её нет: источник уже в шапке группы. inGroup — карточка
   под липкой шапкой группы; visit — время визита в офис, когда день визита уже в шапке. */
const CardTile = ({
    card, canTake, canCall, onOpen, onTake, onCall, draggable, onDragStart, onDragEnd, showSource = true, inGroup = false, visit = '',
}) => {
    const idle = card.last_touch_at || card.taken_at;
    const idleDays = idle && !POOL_STAGES.includes(card.stage) && !card.is_closed ? daysSince(idle) : null;
    const overdue = card.next_contact_at && !card.is_closed && daysSince(card.next_contact_at) > 0;
    const line2 = [card.vacancy_name || card.vacancy_text, card.city].filter(Boolean).join(' · ');
    const inPool = POOL_STAGES.includes(card.stage);
    return (
        <article
            draggable={draggable}
            onDragStart={draggable ? (event) => onDragStart(event, card) : undefined}
            onDragEnd={draggable ? onDragEnd : undefined}
            // В группе (источник «Откликов», день) над карточкой липнет шапка группы: прокрутка к карточке в фокусе оставляет
            // под шапку место.
            className={`${iosCard} group relative cursor-pointer p-3 transition hover:ring-slate-300 ${draggable ? 'active:cursor-grabbing' : ''} ${inGroup ? 'scroll-mt-12' : ''}`}
            onClick={() => onOpen(card.id)}
            role="button"
            tabIndex={0}
            // Enter — только на самой карточке: на кнопке внутри (взять, позвонить) он делает своё и карточку не открывает.
            onKeyDown={(event) => { if (event.key === 'Enter' && event.target === event.currentTarget) onOpen(card.id); }}
        >
            <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                    <div className="truncate text-[13.5px] font-semibold text-slate-900">
                        {card.full_name || 'Без имени'}
                    </div>
                    {line2 && <div className="truncate text-[12px] text-slate-500">{line2}</div>}
                </div>
                {card.recruiter_name && (
                    <span title={card.recruiter_name}
                          className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-600">
                        {initials(card.recruiter_name)}
                    </span>
                )}
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-1.5 empty:hidden">
                {showSource && card.source_name && <SourcePill name={card.source_name} color={card.source_color} />}
                {card.substage && <Pill tone={SUBSTAGE_TONES[card.substage]}>{SUBSTAGE_LABELS[card.substage]}</Pill>}
                {card.repeat_of_card_id && <Pill tone="blue" title="Повторное обращение"><Repeat size={11} /> повторно</Pill>}
                {card.no_answer_suggest && <Pill tone="amber">3 недозвона</Pill>}
                {overdue && <Pill tone="red">просрочено</Pill>}
            </div>
            <div className="mt-2 flex items-center justify-between gap-2 text-[11.5px] tabular-nums text-slate-400">
                {/* Вернувшаяся в пул карточка помнит номер кандидата — дату поступления держим рядом с ним, справа кнопка. */}
                <span className="shrink-0">
                    {card.candidate_no ? `№${card.candidate_no}${inPool ? ` · ${shortTime(card.received_at)}` : ''}` : shortTime(card.received_at)}
                </span>
                {canTake && inPool ? (
                    /* «Взять в работу» — маленькой кнопкой в правом углу (владелец 18.11.2026: широкая кнопка во всю
                       карточку ловила щелчки, которыми открывали карточку). С мышью — по наведению, на сенсорном —
                       всегда; светлая, синеет только под самим курсором. */
                    <button type="button" onClick={(event) => { event.stopPropagation(); onTake(card); }}
                            className={TAKE_BTN}>
                        Взять в работу
                    </button>
                ) : (
                    <span className="truncate">
                        {visit ? `визит ${visit}` : card.next_contact_at && !card.is_closed ? `контакт ${shortTime(card.next_contact_at)}`
                            : idleDays !== null && idleDays >= 7 ? `без касаний ${idleDays} дн` : card.candidate_no ? shortTime(card.received_at) : ''}
                    </span>
                )}
            </div>
            {canCall && (
                <button type="button" aria-label="Позвонить" title="Позвонить из iCore Phone"
                        onClick={(event) => { event.stopPropagation(); onCall(card); }}
                        className={`${ICON_BTN} absolute bottom-1.5 right-1.5 bg-white ring-1 ring-slate-200/70 ${HOVER_REVEAL}`}>
                    <Phone size={14} />
                </button>
            )}
        </article>
    );
};

/* Группа колонки: источник «Откликов» (цветная точка и название) или день «Приглашен в офис / на обучение». Шапка —
   стрелка, подпись и сколько карточек (всего, с учётом отборов и периода, а не только показанных); шапка липнет к верху
   колонки, пока листаешь её карточки. Свёрнутая группа — одна строка, и это вид по умолчанию: сначала видно все группы с
   числами (решение владельца 20.11.2026). Раскрыли — карточки приходят с сервера, до тех пор — «Загружаем…». Не все
   пришли — «Показать ещё N» (у группы без источника или без даты раскрывать нечем — сервер раскрывает группу по id
   источника или по дате, поэтому там кнопки нет); после догрузки фокус встаёт на первую новую карточку, а не теряется
   вместе с кнопкой. muted — прошедший день: подпись тише. */
const BoardGroup = ({ label, dot = '', muted = false, total, open, cards, expandable, onToggle, onExpand, children }) => {
    const box = useRef(null);
    const focusFrom = useRef(null);
    const waiting = open && cards.length === 0 && Number(total || 0) > 0;
    const hidden = open && expandable && !waiting ? Math.max(0, Number(total || 0) - cards.length) : 0;
    useEffect(() => {
        if (focusFrom.current === null || cards.length <= focusFrom.current) return;
        box.current?.querySelectorAll(':scope > article')[focusFrom.current]?.focus();
        focusFrom.current = null;
    }, [cards.length]);
    return (
        <div ref={box} className="flex flex-col gap-2">
            <button type="button" onClick={onToggle} aria-expanded={open}
                    title={open ? `Свернуть «${label}»` : `Развернуть «${label}»`}
                    className="sticky top-0 z-10 flex w-full items-center gap-2 rounded-xl bg-slate-100/95 px-2 py-1.5 text-left backdrop-blur-sm transition hover:bg-slate-200/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/70">
                <ChevronRight size={13} className={`shrink-0 text-slate-400 transition-transform ${open ? 'rotate-90' : ''}`} />
                {dot && <span aria-hidden="true" className={`h-2.5 w-2.5 shrink-0 rounded-full ${dot}`} />}
                <span className={`min-w-0 truncate text-[12.5px] font-semibold ${muted ? 'text-slate-500' : 'text-slate-700'}`}>{label}</span>
                <span className="ml-auto shrink-0 rounded-full bg-white px-2 py-0.5 text-[11.5px] font-semibold tabular-nums text-slate-500 ring-1 ring-slate-200/70">
                    {total}
                </span>
            </button>
            {open && children}
            {waiting && <div className="py-3 text-center text-[12px] text-slate-400">Загружаем…</div>}
            {hidden > 0 && (
                <button type="button" onClick={() => { focusFrom.current = cards.length; onExpand(); }}
                        className="rounded-xl py-1.5 text-center text-[12px] font-medium text-blue-700 transition hover:bg-white/70">
                    Показать ещё {hidden}
                </button>
            )}
        </div>
    );
};

const Column = ({
    column, caps, dropState, collapsed, onToggle, onOpen, onTake, onCall, canDrag, canCall, onDragStart, onDragEnd, onDrop,
    openGroups = [], onToggleGroup, onExpandGroup, today, openDays = [], onToggleDay, onExpandDay,
}) => {
    const [over, setOver] = useState(false);
    const grouped = Array.isArray(column.groups);
    const dated = Array.isArray(column.days);
    const cardsById = useMemo(() => new Map(column.cards.map((card) => [card.id, card])), [column.cards]);
    const tone = STAGE_TONES[column.stage];
    const archive = column.stage === ARCHIVE_STAGE;
    const titleClass = tone === 'red' ? 'text-rose-700' : tone === 'green' ? 'text-emerald-700' : archive ? 'text-slate-400' : 'text-slate-700';
    const surface = over && dropState !== 'invalid' ? 'bg-blue-50 ring-2 ring-blue-400'
        : dropState === 'valid' ? 'bg-blue-50/60 ring-2 ring-blue-200'
            : dropState === 'invalid' ? 'bg-slate-100/80 opacity-45' : archive ? 'bg-slate-100/50' : 'bg-slate-100/80';
    const dragProps = {
        onDragOver: (event) => { event.preventDefault(); setOver(true); },
        onDragLeave: () => setOver(false),
        onDrop: (event) => { event.preventDefault(); setOver(false); onDrop(event, column.stage); },
    };
    if (collapsed) {
        return (
            <button type="button" onClick={onToggle} title={`Развернуть «${column.label}»`} {...dragProps}
                    className={`flex min-h-[220px] w-11 min-w-[44px] flex-col items-center gap-2 rounded-2xl py-3 transition hover:bg-slate-200/70 ${surface}`}>
                <ChevronsRight size={14} className="text-slate-400" />
                <span className={`rounded-full px-1.5 py-0.5 text-[11.5px] font-semibold tabular-nums ring-1 ${archive
                    ? 'bg-slate-100 text-slate-400 ring-slate-200/60' : 'bg-white text-slate-500 ring-slate-200/70'}`}>{column.total}</span>
                <span className={`text-[12.5px] font-semibold [writing-mode:vertical-rl] ${titleClass}`}>{column.label}</span>
            </button>
        );
    }
    const tile = (card, extra = {}) => (
        <CardTile key={card.id} card={card} canTake={caps.take} canCall={canCall(card)} onOpen={onOpen} onTake={onTake} onCall={onCall}
                  draggable={canDrag(card)} onDragStart={onDragStart} onDragEnd={onDragEnd} {...extra} />
    );
    // Дни колонки идут в порядке сервера: сегодня и дальше, ниже прошедшие — перед первым из них подпись «Прошедшие дни».
    const dayGroup = (group, index) => {
        const key = dayKey(group);
        const past = isPastDay(group.day, today);
        const cards = groupCards(group, cardsById);
        return (
            <React.Fragment key={key}>
                {past && !isPastDay(column.days[index - 1]?.day, today) && (
                    <div className="px-2 pt-1 text-[11.5px] font-medium text-slate-400">Прошедшие дни</div>
                )}
                <BoardGroup label={dayLabel(group.day, today)} muted={past} total={group.total} open={openDays.includes(key)}
                            cards={cards} expandable={key !== 'none'} onToggle={() => onToggleDay(key)} onExpand={() => onExpandDay(group)}>
                    {cards.map((card) => tile(card, { inGroup: true, visit: column.stage === 'invited_office' ? visitTime(card) : '' }))}
                </BoardGroup>
            </React.Fragment>
        );
    };
    return (
        <section className={`${COLUMN_WIDTH} group/column flex max-h-[calc(100vh-220px)] flex-col rounded-2xl p-2 transition ${surface}`} {...dragProps}>
            <header className="flex items-center gap-1.5 px-2 pb-2 pt-1">
                <span className={`min-w-0 truncate text-[12.5px] font-semibold ${titleClass}`}>{column.label}</span>
                <span className="shrink-0 rounded-full bg-white px-2 py-0.5 text-[11.5px] font-semibold tabular-nums text-slate-500 ring-1 ring-slate-200/70">
                    {column.total}
                </span>
                <button type="button" onClick={onToggle} aria-label={`Свернуть «${column.label}»`} title="Свернуть"
                        className="ml-auto grid h-6 w-6 shrink-0 place-items-center rounded-full text-slate-400 opacity-0 transition hover:bg-white hover:text-slate-600 focus-visible:opacity-100 group-hover/column:opacity-100">
                    <ChevronsLeft size={14} />
                </button>
            </header>
            <div className={`thin-scroll flex flex-col gap-2 overflow-y-auto px-0.5 pb-1 ${archive ? 'opacity-75 transition-opacity hover:opacity-100' : ''}`}>
                {grouped ? column.groups.map((group) => {
                    const cards = groupCards(group, cardsById);
                    return (
                        <BoardGroup key={groupKey(group)} label={group.name} dot={sourceTone(group.color).dot} total={group.total}
                                    open={openGroups.includes(groupKey(group))} cards={cards}
                                    expandable={group.source_id !== null && group.source_id !== undefined}
                                    onToggle={() => onToggleGroup(groupKey(group))} onExpand={() => onExpandGroup(group)}>
                            {cards.map((card) => tile(card, { showSource: false, inGroup: true }))}
                        </BoardGroup>
                    );
                }) : dated ? column.days.map(dayGroup) : column.cards.map((card) => tile(card))}
                {column.total === 0 && (
                    <div className="py-6 text-center text-[12px] text-slate-400">Пусто</div>
                )}
                {!grouped && !dated && column.total > column.cards.length && (
                    <div className="py-2 text-center text-[11.5px] text-slate-400">
                        Показаны {column.cards.length} из {column.total} — уточните отбор
                    </div>
                )}
            </div>
        </section>
    );
};

/* Отклик вручную (ТЗ 5.2): ФИО, телефон, город, вакансия, источник обязателен. */
const AddResponseModal = ({ open, onClose, api, toast, boot, onCreated }) => {
    const empty = { full_name: '', phone: '', whatsapp: '', city: '', vacancy_id: '', source_id: '', referred_by: '', comment: '' };
    const [form, setForm] = useState(empty);
    const [saving, setSaving] = useState(false);
    useEffect(() => { if (open) setForm(empty); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
    const sources = (boot.dictionaries?.sources || []).filter((s) => s.kind === 'responses' && s.is_active !== false);
    const vacancies = (boot.dictionaries?.vacancies || []).filter((v) => v.is_active !== false);
    const set = (key) => (value) => setForm((prev) => ({ ...prev, [key]: value?.target ? value.target.value : value }));
    const submit = async () => {
        if (!form.phone.trim()) return toast('Укажите телефон', 'error');
        if (!form.source_id) return toast('Укажите источник отклика', 'error');
        setSaving(true);
        try {
            const { data } = await api.post('/cards', {
                full_name: form.full_name, phone: form.phone, whatsapp: form.whatsapp || null, city: form.city || null,
                vacancy_id: form.vacancy_id ? Number(form.vacancy_id) : null, source_id: Number(form.source_id),
                referred_by: form.referred_by || null, comment: form.comment || null,
            });
            toast(data.outcome === 'repeat' ? 'По этому телефону уже есть открытая карточка — отклик дописан в неё' : 'Отклик добавлен в пул', 'success');
            onCreated(data.card);
            onClose();
        } catch (error) {
            toast(errorOf(error, 'Не удалось добавить отклик'), 'error');
        } finally {
            setSaving(false);
        }
        return undefined;
    };
    const isReferral = sources.find((s) => String(s.id) === String(form.source_id))?.code === 'referral';
    return (
        <IosModal open={open} onClose={onClose} title="Добавить отклик" subtitle="Кандидат откликнулся сам — телефон и источник обязательны"
                  footer={(
                      <div className="flex justify-end gap-2">
                          <button type="button" className={iosBtnSecondary} onClick={onClose}>Отмена</button>
                          <button type="button" className={iosBtnPrimary} onClick={submit} disabled={saving}>Добавить</button>
                      </div>
                  )}>
            <div className="grid gap-3 sm:grid-cols-2">
                <div className="sm:col-span-2">
                    <Label required>Телефон</Label>
                    <input className={FIELD} value={form.phone} onChange={set('phone')} placeholder="+7 705 123 45 67" inputMode="tel" />
                </div>
                <div className="sm:col-span-2">
                    <Label>ФИО</Label>
                    <input className={FIELD} value={form.full_name} onChange={set('full_name')} />
                </div>
                <div>
                    <Label required hint="Откуда пришёл отклик: площадка, реклама, знакомый. По источнику считается эффективность и мотивация.">Источник</Label>
                    <CustomSelect variant="ios" value={form.source_id} onChange={set('source_id')} placeholder="Выберите источник"
                                  options={sources.map((s) => ({ value: String(s.id), label: s.name }))} invalid={!form.source_id} />
                </div>
                <div>
                    <Label>Вакансия</Label>
                    <CustomSelect variant="ios" value={form.vacancy_id} onChange={set('vacancy_id')} placeholder="Не указана"
                                  options={[{ value: '', label: 'Не указана' }, ...vacancies.map((v) => ({ value: String(v.id), label: v.name }))]} />
                </div>
                <div>
                    <Label>Город</Label>
                    <input className={FIELD} value={form.city} onChange={set('city')} />
                </div>
                <div>
                    <Label hint="Если WhatsApp на другом номере.">WhatsApp</Label>
                    <input className={FIELD} value={form.whatsapp} onChange={set('whatsapp')} placeholder="как телефон" inputMode="tel" />
                </div>
                {isReferral && (
                    <div className="sm:col-span-2">
                        <Label hint="Сотрудник или кандидат, который привёл человека по «Приведи друга».">Кто привёл</Label>
                        <input className={FIELD} value={form.referred_by} onChange={set('referred_by')} />
                    </div>
                )}
                <div className="sm:col-span-2">
                    <Label>Комментарий</Label>
                    <textarea className={`${FIELD} min-h-[72px]`} value={form.comment} onChange={set('comment')} />
                </div>
            </div>
        </IosModal>
    );
};

const BoardTab = ({ api, toast, boot, caps, onOpenCard, version, onChanged }) => {
    // «Мои и пул» — отбор рекрутера; тренер и руководитель отдела видят свой круг целиком (сервер
    // отбор mine у них и не применяет), руководитель рекрутинга — всё с переключателем.
    const [scope, setScope] = useState(caps.lead || !caps.recruiter ? 'all' : 'mine');
    const [query, setQuery] = useState('');
    const [filters, setFilters] = useState(EMPTY_FILTERS);
    const [filtersOpen, setFiltersOpen] = useState(false);
    const [columns, setColumns] = useState(null);
    const [loading, setLoading] = useState(true);
    const [adding, setAdding] = useState(false);
    const [dragging, setDragging] = useState(null);
    const [collapsed, setCollapsed] = useState(() => readList(COLLAPSED_KEY));
    const [openedArchive, setOpenedArchive] = useState(() => readList(ARCHIVE_OPEN_KEY));
    // Период — на все колонки (по дате отклика). «Отклики»: раскрытые группы источников (по умолчанию свёрнуты все —
    // решение владельца 20.11.2026) и группы, где нажали «Показать ещё».
    const [period, setPeriod] = useState({ from: '', to: '' });
    const [openGroups, setOpenGroups] = useState(() => readList(OPEN_GROUPS_KEY).map(String));
    const [expanded, setExpanded] = useState([]);
    // «Приглашен в офис / на обучение»: раскрытые дни ({этап: [дни]}, свёрнуты все, пока не раскрыли), дни с «Показать ещё»
    // и сегодняшний день сервера — по нему подписаны «Сегодня» и «Завтра».
    const [openDays, setOpenDays] = useState(readOpenDays);
    const [expandedDays, setExpandedDays] = useState({});
    const [today, setToday] = useState(todayIso);
    const timer = useRef(null);
    const requestSeq = useRef(0);
    const [debounced, setDebounced] = useState('');

    useEffect(() => {
        clearTimeout(timer.current);
        timer.current = setTimeout(() => setDebounced(query.trim()), 350);
        return () => clearTimeout(timer.current);
    }, [query]);

    // На доске — ответ на последний запрос: выбрали период, не дождавшись ответа, выбрали другой — запоздавший первый
    // ответ уже не перетрёт второй (разбор 20.11.2026: подпись периода и числа расходились).
    const load = useCallback(() => {
        const seq = requestSeq.current + 1;
        requestSeq.current = seq;
        setLoading(true);
        return api.get('/board', boardQuery({
            mine: scope === 'mine', query: debounced, filters, period, expanded, open: openGroups, openDays, expandedDays,
        }))
            .then(({ data }) => {
                if (seq !== requestSeq.current) return;
                setColumns(data.columns);
                if (data.today) setToday(data.today);
            })
            .catch((error) => { if (seq === requestSeq.current) toast(errorOf(error, 'Не удалось загрузить доску'), 'error'); })
            .finally(() => { if (seq === requestSeq.current) setLoading(false); });
    }, [api, scope, debounced, filters, period, expanded, openGroups, openDays, expandedDays, toast]);

    useEffect(() => { load(); }, [load, version]);

    const dictionaries = boot.dictionaries || {};
    const options = useMemo(() => ({
        vacancies: [{ value: '', label: 'Все вакансии' }, ...(dictionaries.vacancies || []).map((v) => ({ value: String(v.id), label: v.name }))],
        cities: [{ value: '', label: 'Все города' }, ...(dictionaries.cities || []).map((c) => ({ value: c, label: c }))],
        sources: [{ value: '', label: 'Все источники' }, ...(dictionaries.sources || []).map((s) => ({ value: String(s.id), label: s.name }))],
        recruiters: [{ value: '', label: 'Все рекрутеры' }, ...(dictionaries.recruiters || []).map((r) => ({ value: String(r.id), label: r.name }))],
    }), [dictionaries]);

    const take = async (card) => {
        try {
            await api.post(`/cards/${card.id}/take`);
            toast(`${card.full_name || 'Карточка'} — в работе у вас`, 'success');
            onChanged();
        } catch (error) {
            toast(codeOf(error) === 'HIRING_ALREADY_TAKEN' ? 'Уже в работе' : errorOf(error, 'Не удалось взять в работу'), 'error');
            load();
        }
    };
    // Звонок — в обработчике клика (внешнюю схему браузер пускает только по жесту), карточка — с окном результата.
    const call = (card) => {
        try {
            openICorePhoneCall(card.phone);
        } catch (error) {
            toast(error.message || 'Номер для звонка не разобран', 'error');
            return;
        }
        onOpenCard(card.id, { call: true });
    };

    const isMineOrLead = (card) => caps.lead || Number(card.recruiter_id) === Number(boot.me?.id);
    const canCall = (card) => Boolean(card.phone) && !POOL_STAGES.includes(card.stage) && !card.is_closed && isMineOrLead(card);
    const canDrag = (card) => {
        if (card.is_closed && !caps.lead) return false;
        if (POOL_STAGES.includes(card.stage)) return Boolean(caps.take);
        return isMineOrLead(card)
            || (card.group_id && (caps.trainer_group_ids || []).includes(Number(card.group_id)))
            || (card.department_id && (caps.headed_ids || []).includes(Number(card.department_id)));
    };
    // Куда карточке можно: из пула — только «В работе» (атомарное закрепление), дальше — цели ТЗ 4.2.
    const targetsOf = (card) => (POOL_STAGES.includes(card.stage) ? (caps.take ? ['in_work'] : []) : (card.targets || []));
    const onDragStart = (event, card) => {
        event.dataTransfer.setData('text/plain', String(card.id));
        event.dataTransfer.effectAllowed = 'move';
        setDragging(card);
    };
    const onDragEnd = () => setDragging(null);
    const onDrop = (event, stage) => {
        setDragging(null);
        const id = Number(event.dataTransfer.getData('text/plain'));
        if (!id) return;
        const card = (columns || []).flatMap((column) => column.cards).find((item) => item.id === id);
        if (!card || card.stage === stage) return;
        if (POOL_STAGES.includes(card.stage)) {
            if (stage === 'in_work' && caps.take) take(card);
            else toast('Из пула карточка берётся только в работу', 'error');
            return;
        }
        if (!(card.targets || []).includes(stage)) {
            toast(`Из «${(columns || []).find((c) => c.stage === card.stage)?.label}» в «${(columns || []).find((c) => c.stage === stage)?.label}» карточка не переходит`, 'error');
            return;
        }
        onOpenCard(id, { move: stage });
    };
    const dropStateOf = (stage) => {
        if (!dragging || dragging.stage === stage) return null;
        return targetsOf(dragging).includes(stage) ? 'valid' : 'invalid';
    };
    const toggleCollapsed = (stage) => {
        if (stage === ARCHIVE_STAGE) {
            setOpenedArchive((prev) => {
                const next = toggleInList(prev, stage);
                saveStored(ARCHIVE_OPEN_KEY, next);
                return next;
            });
            return;
        }
        setCollapsed((prev) => {
            const next = toggleInList(prev, stage);
            saveStored(COLLAPSED_KEY, next);
            return next;
        });
    };
    const toggleGroup = (key) => setOpenGroups((prev) => {
        const next = toggleInList(prev, key);
        saveStored(OPEN_GROUPS_KEY, next);
        return next;
    });
    // «Показать ещё» — источник уходит в expand, сервер отдаёт его отклики целиком (до тысячи).
    const expandGroup = (group) => setExpanded((prev) => withExpanded(prev, group));
    const toggleDay = (stage, key) => setOpenDays((prev) => {
        const next = openDaysFrom({ ...prev, [stage]: toggleInList(prev[stage], key) });
        saveStored(OPEN_DAYS_KEY, next);
        return next;
    });
    const expandDay = (stage, group) => setExpandedDays((prev) => ({ ...prev, [stage]: withExpandedDay(prev[stage], group) }));

    const activeFilters = Object.values(filters).filter(Boolean).length;
    return (
        <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
                {caps.lead && (
                    <IosSegmented value={scope} onChange={setScope} ariaLabel="Чьи карточки"
                                  options={[{ value: 'mine', label: 'Мои и пул' }, { value: 'all', label: 'Все' }]} />
                )}
                <label className="relative">
                    <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Имя, телефон, город, №"
                           className="h-9 w-60 rounded-xl bg-white pl-8 pr-8 text-[13px] text-slate-900 ring-1 ring-slate-200/70 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500/70" />
                    {query && (
                        <button type="button" aria-label="Очистить" onClick={() => setQuery('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"><X size={14} /></button>
                    )}
                </label>
                <FiltersToggle open={filtersOpen} count={activeFilters} onToggle={() => setFiltersOpen((v) => !v)} />
                {/* Период — на все колонки, по дате отклика (владелец 24.11.2026). Календарь — общий сайта, в портале. */}
                <IosDateRangePicker portal from={period.from} to={period.to} max={todayIso()} presets={PERIOD_PRESETS} views={PERIOD_VIEWS}
                                    triggerClassName={periodTrigger(Boolean(period.from || period.to))}
                                    onChange={({ from, to }) => setPeriod({ from: from || '', to: to || '' })} />
                <div className="ml-auto flex items-center gap-2">
                    <button type="button" className={TOOLBAR_BTN} onClick={load} disabled={loading} aria-label="Обновить" title="Обновить">
                        <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
                    </button>
                    {caps.add_response && (
                        <button type="button" className={TOOLBAR_BTN_PRIMARY} onClick={() => setAdding(true)}>
                            <Plus size={14} /> Добавить отклик
                        </button>
                    )}
                </div>
            </div>
            <FiltersRow open={filtersOpen} count={activeFilters} onReset={() => setFilters(EMPTY_FILTERS)}>
                <CustomSelect variant="ios" value={filters.vacancy_id} onChange={(v) => setFilters((f) => ({ ...f, vacancy_id: v }))} options={options.vacancies} className="w-44" ariaLabel="Вакансия" />
                <CustomSelect variant="ios" value={filters.city} onChange={(v) => setFilters((f) => ({ ...f, city: v }))} options={options.cities} className="w-40" ariaLabel="Город" searchable />
                <CustomSelect variant="ios" value={filters.source_id} onChange={(v) => setFilters((f) => ({ ...f, source_id: v }))} options={options.sources} className="w-44" ariaLabel="Источник" />
                {(caps.lead || caps.read_all) && (
                    <CustomSelect variant="ios" value={filters.recruiter_id} onChange={(v) => setFilters((f) => ({ ...f, recruiter_id: v }))} options={options.recruiters} className="w-44" ariaLabel="Рекрутер" searchable />
                )}
            </FiltersRow>

            {!columns ? <Loading text="Загружаем доску…" /> : (
                /* Нижняя полоса прокрутки — своя, тонкая до наведения (владелец 18.11.2026): .board-scroll в styles.css. */
                <div className="board-scroll -mx-3 overflow-x-auto px-3 pb-1 sm:-mx-5 sm:px-5">
                    <div className="flex items-start gap-3">
                        {columns.map((column) => (
                            <Column key={column.stage} column={column} caps={caps} dropState={dropStateOf(column.stage)}
                                    collapsed={columnCollapsed(column.stage, collapsed, openedArchive)} onToggle={() => toggleCollapsed(column.stage)}
                                    onOpen={onOpenCard} onTake={take} onCall={call} canDrag={canDrag} canCall={canCall}
                                    onDragStart={onDragStart} onDragEnd={onDragEnd} onDrop={onDrop}
                                    {...(column.stage === 'responses' ? {
                                        openGroups, onToggleGroup: toggleGroup, onExpandGroup: expandGroup,
                                    } : DATED_STAGES.includes(column.stage) ? {
                                        today, openDays: openDays[column.stage] || [], onToggleDay: (key) => toggleDay(column.stage, key),
                                        onExpandDay: (group) => expandDay(column.stage, group),
                                    } : {})} />
                        ))}
                    </div>
                </div>
            )}

            <AddResponseModal open={adding} onClose={() => setAdding(false)} api={api} toast={toast} boot={boot}
                              onCreated={() => onChanged()} />
        </div>
    );
};

export default BoardTab;
