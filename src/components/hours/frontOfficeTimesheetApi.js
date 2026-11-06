/*
 * Запросы табеля фронт-офисов: отборы месяца и сам файл.
 *
 * Файл качается тем же путём, что «Сформировать отчёт» «Учета часов»
 * (downloadMonthlyReport в App.jsx): fetch с токеном → blob → <a download> в
 * этом же окне. startFileDownload здесь не годится: он для подписанных ссылок
 * хранилища через скрытый фрейм — ответ ручки с ошибкой ушёл бы во фрейм молча.
 */
import { downloadFileName, timesheetFallbackName, timesheetQuery } from './frontOfficeTimesheet';

const API_PATH = '/api/front_office_timesheet';

async function errorText(response) {
    try {
        const body = await response.json();
        return body?.error || `Ошибка ${response.status}`;
    } catch {
        return `Ошибка ${response.status}`;
    }
}

export async function fetchTimesheetScope({ apiBaseUrl, headers, signal }) {
    const response = await fetch(`${apiBaseUrl}${API_PATH}/scope`, {
        method: 'GET',
        credentials: 'include',
        signal,
        headers,
    });
    if (!response.ok) throw new Error(await errorText(response));
    return response.json();
}

export async function fetchTimesheetData({ apiBaseUrl, headers, month, filters, signal }) {
    const response = await fetch(`${apiBaseUrl}${API_PATH}/data?${timesheetQuery(month, filters)}`, {
        method: 'GET',
        credentials: 'include',
        signal,
        headers,
    });
    if (!response.ok) throw new Error(await errorText(response));
    return response.json();
}

export async function fetchTimesheetOptions({ apiBaseUrl, headers, month, signal }) {
    const query = new URLSearchParams({ month: String(month || '') }).toString();
    const response = await fetch(`${apiBaseUrl}${API_PATH}/options?${query}`, {
        method: 'GET',
        credentials: 'include',
        signal,
        headers,
    });
    if (!response.ok) throw new Error(await errorText(response));
    return response.json();
}

export async function downloadTimesheet({ apiBaseUrl, headers, month, filters, signal }) {
    const response = await fetch(`${apiBaseUrl}${API_PATH}/export?${timesheetQuery(month, filters)}`, {
        method: 'GET',
        credentials: 'include',
        signal,
        headers,
    });
    if (!response.ok) throw new Error(await errorText(response));
    const blob = await response.blob();
    const name = downloadFileName(
        response.headers.get('Content-Disposition') || response.headers.get('content-disposition'),
        timesheetFallbackName(month),
    );
    const link = document.createElement('a');
    const url = window.URL.createObjectURL(blob);
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.URL.revokeObjectURL(url);
    return name;
}
