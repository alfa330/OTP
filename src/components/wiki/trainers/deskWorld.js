/* Мир рабочего места оператора — общий для свободной среды и режима звонка.
 *
 * Отдельным модулем, потому что сценариев два, а мир один: разъехавшиеся копии
 * означали бы, что «Диспетчерская помнит вкладку» в одном режиме есть, а в
 * другом нет, и искать такое пришлось бы руками.
 *
 * Всё, что видно на экранах, приезжает СЛЕПКОМ дела (см. caseData.js). Здесь
 * только состояние: где мы находимся и что уже сделали.
 */

import { emptyCall } from './callMachine.js';
import { prepareCase } from './caseData.js';
import { DEFAULT_CASE } from './fleetData.js';

/** Пустая форма обращения в CRM. */
export const emptyForm = (prefill = {}) => ({
    source: '', phone: '', license: '', account: '',
    park: '', city: '', cats: [], comment: '', duplicate: false,
    ...prefill,
});

/**
 * Собрать мир. caseData не приехал — берём запасной слепок: тренажёр обязан
 * открываться и без сервера.
 */
export const createDeskWorld = ({ today, caseData }) => {
    const prepared = prepareCase(caseData || DEFAULT_CASE, today);
    const clock = new Date();
    const two = (value) => String(value).padStart(2, '0');

    return {
        case: prepared,

        now: `${two(today.day)}.${two(today.month)}.${today.year} `
            + `${two(clock.getHours())}:${two(clock.getMinutes())}:${two(clock.getSeconds())}`,

        // Какая вкладка браузера открыта.
        tab: 'crm',

        // Форма обращения. Меняется свободно (browse), а не шагами.
        form: emptyForm(prepared.crm?.prefill),

        // Где мы внутри Диспетчерской.
        fleetView: 'contractors',
        fleetTab: 'details',
        /* Фильтры кабинета НАКАПЛИВАЮТСЯ между переходами — это правда
           настоящего кабинета, на ней сгорели три прогона съёмки. Поэтому
           набор, а не одно значение, и сбрасывается он только руками. */
        fleetFilters: [],
        fleetPanel: null,
        fleetMenu: null,
        fleetQuery: '',
        fleetOpenId: null,
        fleetOrderId: null,
        fleet404: false,

        // Где мы внутри Okapp.
        oktLogged: false,
        oktView: 'cabinet',
        oktIn: false,
        oktStatus: null,
        oktPhoneMenu: false,

        // Звонок. В свободной среде так и остаётся offline.
        call: emptyCall(),

        // Правки, сделанные над КОПИЕЙ слепка внутри попытки.
        edits: [],

        saved: false,
    };
};

/** Герой дела — тот, чья карточка заполнена. Остальные строки списка пустые. */
export const heroId = (world) => world?.case?.contractor?.id || null;

/** Открыт ли сейчас герой (у остальных карточка показывает пустые состояния). */
export const isHeroOpen = (world) => world?.fleetOpenId === heroId(world);
