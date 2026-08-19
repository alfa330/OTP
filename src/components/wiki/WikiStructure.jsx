import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    Archive, ArchiveRestore, ArrowDown, ArrowUp, Building2, ChevronRight, FolderTree,
    FoldVertical, Globe, KeyRound, Layers, Plus, Loader2, Pencil, Search, UnfoldVertical,
    UserSearch, X,
} from 'lucide-react';
import {
    iosCard, iosGroupLabel, iosInput, iosBtnPrimary, iosBtnSecondary, iosBtnGhost,
    IosBadge, IosMenu, IosModal, IosToggle,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import { selectableSections, sectionPathLabel } from './sectionPicker';
import WikiSectionAccess, { branchDepartment } from './WikiSectionAccess';
import WikiAccessProbe from './WikiAccessProbe';
import {
    FOCUS_TESTS, buildSpaceTree, collapseRows, filterRows, highlightParts,
    reorderPatches, sectionSiblings, structureNeedles,
} from './structureTree';

/* Структура вики: пространства → разделы (дерево).
 *
 * Пространство необязательно привязано к отделу — в исходной вике связь была
 * жёсткой (department_id UNIQUE плюс триггер), из-за чего структура контента
 * была обязана повторять оргструктуру и не могла от неё отличаться.
 *
 * Раздел «публичный» виден всем сотрудникам без единого правила. В оригинале
 * это поле проставлялось только сидом по совпадению названия с «общ» и не
 * имело ни API, ни интерфейса — здесь это обычный переключатель.
 *
 * Над деревом стоит панель: поиск, быстрые фильтры и свёртка. Она не украшение
 * и не «на вырост» — вкладку открывают три разных человека с тремя разными
 * задачами. Управляющий структурой правит дерево целиком; глава отдела приходит
 * за своей веткой в чужом дереве; супервайзер — вообще за одной строкой, чтобы
 * открыть раздел операторам, и остальные ему шум. Раскладка одна на всех, а
 * панель даёт каждому свести её к своему куску: фильтр «могу выдать доступ»
 * появляется, только если человек может не везде, — администратору вики,
 * который может всюду, он бы ничего не отфильтровал.
 *
 * Сама структура — лестница: «Коммерческий директор → СЗоВ → Руководитель
 * группы → Супервайзер → Оператор». Пять уровней в развёрнутом виде занимают
 * экран, поэтому ветки сворачиваются, а состояние свёртки переживает
 * перезагрузку: к своей ветке возвращаются каждый день, разворачивать её заново
 * каждый раз — работа, которую делать не надо.
 */

const errText = (e, fallback) => e?.response?.data?.error || e?.message || fallback;

const PREFS_KEY = 'wiki.structure.tree';

/* Свёрнутые ветки помним между заходами. В приватном режиме Safari доступ к
   localStorage бросает, поэтому обе стороны в try/catch: настройка
   второстепенна, падать из-за неё нельзя (тот же приём, что в WikiOffices). */
const readPrefs = () => {
    try {
        const raw = JSON.parse(window.localStorage.getItem(PREFS_KEY) || '{}');
        return {
            sections: new Set((raw.sections || []).map(Number)),
            spaces: new Set((raw.spaces || []).map(Number)),
        };
    } catch {
        return { sections: new Set(), spaces: new Set() };
    }
};

const toggled = (set, key) => {
    const next = new Set(set);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
};

/* Найденное подсвечиваем прямо в названии: в лестнице из одинаковых
   «Операторов» результат поиска иначе неотличим от строки-контекста. */
const Highlight = ({ text, needles }) => (
    <>
        {highlightParts(text, needles).map((part, i) => (part.hit
            ? <mark key={i} className="rounded bg-amber-200/70 px-0.5 text-slate-900">{part.text}</mark>
            : <React.Fragment key={i}>{part.text}</React.Fragment>))}
    </>
);

/* Отступ вложенности. На телефоне шаг вдвое меньше: пятый уровень лестницы с
   десктопным шагом оставлял названию треть ширины экрана. Глубина уходит в
   CSS-переменную, чтобы шаг задавала раскладка, а не пересчёт в JS. */
const rowIndent = 'pl-[calc(10px+var(--depth)*13px)] sm:pl-[calc(16px+var(--depth)*22px)]';

/* Быстрые фильтры. Взаимоисключающие: «и публичный, и без доступа» — пустой
   ответ по определению, а не полезный срез, поэтому это выбор одного из, а не
   набор галочек. Тон совпадает с бейджем в строке: нажали жёлтый «Без доступа»
   — на экране остались строки с жёлтым бейджем. */
const FOCUS_CHIPS = [
    { key: 'orphan', label: 'Без доступа', on: 'bg-amber-100 text-amber-800 ring-1 ring-amber-200' },
    { key: 'public', label: 'Публичные', on: 'bg-emerald-100 text-emerald-800 ring-1 ring-emerald-200' },
    { key: 'grant', label: 'Могу выдать доступ', on: 'bg-blue-100 text-blue-800 ring-1 ring-blue-200' },
];

/* Список отделов публичного раздела. Через хелпер, а не напрямую: поле пришло
   позже самого раздела, и у ответа, отданного старым сервером, его нет вовсе —
   а .length по undefined роняет всю вкладку. */
const publicDepts = (x) => x?.public_department_ids || [];

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
const SectionRow = ({ row, department, needles, collapsed, canMove, onToggle, onEdit,
                     onAddChild, onArchive, onAccess, onMove, canManageStructure, busy }) => {
    const { section, depth, childCount, descendants, first, last, context } = row;

    // Ветка отдела и должность внутри неё — разные сущности, и на глаз они
    // должны отличаться так же, как отличаются по смыслу.
    const isBranch = !!section.department_id;
    const orphan = section.visibility_scope !== 'public' && !section.rules_count;

    return (
        <div
            style={{ '--depth': depth }}
            className={`flex items-center gap-2 py-2.5 pr-2 transition hover:bg-slate-50 ${rowIndent} ${
                /* Строка-предок найденного бледнее: она здесь ответом на вопрос
                   «который из двух Операторов», а не сама по себе результатом. */
                context ? 'opacity-55' : ''
            }`}
        >
            {childCount > 0 ? (
                <button
                    type="button"
                    aria-expanded={!collapsed}
                    aria-label={`${collapsed ? 'Раскрыть' : 'Свернуть'} раздел «${section.name}»`}
                    onClick={() => onToggle(section)}
                    className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-slate-400 transition hover:bg-slate-200/70 hover:text-slate-600"
                >
                    <ChevronRight
                        size={13}
                        className={`transition-transform ${collapsed ? '' : 'rotate-90'}`}
                    />
                </button>
            ) : (
                /* Пустое место вместо стрелки: без него названия разделов без
                   подразделов уезжали бы левее соседей по ветке. */
                <span className="h-5 w-5 shrink-0" aria-hidden="true" />
            )}
            {isBranch
                ? <Building2 size={15} className="shrink-0 text-indigo-500" />
                : <FolderTree size={15} className="shrink-0 text-amber-500" />}

            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                    <span className="truncate text-[13.5px] font-medium text-slate-900">
                        <Highlight text={section.name} needles={needles} />
                    </span>
                    {/* Свёрнутая ветка обязана сказать, сколько в ней спрятано:
                        иначе «свернул и забыл» превращается в «раздела нет». */}
                    {collapsed && descendants > 0 && (
                        <IosBadge tone="slate" title={`Внутри ещё ${descendants} — ветка свёрнута`}>
                            +{descendants}
                        </IosBadge>
                    )}
                    {isBranch && (
                        <IosBadge tone="blue" title="Ветка отдела: доступ внутри неё считается по этому отделу">
                            <Building2 size={11} /> {section.department_name || 'отдел'}
                        </IosBadge>
                    )}
                    {/* Бейджа «По правилам» нет: это состояние по умолчанию у
                        всех разделов, и повторять его в каждой строке — шум.
                        Отмечаем только исключение — публичный раздел. */}
                    {section.visibility_scope === 'public' && (
                        publicDepts(section).length > 0 ? (
                            <IosBadge
                                tone="green"
                                title={`Публичный, но только для ${publicDepts(section).length} отд.`}
                            >
                                <Globe size={11} /> Публичный · {publicDepts(section).length} отд.
                            </IosBadge>
                        ) : (
                            <IosBadge tone="green" title="Виден всем сотрудникам без правил">
                                <Globe size={11} /> Публичный
                            </IosBadge>
                        )
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
                       возможность.

                       Правка дерева — не то же самое, что выдача доступа:
                       супервайзер раздаёт операторов, но структуру не трогает,
                       и у него в меню остаётся один пункт. */
                    canManageStructure && { key: 'child', label: 'Добавить подраздел', icon: Plus,
                      onSelect: () => onAddChild(section) },
                    canManageStructure && { key: 'edit', label: 'Изменить раздел', icon: Pencil,
                      onSelect: () => onEdit(section) },
                    /* Порядок разделов задавался только временем создания: поле
                       position в API было, интерфейса к нему не было. Пункта нет
                       на краю ветки (переставлять некуда) и во время поиска —
                       двигать строку относительно соседей, которых на экране
                       нет, значит стрелять вслепую. */
                    canManageStructure && canMove && !first && {
                        key: 'up', label: 'Переместить выше', icon: ArrowUp,
                        separatorBefore: true, onSelect: () => onMove(section, -1) },
                    canManageStructure && canMove && !last && {
                        key: 'down', label: 'Переместить ниже', icon: ArrowDown,
                        separatorBefore: first, onSelect: () => onMove(section, 1) },
                    /* Пункт есть, только если сервер сказал, что этот человек
                       вправе раздавать доступ ИМЕННО ТУТ (can_grant_access):
                       потолок должности и граница отдела считаются там же, где
                       проверяются, а не второй раз на клиенте. */
                    onAccess && section.can_grant_access && {
                        key: 'access', label: 'Кому открыт раздел', icon: KeyRound,
                        // Черта отделяет доступ от правки дерева — но только если
                        // сверху что-то есть: у супервайзера это единственный
                        // пункт, и линия висела бы над ним ни к чему.
                        separatorBefore: canManageStructure,
                        // Число правил прямо в пункте: у раздела без единого
                        // правила это единственное место, где видно, что
                        // открывать его некому.
                        hint: orphan ? 'нет правил' : String(section.rules_count),
                        onSelect: () => onAccess(section),
                    },
                    canManageStructure && { key: 'archive', label: 'Убрать в архив', icon: Archive,
                      danger: true, separatorBefore: true,
                      onSelect: () => onArchive(section) },
                ]}
            />
        </div>
    );
};

export default function WikiStructure({ base, headers, showToast, structure, reload, loading,
                                        canManageAccess = false,
                                        canManageStructure = true }) {
    const [departments, setDepartments] = useState([]);
    const [busy, setBusy] = useState(false);

    // Три вкладки вместо одного длинного списка: пространства правят редко,
    // разделы — постоянно, а архив нужен, только когда что-то возвращают.
    const [tab, setTab] = useState('sections');

    const [spaceModal, setSpaceModal] = useState(null);   // {id?, name, ...}
    const [sectionModal, setSectionModal] = useState(null);
    const [accessSection, setAccessSection] = useState(null);
    const [probeOpen, setProbeOpen] = useState(false);

    // Поиск и быстрый фильтр — состояние текущего захода, а не настройка:
    // запомненный фильтр встретил бы человека урезанным деревом без объяснений.
    // Свёрнутые ветки, наоборот, помним: это про «моя ветка», а не про «сейчас».
    const [query, setQuery] = useState('');
    const [focus, setFocus] = useState(null);
    const prefs = useMemo(readPrefs, []);
    const [collapsed, setCollapsed] = useState(prefs.sections);
    const [closedSpaces, setClosedSpaces] = useState(prefs.spaces);

    useEffect(() => {
        try {
            window.localStorage.setItem(PREFS_KEY, JSON.stringify({
                sections: [...collapsed], spaces: [...closedSpaces],
            }));
        } catch {
            // Приватный режим — молча живём без запомненной свёртки.
        }
    }, [collapsed, closedSpaces]);

    useEffect(() => {
        // Справочник нужен только форме раздела; кто структуру не правит —
        // и форму не открывает, а лишний запрос отвечал бы 403 в консоль.
        if (!canManageStructure) { setDepartments([]); return; }
        axios.get(`${base}/access/subjects`, { headers })
            .then((r) => setDepartments(r.data?.department || []))
            .catch(() => setDepartments([]));   // не админ — справочник недоступен, это норма
    }, [base, headers, canManageStructure]);

    const spaces = structure?.spaces || [];
    const sections = structure?.sections || [];

    const activeSpaces = useMemo(() => spaces.filter((x) => x.status !== 'archived'), [spaces]);
    const archivedSpaces = useMemo(() => spaces.filter((x) => x.status === 'archived'), [spaces]);
    const archivedSections = useMemo(
        () => sections.filter((x) => x.status === 'archived'), [sections]);

    const liveSections = useMemo(
        () => sections.filter((x) => x.status !== 'archived'), [sections]);

    // Дерево строим один раз на изменение списка, а не на каждый рендер строки.
    // Только живые разделы: архивные живут на своей вкладке, а рядом с живым
    // двойником были неотличимы — архивируют обычно как раз дубль с тем же именем.
    const tree = useMemo(() => buildSpaceTree(spaces, sections), [spaces, sections]);

    const needles = useMemo(() => structureNeedles(query), [query]);
    const filtering = needles.length > 0 || !!focus;
    const resetFilters = () => { setQuery(''); setFocus(null); };

    const focusCounts = useMemo(() => ({
        orphan: liveSections.filter(FOCUS_TESTS.orphan).length,
        public: liveSections.filter(FOCUS_TESTS.public).length,
        grant: liveSections.filter(FOCUS_TESTS.grant).length,
    }), [liveSections]);

    /* Фильтр показываем, только если ему есть что отсечь: у администратора вики
       can_grant_access стоит на КАЖДОМ разделе, и кнопка «могу выдать доступ» не
       убрала бы ни одной строки. Активный фильтр остаётся на месте, даже когда
       счётчик обнулился (последний раздел без правил починили), — иначе
       выключить его было бы нечем, а дерево осталось бы пустым. */
    const chips = FOCUS_CHIPS.filter(({ key }) => focus === key || (
        focusCounts[key] > 0
        && (key !== 'grant' || focusCounts.grant < liveSections.length)
    ));

    /* Что рисуем на вкладке «Разделы». Пространство, где ничего не нашлось,
       во время поиска исчезает целиком: пять карточек «тут пусто» — это не
       ответ на вопрос, а пять способов его не дать. */
    const spaceViews = useMemo(() => activeSpaces.map((space) => {
        const all = tree.get(space.id) || [];
        const found = filterRows(all, { needles, focus, spaceName: space.name });
        return {
            space,
            total: all.length,
            hits: found.filter((row) => row.matched).length,
            // Во время поиска свёртку не применяем: искать в свёрнутом дереве
            // бессмысленно, найденное всё равно пришлось бы раскрывать руками.
            rows: filtering ? found : collapseRows(found, collapsed),
        };
    }).filter(({ hits }) => !filtering || hits > 0),
    [activeSpaces, tree, needles, focus, filtering, collapsed]);

    const collapsibleIds = useMemo(() => {
        const out = [];
        tree.forEach((rows) => rows.forEach((row) => {
            if (row.childCount > 0) out.push(row.section.id);
        }));
        return out;
    }, [tree]);
    const anyCollapsed = collapsibleIds.some((id) => collapsed.has(id));

    const spaceName = useMemo(
        () => new Map(spaces.map((sp) => [sp.id, sp.name])), [spaces]);

    /* Из какого пространства раздел уезжает. Держим не в состоянии модалки, а
       выводим из списка: состояние модалки правится на каждый ввод, и копия
       исходного значения в нём разошлась бы с деревом после reload. */
    const originalSpaceId = sectionModal?.id
        ? sections.find((x) => x.id === sectionModal.id)?.space_id
        : null;
    const movingSection = !!originalSpaceId
        && Number(sectionModal?.space_id) !== Number(originalSpaceId);

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
            // Тем же порядком: пустой список означает «виден всем», и он обязан
            // доехать до сервера, иначе снятие отделов не сохранится.
            public_department_ids: sectionModal.visibility_scope === 'public'
                ? publicDepts(sectionModal)
                : [],
        };
        setBusy(true);
        const request = sectionModal.id
            ? axios.patch(`${base}/sections/${sectionModal.id}`, payload, { headers })
            : axios.post(`${base}/sections`, payload, { headers });
        request
            .then(() => {
                showToast?.(
                    // Переезд называем переездом: «обновлён» на перенесённой
                    // ветке не даёт понять, случилось ли главное.
                    movingSection ? 'Раздел перенесён в другое пространство'
                        : sectionModal.id ? 'Раздел обновлён' : 'Раздел создан',
                    'success');
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

    /* Порядок внутри ветки. Сервер хранит его в position, и API для него был с
       самого начала — не было интерфейса, поэтому разделы стояли в том порядке,
       в каком их когда-то завели. Меняем значения position у двух соседей, по
       запросу на каждого; успех молчит намеренно — строка на глазах уехала
       выше, и тост на каждое нажатие был бы шумом поверх очевидного. */
    const moveSection = (section, direction) => {
        const patches = reorderPatches(sectionSiblings(sections, section), section.id, direction);
        if (!patches.length) return;
        setBusy(true);
        Promise.all(patches.map((patch) => axios.patch(
            `${base}/sections/${patch.id}`, { position: patch.position }, { headers })))
            .then(() => reload())
            .catch((e) => showToast?.(errText(e, 'Не удалось изменить порядок'), 'error'))
            .finally(() => setBusy(false));
    };

    const moveSpace = (space, direction) => {
        const patches = reorderPatches(activeSpaces, space.id, direction);
        if (!patches.length) return;
        setBusy(true);
        Promise.all(patches.map((patch) => axios.patch(
            `${base}/spaces/${patch.id}`, { position: patch.position }, { headers })))
            .then(() => reload())
            .catch((e) => showToast?.(errText(e, 'Не удалось изменить порядок'), 'error'))
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
                    {tab === 'spaces' && canManageStructure && (
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
                    // Супервайзеру видны только «Разделы»: пространства и архив —
                    // это правка дерева, которой у него нет.
                    canManageStructure && { key: 'spaces', label: 'Пространства', icon: Layers,
                      count: activeSpaces.length },
                    { key: 'sections', label: 'Разделы', icon: FolderTree,
                      count: sections.filter((x) => x.status !== 'archived').length },
                    canManageStructure && { key: 'archive', label: 'Архив', icon: Archive,
                      count: archivedSpaces.length + archivedSections.length },
                ].filter(Boolean).map(({ key, label, icon: Icon, count }) => (
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

            {/* Панель дерева — только на «Разделах»: у пространств пять строк,
                искать в них нечего, а архив открывают раз в месяц и глазами. */}
            {tab === 'sections' && spaces.length > 0 && (
                <div className="space-y-2">
                    <div className="flex flex-wrap items-center gap-2">
                        <div className="relative min-w-[200px] flex-1">
                            <Search size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-slate-400" />
                            <input
                                className={`${iosInput} pl-10 ${query ? 'pr-10' : ''}`}
                                value={query}
                                onChange={(e) => setQuery(e.target.value)}
                                placeholder="Раздел, отдел или пространство"
                                aria-label="Поиск по структуре"
                            />
                            {query && (
                                <button
                                    type="button"
                                    aria-label="Очистить поиск"
                                    onClick={() => setQuery('')}
                                    className="absolute right-2 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-full text-slate-400 transition hover:bg-slate-200/70 hover:text-slate-600"
                                >
                                    <X size={14} />
                                </button>
                            )}
                        </div>
                        {/* Кнопка есть, только когда есть что сворачивать. */}
                        {!filtering && collapsibleIds.length > 0 && (
                            <button
                                type="button"
                                className={iosBtnGhost}
                                onClick={() => setCollapsed(
                                    anyCollapsed ? new Set() : new Set(collapsibleIds))}
                            >
                                {anyCollapsed
                                    ? <><UnfoldVertical size={14} /> Развернуть всё</>
                                    : <><FoldVertical size={14} /> Свернуть всё</>}
                            </button>
                        )}
                    </div>

                    {chips.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                            {chips.map(({ key, label, on }) => (
                                <button
                                    key={key}
                                    type="button"
                                    aria-pressed={focus === key}
                                    onClick={() => setFocus(focus === key ? null : key)}
                                    className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] font-medium transition ${
                                        focus === key ? on : 'bg-slate-100 text-slate-600 hover:bg-slate-200/70'
                                    }`}
                                >
                                    {label}
                                    <span className="tabular-nums opacity-60">{focusCounts[key]}</span>
                                </button>
                            ))}
                        </div>
                    )}
                </div>
            )}

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
            {!loading && tab === 'spaces' && activeSpaces.map((space, index) => (
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
                                {(tree.get(space.id) || []).length} разделов
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
                                  department_id: '', public_department_ids: [],
                              }) },
                            { key: 'edit', label: 'Изменить пространство', icon: Pencil,
                              onSelect: () => setSpaceModal({
                                  id: space.id, name: space.name,
                                  description: space.description || '',
                                  department_id: space.department_id ? String(space.department_id) : '',
                              }) },
                            /* Порядок пространств — тот же разговор, что и у
                               разделов: position сервер хранил, менять его было
                               нечем. Пункт исчезает на краю списка. */
                            index > 0 && { key: 'up', label: 'Переместить выше', icon: ArrowUp,
                              separatorBefore: true, onSelect: () => moveSpace(space, -1) },
                            index < activeSpaces.length - 1 && {
                              key: 'down', label: 'Переместить ниже', icon: ArrowDown,
                              separatorBefore: index === 0, onSelect: () => moveSpace(space, 1) },
                            { key: 'archive', label: 'Убрать в архив', icon: Archive,
                              danger: true, separatorBefore: true,
                              onSelect: () => archiveSpace(space) },
                        ]}
                    />
                </div>
            ))}

            {/* ── Вкладка «Разделы» ── */}
            {!loading && tab === 'sections' && spaceViews.map(({ space, rows, total, hits }) => {
                const spaceClosed = !filtering && closedSpaces.has(space.id);
                return (
                    <section key={space.id} className="space-y-1.5">
                        <div className="flex flex-wrap items-center justify-between gap-2 px-1">
                            {/* Во время поиска шапка — не кнопка: свернуть пространство
                                с найденным нельзя (иначе результат исчезнет), а нажатие
                                без ответа — тот же молчаливый отказ, только тихий. */}
                            <button
                                type="button"
                                disabled={filtering}
                                aria-expanded={!spaceClosed}
                                onClick={() => setClosedSpaces((prev) => toggled(prev, space.id))}
                                className={`flex min-w-0 flex-1 items-center gap-2 rounded-xl py-1 pr-2 text-left transition ${
                                    filtering ? 'cursor-default' : 'hover:bg-slate-100/70'}`}
                            >
                                {!filtering && (
                                    <ChevronRight
                                        size={14}
                                        className={`shrink-0 text-slate-400 transition-transform ${spaceClosed ? '' : 'rotate-90'}`}
                                    />
                                )}
                                <Layers size={15} className="shrink-0 text-indigo-500" />
                                <span className="truncate text-[14px] font-semibold text-slate-900">
                                    <Highlight text={space.name} needles={needles} />
                                </span>
                                {space.department_name && (
                                    <IosBadge tone="slate">{space.department_name}</IosBadge>
                                )}
                                {/* При поиске — «сколько из скольких»: без второго
                                    числа непонятно, весь ли это список. */}
                                <span className="shrink-0 text-[11.5px] tabular-nums text-slate-400">
                                    {filtering ? `${hits} из ${total}` : total}
                                </span>
                            </button>
                            {canManageStructure && (
                                <button
                                    type="button"
                                    className={iosBtnGhost}
                                    onClick={() => setSectionModal({
                                        space_id: space.id, name: '', description: '',
                                        visibility_scope: 'restricted', parent_section_id: '',
                                        department_id: '', public_department_ids: [],
                                    })}
                                >
                                    <Plus size={13} /> Раздел
                                </button>
                            )}
                        </div>

                        {!spaceClosed && (
                            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                {rows.length === 0 && (
                                    <div className="px-4 py-8 text-center text-[13px] text-slate-400">
                                        В пространстве пока нет разделов
                                    </div>
                                )}
                                {rows.map((row) => (
                                    <SectionRow
                                        key={row.section.id}
                                        row={row}
                                        busy={busy}
                                        needles={needles}
                                        collapsed={collapsed.has(row.section.id)}
                                        canMove={!filtering}
                                        onToggle={(x) => setCollapsed((prev) => toggled(prev, x.id))}
                                        department={branchDepartment(sections, row.section.id)}
                                        onEdit={(x) => setSectionModal({
                                            id: x.id, space_id: x.space_id, name: x.name,
                                            description: x.description || '',
                                            visibility_scope: x.visibility_scope,
                                            parent_section_id: x.parent_section_id ? String(x.parent_section_id) : '',
                                            department_id: x.department_id ? String(x.department_id) : '',
                                            public_department_ids: x.public_department_ids || [],
                                        })}
                                        onAddChild={(x) => setSectionModal({
                                            space_id: x.space_id, name: '', description: '',
                                            visibility_scope: 'restricted',
                                            parent_section_id: String(x.id),
                                            department_id: '', public_department_ids: [],
                                        })}
                                        onArchive={archiveSection}
                                        onAccess={setAccessSection}
                                        onMove={moveSection}
                                        canManageStructure={canManageStructure}
                                    />
                                ))}
                            </div>
                        )}
                    </section>
                );
            })}

            {/* Пусто по фильтру — не то же самое, что пустая структура: звать
                «создайте пространство» здесь было бы ответом не на тот вопрос. */}
            {!loading && tab === 'sections' && filtering && spaceViews.length === 0 && (
                <div className={`${iosCard} flex flex-col items-center gap-2 px-6 py-14 text-center`}>
                    <div className="grid h-12 w-12 place-items-center rounded-2xl bg-slate-100 text-slate-400">
                        <Search size={22} />
                    </div>
                    <div className="text-[15px] font-semibold text-slate-900">Ничего не нашлось</div>
                    <p className="max-w-sm text-[13px] leading-relaxed text-slate-500">
                        {query
                            ? `По запросу «${query}» разделов нет.`
                            : 'Под это условие не подходит ни один раздел.'}
                    </p>
                    <button type="button" className={iosBtnSecondary} onClick={resetFilters}>
                        Показать все разделы
                    </button>
                </div>
            )}

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
                        {/* Пространство. Раньше поля не было вовсе: раздел,
                            созданный не там, приходилось заводить заново
                            вручную вместе со всеми подразделами и правилами. */}
                        <div>
                            <label className="mb-1 block px-1 text-[12px] font-medium text-slate-500">
                                Пространство
                            </label>
                            <CustomSelect
                                variant="ios"
                                value={String(sectionModal.space_id || '')}
                                onChange={(v) => setSectionModal({
                                    ...sectionModal,
                                    space_id: Number(v),
                                    /* Родитель остался в прежнем дереве — в новом
                                       пространстве его нет. Сбрасываем в корень,
                                       иначе в поле висит имя раздела, которого в
                                       выбранном пространстве не существует. */
                                    parent_section_id: '',
                                })}
                                options={activeSpaces.map((sp) => ({
                                    value: String(sp.id), label: sp.name,
                                }))}
                                ariaLabel="Пространство раздела"
                            />
                            {sectionModal.id && movingSection && (
                                <p className="mt-1 px-1 text-[11.5px] leading-relaxed text-amber-600">
                                    Раздел переедет из пространства «{spaceName.get(originalSpaceId) || '—'}»
                                    вместе со всеми подразделами. Права и статьи остаются при нём.
                                </p>
                            )}
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

                        <div className={`${iosCard} p-3.5`}>
                            <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0">
                                    <div className="text-[13.5px] font-medium text-slate-900">
                                        Публичный раздел
                                    </div>
                                    <p className="mt-0.5 text-[11.5px] leading-relaxed text-slate-500">
                                        Виден без единого правила доступа. Для служебной
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

                            {/* Кому именно виден. «Публичный» раньше означало
                                «всем в компании» без вариантов, и «Общий
                                сотрудник» открывался в том числе отделам, кому
                                вики не предназначена. Пустой список сохраняет
                                прежний смысл — виден всем. */}
                            {sectionModal.visibility_scope === 'public' && (
                                <div className="mt-3 border-t border-slate-100 pt-3">
                                    <div className="flex gap-1 rounded-xl bg-slate-100 p-1">
                                        {[
                                            { key: 'all', label: 'Всем отделам' },
                                            { key: 'some', label: 'Выбранным' },
                                        ].map(({ key, label }) => {
                                            const active = key === 'some'
                                                ? publicDepts(sectionModal).length > 0
                                                : publicDepts(sectionModal).length === 0;
                                            return (
                                                <button
                                                    key={key}
                                                    type="button"
                                                    onClick={() => setSectionModal({
                                                        ...sectionModal,
                                                        // «Выбранным» без выбора — это «всем», поэтому
                                                        // при переключении отмечаем все отделы разом:
                                                        // дальше человек снимает лишние.
                                                        public_department_ids: key === 'all'
                                                            ? []
                                                            : departments.map((d) => d.id),
                                                    })}
                                                    className={`flex-1 whitespace-nowrap rounded-lg px-2 py-1.5 text-[12.5px] font-medium transition ${
                                                        active
                                                            ? 'bg-white text-slate-900 shadow-sm'
                                                            : 'text-slate-500 hover:text-slate-700'
                                                    }`}
                                                >
                                                    {label}
                                                </button>
                                            );
                                        })}
                                    </div>

                                    {publicDepts(sectionModal).length > 0 && (
                                        <div className="mt-2 divide-y divide-slate-100 rounded-xl bg-slate-50">
                                            {departments.map((d) => {
                                                const on = publicDepts(sectionModal).includes(d.id);
                                                return (
                                                    <label
                                                        key={d.id}
                                                        className="flex cursor-pointer items-center justify-between gap-3 px-3 py-2"
                                                    >
                                                        <span className="min-w-0 flex-1 truncate text-[13px] text-slate-800">
                                                            {d.name}
                                                        </span>
                                                        <IosToggle
                                                            checked={on}
                                                            onChange={(v) => setSectionModal({
                                                                ...sectionModal,
                                                                public_department_ids: v
                                                                    ? [...publicDepts(sectionModal), d.id]
                                                                    : publicDepts(sectionModal)
                                                                        .filter((x) => x !== d.id),
                                                            })}
                                                        />
                                                    </label>
                                                );
                                            })}
                                        </div>
                                    )}

                                    <p className="mt-1.5 px-1 text-[11.5px] leading-relaxed text-slate-400">
                                        {publicDepts(sectionModal).length === 0
                                            ? 'Раздел увидят сотрудники всех отделов.'
                                            : `Раздел увидят только отмеченные отделы (${publicDepts(sectionModal).length}). Остальным его не будет видно вовсе.`}
                                    </p>
                                </div>
                            )}
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
