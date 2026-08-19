import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Loader2 } from 'lucide-react';
import {
    iosCard, iosBtnPrimary, iosBtnSecondary, IosBadge, IosModal,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';

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

const ROLE_TITLE = {
    operator: 'оператор', trainee: 'стажёр', trainer: 'тренер', sv: 'супервайзер',
    supervisor: 'супервайзер', admin: 'руководитель', super_admin: 'директор',
};

export default function WikiAccessProbe({ base, headers, open, onClose }) {
    const [value, setValue] = useState('');
    const [people, setPeople] = useState([]);
    const [probe, setProbe] = useState(null);

    // Список тянем при открытии, а не при монтировании: кнопка живёт в шапке
    // вкладки, а заглядывают сюда изредка.
    useEffect(() => {
        if (!open) return;
        axios.get(`${base}/access/people`, { headers })
            .then((r) => setPeople(r.data?.items || []))
            .catch(() => setPeople([]));
    }, [open, base, headers]);

    const options = useMemo(() => people.map((person) => ({
        value: String(person.id),
        label: [person.name, ROLE_TITLE[person.role] || person.role,
                person.department_name].filter(Boolean).join(' · '),
    })), [people]);

    const run = (explicit) => {
        const userId = String(explicit ?? value).trim();
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
                            disabled={!value || probe?.loading} onClick={() => run()}>
                        {probe?.loading && <Loader2 size={14} className="animate-spin" />} Проверить
                    </button>
                </>
            )}
        >
            <div className="space-y-4">
                <div>
                    <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">
                        Сотрудник
                    </label>
                    <CustomSelect
                        variant="ios"
                        value={value}
                        // Проверяем сразу по выбору: отдельное нажатие «Проверить»
                        // после выбора человека — лишний шаг, кнопка остаётся
                        // только чтобы перезапросить то же самое.
                        onChange={(v) => { setValue(v); run(v); }}
                        options={options}
                        searchable
                        placeholder="Выберите сотрудника…"
                        searchPlaceholder="Поиск по имени…"
                        ariaLabel="Сотрудник для проверки"
                    />
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
