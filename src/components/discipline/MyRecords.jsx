import React, { useEffect, useState } from 'react';
import { CheckCircle2 } from 'lucide-react';
import { apiError } from './disciplineApi';
import { ACCEPT_DOCS, AttachButton, EmptyState, Group, LoadingRows, Spinner, StateBadge, T } from './disciplineUi';
import { fmtDate, fmtDateTime, fmtMoney } from './disciplineRules';

/*
 * Экран оператора (ТЗ п. 3): только свои записи — тип нарушения, дата, статус,
 * применённая мера. Записи коллег не видны. Объяснительную оператор загружает
 * сам — фото или скан прямо с телефона (п. 8.1). Описание обстоятельств,
 * доказательства и комментарий руководителя оператору не показываются.
 */

const MyRecords = ({ api, toast, version, reload, openRecord }) => {
    const [data, setData] = useState(null);
    const [error, setError] = useState(null);
    const [busy, setBusy] = useState(null);

    useEffect(() => {
        let alive = true;
        api.records({ date_from: '2000-01-01' })
            .then((payload) => { if (alive) { setData(payload.items || []); setError(null); } })
            .catch((err) => { if (alive) setError(apiError(err, 'Записи не загрузились')); });
        return () => { alive = false; };
    }, [api, version]);

    const upload = async (item, files) => {
        setBusy(item.id);
        try {
            await api.uploadExplanation(item.id, files[0]);
            toast('Объяснительная отправлена');
            reload();
        } catch (err) {
            toast(apiError(err, 'Файл не загрузился'), 'error');
        } finally {
            setBusy(null);
        }
    };

    if (error) return <Group><EmptyState title={error} /></Group>;
    if (!data) return <LoadingRows rows={4} />;
    if (!data.length) {
        return <Group><EmptyState icon={CheckCircle2} title="Записей о нарушениях нет" /></Group>;
    }

    const waiting = data.filter((item) => item.needs_explanation_now && !item.explanation_received_at && !item.refused_at
        && !['closed', 'cancelled'].includes(item.state));

    return (
        <div className="space-y-4">
            {waiting.length > 0 && (
                <Group title="Нужна объяснительная">
                    <ul className="space-y-3">
                        {waiting.map((item) => (
                            <li key={item.id} className="flex flex-wrap items-center justify-between gap-3">
                                <div className="min-w-0">
                                    <div className={`text-[13.5px] font-semibold text-slate-900 ${T.body}`}>{item.type_title}</div>
                                    <div className={`text-[12.5px] ${item.explanation_overdue ? 'font-medium text-rose-600' : 'text-slate-500'} ${T.sub}`}>
                                        {fmtDateTime(item.occurred_at)} · срок до {fmtDateTime(item.explanation_due_at)}
                                    </div>
                                </div>
                                {busy === item.id ? <Spinner /> : (
                                    <AttachButton label="Загрузить фото или скан" accept={ACCEPT_DOCS} onFiles={(files) => upload(item, files)} />
                                )}
                            </li>
                        ))}
                    </ul>
                </Group>
            )}
            <Group title="Мои записи" cardClassName="p-0 overflow-hidden">
                <ul className="divide-y divide-slate-100">
                    {data.map((item) => (
                        <li key={item.id}>
                            <button type="button" onClick={() => openRecord(item.id)}
                                    className="block w-full space-y-1.5 px-4 py-3 text-left hover:bg-slate-50 xl:px-5">
                                <span className="flex items-center justify-between gap-3">
                                    <span className={`text-[12.5px] tabular-nums text-slate-500 ${T.sub}`}>{fmtDate(item.occurred_at)}</span>
                                    <StateBadge state={item.state} />
                                </span>
                                <span className={`block text-[13.5px] text-slate-900 ${T.body}`}>{item.type_title}</span>
                                {item.measure_title ? (
                                    <span className={`block text-[12.5px] text-slate-600 ${T.sub}`}>
                                        Мера: {item.measure_title}
                                        {item.measure_amount ? ` · ${fmtMoney(item.measure_amount)}` : ''}
                                    </span>
                                ) : null}
                            </button>
                        </li>
                    ))}
                </ul>
            </Group>
        </div>
    );
};

export default MyRecords;
