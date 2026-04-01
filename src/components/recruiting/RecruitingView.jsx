import React, { useMemo, useRef, useState } from 'react';
import FaIcon from '../common/FaIcon';

const SAMPLE_RESUMES = [
    {
        keyword_group: 'sales_manager',
        keyword_query: 'менеджер по продажам',
        page_found: 1,
        title: 'Менеджер по продажам',
        category: 'Продажи и обслуживание клиентов',
        experience: 'Опыт работы 1 год',
        location: 'г. Алматы, Бостандыкский район',
        salary: '350 000 тг',
        education: 'Высшее',
        published_at: 'Опубликовано 01.04.2026',
        detail_url: 'https://www.enbek.kz/ru/resume/menedzher-po-prodazham~1000001'
    },
    {
        keyword_group: 'sales_manager',
        keyword_query: 'sales manager',
        page_found: 2,
        title: 'Sales manager',
        category: 'B2B продажи',
        experience: 'Опыт работы 3 года',
        location: 'г. Алматы, Наурызбайский район',
        salary: '500 000 тг',
        education: 'Высшее',
        published_at: 'Опубликовано 30.03.2026',
        detail_url: 'https://www.enbek.kz/ru/resume/sales-manager~1000002'
    },
    {
        keyword_group: 'call_center_operator',
        keyword_query: 'оператор call-центра',
        page_found: 1,
        title: 'Оператор call-центра',
        category: 'Контакт-центр',
        experience: 'Без опыта',
        location: 'г. Алматы, Алмалинский район',
        salary: '250 000 тг',
        education: 'Среднее специальное',
        published_at: 'Опубликовано 01.04.2026',
        detail_url: 'https://www.enbek.kz/ru/resume/operator-call-centra~1000003'
    },
    {
        keyword_group: 'call_center_operator',
        keyword_query: 'специалист контакт-центра',
        page_found: 3,
        title: 'Специалист контакт-центра',
        category: 'Клиентский сервис',
        experience: 'Опыт работы 6 месяцев',
        location: 'г. Алматы, Медеуский район',
        salary: '220 000 тг',
        education: 'Среднее',
        published_at: 'Опубликовано 29.03.2026',
        detail_url: 'https://www.enbek.kz/ru/resume/spetsialist-kontakt-centra~1000004'
    }
];

const GROUP_LABELS = {
    sales_manager: 'Продажи',
    call_center_operator: 'Call-центр'
};

const RULES = {
    sales_manager: {
        strongTitle: ['менеджер по продажам', 'sales manager', 'account manager', 'специалист по продажам'],
        strongCategory: ['продажи', 'b2b', 'b2c'],
        weakAll: ['клиент', 'аккаунт', 'переговоры'],
        negativeAll: ['бухгалтер', 'водитель', 'кладовщик', 'повар', 'юрист']
    },
    call_center_operator: {
        strongTitle: ['оператор call-центра', 'оператор контакт-центра', 'специалист контакт-центра', 'телемаркетолог'],
        strongCategory: ['контакт-центр', 'call-центр', 'клиентский сервис'],
        weakAll: ['входящие звонки', 'исходящие звонки', 'консультирование'],
        negativeAll: ['бухгалтер', 'водитель', 'кладовщик', 'повар', 'юрист']
    }
};

const PRIORITY_META = {
    high: { label: 'Высокий', className: 'bg-green-100 text-green-800 border-green-200' },
    medium: { label: 'Средний', className: 'bg-amber-100 text-amber-800 border-amber-200' },
    low: { label: 'Низкий', className: 'bg-slate-100 text-slate-700 border-slate-200' }
};

const normalizeText = (value) =>
    String(value || '')
        .toLowerCase()
        .replace(/ё/g, 'е')
        .replace(/\s+/g, ' ')
        .trim();

const parseSalary = (value) => {
    const cleaned = String(value || '').replace(/[^\d]/g, '');
    if (!cleaned) return null;
    const num = Number(cleaned);
    return Number.isFinite(num) ? num : null;
};

const parsePublishedDate = (value) => {
    const match = String(value || '').match(/(\d{2})\.(\d{2})\.(\d{4})/);
    if (!match) return null;
    const [, day, month, year] = match;
    return new Date(`${year}-${month}-${day}T00:00:00`);
};

const scorePatterns = (text, patterns, weight, reasons, label) => {
    let score = 0;
    patterns.forEach((item) => {
        const pattern = normalizeText(item);
        if (pattern && text.includes(pattern)) {
            score += weight;
            reasons.push(`${label}: ${item}`);
        }
    });
    return score;
};

const getPriorityByScore = (score) => {
    if (score >= 70) return 'high';
    if (score >= 40) return 'medium';
    return 'low';
};

const getResumePriority = (item) => {
    const title = normalizeText(item.title);
    const category = normalizeText(item.category);
    const query = normalizeText(item.keyword_query);
    const all = normalizeText([item.title, item.category, item.experience, item.keyword_query].join(' '));
    const rules = RULES[item.keyword_group] || RULES.sales_manager;
    const reasons = [];

    let score = 0;
    score += scorePatterns(title, rules.strongTitle, 32, reasons, 'title');
    score += scorePatterns(category, rules.strongCategory, 16, reasons, 'category');
    score += scorePatterns(all, rules.weakAll, 8, reasons, 'text');
    score -= scorePatterns(all, rules.negativeAll, 18, reasons, 'negative');

    if (query && title && (title.includes(query) || query.includes(title))) {
        score += 12;
        reasons.push('query/title match');
    }

    if (!title) score -= 20;
    if (!category) score -= 6;

    score = Math.max(0, Math.min(100, score));
    const priority = getPriorityByScore(score);
    const reason = [...new Set(reasons)].slice(0, 4).join(' • ') || 'Недостаточно релевантных совпадений';
    return { score, priority, reason };
};

const formatMoney = (value) => {
    if (!value) return '—';
    return `${new Intl.NumberFormat('ru-RU').format(value)} тг`;
};

const exportJson = (filename, data) => {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
};

const getTodayBaseDate = () => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), now.getDate());
};

const RecruitingView = ({ showToast }) => {
    const fileInputRef = useRef(null);
    const [rawItems, setRawItems] = useState([]);
    const [selectedId, setSelectedId] = useState(null);
    const [search, setSearch] = useState('');
    const [groupFilter, setGroupFilter] = useState('all');
    const [priorityFilter, setPriorityFilter] = useState('medium_plus');
    const [sortBy, setSortBy] = useState('priority_desc');
    const [onlyWithSalary, setOnlyWithSalary] = useState(false);
    const [onlyFresh, setOnlyFresh] = useState(false);
    const [minSalary, setMinSalary] = useState('');
    const [importError, setImportError] = useState('');

    const items = useMemo(
        () =>
            rawItems.map((item, index) => {
                const priority = getResumePriority(item);
                return {
                    ...item,
                    __id: item.__id || `resume-${index}`,
                    salaryNum: parseSalary(item.salary),
                    publishedDate: parsePublishedDate(item.published_at),
                    groupLabel: GROUP_LABELS[item.keyword_group] || item.keyword_group || 'Другое',
                    relevanceScore: priority.score,
                    priorityLabel: priority.priority,
                    priorityReason: priority.reason
                };
            }),
        [rawItems]
    );

    const filteredItems = useMemo(() => {
        const today = getTodayBaseDate();
        const minSalaryValue = Number(minSalary || 0);
        const query = normalizeText(search);
        let list = [...items];

        if (groupFilter !== 'all') {
            list = list.filter((item) => item.keyword_group === groupFilter);
        }

        if (priorityFilter === 'high_only') {
            list = list.filter((item) => item.priorityLabel === 'high');
        } else if (priorityFilter === 'medium_plus') {
            list = list.filter((item) => item.priorityLabel === 'high' || item.priorityLabel === 'medium');
        } else if (priorityFilter === 'low_only') {
            list = list.filter((item) => item.priorityLabel === 'low');
        }

        if (query) {
            list = list.filter((item) => {
                const haystack = normalizeText(
                    [item.title, item.category, item.location, item.experience, item.education, item.keyword_query].join(' ')
                );
                return haystack.includes(query);
            });
        }

        if (onlyWithSalary) {
            list = list.filter((item) => Boolean(item.salaryNum));
        }

        if (onlyFresh) {
            list = list.filter((item) => {
                if (!item.publishedDate) return false;
                const limit = new Date(today.getTime() - 3 * 24 * 60 * 60 * 1000);
                return item.publishedDate >= limit;
            });
        }

        if (minSalaryValue > 0) {
            list = list.filter((item) => (item.salaryNum || 0) >= minSalaryValue);
        }

        list.sort((a, b) => {
            if (sortBy === 'priority_desc') return (b.relevanceScore || 0) - (a.relevanceScore || 0);
            if (sortBy === 'priority_asc') return (a.relevanceScore || 0) - (b.relevanceScore || 0);
            if (sortBy === 'salary_desc') return (b.salaryNum || 0) - (a.salaryNum || 0);
            if (sortBy === 'salary_asc') return (a.salaryNum || 0) - (b.salaryNum || 0);
            if (sortBy === 'title_asc') return String(a.title || '').localeCompare(String(b.title || ''), 'ru');
            return (b.publishedDate?.getTime() || 0) - (a.publishedDate?.getTime() || 0);
        });

        return list;
    }, [items, search, groupFilter, priorityFilter, sortBy, onlyWithSalary, onlyFresh, minSalary]);

    const selectedItem = useMemo(() => {
        if (!filteredItems.length) return null;
        if (!selectedId) return filteredItems[0];
        return filteredItems.find((item) => item.__id === selectedId) || filteredItems[0];
    }, [filteredItems, selectedId]);

    const stats = useMemo(() => {
        const withSalary = filteredItems.filter((item) => item.salaryNum);
        const avgSalary = withSalary.length
            ? Math.round(withSalary.reduce((acc, item) => acc + item.salaryNum, 0) / withSalary.length)
            : 0;
        const highPriority = filteredItems.filter((item) => item.priorityLabel === 'high').length;
        const avgScore = filteredItems.length
            ? Math.round(filteredItems.reduce((acc, item) => acc + (item.relevanceScore || 0), 0) / filteredItems.length)
            : 0;

        return {
            total: filteredItems.length,
            avgSalary,
            highPriority,
            avgScore
        };
    }, [filteredItems]);

    const setDemo = () => {
        setRawItems(SAMPLE_RESUMES.map((item, index) => ({ ...item, __id: `sample-${index}` })));
        setSelectedId('sample-0');
        setImportError('');
        if (typeof showToast === 'function') {
            showToast('Загружены демо-данные рекрутинга', 'success');
        }
    };

    const onUploadJson = async (event) => {
        const file = event.target.files?.[0];
        if (!file) return;
        try {
            const text = await file.text();
            const parsed = JSON.parse(text);
            if (!Array.isArray(parsed)) {
                throw new Error('JSON должен содержать массив объектов.');
            }
            setRawItems(parsed.map((item, index) => ({ ...item, __id: item.__id || `file-${index}` })));
            setSelectedId(null);
            setImportError('');
            if (typeof showToast === 'function') {
                showToast(`Импортировано резюме: ${parsed.length}`, 'success');
            }
        } catch (error) {
            const message = String(error?.message || 'Не удалось прочитать JSON файл.');
            setImportError(message);
            if (typeof showToast === 'function') {
                showToast(message, 'error');
            }
        } finally {
            event.target.value = '';
        }
    };

    const onExportFiltered = () => {
        if (!filteredItems.length) return;
        exportJson('recruiting_filtered_resumes.json', filteredItems);
    };

    return (
        <div className="space-y-6">
            <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                        <h2 className="text-2xl font-semibold text-gray-900">
                            <FaIcon className="fas fa-user-tie mr-2 text-blue-600" />
                            Рекрутинг
                        </h2>
                        <p className="mt-1 text-sm text-gray-500">Импорт резюме Enbek, фильтры и быстрый просмотр кандидатов.</p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <input ref={fileInputRef} type="file" accept="application/json" className="hidden" onChange={onUploadJson} />
                        <button
                            type="button"
                            onClick={() => fileInputRef.current?.click()}
                            className="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm font-medium text-blue-700 hover:bg-blue-100"
                        >
                            <FaIcon className="fas fa-upload" />
                            Загрузить JSON
                        </button>
                        <button
                            type="button"
                            onClick={setDemo}
                            className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
                        >
                            <FaIcon className="fas fa-database" />
                            Демо-данные
                        </button>
                        <button
                            type="button"
                            onClick={onExportFiltered}
                            disabled={!filteredItems.length}
                            className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium ${
                                filteredItems.length
                                    ? 'border border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
                                    : 'cursor-not-allowed border border-gray-200 bg-gray-100 text-gray-400'
                            }`}
                        >
                            <FaIcon className="fas fa-download" />
                            Экспорт JSON
                        </button>
                    </div>
                </div>
                {importError ? <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{importError}</div> : null}
            </div>

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                    <div className="text-xs uppercase tracking-wide text-gray-500">Резюме</div>
                    <div className="mt-2 text-2xl font-semibold text-gray-900">{stats.total}</div>
                </div>
                <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                    <div className="text-xs uppercase tracking-wide text-gray-500">Средняя зарплата</div>
                    <div className="mt-2 text-2xl font-semibold text-gray-900">{formatMoney(stats.avgSalary)}</div>
                </div>
                <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                    <div className="text-xs uppercase tracking-wide text-gray-500">Высокий приоритет</div>
                    <div className="mt-2 text-2xl font-semibold text-gray-900">{stats.highPriority}</div>
                </div>
                <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                    <div className="text-xs uppercase tracking-wide text-gray-500">Средний score</div>
                    <div className="mt-2 text-2xl font-semibold text-gray-900">{stats.avgScore}</div>
                </div>
            </div>

            <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
                    <div className="xl:col-span-2">
                        <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-500">Поиск</label>
                        <input
                            type="text"
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            placeholder="Имя, категория, локация..."
                            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                        />
                    </div>
                    <div>
                        <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-500">Группа</label>
                        <select
                            value={groupFilter}
                            onChange={(e) => setGroupFilter(e.target.value)}
                            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                        >
                            <option value="all">Все</option>
                            <option value="sales_manager">Продажи</option>
                            <option value="call_center_operator">Call-центр</option>
                        </select>
                    </div>
                    <div>
                        <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-500">Приоритет</label>
                        <select
                            value={priorityFilter}
                            onChange={(e) => setPriorityFilter(e.target.value)}
                            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                        >
                            <option value="all">Все</option>
                            <option value="medium_plus">Средний и высокий</option>
                            <option value="high_only">Только высокий</option>
                            <option value="low_only">Только низкий</option>
                        </select>
                    </div>
                    <div>
                        <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-500">Сортировка</label>
                        <select
                            value={sortBy}
                            onChange={(e) => setSortBy(e.target.value)}
                            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                        >
                            <option value="priority_desc">Приоритет ↓</option>
                            <option value="priority_asc">Приоритет ↑</option>
                            <option value="published_desc">Свежие сначала</option>
                            <option value="salary_desc">Зарплата ↓</option>
                            <option value="salary_asc">Зарплата ↑</option>
                            <option value="title_asc">По названию</option>
                        </select>
                    </div>
                    <div>
                        <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-gray-500">Мин. зарплата</label>
                        <input
                            type="number"
                            min="0"
                            value={minSalary}
                            onChange={(e) => setMinSalary(e.target.value)}
                            placeholder="0"
                            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
                        />
                    </div>
                </div>

                <div className="mt-3 flex flex-wrap gap-2">
                    <button
                        type="button"
                        onClick={() => setOnlyWithSalary((prev) => !prev)}
                        className={`rounded-lg px-3 py-1.5 text-sm ${onlyWithSalary ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}
                    >
                        Только с зарплатой
                    </button>
                    <button
                        type="button"
                        onClick={() => setOnlyFresh((prev) => !prev)}
                        className={`rounded-lg px-3 py-1.5 text-sm ${onlyFresh ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'}`}
                    >
                        Свежие за 3 дня
                    </button>
                    <button
                        type="button"
                        onClick={() => {
                            setSearch('');
                            setGroupFilter('all');
                            setPriorityFilter('medium_plus');
                            setSortBy('priority_desc');
                            setOnlyWithSalary(false);
                            setOnlyFresh(false);
                            setMinSalary('');
                        }}
                        className="rounded-lg bg-gray-100 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-200"
                    >
                        Сбросить фильтры
                    </button>
                </div>
            </div>

            <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
                <div className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
                    <div className="mb-3 flex items-center justify-between">
                        <h3 className="text-lg font-semibold text-gray-900">Список резюме</h3>
                        <span className="rounded-full bg-gray-100 px-3 py-1 text-xs font-medium text-gray-600">{filteredItems.length} записей</span>
                    </div>

                    {!filteredItems.length ? (
                        <div className="rounded-xl border border-dashed border-gray-300 p-8 text-center text-sm text-gray-500">
                            Нет данных. Загрузите JSON или нажмите "Демо-данные".
                        </div>
                    ) : (
                        <div className="max-h-[680px] space-y-3 overflow-y-auto pr-1">
                            {filteredItems.map((item) => {
                                const isActive = selectedItem?.__id === item.__id;
                                const priorityMeta = PRIORITY_META[item.priorityLabel] || PRIORITY_META.low;
                                return (
                                    <button
                                        key={item.__id}
                                        type="button"
                                        onClick={() => setSelectedId(item.__id)}
                                        className={`w-full rounded-xl border p-3 text-left transition ${
                                            isActive ? 'border-blue-600 bg-blue-50' : 'border-gray-200 bg-white hover:border-gray-300'
                                        }`}
                                    >
                                        <div className="flex flex-wrap items-center gap-2">
                                            <span className="text-sm font-semibold text-gray-900">{item.title || 'Без названия'}</span>
                                            <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-700">{item.groupLabel}</span>
                                            <span className={`rounded-full border px-2 py-0.5 text-xs ${priorityMeta.className}`}>
                                                {priorityMeta.label} • {item.relevanceScore}
                                            </span>
                                        </div>
                                        <div className="mt-2 text-xs text-gray-600">{item.category || 'Категория не указана'}</div>
                                        <div className="mt-2 flex flex-wrap gap-3 text-xs text-gray-500">
                                            <span>
                                                <FaIcon className="fas fa-map-marker-alt mr-1" />
                                                {item.location || 'Локация не указана'}
                                            </span>
                                            <span>
                                                <FaIcon className="fas fa-wallet mr-1" />
                                                {item.salaryNum ? formatMoney(item.salaryNum) : item.salary || 'Зарплата не указана'}
                                            </span>
                                        </div>
                                    </button>
                                );
                            })}
                        </div>
                    )}
                </div>

                <div className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
                    <h3 className="mb-3 text-lg font-semibold text-gray-900">Карточка кандидата</h3>

                    {!selectedItem ? (
                        <div className="rounded-xl border border-dashed border-gray-300 p-8 text-center text-sm text-gray-500">
                            Выберите резюме слева для просмотра деталей.
                        </div>
                    ) : (
                        <div className="space-y-4">
                            <div>
                                <div className="flex flex-wrap items-center gap-2">
                                    <span className="rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-800">{selectedItem.groupLabel}</span>
                                    <span className={`rounded-full border px-2 py-0.5 text-xs ${PRIORITY_META[selectedItem.priorityLabel]?.className || PRIORITY_META.low.className}`}>
                                        {PRIORITY_META[selectedItem.priorityLabel]?.label || 'Низкий'} • {selectedItem.relevanceScore}
                                    </span>
                                    <span className="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600">стр. {selectedItem.page_found || '—'}</span>
                                </div>
                                <h4 className="mt-2 text-xl font-semibold text-gray-900">{selectedItem.title || 'Без названия'}</h4>
                                <div className="mt-1 text-sm text-gray-600">{selectedItem.category || 'Категория не указана'}</div>
                            </div>

                            <div className="grid gap-3 sm:grid-cols-2">
                                <div className="rounded-lg bg-gray-50 p-3">
                                    <div className="text-xs text-gray-500">Локация</div>
                                    <div className="mt-1 text-sm font-medium text-gray-900">{selectedItem.location || '—'}</div>
                                </div>
                                <div className="rounded-lg bg-gray-50 p-3">
                                    <div className="text-xs text-gray-500">Зарплата</div>
                                    <div className="mt-1 text-sm font-medium text-gray-900">{selectedItem.salary || '—'}</div>
                                </div>
                                <div className="rounded-lg bg-gray-50 p-3">
                                    <div className="text-xs text-gray-500">Опыт</div>
                                    <div className="mt-1 text-sm font-medium text-gray-900">{selectedItem.experience || '—'}</div>
                                </div>
                                <div className="rounded-lg bg-gray-50 p-3">
                                    <div className="text-xs text-gray-500">Образование</div>
                                    <div className="mt-1 text-sm font-medium text-gray-900">{selectedItem.education || '—'}</div>
                                </div>
                            </div>

                            <div className="rounded-lg border border-gray-200 p-3">
                                <div className="text-xs text-gray-500">Поисковый запрос</div>
                                <div className="mt-1 text-sm text-gray-900">{selectedItem.keyword_query || '—'}</div>
                            </div>
                            <div className="rounded-lg border border-gray-200 p-3">
                                <div className="text-xs text-gray-500">Причина приоритета</div>
                                <div className="mt-1 text-sm text-gray-900">{selectedItem.priorityReason || '—'}</div>
                            </div>
                            <div className="rounded-lg border border-gray-200 p-3">
                                <div className="text-xs text-gray-500">Дата публикации</div>
                                <div className="mt-1 text-sm text-gray-900">{selectedItem.published_at || '—'}</div>
                            </div>

                            {selectedItem.detail_url ? (
                                <a
                                    href={selectedItem.detail_url}
                                    target="_blank"
                                    rel="noreferrer"
                                    className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm font-medium text-blue-700 hover:bg-blue-100"
                                >
                                    <FaIcon className="fas fa-external-link-alt" />
                                    Открыть резюме на Enbek
                                </a>
                            ) : null}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default RecruitingView;
