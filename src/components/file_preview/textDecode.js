/* Байты текстового файла → строка.

   Кодировку файл о себе не сообщает. Порядок проверки:
   1. метка BOM (UTF-8, UTF-16 LE/BE) — её ставят Блокнот и выгрузки Excel «Юникод»;
   2. строгий UTF-8 — если байты им не являются, декодер бросает исключение,
      а не подставляет «�»;
   3. windows-1251 — так сохраняют CSV и TXT русские Windows и 1С. Без этого
      шага такой файл открывался бы «кракозябрами». */

const decoder = (label, options) => {
    try {
        return new TextDecoder(label, options);
    } catch {
        return null;
    }
};

export function decodeTextBytes(bytes) {
    const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes || []);
    if (data[0] === 0xef && data[1] === 0xbb && data[2] === 0xbf) {
        return { text: decoder('utf-8').decode(data.subarray(3)), encoding: 'utf-8' };
    }
    if (data[0] === 0xff && data[1] === 0xfe) {
        return { text: decoder('utf-16le').decode(data.subarray(2)), encoding: 'utf-16le' };
    }
    if (data[0] === 0xfe && data[1] === 0xff) {
        const utf16be = decoder('utf-16be');
        if (utf16be) return { text: utf16be.decode(data.subarray(2)), encoding: 'utf-16be' };
    }
    const strict = decoder('utf-8', { fatal: true });
    try {
        return { text: strict.decode(data), encoding: 'utf-8' };
    } catch {
        // не UTF-8 — пробуем кириллицу Windows
    }
    const cyrillic = decoder('windows-1251');
    if (cyrillic) return { text: cyrillic.decode(data), encoding: 'windows-1251' };
    return { text: decoder('utf-8').decode(data), encoding: 'utf-8' };
}

/** JSON с отступами, если он разбирается; иначе — как есть. */
export function prettyJson(text) {
    try {
        return JSON.stringify(JSON.parse(text), null, 2);
    } catch {
        return text;
    }
}
