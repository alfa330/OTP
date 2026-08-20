/*
 * «Задача ждёт меня» — единые правила для уведомлений раздела и бейджа сайдбара.
 *
 * Те же правила продублированы в SQL дважды:
 * database.py → Database.get_task_action_needs_summary (бейдж считается на сервере,
 * когда раздел не открыт) и notifications/sources.py → tasks (колокол уведомлений
 * отдаёт не только число, но и сами задачи), плюс в CLI —
 * scripts/task_board.py → task_action_need. Меняете правило — меняйте во всех четырёх.
 *
 * Категории взаимоисключающие: у задачи ровно одна причина, самая срочная.
 * Бэклог не трогаем — это очередь планирования, работы там ещё нет. Единственное
 * исключение — `info`: вопрос задал живой человек, и очередь ответа не отменяет.
 */

export const ACTION_NEED_KINDS = ['overdue', 'returned', 'info', 'review', 'fresh', 'accepted'];

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
  /* Единственная причина, которую поднимает не статус задачи, а живой
     вопрос человека: исполнитель нажал «Не хватает информации». Выше приёмки —
     пока не ответишь, работа стоит. */
  info: {
    order: 2,
    title: 'Просят информацию',
    label: 'Просят информацию',
    hint: 'Исполнителю не хватает данных',
    dot: '#7c3aed',
  },
  review: {
    order: 3,
    title: 'Ждут вашей приёмки',
    label: 'Ждёт приёмки',
    hint: 'Исполнитель сдал работу',
    dot: '#2563eb',
  },
  fresh: {
    order: 4,
    title: 'Новые для вас',
    label: 'Не начата',
    hint: 'Поручена, работа не начата',
    dot: '#64748b',
  },
  /* Единственная причина «к сведению», а не «сделай»: делать с принятой
     задачей нечего, поэтому она и стоит последней. Гаснет просмотром.

     terminal — она же единственная, у которой нет выхода: остальные четыре
     пропадают, как только задача сдвинулась, а принятая не сдвинется уже
     никогда. Поэтому просмотренную её из списка убирают (см.
     collectTaskActionNeeds), иначе панель раздела за пару месяцев
     превращается в кладбище закрытых задач. */
  accepted: {
    order: 5,
    title: 'Работу приняли',
    label: 'Принята',
    hint: 'Поручитель принял работу',
    dot: '#16a34a',
    terminal: true,
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

  /* Исполнителю не хватает информации, и ответ за мной. Раньше проверок
     исполнителя: спрашивающий и отвечающий — разные люди, поэтому причина
     живёт вне ветки «я исполнитель», и бэклог её не отменяет. */
  if (
    task?.info_request
    && ['assigned', 'in_progress', 'returned'].includes(status)
    && reviewAuthorityId(task) === personId
    && !isAssignee
  ) {
    return { kind: 'info', dueAt };
  }

  /* Раньше проверки бэклога и живых статусов: принятая задача из работы вышла,
     но исполнителю о приёмке сказать надо. Кроме случая, когда принимал он сам
     (задача себе) — сообщать человеку о его же клике незачем. */
  if (status === 'accepted' && isAssignee && reviewAuthorityId(task) !== personId) {
    return { kind: 'accepted', dueAt };
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
  if (Number.isNaN(seenAt)) return false;
  /* У терминальной причины отметка вечная. «Задачу тронули — посмотри заново»
     осмысленно, пока задача живая; принятую же правка отчёта или тега сдвигает
     updated_at, и человек снова получал бы звон о приёмке, случившейся неделю
     назад. */
  if (ACTION_NEED_META[kind]?.terminal) return true;
  const updatedAt = new Date(task?.updated_at || 0).getTime();
  return Number.isNaN(updatedAt) ? true : seenAt >= updatedAt;
};

/** Список задач, ждущих пользователя: срочные сверху, внутри группы — по дедлайну. */
export const collectTaskActionNeeds = (tasks, userId, now = Date.now(), localSeen = null) => {
  const list = [];
  (Array.isArray(tasks) ? tasks : []).forEach((task) => {
    const need = taskActionNeed(task, userId, now);
    if (!need) return;
    const seen = isActionNeedSeen(task, need.kind, localSeen);
    // Просмотренная терминальная причина уходит навсегда: задача закрыта и
    // сдвинуться уже не может, так что иначе она осталась бы в списке до
    // скончания века. Остальные причины исчезают сами, когда задача сдвинется.
    if (seen && ACTION_NEED_META[need.kind]?.terminal) return;
    list.push({ ...need, task, seen });
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
