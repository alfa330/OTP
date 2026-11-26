import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { ChevronDown, ChevronRight, ChevronUp, Plus, X } from 'lucide-react';
import {
  IosMenu,
  IosModal,
  IosSegmented,
  iosBtnPrimary,
  iosBtnSecondary,
  iosCard,
  iosGroupLabel,
  iosInput,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import InfoHint, { InfoHintLines } from '../common/InfoHint';
import {
  PROJECT_CODE_RE,
  boardMembersIssue,
  orderBoardMembers,
  pluralRu,
  projectCodeIssue,
  projectListFacts,
  withBoardRoles,
} from './taskProjects';

/*
 * Окно «Проекты» раздела «Задачи» (задача #401): доски отделов, их ключи и кто
 * их видит. Открывает его тот, кто в разделе и так видит всё (админ, не
 * возглавляющий отдел, и супер-админ), — сервер проверяет это сам.
 *
 * Список → проект. Состав доски — одна лента людей, у каждого роль сегментом
 * (владелец 26.11.2026: «у каждой доски свои исполнители, их можно выбирать, а
 * остальные как участники»):
 *   Исполнитель — ему ставят задачи доски; в форме задачи выбирают только их;
 *   Участник    — видит задачи доски и ставит их, но исполнителем не выбирается.
 * Новый в составе — участник: исполнителей выбирают осознанно. 24.11.2026 они
 * были отдельным списком под составом — его убрали («отдельно выбирать не
 * нужно»); теперь роль живёт в той же строке, что и человек.
 * Видимость — отдельно от роли, в меню «···» строки: «видит все задачи доски»
 * (уровень member; сотрудник маркетинга — кроме задач своего руководителя, где сам
 * не участвует: решает сервер) или «только свои» (уровень board — свои и те, где
 * он наблюдатель). На проде на 24.11.2026 «только свои» не стояло ни у кого, поэтому
 * в строке — роль, а редкая отметка видимости — в меню и в подписи под именем.
 * Разделы доски («регулируемый набор разделов» из постановки) — свой список у
 * каждой доски: переименовать на месте, переставить стрелками, убрать.
 * Сохраняется целиком одним запросом: состав и разделы уходят полными списками.
 * Ключ доски можно сменить (владелец 06.11.2026): номера задач остаются, прежние
 * ключи продолжают открывать задачи — сервер держит их в task_key_aliases.
 */

const STATE_OPTIONS = [
  { value: 'active', label: 'Активна' },
  { value: 'archived', label: 'В архиве' },
];

let sectionDraftSeq = 0;
// Ключ строки раздела в черновике: у нового раздела id ещё нет.
const sectionDraftKey = () => `new-${++sectionDraftSeq}`;

const ROLE_EXECUTOR = 'executor';
const ROLE_PARTICIPANT = 'participant';
const ROLE_OPTIONS = [
  { value: ROLE_EXECUTOR, label: 'Исполнитель' },
  { value: ROLE_PARTICIPANT, label: 'Участник' },
];

/* Под «i» у состава — зачем роль и по строке на вариант (правило владельца для
   выбора сегментами), плюс отметка видимости из меню строки. */
const MEMBERS_HINT_LINES = [
  'Исполнитель — ему ставят задачи доски. При постановке задачи исполнителя выбирают только из них: всех сразу или конкретных людей.',
  'Участник — видит задачи доски и может их ставить, но исполнителем его не выбрать.',
  'Только свои задачи — отметка в меню «···» у человека: на доске он видит лишь задачи, где он постановщик, исполнитель или наблюдатель. Без неё видны все задачи доски, а сотруднику маркетинга — кроме задач своего руководителя, где он сам не участвует.',
  'Брать, сдавать и принимать задачу по-прежнему могут только её исполнители и поручитель.',
];

const errorText = (error, fallback) => error?.response?.data?.error || fallback;

const initials = (name) => String(name || '?')
  .split(/\s+/)
  .filter(Boolean)
  .slice(0, 2)
  .map((part) => part[0])
  .join('')
  .toUpperCase();

/* Состав в черновике — исполнители сверху (orderBoardMembers): порядок ставится
   один раз, при открытии, и смена роли строку с места не двигает. Роли —
   withBoardRoles: старый сервер их не присылает, и тогда исполнители все, как у него. */
const draftMembersOf = (project) => orderBoardMembers(withBoardRoles(project?.members)
  .map((person) => ({
    id: Number(person.id),
    name: person.name,
    role: person.role,
    department: person.department,
    access_level: person.access_level === 'board' ? 'board' : 'member',
    is_executor: person.is_executor,
  })));

const draftOf = (project) => ({
  id: project?.id ?? null,
  code: project?.code || '',
  // Ключ, с которым доску открыли: по нему видно, что ключ сменили, и им же
  // подписано окно, пока новый ещё набирают.
  originalCode: project?.code || '',
  name: project?.name || '',
  departmentId: project?.department_id ? String(project.department_id) : '',
  isArchived: Boolean(project?.is_archived),
  members: draftMembersOf(project),
  sections: (Array.isArray(project?.sections) ? project.sections : []).map((section) => ({
    key: `id-${section.id}`,
    id: Number(section.id),
    name: section.name || '',
    taskCount: Number(section.task_count || 0),
  })),
});

/* Что не так с разделами: пустое название или повтор (без учёта регистра). */
const sectionsIssue = (sections) => {
  const seen = new Set();
  for (const section of sections || []) {
    const name = String(section.name || '').trim();
    if (!name) return 'У каждого раздела должно быть название';
    const key = name.toLocaleLowerCase('ru');
    if (seen.has(key)) return `Раздел «${name}» уже есть`;
    seen.add(key);
  }
  return '';
};

/* Строка с ошибкой — подпись поля красным, а не серая кнопка «Сохранить»:
   человек видит, чего не хватает, у самого поля. */
const Field = ({ label, hint, issue, children }) => (
  <div className="space-y-1.5">
    <span className="flex items-center gap-1.5 px-1 text-[12px] font-medium text-slate-600">
      {label}
      {hint && <InfoHint side="left" text={hint} />}
    </span>
    {children}
    {issue && <span className="block px-1 text-[12px] text-rose-600">{issue}</span>}
  </div>
);

const ProjectList = ({ projects, onOpen, onCreate }) => (
  <div className="space-y-3">
    <div className="flex items-center justify-end">
      <button type="button" className={`${iosBtnSecondary} shrink-0 px-3 py-2`} onClick={onCreate}>
        <Plus size={15} strokeWidth={2.2} /> Новый проект
      </button>
    </div>
    <ul className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
      {projects.map((project) => {
        // Люди есть, а исполнителей нет — главное о доске: задачи ей не поставить.
        const { facts, missingExecutors } = projectListFacts(project);
        return (
          <li key={project.id}>
            <button
              type="button"
              onClick={() => onOpen(project)}
              className="flex w-full items-center gap-3 px-3.5 py-2.5 text-left transition hover:bg-slate-50"
            >
              <span className="w-14 shrink-0 text-[12.5px] font-semibold tabular-nums text-slate-500">{project.code}</span>
              <span className="min-w-0 grow basis-0">
                <span className={`block truncate text-[13.5px] font-semibold ${project.is_archived ? 'text-slate-400' : 'text-slate-800'}`}>
                  {project.name}
                </span>
                <span className="block truncate text-[12px] text-slate-500">
                  {facts.join(' · ')}
                  {missingExecutors && <span className="text-rose-600"> · нет исполнителей</span>}
                </span>
              </span>
              {project.is_archived && <span className="shrink-0 text-[11.5px] font-medium text-slate-400">Архив</span>}
              <ChevronRight size={16} className="shrink-0 text-slate-300" />
            </button>
          </li>
        );
      })}
    </ul>
  </div>
);

/* Строка человека в составе: роль — сегментом прямо в строке (её и выбирают),
   видимость и «Убрать с доски» — в меню «···»: отметка «только свои» редкая, а
   постоянный второй переключатель в каждой строке был бы шумом. Видна она и так —
   в подписи под именем. На узком экране роль встаёт под имя, иначе имени не
   остаётся места. */
const MemberRow = ({ person, roleLabels, onRole, onLevel, onRemove, disabled }) => {
  const ownOnly = person.access_level === 'board';
  // «Только свои» — сразу за должностью: длинное название отдела обрезается,
  // а отметка видимости при этом оставалась бы за многоточием.
  const facts = [
    roleLabels?.[person.role],
    ownOnly ? 'видит только свои задачи' : '',
    person.department,
  ].filter(Boolean);
  return (
    <li className="tb-member flex items-center gap-3 px-3.5 py-2">
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-500">
        {initials(person.name)}
      </span>
      <div className="min-w-0 grow basis-0 sm:flex sm:items-center sm:gap-3">
        <span className="block min-w-0 sm:grow sm:basis-0">
          <span className="block truncate text-[13.5px] font-medium text-slate-800">{person.name}</span>
          <span className="block truncate text-[12px] text-slate-500">{facts.join(' · ') || '—'}</span>
        </span>
        <span className="mt-1.5 flex sm:mt-0 sm:shrink-0">
          <IosSegmented
            size="xs"
            value={person.is_executor ? ROLE_EXECUTOR : ROLE_PARTICIPANT}
            options={ROLE_OPTIONS}
            onChange={(role) => { if (!disabled) onRole(person.id, role === ROLE_EXECUTOR); }}
            ariaLabel={`Роль на доске: ${person.name}`}
          />
        </span>
      </div>
      <IosMenu
        label={`Ещё: ${person.name}`}
        disabled={disabled}
        items={[
          {
            key: 'all',
            label: 'Видит все задачи доски',
            checked: !ownOnly,
            onSelect: () => onLevel(person.id, 'member'),
          },
          {
            key: 'own',
            label: 'Видит только свои задачи',
            checked: ownOnly,
            onSelect: () => onLevel(person.id, 'board'),
          },
          {
            key: 'remove',
            label: 'Убрать с доски',
            icon: X,
            danger: true,
            separatorBefore: true,
            onSelect: () => onRemove(person.id),
          },
        ]}
      />
    </li>
  );
};

const SectionRow = ({ section, index, total, onRename, onMove, onRemove, disabled }) => (
  <li className="flex items-center gap-1.5 px-2.5 py-1">
    <input
      className="min-w-0 grow rounded-lg bg-transparent px-2 py-2 text-[13.5px] text-slate-800 outline-none transition focus:bg-slate-100"
      value={section.name}
      maxLength={80}
      disabled={disabled}
      aria-label="Название раздела"
      onChange={(event) => onRename(section.key, event.target.value)}
    />
    {section.taskCount > 0 && (
      <span className="shrink-0 text-[12px] tabular-nums text-slate-400">
        {section.taskCount} {pluralRu(section.taskCount, 'задача', 'задачи', 'задач')}
      </span>
    )}
    <button
      type="button"
      onClick={() => onMove(index, -1)}
      disabled={disabled || index === 0}
      className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30"
      aria-label={`Выше: ${section.name}`}
      title="Выше"
    >
      <ChevronUp size={15} />
    </button>
    <button
      type="button"
      onClick={() => onMove(index, 1)}
      disabled={disabled || index === total - 1}
      className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700 disabled:opacity-30"
      aria-label={`Ниже: ${section.name}`}
      title="Ниже"
    >
      <ChevronDown size={15} />
    </button>
    <button
      type="button"
      onClick={() => onRemove(section.key)}
      disabled={disabled}
      className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
      aria-label={`Убрать раздел: ${section.name}`}
      title="Убрать раздел — его задачи останутся на доске без раздела"
    >
      <X size={15} />
    </button>
  </li>
);

/* Подпись кандидата в «Добавить участника»: должность, а у рядового без своей
   должности (специалист IT заведён 'operator') — отдел, как в строке состава
   (MemberRow). Иначе в списке, где у всех стоит «(HR)» или «(СВ)», он один
   оставался бы без подписи. */
const candidateLabel = (person, roleLabels) => {
  const tag = roleLabels?.[person.role] || person.department;
  return tag ? `${person.name} (${tag})` : person.name;
};

const ProjectEditor = ({ draft, setDraft, isCreate, issues, people, roleLabels, departments, saving }) => {
  const [newSection, setNewSection] = useState('');
  const memberIds = useMemo(() => new Set(draft.members.map((person) => Number(person.id))), [draft.members]);
  const peopleById = useMemo(() => {
    const map = new Map();
    (people || []).forEach((person) => map.set(Number(person.id), person));
    return map;
  }, [people]);
  const addOptions = useMemo(() => (people || [])
    .filter((person) => !memberIds.has(Number(person.id)))
    .map((person) => ({
      value: String(person.id),
      label: candidateLabel(person, roleLabels),
    })), [people, memberIds, roleLabels]);
  const departmentOptions = useMemo(() => [
    { value: '', label: 'Без отдела' },
    ...(departments || [])
      .map((department) => ({ value: String(department.id), label: department.name }))
      .sort((left, right) => left.label.localeCompare(right.label, 'ru')),
  ], [departments]);

  const patch = (next) => setDraft((prev) => ({ ...prev, ...next }));
  /* Новый в составе — участник: исполнителей выбирают сами («их можно будет
     выбирать, а остальные как участники»), иначе случайно добавленный попал бы
     в «Все» при постановке задачи. */
  const newMember = (person) => ({
    id: Number(person.id),
    name: person.name,
    role: person.role,
    department: person.department || '',
    access_level: 'member',
    is_executor: false,
  });
  const addMember = (value) => {
    const person = peopleById.get(Number(value));
    if (!person || memberIds.has(Number(person.id))) return;
    patch({ members: [...draft.members, newMember(person)] });
  };
  const updateMember = (personId, changes) => patch({
    members: draft.members.map((person) => (person.id === personId ? { ...person, ...changes } : person)),
  });
  const setLevel = (personId, level) => updateMember(personId, { access_level: level });
  const setRole = (personId, isExecutor) => updateMember(personId, { is_executor: isExecutor });
  const removeMember = (personId) => patch({ members: draft.members.filter((person) => person.id !== personId) });

  /* Сотрудники отдела доски, которых ещё нет в составе: одной кнопкой — участниками.
     Отдел знают только кандидаты с сервера (у списка получателей его нет). */
  const departmentStaff = useMemo(() => (draft.departmentId
    ? (people || []).filter((person) => String(person.department_id ?? '') === String(draft.departmentId)
      && !memberIds.has(Number(person.id)))
    : []), [draft.departmentId, people, memberIds]);
  const addDepartmentStaff = () => patch({
    members: [...draft.members, ...departmentStaff.map(newMember)],
  });

  const renameSection = (key, name) => patch({
    sections: draft.sections.map((section) => (section.key === key ? { ...section, name } : section)),
  });
  const moveSection = (index, delta) => {
    const next = draft.sections.slice();
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    patch({ sections: next });
  };
  const removeSection = (key) => patch({ sections: draft.sections.filter((section) => section.key !== key) });
  const addSection = () => {
    const name = newSection.trim();
    if (!name) return;
    patch({ sections: [...draft.sections, { key: sectionDraftKey(), id: null, name, taskCount: 0 }] });
    setNewSection('');
  };

  return (
    <div className="space-y-5">
      <section className="space-y-1.5">
        <div className={iosGroupLabel}>Проект</div>
        <div className={`${iosCard} space-y-3 p-4`}>
          <Field label="Название" issue={issues.name}>
            <input
              className={iosInput}
              value={draft.name}
              maxLength={120}
              placeholder="Например, HR отдел"
              disabled={saving}
              onChange={(event) => patch({ name: event.target.value })}
            />
          </Field>
          <Field
            label="Ключ"
            hint={isCreate
              ? 'Латиница и цифры, первой буквой, от 2 до 10 знаков. Попадает в номер задачи и в ссылку: HR-125.'
              : 'Латиница и цифры, первой буквой, от 2 до 10 знаков. Номера задач останутся — сменится только ключ перед ними. Старые ключи и ссылки на задачи продолжат открываться.'}
            issue={issues.code}
          >
            <input
              className={`${iosInput} uppercase tabular-nums`}
              value={draft.code}
              maxLength={10}
              placeholder="HR"
              aria-label="Ключ проекта"
              disabled={saving}
              onChange={(event) => patch({ code: event.target.value.toUpperCase().replace(/[^A-Z0-9]/g, '') })}
            />
            {/* Сменили ключ — показываем, что станет с номерами, до сохранения. */}
            {!isCreate && draft.code !== draft.originalCode && PROJECT_CODE_RE.test(draft.code) && !issues.code && (
              <span className="tb-code-rename block px-1 text-[12px] leading-snug text-slate-500">
                {draft.originalCode}-12 станет {draft.code}-12. Старые ключи и ссылки продолжат открывать задачи.
              </span>
            )}
          </Field>
          <Field
            label="Отдел"
            hint="Сотрудникам отдела эта доска откроется по умолчанию. Кто видит задачи, решает состав ниже, а не отдел."
          >
            <CustomSelect
              variant="ios"
              ariaLabel="Отдел проекта"
              value={draft.departmentId}
              options={departmentOptions}
              onChange={(value) => patch({ departmentId: String(value ?? '') })}
              disabled={saving}
              searchable={departmentOptions.length > 8}
              searchPlaceholder="Найти отдел…"
            />
          </Field>
        </div>
      </section>

      <section className="space-y-1.5" data-project-issue={issues.members ? 'members' : undefined}>
        <div className="flex items-center gap-1.5">
          <span className={iosGroupLabel}>Состав доски</span>
          <InfoHint side="left">
            <InfoHintLines lines={MEMBERS_HINT_LINES} />
          </InfoHint>
        </div>
        <div className={`${iosCard} overflow-hidden`}>
          <div className="p-3">
            <CustomSelect
              variant="ios"
              ariaLabel="Добавить сотрудника на доску"
              value=""
              options={addOptions}
              onChange={addMember}
              disabled={saving || !addOptions.length}
              placeholder="Добавить сотрудника…"
              searchable
              searchPlaceholder="Поиск по имени…"
            />
            {departmentStaff.length > 0 && (
              <button
                type="button"
                onClick={addDepartmentStaff}
                disabled={saving}
                className="mt-2 inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-[13px] font-medium text-[#2563eb] transition hover:bg-[#eef4ff] active:scale-[0.98]"
              >
                <Plus size={14} strokeWidth={2.2} />
                Добавить сотрудников отдела ({departmentStaff.length})
              </button>
            )}
          </div>
          {draft.members.length > 0 ? (
            <ul className="divide-y divide-slate-100 border-t border-slate-100">
              {draft.members.map((person) => (
                <MemberRow
                  key={person.id}
                  person={{ ...person, department: person.department || peopleById.get(person.id)?.department || '' }}
                  roleLabels={roleLabels}
                  onRole={setRole}
                  onLevel={setLevel}
                  onRemove={removeMember}
                  disabled={saving}
                />
              ))}
            </ul>
          ) : (
            <p className="border-t border-slate-100 px-4 py-6 text-center text-[12.5px] text-slate-500">
              Пока людей нет — задачу этой доски поручают как раньше, а видят её только постановщик, исполнители и админы раздела.
            </p>
          )}
        </div>
        {issues.members && <span className="block px-1 text-[12px] text-rose-600">{issues.members}</span>}
      </section>

      <section className="space-y-1.5">
        <div className="flex items-center gap-1.5">
          <span className={iosGroupLabel}>Разделы доски</span>
          <InfoHint
            side="left"
            text="Группы задач внутри доски — например «Подбор» и «Адаптация». При постановке задачи выбирают раздел, доску можно отфильтровать по разделу. Колонки доски от этого не меняются: это по-прежнему статусы задачи. Уберёте раздел — его задачи останутся на доске без раздела."
          />
        </div>
        <div className={`${iosCard} overflow-hidden`}>
          {draft.sections.length > 0 && (
            <ul className="divide-y divide-slate-100">
              {draft.sections.map((section, index) => (
                <SectionRow
                  key={section.key}
                  section={section}
                  index={index}
                  total={draft.sections.length}
                  onRename={renameSection}
                  onMove={moveSection}
                  onRemove={removeSection}
                  disabled={saving}
                />
              ))}
            </ul>
          )}
          <div className={`flex items-center gap-2 p-3${draft.sections.length ? ' border-t border-slate-100' : ''}`}>
            <input
              className={iosInput}
              value={newSection}
              maxLength={80}
              placeholder={draft.sections.length ? 'Ещё раздел' : 'Первый раздел, например «Подбор»'}
              disabled={saving}
              onChange={(event) => setNewSection(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault();
                  addSection();
                }
              }}
            />
            <button
              type="button"
              className={`${iosBtnSecondary} shrink-0 px-3`}
              onClick={addSection}
              disabled={saving || !newSection.trim()}
            >
              Добавить
            </button>
          </div>
        </div>
        {issues.sections && <span className="block px-1 text-[12px] text-rose-600">{issues.sections}</span>}
      </section>

      {!isCreate && (
        <section className={`${iosCard} flex items-center justify-between gap-3 px-4 py-3`}>
          <span className="flex items-center gap-1.5 text-[13.5px] font-medium text-slate-800">
            Доска
            <InfoHint
              side="left"
              text="Активна — в доску ставят задачи. В архиве — новые задачи вручную не поставить; уже поставленные и регламентные остаются на доске с пометкой «архив» и открываются по ключу."
            />
          </span>
          <IosSegmented
            value={draft.isArchived ? 'archived' : 'active'}
            options={STATE_OPTIONS}
            onChange={(value) => { if (!saving) patch({ isArchived: value === 'archived' }); }}
            ariaLabel="Состояние доски"
          />
        </section>
      )}
    </div>
  );
};

const TaskProjectsSheet = ({
  open,
  onClose,
  projects = [],
  people = [],
  roleLabels = {},
  apiBaseUrl,
  buildHeaders,
  notify,
  onSaved,
}) => {
  const [view, setView] = useState('list');
  const [draft, setDraft] = useState(() => draftOf(null));
  const [issuesShown, setIssuesShown] = useState(false);
  const [saving, setSaving] = useState(false);
  const [departments, setDepartments] = useState([]);

  useEffect(() => {
    if (open) return;
    setView('list');
    setIssuesShown(false);
  }, [open]);

  // Справочник отделов — только для поля «Отдел», один раз на открытие окна.
  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    axios.get(`${apiBaseUrl}/api/admin/departments`, { headers: buildHeaders() })
      .then((res) => {
        if (!cancelled) setDepartments(Array.isArray(res?.data?.departments) ? res.data.departments : []);
      })
      .catch(() => { if (!cancelled) setDepartments([]); });
    return () => { cancelled = true; };
  }, [open, apiBaseUrl, buildHeaders]);

  const sortedProjects = useMemo(
    () => (Array.isArray(projects) ? projects : [])
      .slice()
      .sort((left, right) => Number(left.is_archived) - Number(right.is_archived)
        || String(left.code || '').localeCompare(String(right.code || ''), 'en')),
    [projects]
  );

  const isCreate = view === 'create';
  // Отказ сервера по ключу (занят, звался так раньше) — у поля, пока ключ тот же.
  const [codeRefusal, setCodeRefusal] = useState(null);
  const issues = useMemo(() => {
    const found = {};
    const codeProblem = projectCodeIssue(
      { code: draft.code, projectId: draft.id, refusal: codeRefusal, strict: issuesShown },
      projects
    );
    if (codeProblem) found.code = codeProblem;
    if (!issuesShown) return found;
    if (!String(draft.name || '').trim()) found.name = 'Укажите название';
    const memberProblem = boardMembersIssue(draft.members, draft.isArchived);
    if (memberProblem) found.members = memberProblem;
    const sectionProblem = sectionsIssue(draft.sections);
    if (sectionProblem) found.sections = sectionProblem;
    return found;
  }, [issuesShown, draft, projects, codeRefusal]);

  const openProject = useCallback((project) => {
    setDraft(draftOf(project));
    setIssuesShown(false);
    setCodeRefusal(null);
    setView('edit');
  }, []);
  const startCreate = useCallback(() => {
    setDraft(draftOf(null));
    setIssuesShown(false);
    setCodeRefusal(null);
    setView('create');
  }, []);
  const back = useCallback(() => setView('list'), []);

  const save = async () => {
    setIssuesShown(true);
    const name = String(draft.name || '').trim();
    const codeProblem = projectCodeIssue(
      { code: draft.code, projectId: draft.id, refusal: codeRefusal, strict: true },
      projects
    );
    const membersProblem = boardMembersIssue(draft.members, draft.isArchived);
    if (!name || codeProblem || membersProblem || sectionsIssue(draft.sections)) {
      // Название и ключ — наверху окна, их видно и так; до состава внизу длинного
      // окна прокручиваем, иначе «Сохранить» молча ничего не делал бы.
      if (name && !codeProblem && membersProblem) {
        requestAnimationFrame(() => {
          document.querySelector('[data-project-issue="members"]')
            ?.scrollIntoView({ block: 'center', behavior: 'smooth' });
        });
      }
      return;
    }
    const codeChanged = !isCreate && draft.code !== draft.originalCode;
    const payload = {
      name,
      department_id: draft.departmentId ? Number(draft.departmentId) : null,
      members: draft.members.map((person) => ({
        user_id: person.id,
        access_level: person.access_level,
        is_executor: Boolean(person.is_executor),
      })),
      sections: draft.sections.map((section) => (
        section.id ? { id: section.id, name: section.name.trim() } : { name: section.name.trim() }
      )),
    };
    setSaving(true);
    try {
      if (isCreate) {
        await axios.post(`${apiBaseUrl}/api/tasks/projects`, { ...payload, code: draft.code }, { headers: buildHeaders() });
        notify?.(`Проект ${draft.code} создан`);
      } else {
        await axios.patch(
          `${apiBaseUrl}/api/tasks/projects/${draft.id}`,
          // Ключ — только когда его сменили: иначе каждое сохранение состава
          // гоняло бы проверку ключа впустую.
          { ...payload, is_archived: draft.isArchived, ...(codeChanged ? { code: draft.code } : {}) },
          { headers: buildHeaders() }
        );
        notify?.(codeChanged ? `Ключ доски сменён: ${draft.originalCode} → ${draft.code}` : `Проект ${draft.code} сохранён`);
      }
      await onSaved?.({ codeChanged });
      setView('list');
    } catch (error) {
      const refusal = error?.response?.data?.code;
      if (['PROJECT_CODE_TAKEN', 'PROJECT_CODE_USED_BEFORE', 'INVALID_PROJECT_CODE'].includes(refusal)) {
        // Про ключ — красным у поля ключа, а не всплывашкой поверх окна.
        setCodeRefusal({ code: draft.code, text: errorText(error, 'Этот ключ взять нельзя') });
      } else {
        notify?.(errorText(error, 'Не удалось сохранить проект'), 'error');
      }
    } finally {
      setSaving(false);
    }
  };

  const footer = view === 'list' ? null : (
    <div className="flex w-full items-center justify-end gap-2">
      <button type="button" className={iosBtnSecondary} onClick={back} disabled={saving}>Отмена</button>
      <button type="button" className={iosBtnPrimary} onClick={save} disabled={saving}>
        {saving ? 'Сохраняю…' : (isCreate ? 'Создать проект' : 'Сохранить')}
      </button>
    </div>
  );

  return (
    <IosModal
      open={open}
      onClose={onClose}
      onBack={view === 'list' ? null : back}
      title={view === 'list' ? 'Проекты' : (isCreate ? 'Новый проект' : `${draft.originalCode} · ${draft.name || 'Проект'}`)}
      subtitle={view === 'list' ? 'Доски отделов и кто их видит' : (isCreate ? 'Доска, ключ, состав и разделы' : 'Название, ключ, отдел, состав и разделы доски')}
      maxWidth="max-w-2xl"
      footer={footer}
      sizeKey={view}
    >
      {view === 'list' ? (
        <ProjectList projects={sortedProjects} onOpen={openProject} onCreate={startCreate} />
      ) : (
        <ProjectEditor
          draft={draft}
          setDraft={setDraft}
          isCreate={isCreate}
          issues={issues}
          people={people}
          roleLabels={roleLabels}
          departments={departments}
          saving={saving}
        />
      )}
    </IosModal>
  );
};

export default TaskProjectsSheet;
