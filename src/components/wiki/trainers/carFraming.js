/* Что попало в кадр: ракурс и дальность.
 *
 * Модуль чистый — ни three.js, ни DOM. Причина та же, по которой чист движок
 * сценария: «спереди» и «слишком близко» — это ПРАВИЛА фотоконтроля, и
 * проверять их надо тестами, а не обходя трёхмерную машину мышкой по кругу.
 * Сцена присылает сюда три числа (азимут, дистанцию, что открыто) и получает
 * готовый ответ, который уходит в сценарий как payload затвора.
 *
 * Азимут отсчитывается от кормы по часовой стрелке — так же, как устроена
 * сцена: 0° — сзади, 90° — правый борт, 180° — спереди, 270° — левый борт.
 */

/** Азимут в диапазон 0…360 — камера крутится бесконечно в обе стороны. */
export const normalizeAzimuth = (deg) => ((Number(deg) || 0) % 360 + 360) % 360;

/** Расстояние между азимутами по кратчайшей дуге. */
export const azimuthGap = (a, b) => {
    const diff = Math.abs(normalizeAzimuth(a) - normalizeAzimuth(b));
    return diff > 180 ? 360 - diff : diff;
};

/* Сектора кузовных кадров.
 *
 * Борта уже носа и кормы намеренно: перед и зад машина «держит» в кадре с
 * заметного разброса, а вот бок, снятый под 40°, превращается в три четверти —
 * именно такие фото и заворачивает проверка. */
const BODY_VIEWS = [
    { view: 'rear', at: 0, tolerance: 26 },
    { view: 'right', at: 90, tolerance: 22 },
    { view: 'front', at: 180, tolerance: 26 },
    { view: 'left', at: 270, tolerance: 22 },
];

/* Кадры «внутрь»: они возможны только там, где открыта нужная дверь, и только
   с близкого расстояния — снять салон с шести метров нельзя, туда попадёт весь
   борт.
   Азимуты отсчитываются от кормы, а нос машины — на 180°, поэтому у левого
   борта ближе к носу лежит ВОДИТЕЛЬСКАЯ дверь (около 250°), а ближе к корме —
   задняя (около 292°). Сначала эти два значения стояли наоборот: тренажёр
   засчитывал «передний ряд», когда человек стоял у задней двери. */
const INSIDE_VIEWS = [
    { view: 'inside_front', at: 250, tolerance: 28, requires: 'doorFrontLeft', maxDistance: 3.6 },
    { view: 'inside_rear', at: 294, tolerance: 28, requires: 'doorRearLeft', maxDistance: 3.6 },
    { view: 'trunk', at: 0, tolerance: 30, requires: 'trunkOpen', maxDistance: 4.4 },
];

/** Пороги дальности для кузовных кадров, в метрах. Считаны для сверхширика
 *  0,5x — им фотоконтроль и снимают: только с ним машина помещается целиком с
 *  трёх-четырёх метров. */
export const BODY_RANGE = { near: 3.2, far: 7.2 };
/** Для кадров внутрь порог свой: там нужно подойти вплотную. */
export const INSIDE_RANGE = { near: 1.1, far: 4.2 };

/* Насколько дальше надо встать с основным объективом.
 *
 * Отношение углов обзора: 2·tg(64°/2) ÷ 2·tg(42°/2) ≈ 1,63. Переключив 0,5x на
 * 1x, человек сужает кадр ровно во столько раз, и «нормальная» дистанция
 * уезжает туда же. Без этого множителя тренажёр говорил бы «дистанция в
 * порядке» над машиной, которая в силуэт заведомо не влезает: правило считало
 * бы по ширику, а картинка рисовалась бы обычным объективом. */
export const MAIN_LENS_FACTOR = 1.63;

/* Флага нет — считаем, что снимают шириком: правила писались под него, и
   вызовы без состояния камеры (тесты, старый код) обязаны остаться при своих
   порогах. Мир сценария флаг задаёт явно. */
const lensFactor = (opened) => (opened.wide === false ? MAIN_LENS_FACTOR : 1);

/**
 * Что видит камера.
 * @param {object} camera  {azimuth, distance}
 * @param {object} opened  флаги мира: doorFrontLeft, doorRearLeft, trunkOpen
 * @returns {{view: string|null, framing: 'close'|'ok'|'far'}}
 */
export const readFrame = (camera, opened = {}) => {
    const azimuth = normalizeAzimuth(camera.azimuth);
    const distance = Number(camera.distance) || 0;
    /* Зум пересчитывается в дистанцию, а не в отдельные пороги: «сколько метров
       до машины» и «какой объектив» действуют на кадр одинаково, и держать две
       таблицы порогов значило бы держать их в согласии руками. */
    const factor = lensFactor(opened);

    /* Сначала кадры «внутрь». Порядок не косметический: у открытой водительской
       двери азимут 292° попадает и в сектор левого борта, и в сектор салона.
       Человек, подошедший вплотную к раскрытой двери, снимает именно салон —
       иначе на шаге «передний ряд» он получал бы замечание про борт. */
    const inside = INSIDE_VIEWS.find((spec) => opened[spec.requires]
        && distance <= spec.maxDistance * factor
        && azimuthGap(azimuth, spec.at) <= spec.tolerance);
    if (inside) {
        return { view: inside.view, framing: rangeOf(distance / factor, INSIDE_RANGE) };
    }

    const body = BODY_VIEWS.find((spec) => azimuthGap(azimuth, spec.at) <= spec.tolerance);
    return {
        view: body ? body.view : null,
        framing: rangeOf(distance / factor, BODY_RANGE),
    };
};

const rangeOf = (distance, range) => {
    if (distance < range.near) return 'close';
    if (distance > range.far) return 'far';
    return 'ok';
};

/** Куда встать для нужного кадра — этим пользуется подсказка «дожми ещё». */
export const targetAzimuth = (view) => {
    const body = BODY_VIEWS.find((spec) => spec.view === view);
    if (body) return body.at;
    const inside = INSIDE_VIEWS.find((spec) => spec.view === view);
    return inside ? inside.at : null;
};

/** Насколько человек промахнулся мимо нужного ракурса, в градусах. */
export const azimuthError = (view, azimuth) => {
    const at = targetAzimuth(view);
    return at === null ? null : azimuthGap(azimuth, at);
};
