import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import {
    BadgeCheck, Banknote, Boxes, ChevronRight, CreditCard, FileCheck2, Inbox, Landmark, MessageCircleQuestion,
    PackageCheck, Paperclip, PencilLine, ReceiptText, Send,
} from 'lucide-react';
import { iosCard } from '../ui/ios';
import {
    DESK_PAGE_SIZE, TASK_FORMS, deskSkeletonRows, deskTaskAbout, deskTaskReturned, dueChip, fmtMoney, pageRange,
} from './paymentsMeta';
import { Bone, DueChip, NoticeBox, Pager, errorText } from './paymentsUi';
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
 * Стол — первое, что видно при входе в раздел, поэтому он не «загружается» на
 * глазах: первая страница помнится с прошлого входа (paymentsEntry.js) и рисуется
 * сразу, а пока помнить нечего — стоит заготовка списка на столько строк, сколько
 * задач обещает счётчик вкладки.
 */

/* Значок — по тому, ЧТО предстоит сделать: задачи на столе разнородные
   (согласовать, оплатить, приложить чек, принять товар), и свою находят глазами,
   не читая заголовки подряд. Цвет у значка один на все задачи; жёлтым отмечена
   только заявка, которую человеку вернули, — там ждут его ответа. */
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

/* Заготовка списка: строка повторяет геометрию DeskRow (значок, три строки текста, сумма
   справа), поэтому задачи встают на её место без сдвига. */
const DeskSkeleton = ({ rows }) => (
    <div className={`${iosCard} overflow-hidden`} role="status" aria-busy="true" aria-label="Загружаем задачи" data-desk-skeleton>
        {Array.from({ length: rows }, (_, index) => (
            <div key={index} className="flex items-stretch gap-3 pl-4">
                <span className="py-3"><Bone className="h-8 w-8 !rounded-[10px]" /></span>
                <span className={`flex min-w-0 grow basis-0 items-center gap-3 py-3 pr-3 ${index ? 'border-t border-slate-100' : ''}`}>
                    <span className="flex h-[63px] min-w-0 grow basis-0 flex-col justify-between py-[3px]">
                        <Bone className="h-3 w-40 max-w-full" />
                        <Bone className={`h-3 max-w-full ${index % 2 ? 'w-56' : 'w-72'}`} />
                        <Bone className="h-3 w-24" />
                    </span>
                    <Bone className="hidden h-3.5 w-20 shrink-0 sm:block" />
                </span>
            </div>
        ))}
    </div>
);

const PaymentsDesk = ({ apiBaseUrl, headers, refreshKey, soonDays, onOpen, onCount, memoryKey = null, expected = null }) => {
    // Первая страница с прошлого входа в раздел: стол рисуется ею сразу и обновляется фоном.
    const [remembered] = useState(() => recall(memoryKey, 'desk'));
    const [tasks, setTasks] = useState(remembered?.tasks || []);
    const [total, setTotal] = useState(remembered?.total || 0);
    /* Сколько строк в заготовке первой загрузки — решается один раз, при входе: счётчик вкладки
       может прийти посреди неё (первый вход на устройстве), и заготовка, выросшая с трёх строк
       до шести за мгновение до списка, была бы лишним движением. */
    const [firstRows] = useState(() => deskSkeletonRows(expected));
    const answered = useRef(false);
    const [page, setPage] = useState(1);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const topRef = useRef(null);

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
            const list = response.data?.tasks || [];
            const count = Number(response.data?.total) || 0;
            answered.current = true;
            setTasks(list);
            setTotal(count);
            if (pageNumber === 1) remember(memoryKey, 'desk', { tasks: list, total: count });
            countRef.current?.(count);
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

    if (error) return <NoticeBox text={error} />;
    /* Список ещё не пришёл, и помнить нечего — стоит его заготовка. Счётчик вкладки говорит
       «ноль» — сразу пустой стол: ответ почти наверняка это подтвердит, а заготовка, сменяющаяся
       надписью «задач нет», была бы лишним движением. */
    const skeletonRows = loading && !tasks.length ? (answered.current ? deskSkeletonRows(expected) : firstRows) : 0;
    if (skeletonRows) return <DeskSkeleton rows={skeletonRows} />;
    if (!tasks.length) {
        return (
            <div className={`${iosCard} flex flex-col items-center gap-2 px-4 py-14 text-center`}>
                <Inbox size={22} className="text-slate-300" />
                <p className="text-[13.5px] text-slate-500">Задач для вас сейчас нет</p>
            </div>
        );
    }

    return (
        <div ref={topRef} className="scroll-mt-4 space-y-3">
            <div key={page} className={`${iosCard} overflow-hidden motion-safe:animate-fade-in-soft`}>
                {tasks.map((task, index) => (
                    <DeskRow key={`${task.id}:${task.task}`} task={task} first={index === 0} soonDays={soonDays} onOpen={onOpen} />
                ))}
            </div>
            <Pager page={page} pageSize={DESK_PAGE_SIZE} total={total} loading={loading} onPage={goToPage} forms={TASK_FORMS} hideSingle />
        </div>
    );
};

export default PaymentsDesk;
