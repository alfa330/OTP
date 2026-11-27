/*
 * Поиск по доске (владелец 27.11.2026): строка поиска, задержка набора, запрос
 * к серверу и страницы выдачи. Чистые правила — в taskSearch.js.
 *
 * Два урока раздела «Сессии» (заметка «Задержка поиска сбрасывалась»):
 *   • изменчивое состояние загрузчика — в ref, иначе таймер задержки
 *     пересоздаётся на каждый старт/финиш запроса и запрос уходит на каждую букву;
 *   • новый запрос отменяет прежний (AbortController), а отмена — не ошибка:
 *     иначе ответ по устаревшей строке оседал бы в выдаче.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  BOARD_SEARCH_PAGE,
  SEARCH_DEBOUNCE_MS,
  appendSearchPage,
  isSearchableQuery,
  searchRequestParams,
} from './taskSearch.js';

const IDLE = { query: '', tasks: [], total: 0, layout: '', loading: false, loadingMore: false, error: '' };

const isAbort = (error) => error?.name === 'CanceledError' || error?.name === 'AbortError' || error?.code === 'ERR_CANCELED';

/**
 * searchTasks(params, { signal }) → { tasks, total, layout } — запрос к /api/tasks.
 * scopeKey — строка охвата (доска|проект|раздел): сменилась — ищем заново.
 * scopeParams — параметры охвата для запроса (scopeQueryParams).
 */
export default function useTaskBoardSearch({ searchTasks, scopeKey, scopeParams, reloadToken = 0 }) {
  const [text, setText] = useState('');
  const [query, setQuery] = useState('');
  const [state, setState] = useState(IDLE);
  // «Повторить» после сбоя: та же строка, тот же охват — ещё один запрос.
  const [attempt, setAttempt] = useState(0);
  const failedRef = useRef('');

  const searchRef = useRef(searchTasks);
  const paramsRef = useRef(scopeParams);
  const stateRef = useRef(state);
  const controllerRef = useRef(null);
  const requestRef = useRef(0);
  useEffect(() => { searchRef.current = searchTasks; }, [searchTasks]);
  useEffect(() => { paramsRef.current = scopeParams; }, [scopeParams]);
  useEffect(() => { stateRef.current = state; }, [state]);

  // Задержка набора: запрос уходит, когда человек перестал печатать.
  useEffect(() => {
    const trimmed = text.trim();
    if (!isSearchableQuery(trimmed)) {
      setQuery('');
      return undefined;
    }
    const timer = setTimeout(() => setQuery(trimmed), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [text]);

  const request = useCallback(async (value, offset, limit) => {
    controllerRef.current?.abort();
    const controller = typeof AbortController === 'function' ? new AbortController() : null;
    controllerRef.current = controller;
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    try {
      const result = await searchRef.current(
        searchRequestParams(paramsRef.current, value, offset, limit),
        { signal: controller?.signal }
      );
      return requestRef.current === requestId ? (result || { tasks: [], total: 0 }) : null;
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  }, []);

  /* Первая страница — на новую строку, другую доску и после действий с задачами
     (reloadToken): статус в выдаче обязан совпадать с карточкой. Перечитываем
     столько, сколько уже показано, — «Показать ещё» не схлопывается. */
  useEffect(() => {
    if (!query || typeof searchRef.current !== 'function') {
      controllerRef.current?.abort();
      requestRef.current += 1;
      setState(IDLE);
      return;
    }
    const shown = stateRef.current.query === query ? stateRef.current.tasks.length : 0;
    const limit = Math.min(500, Math.max(BOARD_SEARCH_PAGE, shown));
    failedRef.current = '';
    // Прежняя выдача остаётся на экране, пока не пришла новая: без мигания пустотой.
    setState((prev) => ({ ...prev, loading: true, error: '' }));
    request(query, 0, limit)
      .then((result) => {
        if (!result) return;
        setState({
          query,
          tasks: Array.isArray(result.tasks) ? result.tasks : [],
          total: Number(result.total || 0),
          layout: String(result.layout || ''),
          loading: false,
          loadingMore: false,
          error: '',
        });
      })
      .catch((error) => {
        if (isAbort(error)) return;
        // Сбой — это ответ по ЭТОЙ строке (query записан): «Ищу…» не висит вечно,
        // а находки прежней строки не остаются под новой (разбор 27.11.2026).
        failedRef.current = 'page';
        setState({ ...IDLE, query, error: 'Не удалось выполнить поиск.' });
      });
  }, [query, scopeKey, reloadToken, attempt, request]);

  // Отмена висящего запроса, когда доска закрывается.
  useEffect(() => () => controllerRef.current?.abort(), []);

  const loadMore = useCallback(() => {
    const current = stateRef.current;
    if (!current.query || current.loading || current.loadingMore || current.tasks.length >= current.total) return;
    setState((prev) => ({ ...prev, loadingMore: true, error: '' }));
    request(current.query, current.tasks.length, BOARD_SEARCH_PAGE)
      .then((result) => {
        if (!result) return;
        setState((prev) => ({
          ...prev,
          tasks: appendSearchPage(prev.tasks, result.tasks),
          total: Number(result.total || prev.total),
          loadingMore: false,
        }));
      })
      .catch((error) => {
        if (isAbort(error)) return;
        failedRef.current = 'more';
        setState((prev) => ({ ...prev, loadingMore: false, error: 'Не удалось загрузить ещё.' }));
      });
  }, [request]);

  const clear = useCallback(() => setText(''), []);

  // Сорвалась догрузка — повторяем её же; сорвалась первая страница — запрос заново.
  const retry = useCallback(() => {
    if (failedRef.current === 'more') {
      failedRef.current = '';
      setState((prev) => ({ ...prev, error: '' }));
      loadMore();
      return;
    }
    setAttempt((value) => value + 1);
  }, [loadMore]);

  const isActive = isSearchableQuery(text);
  return {
    text,
    setText,
    clear,
    isActive,
    // Строка набрана, а ответа по ней ещё нет: выдача на экране — от прежней строки.
    isPending: isActive && (state.loading || state.query !== text.trim()),
    ...state,
    hasMore: state.tasks.length < state.total,
    loadMore,
    retry,
  };
}
