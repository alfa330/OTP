/*
 * Адрес оценки в разделе «ИИ-оценка».
 *
 * Открытая карточка разговора жила только состоянием React: перезагрузка
 * возвращала к спискам, а показать коллеге «вот эту оценку» можно было лишь
 * словами «найди звонок 8930 из АТС». Теперь адрес повторяет открытую карточку:
 *
 *   ?view=ai_qa&eval=imported_call-8930   — звонок из АТС;
 *   ?view=ai_qa&eval=call-40812           — звонок журнала;
 *   ?view=ai_qa&eval=wz_episode-512       — переписка (c2d_snapshot, ca_episode — так же).
 *
 * Вид в ссылке обязателен: у звонков журнала, звонков из АТС и переписок свои,
 * независимые последовательности id, и голый номер открыл бы чужой разговор.
 * Отдел в ссылке не нужен: карточка сама говорит, чей разговор, и раздел
 * переключается на этот отдел (CallQaView). Права проверяет сервер, как у любой
 * карточки: чужой разговор по ссылке не откроется.
 *
 * Метку ставит и снимает раздел (CallQaView): открыл карточку — метка в адресе,
 * «Назад» — метки нет. Уход в другой раздел снимает её App.jsx (stripAiQaParams).
 * Метка принадлежит разделу, названному в адресе (`view`): входя сюда из меню,
 * раздел видит в строке ещё прежний `view` и чужих меток не читает.
 *
 * replaceState, а не pushState — как у чатов и «Оплаты счетов»: открытые
 * карточки — не история браузера.
 */
import { stripTechnicalQueryParams } from '../../utils/urlHygiene.js';

export const AI_QA_VIEW = 'ai_qa';
export const APP_VIEW_QUERY_PARAM = 'view';
export const AI_QA_EVAL_QUERY_PARAM = 'eval';
// Зеркало call_qa/config.py SUBJECT_KINDS — сверяет tests/ai_qa_eval_link.test.mjs.
export const AI_QA_SUBJECT_KINDS = ['call', 'imported_call', 'wz_episode', 'c2d_snapshot', 'ca_episode'];

/** Оценка в том виде, в каком живёт в адресе: «imported_call-8930». '' — цель битая. */
export const formatEvalTarget = (target) => {
    const subject = String(target?.subject || '').trim();
    const id = Number(target?.id);
    return AI_QA_SUBJECT_KINDS.includes(subject) && Number.isSafeInteger(id) && id > 0 ? `${subject}-${id}` : '';
};

/** Значение метки → { subject, id }. null — мусор, перехода нет. */
export const parseEvalTarget = (value) => {
    // В виде бывают цифры: c2d_snapshot — заявка Chat2Desk у СЗоВ.
    const match = String(value ?? '').trim().match(/^([a-z0-9_]+)-(\d{1,12})$/);
    if (!match || !AI_QA_SUBJECT_KINDS.includes(match[1])) return null;
    const id = Number(match[2]);
    return id > 0 ? { subject: match[1], id } : null;
};

/** Оценка из строки запроса — только если адрес раздела. */
export const readEvalLink = (search) => {
    try {
        const params = new URLSearchParams(String(search ?? ''));
        if (params.get(APP_VIEW_QUERY_PARAM) !== AI_QA_VIEW) return null;
        return parseEvalTarget(params.get(AI_QA_EVAL_QUERY_PARAM));
    } catch (error) {
        return null;
    }
};

/** Снять метку раздела. Возвращает тот же объект URL. */
export const stripAiQaParams = (url) => {
    url.searchParams.delete(AI_QA_EVAL_QUERY_PARAM);
    return url;
};

/** Ссылка, которую копируют и отправляют: только раздел и оценка. Метки,
 * оставшиеся в адресе от входа и других разделов (task_id, ticket_id, фильтры),
 * в ней не нужны — ссылка из Telegram на обращение иначе уехала бы в рабочую
 * группу вместе с оценкой. Путь (база GitHub Pages) берётся из текущего адреса. */
export const buildEvalLink = (target, href = typeof window === 'undefined' ? '' : window.location.href) => {
    const value = formatEvalTarget(target);
    if (!value || !href) return '';
    try {
        const url = new URL(href);
        url.search = '';
        url.hash = '';
        url.searchParams.set(APP_VIEW_QUERY_PARAM, AI_QA_VIEW);
        url.searchParams.set(AI_QA_EVAL_QUERY_PARAM, value);
        return url.toString();
    } catch (error) {
        return '';
    }
};

/** Открытая карточка — в адресную строку; закрыли — метку убираем. Только пока
 * в строке адрес раздела: уход в другой раздел метку уже снял (App.jsx), и
 * вернуть её туда значило бы унести оценку в чужой адрес. Тот же адрес второй
 * раз не пишется: Safari разрешает не больше сотни replaceState за 30 секунд. */
export const syncEvalLink = (target) => {
    if (typeof window === 'undefined') return;
    try {
        const url = new URL(window.location.href);
        if (url.searchParams.get(APP_VIEW_QUERY_PARAM) !== AI_QA_VIEW) return;
        stripTechnicalQueryParams(url);
        const value = formatEvalTarget(target);
        if (value) url.searchParams.set(AI_QA_EVAL_QUERY_PARAM, value);
        else url.searchParams.delete(AI_QA_EVAL_QUERY_PARAM);
        const next = `${url.pathname}${url.search}${url.hash}`;
        const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
        if (next !== current) window.history.replaceState(window.history.state, '', next);
    } catch (error) {
        // В урезанных браузерных контекстах адресная строка недоступна — не беда.
    }
};

/** Текст в буфер обмена. clipboard.writeText есть только в защищённом контексте
 * (и не во всех вебвью) — без запасного пути через execCommand кнопка там молча
 * ничего не делала бы. true — скопировано. */
export const copyText = async (text) => {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch (error) {
            // ниже — запасной путь
        }
    }
    if (typeof document === 'undefined') return false;
    const field = document.createElement('textarea');
    field.value = text;
    field.setAttribute('readonly', '');
    field.style.position = 'fixed';
    field.style.opacity = '0';
    document.body.appendChild(field);
    field.select();
    let copied = false;
    try { copied = document.execCommand('copy'); } catch (error) { copied = false; }
    document.body.removeChild(field);
    return copied;
};
