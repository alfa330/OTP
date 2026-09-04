import React, { useCallback, useMemo, useState } from 'react';
import {
    AlertCircle, ArrowUpRight, Check, ChevronLeft, History, Loader2, Lock,
    Plus, Trash2, X,
} from 'lucide-react';
import { ChatComposer, useThreadAutoScroll } from '../ui/chat';
import { AssistantMessage, fmtChatDate } from './assistantThread.jsx';
import useAssistantChat from './useAssistantChat';
import Orb from './Orb.jsx';

/* Мини-чат шарика: тот же помощник, что во вкладке вики, в колонке 384 пикселя.
 *
 * ЧТО УБРАНО ПО СРАВНЕНИЮ С ВКЛАДКОЙ И ПОЧЕМУ. Вкладка держит слева постоянный
 * список чатов шириной 256 пикселей — в панели это две трети ширины, и ответ с
 * таблицей превратился бы в столбик по слову в строке. Поэтому история здесь
 * не колонка, а экран: кнопка «История» закрывает ленту целиком и так же
 * целиком уходит. Убраны и служебные подписи под ответом (модель, время) —
 * они нужны при разборе жалобы, а разбирают жалобы во вкладке.
 *
 * ЧТО НЕ УБРАНО НИ ПРИ КАКОЙ ШИРИНЕ: источники под ответом, оговорка про архив
 * и приписка про обязательное ознакомление. Помощник отвечает по регламентам,
 * которые оператор пересказывает водителю, и цена ошибки тут не в интерфейсе.
 * Узкая панель — повод убрать техническую справку, а не признак того, что
 * ответом можно пользоваться не глядя.
 *
 * ЗАМОК QR. Оператор, бухгалтер и HR без подтверждённой сессии получают 403 на
 * каждом роуте помощника (wiki/routes.py). Шарик им всё равно показывается —
 * решение владельца: спрятанный шарик означал бы, что для самой массовой роли
 * портала помощника просто нет, и они о нём не узнают. Вместо чата панель
 * открывается замком с той же кнопкой, что и в разделе; модалку с QR рисует
 * сам App, поэтому здесь только вызов onRequestQr.
 */

const PANEL_TITLE = 'Помощник';

const LockScreen = ({ checking, onRequestQr }) => (
    <div className="flex flex-1 flex-col items-center justify-center gap-2.5 px-7 text-center">
        {checking ? (
            <>
                <Loader2 size={20} className="animate-spin text-slate-300" />
                <div className="text-[12.5px] text-slate-400">Проверяем доступ…</div>
            </>
        ) : (
            <>
                <div className="grid h-12 w-12 place-items-center rounded-2xl bg-blue-50 ring-1 ring-blue-100">
                    <Lock size={19} className="text-blue-600" />
                </div>
                <div className="mt-1 text-[13.5px] font-semibold text-slate-900">
                    Помощник открывается по QR
                </div>
                <p className="text-[12px] leading-relaxed text-slate-500">
                    Он отвечает по базе знаний компании, поэтому доступ к нему
                    подтверждает супервайзер или администратор — тем же кодом, что
                    открывает «Вики» и «Обращения».
                </p>
                <button
                    type="button"
                    onClick={onRequestQr}
                    className="mt-1.5 inline-flex items-center gap-1.5 rounded-xl bg-blue-500 px-3.5 py-2 text-[12.5px] font-medium text-white transition hover:bg-blue-600"
                >
                    Сгенерировать QR
                </button>
            </>
        )}
    </div>
);

const HistoryScreen = ({ chats, loading, activeId, onOpen, onDelete, onBack }) => (
    <div className="flex flex-1 flex-col overflow-hidden">
        <div className="flex items-center gap-1.5 border-b border-slate-200/70 px-2.5 py-2">
            <button
                type="button"
                onClick={onBack}
                className="grid h-7 w-7 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                aria-label="Назад к разговору"
            >
                <ChevronLeft size={16} />
            </button>
            <span className="text-[12.5px] font-semibold text-slate-700">История</span>
        </div>
        <div className="flex-1 overflow-y-auto p-1.5">
            {loading && (
                <div className="flex items-center justify-center gap-2 py-8 text-[12px] text-slate-400">
                    <Loader2 size={14} className="animate-spin" /> Загружаем…
                </div>
            )}
            {!loading && !chats.length && (
                <div className="px-4 py-8 text-center text-[12px] text-slate-400">
                    Разговоров пока нет.
                </div>
            )}
            {chats.map((chat) => (
                <div
                    key={chat.id}
                    className={`group flex items-center gap-1.5 rounded-xl px-2.5 py-2 transition ${
                        chat.id === activeId ? 'bg-blue-50' : 'hover:bg-slate-50'
                    }`}
                >
                    <button
                        type="button"
                        onClick={() => onOpen(chat.id)}
                        className="min-w-0 flex-1 text-left"
                    >
                        <div className="truncate text-[12.5px] font-medium text-slate-800">
                            {chat.title || 'Без названия'}
                        </div>
                        <div className="text-[11px] text-slate-400">
                            {fmtChatDate(chat.last_message_at || chat.created_at)}
                            {chat.message_count ? ` · ${chat.message_count}` : ''}
                        </div>
                    </button>
                    <button
                        type="button"
                        onClick={() => onDelete(chat)}
                        className="grid h-6 w-6 shrink-0 place-items-center rounded-lg text-slate-300 opacity-0 transition hover:bg-rose-50 hover:text-rose-500 group-hover:opacity-100"
                        aria-label="Удалить разговор"
                    >
                        <Trash2 size={13} />
                    </button>
                </div>
            ))}
        </div>
    </div>
);

export default function AssistantPanel({
    base, headers, spaceId, spaceName,
    locked = false, lockChecking = false, onRequestQr,
    onOpenArticle, onOpenFullAssistant, onClose, showToast,
}) {
    const chat = useAssistantChat({ base, headers, spaceId, enabled: !locked,
                                    withSuggestions: true });
    const { boxRef, onScroll } = useThreadAutoScroll(chat.messages);
    const [screen, setScreen] = useState('thread');
    const [pendingDelete, setPendingDelete] = useState(null);

    /* Замок рисуем и по локальному признаку, и по ответу сервера. Локальный
       (роль без подтверждённой сессии) экономит запрос и показывает замок сразу;
       серверный ловит случай, когда подтверждение истекло уже при открытой
       вкладке — тогда локально всё ещё «доступ есть», а сервер отвечает 403. */
    const serverLocked = chat.statusError?.code === 'SENSITIVE_ACCESS_REQUIRED';
    const showLock = locked || serverLocked;

    const feedback = useCallback((messageId, value) => {
        chat.sendFeedback(messageId, value)
            .catch(() => showToast?.('Не удалось сохранить оценку', 'error'));
    }, [chat, showToast]);

    const confirmDelete = useCallback(() => {
        const target = pendingDelete;
        if (!target) return;
        setPendingDelete(null);
        chat.removeChat(target.id)
            .then(() => showToast?.('Разговор удалён', 'success'))
            .catch(() => showToast?.('Не удалось удалить разговор', 'error'));
    }, [chat, pendingDelete, showToast]);

    const banner = useMemo(() => {
        if (showLock) return null;
        if (chat.statusError?.code === 'WIKI_DEPARTMENT_DISABLED') {
            return 'База знаний не выдана вашему отделу — обратитесь к администратору вики.';
        }
        if (chat.noAccess) {
            return 'Помощнику не выдан доступ ни к одной статье. Доступ выдаётся отдельно от чтения — обратитесь к администратору вики.';
        }
        if (chat.status && !chat.indexReady) {
            return 'Индекс помощника пуст: администратору нужно собрать его в разделе обслуживания.';
        }
        return null;
    }, [showLock, chat.statusError, chat.noAccess, chat.status, chat.indexReady]);

    const empty = !chat.messages || !chat.messages.length;

    const subtitle = showLock
        ? 'нужно подтверждение доступа'
        : chat.perimeter
            ? `${spaceName ? `${spaceName} · ` : ''}доступно статей: ${chat.perimeter.articles_for_ai}`
            : 'отвечает по вашим статьям вики';

    return (
        <div className="flex h-full flex-col overflow-hidden">
            {/* Шапка */}
            <div className="flex items-center gap-2 border-b border-slate-200/70 bg-white/70 px-2.5 py-2 backdrop-blur">
                <Orb variant="mini" animated={false} />
                <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-semibold leading-tight tracking-[-0.01em] text-slate-900">
                        {PANEL_TITLE}
                    </div>
                    <div className="truncate text-[11px] leading-tight text-slate-400">
                        {subtitle}
                    </div>
                </div>
                {!showLock && (
                    <>
                        <button
                            type="button"
                            onClick={() => { chat.startNewChat(); setScreen('thread'); }}
                            className="grid h-7 w-7 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                            aria-label="Новый разговор"
                            title="Новый разговор"
                        >
                            <Plus size={15} />
                        </button>
                        <button
                            type="button"
                            onClick={() => setScreen((s) => (s === 'history' ? 'thread' : 'history'))}
                            className={`grid h-7 w-7 place-items-center rounded-lg transition hover:bg-slate-100 hover:text-slate-600 ${
                                screen === 'history' ? 'bg-slate-100 text-slate-600' : 'text-slate-400'
                            }`}
                            aria-label="История разговоров"
                            title="История"
                        >
                            <History size={15} />
                        </button>
                        <button
                            type="button"
                            onClick={onOpenFullAssistant}
                            className="grid h-7 w-7 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                            aria-label="Открыть помощника в разделе «Вики»"
                            title="Открыть в разделе «Вики»"
                        >
                            <ArrowUpRight size={15} />
                        </button>
                    </>
                )}
                <button
                    type="button"
                    onClick={onClose}
                    className="grid h-7 w-7 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600"
                    aria-label="Свернуть помощника"
                >
                    <X size={15} />
                </button>
            </div>

            {banner && (
                <div className="flex items-start gap-1.5 border-b border-amber-100 bg-amber-50 px-3 py-2 text-[11.5px] leading-snug text-amber-800">
                    <AlertCircle size={13} className="mt-[1px] shrink-0 text-amber-500" />
                    <span>{banner}</span>
                </div>
            )}

            {showLock ? (
                <LockScreen checking={lockChecking} onRequestQr={onRequestQr} />
            ) : screen === 'history' ? (
                <HistoryScreen
                    chats={chat.chats}
                    loading={chat.chatsLoading}
                    activeId={chat.activeId}
                    onOpen={(id) => { chat.openChat(id); setScreen('thread'); }}
                    onDelete={setPendingDelete}
                    onBack={() => setScreen('thread')}
                />
            ) : (
                <>
                    <div
                        ref={boxRef}
                        onScroll={onScroll}
                        className="flex flex-1 flex-col gap-2 overflow-y-auto bg-slate-50/50 py-3"
                    >
                        {empty && (
                            <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
                                <Orb variant="hero" />
                                <div className="mt-1 text-[13.5px] font-semibold text-slate-900">
                                    Спросите про что угодно из базы знаний
                                </div>
                                <p className="text-[12px] leading-relaxed text-slate-500">
                                    Отвечаю только по статьям, которые вам разрешено
                                    читать, и всегда показываю источник.
                                </p>
                                {/* Подсказки приходят с сервера и собраны из статей
                                    ЭТОГО человека (wiki/ai/suggest.py). Зашитый
                                    список был одинаков для всех и у половины людей
                                    вёл в отказ «в доступных вам статьях этого нет» —
                                    подсказка, ведущая в отказ, учит, что помощник не
                                    работает. Пустой список означает, что предложить
                                    нечего: тогда блока нет вовсе, а не заглушка. */}
                                {chat.canAsk && !!chat.suggestions.length && (
                                    <div className="mt-1.5 flex w-full flex-col gap-1.5">
                                        {chat.suggestions.map((hint) => (
                                            <button
                                                key={hint}
                                                type="button"
                                                onClick={() => chat.setDraft(hint)}
                                                className="rounded-xl border border-slate-200/80 bg-white px-3 py-2 text-left text-[12px] text-slate-600 transition hover:border-indigo-200 hover:bg-indigo-50/40"
                                            >
                                                {hint}
                                            </button>
                                        ))}
                                    </div>
                                )}
                            </div>
                        )}

                        {(chat.messages || []).map((message) => (
                            <AssistantMessage
                                key={message.id}
                                message={message}
                                compact
                                onOpenArticle={onOpenArticle}
                                onFeedback={feedback}
                            />
                        ))}

                        {chat.busy && (
                            <div className="flex justify-start px-4">
                                <div className="flex items-center gap-2 rounded-2xl rounded-bl-md bg-white px-3 py-2 text-[12.5px] text-slate-400 ring-1 ring-slate-200/60">
                                    <Loader2 size={13} className="animate-spin" />
                                    Читаю доступные вам статьи…
                                </div>
                            </div>
                        )}
                    </div>

                    {chat.error && (
                        <div className="flex items-center gap-1.5 border-t border-rose-100 bg-rose-50 px-3 py-1.5 text-[11.5px] text-rose-600">
                            <AlertCircle size={13} className="shrink-0" /> {chat.error}
                        </div>
                    )}

                    <ChatComposer
                        value={chat.draft}
                        onChange={chat.setDraft}
                        onSubmit={chat.ask}
                        busy={chat.busy}
                        disabled={!chat.canAsk}
                        placeholder={chat.canAsk ? 'Спросить помощника…' : 'Помощник пока недоступен'}
                    />
                </>
            )}

            {/* Подтверждение удаления — своим слоем внутри панели, а не общей
                модалкой портала: модалка затемняет весь экран ради строки в
                списке истории и выглядит несоразмерно поводу. */}
            {pendingDelete && (
                <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/80 px-6 backdrop-blur-sm">
                    <div className="w-full rounded-2xl bg-white p-4 text-center shadow-lg ring-1 ring-slate-200">
                        <div className="text-[13px] font-semibold text-slate-900">
                            Удалить разговор?
                        </div>
                        <p className="mt-1 text-[11.5px] leading-relaxed text-slate-500">
                            Он скроется из списка. Вопросы остаются в журнале раздела —
                            авторам вики они нужны, чтобы видеть, о чём спрашивают, а
                            ответа в статьях нет.
                        </p>
                        <div className="mt-3 flex gap-2">
                            <button
                                type="button"
                                onClick={() => setPendingDelete(null)}
                                className="flex-1 rounded-xl bg-slate-100 px-3 py-2 text-[12.5px] font-medium text-slate-600 transition hover:bg-slate-200"
                            >
                                Отмена
                            </button>
                            <button
                                type="button"
                                onClick={confirmDelete}
                                className="flex-1 rounded-xl bg-rose-500 px-3 py-2 text-[12.5px] font-medium text-white transition hover:bg-rose-600"
                            >
                                <Check size={13} className="mr-1 inline" /> Удалить
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
