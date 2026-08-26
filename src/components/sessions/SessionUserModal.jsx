import React from 'react';
import { IosModal, IosPager, iosCard } from '../ui/ios';
import { DEVICE_LABELS, parseUserAgent, roleLabel, sessionWord, sessionWordAcc } from './userAgent';

/**
 * Карточка сотрудника: он сам и ВСЕ его живые сессии.
 *
 * Раздел раньше показывал по строке на сессию, и один человек с четырьмя
 * десятками входов занимал весь список. Теперь строка — человек, а сессии
 * живут здесь: у каждой видно устройство, адрес, сроки и главное — открыт ли
 * ей доступ к чувствительным данным, кем и когда он выдан.
 *
 * Данные тянутся по клику: журнал нужен единицам строк из сотен, и таскать его
 * в каждой странице списка было бы платой за то, на что никто не смотрит.
 */

const dash = (value) => (value === null || value === undefined || value === '' ? '—' : value);

const Row = ({ label, children, mono = false }) => (
    <div className="flex items-start justify-between gap-4 bg-white px-3.5 py-2.5">
        <span className="shrink-0 text-[13px] text-slate-500">{label}</span>
        <span className={`min-w-0 text-right text-[13px] font-medium text-slate-800 ${mono ? 'font-mono tabular-nums break-all' : ''}`}>
            {children}
        </span>
    </div>
);

/* Пары «поле — значение» в две колонки. В одну на широкой карточке метка и
   значение разъезжались на всю ширину, и глазу приходилось прыгать через
   полкарточки. Разделители рисует сама сетка через gap-px по фону. */
const FactGrid = ({ children }) => (
    <div className={`${iosCard} overflow-hidden`}>
        <div className="grid grid-cols-1 gap-px bg-slate-100 sm:grid-cols-2">{children}</div>
    </div>
);

const Section = ({ title, children, right = null }) => (
    <section className="space-y-1.5">
        <div className="flex items-center justify-between gap-3 px-1">
            <h4 className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">{title}</h4>
            {right}
        </div>
        {children}
    </section>
);

const ACCESS_ACTION_LABEL = { granted: 'Доступ открыт', revoked: 'Доступ закрыт' };

const EMPLOYMENT_LABELS = {
    working: 'Работает',
    fired: 'Уволен',
    dismissal: 'На увольнении',
    bs: 'Б/С',
    sick_leave: 'Больничный',
    annual_leave: 'Отпуск',
    unpaid_leave: 'Б/С'
};

const employmentLabel = (status) => EMPLOYMENT_LABELS[status] || dash(status);

/* Сессий у одного человека бывает под сотню, и списком они превращают карточку
   в бесконечную ленту: до подвала с «Прервать все» не дойти. Порог низкий
   намеренно — десяток карточек сессий это уже два экрана. */
export const SESSIONS_PAGE_SIZE = 10;

const grantedByText = (name, role) => (
    name ? `${name} · ${roleLabel(role)}` : 'неизвестно — доступ выдан до того, как завели журнал'
);

const SessionCard = ({
    session,
    formatDate,
    onRevoke,
    isRevoking,
    onGrantAccess,
    isGranting,
    canGrantAccess,
    disabled
}) => {
    const [uaOpen, setUaOpen] = React.useState(false);
    const device = React.useMemo(() => parseUserAgent(session.user_agent), [session.user_agent]);
    const accessOpen = Boolean(session.sensitive_data_unlocked);

    return (
        <div className={`${iosCard} overflow-hidden`}>
            {/* flex-wrap, а не одна строка: на телефоне модалка раскрывается во
                весь экран, и две пилюли рядом со строкой «Планшет · Android 14 ·
                Chrome» ужимали её вдвое (199 → 79 px на ширине 320), а карточка
                режет вылезшее своим overflow-hidden. Не помещаются — кнопки
                уезжают на свою строку, ml-auto держит их справа. С одной кнопкой
                (доступ уже открыт) перенос не нужен и не случается. */}
            <div className="flex flex-wrap items-start justify-between gap-3 px-3.5 py-3">
                <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-[13px] font-semibold text-slate-800">
                            {DEVICE_LABELS[device.type] || DEVICE_LABELS.unknown}
                            {device.os !== '—' ? ` · ${device.os}` : ''}
                            {device.browser !== '—' ? ` · ${device.browser}` : ''}
                        </span>
                        {session.is_current && (
                            <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 ring-1 ring-emerald-200">
                                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                                Ваша текущая
                            </span>
                        )}
                    </div>
                    <div className="mt-0.5 font-mono text-[12px] tabular-nums text-slate-400">
                        {dash(session.ip_address)} · {session.session_id?.slice(0, 8)}…
                    </div>
                </div>
                <div className="ml-auto flex shrink-0 items-center justify-end gap-1.5">
                    {/* Выдать доступ можно только там, где его ещё нет: когда он
                        открыт, об этом говорит янтарная полоса ниже, и второй
                        ответ на тот же вопрос — шум. Право приходит с сервера
                        одним флагом, потому что на клиенте его не посчитать. */}
                    {canGrantAccess && !accessOpen && (
                        <button
                            type="button"
                            onClick={() => onGrantAccess(session)}
                            disabled={disabled || isGranting || isRevoking}
                            className="rounded-lg bg-blue-50 px-2.5 py-1.5 text-[12px] font-medium text-blue-600 ring-1 ring-blue-100 transition hover:bg-blue-600 hover:text-white active:scale-[0.98] disabled:opacity-40"
                        >
                            {isGranting ? 'Открываем…' : 'Открыть доступ'}
                        </button>
                    )}
                    <button
                        type="button"
                        onClick={() => onRevoke(session)}
                        disabled={disabled || isRevoking || isGranting}
                        className="rounded-lg bg-red-50 px-2.5 py-1.5 text-[12px] font-medium text-red-600 ring-1 ring-red-100 transition hover:bg-red-600 hover:text-white active:scale-[0.98] disabled:opacity-40"
                    >
                        {isRevoking ? 'Прерывание…' : 'Прервать'}
                    </button>
                </div>
            </div>

            <div className="grid grid-cols-3 gap-px border-t border-slate-100 bg-slate-100 text-center">
                {[
                    ['Начата', session.created_at],
                    ['Активность', session.last_seen_at],
                    ['Истекает', session.expires_at]
                ].map(([label, value]) => (
                    <div key={label} className="bg-white px-2 py-2">
                        <div className="text-[10px] uppercase tracking-wider text-slate-400">{label}</div>
                        <div className="mt-0.5 text-[12px] tabular-nums text-slate-700">{formatDate(value)}</div>
                    </div>
                ))}
            </div>

            {/* Закрытый доступ — норма, и полосы ему не рисуем: нейтральное
                состояние не должно тянуть внимание. */}
            {accessOpen && (
                <div className="border-t border-amber-100 bg-amber-50/60 px-3.5 py-2.5">
                    <div className="text-[12px] font-medium text-amber-800">Чувствительные данные открыты</div>
                    <div className="mt-0.5 text-[12px] text-amber-700">
                        Выдал: {grantedByText(session.sensitive_data_unlocked_by_name, session.sensitive_data_unlocked_by_role)}
                    </div>
                    {session.sensitive_data_unlocked_at && (
                        <div className="text-[12px] tabular-nums text-amber-600">
                            {formatDate(session.sensitive_data_unlocked_at)}
                        </div>
                    )}
                </div>
            )}

            {session.user_agent && (
                <div className="border-t border-slate-100 px-3.5 py-2">
                    <button
                        type="button"
                        onClick={() => setUaOpen((prev) => !prev)}
                        className="text-[12px] font-medium text-blue-600 transition hover:text-blue-700"
                    >
                        {uaOpen ? 'Скрыть user-agent' : 'Показать user-agent'}
                    </button>
                    {uaOpen && (
                        <p className="mt-2 break-all rounded-xl bg-slate-100 px-3 py-2 font-mono text-[11px] leading-relaxed text-slate-600">
                            {session.user_agent}
                        </p>
                    )}
                </div>
            )}
        </div>
    );
};

const SessionUserModal = ({
    open,
    onClose,
    person,
    detail,
    isLoading,
    error,
    formatDate,
    onRevokeSession,
    onRevokeAll,
    revokingSessionId,
    onGrantAccess,
    grantingSessionId
}) => {
    const user = detail?.user || person || null;
    const sessions = detail?.sessions || [];
    const events = detail?.access_events || [];
    const [journalOpen, setJournalOpen] = React.useState(false);
    const [revokingAll, setRevokingAll] = React.useState(false);
    const [page, setPage] = React.useState(1);

    React.useEffect(() => {
        if (!open) return;
        setJournalOpen(false);
        setRevokingAll(false);
        setPage(1);
    }, [open, user?.user_id]);

    const openSessions = React.useMemo(
        () => sessions.filter((s) => s.sensitive_data_unlocked),
        [sessions]
    );
    // Право на выдачу считает сервер и присылает готовым флагом: у карточки нет
    // ни возглавляемых отделов сотрудника, ни отделов смотрящего, а без них
    // «мой ли это периметр» не посчитать. Пока карточка не загрузилась, кнопки
    // нет: обещать действие по строке списка нельзя — сервер откажет молча.
    //
    // Роль сотрудника кнопку больше НЕ ограничивает: открыть можно любую
    // сессию, где не открыто. Держит ли гейт этого человека — отдельный флаг,
    // и он идёт не в видимость кнопки, а в текст подтверждения: супервайзеру
    // выдача ничего не откроет, и нажимающий должен узнать это до нажатия, а
    // не от получателя через неделю.
    const canGrantAccess = Boolean(detail?.user?.can_grant_sensitive_access);
    // Пока карточка не загрузилась, число берём из строки списка; как только
    // пришли настоящие сессии — только из них. Иначе кнопка предлагала
    // «Прервать все 3» под надписью «Живых сессий нет».
    const sessionsCount = detail ? sessions.length : (person?.sessions_count || 0);
    const busy = revokingAll;

    const pageCount = Math.max(1, Math.ceil(sessions.length / SESSIONS_PAGE_SIZE));
    // Прервали последнюю сессию на последней странице — не оставляем человека
    // на пустой: он бы решил, что карточка сломалась.
    const safePage = Math.min(page, pageCount);
    React.useEffect(() => {
        if (page !== safePage) setPage(safePage);
    }, [page, safePage]);
    const pageStart = (safePage - 1) * SESSIONS_PAGE_SIZE;
    const pageSessions = React.useMemo(
        () => sessions.slice(pageStart, pageStart + SESSIONS_PAGE_SIZE),
        [sessions, pageStart]
    );

    const revokeAll = React.useCallback(async () => {
        if (busy) return;
        setRevokingAll(true);
        try {
            await onRevokeAll(user, sessionsCount);
        } finally {
            setRevokingAll(false);
        }
    }, [busy, onRevokeAll, user, sessionsCount]);

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={user?.user_name || 'Карточка сотрудника'}
            subtitle={user ? `${roleLabel(user.user_role)}${user.user_login ? ` · @${user.user_login}` : ''}` : 'Загрузка…'}
            /* Шире обычной модалки: внутри лежит список сессий, у каждой —
               три колонки дат и строка с адресом и устройством. На max-w-2xl
               они жались и переносились на вторую строку. */
            maxWidth="max-w-4xl"
            footer={
                <>
                    <button
                        type="button"
                        onClick={onClose}
                        className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-[13px] font-medium text-slate-600 transition hover:bg-slate-50 active:scale-[0.98]"
                    >
                        Закрыть
                    </button>
                    {sessionsCount > 0 && (
                        <button
                            type="button"
                            onClick={revokeAll}
                            disabled={busy}
                            className="rounded-xl bg-red-600 px-4 py-2 text-[13px] font-medium text-white shadow-sm transition hover:bg-red-500 active:scale-[0.98] disabled:opacity-50"
                        >
                            {busy ? 'Прерывание…' : `Прервать все ${sessionsCount} ${sessionWordAcc(sessionsCount)}`}
                        </button>
                    )}
                </>
            }
        >
            {isLoading && !detail && (
                <div className="flex items-center justify-center gap-2 py-16 text-[13px] text-slate-400">
                    <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                        <path d="M12 2v4m0 12v4M4.93 4.93l2.83 2.83m8.48 8.48 2.83 2.83M2 12h4m12 0h4" strokeLinecap="round" />
                    </svg>
                    Загрузка карточки…
                </div>
            )}

            {error && !isLoading && (
                <div className="rounded-2xl bg-red-50 px-4 py-3 text-[13px] text-red-700 ring-1 ring-red-100">{error}</div>
            )}

            {user && (
                <div className="space-y-4">
                    <Section title="Сотрудник">
                        <FactGrid>
                            <Row label="Логин" mono>{dash(user.user_login)}</Row>
                            <Row label="Роль">{roleLabel(user.user_role)}</Row>
                            <Row label="Отдел">{dash(user.department_name || person?.department_name)}</Row>
                            <Row label="Супервайзер">{dash(user.supervisor_name || person?.supervisor_name)}</Row>
                            {/* Уволенный с живыми сессиями — то, ради чего в этот
                                раздел и заходят, поэтому статус здесь, а не «где-то
                                в карточке сотрудника». */}
                            <Row label="Трудоустройство">
                                {user.user_status === 'fired' || user.user_status === 'dismissal' ? (
                                    <span className="text-red-600">{employmentLabel(user.user_status)}</span>
                                ) : (
                                    employmentLabel(user.user_status)
                                )}
                            </Row>
                            <Row label="Сессий сейчас">
                                <span className="tabular-nums">{user.active_sessions ?? sessionsCount}</span>
                                {user.total_sessions ? (
                                    <span className="ml-1.5 font-normal text-slate-400">
                                        {'\u00b7'} за всё время {user.total_sessions}
                                    </span>
                                ) : null}
                            </Row>
                        </FactGrid>
                    </Section>

                    <Section
                        title="Доступ к чувствительным данным"
                        right={events.length > 0 ? (
                            <button
                                type="button"
                                onClick={() => setJournalOpen((prev) => !prev)}
                                className="text-[12px] font-medium text-blue-600 transition hover:text-blue-700"
                            >
                                {journalOpen ? 'Скрыть журнал' : `Журнал (${events.length})`}
                            </button>
                        ) : null}
                    >
                        <div className={`${iosCard} overflow-hidden`}>
                            {/* Здесь только состояние: кто и когда открыл — написано
                                у самой сессии ниже. Один ответ на вопрос живёт в
                                одном месте экрана, иначе это шум. */}
                            {/* Пока карточка грузится или упала, утверждать «ничего
                                нет» нельзя — это прямая ложь про доступ к данным. */}
                            <div className="px-3.5 py-3 text-[13px] text-slate-700">
                                {!detail
                                    ? (error ? 'Не удалось узнать состояние доступа.' : 'Проверяем…')
                                    : (openSessions.length === 0
                                        ? 'Ни одной сессии с открытыми данными.'
                                        /* Форма «N из M сессий» спотыкается на
                                           числительных, поэтому счётчик после двоеточия. */
                                        : `Сессий с открытыми данными: ${openSessions.length} из ${sessions.length}. Кто и когда выдал — написано у самой сессии ниже.`)}
                            </div>

                            {journalOpen && events.length > 0 && (
                                <ol className="space-y-2.5 border-t border-slate-100 px-3.5 py-3">
                                    {events.map((event) => (
                                        <li key={event.id} className="flex gap-2.5">
                                            <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
                                                event.action === 'granted' ? 'bg-amber-500' : 'bg-slate-300'
                                            }`} />
                                            <div className="min-w-0">
                                                <div className="text-[13px] font-medium text-slate-800">
                                                    {ACCESS_ACTION_LABEL[event.action] || event.action}
                                                </div>
                                                <div className="text-[12px] text-slate-500">
                                                    {event.actor_name
                                                        ? `${event.actor_name} · ${roleLabel(event.actor_role)}`
                                                        : 'Исполнитель неизвестен'}
                                                </div>
                                                <div className="text-[12px] tabular-nums text-slate-400">
                                                    {formatDate(event.created_at)}
                                                    {event.session_id ? ` · сессия ${event.session_id.slice(0, 8)}…` : ''}
                                                    {event.ip_address ? ` · ${event.ip_address}` : ''}
                                                </div>
                                            </div>
                                        </li>
                                    ))}
                                </ol>
                            )}
                        </div>
                    </Section>

                    <Section title={`Активные сессии${detail && sessions.length ? ` · ${sessions.length}` : ''}`}>
                        {sessions.length === 0 ? (
                            <div className={`${iosCard} px-3.5 py-3 text-[13px] text-slate-500`}>
                                {!detail
                                    ? (error ? 'Список сессий не загрузился.' : 'Загружаем сессии…')
                                    : 'Живых сессий нет.'}
                            </div>
                        ) : (
                            <div className="space-y-2">
                                {/* Пейджер НАД списком: под ним до него пришлось бы
                                    прокручивать десяток карточек — ровно то, от чего
                                    страницы и заводились. Сверху он ещё и оставляет
                                    начало новой страницы прямо под собой. */}
                                <IosPager
                                    page={safePage}
                                    pageCount={pageCount}
                                    total={sessions.length}
                                    from={pageStart + 1}
                                    to={pageStart + pageSessions.length}
                                    onPage={setPage}
                                    unit="сессии"
                                />
                                {pageSessions.map((session) => (
                                    <SessionCard
                                        key={session.session_id}
                                        session={session}
                                        formatDate={formatDate}
                                        onRevoke={onRevokeSession}
                                        isRevoking={revokingSessionId === session.session_id}
                                        onGrantAccess={onGrantAccess}
                                        isGranting={grantingSessionId === session.session_id}
                                        canGrantAccess={canGrantAccess}
                                        disabled={busy}
                                    />
                                ))}
                            </div>
                        )}
                    </Section>
                </div>
            )}
        </IosModal>
    );
};

export default React.memo(SessionUserModal);
