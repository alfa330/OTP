import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    Archive, ArchiveRestore, ChevronRight, FolderTree, Globe, Layers, Lock, Plus,
    Loader2, Pencil,
} from 'lucide-react';
import {
    iosCard, iosGroupLabel, iosInput, iosBtnPrimary, iosBtnSecondary, iosBtnGhost,
    IosBadge, IosModal, IosToggle,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { selectableSections, sectionOptionLabel } from './sectionPicker';

/* Структура вики: пространства → разделы (дерево).
 *
 * Пространство необязательно привязано к отделу — в исходной вике связь была
 * жёсткой (department_id UNIQUE плюс триггер), из-за чего структура контента
 * была обязана повторять оргструктуру и не могла от неё отличаться.
 *
 * Раздел «публичный» виден всем сотрудникам без единого правила. В оригинале
 * это поле проставлялось только сидом по совпадению названия с «общ» и не
 * имело ни API, ни интерфейса — здесь это обычный переключатель.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const SectionRow = ({ section, depth, onEdit, onAddChild, onArchive, onRestore, busy }) => (
    <div
        className="flex items-center gap-2 px-4 py-2.5 transition hover:bg-slate-50"
        style={{ paddingLeft: `${16 + depth * 22}px` }}
    >
        {depth > 0 && <ChevronRight size={13} className="shrink-0 text-slate-300" />}
        <FolderTree size={15} className="shrink-0 text-amber-500" />

        <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-1.5">
                <span className="truncate text-[13.5px] font-medium text-slate-900">
                    {section.name}
                </span>
                {section.visibility_scope === 'public' ? (
                    <IosBadge tone="green" title="Виден всем сотрудникам без правил">
                        <Globe size={11} /> Публичный
                    </IosBadge>
                ) : (
                    <IosBadge tone="slate" title="Виден только по правилам доступа">
                        <Lock size={11} /> По правилам
                    </IosBadge>
                )}
                {section.department_name && (
                    <IosBadge tone="blue" title="Ветка отдела">
                        {section.department_name}
                    </IosBadge>
                )}
                {section.status === 'archived' && <IosBadge tone="amber">В архиве</IosBadge>}
            </div>
            <div className="mt-0.5 flex flex-wrap gap-x-3 text-[11.5px] text-slate-400">
                <span className="tabular-nums">{section.articles_count} статей</span>
                <span className="tabular-nums">{section.rules_count} правил</span>
                {section.owner_name && <span>владелец: {section.owner_name}</span>}
            </div>
        </div>

        {/* Подраздел добавляется прямо со строки родителя. Раньше вложенность
            задавалась только селектом внутри модалки, которую открывала кнопка
            у пространства, — и «добавить внутрь этого раздела» выглядело как
            отсутствующая возможность. */}
        {section.status === 'active' && (
            <button
                type="button"
                onClick={() => onAddChild(section)}
                className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-emerald-50 hover:text-emerald-600"
                aria-label={`Добавить подраздел в «${section.name}»`}
                title="Добавить подраздел"
            >
                <Plus size={15} />
            </button>
        )}
        <button
            type="button"
            onClick={() => onEdit(section)}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-blue-50 hover:text-blue-600"
            aria-label="Изменить раздел"
        >
            <Pencil size={14} />
        </button>
        {section.status === 'active' ? (
            <button
                type="button"
                disabled={busy}
                onClick={() => onArchive(section)}
                className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-amber-50 hover:text-amber-600 disabled:opacity-40"
                aria-label="Убрать в архив"
            >
                <Archive size={14} />
            </button>
        ) : (
            <button
                type="button"
                disabled={busy}
                onClick={() => onRestore(section)}
                className="grid h-8 w-8 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-emerald-50 hover:text-emerald-600 disabled:opacity-40"
                aria-label="Вернуть из архива"
            >
                <ArchiveRestore size={14} />
            </button>
        )}
    </div>
);

export default function WikiStructure({ base, headers, showToast, structure, reload, loading }) {
    const [departments, setDepartments] = useState([]);
    const [busy, setBusy] = useState(false);

    const [spaceModal, setSpaceModal] = useState(null);   // {id?, name, ...}
    const [sectionModal, setSectionModal] = useState(null);

    useEffect(() => {
        axios.get(`${base}/access/subjects`, { headers })
            .then((r) => setDepartments(r.data?.department || []))
            .catch(() => setDepartments([]));   // не админ — справочник недоступен, это норма
    }, [base, headers]);

    const spaces = structure?.spaces || [];
    const sections = structure?.sections || [];

    // Дерево строим один раз на изменение списка, а не на каждый рендер строки.
    const bySpace = useMemo(() => {
        const grouped = new Map();
        const children = new Map();
        sections.forEach((s) => {
            const key = s.parent_section_id || `root:${s.space_id}`;
            if (!children.has(key)) children.set(key, []);
            children.get(key).push(s);
        });
        spaces.forEach((sp) => {
            const walk = (parentKey, depth) => {
                const list = children.get(parentKey) || [];
                return list.flatMap((s) => [{ section: s, depth }, ...walk(s.id, depth + 1)]);
            };
            grouped.set(sp.id, walk(`root:${sp.id}`, 0));
        });
        return grouped;
    }, [spaces, sections]);

    const saveSpace = () => {
        const payload = {
            name: spaceModal.name,
            description: spaceModal.description || null,
            department_id: spaceModal.department_id || null,
        };
        setBusy(true);
        const request = spaceModal.id
            ? axios.patch(`${base}/spaces/${spaceModal.id}`, payload, { headers })
            : axios.post(`${base}/spaces`, payload, { headers });
        request
            .then(() => {
                showToast?.(spaceModal.id ? 'Пространство обновлено' : 'Пространство создано', 'success');
                setSpaceModal(null);
                reload();
            })
            .catch((e) => showToast?.(errText(e, 'Не удалось сохранить'), 'error'))
            .finally(() => setBusy(false));
    };

    const saveSection = () => {
        const payload = {
            space_id: sectionModal.space_id,
            name: sectionModal.name,
            description: sectionModal.description || null,
            visibility_scope: sectionModal.visibility_scope,
            parent_section_id: sectionModal.parent_section_id || null,
            // Пустая строка — это «раздел не принадлежит отделу», её надо
            // отправить как null, иначе бэкенд не снимет отдел с ветки.
            department_id: sectionModal.department_id || null,
        };
        setBusy(true);
        const request = sectionModal.id
            ? axios.patch(`${base}/sections/${sectionModal.id}`, payload, { headers })
            : axios.post(`${base}/sections`, payload, { headers });
        request
            .then(() => {
                showToast?.(sectionModal.id ? 'Раздел обновлён' : 'Раздел создан', 'success');
                setSectionModal(null);
                reload();
            })
            .catch((e) => showToast?.(errText(e, 'Не удалось сохранить'), 'error'))
            .finally(() => setBusy(false));
    };

    const archiveSection = (section) => {
        setBusy(true);
        axios.delete(`${base}/sections/${section.id}`, { headers })
            .then(() => { showToast?.('Раздел убран в архив', 'success'); reload(); })
            .catch((e) => showToast?.(errText(e, 'Не удалось убрать в архив'), 'error'))
            .finally(() => setBusy(false));
    };

    const restoreSection = (section) => {
        setBusy(true);
        axios.patch(`${base}/sections/${section.id}`, { status: 'active' }, { headers })
            .then(() => { showToast?.('Раздел возвращён из архива', 'success'); reload(); })
            .catch((e) => showToast?.(errText(e, 'Не удалось вернуть'), 'error'))
            .finally(() => setBusy(false));
    };

    const departmentOptions = useMemo(() => ([
        { value: '', label: 'Без привязки к отделу' },
        ...departments.map((d) => ({ value: String(d.id), label: d.name })),
    ]), [departments]);

    return (
        <div className="space-y-5">
            <div className="flex flex-wrap items-center justify-between gap-2">
                <div className={iosGroupLabel}>Пространства и разделы</div>
                <button
                    type="button"
                    className={iosBtnPrimary}
                    onClick={() => setSpaceModal({ name: '', description: '', department_id: '' })}
                >
                    <Plus size={15} /> Пространство
                </button>
            </div>

            {loading && (
                <div className={`${iosCard} flex items-center justify-center gap-2 py-12 text-slate-400`}>
                    <Loader2 size={18} className="animate-spin" />
                    <span className="text-[13px]">Загружаем структуру…</span>
                </div>
            )}

            {!loading && spaces.length === 0 && (
                <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                    <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                        <Layers size={22} />
                    </div>
                    <div className="text-[15px] font-semibold text-slate-900">Структуры ещё нет</div>
                    <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">
                        Начните с пространства — например, по отделу. Внутри него создаются
                        разделы, а к разделам уже привязываются статьи и права.
                    </p>
                </div>
            )}

            {!loading && spaces.map((space) => (
                <section key={space.id} className="space-y-1.5">
                    <div className="flex flex-wrap items-center justify-between gap-2 px-1">
                        <div className="flex items-center gap-2">
                            <Layers size={15} className="text-indigo-500" />
                            <span className="text-[14px] font-semibold text-slate-900">{space.name}</span>
                            {space.department_name && (
                                <IosBadge tone="slate">{space.department_name}</IosBadge>
                            )}
                            {space.status === 'archived' && <IosBadge tone="amber">В архиве</IosBadge>}
                        </div>
                        <div className="flex items-center gap-1">
                            <button
                                type="button"
                                className={iosBtnGhost}
                                onClick={() => setSpaceModal({
                                    id: space.id, name: space.name,
                                    description: space.description || '',
                                    department_id: space.department_id ? String(space.department_id) : '',
                                })}
                            >
                                <Pencil size={13} /> Изменить
                            </button>
                            <button
                                type="button"
                                className={iosBtnGhost}
                                onClick={() => setSectionModal({
                                    space_id: space.id, name: '', description: '',
                                    visibility_scope: 'restricted', parent_section_id: '',
                                    department_id: '',
                                })}
                            >
                                <Plus size={13} /> Раздел
                            </button>
                        </div>
                    </div>

                    <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                        {(bySpace.get(space.id) || []).length === 0 && (
                            <div className="px-4 py-8 text-center text-[13px] text-slate-400">
                                В пространстве пока нет разделов
                            </div>
                        )}
                        {(bySpace.get(space.id) || []).map(({ section, depth }) => (
                            <SectionRow
                                key={section.id}
                                section={section}
                                depth={depth}
                                busy={busy}
                                onEdit={(s) => setSectionModal({
                                    id: s.id, space_id: s.space_id, name: s.name,
                                    description: s.description || '',
                                    visibility_scope: s.visibility_scope,
                                    parent_section_id: s.parent_section_id ? String(s.parent_section_id) : '',
                                    department_id: s.department_id ? String(s.department_id) : '',
                                })}
                                onAddChild={(s) => setSectionModal({
                                    space_id: s.space_id, name: '', description: '',
                                    visibility_scope: 'restricted',
                                    parent_section_id: String(s.id),
                                    // Отдел НЕ наследуем от родителя: подраздел ОП
                                    // внутри ветки ОП — это должность, а не второй
                                    // отдел, и метка отдела там только запутает.
                                    department_id: '',
                                })}
                                onArchive={archiveSection}
                                onRestore={restoreSection}
                            />
                        ))}
                    </div>
                </section>
            ))}

            {/* ── Пространство ── */}
            <IosModal
                open={!!spaceModal}
                onClose={() => setSpaceModal(null)}
                title={spaceModal?.id ? 'Изменить пространство' : 'Новое пространство'}
                subtitle="Верхний уровень структуры — обычно отдел или направление"
                footer={(
                    <>
                        <button type="button" className={iosBtnSecondary} onClick={() => setSpaceModal(null)}>
                            Отмена
                        </button>
                        <button
                            type="button"
                            className={iosBtnPrimary}
                            disabled={busy || !spaceModal?.name?.trim()}
                            onClick={saveSpace}
                        >
                            {busy && <Loader2 size={14} className="animate-spin" />} Сохранить
                        </button>
                    </>
                )}
            >
                {spaceModal && (
                    <div className="space-y-3.5">
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Название</label>
                            <input
                                className={iosInput}
                                value={spaceModal.name}
                                autoFocus
                                onChange={(e) => setSpaceModal({ ...spaceModal, name: e.target.value })}
                                placeholder="Например: Отдел продаж"
                            />
                        </div>
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Описание</label>
                            <textarea
                                className={`${iosInput} min-h-[76px] resize-y`}
                                value={spaceModal.description}
                                onChange={(e) => setSpaceModal({ ...spaceModal, description: e.target.value })}
                            />
                        </div>
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Отдел</label>
                            <CustomSelect
                                variant="ios"
                                value={spaceModal.department_id}
                                onChange={(v) => setSpaceModal({ ...spaceModal, department_id: v })}
                                options={departmentOptions}
                                ariaLabel="Отдел пространства"
                            />
                            <p className="mt-1 px-1 text-[11.5px] leading-relaxed text-slate-400">
                                Привязка необязательна. Она нужна, только если хотите выдавать
                                доступ ко всему пространству одной строкой — по отделу.
                            </p>
                        </div>
                    </div>
                )}
            </IosModal>

            {/* ── Раздел ── */}
            <IosModal
                open={!!sectionModal}
                onClose={() => setSectionModal(null)}
                title={sectionModal?.id ? 'Изменить раздел' : 'Новый раздел'}
                subtitle={
                    /* Куда именно ляжет раздел — в заголовке, а не только в
                       селекте ниже: модалку открывают с трёх разных мест, и
                       родитель уже подставлен. */
                    sectionModal?.parent_section_id
                        ? `Внутри раздела «${sections.find((x) => String(x.id) === String(sectionModal.parent_section_id))?.name || '—'}»`
                        : 'Раздел — единица выдачи доступа'
                }
                footer={(
                    <>
                        <button type="button" className={iosBtnSecondary} onClick={() => setSectionModal(null)}>
                            Отмена
                        </button>
                        <button
                            type="button"
                            className={iosBtnPrimary}
                            disabled={busy || !sectionModal?.name?.trim()}
                            onClick={saveSection}
                        >
                            {busy && <Loader2 size={14} className="animate-spin" />} Сохранить
                        </button>
                    </>
                )}
            >
                {sectionModal && (
                    <div className="space-y-3.5">
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">Название</label>
                            <input
                                className={iosInput}
                                value={sectionModal.name}
                                autoFocus
                                onChange={(e) => setSectionModal({ ...sectionModal, name: e.target.value })}
                                placeholder="Например: Регламенты"
                            />
                        </div>
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">
                                Вложить в раздел
                            </label>
                            <CustomSelect
                                variant="ios"
                                value={sectionModal.parent_section_id}
                                onChange={(v) => setSectionModal({ ...sectionModal, parent_section_id: v })}
                                options={[
                                    { value: '', label: 'Верхний уровень' },
                                    // Вкладка целиком показывает и архив (он тут по делу),
                                    // но вкладывать живой раздел в архивный нельзя.
                                    ...selectableSections(sections, sectionModal.parent_section_id)
                                        .filter((s) => s.space_id === sectionModal.space_id && s.id !== sectionModal.id)
                                        .map((s) => ({ value: String(s.id), label: sectionOptionLabel(s) })),
                                ]}
                                ariaLabel="Родительский раздел"
                            />
                        </div>
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">
                                Отдел ветки
                            </label>
                            <CustomSelect
                                variant="ios"
                                value={sectionModal.department_id || ''}
                                onChange={(v) => setSectionModal({ ...sectionModal, department_id: v })}
                                options={[
                                    { value: '', label: 'Не привязан к отделу' },
                                    ...departments.map((d) => ({ value: String(d.id), label: d.name })),
                                ]}
                                searchable
                                ariaLabel="Отдел ветки"
                            />
                            <p className="mt-1 px-1 text-[11.5px] text-slate-400">
                                Отмечает ветку как принадлежащую отделу — например «СЗоВ» или «ОП».
                                Права на неё всё равно выдаются во вкладке «Доступы»: привязка
                                к отделу сама по себе никого не пускает.
                            </p>
                        </div>

                        <div className={`${iosCard} flex items-start justify-between gap-3 p-3.5`}>
                            <div className="min-w-0">
                                <div className="text-[13.5px] font-medium text-slate-900">
                                    Публичный раздел
                                </div>
                                <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-500">
                                    Виден всем сотрудникам без единого правила. Для служебной
                                    информации оставьте выключенным.
                                </p>
                            </div>
                            <IosToggle
                                checked={sectionModal.visibility_scope === 'public'}
                                onChange={(v) => setSectionModal({
                                    ...sectionModal,
                                    visibility_scope: v ? 'public' : 'restricted',
                                })}
                            />
                        </div>
                    </div>
                )}
            </IosModal>
        </div>
    );
}
