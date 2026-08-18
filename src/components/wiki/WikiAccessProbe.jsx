import React, { useState } from 'react';
import axios from 'axios';
import { Loader2, UserSearch } from 'lucide-react';
import {
    iosCard, iosInput, iosBtnPrimary, iosBtnSecondary, IosBadge, IosModal,
} from '../ui/ios';

/* «Почему этот человек видит этот раздел».
 *
 * Жил на вкладке «Доступы», которой больше нет: правила теперь настраиваются
 * из строки раздела. Проверка же не про раздел, а про человека — ей место в
 * шапке вкладки «Структура», отдельной кнопкой.
 *
 * В оригинальной вике такого экрана нет вовсе. При четырёх уровнях правил
 * (раздел → потомки → статья → запрет) без него на вопрос «почему Иванов это
 * видит» отвечают чтением базы.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

export default function WikiAccessProbe({ base, headers, open, onClose }) {
    const [value, setValue] = useState('');
    const [probe, setProbe] = useState(null);

    const run = () => {
        const userId = value.trim();
        if (!userId) return;
        setProbe({ loading: true });
        axios.get(`${base}/access/effective`, { headers, params: { user_id: userId } })
            .then((r) => setProbe({ data: r.data }))
            .catch((e) => setProbe({ error: errText(e, 'Не удалось проверить') }));
    };

    const close = () => { setProbe(null); onClose?.(); };

    return (
        <IosModal
            open={open}
            onClose={close}
            title="Проверить доступ"
            subtitle="Что человек видит в вики и почему"
            footer={(
                <>
                    <button type="button" className={iosBtnSecondary} onClick={close}>
                        Закрыть
                    </button>
                    <button type="button" className={iosBtnPrimary}
                            disabled={!value.trim() || probe?.loading} onClick={run}>
                        {probe?.loading && <Loader2 size={14} className="animate-spin" />} Проверить
                    </button>
                </>
            )}
        >
            <div className="space-y-4">
                <div>
                    <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">
                        ID сотрудника
                    </label>
                    <div className="flex items-center gap-2">
                        <input
                            className={iosInput}
                            inputMode="numeric"
                            autoFocus
                            value={value}
                            onChange={(e) => setValue(e.target.value)}
                            onKeyDown={(e) => { if (e.key === 'Enter') run(); }}
                            placeholder="Например: 42"
                        />
                        <UserSearch size={18} className="shrink-0 text-slate-300" />
                    </div>
                </div>

                {probe?.error && (
                    <div className="rounded-xl bg-rose-50 px-3 py-2.5 text-[13px] text-rose-700">
                        {probe.error}
                    </div>
                )}

                {probe?.data && (
                    <div className="space-y-2">
                        <div className="flex flex-wrap gap-1.5">
                            <IosBadge tone="slate">роль: {probe.data.otp_role}</IosBadge>
                            <IosBadge tone={probe.data.access_mode === 'manual' ? 'amber' : 'slate'}>
                                {probe.data.access_mode === 'manual' ? 'ручная выдача' : 'авто'}
                            </IosBadge>
                            <IosBadge tone="blue">разделов: {probe.data.sections?.length || 0}</IosBadge>
                        </div>
                        <div className={`${iosCard} max-h-72 overflow-y-auto`}>
                            {(probe.data.sections || []).length === 0 && (
                                <div className="px-3 py-8 text-center text-[12.5px] text-slate-400">
                                    Сотрудник не видит ни одного раздела
                                </div>
                            )}
                            {(probe.data.sections || []).map((s) => (
                                <div
                                    key={s.id}
                                    className="flex items-center justify-between gap-3 border-b border-slate-100 px-3.5 py-2.5 last:border-0"
                                >
                                    <span className="truncate text-[13px] text-slate-800">{s.name}</span>
                                    <span className="shrink-0 text-[11.5px] text-slate-400">{s.why}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </IosModal>
    );
}
