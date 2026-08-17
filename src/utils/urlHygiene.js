/*
 * Технические метки в адресной строке: ?v= и ?auth_reload=.
 *
 * Обе ставятся ровно перед window.location.replace, чтобы браузер не отдал
 * закешированный index.html: v — восстановление после устаревшего бандла
 * (staleBundleRecovery.js, lazyWithRetry, кнопка «Обновить» на экране ошибки),
 * auth_reload — перезагрузка после неудавшегося обновления токена. Своё дело
 * они делают в момент загрузки документа, и НИКТО их потом не читает — но они
 * оставались в строке и уезжали в каждую скопированную ссылку:
 *   ...?view=tasks&auth_reload=1786533936834&v=1786951163258&task_id=166
 *
 * Убирать их безопасно: восстановление опирается не на URL, а на sessionStorage
 * (свой ключ и пауза в 20 секунд), и при новой поломке метка ставится заново.
 *
 * Добавляете свою служебную метку — добавляйте её сюда, иначе она тоже начнёт
 * жить в ссылках, которыми делятся люди.
 */

export const TECHNICAL_QUERY_PARAMS = ['v', 'auth_reload'];

/** Убрать технические метки из URL. Возвращает true, если что-то убрали. */
export const stripTechnicalQueryParams = (url) => {
  if (!url || typeof url !== 'object' || !url.searchParams) return false;
  let changed = false;
  TECHNICAL_QUERY_PARAMS.forEach((param) => {
    if (!url.searchParams.has(param)) return;
    url.searchParams.delete(param);
    changed = true;
  });
  return changed;
};

/** То же для готовой ссылки строкой. Неразбираемый href отдаём как есть. */
export const stripTechnicalQueryParamsFromHref = (href) => {
  const source = String(href || '');
  if (!source) return '';
  try {
    const url = new URL(source);
    return stripTechnicalQueryParams(url) ? url.toString() : source;
  } catch (error) {
    return source;
  }
};

/**
 * Почистить адресную строку. Зовётся один раз на загрузку — до того, как URL
 * прочитает роутер, чтобы дальше по приложению ссылка была уже без меток.
 */
export const cleanTechnicalQueryParamsFromAddressBar = () => {
  if (typeof window === 'undefined') return false;
  try {
    const url = new URL(window.location.href);
    if (!stripTechnicalQueryParams(url)) return false;
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
    return true;
  } catch (error) {
    // В урезанных браузерных контекстах адресная строка недоступна — не беда.
    return false;
  }
};
