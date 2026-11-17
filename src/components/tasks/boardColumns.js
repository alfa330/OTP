/*
 * Колонки доски «Задач» и что значит перенос карточки между ними.
 *
 * Вынесено из TaskBoardWorkspace.jsx отдельным модулем, чтобы правила переноса
 * проверялись тестами по поведению: ошибка здесь выглядит как «карточка уехала
 * не в ту колонку» или «доска разрешила то, что сервер отвергнет».
 *
 * Новых статусов колонки не заводят: это жизненный цикл задачи
 * (assigned → in_progress → completed → accepted, возврат — returned),
 * а бэклог — отдельный флаг is_backlog поверх статуса `assigned`.
 */

// Расширение в пути обязательно: модуль гоняется тестами через node --test,
// а там ESM не достраивает '.js' сам, в отличие от сборки Vite.
import { taskAssignees } from './taskAssignees.js';

/* Порядок слева направо: новое → очередь → работа → возврат → приёмка → итог.
   «Возвращено на доработку» — временная колонка (transient): её видно, только
   пока в ней есть задачи, пустая она лишь отнимала бы ширину у доски. */
export const BOARD_COLUMNS = [
  { id: 'todo',     title: 'Новая',                   caption: 'Назначены' },
  { id: 'backlog',  title: 'Бэклог',                  caption: 'Ждут очереди' },
  { id: 'progress', title: 'В работе',                caption: 'Идут сейчас' },
  { id: 'returned', title: 'Возвращено на доработку', caption: 'Ждут исправлений', transient: true },
  { id: 'review',   title: 'На проверке',             caption: 'Ждут приёмки' },
  { id: 'done',     title: 'Готово',                  caption: 'Приняты' },
];

export const columnOfTask = (task) => {
  if (task?.is_backlog) return 'backlog';
  switch (task?.status) {
    case 'in_progress':
      return 'progress';
    case 'returned':
      return 'returned';
    case 'completed':
      return 'review';
    case 'accepted':
      return 'done';
    default:
      return 'todo';
  }
};

/**
 * Колонки, которые сейчас стоят на доске. countOf(id) — сколько задач в колонке;
 * доска отдаёт большее из счётчика базы и загруженных карточек, чтобы колонка не
 * мигала, пока после возврата задачи колонки перезагружаются.
 */
export const visibleBoardColumns = (countOf) => BOARD_COLUMNS
  .filter((column) => !column.transient || Number(countOf(column.id) || 0) > 0);

/**
 * Строки канбана — объединение загруженных колонок (columnState[id].tasks).
 *
 * После действия колонки перечитываются каждая своим запросом, и перенесённая
 * карточка успевает уйти из ответа старой колонки раньше, чем придёт ответ новой:
 * на кадр-два её не было бы на доске вовсе. Пока колонки дочитываются, карточку с
 * оптимистичной правкой (overrides) держит её прежняя строка (keptRows, ключ —
 * id строкой), а правка поверх ставит её в новую колонку. Дочитались все колонки,
 * а строки нет — карточка ушла за порцию колонки, и показывать её не нужно.
 */
export const mergeBoardRows = (columnState, overrides = {}, keptRows = null) => {
  const byId = new Map();
  BOARD_COLUMNS.forEach((column) => {
    (columnState?.[column.id]?.tasks || []).forEach((task) => byId.set(task.id, task));
  });
  const reloading = BOARD_COLUMNS.some((column) => columnState?.[column.id]?.loading);
  if (reloading && keptRows) {
    Object.keys(overrides || {}).forEach((key) => {
      const kept = keptRows.get(String(key));
      if (kept && !byId.has(kept.id)) byId.set(kept.id, kept);
    });
  }
  return [...byId.values()];
};

/**
 * Известно ли уже, стоят ли временные колонки на доске. columnState — состояние
 * колонок доски: fetched — ответ по колонке пришёл после сброса, loading — запрос
 * в пути. Пока ответа нет, доску после полной загрузки не рисуем: иначе колонка
 * возврата «доезжала» бы позже соседних и сдвигала «На проверке» и «Готово» вбок.
 * Сорвавшийся запрос (loading снят, fetched нет) доску не держит.
 */
export const transientColumnsKnown = (columnState) => BOARD_COLUMNS
  .filter((column) => column.transient)
  .every((column) => Boolean(columnState?.[column.id]?.fetched) || !columnState?.[column.id]?.loading);

/* Колонка → состояние задачи, в котором карточка в ней стоит. Тот же срез, что
   BOARD_COLUMN_QUERY, и та же таблица, что _TASK_BOARD_COLUMN_STATES у сервера
   (db/tasks.py): по ней доска сразу ставит карточку, перенесённую суперадмином. */
export const BOARD_COLUMN_STATE = {
  todo:     { status: 'assigned',    is_backlog: false },
  backlog:  { status: 'assigned',    is_backlog: true },
  progress: { status: 'in_progress', is_backlog: false },
  returned: { status: 'returned',    is_backlog: false },
  review:   { status: 'completed',   is_backlog: false },
  done:     { status: 'accepted',    is_backlog: false },
};

/* Среди исполнителей есть уволенный — признак is_fired отдаёт сервер, по тем же
   статусам, что вкладка «Уволенные». */
export const hasFiredAssignee = (task) => taskAssignees(task).some((person) => Boolean(person?.is_fired));

/* Перенос карточки уволенного — в форме оптимистичной правки доски: ручке уходит
   ровно та колонка, куда карточку бросили, а её ответ становится списком
   записанного по задачам; отказ (false) — правку снять. */
export const sendBoardMove = async (onMoveTask, task, column) => {
  const item = await onMoveTask(task, column);
  return item ? [item] : false;
};

/**
 * Разрешение drag&drop: какая операция стоит за переносом карточки между колонками.
 * Возвращает {type:'status'|'board'|'move'|'blocked'|'noop'}.
 */
export const resolveBoardDrop = (task, toColumn, ctx) => {
  const from = columnOfTask(task);
  if (from === toColumn) return { type: 'noop' };
  // Задача коллеги-СВ открыта только на просмотр (задача #298).
  if (task?.viewer_is_observer) return { type: 'blocked', reason: 'Это задача коллеги — её можно только посмотреть' };

  /* Карточку уволенного суперадмин переносит в любую колонку одним шагом (владелец,
     17.11.2026): двигать её по циклу некому — брать в работу и сдавать может только
     исполнитель. Окна сдачи и возврата здесь не нужны: карточка встаёт ровно туда,
     куда её бросили. */
  if (ctx.isSuperAdmin && hasFiredAssignee(task) && BOARD_COLUMN_STATE[toColumn]) {
    return { type: 'move', column: toColumn };
  }

  const creatorId = Number(task?.creator?.id || 0);
  const requesterId = Number(task?.requested_by?.id || 0);
  // Принять в работу и сдать может любой исполнитель — они равноправны.
  const isAssignee = taskAssignees(task).some((person) => Number(person.id) === ctx.currentUserId);
  const isCreator = creatorId === ctx.currentUserId;
  const canPlan = isCreator || ctx.isAdmin;
  // Приёмка за поручителем (или постановщиком). Свою работу себе не принимают —
  // кроме своей инициативы, где принимать больше некому.
  const authorityId = requesterId || creatorId;
  const canReview = (authorityId && authorityId === ctx.currentUserId)
    || (!isAssignee && (ctx.isAdmin || isCreator || ctx.isSupervisor));

  if (toColumn === 'backlog') {
    if (task?.status !== 'assigned') return { type: 'blocked', reason: 'В бэклог можно вернуть только не начатую задачу' };
    if (!canPlan) return { type: 'blocked', reason: 'Двигать в бэклог может постановщик или админ' };
    return { type: 'board', patch: { is_backlog: true } };
  }

  if (toColumn === 'todo') {
    if (from !== 'backlog') return { type: 'blocked', reason: 'Начатую задачу новой уже не сделать — для исправлений есть возврат на доработку' };
    if (!(canPlan || isAssignee)) return { type: 'blocked', reason: 'Нет прав выносить задачу из бэклога' };
    return { type: 'board', patch: { is_backlog: false } };
  }

  if (toColumn === 'progress') {
    // Пока возвращённых нет, временной колонки на доске не видно и бросить
    // карточку туда нельзя — поэтому сданная задача, брошенная в «В работе»,
    // тоже уходит на доработку (и колонка возврата появляется вместе с ней).
    if (from === 'review') {
      if (!canReview) return { type: 'blocked', reason: 'Вернуть на доработку может тот, кто поручил задачу' };
      return { type: 'status', action: 'returned' };
    }
    if (from === 'done') {
      if (!canReview) return { type: 'blocked', reason: 'Возобновить задачу может тот, кто поручил её' };
      return { type: 'status', action: 'reopened' };
    }
    if (!isAssignee) return { type: 'blocked', reason: 'Взять задачу в работу может только исполнитель' };
    return { type: 'status', action: 'in_progress' };
  }

  if (toColumn === 'returned') {
    // Сервер возвращает только сданное или принятое — то же правило здесь.
    if (from !== 'review' && from !== 'done') return { type: 'blocked', reason: 'Вернуть на доработку можно только сданную задачу' };
    if (!canReview) return { type: 'blocked', reason: 'Вернуть на доработку может тот, кто поручил задачу' };
    return { type: 'status', action: 'returned' };
  }

  if (toColumn === 'review') {
    // Возвращённую задачу можно сдать сразу, не беря заново в работу.
    if (from !== 'progress' && from !== 'returned') return { type: 'blocked', reason: 'На проверку задача уходит из работы' };
    if (!isAssignee) return { type: 'blocked', reason: 'Отметить выполненной может только исполнитель' };
    return { type: 'status', action: 'completed' };
  }

  if (toColumn === 'done') {
    if (from !== 'review') return { type: 'blocked', reason: 'Принять можно только задачу на проверке' };
    if (!canReview) return { type: 'blocked', reason: 'Итог принимает тот, кто поручил задачу — свою работу себе не принимают' };
    return { type: 'status', action: 'accepted' };
  }

  return { type: 'blocked', reason: 'Недоступный перенос' };
};

/**
 * Оптимистичная правка карточки после ответа сервера: поля, которые он вернул, —
 * его значениями. Сервер мог решить иначе, чем ждал фронт (задачу в ту же секунду
 * взяли в работу: балл записан, а в бэклог она не ушла) — правка с догадкой фронта
 * никогда не сошлась бы со строкой доски и держала бы карточку не в той колонке до
 * перезагрузки страницы.
 */
export const overrideFromServer = (override, item) => {
  if (!override || !item) return override;
  return Object.fromEntries(Object.entries(override)
    .map(([field, value]) => [field, Object.prototype.hasOwnProperty.call(item, field) ? item[field] : value]));
};
