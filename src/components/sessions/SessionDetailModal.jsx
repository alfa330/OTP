import React from 'react';
import { IosModal, iosCard } from '../ui/ios';
import { DEVICE_LABELS, parseUserAgent, roleLabel } from './userAgent';

/**
 * Карточка одной сессии.
 *
 * Раньше про сессию было известно ровно то, что помещалось в строку таблицы, а
 * главный вопрос раздела — «кто и когда открыл этому человеку чувствительные
 * данные» — не имел ответа вовсе: подтверждающий уходил только в Telegram.
 * Карточка собирает всё про сессию в одном месте и показывает журнал выдачи
 * доступа.
 *
 * Данные тянутся по клику, а не приезжают со списком: журнал нужен единицам
 * строк из сотен, и таскать его в каждой странице списка было бы платой за то,
 * на что никто не смотрит.
 */

const dash = (value) => (value === null || value === undefined || value === '' ? '—' : value);

const Row = ({ label, children, mono = false }) => (
    <div className="flex items-start justify-between gap-4 px-3.5 py-2.5">
        <span className="shrink-0 text-[13px] text-slate-500">{label}</span>
        <span className={`min-w-0 text-right text-[13px] font-medium text-slate-800 ${mono ? 'font-mono tabular-nums break-all' : ''}`}>
            {children}
        </span>
    </div>
);

const Section = ({ title, children, right = null }) => (
    <section className="space-y-1.5">
        <div className="flex items-center justify-between gap-3 px-1">
            <h4 className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">{title}</h4>
            {right}
        </div>
        <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>{children}</div>
    </section>
);

const ACCESS_ACTION_LABEL = {
    granted: 'Доступ открыт',
    revoked: 'Доступ закрыт'
};

const SessionDetailModal = ({
    open,
    onClose,
    session,
    isLoading,
    error,
    formatDate,
    onRevoke,
    isRevoking = false
}) => {
    const [uaOpen, setUaOpen] = React.useState(false);
    const [copied, setCopied] = React.useState(false);

    React.useEffect(() => {
        if (!open) return;
        setUaOpen(false);
        setCopied(false);
    }, [open, session?.session_id]);

    const device = React.useMemo(
        () => parseUserAgent(session?.user_agent),
        [session?.user_agent]
    );

    const copySessionId = React.useCallback(async () => {
        const value = session?.session_id;
        if (!value) return;
        try {
            await navigator.clipboard.writeText(value);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1600);
        } catch (_) {
            /* буфер обмена закрыт браузером — ID и так виден целиком */
        }
    }, [session?.session_id]);

    const isActive = session ? session.is_active !== false : false;
    const accessOpen = Boolean(session?.sensitive_data_unlocked);

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={session?.user_name || 'Карточка сессии'}
            subtitle={session ? `${roleLabel(session.user_role)}${session.user_login ? ` · @${session.user_login}` : ''}` : 'Загрузка…'}
            maxWidth="max-w-2xl"
            footer={
                session && isActive && typeof onRevoke === 'function' ? (
                    <>
                        <button
                            type="button"
                            onClick={onClose}
                            className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-[13px] font-medium text-slate-600 transition hover:bg-slate-50 active:scale-[0.98]"
                        >
                            Закрыть
                        </button>
                        <button
                            type="button"
                            onClick={() => onRevoke(session)}
                            disabled={isRevoking}
                            className="rounded-xl bg-red-600 px-4 py-2 text-[13px] font-medium text-white shadow-sm transition hover:bg-red-500 active:scale-[0.98] disabled:opacity-50"
                        >
                            {isRevoking ? 'Прерывание…' : 'Прервать сессию'}
                        </button>
                    </>
                ) : (
                    <button
                        type="button"
                        onClick={onClose}
                        className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-[13px] font-medium text-slate-600 transition hover:bg-slate-50 active:scale-[0.98]"
                    >
                        Закрыть
                    </button>
                )
            }
        >
            {isLoading && !session && (
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

            {session && (
                <div className="space-y-4">
                    {/* Статусы. Цветом отмечено только то, что требует внимания:
                        прерванная сессия и открытый доступ к данным. Когда
                        отмечать нечего, ряда нет вовсе — пустая строка съедала
                        бы отступ и читалась как «что-то не загрузилось». */}
                    {(session.is_current || !isActive || accessOpen) && (
                    <div className="flex flex-wrap items-center gap-1.5">
                        {session.is_current && (
                            <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-[12px] font-medium text-emerald-700 ring-1 ring-emerald-200">
                                <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
                                Ваша текущая сессия
                            </span>
                        )}
                        {!isActive && (
                            <span className="rounded-full bg-red-50 px-2.5 py-1 text-[12px] font-medium text-red-700 ring-1 ring-red-200">
                                Прервана {session.revoked_at ? formatDate(session.revoked_at) : ''}
                            </span>
                        )}
                        {/* Закрытый доступ — норма, и плашки ему не нужно: своё
                            состояние секция «Доступ» называет сама, а два ответа
                            на один вопрос в одном экране — лишний шум. */}
                        {accessOpen && (
                            <span className="rounded-full bg-amber-50 px-2.5 py-1 text-[12px] font-medium text-amber-700 ring-1 ring-amber-200">
                                Чувствительные данные открыты
                            </span>
                        )}
                    </div>
                    )}

                    <Section
                        title="Сессия"
                        right={
                            <button
                                type="button"
                                onClick={copySessionId}
                                className="text-[12px] font-medium text-blue-600 transition hover:text-blue-700"
                            >
                                {copied ? 'Скопировано' : 'Копировать ID'}
                            </button>
                        }
                    >
                        <Row label="Идентификатор" mono>{dash(session.session_id)}</Row>
                        <Row label="Начата">{formatDate(session.created_at)}</Row>
                        <Row label="Последняя активность">{formatDate(session.last_seen_at)}</Row>
                        <Row label="Истекает">{formatDate(session.expires_at)}</Row>
                        <Row label="IP-адрес" mono>{dash(session.ip_address)}</Row>
                    </Section>

                    <Section title="Устройство">
                        <Row label="Тип">{DEVICE_LABELS[device.type] || DEVICE_LABELS.unknown}</Row>
                        <Row label="Система">{dash(device.os === '—' ? null : device.os)}</Row>
                        <Row label="Браузер">{dash(device.browser === '—' ? null : device.browser)}</Row>
                        {session.user_agent && (
                            <div className="px-3.5 py-2.5">
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
                    </Section>

                    <Section title="Доступ к чувствительным данным">
                        <Row label="Состояние">{accessOpen ? 'Открыт' : 'Закрыт'}</Row>
                        {accessOpen && (
                            <>
                                <Row label="Открыл">
                                    {session.sensitive_data_unlocked_by_name
                                        ? `${session.sensitive_data_unlocked_by_name} · ${roleLabel(session.sensitive_data_unlocked_by_role)}`
                                        : 'Неизвестно (доступ выдан до ведения журнала)'}
                                </Row>
                                <Row label="Когда">{formatDate(session.sensitive_data_unlocked_at)}</Row>
                            </>
                        )}
                        {(session.access_events || []).length === 0 ? (
                            <div className="px-3.5 py-3 text-[12px] text-slate-400">
                                Выдач и отзывов доступа по этой сессии не было.
                            </div>
                        ) : (
                            <div className="px-3.5 py-3">
                                <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                                    История
                                </div>
                                <ol className="space-y-2.5">
                                    {session.access_events.map((event) => (
                                        <li key={event.id} className="flex gap-2.5">
                                            <span
                                                className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${
                                                    event.action === 'granted' ? 'bg-amber-500' : 'bg-slate-300'
                                                }`}
                                            />
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
                                                    {event.ip_address ? ` · ${event.ip_address}` : ''}
                                                </div>
                                            </div>
                                        </li>
                                    ))}
                                </ol>
                            </div>
                        )}
                    </Section>

                    <Section title="Сотрудник">
                        <Row label="Логин" mono>{dash(session.user_login)}</Row>
                        <Row label="Роль">{roleLabel(session.user_role)}</Row>
                        <Row label="Отдел">{dash(session.department_name)}</Row>
                        <Row label="Супервайзер">{dash(session.supervisor_name)}</Row>
                        <Row label="Активных сессий">
                            <span className="tabular-nums">{session.user_active_sessions ?? '—'}</span>
                        </Row>
                    </Section>
                </div>
            )}
        </IosModal>
    );
};

export default React.memo(SessionDetailModal);
