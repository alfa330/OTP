import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    ArrowLeft, Download, FileText, Loader2, Users,
} from 'lucide-react';

import { iosCard, iosGroupLabel, iosBtnSecondary, IosBadge } from '../ui/ios';

/* Статистика одного тренажёра.
 *
 * Три разреза, и порядок у них отвечает на вопросы в том порядке, в котором их
 * задают: сколько всего → откуда заходили → кто проходил → что происходило
 * по попыткам. Последняя таблица подробная и потому идёт последней: её
 * открывают, когда сводке не поверили.
 *
 * Выгрузка собирает ровно эти же четыре среза. Расхождение между экраном и
 * файлом — самый дорогой сорт расхождения: его замечают уже в переписке с
 * заказчиком, и объяснить его нечем.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const STATUS = {
    finished: { label: 'Прошёл', tone: 'green' },
    started: { label: 'Не завершил', tone: 'amber' },
    abandoned: { label: 'Бросил', tone: 'slate' },
};

const SOURCE = { article: 'Из статьи', catalog: 'Из вкладки' };

const ROLE = {
    super_admin: 'Супер-админ',
    admin: 'Администратор',
    sv: 'Супервайзер',
    supervisor: 'Супервайзер',
    trainer: 'Тренер',
    operator: 'Оператор',
    trainee: 'Стажёр',
};

const dateTime = (iso) => {
    if (!iso) return '—';
    const value = new Date(iso);
    if (Number.isNaN(value.getTime())) return '—';
    return value.toLocaleString('ru-RU', {
        day: '2-digit', month: '2-digit', year: '2-digit',
        hour: '2-digit', minute: '2-digit',
    });
};

/** Время попытки. Минуты и секунды, а не «204 000 мс»: цифру читают глазами,
 *  а не сравнивают программой — для сравнения есть выгрузка. */
const duration = (ms) => {
    if (ms === null || ms === undefined) return '—';
    const total = Math.round(ms / 1000);
    return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
};

const Metric = ({ label, value, hint = null }) => (
    <div className="rounded-xl bg-slate-50 px-3 py-2.5">
        <div className="text-[11px] font-medium uppercase tracking-wide text-slate-400">{label}</div>
        <div className="mt-0.5 text-[19px] font-semibold leading-none text-slate-900">{value}</div>
        {hint && <div className="mt-1 text-[11.5px] text-slate-400">{hint}</div>}
    </div>
);

const Th = ({ children, right = false }) => (
    <th className={`whitespace-nowrap px-3 py-2 text-[11.5px] font-medium uppercase
                    tracking-wide text-slate-400 ${right ? 'text-right' : 'text-left'}`}>
        {children}
    </th>
);

const Td = ({ children, right = false, muted = false }) => (
    <td className={`px-3 py-2 text-[12.5px] ${right ? 'text-right tabular-nums' : ''}
                    ${muted ? 'text-slate-400' : 'text-slate-700'}`}>
        {children}
    </td>
);

/** Таблица с подписью и «пусто», одна на все три разреза. */
const Table = ({ title, icon: Icon, count, empty, head, children }) => (
    <section className="space-y-1.5">
        <div className={iosGroupLabel}>
            {Icon && <Icon size={12} className="mr-1 inline align-[-1px]" />}
            {title}{count !== undefined ? ` · ${count}` : ''}
        </div>
        <div className={`${iosCard} overflow-hidden`}>
            {count === 0 ? (
                <p className="px-4 py-3 text-[12.5px] text-slate-400">{empty}</p>
            ) : (
                <div className="overflow-x-auto">
                    <table className="w-full border-collapse">
                        <thead className="border-b border-slate-100">{head}</thead>
                        <tbody className="divide-y divide-slate-50">{children}</tbody>
                    </table>
                </div>
            )}
        </div>
    </section>
);

export default function WikiTrainerStats({
    base, headers, trainer, onBack, onOpenArticle = null, showToast = null,
}) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [since, setSince] = useState('');
    const [until, setUntil] = useState('');
    const [downloading, setDownloading] = useState(false);

    const params = useMemo(
        () => ({ since: since || undefined, until: until || undefined, limit: 200 }),
        [since, until],
    );

    const load = useCallback(() => {
        setLoading(true);
        return axios.get(`${base}/trainers/${encodeURIComponent(trainer.key)}/stats`,
            { headers, params })
            .then((r) => { setData(r.data); setError(''); })
            .catch((e) => setError(errText(e, 'Не удалось загрузить статистику')))
            .finally(() => setLoading(false));
    }, [base, headers, trainer.key, params]);

    useEffect(() => { load(); }, [load]);

    /* Файл забираем через axios, а не ссылкой: раздел авторизуется заголовком,
       а обычная ссылка заголовков не несёт — вместо книги пришла бы страница
       входа. Отдаём его браузеру временной ссылкой на blob. */
    const download = useCallback(() => {
        setDownloading(true);
        axios.get(`${base}/trainers/${encodeURIComponent(trainer.key)}/export`, {
            headers,
            responseType: 'blob',
            params: { ...params, title: trainer.title, app: trainer.app, limit: undefined },
        })
            .then((r) => {
                const url = URL.createObjectURL(new Blob([r.data]));
                const link = document.createElement('a');
                link.href = url;
                link.download = `Тренажёр — ${trainer.title}.xlsx`;
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(url);
            })
            .catch((e) => showToast?.(errText(e, 'Не удалось собрать выгрузку'), 'error'))
            .finally(() => setDownloading(false));
    }, [base, headers, trainer, params, showToast]);

    const totals = data?.totals || {};
    const runs = data?.runs?.items || [];

    return (
        <div className="space-y-3">
            <header className="flex flex-wrap items-center gap-2">
                <button type="button" className={iosBtnSecondary} onClick={onBack}>
                    <ArrowLeft size={15} /> Все тренажёры
                </button>
                <div className="min-w-0 flex-1">
                    <h2 className="truncate text-[16px] font-semibold text-slate-900">
                        {trainer.title}
                    </h2>
                    <p className="text-[12px] text-slate-400">{trainer.subtitle}</p>
                </div>
                <button type="button" className={iosBtnSecondary} onClick={download}
                    disabled={downloading}>
                    {downloading ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
                    Выгрузить в Excel
                </button>
            </header>

            <section className={`${iosCard} flex flex-wrap items-end gap-3 px-4 py-3`}>
                <label className="flex flex-col gap-1">
                    <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">С даты</span>
                    <input type="date" value={since} onChange={(e) => setSince(e.target.value)}
                        className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-[13px]" />
                </label>
                <label className="flex flex-col gap-1">
                    <span className="text-[11px] font-medium uppercase tracking-wide text-slate-400">По дату</span>
                    <input type="date" value={until} onChange={(e) => setUntil(e.target.value)}
                        className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-[13px]" />
                </label>
                {(since || until) && (
                    <button type="button" className="pb-2 text-[12.5px] font-medium text-indigo-600"
                        onClick={() => { setSince(''); setUntil(''); }}>
                        За всё время
                    </button>
                )}
                {loading && (
                    <span className="inline-flex items-center gap-1.5 pb-2 text-[12.5px] text-slate-400">
                        <Loader2 size={13} className="animate-spin" /> считаем…
                    </span>
                )}
            </section>

            {error && (
                <div className={`${iosCard} flex items-center gap-2 px-4 py-3 text-[13px] text-amber-700`}>
                    {error}
                    <button type="button" className="font-semibold text-amber-800 underline" onClick={load}>
                        Повторить
                    </button>
                </div>
            )}

            <section className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
                <Metric label="Запусков" value={totals.runs ?? 0} />
                <Metric label="Прошли" value={totals.finished ?? 0}
                    hint={totals.runs ? `${Math.round(((totals.finished || 0) / totals.runs) * 100)}% попыток` : null} />
                <Metric label="Человек" value={totals.people ?? 0}
                    hint={`прошло ${totals.people_done ?? 0}`} />
                <Metric label="Время" value={duration(totals.median_ms)} hint="медиана" />
                <Metric label="Промахов" value={totals.avg_errors ?? '—'} hint="в среднем" />
                <Metric label="Подсказок" value={totals.avg_hints ?? '—'} hint="в среднем" />
            </section>

            <Table title="Откуда запускали" icon={FileText} count={(data?.articles || []).length}
                empty="Тренажёр ещё ни разу не открывали"
                head={(
                    <tr>
                        <Th>Статья</Th>
                        <Th right>Запусков</Th>
                        <Th right>Прошли</Th>
                        <Th right>Человек</Th>
                        <Th right>Последний раз</Th>
                    </tr>
                )}>
                {(data?.articles || []).map((row) => (
                    <tr key={row.article_id ?? 'catalog'}>
                        <Td>
                            {row.slug && onOpenArticle ? (
                                <button type="button" className="text-left hover:text-indigo-600"
                                    onClick={() => onOpenArticle(row.slug)}>
                                    {row.title}
                                </button>
                            ) : row.title}
                        </Td>
                        <Td right>{row.runs}</Td>
                        <Td right>{row.finished}</Td>
                        <Td right>{row.people}</Td>
                        <Td right muted>{dateTime(row.last_at)}</Td>
                    </tr>
                ))}
            </Table>

            <Table title="Кто проходил" icon={Users} count={(data?.people || []).length}
                empty="Пока никто"
                head={(
                    <tr>
                        <Th>ФИО</Th>
                        <Th>Отдел и группа</Th>
                        <Th right>Попыток</Th>
                        <Th right>Прошёл</Th>
                        <Th right>Промахов</Th>
                        <Th right>Лучшее время</Th>
                        <Th right>Последний раз</Th>
                    </tr>
                )}>
                {(data?.people || []).map((row) => (
                    <tr key={row.user_id ?? row.name}>
                        <Td>
                            {row.name}
                            {row.role && (
                                <span className="ml-1.5 text-[11px] text-slate-400">
                                    {ROLE[row.role] || row.role}
                                </span>
                            )}
                        </Td>
                        <Td muted>
                            {[row.department, row.group].filter(Boolean).join(' · ') || '—'}
                        </Td>
                        <Td right>{row.runs}</Td>
                        <Td right>
                            {row.finished > 0
                                ? <IosBadge tone="green">{row.finished}</IosBadge>
                                : <span className="text-slate-300">—</span>}
                        </Td>
                        <Td right>{row.errors}</Td>
                        <Td right>{duration(row.best_ms)}</Td>
                        <Td right muted>{dateTime(row.last_at)}</Td>
                    </tr>
                ))}
            </Table>

            <Table title="Попытки" count={runs.length}
                empty="Попыток пока нет"
                head={(
                    <tr>
                        <Th>Когда</Th>
                        <Th>Кто</Th>
                        <Th>Результат</Th>
                        <Th right>Дошёл до</Th>
                        <Th right>Промахов</Th>
                        <Th right>Подсказок</Th>
                        <Th right>Время</Th>
                        <Th>Откуда</Th>
                    </tr>
                )}>
                {runs.map((row) => {
                    const status = STATUS[row.status] || { label: row.status, tone: 'slate' };
                    return (
                        <tr key={row.id}>
                            <Td muted>{dateTime(row.started_at)}</Td>
                            <Td>{row.name}</Td>
                            <Td><IosBadge tone={status.tone}>{status.label}</IosBadge></Td>
                            <Td right>
                                {row.stages_total
                                    ? `${row.stages_done} из ${row.stages_total}`
                                    : row.stages_done}
                            </Td>
                            <Td right>{row.errors}</Td>
                            <Td right>{row.hints}</Td>
                            <Td right>{duration(row.duration_ms)}</Td>
                            <Td muted>
                                {row.article_title || SOURCE[row.source] || row.source}
                            </Td>
                        </tr>
                    );
                })}
            </Table>

            {(data?.runs?.total || 0) > runs.length && (
                <p className="px-1 text-[11.5px] text-slate-400">
                    Показаны последние {runs.length} из {data.runs.total} попыток.
                    Все — в выгрузке.
                </p>
            )}
        </div>
    );
}
