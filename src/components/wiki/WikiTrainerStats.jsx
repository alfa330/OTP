import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    ArrowLeft, Download, FileText, Loader2, Users,
} from 'lucide-react';

import { iosCard, iosGroupLabel, iosBtnSecondary, IosBadge } from '../ui/ios';
/* Плитка и таблица — общие с «Аналитикой» (reportKit.jsx). Раньше они жили
   здесь и никуда не экспортировались; со вторым отчётом раздела копия
   разошлась бы с оригиналом на первой же правке. */
import { Metric, Table, Td, Th } from './reportKit';
import { IosDateRangePicker, isoDate } from '../ui/DateRangePicker';

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

export default function WikiTrainerStats({
    base, headers, trainer, onBack, onOpenArticle = null, showToast = null,
}) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    /* Период одним значением: «с» и «по» — это не два независимых фильтра, а
       две границы одного отрезка, и календарь их так и показывает. Пустые
       границы = «за всё время»: ниже они превращаются в отсутствие параметров,
       и сервер отдаёт всю историю — и на экран, и в выгрузку. */
    const [range, setRange] = useState({ from: '', to: '' });
    const [downloading, setDownloading] = useState(false);

    /* Зависимости — примитивы, а не сам `range`: календарь отдаёт новый объект
       на каждый выбор, и по ссылке запрос уходил бы даже за тем же периодом. */
    const params = useMemo(
        () => ({ since: range.from || undefined, until: range.to || undefined, limit: 200 }),
        [range.from, range.to],
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
    /* Какие итоги раскрыты. Карточка обращения — это шесть строк, и показывать
       их у всех попыток сразу значит превратить ленту в простыню. */
    const [shown, setShown] = useState(() => new Set());
    const toggleRow = useCallback((id) => setShown((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    }), []);

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

            {/* Чип сам называет выбранный период, поэтому подписи «С даты» и
                «По дату» не нужны, а отдельная кнопка «За всё время» уехала в
                пресет «Весь период» внутри календаря: возврат к полной истории —
                такой же выбор периода, как и любой другой.
                Дальше сегодняшнего дня смотреть нечего: статистика считается по
                уже состоявшимся попыткам, и завтрашняя граница только добавила
                бы пустых ответов. */}
            <section className={`${iosCard} flex flex-wrap items-center gap-3 px-4 py-3`}>
                <IosDateRangePicker
                    from={range.from} to={range.to} max={isoDate(new Date())}
                    onChange={setRange}
                />
                {loading && (
                    <span className="inline-flex items-center gap-1.5 text-[12.5px] text-slate-400">
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
                    const open = shown.has(row.id);
                    return (
                        <React.Fragment key={row.id}>
                            <tr>
                                <Td muted>{dateTime(row.started_at)}</Td>
                                <Td>{row.name}</Td>
                                <Td>
                                    <span className="flex flex-wrap items-center gap-1.5">
                                        <IosBadge tone={status.tone}>{status.label}</IosBadge>
                                        {/* Итог работы, а не пути: у тренажёра CRM видно,
                                            верную ли ветку категорий выбрал человек.
                                            Промахов может быть ноль и при неверной ветке —
                                            если он дошёл до неё по подсказке. */}
                                        {row.result ? (
                                            <IosBadge tone={row.result.correct ? 'green' : 'red'}>
                                                {row.result.correct ? 'верно' : 'неверно'}
                                            </IosBadge>
                                        ) : null}
                                    </span>
                                </Td>
                                <Td right>
                                    {row.stages_total
                                        ? `${row.stages_done} из ${row.stages_total}`
                                        : row.stages_done}
                                </Td>
                                <Td right>{row.errors}</Td>
                                <Td right>{row.hints}</Td>
                                <Td right>{duration(row.duration_ms)}</Td>
                                <Td muted>
                                    <span className="flex items-center gap-2">
                                        {row.article_title || SOURCE[row.source] || row.source}
                                        {row.result ? (
                                            <button
                                                type="button"
                                                className="shrink-0 rounded-lg px-1.5 py-0.5 text-[11px]
                                                           text-indigo-600 hover:bg-indigo-50"
                                                onClick={() => toggleRow(row.id)}
                                            >
                                                {open ? 'скрыть' : 'что завёл'}
                                            </button>
                                        ) : null}
                                    </span>
                                </Td>
                            </tr>
                            {open && row.result ? (
                                <tr>
                                    <td colSpan={8} className="px-3 pb-3">
                                        <div className="rounded-xl bg-slate-50 p-3">
                                            <div className="mb-1.5 text-[11px] font-semibold uppercase
                                                            tracking-wide text-slate-400">
                                                {row.result.title || 'Итог попытки'}
                                            </div>
                                            <dl className="grid gap-1">
                                                {(row.result.fields || []).map(([label, value]) => (
                                                    <div key={label} className="flex gap-2 text-[12.5px]">
                                                        <dt className="w-40 shrink-0 text-slate-400">{label}</dt>
                                                        <dd className="m-0 min-w-0 text-slate-700">{value}</dd>
                                                    </div>
                                                ))}
                                            </dl>
                                        </div>
                                    </td>
                                </tr>
                            ) : null}
                        </React.Fragment>
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
