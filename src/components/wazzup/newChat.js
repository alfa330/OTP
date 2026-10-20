/*
 * Новый чат по номеру — кнопка «+» рядом с поиском «Чатов ОП».
 *
 * Постановка владельца 20.10.2026: «в самом wazzup есть кнопка плюс рядом с
 * поиском при нажатии на который можно начать чат написав номер». Начинать
 * можно во всех активных каналах, кроме Global и каналов «не писать первыми»;
 * список таких каналов считает сервер (wazzup/new_chat.py) и отдаёт в
 * /api/wazzup/pilot как newChatChannelIds — здесь правило не повторяется.
 *
 * Пока сообщения нет, чата нет и в базе: раздел открывает «заготовку» с флагом
 * isNew, а строку чата заводит первая отправка (newChat в /pilot/send). Как
 * только строка приходит живым потоком или списком, она заменяет заготовку —
 * флаг исчезает сам.
 */

/* Номер — цифрами в международном формате, как WhatsApp-чаты лежат в Wazzup.
   Казахстанские номера пишут как угодно: «8 700…» → 7700…, «700 123 45 67»
   (десять цифр без кода, код города или оператора у нас начинается с 7) →
   7700…; номер с «+» берётся как есть. */
export const normalizeNewChatPhone = (value) => {
    const raw = String(value ?? '').trim();
    let digits = raw.replace(/\D+/g, '');
    if (digits.length === 11 && digits.startsWith('8')) digits = `7${digits.slice(1)}`;
    else if (digits.length === 10 && digits.startsWith('7') && !raw.startsWith('+')) digits = `7${digits}`;
    return /^[1-9]\d{9,14}$/.test(digits) ? digits : '';
};

/** Каналы «+» для выбора: в порядке колонки каналов, только разрешённые сервером. */
export const newChatChannelOptions = (channels, allowedIds) => {
    const allowed = new Set(allowedIds || []);
    return (channels || [])
        .filter((channel) => allowed.has(channel?.channelId))
        .map((channel) => ({ value: channel.channelId, label: channel.name || channel.plainId || channel.channelId }));
};

/** Канал формы по умолчанию: выбранный слева, если в нём можно начинать, иначе первый. */
export const defaultNewChatChannel = (options, preferred) => {
    if (preferred && options.some((option) => option.value === preferred)) return preferred;
    return options[0]?.value || '';
};

/** Заготовка чата, которого ещё нет: открывается как обычный чат раздела. */
export const draftChat = ({ channelId, phone, name = '' }) => ({
    channelId,
    chatId: phone,
    chatType: 'whatsapp',
    contactName: String(name || '').trim() || null,
    contactPhone: phone,
    isNew: true,
});
