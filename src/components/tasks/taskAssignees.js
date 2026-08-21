/*
 * Состав исполнителей задачи.
 *
 * У задачи их может быть несколько, и права у них РАВНЫЕ: каждый принимает в
 * работу, отмечает пункты чеклиста, пишет отчёты и сдаёт результат — «выполнять
 * задачу по этапам может каждый из них». Первый в списке лежит в серверной
 * колонке tasks.assigned_to и показывается первым, но никаких особых прав у него
 * нет: порядок нужен только чтобы люди не прыгали местами между обновлениями.
 *
 * Отдельным модулем, а не внутри TasksView.jsx, по двум причинам:
 *   1) правило «я исполнитель» нужно и списку (TasksView), и доске
 *      (TaskBoardWorkspace), и расчёту «задача ждёт меня» (taskActionNeeds),
 *      а дублировать fallback в трёх местах — прямой путь к расхождению;
 *   2) taskActionNeeds гоняется тестами через `node --test`, куда React не тащат.
 *
 * Расширение '.js' в импортах этого модуля обязательно: node --test не
 * достраивает его сам, в отличие от сборки Vite.
 */

/* Потолок состава. Держим его и в UI, и на API: задача на пятнадцать человек —
   это не задача, а рассылка, и в интерфейсе такой список уже не читается. */
export const TASK_MAX_ASSIGNEES = 10;

/**
 * Все исполнители задачи по порядку.
 * Ответ сервера без `assignees` читается как список из одного человека: раздел
 * продолжает работать на неразвёрнутом бэкенде и на закешированных данных.
 */
export const taskAssignees = (task) => {
  const list = Array.isArray(task?.assignees)
    ? task.assignees.filter((person) => Number(person?.id || 0) > 0)
    : [];
  if (list.length) return list;
  return Number(task?.assignee?.id || 0) > 0 ? [task.assignee] : [];
};

/** Идентификаторы исполнителей строками: селекторы сравнивают значения по строкам. */
export const taskAssigneeIds = (task) =>
  taskAssignees(task).map((person) => String(person.id));

/** Пользователь — один из исполнителей задачи (не «тот единственный», а любой). */
export const isTaskAssignee = (task, userId) => {
  const id = Number(userId || 0);
  return id > 0 && taskAssignees(task).some((person) => Number(person.id) === id);
};

/**
 * Подпись состава одной строкой: «Айгуль» либо «Айгуль и ещё 2».
 * Перечислять всех в строку нельзя — в карточке списка на это нет места.
 */
export const taskAssigneesLabel = (task, emptyLabel = '—') => {
  const people = taskAssignees(task);
  if (!people.length) return emptyLabel;
  const first = people[0]?.name || emptyLabel;
  return people.length > 1 ? `${first} и ещё ${people.length - 1}` : first;
};
