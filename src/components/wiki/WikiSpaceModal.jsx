/* Конструктор пространства: имя, значок, кому выдано, из чего состоит.
 *
 * Одно место на всё пространство. Раньше его настройки были размазаны: имя
 * правилось во вкладке «Структура», кому видно — поштучно у публичных разделов,
 * а «из чего состоит раздел» не настраивалось вовсе. Собрано в одно окно,
 * потому что это один вопрос — «что за вика и для кого».
 *
 * ФОРМА — «grouped list» iOS/macOS, а не столбик залитых полей. Разница не
 * косметическая: в сгруппированном списке подпись стоит СЛЕВА от значения, и
 * строка читается предложением («Название — Тез КЦ»), а карточка с разделителями
 * сама показывает, где кончается один вопрос и начинается другой. Прежний вид —
 * два серых прямоугольника подряд без подписей — заставлял догадываться, что
 * второй из них «описание», по одному лишь placeholder'у, который исчезает при
 * первом же набранном символе.
 */
import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { Archive, Loader2, Plus, X } from 'lucide-react';

import {
    IosModal, IosSegmented, IosToggle, iosBtnPrimary, iosBtnSecondary, iosCard, iosGroupLabel,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { SPACE_TABS, spaceFeatures } from './spaceFeatures';
import { spaceIcon, spaceMonogram } from './spaceIdentity';

/* Значки на выбор. Готовый ряд, а не поле для любого emoji: значок нужен, чтобы
   различать строки в переключателе с одного взгляда, и набор из десятка
   непохожих фигур решает эту задачу лучше, чем свобода поставить туда что
   угодно — включая пустой квадрат из шрифта, которого нет на Windows. */
/* Одиннадцать штук, а не «сколько влезет»: вместе с плиткой-монограммой их
   ровно двенадцать, и сетка из шести колонок раскладывает их в два полных ряда.
   Тринадцатая фигура переносилась бы одна на вторую строку — ряд из одного
   элемента читается как «здесь что-то оборвалось». */
const SPACE_ICONS = ['🚕', '🏢', '💼', '📦', '🛠', '💬', '📊', '🎓', '⚙️', '🌍', '⭐️'];

/* Пустое пространство: все тумблеры включены. Новое пространство, у которого
   половина выключена «по умолчанию», выглядит как сломанное — человек ищет
   пропавшие вкладки, вместо того чтобы выключить лишние сам. */
const blankSpace = () => ({
    name: '',
    description: '',
    icon: '',
    department_ids: [],
    features: spaceFeatures({}),
});

/* Карточка сгруппированного списка. Разделители рисует сама карточка
   (divide-y), а не каждая строка: строка, которая носит собственную границу,
   рано или поздно окажется последней и подчеркнёт дно карточки. */
const Group = ({ children, className = '' }) => (
    <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden ${className}`}>
        {children}
    </div>
);

/* Строка «подпись слева — значение справа». Подпись фиксированной ширины,
   чтобы значения выстроились в колонку: разъехавшиеся поля читаются как
   отдельные формы, а не как один список. */
const Field = ({ label, children }) => (
    <label className="flex items-center gap-3 px-3.5 py-2.5">
        <span className="w-[92px] shrink-0 text-[13px] text-slate-500">{label}</span>
        <span className="min-w-0 flex-1">{children}</span>
    </label>
);

const fieldInput =
    'w-full bg-transparent text-[14px] text-slate-900 placeholder-slate-300 outline-none';

const Hint = ({ children }) => (
    <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">{children}</p>
);

export default function WikiSpaceModal({
    open, space, base, headers, departments, showToast, onClose, onSaved,
}) {
    const creating = !space?.id;
    const [draft, setDraft] = useState(blankSpace);
    const [saving, setSaving] = useState(false);
    /* Архивация подтверждается ВТОРЫМ нажатием той же кнопки, а не окном
       поверх окна. Кнопка стоит в подвале рядом с «Сохранить», промахнуться по
       ней легко, а за ней уезжает вика целиком со всеми разделами. Отдельный
       confirm() здесь был бы системным окном посреди формы в стиле macOS. */
    const [confirmArchive, setConfirmArchive] = useState(false);
    /* Режим держим ОТДЕЛЬНО от списка, хотя на сервере пусто и значит «всем».
       Иначе «Выбранным» с ещё пустым списком неотличимо от «Всем отделам»:
       переключатель отскакивал бы назад, а сохранение молча открыло бы
       пространство всей компании — ровно противоположное тому, что человек
       только что выбрал. */
    const [restricted, setRestricted] = useState(false);

    /* Черновик пересобирается при КАЖДОМ открытии, а не при первом монтировании:
       окно живёт в шапке и открывается для разных пространств подряд. Без сброса
       второе открытие показало бы поля первого. */
    useEffect(() => {
        if (!open) return;
        setDraft(space?.id
            ? {
                name: space.name || '',
                description: space.description || '',
                icon: spaceIcon(space),
                department_ids: [...(space.department_ids || [])],
                features: spaceFeatures(space.features),
            }
            : blankSpace());
        setRestricted((space?.department_ids || []).length > 0);
        setConfirmArchive(false);
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

    const everyone = !restricted;

    const departmentOptions = useMemo(
        () => departments.map((d) => ({ value: d.id, label: d.name })), [departments]);

    const nameOfDepartment = (id) => departments.find((d) => d.id === id)?.name || `№${id}`;

    /* В кнопке селектора — счёт, а не перечисление: названия отделов длинные
       («СЗоВ — Служба заботы о водителях»), и уже на втором кнопка перестаёт
       читаться. Сами отделы показаны чипами под ней. */
    const renderDepartments = (values) => (
        <span className="truncate">
            {values.length === 1 ? nameOfDepartment(values[0]) : `Выбрано: ${values.length}`}
        </span>
    );

    const save = () => {
        const name = draft.name.trim();
        if (!name) { showToast?.('Укажите название пространства', 'error'); return; }
        // Пустой список на сервере означает «видно всем», поэтому режим
        // «Выбранным» без единого отдела сохранять нельзя: получилось бы
        // обратное задуманному, и молча.
        if (restricted && draft.department_ids.length === 0) {
            showToast?.('Выберите хотя бы один отдел — или верните «Всем отделам»', 'error');
            return;
        }
        setSaving(true);
        const payload = {
            name,
            description: draft.description.trim() || null,
            icon: draft.icon || null,
            department_ids: restricted ? draft.department_ids : [],
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
        if (!confirmArchive) { setConfirmArchive(true); return; }
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
                            onBlur={() => setConfirmArchive(false)}
                            disabled={saving}
                            className={`${iosBtnSecondary} mr-auto ${
                                confirmArchive
                                    ? 'bg-rose-600 text-white hover:bg-rose-700'
                                    : 'text-rose-600'
                            }`}
                        >
                            <Archive size={15} />
                            {confirmArchive ? 'Точно в архив?' : 'В архив'}
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
            <div className="space-y-5">
                {/* ── Название и значок ───────────────────────────────────────
                    Значок — не украшение: в переключателе он и есть то, по чему
                    строку узнают, не читая. Без него плитка показывает
                    монограмму имени, и она же стоит здесь в предпросмотре —
                    чтобы было видно, ЧТО именно окажется в списке. */}
                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>Пространство</div>
                    <Group>
                        <Field label="Название">
                            <input
                                className={fieldInput}
                                value={draft.name}
                                autoFocus={creating}
                                placeholder="Например: Тез КЦ"
                                onChange={(e) => patch({ name: e.target.value })}
                            />
                        </Field>
                        <Field label="Описание">
                            <input
                                className={fieldInput}
                                value={draft.description}
                                placeholder="Необязательно"
                                onChange={(e) => patch({ description: e.target.value })}
                            />
                        </Field>
                        <div className="flex items-start gap-3 px-3.5 py-2.5">
                            <span className="w-[92px] shrink-0 pt-1 text-[13px] text-slate-500">
                                Значок
                            </span>
                            <div className="grid w-full max-w-[288px] grid-cols-6 justify-items-center gap-1.5">
                                <button
                                    type="button"
                                    onClick={() => patch({ icon: '' })}
                                    aria-pressed={!draft.icon}
                                    title="Без значка — буквы названия"
                                    className={`grid h-9 w-9 place-items-center rounded-lg text-[12px] font-semibold transition ${
                                        draft.icon
                                            ? 'bg-slate-100 text-slate-400 hover:bg-slate-200'
                                            : 'bg-blue-600 text-white'
                                    }`}
                                >
                                    {spaceMonogram(draft)}
                                </button>
                                {SPACE_ICONS.map((icon) => (
                                    <button
                                        key={icon}
                                        type="button"
                                        onClick={() => patch({ icon })}
                                        aria-pressed={draft.icon === icon}
                                        className={`grid h-9 w-9 place-items-center rounded-lg text-[16px] leading-none transition ${
                                            draft.icon === icon
                                                ? 'bg-blue-600 ring-2 ring-blue-600/25'
                                                : 'bg-slate-100 hover:bg-slate-200'
                                        }`}
                                    >
                                        {icon}
                                    </button>
                                ))}
                            </div>
                        </div>
                    </Group>
                </section>

                {/* ── Кому выдано ────────────────────────────────────────────
                    Это ГРАНИЦА, а не подсказка: раздел чужого пространства не
                    откроется отделу ни правилом, ни публичностью, ни ссылкой.

                    Селектор с поиском, а не столбик тумблеров: отделов в
                    компании больше, чем помещается в окно, и тумблеры росли бы
                    списком, который приходится прокручивать, чтобы найти один
                    нужный. Тот же общий примитив, что у выбора исполнителей
                    задачи, — и ведёт себя он так же. */}
                <section className="space-y-1.5">
                    <div className={iosGroupLabel}>Каким отделам видно</div>
                    <Group className="p-3.5">
                        {/* Общий сегментированный переключатель, а не своя пара
                            кнопок: раньше здесь лежала третья копия того же
                            элемента, и на первой же правке палитры она осталась
                            бы прежней. */}
                        <IosSegmented
                            stretch
                            ariaLabel="Кому видно пространство"
                            value={everyone ? 'all' : 'some'}
                            options={[
                                { value: 'all', label: 'Всем отделам' },
                                { value: 'some', label: 'Выбранным' },
                            ]}
                            /* «Выбранным» переводит в режим выбора, но НЕ
                               отмечает все отделы разом: со списком-селектором
                               это значило бы показать «Выбрано: 5» и заставить
                               снимать лишние. Человек открывает список и
                               отмечает нужные — пустой выбор до первого
                               отмеченного честно подписан ниже. */
                            onChange={(key) => setRestricted(key === 'some')}
                        />

                        {!everyone && (
                            <div className="space-y-2 pt-3">
                                <CustomSelect
                                    variant="ios"
                                    multiple
                                    searchable
                                    searchPlaceholder="Поиск по названию отдела…"
                                    ariaLabel="Отделы, которым видно пространство"
                                    placeholder="Выберите отделы"
                                    value={draft.department_ids}
                                    options={departmentOptions}
                                    onChange={(next) => patch({
                                        department_ids: (Array.isArray(next) ? next : []).map(Number),
                                    })}
                                    renderValue={renderDepartments}
                                />
                                {/* Отмеченные — чипами под селектором: в кнопке они
                                    обрезаются на второй-третьей строке, а список
                                    отделов пространства читают целиком. Крестик
                                    снимает отдел, не открывая список. */}
                                {draft.department_ids.length > 0 && (
                                    <div className="flex flex-wrap gap-1.5">
                                        {draft.department_ids.map((id) => (
                                            <span
                                                key={id}
                                                className="inline-flex max-w-full items-center gap-1 rounded-full bg-slate-100 py-1 pl-2.5 pr-1 text-[12px] text-slate-700"
                                            >
                                                <span className="truncate">{nameOfDepartment(id)}</span>
                                                <button
                                                    type="button"
                                                    onClick={() => toggleDepartment(id, false)}
                                                    aria-label={`Убрать отдел ${nameOfDepartment(id)}`}
                                                    className="grid h-4 w-4 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200 hover:text-slate-600"
                                                >
                                                    <X size={11} />
                                                </button>
                                            </span>
                                        ))}
                                    </div>
                                )}
                            </div>
                        )}
                    </Group>

                    <Hint>
                        {everyone
                            ? 'Пространство увидят сотрудники всех отделов.'
                            : draft.department_ids.length === 0
                                ? 'Ни один отдел не выбран — пространство не увидит никто, кроме супер-администратора.'
                                : `Пространство увидят только выбранные отделы (${draft.department_ids.length}). Остальным не будет видно ни одного его раздела — даже публичного.`}
                    </Hint>
                </section>

                {/* ── Из чего состоит ── */}
                <section className="space-y-1.5">
                    <div className="flex items-end justify-between gap-2">
                        <div className={iosGroupLabel}>Разделы пространства</div>
                        <span className="text-[11px] tabular-nums text-slate-400">
                            вкладок: {tabsLeft}
                        </span>
                    </div>
                    <Group>
                        {SPACE_TABS.map((tab) => {
                            const on = tab.locked || draft.features[tab.key];
                            return (
                                <div key={tab.key}>
                                    <label
                                        className={`flex items-center justify-between gap-3 px-3.5 py-2.5 ${
                                            tab.locked ? '' : 'cursor-pointer'
                                        }`}
                                    >
                                        <span className="min-w-0 flex-1">
                                            <span className="block truncate text-[13.5px] text-slate-800">
                                                {tab.label}
                                            </span>
                                            {/* Пояснение переносится, а не обрезается:
                                                оно объясняет, почему тумблер заперт,
                                                и «…ведут ссылки из поиска, писем и
                                                уведомл…» не объясняет ничего. */}
                                            {tab.locked && (
                                                <span className="block text-[11px] leading-snug text-slate-400">
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
                                        не будет, — предложение без последствий.
                                        Отступ слева и серая заливка — приём
                                        сгруппированных списков iOS: вложенность видна
                                        по ступеньке, а не по рамке внутри рамки. */}
                                    {on && (tab.children || []).map((child) => (
                                        <label
                                            key={child.key}
                                            className="flex cursor-pointer items-center justify-between gap-3 border-t border-slate-100 bg-slate-50/70 py-2 pl-8 pr-3.5"
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
                    </Group>
                    <Hint>
                        Выключенная вкладка исчезает и из редактора: без «Тренажёров»
                        у статьи не будет и типа «Тренажёр».
                    </Hint>
                </section>
            </div>
        </IosModal>
    );
}
