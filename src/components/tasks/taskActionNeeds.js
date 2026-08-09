/*
 * «Задача ждёт меня» — единые правила для уведомлений раздела и бейджа сайдбара.
 *
 * Те же четыре правила продублированы в SQL дважды:
 * database.py → Database.get_task_action_needs_summary (бейдж считается на сервере,
 * когда раздел не открыт) и notifications/sources.py → tasks (колокол уведомлений
 * отдаёт не только число, но и сами задачи). Меняете правило — меняйте во всех трёх.
 *
 * Категории взаимоисключающие: у задачи ровно одна причина, самая срочная.
 * Бэклог не трогаем — это очередь планирования, работы там ещё нет.
 */

export const ACTION_NEED_KINDS = ['overdue', 'returned', 'review', 'fresh'];

export const ACTION_NEED_META = {
  overdue: {
    order: 0,
    title: 'Просрочены',
    label: 'Просрочена',
    hint: 'Дедлайн прошёл',
    dot: '#e11d48',
  },
  returned: {
    order: 1,
    title: 'Вернули на доработку',
    label: 'Возврат',
    hint: 'Итог не приняли',
    dot: '#f59e0b',
  },
  review: {
    order: 2,
    title: 'Ждут вашей приёмки',
    label: 'Ждёт приёмки',
    hint: 'Исполнитель сдал работу',
    dot: '#2563eb',
  },
  fresh: {
    order: 3,
    title: 'Новые для вас',
    label: 'Не начата',
    hint: 'Поручена, работа не начата',
    dot: '#64748b',
  },
};

const parseDueAt = (value) => {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

/** Приёмку закрывает поручитель, а если его нет — постановщик. Совпадает с resolveBoardDrop. */
export const reviewAuthorityId = (task) =>
  Number(task?.requested_by?.id || 0) || Number(task?.creator?.id || 0);

/**
 * Причина, по которой задача ждёт действия пользователя, либо null.
 * @returns {null | {kind: string, dueAt: Date|null}}
 */
export const taskActionNeed = (task, userId, now = Date.now()) => {
  const personId = Number(userId || 0);
  if (!personId || !task) return null;

  const status = String(task?.status || '').toLowerCase();
  const isAssignee = Number(task?.assignee?.id || 0) === personId;
  const dueAt = parseDueAt(task?.due_at);

  if (status === 'completed' && reviewAuthorityId(task) === personId) {
    return { kind: 'review', dueAt };
  }

  if (!isAssignee || task?.is_backlog) return null;
  if (!['assigned', 'in_progress', 'returned'].includes(status)) return null;

  if (dueAt && dueAt.getTime() < now) return { kind: 'overdue', dueAt };
  if (status === 'returned') return { kind: 'returned', dueAt };
  if (status === 'assigned') return { kind: 'fresh', dueAt };
  return null;
};

/** Ключ отметки «просмотрено»: сменилась причина или задачу тронули — отметка сгорает. */
export const actionNeedSeenKey = (task, kind) =>
  `${Number(task?.id || 0)}:${kind}:${task?.updated_at || ''}`;

/**
 * Просмотрено ли уведомление. Серверная отметка (task.action_seen) переживает
 * перезагрузку, локальный набор ключей закрывает мгновение до ответа сервера.
 */
export const isActionNeedSeen = (task, kind, localSeen = null) => {
  if (localSeen && localSeen.has(actionNeedSeenKey(task, kind))) return true;
  const seen = task?.action_seen;
  if (!seen || seen.kind !== kind || !seen.seen_at) return false;
  const seenAt = new Date(seen.seen_at).getTime();
  const updatedAt = new Date(task?.updated_at || 0).getTime();
  if (Number.isNaN(seenAt)) return false;
  return Number.isNaN(updatedAt) ? true : seenAt >= updatedAt;
};

/** Список задач, ждущих пользователя: срочные сверху, внутри группы — по дедлайну. */
export const collectTaskActionNeeds = (tasks, userId, now = Date.now(), localSeen = null) => {
  const list = [];
  (Array.isArray(tasks) ? tasks : []).forEach((task) => {
    const need = taskActionNeed(task, userId, now);
    if (need) list.push({ ...need, task, seen: isActionNeedSeen(task, need.kind, localSeen) });
  });
  return list.sort((left, right) => {
    const byKind = ACTION_NEED_META[left.kind].order - ACTION_NEED_META[right.kind].order;
    if (byKind !== 0) return byKind;
    if (left.seen !== right.seen) return left.seen ? 1 : -1;
    const leftDue = left.dueAt ? left.dueAt.getTime() : Number.POSITIVE_INFINITY;
    const rightDue = right.dueAt ? right.dueAt.getTime() : Number.POSITIVE_INFINITY;
    if (leftDue !== rightDue) return leftDue - rightDue;
    return Number(right.task?.id || 0) - Number(left.task?.id || 0);
  });
};

/** Счётчик бейджа: просмотренные не в счёт, иначе число только растёт. */
export const countUnseenActionNeeds = (needs) =>
  (Array.isArray(needs) ? needs : []).filter((need) => !need.seen).length;

/** Разбивка по причинам — для подзаголовков панели уведомлений. */
export const groupTaskActionNeeds = (needs) => {
  const groups = ACTION_NEED_KINDS.map((kind) => ({ kind, items: [] }));
  const byKind = new Map(groups.map((group) => [group.kind, group]));
  (needs || []).forEach((need) => byKind.get(need.kind)?.items.push(need));
  return groups.filter((group) => group.items.length > 0);
};
