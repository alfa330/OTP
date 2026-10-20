/* Вход в окно «Чатов ОП» в CRM по одноразовой ссылке — до старта портала (embedLaunch.js).
   Отдельным модулем, импортом раньше App.jsx: импорты исполняются по порядку, и портал
   не успеет поднять сохранённую сессию, которую ссылка сейчас сменит. Отрисовку main.jsx
   начинает после обмена: проверка сессии портала, ушедшая без токена, иначе могла бы
   вернуться позже обмена и стереть только что записанные токены. */
import { reloadPending, runEmbedLaunch } from './components/wazzup/embedLaunch';

export const embedLaunchDone = runEmbedLaunch().catch(() => null);
export { reloadPending };
