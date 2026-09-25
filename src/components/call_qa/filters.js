/* Фильтры раздела «ИИ-оценка» — одно место вместо литералов в трёх компонентах.
 *
 * Панель фильтров, запросы обоих списков и подбор «оценить из отбора» обязаны
 * понимать набор ОДИНАКОВО: пока правила жили внутри компонентов, вкладка
 * «Очередь ревью» и вкладка «Звонки» неизбежно разошлись бы, и один и тот же
 * отбор давал бы на них разные выборки.
 *
 * Модуль намеренно чистый (никакого React и axios): его поведение проверяется
 * node-тестом, а не через отрисовку раздела.
 *
 * Зеркало серверной проверки call_qa.api.normalise_list_filters. Пустое
 * значение здесь и там значит «фильтра нет», а не «показать пусто».
 */

const isSet = (value) => value !== null && value !== undefined && value !== '';

/** Ничего не отобрано. Именно этот объект — «чистое» состояние панели. */
export const EMPTY_FILTERS = {
    date_from: '',
    date_to: '',
    direction_id: null,
    group_id: null,
    operator_id: null,
    reviewed: '',      // '' | 'yes' | 'no' — есть ли оценка человека
    score_min: '',
    score_max: '',
    q: '',
    /* Маркетинговые оси (ТЗ #317, ФТ-05…ФТ-10). Зеркало call_qa/marketing/filters.py.
       Списки — массивами кодов/значений; «Не определено» — код NONE_BUCKET. */
    parks: [],
    channels: [],
    campaigns: [],
    stages: [],
    stage_mode: '',     // '' (текущий) | 'at_call' — этап на момент разговора
    reasons: [],
    handler_ids: [],
    handler_group_ids: [],
    /* Учётки CRM без сотрудника портала — «amo:8303491». Только в режиме
       «Ответственный»: у того, кто говорил, учётки CRM нет. */
    handler_keys: [],
    handler_mode: '',   // '' (говорил) | 'crm' — ответственный в amoCRM
    deal_id: '',
};

/* «Не определено» / «Причина не указана» — отдельная корзина, не пустой фильтр:
   у 60% сделок парк пуст, и без корзины самая большая группа лидов исчезала бы
   из любого разреза. Значение то же, что у сервера (mkt.NONE_BUCKET). */
export const NONE_BUCKET = 'none';

/* Оси, которые считает маркетинговый модуль. По этому списку и панель, и
   параметры, и чипы понимают, что относится к маркетингу, — а не по
   перечислению имён в трёх местах. */
export const MARKETING_LIST_KEYS = ['parks', 'channels', 'campaigns', 'stages', 'reasons',
                                    'handler_ids', 'handler_group_ids', 'handler_keys'];

/* Все поля маркетинговой части отбора, включая режимы и номер сделки. */
const MARKETING_KEYS = [...MARKETING_LIST_KEYS, 'stage_mode', 'handler_mode', 'deal_id'];

/** Снять только маркетинговые оси, остальной отбор оставить. */
export const clearMarketing = (filters) => {
    const next = { ...(filters || EMPTY_FILTERS) };
    MARKETING_KEYS.forEach((key) => { next[key] = EMPTY_FILTERS[key]; });
    return next;
};

/* Ключи чипов маркетинговых осей — по ним «Базе разборов» показываются только
   они: остальной отбор к правилам каталога не относится. */
export const MARKETING_CHIP_KEYS = ['parks', 'channels', 'stages', 'reasons', 'handlers', 'deal'];

/* Этап группы «Закрыто-нереализовано» — по нему ФТ-09 включает причину отказа.
   Маркеры те же, что в call_qa/marketing/filters.py (LOST_STAGE_MARKERS): гашение
   контрола на клиенте и отказ сервера обязаны совпадать, иначе панель разрешала
   бы отбор, который сервер отвергает. */
const LOST_STAGE_MARKERS = ['закрыто и не реализовано', 'закрыто-нереализовано',
                            'закрыто не реализовано'];
export const isLostStage = (value) => {
    const text = String(value || '').trim().toLowerCase();
    return LOST_STAGE_MARKERS.some((marker) => text.includes(marker));
};

/** Причина отказа доступна, только когда среди этапов есть «Закрыто-нереализовано». */
export const reasonsAllowed = (filters) => ((filters || EMPTY_FILTERS).stages || []).some(isLostStage);

const listOf = (value) => (Array.isArray(value) ? value.filter((item) => item !== null
    && item !== undefined && String(item) !== '') : []);

/** Есть ли хоть одна маркетинговая ось в отборе. */
export const hasMarketingFilters = (filters) => {
    const f = filters || EMPTY_FILTERS;
    return MARKETING_LIST_KEYS.some((key) => listOf(f[key]).length > 0) || isSet(f.deal_id);
};

/* Группа «без группы» — не пустой фильтр, а отдельная корзина: у части звонков
 * из АТС учётной записи нет вовсе (осталось имя из телефонии), и без этого
 * пункта такие строки просто пропадали бы из любого разреза по группам. Значение
 * строковое, потому что id у корзины быть не может. */
export const NO_GROUP = 'none';

/** Сколько фильтров отобрано — число на кнопке «Фильтры». Период считается ОДНИМ. */
export const countActiveFilters = (filters) => {
    const f = filters || EMPTY_FILTERS;
    let count = 0;
    if (isSet(f.date_from) || isSet(f.date_to)) count += 1;
    if (isSet(f.direction_id)) count += 1;
    if (isSet(f.group_id)) count += 1;
    if (isSet(f.operator_id)) count += 1;
    if (isSet(f.reviewed)) count += 1;
    if (isSet(f.score_min) || isSet(f.score_max)) count += 1;
    if (isSet(f.q)) count += 1;
    /* Маркетинг: канал с кампаниями — ОДИН фильтр (кампания — второй уровень
       канала), «кто обрабатывал» с группами — один, этап с режимом — один. */
    if (listOf(f.parks).length) count += 1;
    if (listOf(f.channels).length || listOf(f.campaigns).length) count += 1;
    if (listOf(f.stages).length) count += 1;
    if (listOf(f.reasons).length) count += 1;
    if (listOf(f.handler_ids).length || listOf(f.handler_group_ids).length
        || listOf(f.handler_keys).length) count += 1;
    if (isSet(f.deal_id)) count += 1;
    return count;
};

/** Сколько маркетинговых осей отобрано — число на кнопке в «Базе разборов». */
export const countMarketingFilters = (filters) => countActiveFilters(
    { ...EMPTY_FILTERS, ...Object.fromEntries(MARKETING_KEYS.map((key) => [key, (filters || EMPTY_FILTERS)[key]])) });

export const hasActiveFilters = (filters) => countActiveFilters(filters) > 0;

/** Параметры запроса. Пустые НЕ отправляем: сервер отличает «нет фильтра» от значения. */
export const filtersToParams = (filters) => {
    const f = filters || EMPTY_FILTERS;
    const params = {};
    if (isSet(f.date_from)) params.date_from = f.date_from;
    if (isSet(f.date_to)) params.date_to = f.date_to;
    if (isSet(f.direction_id)) params.direction_id = f.direction_id;
    if (isSet(f.group_id)) params.group_id = f.group_id;
    if (isSet(f.operator_id)) params.operator_id = f.operator_id;
    if (isSet(f.reviewed)) params.reviewed = f.reviewed;
    if (isSet(f.score_min)) params.score_min = f.score_min;
    if (isSet(f.score_max)) params.score_max = f.score_max;
    if (isSet(f.q)) params.q = String(f.q).trim();
    /* Списки уходят МАССИВАМИ: axios сериализует их повторённым параметром, и
       сервер читает getlist. Склеивать через запятую нельзя — имена этапов и
       причин сами содержат запятые («Нет авто (не цел), аренда»). */
    MARKETING_LIST_KEYS.forEach((key) => {
        const values = listOf(f[key]).map(String);
        if (values.length) params[key] = values;
    });
    // Режимы имеют смысл только при выбранных значениях — как и на сервере.
    if (listOf(f.stages).length && f.stage_mode === 'at_call') params.stage_mode = 'at_call';
    if ((listOf(f.handler_ids).length || listOf(f.handler_group_ids).length
         || listOf(f.handler_keys).length)
        && f.handler_mode === 'crm') params.handler_mode = 'crm';
    if (isSet(f.deal_id)) params.deal_id = String(f.deal_id).trim();
    return params;
};

/** Параметры только маркетинговых осей — для «Базы разборов». */
export const marketingParams = (filters) => {
    const params = filtersToParams(filters);
    return Object.fromEntries(Object.entries(params).filter(([key]) => MARKETING_KEYS.includes(key)));
};

/* Ключ отбора для зависимостей эффекта. Объект фильтров пересоздаётся на каждом
 * рендере, и класть его в deps — это бесконечный перезапрос (та же болезнь, что
 * у нестабильных колбэков в зависимостях). Строка сравнивается по значению. */
export const filtersKey = (filters) => JSON.stringify(filtersToParams(filters));

/** Балл ИИ: пустое поле — «без границы», иначе целое 0…100. */
export const clampScore = (value) => {
    const text = String(value ?? '').trim();
    if (!text) return '';
    const parsed = Math.round(Number(text));
    if (!Number.isFinite(parsed)) return '';
    return String(Math.max(0, Math.min(100, parsed)));
};

const shortDate = (iso) => {
    const parts = String(iso || '').split('-');
    return parts.length === 3 ? `${parts[2]}.${parts[1]}` : iso;
};

/* Имя фильтра перед значением («Группа · Ночная смена») помогает читать чип, но
 * только пока не дублирует само значение: у групп в портале названия вида
 * «Группа Тестбаевой», и выходило «Группа Группа Тестбаевой». Если значение уже
 * начинается с имени фильтра — имя опускаем. */
const chipName = (name, label) => (
    String(label).toLowerCase().startsWith(String(name).toLowerCase()) ? '' : name
);

/** Подписи отобранного — чипы под кнопкой. `clear` снимает ОДИН фильтр. */
export const activeFilterChips = (filters, options = {}) => {
    const f = filters || EMPTY_FILTERS;
    const { directions = [], groups = [], operators = [] } = options;
    const nameOf = (list, id) => (list.find((item) => String(item.id) === String(id)) || {}).name;
    const chips = [];
    const patch = (fields) => ({ ...f, ...fields });
    const add = (key, name, label, next) => chips.push(
        { key, name: chipName(name, label), label, next });

    if (isSet(f.date_from) || isSet(f.date_to)) {
        const label = isSet(f.date_from) && isSet(f.date_to)
            ? (f.date_from === f.date_to ? shortDate(f.date_from)
                : `${shortDate(f.date_from)} — ${shortDate(f.date_to)}`)
            : (isSet(f.date_from) ? `с ${shortDate(f.date_from)}` : `по ${shortDate(f.date_to)}`);
        add('period', 'Период', label, patch({ date_from: '', date_to: '' }));
    }
    if (isSet(f.direction_id)) {
        add('direction', 'Направление',
            nameOf(directions, f.direction_id) || `#${f.direction_id}`,
            patch({ direction_id: null }));
    }
    if (isSet(f.group_id)) {
        add('group', 'Группа',
            f.group_id === NO_GROUP
                ? 'без группы' : (nameOf(groups, f.group_id) || `#${f.group_id}`),
            patch({ group_id: null }));
    }
    if (isSet(f.operator_id)) {
        add('operator', 'Сотрудник',
            nameOf(operators, f.operator_id) || `#${f.operator_id}`,
            patch({ operator_id: null }));
    }
    if (isSet(f.reviewed)) {
        add('reviewed', 'Оценка человека', f.reviewed === 'yes' ? 'есть' : 'нет',
            patch({ reviewed: '' }));
    }
    if (isSet(f.score_min) || isSet(f.score_max)) {
        const label = isSet(f.score_min) && isSet(f.score_max)
            ? `${f.score_min}—${f.score_max}`
            : (isSet(f.score_min) ? `от ${f.score_min}` : `до ${f.score_max}`);
        add('score', 'Балл ИИ', label, patch({ score_min: '', score_max: '' }));
    }
    if (isSet(f.q)) {
        add('q', 'Поиск', f.q, patch({ q: '' }));
    }

    /* Маркетинг. Подписи — из справочника модуля (options.marketing); без него
       чип показывает код, и это лучше, чем пустой чип. Несколько значений одной
       оси — ОДИН чип «TikTok, OLX»: по чипу на значение панель превращалась бы в
       облако тегов, а снимается ось всё равно целиком. */
    const marketing = options.marketing || {};
    const titleOf = (list, code, fallback) => {
        const found = (list || []).find((item) => String(item.code ?? item.value ?? item.id) === String(code));
        return found ? (found.title || found.name || found.value) : (code === NONE_BUCKET ? fallback : code);
    };
    const joined = (values) => values.join(', ');
    const parks = listOf(f.parks);
    if (parks.length) {
        add('parks', 'Таксопарк', joined(parks.map((code) => titleOf(marketing.parks, code, 'не определён'))),
            patch({ parks: [] }));
    }
    const channels = listOf(f.channels);
    const campaigns = listOf(f.campaigns);
    if (channels.length || campaigns.length) {
        const parts = channels.map((code) => titleOf(marketing.channels, code, 'не определён'));
        /* Кампания хранится парой «канал|кампания». В чипе — «Google / весна»,
           а если этот канал и так выбран целиком — только имя кампании: иначе
           вышло бы «Google, Google / весна». */
        if (campaigns.length) {
            parts.push(...campaigns.map((pair) => {
                const text = String(pair);
                const cut = text.indexOf('|');
                if (cut < 0) return text;
                const channel = text.slice(0, cut);
                const name = text.slice(cut + 1);
                return channels.includes(channel)
                    ? name : `${titleOf(marketing.channels, channel, 'не определён')} / ${name}`;
            }));
        }
        add('channels', 'Канал', joined(parts), patch({ channels: [], campaigns: [] }));
    }
    const stages = listOf(f.stages);
    if (stages.length) {
        const name = f.stage_mode === 'at_call' ? 'Этап на момент разговора' : 'Этап';
        // Снятие этапов снимает и причину: без этапа «Закрыто-нереализовано»
        // причина недопустима (ФТ-09), и сервер отверг бы такой отбор.
        add('stages', name, joined(stages), patch({ stages: [], stage_mode: '', reasons: [] }));
    }
    const reasons = listOf(f.reasons);
    if (reasons.length) {
        add('reasons', 'Причина', joined(reasons.map((code) => (code === NONE_BUCKET ? 'не указана' : code))),
            patch({ reasons: [] }));
    }
    const handlers = listOf(f.handler_ids);
    const handlerGroups = listOf(f.handler_group_ids);
    const handlerKeys = listOf(f.handler_keys);
    if (handlers.length || handlerGroups.length || handlerKeys.length) {
        const crm = f.handler_mode === 'crm';
        /* В режиме «Ответственный» имя берём из справочника ответственных, а не
           из списка операторов: id там — сотрудник портала, сопоставленный с
           учёткой CRM, и у операторов раздела его может не быть вовсе. */
        const people = handlers.map((id) => (crm
            ? (marketing.handlers || []).find((item) => String(item.id) === String(id))?.name
            : null) || nameOf(operators, id) || `#${id}`);
        const accounts = handlerKeys.map((key) => (marketing.handlers || [])
            .find((item) => item.key === key)?.name || key);
        const groupNames = handlerGroups.map((id) => nameOf(groups, id) || `#${id}`);
        add('handlers', crm ? 'Ответственный' : 'Говорил',
            joined([...people, ...accounts, ...groupNames]),
            patch({ handler_ids: [], handler_group_ids: [], handler_keys: [], handler_mode: '' }));
    }
    if (isSet(f.deal_id)) {
        add('deal', 'Сделка', `№ ${f.deal_id}`, patch({ deal_id: '' }));
    }
    return chips;
};

/* Что из отбора принимает подтяжка «Из АТС».
 *
 * Сотрудник и период уходят в саму АТС — это её параметры выборки. Направление и
 * группа в АТС не уходят (она про них не знает), но сужают КРУГ ЛЮДЕЙ, среди
 * которых портал ищет, чей звонок подтянуть: без них выбранная в панели группа
 * молча игнорировалась и портал шёл за звонком случайного человека всего отдела.
 *
 * Балл ИИ и наличие оценки человека не идут вовсе: у ещё не подтянутого звонка
 * ни того, ни другого не существует. Поиск по имени — тоже: сотрудника здесь
 * выбирают селектором. */
export const pullParamsFromFilters = (filters) => {
    const f = filters || EMPTY_FILTERS;
    const params = {};
    if (isSet(f.operator_id)) params.operator_id = f.operator_id;
    if (isSet(f.date_from)) params.date_from = f.date_from;
    if (isSet(f.date_to)) params.date_to = f.date_to;
    if (isSet(f.direction_id)) params.direction_id = f.direction_id;
    if (isSet(f.group_id)) params.group_id = f.group_id;
    return params;
};

/** Люди, подходящие под уже выбранные направление и группу — для селектора и подтяжки. */
export const operatorsMatching = (operators, filters) => {
    const f = filters || EMPTY_FILTERS;
    return (operators || []).filter((person) => {
        if (isSet(f.direction_id)
            && String(person.direction_id) !== String(f.direction_id)) return false;
        if (isSet(f.group_id)) {
            if (f.group_id === NO_GROUP) return person.group_id == null;
            if (String(person.group_id) !== String(f.group_id)) return false;
        }
        return true;
    });
};
