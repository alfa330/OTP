import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  Search,
  Filter,
  Download,
  RefreshCw,
  MapPin,
  Briefcase,
  Wallet,
  CalendarDays,
  X,
  ChevronRight,
  Database,
  Users,
  BarChart3,
  Sparkles,
  Clock3,
  SlidersHorizontal,
  ExternalLink,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  PieChart,
  Pie,
  Cell,
} from "recharts";

const GROUP_LABELS = {
  sales_manager: "Продажи",
  call_center_operator: "Call-центр",
};

const PIE_COLORS = ["#6366f1", "#14b8a6", "#f59e0b", "#ef4444", "#8b5cf6", "#10b981"];

const PRIORITY_RULES = {
  sales_manager: {
    strongTitle: [
      "менеджер по продажам",
      "менеджер продаж",
      "специалист по продажам",
      "sales manager",
      "менеджер по работе с клиентами",
      "менеджер по развитию продаж",
      "аккаунт-менеджер",
      "account manager",
      "менеджер b2b",
      "менеджер b2c",
    ],
    strongCategory: ["продажи", "b2b продажи", "b2c продажи", "клиентский сервис", "обслуживание клиентов"],
    mediumAll: ["работа с клиентами", "привлечение клиентов", "активные продажи", "холодные продажи", "телефонные продажи"],
    weakAll: ["менеджер", "sales", "аккаунт", "продаж"],
    negativeAll: ["бухгалтер", "водитель", "кладовщик", "повар", "охранник", "юрист", "дизайнер"],
  },
  call_center_operator: {
    strongTitle: [
      "оператор call-центра",
      "оператор call центра",
      "оператор колл-центра",
      "оператор колл центра",
      "оператор контакт-центра",
      "оператор контакт центра",
      "специалист контакт-центра",
      "специалист контакт центра",
      "оператор на телефоне",
      "телемаркетолог",
    ],
    strongCategory: ["контакт-центр", "контакт центр", "call-центр", "call центр", "колл-центр", "колл центр", "клиентский сервис"],
    mediumAll: ["входящие звонки", "исходящие звонки", "консультирование клиентов", "обработка обращений", "телефонные переговоры"],
    weakAll: ["оператор", "call", "колл", "контакт", "телефон"],
    negativeAll: ["бухгалтер", "водитель", "кладовщик", "повар", "охранник", "юрист", "дизайнер"],
  },
};

const PRIORITY_LABELS = {
  high: "Высокий",
  medium: "Средний",
  low: "Низкий",
};

const DEFAULT_PARSER_PAGES_PER_QUERY = 5;

const DEFAULT_PARSER_KEYWORDS = {
  sales_manager: [
    "менеджер по продажам",
    "специалист по продажам",
    "менеджер продаж",
    "sales manager",
    "менеджер по работе с клиентами",
    "аккаунт-менеджер",
    "менеджер по привлечению клиентов",
    "менеджер по развитию продаж",
    "менеджер b2b продаж",
    "менеджер b2c продаж",
  ],
  call_center_operator: [
    "оператор call центра",
    "оператор call-центра",
    "оператор колл центра",
    "оператор колл-центра",
    "оператор контакт центра",
    "оператор контакт-центра",
    "специалист call центра",
    "специалист контакт центра",
    "телемаркетолог",
    "оператор на телефоне",
    "специалист по работе с клиентами",
    "менеджер call центра",
  ],
};

const RECRUITING_STATS_HIDDEN_STORAGE_KEY = "recruiting.stats.hidden";

function normalizeText(value) {
  return String(value ?? "")
    .toLowerCase()
    .replace(/ё/g, "е")
    .replace(/\s+/g, " ")
    .trim();
}

function extractSalaryNumber(value) {
  if (!value) return null;
  const cleaned = String(value).replace(/[^\d]/g, "");
  if (!cleaned) return null;
  const number = Number(cleaned);
  return Number.isFinite(number) ? number : null;
}

function extractPublishedDate(value) {
  if (!value) return null;
  const match = String(value).match(/(\d{2})\.(\d{2})\.(\d{4})/);
  if (!match) return null;
  const [, dd, mm, yyyy] = match;
  return new Date(`${yyyy}-${mm}-${dd}T00:00:00`);
}

function scorePatternMatches(text, patterns, weight, hits, sourceLabel) {
  if (!text) return 0;
  let score = 0;
  patterns.forEach((pattern) => {
    const normalizedPattern = normalizeText(pattern);
    if (normalizedPattern && text.includes(normalizedPattern)) {
      score += weight;
      hits.push(sourceLabel + ": " + pattern);
    }
  });
  return score;
}

function getPriorityLabel(score) {
  if (score >= 70) return "high";
  if (score >= 40) return "medium";
  return "low";
}

function getResumePriority(item) {
  const title = normalizeText(item.title);
  const category = normalizeText(item.category);
  const query = normalizeText(item.keyword_query);
  const experience = normalizeText(item.experience);
  const all = normalizeText([item.title, item.category, item.keyword_query, item.experience].join(" "));
  const rules = PRIORITY_RULES[item.keyword_group] || PRIORITY_RULES.sales_manager;
  const hits = [];

  let score = 0;
  score += scorePatternMatches(title, rules.strongTitle, 34, hits, "title");
  score += scorePatternMatches(category, rules.strongCategory, 18, hits, "category");
  score += scorePatternMatches(all, rules.mediumAll, 10, hits, "text");
  score += scorePatternMatches(all, rules.weakAll, 4, hits, "weak");
  score -= scorePatternMatches(all, rules.negativeAll, 18, hits, "negative");

  if (query && title && (title.includes(query) || query.includes(title))) {
    score += 14;
    hits.push("query/title alignment");
  }

  if (experience.includes("без опыта")) {
    score += 2;
  }

  if (!title) score -= 20;
  if (!category) score -= 6;

  score = Math.max(0, Math.min(100, score));
  const priorityLabel = getPriorityLabel(score);
  const reason = [...new Set(hits)].slice(0, 4).join(" · ") || "Мало релевантных совпадений";

  return {
    relevanceScore: score,
    priorityLabel,
    priorityLabelRu: PRIORITY_LABELS[priorityLabel],
    priorityReason: reason,
  };
}

function getPriorityBadgeClass(label, active = false) {
  if (active) return "border-white/30 bg-white/20 text-white";
  if (label === "high") return "border-rose-200 bg-rose-50 text-rose-700";
  if (label === "medium") return "border-emerald-200 bg-emerald-50 text-emerald-700";
  return "border-sky-200 bg-sky-50 text-sky-700";
}

function formatMoney(num) {
  if (!num) return "—";
  return new Intl.NumberFormat("ru-RU").format(num) + " тг";
}

function formatDateTime(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString("ru-RU", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function downloadTextFile(filename, content, mimeType = "application/json;charset=utf-8") {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function toCsv(rows) {
  const headers = [
    "keyword_group",
    "keyword_query",
    "page_found",
    "title",
    "category",
    "experience",
    "location",
    "salary",
    "education",
    "published_at",
    "relevanceScore",
    "priorityLabelRu",
    "priorityReason",
    "detail_url",
  ];

  const escape = (value) => {
    const text = String(value ?? "");
    return `"${text.replace(/"/g, '""')}"`;
  };

  return [
    headers.join(","),
    ...rows.map((row) => headers.map((header) => escape(row[header])).join(",")),
  ].join("\n");
}

function toExcelHtml(rows) {
  const columns = [
    { key: "keyword_group", label: "Группа" },
    { key: "keyword_query", label: "Запрос" },
    { key: "page_found", label: "Страница" },
    { key: "title", label: "Должность" },
    { key: "category", label: "Категория" },
    { key: "experience", label: "Опыт" },
    { key: "location", label: "Локация" },
    { key: "salary", label: "Зарплата" },
    { key: "education", label: "Образование" },
    { key: "published_at", label: "Дата публикации" },
    { key: "relevanceScore", label: "Score" },
    { key: "priorityLabelRu", label: "Приоритет" },
    { key: "priorityReason", label: "Причина приоритета" },
    { key: "detail_url", label: "Ссылка" },
  ];

  const escapeHtml = (value) =>
    String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\"/g, "&quot;");

  const tableHeader = columns.map((col) => `<th>${escapeHtml(col.label)}</th>`).join("");
  const tableRows = rows
    .map((row) => {
      const cells = columns.map((col) => `<td>${escapeHtml(row[col.key])}</td>`).join("");
      return `<tr>${cells}</tr>`;
    })
    .join("");

  return [
    "<!DOCTYPE html>",
    '<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:x="urn:schemas-microsoft-com:office:excel" xmlns="http://www.w3.org/TR/REC-html40">',
    "<head>",
    '<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />',
    "<style>table{border-collapse:collapse}th,td{border:1px solid #d1d5db;padding:6px 8px;font-family:Arial,sans-serif;font-size:12px;}th{background:#f8fafc;font-weight:700;}</style>",
    "</head>",
    "<body>",
    `<table><thead><tr>${tableHeader}</tr></thead><tbody>${tableRows}</tbody></table>`,
    "</body>",
    "</html>",
  ].join("");
}

function StatCard({ title, value, hint, icon: Icon }) {
  return (
    <Card className="rounded-2xl border-slate-200/70 shadow-sm">
      <CardContent className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm text-slate-500">{title}</p>
            <div className="mt-2 text-2xl font-semibold text-slate-900">{value}</div>
            <p className="mt-2 text-xs text-slate-500">{hint}</p>
          </div>
          <div className="rounded-2xl bg-gradient-to-br from-blue-500 to-indigo-500 p-3 text-white shadow-sm">
            <Icon className="h-5 w-5" />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function EmptyState({ onRefresh, isRefreshing = false }) {
  return (
    <Card className="rounded-3xl border-dashed border-slate-300 bg-white/80 shadow-sm">
      <CardContent className="flex flex-col items-center justify-center px-6 py-16 text-center">
        <div className="rounded-3xl bg-slate-100 p-4">
          <Database className="h-8 w-8 text-slate-700" />
        </div>
        <h3 className="mt-5 text-xl font-semibold text-slate-900">Нет данных для отображения</h3>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
          Обновите данные из API или запустите парсер, чтобы загрузить резюме и увидеть аналитику.
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Button onClick={onRefresh} disabled={isRefreshing} className="rounded-2xl bg-blue-600 px-5 text-white hover:bg-blue-700">
            <RefreshCw className={`mr-2 h-4 w-4 ${isRefreshing ? "animate-spin" : ""}`} />
            {isRefreshing ? "Обновление..." : "Обновить из API"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ResumeRow({ item, active, onClick }) {
  const salaryNum = extractSalaryNumber(item.salary);

  return (
    <motion.button
      whileHover={{ y: -1 }}
      onClick={onClick}
      className={`w-full rounded-2xl border p-4 text-left transition ${
        active
          ? "border-blue-600 bg-gradient-to-r from-blue-600 to-indigo-600 text-white shadow-lg"
          : "border-slate-200 bg-white hover:border-blue-300 hover:shadow-sm"
      }`}
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className={`truncate text-base font-semibold ${active ? "text-white" : "text-slate-900"}`}>{item.title || "Без названия"}</h4>
            <Badge variant={active ? "secondary" : "outline"} className="rounded-full">
              {GROUP_LABELS[item.keyword_group] || item.keyword_group || "Группа"}
            </Badge>
            <Badge className={`rounded-full border ${getPriorityBadgeClass(item.priorityLabel, active)}`}>
              {item.priorityLabelRu} · {item.relevanceScore}
            </Badge>
          </div>

          <p className={`mt-1 text-sm ${active ? "text-slate-200" : "text-slate-500"}`}>{item.category || "Категория не указана"}</p>
          <p className={`mt-2 line-clamp-2 text-xs ${active ? "text-slate-300" : "text-slate-500"}`}>{item.priorityReason}</p>

          <div className={`mt-3 flex flex-wrap gap-3 text-xs ${active ? "text-slate-200" : "text-slate-600"}`}>
            <span className="inline-flex items-center gap-1.5"><MapPin className="h-3.5 w-3.5" /> {item.location || "Локация не указана"}</span>
            <span className="inline-flex items-center gap-1.5"><Briefcase className="h-3.5 w-3.5" /> {item.experience || "Опыт не указан"}</span>
            <span className="inline-flex items-center gap-1.5"><Wallet className="h-3.5 w-3.5" /> {salaryNum ? formatMoney(salaryNum) : item.salary || "Зарплата не указана"}</span>
          </div>
        </div>

        <div className={`flex items-center gap-3 text-sm ${active ? "text-slate-200" : "text-slate-500"}`}>
          <span>{item.published_at || "Без даты"}</span>
          <ChevronRight className="h-4 w-4" />
        </div>
      </div>
    </motion.button>
  );
}

export default function EnbekResumeDashboard({ user, showToast, apiBaseUrl, withAccessTokenHeader }) {
  const bootstrapKeyRef = useRef("");
  const loadRequestRef = useRef(null);
  const [rawItems, setRawItems] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [search, setSearch] = useState("");
  const [groupFilter, setGroupFilter] = useState("all");
  const [priorityFilter, setPriorityFilter] = useState("medium_plus");
  const [sortBy, setSortBy] = useState("priority_desc");
  const [onlyWithSalary, setOnlyWithSalary] = useState(false);
  const [onlyFresh, setOnlyFresh] = useState(false);
  const [minSalary, setMinSalary] = useState("");
  const [importError, setImportError] = useState("");
  const [isRunningParser, setIsRunningParser] = useState(false);
  const [isLoadingFromApi, setIsLoadingFromApi] = useState(false);
  const [isStatsHidden, setIsStatsHidden] = useState(() => {
    if (typeof window === "undefined") return false;
    try {
      const saved = window.localStorage.getItem(RECRUITING_STATS_HIDDEN_STORAGE_KEY);
      return saved === "1" || saved === "true";
    } catch (_error) {
      return false;
    }
  });
  const [lastRunMeta, setLastRunMeta] = useState(null);
  const [apiStatusMessage, setApiStatusMessage] = useState("");
  const [isParserModalOpen, setIsParserModalOpen] = useState(false);
  const [parserPagesPerQuery, setParserPagesPerQuery] = useState(String(DEFAULT_PARSER_PAGES_PER_QUERY));
  const [parserKeywordDrafts, setParserKeywordDrafts] = useState({
    sales_manager: DEFAULT_PARSER_KEYWORDS.sales_manager.join("\n"),
    call_center_operator: DEFAULT_PARSER_KEYWORDS.call_center_operator.join("\n"),
  });
  const [parserJobId, setParserJobId] = useState("");
  const [parserJobStatus, setParserJobStatus] = useState("idle");
  const [parserProgressPercent, setParserProgressPercent] = useState(0);

  useEffect(() => {
    if (!rawItems.length) {
      setSelectedId(null);
      return;
    }
    if (!selectedId || !rawItems.some((item) => item.__id === selectedId)) {
      setSelectedId(rawItems[0].__id);
    }
  }, [rawItems, selectedId]);

  const buildApiHeaders = useCallback((extraHeaders = {}) => {
    let headers = {
      Accept: "application/json",
      ...extraHeaders,
    };

    if (user?.apiKey) {
      headers["X-API-Key"] = user.apiKey;
    }
    if (user?.id) {
      headers["X-User-Id"] = String(user.id);
    }
    if (typeof withAccessTokenHeader === "function") {
      headers = withAccessTokenHeader(headers) || headers;
    }
    return headers;
  }, [user?.apiKey, user?.id, withAccessTokenHeader]);

  const buildParserKeywordGroupsPayload = useCallback(() => {
    const normalized = {};
    Object.entries(parserKeywordDrafts || {}).forEach(([group, draft]) => {
      const list = String(draft || "")
        .split(/\r?\n/)
        .map((item) => item.trim())
        .filter(Boolean);
      if (list.length) {
        normalized[group] = list;
      }
    });
    return normalized;
  }, [parserKeywordDrafts]);

  const restoreDefaultParserSettings = useCallback(() => {
    setParserPagesPerQuery(String(DEFAULT_PARSER_PAGES_PER_QUERY));
    setParserKeywordDrafts({
      sales_manager: DEFAULT_PARSER_KEYWORDS.sales_manager.join("\n"),
      call_center_operator: DEFAULT_PARSER_KEYWORDS.call_center_operator.join("\n"),
    });
  }, []);

  const normalizeIncomingItems = useCallback((items, source = "api") => {
    if (!Array.isArray(items)) return [];
    return items.map((item, index) => ({
      ...item,
      __id:
        item.__id ||
        (item.id
          ? `${source}-${item.id}`
          : `${source}-${item.detail_url || item.title || "resume"}-${index}`),
    }));
  }, []);

  const loadResumesFromApi = useCallback(async ({ runUuid = null, silent = false } = {}) => {
    if (!apiBaseUrl) {
      throw new Error("API URL не настроен.");
    }

    const requestKey = runUuid ? `run:${runUuid}` : "latest";
    if (loadRequestRef.current?.key === requestKey) {
      if (!silent) {
        setIsLoadingFromApi(true);
      }
      try {
        return await loadRequestRef.current.promise;
      } finally {
        if (!silent) {
          setIsLoadingFromApi(false);
        }
      }
    }

    const requestPromise = (async () => {
      if (!silent) {
        setIsLoadingFromApi(true);
        setApiStatusMessage("");
      }

      try {
        const query = new URLSearchParams({ limit: "5000" });
        if (runUuid) {
          query.set("run_uuid", runUuid);
        }

        const response = await fetch(`${apiBaseUrl}/api/recruiting/resumes?${query.toString()}`, {
          method: "GET",
          headers: buildApiHeaders(),
        });

        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          const errorText =
            payload?.error ||
            payload?.message ||
            `Не удалось загрузить резюме (${response.status})`;
          throw new Error(errorText);
        }

        const items = normalizeIncomingItems(payload?.items, "db");
        setRawItems(items);
        setLastRunMeta(payload?.run || payload?.latest_run || null);
        setImportError("");

        if (!silent) {
          const totalCount = Number(payload?.total ?? items.length ?? 0);
          setApiStatusMessage(
            totalCount > 0
              ? `Загружено ${totalCount} резюме из backend.`
              : "Последний запуск найден, но резюме пока нет."
          );
        }

        return payload;
      } finally {
        if (!silent) {
          setIsLoadingFromApi(false);
        }
      }
    })();

    loadRequestRef.current = { key: requestKey, promise: requestPromise };
    try {
      return await requestPromise;
    } finally {
      if (loadRequestRef.current?.promise === requestPromise) {
        loadRequestRef.current = null;
      }
    }
  }, [apiBaseUrl, buildApiHeaders, normalizeIncomingItems]);

  const handleRefreshFromApi = useCallback(async () => {
    try {
      await loadResumesFromApi();
    } catch (error) {
      const errorText = error?.message || "Не удалось обновить данные из API.";
      setImportError(errorText);
      showToast?.(errorText, "error");
    }
  }, [loadResumesFromApi, showToast]);

  const normalizePercent = useCallback((value) => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return null;
    return Math.max(0, Math.min(100, Math.round(numeric)));
  }, []);

  const inferProgressFromMessages = useCallback((messages) => {
    if (!Array.isArray(messages) || !messages.length) return null;

    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const text = String(messages[i]?.text || "");

      const queryMatch = text.match(/(?:Query|\u0417\u0430\u043F\u0440\u043E\u0441)\s+(\d+)\s*\/\s*(\d+)/i);
      if (queryMatch) {
        const current = Number(queryMatch[1]);
        const total = Number(queryMatch[2]);
        if (Number.isFinite(current) && Number.isFinite(total) && total > 0) {
          return normalizePercent((current / total) * 100);
        }
      }

      const progressMatch = text.match(/(?:Progress|\u041F\u0440\u043E\u0433\u0440\u0435\u0441\u0441):\s*(\d+)\s*\/\s*(\d+)/i);
      if (progressMatch) {
        const current = Number(progressMatch[1]);
        const total = Number(progressMatch[2]);
        if (Number.isFinite(current) && Number.isFinite(total) && total > 0) {
          return normalizePercent((current / total) * 100);
        }
      }
    }

    return null;
  }, [normalizePercent]);

  const getJobProgressPercent = useCallback((job) => {
    const status = String(job?.status || "idle");
    if (status === "success") return 100;
    const direct = normalizePercent(job?.progress_percent);
    if (direct !== null) return direct;
    const inferred = inferProgressFromMessages(job?.messages);
    if (inferred !== null) return inferred;
    if (status === "running" || status === "starting") return 1;
    return 0;
  }, [inferProgressFromMessages, normalizePercent]);

  const handleRunParserManually = useCallback(async () => {
    if (!apiBaseUrl) {
      const errorText = "API URL не настроен.";
      setImportError(errorText);
      showToast?.(errorText, "error");
      return;
    }

    const pages = Number(parserPagesPerQuery);
    if (!Number.isFinite(pages) || pages < 1 || pages > 20) {
      const errorText = "Количество страниц должно быть числом от 1 до 20.";
      setImportError(errorText);
      showToast?.(errorText, "error");
      return;
    }

    const keywordGroups = buildParserKeywordGroupsPayload();
    const keywordCount = Object.values(keywordGroups).reduce((sum, list) => sum + list.length, 0);
    if (!keywordCount) {
      const errorText = "Добавь хотя бы одно ключевое слово для запуска парсера.";
      setImportError(errorText);
      showToast?.(errorText, "error");
      return;
    }

    setIsRunningParser(true);
    setImportError("");
    setApiStatusMessage("");
    setParserProgressPercent(0);
    setParserJobStatus("starting");

    try {
      const response = await fetch(`${apiBaseUrl}/api/recruiting/run`, {
        method: "POST",
        headers: buildApiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          async: true,
          pages_per_query: pages,
          keyword_groups: keywordGroups,
        }),
      });

      const payload = await response.json().catch(() => ({}));
      if (response.status === 409) {
        const activeJob = payload?.job || null;
        if (activeJob?.job_id) {
          setParserJobId(activeJob.job_id);
          setParserJobStatus(activeJob.status || "running");
          setParserProgressPercent(getJobProgressPercent(activeJob));
          setApiStatusMessage(payload?.message || "Парсер уже выполняется. Процент обновится после перезагрузки страницы.");
          setIsRunningParser(true);
          showToast?.(payload?.message || "Парсер уже выполняется.", "error");
          return;
        }
        throw new Error(payload?.message || "Парсер уже выполняется. Попробуй позже.");
      }

      if (!response.ok) {
        const fallback =
          response.status === 409
            ? "Парсер уже выполняется. Попробуй запустить чуть позже."
            : `Не удалось запустить парсер (${response.status}).`;
        throw new Error(payload?.message || payload?.error || fallback);
      }

      const job = payload?.job || null;
      if (!job?.job_id) {
        throw new Error("Сервер не вернул идентификатор задачи парсинга.");
      }

      setParserJobId(job.job_id);
      setParserJobStatus(job.status || "running");
      setParserProgressPercent(getJobProgressPercent(job));
      setApiStatusMessage("Парсер запущен. Процент обновится после перезагрузки страницы.");
      showToast?.("Парсер запущен в фоне.", "success");
    } catch (error) {
      const errorText = error?.message || "Ошибка ручного запуска парсера.";
      setImportError(errorText);
      showToast?.(errorText, "error");
      setIsRunningParser(false);
    }
  }, [apiBaseUrl, buildApiHeaders, buildParserKeywordGroupsPayload, getJobProgressPercent, parserPagesPerQuery, showToast]);

  useEffect(() => {
    let isCancelled = false;
    const bootstrapKey = `${apiBaseUrl || ""}|${user?.id || ""}|${user?.apiKey || ""}`;

    if (!apiBaseUrl || !user?.id || bootstrapKeyRef.current === bootstrapKey) {
      return undefined;
    }
    bootstrapKeyRef.current = bootstrapKey;

    const bootstrapFromApi = async () => {
      setIsLoadingFromApi(true);
      try {
        const payload = await loadResumesFromApi({ silent: true });
        if (isCancelled || !payload) return;

        const totalCount = Number(payload?.total ?? payload?.items?.length ?? 0);
        if (totalCount > 0) {
          setApiStatusMessage(`Загружено ${totalCount} резюме из последнего запуска.`);
        } else if (payload?.latest_run || payload?.run) {
          setApiStatusMessage("Последний запуск найден, но выдача пока пустая.");
        }
      } catch (error) {
        if (!isCancelled) {
          setApiStatusMessage("");
        }
      } finally {
        if (!isCancelled) {
          setIsLoadingFromApi(false);
        }
      }
    };

    bootstrapFromApi();

    return () => {
      isCancelled = true;
    };
  }, [apiBaseUrl, user?.id, user?.apiKey, loadResumesFromApi]);

  useEffect(() => {
    let isCancelled = false;
    if (!apiBaseUrl || !user?.id) {
      return undefined;
    }

    const attachActiveJobIfAny = async () => {
      try {
        const response = await fetch(`${apiBaseUrl}/api/recruiting/run/status`, {
          method: "GET",
          headers: buildApiHeaders(),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || isCancelled) {
          return;
        }

        const job = payload?.job || null;
        if (!job?.job_id) {
          return;
        }

        setParserJobStatus(job.status || "idle");
        setParserProgressPercent(getJobProgressPercent(job));
        if (job?.result?.run) {
          setLastRunMeta(job.result.run);
        }
        if (job.status === "running" || job.status === "starting") {
          setParserJobId(job.job_id);
          setIsRunningParser(true);
          setApiStatusMessage("Подключен к активному запуску парсера. Процент показан на момент последней перезагрузки.");
        } else {
          setParserJobId("");
          setIsRunningParser(false);
        }
      } catch (_error) {
        // Silent by design: section should still work without runtime job snapshot.
      }
    };

    attachActiveJobIfAny();
    return () => {
      isCancelled = true;
    };
  }, [apiBaseUrl, buildApiHeaders, getJobProgressPercent, user?.id]);

  useEffect(() => {
    if (!isParserModalOpen) {
      return undefined;
    }

    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        setIsParserModalOpen(false);
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isParserModalOpen]);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        RECRUITING_STATS_HIDDEN_STORAGE_KEY,
        isStatsHidden ? "1" : "0"
      );
    } catch (_error) {
      // Ignore localStorage write failures.
    }
  }, [isStatsHidden]);

  const hydratedItems = useMemo(() => {
    return rawItems.map((item, index) => {
      const priority = getResumePriority(item);
      return {
        ...item,
        ...priority,
        __id: item.__id || `${item.detail_url || item.title || "resume"}-${index}`,
        salaryNum: extractSalaryNumber(item.salary),
        publishedDate: extractPublishedDate(item.published_at),
        groupLabel: GROUP_LABELS[item.keyword_group] || item.keyword_group || "Другое",
      };
    });
  }, [rawItems]);

  const filteredItems = useMemo(() => {
    let list = [...hydratedItems];
    const searchText = normalizeText(search);
    const minSalaryValue = Number(minSalary || 0);
    const today = new Date("2026-04-01T00:00:00");

    if (groupFilter !== "all") {
      list = list.filter((item) => item.keyword_group === groupFilter);
    }

    if (priorityFilter === "high_only") {
      list = list.filter((item) => item.priorityLabel === "high");
    } else if (priorityFilter === "medium_plus") {
      list = list.filter((item) => item.priorityLabel === "high" || item.priorityLabel === "medium");
    } else if (priorityFilter === "low_only") {
      list = list.filter((item) => item.priorityLabel === "low");
    }

    if (searchText) {
      list = list.filter((item) => {
        const haystack = normalizeText([
          item.title,
          item.category,
          item.location,
          item.experience,
          item.education,
          item.keyword_query,
        ].join(" "));
        return haystack.includes(searchText);
      });
    }

    if (onlyWithSalary) {
      list = list.filter((item) => Boolean(item.salaryNum));
    }

    if (onlyFresh) {
      list = list.filter((item) => item.publishedDate && item.publishedDate >= new Date(today.getTime() - 3 * 24 * 60 * 60 * 1000));
    }

    if (minSalaryValue > 0) {
      list = list.filter((item) => (item.salaryNum || 0) >= minSalaryValue);
    }

    list.sort((a, b) => {
      switch (sortBy) {
        case "priority_desc":
          return (b.relevanceScore || 0) - (a.relevanceScore || 0);
        case "priority_asc":
          return (a.relevanceScore || 0) - (b.relevanceScore || 0);
        case "salary_desc":
          return (b.salaryNum || 0) - (a.salaryNum || 0);
        case "salary_asc":
          return (a.salaryNum || 0) - (b.salaryNum || 0);
        case "title_asc":
          return String(a.title || "").localeCompare(String(b.title || ""), "ru");
        case "published_asc":
          return (a.publishedDate?.getTime() || 0) - (b.publishedDate?.getTime() || 0);
        case "published_desc":
        default:
          return (b.publishedDate?.getTime() || 0) - (a.publishedDate?.getTime() || 0);
      }
    });

    return list;
  }, [hydratedItems, search, groupFilter, priorityFilter, sortBy, onlyWithSalary, onlyFresh, minSalary]);

  const selectedItem = useMemo(() => {
    return filteredItems.find((item) => item.__id === selectedId) || filteredItems[0] || null;
  }, [filteredItems, selectedId]);

  const stats = useMemo(() => {
    const total = filteredItems.length;
    const withSalary = filteredItems.filter((item) => item.salaryNum);
    const avgSalary = withSalary.length
      ? Math.round(withSalary.reduce((sum, item) => sum + item.salaryNum, 0) / withSalary.length)
      : 0;
    const freshCount = filteredItems.filter((item) => item.publishedDate && item.publishedDate >= new Date("2026-03-29T00:00:00")).length;
    const highPriorityCount = filteredItems.filter((item) => item.priorityLabel === "high").length;
    const avgRelevance = filteredItems.length
      ? Math.round(filteredItems.reduce((sum, item) => sum + (item.relevanceScore || 0), 0) / filteredItems.length)
      : 0;
    return { total, avgSalary, freshCount, highPriorityCount, avgRelevance };
  }, [filteredItems]);

  const groupChartData = useMemo(() => {
    const counts = filteredItems.reduce((acc, item) => {
      const key = item.groupLabel;
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});

    return Object.entries(counts).map(([name, value]) => ({ name, value }));
  }, [filteredItems]);

  const queryChartData = useMemo(() => {
    const counts = filteredItems.reduce((acc, item) => {
      const key = item.keyword_query || "Без запроса";
      acc[key] = (acc[key] || 0) + 1;
      return acc;
    }, {});

    return Object.entries(counts)
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 8);
  }, [filteredItems]);

  const locationsData = useMemo(() => {
    const counts = filteredItems.reduce((acc, item) => {
      const raw = item.location || "Не указано";
      const normalized = raw.replace(/^г\.\s*/i, "").split(",")[0].trim() || raw;
      acc[normalized] = (acc[normalized] || 0) + 1;
      return acc;
    }, {});

    return Object.entries(counts)
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 6);
  }, [filteredItems]);

  const parserKeywordStats = useMemo(() => {
    const groups = buildParserKeywordGroupsPayload();
    const groupCount = Object.keys(groups).length;
    const keywordCount = Object.values(groups).reduce((sum, list) => sum + list.length, 0);
    return { groupCount, keywordCount };
  }, [buildParserKeywordGroupsPayload]);

  const parserStatusLabel = useMemo(() => {
    const status = String(parserJobStatus || "idle");
    if (status === "running" || status === "starting") return "Выполняется";
    if (status === "success") return "Успешно";
    if (status === "failed") return "Ошибка";
    if (status === "skipped") return "Пропущен";
    return "Ожидание";
  }, [parserJobStatus]);

  const parserStatusClass = useMemo(() => {
    const status = String(parserJobStatus || "idle");
    if (status === "running" || status === "starting") return "border-blue-200 bg-blue-50 text-blue-700";
    if (status === "success") return "border-emerald-200 bg-emerald-50 text-emerald-700";
    if (status === "failed") return "border-red-200 bg-red-50 text-red-700";
    if (status === "skipped") return "border-amber-200 bg-amber-50 text-amber-700";
    return "border-slate-200 bg-slate-50 text-slate-600";
  }, [parserJobStatus]);

  const parserProgressValue = useMemo(() => {
    const normalized = normalizePercent(parserProgressPercent);
    return normalized === null ? 0 : normalized;
  }, [normalizePercent, parserProgressPercent]);

  const resetFilters = () => {
    setSearch("");
    setGroupFilter("all");
    setPriorityFilter("medium_plus");
    setSortBy("priority_desc");
    setOnlyWithSalary(false);
    setOnlyFresh(false);
    setMinSalary("");
  };

  const exportFilteredExcel = () => {
    downloadTextFile(
      "enbek_filtered_resumes.xls",
      toExcelHtml(filteredItems),
      "application/vnd.ms-excel;charset=utf-8"
    );
  };

  const exportFilteredCsv = () => {
    downloadTextFile("enbek_filtered_resumes.csv", toCsv(filteredItems), "text/csv;charset=utf-8");
  };

  return (
    <div className="min-h-screen bg-gradient-to-b from-sky-50 via-white to-indigo-50 text-slate-900">
      <div className="mx-auto max-w-[1700px] px-4 py-6 sm:px-6 lg:px-8 2xl:max-w-[1900px]">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="mb-6"
        >
          <div className="rounded-3xl border border-blue-100/80 bg-white/90 p-6 shadow-sm backdrop-blur">
            <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <div className="inline-flex items-center gap-2 rounded-full bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700">
                  <Sparkles className="h-3.5 w-3.5" />
                  Панель анализа резюме Enbek
                </div>
                <h1 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">Удобный просмотр и анализ результатов парсинга</h1>
                <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">
                  Обновляй данные из API, применяй фильтры по ролям, зарплате и свежести, анализируй карточки кандидатов
                  и выгружай готовую выборку в Excel или CSV.
                </p>
              </div>

              <div className="flex flex-wrap gap-3">
                <Button
                  variant="outline"
                  className="rounded-2xl border-blue-200 text-blue-700 hover:bg-blue-50"
                  onClick={handleRefreshFromApi}
                  disabled={isLoadingFromApi || isRunningParser}
                >
                  <RefreshCw className={`mr-2 h-4 w-4 ${isLoadingFromApi ? "animate-spin" : ""}`} />
                  Обновить из API
                </Button>
                <Button
                  variant="outline"
                  className="rounded-2xl border-emerald-200 text-emerald-700 hover:bg-emerald-50"
                  onClick={() => setIsParserModalOpen(true)}
                >
                  <RefreshCw className={`mr-2 h-4 w-4 ${isRunningParser ? "animate-spin" : ""}`} />
                  Запустить парсер
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  className="rounded-2xl border-violet-200 text-violet-700 hover:bg-violet-50"
                  onClick={() => setIsStatsHidden((prev) => !prev)}
                >
                  <BarChart3 className="mr-2 h-4 w-4" />
                  {isStatsHidden ? "Показать статистику" : "Скрыть статистику"}
                </Button>
                <Button className="rounded-2xl bg-indigo-600 text-white hover:bg-indigo-700" onClick={exportFilteredExcel} disabled={!filteredItems.length}>
                  <Download className="mr-2 h-4 w-4" />
                  Export Excel
                </Button>
              </div>
            </div>
            {(apiStatusMessage || lastRunMeta) ? (
              <div className="mt-4 rounded-2xl border border-blue-100 bg-blue-50/60 px-4 py-3 text-sm text-slate-700">
                {apiStatusMessage ? <p>{apiStatusMessage}</p> : null}
                {lastRunMeta ? (
                  <p className="mt-1 text-xs text-slate-500">
                    Последний запуск: {formatDateTime(lastRunMeta.finished_at || lastRunMeta.started_at)} · статус {lastRunMeta.status || "unknown"} ·
                    записей {Number(lastRunMeta.total_items || 0)}
                  </p>
                ) : null}
              </div>
            ) : null}
          </div>
        </motion.div>

        <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)] 2xl:grid-cols-[420px_minmax(0,1fr)]">
          <div className="flex flex-col gap-6">
            <Card className="order-2 rounded-3xl border-slate-200/70 shadow-sm">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-lg"><SlidersHorizontal className="h-5 w-5" /> Фильтры и экспорт</CardTitle>
                <CardDescription>Фильтруйте выдачу и выгружайте результат в Excel или CSV.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-5">
                <div className="space-y-2">
                  <Label>Быстрый поиск</Label>
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                    <Input
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      placeholder="Например: продажи, call-центр, Алматы"
                      className="rounded-2xl pl-10"
                    />
                  </div>
                </div>

                <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
                  <div className="space-y-2">
                    <Label>Группа</Label>
                    <Select value={groupFilter} onValueChange={setGroupFilter}>
                      <SelectTrigger className="rounded-2xl">
                        <SelectValue placeholder="Все группы" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">Все группы</SelectItem>
                        <SelectItem value="sales_manager">Продажи</SelectItem>
                        <SelectItem value="call_center_operator">Call-центр</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-2">
                    <Label>Приоритет</Label>
                    <Select value={priorityFilter} onValueChange={setPriorityFilter}>
                      <SelectTrigger className="rounded-2xl">
                        <SelectValue placeholder="Фильтр по приоритету" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">Все</SelectItem>
                        <SelectItem value="medium_plus">Средний и высокий</SelectItem>
                        <SelectItem value="high_only">Только высокий</SelectItem>
                        <SelectItem value="low_only">Только низкий</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>

                  <div className="space-y-2">
                    <Label>Сортировка</Label>
                    <Select value={sortBy} onValueChange={setSortBy}>
                      <SelectTrigger className="rounded-2xl">
                        <SelectValue placeholder="Выбери сортировку" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="priority_desc">Сначала приоритетные</SelectItem>
                        <SelectItem value="priority_asc">Сначала низкий приоритет</SelectItem>
                        <SelectItem value="published_desc">Сначала свежие</SelectItem>
                        <SelectItem value="published_asc">Сначала старые</SelectItem>
                        <SelectItem value="salary_desc">Зарплата ↓</SelectItem>
                        <SelectItem value="salary_asc">Зарплата ↑</SelectItem>
                        <SelectItem value="title_asc">По названию</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>Минимальная зарплата</Label>
                  <Input
                    type="number"
                    min="0"
                    value={minSalary}
                    onChange={(e) => setMinSalary(e.target.value)}
                    placeholder="Например: 250000"
                    className="rounded-2xl"
                  />
                </div>

                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    variant={onlyWithSalary ? "default" : "outline"}
                    className={`rounded-2xl ${
                      onlyWithSalary
                        ? "bg-emerald-600 text-white hover:bg-emerald-700"
                        : "border-emerald-200 text-emerald-700 hover:bg-emerald-50"
                    }`}
                    onClick={() => setOnlyWithSalary((v) => !v)}
                  >
                    <Wallet className="mr-2 h-4 w-4" />
                    Только с зарплатой
                  </Button>
                  <Button
                    type="button"
                    variant={onlyFresh ? "default" : "outline"}
                    className={`rounded-2xl ${
                      onlyFresh
                        ? "bg-sky-600 text-white hover:bg-sky-700"
                        : "border-sky-200 text-sky-700 hover:bg-sky-50"
                    }`}
                    onClick={() => setOnlyFresh((v) => !v)}
                  >
                    <Clock3 className="mr-2 h-4 w-4" />
                    Свежие за 3 дня
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    className="rounded-2xl text-amber-700 hover:bg-amber-50 hover:text-amber-800"
                    onClick={resetFilters}
                  >
                    <RefreshCw className="mr-2 h-4 w-4" />
                    Сбросить
                  </Button>
                </div>

                <Separator />

                <div className="space-y-2">
                  <Label>Экспорт текущей выборки</Label>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      onClick={exportFilteredExcel}
                      disabled={!filteredItems.length}
                      className="rounded-2xl bg-indigo-600 text-white hover:bg-indigo-700"
                    >
                      <Download className="mr-2 h-4 w-4" />
                      Export Excel
                    </Button>
                    <Button
                      variant="outline"
                      onClick={exportFilteredCsv}
                      disabled={!filteredItems.length}
                      className="rounded-2xl border-cyan-200 text-cyan-700 hover:bg-cyan-50"
                    >
                      <Download className="mr-2 h-4 w-4" />
                      Export CSV
                    </Button>
                  </div>
                </div>

                {importError ? (
                  <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{importError}</div>
                ) : null}
              </CardContent>
            </Card>

            {!isStatsHidden ? (
              <Card className="order-1 rounded-3xl border-slate-200/70 shadow-sm">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-lg"><BarChart3 className="h-5 w-5" /> Краткая сводка</CardTitle>
                  <CardDescription>Срез по текущим фильтрам.</CardDescription>
                </CardHeader>
                <CardContent className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
                  <StatCard title="Резюме" value={stats.total} hint="После применения фильтров" icon={Users} />
                  <StatCard title="Средняя зарплата" value={formatMoney(stats.avgSalary)} hint="Среди записей, где зарплата указана" icon={Wallet} />
                  <StatCard title="Свежие" value={stats.freshCount} hint="Опубликовано примерно за последние 3 дня" icon={CalendarDays} />
                  <StatCard title="Приоритетные" value={stats.highPriorityCount} hint="Резюме с высоким score релевантности" icon={Filter} />
                  <StatCard title="Средний score" value={stats.avgRelevance} hint="Средняя релевантность текущей выборки" icon={Sparkles} />
                </CardContent>
              </Card>
            ) : null}
          </div>

          <div className="space-y-6">
            {!hydratedItems.length ? (
              <EmptyState onRefresh={handleRefreshFromApi} isRefreshing={isLoadingFromApi} />
            ) : (
              <>
                {!isStatsHidden ? (
                  <>
                    <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr] 2xl:grid-cols-[1fr_1fr]">
                      <Card className="rounded-3xl border-slate-200/70 shadow-sm">
                        <CardHeader>
                          <CardTitle>Распределение по группам</CardTitle>
                          <CardDescription>Что преобладает в текущей выборке.</CardDescription>
                        </CardHeader>
                        <CardContent className="h-[280px] 2xl:h-[320px]">
                          <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={groupChartData}>
                              <XAxis dataKey="name" tickLine={false} axisLine={false} />
                              <YAxis allowDecimals={false} tickLine={false} axisLine={false} />
                              <Tooltip />
                              <Bar dataKey="value" radius={[10, 10, 0, 0]} />
                            </BarChart>
                          </ResponsiveContainer>
                        </CardContent>
                      </Card>

                      <Card className="rounded-3xl border-slate-200/70 shadow-sm">
                        <CardHeader>
                          <CardTitle>Топ районов / локаций</CardTitle>
                          <CardDescription>Сгруппировано по первому блоку в поле location.</CardDescription>
                        </CardHeader>
                        <CardContent className="h-[280px] 2xl:h-[320px]">
                          <ResponsiveContainer width="100%" height="100%">
                            <PieChart>
                              <Pie data={locationsData} dataKey="value" nameKey="name" innerRadius={58} outerRadius={88} paddingAngle={3}>
                                {locationsData.map((entry, index) => (
                                  <Cell key={entry.name} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                                ))}
                              </Pie>
                              <Tooltip />
                            </PieChart>
                          </ResponsiveContainer>
                        </CardContent>
                      </Card>
                    </div>

                    <Card className="rounded-3xl border-slate-200/70 shadow-sm">
                      <CardHeader>
                        <CardTitle>Топ поисковых запросов</CardTitle>
                        <CardDescription>Какие формулировки дали больше резюме в текущем наборе.</CardDescription>
                      </CardHeader>
                      <CardContent className="h-[260px] 2xl:h-[320px]">
                        <ResponsiveContainer width="100%" height="100%">
                          <BarChart data={queryChartData} layout="vertical" margin={{ left: 24 }}>
                            <XAxis type="number" allowDecimals={false} tickLine={false} axisLine={false} />
                            <YAxis type="category" dataKey="name" width={180} tickLine={false} axisLine={false} />
                            <Tooltip />
                            <Bar dataKey="value" radius={[0, 10, 10, 0]} />
                          </BarChart>
                        </ResponsiveContainer>
                      </CardContent>
                    </Card>
                  </>
                ) : null}

                <Tabs defaultValue="list" className="space-y-6">
                  <TabsList className="grid w-full grid-cols-2 rounded-2xl bg-blue-50 p-1">
                    <TabsTrigger
                      value="list"
                      className="rounded-2xl text-slate-600 data-[state=active]:bg-white data-[state=active]:text-blue-700 data-[state=active]:shadow-sm"
                    >
                      Список резюме
                    </TabsTrigger>
                    <TabsTrigger
                      value="detail"
                      className="rounded-2xl text-slate-600 data-[state=active]:bg-white data-[state=active]:text-blue-700 data-[state=active]:shadow-sm"
                    >
                      Карточка резюме
                    </TabsTrigger>
                  </TabsList>

                  <TabsContent value="list" className="mt-0">
                    <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr] 2xl:grid-cols-[1.2fr_0.8fr]">
                      <Card className="rounded-3xl border-slate-200/70 shadow-sm">
                        <CardHeader>
                          <CardTitle>Найденные резюме</CardTitle>
                          <CardDescription>{filteredItems.length} записей после фильтрации.</CardDescription>
                        </CardHeader>
                        <CardContent>
                          <ScrollArea className="h-[720px] pr-3 2xl:h-[820px]">
                            <div className="space-y-3">
                              {filteredItems.map((item) => (
                                <ResumeRow
                                  key={item.__id}
                                  item={item}
                                  active={selectedItem?.__id === item.__id}
                                  onClick={() => setSelectedId(item.__id)}
                                />
                              ))}
                            </div>
                          </ScrollArea>
                        </CardContent>
                      </Card>

                      <Card className="rounded-3xl border-slate-200/70 shadow-sm">
                        <CardHeader>
                          <CardTitle>Быстрый просмотр</CardTitle>
                          <CardDescription>Выбери строку слева, чтобы увидеть детали.</CardDescription>
                        </CardHeader>
                        <CardContent>
                          {selectedItem ? (
                            <div className="space-y-5">
                              <div>
                                <div className="flex flex-wrap items-center gap-2">
                                  <Badge className="rounded-full bg-indigo-600 text-white hover:bg-indigo-600">{selectedItem.groupLabel}</Badge>
                                  <Badge className={`rounded-full border ${getPriorityBadgeClass(selectedItem.priorityLabel)}`}>{selectedItem.priorityLabelRu} · {selectedItem.relevanceScore}</Badge>
                                  <Badge variant="outline" className="rounded-full">стр. {selectedItem.page_found || "—"}</Badge>
                                </div>
                                <h3 className="mt-3 text-2xl font-semibold text-slate-900">{selectedItem.title || "Без названия"}</h3>
                                <p className="mt-2 text-sm text-slate-500">{selectedItem.category || "Категория не указана"}</p>
                              </div>

                              <div className="grid gap-3 sm:grid-cols-2">
                                <div className="rounded-2xl bg-slate-50 p-4">
                                  <div className="text-xs text-slate-500">Локация</div>
                                  <div className="mt-1 text-sm font-medium text-slate-800">{selectedItem.location || "—"}</div>
                                </div>
                                <div className="rounded-2xl bg-slate-50 p-4">
                                  <div className="text-xs text-slate-500">Зарплата</div>
                                  <div className="mt-1 text-sm font-medium text-slate-800">{selectedItem.salary || "—"}</div>
                                </div>
                                <div className="rounded-2xl bg-slate-50 p-4">
                                  <div className="text-xs text-slate-500">Опыт</div>
                                  <div className="mt-1 text-sm font-medium text-slate-800">{selectedItem.experience || "—"}</div>
                                </div>
                                <div className="rounded-2xl bg-slate-50 p-4">
                                  <div className="text-xs text-slate-500">Образование</div>
                                  <div className="mt-1 text-sm font-medium text-slate-800">{selectedItem.education || "—"}</div>
                                </div>
                              </div>

                              <div className="rounded-2xl border border-slate-200 p-4">
                                <div className="text-xs text-slate-500">Поисковый запрос</div>
                                <div className="mt-1 text-sm font-medium text-slate-800">{selectedItem.keyword_query || "—"}</div>
                              </div>

                              <div className="rounded-2xl border border-slate-200 p-4">
                                <div className="text-xs text-slate-500">Почему резюме приоритетное</div>
                                <div className="mt-1 text-sm font-medium text-slate-800">{selectedItem.priorityReason || "—"}</div>
                              </div>

                              <div className="rounded-2xl border border-slate-200 p-4">
                                <div className="text-xs text-slate-500">Дата публикации</div>
                                <div className="mt-1 text-sm font-medium text-slate-800">{selectedItem.published_at || "—"}</div>
                              </div>

                              {selectedItem.detail_url ? (
                                <Button asChild className="w-full rounded-2xl">
                                  <a href={selectedItem.detail_url} target="_blank" rel="noreferrer">
                                    <ExternalLink className="mr-2 h-4 w-4" />
                                    Открыть карточку резюме
                                  </a>
                                </Button>
                              ) : null}
                            </div>
                          ) : (
                            <div className="rounded-2xl border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">
                              Нет данных для отображения.
                            </div>
                          )}
                        </CardContent>
                      </Card>
                    </div>
                  </TabsContent>

                  <TabsContent value="detail" className="mt-0">
                    <Card className="rounded-3xl border-slate-200/70 shadow-sm">
                      <CardHeader>
                        <CardTitle>Детальная карточка</CardTitle>
                        <CardDescription>Удобный формат для просмотра одной записи без лишнего шума.</CardDescription>
                      </CardHeader>
                      <CardContent>
                        {selectedItem ? (
                          <div className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr] 2xl:grid-cols-[1.35fr_0.65fr]">
                            <div className="space-y-6">
                              <div className="rounded-3xl bg-gradient-to-r from-indigo-700 via-blue-700 to-cyan-600 p-6 text-white shadow-lg">
                                <div className="flex flex-wrap items-center gap-2">
                                  <Badge className="rounded-full bg-white/15 text-white hover:bg-white/15">{selectedItem.groupLabel}</Badge>
                                  <Badge className="rounded-full bg-white/15 text-white hover:bg-white/15">{selectedItem.keyword_query || "Без запроса"}</Badge>
                                  <Badge className="rounded-full bg-white/15 text-white hover:bg-white/15">{selectedItem.priorityLabelRu} · {selectedItem.relevanceScore}</Badge>
                                </div>
                                <h2 className="mt-4 text-3xl font-semibold">{selectedItem.title || "Без названия"}</h2>
                                <p className="mt-2 text-sm text-slate-300">{selectedItem.category || "Категория не указана"}</p>
                                <div className="mt-6 grid gap-3 sm:grid-cols-2">
                                  <div className="rounded-2xl bg-white/10 p-4"><div className="text-xs text-slate-300">Локация</div><div className="mt-1 text-sm font-medium">{selectedItem.location || "—"}</div></div>
                                  <div className="rounded-2xl bg-white/10 p-4"><div className="text-xs text-slate-300">Зарплата</div><div className="mt-1 text-sm font-medium">{selectedItem.salary || "—"}</div></div>
                                  <div className="rounded-2xl bg-white/10 p-4"><div className="text-xs text-slate-300">Опыт</div><div className="mt-1 text-sm font-medium">{selectedItem.experience || "—"}</div></div>
                                  <div className="rounded-2xl bg-white/10 p-4"><div className="text-xs text-slate-300">Образование</div><div className="mt-1 text-sm font-medium">{selectedItem.education || "—"}</div></div>
                                </div>
                              </div>

                              <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">
                                <div className="text-xs text-slate-500">Обоснование приоритета</div>
                                <div className="mt-1 font-medium text-slate-900">{selectedItem.priorityReason || "Мало релевантных совпадений"}</div>
                              </div>

                              <div className="grid gap-4 sm:grid-cols-2">
                                <Card className="rounded-3xl border-slate-200/70">
                                  <CardHeader className="pb-3"><CardTitle className="text-base">Позиция в выдаче</CardTitle></CardHeader>
                                  <CardContent className="text-sm text-slate-600">Найдена по запросу <span className="font-medium text-slate-900">{selectedItem.keyword_query || "—"}</span> на странице <span className="font-medium text-slate-900">{selectedItem.page_found || "—"}</span>.</CardContent>
                                </Card>
                                <Card className="rounded-3xl border-slate-200/70">
                                  <CardHeader className="pb-3"><CardTitle className="text-base">Дата</CardTitle></CardHeader>
                                  <CardContent className="text-sm text-slate-600">{selectedItem.published_at || "Дата не указана"}</CardContent>
                                </Card>
                              </div>
                            </div>

                            <div className="space-y-4">
                              <Card className="rounded-3xl border-slate-200/70">
                                <CardHeader>
                                  <CardTitle className="text-base">Быстрые действия</CardTitle>
                                </CardHeader>
                                <CardContent className="space-y-3">
                                  <Button
                                    onClick={exportFilteredExcel}
                                    className="w-full rounded-2xl bg-indigo-600 text-white hover:bg-indigo-700"
                                  >
                                    Экспорт текущей выборки в Excel
                                  </Button>
                                  <Button
                                    variant="outline"
                                    onClick={exportFilteredCsv}
                                    className="w-full rounded-2xl border-cyan-200 text-cyan-700 hover:bg-cyan-50"
                                  >
                                    Экспорт текущей выборки в CSV
                                  </Button>
                                  {selectedItem.detail_url ? (
                                    <Button
                                      asChild
                                      variant="outline"
                                      className="w-full rounded-2xl border-blue-200 text-blue-700 hover:bg-blue-50"
                                    >
                                      <a href={selectedItem.detail_url} target="_blank" rel="noreferrer">Открыть исходную карточку</a>
                                    </Button>
                                  ) : null}
                                </CardContent>
                              </Card>

                              <Card className="rounded-3xl border-slate-200/70">
                                <CardHeader>
                                  <CardTitle className="text-base">Что можно улучшить дальше</CardTitle>
                                </CardHeader>
                                <CardContent className="space-y-3 text-sm leading-6 text-slate-600">
                                  <p>1. Добавить сохранение пресетов ключевых слов для разных сценариев найма.</p>
                                  <p>2. Добавить сохранение заметок по кандидатам.</p>
                                  <p>3. Сделать сравнение зарплат по группам и свежести.</p>
                                  <p>4. Добавить экспорт live-лога запуска парсера в текстовый файл.</p>
                                </CardContent>
                              </Card>
                            </div>
                          </div>
                        ) : (
                          <div className="rounded-2xl border border-dashed border-slate-300 p-8 text-center text-sm text-slate-500">Нет выбранного резюме.</div>
                        )}
                      </CardContent>
                    </Card>
                  </TabsContent>
                </Tabs>
              </>
            )}
          </div>
        </div>
      </div>

      {isParserModalOpen ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4"
          onClick={() => setIsParserModalOpen(false)}
        >
          <div
            className="w-full max-w-5xl overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">Ручной запуск парсера</h3>
                <p className="mt-1 text-xs text-slate-500">
                  Настрой ключевые слова и количество страниц, затем запусти парсинг.
                </p>
              </div>
              <Button
                type="button"
                variant="ghost"
                className="rounded-full p-2"
                onClick={() => setIsParserModalOpen(false)}
                aria-label="Закрыть"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>

            <div className="max-h-[85vh] overflow-y-auto p-5">
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge className={`rounded-full border ${parserStatusClass}`}>{parserStatusLabel}</Badge>
                  <Badge variant="secondary" className="rounded-full">Групп: {parserKeywordStats.groupCount}</Badge>
                  <Badge variant="secondary" className="rounded-full">Ключевых слов: {parserKeywordStats.keywordCount}</Badge>
                  {parserJobId ? <Badge variant="outline" className="rounded-full">job: {parserJobId.slice(0, 8)}</Badge> : null}
                </div>

                <div className="space-y-2">
                  <Label>Страниц на каждый запрос</Label>
                  <Input
                    type="number"
                    min="1"
                    max="20"
                    value={parserPagesPerQuery}
                    onChange={(e) => setParserPagesPerQuery(e.target.value)}
                    placeholder="От 1 до 20"
                    className="rounded-2xl"
                  />
                </div>

                <div className="grid gap-4 lg:grid-cols-2">
                  {Object.entries(parserKeywordDrafts).map(([group, draft]) => (
                    <div key={group} className="space-y-2">
                      <Label>{GROUP_LABELS[group] || group}: ключевые слова (по одному на строку)</Label>
                      <Textarea
                        value={draft}
                        onChange={(e) => setParserKeywordDrafts((prev) => ({ ...prev, [group]: e.target.value }))}
                        className="min-h-[180px] rounded-2xl text-sm"
                        placeholder="Например: менеджер по продажам"
                      />
                    </div>
                  ))}
                </div>

                <div className="flex flex-wrap gap-2">
                  <Button
                    onClick={handleRunParserManually}
                    className="rounded-2xl bg-emerald-600 text-white hover:bg-emerald-700"
                    disabled={isRunningParser || isLoadingFromApi}
                  >
                    <RefreshCw className={`mr-2 h-4 w-4 ${isRunningParser ? "animate-spin" : ""}`} />
                    {isRunningParser ? "Парсер выполняется..." : "Запустить парсер с настройками"}
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    className="rounded-2xl border-blue-200 text-blue-700 hover:bg-blue-50"
                    onClick={restoreDefaultParserSettings}
                  >
                    Вернуть настройки по умолчанию
                  </Button>
                </div>

                <div className="rounded-2xl border border-blue-100 bg-blue-50/60 p-3">
                  <div className="mb-2 flex items-center justify-between text-sm">
                    <span className="font-medium text-blue-800">Прогресс выполнения</span>
                    <span className="font-semibold text-blue-900">{parserProgressValue}%</span>
                  </div>
                  <div className="h-3 overflow-hidden rounded-full bg-blue-100">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-blue-500 to-indigo-500 transition-all duration-300"
                      style={{ width: `${parserProgressValue}%` }}
                    />
                  </div>
                  <p className="mt-2 text-xs text-slate-500">
                    Процент обновляется после перезагрузки страницы.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

