/* Запросы «Карточки клиента» к порталу — одни и те же у страницы телефона и раздела.
 *
 * getHeaders — функция, а не объект: токен обновляется в фоне, и собранные один раз
 * заголовки ушли бы в запрос протухшими. На странице телефона заголовки подставляет
 * перехватчик axios (ClientCardEmbed.jsx) — там getHeaders не передают вовсе.
 */
import axios from 'axios';

export const createCardsApi = (apiBaseUrl, getHeaders = null) => {
    const url = (path) => `${apiBaseUrl}/api/client_cards${path}`;
    const config = (extra) => ({ headers: getHeaders ? getHeaders() : {}, ...extra });
    const form = (fields, files) => {
        const data = new FormData();
        Object.entries(fields).forEach(([key, value]) => {
            if (value !== null && value !== undefined && value !== '') data.append(key, String(value));
        });
        Array.from(files || []).forEach((file) => data.append('files', file, file.name));
        return data;
    };
    return {
        url,
        // Экран раздела и настройки
        screen: (departmentId) => axios.get(url(''), config({ params: departmentId ? { department_id: departmentId } : {} })),
        saveSettings: (body) => axios.put(url('/settings'), body, config()),
        provideDepartment: (departmentId) => axios.post(url('/departments'), { department_id: departmentId }, config()),
        createCategory: (body) => axios.post(url('/categories'), body, config()),
        updateCategory: (id, body) => axios.patch(url(`/categories/${id}`), body, config()),
        deleteCategory: (id) => axios.delete(url(`/categories/${id}`), config()),
        reorderCategories: (body) => axios.post(url('/categories/reorder'), body, config()),
        createDestination: (body) => axios.post(url('/destinations'), body, config()),
        updateDestination: (id, body) => axios.patch(url(`/destinations/${id}`), body, config()),
        deleteDestination: (id) => axios.delete(url(`/destinations/${id}`), config()),
        testAlert: (departmentId) => axios.post(url('/alerts/test'), { department_id: departmentId }, config()),
        journal: (params) => axios.get(url('/journal'), config({ params })),
        report: (params) => axios.get(url('/report'), config({ params })),
        reportExport: (params) => axios.get(url('/report/export'), config({ params, responseType: 'blob' })),
        // Карточка звонка
        phoneState: () => axios.get(url('/phone/state'), config()),
        openCall: (body) => axios.post(url('/phone/calls'), body, config()),
        callEvent: (cardId, event) => axios.post(url(`/cards/${cardId}/event`), { event }, config()),
        lookup: (cardId) => axios.post(url(`/cards/${cardId}/lookup`), {}, config()),
        chooseAccount: (cardId, accountId) => axios.post(url(`/cards/${cardId}/account`), { account_id: accountId }, config()),
        attachLink: (cardId, link) => axios.post(url(`/cards/${cardId}/account`), { link }, config()),
        unlink: (cardId, linkId) => axios.delete(url(`/cards/${cardId}/links/${linkId}`), config()),
        saveCard: (cardId, body) => axios.patch(url(`/cards/${cardId}`), body, config()),
        completeCard: (cardId) => axios.post(url(`/cards/${cardId}/complete`), {}, config()),
        card: (cardId) => axios.get(url(`/cards/${cardId}`), config()),
        cardBundle: (cardId) => axios.get(url(`/cards/${cardId}`), config({ params: { bundle: 1 } })),
        history: (params) => axios.get(url('/history'), config({ params })),
        // Запросы в другие отделы
        requests: (params) => axios.get(url('/requests'), config({ params })),
        request: (id) => axios.get(url(`/requests/${id}`), config()),
        createRequest: (fields, files) => axios.post(url('/requests'), form(fields, files), config()),
        replyRequest: (id, fields, files) => axios.post(url(`/requests/${id}/reply`), form(fields, files), config()),
        setRequestStatus: (id, status) => axios.post(url(`/requests/${id}/status`), { status }, config()),
        retryRequest: (id) => axios.post(url(`/requests/${id}/retry`), {}, config()),
        requestFileJson: (id, messageId) => axios.get(url(`/requests/${id}/files/${messageId}`), config({ responseType: 'blob' })),
    };
};

/* Вложение переписки. Наш файл сервер отдаёт JSON со ссылкой на хранилище, файл из
   Telegram — самим содержимым. Запрос один (blob), а что пришло — видно по типу ответа.
   Возвращает { url, revoke } — revoke обязателен для адреса из памяти. */
export const resolveRequestFile = async (api, requestId, messageId) => {
    const { data, headers } = await api.requestFileJson(requestId, messageId);
    const type = String(headers?.['content-type'] || data?.type || '');
    if (type.includes('application/json')) {
        const parsed = JSON.parse(await data.text());
        return { url: parsed.url, revoke: () => {} };
    }
    const objectUrl = URL.createObjectURL(data);
    return { url: objectUrl, revoke: () => URL.revokeObjectURL(objectUrl) };
};
