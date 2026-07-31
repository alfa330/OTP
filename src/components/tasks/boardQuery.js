/*
 * Что именно доска просит у сервера: колонка, доска сотрудника, порядок, порция.
 *
 * Вынесено из TaskBoardWorkspace.jsx отдельным модулем, чтобы это можно было
 * гонять тестами: ровно здесь живёт правило «у каждой колонки свой срез»,
 * и ошибка в нём выглядит как «во всех колонках одно и то же».
 */

/** Колонка доски = свой срез статусов. Порядок ключей совпадает с BOARD_COLUMNS. */
export const BOARD_COLUMN_QUERY = {
  backlog:  { backlog: 'only' },
  todo:     { status: 'assigned', backlog: 'exclude' },
  progress: { status: 'in_progress,returned', backlog: 'exclude' },
  review:   { status: 'completed', backlog: 'exclude' },
  done:     { status: 'accepted', backlog: 'exclude' },
};

/**
 * Сколько карточек подгружать за раз: в канбане — в каждую колонку, в бэклоге и
 * таймлайне — на страницу. Настройка клиентская, но сервер режет по своему потолку.
 */
export const BOARD_CHUNK_SIZES = [20, 40, 60];
export const DEFAULT_BOARD_CHUNK = 20;

export const normalizeBoardChunk = (value) => {
  const parsed = Number(value);
  return BOARD_CHUNK_SIZES.includes(parsed) ? parsed : DEFAULT_BOARD_CHUNK;
};

export const boardQueryParams = ({ scope, mode, sort, column, limit, offset, withSummary = true }) => {
  const params = { limit, offset };
  // Сводка стоит семи агрегатов по всей базе — просим её один раз на загрузку доски.
  if (!withSummary) params.summary = 0;
  // Важность сортирует сервер: иначе в выборку попали бы просто самые свежие,
  // а «критичные» из хвоста наверх бы не поднялись.
  if (sort === 'importance') params.sort = 'importance';
  if (column && BOARD_COLUMN_QUERY[column]) Object.assign(params, BOARD_COLUMN_QUERY[column]);
  else if (mode === 'backlog') params.backlog = 'only';
  if (scope === 'my') params.mine = 'any';
  else if (scope === 'assigned') params.mine = 'assignee';
  else if (String(scope || '').startsWith('person:')) {
    params.person_id = Number(String(scope).slice('person:'.length) || 0);
    params.person_scope = 'any';
  }
  return params;
};
