/*
 * Проекты-доски раздела «Задачи» (задача #401).
 *
 * У отдела своя доска и свой ключ на латинице: задача получает номер внутри
 * проекта, и по ключу HR-125 сразу видно, чья она. Глобальный номер задачи при
 * этом остаётся — на него ссылаются Telegram, CLI и старые ссылки.
 *
 * Кто что видит, решает сервер (task_projects.py): участник проекта видит все
 * его задачи, доступ «доска» — свои и те, где человек наблюдатель. Здесь —
 * только правила экрана: что уходит в запрос, какая доска выбрана на входе,
 * какой проект подставить в форму. Вынесено модулем, чтобы гонять тестами
 * (tests/task_projects.test.mjs): ошибка в них выглядит как «раздел открылся
 * пустым» — самый неочевидный вид поломки.
 */

export const TASK_PROJECT_ALL = 'all';
/** Задачи, не привязанные ни к одному проекту (поставлены до проектов). */
export const TASK_PROJECT_NONE = 'none';

export const projectStorageKey = (userId) => `otp.tasks.project:${Number(userId || 0)}`;
/** Последний проект, куда человек ставил задачу, — подставляем его в форму. */
export const composerProjectStorageKey = (userId) => `otp.tasks.composerProject:${Number(userId || 0)}`;

/** Нормализованное значение переключателя: 'all' | 'none' | id проекта строкой. */
export const normalizeProjectValue = (value) => {
  const raw = String(value ?? '').trim();
  if (!raw) return TASK_PROJECT_ALL;
  if (raw === TASK_PROJECT_ALL || raw === TASK_PROJECT_NONE) return raw;
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed > 0 ? String(parsed) : TASK_PROJECT_ALL;
};

/** «Все проекты» — это отсутствие фильтра, а не значение для сервера. */
export const projectQueryParams = (projectId) => {
  const value = normalizeProjectValue(projectId);
  return value === TASK_PROJECT_ALL ? {} : { project_id: value };
};

/* Подпись проекта начинается с ключа, поэтому и порядок — по ключу: ACC, BE, FIN, HR…
   По названию кириллица встала бы впереди латиницы, и список читался бы вразнобой. */
const byCode = (left, right) => String(left?.code || '').localeCompare(String(right?.code || ''), 'en');

/** Подпись проекта в списках: ключ впереди — по нему проект и узнают. */
export const projectLabel = (project) => {
  const code = String(project?.code || '').trim();
  const name = String(project?.name || '').trim();
  if (code && name) return `${code} · ${name}`;
  return code || name || `Проект №${project?.id ?? ''}`;
};

/* Архивная доска остаётся в переключателе, пока на ней есть задачи: их надо
   где-то смотреть. Пустую архивную не показываем — это пункт в никуда. */
const isListedProject = (project, selected) => !project?.is_archived
  || Number(project?.task_count || 0) > 0
  || String(project?.id) === selected;

/**
 * Варианты переключателя: «Все проекты» сверху, «Без проекта» внизу и только
 * когда такие задачи есть. Архивная доска — с пометкой, чтобы в неё не искали,
 * куда поставить новую задачу.
 */
export const buildProjectOptions = (projects, unassignedCount = 0, selectedValue = TASK_PROJECT_ALL) => {
  const selected = normalizeProjectValue(selectedValue);
  const list = (Array.isArray(projects) ? projects : [])
    .filter((project) => isListedProject(project, selected))
    .slice()
    .sort(byCode);
  const options = [
    { value: TASK_PROJECT_ALL, label: 'Все проекты' },
    ...list.map((project) => ({
      value: String(project.id),
      label: project?.is_archived ? `${projectLabel(project)} · архив` : projectLabel(project),
    })),
  ];
  if (Number(unassignedCount) > 0 || selected === TASK_PROJECT_NONE) {
    options.push({ value: TASK_PROJECT_NONE, label: 'Без проекта' });
  }
  return options;
};

/**
 * Список проектов после обновления. Выбранная доска могла пропасть из ответа
 * (человека сняли с доски посреди сессии) — тогда переносим её из прошлого
 * списка: иначе кнопка переключателя опустеет, а раздел останется сужен по
 * значению, которого в списке нет, и вернуться к нему будет нечем.
 */
export const mergeSelectedProject = (projects, previous, selectedValue) => {
  const list = Array.isArray(projects) ? projects : [];
  const value = String(selectedValue ?? '').trim();
  if (!value || value === TASK_PROJECT_ALL || value === TASK_PROJECT_NONE) return list;
  if (list.some((project) => String(project?.id) === value)) return list;
  const kept = (Array.isArray(previous) ? previous : []).find((project) => String(project?.id) === value);
  return kept
    ? [...list, { ...kept, task_count: 0, can_create: false, access: null, members: undefined }]
    : list;
};

/** Переключатель нужен, только когда есть хотя бы один проект — иначе это пустая кнопка. */
export const hasProjectBoards = (projects) => (Array.isArray(projects) ? projects : [])
  .some((project) => !project?.is_archived);

/**
 * Какая доска на входе: сохранённый выбор → доска своего отдела с сервера → все.
 *
 * Оба кандидата обязаны быть в списке. Сохранённый выбор устаревает (проект
 * убрали в архив, человека сняли с доски), а несуществующее значение оставило
 * бы раздел суженным по проекту, которого в переключателе нет.
 */
export const resolveInitialProject = (projects, defaultProjectId, storedValue, unassignedCount = 0) => {
  const available = new Set([TASK_PROJECT_ALL]);
  (Array.isArray(projects) ? projects : [])
    .filter((project) => isListedProject(project, ''))
    .forEach((project) => available.add(String(project.id)));
  if (Number(unassignedCount) > 0) available.add(TASK_PROJECT_NONE);

  const stored = String(storedValue ?? '').trim();
  if (stored && available.has(stored)) return stored;

  if (defaultProjectId === null || defaultProjectId === undefined) return TASK_PROJECT_ALL;
  const fallback = String(defaultProjectId);
  return available.has(fallback) ? fallback : TASK_PROJECT_ALL;
};

/** Проекты, куда человек вправе поставить задачу: доступ к доске и не архив. */
export const creatableProjects = (projects) => (Array.isArray(projects) ? projects : [])
  .filter((project) => project?.can_create)
  .slice()
  .sort(byCode);

/**
 * Проект, который форма подставит сама: открытая доска → последний, куда
 * ставили → доска своего отдела. Пусто — человек выберет сам (поле обязательное,
 * когда выбирать есть из чего).
 */
export const resolveComposerProject = (projects, { boardValue, lastUsed, defaultProjectId } = {}) => {
  const allowed = new Set(creatableProjects(projects).map((project) => String(project.id)));
  for (const candidate of [boardValue, lastUsed, defaultProjectId]) {
    const value = String(candidate ?? '').trim();
    if (value && allowed.has(value)) return value;
  }
  return '';
};

export const findProject = (projects, projectId) => {
  const value = String(projectId ?? '').trim();
  if (!value) return null;
  return (Array.isArray(projects) ? projects : []).find((project) => String(project?.id) === value) || null;
};

/**
 * Кого можно позвать наблюдателем: тех, у кого к доске доступ «доска». Участник
 * проекта и так видит все его задачи — звать его наблюдателем незачем, а
 * исполнители и постановщик видят задачу по своей роли.
 */
export const watcherCandidates = (project, excludeIds = []) => {
  const excluded = new Set((excludeIds || []).map((id) => String(id)));
  return (Array.isArray(project?.members) ? project.members : [])
    .filter((person) => person?.access_level === 'board' && !excluded.has(String(person.id)));
};

/** Наблюдатели, которые остаются при смене проекта: у кого есть доступ к новой доске. */
export const keepWatchersForProject = (watcherIds, project) => {
  const allowed = new Set((Array.isArray(project?.members) ? project.members : []).map((person) => String(person.id)));
  return (Array.isArray(watcherIds) ? watcherIds : []).filter((id) => allowed.has(String(id)));
};

/* ─── Разделы доски («регулируемый набор разделов» из постановки) ───
   У каждой доски свои группы задач — «Подбор», «Адаптация» у HR. Колонки доски
   остаются статусами; раздел — это «о чём задача», а не «на каком она шаге». */

export const TASK_SECTION_ALL = 'all';
export const TASK_SECTION_NONE = 'none';

export const sectionsOf = (project) => (Array.isArray(project?.sections) ? project.sections : []);

export const normalizeSectionValue = (value) => {
  const raw = String(value ?? '').trim();
  if (!raw) return TASK_SECTION_ALL;
  if (raw === TASK_SECTION_ALL || raw === TASK_SECTION_NONE) return raw;
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed > 0 ? String(parsed) : TASK_SECTION_ALL;
};

/** «Все разделы» — отсутствие фильтра; «Без раздела» и раздел — section_id. */
export const sectionQueryParams = (sectionId) => {
  const value = normalizeSectionValue(sectionId);
  return value === TASK_SECTION_ALL ? {} : { section_id: value };
};

/**
 * Варианты фильтра разделов открытой доски. «Без раздела» — только когда такие
 * задачи есть (или он уже выбран): пункт, за которым пусто, — шум.
 */
export const buildSectionOptions = (project, selectedValue = TASK_SECTION_ALL) => {
  const selected = normalizeSectionValue(selectedValue);
  const options = [
    { value: TASK_SECTION_ALL, label: 'Все разделы' },
    ...sectionsOf(project).map((section) => ({ value: String(section.id), label: String(section.name || '') })),
  ];
  if (Number(project?.unsectioned_count || 0) > 0 || selected === TASK_SECTION_NONE) {
    options.push({ value: TASK_SECTION_NONE, label: 'Без раздела' });
  }
  return options;
};

/** Выбранный раздел ещё есть на доске? Удалили — фильтр возвращается на «Все». */
export const resolveSectionFilter = (project, value) => {
  const normalized = normalizeSectionValue(value);
  if (normalized === TASK_SECTION_ALL || normalized === TASK_SECTION_NONE) return normalized;
  return sectionsOf(project).some((section) => String(section.id) === normalized) ? normalized : TASK_SECTION_ALL;
};

// Ключ задачи: латиница и цифры (первой — буква), дефис, номер. Тот же шаблон,
// что в task_projects.py на сервере.
const TASK_KEY_RE = /^\s*#?([A-Za-z][A-Za-z0-9]{1,9})-(\d{1,9})\s*$/;

/** «HR-125» из ссылки или поиска → 'HR-125' в каноническом виде, иначе ''. */
export const normalizeTaskKey = (value) => {
  const match = TASK_KEY_RE.exec(String(value ?? ''));
  if (!match) return '';
  const number = Number(match[2]);
  return number > 0 ? `${match[1].toUpperCase()}-${number}` : '';
};

/** Ключ задачи проекта или '' — у старых задач его нет. */
export const taskKeyOf = (task) => normalizeTaskKey(task?.key || '');

// Ключ доски: тот же шаблон, что CHECK на task_projects.code и task_projects.py.
export const PROJECT_CODE_RE = /^[A-Z][A-Z0-9]{1,9}$/;

/**
 * Что не так с ключом доски в окне «Проекты» — текст у поля или пустая строка.
 * Занятость видна сразу: список досок уже на клиенте, ждать «Сохранить» незачем.
 * Неверный шаблон — только после попытки сохранить (strict): пока человек печатает
 * «H», ругаться рано. refusal — отказ сервера по этому же ключу (бывший ключ чужой
 * доски знает только сервер); сменили ключ — отказ больше не относится к нему.
 */
export const projectCodeIssue = ({ code, projectId = null, refusal = null, strict = false } = {}, projects = []) => {
  const value = String(code || '');
  if (refusal && refusal.code === value) return refusal.text;
  const taken = value && (Array.isArray(projects) ? projects : [])
    .some((project) => project?.code === value && Number(project?.id) !== Number(projectId));
  if (taken) return 'Проект с таким ключом уже есть';
  if (strict && !PROJECT_CODE_RE.test(value)) return 'От 2 до 10 латинских букв и цифр, первой — буква';
  return '';
};
