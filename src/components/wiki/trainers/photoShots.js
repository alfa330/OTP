/* Что именно требует фотоконтроль машины в Яндекс Про.
 *
 * Список снят с приложения кадр в кадр: те же семь плиток, тот же порядок
 * сверху вниз и слева направо, те же подписи. Менять здесь ничего «для
 * красоты» нельзя — тренажёр стоит рядом с инструкцией, по которой водителя
 * потом проверяют, и любое расхождение он оплатит отказом фотоконтроля.
 *
 * Порядок в приложении НЕ совпадает с порядком обхода машины: сначала левый
 * борт, потом перёд, потом правый борт и корма, и только затем салон. Раньше
 * тренажёр вёл по-своему (перёд → борта → корма → салон → багажник), и человек,
 * запомнивший тренажёрный порядок, в приложении искал плитки не там.
 *
 * Модуль без React и без three намеренно: его читают сразу трое — сценарий
 * (шаги и проверки), экран телефона (плитки и видоискатель) и тесты.
 *
 *   view   — ракурс, который посчитает carFraming по положению человека;
 *   open   — что обязано быть ОТКРЫТО в этот момент (флаг мира);
 *   hold   — как приложение просит держать телефон. Пять кузовных кадров
 *            снимают ГОРИЗОНТАЛЬНО: силуэт и подпись на экране развёрнуты на
 *            90°, и это единственная просьба повернуть телефон за весь
 *            фотоконтроль. Салон снимают вертикально;
 *   outline — какой контур рисует приложение поверх кадра.
 */

export const SHOTS = [
    {
        key: 'left',
        title: 'Машина слева',
        view: 'left',
        open: null,
        hold: 'landscape',
        outline: 'side',
    },
    {
        key: 'front',
        title: 'Машина спереди',
        view: 'front',
        open: null,
        hold: 'landscape',
        outline: 'front',
    },
    {
        key: 'right',
        title: 'Машина справа',
        view: 'right',
        open: null,
        hold: 'landscape',
        outline: 'side',
    },
    {
        key: 'rear',
        title: 'Машина сзади',
        view: 'rear',
        open: null,
        hold: 'landscape',
        outline: 'rear',
    },
    {
        key: 'trunk',
        title: 'Открытый багажник',
        view: 'trunk',
        open: 'trunkOpen',
        hold: 'landscape',
        outline: 'trunk',
    },
    {
        key: 'seats_rear',
        title: 'Задний ряд сидений',
        view: 'inside_rear',
        open: 'doorRearLeft',
        hold: 'portrait',
        outline: 'seats_rear',
    },
    {
        key: 'seats_front',
        title: 'Передний ряд сидений',
        view: 'inside_front',
        open: 'doorFrontLeft',
        hold: 'portrait',
        outline: 'seats_front',
    },
];

/** Ключи в порядке приложения — им же нумеруются шаги урока. */
export const SHOT_KEYS = SHOTS.map((shot) => shot.key);

const BY_KEY = new Map(SHOTS.map((shot) => [shot.key, shot]));

export const shotByKey = (key) => BY_KEY.get(String(key || '')) || null;

/** Снятые кадры считаем по миру: там лежит либо миниатюра, либо true. */
export const shotDone = (world, key) => Boolean((world.shots || {})[key]);

/** Сколько плиток ещё пустые — это же число называет отказ по кнопке «Далее». */
export const shotsLeft = (world) => SHOTS.filter((shot) => !shotDone(world, shot.key)).length;

/** Первый неснятый кадр: подсветка плитки и объяснение «сейчас снимаем вот это». */
export const nextShot = (world) => SHOTS.find((shot) => !shotDone(world, shot.key)) || null;

/** Идентификатор нажатия по плитке. Один на весь код — разойдётся, и плитка
 *  перестанет открывать камеру, ничего при этом не сломав заметно. */
export const slotTap = (key) => `slot_${key}`;
