/* Скопировать строку в буфер обмена. Промис: true — скопировано.
 *
 * Сначала Clipboard API. Где его нет или он отказал (WebView2 без жеста
 * пользователя, не https-страница, политика браузера) — прежний способ через
 * скрытое поле и execCommand('copy'): устаревший, но в Chromium работает, а
 * молчаливый отказ выглядел бы как «кнопка не работает». */
export default async function copyText(text) {
    const value = String(text ?? '');
    if (!value) return false;
    try {
        if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(value);
            return true;
        }
    } catch {
        // Отказ Clipboard API — пробуем способ ниже.
    }
    if (typeof document === 'undefined' || !document.body) return false;
    const previous = document.activeElement;
    const area = document.createElement('textarea');
    area.value = value;
    area.setAttribute('readonly', '');
    // Вне экрана и без прокрутки: поле нужно только на одно мгновение выделения.
    area.style.position = 'fixed';
    area.style.top = '0';
    area.style.left = '0';
    area.style.opacity = '0';
    area.style.pointerEvents = 'none';
    document.body.appendChild(area);
    let ok = false;
    try {
        area.select();
        ok = document.execCommand('copy');
    } catch {
        ok = false;
    } finally {
        document.body.removeChild(area);
        // Фокус возвращаем туда, где он был: иначе после копирования клавиатура
        // «теряется», и поиск по офисам приходится нажимать заново.
        if (previous && typeof previous.focus === 'function') {
            try { previous.focus({ preventScroll: true }); } catch { /* элемент уже снят */ }
        }
    }
    return ok;
}
