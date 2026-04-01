import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  Upload,
  Search,
  Filter,
  Download,
  RefreshCw,
  MapPin,
  Briefcase,
  Wallet,
  GraduationCap,
  CalendarDays,
  FileJson,
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

const SAMPLE_DATA = [
  {
    keyword_group: "sales_manager",
    keyword_query: "менеджер по продажам",
    page_found: 1,
    title: "Менеджер по продажам",
    category: "Продажи и обслуживание клиентов",
    experience: "Опыт работы 1 год",
    location: "г. Алматы, Бостандыкский район",
    salary: "350 000 тг.",
    education: "высшее",
    published_at: "Опубликовано 01.04.2026",
    detail_url: "https://www.enbek.kz/ru/resume/menedzher-po-prodazham~1000001",
  },
  {
    keyword_group: "sales_manager",
    keyword_query: "специалист по продажам",
    page_found: 2,
    title: "Специалист по продажам",
    category: "Продажи и маркетинг",
    experience: "Без опыта работы",
    location: "г. Алматы, Ауэзовский район",
    salary: "280 000 тг.",
    education: "среднее специальное",
    published_at: "Опубликовано 31.03.2026",
    detail_url: "https://www.enbek.kz/ru/resume/spetsialist-po-prodazham~1000002",
  },
  {
    keyword_group: "call_center_operator",
    keyword_query: "оператор call-центра",
    page_found: 1,
    title: "Оператор call-центра",
    category: "Контакт-центр",
    experience: "Опыт работы 6 месяцев",
    location: "г. Алматы, Алмалинский район",
    salary: "250 000 тг.",
    education: "высшее",
    published_at: "Опубликовано 01.04.2026",
    detail_url: "https://www.enbek.kz/ru/resume/operator-call-centra~1000003",
  },
  {
    keyword_group: "call_center_operator",
    keyword_query: "оператор контакт-центра",
    page_found: 3,
    title: "Специалист контакт-центра",
    category: "Клиентский сервис",
    experience: "Без опыта работы",
    location: "г. Алматы, Медеуский район",
    salary: "220 000 тг.",
    education: "среднее",
    published_at: "Опубликовано 30.03.2026",
    detail_url: "https://www.enbek.kz/ru/resume/spetsialist-kontakt-centra~1000004",
  },
  {
    keyword_group: "sales_manager",
    keyword_query: "sales manager",
    page_found: 4,
    title: "Sales manager",
    category: "B2B продажи",
    experience: "Опыт работы 3 года",
    location: "г. Алматы, Наурызбайский район",
    salary: "500 000 тг.",
    education: "высшее",
    published_at: "Опубликовано 28.03.2026",
    detail_url: "https://www.enbek.kz/ru/resume/sales-manager~1000005",
  },
];

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
  if (active) return "border-white/20 bg-white/15 text-white";
  if (label === "high") return "border-emerald-200 bg-emerald-50 text-emerald-700";
  if (label === "medium") return "border-amber-200 bg-amber-50 text-amber-700";
  return "border-slate-200 bg-slate-100 text-slate-600";
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
          <div className="rounded-2xl bg-slate-100 p-3 text-slate-700">
            <Icon className="h-5 w-5" />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function EmptyState({ onUseSample }) {
  return (
    <Card className="rounded-3xl border-dashed border-slate-300 bg-white/80 shadow-sm">
      <CardContent className="flex flex-col items-center justify-center px-6 py-16 text-center">
        <div className="rounded-3xl bg-slate-100 p-4">
          <Database className="h-8 w-8 text-slate-700" />
        </div>
        <h3 className="mt-5 text-xl font-semibold text-slate-900">Загрузи JSON с результатами парсинга</h3>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">
          Подходит файл формата <span className="font-medium text-slate-700">enbek_almaty_resumes_fast.json</span>. После загрузки
          появятся фильтры, аналитика, таблица и карточка резюме.
        </p>
        <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
          <Badge className="rounded-full bg-slate-900 px-3 py-1 text-white hover:bg-slate-900">Алматы</Badge>
          <Badge variant="secondary" className="rounded-full px-3 py-1">Продажи</Badge>
          <Badge variant="secondary" className="rounded-full px-3 py-1">Call-центр</Badge>
          <Badge variant="secondary" className="rounded-full px-3 py-1">Поиск и анализ</Badge>
        </div>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Button onClick={onUseSample} className="rounded-2xl px-5">
            <Sparkles className="mr-2 h-4 w-4" />
            Загрузить демо-данные
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
          ? "border-slate-900 bg-slate-900 text-white shadow-lg"
          : "border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm"
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
  const fileInputRef = useRef(null);
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
  const [jsonDraft, setJsonDraft] = useState("");
  const [importError, setImportError] = useState("");
  const [isRunningParser, setIsRunningParser] = useState(false);
  const [isLoadingFromApi, setIsLoadingFromApi] = useState(false);
  const [lastRunMeta, setLastRunMeta] = useState(null);
  const [apiStatusMessage, setApiStatusMessage] = useState("");

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

  const handleRunParserManually = useCallback(async () => {
    if (!apiBaseUrl) {
      const errorText = "API URL не настроен.";
      setImportError(errorText);
      showToast?.(errorText, "error");
      return;
    }

    setIsRunningParser(true);
    setImportError("");
    setApiStatusMessage("");

    try {
      const response = await fetch(`${apiBaseUrl}/api/recruiting/run`, {
        method: "POST",
        headers: buildApiHeaders({ "Content-Type": "application/json" }),
        body: "{}",
      });

      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const fallback =
          response.status === 409
            ? "Парсер уже выполняется. Попробуй запустить чуть позже."
            : `Не удалось запустить парсер (${response.status}).`;
        throw new Error(payload?.message || payload?.error || fallback);
      }

      const latestRun = payload?.latest_run || payload?.result?.run || null;
      const runUuid = latestRun?.run_uuid || null;
      const resumesPayload = await loadResumesFromApi({ runUuid, silent: true });
      const totalCount = Number(resumesPayload?.total ?? resumesPayload?.items?.length ?? 0);

      setLastRunMeta(latestRun);
      setApiStatusMessage(
        totalCount > 0
          ? `Парсер завершен. Обновлено ${totalCount} резюме.`
          : "Парсер завершен, но резюме не найдены."
      );
      showToast?.("Парсер успешно запущен вручную.", "success");
    } catch (error) {
      const errorText = error?.message || "Ошибка ручного запуска парсера.";
      setImportError(errorText);
      showToast?.(errorText, "error");
    } finally {
      setIsRunningParser(false);
    }
  }, [apiBaseUrl, buildApiHeaders, loadResumesFromApi, showToast]);

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

  const handleUseSample = () => {
    setRawItems(SAMPLE_DATA.map((item, index) => ({ ...item, __id: `sample-${index}` })));
    setImportError("");
  };

  const handleFileUpload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;

    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      if (!Array.isArray(parsed)) {
        throw new Error("JSON должен содержать массив объектов.");
      }
      setRawItems(parsed.map((item, index) => ({ ...item, __id: item.__id || `file-${index}` })));
      setImportError("");
    } catch (error) {
      setImportError(error.message || "Не удалось прочитать JSON файл.");
    } finally {
      event.target.value = "";
    }
  };

  const handlePasteImport = () => {
    try {
      const parsed = JSON.parse(jsonDraft);
      if (!Array.isArray(parsed)) {
        throw new Error("JSON должен содержать массив объектов.");
      }
      setRawItems(parsed.map((item, index) => ({ ...item, __id: item.__id || `paste-${index}` })));
      setImportError("");
      setJsonDraft("");
    } catch (error) {
      setImportError(error.message || "Неверный JSON.");
    }
  };

  const resetFilters = () => {
    setSearch("");
    setGroupFilter("all");
    setPriorityFilter("medium_plus");
    setSortBy("priority_desc");
    setOnlyWithSalary(false);
    setOnlyFresh(false);
    setMinSalary("");
  };

  const exportFilteredJson = () => {
    downloadTextFile("enbek_filtered_resumes.json", JSON.stringify(filteredItems, null, 2));
  };

  const exportFilteredCsv = () => {
    downloadTextFile("enbek_filtered_resumes.csv", toCsv(filteredItems), "text/csv;charset=utf-8");
  };

  return (
    <div className="min-h-screen bg-gradient-to-b from-slate-50 via-white to-slate-100 text-slate-900">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="mb-6"
        >
          <div className="rounded-3xl border border-slate-200/70 bg-white/85 p-6 shadow-sm backdrop-blur">
            <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <div className="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
                  <Sparkles className="h-3.5 w-3.5" />
                  Панель анализа резюме Enbek
                </div>
                <h1 className="mt-3 text-3xl font-semibold tracking-tight text-slate-950">Удобный просмотр и анализ результатов парсинга</h1>
                <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-500">
                  Загружай JSON, фильтруй по ролям, зарплате и свежести, просматривай карточки кандидатов и быстро выгружай
                  очищенную выборку для дальнейшей обработки.
                </p>
              </div>

              <div className="flex flex-wrap gap-3">
                <input ref={fileInputRef} type="file" accept="application/json" className="hidden" onChange={handleFileUpload} />
                <Button variant="outline" className="rounded-2xl" onClick={() => fileInputRef.current?.click()}>
                  <Upload className="mr-2 h-4 w-4" />
                  Загрузить JSON
                </Button>
                <Button variant="outline" className="rounded-2xl" onClick={handleUseSample}>
                  <FileJson className="mr-2 h-4 w-4" />
                  Демо-данные
                </Button>
                <Button
                  variant="outline"
                  className="rounded-2xl"
                  onClick={handleRefreshFromApi}
                  disabled={isLoadingFromApi || isRunningParser}
                >
                  <RefreshCw className={`mr-2 h-4 w-4 ${isLoadingFromApi ? "animate-spin" : ""}`} />
                  Обновить из API
                </Button>
                <Button
                  variant="outline"
                  className="rounded-2xl"
                  onClick={handleRunParserManually}
                  disabled={isRunningParser || isLoadingFromApi}
                >
                  <RefreshCw className={`mr-2 h-4 w-4 ${isRunningParser ? "animate-spin" : ""}`} />
                  {isRunningParser ? "Парсер выполняется..." : "Запустить парсер"}
                </Button>
                <Button className="rounded-2xl" onClick={exportFilteredJson} disabled={!filteredItems.length}>
                  <Download className="mr-2 h-4 w-4" />
                  Export JSON
                </Button>
              </div>
            </div>
            {(apiStatusMessage || lastRunMeta) ? (
              <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
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

        <div className="grid gap-6 xl:grid-cols-[360px_minmax(0,1fr)]">
          <div className="space-y-6">
            <Card className="rounded-3xl border-slate-200/70 shadow-sm">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-lg"><SlidersHorizontal className="h-5 w-5" /> Фильтры и импорт</CardTitle>
                <CardDescription>Поддерживает JSON-массив объектов из твоего парсера.</CardDescription>
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

                <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1">
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
                    className="rounded-2xl"
                    onClick={() => setOnlyWithSalary((v) => !v)}
                  >
                    <Wallet className="mr-2 h-4 w-4" />
                    Только с зарплатой
                  </Button>
                  <Button
                    type="button"
                    variant={onlyFresh ? "default" : "outline"}
                    className="rounded-2xl"
                    onClick={() => setOnlyFresh((v) => !v)}
                  >
                    <Clock3 className="mr-2 h-4 w-4" />
                    Свежие за 3 дня
                  </Button>
                  <Button type="button" variant="ghost" className="rounded-2xl" onClick={resetFilters}>
                    <RefreshCw className="mr-2 h-4 w-4" />
                    Сбросить
                  </Button>
                </div>

                <Separator />

                <div className="space-y-2">
                  <Label>Импорт из вставленного JSON</Label>
                  <Textarea
                    value={jsonDraft}
                    onChange={(e) => setJsonDraft(e.target.value)}
                    className="min-h-[140px] rounded-2xl"
                    placeholder='[{"title":"Менеджер по продажам", ...}]'
                  />
                  <div className="flex gap-2">
                    <Button onClick={handlePasteImport} className="rounded-2xl">Импортировать</Button>
                    <Button variant="outline" onClick={exportFilteredCsv} disabled={!filteredItems.length} className="rounded-2xl">
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

            <Card className="rounded-3xl border-slate-200/70 shadow-sm">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-lg"><BarChart3 className="h-5 w-5" /> Краткая сводка</CardTitle>
                <CardDescription>Срез по текущим фильтрам.</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1">
                <StatCard title="Резюме" value={stats.total} hint="После применения фильтров" icon={Users} />
                <StatCard title="Средняя зарплата" value={formatMoney(stats.avgSalary)} hint="Среди записей, где зарплата указана" icon={Wallet} />
                <StatCard title="Свежие" value={stats.freshCount} hint="Опубликовано примерно за последние 3 дня" icon={CalendarDays} />
                <StatCard title="Приоритетные" value={stats.highPriorityCount} hint="Резюме с высоким score релевантности" icon={Filter} />
                <StatCard title="Средний score" value={stats.avgRelevance} hint="Средняя релевантность текущей выборки" icon={Sparkles} />
              </CardContent>
            </Card>
          </div>

          <div className="space-y-6">
            {!hydratedItems.length ? (
              <EmptyState onUseSample={handleUseSample} />
            ) : (
              <>
                <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
                  <Card className="rounded-3xl border-slate-200/70 shadow-sm">
                    <CardHeader>
                      <CardTitle>Распределение по группам</CardTitle>
                      <CardDescription>Что преобладает в текущей выборке.</CardDescription>
                    </CardHeader>
                    <CardContent className="h-[280px]">
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
                    <CardContent className="h-[280px]">
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
                  <CardContent className="h-[260px]">
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

                <Tabs defaultValue="list" className="space-y-6">
                  <TabsList className="grid w-full grid-cols-2 rounded-2xl bg-slate-100 p-1">
                    <TabsTrigger value="list" className="rounded-2xl">Список резюме</TabsTrigger>
                    <TabsTrigger value="detail" className="rounded-2xl">Карточка резюме</TabsTrigger>
                  </TabsList>

                  <TabsContent value="list" className="mt-0">
                    <div className="grid gap-6 xl:grid-cols-[1.1fr_0.9fr]">
                      <Card className="rounded-3xl border-slate-200/70 shadow-sm">
                        <CardHeader>
                          <CardTitle>Найденные резюме</CardTitle>
                          <CardDescription>{filteredItems.length} записей после фильтрации.</CardDescription>
                        </CardHeader>
                        <CardContent>
                          <ScrollArea className="h-[720px] pr-3">
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
                                  <Badge className="rounded-full bg-slate-900 text-white hover:bg-slate-900">{selectedItem.groupLabel}</Badge>
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
                          <div className="grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">
                            <div className="space-y-6">
                              <div className="rounded-3xl bg-slate-950 p-6 text-white shadow-lg">
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
                                  <Button onClick={exportFilteredJson} className="w-full rounded-2xl">Экспорт текущей выборки в JSON</Button>
                                  <Button variant="outline" onClick={exportFilteredCsv} className="w-full rounded-2xl">Экспорт текущей выборки в CSV</Button>
                                  {selectedItem.detail_url ? (
                                    <Button asChild variant="outline" className="w-full rounded-2xl">
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
                                  <p>1. Подключить прямой запуск Python-парсера с backend API.</p>
                                  <p>2. Добавить сохранение заметок по кандидатам.</p>
                                  <p>3. Сделать сравнение зарплат по группам и свежести.</p>
                                  <p>4. Добавить автозагрузку файла результатов после каждого запуска.</p>
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
    </div>
  );
}
