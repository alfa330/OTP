import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Loader2 } from 'lucide-react';
import { IosModal } from '../ui/ios';
import { CopyButton, GroupLabel, PrizePill } from './baigaUi';
import { formatInt, formatMoney, periodLabel, plural } from './baigaMeta';

/*
 * Карточка водителя — по нажатию на строку списка.
 *
 * Зачем она поддержке: водитель звонит с вопросом «я что-нибудь выиграл?», и
 * ответ — это ВСЕ его недели, а не одна строка таблицы. Карточка собирает их
 * тем же поиском по списку (ID водителя), что и кнопка «Список ID/ВУ», —
 * второго способа искать сервер не заводит.
 *
 * Раскладка — iOS «inset grouped»: сверху реквизиты (номер ВУ и ID — с
 * копированием, их диктуют и вставляют), ниже недели, свежая сверху. На
 * телефоне окно — это экран (IosModal), «назад» закрывает его.
 */

const Row = ({ label, children }) => (
    <div className="flex min-h-[44px] items-center justify-between gap-4 px-4 py-2.5">
        <span className="shrink-0 text-[13.5px] text-slate-500">{label}</span>
        <span className="min-w-0 text-right text-[13.5px] text-slate-900">{children}</span>
    </div>
);

const BaigaDriverSheet = ({ row, onClose, apiBaseUrl, headers, weekByStart }) => {
    const open = Boolean(row);
    const [history, setHistory] = useState(null);
    const [error, setError] = useState('');

    useEffect(() => {
        if (!row) return undefined;
        let cancelled = false;
        setHistory(null);
        setError('');
        // По точному ID водителя, а не разбором списка: нестандартный ID там
        // читался бы как номер ВУ, и карточка показывала бы «0 недель».
        axios.post(`${apiBaseUrl}/api/baiga/rows`,
            { filters: { driver: row.driver_id }, sort: 'week', dir: 'desc', page: 1, size: 500 },
            { headers: headers() })
            .then((response) => { if (!cancelled) setHistory(response.data?.rows || []); })
            .catch(() => { if (!cancelled) setError('Не удалось загрузить недели водителя'); });
        return () => { cancelled = true; };
    }, [row, apiBaseUrl, headers]);

    const prizeTotal = useMemo(
        () => (history || []).reduce((sum, item) => sum + (item.prize_amount || 0), 0), [history],
    );
    const prizeCount = (history || []).filter((item) => item.has_prize).length;
    // Недели — по датам, а не по строкам: водитель бывает в двух листах одной
    // недели (файл это допускает с предупреждением), и это одна неделя, не две.
    const weekCount = useMemo(() => new Set((history || []).map((item) => item.period_start)).size, [history]);
    // Сумма в ₸ — только если у призов есть суммы; приз словами («Смартфон»)
    // в тенге не считается, и «призы 0 ₸» было бы неправдой.
    const prizeLabel = prizeTotal > 0
        ? `призы ${formatMoney(prizeTotal)}`
        : `${formatInt(prizeCount)} ${plural(prizeCount, 'приз', 'приза', 'призов')}`;

    return (
        <IosModal
            open={open}
            onClose={onClose}
            title={row?.driver_name || ''}
            subtitle={[row?.city, row?.park].filter(Boolean).join(' · ')}
            maxWidth="max-w-xl"
        >
            {row && (
                <div className="space-y-5">
                    <div className="overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70">
                        <div className="divide-y divide-slate-100">
                            <Row label="Номер ВУ">
                                {row.license
                                    ? <CopyButton value={row.license} label="Скопировать номер ВУ" className="tabular-nums">{row.license}</CopyButton>
                                    : <span className="text-slate-400">—</span>}
                            </Row>
                            <Row label="ID водителя">
                                <CopyButton value={row.driver_id} label="Скопировать ID водителя"
                                            className="break-all text-left font-mono text-[12.5px]">
                                    {row.driver_id}
                                </CopyButton>
                            </Row>
                        </div>
                    </div>

                    <div>
                        <GroupLabel right={history && prizeCount > 0 && (
                            <span className="text-[12px] tabular-nums text-slate-500">{prizeLabel}</span>
                        )}>
                            {history ? `${formatInt(weekCount)} ${plural(weekCount, 'неделя', 'недели', 'недель')}` : 'Недели'}
                        </GroupLabel>
                        <div className="overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200/70">
                            {!history && !error && (
                                <div className="flex items-center justify-center gap-2 py-8 text-[13px] text-slate-500">
                                    <Loader2 size={15} className="animate-spin" /> Собираем недели…
                                </div>
                            )}
                            {error && <div className="px-4 py-6 text-center text-[13px] text-rose-600">{error}</div>}
                            {history && (
                                <ul className="divide-y divide-slate-100">
                                    {history.map((item) => {
                                        const week = weekByStart.get(item.period_start);
                                        return (
                                            <li key={item.id} className="flex items-center gap-3 px-4 py-3">
                                                <div className="min-w-0 grow basis-0">
                                                    <div className="text-[14px] font-medium text-slate-900">
                                                        {periodLabel(item.period_start, week?.period_end)}
                                                    </div>
                                                    <div className="mt-0.5 text-[12.5px] leading-snug text-slate-500">
                                                        {item.zachet}
                                                        {' · '}{formatMoney(item.amount)}
                                                        {' · '}{formatInt(item.trips)} {plural(item.trips, 'поездка', 'поездки', 'поездок')}
                                                    </div>
                                                </div>
                                                <div className="flex shrink-0 flex-col items-end gap-1">
                                                    <span className="text-[13px] font-semibold tabular-nums text-slate-700">
                                                        {item.position} место
                                                    </span>
                                                    {item.has_prize && <PrizePill row={item} size="sm" />}
                                                </div>
                                            </li>
                                        );
                                    })}
                                </ul>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </IosModal>
    );
};

export default BaigaDriverSheet;
