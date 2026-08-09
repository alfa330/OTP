/* Single-flight для snapshot-запросов: пока один запрос выполняется, новые
   сигналы не запускают параллельные запросы, а просят ровно один повтор. */
export function createCoalescedReload(reload) {
  let activePromise = null;
  let rerunRequested = false;

  return function requestReload() {
    if (activePromise) {
      rerunRequested = true;
      return activePromise;
    }

    activePromise = (async () => {
      try {
        do {
          rerunRequested = false;
          await reload();
        } while (rerunRequested);
      } finally {
        // В том числе после исключения: следующая попытка обязана стартовать.
        activePromise = null;
      }
    })();

    return activePromise;
  };
}
