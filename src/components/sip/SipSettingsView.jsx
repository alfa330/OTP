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
 * Три вкладки:
 *   Сотрудники — SIP-номер каждого, второй номер для автодозвона и — по
 *                необходимости — персональные пароль/домен вместо общих.
 *   Общие      — сервер/домен, база пароля и общий код автодозвона.
 *   История    — кто и что менял (и в общем, и по конкретному сотруднику).
 *
 * Пароль оператора по умолчанию собирается как «база + его SIP-номер», домен
 * берётся общий; персональные значения перекрывают их только там, где заданы.
 */

const TABS = [
    { id: 'operators', label: 'Сотрудники' },
    { id: 'common', label: 'Общие' },
    { id: 'history', label: 'История' },
];

const EMPTY_FORM = {
    sip_number: '', sip_password: '', sip_domain: '',
    autodial_number: '', autodial_password: '', autodial_domain: '',
    fop2_enabled: true,
};

// Массово меняются только пароль и домен: номера у каждого свои.
const BULK_FIELDS = [
    { key: 'sip_domain', label: 'Домен основного номера', secret: false },
    { key: 'sip_password', label: 'Пароль основного номера', secret: true },
    { key: 'autodial_domain', label: 'Домен автодозвона', secret: false },
    { key: 'autodial_password', label: 'Пароль автодозвона', secret: true },
];

const EMPTY_BULK = BULK_FIELDS.reduce((acc, f) => ({ ...acc, [f.key]: { on: false, value: '' } }), {});

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
const SipSettingsView = ({ user, showToast, apiBaseUrl, withAccessTokenHeader, canEdit = true,
                           canDownloadPhone = false }) => {
    const [tab, setTab] = useState('operators');

    const [operators, setOperators] = useState([]);
    const [settings, setSettings] = useState({
        sip_server: '', base_password: '', autodial_code: '', autodial_server: '', autodial_base_password: '',
    });
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

    // Множественный выбор: Ctrl/⌘ + клик — по одному, Shift + клик — диапазон.
    const [selected, setSelected] = useState(() => new Set());
    const [bulkOpen, setBulkOpen] = useState(false);
    const [bulkForm, setBulkForm] = useState(EMPTY_BULK);
    const [bulkSaving, setBulkSaving] = useState(false);
    const anchorRef = useRef(null);

    const [commonForm, setCommonForm] = useState({
        sip_server: '', base_password: '', autodial_code: '', autodial_server: '', autodial_base_password: '',
    });
    const [savingCommon, setSavingCommon] = useState(false);

    const [deptEditing, setDeptEditing] = useState(null);
    const [deptForm, setDeptForm] = useState({
        sip_server: '', base_password: '', autodial_code: '', autodial_server: '', autodial_base_password: '',
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
    // Список и общие настройки приходят одним запросом — второй вызов не нужен.
    const fetchOperators = useCallback(async () => {
        setLoading(true);
        try {
            const qs = showInactive ? '?include_inactive=1' : '';
            const resp = await fetch(`${apiBaseUrl}/api/sip_config/operators${qs}`, {
                credentials: 'include',
                headers: authHeaders(),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            setOperators(Array.isArray(data.operators) ? data.operators : []);
            setDepartments(Array.isArray(data.departments) ? data.departments : []);
            const s = data.settings || {};
            setSettings(s);
            setCommonForm({
                sip_server: s.sip_server || '',
                base_password: s.base_password || '',
                autodial_code: s.autodial_code || '',
                autodial_server: s.autodial_server || '',
                autodial_base_password: s.autodial_base_password || '',
            });
        } catch (e) {
            showToastRef.current?.(`Не удалось загрузить настройки SIP: ${e.message}`, 'error');
        } finally {
            setLoading(false);
        }
    }, [apiBaseUrl, authHeaders, showInactive]);

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
        if (!canDownloadPhone) { setReleaseLoading(false); return undefined; }
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
    }, [apiBaseUrl, canDownloadPhone]);

    // История нужна редко — грузим при первом открытии вкладки.
    useEffect(() => {
        if (tab === 'history' && !historyLoadedRef.current) fetchHistory();
    }, [tab, fetchHistory]);

    /* ─── производные данные ─── */
    // Значения по умолчанию для сотрудника: сначала настройки его отдела,
    // потом общие. Персональные поля перекрывают и то, и другое.
    // У автодозвона свой домен — часто это отдельная АТС; не задан — как основной.
    const commonFor = useCallback((op) => {
        const server = op?.department_sip_server || settings.sip_server || '';
        const base = op?.department_base_password || settings.base_password || '';
        return {
            server,
            autodialServer: op?.department_autodial_server || settings.autodial_server || server,
            base,
            // У автодозвона своя база пароля; не задана — как у основного номера.
            autodialBase: op?.department_autodial_base_password || settings.autodial_base_password || base,
            code: op?.department_autodial_code || settings.autodial_code || '',
        };
    }, [settings.sip_server, settings.autodial_server, settings.base_password,
        settings.autodial_base_password, settings.autodial_code]);

    const departmentOptions = useMemo(() => {
        const seen = new Map();
        operators.forEach((op) => {
            if (op.department_id == null) return;
            if (!seen.has(String(op.department_id))) seen.set(String(op.department_id), op.department_name || 'Без названия');
        });
        return [...seen.entries()].map(([value, label]) => ({ value, label }));
    }, [operators]);

    // Один номер на двоих В ОДНОМ ДОМЕНЕ ломает привязку звонков — подсвечиваем.
    const duplicateKeys = useMemo(() => {
        const counts = new Map();
        operators.forEach((op) => {
            const common = commonFor(op);
            [
                [op.sip_number, effectiveDomain(op.sip_domain, common.server)],
                [op.autodial_number, effectiveDomain(op.autodial_domain, common.autodialServer)],
            ].forEach(([number, domain]) => {
                if (!String(number || '').trim()) return;
                const key = numberKey(number, domain);
                counts.set(key, (counts.get(key) || 0) + 1);
            });
        });
        return new Set([...counts.entries()].filter(([, c]) => c > 1).map(([k]) => k));
    }, [operators, commonFor]);

    // Фильтр по домену показываем, только когда АТС действительно несколько.
    const domainOptions = useMemo(() => {
        const seen = new Set();
        operators.forEach((op) => {
            const common = commonFor(op);
            seen.add(effectiveDomain(op.sip_domain, common.server));
            if (op.autodial_number) seen.add(effectiveDomain(op.autodial_domain, common.autodialServer));
        });
        return [...seen].filter(Boolean).sort().map((d) => ({ value: d, label: d }));
    }, [operators, commonFor]);

    const filtered = useMemo(() => {
        const q = search.trim().toLowerCase();
        return operators.filter((op) => {
            if (departmentFilter && String(op.department_id ?? '') !== departmentFilter) return false;
            if (domainFilter) {
                const common = commonFor(op);
                const domains = [effectiveDomain(op.sip_domain, common.server)];
                if (op.autodial_number) domains.push(effectiveDomain(op.autodial_domain, common.autodialServer));
                if (!domains.includes(domainFilter)) return false;
            }
            if (!q) return true;
            return (op.name || '').toLowerCase().includes(q)
                || (op.sip_number || '').toLowerCase().includes(q)
                || (op.autodial_number || '').toLowerCase().includes(q)
                || (op.group_name || '').toLowerCase().includes(q);
        });
    }, [operators, search, departmentFilter, domainFilter, commonFor]);

    const stats = useMemo(() => ({
        total: operators.length,
        withSip: operators.filter((op) => op.sip_number).length,
        withAutodial: operators.filter((op) => op.autodial_number).length,
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
    const conflicts = useMemo(() => {
        if (!editing) return {};
        const taken = new Map();
        operators.forEach((op) => {
            if (op.id === editing.id) return;
            const common = commonFor(op);
            [
                [op.sip_number, effectiveDomain(op.sip_domain, common.server)],
                [op.autodial_number, effectiveDomain(op.autodial_domain, common.autodialServer)],
            ].forEach(([number, domain]) => {
                if (!String(number || '').trim()) return;
                const key = numberKey(number, domain);
                if (!taken.has(key)) taken.set(key, op.name);
            });
        });
        const mainKey = numberKey(form.sip_number, effective.domain);
        const autoKey = numberKey(form.autodial_number, effective.autodialDomain);
        const main = form.sip_number.trim();
        const auto = form.autodial_number.trim();
        return {
            sip_number: main ? taken.get(mainKey) : null,
            autodial_number: auto
                ? (taken.get(autoKey) || (main && mainKey === autoKey ? 'совпадает с основным' : null))
                : null,
        };
    }, [editing, operators, commonFor, form.sip_number, form.autodial_number,
        effective.domain, effective.autodialDomain]);

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
    };

    const closeEditor = () => { setEditing(null); setForm({ ...EMPTY_FORM }); setShowAdvanced(false); };

    const saveOperator = async () => {
        if (!editing || !canEdit) return;
        setSaving(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/sip_config/operators/${editing.id}`, {
                method: 'PUT',
                credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify(form),
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

    const openDeptEditor = (dept) => {
        setDeptEditing(dept);
        setDeptForm({
            sip_server: dept.sip_server || '',
            base_password: dept.base_password || '',
            autodial_code: dept.autodial_code || '',
            autodial_server: dept.autodial_server || '',
            autodial_base_password: dept.autodial_base_password || '',
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
            // Настройки отдела влияют на эффективный домен сотрудников — обновляем их строки.
            setOperators((prev) => prev.map((op) => (op.department_id === saved.department_id ? {
                ...op,
                department_sip_server: saved.sip_server,
                department_base_password: saved.base_password,
                department_autodial_code: saved.autodial_code,
            } : op)));
            historyLoadedRef.current = false;
            showToastRef.current?.(reset ? 'Отдел вернулся к общим настройкам' : 'Настройки отдела сохранены', 'success');
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
            bulkChanges.forEach((f) => { body[f.key] = bulkForm[f.key].value.trim(); });
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

    const saveCommon = async () => {
        if (!canEdit) return;
        setSavingCommon(true);
        try {
            const resp = await fetch(`${apiBaseUrl}/api/sip_config`, {
                method: 'PUT',
                credentials: 'include',
                headers: authHeaders({ 'Content-Type': 'application/json' }),
                body: JSON.stringify({
                    sip_server: commonForm.sip_server.trim(),
                    base_password: commonForm.base_password,
                    autodial_code: commonForm.autodial_code.trim(),
                    autodial_server: commonForm.autodial_server.trim(),
                    autodial_base_password: commonForm.autodial_base_password,
                }),
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
            setSettings(data.settings || {});
            historyLoadedRef.current = false;
            showToastRef.current?.('Общие настройки сохранены', 'success');
        } catch (e) {
            showToastRef.current?.(e.message, 'error');
        } finally {
            setSavingCommon(false);
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

    const commonDirty = (
        commonForm.sip_server.trim() !== (settings.sip_server || '')
        || commonForm.base_password !== (settings.base_password || '')
        || commonForm.autodial_code.trim() !== (settings.autodial_code || '')
        || commonForm.autodial_server.trim() !== (settings.autodial_server || '')
        || commonForm.autodial_base_password !== (settings.autodial_base_password || '')
    );

    /* ─── разметка ─── */
    return (
        <div className="space-y-4" style={{ fontFamily: APPLE_FONT }}>
            {/* Шапка */}
            <div className="sticky top-0 z-10 -mx-1 rounded-2xl border border-slate-200/70 bg-white/80 px-4 py-3 backdrop-blur-xl">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex items-center gap-3">
                        <div className="grid h-10 w-10 place-items-center rounded-2xl bg-blue-50 text-blue-600">
                            <FaIcon className="fas fa-headset" />
                        </div>
                        <div>
                            <h2 className="text-[17px] font-semibold tracking-tight text-slate-900">Настройки SIP</h2>
                            <p className="text-[12px] text-slate-400">
                                Сотрудников: {stats.total} · с номером: {stats.withSip} · автодозвон: {stats.withAutodial}
                            </p>
                        </div>
                    </div>
                    <div className="flex items-center gap-1 rounded-xl bg-slate-100 p-1">
                        {TABS.map((t) => (
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

                {tab === 'operators' && (
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                        <div className="relative">
                            <FaIcon className="fas fa-search pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" style={{ width: 13, height: 13 }} />
                            <input
                                type="text"
                                placeholder="Имя, номер, группа…"
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
                                ariaLabel="Домен"
                                options={[{ value: '', label: 'Все домены' }, ...domainOptions]}
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
                            const isDuplicate = op.sip_number && duplicateKeys.has(numberKey(op.sip_number, mainDomain));
                            const isAutodialDuplicate = op.autodial_number
                                && duplicateKeys.has(numberKey(op.autodial_number, autodialDomain));
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
                                        {op.sip_number ? (
                                            <IosBadge
                                                tone={isDuplicate ? 'red' : 'blue'}
                                                title={isDuplicate
                                                    ? `Номер занят ещё кем-то на домене ${mainDomain || '—'}`
                                                    : `SIP-номер · домен ${mainDomain || '—'}`}
                                            >
                                                <FaIcon className="fas fa-phone" style={{ width: 11, height: 11 }} />
                                                <span className="font-mono">{op.sip_number}</span>
                                            </IosBadge>
                                        ) : (
                                            <span className="text-[11.5px] text-slate-300">номер не задан</span>
                                        )}
                                        {op.autodial_number && (
                                            <IosBadge tone={isAutodialDuplicate ? 'red' : 'slate'} className="hidden sm:inline-flex" title={`Номер автодозвона · домен ${autodialDomain || '—'}`}>
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
                        {canEdit && (
                            <button
                                type="button"
                                onClick={openBulk}
                                className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-3 py-1.5 text-[13px] font-semibold transition hover:bg-blue-500 active:scale-[0.98]"
                            >
                                <FaIcon className="fas fa-key" style={{ width: 12, height: 12 }} />
                                Пароль и домен
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

            {/* Общие */}
            {tab === 'common' && (
                <div className="max-w-2xl space-y-4">
                    {/* Отделы: у каждой АТС свои номера, поэтому подключение
                        задаётся по отделам, а общие настройки — запасной вариант. */}
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
                                                        dept.sip_server ? `домен ${dept.sip_server}` : 'домен общий',
                                                        dept.autodial_server ? `автодозвон ${dept.autodial_server}` : null,
                                                        dept.base_password ? 'своя база пароля' : null,
                                                        dept.autodial_base_password ? 'свой пароль автодозвона' : null,
                                                        dept.autodial_code ? `код ${dept.autodial_code}` : null,
                                                    ].filter(Boolean).join(' · ')
                                                    : `общие настройки${settings.sip_server ? ` · ${settings.sip_server}` : ''}`}
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
                        <p className="px-1 text-[11.5px] text-slate-500">
                            Отдел без своих настроек берёт общие — их и правят ниже.
                        </p>
                    </section>

                    <section className="space-y-1.5">
                        <div className={iosGroupLabel}>Общие: для отделов без своих настроек</div>
                        <div className={`${iosCard} space-y-3 p-4`}>
                            <div>
                                <label className="text-[12.5px] font-medium text-slate-600">SIP-сервер / домен</label>
                                <input
                                    type="text"
                                    value={commonForm.sip_server}
                                    onChange={(e) => setCommonForm((f) => ({ ...f, sip_server: e.target.value }))}
                                    placeholder="напр. 192.168.88.251"
                                    disabled={!canEdit}
                                    className={`${iosInput} mt-1`}
                                />
                            </div>
                            <div>
                                <label className="text-[12.5px] font-medium text-slate-600">База пароля</label>
                                <div className="mt-1">
                                    <SecretInput
                                        value={commonForm.base_password}
                                        onChange={(v) => setCommonForm((f) => ({ ...f, base_password: v }))}
                                        placeholder="общая часть пароля"
                                        disabled={!canEdit}
                                    />
                                </div>
                            </div>
                            <div>
                                <label className="text-[12.5px] font-medium text-slate-600">Домен автодозвона</label>
                                <input
                                    type="text"
                                    value={commonForm.autodial_server}
                                    onChange={(e) => setCommonForm((f) => ({ ...f, autodial_server: e.target.value }))}
                                    placeholder={commonForm.sip_server ? `пусто — как основной: ${commonForm.sip_server}` : 'пусто — как основной'}
                                    disabled={!canEdit}
                                    className={`${iosInput} mt-1`}
                                />
                            </div>
                            <div>
                                <label className="text-[12.5px] font-medium text-slate-600">База пароля автодозвона</label>
                                <div className="mt-1">
                                    <SecretInput
                                        value={commonForm.autodial_base_password}
                                        onChange={(v) => setCommonForm((f) => ({ ...f, autodial_base_password: v }))}
                                        placeholder="пусто — как у основного номера"
                                        disabled={!canEdit}
                                    />
                                </div>
                            </div>
                            <div>
                                <label className="text-[12.5px] font-medium text-slate-600">Код подключения к автодозвону</label>
                                <div className="mt-1 flex items-center gap-2">
                                    <input
                                        type="text"
                                        value={commonForm.autodial_code}
                                        onChange={(e) => setCommonForm((f) => ({ ...f, autodial_code: e.target.value }))}
                                        placeholder="напр. *55"
                                        disabled={!canEdit}
                                        className={`${iosInput} font-mono`}
                                    />
                                    <button
                                        type="button"
                                        onClick={() => copyToClipboard(commonForm.autodial_code.trim(), 'Код')}
                                        disabled={!commonForm.autodial_code.trim()}
                                        className={iosBtnSecondary}
                                        title="Скопировать код"
                                    >
                                        <FaIcon className="fas fa-copy" />
                                    </button>
                                </div>
                            </div>
                            <p className="px-1 text-[11.5px] text-slate-500">
                                Пароль сотрудника = база + его SIP-номер. Если формат другой — впишите
                                в базу <span className="font-mono">{'{номер}'}</span>, например{' '}
                                <span className="font-mono">Secret{'{номер}'}!</span> даст{' '}
                                <span className="font-mono">Secret1024!</span>. У автодозвона обычно
                                отдельная АТС и свой пароль — задайте их здесь, иначе берутся как
                                у основного номера. На код автодозвона звонят один раз со второго
                                номера, чтобы включить режим. Персональные пароль и домен —
                                в карточке сотрудника на вкладке «Сотрудники».
                            </p>
                        </div>
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

                    <div className="flex items-center justify-between gap-3 px-1">
                        <span className="text-[11.5px] text-slate-400">
                            {settings.updated_at
                                ? `Изменено ${fmtDateTime(settings.updated_at)}${settings.updated_by_name ? ` · ${settings.updated_by_name}` : ''}`
                                : 'Ещё не настраивалось'}
                        </span>
                        {canEdit && (
                            <button type="button" onClick={saveCommon} disabled={savingCommon || !commonDirty} className={iosBtnPrimary}>
                                <FaIcon className={savingCommon ? 'fas fa-spinner fa-spin' : 'fas fa-check'} />
                                Сохранить
                            </button>
                        )}
                    </div>
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
                                        s.sip_number ? `номер ${s.sip_number}` : 'номер снят',
                                        s.autodial_number ? `автодозвон ${s.autodial_number}` : null,
                                        s.sip_domain ? `домен ${s.sip_domain}` : null,
                                        s.sip_password ? 'свой пароль' : null,
                                        // Флаг меняет, доходят ли до сотрудника звонки из
                                        // очередей, — в истории он обязан быть виден.
                                        s.fop2_enabled === false ? 'FOP2 выключен' : null,
                                    ]
                                    : [
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
                subtitle={deptEditing?.configured ? 'Свои настройки SIP' : 'Сейчас берёт общие настройки'}
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
                    <div className={`${iosCard} space-y-3 p-4`}>
                        <div>
                            <label className="text-[12.5px] font-medium text-slate-600">SIP-сервер / домен</label>
                            <input
                                type="text"
                                value={deptForm.sip_server}
                                onChange={(e) => setDeptForm((f) => ({ ...f, sip_server: e.target.value }))}
                                placeholder={settings.sip_server ? `общий: ${settings.sip_server}` : 'напр. 192.168.88.251'}
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
                                    placeholder="пусто — общая"
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
                                placeholder={
                                    settings.autodial_server
                                        ? `общий: ${settings.autodial_server}`
                                        : 'пусто — как основной домен'
                                }
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
                                placeholder={settings.autodial_code ? `общий: ${settings.autodial_code}` : 'напр. *55'}
                                disabled={!canEdit}
                                className={`${iosInput} mt-1 font-mono`}
                            />
                        </div>
                    </div>
                    <p className="px-1 text-[11.5px] text-slate-500">
                        Пустое поле берётся из общих настроек, домен и пароль автодозвона — из основного
                        номера отдела. В базе пароля работает <span className="font-mono">{'{номер}'}</span>:{' '}
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

            {/* Массовое изменение пароля и домена */}
            <IosModal
                open={bulkOpen}
                onClose={() => setBulkOpen(false)}
                title="Пароль и домен для выбранных"
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
                                                    placeholder="пусто — вернуть общий"
                                                    disabled={!canEdit}
                                                />
                                            ) : (
                                                <input
                                                    type="text"
                                                    value={state.value}
                                                    onChange={(e) => setBulkForm((f) => ({ ...f, [field.key]: { ...f[field.key], value: e.target.value } }))}
                                                    placeholder="пусто — вернуть общий"
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
                    <p className="px-1 text-[11.5px] text-slate-500">
                        Меняются только включённые поля, остальное у каждого остаётся своим.
                        Пустое значение возвращает настройки отдела (или общие): домен АТС и пароль «база + номер».
                        Номера массово не меняются — они у каждого свои.
                    </p>

                    <section className="space-y-1.5">
                        <div className={iosGroupLabel}>Кого меняем</div>
                        <div className="flex flex-wrap gap-1.5">
                            {selectedOperators.slice(0, 12).map((op) => (
                                <span key={op.id} className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-2.5 py-1 text-[11.5px] text-slate-600">
                                    {op.name}
                                    {op.sip_number && <span className="font-mono text-slate-400">{op.sip_number}</span>}
                                </span>
                            ))}
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
                            disabled={saving || !!conflicts.sip_number || !!conflicts.autodial_number}
                            className={iosBtnPrimary}
                        >
                            <FaIcon className={saving ? 'fas fa-spinner fa-spin' : 'fas fa-check'} />
                            Сохранить
                        </button>
                    </>
                ) : null}
            >
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
                                    На домене {effective.domain || '—'} номер занят: {conflicts.sip_number}
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
                                        : `На домене ${effective.autodialDomain || '—'} номер занят: ${conflicts.autodial_number}`}
                                </p>
                            )}
                            {form.autodial_number.trim() && (
                                <div className="flex items-center justify-between gap-2 rounded-xl bg-slate-50 px-3 py-2.5">
                                    <span className="text-[12px] text-slate-500">
                                        {editingCommon.code
                                            ? <>Позвонить один раз на <span className="font-mono font-medium text-slate-700">{editingCommon.code}</span> — включится режим автодозвона</>
                                            : 'Код подключения не задан — заполните его на вкладке «Общие»'}
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
                                <p className="text-[11.5px] text-slate-500">
                                    Пусто — берётся домен {editingCommon.server || '—'} ({editing?.department_sip_server ? 'настройки отдела' : 'общие настройки'}) и пароль «база + номер».
                                    {(editingCommon.autodialServer !== editingCommon.server
                                        || editingCommon.autodialBase !== editingCommon.base)
                                        && ` У автодозвона свои: домен ${editingCommon.autodialServer || '—'} и база пароля.`}
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
                                <EffectiveRow label="Домен" value={effective.domain} />
                                <EffectiveRow label="Логин" value={form.sip_number.trim()} />
                                <EffectiveRow label="Пароль" value={effective.password} secret />
                                {form.autodial_number.trim() && (
                                    <>
                                        <EffectiveRow label="Логин автодозвона" value={form.autodial_number.trim()} />
                                        <EffectiveRow label="Пароль автодозвона" value={effective.autodialPassword} secret />
                                        {effective.autodialDomain !== effective.domain && (
                                            <EffectiveRow label="Домен автодозвона" value={effective.autodialDomain} />
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
            </IosModal>
        </div>
    );
};

/* Строка «что уйдёт в телефон»: пароль скрыт до нажатия на глаз. */
const EffectiveRow = ({ label, value, secret = false }) => {
    const [shown, setShown] = useState(false);
    const display = !value ? '—' : (secret && !shown ? '••••••••' : value);
    return (
        <div className="flex items-center justify-between gap-3 px-4 py-2.5">
            <span className="text-[12.5px] text-slate-500">{label}</span>
            <span className="flex items-center gap-2">
                <span className="truncate font-mono text-[12.5px] text-slate-800">{display}</span>
                {secret && value && (
                    <button
                        type="button"
                        onClick={() => setShown((v) => !v)}
                        aria-label={shown ? 'Скрыть' : 'Показать'}
                        className="text-slate-400 transition hover:text-slate-600"
                    >
                        <FaIcon className={`fas ${shown ? 'fa-eye-slash' : 'fa-eye'}`} style={{ width: 12, height: 12 }} />
                    </button>
                )}
            </span>
        </div>
    );
};

export default SipSettingsView;
