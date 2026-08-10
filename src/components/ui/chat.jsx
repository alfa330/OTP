import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { CornerDownLeft, Loader2 } from 'lucide-react';

/* Общие примитивы чата в стиле iOS.
 *
 * Появились потому, что каркас чата в проекте уже продублирован: WazzupChatsView
 * (1010 строк) и ChatAppChatsView (778) — почти построчные копии друг друга, у
 * каждой свои MessageBubble, Avatar, SegButton и своя логика прокрутки, и ни один
 * из этих компонентов не экспортирован. Помощник вики стал бы третьей копией.
 *
 * Классы пузыря и разделителя дня взяты из Wazzup дословно: раздел должен
 * выглядеть частью продукта, а не «ещё одним чатом».
 *
 * Два примитива здесь НОВЫЕ, их в проекте не было ни одного:
 *   * ChatComposer — поля ввода сообщения не существовало нигде (ни отправки по
 *     Enter, ни авторесайза): все три чата проекта только читают историю;
 *   * useThreadAutoScroll — автопрокрутки тоже не было ни в одном. Читающему
 *     чату она не нужна, а помощнику нужна: ответ появляется после вопроса, и
 *     прыгать к нему руками пользователь не должен.
 *
 * Тёмной темы в проекте нет, классы dark:* здесь запрещены — Tailwind настроен
 * без darkMode, то есть они сработали бы от системной темы.
 */

/** Пузырь сообщения. out=true — своё (справа, синее). */
export const ChatBubble = ({ out = false, children, meta = null, tone = null }) => {
    const own = out
        ? 'rounded-br-md bg-blue-500 text-white'
        : 'rounded-bl-md bg-white text-slate-900 ring-1 ring-slate-200/60';
    const toned = tone === 'warn'
        ? 'rounded-bl-md bg-amber-50 text-amber-900 ring-1 ring-amber-200/70'
        : tone === 'muted'
            ? 'rounded-bl-md bg-slate-100 text-slate-600 ring-1 ring-slate-200/60'
            : own;
    return (
        <div className={`flex ${out ? 'justify-end' : 'justify-start'} px-4`}>
            <div className={`max-w-[78%] rounded-2xl px-3 py-2 text-[13.5px] leading-snug shadow-[0_1px_1px_rgba(15,23,42,0.05)] ${toned}`}>
                <div className="whitespace-pre-wrap break-words">{children}</div>
                {meta && (
                    <div className={`mt-1 flex items-center gap-1.5 text-[11px] ${out ? 'text-blue-100' : 'text-slate-400'}`}>
                        {meta}
                    </div>
                )}
            </div>
        </div>
    );
};

export const ChatDayDivider = ({ children }) => (
    <div className="flex justify-center py-1.5">
        <span className="rounded-full bg-slate-500/10 px-3 py-1 text-[11px] font-medium text-slate-500">
            {children}
        </span>
    </div>
);

export const ChatSegButton = ({ active, onClick, children }) => (
    <button
        type="button"
        onClick={onClick}
        className={`flex-1 rounded-lg px-3 py-1.5 text-[12.5px] font-medium transition ${
            active ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'
        }`}
    >
        {children}
    </button>
);

export const ChatEmpty = ({ icon: Icon, title, hint = null }) => (
    <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center text-slate-400">
        {Icon && <Icon size={32} strokeWidth={1.5} />}
        <div className="text-[13px]">{title}</div>
        {hint && <div className="max-w-xs text-[12px] text-slate-400">{hint}</div>}
    </div>
);

/**
 * Прокрутка ленты: липнет к низу, только если пользователь и так у низа.
 *
 * Так сделано намеренно. Безусловный прыжок вниз на каждое обновление вырвал бы
 * человека из чтения старого ответа, а это в помощнике происходит часто: ответы
 * длинные, и их дочитывают, пока задаётся следующий вопрос.
 */
export const useThreadAutoScroll = (dependency, { threshold = 120 } = {}) => {
    const boxRef = useRef(null);
    const stickRef = useRef(true);

    const onScroll = useCallback(() => {
        const box = boxRef.current;
        if (!box) return;
        stickRef.current = box.scrollHeight - box.scrollTop - box.clientHeight < threshold;
    }, [threshold]);

    useLayoutEffect(() => {
        const box = boxRef.current;
        if (!box || !stickRef.current) return;
        // requestAnimationFrame: до кадра высота ещё старая, и прыжок недоскакивает.
        const id = requestAnimationFrame(() => {
            if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
        });
        return () => cancelAnimationFrame(id);
    }, [dependency]);

    const scrollToEnd = useCallback(() => {
        stickRef.current = true;
        const box = boxRef.current;
        if (box) box.scrollTop = box.scrollHeight;
    }, []);

    return { boxRef, onScroll, scrollToEnd };
};

/**
 * Поле ввода сообщения: авторост по содержимому, Enter отправляет,
 * Shift+Enter переносит строку.
 *
 * maxLength нужен не для красоты: сервер режет вопрос по длине, и узнавать об
 * этом ошибкой после отправки — плохой обмен.
 */
export const ChatComposer = ({
    value, onChange, onSubmit, busy = false, disabled = false,
    placeholder = 'Спросите что-нибудь…', maxLength = 1000, hint = null,
}) => {
    const areaRef = useRef(null);
    const [rows, setRows] = useState(1);

    useEffect(() => {
        const area = areaRef.current;
        if (!area) return;
        area.style.height = 'auto';
        area.style.height = `${Math.min(area.scrollHeight, 140)}px`;
        setRows(value.includes('\n') ? 2 : 1);
    }, [value]);

    const submit = () => {
        const trimmed = (value || '').trim();
        if (!trimmed || busy || disabled) return;
        onSubmit(trimmed);
    };

    const left = maxLength - (value || '').length;

    return (
        <div className="border-t border-slate-200/70 bg-white/90 px-3 py-2.5 backdrop-blur">
            <div className="flex items-end gap-2">
                <textarea
                    ref={areaRef}
                    rows={rows}
                    value={value}
                    disabled={disabled}
                    maxLength={maxLength}
                    onChange={(e) => onChange(e.target.value)}
                    onKeyDown={(e) => {
                        if (e.key === 'Enter' && !e.shiftKey) {
                            e.preventDefault();
                            submit();
                        }
                    }}
                    placeholder={placeholder}
                    className="max-h-[140px] min-h-[40px] flex-1 resize-none rounded-xl border-0 bg-slate-100 px-3.5 py-2.5 text-[14px] text-slate-900 placeholder-slate-400 transition focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70 disabled:opacity-60"
                />
                <button
                    type="button"
                    onClick={submit}
                    disabled={busy || disabled || !(value || '').trim()}
                    className="inline-flex h-[40px] shrink-0 items-center justify-center gap-1.5 rounded-xl bg-blue-600 px-3.5 text-[13px] font-semibold text-white shadow-sm transition-all hover:bg-blue-700 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50"
                >
                    {busy ? <Loader2 size={15} className="animate-spin" /> : <CornerDownLeft size={15} />}
                    <span className="hidden sm:inline">{busy ? 'Думаю…' : 'Спросить'}</span>
                </button>
            </div>
            <div className="mt-1 flex items-center justify-between px-1 text-[11px] text-slate-400">
                <span>{hint || 'Enter — отправить, Shift+Enter — перенос строки'}</span>
                {left < 200 && <span className={left < 0 ? 'text-rose-500' : ''}>{left}</span>}
            </div>
        </div>
    );
};
