/* Точки парка: номер принадлежит паре «парк + место», где место это офис из
 * справочника или «онлайн» — парк без адреса, принимающий только по телефону.
 *
 * Отдельный модуль, а не часть ParkEditor: правила «что можно сохранить» и
 * преобразование формы в тело запроса нужны и форме, и списку парков, и тесту
 * (tests/wiki_park_points.test.mjs) — а тест не поднимет JSX.
 */

export const ONLINE = 'online';

/* Ключ строки нужен React'у, а id у новой строки ещё нет: индекс массива в
   роли ключа перепутал бы поля при удалении строки посередине. */
let sequence = 0;
const nextKey = () => { sequence += 1; return `point-${sequence}`; };

/* Повтор снимается и здесь, а не только на сервере (wiki/offices.clean_phones):
   иначе форма показывала бы номер дважды, а после сохранения он оставался бы
   один — и это читается как «правка не сохранилась». */
const cleanPhones = (phones) => [...new Set((phones || [])
    .map((phone) => phone.trim())
    .filter(Boolean))];

export const emptyPoint = (officeId) => ({ key: nextKey(), office_id: officeId, phones: [''] });

/* Точки для формы: сперва онлайн — это общий номер парка, за ним офисы в том
   порядке, в котором их отдал сервер (город, позиция). */
export const pointsFromPark = (park) => {
    const points = [];
    if (park.phones?.length) {
        points.push({ key: nextKey(), office_id: null, phones: [...park.phones] });
    }
    (park.offices || []).forEach((link) => points.push({
        key: nextKey(),
        office_id: link.office_id,
        phones: link.phones?.length ? [...link.phones] : [''],
    }));
    return points;
};

/* Обратное преобразование: офисы отдельно, номера без офиса отдельно — так их
   и хранит сервер (wiki_park_phones с office_id = NULL).

   Строки с одним и тем же офисом сливаются в одну. В форме такого не собрать —
   занятый офис в селекторе недоступен, — но на сервере пара «парк + офис»
   одна, и вторую строку он молча отбросил бы вместе с её номерами. */
export const pointsPayload = (points) => {
    const offices = [];
    (points || [])
        .filter((point) => typeof point.office_id === 'number')
        .forEach((point) => {
            const phones = cleanPhones(point.phones);
            const same = offices.find((office) => office.office_id === point.office_id);
            if (same) same.phones = cleanPhones([...same.phones, ...phones]);
            else offices.push({ office_id: point.office_id, phones });
        });
    return {
        offices,
        phones: cleanPhones((points || [])
            .filter((point) => point.office_id === null)
            .flatMap((point) => point.phones)),
    };
};

/* Что мешает сохранить. Строкой, а не булевым: кнопка гаснет, и без причины
   рядом это выглядит поломкой формы. */
export const parkDraftIssue = (draft) => {
    if (!draft) return null;
    if (!draft.name?.trim()) return 'Укажите название парка';
    const points = draft.points || [];
    if (points.some((point) => point.office_id === undefined)) {
        return 'В одной из строк не выбрано, куда звонят';
    }
    if (points.some((point) => cleanPhones(point.phones).length === 0)) {
        return 'В каждой строке нужен хотя бы один номер';
    }
    const places = points.map((point) => (point.office_id === null ? ONLINE : point.office_id));
    if (new Set(places).size !== places.length) {
        return 'Одно и то же место выбрано дважды — соберите его номера в одной строке';
    }
    return null;
};
