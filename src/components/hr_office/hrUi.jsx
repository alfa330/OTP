import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Loader2 } from 'lucide-react';
import InfoHint from '../common/InfoHint';
import { SCREEN_LEAVE_MS, WINDOW_LEAVE_MS } from '../ui/ios';
import { errorText } from './hrOffice';

/*
 * Списки страницами с сервера (владелец 12.11.2026: «сделать нормальную пагинацию») — как в «Учете
 * сотрудников»: по 50 строк, под списком общий IosPager «1–50 из 265» с номерами страниц. Поиск уходит
 * после паузы в печати; сменился отбор — снова первая страница; ответ на прежний запрос новый не
 * перетирает; опустевшая страница (последнего на ней оформили) — шаг на последнюю непустую.
 */
export const PAGE_SIZE = 50;

/*
 * Узкий экран — телефон стоя (уже 640 px, ступень `sm` Tailwind). На нём табель показывается списком
 * людей с месяцем человека отдельным экраном, а не таблицей на 31 день: таблица в 390 px — это колонка
 * имён и дни за краем. Телефон боком и планшет (640+) получают таблицу. Признак оболочки телефона
 * (useIsMobileShell) сюда не годится: он включён и у телефона боком (844 px), где таблица уместна.
 */
const NARROW_QUERY = '(max-width: 639px)';

export function useNarrowScreen() {
    const read = () => (typeof window !== 'undefined' && typeof window.matchMedia === 'function'
        ? window.matchMedia(NARROW_QUERY).matches : false);
    const [narrow, setNarrow] = useState(read);
    useEffect(() => {
        if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return undefined;
        const query = window.matchMedia(NARROW_QUERY);
        const update = () => setNarrow(query.matches);
        update();
        if (query.addEventListener) query.addEventListener('change', update);
        else query.addListener?.(update);
        return () => {
            if (query.removeEventListener) query.removeEventListener('change', update);
            else query.removeListener?.(update);
        };
    }, []);
    return narrow;
}

export function pagerProps(page, total, pageSize = PAGE_SIZE) {
    const count = Number(total) || 0;
    return {
        page,
        pageCount: Math.max(1, Math.ceil(count / pageSize)),
        total: count,
        from: count ? (page - 1) * pageSize + 1 : 0,
        to: Math.min(count, page * pageSize),
    };
}

export function useServerPage(api, path, params, {
    searchField = 'q', itemsField = 'people', delay = 300, fallback = 'Список не загрузился',
} = {}) {
    const search = String(params?.[searchField] ?? '').trim();
    const [settled, setSettled] = useState(search);
    useEffect(() => {
        if (search === settled) return undefined;
        const timer = window.setTimeout(() => setSettled(search), delay);
        return () => window.clearTimeout(timer);
    }, [search, settled, delay]);
    const filters = {};
    Object.entries(params || {}).forEach(([name, value]) => {
        if (name !== searchField && value !== undefined && value !== null && value !== '') filters[name] = value;
    });
    if (settled) filters[searchField] = settled;
    const key = JSON.stringify(filters);
    const [pageState, setPageState] = useState({ key, number: 1 });
    const page = pageState.key === key ? pageState.number : 1;
    const setPage = useCallback((number) => setPageState({ key, number }), [key]);
    const [state, setState] = useState({ data: null, error: '', loading: true });
    const ticket = useRef(0);
    const load = useCallback(() => {
        const mine = ticket.current + 1;
        ticket.current = mine;
        setState((prev) => ({ ...prev, loading: true }));
        return api.get(path, { ...JSON.parse(key), offset: (page - 1) * PAGE_SIZE, limit: PAGE_SIZE })
            .then(({ data }) => {
                if (mine !== ticket.current) return;
                if (!(data?.[itemsField] || []).length && data?.total > 0 && page > 1) {
                    setPageState({ key, number: Math.max(1, Math.ceil(data.total / PAGE_SIZE)) });
                    return;
                }
                setState({ data, error: '', loading: false });
            })
            .catch((requestError) => {
                if (mine !== ticket.current) return;
                setState((prev) => ({ ...prev, error: errorText(requestError, fallback), loading: false }));
            });
    }, [api, path, key, page, itemsField, fallback]);
    useEffect(() => { load(); }, [load]);
    return { ...state, page, setPage, reload: load, query: filters, searching: search !== settled };
}

/*
 * ОКНО УХОДИТ ПЛАВНО, ТОЛЬКО ЕСЛИ ОСТАЁТСЯ В ДЕРЕВЕ. IosModal гасит себя сам, когда `open`
 * становится false; окно же, которое родитель снимает условием (`{x && <Окно/>}`), исчезает
 * за кадр — так было во всех окнах «Приема», «Табеля» и «АВР» (владелец 10.11.2026: «сейчас
 * всё слишком резко»). Хук держит окно смонтированным, пока доигрывает уход:
 *   const [sheet, keptSheet, openSheet, closeSheet, swapSheet, token] = useSheetState('');
 *   {keptSheet === 'journal' && <HrJournalSheet key={token} open={sheet === 'journal'} onClose={closeSheet} />}
 * value — что открыто сейчас, kept — что ещё в дереве (то же значение, пока окно уходит),
 * token — номер открытия: окно с key={token} при каждом открытии монтируется заново. Без него
 * окно, открытое снова, пока прежнее ещё гаснет, досталось бы тем же экземпляром — со старым
 * шагом («итог» загрузки вместо выбора файла) и со старым значением ячейки табеля.
 */
const LEAVE_MS = Math.max(WINDOW_LEAVE_MS, SCREEN_LEAVE_MS) + 60;

export function useSheetState(initial = null) {
    const [value, setValue] = useState(initial);
    const [kept, setKept] = useState(initial);
    const [token, setToken] = useState(0);
    const timer = useRef(0);
    const current = useRef(initial);
    current.current = value;
    const open = useCallback((next) => {
        /* Кнопка, которой окно открыли, остаётся в фокусе под затемнением: Enter или пробел
           нажали бы её снова. Фокус с неё снимаем, а повторное открытие того же окна — ничего не
           делает (раньше заново монтировало его и теряло сверку, выбор тёзок, галочки). */
        if (typeof document !== 'undefined' && document.activeElement instanceof HTMLElement) document.activeElement.blur();
        if (current.current === next && next !== initial) return;
        window.clearTimeout(timer.current);
        setToken((count) => count + 1);
        setKept(next);
        setValue(next);
    }, [initial]);
    const close = useCallback(() => {
        setValue(initial);
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => setKept(initial), LEAVE_MS);
    }, [initial]);
    /* Одно окно сменяет другое (итог загрузки → журнал): сначала уходит прежнее, потом
       открывается новое — два затемнения разом не встают, ничего не мигает. */
    const swap = useCallback((next) => {
        setValue(initial);
        window.clearTimeout(timer.current);
        timer.current = window.setTimeout(() => { setToken((count) => count + 1); setKept(next); setValue(next); }, LEAVE_MS);
    }, [initial]);
    useEffect(() => () => window.clearTimeout(timer.current), []);
    return [value, kept, open, close, swap, token];
}

/*
 * Общие детали интерфейса разделов «Прием», «Табель учета рабочего времени» и «АВР». Язык — тот же, что у «Воронки найма» и «Аренды авто»:
 * белые поверхности с тонким кольцом, кнопки строки инструментов 36 px, цвет только со смыслом.
 */
export const TOOLBAR_BTN =
    'inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-white px-3.5 text-[13px] font-medium text-slate-700 ring-1 ring-slate-200/70 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition hover:bg-slate-50 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50';
export const TOOLBAR_BTN_PRIMARY =
    'inline-flex h-9 shrink-0 items-center justify-center gap-1.5 rounded-xl bg-blue-600 px-3.5 text-[13px] font-semibold text-white shadow-sm transition hover:bg-blue-700 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50';
export const FIELD =
    'w-full rounded-xl border-0 bg-slate-100 px-3.5 py-2.5 text-[14px] text-slate-900 placeholder-slate-400 transition focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70';
export const FIELD_INVALID = 'ring-2 ring-rose-400/80 bg-rose-50/60';

/* Вид значения ячейки табеля — один для таблицы на компьютере и списка дней на телефоне. */
export const CELL_TONE = {
    work: 'text-[12.5px] font-medium text-slate-700',
    off: 'text-[11.5px] text-slate-300',
    leave: 'rounded-md bg-emerald-50 px-1 text-[11px] font-semibold text-emerald-700 ring-1 ring-emerald-100',
    unpaid: 'rounded-md bg-amber-50 px-1 text-[11px] font-semibold text-amber-700 ring-1 ring-amber-100',
    sick: 'rounded-md bg-sky-50 px-1 text-[11px] font-semibold text-sky-700 ring-1 ring-sky-100',
    empty: 'text-[11px] text-slate-200',
};

export function Loading({ text = 'Загружаем…' }) {
    return (
        <div className="flex min-h-[200px] items-center justify-center gap-2 text-[13.5px] text-slate-500">
            <Loader2 size={16} className="animate-spin" /> {text}
        </div>
    );
}

/* Поле формы: подпись, «i» с пояснением (а не строка под полем) и ошибка красным у поля. */
export function Field({ label, hint = '', error = '', note = '', children, className = '' }) {
    return (
        <div className={`block space-y-1 ${className}`}>
            <span className="flex items-center gap-1.5 px-1 text-[12px] font-medium text-slate-500">
                {label}
                {hint && <InfoHint text={hint} />}
            </span>
            {children}
            {error && <span className="block px-1 text-[12px] text-rose-600">{error}</span>}
            {!error && note && <span className="block px-1 text-[12px] text-amber-700">{note}</span>}
        </div>
    );
}

export function Empty({ children }) {
    return <div className="px-4 py-10 text-center text-[13.5px] text-slate-500">{children}</div>;
}
