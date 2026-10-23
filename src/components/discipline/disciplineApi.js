import axios from 'axios';

/*
 * Запросы раздела «Дисциплина» — один клиент на раздел. Заголовки авторизации
 * берутся при каждом вызове (headers()), а не один раз: токен обновляется, пока
 * раздел открыт. Ошибка сервера уже по-русски — её и показываем.
 */

export const apiError = (error, fallback = 'Не удалось выполнить действие') => {
    const data = error?.response?.data;
    if (data && typeof data === 'object' && data.error) return String(data.error);
    if (error?.response?.status === 413) return 'Файл слишком большой';
    return fallback;
};

export const apiCode = (error) => error?.response?.data?.code || null;

export const createDisciplineApi = (apiBaseUrl, headers) => {
    const base = `${apiBaseUrl}/api/discipline`;
    const get = (path, params) => axios.get(`${base}${path}`, { headers: headers(), params }).then((r) => r.data);
    const send = (method, path, body) => axios({ method, url: `${base}${path}`, data: body, headers: headers() })
        .then((r) => r.data);
    const form = (path, fields, files) => {
        const data = new FormData();
        Object.entries(fields || {}).forEach(([key, value]) => {
            if (value === undefined || value === null || value === '') return;
            if (Array.isArray(value)) value.forEach((item) => data.append(key, item));
            else data.append(key, value);
        });
        (files || []).forEach(([field, file]) => data.append(field, file, file.name));
        return axios.post(`${base}${path}`, data, { headers: headers() }).then((r) => r.data);
    };
    /* Файл приходит blob'ом — и отказ сервера тоже: его JSON лежит внутри blob,
       и без распаковки человек видел бы заглушку вместо настоящей причины
       («Файл не найден в хранилище», «Выгрузка — для руководителя»). */
    const blob = (path, params, signal) => axios.get(`${base}${path}`, { headers: headers(), params, responseType: 'blob', signal })
        .then((r) => r.data)
        .catch(async (error) => {
            const data = error?.response?.data;
            if (typeof Blob !== 'undefined' && data instanceof Blob && /json/i.test(data.type || '')) {
                try { error.response.data = JSON.parse(await data.text()); } catch { /* останется заглушка */ }
            }
            throw error;
        });
    return {
        ping: () => get('/ping'),
        summary: (params) => get('/summary', params),
        records: (params) => get('/records', params),
        record: (id) => get(`/records/${id}`),
        exportRecords: (params) => blob('/records/export', params),
        searchOperators: (q) => get('/operators/search', { q }),
        searchPeople: (q) => get('/people/search', { q }),
        operatorContext: (params) => get('/operator-context', params),
        createRecord: (fields, files) => form('/records', fields, files),
        editRecord: (id, body) => send('patch', `/records/${id}`, body),
        cancelRecord: (id, reason) => send('post', `/records/${id}/cancel`, { reason }),
        closeRecord: (id) => send('post', `/records/${id}/close`, {}),
        comment: (id, comment) => send('post', `/records/${id}/comment`, { comment }),
        uploadExplanation: (id, file) => form(`/records/${id}/explanation`, {}, [['file', file]]),
        explanationBlank: (id) => blob(`/records/${id}/explanation.docx`),
        refusal: (id, file, witnessIds) => form(`/records/${id}/refusal`, { witness_ids: witnessIds }, [['file', file]]),
        addEvidence: (id, files) => form(`/records/${id}/evidence`, {}, files.map((file) => ['files', file])),
        training: (id, body) => send('post', `/records/${id}/training`, body),
        measure: (id, body) => send('post', `/records/${id}/measure`, body),
        approveMeasure: (id) => send('post', `/records/${id}/measure/approve`, {}),
        file: (id, inline = false, signal) => blob(`/files/${id}`, inline ? { inline: 1 } : undefined, signal),
        invalidateFile: (id, reason) => send('post', `/files/${id}/invalidate`, { reason }),
        updateAct: (id, body) => send('patch', `/acts/${id}`, body),
        uploadAct: (id, file) => form(`/acts/${id}/file`, {}, [['file', file]]),
        actDraft: (id) => blob(`/acts/${id}/draft.docx`),
        operatorCard: (id) => get(`/operators/${id}`),
        controls: (state) => get('/controls', { state }),
        createControl: (body) => send('post', '/controls', body),
        decideControl: (id, body) => send('post', `/controls/${id}/decide`, body),
        settings: (departmentId) => get('/settings', { department_id: departmentId }),
        saveSettings: (departmentId, settings) => send('put', '/settings', { department_id: departmentId, settings }),
        saveType: (body) => send('post', '/types', body),
        saveMeasure: (body) => send('post', '/measures', body),
        uploadTemplate: (departmentId, file) => form('/settings/template', { department_id: departmentId }, [['file', file]]),
        report: (kind, params) => get(`/reports/${kind}`, params),
        reportXlsx: (kind, params) => blob(`/reports/${kind}`, { ...params, format: 'xlsx' }),
        audit: (params) => get('/audit', params),
        auditXlsx: (params) => blob('/audit', { ...params, format: 'xlsx' }),
    };
};

/* Скачать полученный blob в этом же окне (правило портала: без новых вкладок). */
export const saveBlob = (blob, fileName) => {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = fileName || 'file';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
};
