import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Loader2 } from 'lucide-react';
import { IosSection, iosBtnPrimary } from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { fmtDateTime } from '../parcels/parcelMeta';
import { KNOWN_TARIFFS, parseCount, tariffLabel } from './waterMeta';

/*
 * «Настройки» — условия программы и пороги остатка (ТЗ 3.2, 6, 7).
 *
 * «Предусмотреть возможность дальнейшего изменения количества заказов и
 * перечня доступных тарифов без переработки основной логики системы» — всё,
 * что постановщик назвал числом, здесь поле, а не константа в коде.
 *
 * Видит только региональный руководитель (глава фронт-офисов) и глобальный
 * админ; сервер закрыт тем же правилом.
 */

const NUMBER_FIELDS = [
    ['min_trips', 'Поездок для выдачи за активность', 'С прошлой выдачи; первый раз — за последние 7 дней'],
    ['cooldown_days', 'Дней между выдачами', 'Не раньше, чем через столько дней после прошлой'],
    ['welcome_blocks', 'Блоков в приветственной выдаче', ''],
    ['activity_blocks', 'Блоков за активность', ''],
];

const THRESHOLD_FIELDS = [
    ['low_threshold', 'Низкий остаток', 'блоков и меньше'],
    ['buy_threshold', 'Требуется закупка', 'блоков и меньше'],
];

const NUMBER_INPUT = 'w-20 shrink-0 rounded-xl border-0 bg-slate-100 px-3 py-2 text-center text-[14px] '
    + 'tabular-nums text-slate-900 transition focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-500/70';

const Field = ({ label, hint, value, onChange }) => (
    <label className="flex items-center justify-between gap-3">
        <span className="min-w-0">
            <span className="block text-[13.5px] text-slate-800">{label}</span>
            {hint && <span className="block text-[11.5px] text-slate-500">{hint}</span>}
        </span>
        {/* Не iosInput: в нём w-full, и узкое поле растягивалось на всю
            строку, сжимая подпись в столбик по слову. */}
        <input className={NUMBER_INPUT} inputMode="numeric"
               value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
);

const WaterSettings = ({ apiBaseUrl, headers, onSaved, showToast }) => {
    const [loaded, setLoaded] = useState(null);
    const [draft, setDraft] = useState(null);
    const [candidates, setCandidates] = useState([]);
    const [headIds, setHeadIds] = useState([]);
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);

    useEffect(() => {
        axios.get(`${apiBaseUrl}/api/water/settings`, { headers: headers() })
            .then((response) => {
                const settings = response.data?.settings || {};
                setLoaded(settings);
                setDraft(settings);
                setCandidates(response.data?.candidates || []);
                setHeadIds(response.data?.head_ids || []);
            })
            .catch((requestError) => setError(requestError?.response?.data?.error || 'Не удалось загрузить настройки'));
    }, [apiBaseUrl, headers]);

    const tariffs = useMemo(() => {
        const extra = (draft?.tariffs || []).filter((code) => !KNOWN_TARIFFS.includes(code));
        return [...KNOWN_TARIFFS, ...extra];
    }, [draft?.tariffs]);

    if (error) return <div className="text-[13px] text-rose-600">{error}</div>;
    if (!draft) {
        return (
            <div className="flex items-center justify-center gap-2 py-10 text-[13px] text-slate-500">
                <Loader2 size={15} className="animate-spin" /> Загружаем настройки…
            </div>
        );
    }

    const set = (name, value) => setDraft((prev) => ({ ...prev, [name]: value }));
    const numbers = [...NUMBER_FIELDS, ...THRESHOLD_FIELDS].map(([name]) => name);
    const parsed = Object.fromEntries(numbers.map((name) => [name, parseCount(draft[name])]));
    const invalid = numbers.some((name) => !Number.isInteger(parsed[name]))
        || !(draft.tariffs || []).length
        || parsed.buy_threshold > parsed.low_threshold;
    const dirty = JSON.stringify(draft) !== JSON.stringify(loaded);

    const toggleTariff = (code) => set('tariffs', (draft.tariffs || []).includes(code)
        ? draft.tariffs.filter((item) => item !== code)
        : [...(draft.tariffs || []), code]);

    const save = async () => {
        if (invalid || saving) return;
        setSaving(true);
        try {
            const response = await axios.put(`${apiBaseUrl}/api/water/settings`, {
                ...parsed, tariffs: draft.tariffs, notify_user_ids: draft.notify_user_ids || [],
            }, { headers: headers() });
            const settings = response.data?.settings || draft;
            setLoaded(settings);
            setDraft(settings);
            onSaved?.(settings);
            showToast?.('Настройки сохранены', 'success');
        } catch (requestError) {
            showToast?.(requestError?.response?.data?.error || 'Не удалось сохранить', 'error');
        } finally {
            setSaving(false);
        }
    };

    const recipients = draft.notify_user_ids || [];
    const heads = candidates.filter((person) => headIds.includes(person.id));
    const recipientHint = recipients.length
        ? 'Кому не привязан Telegram, сообщение не придёт'
        : heads.length
            ? `Пока никто не выбран — пишем главе фронт-офисов (${heads.map((person) => person.name).join(', ')})`
            : 'Пока никто не выбран — уведомление никому не уйдёт';

    return (
        <div className="max-w-2xl space-y-5">
            <IosSection title="Право на воду">
                {NUMBER_FIELDS.map(([name, label, hint]) => (
                    <Field key={name} label={label} hint={hint} value={draft[name] ?? ''} onChange={(value) => set(name, value)} />
                ))}
            </IosSection>

            <IosSection title="Тарифы программы" hint="Вода положена водителю, у машины которого есть хотя бы один из отмеченных тарифов">
                <div className="flex flex-wrap gap-1.5">
                    {tariffs.map((code) => {
                        const on = (draft.tariffs || []).includes(code);
                        return (
                            <button key={code} type="button" onClick={() => toggleTariff(code)} aria-pressed={on}
                                    className={`rounded-full px-3 py-1.5 text-[12.5px] font-medium transition active:scale-[0.98] ${
                                        on ? 'bg-slate-900 text-white' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'}`}>
                                {tariffLabel(code)}
                            </button>
                        );
                    })}
                </div>
            </IosSection>

            <IosSection title="Пороги остатка" hint="Общие для всех офисов. Свой порог офиса задаётся в его карточке во вкладке «Остатки»">
                {THRESHOLD_FIELDS.map(([name, label, hint]) => (
                    <Field key={name} label={label} hint={hint} value={draft[name] ?? ''} onChange={(value) => set(name, value)} />
                ))}
                {parsed.buy_threshold > parsed.low_threshold && (
                    <p className="text-[12px] text-rose-600">Порог закупки не может быть больше порога «низкий остаток»</p>
                )}
            </IosSection>

            <IosSection title="«Требуется закупка» в Telegram" hint={recipientHint}>
                <CustomSelect
                    multiple
                    searchable
                    variant="ios"
                    /* CustomSelect в режиме multiple держит ключи строками —
                       сравниваем строками, храним числами (как SessionModal). */
                    value={recipients.map(String)}
                    onChange={(value) => set('notify_user_ids', (value || []).map(Number))}
                    options={candidates.map((person) => ({
                        value: person.id,
                        label: person.name,
                        meta: person.has_telegram ? (person.city || '') : 'нет Telegram',
                        muted: !person.has_telegram,
                    }))}
                    placeholder="Выберите сотрудников"
                    renderValue={(values) => (values.length
                        ? candidates.filter((person) => values.map(String).includes(String(person.id)))
                            .map((person) => person.name).join(', ')
                        : 'Выберите сотрудников')}
                    ariaLabel="Получатели"
                />
            </IosSection>

            <div className="flex flex-wrap items-center gap-3">
                <button type="button" className={iosBtnPrimary} disabled={!dirty || invalid || saving} onClick={save}>
                    {saving && <Loader2 size={15} className="animate-spin" />}
                    Сохранить
                </button>
                {loaded?.updated_by_name && (
                    <span className="text-[12px] text-slate-400">
                        Изменил {loaded.updated_by_name}, {fmtDateTime(loaded.updated_at)}
                    </span>
                )}
            </div>
        </div>
    );
};

export default WaterSettings;
