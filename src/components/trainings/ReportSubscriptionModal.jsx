import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { BellRing, Send, TriangleAlert } from 'lucide-react';
import { IosModal, IosToggle, iosBtnSecondary, iosBtnGhost, iosCard } from '../ui/ios';
import { errText } from './constants';

/* Подписка на Telegram-сводки по проведённым тренингам (задача #261).
 *
 * Один главный переключатель — «получать сводки в Telegram». Он и есть тот
 * самый «специальный переключатель» раздела: включил — приходит всё, что
 * требует постановка (день, неделя, месяц). Периодичности показываются ТОЛЬКО
 * когда главный включён: три ряда на выключенной настройке — это три строки,
 * которые ничего не значат.
 *
 * Выключение главного гасит все три сразу, но ЗАПОМИНАТЬ прежний набор мы не
 * пытаемся: настройка, которая при повторном включении возвращает не то, что
 * показывает, врёт. Включение всегда даёт полный набор.
 *
 * Оптимистичной правки здесь нет намеренно: значение приходит с сервера тем же
 * ответом, что и запись, и подменять его локально незачем — а вот разойтись с
 * ним при отказе очень легко.
 */

const PERIOD_ORDER = ['daily', 'weekly', 'monthly'];

const FALLBACK_META = {
    daily: { label: 'Ежедневно', hint: 'За вчера' },
    weekly: { label: 'Еженедельно', hint: 'За прошлую неделю' },
    monthly: { label: 'Ежемесячно', hint: 'За прошлый месяц' },
};

export default function ReportSubscriptionModal({ open, onClose, apiBaseUrl, headers, onToast }) {
    const [state, setState] = useState(null);
    const [loading, setLoading] = useState(false);
    const [loadError, setLoadError] = useState('');
    const [saving, setSaving] = useState('');   // какая периодичность сейчас пишется
    const [sending, setSending] = useState('');

    const load = useCallback(async () => {
        setLoading(true);
        setLoadError('');
        try {
            const response = await axios.get(`${apiBaseUrl}/api/trainings/report_subscription`, headers);
            setState(response?.data || null);
        } catch (error) {
            setLoadError(errText(error, 'Не удалось загрузить настройку'));
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, headers]);

    useEffect(() => {
        if (!open) return;
        load();
    }, [open, load]);

    const periods = useMemo(() => state?.periods || {}, [state]);
    const anyOn = PERIOD_ORDER.some((key) => periods[key]);

    const meta = useMemo(() => {
        const byValue = {};
        (state?.period_meta || []).forEach((item) => { byValue[item.value] = item; });
        return PERIOD_ORDER.map((value) => ({
            value,
            label: byValue[value]?.label || FALLBACK_META[value].label,
            hint: byValue[value]?.hint || FALLBACK_META[value].hint,
        }));
    }, [state]);

    const save = useCallback(async (payload, busyKey) => {
        setSaving(busyKey);
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/trainings/report_subscription`, payload, headers);
            setState((current) => ({ ...(current || {}), ...(response?.data || {}) }));
        } catch (error) {
            onToast?.(errText(error, 'Не удалось сохранить настройку'), 'error');
        } finally {
            setSaving('');
        }
    }, [apiBaseUrl, headers, onToast]);

    const toggleMaster = useCallback((next) => {
        save({ periods: { daily: next, weekly: next, monthly: next } }, 'master');
    }, [save]);

    const togglePeriod = useCallback((period, next) => {
        save({ period, enabled: next }, period);
    }, [save]);

    const sendNow = useCallback(async (period) => {
        setSending(period);
        try {
            const response = await axios.post(
                `${apiBaseUrl}/api/trainings/report_preview`, { period }, headers);
            onToast?.(`Сводка за ${response?.data?.period_label || 'период'} отправлена в Telegram`, 'success');
        } catch (error) {
            onToast?.(errText(error, 'Не удалось отправить сводку'), 'error');
        } finally {
            setSending('');
        }
    }, [apiBaseUrl, headers, onToast]);

    const telegramConnected = state?.telegram_connected !== false;
    const busy = Boolean(saving);

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title="Отчёты по тренингам в Telegram"
            subtitle={state?.scope_label ? `Область: ${state.scope_label}` : undefined}
            maxWidth="max-w-md"
            footer={(
                <button type="button" onClick={onClose} className={iosBtnSecondary}>Готово</button>
            )}
        >
            {loading && !state && (
                <div className="py-8 text-center text-[13px] text-slate-500">Загрузка…</div>
            )}

            {loadError && (
                <div className="rounded-2xl bg-rose-50 px-4 py-3 text-[12.5px] text-rose-700 ring-1 ring-rose-200/70">
                    {loadError}
                    <button type="button" onClick={load} className={`${iosBtnGhost} mt-1 !text-rose-700`}>
                        Повторить
                    </button>
                </div>
            )}

            {state && !loadError && (
                <div className="space-y-3">
                    {/* Главный переключатель. Подпись обещает ровно то, что придёт:
                        дата, тема, кто проводил и ФИО участников — это и есть
                        постановка задачи, и человек должен понимать, на что
                        подписывается, не открывая первое письмо. */}
                    <div className={`${iosCard} flex items-start justify-between gap-3 p-4`}>
                        <div className="min-w-0">
                            <div className="flex items-center gap-2 text-[13.5px] font-semibold text-slate-900">
                                <BellRing size={15} className="shrink-0 text-slate-400" />
                                Получать сводки в Telegram
                            </div>
                            <p className="mt-1 text-[12px] leading-relaxed text-slate-500">
                                Дата занятия, тема, кто проводил, количество участников и ФИО каждого —
                                сообщением и файлом Excel.
                            </p>
                        </div>
                        <IosToggle
                            checked={anyOn}
                            onChange={toggleMaster}
                            disabled={busy || !telegramConnected}
                        />
                    </div>

                    {!telegramConnected && (
                        <div className="flex items-start gap-2 rounded-2xl bg-amber-50 px-4 py-3 text-[12px] leading-relaxed text-amber-800 ring-1 ring-amber-200/70">
                            <TriangleAlert size={14} className="mt-0.5 shrink-0" />
                            <span>
                                К вашей учётной записи не привязан Telegram — отправлять сводку некуда.
                                Напишите боту портала, чтобы он вас узнал, и вернитесь сюда.
                            </span>
                        </div>
                    )}

                    {/* Периодичности — только при включённом главном. Подсказка
                        о времени приходит с сервера, чтобы обещание и
                        расписание рассылки не разъезжались. */}
                    {anyOn && (
                        <div className={`${iosCard} divide-y divide-slate-100`}>
                            {meta.map((item) => (
                                <div key={item.value} className="flex items-center justify-between gap-3 px-4 py-3">
                                    <div className="min-w-0">
                                        <div className="text-[13px] font-medium text-slate-800">{item.label}</div>
                                        <div className="text-[11.5px] leading-relaxed text-slate-500">{item.hint}</div>
                                    </div>
                                    <div className="flex shrink-0 items-center gap-1.5">
                                        <button
                                            type="button"
                                            onClick={() => sendNow(item.value)}
                                            disabled={Boolean(sending) || !telegramConnected}
                                            title="Прислать эту сводку сейчас"
                                            className={iosBtnGhost}
                                        >
                                            <Send size={13} />
                                            {sending === item.value ? 'Отправляю…' : 'Сейчас'}
                                        </button>
                                        <IosToggle
                                            checked={Boolean(periods[item.value])}
                                            onChange={(next) => togglePeriod(item.value, next)}
                                            disabled={busy || !telegramConnected}
                                        />
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}

                    <p className="px-1 text-[11px] leading-relaxed text-slate-500">
                        {state.scope === 'global'
                            ? 'Сводка приходит по всем отделам.'
                            : `Сводка приходит по вашим отделам: ${state.scope_label}.`}
                    </p>
                </div>
            )}
        </IosModal>
    );
}
