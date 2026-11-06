/*
 * Что именно доска просит у сервера: колонка, доска сотрудника, порядок, порция.
 *
 * Вынесено из TaskBoardWorkspace.jsx отдельным модулем, чтобы это можно было
 * гонять тестами: ровно здесь живёт правило «у каждой колонки свой срез»,
 * и ошибка в нём выглядит как «во всех колонках одно и то же».
 */

// Расширение в пути обязательно: этот модуль гоняется тестами через node --test,
// а там ESM не достраивает '.js' сам, в отличие от сборки Vite.
import { projectQueryParams, sectionQueryParams } from './taskProjects.js';

/** Колонка доски = свой срез статусов. Порядок ключей совпадает с BOARD_COLUMNS. */
export const BOARD_COLUMN_QUERY = {
  todo:     { status: 'assigned', backlog: 'exclude' },
  backlog:  { backlog: 'only' },
  progress: { status: 'in_progress', backlog: 'exclude' },
  returned: { status: 'returned', backlog: 'exclude' },
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

/**
 * Чьи задачи просим: «Мои» / «На мне» / доска сотрудника («Все» — без фильтра),
 * на какой доске проекта и в каком её разделе (задача #401).
 * Общее у доски и выгрузки: в Excel должен уехать ровно тот же охват, что виден
 * на экране, поэтому охват описан один раз.
 */
export const scopeQueryParams = (scope, projectId = null, sectionId = null) => {
  const params = { ...projectQueryParams(projectId), ...sectionQueryParams(sectionId) };
  if (scope === 'my') return { ...params, mine: 'any' };
  if (scope === 'assigned') return { ...params, mine: 'assignee' };
  if (String(scope || '').startsWith('person:')) {
    return {
      ...params,
      person_id: Number(String(scope).slice('person:'.length) || 0),
      person_scope: 'any',
    };
  }
  return params;
};

export const boardQueryParams = ({
  scope, mode, sort, column, limit, offset, projectId = null, sectionId = null, withSummary = true,
}) => {
  const params = { limit, offset };
  // Сводка стоит семи агрегатов по всей базе — просим её один раз на загрузку доски.
  if (!withSummary) params.summary = 0;
  // Важность сортирует сервер: иначе в выборку попали бы просто самые свежие,
  // а «критичные» из хвоста наверх бы не поднялись.
  if (sort === 'importance') params.sort = 'importance';
  if (column && BOARD_COLUMN_QUERY[column]) Object.assign(params, BOARD_COLUMN_QUERY[column]);
  else if (mode === 'backlog') params.backlog = 'only';
  return Object.assign(params, scopeQueryParams(scope, projectId, sectionId));
};
