export function prepareTemplate(item, values = {}) {
    if (item.source !== 'wazzup' || item.kind === 'text') return { text: item.text, preview: '' };
    if (!item.supported || !item.templateCode) throw new Error(item.unsupportedReason || 'Этот шаблон пока не поддерживается');
    let text = item.templateCode;
    let preview = item.text;
    for (const key of item.variables || []) {
        const value = String(values[key] || '').trim();
        if (!value || /[\[\]\r\n]/.test(value)) throw new Error('Заполните переменные без квадратных скобок и переноса строки');
        text = text.split(`[[${key}]]`).join(`[[${value}]]`);
        const number = key.match(/bodyVar(\d+)/)?.[1];
        if (number) preview = preview.split(`{{${number}}}`).join(value);
    }
    return { text, preview: preview || item.title };
}
