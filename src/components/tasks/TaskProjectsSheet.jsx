import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { ChevronDown, ChevronRight, ChevronUp, Plus, X } from 'lucide-react';
import {
  IosModal,
  IosSegmented,
  iosBtnPrimary,
  iosBtnSecondary,
  iosCard,
  iosGroupLabel,
  iosInput,
} from '../ui/ios';
import CustomSelect from '../ui/CustomSelect';
import InfoHint from '../common/InfoHint';
import { PROJECT_CODE_RE, projectCodeIssue } from './taskProjects';

/*
 * Окно «Проекты» раздела «Задачи» (задача #401): доски отделов, их ключи и кто
 * их видит. Открывает его тот, кто в разделе и так видит всё (админ, не
 * возглавляющий отдел, и супер-админ), — сервер проверяет это сам.
 *
 * Список → проект. Состав доски — одна лента людей, у каждого уровень сегментом:
 *   Участник — видит все задачи проекта;
 *   Доска    — ставит задачи в проект и видит свои и те, где он наблюдатель.
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

const ACCESS_OPTIONS = [
  { value: 'member', label: 'Участник' },
  { value: 'board', label: 'Доска' },
];

const pluralRu = (count, one, few, many) => {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
};

const errorText = (error, fallback) => error?.response?.data?.error || fallback;

const initials = (name) => String(name || '?')
  .split(/\s+/)
  .filter(Boolean)
  .slice(0, 2)
  .map((part) => part[0])
  .join('')
  .toUpperCase();

const draftOf = (project) => ({
  id: project?.id ?? null,
  code: project?.code || '',
  // Ключ, с которым доску открыли: по нему видно, что ключ сменили, и им же
  // подписано окно, пока новый ещё набирают.
  originalCode: project?.code || '',
  name: project?.name || '',
  departmentId: project?.department_id ? String(project.department_id) : '',
  isArchived: Boolean(project?.is_archived),
  members: (Array.isArray(project?.members) ? project.members : []).map((person) => ({
    id: Number(person.id),
    name: person.name,
    role: person.role,
    department: person.department,
    access_level: person.access_level === 'board' ? 'board' : 'member',
  })),
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
        const members = Array.isArray(project.members) ? project.members : [];
        const memberCount = members.filter((person) => person.access_level !== 'board').length;
        const boardCount = members.length - memberCount;
        const taskCount = Number(project.task_count || 0);
        const sectionCount = Array.isArray(project.sections) ? project.sections.length : 0;
        const facts = [
          project.department_name || 'Без отдела',
          memberCount ? `${memberCount} ${pluralRu(memberCount, 'участник', 'участника', 'участников')}` : 'нет участников',
          boardCount ? `${boardCount} с доступом к доске` : '',
          sectionCount ? `${sectionCount} ${pluralRu(sectionCount, 'раздел', 'раздела', 'разделов')}` : '',
          taskCount ? `${taskCount} ${pluralRu(taskCount, 'задача', 'задачи', 'задач')}` : '',
        ].filter(Boolean);
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
                <span className="block truncate text-[12px] text-slate-500">{facts.join(' · ')}</span>
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

const MemberRow = ({ person, roleLabels, onLevel, onRemove, disabled }) => (
  <li className="flex items-center gap-3 px-3.5 py-2">
    <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-500">
      {initials(person.name)}
    </span>
    <span className="min-w-0 grow basis-0">
      <span className="block truncate text-[13.5px] font-medium text-slate-800">{person.name}</span>
      <span className="block truncate text-[12px] text-slate-500">
        {[roleLabels?.[person.role], person.department].filter(Boolean).join(' · ') || '—'}
      </span>
    </span>
    <IosSegmented
      size="xs"
      value={person.access_level}
      options={ACCESS_OPTIONS}
      onChange={(level) => { if (!disabled) onLevel(person.id, level); }}
      ariaLabel={`Доступ: ${person.name}`}
    />
    <button
      type="button"
      onClick={() => onRemove(person.id)}
      disabled={disabled}
      className="grid h-7 w-7 shrink-0 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
      aria-label={`Убрать: ${person.name}`}
      title="Убрать с доски"
    >
      <X size={15} />
    </button>
  </li>
);

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
      label: roleLabels?.[person.role] ? `${person.name} (${roleLabels[person.role]})` : person.name,
    })), [people, memberIds, roleLabels]);
  const departmentOptions = useMemo(() => [
    { value: '', label: 'Без отдела' },
    ...(departments || [])
      .map((department) => ({ value: String(department.id), label: department.name }))
      .sort((left, right) => left.label.localeCompare(right.label, 'ru')),
  ], [departments]);

  const patch = (next) => setDraft((prev) => ({ ...prev, ...next }));
  const addMember = (value) => {
    const person = peopleById.get(Number(value));
    if (!person || memberIds.has(Number(person.id))) return;
    patch({
      members: [...draft.members, {
        id: Number(person.id),
        name: person.name,
        role: person.role,
        department: person.department || '',
        access_level: 'member',
      }],
    });
  };
  const setLevel = (personId, level) => patch({
    members: draft.members.map((person) => (person.id === personId ? { ...person, access_level: level } : person)),
  });
  const removeMember = (personId) => patch({ members: draft.members.filter((person) => person.id !== personId) });

  /* Сотрудники отдела доски, которых ещё нет в составе: одной кнопкой — участниками.
     Отдел знают только кандидаты с сервера (у списка получателей его нет). */
  const departmentStaff = useMemo(() => (draft.departmentId
    ? (people || []).filter((person) => String(person.department_id ?? '') === String(draft.departmentId)
      && !memberIds.has(Number(person.id)))
    : []), [draft.departmentId, people, memberIds]);
  const addDepartmentStaff = () => patch({
    members: [...draft.members, ...departmentStaff.map((person) => ({
      id: Number(person.id),
      name: person.name,
      role: person.role,
      department: person.department || '',
      access_level: 'member',
    }))],
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

      <section className="space-y-1.5">
        <div className="flex items-center gap-1.5">
          <span className={iosGroupLabel}>Состав доски</span>
          <InfoHint
            side="left"
            text="Участник видит все задачи проекта. «Доска» — ставит задачи в проект и видит свои и те, где его добавили наблюдателем. Работать с задачей — брать, сдавать, принимать — по-прежнему могут только её исполнители и поручитель."
          />
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
                  onLevel={setLevel}
                  onRemove={removeMember}
                  disabled={saving}
                />
              ))}
            </ul>
          ) : (
            <p className="border-t border-slate-100 px-4 py-6 text-center text-[12.5px] text-slate-500">
              Пока задачи проекта видят только их участники и админы раздела.
            </p>
          )}
        </div>
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
    if (!name || codeProblem || sectionsIssue(draft.sections)) return;
    const codeChanged = !isCreate && draft.code !== draft.originalCode;
    const payload = {
      name,
      department_id: draft.departmentId ? Number(draft.departmentId) : null,
      members: draft.members.map((person) => ({ user_id: person.id, access_level: person.access_level })),
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
