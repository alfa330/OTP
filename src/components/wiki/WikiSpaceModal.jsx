/* Конструктор пространства: имя, кому выдано, из чего состоит.
 *
 * Одно место на всё пространство. Раньше его настройки были размазаны: имя
 * правилось во вкладке «Структура», кому видно — поштучно у публичных разделов,
 * а «из чего состоит раздел» не настраивалось вовсе. Собрано в одно окно,
 * потому что это один вопрос — «что за вика и для кого».
 */
import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Archive, Loader2, Plus } from 'lucide-react';

import { IosModal, IosToggle, iosBtnPrimary, iosBtnSecondary, iosInput, iosGroupLabel }
    from '../ui/ios';
import { SPACE_TABS, spaceFeatures } from './spaceFeatures';

/* Пустое пространство: все тумблеры включены. Новое пространство, у которого
   половина выключена «по умолчанию», выглядит как сломанное — человек ищет
   пропавшие вкладки, вместо того чтобы выключить лишние сам. */
const blankSpace = () => ({
    name: '',
    description: '',
    department_ids: [],
    features: spaceFeatures({}),
});

export default function WikiSpaceModal({
    open, space, base, headers, departments, showToast, onClose, onSaved,
}) {
    const creating = !space?.id;
    const [draft, setDraft] = useState(blankSpace);
    const [saving, setSaving] = useState(false);

    /* Черновик пересобирается при КАЖДОМ открытии, а не при первом монтировании:
       окно живёт в шапке и открывается для разных пространств подряд. Без сброса
       второе открытие показало бы поля первого. */
    useEffect(() => {
        if (!open) return;
        setDraft(space?.id
            ? {
                name: space.name || '',
                description: space.description || '',
                department_ids: [...(space.department_ids || [])],
                features: spaceFeatures(space.features),
            }
            : blankSpace());
    }, [open, space]);

    const patch = (fields) => setDraft((prev) => ({ ...prev, ...fields }));

    const setFeature = (key, value) => setDraft((prev) => ({
        ...prev,
        features: { ...prev.features, [key]: value },
    }));

    const toggleDepartment = (id, on) => patch({
        department_ids: on
            ? [...draft.department_ids, id]
            : draft.department_ids.filter((x) => x !== id),
    });

    const everyone = draft.department_ids.length === 0;

    const save = () => {
        const name = draft.name.trim();
        if (!name) { showToast?.('Укажите название пространства', 'error'); return; }
        setSaving(true);
        const payload = {
            name,
            description: draft.description.trim() || null,
            department_ids: draft.department_ids,
            features: draft.features,
        };
        const request = creating
            ? axios.post(`${base}/spaces`, payload, { headers })
            : axios.patch(`${base}/spaces/${space.id}`, payload, { headers });
        request
            .then(({ data }) => {
                showToast?.(creating ? 'Пространство создано' : 'Пространство обновлено',
                            'success');
                onSaved?.(creating ? data?.id : space.id);
                onClose?.();
            })
            .catch((error) => showToast?.(
                error?.response?.data?.error || 'Не удалось сохранить пространство', 'error'))
            .finally(() => setSaving(false));
    };

    const archive = () => {
        setSaving(true);
        axios.delete(`${base}/spaces/${space.id}`, { headers })
            .then(() => {
                showToast?.('Пространство убрано в архив', 'success');
                onSaved?.(null);
                onClose?.();
            })
            .catch((error) => showToast?.(
                error?.response?.data?.error || 'Не удалось убрать в архив', 'error'))
            .finally(() => setSaving(false));
    };

    /* Сколько вкладок останется. Число рядом с заголовком отвечает на вопрос,
       который иначе пришлось бы проверять закрытием окна. */
    const tabsLeft = useMemo(
        () => SPACE_TABS.filter((tab) => tab.locked || draft.features[tab.key]).length,
        [draft.features],
    );

    return (
        <IosModal
            open={open}
            onClose={onClose}
            maxWidth="max-w-xl"
            title={creating ? 'Новое пространство' : `Пространство «${space?.name || ''}»`}
            subtitle="Кому выдано и из чего состоит"
            footer={(
                <>
                    {!creating && (
                        <button
                            type="button"
                            onClick={archive}
                            disabled={saving}
                            className={`${iosBtnSecondary} mr-auto text-rose-600`}
                        >
                            <Archive size={15} /> В архив
                        </button>
                    )}
                    <button type="button" onClick={onClose} className={iosBtnSecondary}>
                        Отмена
                    </button>
                    <button type="button" onClick={save} disabled={saving} className={iosBtnPrimary}>
                        {saving ? <Loader2 size={15} className="animate-spin" /> : <Plus size={15} />}
                        {creating ? 'Создать' : 'Сохранить'}
                    </button>
                </>
            )}
        >
            <div className="space-y-4">
                {/* ── Название ── */}
                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>Название</div>
                    <input
                        className={iosInput}
                        value={draft.name}
                        autoFocus={creating}
                        placeholder="Например: Тез КЦ"
                        onChange={(e) => patch({ name: e.target.value })}
                    />
                    <input
                        className={iosInput}
                        value={draft.description}
                        placeholder="Описание — необязательно"
                        onChange={(e) => patch({ description: e.target.value })}
                    />
                </section>

                {/* ── Кому выдано ────────────────────────────────────────────
                    Это ГРАНИЦА, а не подсказка: раздел чужого пространства не
                    откроется отделу ни правилом, ни публичностью, ни ссылкой. */}
                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>Каким отделам видно</div>
                    <div className="flex gap-1 rounded-xl bg-slate-100 p-1">
                        {[
                            { key: 'all', label: 'Всем отделам' },
                            { key: 'some', label: 'Выбранным' },
                        ].map(({ key, label }) => {
                            const active = key === 'some' ? !everyone : everyone;
                            return (
                                <button
                                    key={key}
                                    type="button"
                                    /* «Выбранным» без выбора — это «всем», поэтому при
                                       переключении отмечаем все отделы разом: дальше
                                       человек снимает лишние. Ровно как у публичного
                                       раздела — механика одна, и вести себя обязана
                                       одинаково. */
                                    onClick={() => patch({
                                        department_ids: key === 'all'
                                            ? [] : departments.map((d) => d.id),
                                    })}
                                    className={`flex-1 whitespace-nowrap rounded-lg px-2 py-1.5 text-[12.5px] font-medium transition ${
                                        active ? 'bg-white text-slate-900 shadow-sm'
                                               : 'text-slate-500 hover:text-slate-700'
                                    }`}
                                >
                                    {label}
                                </button>
                            );
                        })}
                    </div>

                    {!everyone && (
                        <div className="divide-y divide-slate-100 overflow-hidden rounded-xl bg-slate-50">
                            {departments.map((d) => (
                                <label
                                    key={d.id}
                                    className="flex cursor-pointer items-center justify-between gap-3 px-3 py-2"
                                >
                                    <span className="min-w-0 flex-1 truncate text-[13px] text-slate-800">
                                        {d.name}
                                    </span>
                                    <IosToggle
                                        checked={draft.department_ids.includes(d.id)}
                                        onChange={(v) => toggleDepartment(d.id, v)}
                                    />
                                </label>
                            ))}
                        </div>
                    )}

                    <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">
                        {everyone
                            ? 'Пространство увидят сотрудники всех отделов.'
                            : `Пространство увидят только отмеченные отделы (${draft.department_ids.length}). Остальным не будет видно ни одного его раздела — даже публичного.`}
                    </p>
                </section>

                {/* ── Из чего состоит ── */}
                <section className="space-y-1.5">
                    <div className="flex items-end justify-between gap-2">
                        <div className={iosGroupLabel}>Разделы пространства</div>
                        <span className="text-[11px] tabular-nums text-slate-400">
                            вкладок: {tabsLeft}
                        </span>
                    </div>
                    <div className="divide-y divide-slate-100 overflow-hidden rounded-xl bg-slate-50">
                        {SPACE_TABS.map((tab) => {
                            const on = tab.locked || draft.features[tab.key];
                            return (
                                <div key={tab.key}>
                                    <label
                                        className={`flex items-center justify-between gap-3 px-3 py-2 ${
                                            tab.locked ? '' : 'cursor-pointer'
                                        }`}
                                    >
                                        <span className="min-w-0 flex-1">
                                            <span className="block truncate text-[13px] font-medium text-slate-800">
                                                {tab.label}
                                            </span>
                                            {tab.locked && (
                                                <span className="block truncate text-[11px] text-slate-400">
                                                    {tab.lockedHint}
                                                </span>
                                            )}
                                        </span>
                                        <IosToggle
                                            checked={on}
                                            disabled={tab.locked}
                                            onChange={(v) => setFeature(tab.key, v)}
                                        />
                                    </label>

                                    {/* Половины появляются ТОЛЬКО у включённой вкладки:
                                        настраивать содержимое того, чего в пространстве
                                        не будет, — предложение без последствий. */}
                                    {on && (tab.children || []).map((child) => (
                                        <label
                                            key={child.key}
                                            className="flex cursor-pointer items-center justify-between gap-3 border-t border-slate-100 bg-white/60 py-2 pl-7 pr-3"
                                        >
                                            <span className="min-w-0 flex-1 truncate text-[12.5px] text-slate-600">
                                                {child.label}
                                            </span>
                                            <IosToggle
                                                checked={draft.features[child.key]}
                                                onChange={(v) => setFeature(child.key, v)}
                                            />
                                        </label>
                                    ))}
                                </div>
                            );
                        })}
                    </div>
                    <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">
                        Выключенная вкладка исчезает и из редактора: без «Тренажёров»
                        у статьи не будет и типа «Тренажёр».
                    </p>
                </section>
            </div>
        </IosModal>
    );
}
