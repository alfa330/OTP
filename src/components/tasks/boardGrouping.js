/*
 * Раскладка задач по дням для окна статуса: день — строка, карточки внутри дня
 * идут по горизонтали. Порядок задач сохраняется тот, что прислал сервер
 * (свежие сверху либо по важности), группировка его не пересортировывает.
 */

const DAY_MS = 24 * 60 * 60 * 1000;

const startOfDay = (value) => {
  // new Date(null) — это 1970, а не «нет даты»: пустые значения отбиваем сами.
  if (value === null || value === undefined || value === '') return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  date.setHours(0, 0, 0, 0);
  return date;
};

/** Ключ дня — локальная дата, а не UTC: иначе вечерние задачи уезжают в завтра. */
export const dayKeyOf = (value) => {
  const day = startOfDay(value);
  if (!day) return '';
  const pad = (num) => String(num).padStart(2, '0');
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
};

export const dayLabelOf = (value, now = Date.now()) => {
  const day = startOfDay(value);
  if (!day) return 'Без даты';
  const today = startOfDay(now);
  const diffDays = Math.round((today.getTime() - day.getTime()) / DAY_MS);
  if (diffDays === 0) return 'Сегодня';
  if (diffDays === 1) return 'Вчера';
  if (diffDays === -1) return 'Завтра';
  return day.toLocaleDateString('ru-RU', {
    day: 'numeric',
    month: 'long',
    ...(day.getFullYear() === today.getFullYear() ? {} : { year: 'numeric' }),
  });
};

/**
 * Группы дней в том порядке, в каком задачи пришли с сервера.
 * @returns [{ key, label, tasks }]
 */
export const groupTasksByDay = (tasks, { field = 'created_at', now = Date.now() } = {}) => {
  const groups = [];
  const byKey = new Map();
  (Array.isArray(tasks) ? tasks : []).forEach((task) => {
    const raw = task?.[field];
    const key = dayKeyOf(raw) || 'unknown';
    let group = byKey.get(key);
    if (!group) {
      group = { key, label: dayKeyOf(raw) ? dayLabelOf(raw, now) : 'Без даты', tasks: [] };
      byKey.set(key, group);
      groups.push(group);
    }
    group.tasks.push(task);
  });
  return groups;
};
