import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    Archive, ArchiveRestore, Building2, ChevronRight, FolderTree, Globe, KeyRound,
    Layers, Plus, Loader2, Pencil, UserSearch,
} from 'lucide-react';
import {
    iosCard, iosGroupLabel, iosInput, iosBtnPrimary, iosBtnSecondary, iosBtnGhost,
    IosBadge, IosMenu, IosModal, IosToggle,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { selectableSections, sectionPathLabel } from './sectionPicker';
import WikiSectionAccess, { branchDepartment } from './WikiSectionAccess';
import WikiAccessProbe from './WikiAccessProbe';

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

/* Строка живого раздела. Архивные сюда не попадают — у них своя вкладка,
   поэтому ни ветки «вернуть», ни бейджа «в архиве» здесь нет.

   Все действия — под «тремя точками», включая доступ: отдельной вкладки
   «Доступы» больше нет. Там раздел выбирался селектом из плоского списка, в
   котором у СЗоВ и у ОП свои одноимённые «Руководитель», «Супервайзер»,
   «Оператор», — и правило регулярно уезжало в чужую ветку.

   Ряд из четырёх круглых иконок был первым заходом и от него отказались:
   в строке они читаются как украшение, что делает каждая — понятно только по
   наведению (а на телефоне наведения нет), и мишени стоят вплотную, так что
   «в архив» ловится вместо «изменить». */
const SectionRow = ({ section, depth, department, onEdit, onAddChild, onArchive,
                     onAccess, busy }) => {
    // Ветка отдела и должность внутри неё — разные сущности, и на глаз они
    // должны отличаться так же, как отличаются по смыслу.
    const isBranch = !!section.department_id;
    const orphan = section.visibility_scope !== 'public' && !section.rules_count;

    return (
        <div
            className="flex items-center gap-2 px-4 py-2.5 transition hover:bg-slate-50"
            style={{ paddingLeft: `${16 + depth * 22}px` }}
        >
            {depth > 0 && <ChevronRight size={13} className="shrink-0 text-slate-300" />}
            {isBranch
                ? <Building2 size={15} className="shrink-0 text-indigo-500" />
                : <FolderTree size={15} className="shrink-0 text-amber-500" />}

            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                    <span className="truncate text-[13.5px] font-medium text-slate-900">
                        {section.name}
                    </span>
                    {isBranch && (
                        <IosBadge tone="blue" title="Ветка отдела: доступ внутри неё считается по этому отделу">
                            <Building2 size={11} /> {section.department_name || 'отдел'}
                        </IosBadge>
                    )}
                    {/* Бейджа «По правилам» нет: это состояние по умолчанию у
                        всех разделов, и повторять его в каждой строке — шум.
                        Отмечаем только исключение — публичный раздел. */}
                    {section.visibility_scope === 'public' && (
                        <IosBadge tone="green" title="Виден всем сотрудникам без правил">
                            <Globe size={11} /> Публичный
                        </IosBadge>
                    )}
                    {/* Раздел без единого правила не видит никто, кроме админов.
                        Молчать об этом нельзя: у веток «Супервайзер» и
                        «Руководитель группы» так и вышло — статьи лежат, а
                        открыть их некому. */}
                    {orphan && (
                        <IosBadge tone="amber" title="Ни одного правила: раздел не виден никому, кроме администраторов">
                            доступа нет
                        </IosBadge>
                    )}
                </div>
                <div className="mt-0.5 flex flex-wrap gap-x-3 text-[11.5px] text-slate-400">
                    <span className="tabular-nums">{section.articles_count} статей</span>
                    <span className="tabular-nums">{section.rules_count} правил</span>
                    {!isBranch && department && <span>в отделе {department.name}</span>}
                    {section.owner_name && <span>владелец: {section.owner_name}</span>}
                </div>
            </div>

            <IosMenu
                label={`Действия с разделом «${section.name}»`}
                disabled={busy}
                items={[
                    /* Подраздел добавляется прямо со строки родителя. Раньше
                       вложенность задавалась только селектом внутри модалки,
                       которую открывала кнопка у пространства, — и «добавить
                       внутрь этого раздела» выглядело как отсутствующая
                       возможность. */
                    { key: 'child', label: 'Добавить подраздел', icon: Plus,
                      onSelect: () => onAddChild(section) },
                    { key: 'edit', label: 'Изменить раздел', icon: Pencil,
                      onSelect: () => onEdit(section) },
                    onAccess && {
                        key: 'access', label: 'Кому открыт раздел', icon: KeyRound,
                        // Число правил прямо в пункте: у раздела без единого
                        // правила это единственное место, где видно, что
                        // открывать его некому.
                        hint: orphan ? 'нет правил' : String(section.rules_count),
                        onSelect: () => onAccess(section),
                    },
                    { key: 'archive', label: 'Убрать в архив', icon: Archive,
                      danger: true, separatorBefore: true,
                      onSelect: () => onArchive(section) },
                ]}
            />
        </div>
    );
};

export default function WikiStructure({ base, headers, showToast, structure, reload, loading,
                                        canManageAccess = false }) {
    const [departments, setDepartments] = useState([]);
    const [busy, setBusy] = useState(false);

    // Три вкладки вместо одного длинного списка: пространства правят редко,
    // разделы — постоянно, а архив нужен, только когда что-то возвращают.
    const [tab, setTab] = useState('sections');

    const [spaceModal, setSpaceModal] = useState(null);   // {id?, name, ...}
    const [sectionModal, setSectionModal] = useState(null);
    const [accessSection, setAccessSection] = useState(null);
    const [probeOpen, setProbeOpen] = useState(false);

    useEffect(() => {
        axios.get(`${base}/access/subjects`, { headers })
            .then((r) => setDepartments(r.data?.department || []))
            .catch(() => setDepartments([]));   // не админ — справочник недоступен, это норма
    }, [base, headers]);

    const spaces = structure?.spaces || [];
    const sections = structure?.sections || [];

    const activeSpaces = useMemo(() => spaces.filter((x) => x.status !== 'archived'), [spaces]);
    const archivedSpaces = useMemo(() => spaces.filter((x) => x.status === 'archived'), [spaces]);
    const archivedSections = useMemo(
        () => sections.filter((x) => x.status === 'archived'), [sections]);

    // Дерево строим один раз на изменение списка, а не на каждый рендер строки.
    // Только живые разделы: архивные живут на своей вкладке, а рядом с живым
    // двойником были неотличимы — архивируют обычно как раз дубль с тем же именем.
    const bySpace = useMemo(() => {
        const grouped = new Map();
        const alive = sections.filter((x) => x.status !== 'archived');
        const shown = new Set(alive.map((x) => x.id));
        const children = new Map();
        alive.forEach((s) => {
            // Родитель в архиве — живая ветка поднимается в корень пространства,
            // иначе она пропала бы из вкладки вместе с ним.
            const key = (s.parent_section_id && shown.has(s.parent_section_id))
                ? s.parent_section_id
                : `root:${s.space_id}`;
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

    const spaceName = useMemo(
        () => new Map(spaces.map((sp) => [sp.id, sp.name])), [spaces]);

    const archiveSpace = (space) => {
        setBusy(true);
        axios.delete(`${base}/spaces/${space.id}`, { headers })
            .then(() => { showToast?.('Пространство убрано в архив', 'success'); reload(); })
            .catch((e) => showToast?.(errText(e, 'Не удалось убрать в архив'), 'error'))
            .finally(() => setBusy(false));
    };

    const restoreSpace = (space) => {
        setBusy(true);
        axios.patch(`${base}/spaces/${space.id}`, { status: 'active' }, { headers })
            .then(() => { showToast?.('Пространство возвращено из архива', 'success'); reload(); })
            .catch((e) => showToast?.(errText(e, 'Не удалось вернуть'), 'error'))
            .finally(() => setBusy(false));
    };

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
            // Ключ шлём всегда, в том числе пустым: сняли отдел — раздел обязан
            // перестать быть веткой, а без ключа сервер просто не тронет поле.
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
                <div className={iosGroupLabel}>Структура вики</div>
                <div className="flex flex-wrap items-center gap-2">
                    {/* Проверка «что видит человек» переехала сюда со вкладки
                        «Доступы»: она про сотрудника, а не про раздел, и строкой
                        дерева её не открыть. */}
                    {canManageAccess && (
                        <button type="button" className={iosBtnGhost}
                                onClick={() => setProbeOpen(true)}>
                            <UserSearch size={14} /> Проверить доступ
                        </button>
                    )}
                    {tab === 'spaces' && (
                        <button
                            type="button"
                            className={iosBtnPrimary}
                            onClick={() => setSpaceModal({ name: '', description: '', department_id: '' })}
                        >
                            <Plus size={15} /> Пространство
                        </button>
                    )}
                </div>
            </div>

            <div className="flex gap-1 overflow-x-auto rounded-2xl bg-slate-100 p-1">
                {[
                    { key: 'spaces', label: 'Пространства', icon: Layers, count: activeSpaces.length },
                    { key: 'sections', label: 'Разделы', icon: FolderTree,
                      count: sections.filter((x) => x.status !== 'archived').length },
                    { key: 'archive', label: 'Архив', icon: Archive,
                      count: archivedSpaces.length + archivedSections.length },
                ].map(({ key, label, icon: Icon, count }) => (
                    <button
                        key={key}
                        type="button"
                        onClick={() => setTab(key)}
                        className={`flex shrink-0 items-center gap-1.5 rounded-xl px-3.5 py-2 text-[13px] font-medium transition ${
                            tab === key
                                ? 'bg-white text-slate-900 shadow-sm'
                                : 'text-slate-500 hover:text-slate-700'
                        }`}
                    >
                        <Icon size={14} /> {label}
                        <span className="tabular-nums text-[11.5px] text-slate-400">{count}</span>
                    </button>
                ))}
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

            {/* ── Вкладка «Пространства» ── */}
            {!loading && tab === 'spaces' && activeSpaces.map((space) => (
                <div key={space.id} className={`${iosCard} flex flex-wrap items-center gap-2 px-4 py-3`}>
                    <Layers size={15} className="shrink-0 text-indigo-500" />
                    <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                            <span className="text-[14px] font-semibold text-slate-900">{space.name}</span>
                            {space.department_name && (
                                <IosBadge tone="slate">{space.department_name}</IosBadge>
                            )}
                        </div>
                        <div className="mt-0.5 text-[11.5px] text-slate-400">
                            <span className="tabular-nums">
                                {(bySpace.get(space.id) || []).length} разделов
                            </span>
                            {space.description && <span> · {space.description}</span>}
                        </div>
                    </div>
                    {/* Те же «три точки», что и у раздела: строки соседние, и
                        два разных способа вызвать одни и те же действия читались
                        бы как два разных вида объектов. */}
                    <IosMenu
                        label={`Действия с пространством «${space.name}»`}
                        disabled={busy}
                        items={[
                            { key: 'section', label: 'Добавить раздел', icon: Plus,
                              onSelect: () => setSectionModal({
                                  space_id: space.id, name: '', description: '',
                                  visibility_scope: 'restricted', parent_section_id: '',
                                  department_id: '',
                              }) },
                            { key: 'edit', label: 'Изменить пространство', icon: Pencil,
                              onSelect: () => setSpaceModal({
                                  id: space.id, name: space.name,
                                  description: space.description || '',
                                  department_id: space.department_id ? String(space.department_id) : '',
                              }) },
                            { key: 'archive', label: 'Убрать в архив', icon: Archive,
                              danger: true, separatorBefore: true,
                              onSelect: () => archiveSpace(space) },
                        ]}
                    />
                </div>
            ))}

            {/* ── Вкладка «Разделы» ── */}
            {!loading && tab === 'sections' && activeSpaces.map((space) => (
                <section key={space.id} className="space-y-1.5">
                    <div className="flex flex-wrap items-center justify-between gap-2 px-1">
                        <div className="flex items-center gap-2">
                            <Layers size={15} className="text-indigo-500" />
                            <span className="text-[14px] font-semibold text-slate-900">{space.name}</span>
                            {space.department_name && (
                                <IosBadge tone="slate">{space.department_name}</IosBadge>
                            )}
                        </div>
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
                                department={branchDepartment(sections, section.id)}
                                onEdit={(x) => setSectionModal({
                                    id: x.id, space_id: x.space_id, name: x.name,
                                    description: x.description || '',
                                    visibility_scope: x.visibility_scope,
                                    parent_section_id: x.parent_section_id ? String(x.parent_section_id) : '',
                                    department_id: x.department_id ? String(x.department_id) : '',
                                })}
                                onAddChild={(x) => setSectionModal({
                                    space_id: x.space_id, name: '', description: '',
                                    visibility_scope: 'restricted',
                                    parent_section_id: String(x.id),
                                    department_id: '',
                                })}
                                onArchive={archiveSection}
                                onAccess={canManageAccess ? setAccessSection : null}
                            />
                        ))}
                    </div>
                </section>
            ))}

            {/* ── Вкладка «Архив» ── */}
            {/* Удаление везде мягкое: и раздел, и пространство уходят сюда, а не
                исчезают. У пространства это принципиально — физическое удаление
                снесло бы каскадом все его разделы вместе со статьями. */}
            {!loading && tab === 'archive' && (
                archivedSpaces.length === 0 && archivedSections.length === 0 ? (
                    <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                        <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                            <Archive size={22} />
                        </div>
                        <div className="text-[15px] font-semibold text-slate-900">Архив пуст</div>
                        <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">
                            Сюда попадает всё, что убрали из структуры. Ничего не удаляется
                            насовсем — любой элемент можно вернуть на место.
                        </p>
                    </div>
                ) : (
                    <div className="space-y-5">
                        {archivedSpaces.length > 0 && (
                            <section className="space-y-1.5">
                                <div className="px-1 text-[12px] font-medium text-slate-500">Пространства</div>
                                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                    {archivedSpaces.map((space) => (
                                        <div key={space.id} className="flex items-center gap-2 px-4 py-2.5">
                                            <Layers size={15} className="shrink-0 text-slate-300" />
                                            <div className="min-w-0 flex-1">
                                                <div className="truncate text-[13.5px] font-medium text-slate-900">
                                                    {space.name}
                                                </div>
                                                {space.department_name && (
                                                    <div className="mt-0.5 text-[11.5px] text-slate-400">
                                                        {space.department_name}
                                                    </div>
                                                )}
                                            </div>
                                            <button
                                                type="button"
                                                disabled={busy}
                                                className={iosBtnGhost}
                                                onClick={() => restoreSpace(space)}
                                            >
                                                <ArchiveRestore size={13} /> Вернуть
                                            </button>
                                        </div>
                                    ))}
                                </div>
                            </section>
                        )}

                        {archivedSections.length > 0 && (
                            <section className="space-y-1.5">
                                <div className="px-1 text-[12px] font-medium text-slate-500">Разделы</div>
                                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                    {archivedSections.map((section) => (
                                        <div key={section.id} className="flex items-center gap-2 px-4 py-2.5">
                                            <FolderTree size={15} className="shrink-0 text-slate-300" />
                                            <div className="min-w-0 flex-1">
                                                <div className="flex flex-wrap items-center gap-1.5">
                                                    <span className="truncate text-[13.5px] font-medium text-slate-900">
                                                        {section.name}
                                                    </span>
                                                </div>
                                                <div className="mt-0.5 flex flex-wrap gap-x-3 text-[11.5px] text-slate-400">
                                                    <span>{spaceName.get(section.space_id) || '—'}</span>
                                                    {/* Статьи из архивного раздела никуда не делись —
                                                        показываем счётчик, чтобы «вернуть» не выглядело
                                                        восстановлением пустышки. */}
                                                    <span className="tabular-nums">{section.articles_count} статей</span>
                                                </div>
                                            </div>
                                            <button
                                                type="button"
                                                disabled={busy}
                                                className={iosBtnGhost}
                                                onClick={() => restoreSection(section)}
                                            >
                                                <ArchiveRestore size={13} /> Вернуть
                                            </button>
                                        </div>
                                    ))}
                                </div>
                            </section>
                        )}
                    </div>
                )
            )}

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
                                        // Путь целиком: одноимённые ветки СЗоВ и ОП
                                        // в плоском списке неразличимы.
                                        .map((s) => ({ value: String(s.id), label: sectionPathLabel(sections, s.id) })),
                                ]}
                                ariaLabel="Родительский раздел"
                            />
                        </div>
                        {/* Чем является раздел. Ветка отдела (СЗоВ, ОП) сама
                            доступ не раздаёт — она задаёт отдел, в границах
                            которого считаются права её подразделов: «Оператор»
                            внутри СЗоВ открывается операторам СЗоВ, а не всей
                            компании. Должность — обычный раздел, и права на
                            неё выдаются из строки кнопкой с ключом.

                            Вид раздела не хранится вторым полем: он выводится
                            из отдела, иначе пара «ветка без отдела» разъедется
                            с уникальным индексом на сервере. */}
                        <div className="space-y-1.5">
                            <label className="block px-1 text-[12px] font-medium text-slate-500">
                                Чем является раздел
                            </label>
                            <div className="flex gap-1 rounded-2xl bg-slate-100 p-1">
                                {[
                                    { key: 'common', label: 'Должность', icon: FolderTree },
                                    { key: 'department', label: 'Отдел', icon: Building2 },
                                ].map(({ key, label, icon: Icon }) => {
                                    const active = key === 'department'
                                        ? !!sectionModal.department_id
                                        : !sectionModal.department_id;
                                    return (
                                        <button
                                            key={key}
                                            type="button"
                                            onClick={() => setSectionModal({
                                                ...sectionModal,
                                                // Первый отдел из справочника, чтобы переключение
                                                // сразу что-то значило; список рядом.
                                                department_id: key === 'department'
                                                    ? (sectionModal.department_id
                                                        || (departments[0] ? String(departments[0].id) : ''))
                                                    : '',
                                            })}
                                            className={`flex flex-1 items-center justify-center gap-1.5 rounded-xl px-3 py-2 text-[13px] font-medium transition ${
                                                active
                                                    ? 'bg-white text-slate-900 shadow-sm'
                                                    : 'text-slate-500 hover:text-slate-700'
                                            }`}
                                        >
                                            <Icon size={14} /> {label}
                                        </button>
                                    );
                                })}
                            </div>
                            {sectionModal.department_id ? (
                                <>
                                    <CustomSelect
                                        variant="ios"
                                        value={sectionModal.department_id}
                                        onChange={(v) => setSectionModal({ ...sectionModal, department_id: v })}
                                        options={departments.map((d) => ({
                                            value: String(d.id), label: d.name,
                                        }))}
                                        searchable
                                        ariaLabel="Отдел ветки"
                                    />
                                    <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">
                                        Доступ у самой ветки настраивать не нужно — его выдают
                                        в подразделах: «Руководитель группы», «Супервайзер»,
                                        «Оператор». Права там будут действовать только внутри
                                        этого отдела.
                                    </p>
                                </>
                            ) : (
                                <p className="px-1 text-[11.5px] leading-relaxed text-slate-400">
                                    Обычный раздел. Если он лежит внутри ветки отдела, права
                                    выдаются по должностям этого отдела; если нет — по должностям
                                    всей компании.
                                </p>
                            )}
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

            {/* ── Доступ к разделу ── */}
            {accessSection && (
                <WikiSectionAccess
                    base={base}
                    headers={headers}
                    showToast={showToast}
                    section={sections.find((x) => x.id === accessSection.id) || accessSection}
                    sections={sections}
                    onClose={() => setAccessSection(null)}
                    reload={reload}
                />
            )}

            <WikiAccessProbe
                base={base}
                headers={headers}
                open={probeOpen}
                onClose={() => setProbeOpen(false)}
            />
        </div>
    );
}
