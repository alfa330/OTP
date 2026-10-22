import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import {
    BadgeCheck, Banknote, Boxes, ChevronRight, CreditCard, FileCheck2, Inbox, Landmark, MessageCircleQuestion,
    PackageCheck, Paperclip, PencilLine, ReceiptText, Send,
} from 'lucide-react';
import { iosCard } from '../ui/ios';
import {
    DESK_PAGE_SIZE, TASK_FORMS, deskAnswer, deskLayout, deskSkeletonRows, deskTaskAbout, deskTaskReturned, dueChip,
    fmtMoney, netAmount, ownRequestStatus, ownSkeletonRows, pageRange, stageProgress, stageProgressTitle,
} from './paymentsMeta';
import { Bone, DueChip, NoticeBox, Pager, SectionTitle, errorText, useFits } from './paymentsUi';
import { recall, remember } from './paymentsEntry';

/*
 * «Мои задачи» — персональный рабочий стол (ТЗ «Закуп и оплата», п. 16).
 *
 * Здесь только то, по чему от человека ждут действия: его согласования, счета
 * и пополнения его подразделения, возвраты на доработку, «приложите чек»,
 * «подтвердите получение», постановка на учёт. Заявки, где он просто участник,
 * сюда не попадают — они в «Заявках».
 *
 * Строка отвечает на два вопроса: что сделать и по какой заявке. Щелчок
 * открывает заявку, где действие и выполняется. Длинный стол — страницами по
 * двадцать, как списки раздела «Задачи».
 *
 * Рядом с задачами — «Мои заявки»: отправленные заявки самого человека, которые идут у
 * других, — на каком этапе каждая и у кого (просьба владельца 22.10.2026 — человек сразу
 * видит свою заявку и её статус, не переходя в «Заявки»). Заявка, по которой ждут его
 * самого, стоит в задачах и второй раз не повторяется. «Все» над блоком открывает
 * «Заявки» с отбором по нему как инициатору (там и черновики, и закрытые). Нет таких
 * заявок — блока нет, и стол выглядит как раньше.
 *
 * Где блок: на широком столе — колонкой справа от задач, на узком (телефон, ноутбук с
 * развёрнутым меню) — над задачами, чтобы длинный список задач не уводил его вниз. Место
 * решается один раз, при входе, по тому, что о заявках известно: неизвестно (первый вход
 * на устройстве) — блок встаёт под задачами одной колонкой, и его появление ничего не
 * сдвигает; со следующего входа он на своём месте с первого кадра.
 *
 * Стол — первое, что видно при входе в раздел, поэтому он не «загружается» на
 * глазах: первая страница помнится с прошлого входа (paymentsEntry.js) и рисуется
 * сразу, а пока помнить нечего — стоит заготовка списка на столько строк, сколько
 * задач обещает счётчик вкладки (и своих заявок — счётчик `/ping`).
 */

/* Значок — по тому, ЧТО предстоит сделать: задачи на столе разнородные
   (согласовать, оплатить, приложить чек, принять товар), и свою находят глазами,
   не читая заголовки подряд. Цвет у значка один на все задачи; жёлтым отмечена
   только заявка, которую человеку вернули, — там ждут его ответа. У своей заявки
   значок — того, что с ней происходит сейчас: согласуют, оплачивают, ставят на учёт. */
const TASK_ICONS = {
    entity_clarify: Landmark,
    manager_approval: BadgeCheck,
    approval: BadgeCheck,
    invoice_payment: Banknote,
    card_topup: CreditCard,
    receipt_confirm: ReceiptText,
    receiving: PackageCheck,
    asset_registration: Boxes,
    closing_docs: FileCheck2,
    docs_needed: Paperclip,
};
const RETURNED_ICONS = { rework: PencilLine, clarification: MessageCircleQuestion };

const taskIcon = (task) => {
    if (task.task !== 'initiation') return TASK_ICONS[task.task] || Inbox;
    return RETURNED_ICONS[task.subtask?.status] || Send;
};

/* «Мои заявки» встают колонкой справа от задач, когда стол шире этого: на ноутбуке 1366 px
   со свёрнутым меню стол — 1166 px, колонки по ~666 и ~476 px. Меряется сам стол, а не
   окно: ширину ему оставляет ещё и меню портала. Колонки — стилем: классы сеток телефонная
   оболочка перестраивает по-своему (mobile-shell.css). */
const DESK_COLUMNS_MIN = 1100;
const DESK_COLUMNS = { display: 'grid', gridTemplateColumns: 'minmax(0, 7fr) minmax(0, 5fr)', columnGap: 24, alignItems: 'start' };

const DeskRow = ({ task, first, soonDays, onOpen }) => {
    const Icon = taskIcon(task);
    const returned = deskTaskReturned(task);
    const asked = [task.clarify_by_name, task.clarify_label].filter(Boolean).join(': ');
    const what = [task.expense_name, task.counterparty_name].filter(Boolean).join(' · ');
    const about = deskTaskAbout(task);
    /* Сумма и срок стоят справа от текста; на телефоне они забирали бы у него половину
       ширины (название заявки обрезалось до двух слов), поэтому там уходят под текст. */
    const amount = <span className="text-[14px] font-semibold tabular-nums text-slate-900">{fmtMoney(task.amount)}</span>;
    const due = dueChip(task) ? <DueChip request={task} soonDays={soonDays} /> : null;
    return (
        <button
            type="button"
            data-task={task.task}
            onClick={() => onOpen(task.id)}
            className="flex w-full items-stretch gap-3 pl-4 text-left transition hover:bg-slate-50 active:bg-slate-100"
        >
            <span className="py-3">
                <span className={`flex h-8 w-8 items-center justify-center rounded-[10px] ${returned ? 'bg-amber-50 text-amber-600' : 'bg-slate-100 text-slate-500'}`}>
                    <Icon size={16} aria-hidden="true" />
                </span>
            </span>
            {/* Линия между строками начинается от текста, а не от края — как в списках iOS. */}
            <span className={`flex min-w-0 grow basis-0 items-center gap-3 py-3 pr-3 ${first ? '' : 'border-t border-slate-100'}`}>
                <span className="min-w-0 grow basis-0">
                    <span className="block text-[14px] font-semibold leading-snug text-slate-900">{task.title}</span>
                    <span className="mt-0.5 block truncate text-[13px] text-slate-600" title={what}>{what}</span>
                    {(asked || task.clarify_comment) && (
                        <span className="mt-1 text-[12.5px] leading-[18px] text-amber-700 line-clamp-2">
                            {asked}{asked && task.clarify_comment ? ' — ' : ''}{task.clarify_comment || ''}
                        </span>
                    )}
                    <span className="mt-1 block truncate text-[12px] tabular-nums text-slate-400" title={about}>{about}</span>
                    <span className="mt-1.5 flex items-center gap-2 sm:hidden">{amount}{due}</span>
                </span>
                <span className="hidden shrink-0 flex-col items-end gap-1 sm:flex">{amount}{due}</span>
                <ChevronRight size={16} className="shrink-0 text-slate-300" aria-hidden="true" />
            </span>
        </button>
    );
};

/* Полоса хода заявки: по отрезку на этап этой заявки — пройденные зелёные, текущий синий,
   впереди серые. Цвета — те же, что у ленты этапов в окне заявки. Подписей нет: этап
   словами стоит строкой ниже, весь ход — в подсказке. Читалке экрана полоса не нужна —
   этап она прочтёт той же строкой. Отрезки — сеткой из стиля, по той же причине, что
   и колонки стола. */
const STAGE_FILL = { done: 'bg-emerald-500', current: 'bg-blue-500', pending: 'bg-slate-200' };

const StageBar = ({ lifecycle }) => {
    const progress = stageProgress(lifecycle);
    if (!progress.total) return null;
    return (
        <span
            aria-hidden="true"
            title={stageProgressTitle(progress)}
            data-stage-bar
            className="mt-2 h-1 w-full max-w-[300px]"
            style={{ display: 'grid', gridTemplateColumns: `repeat(${progress.total}, minmax(0, 1fr))`, columnGap: 3 }}
        >
            {progress.steps.map((stage) => {
                const fill = stage.state === 'current' ? 'current' : stage.state === 'done' ? 'done' : 'pending';
                return <span key={stage.code} data-stage={fill} className={`rounded-full ${STAGE_FILL[fill]}`} />;
            })}
        </span>
    );
};

/* Своя заявка: что закупаем, номер и поставщик, ход по этапам, «этап · у кого», сумма и срок.
   Название — в одну строку: строка всегда одного роста, и заготовка блока ей равна. */
const OwnRow = ({ item, first, soonDays, onOpen }) => {
    const status = ownRequestStatus(item);
    const Icon = taskIcon({ task: item.current_kind });
    const about = [`№${item.id}`, item.counterparty_name].filter(Boolean).join(' · ');
    const where = [status.label, status.who].filter(Boolean).join(' · ');
    const amount = <span className="text-[14px] font-semibold tabular-nums text-slate-900">{fmtMoney(netAmount(item))}</span>;
    const due = dueChip(item) ? <DueChip request={item} soonDays={soonDays} /> : null;
    return (
        <button
            type="button"
            data-own-request={item.id}
            onClick={() => onOpen(item.id)}
            className="flex w-full items-stretch gap-3 pl-4 text-left transition hover:bg-slate-50 active:bg-slate-100"
        >
            <span className="py-3">
                <span className="flex h-8 w-8 items-center justify-center rounded-[10px] bg-slate-100 text-slate-500">
                    <Icon size={16} aria-hidden="true" />
                </span>
            </span>
            <span className={`flex min-w-0 grow basis-0 items-center gap-3 py-3 pr-3 ${first ? '' : 'border-t border-slate-100'}`}>
                <span className="min-w-0 grow basis-0">
                    <span className="block truncate text-[14px] font-semibold leading-snug text-slate-900" title={item.expense_name}>{item.expense_name}</span>
                    <span className="mt-0.5 block truncate text-[13px] tabular-nums text-slate-600" title={about}>{about}</span>
                    <StageBar lifecycle={item.lifecycle} />
                    <span className="mt-1.5 block truncate text-[12.5px] leading-[18px]" title={where} data-own-status>
                        <span className="font-medium text-slate-900">{status.label}</span>
                        {status.who && <span className="text-slate-500"> · {status.who}</span>}
                    </span>
                    <span className="mt-1.5 flex items-center gap-2 sm:hidden">{amount}{due}</span>
                </span>
                <span className="hidden shrink-0 flex-col items-end gap-1 sm:flex">{amount}{due}</span>
                <ChevronRight size={16} className="shrink-0 text-slate-300" aria-hidden="true" />
            </span>
        </button>
    );
};

/* Заготовка списка: строка повторяет геометрию DeskRow (значок, три строки текста, сумма
   справа, на телефоне — своей строкой под текстом), поэтому задачи встают на её место без
   сдвига. */
const DeskSkeleton = ({ rows }) => (
    <div className={`${iosCard} overflow-hidden`} role="status" aria-busy="true" aria-label="Загружаем задачи" data-desk-skeleton>
        {Array.from({ length: rows }, (_, index) => (
            <div key={index} className="flex items-stretch gap-3 pl-4">
                <span className="py-3"><Bone className="h-8 w-8 !rounded-[10px]" /></span>
                <span className={`flex min-w-0 grow basis-0 items-center gap-3 py-3 pr-3 ${index ? 'border-t border-slate-100' : ''}`}>
                    <span className="min-w-0 grow basis-0">
                        <span className="flex h-[63px] flex-col justify-between py-[3px]">
                            <Bone className="h-3 w-40 max-w-full" />
                            <Bone className={`h-3 max-w-full ${index % 2 ? 'w-56' : 'w-72'}`} />
                            <Bone className="h-3 w-24" />
                        </span>
                        <span className="mt-1.5 flex h-[21px] items-center sm:hidden"><Bone className="h-3.5 w-20" /></span>
                    </span>
                    <Bone className="hidden h-3.5 w-20 shrink-0 sm:block" />
                </span>
            </div>
        ))}
    </div>
);

/* Заготовка «Моих заявок»: те же строки, что у OwnRow, с тем же кеглем и межстрочием, только
   вместо текста — полосы. Строк столько же, сколько придёт заявок (ownSkeletonRows), поэтому
   над задачами блок стоит своим ростом с первого кадра и задачи под ним не сдвигаются. */
const OwnSkeleton = ({ rows }) => (
    <div className={`${iosCard} overflow-hidden`} role="status" aria-busy="true" aria-label="Загружаем ваши заявки" data-own-skeleton>
        {Array.from({ length: rows }, (_, index) => (
            <div key={index} className="flex items-stretch gap-3 pl-4" data-own-bone>
                <span className="py-3"><Bone className="h-8 w-8 !rounded-[10px]" /></span>
                <span className={`flex min-w-0 grow basis-0 items-center gap-3 py-3 pr-3 ${index ? 'border-t border-slate-100' : ''}`}>
                    <span className="min-w-0 grow basis-0">
                        <span className="block text-[14px] leading-snug"><Bone inline className={`h-3 max-w-full ${index % 2 ? 'w-44' : 'w-56'}`} /></span>
                        <span className="mt-0.5 block text-[13px]"><Bone inline className="h-3 w-32 max-w-full" /></span>
                        <Bone className="mt-2 h-1 w-full max-w-[300px]" />
                        <span className="mt-1.5 block text-[12.5px] leading-[18px]"><Bone inline className="h-2.5 w-40 max-w-full" /></span>
                        <span className="mt-1.5 block text-[14px] sm:hidden"><Bone inline className="h-3.5 w-20" /></span>
                    </span>
                    <Bone className="hidden h-3.5 w-20 shrink-0 sm:block" />
                </span>
            </div>
        ))}
    </div>
);

const PaymentsDesk = ({
    apiBaseUrl, headers, refreshKey, soonDays, onOpen, onCount, onShowOwn = null, memoryKey = null, expected = null,
    expectedOwn = null,
}) => {
    // Первая страница с прошлого входа в раздел: стол рисуется ею сразу и обновляется фоном.
    const [remembered] = useState(() => recall(memoryKey, 'desk'));
    const [tasks, setTasks] = useState(remembered?.tasks || []);
    const [total, setTotal] = useState(remembered?.total || 0);
    // Свои заявки; null — ещё не известны: стол не приходил, и помнить нечего.
    const [own, setOwn] = useState(() => (remembered?.mine ? deskAnswer(remembered).mine : null));
    /* Сколько строк в заготовке первой загрузки — решается один раз, при входе: счётчик вкладки
       может прийти посреди неё (первый вход на устройстве), и заготовка, выросшая с трёх строк
       до шести за мгновение до списка, была бы лишним движением. */
    const [firstRows] = useState(() => deskSkeletonRows(expected));
    const [firstOwnRows] = useState(() => ownSkeletonRows(expectedOwn));
    // Известно ли при входе, что свои заявки есть, — от этого место блока (см. шапку файла).
    const [ownKnown] = useState(() => firstOwnRows > 0 || Boolean(remembered?.mine?.items?.length));
    const answered = useRef(false);
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const topRef = useRef(null);
    const [rootRef, roomy] = useFits(DESK_COLUMNS_MIN);

    const countRef = useRef(onCount);
    useEffect(() => { countRef.current = onCount; }, [onCount]);

    const ticket = useRef(0);
    const load = useCallback(async (pageNumber) => {
        const mine = ticket.current + 1;
        ticket.current = mine;
        setLoading(true);
        setError('');
        try {
            const params = new URLSearchParams({ limit: String(DESK_PAGE_SIZE), offset: String((pageNumber - 1) * DESK_PAGE_SIZE) });
            const response = await axios.get(`${apiBaseUrl}/api/payments/desk?${params}`, { headers: headers() });
            if (ticket.current !== mine) return;
            const answer = deskAnswer(response.data);
            answered.current = true;
            setTasks(answer.tasks);
            setTotal(answer.total);
            setOwn(answer.mine);
            if (pageNumber === 1) remember(memoryKey, 'desk', answer);
            countRef.current?.(answer.total);
        } catch (err) {
            if (ticket.current !== mine) return;
            answered.current = true;
            setError(errorText(err, 'Не удалось загрузить задачи'));
        } finally {
            if (ticket.current === mine) setLoading(false);
        }
    }, [apiBaseUrl, headers, memoryKey]);

    // После действия над заявкой (refreshKey) перечитывается та же страница.
    useEffect(() => { load(page); }, [load, page, refreshKey]);

    // Задачу выполнили, и последняя страница опустела, — встаём на новую последнюю.
    const lastPage = pageRange(page, DESK_PAGE_SIZE, total).page;
    useEffect(() => {
        if (!loading && total > 0 && lastPage !== page) setPage(lastPage);
    }, [loading, total, lastPage, page]);

    const goToPage = (next) => {
        setPage(next);
        const top = topRef.current?.getBoundingClientRect().top;
        if (top !== undefined && top < 0) topRef.current.scrollIntoView({ block: 'start', behavior: 'smooth' });
    };

    /* Список ещё не пришёл, и помнить нечего — стоит его заготовка. Счётчик вкладки говорит
       «ноль» — сразу пустой стол: ответ почти наверняка это подтвердит, а заготовка, сменяющаяся
       надписью «задач нет», была бы лишним движением. */
    const skeletonRows = loading && !tasks.length ? (answered.current ? deskSkeletonRows(expected) : firstRows) : 0;
    // Свои заявки до первого ответа — заготовкой по счётчику `/ping`; после — как есть.
    const ownSkeleton = own ? 0 : (loading && !answered.current ? firstOwnRows : 0);
    const ownItems = own?.items || [];
    const withOwn = !error && (ownItems.length > 0 || ownSkeleton > 0);
    const layout = deskLayout({ withOwn, ownKnown, roomy });
    const columns = layout === 'columns';
    const ownFirst = layout === 'own-first';

    let taskList;
    if (skeletonRows) {
        taskList = <DeskSkeleton rows={skeletonRows} />;
    } else if (!tasks.length) {
        /* Рядом стоят свои заявки — пустота одной строкой, иначе крупная надпись с картинкой
           занимала бы пол-экрана. */
        taskList = withOwn ? (
            <div className={`${iosCard} px-4 py-3.5 text-center text-[13.5px] text-slate-500`} data-desk-empty>Задач для вас сейчас нет</div>
        ) : (
            <div className={`${iosCard} flex flex-col items-center gap-2 px-4 py-14 text-center`} data-desk-empty>
                <Inbox size={22} className="text-slate-300" />
                <p className="text-[13.5px] text-slate-500">Задач для вас сейчас нет</p>
            </div>
        );
    } else {
        taskList = (
            <div ref={topRef} className="scroll-mt-4 space-y-3">
                <div key={page} className={`${iosCard} overflow-hidden motion-safe:animate-fade-in-soft`}>
                    {tasks.map((task, index) => (
                        <DeskRow key={`${task.id}:${task.task}`} task={task} first={index === 0} soonDays={soonDays} onOpen={onOpen} />
                    ))}
                </div>
                <Pager page={page} pageSize={DESK_PAGE_SIZE} total={total} loading={loading} onPage={goToPage} forms={TASK_FORMS} hideSingle />
            </div>
        );
    }

    /* «Все» ростом в строку подписи (leading-4): кнопка выше подписи опускала бы заголовок, и
       карточка «Моих заявок» стояла бы ниже соседней. Поле для пальца — отрицательными полями. */
    const showAll = onShowOwn ? (
        <button
            type="button"
            onClick={onShowOwn}
            aria-label="Все мои заявки"
            className="-my-1.5 rounded-md px-1.5 py-1.5 text-[13px] font-medium leading-4 text-blue-600 transition hover:text-blue-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60"
        >
            Все
        </button>
    ) : null;

    const ownSection = withOwn ? (
        <section className="space-y-1.5" data-own-requests>
            <SectionTitle right={showAll}>Мои заявки</SectionTitle>
            {ownItems.length > 0 ? (
                <div className={`${iosCard} overflow-hidden`}>
                    {ownItems.map((item, index) => (
                        <OwnRow key={item.id} item={item} first={index === 0} soonDays={soonDays} onOpen={onOpen} />
                    ))}
                </div>
            ) : <OwnSkeleton rows={ownSkeleton} />}
        </section>
    ) : null;

    return (
        <div ref={rootRef} data-desk={layout} className={columns ? '' : 'space-y-5'} style={columns ? DESK_COLUMNS : undefined}>
            {error && <NoticeBox text={error} />}
            {ownFirst && ownSection}
            {!error && (withOwn ? (
                <section className="space-y-1.5" data-desk-tasks>
                    <SectionTitle>Ждут вас</SectionTitle>
                    {taskList}
                </section>
            ) : taskList)}
            {!ownFirst && ownSection}
        </div>
    );
};

export default PaymentsDesk;
