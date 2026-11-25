/* Мир рабочего места оператора — общий для курса, аттестации и режима звонка.
 *
 * Рабочее место — одна учебная CRM (владелец, 23.11.2026: Диспетчерская и
 * Oktell не нужны, всё проверяется через CRM). Отдельным модулем, потому что
 * сценариев несколько, а мир один: разъехавшиеся копии означали бы, что
 * «CRM помнит вкладку карточки» в одном режиме есть, а в другом нет.
 *
 * Всё, что видно на экранах, приезжает СЛЕПКАМИ дел (crmPeople.js, даты —
 * caseData.prepareCase). Здесь только состояние: где мы находимся и что уже
 * сделали.
 */

import { emptyCall } from './callMachine.js';
import { isFleetContract, prepareCase } from './caseData.js';
import { prepareFleetCase } from './fleetCase.js';
import { emptyForm } from './crmModel.js';
import { emptyYaForm } from './crmYandex.js';
import { CRM_PEOPLE, DEFAULT_CASE } from './crmPeople.js';

export { emptyForm };

/**
 * Собрать мир. caseData — дело героя; не приехал — берём учебное. Остальные
 * водители учебной CRM — всегда свои: по ним идут курс и аттестация.
 */
/* Стартовая страница Диспетчерской (вкладка 'fleet' среды Windows). */
const FLEET_START = {
    fleetView: 'contractors',
    fleetTab: 'details',
    // Фильтры кабинета накапливаются между переходами, как в настоящем.
    fleetFilters: [],
    fleetPanel: null,
    fleetMenu: null,
    fleetQuery: '',
    fleetOpenId: null,
    fleetOrderId: null,
    fleet404: false,
};

export const createDeskWorld = ({ today, caseData }) => {
    const prepared = prepareCase(caseData || DEFAULT_CASE, today);
    const heroId = prepared.contractor?.id;
    const people = CRM_PEOPLE
        .filter((person) => person.contractor.id !== heroId)
        .map((person) => prepareCase(person, today));
    const clock = new Date();
    const two = (value) => String(value).padStart(2, '0');

    /* Дело в форме кабинета (дела голосового тренажёра) открывается и в
       Диспетчерской — третьей вкладке браузера среды Windows. */
    const raw = caseData || DEFAULT_CASE;
    const fleetCase = raw && isFleetContract(raw) ? prepareFleetCase(raw, today) : null;

    return {
        case: prepared,
        // Все водители учебной CRM: герой первым — у него учётка 50417.
        crmCases: [prepared, ...people],
        // Диспетчерская: дело в форме кабинета или null (вкладки нет).
        fleetCase,

        now: `${two(today.day)}.${two(today.month)}.${today.year} `
            + `${two(clock.getHours())}:${two(clock.getMinutes())}:${two(clock.getSeconds())}`,
        /* «Сейчас» тренажёра числом: от него считаются период вкладок карточки
           и времена в учебных списках CRM («обновлено 40 минут назад»). */
        nowMs: new Date(today.year, today.month - 1, today.day,
            clock.getHours(), clock.getMinutes()).getTime(),

        /* Вкладка браузера: 'crm' — «Обращения»; в среде Windows ещё 'crm_ya' —
           «Обращения в Яндекс» и 'fleet' — Диспетчерская. Страница CRM у каждой
           вкладки своя (tabPages), см. screensCrm.switchTab. */
        tab: 'crm',
        tabPages: {},

        // Где мы внутри Диспетчерской — только когда она есть: у курса её нет.
        ...(fleetCase ? FLEET_START : {}),

        /* Где мы внутри CRM. Браузер оператора на смене открыт на «Создать
           обращение» — с него и начинаем. */
        crmView: 'request_new',
        crmStub: null,
        crmGroups: ['tickets'],
        crmMenuQuery: '',
        // Уведомление CRM снизу справа («Готово · Обращение создано»).
        crmNote: null,
        // Сохранённые в этой попытке обращения — для списка «Обращения».
        crmRequests: [],
        // Обращение, открытое из списка «Обращения» (глазок в строке).
        crmShown: null,

        // Форма обращения.
        form: emptyForm(prepared.crm?.prefill),

        /* «Учетные записи водителей»: что искали, что нашлось, что открыто.
           История поиска — своя у попытки, как у учётки оператора в CRM. */
        drvQuery: '',
        drvSearched: null,
        drvHistory: [],
        drvOpenId: null,
        drvOpenNumber: null,
        drvTab: 'general',
        drvModal: null,
        drvOrder: null,
        drvOpenedAt: null,
        // Обновлённые из «Диспетчерской» карточки: ID водителя → время.
        drvRefreshed: {},
        // «Код»: кому и когда «отправили» новый код (учебно, без SMS).
        drvCodeSent: [],

        /* «Обращения в Яндекс»: форма, отправленные в попытке, открытое,
           ответы стажёра в переписке и прочитанные ответы поддержки. */
        yaForm: emptyYaForm(),
        // Применённый фильтр списка (null — без фильтра).
        yaFilter: null,
        yaAppeals: [],
        yaOpen: null,
        yaThreads: {},
        yaRead: [],

        /* Линия. Звонки приходят только тому, кто на линии: в режиме звонка
           стажёр на линии с начала смены, в курсе и аттестации звонков нет. */
        onLine: false,
        call: emptyCall(),

        // Итог аттестации: ключ задания → { ok, problem }.
        exam: {},

        saved: false,
    };
};

/** Герой дела — тот, кто звонит в режиме звонка и с кем идёт курс. */
export const heroId = (world) => world?.case?.contractor?.id || null;
