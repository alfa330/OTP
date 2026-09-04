import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';
import CustomSelect from '../ui/CustomSelect';
import {
    APPLE_FONT, iosCard, iosInput, iosGroupLabel,
    iosBtnPrimary, iosBtnSecondary, iosBtnGhost,
    IosBadge, IosModal, IosToggle,
} from '../ui/ios';

/*
 * Раздел «Настройки SIP» (iCORE Phone).
 *
 * Один компонент обслуживает два пункта меню — «Таксопарки» (локальная АТС
 * Asterisk) и «Tez» (облачная Binotel). Разводит их проп provider: от него
 * зависят набор вкладок, поля в карточке сотрудника и параметр запроса.
 * Форкать компонент незачем — список, поиск, выделение и история одинаковы.
 *
 *   provider='asterisk' — вкладки Сотрудники / Общие / История. На «Общих»
 *                         только карточки отделов: у каждой АТС свой домен,
 *                         своя база пароля и свой код автодозвона.
 *   provider='binotel'  — вкладки Сотрудники / История. Вкладки «Общие» нет:
 *                         у Binotel сервер, логин и пароль персональные,
 *                         задавать «на отдел» там нечего.
 *
 * Глобального яруса «общие настройки для отделов без своих» больше нет ни у
 * того, ни у другого: значения по умолчанию берутся ТОЛЬКО из карточки отдела.
 */

// Провайдер телефонии отдела. Переезд между этими двумя значениями и есть
// переезд отдела между разделами «Таксопарки» и «Tez».
const PROVIDER_CHOICES = [
    { id: 'asterisk', label: 'Локальная АТС', icon: 'fas fa-server' },
    { id: 'binotel', label: 'Binotel', icon: 'fas fa-cloud' },
];

// Разделы одного экрана «Настройки SIP». Провайдер выбирается здесь, а не
// отдельным пунктом сайдбара: админу нужно сравнивать отделы, а не прыгать.
const PROVIDER_SECTIONS = [
    { id: 'asterisk', label: 'Таксопарки', icon: 'fas fa-headset',
      hint: 'Отделы на локальной АТС: общий сервер и база пароля' },
    { id: 'binotel', label: 'Тез', icon: 'fas fa-phone-volume',
      hint: 'Отдел на Binotel: у каждого свой сервер, логин и пароль' },
];

const TABS_BY_PROVIDER = {
    asterisk: [
        { id: 'operators', label: 'Сотрудники' },
        { id: 'common', label: 'Общие' },
        { id: 'history', label: 'История' },
    ],
    binotel: [
        { id: 'operators', label: 'Сотрудники' },
        { id: 'history', label: 'История' },
    ],
};

// Заголовок повторяет подпись пункта меню: два раздела ведут в один компонент,
// и без разной шапки непонятно, где ты находишься.
const SECTION_TITLE = {
    asterisk: 'Настройки SIP — Таксопарки',
    binotel: 'Настройки SIP — Tez',
};

// Адрес кабинета по умолчанию — тот же, что в database.py
// (BINOTEL_CABINET_URL_DEFAULT): пустое поле у сотрудника значит именно его.
const BINOTEL_CABINET_URL_DEFAULT = 'https://my.binotel.kz';

const EMPTY_FORM = {
    sip_number: '', sip_password: '', sip_domain: '',
    autodial_number: '', autodial_password: '', autodial_domain: '',
    fop2_enabled: true,
    // Поля Binotel: логин у провайдера и учётка кабинета my.binotel.kz.
    sip_login: '', binotel_cabinet_login: '', binotel_cabinet_password: '',
    binotel_employee_id: '', binotel_cabinet_url: '',
};

// Массово меняются пароль, домен и вход в FOP2: номера у каждого свои.
const BULK_FIELDS = [
    { key: 'sip_domain', label: 'Домен основного номера', secret: false },
    { key: 'sip_password', label: 'Пароль основного номера', secret: true },
    { key: 'autodial_domain', label: 'Домен автодозвона', secret: false },
    { key: 'autodial_password', label: 'Пароль автодозвона', secret: true },
    // Не текст, а состояние: у выключателя нет «общего» значения, к которому
    // возвращает пустая строка, — поэтому и выбор из двух вариантов, а не поле.
    { key: 'fop2_enabled', label: 'Вход в FOP2', flag: true },
];

// Выключатель — не текст: «пусто» для него ничего не возвращает, зато нужно
// третье состояние «не трогать». Одним переключателем на три положения, а не
// тумблером «менять это поле» рядом с подписью «Вход в FOP2»: зелёный тумблер
// там читался бы как сам вход, и «Не входит» ниже противоречило бы ему.
const BULK_FLAG_CHOICES = [
    { on: false, value: false, label: 'Не менять' },
    { on: true, value: true, label: 'Входит' },
    { on: true, value: false, label: 'Не входит' },
];

const bulkFlagPicked = (state, choice) => (
    state.on === choice.on && (!choice.on || state.value === choice.value)
);

const EMPTY_BULK = BULK_FIELDS.reduce(
    (acc, f) => ({ ...acc, [f.key]: { on: false, value: f.flag ? false : '' } }), {});

const formFromOperator = (op) => ({
    sip_number: op?.sip_number || '',
    sip_password: op?.sip_password || '',
    sip_domain: op?.sip_domain || '',
    autodial_number: op?.autodial_number || '',
    autodial_password: op?.autodial_password || '',
    autodial_domain: op?.autodial_domain || '',
    // Вход в FOP2 включён у всех, кому его отдельно не выключили: у записей,
    // созданных до появления флага, поля просто нет — это не «выключено».
    fop2_enabled: op?.fop2_enabled !== false,
    sip_login: op?.sip_login || '',
    binotel_cabinet_login: op?.binotel_cabinet_login || '',
    // Пароля кабинета в ответе нет никогда — только признак «задан». Пустое поле
    // уходит на бэкенд как «не менять», поэтому и стартуем всегда с пустого.
    binotel_cabinet_password: '',
    binotel_employee_id: op?.binotel_employee_id ? String(op.binotel_employee_id) : '',
    binotel_cabinet_url: op?.binotel_cabinet_url || '',
});

const hasPersonalPassword = (op) => Boolean(op?.sip_password || op?.autodial_password);

// Интересно только выключенное состояние: включённый FOP2 — норма, и помечать
// им весь список нечего.
const fop2Disabled = (op) => op?.fop2_enabled === false;

const hasPersonalParams = (op) => Boolean(
    op?.sip_password || op?.sip_domain || op?.autodial_password || op?.autodial_domain
);

// Номер уникален в пределах домена: на разных АТС одинаковые внутренние номера —
// норма. Поэтому и подсветка дублей, и проверка занятости идут по паре.
// База пароля может быть шаблоном: «Secret{номер}!» → «Secret1024!».
// Без плейсхолдера — по-старому, база + номер (те же правила, что на бэкенде).
const PASSWORD_PLACEHOLDER = /\{[^{}]*\}/;
const buildSipPassword = (template, number) => {
    const tpl = String(template || '');
    const num = String(number || '').trim();
    if (!tpl || !num) return '';
    return PASSWORD_PLACEHOLDER.test(tpl) ? tpl.replace(new RegExp(PASSWORD_PLACEHOLDER, 'g'), num) : `${tpl}${num}`;
};

const normDomain = (value) => String(value || '').trim().toLowerCase();
const effectiveDomain = (personal, common) => normDomain(personal) || normDomain(common);
const numberKey = (number, domain) => `${String(number || '').trim()}@${normDomain(domain)}`;

// SIP-логин Binotel сравниваем без регистра: провайдер отдаёт его как есть,
// а два одинаковых логина выбивают друг друга из регистрации.
const normLogin = (value) => String(value || '').trim().toLowerCase();

// Псевдо-значение фильтра для сотрудников, у чьего отдела домен не задан. Без
// него такие строки исчезали из списка при любом выборе домена — и выглядело
// это как «сотрудников нет», а не «телефония не настроена».
const NO_DOMAIN = 'no-domain';

const fmtSize = (bytes) => {
    const value = Number(bytes || 0);
    if (!value) return '';
    return `${(value / (1024 * 1024)).toFixed(1)} МБ`;
};

const fmtDateTime = (iso) => {
    if (!iso) return '';
    try {
        return new Date(iso).toLocaleString('ru-RU', {
            day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
        });
    } catch { return iso; }
};

/* Поле с показом/скрытием пароля — одинаковое в форме сотрудника и в общих. */
const SecretInput = ({ value, onChange, placeholder, disabled }) => {
    const [shown, setShown] = useState(false);
    return (
        <div className="relative">
            <input
                type={shown ? 'text' : 'password'}
                value={value}
                onChange={(e) => onChange(e.target.value)}
                placeholder={placeholder}
                disabled={disabled}
                autoComplete="new-password"
                className={`${iosInput} pr-10`}
            />
            <button
                type="button"
                onClick={() => setShown((v) => !v)}
                aria-label={shown ? 'Скрыть' : 'Показать'}
                className="absolute inset-y-0 right-0 grid w-10 place-items-center text-slate-400 transition hover:text-slate-600"
            >
                <FaIcon className={`fas ${shown ? 'fa-eye-slash' : 'fa-eye'}`} style={{ width: 14, height: 14 }} />
            </button>
        </div>
    );
};

// canDownloadPhone — программа iCORE Phone положена отделу продаж и админам, а раздел
// «Настройки SIP» ведут главы любых отделов. Поэтому право на скачивание приходит
// отдельным пропом, а не выводится из canEdit.
// allowedProviders — разделы, доступные этому человеку: у главы отдела он один,
// у админа оба. Открытый раздел живёт в состоянии, а не в пропе, потому что
// переключается прямо здесь, без ухода из раздела.
const SipSettingsView = ({ user, showToast, apiBaseUrl, withAccessTokenHeader, canEdit = true,
                           canDownloadPhone = false, allowedProviders = ['asterisk'],
                           initialProvider = '', canSwitchProvider = false }) => {
    const [provider, setProvider] = useState(() => (
        allowedProviders.includes(initialProvider) ? initialProvider : (allowedProviders[0] || 'asterisk')
    ));
    const isBinotel = provider === 'binotel';
    const tabs = TABS_BY_PROVIDER[provider] || TABS_BY_PROVIDER.asterisk;
    const [tab, setTab] = useState('operators');

    const [operators, setOperators] = useState([]);
    const [departments, setDepartments] = useState([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState('');
    const [departmentFilter, setDepartmentFilter] = useState('');
    const [domainFilter, setDomainFilter] = useState('');
    const [showInactive, setShowInactive] = useState(false);

    const [editing, setEditing] = useState(null);   // строка сотрудника
    const [form, setForm] = useState({ ...EMPTY_FORM });
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [saving, setSaving] = useState(false);
    // Пароль кабинета Binotel — только на запись: пока его не собираются менять,
    // поля вообще нет, иначе пустое поле читалось бы как «пароля нет».
    const [replacingCabinetPassword, setReplacingCabinetPassword] = useState(false);
    const [resolvingEmployee, setResolvingEmployee] = useState(false);

    // Множественный выбор: Ctrl/⌘ + клик — по одному, Shift + клик — диапазон.
    const [selected, setSelected] = useState(() => new Set());
    const [bulkOpen, setBulkOpen] = useState(false);
    const [bulkForm, setBulkForm] = useState(EMPTY_BULK);
    const [bulkSaving, setBulkSaving] = useState(false);
    const anchorRef = useRef(null);

    const [deptEditing, setDeptEditing] = useState(null);
    const [deptForm, setDeptForm] = useState({
        sip_server: '', base_password: '', autodial_code: '', autodial_server: '', autodial_base_password: '',
        provider: 'asterisk',
    });
    const [deptSaving, setDeptSaving] = useState(false);

    const [history, setHistory] = useState([]);
    const [historyLoading, setHistoryLoading] = useState(false);
    const historyLoadedRef = useRef(false);

    // Версия телефона живёт своей жизнью: манифест публичный и к настройкам SIP
    // отношения не имеет, поэтому и грузится отдельно — сбой здесь не должен
    // мешать править номера.
    const [release, setRelease] = useState(null);
    const [releaseLoading, setReleaseLoading] = useState(true);
    const [releaseError, setReleaseError] = useState('');
    const [downloading, setDownloading] = useState(false);

    // showToast меняет идентичность при каждом тосте — держим в ref, чтобы не
    // перезапускать загрузку списка.
    const showToastRef = useRef(showToast);
    showToastRef.current = showToast;

    const authHeaders = useCallback(
        (extra = {}) => withAccessTokenHeader({ 'X-User-Id': String(user?.id ?? ''), ...extra }),
        [withAccessTokenHeader, user?.id]
    );

    /* ─── загрузка ─── */
    // Список и настройки отделов приходят одним запросом — второй вызов не нужен.
    // Переезд в другой раздел: списки, фильтры и выделение к нему не относятся,
    // а вкладки у провайдеров разные — «Общие» в Tez нет вообще.
    const switchProvider = (next) => {
        if (next === provider || !allowedProviders.includes(next)) return;
        setProvider(next);
        setTab('operators');
        setSearch('');
        setDepartmentFilter('');
        setDomainFilter('');
        setSelected(new Set());
        setEditing(null);
        setDeptEditing(null);
        historyLoadedRef.current = false;
    };

    // Переход из карточки сотрудника просит открыть раздел его отдела.
    useEffect(() => {
        if (initialProvider && allowedProviders.includes(initialProvider)) {
            setProvider(initialProvider);
            setTab('operators');
        }
    }, [initialProvider]);   // eslint-disable-line react-hooks/exhaustive-deps

    const fetchOperators = useCallback(async () => {
        setLoading(true);
        try {
            // Провайдер уходит параметром: «Таксопарки» и «Tez» смотрят на разные
            // отделы, и смешивать их в одном списке нельзя — поля у них разные.
            const qs = `?provider=${encodeURIComponent(provider)}${showInactive ? '&include_inactive=1' : ''}`;
            const resp = await fetch(`${apiBaseUrl}/api/sip_config/operators${qs}`, {
                credentials: 'include',
                headers: authHeaders(),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            // Фильтр по провайдеру дублируем на клиенте: если параметр не учтён,
            // в разделе Tez окажутся операторы локальной АТС с пустыми логинами —
            // и это выглядело бы как потерянные настройки, а не как чужой список.
            const sameProvider = (row) => (row?.department_provider || row?.provider || 'asterisk') === provider;
            setOperators(Array.isArray(data.operators) ? data.operators.filter(sameProvider) : []);
            setDepartments(Array.isArray(data.departments) ? data.departments.filter(sameProvider) : []);
        } catch (e) {
            showToastRef.current?.(`Не удалось загрузить настройки SIP: ${e.message}`, 'error');
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, authHeaders, showInactive, provider]);

    useEffect(() => { fetchOperators(); }, [fetchOperators]);

    const fetchHistory = useCallback(async () => {
        setHistoryLoading(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/sip_config/history?limit=100`, {
                credentials: 'include',
                headers: authHeaders(),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            setHistory(Array.isArray(data.history) ? data.history : []);
            historyLoadedRef.current = true;
        } catch (e) {
            showToastRef.current?.(`Не удалось загрузить историю: ${e.message}`, 'error');
        } finally {
            setHistoryLoading(false);
        }
    }, [apiBaseUrl, authHeaders]);

    // Манифест версии публичный (телефон читает его до входа оператора), поэтому
    // без токена; cache: 'no-store' — чтобы после публикации не показывать старое.
    useEffect(() => {
        // Кому телефон не положен, тому и версия ни к чему — не ходим за манифестом.
        // В разделе Tez блока со скачиванием нет вовсе: он живёт на вкладке
        // «Общие», а её там нет.
        if (!canDownloadPhone || isBinotel) { setReleaseLoading(false); return undefined; }
        let alive = true;
        (async () => {
            try {
                const resp = await fetch(`${apiBaseUrl}/api/phone/version`, { cache: 'no-store' });
                const data = await resp.json().catch(() => ({}));
                if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
                if (!alive) return;
                setRelease(data.release || null);
                setReleaseError('');
            } catch (e) {
                if (alive) setReleaseError(e.message);
            } finally {
                if (alive) setReleaseLoading(false);
            }
        })();
        return () => { alive = false; };
    }, [apiBaseUrl, canDownloadPhone, isBinotel]);

    // История нужна редко — грузим при первом открытии вкладки.
    useEffect(() => {
        if (tab === 'history' && !historyLoadedRef.current) fetchHistory();
    }, [tab, fetchHistory]);

    /* ─── производные данные ─── */
    // Значения по умолчанию для сотрудника — только настройки его отдела.
    // Общего яруса больше нет: у каждой АТС свой домен и своя база пароля, и
    // «запасное» общее значение молча уводило номера не на ту станцию.
    // У автодозвона свой домен — часто это отдельная АТС; не задан — как основной.
    const commonFor = useCallback((op) => {
        const server = op?.department_sip_server || '';
        const base = op?.department_base_password || '';
        return {
            server,
            autodialServer: op?.department_autodial_server || server,
            base,
            // У автодозвона своя база пароля; не задана — как у основного номера.
            autodialBase: op?.department_autodial_base_password || base,
            code: op?.department_autodial_code || '',
        };
    }, []);

    const departmentOptions = useMemo(() => {
        const seen = new Map();
        operators.forEach((op) => {
            if (op.department_id == null) return;
            if (!seen.has(String(op.department_id))) seen.set(String(op.department_id), op.department_name || 'Без названия');
        });
        return [...seen.entries()].map(([value, label]) => ({ value, label }));
    }, [operators]);

    // Один номер на двоих В ОДНОМ ДОМЕНЕ ломает привязку звонков — подсвечиваем.
    // Строки без домена в подсчёт не идут: их ключи схлопнулись бы в «1024@», и
    // одинаковые внутренние номера РАЗНЫХ ненастроенных отделов начали бы ложно
    // конфликтовать. Для них своя пометка «домен отдела не задан».
    const duplicateKeys = useMemo(() => {
        const counts = new Map();
        operators.forEach((op) => {
            const common = commonFor(op);
            [
                [op.sip_number, effectiveDomain(op.sip_domain, common.server)],
                [op.autodial_number, effectiveDomain(op.autodial_domain, common.autodialServer)],
            ].forEach(([number, domain]) => {
                if (!String(number || '').trim()) return;
                if (!domain) return;
                const key = numberKey(number, domain);
                counts.set(key, (counts.get(key) || 0) + 1);
            });
        });
        return new Set([...counts.entries()].filter(([, c]) => c > 1).map(([k]) => k));
    }, [operators, commonFor]);

    // SIP-логин Binotel уникален глобально, без привязки к домену: провайдер один
    // на всех, и один логин на двоих — это выбитая регистрация, а не «другая АТС».
    const duplicateLogins = useMemo(() => {
        if (!isBinotel) return new Set();
        const counts = new Map();
        operators.forEach((op) => {
            const login = normLogin(op.sip_login);
            if (!login) return;
            counts.set(login, (counts.get(login) || 0) + 1);
        });
        return new Set([...counts.entries()].filter(([, c]) => c > 1).map(([k]) => k));
    }, [operators, isBinotel]);

    // Домены, по которым сотрудник попадает в фильтр. Отдел без домена даёт
    // псевдо-значение: иначе такие строки пропадали при любом выборе домена.
    const filterDomainsOf = useCallback((op) => {
        const common = commonFor(op);
        const list = [];
        const mainAccount = isBinotel ? op?.sip_login : op?.sip_number;
        if (String(mainAccount || '').trim()) {
            list.push(effectiveDomain(op?.sip_domain, common.server) || NO_DOMAIN);
        }
        if (!isBinotel && String(op?.autodial_number || '').trim()) {
            list.push(effectiveDomain(op?.autodial_domain, common.autodialServer) || NO_DOMAIN);
        }
        return list;
    }, [commonFor, isBinotel]);

    // Фильтр по домену показываем, только когда АТС действительно несколько.
    const domainOptions = useMemo(() => {
        const seen = new Set();
        operators.forEach((op) => filterDomainsOf(op).forEach((d) => seen.add(d)));
        const named = [...seen].filter((d) => d !== NO_DOMAIN).sort().map((d) => ({ value: d, label: d }));
        return seen.has(NO_DOMAIN)
            ? [...named, { value: NO_DOMAIN, label: 'Без домена' }]
            : named;
    }, [operators, filterDomainsOf]);

    const filtered = useMemo(() => {
        const q = search.trim().toLowerCase();
        return operators.filter((op) => {
            if (departmentFilter && String(op.department_id ?? '') !== departmentFilter) return false;
            if (domainFilter && !filterDomainsOf(op).includes(domainFilter)) return false;
            if (!q) return true;
            return (op.name || '').toLowerCase().includes(q)
                || (op.sip_number || '').toLowerCase().includes(q)
                || (op.sip_login || '').toLowerCase().includes(q)
                || (op.autodial_number || '').toLowerCase().includes(q)
                || (op.group_name || '').toLowerCase().includes(q);
        });
    }, [operators, search, departmentFilter, domainFilter, filterDomainsOf]);

    const stats = useMemo(() => ({
        total: operators.length,
        withSip: operators.filter((op) => op.sip_number).length,
        withAutodial: operators.filter((op) => op.autodial_number).length,
        withLogin: operators.filter((op) => op.sip_login).length,
        withCabinet: operators.filter((op) => op.has_binotel_cabinet_password).length,
    }), [operators]);

    /* ─── эффективные значения (что уйдёт в телефон) ─── */
    const editingCommon = useMemo(() => commonFor(editing), [commonFor, editing]);

    const effective = useMemo(() => {
        const number = form.sip_number.trim();
        const autodial = form.autodial_number.trim();
        return {
            domain: (form.sip_domain || editingCommon.server || '').trim(),
            password: form.sip_password || buildSipPassword(editingCommon.base, number),
            autodialDomain: (form.autodial_domain || editingCommon.autodialServer || '').trim(),
            autodialPassword: form.autodial_password || buildSipPassword(editingCommon.autodialBase, autodial),
        };
    }, [form, editingCommon]);

    // Конфликт видно ещё до сохранения — бэкенд его тоже не пропустит.
    // Считаем по паре «номер + домен»: тот же номер на другой АТС — не конфликт.
    // Пустой домен из проверки исключён: сравнивать там не с чем, а ложный
    // конфликт намертво заблокировал бы кнопку «Сохранить».
    const conflicts = useMemo(() => {
        if (!editing || isBinotel) return {};
        const taken = new Map();
        operators.forEach((op) => {
            if (op.id === editing.id) return;
            const common = commonFor(op);
            [
                [op.sip_number, effectiveDomain(op.sip_domain, common.server)],
                [op.autodial_number, effectiveDomain(op.autodial_domain, common.autodialServer)],
            ].forEach(([number, domain]) => {
                if (!String(number || '').trim()) return;
                if (!domain) return;
                const key = numberKey(number, domain);
                if (!taken.has(key)) taken.set(key, op.name);
            });
        });
        const mainKey = numberKey(form.sip_number, effective.domain);
        const autoKey = numberKey(form.autodial_number, effective.autodialDomain);
        const main = form.sip_number.trim();
        const auto = form.autodial_number.trim();
        // Второй номер, совпавший с основным, — конфликт всегда, даже без домена:
        // один телефон не поднимет две одинаковые регистрации.
        const sameAsMain = Boolean(main && auto && mainKey === autoKey);
        return {
            sip_number: main && effective.domain ? (taken.get(mainKey) || null) : null,
            autodial_number: !auto ? null : (
                sameAsMain
                    ? 'совпадает с основным'
                    : (effective.autodialDomain ? (taken.get(autoKey) || null) : null)
            ),
        };
    }, [editing, isBinotel, operators, commonFor, form.sip_number, form.autodial_number,
        effective.domain, effective.autodialDomain]);

    // У Binotel конфликт другой: логин глобально уникален, а внутренние номера
    // повторяться могут — они нужны только для привязки звонков к оператору.
    const loginConflict = useMemo(() => {
        if (!isBinotel || !editing) return null;
        const login = normLogin(form.sip_login);
        if (!login) return null;
        const owner = operators.find((op) => op.id !== editing.id && normLogin(op.sip_login) === login);
        return owner ? (owner.name || 'другой сотрудник') : null;
    }, [isBinotel, editing, operators, form.sip_login]);

    // Те же три поля, что требует бэкенд (_binotel_operator_payload_error):
    // у Binotel наследовать нечего, и пустое поле здесь — не «взять из отдела»,
    // а нерабочая регистрация. Проверяем на месте, чтобы человек видел, чего не
    // хватает, а не ловил 400 после нажатия «Сохранить».
    const binotelMissing = useMemo(() => {
        if (!isBinotel || !editing) return '';
        if (!form.sip_domain.trim()) return 'Укажите SIP-сервер — например sip52.binotel.com';
        if (!form.sip_login.trim()) return 'Укажите SIP-логин: регистрация идёт им, а не внутренним номером';
        if (!form.sip_password.trim()) return 'Укажите SIP-пароль: у Binotel он персональный и из базы отдела не собирается';
        return '';
    }, [isBinotel, editing, form.sip_domain, form.sip_login, form.sip_password]);

    const saveBlocked = isBinotel
        ? Boolean(loginConflict || binotelMissing)
        : Boolean(conflicts.sip_number || conflicts.autodial_number);

    const selectedOperators = useMemo(
        () => operators.filter((op) => selected.has(op.id)),
        [operators, selected]
    );

    /* ─── выбор строк ─── */
    const toggleSelected = (id) => setSelected((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });

    const handleRowClick = (event, op, index) => {
        if (event.ctrlKey || event.metaKey) {
            anchorRef.current = index;
            toggleSelected(op.id);
            return;
        }
        if (event.shiftKey) {
            const from = anchorRef.current == null ? index : anchorRef.current;
            const [start, end] = from <= index ? [from, index] : [index, from];
            setSelected((prev) => {
                const next = new Set(prev);
                for (let i = start; i <= end; i += 1) next.add(filtered[i].id);
                return next;
            });
            anchorRef.current = index;
            return;
        }
        openEditor(op);
    };

    /* ─── действия ─── */
    const openEditor = (op) => {
        setEditing(op);
        setForm(formFromOperator(op));
        setShowAdvanced(hasPersonalParams(op));
        setReplacingCabinetPassword(false);
    };

    const closeEditor = () => {
        setEditing(null);
        setForm({ ...EMPTY_FORM });
        setShowAdvanced(false);
        setReplacingCabinetPassword(false);
    };

    // Отправляем только те поля, которые у провайдера вообще есть. Лишние бэкенд
    // вычистил бы сам, но тогда карточка Тез слала бы автодозвон и FOP2, а
    // карточка Таксопарков — учётку кабинета: в истории это выглядит как правка.
    const operatorPayload = () => (isBinotel
        ? {
            sip_number: form.sip_number,
            sip_login: form.sip_login,
            sip_password: form.sip_password,
            sip_domain: form.sip_domain,
            binotel_cabinet_login: form.binotel_cabinet_login,
            // Пусто = «не менять»: пароль кабинета наружу не отдаётся, и стереть
            // его случайной пересохранкой карточки нельзя.
            binotel_cabinet_password: form.binotel_cabinet_password,
            binotel_employee_id: form.binotel_employee_id,
            binotel_cabinet_url: form.binotel_cabinet_url,
        }
        : {
            sip_number: form.sip_number,
            sip_password: form.sip_password,
            sip_domain: form.sip_domain,
            autodial_number: form.autodial_number,
            autodial_password: form.autodial_password,
            autodial_domain: form.autodial_domain,
            fop2_enabled: form.fop2_enabled,
        });

    const saveOperator = async () => {
        if (!editing || !canEdit) return;
        setSaving(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/sip_config/operators/${editing.id}`, {
                method: 'PUT',
                credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(operatorPayload()),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            setOperators((prev) => prev.map((op) => (op.id === data.operator.id ? { ...op, ...data.operator } : op)));
            historyLoadedRef.current = false;
            showToastRef.current?.('SIP-настройки сохранены', 'success');
            closeEditor();
        } catch (e) {
            showToastRef.current?.(e.message, 'error');
        } finally {
            setSaving(false);
        }
    };

    // Employee ID в кабинете Binotel руками не найти — он виден только в ответе
    // API. Бэкенд заходит учёткой сотрудника, находит его запись и проставляет id.
    const resolveEmployeeId = async () => {
        if (!editing || !canEdit) return;
        setResolvingEmployee(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/sip_config/binotel/resolve_employee_ids`, {
                method: 'POST',
                credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                // Учётку шлём из формы: кнопку жмут до сохранения карточки, и в
                // базе её ещё нет. Пустой пароль там означает «взять сохранённый».
                body: JSON.stringify({
                    user_ids: [editing.id],
                    cabinet_login: form.binotel_cabinet_login,
                    cabinet_password: form.binotel_cabinet_password,
                    cabinet_url: form.binotel_cabinet_url,
                }),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            // Кабинет опрашивается по каждому сотруднику отдельно, поэтому HTTP 200
            // ещё не значит успех: отказ приезжает полем error внутри самой строки.
            const item = (data.operators || []).find((row) => Number(row.user_id) === Number(editing.id));
            if (!item) throw new Error('Кабинет не вернул запись сотрудника');
            if (item.error) throw new Error(item.error);
            // Из кабинета приходит не только employee ID: там же настоящий SIP-логин
            // и внутренний номер, и бэкенд их уже сохранил. Показываем ровно то, что
            // легло в базу, — перечитывать список ради этого не нужно.
            const patch = {
                binotel_employee_id: String(item.employee_id || ''),
                sip_login: String(item.sip_login || ''),
                sip_number: String(item.sip_number || ''),
            };
            setOperators((prev) => prev.map((op) => (op.id === editing.id ? { ...op, ...patch } : op)));
            setEditing((prev) => (prev ? { ...prev, ...patch } : prev));
            setForm((f) => ({ ...f, ...patch }));
            historyLoadedRef.current = false;
            showToastRef.current?.(
                `Employee ID определён: ${patch.binotel_employee_id || '—'}`, 'success');
        } catch (e) {
            showToastRef.current?.(`Не удалось определить employee ID: ${e.message}`, 'error');
        } finally {
            setResolvingEmployee(false);
        }
    };

    const openDeptEditor = (dept) => {
        setDeptEditing(dept);
        setDeptForm({
            sip_server: dept.sip_server || '',
            base_password: dept.base_password || '',
            autodial_code: dept.autodial_code || '',
            autodial_server: dept.autodial_server || '',
            autodial_base_password: dept.autodial_base_password || '',
            provider: dept.provider || 'asterisk',
        });
    };

    const saveDepartment = async (reset = false) => {
        if (!deptEditing || !canEdit) return;
        setDeptSaving(true);
        try {
            const body = reset
                ? { sip_server: '', base_password: '', autodial_code: '', autodial_server: '', autodial_base_password: '' }
                : {
                    sip_server: deptForm.sip_server.trim(),
                    base_password: deptForm.base_password,
                    autodial_code: deptForm.autodial_code.trim(),
                    autodial_server: deptForm.autodial_server.trim(),
                    autodial_base_password: deptForm.autodial_base_password,
                    // Тот самый переключатель, которым отдел переезжает между
                    // разделами «Таксопарки» и «Tez».
                    provider: deptForm.provider,
                };
            const resp = await fetch(`${apiBaseUrl}/api/sip_config/departments/${deptEditing.department_id}`, {
                method: 'PUT',
                credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(body),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            const saved = data.department;
            setDepartments((prev) => prev.map((d) => (d.department_id === saved.department_id ? saved : d)));
            // Настройки отдела влияют на эффективный домен сотрудников — обновляем
            // их строки полностью. Раньше патчились три поля из пяти, и домен
            // автодозвона с базой его пароля оставались от прошлой настройки,
            // пока список не перезагрузят: превью врало сразу после сохранения.
            setOperators((prev) => prev.map((op) => (op.department_id === saved.department_id ? {
                ...op,
                department_sip_server: saved.sip_server,
                department_base_password: saved.base_password,
                department_autodial_code: saved.autodial_code,
                department_autodial_server: saved.autodial_server,
                department_autodial_base_password: saved.autodial_base_password,
                department_provider: saved.provider || op.department_provider,
            } : op)));
            historyLoadedRef.current = false;
            showToastRef.current?.(reset ? 'Настройки отдела сброшены' : 'Настройки отдела сохранены', 'success');
            setDeptEditing(null);
        } catch (e) {
            showToastRef.current?.(e.message, 'error');
        } finally {
            setDeptSaving(false);
        }
    };

    const openBulk = () => { setBulkForm(EMPTY_BULK); setBulkOpen(true); };

    const bulkChanges = BULK_FIELDS.filter((f) => bulkForm[f.key].on);

    const applyBulk = async () => {
        if (!canEdit || !bulkChanges.length || !selected.size) return;
        setBulkSaving(true);
        try {
            const body = { user_ids: [...selected] };
            bulkChanges.forEach((f) => {
                const { value } = bulkForm[f.key];
                body[f.key] = f.flag ? Boolean(value) : value.trim();
            });
            const resp = await fetch(`${apiBaseUrl}/api/sip_config/operators/bulk`, {
                method: 'PUT',
                credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(body),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            const updated = new Map((data.operators || []).map((op) => [op.id, op]));
            setOperators((prev) => prev.map((op) => (updated.has(op.id) ? { ...op, ...updated.get(op.id) } : op)));
            historyLoadedRef.current = false;
            showToastRef.current?.(`Изменено сотрудников: ${updated.size}`, 'success');
            setBulkOpen(false);
            setSelected(new Set());
        } catch (e) {
            showToastRef.current?.(e.message, 'error');
        } finally {
            setBulkSaving(false);
        }
    };

    // Ссылка на файл подписана на час, поэтому в разметке её держать нельзя:
    // берём свежую в момент нажатия и сразу открываем.
    const downloadPhone = async () => {
        setDownloading(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/phone/download`, {
                credentials: 'include',
                headers: authHeaders(),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            window.open(data.url, '_blank', 'noopener');
        } catch (e) {
            showToastRef.current?.(`Не удалось скачать: ${e.message}`, 'error');
        } finally {
            setDownloading(false);
        }
    };

    const copyToClipboard = async (value, label) => {
        if (!value) return;
        try {
            await navigator.clipboard.writeText(value);
            showToastRef.current?.(`${label} скопирован`, 'success');
        } catch {
            showToastRef.current?.('Не удалось скопировать', 'error');
        }
    };

    /* ─── разметка ─── */
    return (
        <div className="space-y-4" style={{ fontFamily: APPLE_FONT }}>
            {/* Шапка */}
            <div className="sticky top-0 z-10 -mx-1 rounded-2xl border border-slate-200/70 bg-white/80 px-4 py-3 backdrop-blur-xl">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex items-center gap-3">
                        <div className="grid h-10 w-10 place-items-center rounded-2xl bg-blue-50 text-blue-600">
                            <FaIcon className={`fas ${isBinotel ? 'fa-phone-volume' : 'fa-headset'}`} />
                        </div>
                        <div>
                            <h2 className="text-[17px] font-semibold tracking-tight text-slate-900">
                                {SECTION_TITLE[provider] || SECTION_TITLE.asterisk}
                            </h2>
                            <p className="text-[12px] text-slate-400">
                                {isBinotel
                                    ? `Сотрудников: ${stats.total} · с SIP-логином: ${stats.withLogin} · с учёткой кабинета: ${stats.withCabinet}`
                                    : `Сотрудников: ${stats.total} · с номером: ${stats.withSip} · автодозвон: ${stats.withAutodial}`}
                            </p>
                        </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                    {/* Переключатель раздела. Показываем только тем, кому доступен
                        не один провайдер: главе отдела выбирать не из чего, а
                        админу прыгать за этим по сайдбару незачем. */}
                    {allowedProviders.length > 1 && (
                        <div className="flex items-center gap-1 rounded-xl bg-slate-100 p-1">
                            {PROVIDER_SECTIONS.filter((s) => allowedProviders.includes(s.id)).map((s) => (
                                <button
                                    key={s.id}
                                    type="button"
                                    onClick={() => switchProvider(s.id)}
                                    aria-pressed={provider === s.id}
                                    title={s.hint}
                                    className={`flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium transition ${
                                        provider === s.id
                                            ? 'bg-white text-slate-900 shadow-sm'
                                            : 'text-slate-500 hover:text-slate-800'
                                    }`}
                                >
                                    <FaIcon className={s.icon} />
                                    {s.label}
                                </button>
                            ))}
                        </div>
                    )}
                    <div className="flex items-center gap-1 rounded-xl bg-slate-100 p-1">
                        {tabs.map((t) => (
                            <button
                                key={t.id}
                                type="button"
                                onClick={() => setTab(t.id)}
                                aria-pressed={tab === t.id}
                                className={`rounded-lg px-3 py-1.5 text-[13px] font-medium transition ${
                                    tab === t.id
                                        ? 'bg-white text-slate-900 shadow-sm'
                                        : 'text-slate-500 hover:text-slate-800'
                                }`}
                            >
                                {t.label}
                            </button>
                        ))}
                    </div>
                    </div>
                </div>

                {tab === 'operators' && (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                        <div className="relative">
                            <FaIcon className="fas fa-search pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" style={{ width: 13, height: 13 }} />
                            <input
                                type="text"
                                placeholder={isBinotel ? 'Имя, номер, SIP-логин…' : 'Имя, номер, группа…'}
                                value={search}
                                onChange={(e) => setSearch(e.target.value)}
                                className={`${iosInput} w-56 pl-9`}
                            />
                        </div>
                        {departmentOptions.length > 1 && (
                            <CustomSelect
                                className="w-48"
                                variant="ios"
                                value={departmentFilter}
                                onChange={setDepartmentFilter}
                                ariaLabel="Отдел"
                                options={[{ value: '', label: 'Все отделы' }, ...departmentOptions]}
                            />
                        )}
                        {domainOptions.length > 1 && (
                            <CustomSelect
                                className="w-52"
                                variant="ios"
                                value={domainFilter}
                                onChange={setDomainFilter}
                                ariaLabel={isBinotel ? 'SIP-сервер' : 'Домен'}
                                options={[
                                    { value: '', label: isBinotel ? 'Все SIP-серверы' : 'Все домены' },
                                    ...domainOptions,
                                ]}
                            />
                        )}
                        <button
                            type="button"
                            onClick={() => setShowInactive((v) => !v)}
                            className={`${iosBtnGhost} ${showInactive ? 'bg-slate-100 text-slate-800' : ''}`}
                            title="Показывать уволенных — чтобы освободить занятый ими номер"
                        >
                            <FaIcon className="fas fa-users" />
                            <span className="hidden sm:inline">Уволенные</span>
                        </button>
                        <button onClick={fetchOperators} disabled={loading} className={iosBtnGhost} title="Обновить">
                            <FaIcon className={`fas fa-sync-alt ${loading ? 'animate-spin' : ''}`} />
                        </button>
                        {!selected.size && filtered.length > 1 && (
                            <span className="ml-auto hidden text-[11.5px] text-slate-400 lg:inline">
                                Ctrl + клик — выбрать несколько, Shift + клик — диапазон
                            </span>
                        )}
                    </div>
                )}
            </div>

            {/* Сотрудники */}
            {tab === 'operators' && (
                loading ? (
                    <div className="flex items-center justify-center py-16 text-slate-400">
                        <FaIcon className="fas fa-spinner fa-spin mr-2" /> Загрузка…
                    </div>
                ) : filtered.length === 0 ? (
                    <div className={`${iosCard} flex flex-col items-center justify-center py-16 text-slate-400`}>
                        <FaIcon className="fas fa-headset mb-2" style={{ width: 28, height: 28 }} />
                        <p className="text-[13px]">{search || departmentFilter || domainFilter ? 'Ничего не найдено' : 'Нет сотрудников'}</p>
                    </div>
                ) : (
                    <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                        {filtered.map((op, index) => {
                            const common = commonFor(op);
                            const mainDomain = effectiveDomain(op.sip_domain, common.server);
                            const autodialDomain = effectiveDomain(op.autodial_domain, common.autodialServer);
                            // Пустой домен — не дубль, а ненастроенный отдел: об этом и
                            // говорим прямо, иначе «номер занят» было бы неправдой.
                            const mainDomainMissing = Boolean(op.sip_number) && !mainDomain;
                            const autodialDomainMissing = Boolean(op.autodial_number) && !autodialDomain;
                            const isDuplicate = Boolean(op.sip_number) && Boolean(mainDomain)
                                && duplicateKeys.has(numberKey(op.sip_number, mainDomain));
                            const isAutodialDuplicate = Boolean(op.autodial_number) && Boolean(autodialDomain)
                                && duplicateKeys.has(numberKey(op.autodial_number, autodialDomain));
                            const isLoginDuplicate = Boolean(op.sip_login)
                                && duplicateLogins.has(normLogin(op.sip_login));
                            const isSelected = selected.has(op.id);
                            return (
                                <button
                                    key={op.id}
                                    type="button"
                                    onMouseDown={(e) => { if (e.shiftKey) e.preventDefault(); }}
                                    onClick={(e) => handleRowClick(e, op, index)}
                                    className={`flex w-full items-center gap-3 px-4 py-3 text-left transition ${
                                        isSelected ? 'bg-blue-50/70 hover:bg-blue-50' : 'hover:bg-slate-50'
                                    }`}
                                >
                                    <div className={`grid h-9 w-9 shrink-0 place-items-center rounded-full text-[12.5px] font-semibold transition ${
                                        isSelected ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-500'
                                    }`}>
                                        {isSelected
                                            ? <FaIcon className="fas fa-check" style={{ width: 13, height: 13 }} />
                                            : (op.name || '?').trim().charAt(0).toUpperCase()}
                                    </div>
                                    <div className="min-w-0 flex-1">
                                        <div className="flex items-center gap-2">
                                            <span className="truncate text-[14px] font-medium text-slate-900">{op.name}</span>
                                            {(op.status === 'fired' || op.status === 'dismissal') && (
                                                <IosBadge tone="slate">Уволен</IosBadge>
                                            )}
                                        </div>
                                        <div className="truncate text-[11.5px] text-slate-400">
                                            {[op.department_name, op.group_name].filter(Boolean).join(' · ') || 'Без отдела'}
                                        </div>
                                    </div>
                                    <div className="flex shrink-0 items-center gap-1.5">
                                        {isBinotel ? (
                                            <>
                                                {/* Внутренний номер в Binotel не логин: он нужен только
                                                    для привязки звонков, и дублей по нему не считаем. */}
                                                {op.sip_number ? (
                                                    <IosBadge tone="slate" title="Внутренний номер Binotel — по нему звонки привязываются к оператору">
                                                        <FaIcon className="fas fa-phone" style={{ width: 11, height: 11 }} />
                                                        <span className="font-mono">{op.sip_number}</span>
                                                    </IosBadge>
                                                ) : (
                                                    <span className="text-[11.5px] text-slate-300">номер не задан</span>
                                                )}
                                                {op.sip_login ? (
                                                    <IosBadge
                                                        tone={isLoginDuplicate ? 'red' : 'blue'}
                                                        title={isLoginDuplicate
                                                            ? 'SIP-логин занят ещё кем-то — регистрации будут выбивать друг друга'
                                                            : 'SIP-логин Binotel'}
                                                    >
                                                        <FaIcon className="fas fa-id-card" style={{ width: 11, height: 11 }} />
                                                        <span className="font-mono">{op.sip_login}</span>
                                                    </IosBadge>
                                                ) : (
                                                    <IosBadge tone="amber" title="Без SIP-логина телефон не зарегистрируется">
                                                        логин не задан
                                                    </IosBadge>
                                                )}
                                                {op.sip_domain && (
                                                    <IosBadge tone="slate" className="hidden max-w-[180px] md:inline-flex" title={`SIP-сервер: ${op.sip_domain}`}>
                                                        <FaIcon className="fas fa-globe" style={{ width: 11, height: 11 }} />
                                                        <span className="truncate font-mono">{op.sip_domain}</span>
                                                    </IosBadge>
                                                )}
                                                {op.sip_password && (
                                                    <span className="hidden h-6 w-6 place-items-center rounded-full bg-slate-100 text-slate-400 sm:grid" title="SIP-пароль задан">
                                                        <FaIcon className="fas fa-key" style={{ width: 11, height: 11 }} />
                                                    </span>
                                                )}
                                                {op.has_binotel_cabinet_password && (
                                                    <span className="hidden h-6 w-6 place-items-center rounded-full bg-blue-50 text-blue-500 sm:grid" title="Учётка кабинета my.binotel.kz задана — телефон может менять статус оператора">
                                                        <FaIcon className="fas fa-desktop" style={{ width: 11, height: 11 }} />
                                                    </span>
                                                )}
                                            </>
                                        ) : (
                                            <>
                                                {op.sip_number ? (
                                                    <IosBadge
                                                        tone={mainDomainMissing ? 'amber' : (isDuplicate ? 'red' : 'blue')}
                                                        title={isDuplicate
                                                            ? `Номер занят ещё кем-то на домене ${mainDomain}`
                                                            : (mainDomainMissing
                                                                ? 'Домен отдела не задан — телефону некуда регистрироваться'
                                                                : `SIP-номер · домен ${mainDomain}`)}
                                                    >
                                                        <FaIcon className="fas fa-phone" style={{ width: 11, height: 11 }} />
                                                        <span className="font-mono">{op.sip_number}</span>
                                                    </IosBadge>
                                                ) : (
                                                    <span className="text-[11.5px] text-slate-300">номер не задан</span>
                                                )}
                                                {(mainDomainMissing || autodialDomainMissing) && (
                                                    <IosBadge tone="amber" className="hidden sm:inline-flex" title="Заполните SIP-сервер в карточке отдела на вкладке «Общие»">
                                                        домен отдела не задан
                                                    </IosBadge>
                                                )}
                                                {op.autodial_number && (
                                                    <IosBadge
                                                        tone={autodialDomainMissing ? 'amber' : (isAutodialDuplicate ? 'red' : 'slate')}
                                                        className="hidden sm:inline-flex"
                                                        title={`Номер автодозвона · домен ${autodialDomain || 'не задан'}`}
                                                    >
                                                        <FaIcon className="fas fa-phone-volume" style={{ width: 11, height: 11 }} />
                                                        <span className="font-mono">{op.autodial_number}</span>
                                                    </IosBadge>
                                                )}
                                                {/* Свой домен показываем текстом: именно он объясняет,
                                                    почему одинаковые номера у разных людей — не дубль. */}
                                                {op.sip_domain && (
                                                    <IosBadge tone="slate" className="hidden max-w-[160px] md:inline-flex" title={`Персональный домен: ${op.sip_domain}`}>
                                                        <FaIcon className="fas fa-globe" style={{ width: 11, height: 11 }} />
                                                        <span className="truncate font-mono">{op.sip_domain}</span>
                                                    </IosBadge>
                                                )}
                                                {hasPersonalPassword(op) && (
                                                    <span className="hidden h-6 w-6 place-items-center rounded-full bg-slate-100 text-slate-400 sm:grid" title="Персональный пароль">
                                                        <FaIcon className="fas fa-key" style={{ width: 11, height: 11 }} />
                                                    </span>
                                                )}
                                                {fop2Disabled(op) && (
                                                    <span className="hidden h-6 w-6 place-items-center rounded-full bg-amber-50 text-amber-600 sm:grid" title="Вход в FOP2 выключен — сотрудник не встаёт в очереди Asterisk">
                                                        <FaIcon className="fas fa-user-slash" style={{ width: 11, height: 11 }} />
                                                    </span>
                                                )}
                                            </>
                                        )}
                                    </div>
                                    <FaIcon className="fas fa-chevron-right shrink-0 text-slate-300" style={{ width: 12, height: 12 }} />
                                </button>
                            );
                        })}
                    </div>
                )
            )}

            {/* Панель выбранных: появляется, когда отметили хотя бы одного */}
            {tab === 'operators' && selected.size > 0 && (
                <div className="pointer-events-none fixed inset-x-0 bottom-6 z-30 flex justify-center px-4">
                    <div className="pointer-events-auto flex items-center gap-1.5 rounded-2xl bg-slate-900/90 px-3 py-2 text-white shadow-2xl ring-1 ring-white/10 backdrop-blur-xl">
                        <span className="px-1.5 text-[13px] font-medium">Выбрано: {selected.size}</span>
                        {isBinotel && (
                            <span className="max-w-[280px] px-1.5 text-[12px] leading-snug text-slate-300">
                                у Binotel сервер, логин и пароль у каждого свои — правятся по одному
                            </span>
                        )}
                        {canEdit && !isBinotel && (
                            <button
                                type="button"
                                onClick={openBulk}
                                className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-3 py-1.5 text-[13px] font-semibold transition hover:bg-blue-500 active:scale-[0.98]"
                            >
                                <FaIcon className="fas fa-sliders" style={{ width: 12, height: 12 }} />
                                Изменить
                            </button>
                        )}
                        <button
                            type="button"
                            onClick={() => setSelected(new Set(filtered.map((op) => op.id)))}
                            className="rounded-xl px-3 py-1.5 text-[13px] font-medium text-slate-300 transition hover:bg-white/10 hover:text-white"
                        >
                            Все ({filtered.length})
                        </button>
                        <button
                            type="button"
                            onClick={() => { setSelected(new Set()); anchorRef.current = null; }}
                            className="rounded-xl px-3 py-1.5 text-[13px] font-medium text-slate-300 transition hover:bg-white/10 hover:text-white"
                        >
                            Снять
                        </button>
                    </div>
                </div>
            )}

            {/* Общие — только у локальной АТС: у Binotel всё персональное */}
            {tab === 'common' && !isBinotel && (
                <div className="max-w-2xl space-y-4">
                    {/* Отделы: у каждой АТС свои номера, домен и пароли, поэтому
                        подключение задаётся по отделам — общего яруса нет. */}
                    <section className="space-y-1.5">
                        <div className="flex items-end justify-between gap-2">
                            <div className={iosGroupLabel}>Отделы</div>
                            <span className="text-[11.5px] text-slate-400">
                                со своими настройками: {departments.filter((d) => d.configured).length} из {departments.length}
                            </span>
                        </div>
                        {departments.length === 0 ? (
                            <div className={`${iosCard} px-4 py-6 text-center text-[13px] text-slate-400`}>
                                Отделы не найдены
                            </div>
                        ) : (
                            <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                {departments.map((dept) => (
                                    <button
                                        key={dept.department_id}
                                        type="button"
                                        onClick={() => openDeptEditor(dept)}
                                        className="flex w-full items-center gap-3 px-4 py-3 text-left transition hover:bg-slate-50"
                                    >
                                        <div className={`grid h-9 w-9 shrink-0 place-items-center rounded-full ${
                                            dept.configured ? 'bg-blue-50 text-blue-600' : 'bg-slate-100 text-slate-400'
                                        }`}>
                                            <FaIcon className="fas fa-building" style={{ width: 14, height: 14 }} />
                                        </div>
                                        <div className="min-w-0 flex-1">
                                            <div className="truncate text-[14px] font-medium text-slate-900">{dept.department_name}</div>
                                            <div className="truncate text-[11.5px] text-slate-400">
                                                {dept.configured
                                                    ? [
                                                        dept.sip_server ? `домен ${dept.sip_server}` : 'домен не задан',
                                                        dept.autodial_server ? `автодозвон ${dept.autodial_server}` : null,
                                                        dept.base_password ? 'своя база пароля' : null,
                                                        dept.autodial_base_password ? 'свой пароль автодозвона' : null,
                                                        dept.autodial_code ? `код ${dept.autodial_code}` : null,
                                                    ].filter(Boolean).join(' · ')
                                                    : 'телефония не настроена'}
                                            </div>
                                        </div>
                                        <span className="hidden shrink-0 text-[11.5px] text-slate-400 sm:inline">
                                            {dept.operators_count} чел.
                                        </span>
                                        {dept.configured
                                            ? <IosBadge tone="blue">Настроен</IosBadge>
                                            : <IosBadge tone="slate">По умолчанию</IosBadge>}
                                        <FaIcon className="fas fa-chevron-right shrink-0 text-slate-300" style={{ width: 12, height: 12 }} />
                                    </button>
                                ))}
                            </div>
                        )}
                        <p className="px-1 text-[11.5px] leading-relaxed text-slate-500">
                            Домен, база пароля и код автодозвона задаются в карточке отдела —
                            настроек «на всех» больше нет. Пароль сотрудника = база + его
                            SIP-номер; другой формат задаётся плейсхолдером{' '}
                            <span className="font-mono">{'{номер}'}</span>:{' '}
                            <span className="font-mono">Secret{'{номер}'}!</span> даст{' '}
                            <span className="font-mono">Secret1024!</span>. У автодозвона обычно
                            отдельная АТС и свой пароль — задайте их в той же карточке, иначе
                            берутся как у основного номера. На код автодозвона звонят один раз
                            со второго номера, чтобы включить режим. Персональные пароль и
                            домен — в карточке сотрудника на вкладке «Сотрудники».
                        </p>
                    </section>

                    {/* Программа телефона. Раздают её отсюда же, где ведут SIP-настройки:
                        обновляется парк машин сам, но ссылка нужна для новых сотрудников.
                        Видна только тем, кому телефон положен (отдел продаж и админы) —
                        у главы другого отдела ссылка всё равно вернула бы 403. */}
                    {canDownloadPhone && (
                    <section className="space-y-1.5">
                        <div className={iosGroupLabel}>Программа iCORE Phone</div>
                        <div className={`${iosCard} space-y-3 p-4`}>
                            {releaseLoading ? (
                                <p className="text-[12.5px] text-slate-400">Загружаю сведения о версии…</p>
                            ) : releaseError ? (
                                <p className="text-[12.5px] text-rose-600">Не удалось получить версию: {releaseError}</p>
                            ) : !release ? (
                                <p className="text-[12.5px] text-slate-500">
                                    Дистрибутив ещё не опубликован. Он появится здесь после первой
                                    сборки с публикацией.
                                </p>
                            ) : (
                                <>
                                    <div className="flex flex-wrap items-center gap-2">
                                        <IosBadge tone="blue">Версия {release.version}</IosBadge>
                                        {release.mandatory && <IosBadge tone="slate">Обязательное</IosBadge>}
                                        <span className="text-[11.5px] text-slate-400">
                                            {[fmtSize(release.size), release.published_at ? fmtDateTime(release.published_at) : '']
                                                .filter(Boolean).join(' · ')}
                                        </span>
                                    </div>
                                    {release.notes && (
                                        <p className="text-[12.5px] leading-relaxed text-slate-600">{release.notes}</p>
                                    )}
                                    <div className="flex items-center justify-between gap-2 rounded-xl bg-slate-50 px-3 py-2.5">
                                        <span className="min-w-0 truncate font-mono text-[11px] text-slate-500" title={release.sha256}>
                                            sha256 {release.sha256}
                                        </span>
                                        <button
                                            type="button"
                                            onClick={() => copyToClipboard(release.sha256, 'sha256')}
                                            className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-white hover:text-slate-700"
                                            title="Скопировать sha256"
                                        >
                                            <FaIcon className="fas fa-copy" style={{ width: 12, height: 12 }} />
                                        </button>
                                    </div>
                                </>
                            )}
                            <button
                                type="button"
                                onClick={downloadPhone}
                                disabled={downloading || !release}
                                className={iosBtnPrimary}
                            >
                                <FaIcon className={downloading ? 'fas fa-spinner fa-spin' : 'fas fa-download'} />
                                Скачать установщик
                            </button>
                            <p className="px-1 text-[11.5px] leading-relaxed text-slate-500">
                                Ставится без прав администратора, только текущему пользователю.
                                Обновления телефон потом качает сам и применяет в первую паузу
                                без звонков — переустанавливать вручную не нужно.
                            </p>
                        </div>
                    </section>
                    )}
                </div>
            )}

            {/* История */}
            {tab === 'history' && (
                <div className="max-w-3xl">
                    {historyLoading ? (
                        <div className="flex items-center justify-center py-16 text-slate-400">
                            <FaIcon className="fas fa-spinner fa-spin mr-2" /> Загрузка…
                        </div>
                    ) : history.length === 0 ? (
                        <div className={`${iosCard} flex flex-col items-center justify-center py-16 text-slate-400`}>
                            <FaIcon className="fas fa-clock-rotate-left mb-2" style={{ width: 28, height: 28 }} />
                            <p className="text-[13px]">Изменений пока нет</p>
                        </div>
                    ) : (
                        <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                            {history.map((h, i) => {
                                const s = h.settings || {};
                                const parts = h.target_user_id
                                    ? [
                                        // Массовая правка номеров не касается, и ключа sip_number
                                        // в её снимке нет: «номер снят» было бы неправдой.
                                        s.sip_number ? `номер ${s.sip_number}` : (s.bulk ? null : 'номер снят'),
                                        s.sip_login ? `SIP-логин ${s.sip_login}` : null,
                                        s.autodial_number ? `автодозвон ${s.autodial_number}` : null,
                                        s.sip_domain ? `домен ${s.sip_domain}` : null,
                                        s.sip_password ? 'свой пароль' : null,
                                        s.binotel_cabinet_login ? `кабинет ${s.binotel_cabinet_login}` : null,
                                        // Флаг меняет, доходят ли до сотрудника звонки из
                                        // очередей, — в истории он обязан быть виден.
                                        s.fop2_enabled === false ? 'FOP2 выключен' : null,
                                        s.bulk ? 'массово' : null,
                                    ]
                                    : [
                                        // Ветка «глобус» остаётся ради архива: общий ярус убрали,
                                        // но старые строки истории должны читаться по-прежнему.
                                        s.sip_server ? `сервер ${s.sip_server}` : 'сервер общий',
                                        s.autodial_server ? `автодозвон ${s.autodial_server}` : null,
                                        s.base_password ? 'своя база пароля' : null,
                                        s.autodial_base_password ? 'свой пароль автодозвона' : null,
                                        s.autodial_code ? `код ${s.autodial_code}` : null,
                                    ];
                                const icon = h.target_user_id ? 'fa-user' : (h.department_id ? 'fa-building' : 'fa-globe');
                                const title = h.target_user_id
                                    ? (h.target_user_name || 'Сотрудник удалён')
                                    : (h.department_id ? (h.department_name || 'Отдел удалён') : 'Общие настройки');
                                return (
                                    <div key={i} className="flex items-start gap-3 px-4 py-3">
                                        <div className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-slate-100 text-slate-400">
                                            <FaIcon className={`fas ${icon}`} style={{ width: 13, height: 13 }} />
                                        </div>
                                        <div className="min-w-0 flex-1">
                                            <div className="text-[13.5px] font-medium text-slate-800">{title}</div>
                                            <div className="truncate text-[12px] text-slate-500">
                                                {parts.filter(Boolean).join(' · ') || '—'}
                                            </div>
                                        </div>
                                        <div className="shrink-0 text-right text-[11.5px] text-slate-400">
                                            <div>{fmtDateTime(h.changed_at)}</div>
                                            <div>{h.changed_by_name || '—'}</div>
                                        </div>
                                    </div>
                                );
                            })}
                        </div>
                    )}
                </div>
            )}

            {/* Настройки отдела */}
            <IosModal
                open={!!deptEditing}
                onClose={() => setDeptEditing(null)}
                title={deptEditing?.department_name || ''}
                subtitle={deptEditing?.configured ? 'Свои настройки SIP' : 'Телефония ещё не настроена'}
                footer={canEdit ? (
                    <>
                        {deptEditing?.configured && (
                            <button
                                type="button"
                                onClick={() => saveDepartment(true)}
                                disabled={deptSaving}
                                className="mr-auto inline-flex items-center gap-2 rounded-xl bg-rose-50 px-4 py-2.5 text-[13.5px] font-semibold text-rose-600 transition hover:bg-rose-100 active:scale-[0.98] disabled:opacity-50"
                            >
                                <FaIcon className="fas fa-rotate-left" />
                                Вернуть общие
                            </button>
                        )}
                        <button type="button" onClick={() => setDeptEditing(null)} className={iosBtnSecondary}>Отмена</button>
                        <button type="button" onClick={() => saveDepartment(false)} disabled={deptSaving} className={iosBtnPrimary}>
                            <FaIcon className={deptSaving ? 'fas fa-spinner fa-spin' : 'fas fa-check'} />
                            Сохранить
                        </button>
                    </>
                ) : null}
            >
                <div className="space-y-4">
                    {/* Переключатель провайдера. Только админам: перевод отдела на
                        другую АТС меняет саму модель его настроек, и глава одного
                        отдела не должен двигать его целиком. Сервер это тоже
                        проверяет — без доступа к целевому разделу вернётся 403. */}
                    {canSwitchProvider && (
                        <div className={`${iosCard} space-y-2 p-4`}>
                            <div className={iosGroupLabel}>Телефония отдела</div>
                            <div className="flex gap-2">
                                {PROVIDER_CHOICES.map((choice) => (
                                    <button
                                        key={choice.id}
                                        type="button"
                                        disabled={!canEdit}
                                        onClick={() => setDeptForm((f) => ({ ...f, provider: choice.id }))}
                                        className={deptForm.provider === choice.id ? iosBtnPrimary : iosBtnSecondary}
                                    >
                                        <FaIcon className={choice.icon} />
                                        {choice.label}
                                    </button>
                                ))}
                            </div>
                            <div className="text-[12px] leading-snug text-slate-500">
                                {deptForm.provider === 'binotel'
                                    ? 'У Binotel общих настроек нет: сервер, логин и пароль у каждого свои, FOP2 и автодозвона нет. После сохранения отдел уйдёт в раздел «Настройки SIP — Tez», а поля ниже перестанут применяться.'
                                    : 'Локальная АТС: сервер и база пароля общие для отдела, логин равен внутреннему номеру, пароль собирается из базы и номера.'}
                            </div>
                        </div>
                    )}
                    <div className={`${iosCard} space-y-3 p-4`}>
                        <div>
                            <label className="text-[12.5px] font-medium text-slate-600">SIP-сервер / домен</label>
                            <input
                                type="text"
                                value={deptForm.sip_server}
                                onChange={(e) => setDeptForm((f) => ({ ...f, sip_server: e.target.value }))}
                                placeholder="напр. 192.168.88.251"
                                disabled={!canEdit}
                                className={`${iosInput} mt-1`}
                            />
                        </div>
                        <div>
                            <label className="text-[12.5px] font-medium text-slate-600">База пароля</label>
                            <div className="mt-1">
                                <SecretInput
                                    value={deptForm.base_password}
                                    onChange={(v) => setDeptForm((f) => ({ ...f, base_password: v }))}
                                    placeholder="общая часть пароля"
                                    disabled={!canEdit}
                                />
                            </div>
                        </div>
                        <div>
                            <label className="text-[12.5px] font-medium text-slate-600">Домен автодозвона</label>
                            <input
                                type="text"
                                value={deptForm.autodial_server}
                                onChange={(e) => setDeptForm((f) => ({ ...f, autodial_server: e.target.value }))}
                                placeholder="пусто — как основной домен"
                                disabled={!canEdit}
                                className={`${iosInput} mt-1`}
                            />
                        </div>
                        <div>
                            <label className="text-[12.5px] font-medium text-slate-600">База пароля автодозвона</label>
                            <div className="mt-1">
                                <SecretInput
                                    value={deptForm.autodial_base_password}
                                    onChange={(v) => setDeptForm((f) => ({ ...f, autodial_base_password: v }))}
                                    placeholder="пусто — как у основного номера"
                                    disabled={!canEdit}
                                />
                            </div>
                        </div>
                        <div>
                            <label className="text-[12.5px] font-medium text-slate-600">Код подключения к автодозвону</label>
                            <input
                                type="text"
                                value={deptForm.autodial_code}
                                onChange={(e) => setDeptForm((f) => ({ ...f, autodial_code: e.target.value }))}
                                placeholder="напр. *55"
                                disabled={!canEdit}
                                className={`${iosInput} mt-1 font-mono`}
                            />
                        </div>
                    </div>
                    <p className="px-1 text-[11.5px] text-slate-500">
                        Пустые домен и пароль автодозвона берутся от основного номера отдела.
                        В базе пароля работает <span className="font-mono">{'{номер}'}</span>:{' '}
                        <span className="font-mono">Secret{'{номер}'}!</span> → <span className="font-mono">Secret1024!</span>.
                        Сотрудников с телефоном в отделе: {deptEditing?.operators_count ?? 0}.
                    </p>
                    {deptEditing?.updated_at && (
                        <p className="px-1 text-[11.5px] text-slate-400">
                            Изменено {fmtDateTime(deptEditing.updated_at)}{deptEditing.updated_by_name ? ` · ${deptEditing.updated_by_name}` : ''}
                        </p>
                    )}
                </div>
            </IosModal>

            {/* Массовое изменение пароля, домена и входа в FOP2 */}
            <IosModal
                open={bulkOpen}
                onClose={() => setBulkOpen(false)}
                title="Настройки для выбранных"
                subtitle={`Сотрудников: ${selected.size}`}
                footer={(
                    <>
                        <button type="button" onClick={() => setBulkOpen(false)} className={iosBtnSecondary}>Отмена</button>
                        <button
                            type="button"
                            onClick={applyBulk}
                            disabled={bulkSaving || !bulkChanges.length}
                            className={iosBtnPrimary}
                        >
                            <FaIcon className={bulkSaving ? 'fas fa-spinner fa-spin' : 'fas fa-check'} />
                            Применить к {selected.size}
                        </button>
                    </>
                )}
            >
                <div className="space-y-4">
                    <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                        {BULK_FIELDS.map((field) => {
                            const state = bulkForm[field.key];
                            if (field.flag) {
                                return (
                                    <div key={field.key} className="px-4 py-3">
                                        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
                                            <span className="text-[13.5px] font-medium text-slate-700">{field.label}</span>
                                            <div className="flex items-center gap-1 rounded-xl bg-slate-100 p-1">
                                                {BULK_FLAG_CHOICES.map((choice) => (
                                                    <button
                                                        key={choice.label}
                                                        type="button"
                                                        disabled={!canEdit}
                                                        aria-pressed={bulkFlagPicked(state, choice)}
                                                        onClick={() => setBulkForm((f) => ({
                                                            ...f, [field.key]: { on: choice.on, value: choice.value },
                                                        }))}
                                                        className={`flex-1 whitespace-nowrap rounded-lg px-3 py-1.5 text-[13px] font-medium transition ${
                                                            bulkFlagPicked(state, choice)
                                                                ? 'bg-white text-slate-900 shadow-sm'
                                                                : 'text-slate-500 hover:text-slate-800'
                                                        }`}
                                                    >
                                                        {choice.label}
                                                    </button>
                                                ))}
                                            </div>
                                        </div>
                                        {/* Предупреждение то же, что в карточке сотрудника: без него
                                            массовое выключение выглядит безобиднее, чем оно есть. */}
                                        {state.on && !state.value && (
                                            <p className="mt-2 flex items-start gap-1.5 rounded-xl bg-amber-50 px-3 py-2 text-[11.5px] leading-relaxed text-amber-700">
                                                <FaIcon className="fas fa-triangle-exclamation mt-0.5 shrink-0" style={{ width: 11, height: 11 }} />
                                                <span>
                                                    Выбранные перестанут вставать в очереди Asterisk: статусы
                                                    «Перерыв», «Тренинг» и «Техническая пауза» больше не будут
                                                    снимать их с очередей. Режим автодозвона не затрагивается.
                                                </span>
                                            </p>
                                        )}
                                    </div>
                                );
                            }
                            return (
                                <div key={field.key} className="px-4 py-3">
                                    <div className="flex items-center justify-between gap-3">
                                        <span className="text-[13.5px] font-medium text-slate-700">{field.label}</span>
                                        <IosToggle
                                            checked={state.on}
                                            disabled={!canEdit}
                                            onChange={(v) => setBulkForm((f) => ({ ...f, [field.key]: { ...f[field.key], on: v } }))}
                                        />
                                    </div>
                                    {state.on && (
                                        <div className="mt-2">
                                            {field.secret ? (
                                                <SecretInput
                                                    value={state.value}
                                                    onChange={(v) => setBulkForm((f) => ({ ...f, [field.key]: { ...f[field.key], value: v } }))}
                                                    placeholder="пусто — вернуть настройки отдела"
                                                    disabled={!canEdit}
                                                />
                                            ) : (
                                                <input
                                                    type="text"
                                                    value={state.value}
                                                    onChange={(e) => setBulkForm((f) => ({ ...f, [field.key]: { ...f[field.key], value: e.target.value } }))}
                                                    placeholder="пусто — вернуть настройки отдела"
                                                    disabled={!canEdit}
                                                    className={iosInput}
                                                />
                                            )}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                    <p className="px-1 text-[11.5px] leading-relaxed text-slate-500">
                        Меняются только включённые поля, остальное у каждого остаётся своим.
                        Пустой пароль или домен возвращает настройки отдела: домен АТС
                        и пароль «база + номер». Номера массово не меняются — они у каждого свои.
                    </p>

                    <section className="space-y-1.5">
                        <div className={iosGroupLabel}>Кого меняем</div>
                        <div className="flex flex-wrap gap-1.5">
                            {selectedOperators.slice(0, 12).map((op) => {
                                const mark = isBinotel ? op.sip_login : op.sip_number;
                                return (
                                    <span key={op.id} className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-[11.5px] text-slate-600">
                                        {op.name}
                                        {mark && <span className="font-mono text-slate-400">{mark}</span>}
                                    </span>
                                );
                            })}
                            {selectedOperators.length > 12 && (
                                <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-1 text-[11.5px] text-slate-500">
                                    ещё {selectedOperators.length - 12}
                                </span>
                            )}
                        </div>
                    </section>
                </div>
            </IosModal>

            {/* Карточка сотрудника */}
            <IosModal
                open={!!editing}
                onClose={closeEditor}
                title={editing?.name || ''}
                subtitle={[editing?.department_name, editing?.group_name].filter(Boolean).join(' · ') || 'SIP-настройки'}
                footer={canEdit ? (
                    <>
                        <button type="button" onClick={closeEditor} className={iosBtnSecondary}>Отмена</button>
                        <button
                            type="button"
                            onClick={saveOperator}
                            disabled={saving || saveBlocked}
                            className={iosBtnPrimary}
                        >
                            <FaIcon className={saving ? 'fas fa-spinner fa-spin' : 'fas fa-check'} />
                            Сохранить
                        </button>
                    </>
                ) : null}
            >
                {isBinotel ? (
                    /* ─── Tez: всё персональное; автодозвона и FOP2 здесь нет ─── */
                    <div className="space-y-4">
                        <section className="space-y-1.5">
                            <div className={iosGroupLabel}>Регистрация в Binotel</div>
                            <div className={`${iosCard} space-y-3 p-4`}>
                                <div>
                                    <label className="text-[12.5px] font-medium text-slate-600">SIP-сервер</label>
                                    <input
                                        type="text"
                                        value={form.sip_domain}
                                        onChange={(e) => setForm((f) => ({ ...f, sip_domain: e.target.value }))}
                                        placeholder="напр. sip52.binotel.com"
                                        disabled={!canEdit}
                                        className={`${iosInput} mt-1 font-mono`}
                                    />
                                </div>
                                <div>
                                    <label className="text-[12.5px] font-medium text-slate-600">SIP-логин</label>
                                    <input
                                        type="text"
                                        value={form.sip_login}
                                        onChange={(e) => setForm((f) => ({ ...f, sip_login: e.target.value }))}
                                        placeholder="напр. 68m77pnw"
                                        disabled={!canEdit}
                                        className={`${iosInput} mt-1 font-mono`}
                                    />
                                    {loginConflict && (
                                        <p className="mt-1 flex items-center gap-1.5 px-1 text-[11.5px] text-rose-600">
                                            <FaIcon className="fas fa-triangle-exclamation" style={{ width: 11, height: 11 }} />
                                            Логин занят: {loginConflict}
                                        </p>
                                    )}
                                </div>
                                <div>
                                    <label className="text-[12.5px] font-medium text-slate-600">SIP-пароль</label>
                                    <div className="mt-1">
                                        <SecretInput
                                            value={form.sip_password}
                                            onChange={(v) => setForm((f) => ({ ...f, sip_password: v }))}
                                            placeholder="пароль SIP-линии из кабинета"
                                            disabled={!canEdit}
                                        />
                                    </div>
                                </div>
                                <div>
                                    <label className="text-[12.5px] font-medium text-slate-600">Внутренний номер</label>
                                    <input
                                        type="text"
                                        value={form.sip_number}
                                        onChange={(e) => setForm((f) => ({ ...f, sip_number: e.target.value }))}
                                        placeholder="напр. 105"
                                        disabled={!canEdit}
                                        className={`${iosInput} mt-1 font-mono`}
                                    />
                                    <p className="mt-1 px-1 text-[11.5px] leading-relaxed text-slate-500">
                                        Нужен для привязки звонков Binotel к оператору, в регистрации
                                        не участвует.
                                    </p>
                                </div>
                                {binotelMissing && (
                                    <p className="flex items-start gap-1.5 rounded-xl bg-amber-50 px-3 py-2 text-[11.5px] leading-relaxed text-amber-700">
                                        <FaIcon className="fas fa-triangle-exclamation mt-0.5 shrink-0" style={{ width: 11, height: 11 }} />
                                        <span>{binotelMissing}</span>
                                    </p>
                                )}
                            </div>
                        </section>

                        {/* Кабинет нужен телефону, чтобы переключать статус оператора:
                            SIP-пароля в API кабинета нет, а статусы иначе не поставить. */}
                        <section className="space-y-1.5">
                            <div className={iosGroupLabel}>Кабинет my.binotel.kz</div>
                            <div className={`${iosCard} space-y-3 p-4`}>
                                <div>
                                    <label className="text-[12.5px] font-medium text-slate-600">Логин кабинета</label>
                                    <input
                                        type="text"
                                        value={form.binotel_cabinet_login}
                                        onChange={(e) => setForm((f) => ({ ...f, binotel_cabinet_login: e.target.value }))}
                                        placeholder="почта, с которой заходят в кабинет"
                                        autoComplete="off"
                                        disabled={!canEdit}
                                        className={`${iosInput} mt-1`}
                                    />
                                </div>
                                <div>
                                    <label className="text-[12.5px] font-medium text-slate-600">Пароль кабинета</label>
                                    {editing?.has_binotel_cabinet_password && !replacingCabinetPassword ? (
                                        // Пароль наружу не отдаётся никогда — показываем только признак.
                                        // Пустое поле рядом с «задан» читалось бы как «пароля нет».
                                        <div className="mt-1 flex items-center justify-between gap-2 rounded-xl bg-slate-50 px-3 py-2.5">
                                            <IosBadge tone="green">Задан</IosBadge>
                                            {canEdit && (
                                                <button
                                                    type="button"
                                                    onClick={() => setReplacingCabinetPassword(true)}
                                                    className={iosBtnSecondary}
                                                >
                                                    <FaIcon className="fas fa-pen" />
                                                    Заменить
                                                </button>
                                            )}
                                        </div>
                                    ) : (
                                        <div className="mt-1">
                                            <SecretInput
                                                value={form.binotel_cabinet_password}
                                                onChange={(v) => setForm((f) => ({ ...f, binotel_cabinet_password: v }))}
                                                placeholder={editing?.has_binotel_cabinet_password
                                                    ? 'пусто — оставить прежний'
                                                    : 'пароль от кабинета'}
                                                disabled={!canEdit}
                                            />
                                        </div>
                                    )}
                                </div>
                                <div>
                                    <label className="text-[12.5px] font-medium text-slate-600">Адрес кабинета</label>
                                    <input
                                        type="text"
                                        value={form.binotel_cabinet_url}
                                        onChange={(e) => setForm((f) => ({ ...f, binotel_cabinet_url: e.target.value }))}
                                        placeholder={`пусто — ${BINOTEL_CABINET_URL_DEFAULT}`}
                                        disabled={!canEdit}
                                        className={`${iosInput} mt-1 font-mono`}
                                    />
                                </div>
                                <div>
                                    <label className="text-[12.5px] font-medium text-slate-600">Binotel employee ID</label>
                                    <div className="mt-1 flex items-center gap-2">
                                        <input
                                            type="text"
                                            value={form.binotel_employee_id}
                                            onChange={(e) => setForm((f) => ({ ...f, binotel_employee_id: e.target.value }))}
                                            placeholder="напр. 41288"
                                            disabled={!canEdit}
                                            className={`${iosInput} font-mono`}
                                        />
                                        {canEdit && (
                                            <button
                                                type="button"
                                                onClick={resolveEmployeeId}
                                                disabled={resolvingEmployee}
                                                className={`${iosBtnSecondary} whitespace-nowrap`}
                                                title="Зайти в кабинет учёткой сотрудника и вытащить его employee ID"
                                            >
                                                <FaIcon className={resolvingEmployee ? 'fas fa-spinner fa-spin' : 'fas fa-wand-magic-sparkles'} />
                                                Определить автоматически
                                            </button>
                                        )}
                                    </div>
                                    <p className="mt-1 px-1 text-[11.5px] leading-relaxed text-slate-500">
                                        Без employee ID телефон не сможет менять статус сотрудника
                                        в Binotel. В кабинете он не показывается — определяется по
                                        логину и паролю выше, поэтому их надо сначала сохранить.
                                    </p>
                                </div>
                            </div>
                        </section>

                        {/* Что реально уйдёт в телефон. Превью пароля-шаблона здесь нет:
                            у Binotel всё персональное, база пароля отдела не работает. */}
                        {(form.sip_login.trim() || form.sip_number.trim()) && (
                            <section className="space-y-1.5">
                                <div className={iosGroupLabel}>Данные для телефона</div>
                                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                    <EffectiveRow label="SIP-сервер" value={form.sip_domain.trim()} hint="укажите SIP-сервер выше" />
                                    <EffectiveRow label="Логин" value={form.sip_login.trim()} hint="укажите SIP-логин выше" />
                                    <EffectiveRow label="Пароль" value={form.sip_password} secret hint="задайте SIP-пароль выше" />
                                    <EffectiveRow label="Внутренний номер" value={form.sip_number.trim()} hint="нужен для привязки звонков" />
                                </div>
                            </section>
                        )}

                        {editing?.updated_at && (
                            <p className="px-1 text-[11.5px] text-slate-400">
                                Изменено {fmtDateTime(editing.updated_at)}{editing.updated_by_name ? ` · ${editing.updated_by_name}` : ''}
                            </p>
                        )}
                    </div>
                ) : (
                    /* ─── Таксопарки: локальная АТС, автодозвон и FOP2 ─── */
                    <div className="space-y-4">
                        <section className="space-y-1.5">
                            <div className={iosGroupLabel}>Основной номер</div>
                            <div className={`${iosCard} space-y-2 p-4`}>
                                <input
                                    type="text"
                                    value={form.sip_number}
                                    onChange={(e) => setForm((f) => ({ ...f, sip_number: e.target.value }))}
                                    placeholder="напр. 1024"
                                    disabled={!canEdit}
                                    className={`${iosInput} font-mono`}
                                />
                                {conflicts.sip_number && (
                                    <p className="flex items-center gap-1.5 px-1 text-[11.5px] text-rose-600">
                                        <FaIcon className="fas fa-triangle-exclamation" style={{ width: 11, height: 11 }} />
                                        На домене {effective.domain} номер занят: {conflicts.sip_number}
                                    </p>
                                )}
                            </div>
                        </section>

                        <section className="space-y-1.5">
                            <div className={iosGroupLabel}>Номер для автодозвона</div>
                            <div className={`${iosCard} space-y-2 p-4`}>
                                <input
                                    type="text"
                                    value={form.autodial_number}
                                    onChange={(e) => setForm((f) => ({ ...f, autodial_number: e.target.value }))}
                                    placeholder="второй номер, необязательно"
                                    disabled={!canEdit}
                                    className={`${iosInput} font-mono`}
                                />
                                {conflicts.autodial_number && (
                                    <p className="flex items-center gap-1.5 px-1 text-[11.5px] text-rose-600">
                                        <FaIcon className="fas fa-triangle-exclamation" style={{ width: 11, height: 11 }} />
                                        {conflicts.autodial_number === 'совпадает с основным'
                                            ? 'Совпадает с основным номером на том же домене'
                                            : `На домене ${effective.autodialDomain} номер занят: ${conflicts.autodial_number}`}
                                    </p>
                                )}
                                {form.autodial_number.trim() && (
                                    <div className="flex items-center justify-between gap-2 rounded-xl bg-slate-50 px-3 py-2.5">
                                        <span className="text-[12px] text-slate-500">
                                            {editingCommon.code
                                                ? <>Позвонить один раз на <span className="font-mono font-medium text-slate-700">{editingCommon.code}</span> — включится режим автодозвона</>
                                                : 'Код подключения не задан — заполните его в карточке отдела'}
                                        </span>
                                        {editingCommon.code && (
                                            <button
                                                type="button"
                                                onClick={() => copyToClipboard(editingCommon.code, 'Код')}
                                                className="grid h-7 w-7 shrink-0 place-items-center rounded-full text-slate-400 transition hover:bg-white hover:text-slate-700"
                                                title="Скопировать код"
                                            >
                                                <FaIcon className="fas fa-copy" style={{ width: 12, height: 12 }} />
                                            </button>
                                        )}
                                    </div>
                                )}
                            </div>
                        </section>

                        {/* Вход в FOP2. Выключают тем, кто не работает на очередях Asterisk:
                            скрытый браузер FOP2 им ничего не даёт, а сломаться может. */}
                        <section className="space-y-1.5">
                            <div className={iosGroupLabel}>Очереди Asterisk (FOP2)</div>
                            <div className={`${iosCard} space-y-2 p-4`}>
                                <div className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-3.5 py-2.5">
                                    <span className="text-[13px] text-slate-700">Входить в FOP2</span>
                                    <IosToggle
                                        checked={form.fop2_enabled}
                                        onChange={(v) => setForm((f) => ({ ...f, fop2_enabled: v }))}
                                        disabled={!canEdit}
                                    />
                                </div>
                                <p className="mt-1 text-[11.5px] leading-relaxed text-slate-500">
                                    Касается только основного номера — режим автодозвона работает через
                                    свой FOP2 и не затрагивается. Применится при следующем запуске
                                    телефона у сотрудника.
                                </p>
                                {!form.fop2_enabled && (
                                    <p className="flex items-start gap-1.5 rounded-xl bg-amber-50 px-3 py-2 text-[11.5px] leading-relaxed text-amber-700">
                                        <FaIcon className="fas fa-triangle-exclamation mt-0.5 shrink-0" style={{ width: 11, height: 11 }} />
                                        <span>
                                            Статусы «Перерыв», «Тренинг» и «Техническая пауза» больше не будут
                                            снимать сотрудника с очередей Asterisk — останется только запрет
                                            звонков в самом телефоне.
                                        </span>
                                    </p>
                                )}
                            </div>
                        </section>

                        {/* Персональные пароль/домен нужны редко — держим под кнопкой */}
                        <section className="space-y-1.5">
                            <button
                                type="button"
                                onClick={() => setShowAdvanced((v) => !v)}
                                className="flex w-full items-center justify-between rounded-xl px-1 py-1 text-left"
                            >
                                <span className={iosGroupLabel}>Персональные пароль и домен</span>
                                <FaIcon className={`fas ${showAdvanced ? 'fa-chevron-up' : 'fa-chevron-down'} text-slate-400`} style={{ width: 12, height: 12 }} />
                            </button>
                            {showAdvanced && (
                                <div className={`${iosCard} space-y-3 p-4`}>
                                    <p className="text-[11.5px] leading-relaxed text-slate-500">
                                        {editingCommon.server
                                            ? `Пусто — берётся домен ${editingCommon.server} из настроек отдела и пароль «база + номер».`
                                            : 'У отдела не задан домен — впишите его здесь или в карточке отдела на вкладке «Общие».'}
                                        {(editingCommon.autodialServer !== editingCommon.server
                                            || editingCommon.autodialBase !== editingCommon.base)
                                            && ` У автодозвона свои: домен ${editingCommon.autodialServer || 'не задан'} и база пароля.`}
                                    </p>
                                    <div>
                                        <label className="text-[12.5px] font-medium text-slate-600">Пароль основного номера</label>
                                        <div className="mt-1">
                                            <SecretInput
                                                value={form.sip_password}
                                                onChange={(v) => setForm((f) => ({ ...f, sip_password: v }))}
                                                placeholder="как у всех"
                                                disabled={!canEdit}
                                            />
                                        </div>
                                    </div>
                                    <div>
                                        <label className="text-[12.5px] font-medium text-slate-600">Домен основного номера</label>
                                        <input
                                            type="text"
                                            value={form.sip_domain}
                                            onChange={(e) => setForm((f) => ({ ...f, sip_domain: e.target.value }))}
                                            placeholder="как у всех"
                                            disabled={!canEdit}
                                            className={`${iosInput} mt-1`}
                                        />
                                    </div>
                                    {form.autodial_number.trim() && (
                                        <>
                                            <div>
                                                <label className="text-[12.5px] font-medium text-slate-600">Пароль номера автодозвона</label>
                                                <div className="mt-1">
                                                    <SecretInput
                                                        value={form.autodial_password}
                                                        onChange={(v) => setForm((f) => ({ ...f, autodial_password: v }))}
                                                        placeholder="как у всех"
                                                        disabled={!canEdit}
                                                    />
                                                </div>
                                            </div>
                                            <div>
                                                <label className="text-[12.5px] font-medium text-slate-600">Домен номера автодозвона</label>
                                                <input
                                                    type="text"
                                                    value={form.autodial_domain}
                                                    onChange={(e) => setForm((f) => ({ ...f, autodial_domain: e.target.value }))}
                                                    placeholder="как у всех"
                                                    disabled={!canEdit}
                                                    className={`${iosInput} mt-1`}
                                                />
                                            </div>
                                        </>
                                    )}
                                </div>
                            )}
                        </section>

                        {/* Что реально уйдёт в телефон */}
                        {form.sip_number.trim() && (
                            <section className="space-y-1.5">
                                <div className={iosGroupLabel}>Данные для телефона</div>
                                <div className={`${iosCard} divide-y divide-slate-100 overflow-hidden`}>
                                    <EffectiveRow label="Домен" value={effective.domain} hint="задайте домен в карточке отдела" />
                                    <EffectiveRow label="Логин" value={form.sip_number.trim()} />
                                    <EffectiveRow label="Пароль" value={effective.password} secret hint="задайте базу пароля в карточке отдела" />
                                    {form.autodial_number.trim() && (
                                        <>
                                            <EffectiveRow label="Логин автодозвона" value={form.autodial_number.trim()} />
                                            <EffectiveRow label="Пароль автодозвона" value={effective.autodialPassword} secret hint="задайте базу пароля автодозвона в карточке отдела" />
                                            {effective.autodialDomain !== effective.domain && (
                                                <EffectiveRow label="Домен автодозвона" value={effective.autodialDomain} hint="задайте домен автодозвона в карточке отдела" />
                                            )}
                                        </>
                                    )}
                                </div>
                            </section>
                        )}

                        {editing?.updated_at && (
                            <p className="px-1 text-[11.5px] text-slate-400">
                                Изменено {fmtDateTime(editing.updated_at)}{editing.updated_by_name ? ` · ${editing.updated_by_name}` : ''}
                            </p>
                        )}
                    </div>
                )}
            </IosModal>
        </div>
    );
};

/* Строка «что уйдёт в телефон»: пароль скрыт до нажатия на глаз.
   Пустое значение объясняем словами (hint): «—» не подсказывает, где его взять. */
const EffectiveRow = ({ label, value, secret = false, hint = '' }) => {
    const [shown, setShown] = useState(false);
    const display = secret && !shown ? '••••••••' : value;
    return (
        <div className="flex items-center justify-between gap-3 px-4 py-2.5">
            <span className="text-[12.5px] text-slate-500">{label}</span>
            <span className="flex min-w-0 items-center gap-2">
                {value ? (
                    <span className="truncate font-mono text-[12.5px] text-slate-800">{display}</span>
                ) : (
                    <span className={`truncate text-right text-[12px] ${hint ? 'text-amber-600' : 'text-slate-400'}`}>
                        {hint || '—'}
                    </span>
                )}
                {secret && value && (
                    <button
                        type="button"
                        onClick={() => setShown((v) => !v)}
                        aria-label={shown ? 'Скрыть' : 'Показать'}
                        className="shrink-0 text-slate-400 transition hover:text-slate-600"
                    >
                        <FaIcon className={`fas ${shown ? 'fa-eye-slash' : 'fa-eye'}`} style={{ width: 12, height: 12 }} />
                    </button>
                )}
            </span>
        </div>
    );
};

export default SipSettingsView;
