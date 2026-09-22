import React, { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
import {
    Search, Loader2, PhoneCall, PhoneIncoming, MessageSquare, Bot, User2, ChevronRight,
    AlertCircle, Headphones, CloudDownload, ShieldAlert, BookMarked,
} from 'lucide-react';
import { iosCard, iosInput, iosBtnPrimary, iosBtnSecondary, iosGroupLabel, IosBadge, IosModal, scoreTone } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import IosDateRangePicker, { isoDate } from '../ui/DateRangePicker';
import { SUBJECT_IMPORTED_CALL, SUBJECT_C2D_SNAPSHOT, isChat, canPullCalls, subjectTitle } from './subjects';

/* Точечный подбор: найти конкретный звонок или переписку по номеру телефона,
 * сотруднику и периоду — и открыть на оценку.
 *
 * «Случайный звонок» отвечает на «дай что-нибудь», а здесь человек ищет
 * разговор, о котором знает: клиент назвал номер, сотрудник — фамилию. Поиск
 * идёт по СВОИМ данным отдела (/api/ai-qa/find): журнал, звонки, подтянутые из
 * АТС, эпизоды переписки; у отдела продаж ещё касания CDR — звонки, которых в
 * портале пока нет (запись принесёт мост). У СЗоВ и Тез КЦ в саму АТС по
 * номеру можно сходить отдельной кнопкой: их АТС отдают звонки от сотрудника,
 * поэтому нужен и он.
 *
 * Открытие строки — то же, что клик в списке: onOpen({ id, subject, … }).
 * Касание CDR сперва подтягивается в пул (POST pull-call с linkedid) и
 * открывается уже как звонок из АТС. */

const shiftDays = (days) => {
    const d = new Date();
    d.setDate(d.getDate() + days);
    return isoDate(d);
};
// Форма пресета — как у календаря: { label, range: () => ({ from, to }) }.
const PRESETS = [
    { label: 'Сегодня', range: () => ({ from: isoDate(new Date()), to: isoDate(new Date()) }) },
    { label: '7 дней', range: () => ({ from: shiftDays(-6), to: isoDate(new Date()) }) },
    { label: '30 дней', range: () => ({ from: shiftDays(-29), to: isoDate(new Date()) }) },
    { label: 'Весь период', range: () => ({ from: '', to: '' }) },
];

const digitsOf = (value) => String(value || '').replace(/\D/g, '');

function ResultRow({ item, onPick, busy }) {
    const chat = isChat(item.subject);
    const Icon = chat ? MessageSquare : item.subject === 'cdr_touch' ? PhoneIncoming : PhoneCall;
    const untrusted = item.subject === 'cdr_touch' && item.recording_trusted === false;
    const title = item.subject === 'cdr_touch'
        ? `Звонок ${item.call_type === 'in' ? 'входящий' : 'исходящий'}`
        : subjectTitle(item.subject, item.id);
    return (
        <button type="button" onClick={() => onPick(item)} disabled={busy || untrusted}
                title={untrusted ? 'Запись этого звонка станция подставила от другого агента — слушать и оценивать её нельзя' : undefined}
                className={`${iosCard} flex w-full items-center gap-3 p-3 text-left transition hover:ring-blue-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/60 active:scale-[0.995] disabled:cursor-not-allowed disabled:opacity-60`}>
            <Icon size={16} className="shrink-0 text-slate-400" aria-hidden="true" />
            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-[13.5px] font-semibold text-slate-900">{title}</span>
                    <span className="text-[12.5px] tabular-nums text-slate-500">{item.datetime}</span>
                    {item.subject === SUBJECT_IMPORTED_CALL && <IosBadge tone="blue">из АТС</IosBadge>}
                    {item.subject === 'cdr_touch' && (
                        <IosBadge tone="blue" title="Звонка в портале ещё нет: запись принесёт мост после подтяжки">
                            <CloudDownload size={11} />CDR
                        </IosBadge>
                    )}
                    {item.audio === 'pending' && (
                        <IosBadge tone="amber" title="Запись ещё докачивается"><Headphones size={11} />запись готовится</IosBadge>
                    )}
                    {untrusted && <IosBadge tone="red"><ShieldAlert size={11} />чужая запись</IosBadge>}
                    {item.in_journal && <IosBadge tone="slate" title="Есть оценка в «Журнале оценок»"><BookMarked size={11} />в журнале</IosBadge>}
                </div>
                <p className="mt-0.5 truncate text-[12px] text-slate-400">
                    {item.operator}{item.direction && item.direction !== '—' ? ` · ${item.direction}` : ''}
                    {item.phone ? ` · ${item.phone}` : ''}
                    {item.contact && item.contact !== item.phone ? ` · ${item.contact}` : ''}
                    {item.talk_seconds != null ? ` · ${Math.floor(item.talk_seconds / 60)}:${String(item.talk_seconds % 60).padStart(2, '0')}` : ''}
                    {item.messages_count != null ? ` · ${item.messages_count} сообщений` : ''}
                    {item.operator_share != null ? ` · ответы оператора ${item.operator_share}%` : ''}
                </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
                {item.ai_score != null && (
                    <IosBadge tone={scoreTone(item.ai_score)} className="tabular-nums" title="Оценка ИИ">
                        <Bot size={11} />{item.ai_score}
                    </IosBadge>
                )}
                {item.human_score != null && (
                    <IosBadge tone="slate" className="tabular-nums" title="Оценка человека">
                        <User2 size={11} />{Math.round(item.human_score)}
                    </IosBadge>
                )}
                <ChevronRight size={16} className="text-slate-300" />
            </div>
        </button>
    );
}

export default function FindSubjectModal({
    open, onClose, apiBaseUrl, withAccessTokenHeader, department, family = 'calls',
    initialFilters = null, showToast, onOpen,
}) {
    const headers = () => (withAccessTokenHeader ? withAccessTokenHeader() : {});
    const chats = family === 'chats';
    const [phone, setPhone] = useState('');
    const [operatorId, setOperatorId] = useState(initialFilters?.operator_id ?? null);
    const [period, setPeriod] = useState({ from: initialFilters?.date_from || '', to: initialFilters?.date_to || '' });
    const [operators, setOperators] = useState([]);
    const [items, setItems] = useState(null);       // null — ещё не искали
    const [truncated, setTruncated] = useState(false);
    const [busy, setBusy] = useState(false);
    const [pulling, setPulling] = useState(false);
    const [error, setError] = useState(null);
    const request = useRef({ id: 0, controller: null });
    const phoneRef = useRef(null);

    // Открытие — чистый лист с тем, что уже выбрано в панели фильтров раздела.
    useEffect(() => {
        if (!open) return;
        setPhone('');
        setOperatorId(initialFilters?.operator_id ?? null);
        setPeriod({ from: initialFilters?.date_from || '', to: initialFilters?.date_to || '' });
        setItems(null); setTruncated(false); setError(null);
        window.setTimeout(() => phoneRef.current?.focus?.(), 50);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open]);

    useEffect(() => {
        if (!open || !apiBaseUrl) return undefined;
        const controller = new AbortController();
        axios.get(`${apiBaseUrl}/api/ai-qa/filter-options`, {
            params: { ...(department ? { department } : {}), subject: family },
            headers: headers(), signal: controller.signal,
        })
            .then((r) => setOperators(r.data?.operators || []))
            .catch(() => setOperators([]));
        return () => controller.abort();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, apiBaseUrl, department, family]);

    useEffect(() => () => request.current.controller?.abort(), []);

    const operatorOptions = useMemo(() => [
        { value: null, label: 'Любой сотрудник' },
        ...operators.map((p) => ({ value: p.id, label: `${p.name}${p.fired ? ' · уволен' : ''}` })),
    ], [operators]);

    const canSearch = digitsOf(phone).length >= 4 || operatorId != null;
    const canSearchPbx = !chats && canPullCalls(department) && department !== 'op'
        && digitsOf(phone).length >= 4 && operatorId != null;

    const search = () => {
        if (!apiBaseUrl || !canSearch) return;
        request.current.controller?.abort();
        const controller = new AbortController();
        const requestId = request.current.id + 1;
        request.current = { id: requestId, controller };
        setBusy(true); setError(null);
        axios.get(`${apiBaseUrl}/api/ai-qa/find`, {
            params: {
                kind: family, ...(department ? { department } : {}),
                ...(digitsOf(phone).length >= 4 ? { phone: digitsOf(phone) } : {}),
                ...(operatorId != null ? { operator_id: operatorId } : {}),
                ...(period.from ? { date_from: period.from } : {}),
                ...(period.to ? { date_to: period.to } : {}),
            },
            headers: headers(), signal: controller.signal,
        })
            .then((r) => {
                if (requestId !== request.current.id) return;
                setItems(r.data?.items || []);
                setTruncated(Boolean(r.data?.truncated));
            })
            .catch((e) => {
                if (axios.isCancel(e) || requestId !== request.current.id) return;
                setItems([]);
                setError(e?.response?.data?.error || 'Не удалось выполнить поиск');
            })
            .finally(() => { if (requestId === request.current.id) setBusy(false); });
    };

    /* Подтяжка из АТС/CDR: строки в пуле ещё нет, сервер кладёт её и отдаёт
     * звонок из АТС — открываем его карточкой сразу. */
    const pull = async (body, successText) => {
        if (!apiBaseUrl) return;
        setPulling(true);
        try {
            const r = await axios.post(`${apiBaseUrl}/api/ai-qa/pull-call`,
                { ...(department ? { department } : {}), count: 1, ...body }, { headers: headers() });
            const call = (r.data?.calls || [])[0] || r.data?.call;
            if (!call) { showToast?.('АТС не вернула подходящий звонок', 'error'); return; }
            showToast?.(call.audio_pending ? `${successText} — качаю запись и оцениваю` : `${successText} — оцениваю`, 'success');
            onOpen?.({ id: call.id, subject: SUBJECT_IMPORTED_CALL, operator: call.operator_name, datetime: call.datetime });
            onClose?.();
        } catch (e) {
            showToast?.(e?.response?.data?.error || 'Не удалось подтянуть звонок', 'error');
        } finally {
            setPulling(false);
        }
    };

    const pickItem = (item) => {
        if (item.subject === 'cdr_touch') {
            pull({ linkedid: item.linkedid, operator_id: item.operator_id }, 'Звонок подтянут');
            return;
        }
        onOpen?.({ id: item.id, subject: item.subject, operator: item.operator, datetime: item.datetime });
        onClose?.();
    };

    const searchPbx = () => pull({
        operator_id: operatorId, phone: digitsOf(phone),
        ...(period.from ? { date_from: period.from } : {}),
        ...(period.to ? { date_to: period.to } : {}),
    }, 'Звонок найден в АТС');

    const unit = chats ? (department === 'szov' ? 'заявку' : 'чат') : 'звонок';

    return (
        /* Окно шире обычной формы: под полосой полей лежит список найденных
           разговоров с датой, сотрудником, номером и двумя баллами в строке. */
        <IosModal open={open} onClose={onClose} maxWidth="max-w-4xl"
                  title={chats ? 'Найти переписку' : 'Найти звонок'}
                  subtitle={`По номеру телефона, сотруднику и периоду · ${chats ? 'откроется на оценку' : 'откроется на оценку ИИ'}`}
                  footer={(
                      <>
                          {canSearchPbx && (
                              <button type="button" onClick={searchPbx} disabled={pulling || busy}
                                      className={iosBtnSecondary}
                                      title="Спросить саму АТС: у неё есть звонки, которых ещё нет в портале">
                                  {pulling ? <Loader2 size={14} className="animate-spin" /> : <PhoneIncoming size={14} />}
                                  Искать в АТС
                              </button>
                          )}
                          <button type="button" onClick={search} disabled={!canSearch || busy || pulling} className={iosBtnPrimary}>
                              {busy ? <Loader2 size={15} className="animate-spin" /> : <Search size={15} />}
                              Найти
                          </button>
                      </>
                  )}>
            {/* Три поля в один ряд: номер, сотрудник, период. Календарь периода
                уходит в портал — тело модалки прокручивается и absolute-поповер
                режет, календарь не помещался. Чип периода в габаритах поля
                (`[&>span]:flex-1`, как в фильтрах «Посылок»), чтобы ряд читался
                как три равных поля, а не два поля и кнопка. */}
            <form className="grid gap-3 sm:grid-cols-3" onSubmit={(e) => { e.preventDefault(); search(); }}>
                <label className="block space-y-1.5">
                    <span className={iosGroupLabel}>Номер телефона</span>
                    <input ref={phoneRef} type="tel" inputMode="tel" className={iosInput} value={phone}
                           onChange={(e) => setPhone(e.target.value)}
                           placeholder="Хватит последних цифр — от четырёх" />
                </label>
                <label className="block space-y-1.5">
                    <span className={iosGroupLabel}>Сотрудник</span>
                    <CustomSelect value={operatorId} onChange={(v) => setOperatorId(v || null)}
                                  options={operatorOptions} placeholder="Любой сотрудник"
                                  variant="ios" searchable ariaLabel="Сотрудник" />
                </label>
                <div className="block space-y-1.5">
                    <span className={iosGroupLabel}>Период</span>
                    <div className="flex items-center gap-2">
                        <div className="min-w-0 flex-1">
                            <IosDateRangePicker from={period.from} to={period.to} max={isoDate(new Date())}
                                                presets={PRESETS} portal
                                                triggerClassName={`${iosInput} flex items-center gap-2 text-left [&>span]:flex-1 [&>span]:truncate`}
                                                onChange={({ from, to }) => setPeriod({ from: from || '', to: to || '' })} />
                        </div>
                        {(period.from || period.to) && (
                            <button type="button" onClick={() => setPeriod({ from: '', to: '' })}
                                    className="shrink-0 text-[12.5px] font-medium text-slate-500 hover:text-slate-800"
                                    title="Снять период">
                                весь
                            </button>
                        )}
                    </div>
                </div>
                {/* Кнопка формы — чтобы Enter в поле номера запускал поиск. */}
                <button type="submit" className="hidden" aria-hidden="true" tabIndex={-1} />
            </form>

            {/* Список не ниже нескольких строк даже пустой: окно не должно
                менять высоту от поиска к поиску. */}
            <div className="mt-4 min-h-[300px]">
                {items === null && !busy ? (
                    <p className="px-1 text-[12.5px] text-slate-400">
                        Укажите номер телефона или сотрудника — найдём {unit} в журнале, среди подтянутых
                        из АТС{department === 'op' && !chats ? ' и в касаниях CDR' : ''}{chats ? ' и в эпизодах переписки' : ''}.
                        {canPullCalls(department) && department !== 'op' && !chats
                            ? ' Если в портале его ещё нет — «Искать в АТС» (нужны и номер, и сотрудник).' : ''}
                    </p>
                ) : busy ? (
                    <div className={`${iosCard} flex items-center justify-center gap-2 px-6 py-10 text-slate-500`} role="status">
                        <Loader2 size={18} className="animate-spin" />Ищу…
                    </div>
                ) : error ? (
                    <div className={`${iosCard} flex items-center gap-2 px-4 py-4 text-[13px] text-rose-600`} role="alert">
                        <AlertCircle size={16} />{error}
                    </div>
                ) : items.length === 0 ? (
                    <div className={`${iosCard} px-5 py-8 text-center`}>
                        <p className="text-[13.5px] font-medium text-slate-700">Ничего не нашлось</p>
                        <p className="mt-1 text-[12.5px] text-slate-500">
                            Проверьте номер, расширьте период или снимите сотрудника.
                            {canSearchPbx ? ' Или спросите саму АТС кнопкой «Искать в АТС».' : ''}
                        </p>
                    </div>
                ) : (
                    <div className="space-y-2">
                        {items.map((item) => (
                            <ResultRow key={`${item.subject}-${item.id}`} item={item} onPick={pickItem} busy={pulling} />
                        ))}
                        {truncated && (
                            <p className="px-1 text-[11.5px] text-slate-400">Показаны первые {items.length} — уточните номер или период.</p>
                        )}
                    </div>
                )}
            </div>
        </IosModal>
    );
}
