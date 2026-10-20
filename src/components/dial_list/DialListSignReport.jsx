import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Loader2, Send } from 'lucide-react';
import { IosHint, IosModal, iosBtnGhost, iosBtnPrimary, iosBtnSecondary, iosGroupLabel } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { lastSentLine, reportNotices, sameChatIds, scheduleText } from './signReport';

/*
 * «Отчёт в Telegram» во вкладке «Аналитика» удалённого КЦ (владелец, 19.10.2026): руководитель
 * выбирает группы, и в 10:00 и в 19:00 туда приходит картинка «Подписание водителей по городам»
 * (dial_list/sign_report.py) — по базе обзваниваемого месяца.
 *
 * Своего списка групп раздел не ведёт: в выборе — группы, куда добавлен бот портала (тот же
 * реестр, что у «Обращений» и «Лидов OLX»). Кнопка открывает окно; группы и итог последней
 * отправки грузятся при открытии, а картинка — только по «Как выглядит»: собирать её на каждое
 * открытие незачем.
 */

const NOTICE_TONE = { amber: 'text-amber-700', rose: 'text-rose-600' };

const GROUPS_HINT = 'Картинка по базе обзваниваемого месяца: сколько водителей подписали документы и сколько '
    + 'нет — по городам, от лучшего города к худшему. «Подписали» — в том числе те, чей документ ждёт '
    + 'подписи Яндекса. За полчаса до отправки сервер заново проверяет в Sapar всех, кто ещё не подписал, — '
    + 'в отчёте статусы на время рассылки. В списке — группы, куда добавлен бот портала: нужной нет — '
    + 'добавьте в неё бота, и она появится здесь сама.';

const DialListSignReport = ({ apiBaseUrl, authHeaders, departmentId, canEdit = true, showToast }) => {
    const [open, setOpen] = useState(false);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');
    const [draft, setDraft] = useState(null);
    const [saving, setSaving] = useState(false);
    const [preview, setPreview] = useState({ url: '', loading: false, error: '' });
    const previewUrl = useRef('');
    // showToast — новая функция на каждый рендер App: держим её в ref, а не в зависимостях.
    const toastRef = useRef(showToast);
    toastRef.current = showToast;

    const base = `${apiBaseUrl}/api/dial_list/departments/${departmentId}/sign_report`;

    const dropPreview = useCallback(() => {
        if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
        previewUrl.current = '';
        setPreview({ url: '', loading: false, error: '' });
    }, []);

    const load = useCallback(async () => {
        setLoading(true);
        setError('');
        try {
            const resp = await fetch(`${base}/chats`, { credentials: 'include', headers: authHeaders() });
            const body = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(body?.error || `HTTP ${resp.status}`);
            setData(body);
        } catch (e) {
            setError(e.message || 'Не удалось загрузить группы');
        } finally {
            setLoading(false);
        }
    }, [base, authHeaders]);

    useEffect(() => {
        if (!open) return;
        setDraft(null);
        load();
    }, [open, load]);

    // Отдел сменили или окно закрыли — картинка прежнего отчёта не нужна.
    useEffect(() => { if (!open) dropPreview(); }, [open, dropPreview]);
    useEffect(() => () => { if (previewUrl.current) URL.revokeObjectURL(previewUrl.current); }, []);
    useEffect(() => { setOpen(false); setData(null); }, [departmentId]);

    const showPreview = async () => {
        dropPreview();
        setPreview({ url: '', loading: true, error: '' });
        try {
            const resp = await fetch(`${base}/preview`, { credentials: 'include', headers: authHeaders() });
            if (!resp.ok) {
                const body = await resp.json().catch(() => ({}));
                throw new Error(body?.error || `HTTP ${resp.status}`);
            }
            const url = URL.createObjectURL(await resp.blob());
            previewUrl.current = url;
            setPreview({ url, loading: false, error: '' });
        } catch (e) {
            setPreview({ url: '', loading: false, error: e.message || 'Не удалось собрать картинку' });
        }
    };

    const chosen = useMemo(() => (data?.chosen_ids || []).map(Number), [data]);
    // Варианты — мемо: CustomSelect пересчитывает подсветку на каждый новый массив.
    const options = useMemo(() => {
        const list = (data?.available || []).map((g) => ({ value: Number(g.chat_id), label: g.title || String(g.chat_id) }));
        // Выбранная группа, где бота уже нет, в реестре не значится — оставляем её в списке,
        // чтобы её можно было снять.
        (data?.selected || []).forEach((s) => {
            if (!list.some((o) => o.value === Number(s.chat_id))) {
                list.push({ value: Number(s.chat_id), label: s.title || String(s.chat_id) });
            }
        });
        return list;
    }, [data]);
    const titles = useCallback((values) => (values || [])
        .map((id) => options.find((o) => String(o.value) === String(id))?.label || id)
        .join(', '), [options]);

    const value = draft === null ? chosen : draft;
    const changed = draft !== null && !sameChatIds(draft, chosen);
    const notices = reportNotices(data?.selected);
    const sent = lastSentLine(data?.selected);
    const when = scheduleText(data?.schedule) || 'в 10:00 и 19:00';

    const save = async () => {
        setSaving(true);
        try {
            const resp = await fetch(`${base}/chats`, {
                method: 'PUT',
                credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({ chat_ids: value }),
            });
            const body = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(body?.error || `HTTP ${resp.status}`);
            setData(body);
            setDraft(null);
            toastRef.current?.(value.length ? `Отчёт будет приходить ${when}` : 'Отчёт в Telegram выключен', 'success');
            setOpen(false);
        } catch (e) {
            toastRef.current?.(e.message || 'Не удалось сохранить группы', 'error');
        } finally {
            setSaving(false);
        }
    };

    return (
        <>
            <button
                type="button"
                onClick={() => setOpen(true)}
                className={`${iosBtnGhost} shrink-0`}
                title="Картинка «Подписание водителей по городам» в Telegram-группы"
            >
                <Send size={14} />
                Отчёт в Telegram
            </button>
            <IosModal
                open={open}
                onClose={() => setOpen(false)}
                title="Отчёт в Telegram"
                subtitle={`Подписание водителей по городам · ${when}`}
                footer={canEdit ? (
                    <>
                        <button type="button" onClick={() => setOpen(false)} className={iosBtnSecondary}>Отмена</button>
                        <button
                            type="button"
                            onClick={save}
                            disabled={!changed || saving}
                            className={`${iosBtnPrimary} disabled:opacity-40`}
                        >
                            {saving ? 'Сохраняем…' : 'Сохранить'}
                        </button>
                    </>
                ) : null}
            >
                <div className="space-y-4">
                    <section className="space-y-1.5">
                        <div className="flex items-center gap-1.5 px-1">
                            <span className={iosGroupLabel}>Группы</span>
                            <IosHint text={GROUPS_HINT} label="Что за отчёт" />
                        </div>
                        {!data && loading && (
                            <div className="flex items-center gap-2 px-1 py-2 text-[13px] text-slate-500">
                                <Loader2 size={14} className="animate-spin" /> Загружаем группы…
                            </div>
                        )}
                        {error && <p className="px-1 text-[12.5px] text-rose-600">{error}</p>}
                        {data && (options.length ? (
                            /* В кнопке — имена групп, а не «Выбрано: N»: важно ровно то, КУДА уходит отчёт. */
                            <CustomSelect
                                multiple
                                value={value}
                                onChange={setDraft}
                                options={options}
                                placeholder="Выберите группы"
                                searchable
                                variant="ios"
                                renderValue={titles}
                                disabled={!canEdit}
                                ariaLabel="Группы для отчёта"
                            />
                        ) : (
                            <p className="px-1 text-[12.5px] text-amber-700">
                                {data.registry_ok === false
                                    ? 'Список групп бота сейчас недоступен — попробуйте через минуту.'
                                    : 'Бот пока не добавлен ни в одну группу. Добавьте его туда, где нужен отчёт, — группа появится здесь сама.'}
                            </p>
                        ))}
                        {notices.map((n) => (
                            <p key={n.text} className={`px-1 text-[12.5px] ${NOTICE_TONE[n.tone]}`}>{n.text}</p>
                        ))}
                        {sent && <p className="px-1 text-[12px] text-slate-500">{sent}</p>}
                    </section>

                    <section className="space-y-2">
                        {!preview.url && (
                            <button type="button" onClick={showPreview} disabled={preview.loading} className={iosBtnGhost}>
                                {preview.loading ? <Loader2 size={14} className="animate-spin" /> : null}
                                {preview.loading ? 'Собираем картинку…' : 'Как выглядит отчёт'}
                            </button>
                        )}
                        {preview.error && <p className="px-1 text-[12.5px] text-rose-600">{preview.error}</p>}
                        {preview.url && (
                            /* Целиком — в новой вкладке: в окне картинка ужата по ширине. */
                            <a href={preview.url} target="_blank" rel="noreferrer" title="Открыть в полном размере">
                                <img
                                    src={preview.url}
                                    alt="Подписание водителей по городам — как сейчас"
                                    className="w-full rounded-xl ring-1 ring-slate-200"
                                />
                            </a>
                        )}
                    </section>
                </div>
            </IosModal>
        </>
    );
};

export default DialListSignReport;
