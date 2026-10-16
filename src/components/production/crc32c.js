/*
 * CRC32C (Castagnoli) — контрольная сумма, которую облачное хранилище Google
 * считает для каждого объекта сама (поле crc32c). Браузер считает её же по
 * исходному файлу, пока тот грузится, и отдаёт серверу при завершении: сервер
 * сверяет две суммы и только тогда кладёт материал в раздел. Так ролик,
 * побитый обрывом или сбойным прокси, не попадёт на проверку постановщику.
 *
 * Почему не MD5: в браузере его нет (SubtleCrypto не умеет MD5), а сумму
 * больше 2 ГБ нельзя считать «разом» — только кусками, по мере чтения файла.
 * CRC32C считается кусками естественно и быстро: слайсинг по 8 байт.
 *
 * Модуль без React и DOM: его целиком проверяет node --test
 * (tests/production_meta.test.mjs, контрольные значения из RFC 3720).
 */

const POLY = 0x82f63b78;

const TABLE = (() => {
    const table = new Uint32Array(256 * 8);
    for (let i = 0; i < 256; i += 1) {
        let c = i;
        for (let k = 0; k < 8; k += 1) c = (c & 1) ? (POLY ^ (c >>> 1)) : (c >>> 1);
        table[i] = c >>> 0;
    }
    for (let i = 0; i < 256; i += 1) {
        let c = table[i];
        for (let slice = 1; slice < 8; slice += 1) {
            c = table[c & 0xff] ^ (c >>> 8);
            table[slice * 256 + i] = c >>> 0;
        }
    }
    return table;
})();

/** Счётчик суммы: update(Uint8Array) сколько угодно раз, затем value(). */
export const createCrc32c = () => {
    let crc = 0xffffffff;
    const T = TABLE;
    return {
        update(bytes) {
            const data = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
            let c = crc;
            let i = 0;
            const n = data.length;
            while (n - i >= 8) {
                const a = (data[i] | (data[i + 1] << 8) | (data[i + 2] << 16) | (data[i + 3] << 24)) ^ c;
                const b = data[i + 4] | (data[i + 5] << 8) | (data[i + 6] << 16) | (data[i + 7] << 24);
                c = T[1792 + (a & 0xff)] ^ T[1536 + ((a >>> 8) & 0xff)]
                    ^ T[1280 + ((a >>> 16) & 0xff)] ^ T[1024 + (a >>> 24)]
                    ^ T[768 + (b & 0xff)] ^ T[512 + ((b >>> 8) & 0xff)]
                    ^ T[256 + ((b >>> 16) & 0xff)] ^ T[b >>> 24];
                i += 8;
            }
            while (i < n) {
                c = T[(c ^ data[i]) & 0xff] ^ (c >>> 8);
                i += 1;
            }
            crc = c >>> 0;
            return this;
        },
        value() {
            return (crc ^ 0xffffffff) >>> 0;
        },
    };
};

export const crc32c = (bytes) => createCrc32c().update(bytes).value();
