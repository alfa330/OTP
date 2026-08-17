/*
 * Отдел раздела «Задачи».
 *
 * Принадлежность задачи отделу нигде не хранится — её выводим из членства того,
 * кто задачу поставил (created_by). Значит, перевод сотрудника в другой отдел
 * переносит туда и его задачи: это осознанное поведение, а не рассинхрон.
 *
 * Вынесено отдельным модулем, чтобы правило «что уходит в запрос» и правило
 * «какой отдел выбран на входе» можно было гонять тестами: ошибка в них
 * выглядит как «раздел открылся пустым» — самый неочевидный вид поломки.
 */

export const TASK_DEPARTMENT_ALL = 'all';
/** Задачи постановщиков, у которых отдел не проставлен. */
export const TASK_DEPARTMENT_NONE = 'none';

export const departmentStorageKey = (userId) => `otp.tasks.department:${Number(userId || 0)}`;

/** Нормализованное значение переключателя: 'all' | 'none' | id отдела строкой. */
export const normalizeDepartmentValue = (value) => {
  const raw = String(value ?? '').trim();
  if (!raw) return TASK_DEPARTMENT_ALL;
  if (raw === TASK_DEPARTMENT_ALL || raw === TASK_DEPARTMENT_NONE) return raw;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? String(parsed) : TASK_DEPARTMENT_ALL;
};

/** «Все отделы» — это отсутствие фильтра, а не отдельное значение для сервера. */
export const departmentQueryParams = (departmentId) => {
  const value = normalizeDepartmentValue(departmentId);
  return value === TASK_DEPARTMENT_ALL ? {} : { department_id: value };
};

/** Значение переключателя для строки списка отделов (id = null — «Без отдела»). */
export const departmentValueOf = (department) => (
  department?.id === null || department?.id === undefined
    ? TASK_DEPARTMENT_NONE
    : String(department.id)
);

/**
 * Список отделов после обновления. Выбранный отдел мог остаться без задач и
 * пропасть из ответа — тогда переносим его из прошлого списка: иначе кнопка
 * переключателя опустеет, а раздел останется сужен по значению, которого в
 * списке уже нет, и вернуться к нему будет нечем.
 */
export const mergeSelectedDepartment = (departments, previous, selectedValue) => {
  const list = Array.isArray(departments) ? departments : [];
  const value = String(selectedValue ?? '').trim();
  if (!value || value === TASK_DEPARTMENT_ALL) return list;
  if (list.some((item) => departmentValueOf(item) === value)) return list;
  const kept = (Array.isArray(previous) ? previous : []).find((item) => departmentValueOf(item) === value);
  return kept ? [...list, { ...kept, task_count: 0 }] : list;
};

/** Варианты для CustomSelect: «Все отделы» сверху, «Без отдела» — в самом низу. */
export const buildDepartmentOptions = (departments) => {
  const list = Array.isArray(departments) ? departments : [];
  const named = list
    .filter((item) => departmentValueOf(item) !== TASK_DEPARTMENT_NONE)
    .map((item) => ({ value: String(item.id), label: String(item?.name || `Отдел №${item.id}`) }))
    .sort((left, right) => left.label.localeCompare(right.label, 'ru'));
  const hasNoDepartment = list.some((item) => departmentValueOf(item) === TASK_DEPARTMENT_NONE);
  return [
    { value: TASK_DEPARTMENT_ALL, label: 'Все отделы' },
    ...named,
    ...(hasNoDepartment ? [{ value: TASK_DEPARTMENT_NONE, label: 'Без отдела' }] : []),
  ];
};

/**
 * Какой отдел показать на входе: сохранённый выбор → отдел пользователя с
 * сервера → все отделы.
 *
 * Оба кандидата обязаны быть в списке. Сохранённый выбор устаревает (отдел
 * переименовали, задачи из него разобрали), а несуществующее значение оставило
 * бы переключатель с пустой кнопкой и пустым списком под ней.
 */
export const resolveInitialDepartment = (departments, defaultDepartmentId, storedValue) => {
  const available = new Set([TASK_DEPARTMENT_ALL]);
  (Array.isArray(departments) ? departments : []).forEach((item) => available.add(departmentValueOf(item)));

  const stored = String(storedValue ?? '').trim();
  if (stored && available.has(stored)) return stored;

  if (defaultDepartmentId === null || defaultDepartmentId === undefined) return TASK_DEPARTMENT_ALL;
  const fallback = String(defaultDepartmentId);
  return available.has(fallback) ? fallback : TASK_DEPARTMENT_ALL;
};
