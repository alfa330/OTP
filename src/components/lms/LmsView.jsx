import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { matchPath, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import ReactQuill from "react-quill";
import DOMPurify from "dompurify";
import {
  BookOpen, Play, CheckCircle, Clock, Award, Bell, Search, ChevronRight,
  ChevronDown, BarChart2, Plus, Minus, Trash2, Edit, Settings, Lock, Star, Download,
  X, Check, AlertCircle, ArrowLeft, Video, FileText, HelpCircle, Upload,
  Users, TrendingUp, Target, GripVertical, Filter, Calendar,
  PlayCircle, AlignLeft, Layers, ChevronLeft, Eye,
  BookMarked, Zap, ToggleLeft, ToggleRight, LayoutGrid, List, Percent,
  UserCheck, RefreshCw, ClipboardList, PlusCircle, LogOut, ChevronUp,
  Save, Image, Link2, FileCheck, Volume2, Maximize, AlertTriangle, Rocket, Archive, RotateCcw,
  XCircle, CheckSquare, Square, Type, ToggleRight as RadioIcon, CalendarDays
} from "lucide-react";
import "react-quill/dist/quill.snow.css";
import "./LmsRichText.css";

const SkeletonBlock = ({ className = "", delay }) => {
  const normalizedClassName = String(className || "").trim();
  const hasRoundedToken = /(^|\s)rounded(?:-[^\s]+)?(?=\s|$)/.test(normalizedClassName);
  const softenedRoundedClassName = normalizedClassName.replace(
    /\brounded-(md|lg|xl)\b/g,
    (_, size) => {
      if (size === "md") return "rounded-lg";
      if (size === "lg") return "rounded-xl";
      return "rounded-2xl";
    }
  );
  const randomDelayRef = useRef(Math.floor(Math.random() * 220));
  const delayValue = Number(delay);
  const resolvedDelay = Number.isFinite(delayValue) ? Math.max(0, delayValue) : randomDelayRef.current;
  const radiusClass = hasRoundedToken ? "" : "rounded-2xl";

  return (
    <span
      aria-hidden="true"
      className={`lms-skeleton-block block ${radiusClass} ${softenedRoundedClassName}`.trim()}
      style={{ "--lms-skeleton-delay": `${resolvedDelay}ms` }}
    />
  );
};

// ─── MOCK DATA ────────────────────────────────────────────────────────────────

const COURSES = [
  {
    id: 1, title: "Корпоративная безопасность и защита данных",
    category: "Безопасность", cover: "🔐", color: "from-blue-600 to-indigo-700",
    description: "Обучение сотрудников основам защиты корпоративных данных, работе с конфиденциальной информацией и предотвращением утечек.",
    skills: ["Защита данных", "GDPR", "Управление рисками", "Кибербезопасность"],
    duration: "8 ч", lessons: 12, modules: 3, progress: 65,
    deadline: "2025-04-15", mandatory: true, status: "in_progress",
    rating: 4.8, reviews: 142, passingScore: 80, maxAttempts: 3, attemptsUsed: 1,
    modules_data: [
      { id: 1, title: "Основы информационной безопасности", lessons: [
        { id: 1, title: "Введение в ИБ", type: "video", duration: "18 мин", status: "completed", locked: false },
        { id: 2, title: "Классификация данных", type: "video", duration: "22 мин", status: "completed", locked: false },
        { id: 3, title: "Угрозы и уязвимости", type: "video", duration: "31 мин", status: "in_progress", locked: false },
        { id: 4, title: "Тест по модулю 1", type: "quiz", duration: "20 мин", status: "locked", locked: true, requiresTest: true },
      ]},
      { id: 2, title: "Практика защиты данных", lessons: [
        { id: 5, title: "Шифрование данных", type: "video", duration: "25 мин", status: "locked", locked: true },
        { id: 6, title: "Парольная политика", type: "text", duration: "15 мин", status: "locked", locked: true },
        { id: 7, title: "Тест по модулю 2", type: "quiz", duration: "15 мин", status: "locked", locked: true, requiresTest: true },
      ]},
      { id: 3, title: "Регуляторные требования", lessons: [
        { id: 8, title: "Основы GDPR", type: "video", duration: "40 мин", status: "locked", locked: true },
        { id: 9, title: "Внутренние политики", type: "text", duration: "20 мин", status: "locked", locked: true },
        { id: 10, title: "Итоговый тест", type: "quiz", duration: "30 мин", status: "locked", locked: true, requiresTest: true },
      ]},
    ],
  },
  {
    id: 2, title: "Управление проектами по методологии Agile",
    category: "Менеджмент", cover: "🚀", color: "from-emerald-500 to-teal-600",
    description: "Освоение гибких методологий управления проектами: Scrum, Kanban и гибридных подходов.",
    skills: ["Scrum", "Kanban", "Agile", "Ретроспективы", "Планирование спринтов"],
    duration: "12 ч", lessons: 18, modules: 4, progress: 100,
    deadline: "2025-03-01", mandatory: false, status: "completed",
    rating: 4.9, reviews: 267, passingScore: 80, maxAttempts: 2, attemptsUsed: 1,
    modules_data: [],
  },
  {
    id: 3, title: "Эффективные коммуникации в команде",
    category: "Soft Skills", cover: "💬", color: "from-violet-500 to-purple-700",
    description: "Развитие навыков деловой коммуникации, проведения встреч и работы с обратной связью.",
    skills: ["Переговоры", "Деловая переписка", "Публичные выступления"],
    duration: "6 ч", lessons: 9, modules: 2, progress: 0,
    deadline: "2025-05-01", mandatory: false, status: "not_started",
    rating: 4.7, reviews: 89, passingScore: 75, maxAttempts: 3, attemptsUsed: 0,
    modules_data: [],
  },
  {
    id: 4, title: "Финансовая грамотность для менеджеров",
    category: "Финансы", cover: "📊", color: "from-amber-500 to-orange-600",
    description: "Базовые финансовые инструменты: P&L, EBITDA, бюджетирование, анализ отчётности.",
    skills: ["Финансовый анализ", "Бюджетирование", "KPI", "ROI"],
    duration: "10 ч", lessons: 15, modules: 3, progress: 30,
    deadline: "2025-03-28", mandatory: true, status: "overdue",
    rating: 4.6, reviews: 54, passingScore: 80, maxAttempts: 2, attemptsUsed: 2,
    modules_data: [],
  },
  {
    id: 5, title: "Введение в Data Driven решения",
    category: "Аналитика", cover: "📈", color: "from-cyan-500 to-blue-600",
    description: "Как принимать управленческие решения на основе данных, работа с дашбордами и метриками.",
    skills: ["Аналитика данных", "BI-инструменты", "A/B тесты"],
    duration: "9 ч", lessons: 14, modules: 3, progress: 0,
    deadline: "2025-06-01", mandatory: false, status: "not_started",
    rating: 4.8, reviews: 38, passingScore: 80, maxAttempts: 3, attemptsUsed: 0,
    modules_data: [],
  },
  {
    id: 6, title: "Охрана труда и промышленная безопасность",
    category: "Обязательное", cover: "⛑️", color: "from-rose-500 to-red-700",
    description: "Обязательный курс по технике безопасности, пожарной охране и охране труда.",
    skills: ["Охрана труда", "Пожарная безопасность", "Первая помощь"],
    duration: "4 ч", lessons: 7, modules: 2, progress: 100,
    deadline: "2024-12-31", mandatory: true, status: "completed",
    rating: 4.5, reviews: 312, passingScore: 90, maxAttempts: 5, attemptsUsed: 1,
    modules_data: [],
  },
];

// Расширенный банк вопросов с 4 типами
const QUIZ_QUESTIONS = [
  {
    id: 1, type: "single",
    text: "Какое из следующих действий является нарушением политики информационной безопасности?",
    options: [
      "Использование корпоративного VPN при работе из дома",
      "Передача рабочего ноутбука коллеге без уведомления ИТ-отдела",
      "Использование двухфакторной аутентификации",
      "Регулярная смена пароля согласно политике",
    ], correct: 1, explanation: "Передача оборудования без уведомления ИТ нарушает политику учёта активов и цепочку ответственности.",
  },
  {
    id: 2, type: "single",
    text: "Согласно GDPR, в течение какого срока организация обязана уведомить регулятора об утечке персональных данных?",
    options: ["24 часов", "48 часов", "72 часов", "7 рабочих дней"],
    correct: 2, explanation: "Статья 33 GDPR обязывает уведомить надзорный орган не позднее 72 часов после обнаружения нарушения.",
  },
  {
    id: 3, type: "multiple",
    text: "Какие из перечисленных мер относятся к базовым требованиям парольной политики? (выберите все верные)",
    options: [
      "Минимальная длина пароля 8 символов",
      "Использование имени пользователя в пароле",
      "Наличие спецсимволов и цифр",
      "Смена пароля не реже раза в 90 дней",
    ], correct: [0, 2, 3], explanation: "Имя пользователя в пароле снижает его стойкость — это нарушение парольной политики.",
  },
  {
    id: 4, type: "bool",
    text: "Открытые Wi-Fi сети в кафе и аэропортах безопасны для передачи корпоративных данных при использовании HTTPS.",
    options: ["Верно", "Неверно"], correct: 1,
    explanation: "Даже HTTPS не защищает от атак типа SSL Strip или поддельных точек доступа (Evil Twin). Требуется корпоративный VPN.",
  },
  {
    id: 5, type: "single",
    text: "Какой класс конфиденциальности присваивается данным, предназначенным исключительно для внутреннего использования?",
    options: ["Публичный", "Внутренний", "Конфиденциальный", "Строго секретный"],
    correct: 1, explanation: "Класс «Внутренний» означает данные, доступные только сотрудникам, но не требующие специальной защиты.",
  },
  {
    id: 6, type: "text",
    text: "Как называется принцип, при котором пользователю предоставляются только те права, которые необходимы для выполнения его обязанностей?",
    options: [], correct: "least privilege",
    keywords: ["least privilege", "минимальных привилегий", "наименьших привилегий", "минимальные привилегии"],
    explanation: "Принцип минимальных привилегий (Least Privilege) — фундаментальный принцип информационной безопасности.",
  },
];

const EMPLOYEES = [
  { id: 1, name: "Иванова Анна С.", dept: "HR", courses: 5, completed: 4, avgScore: 91, overdue: 0, lastActive: "Сегодня", testTime: "1ч 12м", attempts: 6 },
  { id: 2, name: "Петров Дмитрий К.", dept: "Разработка", courses: 6, completed: 3, avgScore: 84, overdue: 1, lastActive: "Вчера", testTime: "2ч 45м", attempts: 9 },
  { id: 3, name: "Сидорова Мария О.", dept: "Финансы", courses: 4, completed: 4, avgScore: 95, overdue: 0, lastActive: "Сегодня", testTime: "0ч 58м", attempts: 4 },
  { id: 4, name: "Козлов Антон В.", dept: "Продажи", courses: 5, completed: 2, avgScore: 72, overdue: 2, lastActive: "3 дня назад", testTime: "3ч 20м", attempts: 14 },
  { id: 5, name: "Новиков Сергей Ю.", dept: "Маркетинг", courses: 4, completed: 3, avgScore: 88, overdue: 0, lastActive: "Вчера", testTime: "1ч 35м", attempts: 7 },
  { id: 6, name: "Соколова Елена Р.", dept: "Юридический", courses: 6, completed: 5, avgScore: 97, overdue: 0, lastActive: "Сегодня", testTime: "1ч 05м", attempts: 5 },
  { id: 7, name: "Лебедев Игорь А.", dept: "Операции", courses: 5, completed: 1, avgScore: 68, overdue: 3, lastActive: "7 дней назад", testTime: "4ч 10м", attempts: 18 },
];

// Статистика ошибок по вопросам для аналитики
const QUESTION_FAIL_STATS = [
  { questionId: 4, text: "Открытые Wi-Fi и HTTPS...", failRate: 68, course: "Корп. безопасность" },
  { questionId: 3, text: "Требования парольной политики...", failRate: 52, course: "Корп. безопасность" },
  { questionId: 6, text: "Принцип минимальных привилегий...", failRate: 47, course: "Корп. безопасность" },
  { questionId: 2, text: "Срок уведомления по GDPR...", failRate: 41, course: "Корп. безопасность" },
];

const NOTIFICATIONS = [
  { id: 1, type: "deadline", title: "Приближается дедлайн", message: "Курс «Финансовая грамотность» необходимо завершить до 28 марта", time: "2 ч назад", read: false },
  { id: 2, type: "completed", title: "Курс завершён", message: "Вы успешно прошли курс «Управление проектами по методологии Agile»", time: "3 дня назад", read: false },
  { id: 3, type: "assigned", title: "Назначен новый курс", message: "Вам назначен обязательный курс «Охрана труда»", time: "1 нед назад", read: true },
  { id: 4, type: "certificate", title: "Сертификат доступен", message: "Сертификат по курсу «Agile» готов к скачиванию", time: "3 дня назад", read: true },
];

const CERTIFICATES = [
  { id: 1, course: "Управление проектами по методологии Agile", hours: 12, date: "01.03.2025", number: "LMS-2025-00142", color: "from-emerald-500 to-teal-600", employee: "Иванов Алексей Иванович" },
  { id: 2, course: "Охрана труда и промышленная безопасность", hours: 4, date: "31.12.2024", number: "LMS-2024-00891", color: "from-rose-500 to-red-600", employee: "Иванов Алексей Иванович" },
];

// ─── HELPERS ──────────────────────────────────────────────────────────────────

const RICH_TEXT_TOOLBAR = [
  [{ header: [2, 3, false] }],
  [{ align: ["", "center", "right"] }],
  ["bold", "italic", "underline", "strike"],
  [{ list: "ordered" }, { list: "bullet" }],
  ["blockquote", "link", "image", "attachment"],
  ["clean"],
];

const RICH_TEXT_FORMATS = [
  "header",
  "align",
  "bold",
  "italic",
  "underline",
  "strike",
  "list",
  "bullet",
  "blockquote",
  "link",
  "image",
  "attachment",
  "width",
];

const RICH_TEXT_SANITIZE_OPTIONS = {
  ALLOWED_TAGS: [
    "p", "br",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "b", "em", "i", "u", "s",
    "ul", "ol", "li",
    "blockquote",
    "a",
    "img",
    "div",
    "span",
  ],
  ALLOWED_ATTR: [
    "href", "rel", "class",
    "data-list", "data-checked",
    "data-url", "data-name", "data-mime", "data-size", "data-embed-id",
    "src", "alt", "title", "download", "width",
  ],
};

const Quill = ReactQuill.Quill;
const BlockEmbed = Quill.import("blots/block/embed");

const createRichEmbedId = () => `lms-${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36).slice(-4)}`;

class AttachmentBlot extends BlockEmbed {
  static create(value) {
    const node = super.create();
    const payload = value && typeof value === "object" ? value : { url: String(value || "") };
    const url = String(payload.url || payload.href || "").trim();
    const name = String(payload.name || payload.fileName || "Файл").trim() || "Файл";
    const mime = String(payload.mimeType || payload.mime || "").trim();
    const size = Number(payload.size || 0) || 0;

    node.setAttribute("contenteditable", "false");
    node.setAttribute("draggable", "true");
    node.setAttribute("data-embed-id", createRichEmbedId());
    node.setAttribute("data-url", url);
    node.setAttribute("data-name", name);
    if (mime) node.setAttribute("data-mime", mime);
    if (size > 0) node.setAttribute("data-size", String(size));

    const link = document.createElement("a");
    link.className = "lms-file-link";
    link.href = url || "#";
    link.setAttribute("rel", "noopener noreferrer");
    link.setAttribute("download", name);

    const icon = document.createElement("span");
    icon.className = "lms-file-icon";
    icon.setAttribute("aria-hidden", "true");

    const title = document.createElement("span");
    title.className = "lms-file-title";
    title.textContent = name;

    const subtitle = document.createElement("span");
    subtitle.className = "lms-file-subtitle";
    subtitle.textContent = "Нажмите, чтобы скачать";

    const content = document.createElement("span");
    content.className = "lms-file-content";
    content.appendChild(title);
    content.appendChild(subtitle);

    const action = document.createElement("span");
    action.className = "lms-file-download";
    action.textContent = "Скачать";

    link.appendChild(icon);
    link.appendChild(content);
    link.appendChild(action);
    node.appendChild(link);
    return node;
  }

  static value(node) {
    return {
      url: String(node?.getAttribute("data-url") || "").trim(),
      name: String(node?.getAttribute("data-name") || "Файл").trim() || "Файл",
      mimeType: String(node?.getAttribute("data-mime") || "").trim(),
      size: Number(node?.getAttribute("data-size") || 0) || 0,
    };
  }
}

AttachmentBlot.blotName = "attachment";
AttachmentBlot.tagName = "div";
AttachmentBlot.className = "lms-file-embed";

if (!Quill.imports["formats/attachment"]) {
  Quill.register(AttachmentBlot);
}

const stripHtmlToText = (value) => String(value || "")
  .replace(/<style[\s\S]*?<\/style>/gi, " ")
  .replace(/<script[\s\S]*?<\/script>/gi, " ")
  .replace(/<[^>]+>/g, " ")
  .replace(/&nbsp;/gi, " ")
  .replace(/&#160;/gi, " ")
  .replace(/\s+/g, " ")
  .trim();

const normalizeRichTextValue = (value) => {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const plain = stripHtmlToText(raw);
  return plain ? raw : "";
};

const escapeHtml = (value) => String(value || "")
  .replace(/&/g, "&amp;")
  .replace(/</g, "&lt;")
  .replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;")
  .replace(/'/g, "&#039;");

const prepareRichTextValue = (value) => {
  const raw = String(value || "");
  if (!raw.trim()) return "";
  if (/<\/?[a-z][\s\S]*>/i.test(raw)) return raw;
  return escapeHtml(raw).replace(/\n/g, "<br/>");
};

const sanitizeRichHtml = (value) => {
  const prepared = prepareRichTextValue(value);
  if (!prepared) return "";
  return DOMPurify.sanitize(prepared, RICH_TEXT_SANITIZE_OPTIONS);
};

function RichTextEditor({
  value,
  onChange,
  placeholder = "",
  minHeight = 140,
  onImageUpload = null,
  onFileUpload = null,
}) {
  const editorShellRef = useRef(null);
  const quillRef = useRef(null);
  const dragEmbedRef = useRef(null);
  const dragOverMetaRef = useRef(null);
  const [selectedEmbed, setSelectedEmbed] = useState(null);
  const [imageControlsPos, setImageControlsPos] = useState(null);
  const [dropIndicator, setDropIndicator] = useState(null);

  const getEditor = useCallback(() => quillRef.current?.getEditor?.() || null, []);

  const clearSelectedEmbedStyles = useCallback((root) => {
    if (!root) return;
    root.querySelectorAll(".lms-embed-selected").forEach((node) => node.classList.remove("lms-embed-selected"));
  }, []);

  const ensureEmbedAttrs = useCallback((root) => {
    if (!root) return;
    root.querySelectorAll("img").forEach((img) => {
      if (!img.getAttribute("data-embed-id")) {
        img.setAttribute("data-embed-id", createRichEmbedId());
      }
      img.setAttribute("draggable", "true");
      img.classList.add("lms-image-embed");
    });
    root.querySelectorAll(".lms-file-embed").forEach((node) => {
      if (!node.getAttribute("data-embed-id")) {
        node.setAttribute("data-embed-id", createRichEmbedId());
      }
      node.setAttribute("draggable", "true");
    });
  }, []);

  const clearSelectedEmbed = useCallback(() => {
    const quill = getEditor();
    if (quill) clearSelectedEmbedStyles(quill.root);
    setImageControlsPos(null);
    setSelectedEmbed(null);
  }, [getEditor, clearSelectedEmbedStyles]);

  const resolveSelectedNode = useCallback(() => {
    if (!selectedEmbed) return null;
    const quill = getEditor();
    if (!quill) return null;
    const root = quill.root;
    if (selectedEmbed.embedId) {
      const byId = root.querySelector(`[data-embed-id="${selectedEmbed.embedId}"]`);
      if (byId) return byId;
    }
    if (selectedEmbed.index != null) {
      const [leaf] = quill.getLeaf(selectedEmbed.index);
      if (leaf?.domNode instanceof HTMLElement) {
        return leaf.domNode;
      }
    }
    return null;
  }, [selectedEmbed, getEditor]);

  const getImageControlsPosition = useCallback((node) => {
    if (!(node instanceof HTMLImageElement)) return null;
    const shell = editorShellRef.current;
    if (!shell) return null;
    const shellRect = shell.getBoundingClientRect();
    const imageRect = node.getBoundingClientRect();
    return {
      top: Math.max(8, Math.round(imageRect.top - shellRect.top + 8)),
      left: Math.max(8, Math.round(imageRect.right - shellRect.left - 8)),
    };
  }, []);

  const syncImageControlsPosition = useCallback(() => {
    if (!selectedEmbed || selectedEmbed.type !== "image") {
      setImageControlsPos((prev) => (prev ? null : prev));
      return;
    }
    const node = resolveSelectedNode();
    const next = getImageControlsPosition(node);
    setImageControlsPos((prev) => {
      if (!next) return prev ? null : prev;
      if (prev && prev.top === next.top && prev.left === next.left) return prev;
      return next;
    });
  }, [selectedEmbed, resolveSelectedNode, getImageControlsPosition]);

  const selectEmbedNode = useCallback((node, type) => {
    const quill = getEditor();
    if (!quill || !node) return;
    const root = quill.root;
    ensureEmbedAttrs(root);
    clearSelectedEmbedStyles(root);
    node.classList.add("lms-embed-selected");
    const blot = Quill.find(node);
    const index = blot ? quill.getIndex(blot) : null;
    const embedId = String(node.getAttribute("data-embed-id") || createRichEmbedId());
    node.setAttribute("data-embed-id", embedId);
    const nextState = {
      type,
      embedId,
      index: Number.isFinite(Number(index)) ? Number(index) : null,
      width: null,
    };
    if (type === "image") {
      const width = Math.max(1, Number(node.getAttribute("width") || Math.round(node.getBoundingClientRect().width || 0) || 0));
      nextState.width = width;
      const nextPos = getImageControlsPosition(node);
      setImageControlsPos((prev) => {
        if (!nextPos) return prev ? null : prev;
        if (prev && prev.top === nextPos.top && prev.left === nextPos.left) return prev;
        return nextPos;
      });
    } else {
      setImageControlsPos((prev) => (prev ? null : prev));
    }
    setSelectedEmbed(nextState);
  }, [getEditor, ensureEmbedAttrs, clearSelectedEmbedStyles, getImageControlsPosition]);

  const removeSelectedEmbed = useCallback(() => {
    const quill = getEditor();
    if (!quill || !selectedEmbed) return;
    const node = resolveSelectedNode();
    if (!(node instanceof HTMLElement)) {
      clearSelectedEmbed();
      return;
    }
    const blot = Quill.find(node);
    if (!blot) {
      clearSelectedEmbed();
      return;
    }
    const index = quill.getIndex(blot);
    quill.deleteText(index, 1, "user");
    clearSelectedEmbed();
  }, [getEditor, selectedEmbed, resolveSelectedNode, clearSelectedEmbed]);

  const resizeSelectedImage = useCallback((deltaPx) => {
    if (!selectedEmbed || selectedEmbed.type !== "image") return;
    const quill = getEditor();
    if (!quill) return;
    const node = resolveSelectedNode();
    if (!(node instanceof HTMLImageElement)) return;
    const editorWidth = Math.max(280, Math.round(quill.root.getBoundingClientRect().width || 0));
    const minWidth = 120;
    const maxWidth = Math.max(minWidth, editorWidth - 32);
    const currentWidth = Math.max(1, Number(node.getAttribute("width") || Math.round(node.getBoundingClientRect().width || 0) || minWidth));
    const nextWidth = Math.max(minWidth, Math.min(maxWidth, currentWidth + deltaPx));
    const blot = Quill.find(node);
    if (blot && typeof blot.format === "function") {
      blot.format("width", String(Math.round(nextWidth)));
      quill.update("user");
    } else {
      node.setAttribute("width", String(Math.round(nextWidth)));
      quill.update("user");
    }
    setSelectedEmbed((prev) => (prev ? { ...prev, width: Math.round(nextWidth) } : prev));
    requestAnimationFrame(() => syncImageControlsPosition());
  }, [selectedEmbed, getEditor, resolveSelectedNode, syncImageControlsPosition]);

  const handleImageInsert = useCallback(() => {
    if (typeof onImageUpload !== "function") return;
    const quill = getEditor();
    if (!quill) return;

    const input = document.createElement("input");
    input.setAttribute("type", "file");
    input.setAttribute("accept", "image/*");
    input.click();

    input.onchange = async () => {
      try {
        const file = input.files?.[0];
        if (!file) return;
        const imageUrl = String(await onImageUpload(file)).trim();
        if (!imageUrl) return;
        const range = quill.getSelection(true);
        const index = range?.index ?? quill.getLength();
        quill.insertEmbed(index, "image", imageUrl, "user");
        const [leaf] = quill.getLeaf(index);
        if (leaf?.domNode instanceof HTMLImageElement) {
          ensureEmbedAttrs(quill.root);
          selectEmbedNode(leaf.domNode, "image");
        }
        quill.setSelection(index + 1, 0, "user");
      } catch (_) {
        // Ошибка уже обработана в upload callback
      }
    };
  }, [onImageUpload, getEditor, ensureEmbedAttrs, selectEmbedNode]);

  const handleAttachmentInsert = useCallback(() => {
    if (typeof onFileUpload !== "function") return;
    const quill = getEditor();
    if (!quill) return;
    const input = document.createElement("input");
    input.setAttribute("type", "file");
    input.setAttribute("accept", "*/*");
    input.click();
    input.onchange = async () => {
      try {
        const file = input.files?.[0];
        if (!file) return;
        const uploaded = await onFileUpload(file);
        const payload = uploaded && typeof uploaded === "object"
          ? uploaded
          : { url: String(uploaded || ""), name: file.name || "Файл", mimeType: file.type || "", size: file.size || 0 };
        const url = String(payload.url || payload.href || "").trim();
        if (!url) return;
        const range = quill.getSelection(true);
        const index = range?.index ?? quill.getLength();
        quill.insertEmbed(index, "attachment", {
          url,
          name: String(payload.name || file.name || "Файл"),
          mimeType: String(payload.mimeType || payload.mime || file.type || ""),
          size: Number(payload.size || file.size || 0) || 0,
        }, "user");
        const [leaf] = quill.getLeaf(index);
        if (leaf?.domNode instanceof HTMLElement) {
          ensureEmbedAttrs(quill.root);
          selectEmbedNode(leaf.domNode, "attachment");
        }
        quill.setSelection(index + 1, 0, "user");
      } catch (_) {
        // error handled in upload callback
      }
    };
  }, [onFileUpload, getEditor, ensureEmbedAttrs, selectEmbedNode]);

  const richTextModules = useMemo(() => ({
    toolbar: {
      container: RICH_TEXT_TOOLBAR,
      handlers: {
        image: handleImageInsert,
        attachment: handleAttachmentInsert,
      },
    },
  }), [handleImageInsert, handleAttachmentInsert]);

  useEffect(() => {
    const quill = getEditor();
    if (!quill) return;
    ensureEmbedAttrs(quill.root);
  }, [value, getEditor, ensureEmbedAttrs]);

  useEffect(() => {
    syncImageControlsPosition();
  }, [syncImageControlsPosition, value]);

  useEffect(() => {
    if (!selectedEmbed || selectedEmbed.type !== "image") return undefined;
    const quill = getEditor();
    if (!quill) return undefined;
    const root = quill.root;
    const container = root.parentElement;
    const handleReposition = () => requestAnimationFrame(() => syncImageControlsPosition());
    window.addEventListener("resize", handleReposition);
    window.addEventListener("scroll", handleReposition, true);
    root.addEventListener("scroll", handleReposition);
    container?.addEventListener("scroll", handleReposition);
    return () => {
      window.removeEventListener("resize", handleReposition);
      window.removeEventListener("scroll", handleReposition, true);
      root.removeEventListener("scroll", handleReposition);
      container?.removeEventListener("scroll", handleReposition);
    };
  }, [selectedEmbed, getEditor, syncImageControlsPosition]);

  useEffect(() => {
    const quill = getEditor();
    if (!quill) return undefined;
    const root = quill.root;
    const clearDropTargets = () => {
      root.querySelectorAll(".lms-drop-target-before, .lms-drop-target-after").forEach((node) => {
        node.classList.remove("lms-drop-target-before");
        node.classList.remove("lms-drop-target-after");
      });
    };

    const clearDropHint = () => {
      clearDropTargets();
      dragOverMetaRef.current = null;
      setDropIndicator(null);
    };

    const resolveDropMeta = (event) => {
      let dropIndex = null;
      let dropTargetNode = null;
      let dropPosition = "before";
      let dropLineViewportY = null;
      const target = event.target;

      if (target instanceof HTMLElement) {
        const targetEmbed = target.closest(".lms-file-embed, img");
        if (targetEmbed && root.contains(targetEmbed)) {
          const targetBlot = Quill.find(targetEmbed);
          if (targetBlot) {
            const targetIndex = quill.getIndex(targetBlot);
            const targetRect = targetEmbed.getBoundingClientRect();
            const insertBefore = event.clientY < targetRect.top + targetRect.height / 2;
            dropIndex = targetIndex + (insertBefore ? 0 : 1);
            dropTargetNode = targetEmbed;
            dropPosition = insertBefore ? "before" : "after";
            dropLineViewportY = insertBefore ? targetRect.top : targetRect.bottom;
          }
        } else {
          const targetBlot = Quill.find(target, true);
          if (targetBlot) {
            try {
              dropIndex = quill.getIndex(targetBlot);
            } catch (_) {
              dropIndex = null;
            }
          }
        }
      }

      if (dropIndex == null) {
        const range = quill.getSelection(true);
        dropIndex = range?.index ?? quill.getLength();
      }

      dropIndex = Math.max(0, Math.min(Number(dropIndex) || 0, quill.getLength()));

      if (dropLineViewportY == null) {
        const maxIndex = Math.max(0, quill.getLength() - 1);
        const boundsIndex = Math.max(0, Math.min(dropIndex, maxIndex));
        const bounds = quill.getBounds(boundsIndex, 0);
        const lineOffset = Number(bounds?.top || 0) + Number(bounds?.height || 16);
        dropLineViewportY = root.getBoundingClientRect().top + lineOffset;
      }

      const shellRect = editorShellRef.current?.getBoundingClientRect() || root.getBoundingClientRect();
      const rootRect = root.getBoundingClientRect();
      return {
        dropIndex,
        dropTargetNode,
        dropPosition,
        indicator: {
          top: Math.max(4, Math.round(dropLineViewportY - shellRect.top)),
          left: Math.max(4, Math.round(rootRect.left - shellRect.left + 8)),
          width: Math.max(40, Math.round(rootRect.width - 16)),
        },
      };
    };

    const onClick = (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const fileNode = target.closest(".lms-file-embed");
      if (fileNode && root.contains(fileNode)) {
        event.preventDefault();
        selectEmbedNode(fileNode, "attachment");
        return;
      }
      const imageNode = target.closest("img");
      if (imageNode && root.contains(imageNode)) {
        event.preventDefault();
        selectEmbedNode(imageNode, "image");
        return;
      }
      clearDropHint();
      clearSelectedEmbed();
    };

    const onKeyDown = (event) => {
      if (!selectedEmbed) return;
      if (event.key === "Backspace" || event.key === "Delete") {
        event.preventDefault();
        removeSelectedEmbed();
      }
    };

    const onDragStart = (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      clearDropHint();

      const fileNode = target.closest(".lms-file-embed");
      if (fileNode && root.contains(fileNode)) {
        const blot = Quill.find(fileNode);
        if (!blot) return;
        const sourceIndex = quill.getIndex(blot);
        dragEmbedRef.current = {
          kind: "attachment",
          sourceIndex,
          payload: AttachmentBlot.value(fileNode),
        };
        if (event.dataTransfer) {
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", "lms-attachment");
        }
        selectEmbedNode(fileNode, "attachment");
        return;
      }

      const imageNode = target.closest("img");
      if (imageNode && root.contains(imageNode)) {
        const blot = Quill.find(imageNode);
        if (!blot) return;
        const sourceIndex = quill.getIndex(blot);
        dragEmbedRef.current = {
          kind: "image",
          sourceIndex,
          payload: {
            src: String(imageNode.getAttribute("src") || "").trim(),
            width: String(imageNode.getAttribute("width") || "").trim(),
          },
        };
        if (event.dataTransfer) {
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", "lms-image");
        }
        selectEmbedNode(imageNode, "image");
      }
    };

    const onDragOver = (event) => {
      if (!dragEmbedRef.current) return;
      event.preventDefault();
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = "move";
      }
      const meta = resolveDropMeta(event);
      if (!meta) return;
      clearDropTargets();
      if (meta.dropTargetNode) {
        meta.dropTargetNode.classList.add(meta.dropPosition === "before" ? "lms-drop-target-before" : "lms-drop-target-after");
      }
      dragOverMetaRef.current = meta;
      setDropIndicator((prev) => {
        if (
          prev
          && prev.top === meta.indicator.top
          && prev.left === meta.indicator.left
          && prev.width === meta.indicator.width
        ) {
          return prev;
        }
        return meta.indicator;
      });
    };

    const onDrop = (event) => {
      if (!dragEmbedRef.current) return;
      event.preventDefault();
      const dragged = dragEmbedRef.current;
      dragEmbedRef.current = null;
      const dropMeta = dragOverMetaRef.current || resolveDropMeta(event);
      clearDropHint();

      const sourceIndex = Number(dragged?.sourceIndex);
      if (!Number.isFinite(sourceIndex)) return;
      let dropIndex = Number(dropMeta?.dropIndex);
      if (!Number.isFinite(dropIndex)) dropIndex = quill.getLength();

      if (dropIndex > sourceIndex) dropIndex -= 1;
      dropIndex = Math.max(0, dropIndex);
      if (dropIndex === sourceIndex) return;

      quill.deleteText(sourceIndex, 1, "user");

      if (dragged.kind === "image") {
        const src = String(dragged?.payload?.src || "").trim();
        if (!src) return;
        quill.insertEmbed(dropIndex, "image", src, "user");
        const [leaf] = quill.getLeaf(dropIndex);
        if (leaf?.domNode instanceof HTMLImageElement) {
          if (dragged?.payload?.width) {
            leaf.domNode.setAttribute("width", dragged.payload.width);
          }
          ensureEmbedAttrs(root);
          selectEmbedNode(leaf.domNode, "image");
        }
      } else if (dragged.kind === "attachment") {
        quill.insertEmbed(dropIndex, "attachment", dragged.payload || {}, "user");
        const [leaf] = quill.getLeaf(dropIndex);
        if (leaf?.domNode instanceof HTMLElement) {
          ensureEmbedAttrs(root);
          selectEmbedNode(leaf.domNode, "attachment");
        }
      }

      quill.setSelection(Math.min(dropIndex + 1, quill.getLength()), 0, "user");
    };

    const onDragLeave = (event) => {
      if (!dragEmbedRef.current) return;
      const relatedTarget = event.relatedTarget;
      if (relatedTarget instanceof Node && root.contains(relatedTarget)) return;
      clearDropHint();
    };

    const onDragEnd = () => {
      dragEmbedRef.current = null;
      clearDropHint();
    };

    root.addEventListener("click", onClick);
    root.addEventListener("keydown", onKeyDown);
    root.addEventListener("dragstart", onDragStart);
    root.addEventListener("dragover", onDragOver);
    root.addEventListener("drop", onDrop);
    root.addEventListener("dragleave", onDragLeave);
    root.addEventListener("dragend", onDragEnd);

    return () => {
      clearDropHint();
      root.removeEventListener("click", onClick);
      root.removeEventListener("keydown", onKeyDown);
      root.removeEventListener("dragstart", onDragStart);
      root.removeEventListener("dragover", onDragOver);
      root.removeEventListener("drop", onDrop);
      root.removeEventListener("dragleave", onDragLeave);
      root.removeEventListener("dragend", onDragEnd);
    };
  }, [getEditor, selectEmbedNode, clearSelectedEmbed, selectedEmbed, removeSelectedEmbed, ensureEmbedAttrs]);

  return (
    <div
      ref={editorShellRef}
      className="lms-rich-text-editor"
      style={{ "--lms-editor-min-height": `${Math.max(100, Number(minHeight || 140))}px` }}
    >
      <ReactQuill
        ref={quillRef}
        theme="snow"
        value={prepareRichTextValue(value)}
        onChange={(html, _delta, source) => {
          if (source !== "user") return;
          onChange?.(normalizeRichTextValue(html));
        }}
        placeholder={placeholder}
        modules={richTextModules}
        formats={RICH_TEXT_FORMATS}
      />
      {dropIndicator && (
        <div
          className="lms-rich-drop-indicator"
          style={{
            top: `${dropIndicator.top}px`,
            left: `${dropIndicator.left}px`,
            width: `${dropIndicator.width}px`,
          }}
        >
          <span className="lms-rich-drop-indicator-dot" />
          <span className="lms-rich-drop-indicator-dot lms-rich-drop-indicator-dot-end" />
        </div>
      )}
      {selectedEmbed?.type === "image" && imageControlsPos && (
        <div
          className="lms-rich-image-controls"
          style={{ top: `${imageControlsPos.top}px`, left: `${imageControlsPos.left}px` }}
        >
          <button
            type="button"
            className="lms-rich-image-control-btn"
            onClick={() => resizeSelectedImage(-80)}
            aria-label="Уменьшить изображение"
            title="Уменьшить"
          >
            <Minus size={13} />
          </button>
          <button
            type="button"
            className="lms-rich-image-control-btn"
            onClick={() => resizeSelectedImage(80)}
            aria-label="Увеличить изображение"
            title="Увеличить"
          >
            <Plus size={13} />
          </button>
          <button
            type="button"
            className="lms-rich-image-control-btn lms-rich-image-control-btn-danger"
            onClick={removeSelectedEmbed}
            aria-label="Удалить изображение"
            title="Удалить"
          >
            <Trash2 size={13} />
          </button>
        </div>
      )}
      {selectedEmbed && selectedEmbed.type !== "image" && (
        <div className="lms-rich-embed-toolbar">
          <span className="lms-rich-embed-badge">Файл</span>
          <button type="button" className="lms-rich-embed-btn lms-rich-embed-btn-danger" onClick={removeSelectedEmbed} aria-label="Удалить элемент">
            <X size={12} />
          </button>
        </div>
      )}
    </div>
  );
}

function RichTextContent({ value, className = "", emptyState = null }) {
  const safeHtml = sanitizeRichHtml(value);
  if (!safeHtml) return emptyState;
  return <div className={`lms-rich-content ${className}`.trim()} dangerouslySetInnerHTML={{ __html: safeHtml }} />;
}

const statusConfig = {
  completed: { label: "Завершён", bg: "bg-emerald-50", text: "text-emerald-700", border: "border-emerald-200", dot: "bg-emerald-500" },
  completed_late: { label: "Завершён с опозданием", bg: "bg-orange-50", text: "text-orange-700", border: "border-orange-200", dot: "bg-orange-500" },
  in_progress: { label: "В процессе", bg: "bg-blue-50", text: "text-blue-700", border: "border-blue-200", dot: "bg-blue-500" },
  not_started: { label: "Не начат", bg: "bg-slate-50", text: "text-slate-600", border: "border-slate-200", dot: "bg-slate-400" },
  overdue: { label: "Просрочен", bg: "bg-red-50", text: "text-red-700", border: "border-red-200", dot: "bg-red-500" },
  waiting_test: { label: "Ожидает тест", bg: "bg-violet-50", text: "text-violet-700", border: "border-violet-200", dot: "bg-violet-500" },
  test_failed: { label: "Тест не пройден", bg: "bg-red-50", text: "text-red-700", border: "border-red-200", dot: "bg-red-500" },
};

const lessonIcons = { video: Video, text: FileText, quiz: HelpCircle, combined: Layers };

const formatDeadline = (date) => {
  if (!date) return null;
  const d = new Date(date);
  const now = new Date();
  const diff = Math.ceil((d - now) / (1000 * 60 * 60 * 24));
  if (diff < 0) return { label: `Просрочен на ${Math.abs(diff)} дн`, urgent: true, overdue: true };
  if (diff <= 7) return { label: `${diff} дней`, urgent: true, overdue: false };
  return { label: d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }), urgent: false, overdue: false };
};

const parseLmsDate = (value) => {
  if (!value) return null;
  const parsed = value instanceof Date ? value : new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

const normalizeLmsDeadlineDate = (value) => {
  const parsed = parseLmsDate(value);
  if (!parsed) return null;
  const isMidnight = (
    parsed.getHours() === 0
    && parsed.getMinutes() === 0
    && parsed.getSeconds() === 0
    && parsed.getMilliseconds() === 0
  );
  if (!isMidnight) return parsed;
  const endOfDay = new Date(parsed);
  endOfDay.setHours(23, 59, 59, 999);
  return endOfDay;
};

const resolveDeadlineStatusByDates = ({ dueAt, completedAt, fallbackStatus }) => {
  const completed = parseLmsDate(completedAt);
  const effectiveDueAt = normalizeLmsDeadlineDate(dueAt);
  if (completed) {
    if (effectiveDueAt && completed.getTime() > effectiveDueAt.getTime()) return "orange";
    return "green";
  }
  if (effectiveDueAt && Date.now() > effectiveDueAt.getTime()) return "red";
  const fallback = String(fallbackStatus || "").trim().toLowerCase();
  if (fallback === "green" || fallback === "orange" || fallback === "red") return fallback;
  return null;
};

const formatDateTimeLabel = (value) => {
  const parsed = parseLmsDate(value);
  if (!parsed) return "";
  return parsed.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const formatDeadlineForStatus = (date, status) => {
  if (!date) return null;
  const base = formatDeadline(date);
  if (!base) return null;
  const statusNorm = String(status || "").trim().toLowerCase();
  const completedStatus = statusNorm === "completed" || statusNorm === "completed_late";
  if (!completedStatus) return base;
  const parsed = new Date(date);
  return {
    label: Number.isNaN(parsed.getTime())
      ? base.label
      : parsed.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }),
    urgent: false,
    overdue: false,
  };
};

// Проверка правильности ответа
function isAnswerCorrect(question, userAnswer) {
  if (question.type === "single" || question.type === "bool") {
    return userAnswer === question.correct;
  }
  if (question.type === "multiple") {
    if (!Array.isArray(userAnswer)) return false;
    const sortedUser = [...userAnswer].sort();
    const sortedCorrect = [...question.correct].sort();
    return sortedUser.length === sortedCorrect.length && sortedUser.every((v, i) => v === sortedCorrect[i]);
  }
  if (question.type === "text") {
    if (!userAnswer) return false;
    return question.keywords.some(k => userAnswer.toLowerCase().includes(k.toLowerCase()));
  }
  return false;
}

const COURSE_COLORS = [
  "from-blue-600 to-indigo-700",
  "from-emerald-500 to-teal-600",
  "from-violet-500 to-purple-700",
  "from-amber-500 to-orange-600",
  "from-cyan-500 to-blue-600",
  "from-rose-500 to-red-700",
];

const COURSE_COVERS = ["📘", "🚀", "💬", "📊", "📈", "🛡️", "🎯", "🧠"];

const TEXT_MATERIAL_TYPES = new Set(["text", "pdf", "link"]);

const normalizeLmsRole = (value) => {
  const role = String(value || "").trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (role === "superadmin") return "super_admin";
  if (role === "supervisor") return "sv";
  return role;
};

const isPublishedLmsCourse = (courseLike) => String(courseLike?.status || "").trim().toLowerCase() === "published";

const isPublishedLmsCourseVersion = (versionLike) => String(versionLike?.status || "").trim().toLowerCase() === "published";

const isAssignableLmsCourse = (courseLike) => (
  isPublishedLmsCourse(courseLike) && isPublishedLmsCourseVersion(courseLike?.current_version)
);

const mapAssignmentStatusToUi = (assignmentStatus, deadlineStatus) => {
  const status = String(assignmentStatus || "").trim().toLowerCase();
  const deadline = String(deadlineStatus || "").trim().toLowerCase();
  if (status === "completed") {
    return deadline === "orange" ? "completed_late" : "completed";
  }
  if (deadline === "red") return "overdue";
  if (status === "in_progress") return "in_progress";
  return "not_started";
};

const resolveCourseMandatoryFlag = (courseLike = {}) => {
  const assignmentId = Number(courseLike?.assignmentId || courseLike?.assignment_id || 0);
  if (assignmentId > 0) return true;
  const explicitMandatory =
    courseLike?.mandatory ??
    courseLike?.is_mandatory ??
    courseLike?.required ??
    courseLike?.is_required;
  return Boolean(explicitMandatory);
};

const clampLmsProgress = (value) => Math.max(0, Math.min(100, Number(value) || 0));

const LMS_VIDEO_POSITION_STORAGE_PREFIX = "lms:video-position:v1";
const LMS_VIDEO_POSITION_MAX_AGE_MS = 1000 * 60 * 60 * 24 * 30;

const normalizeVideoStorageSource = (value) => {
  const raw = String(value || "").trim();
  if (!raw) return "";
  try {
    const parsed = new URL(raw, typeof window !== "undefined" ? window.location.origin : "http://localhost");
    const origin = String(parsed.origin || "").toLowerCase();
    const pathname = String(parsed.pathname || "").toLowerCase();
    return `${origin}${pathname}`.trim();
  } catch (_) {
    return raw.split("#")[0].split("?")[0].trim().toLowerCase();
  }
};

const buildLmsVideoPositionStorageKey = ({ scope = "lesson", lessonId = 0, materialId = 0, videoUrl = "" } = {}) => {
  const safeScope = String(scope || "lesson").trim().toLowerCase() || "lesson";
  const lessonToken = Number(lessonId || 0) > 0 ? String(Number(lessonId || 0)) : "0";
  const materialToken = Number(materialId || 0) > 0 ? String(Number(materialId || 0)) : "0";
  const sourceTokenRaw = normalizeVideoStorageSource(videoUrl) || "no-source";
  const sourceToken = sourceTokenRaw.slice(0, 280);
  return `${LMS_VIDEO_POSITION_STORAGE_PREFIX}:${safeScope}:${lessonToken}:${materialToken}:${sourceToken}`;
};

const readLmsVideoPositionFromStorage = (storageKey) => {
  if (!storageKey || typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;

    const updatedAtMs = Number(parsed.updated_at_ms || 0);
    if (Number.isFinite(updatedAtMs) && updatedAtMs > 0) {
      const ageMs = Date.now() - updatedAtMs;
      if (ageMs > LMS_VIDEO_POSITION_MAX_AGE_MS) {
        window.localStorage.removeItem(storageKey);
        return null;
      }
    }

    const positionSeconds = Math.max(0, Number(parsed.position_seconds || 0));
    const durationSeconds = Math.max(0, Number(parsed.duration_seconds || 0));
    const progressFromPayload = parsed.progress_ratio != null ? Number(parsed.progress_ratio) : null;
    const progressFromPosition = durationSeconds > 0 ? (positionSeconds / durationSeconds) * 100 : 0;
    const progressRatio = clampLmsProgress(
      progressFromPayload != null && Number.isFinite(progressFromPayload)
        ? progressFromPayload
        : progressFromPosition
    );

    return {
      position_seconds: Number(positionSeconds.toFixed(2)),
      duration_seconds: Number(durationSeconds.toFixed(2)),
      progress_ratio: Number(progressRatio.toFixed(2)),
      updated_at_ms: Number.isFinite(updatedAtMs) ? updatedAtMs : 0,
    };
  } catch (_) {
    return null;
  }
};

const writeLmsVideoPositionToStorage = (storageKey, payload = {}) => {
  if (!storageKey || typeof window === "undefined") return;
  try {
    const positionSeconds = Math.max(0, Number(payload.position_seconds || 0));
    const durationSeconds = Math.max(0, Number(payload.duration_seconds || 0));
    const progressCandidate = payload.progress_ratio != null
      ? Number(payload.progress_ratio)
      : (durationSeconds > 0 ? (positionSeconds / durationSeconds) * 100 : 0);
    const progressRatio = clampLmsProgress(progressCandidate);
    const normalizedPayload = {
      position_seconds: Number(positionSeconds.toFixed(2)),
      duration_seconds: Number(durationSeconds.toFixed(2)),
      progress_ratio: Number(progressRatio.toFixed(2)),
      updated_at_ms: Date.now(),
    };
    window.localStorage.setItem(storageKey, JSON.stringify(normalizedPayload));
  } catch (_) {
    // ignore storage write errors (quota/security)
  }
};

const clearLmsVideoPositionFromStorage = (storageKey) => {
  if (!storageKey || typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(storageKey);
  } catch (_) {
    // ignore storage remove errors
  }
};

const preventLmsVideoAssetAction = (event) => {
  event.preventDefault();
};

const LMS_PROTECTED_VIDEO_PROPS = Object.freeze({
  controlsList: "nodownload noplaybackrate nofullscreen",
  disablePictureInPicture: true,
  disableRemotePlayback: true,
  draggable: false,
  referrerPolicy: "no-referrer",
  onContextMenu: preventLmsVideoAssetAction,
  onDragStart: preventLmsVideoAssetAction,
});

const isCompletedLmsStatus = (status) => status === "completed" || status === "completed_late";

const mapAdminProgressRowToUiStatus = (row) => {
  const statusNorm = String(row?.status || "").trim().toLowerCase();
  const deadlineStatus = resolveDeadlineStatusByDates({
    dueAt: row?.due_at,
    completedAt: row?.completed_at,
    fallbackStatus: statusNorm === "completed" ? null : row?.deadline_status,
  });
  return mapAssignmentStatusToUi(statusNorm, deadlineStatus);
};

const resolveAdminCourseAggregateStatus = (stat = {}) => {
  const total = Math.max(0, Number(stat?.total || 0));
  if (!total) return "not_started";

  const overdue = Math.max(0, Number(stat?.overdue || 0));
  const inProgress = Math.max(0, Number(stat?.inProgress || 0));
  const completed = Math.max(0, Number(stat?.completed || 0));
  const completedLate = Math.max(0, Number(stat?.completedLate || 0));
  const notStarted = Math.max(0, Number(stat?.notStarted || 0));

  if (overdue > 0) return "overdue";
  if (inProgress > 0) return "in_progress";
  if (completed + completedLate >= total) {
    return completedLate > 0 ? "completed_late" : "completed";
  }
  if (notStarted >= total) return "not_started";
  if (completed + completedLate > 0) return "in_progress";
  return "not_started";
};

const pickCourseVisual = (courseId, category = "") => {
  const base = Number(courseId) || Math.abs(String(category || "").length);
  const color = COURSE_COLORS[Math.abs(base) % COURSE_COLORS.length];
  const cover = COURSE_COVERS[Math.abs(base) % COURSE_COVERS.length];
  return { color, cover };
};

const formatDurationLabel = (seconds) => {
  const safe = Math.max(0, Number(seconds) || 0);
  if (!safe) return "—";
  const totalMinutes = Math.ceil(safe / 60);
  if (totalMinutes < 60) return `${totalMinutes} мин`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return minutes > 0 ? `${hours} ч ${minutes} мин` : `${hours} ч`;
};

const LMS_CLIENT_SESSION_STORAGE_KEY = "lms:client-session-id";

const createLmsClientSessionKey = () => {
  if (typeof window !== "undefined" && window.crypto?.randomUUID) {
    return `lms-${window.crypto.randomUUID()}`;
  }
  return `lms-${Math.random().toString(36).slice(2, 10)}-${Date.now().toString(36)}`;
};

const getOrCreateLmsClientSessionKey = () => {
  if (typeof window === "undefined") return "";
  try {
    const existing = window.sessionStorage?.getItem(LMS_CLIENT_SESSION_STORAGE_KEY);
    if (existing) return String(existing);
    const next = createLmsClientSessionKey();
    window.sessionStorage?.setItem(LMS_CLIENT_SESSION_STORAGE_KEY, next);
    return next;
  } catch (_) {
    return "";
  }
};

const appendClientSessionKeyToPath = (path, clientSessionKey) => {
  const normalizedPath = String(path || "").trim();
  const normalizedKey = String(clientSessionKey || "").trim();
  if (!normalizedPath || !normalizedKey) return normalizedPath;
  const separator = normalizedPath.includes("?") ? "&" : "?";
  return `${normalizedPath}${separator}client_session_key=${encodeURIComponent(normalizedKey)}`;
};

const resolveLmsHeartbeatIntervalMs = (lessonLike, fallbackSeconds = 5) => {
  const configuredSeconds = Number(
    lessonLike?.antiCheat?.heartbeat_seconds
    ?? lessonLike?.anti_cheat?.heartbeat_seconds
    ?? fallbackSeconds
  );
  const normalizedSeconds = Number.isFinite(configuredSeconds)
    ? Math.max(3, Math.min(60, configuredSeconds))
    : fallbackSeconds;
  return Math.round(normalizedSeconds * 1000);
};

function useLmsLessonSessionLifecycle({
  enabled,
  lessonId,
  clientSessionKey,
  lmsRequest,
  onHidden,
  onVisible,
  onBeforeClose,
}) {
  const onHiddenRef = useRef(onHidden);
  const onVisibleRef = useRef(onVisible);
  const onBeforeCloseRef = useRef(onBeforeClose);
  const sessionClosedRef = useRef(false);

  useEffect(() => {
    onHiddenRef.current = onHidden;
  }, [onHidden]);

  useEffect(() => {
    onVisibleRef.current = onVisible;
  }, [onVisible]);

  useEffect(() => {
    onBeforeCloseRef.current = onBeforeClose;
  }, [onBeforeClose]);

  useEffect(() => {
    sessionClosedRef.current = false;
  }, [lessonId, clientSessionKey]);

  const postLessonEvent = useCallback(async (eventType, payload = {}, options = {}) => {
    if (!enabled || !lessonId || typeof lmsRequest !== "function") return null;
    return lmsRequest(`/api/lms/lessons/${lessonId}/event`, {
      method: "POST",
      keepalive: options?.keepalive === true,
      body: {
        event_type: eventType,
        payload,
        client_session_key: clientSessionKey || undefined,
        client_ts: new Date().toISOString(),
      },
    });
  }, [enabled, lessonId, lmsRequest, clientSessionKey]);

  const sendSessionEnd = useCallback((reason = "client_close", options = {}) => {
    if (!enabled || !lessonId || sessionClosedRef.current) return;
    sessionClosedRef.current = true;
    try {
      onBeforeCloseRef.current?.(reason);
    } catch (_) {
      // ignore non-blocking cleanup errors
    }
    void postLessonEvent("session_end", { reason }, options).catch(() => {});
  }, [enabled, lessonId, postLessonEvent]);

  useEffect(() => {
    if (!enabled || typeof document === "undefined") return undefined;
    const handleVisibilityChange = () => {
      const isVisible = !document.hidden;
      if (isVisible) {
        onVisibleRef.current?.();
        void postLessonEvent("tab_visible").catch(() => {});
        return;
      }
      onHiddenRef.current?.();
      void postLessonEvent("tab_hidden").catch(() => {});
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [enabled, postLessonEvent]);

  useEffect(() => {
    if (!enabled || typeof window === "undefined") return undefined;
    const handlePageHide = () => {
      sendSessionEnd("pagehide", { keepalive: true });
    };
    const handleBeforeUnload = () => {
      sendSessionEnd("beforeunload", { keepalive: true });
    };
    window.addEventListener("pagehide", handlePageHide);
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => {
      window.removeEventListener("pagehide", handlePageHide);
      window.removeEventListener("beforeunload", handleBeforeUnload);
      sendSessionEnd("lesson_unmount");
    };
  }, [enabled, sendSessionEnd]);

  return {
    postLessonEvent,
    sendSessionEnd,
  };
}

const inferLessonType = (lessonLike) => {
  const explicit = String(lessonLike?.lesson_type || lessonLike?.type || "").trim().toLowerCase();
  if (explicit === "quiz") return "quiz";

  const materials = Array.isArray(lessonLike?.materials) ? lessonLike.materials : [];
  const hasStructuredBlocks = Array.isArray(lessonLike?.blocks)
    && lessonLike.blocks.some((blockItem) => {
      const blockType = String(blockItem?.type || blockItem?.material_type || "").toLowerCase();
      return blockType === "text" || blockType === "video";
    });
  const hasVideo = materials.some((m) => String(m?.material_type || m?.type || "").toLowerCase() === "video");
  const hasText = materials.some((m) => String(m?.material_type || m?.type || "").toLowerCase() === "text");
  const hasCombinedFlag = materials.some((m) => {
    const meta = m?.metadata;
    if (!meta || typeof meta !== "object") return false;
    const marker = String(meta?.combined_block ?? "").trim().toLowerCase();
    return marker === "true" || marker === "1" || marker === "yes" || marker === "y" || marker === "t";
  });
  const combinedByStructure = hasStructuredBlocks || (hasCombinedFlag && hasVideo && hasText);

  if (explicit === "combined") return "combined";
  if (combinedByStructure && hasVideo && hasText) return "combined";

  if (explicit === "text") return "text";
  if (explicit === "video") {
    // Legacy safety: some old text lessons were persisted as `video` after migration.
    if (!hasVideo && hasText) return "text";
    return "video";
  }

  if (hasVideo) return "video";
  if (hasText) return "text";
  if (!materials.length) return "text";
  if (materials.every((m) => TEXT_MATERIAL_TYPES.has(String(m?.material_type || m?.type || "").toLowerCase()))) return "text";
  return "text";
};

const resolveCourseAttemptTests = (tests) => {
  const safeTests = Array.isArray(tests) ? tests : [];
  const finalTests = safeTests.filter((test) => Boolean(test?.is_final));
  return finalTests.length > 0 ? finalTests : safeTests;
};

const toRelativeTime = (isoDate) => {
  if (!isoDate) return "";
  const dt = new Date(isoDate);
  if (Number.isNaN(dt.getTime())) return "";
  const diffMs = Date.now() - dt.getTime();
  const diffSec = Math.max(0, Math.floor(diffMs / 1000));
  if (diffSec < 60) return "только что";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin} мин назад`;
  const diffHours = Math.floor(diffMin / 60);
  if (diffHours < 24) return `${diffHours} ч назад`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 7) return `${diffDays} дн назад`;
  return dt.toLocaleDateString("ru-RU");
};

const toDateInputValue = (value) => {
  if (!value) return "";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const timezoneOffsetMs = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - timezoneOffsetMs).toISOString().slice(0, 10);
};

const mapHomeCourseToView = (course) => {
  const courseId = Number(course?.course_id || 0);
  const assignmentId = Number(course?.assignment_id || 0);
  const visual = pickCourseVisual(courseId, course?.category);
  const totalLessons = Math.max(0, Number(course?.total_lessons || 0));
  const completedLessons = Math.max(0, Number(course?.completed_lessons || 0));
  const progress = Math.max(0, Math.min(100, Math.round(Number(course?.progress_percent || 0))));
  const coverUrl = String(course?.cover_url || "").trim();
  const skills = Array.isArray(course?.skills)
    ? course.skills.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  // Теперь бекенд честно отдаёт сумму секунд (уроки + тесты), берём её.
  // Иначе фолбэк по количеству уроков.
  const apiDurationSeconds = Number(course?.total_duration_seconds || 0);
  const fallbackMinutes = totalLessons * 15;
  const durationLabel = apiDurationSeconds > 0
    ? formatDurationLabel(apiDurationSeconds)
    : formatDurationLabel(fallbackMinutes * 60);
  const deadlineStatus = resolveDeadlineStatusByDates({
    dueAt: course?.due_at,
    completedAt: course?.completed_at,
    fallbackStatus: String(course?.status || "").trim().toLowerCase() === "completed" ? null : course?.deadline_status,
  });
  const deadlineValue = course?.due_at || course?.deadline || course?.dueAt || null;
  const mandatory = resolveCourseMandatoryFlag({
    assignmentId,
    mandatory: course?.mandatory,
    is_mandatory: course?.is_mandatory,
    required: course?.required,
    is_required: course?.is_required,
  });
  return {
    id: courseId,
    assignmentId: assignmentId || null,
    courseVersionId: Number(course?.course_version_id || 0) || null,
    title: String(course?.title || "Без названия"),
    category: String(course?.category || "Без категории"),
    cover: visual.cover,
    coverUrl,
    color: visual.color,
    description: String(course?.description || ""),
    skills,
    duration: durationLabel,
    lessons: totalLessons,
    modules: 0,
    progress,
    completedLessons,
    deadline: deadlineValue,
    mandatory,
    status: mapAssignmentStatusToUi(course?.status, deadlineStatus),
    rating: course?.best_score ? Math.max(1, Math.min(5, Number(course.best_score) / 20)) : 0,
    reviews: 0,
    passingScore: 80,
    maxAttempts: 0,
    attemptsUsed: 0,
    hasCourseAttemptLimit: false,
    modules_data: [],
  };
};

const mapApiQuestionToPreview = (question, fallbackId = 0) => {
  const qType = mapApiQuestionTypeToView(question?.type);
  const apiOptions = Array.isArray(question?.options) ? question.options : [];
  const optionRows = apiOptions.map((option, optionIndex) => ({
    id: Number(option?.id || optionIndex + 1),
    text: String(option?.text || `Вариант ${optionIndex + 1}`).trim(),
    isCorrect: Boolean(option?.is_correct),
  }));
  let correct = qType === "multiple" ? [] : null;
  if (qType === "multiple") {
    correct = optionRows
      .map((optionRow, optionIndex) => (optionRow.isCorrect ? optionIndex : null))
      .filter((index) => index != null);
  } else if (qType === "single" || qType === "bool") {
    const idx = optionRows.findIndex((optionRow) => optionRow.isCorrect);
    correct = idx >= 0 ? idx : 0;
  }
  return {
    id: Number(question?.id || fallbackId || Date.now() + Math.floor(Math.random() * 1000)),
    text: String(question?.prompt || question?.text || "Вопрос").trim(),
    type: qType,
    options: optionRows,
    correct,
    correctTextAnswers: Array.isArray(question?.correct_text_answers)
      ? question.correct_text_answers.map((item) => String(item || "").trim()).filter(Boolean)
      : [],
    explanation: String(question?.metadata?.explanation || question?.explanation || "").trim(),
    points: Math.max(0, Number(question?.points || 0)),
  };
};

const mapCourseDetailToView = (coursePayload, fallbackCourse = {}) => {
  const courseId = Number(coursePayload?.id || fallbackCourse?.id || 0);
  const visual = {
    color: fallbackCourse?.color || pickCourseVisual(courseId, coursePayload?.category).color,
    cover: fallbackCourse?.cover || pickCourseVisual(courseId, coursePayload?.category).cover,
  };
  const assignment = coursePayload?.assignment || {};
  const lessonProgress = assignment?.lesson_progress || {};
  const testProgress = assignment?.tests || {};
  const modulesRaw = Array.isArray(coursePayload?.modules) ? coursePayload.modules : [];
  const testsRaw = Array.isArray(coursePayload?.tests) ? coursePayload.tests : [];
  const courseAttemptTests = resolveCourseAttemptTests(testsRaw);
  const versionCoverUrl = String(coursePayload?.course_version?.cover_url || "").trim();
  const versionSkills = Array.isArray(coursePayload?.course_version?.skills)
    ? coursePayload.course_version.skills.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  const testsByModule = new Map();
  const testsBySourceLesson = new Map();
  testsRaw.forEach((test) => {
    const key = test?.module_id == null ? "__course__" : String(test.module_id);
    const prev = testsByModule.get(key) || [];
    prev.push(test);
    testsByModule.set(key, prev);
    const sourceLessonId = Number(test?.source_lesson_id || 0);
    if (sourceLessonId > 0) {
      const list = testsBySourceLesson.get(sourceLessonId) || [];
      list.push(test);
      testsBySourceLesson.set(sourceLessonId, list);
    }
  });
  const consumedSourceLinkedTestIds = new Set();

  const assignmentId = Number(assignment?.id || fallbackCourse?.assignmentId || 0);
  const hasAssignmentContext = assignmentId > 0;
  const courseDeadline = assignment?.due_at || fallbackCourse?.deadline || null;
  const assignmentDeadlineStatus = resolveDeadlineStatusByDates({
    dueAt: courseDeadline,
    completedAt: assignment?.completed_at,
    fallbackStatus: String(assignment?.status || "").trim().toLowerCase() === "completed" ? null : assignment?.deadline_status,
  });
  const mandatory = resolveCourseMandatoryFlag({
    assignmentId,
    mandatory: assignment?.mandatory ?? fallbackCourse?.mandatory,
    is_mandatory: assignment?.is_mandatory,
    required: assignment?.required,
    is_required: assignment?.is_required,
  });

  let progressionLocked = false;
  let regularLessonsTotal = 0;
  let regularLessonsCompleted = 0;
  let progressItemsTotal = 0;
  let progressItemsCompleted = 0;
  let durationSeconds = 0;

  const mapTestLesson = (test, isLockedByFlow = false) => {
    const testState = testProgress?.[test.id] || testProgress?.[String(test.id)] || {};
    const attemptsUsed = Math.max(0, Number(testState?.attempts_used || 0));
    const attemptLimit = Math.max(1, Number(test?.attempt_limit || coursePayload?.course_version?.attempt_limit || coursePayload?.default_attempt_limit || 3));
    const passedAny = Boolean(testState?.passed_any);
    const previewQuestions = Array.isArray(test?.questions)
      ? test.questions.map((questionItem, questionIndex) => mapApiQuestionToPreview(questionItem, questionIndex + 1))
      : [];
    const configuredMinutes = Math.max(0, Number(test?.time_limit_minutes || test?.time_limit || 0));
    const configuredSeconds = Math.max(0, Number(test?.time_limit_seconds || 0));
    const timeLimitSeconds = configuredSeconds > 0 ? configuredSeconds : (configuredMinutes > 0 ? configuredMinutes * 60 : 0);
    const fallbackMinutes = Math.max(1, Math.ceil((Number(test?.question_count || 0) || 1) * 1.5));
    const displayMinutes = timeLimitSeconds > 0 ? Math.max(1, Math.round(timeLimitSeconds / 60)) : fallbackMinutes;
    let testStatus = "not_started";
    if (passedAny) testStatus = "completed";
    else if (attemptsUsed > 0) testStatus = attemptsUsed >= attemptLimit ? "test_failed" : "in_progress";
    
    // Safely parse score if available. Different APIs might return max_score_percent or score_percent or score.
    const rawScore = testState?.max_score_percent ?? testState?.score_percent ?? testState?.score ?? testState?.best_score_percent ?? testState?.best_score ?? null;
    const scoreVal = rawScore != null ? Number(rawScore) : null;

    return {
      id: `test-${test.id}`,
      apiTestId: Number(test.id),
      title: String(test?.title || "Тест"),
      description: String(test?.description || ""),
      type: "quiz",
      duration: `${displayMinutes} мин`,
      durationSeconds: timeLimitSeconds > 0 ? timeLimitSeconds : displayMinutes * 60,
      timeLimitMinutes: timeLimitSeconds > 0 ? Math.max(1, Math.round(timeLimitSeconds / 60)) : null,
      timeLimitSeconds: timeLimitSeconds > 0 ? timeLimitSeconds : null,
      status: testStatus,
      locked: isLockedByFlow && testStatus !== "completed",
      requiresTest: true,
      maxAttempts: attemptLimit,
      attemptsUsed,
      score: scoreVal,
      isFinal: Boolean(test?.is_final),
      passingScore: Number(test?.pass_threshold || coursePayload?.course_version?.pass_threshold || coursePayload?.default_pass_threshold || 80),
      questionCount: Math.max(0, Number(test?.question_count || 0)),
      moduleId: test?.module_id == null ? null : Number(test.module_id),
      sourceLessonId: Number(test?.source_lesson_id || 0) || null,
      _position: Number(test?.position || test?.id || 0),
      quizQuestions: previewQuestions,
    };
  };

  const modulesData = modulesRaw
    .slice()
    .sort((a, b) => Number(a?.position || 0) - Number(b?.position || 0))
    .map((moduleItem, moduleIndex) => {
      const moduleId = Number(moduleItem?.id || moduleIndex + 1);
      const lessonsRaw = Array.isArray(moduleItem?.lessons) ? moduleItem.lessons.slice() : [];
      lessonsRaw.sort((a, b) => Number(a?.position || 0) - Number(b?.position || 0));
      const lessons = [];

      lessonsRaw.forEach((lessonItem, lessonIndex) => {
        const lessonId = Number(lessonItem?.id || lessonIndex + 1);
        const progressRow = lessonProgress?.[lessonId] || lessonProgress?.[String(lessonId)] || {};
        const completionRatio = Math.max(0, Math.min(100, Number(progressRow?.completion_ratio || 0)));
        let status = "not_started";
        if (String(progressRow?.status || "").toLowerCase() === "completed" || completionRatio >= 99) status = "completed";
        else if (String(progressRow?.status || "").toLowerCase() === "in_progress" || completionRatio > 0) status = "in_progress";
        const isLocked = progressionLocked && status !== "completed";
        const lessonType = inferLessonType(lessonItem);
        const lessonMaterials = Array.isArray(lessonItem?.materials) ? lessonItem.materials : [];
        const duration = formatDurationLabel(Number(lessonItem?.duration_seconds || 0));
        const linkedTests = Array.isArray(testsBySourceLesson.get(lessonId)) ? testsBySourceLesson.get(lessonId) : [];
        const linkedTestRaw = lessonType === "combined"
          ? (linkedTests.find((item) => !Boolean(item?.is_final)) || linkedTests[0] || null)
          : null;
        const linkedTestLesson = linkedTestRaw ? mapTestLesson(linkedTestRaw, false) : null;
        if (linkedTestLesson?.apiTestId) {
          consumedSourceLinkedTestIds.add(Number(linkedTestLesson.apiTestId));
        }
        const combinedBlocks = lessonType === "combined"
          ? (
            Array.isArray(lessonItem?.blocks) && lessonItem.blocks.length > 0
              ? lessonItem.blocks
              : lessonMaterials
                .filter((materialItem) => {
                  const materialType = String(materialItem?.material_type || materialItem?.type || "").toLowerCase();
                  return materialType === "text" || materialType === "video";
                })
                .map((materialItem, blockIndex) => ({
                  id: Number(materialItem?.id || 0) || `block-${lessonId}-${blockIndex + 1}`,
                  type: String(materialItem?.material_type || materialItem?.type || "text").toLowerCase(),
                  title: String(materialItem?.title || `Блок ${blockIndex + 1}`),
                  content_text: materialItem?.content_text || "",
                  content_url: materialItem?.content_url || materialItem?.url || materialItem?.signed_url || "",
                  url: materialItem?.url || materialItem?.signed_url || materialItem?.content_url || "",
                  signed_url: materialItem?.signed_url || materialItem?.url || materialItem?.content_url || "",
                  mime_type: materialItem?.mime_type || "",
                  bucket: materialItem?.bucket || materialItem?.gcs_bucket || "",
                  blob_path: materialItem?.blob_path || materialItem?.gcs_blob_path || "",
                  metadata: materialItem?.metadata && typeof materialItem.metadata === "object" ? materialItem.metadata : {},
                  position: Number(materialItem?.position || blockIndex + 1),
                }))
          )
          : [];
        const contentCompleted = status === "completed";
        const combinedNeedsTest = Boolean(linkedTestLesson) && linkedTestLesson.status !== "completed";
        const visualStatus = lessonType === "combined" && contentCompleted && combinedNeedsTest ? "in_progress" : status;

        lessons.push({
          id: lessonId,
          apiLessonId: lessonId,
          title: String(lessonItem?.title || `Урок ${lessonIndex + 1}`),
          description: String(lessonItem?.description || ""),
          type: lessonType,
          duration,
          durationSeconds: Number(lessonItem?.duration_seconds || 0),
          status: visualStatus,
          contentCompleted,
          locked: isLocked,
          completionRatio,
          allowFastForward: Boolean(lessonItem?.allow_fast_forward),
          completionThreshold: Number(lessonItem?.completion_threshold || 0),
          materials: lessonMaterials,
          combinedBlocks,
          combinedTest: linkedTestLesson ? {
            ...linkedTestLesson,
            locked: !contentCompleted || Boolean(isLocked),
            canStart: contentCompleted && linkedTestLesson.status !== "completed" && linkedTestLesson.attemptsUsed < linkedTestLesson.maxAttempts,
          } : null,
          combinedHasTest: Boolean(linkedTestLesson),
          moduleId,
          _position: Number(lessonItem?.position || lessonIndex + 1),
        });

        regularLessonsTotal += 1;
        progressItemsTotal += 1;
        durationSeconds += Math.max(0, Number(lessonItem?.duration_seconds || 0));
        if (contentCompleted) {
          regularLessonsCompleted += 1;
          progressItemsCompleted += 1;
        }
        if (!contentCompleted) progressionLocked = true;
        if (linkedTestLesson) {
          progressItemsTotal += 1;
          durationSeconds += Math.max(0, Number(linkedTestLesson.durationSeconds || 0));
          if (linkedTestLesson.status === "completed") progressItemsCompleted += 1;
          if (linkedTestLesson.status !== "completed") progressionLocked = true;
        }
      });

      const moduleTests = (testsByModule.get(String(moduleId)) || [])
        .slice()
        .filter((test) => !consumedSourceLinkedTestIds.has(Number(test?.id || 0)));
      moduleTests.forEach((test) => {
        const moduleHasIncompleteLesson = lessons.some((item) => item.type !== "quiz" && item.status !== "completed");
        const mappedTest = mapTestLesson(test, progressionLocked || moduleHasIncompleteLesson);
        lessons.push(mappedTest);
        // Добавляем длительность теста в общее время курса
        durationSeconds += Math.max(0, Number(mappedTest.durationSeconds || 0));
        progressItemsTotal += 1;
        if (mappedTest.status === "completed") progressItemsCompleted += 1;
        if (mappedTest.status !== "completed") progressionLocked = true;
      });

      lessons.sort((a, b) => Number(a?._position || 0) - Number(b?._position || 0));

      return {
        id: moduleId,
        title: String(moduleItem?.title || `Модуль ${moduleIndex + 1}`),
        description: String(moduleItem?.description || ""),
        lessons,
      };
    });

  const unboundTests = (testsByModule.get("__course__") || [])
    .slice()
    .filter((test) => !consumedSourceLinkedTestIds.has(Number(test?.id || 0)));
  if (unboundTests.length > 0) {
    if (modulesData.length === 0) {
      modulesData.push({
        id: 1,
        title: "Модуль",
        description: "",
        lessons: [],
      });
    }
    const targetModule = modulesData[modulesData.length - 1];
    unboundTests.forEach((test) => {
      const mappedTest = mapTestLesson(test, progressionLocked);
      targetModule.lessons.push(mappedTest);
      // Добавляем длительность финального теста к общему времени
      durationSeconds += Math.max(0, Number(mappedTest.durationSeconds || 0));
      progressItemsTotal += 1;
      if (mappedTest.status === "completed") progressItemsCompleted += 1;
      if (mappedTest.status !== "completed") progressionLocked = true;
    });
  }

  const isAssignmentCompleted = String(assignment?.status || "").toLowerCase() === "completed";
  const computedProgressPercent = isAssignmentCompleted
    ? 100
    : (progressItemsTotal > 0 ? Math.round((progressItemsCompleted / progressItemsTotal) * 100) : 0);
  const progressPercent = hasAssignmentContext
    ? computedProgressPercent
    : clampLmsProgress(fallbackCourse?.progress);

  let status = hasAssignmentContext
    ? mapAssignmentStatusToUi(assignment?.status, assignmentDeadlineStatus)
    : String(fallbackCourse?.status || "not_started");
  if (hasAssignmentContext && status !== "completed" && courseAttemptTests.length > 0 && regularLessonsTotal > 0 && regularLessonsCompleted >= regularLessonsTotal) {
    const hasFailedRequiredTest = courseAttemptTests.some((test) => {
      const testState = testProgress?.[test.id] || testProgress?.[String(test.id)] || {};
      const passedAny = Boolean(testState?.passed_any);
      const attemptsUsed = Math.max(0, Number(testState?.attempts_used || 0));
      const attemptLimit = Math.max(1, Number(test?.attempt_limit || coursePayload?.course_version?.attempt_limit || coursePayload?.default_attempt_limit || 3));
      return !passedAny && attemptsUsed >= attemptLimit;
    });
    status = hasFailedRequiredTest ? "test_failed" : "waiting_test";
  }

  const hasCourseAttemptLimit = hasAssignmentContext
    ? courseAttemptTests.length > 0
    : Boolean(fallbackCourse?.hasCourseAttemptLimit);
  const attemptsUsedTotal = hasAssignmentContext
    ? courseAttemptTests.reduce((sum, test) => {
      const testState = testProgress?.[test.id] || testProgress?.[String(test.id)] || {};
      return sum + Math.max(0, Number(testState?.attempts_used || 0));
    }, 0)
    : Math.max(0, Number(fallbackCourse?.attemptsUsed || 0));
  const maxAttempts = hasAssignmentContext && courseAttemptTests.length > 0
    ? Math.max(...courseAttemptTests.map((test) => Math.max(1, Number(test?.attempt_limit || coursePayload?.course_version?.attempt_limit || coursePayload?.default_attempt_limit || 3))))
    : Math.max(0, Number(
      fallbackCourse?.maxAttempts
      || coursePayload?.course_version?.attempt_limit
      || coursePayload?.default_attempt_limit
      || 0
    ));

  const lessonsCountWithTests = modulesData.reduce((sum, mod) => sum + (Array.isArray(mod?.lessons) ? mod.lessons.length : 0), 0);

  return {
    id: courseId,
    assignmentId: assignmentId || null,
    courseVersionId: Number(coursePayload?.course_version?.id || fallbackCourse?.courseVersionId || 0) || null,
    title: String(coursePayload?.title || fallbackCourse?.title || "Без названия"),
    category: String(coursePayload?.category || fallbackCourse?.category || "Без категории"),
    cover: visual.cover,
    coverUrl: versionCoverUrl || String(fallbackCourse?.coverUrl || "").trim(),
    color: visual.color,
    description: String(coursePayload?.description || fallbackCourse?.description || ""),
    skills: versionSkills.length ? versionSkills : (Array.isArray(fallbackCourse?.skills) ? fallbackCourse.skills : []),
    // Длительность — автоматическая сумма всех уроков + тестов
    duration: formatDurationLabel(durationSeconds),
    lessons: lessonsCountWithTests,
    modules: modulesData.length,
    progress: Math.max(0, Math.min(100, progressPercent)),
    deadline: courseDeadline,
    mandatory,
    status,
    rating: Number(fallbackCourse?.rating || 0),
    reviews: Number(fallbackCourse?.reviews || 0),
    passingScore: Number(coursePayload?.course_version?.pass_threshold || coursePayload?.default_pass_threshold || fallbackCourse?.passingScore || 80),
    maxAttempts,
    attemptsUsed: attemptsUsedTotal,
    hasCourseAttemptLimit,
    modules_data: modulesData,
    tests: testsRaw,
    assignment,
    // Marker that this course object came from a full detail payload
    // (POST /api/lms/courses/:id/open or admin detail) and therefore
    // carries modules/lessons data. Partial home-list courses don't set it,
    // which lets the cache logic distinguish "lightweight" from "full" entries.
    __detailLoaded: true,
  };
};

const flattenCourseLessons = (courseLike) => {
  const modules = Array.isArray(courseLike?.modules_data) ? courseLike.modules_data : [];
  const lessons = [];
  modules.forEach((moduleItem) => {
    const moduleLessons = Array.isArray(moduleItem?.lessons) ? moduleItem.lessons : [];
    moduleLessons.forEach((lessonItem) => lessons.push(lessonItem));
  });
  return lessons;
};

const mapApiQuestionTypeToView = (apiType) => {
  const normalized = String(apiType || "").toLowerCase();
  if (normalized === "true_false") return "bool";
  return normalized || "single";
};

const buildAnswerPayloadForApi = (question, answerValue) => {
  const qType = mapApiQuestionTypeToView(question?.type);
  if (qType === "multiple") {
    const optionIds = Array.isArray(answerValue) ? answerValue : [];
    return { option_ids: optionIds };
  }
  if (qType === "text") {
    return { text: String(answerValue || "") };
  }
  if (qType === "bool" || qType === "single") {
    if (answerValue == null || answerValue === "") return {};
    return { option_id: answerValue };
  }
  return { value: answerValue };
};

// ─── MAIN APP ─────────────────────────────────────────────────────────────────

const LMS_CATALOG_TABS = new Set(["available", "completed", "certificates", "notifications"]);
const LMS_ADMIN_TABS = new Set(["analytics", "employees", "courses"]);

const normalizeLmsCatalogTab = (value) => {
  const normalized = String(value || "").trim().toLowerCase();
  return LMS_CATALOG_TABS.has(normalized) ? normalized : "available";
};

const normalizeLmsAdminTab = (value) => {
  const normalized = String(value || "").trim().toLowerCase();
  return LMS_ADMIN_TABS.has(normalized) ? normalized : "analytics";
};

const buildLmsCatalogPath = (tab = "available") => {
  const normalizedTab = normalizeLmsCatalogTab(tab);
  return normalizedTab === "available" ? "/lms/catalog" : `/lms/catalog?tab=${encodeURIComponent(normalizedTab)}`;
};

const buildLmsAdminPath = (tab = "analytics") => {
  const normalizedTab = normalizeLmsAdminTab(tab);
  return normalizedTab === "analytics" ? "/lms/admin" : `/lms/admin?tab=${encodeURIComponent(normalizedTab)}`;
};

const buildLmsCoursePath = (courseId) => `/lms/course/${encodeURIComponent(String(courseId || ""))}`;

const buildLmsLessonPath = (courseId, lessonId) =>
  `${buildLmsCoursePath(courseId)}/lesson/${encodeURIComponent(String(lessonId || ""))}`;

const buildLmsBuilderPath = (courseId = null, options = {}) => {
  const normalizedCourseId = Number(courseId || 0) || null;
  const normalizedDraftVersionId = Number(options?.draftVersionId || 0) || null;
  const path = normalizedCourseId ? `/lms/builder/${normalizedCourseId}` : "/lms/builder";
  if (!normalizedDraftVersionId) return path;
  return `${path}?draftVersionId=${encodeURIComponent(String(normalizedDraftVersionId))}`;
};

const resolveLmsRouteState = (pathname = "") => {
  const lessonMatch = matchPath("/lms/course/:courseId/lesson/:lessonId", pathname);
  if (lessonMatch) {
    const courseId = Number(lessonMatch.params?.courseId || 0) || null;
    const lessonId = String(lessonMatch.params?.lessonId || "").trim() || null;
    if (!courseId || !lessonId) return null;
    return {
      view: "lesson",
      courseId,
      lessonId,
    };
  }

  const courseMatch = matchPath("/lms/course/:courseId", pathname);
  if (courseMatch) {
    const courseId = Number(courseMatch.params?.courseId || 0) || null;
    if (!courseId) return null;
    return {
      view: "course",
      courseId,
      lessonId: null,
    };
  }

  const builderMatch = matchPath("/lms/builder/:courseId", pathname);
  if (builderMatch) {
    const courseId = Number(builderMatch.params?.courseId || 0) || null;
    if (!courseId) return null;
    return {
      view: "builder",
      courseId,
      lessonId: null,
    };
  }

  if (pathname === "/lms" || pathname === "/lms/") {
    return { view: "catalog", courseId: null, lessonId: null };
  }

  if (matchPath("/lms/catalog", pathname)) {
    return { view: "catalog", courseId: null, lessonId: null };
  }

  if (matchPath("/lms/admin", pathname)) {
    return { view: "admin", courseId: null, lessonId: null };
  }

  if (matchPath("/lms/builder", pathname)) {
    return { view: "builder", courseId: null, lessonId: null };
  }

  return null;
};

const isModifiedRouteEvent = (event) => Boolean(
  event?.metaKey
  || event?.ctrlKey
  || event?.shiftKey
  || event?.altKey
  || event?.button === 1
);

export default function LmsView({ user, apiBaseUrl, withAccessTokenHeader, showToast }) {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const role = normalizeLmsRole(user?.role);
  const canUseLearnerApi = role === "operator" || role === "trainee";
  const canUseManagerApi = role === "sv" || role === "trainer" || role === "admin" || role === "super_admin";
  const isEditorRole = role === "trainer" || role === "admin" || role === "super_admin";
  const canDeleteCourses = role === "sv" || role === "admin" || role === "super_admin";
  const canGoCatalog = canUseLearnerApi;
  const apiRoot = String(apiBaseUrl || "").trim().replace(/\/+$/, "");
  const lmsClientSessionKey = useMemo(() => getOrCreateLmsClientSessionKey(), []);
  const routeState = useMemo(() => resolveLmsRouteState(location.pathname), [location.pathname]);
  const view = routeState?.view || (canGoCatalog ? "catalog" : "admin");
  const routeCourseId = routeState?.courseId || null;
  const routeLessonId = routeState?.lessonId || null;
  const catalogTab = normalizeLmsCatalogTab(searchParams.get("tab"));
  const adminTab = normalizeLmsAdminTab(searchParams.get("tab"));
  const builderInitialCourseId = view === "builder" ? routeCourseId : null;
  const builderInitialDraftVersionId = view === "builder"
    ? (Number(searchParams.get("draftVersionId") || 0) || null)
    : null;
  const routeBackTo = typeof location.state?.backTo === "string" ? location.state.backTo : "";
  // Grand-parent back-link (e.g. the catalog/admin URL when we are inside a
  // lesson). It is propagated one level deeper so that goBack from a lesson
  // returns to the course page while still letting the course page remember
  // the exact catalog/admin tab the user originally came from.
  const routeParentBackTo = typeof location.state?.parentBackTo === "string" ? location.state.parentBackTo : "";
  const [selectedCourse, setSelectedCourse] = useState(null);
  const [selectedLesson, setSelectedLesson] = useState(null);
  const [searchQuery, setSearchQuery] = useState("");
  const isAdmin = canUseManagerApi;
  const [quizView, setQuizView] = useState("intro");
  const [quizAnswers, setQuizAnswers] = useState({});
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const [courses, setCourses] = useState([]);
  const [certificates, setCertificates] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [markingAllNotificationsRead, setMarkingAllNotificationsRead] = useState(false);
  const [adminCourses, setAdminCourses] = useState([]);
  const [adminAnalytics, setAdminAnalytics] = useState(null);
  const [adminProgressRows, setAdminProgressRows] = useState([]);
  const [adminAttempts, setAdminAttempts] = useState([]);
  const [learners, setLearners] = useState([]);
  const [adminSelectedMonth, setAdminSelectedMonth] = useState(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  });
  const [loadingHome, setLoadingHome] = useState(false);
  const [loadingAdmin, setLoadingAdmin] = useState(false);
  const [busyCourseId, setBusyCourseId] = useState(null);
  const [homeError, setHomeError] = useState("");
  const [apiMode, setApiMode] = useState(false);
  const showToastRef = useRef(showToast);
  const withAccessTokenHeaderRef = useRef(withAccessTokenHeader);
  const routeLoadTokenRef = useRef(0);
  // Tracks which lesson's quiz state (view/answers) we have already
  // initialized to "intro". Prevents re-mounting of the quiz "result" view
  // back to "intro" when the route effect re-runs after post-finish data
  // refresh (e.g. loadLearnerDashboard identity changing).
  const quizInitializedLessonKeyRef = useRef(null);
  const homeLoadPromiseRef = useRef(null);
  const homeLoadedRef = useRef(false);
  const adminLoadPromisesRef = useRef(new Map());
  const adminLearningSessionsCacheRef = useRef(new Map());
  const adminLearningSessionsPromisesRef = useRef(new Map());
  const adminCacheRef = useRef({
    builderLoaded: false,
    coursesLoaded: false,
    courseAnalytics: new Set(),
    tabMonthCache: new Set(),
  });
  const courseCacheRef = useRef(new Map());
  const courseLoadPromisesRef = useRef(new Map());
  const lessonCacheRef = useRef(new Map());
  const lessonLoadPromisesRef = useRef(new Map());

  useEffect(() => {
    showToastRef.current = showToast;
  }, [showToast]);

  useEffect(() => {
    withAccessTokenHeaderRef.current = withAccessTokenHeader;
  }, [withAccessTokenHeader]);

  const emitToast = useCallback((message, type = "info") => {
    if (!message) return;
    const toastFn = showToastRef.current;
    if (typeof toastFn === "function") {
      toastFn(message, type);
      return;
    }
    if (type === "error") console.error(message);
    else console.log(message);
  }, []);

  const lmsRequest = useCallback(async (path, options = {}) => {
    if (!apiRoot) {
      throw new Error("LMS API base URL is not configured");
    }
    const url = `${apiRoot}${path.startsWith("/") ? path : `/${path}`}`;
    const method = String(options?.method || "GET").toUpperCase();
    const headers = { ...(options?.headers || {}) };
    let body = options?.body;

    if (body && !(body instanceof FormData) && typeof body !== "string") {
      body = JSON.stringify(body);
      if (!headers["Content-Type"]) {
        headers["Content-Type"] = "application/json";
      }
    }

    const headerFn = withAccessTokenHeaderRef.current;
    const finalHeaders = typeof headerFn === "function"
      ? headerFn(headers)
      : headers;

    const response = await fetch(url, {
      method,
      headers: finalHeaders,
      body,
      signal: options?.signal,
      keepalive: options?.keepalive === true,
      credentials: "include",
    });

    const contentType = String(response.headers.get("content-type") || "").toLowerCase();
    const isJson = contentType.includes("application/json");
    const payload = isJson ? await response.json() : null;

    if (!response.ok) {
      const errorText = payload?.error || `HTTP ${response.status}`;
      throw new Error(errorText);
    }
    return payload;
  }, [apiRoot]);

  const loadLearnerDashboard = useCallback(async (options = {}) => {
    const force = Boolean(options?.force);
    if (!apiRoot || !canUseLearnerApi) return null;
    if (!force && homeLoadedRef.current) {
      return {
        courses,
        certificates,
        notifications,
      };
    }
    if (homeLoadPromiseRef.current) {
      return homeLoadPromiseRef.current;
    }

    const loadPromise = (async () => {
      setLoadingHome(true);
      setHomeError("");
      try {
      const [homeRes, certRes, notifRes] = await Promise.all([
        lmsRequest("/api/lms/home"),
        lmsRequest("/api/lms/certificates"),
        lmsRequest("/api/lms/notifications?limit=100"),
      ]);

      const mappedCourses = (Array.isArray(homeRes?.courses) ? homeRes.courses : []).map(mapHomeCourseToView);
      const courseTitleById = new Map(
        mappedCourses.map((item) => [Number(item.id), item.title])
      );

      const mappedCertificates = (Array.isArray(certRes?.certificates) ? certRes.certificates : []).map((item, index) => {
        const visual = pickCourseVisual(item?.course_id || index, item?.course_id || "");
        return {
          id: Number(item?.id || index + 1),
          course: courseTitleById.get(Number(item?.course_id || 0)) || `Курс #${item?.course_id || "-"}`,
          hours: "—",
          date: item?.issued_at ? new Date(item.issued_at).toLocaleDateString("ru-RU") : "—",
          number: String(item?.certificate_number || `LMS-${item?.id || index + 1}`),
          color: visual.color,
          employee: String(user?.name || user?.login || "Сотрудник"),
          status: String(item?.status || "active"),
          scorePercent: item?.score_percent != null ? Number(item.score_percent) : null,
          verifyUrl: item?.verify_url || "",
        };
      });

      const mappedNotifications = (Array.isArray(notifRes?.notifications) ? notifRes.notifications : []).map((item) => {
        const rawType = String(item?.type || "").toLowerCase();
        let type = "assigned";
        if (rawType.includes("deadline")) type = "deadline";
        else if (rawType.includes("complete")) type = "completed";
        else if (rawType.includes("cert")) type = "certificate";
        return {
          id: Number(item?.id || 0),
          type,
          title: String(item?.title || "Уведомление LMS"),
          message: String(item?.message || ""),
          time: toRelativeTime(item?.created_at),
          read: Boolean(item?.is_read),
          createdAt: item?.created_at || null,
        };
      });

      setCourses(mappedCourses.length ? mappedCourses : []);
      setCertificates(mappedCertificates);
      setNotifications(mappedNotifications);
      homeLoadedRef.current = true;
      setApiMode(true);
      return {
        courses: mappedCourses.length ? mappedCourses : [],
        certificates: mappedCertificates,
        notifications: mappedNotifications,
      };
    } catch (error) {
      setHomeError(String(error?.message || "Не удалось загрузить данные LMS"));
      emitToast(`LMS: ${String(error?.message || "ошибка загрузки")}`, "error");
      homeLoadedRef.current = false;
      setApiMode(false);
      } finally {
        setLoadingHome(false);
      }
    })();

    homeLoadPromiseRef.current = loadPromise;
    try {
      return await loadPromise;
    } finally {
      if (homeLoadPromiseRef.current === loadPromise) {
        homeLoadPromiseRef.current = null;
      }
    }
  }, [apiRoot, canUseLearnerApi, lmsRequest, user?.name, user?.login, emitToast, courses, certificates, notifications]);

  const loadAdminData = useCallback(async (options = {}) => {
    const scope = String(options?.scope || "analytics").trim().toLowerCase();
    const month = String(options?.month || "").trim();
    const force = Boolean(options?.force);
    const normalizedCourseId = Number(options?.courseId || 0) || null;
    if (!apiRoot || !canUseManagerApi) return null;

    const cacheState = adminCacheRef.current;

    if (!force) {
      if (scope === "builder" && cacheState.builderLoaded) {
        return { adminCourses, learners };
      }
      if (scope === "course" && normalizedCourseId && cacheState.courseAnalytics.has(normalizedCourseId)) {
        return { progressRows: adminProgressRows, attempts: adminAttempts };
      }
      const tabKey = (scope === "analytics" || scope === "employees") ? `${scope}:${month}` : scope;
      if (cacheState.tabMonthCache.has(tabKey)) {
        return { adminCourses, analytics: adminAnalytics, progressRows: adminProgressRows, attempts: adminAttempts };
      }
    }

    const loadKey = scope === "course" && normalizedCourseId
      ? `course:${normalizedCourseId}`
      : (scope === "analytics" || scope === "employees") ? `${scope}:${month}` : scope;
    const existingPromise = adminLoadPromisesRef.current.get(loadKey);
    if (existingPromise) return existingPromise;

    const loadPromise = (async () => {
      setLoadingAdmin(true);
      try {
        const mq = month ? `month=${encodeURIComponent(month)}` : "";

        if (scope === "builder") {
          const [coursesRes, learnersRes] = await Promise.all([
            lmsRequest("/api/lms/admin/courses"),
            lmsRequest("/api/lms/admin/learners"),
          ]);
          const nextCourses = Array.isArray(coursesRes?.courses) ? coursesRes.courses : [];
          const nextLearners = Array.isArray(learnersRes?.learners) ? learnersRes.learners : [];
          setAdminCourses(nextCourses);
          setLearners(nextLearners);
          cacheState.builderLoaded = true;
          cacheState.coursesLoaded = true;
          setApiMode(true);
          return { adminCourses: nextCourses, learners: nextLearners };
        }

        if (scope === "course" && normalizedCourseId) {
          const [progressRes, attemptsRes] = await Promise.all([
            lmsRequest(`/api/lms/admin/progress?course_id=${normalizedCourseId}`).catch(() => ({ rows: [] })),
            lmsRequest(`/api/lms/admin/attempts?course_id=${normalizedCourseId}&limit=1000`).catch(() => ({ attempts: [] })),
          ]);
          const nextProgressRows = Array.isArray(progressRes?.rows) ? progressRes.rows : [];
          const nextAttempts = Array.isArray(attemptsRes?.attempts) ? attemptsRes.attempts : [];
          setAdminProgressRows(nextProgressRows);
          setAdminAttempts(nextAttempts);
          cacheState.courseAnalytics.add(normalizedCourseId);
          setApiMode(true);
          return { progressRows: nextProgressRows, attempts: nextAttempts };
        }

        if (scope === "analytics") {
          const [analyticsRes, coursesRes] = await Promise.all([
            lmsRequest(`/api/lms/admin/analytics${mq ? `?${mq}` : ""}`).catch(() => null),
            cacheState.coursesLoaded ? Promise.resolve(null) : lmsRequest("/api/lms/admin/courses").catch(() => null),
          ]);
          const nextAnalytics = analyticsRes && typeof analyticsRes === "object" ? analyticsRes : null;
          setAdminAnalytics(nextAnalytics);
          if (coursesRes) {
            setAdminCourses(Array.isArray(coursesRes?.courses) ? coursesRes.courses : []);
            cacheState.coursesLoaded = true;
          }
          cacheState.tabMonthCache.add(`analytics:${month}`);
          setApiMode(true);
          return { analytics: nextAnalytics };
        }

        if (scope === "employees") {
          const [progressRes, attemptsRes] = await Promise.all([
            lmsRequest(`/api/lms/admin/progress${mq ? `?${mq}` : ""}`).catch(() => ({ rows: [] })),
            lmsRequest(`/api/lms/admin/attempts?limit=1000${mq ? `&${mq}` : ""}`).catch(() => ({ attempts: [] })),
          ]);
          const nextProgressRows = Array.isArray(progressRes?.rows) ? progressRes.rows : [];
          const nextAttempts = Array.isArray(attemptsRes?.attempts) ? attemptsRes.attempts : [];
          setAdminProgressRows(nextProgressRows);
          setAdminAttempts(nextAttempts);
          if (!cacheState.coursesLoaded) {
            lmsRequest("/api/lms/admin/courses").then((res) => {
              if (Array.isArray(res?.courses)) { setAdminCourses(res.courses); cacheState.coursesLoaded = true; }
            }).catch(() => {});
          }
          cacheState.tabMonthCache.add(`employees:${month}`);
          setApiMode(true);
          return { progressRows: nextProgressRows, attempts: nextAttempts };
        }

        if (scope === "courses") {
          const [coursesRes, analyticsRes] = await Promise.all([
            lmsRequest("/api/lms/admin/courses").catch(() => null),
            cacheState.tabMonthCache.has(`analytics:${month}`) ? Promise.resolve(null)
              : lmsRequest(`/api/lms/admin/analytics${mq ? `?${mq}` : ""}`).catch(() => null),
          ]);
          if (coursesRes) {
            setAdminCourses(Array.isArray(coursesRes?.courses) ? coursesRes.courses : []);
            cacheState.coursesLoaded = true;
          }
          if (analyticsRes && typeof analyticsRes === "object") {
            setAdminAnalytics(analyticsRes);
            cacheState.tabMonthCache.add(`analytics:${month}`);
          }
          cacheState.tabMonthCache.add("courses");
          setApiMode(true);
          return { adminCourses };
        }

      } catch (error) {
        emitToast(`LMS admin: ${String(error?.message || "ошибка загрузки")}`, "error");
      } finally {
        setLoadingAdmin(false);
      }
    })();

    adminLoadPromisesRef.current.set(loadKey, loadPromise);
    try {
      return await loadPromise;
    } finally {
      if (adminLoadPromisesRef.current.get(loadKey) === loadPromise) {
        adminLoadPromisesRef.current.delete(loadKey);
      }
    }
  }, [
    apiRoot,
    canUseManagerApi,
    lmsRequest,
    emitToast,
    adminCourses,
    adminAnalytics,
    adminProgressRows,
    adminAttempts,
    learners,
  ]);

  const loadAdminLearningSessions = useCallback(async (options = {}) => {
    if (!apiRoot || !canUseManagerApi) return [];
    const params = new URLSearchParams();
    const courseId = Number(options?.courseId || 0);
    const userId = Number(options?.userId || 0);
    const assignmentId = Number(options?.assignmentId || 0);
    const limit = Math.max(1, Math.min(100, Number(options?.limit || 20) || 20));
    if (courseId > 0) params.set("course_id", String(courseId));
    if (userId > 0) params.set("user_id", String(userId));
    if (assignmentId > 0) params.set("assignment_id", String(assignmentId));
    params.set("limit", String(limit));
    const query = params.toString();
    const cacheKey = query || "all";
    if (adminLearningSessionsCacheRef.current.has(cacheKey)) {
      return adminLearningSessionsCacheRef.current.get(cacheKey);
    }
    const existingPromise = adminLearningSessionsPromisesRef.current.get(cacheKey);
    if (existingPromise) return existingPromise;
    const loadPromise = lmsRequest(`/api/lms/admin/learning-sessions${query ? `?${query}` : ""}`)
      .then((payload) => {
        const sessions = Array.isArray(payload?.sessions) ? payload.sessions : [];
        adminLearningSessionsCacheRef.current.set(cacheKey, sessions);
        return sessions;
      })
      .finally(() => {
        if (adminLearningSessionsPromisesRef.current.get(cacheKey) === loadPromise) {
          adminLearningSessionsPromisesRef.current.delete(cacheKey);
        }
      });
    adminLearningSessionsPromisesRef.current.set(cacheKey, loadPromise);
    return loadPromise;
  }, [apiRoot, canUseManagerApi, lmsRequest]);

  const getCourseCacheKey = useCallback((courseId) => {
    const normalizedCourseId = Number(courseId || 0) || 0;
    const scope = canUseLearnerApi ? "learner" : "manager";
    return `${scope}:${normalizedCourseId}`;
  }, [canUseLearnerApi]);

  const getLessonCacheKey = useCallback((courseId, lessonId) => (
    `${getCourseCacheKey(courseId)}:lesson:${String(lessonId || "").trim()}`
  ), [getCourseCacheKey]);

  const storeLessonInCache = useCallback((courseId, lessonLike) => {
    const normalizedCourseId = Number(courseId || 0) || null;
    const normalizedLessonId = String(lessonLike?.id || "").trim() || null;
    if (!normalizedCourseId || !normalizedLessonId) return null;

    const cacheKey = getLessonCacheKey(normalizedCourseId, normalizedLessonId);
    const previousLesson = lessonCacheRef.current.get(cacheKey) || {};
    const nextLesson = {
      ...previousLesson,
      ...lessonLike,
      id: lessonLike?.id ?? previousLesson?.id ?? normalizedLessonId,
      __detailLoaded: Boolean(lessonLike?.__detailLoaded || previousLesson?.__detailLoaded),
    };
    lessonCacheRef.current.set(cacheKey, nextLesson);
    return nextLesson;
  }, [getLessonCacheKey]);

  const mergeLessonIntoCourse = useCallback((courseLike, nextLesson) => {
    if (!courseLike || !nextLesson) return courseLike;
    let lessonWasUpdated = false;
    const nextModules = (Array.isArray(courseLike?.modules_data) ? courseLike.modules_data : []).map((moduleItem) => {
      const nextLessons = (Array.isArray(moduleItem?.lessons) ? moduleItem.lessons : []).map((lessonItem) => {
        if (String(lessonItem?.id) !== String(nextLesson?.id)) {
          return lessonItem;
        }
        lessonWasUpdated = true;
        return { ...lessonItem, ...nextLesson };
      });
      return lessonWasUpdated ? { ...moduleItem, lessons: nextLessons } : moduleItem;
    });
    if (!lessonWasUpdated) return courseLike;
    return { ...courseLike, modules_data: nextModules };
  }, []);

  const storeCourseInCache = useCallback((courseLike) => {
    const normalizedCourseId = Number(courseLike?.id || 0) || null;
    if (!normalizedCourseId) return null;
    const cacheKey = getCourseCacheKey(normalizedCourseId);
    const previousCourse = courseCacheRef.current.get(cacheKey) || {};
    const nextCourse = { ...previousCourse, ...courseLike, id: normalizedCourseId };
    courseCacheRef.current.set(cacheKey, nextCourse);
    return nextCourse;
  }, [getCourseCacheKey]);

  const invalidateCourseCache = useCallback((courseId = null) => {
    const normalizedCourseId = Number(courseId || 0) || null;
    if (!normalizedCourseId) {
      courseCacheRef.current.clear();
      courseLoadPromisesRef.current.clear();
      lessonCacheRef.current.clear();
      lessonLoadPromisesRef.current.clear();
      return;
    }

    const courseKey = getCourseCacheKey(normalizedCourseId);
    courseCacheRef.current.delete(courseKey);
    courseLoadPromisesRef.current.delete(courseKey);

    const lessonKeyPrefix = `${courseKey}:lesson:`;
    Array.from(lessonCacheRef.current.keys()).forEach((key) => {
      if (key.startsWith(lessonKeyPrefix)) {
        lessonCacheRef.current.delete(key);
      }
    });
    Array.from(lessonLoadPromisesRef.current.keys()).forEach((key) => {
      if (key.startsWith(lessonKeyPrefix)) {
        lessonLoadPromisesRef.current.delete(key);
      }
    });
  }, [getCourseCacheKey]);

  const invalidateAdminCache = useCallback((options = {}) => {
    const cacheState = adminCacheRef.current;
    cacheState.builderLoaded = false;

    const normalizedCourseId = Number(options?.courseId || 0) || null;
    if (normalizedCourseId) {
      cacheState.courseAnalytics.delete(normalizedCourseId);
      return;
    }

    cacheState.courseAnalytics = new Set();
    cacheState.tabMonthCache = new Set();
    cacheState.coursesLoaded = false;
  }, []);

  const hydrateLearnerLessonDetail = useCallback((lesson, detail) => {
    const lessonPayload = detail?.lesson || {};
    const progressPayload = detail?.progress || {};
    const materialsPayload = Array.isArray(detail?.materials) ? detail.materials : (lesson?.materials || []);
    const linkedTestPayload = detail?.linked_test && typeof detail.linked_test === "object" ? detail.linked_test : null;
    const combinedVideoProgressPayload = Array.isArray(detail?.combined_video_progress) ? detail.combined_video_progress : [];
    const mappedCombinedVideoProgress = combinedVideoProgressPayload.reduce((acc, item) => {
      const materialId = Number(item?.material_id || 0);
      if (materialId <= 0) return acc;
      acc[String(materialId)] = clampLmsProgress(item?.progress_ratio);
      return acc;
    }, {});
    const linkedTestBestScoreRaw = linkedTestPayload
      ? (linkedTestPayload.best_score
        ?? linkedTestPayload.max_score_percent
        ?? linkedTestPayload.score_percent
        ?? linkedTestPayload.score
        ?? null)
      : null;
    const linkedTestBestScore = linkedTestBestScoreRaw != null ? Number(linkedTestBestScoreRaw) : null;
    const linkedTestAttemptLimit = linkedTestPayload
      ? Math.max(1, Number(linkedTestPayload.attempt_limit || 3))
      : 0;
    const linkedTestAttemptsUsed = linkedTestPayload
      ? Math.max(0, Number(linkedTestPayload.attempts_used || 0))
      : 0;
    let linkedTestStatus = "not_started";
    if (linkedTestPayload) {
      if (linkedTestPayload.passed_any) {
        linkedTestStatus = "completed";
      } else if (linkedTestAttemptsUsed > 0) {
        linkedTestStatus = linkedTestAttemptsUsed >= linkedTestAttemptLimit ? "test_failed" : "in_progress";
      }
    }
    const mappedLinkedTest = linkedTestPayload ? {
      id: `test-${linkedTestPayload.id}`,
      apiTestId: Number(linkedTestPayload.id || 0),
      title: String(linkedTestPayload.title || "Тест"),
      description: String(linkedTestPayload.description || ""),
      type: "quiz",
      duration: Number(linkedTestPayload.time_limit_minutes || 0) > 0
        ? `${Math.max(1, Number(linkedTestPayload.time_limit_minutes || 0))} мин`
        : "20 мин",
      durationSeconds: Number(linkedTestPayload.time_limit_minutes || 0) > 0
        ? Math.max(1, Number(linkedTestPayload.time_limit_minutes || 0)) * 60
        : 20 * 60,
      timeLimitMinutes: Number(linkedTestPayload.time_limit_minutes || 0) || null,
      status: linkedTestStatus,
      locked: !Boolean(linkedTestPayload.content_completed),
      requiresTest: true,
      maxAttempts: linkedTestAttemptLimit,
      attemptsUsed: linkedTestAttemptsUsed,
      score: linkedTestBestScore,
      isFinal: Boolean(linkedTestPayload.is_final),
      passingScore: Number(linkedTestPayload.pass_threshold || 80),
      questionCount: Math.max(0, Number(linkedTestPayload.question_count || 0)),
      canStart: Boolean(linkedTestPayload.can_start),
    } : null;
    const inferredType = inferLessonType({
      ...lessonPayload,
      type: lessonPayload?.lesson_type || lesson?.type,
      materials: materialsPayload,
    });
    return {
      ...lesson,
      __detailLoaded: true,
      title: lessonPayload?.title || lesson?.title,
      description: lessonPayload?.description || lesson?.description,
      type: inferredType,
      duration: formatDurationLabel(lessonPayload?.duration_seconds || lesson?.durationSeconds || 0),
      durationSeconds: Number(lessonPayload?.duration_seconds || lesson?.durationSeconds || 0),
      materials: materialsPayload,
      combinedBlocks: inferredType === "combined"
        ? (
          Array.isArray(lessonPayload?.blocks) && lessonPayload.blocks.length > 0
            ? lessonPayload.blocks
            : materialsPayload
              .filter((materialItem) => {
                const materialType = String(materialItem?.material_type || materialItem?.type || "").toLowerCase();
                return materialType === "text" || materialType === "video";
              })
              .map((materialItem, blockIndex) => ({
                id: Number(materialItem?.id || 0) || `block-${lesson?.apiLessonId || lesson?.id || "x"}-${blockIndex + 1}`,
                type: String(materialItem?.material_type || materialItem?.type || "text").toLowerCase(),
                title: String(materialItem?.title || `Блок ${blockIndex + 1}`),
                content_text: materialItem?.content_text || "",
                content_url: materialItem?.content_url || materialItem?.url || materialItem?.signed_url || "",
                url: materialItem?.url || materialItem?.signed_url || materialItem?.content_url || "",
                signed_url: materialItem?.signed_url || materialItem?.url || materialItem?.content_url || "",
                metadata: materialItem?.metadata && typeof materialItem.metadata === "object" ? materialItem.metadata : {},
                position: Number(materialItem?.position || blockIndex + 1),
              }))
        )
        : [],
      combinedTest: inferredType === "combined" ? mappedLinkedTest : null,
      combinedHasTest: inferredType === "combined" && Boolean(mappedLinkedTest),
      combinedVideoProgress: inferredType === "combined" ? mappedCombinedVideoProgress : {},
      contentCompleted: String(progressPayload?.status || "").toLowerCase() === "completed",
      completionRatio: Number(progressPayload?.completion_ratio || lesson?.completionRatio || 0),
      status: String(progressPayload?.status || lesson?.status || "not_started"),
      apiProgress: progressPayload,
      apiSession: detail?.session || null,
      antiCheat: detail?.anti_cheat || null,
    };
  }, []);

  const ensureCourseLoaded = useCallback(async (courseId, options = {}) => {
    const normalizedCourseId = Number(courseId || 0) || null;
    if (!normalizedCourseId) return null;
    const cacheKey = getCourseCacheKey(normalizedCourseId);
    if (!options?.force) {
      const cachedCourse = courseCacheRef.current.get(cacheKey);
      // Only trust the cache when we have a full course detail payload
      // (marked with __detailLoaded by mapCourseDetailToView). Lightweight
      // entries produced by openCourse / the home dashboard mapper have
      // modules_data === [] and must not short-circuit the detail fetch,
      // otherwise the course page renders with no modules/lessons and the
      // "Продолжить" button stays disabled until a hard reload.
      if (cachedCourse && cachedCourse.__detailLoaded && Array.isArray(cachedCourse?.modules_data)) {
        return cachedCourse;
      }
    }

    const inFlightPromise = courseLoadPromisesRef.current.get(cacheKey);
    if (inFlightPromise) {
      return inFlightPromise;
    }

    const requestPromise = (async () => {
      const fallbackCourse = canUseLearnerApi
        ? (courses.find((item) => Number(item?.id || 0) === normalizedCourseId) || { id: normalizedCourseId })
        : { id: normalizedCourseId };

      let nextCourse = fallbackCourse;
      if (canUseLearnerApi) {
        const openQuery = options?.skipStart ? "?skip_start=1" : "";
        const detail = await lmsRequest(`/api/lms/courses/${normalizedCourseId}/open${openQuery}`, { method: "POST" });
        if (!detail?.course) {
          throw new Error("Не удалось загрузить курс");
        }
        nextCourse = mapCourseDetailToView(detail.course, fallbackCourse);
        setCourses((prev) => {
          const exists = prev.some((item) => Number(item?.id || 0) === normalizedCourseId);
          return exists
            ? prev.map((item) => (Number(item?.id || 0) === normalizedCourseId ? { ...item, ...nextCourse } : item))
            : [...prev, nextCourse];
        });
      } else {
        const detail = await lmsRequest(`/api/lms/admin/courses?course_id=${normalizedCourseId}`);
        if (!detail?.course) {
          throw new Error("Не удалось загрузить курс");
        }
        nextCourse = mapCourseDetailToView(detail.course, fallbackCourse);
      }

      return storeCourseInCache(nextCourse);
    })();

    courseLoadPromisesRef.current.set(cacheKey, requestPromise);
    try {
      return await requestPromise;
    } finally {
      if (courseLoadPromisesRef.current.get(cacheKey) === requestPromise) {
        courseLoadPromisesRef.current.delete(cacheKey);
      }
    }
  }, [canUseLearnerApi, courses, getCourseCacheKey, lmsRequest, storeCourseInCache]);

  const ensureLessonLoaded = useCallback(async (courseId, lessonId, options = {}) => {
    const normalizedCourseId = Number(courseId || 0) || null;
    const normalizedLessonId = String(lessonId || "").trim();
    if (!normalizedCourseId || !normalizedLessonId) return null;
    const cacheKey = getLessonCacheKey(normalizedCourseId, normalizedLessonId);
    if (!options?.force) {
      const cachedLesson = lessonCacheRef.current.get(cacheKey);
      const canReuseCachedLesson = Boolean(
        cachedLesson
        && (
          !canUseLearnerApi
          || !cachedLesson?.apiLessonId
          || cachedLesson?.type === "quiz"
          || cachedLesson?.__detailLoaded
        )
      );
      if (canReuseCachedLesson) return cachedLesson;
    }

    const inFlightPromise = lessonLoadPromisesRef.current.get(cacheKey);
    if (inFlightPromise) {
      return inFlightPromise;
    }

    const requestPromise = (async () => {
      const baseCourse = options?.course || courseCacheRef.current.get(getCourseCacheKey(normalizedCourseId));
      const baseLesson = flattenCourseLessons(baseCourse).find((item) => String(item?.id) === normalizedLessonId) || null;
      if (!baseLesson) {
        throw new Error("Урок не найден");
      }

      if (!canUseLearnerApi || !baseLesson?.apiLessonId || baseLesson?.type === "quiz") {
        return storeLessonInCache(normalizedCourseId, baseLesson);
      }

      const detail = await lmsRequest(
        appendClientSessionKeyToPath(`/api/lms/lessons/${baseLesson.apiLessonId}`, lmsClientSessionKey)
      );
      const nextLesson = hydrateLearnerLessonDetail(baseLesson, detail);
      storeLessonInCache(normalizedCourseId, nextLesson);

      const cachedCourse = courseCacheRef.current.get(getCourseCacheKey(normalizedCourseId)) || baseCourse;
      const nextCourse = mergeLessonIntoCourse(cachedCourse, nextLesson);
      storeCourseInCache(nextCourse);
      return nextLesson;
    })();

    lessonLoadPromisesRef.current.set(cacheKey, requestPromise);
    try {
      return await requestPromise;
    } finally {
      if (lessonLoadPromisesRef.current.get(cacheKey) === requestPromise) {
        lessonLoadPromisesRef.current.delete(cacheKey);
      }
    }
  }, [
    canUseLearnerApi,
    getCourseCacheKey,
    getLessonCacheKey,
    hydrateLearnerLessonDetail,
    lmsClientSessionKey,
    storeLessonInCache,
    lmsRequest,
    mergeLessonIntoCourse,
    storeCourseInCache,
  ]);

  useEffect(() => {
    if (routeState) return;
    navigate(canGoCatalog ? "/lms" : "/lms/admin", { replace: true });
  }, [routeState, canGoCatalog, navigate]);

  useEffect(() => {
    if (!routeState) return;
    if (view === "catalog" && !canGoCatalog) {
      navigate("/lms/admin", { replace: true });
      return;
    }
    if ((view === "admin" || view === "builder") && !canUseManagerApi) {
      navigate(canGoCatalog ? "/lms" : "/lms/admin", { replace: true });
    }
  }, [routeState, view, canGoCatalog, canUseManagerApi, navigate]);

  useEffect(() => {
    if (view !== "catalog" || location.pathname !== "/lms/catalog") return;
    const rawTab = String(searchParams.get("tab") || "").trim().toLowerCase();
    if (!rawTab) return;
    const normalizedTab = normalizeLmsCatalogTab(rawTab);
    if (normalizedTab !== rawTab) {
      navigate(buildLmsCatalogPath(normalizedTab), { replace: true, state: location.state });
    }
  }, [view, location.pathname, location.state, navigate, searchParams]);

  useEffect(() => {
    if (view !== "admin") return;
    const rawTab = String(searchParams.get("tab") || "").trim().toLowerCase();
    if (!rawTab) return;
    const normalizedTab = normalizeLmsAdminTab(rawTab);
    if (normalizedTab !== rawTab) {
      navigate(buildLmsAdminPath(normalizedTab), { replace: true, state: location.state });
    }
  }, [view, location.state, navigate, searchParams]);

  useEffect(() => {
    let cancelled = false;
    const loadToken = routeLoadTokenRef.current + 1;
    routeLoadTokenRef.current = loadToken;
    const isCurrentRoute = () => !cancelled && routeLoadTokenRef.current === loadToken;

    const syncSelectedCourse = (courseLike) => {
      if (!isCurrentRoute()) return;
      setSelectedCourse(courseLike || null);
    };

    const syncSelectedLesson = (lessonLike) => {
      if (!isCurrentRoute()) return;
      setSelectedLesson(lessonLike || null);
    };

    if (view === "catalog") {
      setBusyCourseId(null);
      syncSelectedCourse(null);
      syncSelectedLesson(null);
      quizInitializedLessonKeyRef.current = null;
      if (canUseLearnerApi) {
        void loadLearnerDashboard();
      }
      return () => {
        cancelled = true;
      };
    }

    if (view === "admin") {
      setBusyCourseId(null);
      syncSelectedCourse(null);
      syncSelectedLesson(null);
      quizInitializedLessonKeyRef.current = null;
      if (canUseManagerApi) {
        void loadAdminData({ scope: adminTab, month: adminSelectedMonth });
      }
      return () => {
        cancelled = true;
      };
    }

    if (view === "builder") {
      setBusyCourseId(null);
      syncSelectedCourse(null);
      syncSelectedLesson(null);
      quizInitializedLessonKeyRef.current = null;
      if (canUseManagerApi) {
        void loadAdminData({ scope: "builder" });
      }
      return () => {
        cancelled = true;
      };
    }

    if (view === "course" && routeCourseId) {
      syncSelectedLesson(null);
      quizInitializedLessonKeyRef.current = null;
      void (async () => {
        try {
          const courseLike = await ensureCourseLoaded(routeCourseId);
          if (!isCurrentRoute()) return;
          syncSelectedCourse(courseLike);
          setBusyCourseId(null);
          if (canUseManagerApi) {
            void loadAdminData({ scope: "course", courseId: routeCourseId });
          }
        } catch (error) {
          emitToast(`Не удалось открыть курс: ${String(error?.message || "ошибка")}`, "error");
          if (isCurrentRoute()) {
            setBusyCourseId(null);
            navigate(canGoCatalog ? buildLmsCatalogPath(catalogTab) : buildLmsAdminPath(adminTab), { replace: true });
          }
        }
      })();
      return () => {
        cancelled = true;
      };
    }

    if (view === "lesson" && routeCourseId && routeLessonId) {
      void (async () => {
        try {
          const courseLike = await ensureCourseLoaded(routeCourseId);
          if (!isCurrentRoute()) return;
          syncSelectedCourse(courseLike);
          setBusyCourseId(null);
          const baseLesson = flattenCourseLessons(courseLike).find((item) => String(item?.id) === String(routeLessonId));
          if (!baseLesson) {
            throw new Error("Урок не найден в курсе");
          }
          syncSelectedLesson(baseLesson);
          // Only reset quiz UI state on a genuine lesson change. If the effect
          // re-runs for the same lesson (e.g. after a post-quiz data refresh
          // bumps loadLearnerDashboard identity), preserve the current
          // quiz view so the user keeps seeing the result screen instead of
          // being snapped back to the intro.
          const lessonKey = `${routeCourseId}:${routeLessonId}`;
          if (quizInitializedLessonKeyRef.current !== lessonKey) {
            quizInitializedLessonKeyRef.current = lessonKey;
            setQuizView("intro");
            setQuizAnswers({});
          }
          const hydratedLesson = await ensureLessonLoaded(routeCourseId, routeLessonId, { course: courseLike });
          if (!isCurrentRoute()) return;
          const cachedCourse = courseCacheRef.current.get(getCourseCacheKey(routeCourseId)) || courseLike;
          syncSelectedCourse(cachedCourse);
          syncSelectedLesson(hydratedLesson);
          if (canUseManagerApi) {
            void loadAdminData({ scope: "course", courseId: routeCourseId });
          }
        } catch (error) {
          emitToast(`Не удалось открыть урок: ${String(error?.message || "ошибка")}`, "error");
          if (isCurrentRoute()) {
            // When bouncing the user back to the course page on lesson load
            // error we must use the *grand-parent* back-link (catalog/admin),
            // not the lesson's own routeBackTo — which now points to the
            // course page itself and would create a back-loop from course.
            const listFallback = canGoCatalog ? buildLmsCatalogPath(catalogTab) : buildLmsAdminPath(adminTab);
            navigate(buildLmsCoursePath(routeCourseId), {
              replace: true,
              state: { backTo: routeParentBackTo || listFallback },
            });
          }
        }
      })();
    }

    return () => {
      cancelled = true;
    };
  }, [
    view,
    routeCourseId,
    routeLessonId,
    routeBackTo,
    routeParentBackTo,
    canGoCatalog,
    canUseLearnerApi,
    canUseManagerApi,
    catalogTab,
    adminTab,
    adminSelectedMonth,
    loadLearnerDashboard,
    loadAdminData,
    ensureCourseLoaded,
    ensureLessonLoaded,
    emitToast,
    navigate,
    getCourseCacheKey,
  ]);

  const handleDeleteAdminCourse = useCallback(async (courseLike) => {
    if (!canUseManagerApi) {
      emitToast("Недостаточно прав для удаления курса", "error");
      return false;
    }
    if (!canDeleteCourses) {
      emitToast("Удаление курсов недоступно для вашей роли", "error");
      return false;
    }
    if (typeof lmsRequest !== "function") {
      emitToast("LMS API не подключен", "error");
      return false;
    }

    const courseId = Number(courseLike?.id || courseLike || 0);
    if (!courseId) {
      emitToast("Некорректный курс", "error");
      return false;
    }

    try {
      await lmsRequest(`/api/lms/admin/courses/${courseId}`, { method: "DELETE" });
      invalidateAdminCache({ courseId });
      invalidateCourseCache(courseId);
      setAdminCourses((prev) => prev.filter((item) => Number(item?.id || 0) !== courseId));
      setAdminProgressRows((prev) => prev.filter((item) => Number(item?.course_id || 0) !== courseId));
      setAdminAttempts((prev) => prev.filter((item) => Number(item?.course_id || 0) !== courseId));
      emitToast("Курс и его файлы в GCS удалены", "success");
      await loadAdminData({ scope: "courses", force: true });
      return true;
    } catch (error) {
      emitToast(`Не удалось удалить курс: ${String(error?.message || "ошибка")}`, "error");
      return false;
    }
  }, [canUseManagerApi, canDeleteCourses, lmsRequest, emitToast, invalidateAdminCache, invalidateCourseCache, loadAdminData]);

  const handleArchiveAdminCourse = useCallback(async (courseLike) => {
    if (!canUseManagerApi) {
      emitToast("Недостаточно прав для архивирования курса", "error");
      return false;
    }
    if (typeof lmsRequest !== "function") {
      emitToast("LMS API не подключен", "error");
      return false;
    }

    const courseId = Number(courseLike?.id || courseLike || 0);
    if (!courseId) {
      emitToast("Некорректный курс", "error");
      return false;
    }

    try {
      await lmsRequest("/api/lms/admin/courses", {
        method: "PATCH",
        body: {
          course_id: courseId,
          status: "archived",
        },
      });
      invalidateAdminCache({ courseId });
      invalidateCourseCache(courseId);
      emitToast("Курс архивирован и скрыт из LMS сотрудников", "success");
      await loadAdminData({ scope: "courses", force: true });
      return true;
    } catch (error) {
      emitToast(`Не удалось архивировать курс: ${String(error?.message || "ошибка")}`, "error");
      return false;
    }
  }, [canUseManagerApi, lmsRequest, emitToast, invalidateAdminCache, invalidateCourseCache, loadAdminData]);

  const handleRestoreAdminCourse = useCallback(async (courseLike) => {
    if (!canUseManagerApi) {
      emitToast("Недостаточно прав для восстановления курса", "error");
      return false;
    }
    if (typeof lmsRequest !== "function") {
      emitToast("LMS API не подключен", "error");
      return false;
    }

    const courseId = Number(courseLike?.id || courseLike || 0);
    if (!courseId) {
      emitToast("Некорректный курс", "error");
      return false;
    }

    try {
      await lmsRequest("/api/lms/admin/courses", {
        method: "PATCH",
        body: {
          course_id: courseId,
          status: "published",
        },
      });
      invalidateAdminCache({ courseId });
      invalidateCourseCache(courseId);
      emitToast("Курс восстановлен из архива", "success");
      await loadAdminData({ scope: "courses", force: true });
      return true;
    } catch (error) {
      emitToast(`Не удалось восстановить курс: ${String(error?.message || "ошибка")}`, "error");
      return false;
    }
  }, [canUseManagerApi, lmsRequest, emitToast, invalidateAdminCache, invalidateCourseCache, loadAdminData]);

  const handleAssignAdminCourseToEmployee = useCallback(async ({ courseId, userId, dueDate, employeeName, courseTitle }) => {
    if (!canUseManagerApi) {
      emitToast("Недостаточно прав для назначения курса", "error");
      return false;
    }
    if (typeof lmsRequest !== "function") {
      emitToast("LMS API не подключен", "error");
      return false;
    }

    const normalizedCourseId = Number(courseId || 0);
    const normalizedUserId = Number(userId || 0);
    if (!normalizedCourseId || !normalizedUserId) {
      emitToast("Некорректные параметры назначения", "error");
      return false;
    }

    try {
      await lmsRequest(`/api/lms/admin/courses/${normalizedCourseId}/assignments`, {
        method: "POST",
        body: {
          user_ids: [normalizedUserId],
          due_at: dueDate ? `${dueDate} 23:59:59` : null,
        },
      });
      invalidateAdminCache({ courseId: normalizedCourseId });
      invalidateCourseCache(normalizedCourseId);
      const employeeLabel = String(employeeName || `#${normalizedUserId}`).trim();
      const courseLabel = String(courseTitle || `#${normalizedCourseId}`).trim();
      emitToast(`Курс «${courseLabel}» назначен сотруднику ${employeeLabel}`, "success");
      await loadAdminData({ scope: "dashboard", force: true });
      return true;
    } catch (error) {
      emitToast(`Не удалось назначить курс: ${String(error?.message || "ошибка")}`, "error");
      return false;
    }
  }, [canUseManagerApi, lmsRequest, emitToast, invalidateAdminCache, invalidateCourseCache, loadAdminData]);

  const markNotificationRead = useCallback(async (notificationId) => {
    setNotifications((prev) => prev.map((item) => (item.id === notificationId ? { ...item, read: true } : item)));
    if (!apiRoot || !canUseLearnerApi) return;
    try {
      await lmsRequest(`/api/lms/notifications/${notificationId}/read`, { method: "POST" });
    } catch (error) {
      emitToast(String(error?.message || "Не удалось отметить уведомление"), "error");
    }
  }, [
    apiRoot,
    canUseLearnerApi,
    lmsRequest,
    emitToast,
  ]);

  const markAllNotificationsRead = useCallback(async () => {
    if (markingAllNotificationsRead) return;
    const unreadIds = (Array.isArray(notifications) ? notifications : [])
      .filter((item) => !item?.read)
      .map((item) => Number(item?.id || 0))
      .filter((id) => id > 0);

    if (unreadIds.length === 0) return;

    setNotifications((prev) => prev.map((item) => (item?.read ? item : { ...item, read: true })));

    if (!apiRoot || !canUseLearnerApi) return;

    setMarkingAllNotificationsRead(true);
    try {
      const results = await Promise.allSettled(
        unreadIds.map((notificationId) =>
          lmsRequest(`/api/lms/notifications/${notificationId}/read`, { method: "POST" })
        )
      );
      const failedCount = results.filter((result) => result.status === "rejected").length;
      if (failedCount > 0) {
        emitToast(`Не удалось отметить ${failedCount} уведомл.`, "error");
        await loadLearnerDashboard({ force: true });
      }
    } finally {
      setMarkingAllNotificationsRead(false);
    }
  }, [
    markingAllNotificationsRead,
    notifications,
    apiRoot,
    canUseLearnerApi,
    lmsRequest,
    emitToast,
    loadLearnerDashboard,
  ]);

  const downloadCertificate = useCallback(async (certificate) => {
    if (!certificate?.id || !apiRoot || !canUseLearnerApi) return;
    try {
      const headerFn = withAccessTokenHeaderRef.current;
      const headers = typeof headerFn === "function" ? headerFn({}) : {};
      const response = await fetch(`${apiRoot}/api/lms/certificates/${certificate.id}/download`, {
        method: "GET",
        headers,
        credentials: "include",
      });
      if (!response.ok) {
        let errorText = `HTTP ${response.status}`;
        try {
          const payload = await response.json();
          errorText = payload?.error || errorText;
        } catch (_) {
          // ignore JSON parse errors
        }
        throw new Error(errorText);
      }
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = `${certificate.number || `certificate-${certificate.id}`}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (error) {
      emitToast(`Не удалось скачать сертификат: ${String(error?.message || "ошибка")}`, "error");
    }
  }, [apiRoot, canUseLearnerApi, emitToast]);

  const openCourse = useCallback((course, event = null) => {
    if (!course?.id) return;
    const targetPath = buildLmsCoursePath(course.id);
    storeCourseInCache(course);
    setBusyCourseId(Number(course.id));

    if (isModifiedRouteEvent(event)) {
      window.open(targetPath, "_blank", "noopener,noreferrer");
      setBusyCourseId(null);
      return;
    }

    navigate(targetPath, {
      state: {
        backTo: `${location.pathname}${location.search}`,
      },
    });
  }, [location.pathname, location.search, navigate, storeCourseInCache]);

  const refreshSelectedCourse = useCallback(async () => {
    const targetCourseId = Number(routeCourseId || selectedCourse?.id || 0) || null;
    if (!targetCourseId) return null;

    invalidateCourseCache(targetCourseId);
    const refreshedCourse = await ensureCourseLoaded(targetCourseId, { force: true, skipStart: true });
    if (refreshedCourse) {
      setSelectedCourse(refreshedCourse);
      if (routeLessonId) {
        const refreshedLesson = flattenCourseLessons(refreshedCourse).find((item) => String(item?.id) === String(routeLessonId));
        if (refreshedLesson) {
          storeLessonInCache(targetCourseId, refreshedLesson);
          setSelectedLesson(refreshedLesson);
        }
      }
    }

    return refreshedCourse;
  }, [
    ensureCourseLoaded,
    invalidateCourseCache,
    routeCourseId,
    routeLessonId,
    selectedCourse,
    storeLessonInCache,
  ]);

  const openLesson = useCallback((lesson, event = null) => {
    if (!lesson) return;

    const activeCourseId = Number(routeCourseId || selectedCourse?.id || 0) || null;
    const normalizedLessonId = String(lesson?.id || "").trim();
    if (!activeCourseId || !normalizedLessonId) return;

    storeLessonInCache(activeCourseId, lesson);
    const cachedCourse = courseCacheRef.current.get(getCourseCacheKey(activeCourseId)) || selectedCourse;
    if (cachedCourse) {
      storeCourseInCache(mergeLessonIntoCourse(cachedCourse, lesson));
    }

    const targetPath = buildLmsLessonPath(activeCourseId, normalizedLessonId);
    // A lesson's immediate parent is always its course page. Previously we
    // fell back to `routeBackTo`, which meant that when the user opened a
    // lesson from the course page (whose routeBackTo was the catalog URL),
    // the lesson's backTo became the catalog URL — causing goBack from the
    // lesson to jump past the course page straight to the catalog.
    const backTo = buildLmsCoursePath(activeCourseId);
    // Preserve the grand-parent (catalog/admin) URL so goBack chain still
    // returns course → original catalog tab.
    const parentBackTo = routeParentBackTo || routeBackTo || "";
    if (isModifiedRouteEvent(event)) {
      window.open(targetPath, "_blank", "noopener,noreferrer");
      return;
    }

    navigate(targetPath, {
      state: parentBackTo ? { backTo, parentBackTo } : { backTo },
    });
  }, [
    getCourseCacheKey,
    mergeLessonIntoCourse,
    navigate,
    routeBackTo,
    routeParentBackTo,
    routeCourseId,
    selectedCourse,
    storeCourseInCache,
    storeLessonInCache,
  ]);

  const handleCompleteLesson = useCallback(async (lesson) => {
    if (!apiRoot || !canUseLearnerApi || !lesson?.apiLessonId) return false;
    try {
      await lmsRequest(`/api/lms/lessons/${lesson.apiLessonId}/complete`, {
        method: "POST",
        body: {
          client_session_key: lmsClientSessionKey || undefined,
        },
      });
      emitToast("Урок отмечен как завершенный", "success");
      setSelectedLesson((prev) => (
        prev
          ? { ...prev, status: "completed", completionRatio: 100, contentCompleted: true }
          : prev
      ));
      const refreshedCourse = await refreshSelectedCourse();
      await loadLearnerDashboard({ force: true });
      if (refreshedCourse) {
        const refreshedCurrentLesson = flattenCourseLessons(refreshedCourse).find((item) => String(item?.id) === String(lesson.id));
        if (refreshedCurrentLesson) {
          setSelectedLesson(refreshedCurrentLesson);
        }
      }
      return true;
    } catch (error) {
      emitToast(`Не удалось завершить урок: ${String(error?.message || "ошибка")}`, "error");
      return false;
    }
  }, [apiRoot, canUseLearnerApi, lmsRequest, emitToast, refreshSelectedCourse, loadLearnerDashboard, lmsClientSessionKey]);

  const handleQuizFinished = useCallback(async () => {
    try {
      await refreshSelectedCourse();
      await loadLearnerDashboard({ force: true });
    } catch (_) {
      // ignore refresh errors here
    }
  }, [refreshSelectedCourse, loadLearnerDashboard]);

  const openBuilder = useCallback((courseId = null, options = {}) => {
    const targetPath = buildLmsBuilderPath(courseId, options);
    navigate(targetPath, {
      state: {
        backTo: `${location.pathname}${location.search}`,
      },
    });
  }, [location.pathname, location.search, navigate]);

  const goBack = useCallback(() => {
    const listPath = canGoCatalog ? buildLmsCatalogPath(catalogTab) : buildLmsAdminPath(adminTab);

    // From a lesson we always return to the parent course page. We also
    // forward the grand-parent (catalog/admin) back-link so the user can
    // unwind the full chain: lesson → course → original catalog tab.
    if (view === "lesson" && routeCourseId) {
      const coursePath = buildLmsCoursePath(routeCourseId);
      const target = routeBackTo || coursePath;
      const nextState = routeParentBackTo ? { backTo: routeParentBackTo } : undefined;
      navigate(target, nextState ? { state: nextState } : undefined);
      return;
    }

    let fallbackPath = listPath;
    if ((view === "builder" || view === "admin") && canUseManagerApi) {
      fallbackPath = listPath;
    }
    navigate(routeBackTo || fallbackPath);
  }, [
    view,
    routeCourseId,
    routeBackTo,
    routeParentBackTo,
    canGoCatalog,
    canUseManagerApi,
    catalogTab,
    adminTab,
    navigate,
  ]);

  const setCatalogTab = useCallback((nextTab) => {
    navigate(buildLmsCatalogPath(nextTab));
  }, [navigate]);

  const setAdminTab = useCallback((nextTab) => {
    navigate(buildLmsAdminPath(nextTab));
  }, [navigate]);

  const handleAdminMonthChange = useCallback((newMonth) => {
    setAdminSelectedMonth(newMonth);
    const cacheState = adminCacheRef.current;
    cacheState.tabMonthCache = new Set();
  }, []);

  const unreadNotificationsCount = useMemo(
    () => (Array.isArray(notifications) ? notifications.filter((item) => !item?.read).length : 0),
    [notifications]
  );
  const selectedCourseAnalytics = useMemo(() => {
    if (!canUseManagerApi || !selectedCourse?.id) return null;
    const selectedCourseId = Number(selectedCourse.id || 0);
    if (!selectedCourseId) return null;

    const rows = (Array.isArray(adminProgressRows) ? adminProgressRows : [])
      .filter((row) => Number(row?.course_id || 0) === selectedCourseId);
    const attemptsForCourse = (Array.isArray(adminAttempts) ? adminAttempts : [])
      .filter((attemptItem) => Number(attemptItem?.course_id || 0) === selectedCourseId);

    const assignedCount = rows.length;
    let completedCount = 0;
    let inProgressCount = 0;
    let overdueCount = 0;
    let notStartedCount = 0;
    let progressSum = 0;
    const learners = new Set();

    rows.forEach((row) => {
      const uiStatus = mapAdminProgressRowToUiStatus(row);
      if (uiStatus === "overdue") overdueCount += 1;
      else if (uiStatus === "in_progress") inProgressCount += 1;
      else if (isCompletedLmsStatus(uiStatus)) completedCount += 1;
      else notStartedCount += 1;
      progressSum += clampLmsProgress(row?.progress_percent);
      const learnerId = Number(row?.user_id || 0);
      if (learnerId > 0) learners.add(learnerId);
    });

    const scoredAttempts = attemptsForCourse.filter((item) => item?.score_percent != null);
    const passedAttempts = scoredAttempts.filter((item) => Boolean(item?.passed));
    const finalScoredAttempts = attemptsForCourse.filter((item) => Boolean(item?.is_final) && item?.score_percent != null);
    const testStatsMap = new Map();

    const avgScore = scoredAttempts.length
      ? Math.round(scoredAttempts.reduce((sum, item) => sum + Number(item?.score_percent || 0), 0) / scoredAttempts.length)
      : null;
    const avgFinalScore = finalScoredAttempts.length
      ? Math.round(finalScoredAttempts.reduce((sum, item) => sum + Number(item?.score_percent || 0), 0) / finalScoredAttempts.length)
      : null;
    const passRate = scoredAttempts.length
      ? Math.round((passedAttempts.length / scoredAttempts.length) * 100)
      : null;

    attemptsForCourse.forEach((item) => {
      const explicitTestId = Number(item?.test_id || 0);
      const testTitle = String(item?.test_title || "Тест").trim() || "Тест";
      const statKey = explicitTestId > 0 ? `id:${explicitTestId}` : `title:${testTitle.toLowerCase()}`;
      const prev = testStatsMap.get(statKey) || {
        key: statKey,
        testId: explicitTestId || null,
        title: testTitle,
        attempts: 0,
        scoredCount: 0,
        scoreSum: 0,
        passedCount: 0,
        lastScore: null,
        lastPassed: null,
        lastAtMs: 0,
      };
      prev.attempts += 1;
      const scoreRaw = item?.score_percent;
      const hasScore = scoreRaw != null && scoreRaw !== "";
      if (hasScore) {
        const normalizedScore = Math.max(0, Math.min(100, Math.round(Number(scoreRaw) || 0)));
        prev.scoredCount += 1;
        prev.scoreSum += normalizedScore;
      }
      if (Boolean(item?.passed)) prev.passedCount += 1;
      const ts = Date.parse(item?.started_at || item?.finished_at || "") || 0;
      if (ts >= prev.lastAtMs) {
        prev.lastAtMs = ts;
        prev.lastScore = hasScore ? Math.max(0, Math.min(100, Math.round(Number(scoreRaw) || 0))) : null;
        prev.lastPassed = item?.passed == null ? null : Boolean(item?.passed);
      }
      testStatsMap.set(statKey, prev);
    });

    const testStats = Array.from(testStatsMap.values())
      .map((item) => ({
        key: item.key,
        testId: item.testId,
        title: item.title,
        attempts: item.attempts,
        avgScore: item.scoredCount > 0 ? Math.round(item.scoreSum / item.scoredCount) : null,
        passRate: item.scoredCount > 0 ? Math.round((item.passedCount / item.scoredCount) * 100) : null,
        lastScore: item.lastScore,
        lastPassed: item.lastPassed,
        lastAt: item.lastAtMs > 0 ? new Date(item.lastAtMs).toISOString() : null,
      }))
      .sort((left, right) => {
        const l = Date.parse(left?.lastAt || "") || 0;
        const r = Date.parse(right?.lastAt || "") || 0;
        return r - l;
      });

    const recentAttempts = [...attemptsForCourse]
      .sort((left, right) => {
        const leftTs = Date.parse(left?.started_at || left?.finished_at || "") || 0;
        const rightTs = Date.parse(right?.started_at || right?.finished_at || "") || 0;
        return rightTs - leftTs;
      })
      .slice(0, 12)
      .map((item, index) => ({
        key: `${item?.attempt_id || index}-${item?.test_id || 0}`,
        testTitle: String(item?.test_title || "Тест"),
        employeeName: String(item?.user_name || `#${item?.user_id || "—"}`),
        score: item?.score_percent != null ? Math.round(Number(item.score_percent)) : null,
        passed: Boolean(item?.passed),
        isFinal: Boolean(item?.is_final),
        startedAt: item?.started_at || null,
      }));

    return {
      assignedCount,
      learnerCount: learners.size,
      completedCount,
      inProgressCount,
      overdueCount,
      notStartedCount,
      avgProgress: assignedCount > 0 ? Math.round(progressSum / assignedCount) : 0,
      attemptsCount: attemptsForCourse.length,
      avgScore,
      avgFinalScore,
      passRate,
      testStats,
      recentAttempts,
    };
  }, [canUseManagerApi, selectedCourse?.id, adminProgressRows, adminAttempts]);
  const handleOpenSelectedCourseAnalytics = useCallback(() => {
    if (typeof document === "undefined") return;
    const node = document.getElementById("lms-course-analytics");
    if (!node) return;
    node.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);
  const isLessonLayout = view === "lesson" && Boolean(selectedLesson) && Boolean(selectedCourse);

  return (
    <div className={`min-h-screen bg-slate-50 font-sans ${isLessonLayout ? "h-screen overflow-hidden" : ""}`} style={{ fontFamily: "'DM Sans', system-ui, sans-serif" }}>
      <TopNav
        view={view}
        goBack={goBack}
        canGoCatalog={canGoCatalog}
        unreadNotificationsCount={unreadNotificationsCount}
        notifications={notifications}
        notificationsLoading={loadingHome}
        onNotificationRead={markNotificationRead}
        onMarkAllNotificationsRead={markAllNotificationsRead}
        markingAllNotificationsRead={markingAllNotificationsRead}
      />
      <main className={isLessonLayout ? "pt-16 h-screen overflow-hidden" : "pt-16"}>
        {homeError && canUseLearnerApi && (
          <div className="lms-shell py-4">
            <div className="rounded-xl border border-amber-200 bg-amber-50 text-amber-800 text-sm px-4 py-3">
              {homeError}
            </div>
          </div>
        )}

        {view === "course" && !selectedCourse && (
          <div className="lms-shell py-10">
            <div className="rounded-2xl border border-slate-200 bg-white px-6 py-10 text-sm text-slate-500">
              Загрузка курса...
            </div>
          </div>
        )}

        {view === "lesson" && (!selectedLesson || !selectedCourse) && (
          <div className="lms-shell py-10">
            <div className="rounded-2xl border border-slate-200 bg-white px-6 py-10 text-sm text-slate-500">
              Загрузка урока...
            </div>
          </div>
        )}

        {view === "catalog" && (
          <CatalogView
            tab={catalogTab}
            setTab={setCatalogTab}
            searchQuery={searchQuery}
            setSearchQuery={setSearchQuery}
            onOpenCourse={openCourse}
            isAdmin={isAdmin}
            onOpenBuilder={() => openBuilder(null)}
            courses={courses}
            certificates={certificates}
            notifications={notifications}
            loading={loadingHome}
            busyCourseId={busyCourseId}
            onNotificationRead={markNotificationRead}
            onCertificateDownload={downloadCertificate}
            onRefresh={() => loadLearnerDashboard({ force: true })}
          />
        )}
        {view === "course" && selectedCourse && (
          <CourseDetail
            course={selectedCourse}
            onStartLesson={openLesson}
            isManagerMode={canUseManagerApi}
            onEditCourse={(courseLike) => openBuilder(Number(courseLike?.id || selectedCourse?.id || 0) || null)}
            onOpenCourseAnalytics={handleOpenSelectedCourseAnalytics}
            courseAnalytics={selectedCourseAnalytics}
          />
        )}
        {view === "lesson" && selectedLesson && selectedCourse && (
          <LessonView
            lesson={selectedLesson}
            course={selectedCourse}
            onBack={goBack}
            quizView={quizView}
            setQuizView={setQuizView}
            quizAnswers={quizAnswers}
            setQuizAnswers={setQuizAnswers}
            sidebarOpen={sidebarOpen}
            setSidebarOpen={setSidebarOpen}
            onSelectLesson={openLesson}
            lmsRequest={lmsRequest}
            apiMode={apiMode && canUseLearnerApi}
            blockTranscriptCopy={canUseLearnerApi}
            onCompleteLesson={handleCompleteLesson}
            onQuizFinished={handleQuizFinished}
            emitToast={emitToast}
            isManagerMode={canUseManagerApi}
            courseAnalytics={selectedCourseAnalytics}
            clientSessionKey={lmsClientSessionKey}
          />
        )}
        {view === "builder" && (
          <CourseBuilder
            onBack={goBack}
            lmsRequest={lmsRequest}
            canUseManagerApi={canUseManagerApi}
            learners={learners}
            adminCourses={adminCourses}
            loading={loadingAdmin}
            emitToast={emitToast}
            onAfterSave={() => {
              invalidateAdminCache();
              invalidateCourseCache();
              return loadAdminData({ scope: "builder", force: true });
            }}
            initialCourseId={builderInitialCourseId}
            initialDraftVersionId={builderInitialDraftVersionId}
          />
        )}
        {view === "admin" && (
          <AdminView
            tab={adminTab}
            setTab={setAdminTab}
            adminCourses={adminCourses}
            progressRows={adminProgressRows}
            attempts={adminAttempts}
            analytics={adminAnalytics}
            loading={loadingAdmin}
            selectedMonth={adminSelectedMonth}
            onMonthChange={handleAdminMonthChange}
            onOpenBuilder={openBuilder}
            onOpenCourse={openCourse}
            onDeleteCourse={handleDeleteAdminCourse}
            onArchiveCourse={handleArchiveAdminCourse}
            onRestoreCourse={handleRestoreAdminCourse}
            onAssignCourseToEmployee={handleAssignAdminCourseToEmployee}
            canDeleteCourses={canDeleteCourses}
            busyCourseId={busyCourseId}
            isEditorMode={isEditorRole}
            loadLearningSessions={loadAdminLearningSessions}
          />
        )}
      </main>
    </div>
  );
}

// ─── TOP NAVIGATION ───────────────────────────────────────────────────────────

function TopNav({
  view,
  goBack,
  canGoCatalog = true,
  unreadNotificationsCount = 0,
  notifications = [],
  notificationsLoading = false,
  onNotificationRead,
  onMarkAllNotificationsRead,
  markingAllNotificationsRead = false,
}) {
  const [isNotificationsOpen, setIsNotificationsOpen] = useState(false);
  const notificationDropdownRef = useRef(null);
  const notificationTriggerRef = useRef(null);
  const showBack = ["course", "lesson", "builder"].includes(view) || (view === "admin" && canGoCatalog);
  const backLabels = { course: "Все курсы", lesson: "Курс", builder: "Все курсы", admin: "Все курсы" };
  const safeNotifications = Array.isArray(notifications) ? notifications : [];
  const topNotifications = useMemo(
    () =>
      [...safeNotifications]
        .sort((left, right) => {
          const leftTs = left?.createdAt ? Date.parse(left.createdAt) : 0;
          const rightTs = right?.createdAt ? Date.parse(right.createdAt) : 0;
          return rightTs - leftTs;
        })
        .slice(0, 8),
    [safeNotifications]
  );
  const unreadCount = Math.max(0, Number(unreadNotificationsCount || 0));
  const unreadBadgeLabel = unreadCount > 99 ? "99+" : String(unreadCount);
  const canOpenNotifications = canGoCatalog;
  const canMarkAllRead =
    unreadCount > 0 && typeof onMarkAllNotificationsRead === "function" && !markingAllNotificationsRead;
  const iconMap = {
    deadline: AlertCircle,
    completed: CheckCircle,
    assigned: BookOpen,
    certificate: Award,
  };
  const colorMap = {
    deadline: "text-amber-600 bg-amber-50",
    completed: "text-emerald-600 bg-emerald-50",
    assigned: "text-indigo-600 bg-indigo-50",
    certificate: "text-violet-600 bg-violet-50",
  };

  useEffect(() => {
    if (!isNotificationsOpen) return;
    const handleMouseDown = (event) => {
      const target = event.target;
      if (notificationDropdownRef.current?.contains(target)) return;
      if (notificationTriggerRef.current?.contains(target)) return;
      setIsNotificationsOpen(false);
    };
    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        setIsNotificationsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleMouseDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleMouseDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isNotificationsOpen]);

  useEffect(() => {
    if (!canOpenNotifications && isNotificationsOpen) {
      setIsNotificationsOpen(false);
    }
  }, [canOpenNotifications, isNotificationsOpen]);

  const handleNotificationButtonClick = () => {
    if (!canOpenNotifications) return;
    setIsNotificationsOpen((prev) => !prev);
  };

  const handleNotificationItemClick = (notification) => {
    const notificationId = Number(notification?.id || 0);
    if (!notification?.read && notificationId > 0 && typeof onNotificationRead === "function") {
      void onNotificationRead(notificationId);
    }
  };

  const handleMarkAllRead = () => {
    if (!canMarkAllRead) return;
    void onMarkAllNotificationsRead();
  };

  return (
    <header
      className="fixed top-0 right-0 z-40 bg-white border-b border-slate-200 h-16"
      style={{ left: "var(--main-content-sidebar-offset, 0px)" }}
    >
      <div className="lms-shell h-full flex items-center justify-between gap-4">
        <div className="flex items-center gap-4">
          {showBack ? (
            <button onClick={goBack} className="flex items-center gap-2 text-sm text-slate-500 hover:text-slate-800 transition-colors">
              <ChevronLeft size={18} /> {backLabels[view]}
            </button>
          ) : (
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 bg-indigo-600 rounded-lg flex items-center justify-center"><BookOpen size={16} className="text-white" /></div>
              <span className="text-[15px] font-semibold text-slate-900 tracking-tight">CorpLearn</span>
            </div>
          )}
        </div>
        <div className="flex items-center gap-3">
          {canOpenNotifications && (
            <div className="relative">
              <button
                ref={notificationTriggerRef}
                type="button"
                onClick={handleNotificationButtonClick}
                className={`relative inline-flex h-9 min-w-9 items-center justify-center rounded-xl border bg-white px-2.5 text-slate-600 transition-colors ${isNotificationsOpen ? "border-indigo-300 text-indigo-700 bg-indigo-50" : "border-slate-200 hover:border-indigo-300 hover:text-indigo-700 hover:bg-indigo-50"}`}
                title={unreadCount > 0 ? `Уведомления: ${unreadCount} непрочитанных` : "Уведомления"}
                aria-label={unreadCount > 0 ? `Уведомления, ${unreadCount} непрочитанных` : "Уведомления"}
                aria-expanded={isNotificationsOpen}
              >
                <Bell size={16} />
                {unreadCount > 0 && (
                  <span className="absolute -top-1.5 -right-1.5 min-w-5 h-5 px-1 rounded-full bg-rose-500 text-white text-[10px] font-semibold leading-5 shadow-sm shadow-rose-500/30">
                    {unreadBadgeLabel}
                  </span>
                )}
              </button>

              {isNotificationsOpen && (
                <div
                  ref={notificationDropdownRef}
                  className="absolute top-full right-0 mt-2 w-[360px] max-w-[calc(100vw-1rem)] rounded-2xl border border-slate-200 bg-white shadow-xl shadow-slate-900/10 overflow-hidden"
                >
                  <div className="px-4 py-3 border-b border-slate-100 bg-slate-50/80">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-xs font-semibold text-slate-800">Уведомления</p>
                      <button
                        type="button"
                        onClick={handleMarkAllRead}
                        disabled={!canMarkAllRead}
                        className="text-[11px] font-medium text-indigo-600 hover:text-indigo-700 disabled:text-slate-400 disabled:cursor-not-allowed"
                      >
                        {markingAllNotificationsRead ? "Отмечаем..." : "Отметить прочитанным все"}
                      </button>
                    </div>
                    <p className="text-[10px] text-slate-500 mt-1">
                      {unreadCount > 0 ? `Непрочитанных: ${unreadCount}` : "Все уведомления прочитаны"}
                    </p>
                  </div>

                  <div className="max-h-[320px] overflow-y-auto custom-scrollbar">
                    {notificationsLoading && safeNotifications.length === 0 ? (
                      <div className="p-3 space-y-2">
                        {Array.from({ length: 4 }).map((_, idx) => (
                          <div key={`top-nav-notification-skeleton-${idx}`} className="rounded-xl border border-slate-100 p-3 space-y-2">
                            <SkeletonBlock className="w-7 h-3.5" />
                            <SkeletonBlock className="w-11/12 h-3" />
                            <SkeletonBlock className="w-24 h-2.5" />
                          </div>
                        ))}
                      </div>
                    ) : topNotifications.length === 0 ? (
                      <div className="px-4 py-8 text-center text-[11px] text-slate-500">
                        Уведомлений пока нет
                      </div>
                    ) : (
                      <div className="p-2">
                        {topNotifications.map((notification) => {
                          const Icon = iconMap[notification?.type] || Bell;
                          const iconClass = colorMap[notification?.type] || "text-slate-600 bg-slate-100";
                          const notificationId = Number(notification?.id || 0);
                          return (
                            <button
                              type="button"
                              key={notificationId || `top-notification-${notification?.title || "item"}`}
                              onClick={() => handleNotificationItemClick(notification)}
                              className={`w-full text-left rounded-xl border p-3 mb-1.5 last:mb-0 transition-all ${notification?.read ? "border-slate-100 bg-white" : "border-indigo-100 bg-indigo-50/40 hover:bg-indigo-50"}`}
                            >
                              <div className="flex items-start gap-2.5">
                                <span className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ${iconClass}`}>
                                  <Icon size={14} />
                                </span>
                                <span className="flex-1 min-w-0">
                                  <span className="flex items-start justify-between gap-2">
                                    <span className="text-[12px] font-semibold text-slate-800 leading-4">
                                      {notification?.title || "Уведомление LMS"}
                                    </span>
                                    {!notification?.read && (
                                      <span className="w-2 h-2 rounded-full bg-indigo-500 flex-shrink-0 mt-1" />
                                    )}
                                  </span>
                                  {!!notification?.message && (
                                    <span className="block mt-0.5 text-[11px] text-slate-500 leading-4 truncate">
                                      {notification.message}
                                    </span>
                                  )}
                                  <span className="block mt-1 text-[10px] text-slate-400">
                                    {notification?.time || ""}
                                  </span>
                                </span>
                              </div>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </header>
  );
}

// ─── CATALOG VIEW ─────────────────────────────────────────────────────────────

function CatalogView({
  tab,
  setTab,
  searchQuery,
  setSearchQuery,
  onOpenCourse,
  isAdmin,
  onOpenBuilder,
  courses = [],
  certificates = [],
  notifications = [],
  loading = false,
  busyCourseId = null,
  onNotificationRead,
  onCertificateDownload,
  onRefresh,
}) {
  const [filter, setFilter] = useState("all");
  const [gridView, setGridView] = useState(true);
  const [sortBy, setSortBy] = useState("default");

  const safeCourses = Array.isArray(courses) ? courses : [];
  const safeCertificates = Array.isArray(certificates) ? certificates : [];
  const safeNotifications = Array.isArray(notifications) ? notifications : [];
  const isCoursesLoading = loading && safeCourses.length === 0;
  const isCertificatesLoading = loading && safeCertificates.length === 0;
  const isNotificationsLoading = loading && safeNotifications.length === 0;

  const tabs = [
    { id: "available", label: "Доступные", icon: BookOpen, count: safeCourses.filter((c) => c.status !== "completed" && c.status !== "completed_late").length },
    { id: "completed", label: "Пройденные", icon: CheckCircle, count: safeCourses.filter((c) => c.status === "completed" || c.status === "completed_late").length },
    { id: "certificates", label: "Сертификаты", icon: Award, count: safeCertificates.length },
    { id: "notifications", label: "Уведомления", icon: Bell, count: safeNotifications.filter((n) => !n.read).length },
  ];

  const filters = [
    { id: "all", label: "Все" }, { id: "mandatory", label: "Обязательные" },
    { id: "in_progress", label: "В процессе" }, { id: "overdue", label: "Просроченные" },
  ];

  const filteredCourses = safeCourses.filter(c => {
    const isCompleted = c.status === "completed" || c.status === "completed_late";
    const matchesTab = tab === "available" ? !isCompleted : isCompleted;
    const matchesSearch = c.title.toLowerCase().includes(searchQuery.toLowerCase()) || c.category.toLowerCase().includes(searchQuery.toLowerCase());
    const isMandatory = resolveCourseMandatoryFlag(c);
    const matchesFilter = filter === "all" || (filter === "mandatory" && isMandatory) || c.status === filter;
    return matchesTab && matchesSearch && matchesFilter;
  }).sort((a, b) => {
    if (sortBy === "deadline") {
      const da = a.deadline ? new Date(a.deadline) : new Date("9999");
      const db = b.deadline ? new Date(b.deadline) : new Date("9999");
      return da - db;
    }
    return 0;
  });

  const stats = {
    assigned: safeCourses.length,
    inProgress: safeCourses.filter((c) => ["in_progress", "waiting_test", "test_failed"].includes(c.status)).length,
    completed: safeCourses.filter((c) => c.status === "completed" || c.status === "completed_late").length,
    overdue: safeCourses.filter((c) => c.status === "overdue").length,
  };

  return (
    <div className="lms-shell py-8">
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Обучение</h1>
          <p className="text-sm text-slate-500 mt-0.5">Корпоративная платформа развития сотрудников</p>
          {loading && <p className="text-xs text-indigo-600 mt-1">Синхронизация с LMS API...</p>}
        </div>
        <div className="flex items-center gap-2">
          {typeof onRefresh === "function" && (
            <button
              onClick={onRefresh}
              className="flex items-center gap-2 bg-white border border-slate-200 hover:border-indigo-300 text-slate-600 text-sm px-3 py-2.5 rounded-xl font-medium transition-colors"
            >
              <RefreshCw size={14} /> Обновить
            </button>
          )}
          {isAdmin && (
            <button onClick={onOpenBuilder} className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm px-4 py-2.5 rounded-xl font-medium transition-colors shadow-sm shadow-indigo-200">
              <Plus size={16} /> Создать курс
            </button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-4 gap-4 mb-8">
        {isCoursesLoading ? (
          Array.from({ length: 4 }).map((_, idx) => (
            <div key={`catalog-stats-skeleton-${idx}`} className="bg-white rounded-2xl border border-slate-200 p-4 flex items-center gap-3">
              <SkeletonBlock className="w-10 h-10 flex-shrink-0" />
              <div className="space-y-2">
                <SkeletonBlock className="w-12 h-5" />
                <SkeletonBlock className="w-24 h-3.5" />
              </div>
            </div>
          ))
        ) : (
          [
            { label: "Назначено курсов", value: String(stats.assigned), icon: BookOpen, color: "text-indigo-600", bg: "bg-indigo-50" },
            { label: "В процессе", value: String(stats.inProgress), icon: PlayCircle, color: "text-blue-600", bg: "bg-blue-50" },
            { label: "Завершено", value: String(stats.completed), icon: CheckCircle, color: "text-emerald-600", bg: "bg-emerald-50" },
            { label: "Просрочено", value: String(stats.overdue), icon: AlertCircle, color: "text-red-600", bg: "bg-red-50" },
          ].map(s => (
            <div key={s.label} className="bg-white rounded-2xl border border-slate-200 p-4 flex items-center gap-3">
              <div className={`w-10 h-10 ${s.bg} rounded-xl flex items-center justify-center flex-shrink-0`}>
                <s.icon size={18} className={s.color} />
              </div>
              <div>
                <div className="text-xl font-bold text-slate-900">{s.value}</div>
                <div className="text-xs text-slate-500">{s.label}</div>
              </div>
            </div>
          ))
        )}
      </div>

      <div className="flex items-center gap-1 mb-6 bg-slate-100 p-1 rounded-xl w-fit">
        {tabs.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all ${tab === t.id ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}>
            <t.icon size={14} />
            {t.label}
            {t.count > 0 && (
              <span className={`text-xs px-1.5 py-0.5 rounded-full ${tab === t.id ? "bg-indigo-100 text-indigo-700" : "bg-slate-200 text-slate-600"}`}>{t.count}</span>
            )}
          </button>
        ))}
      </div>

      {tab === "certificates" && (
        <CertificatesView
          certificates={safeCertificates}
          onDownload={onCertificateDownload}
          loading={isCertificatesLoading}
        />
      )}
      {tab === "notifications" && (
        <NotificationsView
          notifications={safeNotifications}
          onRead={onNotificationRead}
          loading={isNotificationsLoading}
        />
      )}

      {(tab === "available" || tab === "completed") && (
        <>
          <div className="flex items-center gap-3 mb-6">
            <div className="relative flex-1 max-w-xl 2xl:max-w-2xl">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input value={searchQuery} onChange={e => setSearchQuery(e.target.value)} placeholder="Поиск курсов..." className="w-full pl-9 pr-4 py-2.5 bg-white border border-slate-200 rounded-xl text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 transition-all" />
            </div>
            <div className="flex items-center gap-2">
              {filters.map(f => (
                <button key={f.id} onClick={() => setFilter(f.id)} className={`px-3 py-2 text-xs rounded-xl border font-medium transition-all ${filter === f.id ? "bg-indigo-600 text-white border-indigo-600" : "bg-white text-slate-600 border-slate-200 hover:border-slate-300"}`}>
                  {f.label}
                </button>
              ))}
            </div>
            <select value={sortBy} onChange={e => setSortBy(e.target.value)} className="text-xs bg-white border border-slate-200 rounded-xl px-3 py-2 text-slate-600 focus:outline-none focus:border-indigo-400">
              <option value="default">По умолчанию</option>
              <option value="deadline">По дедлайну</option>
            </select>
            <div className="flex items-center gap-1 bg-white border border-slate-200 rounded-xl p-1">
              <button onClick={() => setGridView(true)} className={`p-1.5 rounded-lg transition-colors ${gridView ? "bg-slate-100 text-slate-800" : "text-slate-400 hover:text-slate-600"}`}><LayoutGrid size={14} /></button>
              <button onClick={() => setGridView(false)} className={`p-1.5 rounded-lg transition-colors ${!gridView ? "bg-slate-100 text-slate-800" : "text-slate-400 hover:text-slate-600"}`}><List size={14} /></button>
            </div>
          </div>

          {isCoursesLoading ? (
            gridView ? (
              <div className="grid [grid-template-columns:repeat(auto-fill,minmax(290px,1fr))] gap-5">
                {Array.from({ length: 6 }).map((_, idx) => (
                  <div key={`catalog-card-skeleton-${idx}`} className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                    <SkeletonBlock className="h-32 w-full rounded-none" />
                    <div className="p-5 space-y-3">
                      <SkeletonBlock className="w-20 h-3" />
                      <SkeletonBlock className="w-11/12 h-4" />
                      <SkeletonBlock className="w-8/12 h-3" />
                      <SkeletonBlock className="w-full h-1.5 rounded-full" />
                      <div className="flex items-center justify-between pt-1">
                        <SkeletonBlock className="w-24 h-3.5" />
                        <SkeletonBlock className="w-24 h-8" />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {Array.from({ length: 6 }).map((_, idx) => (
                  <div key={`catalog-list-skeleton-${idx}`} className="bg-white rounded-2xl border border-slate-200 p-5 flex items-center gap-5">
                    <SkeletonBlock className="w-14 h-14 flex-shrink-0" />
                    <div className="flex-1 space-y-2">
                      <SkeletonBlock className="w-24 h-3" />
                      <SkeletonBlock className="w-9/12 h-4" />
                      <SkeletonBlock className="w-6/12 h-3" />
                    </div>
                    <SkeletonBlock className="w-20 h-6" />
                    <SkeletonBlock className="w-4 h-4 rounded-md" />
                  </div>
                ))}
              </div>
            )
          ) : filteredCourses.length === 0 ? (
            <div className="text-center py-20 text-slate-400"><BookOpen size={40} className="mx-auto mb-3 opacity-30" /><p className="text-sm">Курсы не найдены</p></div>
          ) : gridView ? (
            <div className="grid [grid-template-columns:repeat(auto-fill,minmax(290px,1fr))] gap-5">
              {filteredCourses.map(c => (
                <CourseCard
                  key={c.id}
                  course={c}
                  busy={busyCourseId === c.id}
                  onClick={(event) => onOpenCourse(c, event)}
                />
              ))}
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              {filteredCourses.map(c => (
                <CourseListItem
                  key={c.id}
                  course={c}
                  busy={busyCourseId === c.id}
                  onClick={(event) => onOpenCourse(c, event)}
                />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ─── COURSE CARD ──────────────────────────────────────────────────────────────

function CourseCard({ course, onClick, busy = false, actions = null, managerMode = false }) {
  const st = statusConfig[course.status] || statusConfig.not_started;
  const dl = course.deadline ? formatDeadline(course.deadline) : null;
  const isMandatory = resolveCourseMandatoryFlag(course);
  const attemptsLeft = Math.max(0, Number(course.maxAttempts || 0) - Number(course.attemptsUsed || 0));
  const showProgressBar = managerMode || (course.status !== "completed" && course.status !== "not_started");
  const progressLabel = managerMode ? "Прошли" : "Прогресс";
  const actionLabel = managerMode
    ? "Открыть"
    : ((course.status === "completed" || course.status === "completed_late") ? "Просмотр" : course.status === "not_started" ? "Начать" : "Продолжить");

  return (
    <div onClick={(event) => !busy && onClick?.(event)} className={`bg-white rounded-2xl border border-slate-200 overflow-hidden transition-all group ${busy ? "opacity-70 cursor-wait" : "cursor-pointer hover:shadow-md hover:border-slate-300"}`}>
      <div className={`h-32 bg-gradient-to-br ${course.color} flex items-center justify-center relative overflow-hidden`}>
        {course.coverUrl ? (
          <img src={course.coverUrl} alt={course.title} className="absolute inset-0 w-full h-full object-cover object-center" />
        ) : (
          <span className="text-5xl">{course.cover}</span>
        )}
        <div className="absolute inset-0 bg-black/10" />
        {isMandatory && (
          <div className="absolute top-3 left-3 bg-white/20 backdrop-blur-sm text-white text-[10px] font-semibold px-2 py-1 rounded-full border border-white/30 z-10">Обязательный</div>
        )}
        {!managerMode && course.status === "completed" && (
          <div className="absolute top-3 right-3 w-8 h-8 bg-white rounded-full flex items-center justify-center shadow-md z-10"><CheckCircle size={16} className="text-emerald-600" /></div>
        )}
        {!managerMode && course.status === "overdue" && (
          <div className="absolute top-3 right-3 w-8 h-8 bg-red-500 rounded-full flex items-center justify-center shadow-md z-10"><AlertCircle size={16} className="text-white" /></div>
        )}
      </div>
      <div className="p-5">
        <div className="flex items-start justify-between gap-2 mb-2">
          <span className="text-[10px] font-semibold text-indigo-600 uppercase tracking-wider">{course.category}</span>
          {!managerMode && <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${st.bg} ${st.text}`}>{st.label}</span>}
          {managerMode && course?.editorDraftLabel && (
            <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-amber-50 text-amber-700">
              {course.editorDraftLabel}
            </span>
          )}
        </div>
        <h3 className="text-sm font-semibold text-slate-900 leading-snug mb-3 group-hover:text-indigo-700 transition-colors line-clamp-2">{course.title}</h3>
        <div className="flex items-center gap-3 text-xs text-slate-500 mb-4">
          <span className="flex items-center gap-1"><Clock size={11} /> {course.duration}</span>
          <span className="flex items-center gap-1"><BookOpen size={11} /> {course.lessons} уроков</span>
          <span className="flex items-center gap-1"><Star size={11} className="text-amber-400 fill-amber-400" /> {course.rating}</span>
        </div>
        {showProgressBar && (
          <div className="mb-3">
            <div className="flex justify-between text-xs text-slate-500 mb-1.5"><span>{progressLabel}</span><span className="font-semibold text-slate-700">{course.progress}%</span></div>
            <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
              <div className={`h-full rounded-full transition-all ${!managerMode && course.status === "overdue" ? "bg-red-500" : "bg-indigo-500"}`} style={{ width: `${course.progress}%` }} />
            </div>
          </div>
        )}
        {/* Попытки */}
        {!managerMode && course.hasCourseAttemptLimit && Number(course.maxAttempts || 0) > 0 && course.status !== "completed" && course.status !== "completed_late" && (
          <div className={`flex items-center gap-1 text-[10px] mb-3 ${attemptsLeft <= 1 ? "text-red-600" : "text-slate-500"}`}>
            <RefreshCw size={10} />
            <span>Попыток осталось: <strong>{attemptsLeft <= 0 ? "нет" : attemptsLeft}</strong> из {course.maxAttempts}</span>
          </div>
        )}
        <div className="flex items-center justify-between">
          {!managerMode && dl && (
            <div className={`flex items-center gap-1 text-xs ${dl.overdue ? "text-red-600" : dl.urgent ? "text-amber-600" : "text-slate-500"}`}>
              <Calendar size={11} />
              {dl.overdue ? `Просрочен ${Math.abs(Math.ceil((new Date(course.deadline) - new Date()) / 86400000))} дн` : `До ${dl.label}`}
            </div>
          )}
          <div className="ml-auto flex items-center gap-2" onClick={(event) => event.stopPropagation()}>
            {actions}
            <button
              onClick={(event) => !busy && onClick?.(event)}
              className={`text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors ${managerMode ? "bg-indigo-600 text-white hover:bg-indigo-700" : (course.status === "completed" || course.status === "completed_late" ? "bg-emerald-50 text-emerald-700 hover:bg-emerald-100" : "bg-indigo-600 text-white hover:bg-indigo-700")}`}
            >
              {busy ? "Загрузка..." : actionLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function CourseListItem({ course, onClick, busy = false, actions = null, managerMode = false }) {
  const st = statusConfig[course.status] || statusConfig.not_started;
  const dl = course.deadline ? formatDeadline(course.deadline) : null;
  const isMandatory = resolveCourseMandatoryFlag(course);
  const showProgressBar = managerMode || (course.status !== "completed" && course.status !== "not_started");
  return (
    <div onClick={(event) => !busy && onClick?.(event)} className={`bg-white rounded-2xl border border-slate-200 p-5 flex items-center gap-5 transition-all group ${busy ? "opacity-70 cursor-wait" : "cursor-pointer hover:shadow-sm hover:border-slate-300"}`}>
      <div className={`w-14 h-14 rounded-xl bg-gradient-to-br ${course.color} flex items-center justify-center text-2xl flex-shrink-0 overflow-hidden`}>
        {course.coverUrl ? (
          <img src={course.coverUrl} alt={course.title} className="w-full h-full object-cover object-center" />
        ) : (
          course.cover
        )}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-[10px] font-semibold text-indigo-600 uppercase tracking-wider">{course.category}</span>
          {isMandatory && <span className="text-[10px] font-semibold text-amber-600 bg-amber-50 px-2 py-0.5 rounded-full">Обязательный</span>}
          {managerMode && course?.editorDraftLabel && (
            <span className="text-[10px] font-semibold text-amber-700 bg-amber-50 px-2 py-0.5 rounded-full">
              {course.editorDraftLabel}
            </span>
          )}
        </div>
        <h3 className="text-sm font-semibold text-slate-900 group-hover:text-indigo-700 transition-colors truncate">{course.title}</h3>
        <div className="flex items-center gap-3 text-xs text-slate-500 mt-1">
          <span className="flex items-center gap-1"><Clock size={11} /> {course.duration}</span>
          <span className="flex items-center gap-1"><BookOpen size={11} /> {course.lessons} уроков</span>
          {course.hasCourseAttemptLimit && (
            <span className="flex items-center gap-1"><RefreshCw size={11} /> {Math.max(0, Number(course.maxAttempts || 0) - Number(course.attemptsUsed || 0))} поп.</span>
          )}
        </div>
        {!managerMode && dl && (
          <div className={`mt-1.5 flex items-center gap-1 text-[11px] ${dl.overdue ? "text-red-600" : dl.urgent ? "text-amber-600" : "text-slate-500"}`}>
            <Calendar size={11} />
            {dl.overdue ? `Просрочен ${Math.abs(Math.ceil((new Date(course.deadline) - new Date()) / 86400000))} дн` : `До ${dl.label}`}
          </div>
        )}
      </div>
      {showProgressBar && (
        <div className="w-32">
          <div className="flex justify-between text-xs text-slate-500 mb-1"><span>{managerMode ? "Прошли" : "Прогресс"}</span><span>{course.progress}%</span></div>
          <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
            <div className="h-full bg-indigo-500 rounded-full" style={{ width: `${course.progress}%` }} />
          </div>
        </div>
      )}
      {!managerMode && <span className={`text-xs font-semibold px-2.5 py-1 rounded-full ${st.bg} ${st.text}`}>{st.label}</span>}
      <div className="flex items-center gap-1.5" onClick={(event) => event.stopPropagation()}>
        {actions}
        <ChevronRight size={16} className="text-slate-400 flex-shrink-0" />
      </div>
    </div>
  );
}

// ─── COURSE DETAIL ────────────────────────────────────────────────────────────

function CourseDetail({
  course,
  onStartLesson,
  isManagerMode = false,
  onEditCourse,
  onOpenCourseAnalytics,
  courseAnalytics = null,
}) {
  const modulesData = Array.isArray(course?.modules_data) ? course.modules_data : [];
  const skills = Array.isArray(course?.skills) ? course.skills : [];
  const [openModules, setOpenModules] = useState([modulesData[0]?.id || 1]);
  const toggleModule = (id) => setOpenModules(p => p.includes(id) ? p.filter(x => x !== id) : [...p, id]);
  const dl = course.deadline ? formatDeadline(course.deadline) : null;
  const isMandatory = resolveCourseMandatoryFlag(course);
  const firstLesson = modulesData[0]?.lessons?.[0] || null;
  const attemptsLeft = Math.max(0, Number(course.maxAttempts || 0) - Number(course.attemptsUsed || 0));
  const hasCourseAnalytics = isManagerMode && courseAnalytics && typeof courseAnalytics === "object";
  const [analyticsSection, setAnalyticsSection] = useState("summary");
  const handleOpenProgram = useCallback((event) => {
    if (!firstLesson) return;
    onStartLesson(firstLesson, event);
  }, [firstLesson, onStartLesson]);

  useEffect(() => {
    setOpenModules(modulesData[0]?.id ? [modulesData[0].id] : []);
  }, [course?.id]);

  useEffect(() => {
    setAnalyticsSection("summary");
  }, [course?.id]);

  return (
    <div className="lms-shell py-8">
      <div className={`rounded-3xl bg-gradient-to-br ${course.color} p-8 mb-8 relative overflow-hidden`}>
        {course.coverUrl ? (
          <img src={course.coverUrl} alt={course.title} className="absolute inset-0 w-full h-full object-cover object-center opacity-30" />
        ) : (
          <div className="absolute right-8 top-8 text-8xl opacity-20">{course.cover}</div>
        )}
        <div className="relative z-10 max-w-2xl 2xl:max-w-3xl">
          <div className="flex items-center gap-2 mb-4">
            <span className="text-xs font-semibold text-white/70 uppercase tracking-wider">{course.category}</span>
            {isMandatory && <span className="text-xs font-semibold bg-white/20 text-white px-2.5 py-1 rounded-full">Обязательный</span>}
          </div>
          <h1 className="text-3xl font-bold text-white mb-4 leading-tight tracking-tight">{course.title}</h1>
          <RichTextContent
            value={course.description}
            className="text-white/80 text-sm leading-relaxed mb-6 [&_a]:text-white [&_strong]:text-white"
            emptyState={<div className="text-white/70 text-sm mb-6">Описание курса не заполнено</div>}
          />
          <div className="flex items-center gap-5 text-white/70 text-sm mb-6">
            <span className="flex items-center gap-2"><Clock size={15} /> {course.duration}</span>
            <span className="flex items-center gap-2"><BookOpen size={15} /> {course.lessons} уроков</span>
            <span className="flex items-center gap-2"><Layers size={15} /> {course.modules} модулей</span>
            <span className="flex items-center gap-2"><Star size={15} className="text-amber-300 fill-amber-300" /> {course.rating} ({course.reviews})</span>
          </div>
          {course.progress > 0 && course.status !== "completed" && (
            <div className="mb-6">
              <div className="flex justify-between text-white/70 text-xs mb-2"><span>Прогресс курса</span><span className="font-semibold text-white">{course.progress}%</span></div>
              <div className="h-2 bg-white/20 rounded-full overflow-hidden"><div className="h-full bg-white rounded-full" style={{ width: `${course.progress}%` }} /></div>
            </div>
          )}
          {isManagerMode && (
            <div className="flex flex-wrap items-center gap-2 mb-4">
              <button
                onClick={() => onEditCourse?.(course)}
                className="inline-flex items-center gap-1.5 bg-white/20 hover:bg-white/30 border border-white/30 text-white text-sm font-semibold px-3.5 py-2 rounded-xl transition-colors"
              >
                <Edit size={14} /> Редактировать
              </button>
              <button
                onClick={() => onOpenCourseAnalytics?.(course)}
                className="inline-flex items-center gap-1.5 bg-white/20 hover:bg-white/30 border border-white/30 text-white text-sm font-semibold px-3.5 py-2 rounded-xl transition-colors"
              >
                <BarChart2 size={14} /> Аналитика курса
              </button>
            </div>
          )}
          <button
            onClick={(event) => {
              if (isManagerMode) {
                handleOpenProgram(event);
                return;
              }
              if (firstLesson) onStartLesson(firstLesson, event);
            }}
            disabled={!firstLesson}
            className="bg-white text-slate-900 font-semibold px-6 py-3 rounded-xl hover:bg-white/90 transition-colors text-sm shadow-lg disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {isManagerMode
              ? (firstLesson ? "Перейти к первому уроку" : "Программа курса пуста")
              : (course.status === "not_started" ? "Начать курс" : course.status === "completed" ? "Повторить" : "Продолжить обучение")}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-12 gap-8 2xl:gap-10">
        <div className="xl:col-span-8 2xl:col-span-9 space-y-6">
          <div className="bg-white rounded-2xl border border-slate-200 p-6">
            <h2 className="text-base font-semibold text-slate-900 mb-4">Приобретаемые навыки</h2>
            <div className="flex flex-wrap gap-2">
              {skills.map(s => <span key={s} className="text-xs font-medium bg-indigo-50 text-indigo-700 border border-indigo-100 px-3 py-1.5 rounded-full">{s}</span>)}
              {skills.length === 0 && <span className="text-xs text-slate-400">Навыки не добавлены</span>}
            </div>
          </div>

          {modulesData.length > 0 && (
            <div id="lms-course-curriculum" className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
              <div className="px-6 py-5 border-b border-slate-100">
                <h2 className="text-base font-semibold text-slate-900">Программа курса</h2>
                <p className="text-xs text-slate-500 mt-1">{course.modules} модуля · {course.lessons} уроков</p>
              </div>
              {modulesData.map((mod, moduleIndex) => (
                <div key={mod.id} className="border-b border-slate-100 last:border-0">
                  <button onClick={() => toggleModule(mod.id)} className="w-full flex items-center justify-between px-6 py-4 hover:bg-slate-50 transition-colors">
                    <div className="flex items-center gap-3">
                      <div className="w-7 h-7 rounded-lg bg-indigo-50 text-indigo-600 flex items-center justify-center text-xs font-bold">{moduleIndex + 1}</div>
                      <span className="text-sm font-semibold text-slate-800">{mod.title}</span>
                      <span className="text-xs text-slate-400">{mod.lessons.length} уроков</span>
                    </div>
                    {openModules.includes(mod.id) ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
                  </button>
                  {openModules.includes(mod.id) && (
                    <div className="px-6 pb-4 space-y-1">
                      {mod.lessons.map((l, lessonIndex) => {
                        const Icon = lessonIcons[l.type];
                        const lessonLocked = isManagerMode ? false : Boolean(l.locked);
                        return (
                          <div key={l.id} onClick={(event) => !lessonLocked && onStartLesson(l, event)} className={`flex items-center gap-3 p-3 rounded-xl transition-all ${lessonLocked ? "opacity-50 cursor-not-allowed" : "cursor-pointer hover:bg-indigo-50 group"}`}>
                            <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${isManagerMode ? "bg-indigo-50" : (l.status === "completed" ? "bg-emerald-50" : lessonLocked ? "bg-slate-100" : "bg-indigo-50")}`}>
                              {isManagerMode
                                ? <Icon size={14} className="text-indigo-600" />
                                : (l.status === "completed" ? <CheckCircle size={14} className="text-emerald-600" /> : lessonLocked ? <Lock size={14} className="text-slate-400" /> : <Icon size={14} className="text-indigo-600" />)}
                            </div>
                            <div className="flex-1">
                              <p className={`text-xs font-medium ${lessonLocked ? "text-slate-400" : "text-slate-800 group-hover:text-indigo-700"}`}>{lessonIndex + 1}. {l.title}</p>
                              {!isManagerMode && <p className="text-[10px] text-slate-400 mt-0.5">{l.type === "video" ? "Видеоурок" : l.type === "text" ? "Текстовый материал" : l.type === "combined" ? "Комбо-урок" : "Тест"} · {l.duration}</p>}
                            </div>
                            {!isManagerMode && l.requiresTest && !lessonLocked && <span className="text-[10px] bg-violet-50 text-violet-600 px-1.5 py-0.5 rounded-full font-medium">Тест</span>}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="xl:col-span-4 2xl:col-span-3 space-y-4">
          <div className="bg-white rounded-2xl border border-slate-200 p-5">
            <h3 className="text-sm font-semibold text-slate-900 mb-4">Параметры курса</h3>
            <div className="space-y-3">
              {[
                { label: "Дедлайн", value: dl ? (dl.overdue ? <span className="text-red-600 font-semibold">Просрочен</span> : <span className={dl.urgent ? "text-amber-600 font-semibold" : "text-slate-700"}>{dl.label}</span>) : "Не задан", icon: Calendar },
                { label: "Проходной балл", value: `${course.passingScore}%`, icon: Target },
                { label: "Попыток доступно", value: !course.hasCourseAttemptLimit ? "-" : <span className={attemptsLeft <= 0 ? "text-red-600 font-semibold" : attemptsLeft <= 1 ? "text-amber-600 font-semibold" : ""}>{attemptsLeft <= 0 ? "Исчерпаны" : `${attemptsLeft} из ${course.maxAttempts}`}</span>, icon: RefreshCw },
                { label: "Модулей", value: course.modules, icon: Layers },
              ].map(r => (
                <div key={r.label} className="flex items-center justify-between py-2 border-b border-slate-50 last:border-0">
                  <div className="flex items-center gap-2 text-xs text-slate-500"><r.icon size={13} /> {r.label}</div>
                  <span className="text-xs font-semibold text-slate-800">{r.value}</span>
                </div>
              ))}
            </div>
          </div>
          {hasCourseAnalytics && (
            <div id="lms-course-analytics" className="bg-white rounded-2xl border border-slate-200 p-5 space-y-4">
              <div>
                <h3 className="text-sm font-semibold text-slate-900">Аналитика курса</h3>
                <p className="text-[11px] text-slate-500 mt-1">Нажмите блок ниже, чтобы показать только нужный срез</p>
              </div>
              <div className="grid grid-cols-2 gap-2">
                {[
                  { id: "summary", label: "Сводка" },
                  { id: "progress", label: "Прогресс" },
                  { id: "tests", label: "Тесты" },
                  { id: "recent", label: "Попытки" },
                ].map((sectionItem) => (
                  <button
                    key={sectionItem.id}
                    onClick={() => setAnalyticsSection(sectionItem.id)}
                    className={`text-left rounded-xl border px-3 py-2 transition-colors ${analyticsSection === sectionItem.id ? "border-indigo-300 bg-indigo-50" : "border-slate-200 bg-slate-50 hover:bg-slate-100"}`}
                  >
                    <p className="text-[10px] uppercase tracking-wide text-slate-400">Раздел</p>
                    <p className="text-sm font-semibold text-slate-800 mt-0.5">{sectionItem.label}</p>
                  </button>
                ))}
              </div>
              {analyticsSection === "summary" && (
                <div className="grid grid-cols-2 gap-2">
                  {[
                    { label: "Назначено", value: courseAnalytics.assignedCount },
                    { label: "Завершили", value: courseAnalytics.completedCount },
                    { label: "В процессе", value: courseAnalytics.inProgressCount },
                    { label: "Просрочено", value: courseAnalytics.overdueCount },
                  ].map((item) => (
                    <div key={item.label} className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2">
                      <p className="text-[10px] uppercase tracking-wide text-slate-400">{item.label}</p>
                      <p className="text-sm font-semibold text-slate-800 mt-0.5">{item.value}</p>
                    </div>
                  ))}
                </div>
              )}
              {analyticsSection === "progress" && (
                <div className="grid grid-cols-2 gap-2">
                  {[
                    { label: "Сотрудников", value: courseAnalytics.learnerCount },
                    { label: "Ср. прогресс", value: `${courseAnalytics.avgProgress}%` },
                    { label: "Не начали", value: courseAnalytics.notStartedCount },
                    { label: "Завершили", value: courseAnalytics.completedCount },
                  ].map((item) => (
                    <div key={item.label} className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2">
                      <p className="text-[10px] uppercase tracking-wide text-slate-400">{item.label}</p>
                      <p className="text-sm font-semibold text-slate-800 mt-0.5">{item.value}</p>
                    </div>
                  ))}
                </div>
              )}
              {analyticsSection === "tests" && (
                <div className="grid grid-cols-2 gap-2">
                  {[
                    { label: "Попытки", value: courseAnalytics.attemptsCount },
                    { label: "Ср. балл", value: courseAnalytics.avgScore == null ? "—" : `${courseAnalytics.avgScore}%` },
                    { label: "Итог. тест", value: courseAnalytics.avgFinalScore == null ? "—" : `${courseAnalytics.avgFinalScore}%` },
                    { label: "Успешность", value: courseAnalytics.passRate == null ? "—" : `${courseAnalytics.passRate}%` },
                  ].map((item) => (
                    <div key={item.label} className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2">
                      <p className="text-[10px] uppercase tracking-wide text-slate-400">{item.label}</p>
                      <p className="text-sm font-semibold text-slate-800 mt-0.5">{item.value}</p>
                    </div>
                  ))}
                </div>
              )}
              {analyticsSection === "recent" && Array.isArray(courseAnalytics?.recentAttempts) && courseAnalytics.recentAttempts.length > 0 && (
                <div>
                  <h4 className="text-xs font-semibold text-slate-700 mb-2">Последние 3 попытки</h4>
                  <div className="space-y-2">
                    {courseAnalytics.recentAttempts.slice(0, 3).map((attemptItem) => (
                      <div key={attemptItem.key} className="rounded-lg border border-slate-100 px-3 py-2">
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-xs font-medium text-slate-800 truncate">{attemptItem.testTitle}</p>
                          <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${attemptItem.passed ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>
                            {attemptItem.passed ? "Сдан" : "Не сдан"}
                          </span>
                        </div>
                        <p className="text-[11px] text-slate-500 mt-1">{attemptItem.employeeName}{attemptItem.score != null ? ` • ${attemptItem.score}%` : ""}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {analyticsSection === "recent" && (!Array.isArray(courseAnalytics?.recentAttempts) || courseAnalytics.recentAttempts.length === 0) && (
                <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-3 py-6 text-center text-xs text-slate-500">
                  Попытки пока отсутствуют
                </div>
              )}
            </div>
          )}
          <div className="bg-white rounded-2xl border border-slate-200 p-5">
            <h3 className="text-sm font-semibold text-slate-900 mb-3">Отзывы</h3>
            <div className="text-center mb-4">
              <div className="text-3xl font-bold text-slate-900">{course.rating}</div>
              <div className="flex items-center justify-center gap-0.5 my-1">
                {[1,2,3,4,5].map(i => <Star key={i} size={14} className={i <= Math.round(course.rating) ? "text-amber-400 fill-amber-400" : "text-slate-200 fill-slate-200"} />)}
              </div>
              <div className="text-xs text-slate-400">{course.reviews} оценок</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── LESSON VIEW ──────────────────────────────────────────────────────────────

function LessonView({
  lesson,
  course,
  onBack,
  quizView,
  setQuizView,
  quizAnswers,
  setQuizAnswers,
  sidebarOpen,
  setSidebarOpen,
  onSelectLesson,
  lmsRequest,
  apiMode,
  blockTranscriptCopy,
  onCompleteLesson,
  onQuizFinished,
  emitToast,
  isManagerMode = false,
  courseAnalytics = null,
  clientSessionKey,
}) {
  const isQuiz = lesson.type === "quiz";
  const isTextLesson = lesson.type === "text";
  const isCombinedLesson = lesson.type === "combined";
  const lessonAttemptLimit = Math.max(0, Number(lesson?.maxAttempts ?? course?.maxAttempts ?? 0));
  const lessonAttemptsUsed = Math.max(0, Number(lesson?.attemptsUsed ?? course?.attemptsUsed ?? 0));
  const lessonAttemptsLeft = Math.max(0, lessonAttemptLimit - lessonAttemptsUsed);
  const lessonLocation = (() => {
    const modules = Array.isArray(course?.modules_data) ? course.modules_data : [];
    for (let moduleIndex = 0; moduleIndex < modules.length; moduleIndex += 1) {
      const lessons = Array.isArray(modules[moduleIndex]?.lessons) ? modules[moduleIndex].lessons : [];
      for (let lessonIndex = 0; lessonIndex < lessons.length; lessonIndex += 1) {
        if (String(lessons[lessonIndex]?.id) === String(lesson?.id)) {
          return { moduleNumber: moduleIndex + 1, lessonNumber: lessonIndex + 1 };
        }
      }
    }
    return { moduleNumber: 1, lessonNumber: 1 };
  })();

  return (
    <div className="flex h-full min-h-0 overflow-hidden">
      {/* Sidebar */}
      <div className={`${sidebarOpen ? "w-80 2xl:w-96" : "w-0"} flex h-full min-h-0 flex-shrink-0 flex-col transition-all duration-300 overflow-hidden bg-white border-r border-slate-200`}>
        <div className="p-4 border-b border-slate-100">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Программа курса</p>
          <p className="text-sm font-semibold text-slate-900 leading-tight">{course.title}</p>
          <div className="mt-3">
            <div className="flex justify-between text-xs text-slate-400 mb-1"><span>Прогресс</span><span>{course.progress}%</span></div>
            <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden"><div className="h-full bg-indigo-500 rounded-full" style={{ width: `${course.progress}%` }} /></div>
          </div>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto pb-20 custom-scrollbar">
          {course.modules_data.map((mod, moduleIndex) => (
            <div key={mod.id}>
              <div className="px-4 py-3 bg-slate-50 border-b border-slate-100">
                <p className="text-xs font-semibold text-slate-600">{moduleIndex + 1}. {mod.title}</p>
              </div>
              {mod.lessons.map((l, lessonIndex) => {
                const Icon = lessonIcons[l.type] || BookOpen;
                const isActive = l.id === lesson.id;
                const dl = course.deadline ? formatDeadline(course.deadline) : null;
                const lessonLocked = isManagerMode ? false : Boolean(l.locked);
                return (
                  <button key={l.id} onClick={(event) => !lessonLocked && onSelectLesson(l, event)} className={`w-full flex items-start gap-3 px-4 py-3 border-b border-slate-50 transition-colors text-left ${isActive ? "bg-indigo-50 border-l-2 border-l-indigo-500" : lessonLocked ? "opacity-50 cursor-not-allowed" : "hover:bg-slate-50"}`}>
                    <div className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5 ${isActive ? "bg-indigo-600" : isManagerMode ? "bg-indigo-50" : l.status === "completed" ? "bg-emerald-50" : lessonLocked ? "bg-slate-100" : "bg-slate-100"}`}>
                      {isActive
                        ? <Play size={11} className="text-white ml-0.5" />
                        : isManagerMode
                          ? <Icon size={12} className="text-indigo-600" />
                          : l.status === "completed"
                            ? <CheckCircle size={13} className="text-emerald-600" />
                            : lessonLocked
                              ? <Lock size={11} className="text-slate-400" />
                              : <Icon size={12} className="text-slate-500" />}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className={`text-xs font-medium leading-snug ${isActive ? "text-indigo-700" : "text-slate-700"}`}>{lessonIndex + 1}. {l.title}</p>
                      <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                        <span className="text-[10px] text-slate-400">{l.duration}</span>
                        {l.type === "quiz" && <span className="text-[10px] bg-violet-50 text-violet-600 px-1.5 py-0.5 rounded-full font-medium">Тест</span>}
                        {l.type === "combined" && <span className="text-[10px] bg-sky-50 text-sky-700 px-1.5 py-0.5 rounded-full font-medium">Комбо</span>}
                        {!isManagerMode && l.status === "in_progress" && <span className="text-[10px] bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded-full font-medium">В процессе</span>}
                        {!isManagerMode && l.requiresTest && l.status !== "completed" && !lessonLocked && <span className="text-[10px] bg-violet-50 text-violet-600 px-1.5 py-0.5 rounded-full font-medium flex items-center gap-0.5"><HelpCircle size={8} /> Тест</span>}
                        {!isManagerMode && dl?.overdue && l.status !== "completed" && <span className="text-[10px] bg-red-50 text-red-600 px-1.5 py-0.5 rounded-full font-medium">Просрочен</span>}
                        {!isManagerMode && l.status === "completed" && <span className="text-[10px] bg-emerald-50 text-emerald-600 px-1.5 py-0.5 rounded-full font-medium">✓ Завершён</span>}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 min-h-0 overflow-y-auto bg-slate-50 custom-scrollbar">
        <div className="sticky top-0 z-10 bg-white border-b border-slate-200 px-6 py-3 flex items-center gap-3">
          <button onClick={() => setSidebarOpen(!sidebarOpen)} className="p-2 rounded-lg hover:bg-slate-100 text-slate-500 transition-colors"><AlignLeft size={16} /></button>
          <div className="flex-1">
            <p className="text-xs text-slate-400">Модуль {lessonLocation.moduleNumber} · Урок {lessonLocation.lessonNumber}</p>
            <p className="text-sm font-semibold text-slate-900">{lesson.title}</p>
          </div>
          <div className="flex items-center gap-3">
            {!isManagerMode && lesson.type === "quiz" && lessonAttemptLimit > 0 && (
              <div className="flex items-center gap-1.5 text-xs text-slate-500 bg-slate-50 border border-slate-200 px-3 py-1.5 rounded-lg">
                <RefreshCw size={12} />
                <span>Попыток: <strong className={lessonAttemptsLeft <= 1 ? "text-red-600" : "text-slate-700"}>{lessonAttemptsLeft}</strong> / {lessonAttemptLimit}</span>
              </div>
            )}
            <div className="flex items-center gap-2 text-xs text-slate-500"><Clock size={13} /> {lesson.duration}</div>
          </div>
        </div>

        <div className="max-w-6xl 2xl:max-w-7xl mx-auto px-8 2xl:px-10 py-8">
          {isQuiz ? (
            isManagerMode ? (
              <ManagerQuizPreviewSection lesson={lesson} course={course} courseAnalytics={courseAnalytics} />
            ) : apiMode && Number(lesson?.apiTestId) > 0 ? (
              <ApiQuizSection
                quizView={quizView}
                setQuizView={setQuizView}
                answers={quizAnswers}
                setAnswers={setQuizAnswers}
                course={course}
                lesson={lesson}
                lmsRequest={lmsRequest}
                onFinished={onQuizFinished}
                emitToast={emitToast}
              />
            ) : (
              <QuizSection
                quizView={quizView}
                setQuizView={setQuizView}
                answers={quizAnswers}
                setAnswers={setQuizAnswers}
                course={course}
              />
            )
          ) : isCombinedLesson ? (
            <CombinedLesson
              lesson={lesson}
              course={course}
              onCompleteLesson={onCompleteLesson}
              isManagerMode={isManagerMode}
              quizView={quizView}
              setQuizView={setQuizView}
              quizAnswers={quizAnswers}
              setQuizAnswers={setQuizAnswers}
              lmsRequest={lmsRequest}
              onQuizFinished={onQuizFinished}
              emitToast={emitToast}
              blockTranscriptCopy={blockTranscriptCopy}
              clientSessionKey={clientSessionKey}
            />
          ) : isTextLesson ? (
            <TextLesson
              lesson={lesson}
              onCompleteLesson={onCompleteLesson}
              isManagerMode={isManagerMode}
              lmsRequest={lmsRequest}
              clientSessionKey={clientSessionKey}
            />
          ) : (
            <VideoLesson
              lesson={lesson}
              apiMode={apiMode}
              blockTranscriptCopy={blockTranscriptCopy}
              lmsRequest={lmsRequest}
              onCompleteLesson={onCompleteLesson}
              emitToast={emitToast}
              isManagerMode={isManagerMode}
              clientSessionKey={clientSessionKey}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function CombinedVideoBlockPlayer({
  lessonId,
  blockMaterialId,
  blockVideoUrl,
  completionThreshold = 95,
  allowFastForward = false,
  isManagerMode = false,
  postLessonEvent,
  emitToast,
  initialDurationSeconds = 0,
  initialProgress = 0,
  heartbeatIntervalMs = 5000,
  onProgressChange,
}) {
  const normalizedThreshold = Math.max(1, Math.min(100, Number(completionThreshold || 95)));
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(clampLmsProgress(initialProgress));
  const [tabHidden, setTabHidden] = useState(false);
  const [totalSeconds, setTotalSeconds] = useState(Math.max(1, Number(initialDurationSeconds || 18 * 60)));
  const [displayCurrentSeconds, setDisplayCurrentSeconds] = useState(
    Math.max(0, (clampLmsProgress(initialProgress) / 100) * Math.max(1, Number(initialDurationSeconds || 18 * 60)))
  );
  const videoRef = useRef(null);
  const progressRef = useRef(progress);
  const visibleRef = useRef(typeof document !== "undefined" ? !document.hidden : true);
  const maxAllowedSecondsRef = useRef(0);
  const seekToastAtRef = useRef(0);
  const reportIntervalRef = useRef(null);
  const reportInFlightRef = useRef(false);
  const lastProgressReportAtRef = useRef(0);
  const lastActiveReportAtRef = useRef(null);
  const lastLocalPersistAtRef = useRef(0);
  const restoredPositionRef = useRef(0);
  const totalSecondsRef = useRef(totalSeconds);
  const displayCurrentSecondsRef = useRef(displayCurrentSeconds);
  const hideControlsTimerRef = useRef(null);
  const [controlsVisible, setControlsVisible] = useState(true);

  const safeTotalSeconds = Math.max(1, Number(totalSeconds || 0));
  const currentSeconds = Math.max(0, Math.floor(Number(displayCurrentSeconds || 0)));
  const localStorageKey = useMemo(() => {
    if (isManagerMode) return "";
    return buildLmsVideoPositionStorageKey({
      scope: "combined",
      lessonId: Number(lessonId || 0),
      materialId: Number(blockMaterialId || 0),
      videoUrl: blockVideoUrl,
    });
  }, [isManagerMode, lessonId, blockMaterialId, blockVideoUrl]);
  const canTrackEvents =
    !isManagerMode
    && typeof postLessonEvent === "function"
    && Number(lessonId || 0) > 0
    && Number(blockMaterialId || 0) > 0;
  const canSeekForward = isManagerMode || Boolean(allowFastForward) || progress >= normalizedThreshold;

  const takePlaybackActiveDeltaSeconds = useCallback((options = {}) => {
    const nowTs = Date.now();
    const video = videoRef.current;
    const includeCurrent = options?.includeCurrent === true;
    const isActivelyPlaying = Boolean(
      visibleRef.current
      && video
      && !video.ended
      && (includeCurrent || !video.paused)
    );
    const lastTs = lastActiveReportAtRef.current;
    lastActiveReportAtRef.current = isActivelyPlaying ? nowTs : null;
    if (!isActivelyPlaying || !lastTs) return 0;
    const capSeconds = Math.max(1, Math.min(60, (Number(heartbeatIntervalMs || 5000) / 1000) * 2));
    return Math.max(0, Math.min((nowTs - lastTs) / 1000, capSeconds));
  }, [heartbeatIntervalMs]);

  useEffect(() => {
    totalSecondsRef.current = totalSeconds;
  }, [totalSeconds]);

  useEffect(() => {
    displayCurrentSecondsRef.current = displayCurrentSeconds;
  }, [displayCurrentSeconds]);

  const persistLocalProgress = useCallback((force = false, override = null) => {
    if (!localStorageKey) return;
    const nowTs = Date.now();
    if (!force && nowTs - lastLocalPersistAtRef.current < 1000) return;
    const video = videoRef.current;
    const duration = Math.max(1, Number(
      override?.durationSeconds
      ?? video?.duration
      ?? totalSecondsRef.current
      ?? 0
    ));
    const position = Math.max(0, Number(
      override?.positionSeconds
      ?? video?.currentTime
      ?? displayCurrentSecondsRef.current
      ?? 0
    ));
    const progressCandidate = override?.progressRatio != null
      ? Number(override.progressRatio)
      : (duration > 0 ? (position / duration) * 100 : Number(progressRef.current || 0));
    writeLmsVideoPositionToStorage(localStorageKey, {
      position_seconds: position,
      duration_seconds: duration,
      progress_ratio: clampLmsProgress(progressCandidate),
    });
    lastLocalPersistAtRef.current = nowTs;
  }, [localStorageKey]);

  useEffect(() => {
    const nextProgress = clampLmsProgress(initialProgress);
    const nextDuration = Math.max(1, Number(initialDurationSeconds || 18 * 60));
    const storedPosition = readLmsVideoPositionFromStorage(localStorageKey);
    const storedDuration = Math.max(1, Number(storedPosition?.duration_seconds || 0) || nextDuration);
    const restoredProgressByPosition = clampLmsProgress(
      storedDuration > 0
        ? (Math.max(0, Number(storedPosition?.position_seconds || 0)) / storedDuration) * 100
        : 0
    );
    const restoredProgress = clampLmsProgress(
      storedPosition?.progress_ratio != null
        ? Number(storedPosition.progress_ratio)
        : restoredProgressByPosition
    );
    const mergedProgress = Math.max(nextProgress, restoredProgress);
    const mergedDuration = Math.max(nextDuration, storedDuration);
    const storedPositionSeconds = Math.max(0, Number(storedPosition?.position_seconds || 0));
    const restoredPositionSeconds = Math.max(
      storedPositionSeconds,
      (mergedProgress / 100) * mergedDuration
    );
    setPlaying(false);
    setProgress(mergedProgress);
    setTotalSeconds(mergedDuration);
    setDisplayCurrentSeconds(restoredPositionSeconds);
    progressRef.current = mergedProgress;
    // Seed anti-seek ceiling with the actual restored position so the
    // programmatic seek in handleLoadedMetadata is not immediately
    // rewound back by handleSeeking.
    maxAllowedSecondsRef.current = Math.max(
      (mergedProgress / 100) * mergedDuration,
      storedPositionSeconds,
      restoredPositionSeconds
    );
    restoredPositionRef.current = restoredPositionSeconds;
    if (videoRef.current) {
      try {
        videoRef.current.pause();
      } catch (_) {
        // ignore
      }
    }
    writeLmsVideoPositionToStorage(localStorageKey, {
      position_seconds: restoredPositionSeconds,
      duration_seconds: mergedDuration,
      progress_ratio: mergedProgress,
    });
    lastActiveReportAtRef.current = null;
    reportInFlightRef.current = false;
    lastLocalPersistAtRef.current = Date.now();
  }, [blockMaterialId, blockVideoUrl, initialDurationSeconds, initialProgress, localStorageKey]);

  useEffect(() => {
    if (!localStorageKey || typeof window === "undefined") return undefined;
    const flushLocalProgress = () => {
      persistLocalProgress(true);
    };
    window.addEventListener("pagehide", flushLocalProgress);
    window.addEventListener("beforeunload", flushLocalProgress);
    return () => {
      flushLocalProgress();
      window.removeEventListener("pagehide", flushLocalProgress);
      window.removeEventListener("beforeunload", flushLocalProgress);
    };
  }, [localStorageKey, persistLocalProgress]);

  useEffect(() => {
    progressRef.current = progress;
    if (typeof onProgressChange === "function") {
      onProgressChange(progress);
    }
  }, [progress, onProgressChange]);

  const reportCombinedProgress = useCallback(async (options = {}) => {
    if (!canTrackEvents) return;
    if (reportInFlightRef.current && options?.force !== true) return;
    reportInFlightRef.current = true;
    const video = videoRef.current;
    const duration = Math.max(1, Number(video?.duration || safeTotalSeconds || 0));
    const position = Math.max(0, Number(video?.currentTime || displayCurrentSeconds || 0));
    const ratio = clampLmsProgress((position / duration) * 100);
    const activeDeltaSeconds = options?.activeDeltaSeconds != null
      ? Math.max(0, Number(options.activeDeltaSeconds) || 0)
      : takePlaybackActiveDeltaSeconds();
    try {
      await postLessonEvent("combined_video_progress", {
        material_id: Number(blockMaterialId),
        progress_ratio: Number(ratio.toFixed(2)),
        position_seconds: Number(position.toFixed(2)),
        duration_seconds: Number(duration.toFixed(2)),
        active_delta_seconds: Number(activeDeltaSeconds.toFixed(2)),
        tab_visible: visibleRef.current,
      });
    } catch (_) {
      // silent: non-blocking telemetry
    } finally {
      reportInFlightRef.current = false;
    }
  }, [canTrackEvents, postLessonEvent, blockMaterialId, safeTotalSeconds, displayCurrentSeconds, takePlaybackActiveDeltaSeconds]);

  useEffect(() => {
    if (!playing || !canTrackEvents) {
      clearInterval(reportIntervalRef.current);
      return undefined;
    }
    reportIntervalRef.current = setInterval(() => {
      void reportCombinedProgress();
    }, heartbeatIntervalMs);
    return () => clearInterval(reportIntervalRef.current);
  }, [playing, canTrackEvents, reportCombinedProgress, heartbeatIntervalMs]);

  useEffect(() => {
    if (isManagerMode) return undefined;
    const handleVisibilityChange = () => {
      const isVisible = !document.hidden;
      const activeDeltaSeconds = takePlaybackActiveDeltaSeconds({ includeCurrent: true });
      visibleRef.current = isVisible;
      if (!isVisible && playing) {
        setPlaying(false);
        if (videoRef.current && !videoRef.current.paused) {
          videoRef.current.pause();
        }
        persistLocalProgress(true);
        void reportCombinedProgress({ activeDeltaSeconds, force: true });
        setTabHidden(true);
        setTimeout(() => setTabHidden(false), 3000);
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, [playing, isManagerMode, persistLocalProgress, reportCombinedProgress, takePlaybackActiveDeltaSeconds]);

  const handleLoadedMetadata = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    const mediaDuration = Number(video.duration || 0);
    if (!Number.isFinite(mediaDuration) || mediaDuration <= 0) return;
    const safeDuration = Math.max(1, mediaDuration);
    setTotalSeconds((prev) => (Math.abs(prev - safeDuration) >= 0.25 ? safeDuration : prev));
    const resumeFromProgress = Math.max(0, Math.min(mediaDuration, (progressRef.current / 100) * mediaDuration));
    const resumeFromStorage = Math.max(0, Math.min(mediaDuration, Number(restoredPositionRef.current || 0)));
    const resumePosition = Math.max(resumeFromProgress, resumeFromStorage);
    // Raise anti-seek ceiling BEFORE programmatically seeking so the
    // resulting `seeking` event is not rewound by handleSeeking.
    const normalizedProgress = clampLmsProgress(progressRef.current || 0);
    const restoredAllowed = Math.max(0, (normalizedProgress / 100) * safeDuration);
    maxAllowedSecondsRef.current = Math.max(
      maxAllowedSecondsRef.current,
      restoredAllowed,
      resumePosition,
      Number(video.currentTime || 0)
    );
    if (resumePosition > 0 && Math.abs(Number(video.currentTime || 0) - resumePosition) > 1.5) {
      video.currentTime = resumePosition;
    }
    const nextSeconds = Math.max(0, Number(video.currentTime || resumePosition || 0));
    maxAllowedSecondsRef.current = Math.max(maxAllowedSecondsRef.current, nextSeconds);
    setDisplayCurrentSeconds(nextSeconds);
    persistLocalProgress(true, {
      positionSeconds: nextSeconds,
      durationSeconds: safeDuration,
      progressRatio: clampLmsProgress((nextSeconds / safeDuration) * 100),
    });
  }, [persistLocalProgress]);

  const handleVideoEnded = useCallback(() => {
    const video = videoRef.current;
    const duration = Math.max(
      1,
      Number(video?.duration || 0) > 0
        ? Number(video.duration)
        : Number(totalSecondsRef.current || 0)
    );
    setPlaying(false);
    setProgress(100);
    progressRef.current = 100;
    setDisplayCurrentSeconds(duration);
    maxAllowedSecondsRef.current = Math.max(maxAllowedSecondsRef.current, duration);
    persistLocalProgress(true, {
      positionSeconds: duration,
      durationSeconds: duration,
      progressRatio: 100,
    });
    if (canTrackEvents) {
      const activeDeltaSeconds = takePlaybackActiveDeltaSeconds({ includeCurrent: true });
      void postLessonEvent("combined_video_progress", {
        material_id: Number(blockMaterialId),
        progress_ratio: 100,
        position_seconds: Number(duration.toFixed(2)),
        duration_seconds: Number(duration.toFixed(2)),
        active_delta_seconds: Number(activeDeltaSeconds.toFixed(2)),
        tab_visible: visibleRef.current,
      }).catch(() => {});
    }
  }, [canTrackEvents, postLessonEvent, blockMaterialId, persistLocalProgress, takePlaybackActiveDeltaSeconds]);

  const maybeReportProgress = () => {
    if (!canTrackEvents) return;
    const nowTs = Date.now();
    const minIntervalMs = Math.max(heartbeatIntervalMs, 5000);
    if (nowTs - lastProgressReportAtRef.current < minIntervalMs) return;
    lastProgressReportAtRef.current = nowTs;
    void reportCombinedProgress();
  };

  const handleTimeUpdate = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    const mediaDuration = Number(video.duration || safeTotalSeconds || 0);
    const currentTime = Math.max(0, Number(video.currentTime || 0));
    if (!Number.isFinite(mediaDuration) || mediaDuration <= 0) return;
    const safeDuration = Math.max(1, mediaDuration);
    if (Math.abs(totalSeconds - safeDuration) >= 0.25) {
      setTotalSeconds(safeDuration);
    }
    maxAllowedSecondsRef.current = Math.max(maxAllowedSecondsRef.current, currentTime);
    setDisplayCurrentSeconds(currentTime);
    const nextProgress = clampLmsProgress((currentTime / safeDuration) * 100);
    setProgress(nextProgress);
    progressRef.current = nextProgress;
    persistLocalProgress(false, {
      positionSeconds: currentTime,
      durationSeconds: safeDuration,
      progressRatio: nextProgress,
    });
  }, [safeTotalSeconds, totalSeconds, persistLocalProgress]);

  const notifySeekBlocked = () => {
    const nowTs = Date.now();
    if (nowTs - seekToastAtRef.current > 1500) {
      seekToastAtRef.current = nowTs;
      emitToast?.("Перемотка вперед недоступна", "error");
    }
  };

  const seekVideoTo = (targetSeconds) => {
    const video = videoRef.current;
    if (!video) return;
    const duration = Math.max(1, Number(video.duration || safeTotalSeconds || 0));
    const boundedSeconds = Math.max(0, Math.min(duration, Number(targetSeconds || 0)));
    if (!canSeekForward && boundedSeconds > maxAllowedSecondsRef.current + 0.75) {
      const restoredSeconds = Math.max(0, maxAllowedSecondsRef.current);
      video.currentTime = restoredSeconds;
      setDisplayCurrentSeconds(restoredSeconds);
      const correctedProgress = clampLmsProgress((restoredSeconds / duration) * 100);
      setProgress(correctedProgress);
      progressRef.current = correctedProgress;
      persistLocalProgress(true, {
        positionSeconds: restoredSeconds,
        durationSeconds: duration,
        progressRatio: correctedProgress,
      });
      notifySeekBlocked();
      return;
    }
    video.currentTime = boundedSeconds;
    setDisplayCurrentSeconds(boundedSeconds);
    const nextProgress = clampLmsProgress((boundedSeconds / duration) * 100);
    setProgress(nextProgress);
    progressRef.current = nextProgress;
    if (canSeekForward) {
      maxAllowedSecondsRef.current = Math.max(maxAllowedSecondsRef.current, boundedSeconds);
    }
    persistLocalProgress(true, {
      positionSeconds: boundedSeconds,
      durationSeconds: duration,
      progressRatio: nextProgress,
    });
    maybeReportProgress();
  };

  const handleSeekSliderChange = (event) => {
    seekVideoTo(Number(event?.target?.value || 0));
  };

  const handleRewind10 = () => {
    const current = Number(videoRef.current?.currentTime || currentSeconds || 0);
    seekVideoTo(current - 10);
  };

  const handleSeeking = () => {
    const video = videoRef.current;
    if (!video || canSeekForward) return;
    const current = Math.max(0, Number(video.currentTime || 0));
    const allowed = maxAllowedSecondsRef.current + 0.75;
    if (current > allowed) {
      const restoredSeconds = Math.max(0, maxAllowedSecondsRef.current);
      video.currentTime = restoredSeconds;
      setDisplayCurrentSeconds(restoredSeconds);
      const correctedProgress = clampLmsProgress(
        (restoredSeconds / Math.max(1, Number(video.duration || safeTotalSeconds || 0))) * 100
      );
      setProgress(correctedProgress);
      progressRef.current = correctedProgress;
      persistLocalProgress(true, {
        positionSeconds: restoredSeconds,
        durationSeconds: Math.max(1, Number(video.duration || safeTotalSeconds || 0)),
        progressRatio: correctedProgress,
      });
      notifySeekBlocked();
    }
  };

  const handleVideoPlay = () => {
    lastActiveReportAtRef.current = Date.now();
    setPlaying(true);
  };

  const handleVideoPause = () => {
    const activeDeltaSeconds = takePlaybackActiveDeltaSeconds({ includeCurrent: true });
    setPlaying(false);
    persistLocalProgress(true);
    void reportCombinedProgress({ activeDeltaSeconds });
  };

  const togglePlayback = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      void video.play().catch(() => {});
      return;
    }
    video.pause();
  };

  const scheduleHideControls = useCallback(() => {
    if (hideControlsTimerRef.current) {
      clearTimeout(hideControlsTimerRef.current);
    }
    hideControlsTimerRef.current = setTimeout(() => {
      const video = videoRef.current;
      if (video && !video.paused) {
        setControlsVisible(false);
      }
    }, 2500);
  }, []);

  const revealControls = useCallback(() => {
    setControlsVisible(true);
    scheduleHideControls();
  }, [scheduleHideControls]);

  useEffect(() => {
    if (!playing) {
      setControlsVisible(true);
      if (hideControlsTimerRef.current) {
        clearTimeout(hideControlsTimerRef.current);
        hideControlsTimerRef.current = null;
      }
      return;
    }
    scheduleHideControls();
  }, [playing, scheduleHideControls]);

  useEffect(() => () => {
    if (hideControlsTimerRef.current) {
      clearTimeout(hideControlsTimerRef.current);
    }
  }, []);

  const formatVideoClock = (seconds) => {
    const safe = Math.max(0, Math.floor(Number(seconds || 0)));
    const minutes = Math.floor(safe / 60);
    const secs = safe % 60;
    return `${minutes}:${String(secs).padStart(2, "0")}`;
  };

  return (
    <div className="space-y-2">
      {tabHidden && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 flex items-center gap-2 text-xs text-amber-700">
          <AlertTriangle size={14} /> Видео поставлено на паузу — вы переключили вкладку браузера
        </div>
      )}
      <div
        className={`bg-slate-900 rounded-2xl overflow-hidden relative aspect-video ${playing && !controlsVisible ? "cursor-none" : ""}`}
        onMouseMove={revealControls}
        onMouseEnter={revealControls}
        onMouseLeave={() => { if (playing) setControlsVisible(false); }}
        onTouchStart={revealControls}
      >
        {blockVideoUrl ? (
          <>
            <video
              ref={videoRef}
              src={blockVideoUrl}
              className="w-full h-full bg-black cursor-pointer"
              controls={false}
              playsInline
              preload="metadata"
              {...LMS_PROTECTED_VIDEO_PROPS}
              onClick={togglePlayback}
              onLoadedMetadata={handleLoadedMetadata}
              onTimeUpdate={handleTimeUpdate}
              onSeeking={handleSeeking}
              onPlay={handleVideoPlay}
              onPause={handleVideoPause}
              onEnded={handleVideoEnded}
            />
            {!playing && (
              <button
                type="button"
                onClick={togglePlayback}
                className="absolute inset-0 m-auto w-16 h-16 rounded-full bg-white/20 hover:bg-white/30 text-white flex items-center justify-center transition-colors backdrop-blur-sm"
                aria-label="Play"
              >
                <Play size={26} className="ml-1" />
              </button>
            )}
            <div
              className={`absolute left-0 right-0 bottom-0 p-3 bg-gradient-to-t from-black/80 via-black/45 to-transparent transition-opacity duration-300 ${controlsVisible ? "opacity-100" : "opacity-0 pointer-events-none"}`}
              onClick={(event) => event.stopPropagation()}
            >
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleRewind10}
                  className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md bg-white/20 hover:bg-white/30 text-white text-[11px] font-semibold transition-colors"
                >
                  <ChevronLeft size={12} /> 10с
                </button>
                <input
                  type="range"
                  min={0}
                  max={Math.max(1, Math.floor(safeTotalSeconds))}
                  step={1}
                  value={Math.max(0, Math.min(Math.floor(safeTotalSeconds), currentSeconds))}
                  onChange={handleSeekSliderChange}
                  className="flex-1 accent-indigo-500"
                />
                <div className="text-[11px] text-white/90 font-mono tabular-nums min-w-[92px] text-right">
                  {formatVideoClock(currentSeconds)} / {formatVideoClock(safeTotalSeconds)}
                </div>
              </div>
            </div>
          </>
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-center px-4">
            <Video size={24} className="text-white/50" />
            <p className="text-sm text-white/70">Видео не прикреплено к блоку</p>
          </div>
        )}
      </div>
    </div>
  );
}

function CombinedLesson({
  lesson,
  course,
  onCompleteLesson,
  isManagerMode = false,
  quizView,
  setQuizView,
  quizAnswers,
  setQuizAnswers,
  lmsRequest,
  onQuizFinished,
  emitToast,
  blockTranscriptCopy = false,
  clientSessionKey,
}) {
  const effectiveBlockTranscriptCopy = !isManagerMode && Boolean(blockTranscriptCopy);
  const handleBlockTranscriptCopyAttempt = useCallback((event) => {
    if (!effectiveBlockTranscriptCopy) return;
    event.preventDefault();
  }, [effectiveBlockTranscriptCopy]);
  const resolveContentCompletion = (lessonLike) => (
    Boolean(lessonLike?.contentCompleted)
    || String(lessonLike?.status || "").toLowerCase() === "completed"
  );
  const [completing, setCompleting] = useState(false);
  const [completed, setCompleted] = useState(resolveContentCompletion(lesson));
  const [videoProgressMap, setVideoProgressMap] = useState({});
  const [isCombinedTestModalOpen, setIsCombinedTestModalOpen] = useState(false);
  const materials = Array.isArray(lesson?.materials) ? lesson.materials : [];
  const linkedTest = lesson?.combinedTest && typeof lesson.combinedTest === "object" ? lesson.combinedTest : null;
  const lessonApiId = Number(lesson?.apiLessonId || 0);
  const completionThreshold = Math.max(1, Math.min(100, Number(lesson?.completionThreshold || 95)));
  const allowFastForward = Boolean(lesson?.allowFastForward);
  const canTrackLesson = !isManagerMode && typeof lmsRequest === "function" && lessonApiId > 0;
  const heartbeatIntervalMs = resolveLmsHeartbeatIntervalMs(lesson);
  const serverVideoProgressByMaterial = useMemo(() => (
    lesson?.combinedVideoProgress && typeof lesson.combinedVideoProgress === "object"
      ? lesson.combinedVideoProgress
      : {}
  ), [lesson?.combinedVideoProgress]);
  const blocks = useMemo(() => (
    (
      Array.isArray(lesson?.combinedBlocks) && lesson.combinedBlocks.length > 0
        ? lesson.combinedBlocks
        : materials
          .filter((materialItem) => {
            const materialType = String(materialItem?.material_type || materialItem?.type || "").toLowerCase();
            return materialType === "text" || materialType === "video";
          })
          .map((materialItem, idx) => ({
            id: Number(materialItem?.id || 0) || `combined-block-${lesson?.id || "x"}-${idx + 1}`,
            type: String(materialItem?.material_type || materialItem?.type || "text").toLowerCase(),
            title: String(materialItem?.title || `Блок ${idx + 1}`),
            content_text: materialItem?.content_text || "",
            content_url: materialItem?.content_url || materialItem?.url || materialItem?.signed_url || "",
            url: materialItem?.url || materialItem?.signed_url || materialItem?.content_url || "",
            signed_url: materialItem?.signed_url || materialItem?.url || materialItem?.content_url || "",
            metadata: materialItem?.metadata && typeof materialItem.metadata === "object" ? materialItem.metadata : {},
            position: Number(materialItem?.position || idx + 1),
          }))
    )
      .slice()
      .sort((left, right) => Number(left?.position || 0) - Number(right?.position || 0))
  ), [lesson?.combinedBlocks, lesson?.id, materials]);

  const resolveBlockKey = (blockItem, blockIndex) => String(blockItem?.id || `block-${blockIndex + 1}`);
  const videoBlocks = useMemo(
    () => blocks.filter((blockItem) => String(blockItem?.type || "").toLowerCase() === "video"),
    [blocks]
  );

  useEffect(() => {
    setCompleted(resolveContentCompletion(lesson));
  }, [lesson?.id, lesson?.status, lesson?.contentCompleted]);

  useEffect(() => {
    setVideoProgressMap((prev) => {
      const next = {};
      videoBlocks.forEach((blockItem, blockIndex) => {
        const blockKey = resolveBlockKey(blockItem, blockIndex);
        const materialId = Number(blockItem?.id || blockItem?.material_id || 0);
        const serverProgress = materialId > 0
          ? clampLmsProgress(serverVideoProgressByMaterial?.[String(materialId)] ?? serverVideoProgressByMaterial?.[materialId])
          : 0;
        const prevProgress = clampLmsProgress(prev?.[blockKey]);
        next[blockKey] = completed ? 100 : Math.max(prevProgress, serverProgress);
      });
      return next;
    });
  }, [lesson?.id, lesson?.apiLessonId, completed, serverVideoProgressByMaterial, videoBlocks]);

  const allVideosReady = videoBlocks.every((blockItem, blockIndex) => {
    const blockKey = resolveBlockKey(blockItem, blockIndex);
    return clampLmsProgress(videoProgressMap?.[blockKey]) >= completionThreshold;
  });
  const linkedTestStatus = String(linkedTest?.status || "").toLowerCase();
  const linkedTestPending = Boolean(linkedTest) && linkedTestStatus !== "completed";

  useEffect(() => {
    setIsCombinedTestModalOpen(false);
  }, [lesson?.id]);

  const { postLessonEvent } = useLmsLessonSessionLifecycle({
    enabled: canTrackLesson,
    lessonId: lessonApiId,
    clientSessionKey,
    lmsRequest,
  });

  useEffect(() => {
    if (!isCombinedTestModalOpen || typeof window === "undefined") return undefined;
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        setIsCombinedTestModalOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isCombinedTestModalOpen]);

  const handleComplete = async () => {
    if (completed || completing) return;
    if (!allVideosReady) {
      emitToast?.(`Просмотрите все видео-блоки минимум на ${completionThreshold}%`, "error");
      return;
    }
    if (typeof onCompleteLesson !== "function") return;
    setCompleting(true);
    try {
      const ok = await onCompleteLesson(lesson);
      if (ok) setCompleted(true);
    } finally {
      setCompleting(false);
    }
  };

  const openCombinedTestModal = () => {
    if (!linkedTest || isManagerMode) return;
    if (typeof setQuizAnswers === "function") setQuizAnswers({});
    if (typeof setQuizView === "function") setQuizView("intro");
    setIsCombinedTestModalOpen(true);
  };

  const closeCombinedTestModal = () => {
    setIsCombinedTestModalOpen(false);
  };

  return (
    <div className="space-y-5">
      {blocks.length === 0 && (
        <div className="bg-white rounded-2xl border border-dashed border-slate-300 px-5 py-8 text-center text-sm text-slate-500">
          Блоки урока не добавлены
        </div>
      )}
      {blocks.map((blockItem, blockIndex) => {
        const blockType = String(blockItem?.type || "text").toLowerCase();
        const blockTitle = String(blockItem?.title || (blockType === "video" ? `Видео блок ${blockIndex + 1}` : `Текстовый блок ${blockIndex + 1}`));
        if (blockType === "video") {
          const videoUrl = String(blockItem?.url || blockItem?.signed_url || blockItem?.content_url || "").trim();
          const blockKey = resolveBlockKey(blockItem, blockIndex);
          const materialId = Number(blockItem?.id || blockItem?.material_id || 0);
          const transcriptRich = normalizeRichTextValue(blockItem?.content_text || blockItem?.contentText || "");
          const initialProgress = materialId > 0
            ? clampLmsProgress(serverVideoProgressByMaterial?.[String(materialId)] ?? serverVideoProgressByMaterial?.[materialId])
            : 0;
          return (
            <div key={blockKey} className="space-y-3">
              <div className="bg-white rounded-2xl border border-slate-200 p-5">
                <div className="flex items-center gap-2 mb-3">
                  <span className="w-7 h-7 rounded-lg bg-blue-50 text-blue-600 flex items-center justify-center"><Video size={14} /></span>
                  <h3 className="text-sm font-semibold text-slate-900">{blockTitle}</h3>
                </div>
                <CombinedVideoBlockPlayer
                  lessonId={lessonApiId}
                  blockMaterialId={materialId}
                  blockVideoUrl={videoUrl}
                  completionThreshold={completionThreshold}
                  allowFastForward={allowFastForward}
                  isManagerMode={isManagerMode}
                  postLessonEvent={postLessonEvent}
                  emitToast={emitToast}
                  initialDurationSeconds={Number(blockItem?.metadata?.duration_seconds || 0)}
                  initialProgress={initialProgress}
                  heartbeatIntervalMs={heartbeatIntervalMs}
                  onProgressChange={(nextProgress) => {
                    setVideoProgressMap((prev) => {
                      const prevProgress = clampLmsProgress(prev?.[blockKey]);
                      const normalizedNext = clampLmsProgress(nextProgress);
                      if (Math.abs(prevProgress - normalizedNext) < 0.2) return prev;
                      return { ...prev, [blockKey]: normalizedNext };
                    });
                  }}
                />
              </div>
              <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                <div className="flex border-b border-slate-100">
                  <div className="flex items-center gap-2 px-5 py-3.5 text-xs font-medium border-b-2 border-indigo-500 text-indigo-600">
                    <AlignLeft size={13} /> Транскрипт
                  </div>
                </div>
                <div className="p-5">
                  <div
                    className="max-h-[min(420px,55vh)] overflow-y-auto custom-scrollbar pr-2 text-sm text-slate-600 leading-relaxed"
                    onCopy={handleBlockTranscriptCopyAttempt}
                    onCut={handleBlockTranscriptCopyAttempt}
                    onContextMenu={handleBlockTranscriptCopyAttempt}
                    onDragStart={handleBlockTranscriptCopyAttempt}
                    onSelectStart={handleBlockTranscriptCopyAttempt}
                    style={effectiveBlockTranscriptCopy ? { userSelect: "none", WebkitUserSelect: "none" } : undefined}
                  >
                    <RichTextContent
                      value={transcriptRich}
                      emptyState={<div className="text-xs text-slate-400">Транскрипт видео-блока не заполнен</div>}
                    />
                  </div>
                </div>
              </div>
            </div>
          );
        }
        const richText = normalizeRichTextValue(blockItem?.content_text || "");
        return (
          <div key={blockItem?.id || `${blockType}-${blockIndex + 1}`} className="bg-white rounded-2xl border border-slate-200 p-6">
            <div className="flex items-center gap-2 mb-3">
              <span className="w-7 h-7 rounded-lg bg-emerald-50 text-emerald-600 flex items-center justify-center"><FileText size={14} /></span>
              <h3 className="text-sm font-semibold text-slate-900">{blockTitle}</h3>
            </div>
            <RichTextContent
              value={richText}
              className="text-sm text-slate-700 leading-relaxed"
              emptyState={<div className="text-xs text-slate-400">Текст блока не заполнен</div>}
            />
          </div>
        );
      })}

      {!isManagerMode && !completed && (
        <div className="space-y-2">
          {!allVideosReady && videoBlocks.length > 0 && (
            <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2">
              Для завершения урока просмотрите все видео-блоки минимум на {completionThreshold}%.
            </div>
          )}
          <div className="flex justify-end">
            <button
              onClick={handleComplete}
              disabled={completing || !allVideosReady}
              className="inline-flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold px-4 py-2.5 rounded-xl transition-colors"
            >
              {completing ? <RefreshCw size={14} className="animate-spin" /> : <CheckCircle size={14} />}
              {completing ? "Сохранение..." : "Отметить урок пройденным"}
            </button>
          </div>
        </div>
      )}

      {false && completed && (
        linkedTest && !isManagerMode ? (
          <ApiQuizSection
            quizView={quizView}
            setQuizView={setQuizView}
            answers={quizAnswers}
            setAnswers={setQuizAnswers}
            course={course}
            lesson={linkedTest}
            lmsRequest={lmsRequest}
            onFinished={onQuizFinished}
            emitToast={emitToast}
          />
        ) : (
          <div className="flex items-center gap-1.5 text-xs text-emerald-700 bg-emerald-50 px-3 py-2 rounded-xl font-semibold w-fit">
            <CheckCircle size={12} /> Урок завершен
          </div>
        )
      )}
      {completed && !isManagerMode && linkedTest && (() => {
        const lt = linkedTest;
        const ltPassed = linkedTestStatus === "completed";
        const ltExhausted = lt.attemptsUsed > 0 && lt.attemptsUsed >= lt.maxAttempts && !ltPassed;
        const ltAttempted = lt.attemptsUsed > 0;
        const ltAttemptsLeft = Math.max(0, Number(lt.maxAttempts || 0) - Number(lt.attemptsUsed || 0));
        const ltPassingScore = Math.round(Number(lt.passingScore || 80));
        const ltScore = lt.score != null ? Math.round(Number(lt.score)) : null;
        const accent = ltPassed
          ? { icon: "bg-emerald-100 text-emerald-600", chip: "bg-emerald-50 text-emerald-700", bar: "bg-emerald-500", btn: "bg-emerald-600 hover:bg-emerald-700" }
          : ltExhausted
            ? { icon: "bg-red-100 text-red-600", chip: "bg-red-50 text-red-700", bar: "bg-red-500", btn: "bg-red-600 hover:bg-red-700" }
            : ltAttempted
              ? { icon: "bg-amber-100 text-amber-600", chip: "bg-amber-50 text-amber-700", bar: "bg-amber-500", btn: "bg-indigo-600 hover:bg-indigo-700" }
              : { icon: "bg-indigo-100 text-indigo-600", chip: "bg-indigo-50 text-indigo-700", bar: "bg-indigo-500", btn: "bg-indigo-600 hover:bg-indigo-700" };
        const statusLabel = ltPassed
          ? "Тест пройден"
          : ltExhausted
            ? "Тест не пройден"
            : ltAttempted
              ? "Можно улучшить результат"
              : "Можно пройти тест";
        const buttonLabel = ltExhausted
          ? "Попытки исчерпаны"
          : ltPassed
            ? "Просмотр результатов"
            : ltAttempted
              ? "Пересдать тест"
              : "Перейти к тесту";
        const ButtonIcon = ltPassed ? CheckCircle : ltAttempted ? RefreshCw : PlayCircle;
        return (
          <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
            <div className="p-5 flex flex-col sm:flex-row sm:items-center gap-4">
              <div className={`w-12 h-12 rounded-2xl flex items-center justify-center flex-shrink-0 ${accent.icon}`}>
                {ltPassed ? <CheckCircle size={22} /> : ltExhausted ? <XCircle size={22} /> : <HelpCircle size={22} />}
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap mb-1">
                  <p className="text-sm font-semibold text-slate-900 truncate">{lt.title || "Тест по уроку"}</p>
                  <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full whitespace-nowrap ${accent.chip}`}>{statusLabel}</span>
                </div>
                {ltScore != null ? (
                  <div className="flex items-baseline gap-2">
                    <span className="text-2xl font-bold text-slate-900 leading-none">{ltScore}%</span>
                    <span className="text-[11px] text-slate-500">лучший результат · порог {ltPassingScore}%</span>
                  </div>
                ) : (
                  <p className="text-xs text-slate-500">
                    {ltExhausted ? "Попытки исчерпаны" : `Порог прохождения ${ltPassingScore}% · ${lt.questionCount || "—"} вопросов`}
                  </p>
                )}
                <div className="flex items-center gap-3 mt-2 text-[11px] text-slate-500 flex-wrap">
                  <span className="inline-flex items-center gap-1"><Clock size={11} /> {lt.duration || "—"}</span>
                  {Number(lt.maxAttempts || 0) > 0 && (
                    <span className="inline-flex items-center gap-1">
                      <RefreshCw size={11} />
                      Попыток: <strong className={ltAttemptsLeft <= 1 && !ltPassed ? "text-red-600" : "text-slate-700"}>{ltAttemptsLeft}</strong> / {lt.maxAttempts}
                    </span>
                  )}
                </div>
              </div>
              <button
                type="button"
                onClick={openCombinedTestModal}
                disabled={ltExhausted && !ltPassed}
                className={`inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-white text-xs font-semibold transition-colors whitespace-nowrap disabled:opacity-60 disabled:cursor-not-allowed ${accent.btn}`}
              >
                <ButtonIcon size={13} />
                {buttonLabel}
              </button>
            </div>
            {ltScore != null && (
              <div className="h-1 bg-slate-100">
                <div className={`h-full ${accent.bar} transition-all`} style={{ width: `${Math.min(100, Math.max(0, ltScore))}%` }} />
              </div>
            )}
          </div>
        );
      })()}
      {completed && (!linkedTest || isManagerMode) && (
        <div className="flex items-center gap-1.5 text-xs text-emerald-700 bg-emerald-50 px-3 py-2 rounded-xl font-semibold w-fit">
          <CheckCircle size={12} /> {"\u0423\u0440\u043e\u043a \u0437\u0430\u0432\u0435\u0440\u0448\u0435\u043d"}
        </div>
      )}
      {isCombinedTestModalOpen && linkedTest && !isManagerMode && (
        <div className="fixed inset-0 z-[110] flex items-center justify-center p-4 bg-slate-900/55 backdrop-blur-sm" onClick={closeCombinedTestModal}>
          <div className="w-full max-w-5xl max-h-[92vh] bg-white rounded-2xl border border-slate-200 shadow-xl flex flex-col overflow-hidden" onClick={(event) => event.stopPropagation()}>
            <div className="px-5 py-3 border-b border-slate-200 bg-slate-50 flex items-center justify-between gap-3">
              <div>
                <p className="text-[11px] uppercase tracking-wide text-slate-500">{"\u041a\u043e\u043c\u0431\u043e-\u0443\u0440\u043e\u043a"}</p>
                <p className="text-sm font-semibold text-slate-900">{linkedTest?.title || "Тест"}</p>
              </div>
              <button
                type="button"
                onClick={closeCombinedTestModal}
                className="inline-flex items-center justify-center p-2 rounded-lg text-slate-500 hover:text-slate-700 hover:bg-white border border-slate-200 transition-colors"
                aria-label={"\u0417\u0430\u043a\u0440\u044b\u0442\u044c"}
              >
                <X size={14} />
              </button>
            </div>
            <div className="p-4 sm:p-5 bg-slate-50 overflow-y-auto custom-scrollbar">
              <ApiQuizSection
                quizView={quizView}
                setQuizView={setQuizView}
                answers={quizAnswers}
                setAnswers={setQuizAnswers}
                course={course}
                lesson={linkedTest}
                lmsRequest={lmsRequest}
                onFinished={onQuizFinished}
                emitToast={emitToast}
              />
            </div>
          </div>
        </div>
      )}
      {isManagerMode && linkedTest && (
        <div className="rounded-xl border border-violet-200 bg-violet-50 px-4 py-3 text-xs text-violet-700">
          После завершения блоков у сотрудника станет доступен тест: <strong>{linkedTest.title || "Тест"}</strong>
        </div>
      )}
    </div>
  );
}

function TextLesson({
  lesson,
  onCompleteLesson,
  isManagerMode = false,
  lmsRequest,
  clientSessionKey,
}) {
  const [completing, setCompleting] = useState(false);
  const [completed, setCompleted] = useState(String(lesson?.status || "").toLowerCase() === "completed");
  const materials = Array.isArray(lesson?.materials) ? lesson.materials : [];
  const transcriptMaterial = materials.find((item) => String(item?.material_type || "").toLowerCase() === "text" && item?.content_text);
  const content = normalizeRichTextValue(transcriptMaterial?.content_text || lesson?.description || "");
  const lessonFiles = materials.filter((item) => {
    const type = String(item?.material_type || "").toLowerCase();
    if (type === "text") return false;
    return Boolean(item?.url || item?.signed_url || item?.content_url);
  });
  const lessonId = Number(lesson?.apiLessonId || 0);
  const canTrack = !isManagerMode && typeof lmsRequest === "function" && lessonId > 0;
  const heartbeatIntervalMs = resolveLmsHeartbeatIntervalMs(lesson);
  const idleTimeoutMs = 60_000;
  const visibleRef = useRef(typeof document !== "undefined" ? !document.hidden : true);
  const focusedRef = useRef(typeof document !== "undefined" ? document.hasFocus?.() !== false : true);
  const confirmedSecondsRef = useRef(Math.max(0, Number(lesson?.apiProgress?.confirmed_seconds || 0)));
  const lastActivityAtRef = useRef(Date.now());
  const lastAccumulatedAtRef = useRef(Date.now());
  const lastActivitySignalAtRef = useRef(0);
  const heartbeatRef = useRef(null);

  useEffect(() => {
    setCompleted(String(lesson?.status || "").toLowerCase() === "completed");
  }, [lesson?.id, lesson?.status]);

  useEffect(() => {
    confirmedSecondsRef.current = Math.max(0, Number(lesson?.apiProgress?.confirmed_seconds || 0));
    const nowTs = Date.now();
    lastActivityAtRef.current = nowTs;
    lastAccumulatedAtRef.current = nowTs;
  }, [lesson?.id, lesson?.apiProgress?.confirmed_seconds]);

  const flushTrackedSeconds = useCallback(() => {
    const nowTs = Date.now();
    const lastTick = lastAccumulatedAtRef.current || nowTs;
    const isActiveUser = nowTs - lastActivityAtRef.current <= idleTimeoutMs;
    if (visibleRef.current && focusedRef.current && isActiveUser) {
      confirmedSecondsRef.current += Math.max(0, (nowTs - lastTick) / 1000);
    }
    lastAccumulatedAtRef.current = nowTs;
    return confirmedSecondsRef.current;
  }, [idleTimeoutMs]);

  const sendTextHeartbeat = useCallback(async (options = {}) => {
    if (!canTrack) return null;
    const confirmedSeconds = flushTrackedSeconds();
    const effectiveDuration = Math.max(1, Number(lesson?.durationSeconds || confirmedSeconds || 0));
    const tabVisible = Boolean(
      visibleRef.current
      && focusedRef.current
      && Date.now() - lastActivityAtRef.current <= idleTimeoutMs
    );
    return lmsRequest(`/api/lms/lessons/${lessonId}/heartbeat`, {
      method: "POST",
      keepalive: options?.keepalive === true,
      body: {
        position_seconds: Number(confirmedSeconds.toFixed(2)),
        media_duration_seconds: Number(effectiveDuration.toFixed(2)),
        tab_visible: tabVisible,
        client_session_key: clientSessionKey || undefined,
        client_ts: new Date().toISOString(),
      },
    });
  }, [canTrack, flushTrackedSeconds, lesson?.durationSeconds, lessonId, lmsRequest, clientSessionKey, idleTimeoutMs]);

  useLmsLessonSessionLifecycle({
    enabled: canTrack,
    lessonId,
    clientSessionKey,
    lmsRequest,
    onHidden: () => {
      flushTrackedSeconds();
      visibleRef.current = false;
      void sendTextHeartbeat().catch(() => {});
    },
    onVisible: () => {
      visibleRef.current = true;
      const nowTs = Date.now();
      lastActivityAtRef.current = nowTs;
      lastAccumulatedAtRef.current = nowTs;
    },
    onBeforeClose: () => {
      flushTrackedSeconds();
    },
  });

  useEffect(() => {
    if (!canTrack || typeof window === "undefined") return undefined;
    const handleFocus = () => {
      focusedRef.current = true;
      const nowTs = Date.now();
      lastActivityAtRef.current = nowTs;
      lastAccumulatedAtRef.current = nowTs;
    };
    const handleBlur = () => {
      flushTrackedSeconds();
      focusedRef.current = false;
      void sendTextHeartbeat().catch(() => {});
    };
    window.addEventListener("focus", handleFocus);
    window.addEventListener("blur", handleBlur);
    return () => {
      window.removeEventListener("focus", handleFocus);
      window.removeEventListener("blur", handleBlur);
    };
  }, [canTrack, flushTrackedSeconds, sendTextHeartbeat]);

  useEffect(() => {
    if (!canTrack || typeof window === "undefined") return undefined;
    const markActivity = (event) => {
      const nowTs = Date.now();
      if (event?.type === "mousemove" && nowTs - lastActivitySignalAtRef.current < 10_000) return;
      if (event?.type === "scroll" && nowTs - lastActivitySignalAtRef.current < 2_000) return;
      lastActivitySignalAtRef.current = nowTs;
      lastActivityAtRef.current = nowTs;
    };
    window.addEventListener("pointerdown", markActivity, { passive: true });
    window.addEventListener("keydown", markActivity);
    window.addEventListener("scroll", markActivity, { passive: true });
    window.addEventListener("touchstart", markActivity, { passive: true });
    window.addEventListener("mousemove", markActivity, { passive: true });
    return () => {
      window.removeEventListener("pointerdown", markActivity);
      window.removeEventListener("keydown", markActivity);
      window.removeEventListener("scroll", markActivity);
      window.removeEventListener("touchstart", markActivity);
      window.removeEventListener("mousemove", markActivity);
    };
  }, [canTrack]);

  useEffect(() => {
    if (!canTrack) {
      clearInterval(heartbeatRef.current);
      return undefined;
    }
    heartbeatRef.current = setInterval(() => {
      void sendTextHeartbeat().catch(() => {});
    }, heartbeatIntervalMs);
    return () => clearInterval(heartbeatRef.current);
  }, [canTrack, sendTextHeartbeat, heartbeatIntervalMs]);

  const handleComplete = async () => {
    if (completed || completing) return;
    if (typeof onCompleteLesson !== "function") return;
    setCompleting(true);
    try {
      if (canTrack) {
        try {
          await sendTextHeartbeat();
        } catch (_) {
          // non-blocking sync before complete
        }
      }
      const ok = await onCompleteLesson(lesson);
      if (ok) setCompleted(true);
    } finally {
      setCompleting(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-2xl border border-slate-200 p-6">
        <h3 className="text-sm font-semibold text-slate-900 mb-3">Материал урока</h3>
        <RichTextContent
          value={content}
          className="text-sm text-slate-700 leading-relaxed"
          emptyState={<div className="text-xs text-slate-400">Текст урока не заполнен</div>}
        />
      </div>

      <div className="bg-white rounded-2xl border border-slate-200 p-5">
        <h3 className="text-sm font-semibold text-slate-900 mb-3">Дополнительные материалы</h3>
        <div className="space-y-2">
          {lessonFiles.length === 0 && (
            <div className="text-xs text-slate-400">Материалы не добавлены</div>
          )}
          {lessonFiles.map((material, idx) => {
            const name = String(material?.title || `Материал ${idx + 1}`);
            const metaName = String(material?.metadata?.uploaded_file_name || "");
            const label = metaName || name;
            const fileUrl = String(material?.url || material?.signed_url || material?.content_url || "").trim();
            return (
              <a
                key={`${material?.id || idx}-${label}`}
                href={fileUrl || "#"}
                rel="noopener noreferrer"
                download={label}
                className="lms-file-link"
              >
                <span className="lms-file-icon" aria-hidden="true" />
                <span className="lms-file-content">
                  <span className="lms-file-title">{label}</span>
                  <span className="lms-file-subtitle">Нажмите, чтобы скачать</span>
                </span>
                <span className="lms-file-download">Скачать</span>
              </a>
            );
          })}
        </div>
      </div>

      {!isManagerMode && !completed && (
        <div className="flex justify-end">
          <button
            onClick={handleComplete}
            disabled={completing}
            className="inline-flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold px-4 py-2.5 rounded-xl transition-colors"
          >
            {completing ? <RefreshCw size={14} className="animate-spin" /> : <CheckCircle size={14} />}
            {completing ? "Сохранение..." : "Отметить как завершенный"}
          </button>
        </div>
      )}
      {completed && (
        <div className="flex items-center gap-1.5 text-xs text-emerald-700 bg-emerald-50 px-3 py-2 rounded-xl font-semibold w-fit">
          <CheckCircle size={12} /> Урок завершён
        </div>
      )}
    </div>
  );
}

// ─── VIDEO LESSON ─────────────────────────────────────────────────────────────

function VideoLesson({
  lesson,
  apiMode,
  blockTranscriptCopy = false,
  lmsRequest,
  onCompleteLesson,
  emitToast,
  isManagerMode = false,
  clientSessionKey,
}) {
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(Math.max(0, Math.min(100, Number(lesson?.completionRatio ?? (String(lesson?.status || "").toLowerCase() === "completed" ? 100 : 0)))));
  const [activeTab, setActiveTab] = useState("transcript");
  const [tabHidden, setTabHidden] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [completed, setCompleted] = useState(String(lesson?.status || "").toLowerCase() === "completed");
  const [totalSeconds, setTotalSeconds] = useState(Math.max(1, Number(lesson?.durationSeconds || 18 * 60)));
  const [displayCurrentSeconds, setDisplayCurrentSeconds] = useState(0);
  const heartbeatRef = useRef(null);
  const heartbeatInFlightRef = useRef(false);
  const sendHeartbeatRef = useRef(null);
  const videoRef = useRef(null);
  const playingRef = useRef(playing);
  const progressRef = useRef(progress);
  const visibleRef = useRef(typeof document !== "undefined" ? !document.hidden : true);
  const maxAllowedSecondsRef = useRef(0);
  const seekToastAtRef = useRef(0);
  const autoCompleteTriggeredRef = useRef(false);
  const lastActiveHeartbeatAtRef = useRef(null);
  const lastLocalPersistAtRef = useRef(0);
  const restoredPositionRef = useRef(0);
  const totalSecondsRef = useRef(totalSeconds);
  const displayCurrentSecondsRef = useRef(displayCurrentSeconds);
  const hideControlsTimerRef = useRef(null);
  const [controlsVisible, setControlsVisible] = useState(true);

  const lessonId = Number(lesson?.apiLessonId || 0);
  const canTrack = !isManagerMode && apiMode && typeof lmsRequest === "function" && lessonId > 0;
  const heartbeatIntervalMs = resolveLmsHeartbeatIntervalMs(lesson);
  const completionThreshold = Math.max(1, Math.min(100, Number(lesson?.completionThreshold || 95)));
  const canSeekForward = isManagerMode || Boolean(lesson?.allowFastForward) || completed || progress >= completionThreshold;
  const effectiveBlockTranscriptCopy = !isManagerMode && Boolean(blockTranscriptCopy);
  const materials = Array.isArray(lesson?.materials) ? lesson.materials : [];
  const videoMaterial = materials.find((item) => {
    const type = String(item?.material_type || item?.type || "").toLowerCase();
    const url = item?.url || item?.signed_url || item?.content_url;
    return type === "video" && Boolean(url);
  });
  const videoDurationMeta = Number(videoMaterial?.metadata?.duration_seconds || 0);
  const videoUrl = videoMaterial?.url || videoMaterial?.signed_url || videoMaterial?.content_url || "";
  const videoMaterialId = Number(videoMaterial?.id || videoMaterial?.material_id || 0);
  const safeTotalSeconds = Math.max(1, Number(totalSeconds || 0));
  const currentSeconds = Math.max(0, Math.floor(Number(displayCurrentSeconds || 0)));
  const videoStorageKey = useMemo(() => {
    if (isManagerMode) return "";
    return buildLmsVideoPositionStorageKey({
      scope: "lesson",
      lessonId: lessonId || Number(lesson?.id || 0),
      materialId: videoMaterialId,
      videoUrl,
    });
  }, [isManagerMode, lessonId, lesson?.id, videoMaterialId, videoUrl]);
  const transcriptMaterial = materials.find((item) => String(item?.material_type || "").toLowerCase() === "text" && item?.content_text);
  const transcriptText = normalizeRichTextValue(transcriptMaterial?.content_text || lesson?.description || "");
  const lessonFiles = materials.filter((item) => {
    const type = String(item?.material_type || item?.type || "").toLowerCase();
    if (type === "video") return false;
    return Boolean(item?.url || item?.signed_url || item?.content_url);
  });

  const takePlaybackActiveDeltaSeconds = useCallback((options = {}) => {
    const nowTs = Date.now();
    const video = videoRef.current;
    const includeCurrent = options?.includeCurrent === true;
    const isActivelyPlaying = Boolean(
      visibleRef.current
      && video
      && !video.ended
      && (includeCurrent || !video.paused)
    );
    const lastTs = lastActiveHeartbeatAtRef.current;
    lastActiveHeartbeatAtRef.current = isActivelyPlaying ? nowTs : null;
    if (!isActivelyPlaying || !lastTs) return 0;
    const capSeconds = Math.max(1, Math.min(60, (Number(heartbeatIntervalMs || 5000) / 1000) * 2));
    return Math.max(0, Math.min((nowTs - lastTs) / 1000, capSeconds));
  }, [heartbeatIntervalMs]);

  useEffect(() => {
    totalSecondsRef.current = totalSeconds;
  }, [totalSeconds]);

  useEffect(() => {
    displayCurrentSecondsRef.current = displayCurrentSeconds;
  }, [displayCurrentSeconds]);

  useEffect(() => {
    playingRef.current = playing;
  }, [playing]);

  const persistLocalProgress = useCallback((force = false, override = null) => {
    if (!videoStorageKey) return;
    const nowTs = Date.now();
    if (!force && nowTs - lastLocalPersistAtRef.current < 1000) return;
    const video = videoRef.current;
    const duration = Math.max(1, Number(
      override?.durationSeconds
      ?? video?.duration
      ?? totalSecondsRef.current
      ?? 0
    ));
    const position = Math.max(0, Number(
      override?.positionSeconds
      ?? video?.currentTime
      ?? displayCurrentSecondsRef.current
      ?? 0
    ));
    const progressCandidate = override?.progressRatio != null
      ? Number(override.progressRatio)
      : (duration > 0 ? (position / duration) * 100 : Number(progressRef.current || 0));
    writeLmsVideoPositionToStorage(videoStorageKey, {
      position_seconds: position,
      duration_seconds: duration,
      progress_ratio: clampLmsProgress(progressCandidate),
    });
    lastLocalPersistAtRef.current = nowTs;
  }, [videoStorageKey]);

  useLmsLessonSessionLifecycle({
    enabled: canTrack,
    lessonId,
    clientSessionKey,
    lmsRequest,
    onHidden: () => {
      const activeDeltaSeconds = takePlaybackActiveDeltaSeconds({ includeCurrent: true });
      visibleRef.current = false;
      if (playingRef.current) {
        setPlaying(false);
        if (videoRef.current && !videoRef.current.paused) {
          videoRef.current.pause();
        }
      }
      persistLocalProgress(true);
      setTabHidden(true);
      setTimeout(() => setTabHidden(false), 3000);
      const pendingHeartbeat = sendHeartbeatRef.current?.({ activeDeltaSeconds });
      if (pendingHeartbeat && typeof pendingHeartbeat.catch === "function") {
        void pendingHeartbeat.catch(() => {});
      }
    },
    onVisible: () => {
      visibleRef.current = true;
    },
    onBeforeClose: () => {
      const activeDeltaSeconds = takePlaybackActiveDeltaSeconds({ includeCurrent: true });
      persistLocalProgress(true);
      const pendingHeartbeat = sendHeartbeatRef.current?.({ keepalive: true, activeDeltaSeconds });
      if (pendingHeartbeat && typeof pendingHeartbeat.catch === "function") {
        void pendingHeartbeat.catch(() => {});
      }
    },
  });

  // Full hydration — runs only when the lesson identity (or its video
  // source/storage key) changes. Intentionally independent of
  // lesson.completionRatio / lesson.status so that our own completion
  // flow (which updates those fields on the parent) does NOT re-pause the
  // video, reset playback state, or make the slider oscillate.
  useEffect(() => {
    const isLessonCompleted = String(lesson?.status || "").toLowerCase() === "completed";
    const next = clampLmsProgress(lesson?.completionRatio ?? (isLessonCompleted ? 100 : 0));
    const fallbackDuration = Math.max(1, Number(videoDurationMeta || lesson?.durationSeconds || 18 * 60));
    const storedPosition = isLessonCompleted ? null : readLmsVideoPositionFromStorage(videoStorageKey);
    const storedDuration = Math.max(1, Number(storedPosition?.duration_seconds || 0) || fallbackDuration);
    const storedPositionSeconds = Math.max(0, Number(storedPosition?.position_seconds || 0));
    const restoredProgressByPosition = clampLmsProgress(
      storedDuration > 0
        ? (storedPositionSeconds / storedDuration) * 100
        : 0
    );
    const restoredProgress = clampLmsProgress(
      storedPosition?.progress_ratio != null
        ? Number(storedPosition.progress_ratio)
        : restoredProgressByPosition
    );
    const mergedProgress = isLessonCompleted ? 100 : Math.max(next, restoredProgress);
    const mergedDuration = Math.max(fallbackDuration, storedDuration);
    const restoredPositionSeconds = isLessonCompleted
      ? mergedDuration
      : Math.max(
        storedPositionSeconds,
        (mergedProgress / 100) * mergedDuration
      );

    setProgress(mergedProgress);
    setTotalSeconds(mergedDuration);
    setDisplayCurrentSeconds(restoredPositionSeconds);
    progressRef.current = mergedProgress;
    setCompleted(isLessonCompleted);
    setPlaying(false);
    // Seed the anti-seek ceiling with the larger of (progress-derived seconds,
    // stored position, restored resume position). Otherwise the initial
    // programmatic seek in handleLoadedMetadata — which restores the user
    // to their last position — is immediately rewound by handleSeeking
    // because allowed < currentTime when progress_ratio was persisted
    // slightly above position_seconds/duration.
    maxAllowedSecondsRef.current = Math.max(
      0,
      (mergedProgress / 100) * mergedDuration,
      storedPositionSeconds,
      restoredPositionSeconds
    );
    restoredPositionRef.current = restoredPositionSeconds;
    if (videoRef.current) {
      try {
        videoRef.current.pause();
      } catch (_) {
        // ignore
      }
    }
    if (isLessonCompleted) {
      clearLmsVideoPositionFromStorage(videoStorageKey);
    } else {
      writeLmsVideoPositionToStorage(videoStorageKey, {
        position_seconds: restoredPositionSeconds,
        duration_seconds: mergedDuration,
        progress_ratio: mergedProgress,
      });
      lastLocalPersistAtRef.current = Date.now();
    }
    lastActiveHeartbeatAtRef.current = null;
    heartbeatInFlightRef.current = false;
    autoCompleteTriggeredRef.current = false;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videoStorageKey]);

  // Light sync of the "completed" flag when the lesson arrives already
  // completed (e.g. from an external tab or after our own complete call).
  // This intentionally does NOT touch video playback or the progress
  // bar position so it will not interfere with live playback.
  useEffect(() => {
    const isLessonCompleted = String(lesson?.status || "").toLowerCase() === "completed";
    if (!isLessonCompleted) return;
    setCompleted(true);
    setProgress((prev) => (prev >= 100 ? prev : 100));
    progressRef.current = Math.max(progressRef.current, 100);
    clearLmsVideoPositionFromStorage(videoStorageKey);
  }, [lesson?.status, videoStorageKey]);

  useEffect(() => {
    if (!videoStorageKey || typeof window === "undefined") return undefined;
    const flushLocalProgress = () => {
      persistLocalProgress(true);
    };
    window.addEventListener("pagehide", flushLocalProgress);
    window.addEventListener("beforeunload", flushLocalProgress);
    return () => {
      flushLocalProgress();
      window.removeEventListener("pagehide", flushLocalProgress);
      window.removeEventListener("beforeunload", flushLocalProgress);
    };
  }, [videoStorageKey, persistLocalProgress]);

  useEffect(() => {
    if (!completed) return;
    clearLmsVideoPositionFromStorage(videoStorageKey);
  }, [completed, videoStorageKey]);

  useEffect(() => {
    progressRef.current = progress;
  }, [progress]);

  const sendHeartbeat = useCallback(async (options = {}) => {
    if (!canTrack) return null;
    const positionSecondsFromVideo = Number(videoRef.current?.currentTime || 0);
    const mediaDurationSeconds = Number(videoRef.current?.duration || safeTotalSeconds || 0);
    const localSeconds = Number.isFinite(positionSecondsFromVideo) && positionSecondsFromVideo >= 0
      ? positionSecondsFromVideo
      : Math.floor((progressRef.current * safeTotalSeconds) / 100);
    const activeDeltaSeconds = options?.activeDeltaSeconds != null
      ? Math.max(0, Number(options.activeDeltaSeconds) || 0)
      : takePlaybackActiveDeltaSeconds();
    const payload = await lmsRequest(`/api/lms/lessons/${lessonId}/heartbeat`, {
      method: "POST",
      keepalive: options?.keepalive === true,
      body: {
        position_seconds: Math.max(0, Number(localSeconds.toFixed(2))),
        media_duration_seconds: Number.isFinite(mediaDurationSeconds) && mediaDurationSeconds > 0
          ? Number(mediaDurationSeconds.toFixed(2))
          : undefined,
        tab_visible: visibleRef.current,
        active_delta_seconds: Number(activeDeltaSeconds.toFixed(2)),
        client_session_key: clientSessionKey || undefined,
        client_ts: new Date().toISOString(),
      },
    });
    if (payload?.position_seconds != null && Number.isFinite(Number(payload.position_seconds))) {
      const serverPosition = Math.max(0, Number(payload.position_seconds));
      const serverProgress = clampLmsProgress((serverPosition / safeTotalSeconds) * 100);
      const nextProgress = Math.max(clampLmsProgress(progressRef.current), serverProgress);
      setProgress(nextProgress);
      progressRef.current = nextProgress;
      const resolvedPosition = Math.max(
        serverPosition,
        Number(videoRef.current?.currentTime || 0),
        Number(displayCurrentSecondsRef.current || 0)
      );
      maxAllowedSecondsRef.current = Math.max(maxAllowedSecondsRef.current, resolvedPosition);
      if (!videoRef.current || videoRef.current.paused) {
        setDisplayCurrentSeconds(Math.min(safeTotalSeconds, resolvedPosition));
      }
      persistLocalProgress(true, {
        positionSeconds: Math.min(safeTotalSeconds, resolvedPosition),
        durationSeconds: safeTotalSeconds,
        progressRatio: nextProgress,
      });
    }
    return payload || null;
  }, [canTrack, lmsRequest, lessonId, safeTotalSeconds, persistLocalProgress, clientSessionKey, takePlaybackActiveDeltaSeconds]);

  useEffect(() => {
    sendHeartbeatRef.current = sendHeartbeat;
  }, [sendHeartbeat]);

  useEffect(() => {
    if (!playing || !canTrack || !lessonId) {
      clearInterval(heartbeatRef.current);
      return undefined;
    }

    heartbeatRef.current = setInterval(() => {
      if (heartbeatInFlightRef.current) return;
      heartbeatInFlightRef.current = true;
      void sendHeartbeat()
        .catch(() => {
          // non-fatal
        })
        .finally(() => {
          heartbeatInFlightRef.current = false;
        });
    }, heartbeatIntervalMs);

    return () => clearInterval(heartbeatRef.current);
  }, [playing, canTrack, lessonId, sendHeartbeat, heartbeatIntervalMs]);

  const syncHeartbeatBeforeComplete = useCallback(async () => {
    if (!canTrack) return;
    // If the user has watched past the completion threshold, send one
    // heartbeat with the full confirmed position so the server's
    // completion_ratio reaches >= threshold even when the browser freezes
    // video.currentTime a fraction short of duration on the final frame.
    const currentProgress = clampLmsProgress(progressRef.current || 0);
    const duration = Math.max(
      1,
      Number(videoRef.current?.duration || 0) > 0
        ? Number(videoRef.current.duration)
        : Number(totalSecondsRef.current || 0)
    );
    if (currentProgress >= completionThreshold && lessonId) {
      const activeDeltaSeconds = takePlaybackActiveDeltaSeconds({ includeCurrent: true });
      try {
        await lmsRequest(`/api/lms/lessons/${lessonId}/heartbeat`, {
          method: "POST",
          body: {
            position_seconds: Number(duration.toFixed(2)),
            media_duration_seconds: Number(duration.toFixed(2)),
            tab_visible: visibleRef.current,
            active_delta_seconds: Number(activeDeltaSeconds.toFixed(2)),
            client_session_key: clientSessionKey || undefined,
            client_ts: new Date().toISOString(),
          },
        });
      } catch (_) {
        // fall through to the normal heartbeat
      }
      return;
    }
    await sendHeartbeat();
  }, [canTrack, completionThreshold, lessonId, lmsRequest, sendHeartbeat, clientSessionKey, takePlaybackActiveDeltaSeconds]);

  const handleLoadedMetadata = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    const mediaDuration = Number(video.duration || 0);
    if (!Number.isFinite(mediaDuration) || mediaDuration <= 0) return;
    const safeDuration = Math.max(1, mediaDuration);
    setTotalSeconds((prev) => (Math.abs(prev - safeDuration) >= 0.25 ? safeDuration : prev));
    const resumeFromProgress = Math.max(0, Math.min(mediaDuration, (progressRef.current / 100) * mediaDuration));
    const resumeFromStorage = Math.max(0, Math.min(mediaDuration, Number(restoredPositionRef.current || 0)));
    const resumePosition = Math.max(resumeFromProgress, resumeFromStorage);
    // Raise the anti-seek ceiling BEFORE programmatically seeking to the
    // resume position — otherwise the resulting `seeking` event arrives
    // with currentTime > allowed and handleSeeking rewinds the user back
    // to the lower, progress-derived ceiling.
    const normalizedProgress = clampLmsProgress(progressRef.current || 0);
    const restoredAllowed = Math.max(0, (normalizedProgress / 100) * safeDuration);
    maxAllowedSecondsRef.current = Math.max(
      maxAllowedSecondsRef.current,
      restoredAllowed,
      resumePosition,
      Number(video.currentTime || 0)
    );
    if (resumePosition > 0 && Math.abs(Number(video.currentTime || 0) - resumePosition) > 1.5) {
      video.currentTime = resumePosition;
    }
    const nextSeconds = Math.max(0, Number(video.currentTime || resumePosition || 0));
    maxAllowedSecondsRef.current = Math.max(maxAllowedSecondsRef.current, nextSeconds);
    setDisplayCurrentSeconds(nextSeconds);
    persistLocalProgress(true, {
      positionSeconds: nextSeconds,
      durationSeconds: safeDuration,
      progressRatio: clampLmsProgress((nextSeconds / safeDuration) * 100),
    });
  }, [persistLocalProgress]);

  const handleVideoEnded = useCallback(() => {
    const video = videoRef.current;
    const duration = Math.max(
      1,
      Number(video?.duration || 0) > 0
        ? Number(video.duration)
        : Number(totalSecondsRef.current || 0)
    );
    setPlaying(false);
    setProgress(100);
    progressRef.current = 100;
    setDisplayCurrentSeconds(duration);
    maxAllowedSecondsRef.current = Math.max(maxAllowedSecondsRef.current, duration);
    const activeDeltaSeconds = takePlaybackActiveDeltaSeconds({ includeCurrent: true });
    persistLocalProgress(true, {
      positionSeconds: duration,
      durationSeconds: duration,
      progressRatio: 100,
    });
    // Force one heartbeat reporting the full duration so the server's
    // confirmed_seconds reaches 100% even if video.currentTime stops a
    // fraction short of duration on the final frame.
    if (canTrack && lessonId) {
      void lmsRequest(`/api/lms/lessons/${lessonId}/heartbeat`, {
        method: "POST",
        body: {
          position_seconds: Number(duration.toFixed(2)),
          media_duration_seconds: Number(duration.toFixed(2)),
          tab_visible: visibleRef.current,
          active_delta_seconds: Number(activeDeltaSeconds.toFixed(2)),
          client_session_key: clientSessionKey || undefined,
          client_ts: new Date().toISOString(),
        },
      }).catch(() => {});
    }
  }, [canTrack, lessonId, lmsRequest, persistLocalProgress, clientSessionKey, takePlaybackActiveDeltaSeconds]);

  const handleTimeUpdate = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    const mediaDuration = Number(video.duration || safeTotalSeconds || 0);
    const currentTime = Math.max(0, Number(video.currentTime || 0));
    if (!Number.isFinite(mediaDuration) || mediaDuration <= 0) return;
    const safeDuration = Math.max(1, mediaDuration);
    if (Math.abs(totalSeconds - safeDuration) >= 0.25) {
      setTotalSeconds(safeDuration);
    }
    maxAllowedSecondsRef.current = Math.max(maxAllowedSecondsRef.current, currentTime);
    setDisplayCurrentSeconds(currentTime);
    const nextProgress = clampLmsProgress((currentTime / safeDuration) * 100);
    setProgress(nextProgress);
    progressRef.current = nextProgress;
    persistLocalProgress(false, {
      positionSeconds: currentTime,
      durationSeconds: safeDuration,
      progressRatio: nextProgress,
    });
  }, [safeTotalSeconds, totalSeconds, persistLocalProgress]);

  const notifySeekBlocked = () => {
    const nowTs = Date.now();
    if (nowTs - seekToastAtRef.current > 1500) {
      seekToastAtRef.current = nowTs;
      emitToast?.("Перемотка вперед недоступна", "error");
    }
  };

  const seekVideoTo = (targetSeconds) => {
    const video = videoRef.current;
    if (!video) return;
    const duration = Math.max(1, Number(video.duration || safeTotalSeconds || 0));
    const boundedSeconds = Math.max(0, Math.min(duration, Number(targetSeconds || 0)));
    if (!canSeekForward && boundedSeconds > maxAllowedSecondsRef.current + 0.75) {
      const restoredSeconds = Math.max(0, maxAllowedSecondsRef.current);
      video.currentTime = restoredSeconds;
      setDisplayCurrentSeconds(restoredSeconds);
      const correctedProgress = clampLmsProgress((restoredSeconds / duration) * 100);
      setProgress(correctedProgress);
      progressRef.current = correctedProgress;
      persistLocalProgress(true, {
        positionSeconds: restoredSeconds,
        durationSeconds: duration,
        progressRatio: correctedProgress,
      });
      notifySeekBlocked();
      return;
    }
    video.currentTime = boundedSeconds;
    setDisplayCurrentSeconds(boundedSeconds);
    const nextProgress = clampLmsProgress((boundedSeconds / duration) * 100);
    setProgress(nextProgress);
    progressRef.current = nextProgress;
    if (canSeekForward) {
      maxAllowedSecondsRef.current = Math.max(maxAllowedSecondsRef.current, boundedSeconds);
    }
    persistLocalProgress(true, {
      positionSeconds: boundedSeconds,
      durationSeconds: duration,
      progressRatio: nextProgress,
    });
  };

  const handleSeekSliderChange = (event) => {
    seekVideoTo(Number(event?.target?.value || 0));
  };

  const handleRewind10 = () => {
    const current = Number(videoRef.current?.currentTime || currentSeconds || 0);
    seekVideoTo(current - 10);
  };

  const handleSeeking = () => {
    const video = videoRef.current;
    if (!video || canSeekForward) return;
    const current = Math.max(0, Number(video.currentTime || 0));
    const allowed = maxAllowedSecondsRef.current + 0.75;
    if (current > allowed) {
      const restoredSeconds = Math.max(0, maxAllowedSecondsRef.current);
      video.currentTime = restoredSeconds;
      setDisplayCurrentSeconds(restoredSeconds);
      const correctedProgress = clampLmsProgress(
        (restoredSeconds / Math.max(1, Number(video.duration || safeTotalSeconds || 0))) * 100
      );
      setProgress(correctedProgress);
      progressRef.current = correctedProgress;
      persistLocalProgress(true, {
        positionSeconds: restoredSeconds,
        durationSeconds: Math.max(1, Number(video.duration || safeTotalSeconds || 0)),
        progressRatio: correctedProgress,
      });
      notifySeekBlocked();
    }
  };

  const handleVideoPlay = () => {
    lastActiveHeartbeatAtRef.current = Date.now();
    setPlaying(true);
  };

  const handleVideoPause = () => {
    const activeDeltaSeconds = takePlaybackActiveDeltaSeconds({ includeCurrent: true });
    setPlaying(false);
    persistLocalProgress(true);
    void sendHeartbeat({ activeDeltaSeconds }).catch(() => {});
  };

  const togglePlayback = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      void video.play().catch(() => {});
      return;
    }
    video.pause();
  };

  const scheduleHideControls = useCallback(() => {
    if (hideControlsTimerRef.current) {
      clearTimeout(hideControlsTimerRef.current);
    }
    hideControlsTimerRef.current = setTimeout(() => {
      const video = videoRef.current;
      if (video && !video.paused) {
        setControlsVisible(false);
      }
    }, 2500);
  }, []);

  const revealControls = useCallback(() => {
    setControlsVisible(true);
    scheduleHideControls();
  }, [scheduleHideControls]);

  useEffect(() => {
    if (!playing) {
      setControlsVisible(true);
      if (hideControlsTimerRef.current) {
        clearTimeout(hideControlsTimerRef.current);
        hideControlsTimerRef.current = null;
      }
      return;
    }
    scheduleHideControls();
  }, [playing, scheduleHideControls]);

  useEffect(() => () => {
    if (hideControlsTimerRef.current) {
      clearTimeout(hideControlsTimerRef.current);
    }
  }, []);

  const formatVideoClock = (seconds) => {
    const safe = Math.max(0, Math.floor(Number(seconds || 0)));
    const minutes = Math.floor(safe / 60);
    const secs = safe % 60;
    return `${minutes}:${String(secs).padStart(2, "0")}`;
  };

  const handleComplete = useCallback(async () => {
    if (isManagerMode) return;
    if (completed || completing) return;
    if (typeof onCompleteLesson !== "function") return;
    setCompleting(true);
    try {
      try {
        await syncHeartbeatBeforeComplete();
      } catch (_) {
        // no-op, complete endpoint still performs final validation
      }
      const ok = await onCompleteLesson(lesson);
      if (ok) {
        setCompleted(true);
        setProgress(100);
        setDisplayCurrentSeconds(safeTotalSeconds);
        clearLmsVideoPositionFromStorage(videoStorageKey);
      }
    } finally {
      setCompleting(false);
    }
  }, [completed, completing, onCompleteLesson, lesson, syncHeartbeatBeforeComplete, safeTotalSeconds, isManagerMode, videoStorageKey]);

  useEffect(() => {
    if (isManagerMode) return;
    if (completed || completing) return;
    if (progress < completionThreshold) return;
    if (autoCompleteTriggeredRef.current) return;
    autoCompleteTriggeredRef.current = true;
    handleComplete();
  }, [progress, completionThreshold, completed, completing, handleComplete, isManagerMode]);

  const handleTranscriptCopyAttempt = useCallback((event) => {
    if (!effectiveBlockTranscriptCopy) return;
    event.preventDefault();
  }, [effectiveBlockTranscriptCopy]);

  return (
    <div>
      {tabHidden && (
        <div className="mb-4 bg-amber-50 border border-amber-200 rounded-xl p-3 flex items-center gap-2 text-xs text-amber-700">
          <AlertTriangle size={14} /> Видео поставлено на паузу — вы переключили вкладку браузера
        </div>
      )}

      <div
        className={"bg-slate-900 rounded-2xl overflow-hidden mb-6 relative aspect-video" + (playing && !controlsVisible ? " cursor-none" : "")}
        onMouseMove={revealControls}
        onMouseEnter={revealControls}
        onMouseLeave={() => { if (playing) setControlsVisible(false); }}
        onTouchStart={revealControls}
      >
        {videoUrl ? (
          <>
            <video
              ref={videoRef}
              src={videoUrl}
              className="w-full h-full bg-black cursor-pointer"
              controls={false}
              playsInline
              preload="metadata"
              {...LMS_PROTECTED_VIDEO_PROPS}
              onClick={togglePlayback}
              onLoadedMetadata={handleLoadedMetadata}
              onTimeUpdate={handleTimeUpdate}
              onSeeking={handleSeeking}
              onPlay={handleVideoPlay}
              onPause={handleVideoPause}
              onEnded={handleVideoEnded}
            />
            {!playing && (
              <button
                type="button"
                onClick={togglePlayback}
                className="absolute inset-0 m-auto w-16 h-16 rounded-full bg-white/20 hover:bg-white/30 text-white flex items-center justify-center transition-colors backdrop-blur-sm"
                aria-label="Play"
              >
                <Play size={26} className="ml-1" />
              </button>
            )}
            {!isManagerMode && !completed && (
              <button
                type="button"
                onClick={(event) => { event.stopPropagation(); handleComplete(); }}
                disabled={completing || progress < completionThreshold}
                title={progress < completionThreshold ? `Досмотрите минимум до ${completionThreshold}%` : "Отметить урок как завершенный"}
                className={"absolute top-3 right-3 inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-[11px] font-semibold shadow-lg backdrop-blur-sm transition-all duration-300 bg-indigo-600/90 hover:bg-indigo-600 text-white disabled:bg-slate-900/55 disabled:text-white/70 disabled:cursor-not-allowed" + (controlsVisible ? " opacity-100" : " opacity-0 pointer-events-none")}
              >
                {completing ? <RefreshCw size={13} className="animate-spin" /> : <CheckCircle size={13} />}
                <span className="hidden sm:inline">{completing ? "Сохранение..." : progress < completionThreshold ? `До завершения ${Math.max(0, Math.ceil(completionThreshold - progress))}%` : "Отметить как завершенный"}</span>
                <span className="sm:hidden">{completing ? "..." : progress < completionThreshold ? `${Math.max(0, Math.ceil(completionThreshold - progress))}%` : "Завершить"}</span>
              </button>
            )}
            <div
              className={"absolute left-0 right-0 bottom-0 p-3 bg-gradient-to-t from-black/80 via-black/45 to-transparent transition-opacity duration-300" + (controlsVisible ? " opacity-100" : " opacity-0 pointer-events-none")}
              onClick={(event) => event.stopPropagation()}
            >
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleRewind10}
                  className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md bg-white/20 hover:bg-white/30 text-white text-[11px] font-semibold transition-colors"
                >
                  <ChevronLeft size={12} /> 10с
                </button>
                <input
                  type="range"
                  min={0}
                  max={Math.max(1, Math.floor(safeTotalSeconds))}
                  step={1}
                  value={Math.max(0, Math.min(Math.floor(safeTotalSeconds), currentSeconds))}
                  onChange={handleSeekSliderChange}
                  className="flex-1 accent-indigo-500"
                />
                <div className="text-[11px] text-white/90 font-mono tabular-nums min-w-[92px] text-right">
                  {formatVideoClock(currentSeconds)} / {formatVideoClock(safeTotalSeconds)}
                </div>
              </div>
              {!canSeekForward && (
                <p className="mt-1 text-[10px] text-white/70">Перемотка вперед станет доступна после завершения просмотра</p>
              )}
            </div>
          </>
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-center px-4">
            <Video size={24} className="text-white/50" />
            <p className="text-sm text-white/70">Видео не прикреплено к уроку</p>
          </div>
        )}
      </div>

      <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
        <div className="flex border-b border-slate-100">
          {[{ id: "transcript", label: "Транскрипт", icon: AlignLeft }, { id: "notes", label: "Конспект", icon: FileText }, { id: "materials", label: "Материалы", icon: BookMarked }].map((t) => (
            <button key={t.id} onClick={() => setActiveTab(t.id)} className={`flex items-center gap-2 px-5 py-3.5 text-xs font-medium transition-colors border-b-2 ${activeTab === t.id ? "border-indigo-500 text-indigo-600" : "border-transparent text-slate-500 hover:text-slate-700"}`}>
              <t.icon size={13} /> {t.label}
            </button>
          ))}
        </div>
        <div className="p-5">
          {activeTab === "transcript" && (
            <div
              className="max-h-[min(420px,55vh)] overflow-y-auto custom-scrollbar pr-2 text-sm text-slate-600 leading-relaxed"
              onCopy={handleTranscriptCopyAttempt}
              onCut={handleTranscriptCopyAttempt}
              onContextMenu={handleTranscriptCopyAttempt}
              onDragStart={handleTranscriptCopyAttempt}
              onSelectStart={handleTranscriptCopyAttempt}
              style={effectiveBlockTranscriptCopy ? { userSelect: "none", WebkitUserSelect: "none" } : undefined}
            >
              {transcriptText ? (
                <RichTextContent value={transcriptText} />
              ) : (
                <>
                  <p>В этом уроке мы рассмотрим <strong className="text-slate-800">основные принципы информационной безопасности</strong> и их практическое применение в корпоративной среде.</p>
                  <p>Информационная безопасность — это практика предотвращения несанкционированного доступа, использования, раскрытия, нарушения, изменения, проверки, записи или уничтожения информации.</p>
                </>
              )}
            </div>
          )}
          {activeTab === "notes" && (
            <textarea className="w-full h-40 text-sm text-slate-700 resize-none focus:outline-none placeholder-slate-300" placeholder="Добавьте заметки к уроку..." />
          )}
          {activeTab === "materials" && (
            <div className="space-y-2">
              {lessonFiles.length === 0 && (
                <div className="text-xs text-slate-400">Материалы не добавлены</div>
              )}
              {lessonFiles.map((material, idx) => {
                const name = String(material?.title || `Материал ${idx + 1}`);
                const metaName = String(material?.metadata?.uploaded_file_name || "");
                const label = metaName || name;
                const fileUrl = String(material?.url || material?.signed_url || material?.content_url || "").trim();
                return (
                  <a
                    key={`${material?.id || idx}-${label}`}
                    href={fileUrl || "#"}
                    rel="noopener noreferrer"
                    download={label}
                    className="lms-file-link"
                  >
                    <span className="lms-file-icon" aria-hidden="true" />
                    <span className="lms-file-content">
                      <span className="lms-file-title">{label}</span>
                      <span className="lms-file-subtitle">Нажмите, чтобы скачать</span>
                    </span>
                    <span className="lms-file-download">Скачать</span>
                  </a>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── QUIZ SECTION (полная переработка по ТЗ) ──────────────────────────────────

function ApiQuizSection({ quizView, setQuizView, answers, setAnswers, course, lesson, lmsRequest, onFinished, emitToast }) {
  const [attempt, setAttempt] = useState(null);
  const [questions, setQuestions] = useState([]);
  const [currentQ, setCurrentQ] = useState(0);
  const [timeLeft, setTimeLeft] = useState(Math.max(60, Number(lesson?.timeLimitSeconds || lesson?.durationSeconds || (Number(lesson?.timeLimitMinutes || 0) * 60) || 20 * 60)));
  const [textInput, setTextInput] = useState("");
  const [loadingStart, setLoadingStart] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [result, setResult] = useState(null);
  const [autoFinished, setAutoFinished] = useState(false);
  const timerRef = useRef(null);

  const attemptsLeft = Math.max(
    0,
    Number(lesson?.maxAttempts ?? course?.maxAttempts ?? 3) - Number(lesson?.attemptsUsed ?? course?.attemptsUsed ?? 0)
  );

  const passThreshold = Number(result?.pass_threshold || attempt?.pass_threshold || lesson?.passingScore || course?.passingScore || 80);

  const parseMinutesFromLabel = (label) => {
    const match = String(label || "").match(/(\d+)/);
    const parsed = Number(match?.[1] || 0);
    return Number.isFinite(parsed) ? parsed : 0;
  };

  const resolveQuizTimeLimitSeconds = (attemptPayload = null) => {
    const rawAttempt = attemptPayload || {};
    const attemptSeconds = Math.max(0, Number(rawAttempt?.time_limit_seconds || 0));
    if (attemptSeconds > 0) return attemptSeconds;

    const attemptMinutes = Math.max(0, Number(rawAttempt?.time_limit_minutes || rawAttempt?.time_limit || 0));
    if (attemptMinutes > 0) return attemptMinutes * 60;

    const lessonSeconds = Math.max(0, Number(lesson?.timeLimitSeconds || lesson?.durationSeconds || 0));
    if (lessonSeconds > 0) return lessonSeconds;

    const lessonMinutes = Math.max(0, Number(lesson?.timeLimitMinutes || 0));
    if (lessonMinutes > 0) return lessonMinutes * 60;

    const labelMinutes = Math.max(0, parseMinutesFromLabel(lesson?.duration));
    if (labelMinutes > 0) return labelMinutes * 60;

    return 20 * 60;
  };

  const normalizeQuestions = (items) => {
    if (!Array.isArray(items)) return [];
    return items.map((item) => ({
      id: Number(item?.id || 0),
      type: mapApiQuestionTypeToView(item?.type),
      text: String(item?.prompt || "Вопрос"),
      options: Array.isArray(item?.options)
        ? item.options.map((opt, idx) => ({ id: Number(opt?.id || idx), text: String(opt?.text || `Вариант ${idx + 1}`) }))
        : [],
      points: Number(item?.points || 1),
      required: Boolean(item?.required),
      isApiQuestion: true,
    }));
  };

  const resetForRun = (nextTimeLimitSeconds) => {
    const resolvedTime = Math.max(60, Number(nextTimeLimitSeconds || resolveQuizTimeLimitSeconds(null)));
    setAnswers({});
    setCurrentQ(0);
    setTimeLeft(resolvedTime);
    setTextInput("");
    setAutoFinished(false);
  };

  const startApiQuiz = async () => {
    if (attemptsLeft <= 0 || loadingStart) return;
    setLoadingStart(true);
    try {
      const payload = await lmsRequest(`/api/lms/tests/${lesson.apiTestId}/start`, { method: "POST" });
      const startedAttempt = payload?.attempt || null;
      const resolvedTimeLimitSeconds = resolveQuizTimeLimitSeconds(startedAttempt);
      setAttempt(startedAttempt);
      setQuestions(normalizeQuestions(payload?.questions));
      setResult(null);
      resetForRun(resolvedTimeLimitSeconds);
      setQuizView("active");
    } catch (error) {
      emitToast?.(`Не удалось начать тест: ${String(error?.message || "ошибка")}`, "error");
    } finally {
      setLoadingStart(false);
    }
  };

  const submitAnswersBeforeFinish = useCallback(async () => {
    if (!attempt?.id || !Array.isArray(questions) || questions.length === 0) return;
    const answerItems = questions
      .map((question) => {
        const userAnswer = answers[question.id];
        const hasAnswer = question.type === "multiple"
          ? Array.isArray(userAnswer) && userAnswer.length > 0
          : question.type === "text"
            ? Boolean(String(userAnswer || "").trim())
            : userAnswer !== undefined && userAnswer !== null && userAnswer !== "";
        if (!hasAnswer) return null;
        return {
          question_id: question.id,
          answer_payload: buildAnswerPayloadForApi(question, userAnswer),
        };
      })
      .filter(Boolean);
    if (answerItems.length === 0) return;

    try {
      await lmsRequest(`/api/lms/tests/attempts/${attempt.id}/answers`, {
        method: "PATCH",
        body: { answers: answerItems },
      });
    } catch (bulkError) {
      const errorText = String(bulkError?.message || "").toLowerCase();
      const canFallback = errorText.includes("404") || errorText.includes("method") || errorText.includes("not found");
      if (!canFallback) throw bulkError;

      // Backward compatibility: older backends may not have /answers endpoint yet.
      const requests = answerItems.map((item) => (
        lmsRequest(`/api/lms/tests/attempts/${attempt.id}/answer`, {
          method: "PATCH",
          body: item,
        })
      ));
      await Promise.all(requests);
    }
  }, [attempt?.id, questions, answers, lmsRequest]);

  const finishApiQuiz = useCallback(async (auto = false) => {
    if (!attempt?.id || finishing) return;
    setFinishing(true);
    try {
      await submitAnswersBeforeFinish();
      const payload = await lmsRequest(`/api/lms/tests/attempts/${attempt.id}/finish`, { method: "POST" });
      const finish = payload?.result || {};
      const breakdown = Array.isArray(finish?.breakdown) ? finish.breakdown : [];
      const rows = questions.map((question) => {
        const userAnswer = answers[question.id];
        const b = breakdown.find((item) => Number(item?.question_id) === Number(question.id));
        return {
          question,
          userAnswer,
          correct: Boolean(b?.is_correct),
          points_awarded: Number(b?.points_awarded || 0),
          points_total: Number(b?.points_total || question.points || 1),
        };
      });
      setResult({
        score_percent: Number(finish?.score_percent || 0),
        pass_threshold: Number(finish?.pass_threshold || attempt?.pass_threshold || 80),
        rows,
      });
      if (auto) setAutoFinished(true);
      setQuizView("result");
      if (typeof onFinished === "function") await onFinished();
    } catch (error) {
      emitToast?.(`Не удалось завершить тест: ${String(error?.message || "ошибка")}`, "error");
    } finally {
      setFinishing(false);
    }
  }, [attempt?.id, attempt?.pass_threshold, finishing, lmsRequest, questions, answers, setQuizView, onFinished, emitToast, submitAnswersBeforeFinish]);

  useEffect(() => {
    if (quizView !== "active") return;
    timerRef.current = setInterval(() => setTimeLeft((t) => Math.max(0, t - 1)), 1000);
    return () => clearInterval(timerRef.current);
  }, [quizView]);

  useEffect(() => {
    if (quizView === "active" && timeLeft <= 0) {
      clearInterval(timerRef.current);
      void finishApiQuiz(true);
    }
  }, [quizView, timeLeft, finishApiQuiz]);

  const q = questions[currentQ];
  useEffect(() => {
    if (!q) return;
    if (q.type === "text") {
      const value = answers[q.id];
      setTextInput(typeof value === "string" ? value : "");
    } else {
      setTextInput("");
    }
  }, [q?.id, q?.type, currentQ, answers]);

  const allAnswered = questions.length > 0 && questions.every((question) => {
    const answer = answers[question.id];
    if (question.type === "multiple") return Array.isArray(answer) && answer.length > 0;
    if (question.type === "text") return Boolean(String(answer || "").trim());
    return answer !== undefined && answer !== null && answer !== "";
  });

  const formatAnswer = (question, answerValue) => {
    if (question.type === "multiple") {
      const ids = Array.isArray(answerValue) ? answerValue : [];
      return question.options.filter((opt) => ids.includes(opt.id)).map((opt) => opt.text).join(", ") || "(не выбран)";
    }
    if (question.type === "single" || question.type === "bool") {
      const selected = question.options.find((opt) => opt.id === answerValue);
      return selected?.text || "(не выбран)";
    }
    return String(answerValue || "(не введён)");
  };

  if (quizView === "intro") {
    const hasPriorScore = lesson?.score != null;
    const priorScore = hasPriorScore ? Math.round(Number(lesson.score) || 0) : 0;
    const priorPassed = hasPriorScore && priorScore >= Math.round(passThreshold);
    const totalAttempts = Number(lesson?.maxAttempts ?? course?.maxAttempts ?? 3);
    return (
      <div className="space-y-5 max-w-2xl mx-auto">
        {hasPriorScore && (
          <div className={`rounded-2xl border p-6 text-center ${priorPassed ? "bg-emerald-50/70 border-emerald-200" : "bg-amber-50/70 border-amber-200"}`}>
            <div className={`w-16 h-16 rounded-2xl flex items-center justify-center mx-auto mb-4 ${priorPassed ? "bg-emerald-100" : "bg-amber-100"}`}>
              {priorPassed ? <CheckCircle size={30} className="text-emerald-600" /> : <RefreshCw size={28} className="text-amber-600" />}
            </div>
            <p className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">Ваш лучший результат</p>
            <div className="flex items-baseline justify-center gap-2 mb-2">
              <span className="text-5xl font-bold text-slate-900 leading-none">{priorScore}%</span>
              <span className="text-sm text-slate-400">/ 100%</span>
            </div>
            <p className={`text-sm font-semibold ${priorPassed ? "text-emerald-700" : "text-amber-700"}`}>
              {priorPassed ? "Тест пройден" : "Тест не пройден"}
            </p>
            <p className="text-xs text-slate-500 mt-1">Порог прохождения: {Math.round(passThreshold)}%</p>
          </div>
        )}

        <div className="bg-white rounded-2xl border border-slate-200 p-8 text-center">
          <div className="w-16 h-16 bg-violet-100 rounded-2xl flex items-center justify-center mx-auto mb-5"><HelpCircle size={28} className="text-violet-600" /></div>
          <h2 className="text-xl font-bold text-slate-900 mb-2">{lesson?.title || "Тест"}</h2>
          <p className="text-sm text-slate-500 mb-4">{hasPriorScore ? "Вы можете пройти тест ещё раз, чтобы улучшить результат" : "Проверьте свои знания по материалам курса"}</p>

          <div className="grid grid-cols-3 gap-4 mb-5">
            {[{ label: "Вопросов", value: lesson?.questionCount || "—" }, { label: "Время", value: lesson?.duration || "—" }, { label: "Порог", value: `${Math.round(passThreshold)}%` }].map(s => (
              <div key={s.label} className="bg-slate-50 rounded-xl p-4">
                <div className="text-xl font-bold text-slate-900">{s.value}</div>
                <div className="text-xs text-slate-500 mt-1">{s.label}</div>
              </div>
            ))}
          </div>

          <div className={`flex items-center justify-center gap-2 text-sm mb-5 p-3 rounded-xl ${attemptsLeft <= 1 ? "bg-red-50 text-red-700" : "bg-slate-50 text-slate-600"}`}>
            <RefreshCw size={14} />
            <span>Доступно попыток: <strong>{attemptsLeft}</strong> из {totalAttempts}</span>
          </div>

          <div className="text-left bg-slate-50 rounded-xl p-4 mb-6">
            <p className="text-xs font-semibold text-slate-700 mb-2">На тесте могут быть следующие типы вопросов:</p>
            <div className="grid grid-cols-2 gap-2">
              {[{ icon: RadioIcon, label: "Один правильный ответ" }, { icon: CheckSquare, label: "Несколько ответов" }, { icon: Check, label: "Верно / Неверно" }, { icon: Type, label: "Текстовый ввод" }].map(t => (
                <div key={t.label} className="flex items-center gap-2 text-xs text-slate-600"><t.icon size={12} className="text-indigo-500" />{t.label}</div>
              ))}
            </div>
          </div>

          <button onClick={startApiQuiz} disabled={attemptsLeft <= 0 || loadingStart} className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-3 rounded-xl text-sm transition-colors">
            {loadingStart ? "Подготовка..." : attemptsLeft <= 0 ? "Попытки исчерпаны" : hasPriorScore ? (priorPassed ? "Пройти ещё раз" : "Пересдать тест") : "Начать тест"}
          </button>
        </div>
      </div>
    );
  }

  if (quizView === "result" && result) {
    const scorePercent = Number(result?.score_percent || 0);
    const passed = scorePercent >= passThreshold;
    return (
      <div className="space-y-5 max-w-3xl 2xl:max-w-4xl mx-auto">
        <div className="bg-white rounded-2xl border border-slate-200 p-8 text-center">
          {autoFinished && (
            <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 mb-5 text-xs text-amber-700 flex items-center justify-center gap-2">
              <Clock size={13} /> Тест завершён автоматически по истечении времени
            </div>
          )}
          <div className={`w-20 h-20 rounded-full flex items-center justify-center mx-auto mb-5 ${passed ? "bg-emerald-100" : "bg-red-100"}`}>
            {passed ? <CheckCircle size={36} className="text-emerald-600" /> : <XCircle size={36} className="text-red-500" />}
          </div>
          <h2 className="text-3xl font-bold text-slate-900 mb-1">{Math.round(scorePercent)}%</h2>
          <p className={`text-sm font-semibold mb-1 ${passed ? "text-emerald-600" : "text-red-600"}`}>{passed ? "Тест пройден!" : "Тест не пройден"}</p>
          <p className="text-sm text-slate-500 mb-6">{passed ? "Отличный результат." : `Для прохождения необходимо набрать ${Math.round(passThreshold)}%.`}</p>

          <div className="flex gap-3 justify-center">
            {attemptsLeft > 0 && (
              <button onClick={() => setQuizView("intro")} className="max-w-[200px] w-full bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold py-3 rounded-xl text-sm transition-colors flex items-center justify-center gap-2">
                <RefreshCw size={14} /> {passed ? "Повторить тест" : "Пересдать"}
              </button>
            )}
            {attemptsLeft <= 0 && (
              <div className="max-w-[200px] w-full bg-red-50 text-red-700 font-semibold py-3 rounded-xl text-sm flex items-center justify-center gap-2">
                <XCircle size={14} /> Попытки исчерпаны
              </div>
            )}
            {passed && (
              <div className="max-w-[200px] w-full text-emerald-700 bg-emerald-50 font-semibold py-3 rounded-xl text-sm flex items-center justify-center gap-2 border border-emerald-100">
                <CheckCircle size={14} /> Успешно
              </div>
            )}
          </div>
        </div>
        <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-100 text-sm font-semibold text-slate-900">Разбор ответов</div>
          <div className="divide-y divide-slate-100">
            {result.rows.map((row, idx) => (
              <div key={`${row.question.id}-${idx}`} className="p-5">
                <p className="text-sm font-medium text-slate-900">{idx + 1}. {row.question.text}</p>
                <p className="text-xs text-slate-500 mt-1">Ваш ответ: <span className="font-medium text-slate-700">{formatAnswer(row.question, row.userAnswer)}</span></p>
                <p className={`text-xs mt-1 ${row.correct ? "text-emerald-600" : "text-red-600"}`}>{row.correct ? "Верно" : "Неверно"} · {row.points_awarded}/{row.points_total} б.</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (!q) {
    return <div className="bg-white rounded-2xl border border-slate-200 p-8 text-center text-slate-500">Вопросы не загружены.</div>;
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6 bg-white rounded-2xl border border-slate-200 px-5 py-4">
        <div className="flex items-center gap-2 flex-wrap">
          {questions.map((question, idx) => {
            const hasAnswer = Array.isArray(answers[question.id]) ? answers[question.id].length > 0 : answers[question.id] !== undefined && answers[question.id] !== "";
            return <button key={question.id} onClick={() => setCurrentQ(idx)} className={`w-8 h-8 rounded-lg text-xs font-semibold transition-all ${idx === currentQ ? "bg-indigo-600 text-white" : hasAnswer ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-500 hover:bg-slate-200"}`}>{idx + 1}</button>;
          })}
        </div>
        <div className="flex items-center gap-2 px-4 py-2 rounded-xl font-mono font-bold text-sm bg-slate-100 text-slate-700"><Clock size={14} /> {Math.floor(timeLeft / 60)}:{String(timeLeft % 60).padStart(2, "0")}</div>
      </div>

      <div className="bg-white rounded-2xl border border-slate-200 p-8 mb-5">
        <p className="text-base font-semibold text-slate-900 leading-relaxed mb-6">{q.text}</p>
        {(q.type === "single" || q.type === "bool") && (
          <div className="space-y-3">
            {q.options.map((opt) => (
              <button key={opt.id} onClick={() => { setAnswers((prev) => ({ ...prev, [q.id]: opt.id })); }} className={`w-full text-left flex items-center gap-4 px-5 py-4 rounded-xl border-2 transition-all text-sm ${answers[q.id] === opt.id ? "border-indigo-500 bg-indigo-50 text-indigo-800" : "border-slate-200 hover:border-indigo-200 hover:bg-indigo-50/50 text-slate-700"}`}>
                <span className="font-medium">{opt.text}</span>
              </button>
            ))}
          </div>
        )}
        {q.type === "multiple" && (
          <div className="space-y-3">
            <div className="flex items-start gap-2 rounded-xl border border-violet-200 bg-violet-50 px-3 py-2">
              <AlertCircle size={14} className="mt-0.5 text-violet-600 flex-shrink-0" />
              <p className="text-xs font-medium text-violet-700">
                {"\u041c\u043e\u0436\u043d\u043e \u0432\u044b\u0431\u0440\u0430\u0442\u044c \u043d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u043e \u0432\u0430\u0440\u0438\u0430\u043d\u0442\u043e\u0432 \u043e\u0442\u0432\u0435\u0442\u0430"}
              </p>
            </div>
            {q.options.map((opt) => {
              const selected = Array.isArray(answers[q.id]) && answers[q.id].includes(opt.id);
              return (
                <button key={opt.id} onClick={() => { setAnswers((prev) => { const oldValue = Array.isArray(prev[q.id]) ? prev[q.id] : []; const nextValue = oldValue.includes(opt.id) ? oldValue.filter((id) => id !== opt.id) : [...oldValue, opt.id]; return { ...prev, [q.id]: nextValue }; }); }} className={`w-full text-left flex items-center gap-4 px-5 py-4 rounded-xl border-2 transition-all text-sm ${selected ? "border-indigo-500 bg-indigo-50 text-indigo-800" : "border-slate-200 hover:border-indigo-200 hover:bg-indigo-50/50 text-slate-700"}`}>
                  <div className={`w-6 h-6 rounded border-2 flex items-center justify-center flex-shrink-0 ${selected ? "border-indigo-600 bg-indigo-600" : "border-slate-300"}`}>
                    {selected && <Check size={12} className="text-white" />}
                  </div>
                  <span className="font-medium">{opt.text}</span>
                </button>
              );
            })}
          </div>
        )}
        {q.type === "text" && (
          <input value={textInput} onChange={(e) => { setTextInput(e.target.value); setAnswers((prev) => ({ ...prev, [q.id]: e.target.value })); }} placeholder="Ваш ответ..." className="w-full px-4 py-3 bg-slate-50 border-2 border-slate-200 rounded-xl text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:border-indigo-400 transition-all" />
        )}
      </div>

      <div className="flex items-center justify-between">
        <button disabled={currentQ === 0} onClick={() => setCurrentQ((prev) => prev - 1)} className="flex items-center gap-2 px-4 py-2.5 bg-white border border-slate-200 rounded-xl text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"><ChevronLeft size={16} /> Назад</button>
        {currentQ < questions.length - 1 ? (
          <button onClick={() => setCurrentQ((prev) => prev + 1)} className="flex items-center gap-2 px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 rounded-xl text-sm font-medium text-white transition-colors">Далее <ChevronRight size={16} /></button>
        ) : (
          <button onClick={() => void finishApiQuiz(false)} disabled={!allAnswered || finishing} className="flex items-center gap-2 px-5 py-2.5 bg-emerald-600 hover:bg-emerald-700 rounded-xl text-sm font-semibold text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
            {finishing ? <RefreshCw size={16} className="animate-spin" /> : <FileCheck size={16} />} {finishing ? "Отправка..." : "Завершить тест"}
          </button>
        )}
      </div>
    </div>
  );
}

function ManagerQuizPreviewSection({ lesson, course, courseAnalytics = null }) {
  const previewQuestions = Array.isArray(lesson?.quizQuestions) ? lesson.quizQuestions : [];
  const testStats = Array.isArray(courseAnalytics?.testStats) ? courseAnalytics.testStats : [];
  const resolvedTestStat = useMemo(() => {
    const testId = Number(lesson?.apiTestId || 0);
    if (testId > 0) {
      const byId = testStats.find((item) => Number(item?.testId || 0) === testId);
      if (byId) return byId;
    }
    const lessonTitle = String(lesson?.title || "").trim().toLowerCase();
    if (!lessonTitle) return null;
    return testStats.find((item) => String(item?.title || "").trim().toLowerCase() === lessonTitle) || null;
  }, [lesson?.apiTestId, lesson?.title, testStats]);

  const questionTypeLabel = (qType) => {
    if (qType === "multiple") return "Несколько ответов";
    if (qType === "bool") return "Верно / Неверно";
    if (qType === "text") return "Текстовый ответ";
    return "Один ответ";
  };

  const formatLastAttempt = (value) => {
    if (!value) return "—";
    try {
      return new Date(value).toLocaleString("ru-RU");
    } catch (_) {
      return "—";
    }
  };

  return (
    <div className="space-y-5 max-w-4xl 2xl:max-w-5xl mx-auto">
      <div className="bg-white rounded-2xl border border-slate-200 p-6">
        <div className="flex items-center justify-between gap-4 mb-4">
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-400">Режим просмотра теста</p>
            <h3 className="text-lg font-semibold text-slate-900 mt-1">{lesson?.title || "Тест"}</h3>
          </div>
          <span className="text-[11px] bg-indigo-50 text-indigo-700 border border-indigo-200 px-2.5 py-1 rounded-full font-semibold">
            Только просмотр
          </span>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          {[
            { label: "Вопросов", value: Math.max(0, Number(lesson?.questionCount || previewQuestions.length || 0)) },
            { label: "Порог", value: `${Math.max(0, Number(lesson?.passingScore || course?.passingScore || 0))}%` },
            { label: "Попыток", value: resolvedTestStat?.attempts ?? 0 },
            { label: "Ср. балл", value: resolvedTestStat?.avgScore == null ? "—" : `${resolvedTestStat.avgScore}%` },
          ].map((item) => (
            <div key={item.label} className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2">
              <p className="text-[10px] uppercase tracking-wide text-slate-400">{item.label}</p>
              <p className="text-sm font-semibold text-slate-800 mt-0.5">{item.value}</p>
            </div>
          ))}
        </div>
        {resolvedTestStat && (
          <p className="text-xs text-slate-500 mt-3">
            Последняя попытка: {formatLastAttempt(resolvedTestStat.lastAt)} · Успешность: {resolvedTestStat.passRate == null ? "—" : `${resolvedTestStat.passRate}%`}
          </p>
        )}
      </div>

      {previewQuestions.length === 0 ? (
        <div className="bg-white rounded-2xl border border-slate-200 p-8 text-center text-sm text-slate-500">
          Вопросы теста недоступны в текущем API-ответе курса.
        </div>
      ) : (
        <div className="space-y-4">
          {previewQuestions.map((questionItem, index) => {
            const options = Array.isArray(questionItem?.options) ? questionItem.options : [];
            const qType = String(questionItem?.type || "single");
            const correctIndexes = qType === "multiple"
              ? (Array.isArray(questionItem?.correct) ? questionItem.correct.map((item) => Number(item)) : [])
              : (qType === "single" || qType === "bool")
                ? [Number(questionItem?.correct ?? 0)]
                : [];
            return (
              <div key={`${questionItem?.id || index}-${index}`} className="bg-white rounded-2xl border border-slate-200 p-5">
                <div className="flex items-start justify-between gap-3 mb-3">
                  <p className="text-sm font-semibold text-slate-900">{index + 1}. {questionItem?.text || "Вопрос"}</p>
                  <span className="text-[10px] bg-slate-100 text-slate-600 px-2 py-0.5 rounded-full font-medium whitespace-nowrap">
                    {questionTypeLabel(qType)}
                  </span>
                </div>

                {(qType === "single" || qType === "bool" || qType === "multiple") && (
                  <div className="space-y-2">
                    {options.map((optionItem, optionIndex) => {
                      const isCorrect = qType === "multiple"
                        ? correctIndexes.includes(optionIndex)
                        : correctIndexes[0] === optionIndex;
                      return (
                        <div
                          key={`${optionItem?.id || optionIndex}-${optionIndex}`}
                          className={`flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-xs ${isCorrect ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-slate-100 bg-white text-slate-600"}`}
                        >
                          <span>{optionItem?.text || `Вариант ${optionIndex + 1}`}</span>
                          {isCorrect && <span className="text-[10px] font-semibold text-emerald-700">Правильный</span>}
                        </div>
                      );
                    })}
                  </div>
                )}

                {qType === "text" && (
                  <div className="space-y-2">
                    <p className="text-xs text-slate-500">Правильные ключевые слова:</p>
                    <div className="flex flex-wrap gap-1.5">
                      {(Array.isArray(questionItem?.correctTextAnswers) ? questionItem.correctTextAnswers : []).map((answerItem, answerIndex) => (
                        <span key={`${answerItem}-${answerIndex}`} className="text-[11px] bg-emerald-50 text-emerald-700 border border-emerald-200 px-2 py-0.5 rounded-full">
                          {answerItem}
                        </span>
                      ))}
                      {(!Array.isArray(questionItem?.correctTextAnswers) || questionItem.correctTextAnswers.length === 0) && (
                        <span className="text-xs text-slate-400">Ключевые слова не указаны</span>
                      )}
                    </div>
                  </div>
                )}

                {questionItem?.explanation && (
                  <p className="text-xs text-slate-500 mt-3">
                    Пояснение: {questionItem.explanation}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function QuizSection({ quizView, setQuizView, answers, setAnswers, course }) {
  const [currentQ, setCurrentQ] = useState(0);
  const [timeLeft, setTimeLeft] = useState(20 * 60);
  const [score, setScore] = useState(null);
  const [results, setResults] = useState([]);
  const [autoFinished, setAutoFinished] = useState(false);
  const [textInput, setTextInput] = useState("");
  const timerRef = useRef(null);

  const attemptsLeft = course ? Math.max(0, Number(course.maxAttempts || 0) - Number(course.attemptsUsed || 0)) : 3;

  const handleFinish = useCallback((auto = false) => {
    const res = QUIZ_QUESTIONS.map(q => {
      const userAns = answers[q.id];
      const correct = isAnswerCorrect(q, userAns);
      return { question: q, userAnswer: userAns, correct };
    });
    const correctCount = res.filter(r => r.correct).length;
    setScore(Math.round(correctCount / QUIZ_QUESTIONS.length * 100));
    setResults(res);
    if (auto) setAutoFinished(true);
    setQuizView("result");
  }, [answers, setQuizView]);

  useEffect(() => {
    if (quizView !== "active") return;
    timerRef.current = setInterval(() => {
      setTimeLeft(t => {
        if (t <= 1) {
          clearInterval(timerRef.current);
          handleFinish(true);
          return 0;
        }
        return t - 1;
      });
    }, 1000);
    return () => clearInterval(timerRef.current);
  }, [quizView, handleFinish]);

  // Автосохранение текстового ответа
  useEffect(() => {
    if (QUIZ_QUESTIONS[currentQ]?.type === "text" && textInput) {
      setAnswers(p => ({ ...p, [QUIZ_QUESTIONS[currentQ].id]: textInput }));
    }
  }, [textInput, currentQ]);

  const formatTime = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  const isUrgent = timeLeft < 5 * 60;
  const q = QUIZ_QUESTIONS[currentQ];

  const handleSingleAnswer = (qId, optIdx) => setAnswers(p => ({ ...p, [qId]: optIdx }));

  const handleMultiAnswer = (qId, optIdx) => {
    setAnswers(p => {
      const prev = Array.isArray(p[qId]) ? p[qId] : [];
      return { ...p, [qId]: prev.includes(optIdx) ? prev.filter(x => x !== optIdx) : [...prev, optIdx] };
    });
  };

  const allAnswered = QUIZ_QUESTIONS.every(q => {
    const a = answers[q.id];
    if (q.type === "multiple") return Array.isArray(a) && a.length > 0;
    if (q.type === "text") return a && a.toString().trim().length > 0;
    return a !== undefined;
  });

  // ── INTRO ──
  if (quizView === "intro") {
    return (
      <div className="bg-white rounded-2xl border border-slate-200 p-8 text-center max-w-xl mx-auto">
        <div className="w-16 h-16 bg-violet-100 rounded-2xl flex items-center justify-center mx-auto mb-5">
          <HelpCircle size={28} className="text-violet-600" />
        </div>
        <h2 className="text-xl font-bold text-slate-900 mb-2">Тест по модулю 1</h2>
        <p className="text-sm text-slate-500 mb-6">Проверьте свои знания по основам информационной безопасности</p>
        <div className="grid grid-cols-3 gap-4 mb-6">
          {[{ label: "Вопросов", value: QUIZ_QUESTIONS.length }, { label: "Время", value: "20 мин" }, { label: "Порог", value: "80%" }].map(s => (
            <div key={s.label} className="bg-slate-50 rounded-xl p-4">
              <div className="text-xl font-bold text-slate-900">{s.value}</div>
              <div className="text-xs text-slate-500 mt-1">{s.label}</div>
            </div>
          ))}
        </div>
        {/* Попытки */}
        <div className={`flex items-center justify-center gap-2 text-sm mb-6 p-3 rounded-xl ${attemptsLeft <= 1 ? "bg-red-50 text-red-700" : "bg-slate-50 text-slate-600"}`}>
          <RefreshCw size={14} />
          <span>Доступно попыток: <strong>{attemptsLeft}</strong> из {course?.maxAttempts}</span>
        </div>
        {/* Типы вопросов */}
        <div className="text-left bg-slate-50 rounded-xl p-4 mb-6">
          <p className="text-xs font-semibold text-slate-700 mb-2">Типы вопросов в тесте:</p>
          <div className="grid grid-cols-2 gap-2">
            {[{ icon: RadioIcon, label: "Один правильный ответ" }, { icon: CheckSquare, label: "Несколько ответов" }, { icon: Check, label: "Верно / Неверно" }, { icon: Type, label: "Текстовый ввод" }].map(t => (
              <div key={t.label} className="flex items-center gap-2 text-xs text-slate-600"><t.icon size={12} className="text-indigo-500" />{t.label}</div>
            ))}
          </div>
        </div>
        <button onClick={() => setQuizView("active")} disabled={attemptsLeft <= 0} className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-3 rounded-xl text-sm transition-colors">
          {attemptsLeft <= 0 ? "Попытки исчерпаны" : "Начать тест"}
        </button>
      </div>
    );
  }

  // ── RESULT ──
  if (quizView === "result") {
    const passed = score >= 80;
    return (
      <div className="space-y-5 max-w-3xl 2xl:max-w-4xl mx-auto">
        {/* Summary card */}
        <div className="bg-white rounded-2xl border border-slate-200 p-8 text-center">
          {autoFinished && (
            <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 mb-5 text-xs text-amber-700 flex items-center justify-center gap-2">
              <Clock size={13} /> Тест завершён автоматически по истечении времени
            </div>
          )}
          <div className={`w-20 h-20 rounded-full flex items-center justify-center mx-auto mb-5 ${passed ? "bg-emerald-100" : "bg-red-100"}`}>
            {passed ? <CheckCircle size={36} className="text-emerald-600" /> : <XCircle size={36} className="text-red-500" />}
          </div>
          <h2 className="text-3xl font-bold text-slate-900 mb-1">{score}%</h2>
          <p className={`text-sm font-semibold mb-1 ${passed ? "text-emerald-600" : "text-red-600"}`}>{passed ? "Тест пройден!" : "Тест не пройден"}</p>
          <p className="text-sm text-slate-500 mb-6">{passed ? "Отличный результат. Вы можете продолжить курс." : `Для прохождения необходимо набрать 80%. Ваш результат: ${score}%.`}</p>
          <div className="grid grid-cols-3 gap-3 mb-6">
            {[
              { label: "Правильных", value: results.filter(r => r.correct).length, color: "text-emerald-600 bg-emerald-50" },
              { label: "Неправильных", value: results.filter(r => !r.correct).length, color: "text-red-600 bg-red-50" },
              { label: "Всего вопросов", value: QUIZ_QUESTIONS.length, color: "text-slate-700 bg-slate-50" },
            ].map(s => (
              <div key={s.label} className={`rounded-xl p-3 ${s.color.split(" ")[1]}`}>
                <div className={`text-2xl font-bold ${s.color.split(" ")[0]}`}>{s.value}</div>
                <div className="text-xs text-slate-500 mt-0.5">{s.label}</div>
              </div>
            ))}
          </div>
          <div className="flex gap-3 justify-center">
            {attemptsLeft > 1 && (
              <button onClick={() => { setAnswers({}); setCurrentQ(0); setTimeLeft(20*60); setAutoFinished(false); setTextInput(""); setQuizView("active"); }} className="max-w-[250px] w-full bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold py-3 rounded-xl text-sm transition-colors flex items-center justify-center gap-2">
                <RefreshCw size={14} /> {passed ? "Повторить тест" : "Пересдать"}
              </button>
            )}
            {attemptsLeft <= 1 && (
              <div className="max-w-[250px] w-full bg-red-50 text-red-700 font-semibold py-3 rounded-xl text-sm flex items-center justify-center gap-2">
                <XCircle size={14} /> Попытки исчерпаны
              </div>
            )}
            {passed && (
              <div className="max-w-[250px] w-full text-emerald-700 bg-emerald-50 font-semibold py-3 rounded-xl text-sm flex items-center justify-center gap-2 border border-emerald-100">
                <CheckCircle size={14} /> Успешно
              </div>
            )}
          </div>
        </div>

        {/* Detailed review — показ правильных/неправильных ответов (ТЗ 4.4, 5.8) */}
        <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-100 flex items-center gap-2">
            <ClipboardList size={16} className="text-slate-600" />
            <h3 className="text-sm font-semibold text-slate-900">Разбор ответов</h3>
          </div>
          <div className="divide-y divide-slate-100">
            {results.map((r, i) => {
              const q = r.question;
              const ua = r.userAnswer;
              return (
                <div key={q.id} className="p-5">
                  <div className="flex items-start gap-3 mb-3">
                    <div className={`w-7 h-7 rounded-full flex items-center justify-center flex-shrink-0 text-xs font-bold ${r.correct ? "bg-emerald-100 text-emerald-700" : "bg-red-100 text-red-700"}`}>
                      {r.correct ? <Check size={13} /> : <X size={13} />}
                    </div>
                    <div className="flex-1">
                      <p className="text-sm font-medium text-slate-900 leading-snug">{i + 1}. {q.text}</p>
                      {/* Тип вопроса */}
                      <span className="text-[10px] text-slate-400 mt-0.5 inline-block">
                        {q.type === "single" ? "Один ответ" : q.type === "multiple" ? "Несколько ответов" : q.type === "bool" ? "Верно/Неверно" : "Текстовый ввод"}
                      </span>
                    </div>
                  </div>

                  {/* Варианты для single/bool */}
                  {(q.type === "single" || q.type === "bool") && (
                    <div className="ml-10 space-y-1.5">
                      {q.options.map((opt, oi) => {
                        const isCorrectOpt = oi === q.correct;
                        const isUserOpt = ua === oi;
                        return (
                          <div key={oi} className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs border ${isCorrectOpt ? "bg-emerald-50 border-emerald-200 text-emerald-800" : isUserOpt && !isCorrectOpt ? "bg-red-50 border-red-200 text-red-800" : "border-slate-100 text-slate-600"}`}>
                            <div className={`w-4 h-4 rounded-full border flex items-center justify-center flex-shrink-0 ${isCorrectOpt ? "border-emerald-500 bg-emerald-500" : isUserOpt && !isCorrectOpt ? "border-red-400 bg-red-400" : "border-slate-300"}`}>
                              {isCorrectOpt && <Check size={9} className="text-white" />}
                              {isUserOpt && !isCorrectOpt && <X size={9} className="text-white" />}
                            </div>
                            {opt}
                            {isCorrectOpt && <span className="ml-auto text-emerald-600 font-semibold text-[10px]">Верно</span>}
                            {isUserOpt && !isCorrectOpt && <span className="ml-auto text-red-600 font-semibold text-[10px]">Ваш ответ</span>}
                          </div>
                        );
                      })}
                    </div>
                  )}

                  {/* Варианты для multiple */}
                  {q.type === "multiple" && (
                    <div className="ml-10 space-y-1.5">
                      {q.options.map((opt, oi) => {
                        const isCorrectOpt = q.correct.includes(oi);
                        const isUserOpt = Array.isArray(ua) && ua.includes(oi);
                        return (
                          <div key={oi} className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs border ${isCorrectOpt ? "bg-emerald-50 border-emerald-200 text-emerald-800" : isUserOpt && !isCorrectOpt ? "bg-red-50 border-red-200 text-red-800" : "border-slate-100 text-slate-600"}`}>
                            <div className={`w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 ${isCorrectOpt ? "border-emerald-500 bg-emerald-500" : isUserOpt && !isCorrectOpt ? "border-red-400 bg-red-400" : "border-slate-300"}`}>
                              {(isCorrectOpt || isUserOpt) && <Check size={9} className="text-white" />}
                            </div>
                            {opt}
                            {isCorrectOpt && !isUserOpt && <span className="ml-auto text-emerald-600 font-semibold text-[10px]">Нужно выбрать</span>}
                            {isUserOpt && !isCorrectOpt && <span className="ml-auto text-red-600 font-semibold text-[10px]">Лишний ответ</span>}
                          </div>
                        );
                      })}
                    </div>
                  )}

                  {/* Text answer */}
                  {q.type === "text" && (
                    <div className="ml-10 space-y-1.5">
                      <div className={`px-3 py-2 rounded-lg text-xs border ${r.correct ? "bg-emerald-50 border-emerald-200 text-emerald-800" : "bg-red-50 border-red-200 text-red-800"}`}>
                        Ваш ответ: <strong>{ua || "(не введён)"}</strong>
                      </div>
                      {!r.correct && (
                        <div className="px-3 py-2 rounded-lg text-xs bg-emerald-50 border border-emerald-200 text-emerald-800">
                          Ожидаемый ответ: <strong>Принцип наименьших привилегий (Least Privilege)</strong>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Объяснение */}
                  {q.explanation && (
                    <div className="ml-10 mt-2 flex gap-2 text-xs text-slate-500 bg-slate-50 rounded-lg px-3 py-2">
                      <AlertCircle size={12} className="flex-shrink-0 mt-0.5 text-indigo-400" />
                      {q.explanation}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  // ── ACTIVE QUIZ ──
  return (
    <div>
      {/* Quiz header */}
      <div className="flex items-center justify-between mb-6 bg-white rounded-2xl border border-slate-200 px-5 py-4">
        <div className="flex items-center gap-2 flex-wrap">
          {QUIZ_QUESTIONS.map((_, i) => {
            const qi = QUIZ_QUESTIONS[i];
            const hasAns = Array.isArray(answers[qi.id]) ? answers[qi.id].length > 0 : answers[qi.id] !== undefined;
            return (
              <button key={i} onClick={() => { setCurrentQ(i); if (qi.type === "text") setTextInput(answers[qi.id] || ""); }} className={`w-8 h-8 rounded-lg text-xs font-semibold transition-all ${i === currentQ ? "bg-indigo-600 text-white" : hasAns ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-500 hover:bg-slate-200"}`}>
                {i + 1}
              </button>
            );
          })}
        </div>
        <div className={`flex items-center gap-2 px-4 py-2 rounded-xl font-mono font-bold text-sm ${isUrgent ? "bg-red-50 text-red-600 animate-pulse" : "bg-slate-100 text-slate-700"}`}>
          <Clock size={14} /> {formatTime(timeLeft)}
          {isUrgent && <span className="text-[10px] font-normal">осталось!</span>}
        </div>
      </div>

      {/* Question */}
      <div className="bg-white rounded-2xl border border-slate-200 p-8 mb-5">
        <div className="flex items-center gap-2 mb-5">
          <span className="text-xs font-semibold text-indigo-600 bg-indigo-50 px-2.5 py-1 rounded-full">Вопрос {currentQ + 1} из {QUIZ_QUESTIONS.length}</span>
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            q.type === "multiple" ? "bg-violet-50 text-violet-600" :
            q.type === "bool" ? "bg-amber-50 text-amber-600" :
            q.type === "text" ? "bg-cyan-50 text-cyan-600" :
            "bg-slate-100 text-slate-500"
          }`}>
            {q.type === "multiple" ? "Несколько правильных ответов" : q.type === "bool" ? "Верно / Неверно" : q.type === "text" ? "Текстовый ввод" : "Один правильный ответ"}
          </span>
        </div>
        <p className="text-base font-semibold text-slate-900 leading-relaxed mb-6">{q.text}</p>

        {/* Single / Bool */}
        {(q.type === "single" || q.type === "bool") && (
          <div className="space-y-3">
            {q.options.map((opt, i) => (
              <button key={i} onClick={() => handleSingleAnswer(q.id, i)} className={`w-full text-left flex items-center gap-4 px-5 py-4 rounded-xl border-2 transition-all text-sm ${answers[q.id] === i ? "border-indigo-500 bg-indigo-50 text-indigo-800" : "border-slate-200 hover:border-indigo-200 hover:bg-indigo-50/50 text-slate-700"}`}>
                <div className={`w-6 h-6 rounded-full border-2 flex items-center justify-center flex-shrink-0 ${answers[q.id] === i ? "border-indigo-600 bg-indigo-600" : "border-slate-300"}`}>
                  {answers[q.id] === i && <Check size={12} className="text-white" />}
                </div>
                <span className="font-medium">{opt}</span>
              </button>
            ))}
          </div>
        )}

        {/* Multiple */}
        {q.type === "multiple" && (
          <div className="space-y-3">
            <div className="flex items-start gap-2 rounded-xl border border-violet-200 bg-violet-50 px-3 py-2">
              <AlertCircle size={14} className="mt-0.5 text-violet-600 flex-shrink-0" />
              <p className="text-xs font-medium text-violet-700">
                {"\u041c\u043e\u0436\u043d\u043e \u0432\u044b\u0431\u0440\u0430\u0442\u044c \u043d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u043e \u0432\u0430\u0440\u0438\u0430\u043d\u0442\u043e\u0432 \u043e\u0442\u0432\u0435\u0442\u0430"}
              </p>
            </div>
            {q.options.map((opt, i) => {
              const isSelected = Array.isArray(answers[q.id]) && answers[q.id].includes(i);
              return (
                <button key={i} onClick={() => handleMultiAnswer(q.id, i)} className={`w-full text-left flex items-center gap-4 px-5 py-4 rounded-xl border-2 transition-all text-sm ${isSelected ? "border-indigo-500 bg-indigo-50 text-indigo-800" : "border-slate-200 hover:border-indigo-200 hover:bg-indigo-50/50 text-slate-700"}`}>
                  <div className={`w-6 h-6 rounded border-2 flex items-center justify-center flex-shrink-0 ${isSelected ? "border-indigo-600 bg-indigo-600" : "border-slate-300"}`}>
                    {isSelected && <Check size={12} className="text-white" />}
                  </div>
                  <span className="font-medium">{opt}</span>
                </button>
              );
            })}
          </div>
        )}

        {/* Text */}
        {q.type === "text" && (
          <div>
            <p className="text-xs text-slate-400 mb-2">Введите ответ в свободной форме</p>
            <input
              value={textInput}
              onChange={e => { setTextInput(e.target.value); setAnswers(p => ({ ...p, [q.id]: e.target.value })); }}
              placeholder="Ваш ответ..."
              className="w-full px-4 py-3 bg-slate-50 border-2 border-slate-200 rounded-xl text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:border-indigo-400 transition-all"
            />
          </div>
        )}
      </div>

      {/* Navigation */}
      <div className="flex items-center justify-between">
        <button disabled={currentQ === 0} onClick={() => setCurrentQ(p => p - 1)} className="flex items-center gap-2 px-4 py-2.5 bg-white border border-slate-200 rounded-xl text-sm font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
          <ChevronLeft size={16} /> Назад
        </button>
        <span className="text-xs text-slate-400">
          Отвечено: {QUIZ_QUESTIONS.filter(q => { const a = answers[q.id]; return Array.isArray(a) ? a.length > 0 : a !== undefined; }).length} / {QUIZ_QUESTIONS.length}
        </span>
        {currentQ < QUIZ_QUESTIONS.length - 1 ? (
          <button onClick={() => { setCurrentQ(p => p + 1); const nextQ = QUIZ_QUESTIONS[currentQ + 1]; if (nextQ?.type === "text") setTextInput(answers[nextQ.id] || ""); }} className="flex items-center gap-2 px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 rounded-xl text-sm font-medium text-white transition-colors">
            Далее <ChevronRight size={16} />
          </button>
        ) : (
          <button onClick={() => handleFinish(false)} disabled={!allAnswered} className="flex items-center gap-2 px-5 py-2.5 bg-emerald-600 hover:bg-emerald-700 rounded-xl text-sm font-semibold text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed">
            <FileCheck size={16} /> Завершить тест
          </button>
        )}
      </div>
      {!allAnswered && currentQ === QUIZ_QUESTIONS.length - 1 && (
        <p className="text-center text-xs text-slate-400 mt-3">Ответьте на все вопросы, чтобы завершить тест</p>
      )}
    </div>
  );
}

// ─── CERTIFICATES ─────────────────────────────────────────────────────────────

function CertificatesView({ certificates = [], onDownload, loading = false }) {
  const safeCertificates = Array.isArray(certificates) ? certificates : [];
  if (loading) {
    return (
      <div className="grid [grid-template-columns:repeat(auto-fill,minmax(340px,1fr))] gap-5">
        {Array.from({ length: 6 }).map((_, idx) => (
          <div key={`certificate-skeleton-${idx}`} className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
            <SkeletonBlock className="h-36 w-full rounded-none" />
            <div className="p-5 space-y-3">
              <SkeletonBlock className="w-9/12 h-4" />
              <SkeletonBlock className="w-6/12 h-3.5" />
              <SkeletonBlock className="w-5/12 h-3.5" />
              <div className="flex gap-2 pt-1">
                <SkeletonBlock className="h-9 flex-1" />
                <SkeletonBlock className="h-9 w-10" />
              </div>
            </div>
          </div>
        ))}
      </div>
    );
  }
  return (
    <div>
      {safeCertificates.length === 0 ? (
        <div className="text-center py-20 text-slate-400"><Award size={40} className="mx-auto mb-3 opacity-30" /><p className="text-sm">Сертификатов пока нет</p></div>
      ) : (
        <div className="grid [grid-template-columns:repeat(auto-fill,minmax(340px,1fr))] gap-5">
          {safeCertificates.map((cert, index) => (
            <div key={cert.id} className="bg-white rounded-2xl border border-slate-200 overflow-hidden hover:shadow-md transition-all">
              {/* Certificate preview */}
              <div className={`h-36 bg-gradient-to-br ${cert.color || pickCourseVisual(cert?.id || index).color} flex items-center justify-center relative px-6`}>
                <div className="absolute inset-0 opacity-10">
                  <div className="absolute top-2 left-2 w-16 h-16 border-2 border-white rounded-full" />
                  <div className="absolute bottom-2 right-2 w-10 h-10 border border-white rounded-full" />
                </div>
                <div className="text-center relative z-10">
                  <Award size={24} className="text-white/60 mx-auto mb-1" />
                  <p className="text-white/60 text-[9px] uppercase tracking-widest mb-1">Сертификат о прохождении</p>
                  <p className="text-white font-bold text-sm leading-tight text-center px-2 line-clamp-2">{cert.course}</p>
                  <p className="text-white/70 text-[10px] mt-1">{cert.employee}</p>
                </div>
              </div>
              <div className="p-5">
                <div className="flex items-center justify-between mb-3">
                  <div><p className="text-xs text-slate-500 mb-0.5">Дата выдачи</p><p className="text-sm font-semibold text-slate-800">{cert.date}</p></div>
                  <div className="text-right"><p className="text-xs text-slate-500 mb-0.5">Объём</p><p className="text-sm font-semibold text-slate-800">{cert.hours} ч</p></div>
                </div>
                <p className="text-[10px] text-slate-400 mb-4 font-mono">№ {cert.number}</p>
                <div className="flex gap-2">
                  <button onClick={() => onDownload?.(cert)} className="flex-1 flex items-center justify-center gap-2 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 text-xs font-semibold py-2.5 rounded-xl transition-colors"><Download size={13} /> Скачать PDF</button>
                  <button onClick={() => cert?.verifyUrl && window.open(cert.verifyUrl, "_blank", "noopener,noreferrer")} className="flex items-center justify-center gap-2 bg-slate-50 hover:bg-slate-100 text-slate-600 text-xs font-semibold px-3 py-2.5 rounded-xl transition-colors disabled:opacity-50" disabled={!cert?.verifyUrl}><Link2 size={13} /></button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── NOTIFICATIONS ────────────────────────────────────────────────────────────

function NotificationsView({ notifications = [], onRead, loading = false }) {
  const iconMap = { deadline: AlertCircle, completed: CheckCircle, assigned: BookOpen, certificate: Award };
  const colorMap = { deadline: "text-amber-600 bg-amber-50", completed: "text-emerald-600 bg-emerald-50", assigned: "text-indigo-600 bg-indigo-50", certificate: "text-violet-600 bg-violet-50" };
  const safeNotifications = Array.isArray(notifications) ? notifications : [];
  if (loading) {
    return (
      <div className="space-y-3 max-w-4xl 2xl:max-w-5xl">
        {Array.from({ length: 6 }).map((_, idx) => (
          <div key={`notification-skeleton-${idx}`} className="bg-white rounded-2xl border border-slate-200 p-5 flex items-start gap-4">
            <SkeletonBlock className="w-10 h-10 flex-shrink-0" />
            <div className="flex-1 space-y-2.5">
              <SkeletonBlock className="w-7/12 h-4" />
              <SkeletonBlock className="w-11/12 h-3.5" />
              <SkeletonBlock className="w-24 h-3" />
            </div>
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="space-y-3 max-w-4xl 2xl:max-w-5xl">
      {safeNotifications.length === 0 && (
        <div className="text-center py-20 text-slate-400">
          <Bell size={40} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">Уведомлений пока нет</p>
        </div>
      )}
      {safeNotifications.map(n => {
        const Icon = iconMap[n.type] || Bell;
        const cls = colorMap[n.type] || "text-slate-600 bg-slate-100";
        return (
          <div key={n.id} onClick={() => !n.read && onRead?.(n.id)} className={`bg-white rounded-2xl border p-5 flex items-start gap-4 transition-all ${n.read ? "border-slate-200 opacity-75" : "border-slate-200 shadow-sm cursor-pointer hover:border-indigo-200"}`}>
            <div className={`w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0 ${cls}`}><Icon size={18} /></div>
            <div className="flex-1">
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-semibold text-slate-900">{n.title}</p>
                {!n.read && <div className="w-2 h-2 bg-indigo-500 rounded-full flex-shrink-0 mt-1.5" />}
              </div>
              <p className="text-xs text-slate-600 mt-1 leading-relaxed">{n.message}</p>
              <p className="text-[10px] text-slate-400 mt-2">{n.time}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─── COURSE BUILDER ───────────────────────────────────────────────────────────

function CourseBuilder({
  onBack,
  lmsRequest,
  canUseManagerApi,
  learners = [],
  adminCourses = [],
  loading = false,
  emitToast,
  onAfterSave,
  initialCourseId = null,
  initialDraftVersionId = null,
}) {
  const buildLesson = useCallback((overrides = {}) => ({
    id: Date.now() + Math.floor(Math.random() * 1000),
    title: "Новый урок",
    type: "video",
    description: "",
    durationSeconds: "",
    completionThreshold: 95,
    contentText: "",
    materials: [],
    quizQuestionsPerTest: 5,
    quizTimeLimitMinutes: "",
    quizPassingScore: 80,
    quizAttemptLimit: 3,
    quizRandomOrder: true,
    quizShowExplanations: true,
    quizQuestions: [],
    combinedBlocks: [],
    removedCombinedBlocks: [],
    combinedHasQuiz: false,
    combinedQuizQuestionsPerTest: 5,
    combinedQuizTimeLimitMinutes: "",
    combinedQuizPassingScore: 80,
    combinedQuizAttemptLimit: 3,
    combinedQuizRandomOrder: true,
    combinedQuizShowExplanations: true,
    combinedQuizQuestions: [],
    ...overrides,
  }), []);

  const [tab, setTab] = useState("settings");
  const [modules, setModules] = useState([
    {
      id: 1,
      title: "Модуль 1: Введение",
      expanded: true,
      lessons: [
        buildLesson({ id: 1, title: "Вводный урок", type: "video" }),
        buildLesson({ id: 2, title: "Основные концепции", type: "text", durationSeconds: 8 * 60 }),
      ],
    },
  ]);
  const [questions, setQuestions] = useState([
    { id: 1, text: "Вопрос 1", type: "single", options: ["Ответ A", "Ответ B", "Ответ C", "Ответ D"], correct: 0, explanation: "" },
  ]);
  const [settings, setSettings] = useState({
    title: "",
    description: "",
    category: "",
    mandatory: false,
    passingScore: 80,
    maxAttempts: 3,
    questionsPerTest: 5,
    finalTestTimeLimitMinutes: 20,
    randomOrder: true,
    showExplanations: true,
    coverUrl: "",
    coverBucket: "",
    coverBlobPath: "",
    skills: [],
  });
  const [customCategories, setCustomCategories] = useState([]);
  const [categoryDropdownOpen, setCategoryDropdownOpen] = useState(false);
  const [newCategoryInput, setNewCategoryInput] = useState("");
  const categoryDropdownRef = useRef(null);
  const [newSkill, setNewSkill] = useState("");
  const [lessonMaterialLink, setLessonMaterialLink] = useState("");
  const [selectedLessonId, setSelectedLessonId] = useState(null);
  const [draggedCombinedBlockId, setDraggedCombinedBlockId] = useState(null);
  const [builderLessonPanelMode, setBuilderLessonPanelMode] = useState("edit");
  const [operatorEditField, setOperatorEditField] = useState(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [loadingCourseDraft, setLoadingCourseDraft] = useState(false);
  const [editingCourseId, setEditingCourseId] = useState(null);
  const [savedVersionId, setSavedVersionId] = useState(null);
  const [savedVersionNumber, setSavedVersionNumber] = useState(null);
  const [courseHistoryVersions, setCourseHistoryVersions] = useState([]);
  const [historyViewVersionId, setHistoryViewVersionId] = useState(null);
  const [activeCourseVersionNumber, setActiveCourseVersionNumber] = useState(null);
  const [activeCourseVersionStatus, setActiveCourseVersionStatus] = useState("");
  const [publishCertificatesAction, setPublishCertificatesAction] = useState("keep");
  const [createdCourseId, setCreatedCourseId] = useState(null);
  const [selectedLearnerIds, setSelectedLearnerIds] = useState([]);
  const [assigning, setAssigning] = useState(false);
  const [assignmentSearch, setAssignmentSearch] = useState("");
  const [assignmentDueAt, setAssignmentDueAt] = useState("");
  const [assignmentCourseId, setAssignmentCourseId] = useState(null);
  const [coverUploading, setCoverUploading] = useState(false);
  const [lessonUploading, setLessonUploading] = useState(false);
  const coverInputRef = useRef(null);
  const lessonVideoInputRef = useRef(null);
  const lessonMaterialInputRef = useRef(null);

  const updateLessonById = useCallback((lessonId, updater) => {
    setModules((prev) => prev.map((moduleItem) => ({
      ...moduleItem,
      lessons: (Array.isArray(moduleItem?.lessons) ? moduleItem.lessons : []).map((lessonItem) => {
        if (lessonItem.id !== lessonId) return lessonItem;
        const nextLesson = typeof updater === "function" ? updater(lessonItem) : { ...lessonItem, ...(updater || {}) };
        return {
          ...lessonItem,
          ...nextLesson,
          materials: Array.isArray(nextLesson?.materials) ? nextLesson.materials : (Array.isArray(lessonItem?.materials) ? lessonItem.materials : []),
        };
      }),
    })));
  }, []);

  const selectedLessonModel = (() => {
    for (const moduleItem of modules) {
      const found = (Array.isArray(moduleItem?.lessons) ? moduleItem.lessons : []).find((lessonItem) => lessonItem.id === selectedLessonId);
      if (found) return found;
    }
    return null;
  })();

  useEffect(() => {
    setOperatorEditField(null);
  }, [selectedLessonId, builderLessonPanelMode]);

  useEffect(() => {
    if (!categoryDropdownOpen) return;
    const handleOutsideClick = (e) => {
      if (categoryDropdownRef.current && !categoryDropdownRef.current.contains(e.target)) {
        setCategoryDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, [categoryDropdownOpen]);

  const addModule = () => setModules((prev) => [...prev, { id: Date.now(), title: `Модуль ${prev.length + 1}`, expanded: true, lessons: [] }]);
  const toggleModule = (id) => setModules((prev) => prev.map((moduleItem) => moduleItem.id === id ? { ...moduleItem, expanded: !moduleItem.expanded } : moduleItem));
  const addLesson = (modId) => setModules((prev) => prev.map((moduleItem) => moduleItem.id === modId ? { ...moduleItem, lessons: [...moduleItem.lessons, buildLesson()] } : moduleItem));
  const deleteLesson = (modId, lessonId) => {
    setModules((prev) => prev.map((moduleItem) => moduleItem.id === modId ? { ...moduleItem, lessons: moduleItem.lessons.filter((lessonItem) => lessonItem.id !== lessonId) } : moduleItem));
    setSelectedLessonId((prev) => (prev === lessonId ? null : prev));
  };
  const deleteModule = (id) => setModules((prev) => prev.filter((moduleItem) => moduleItem.id !== id));

  const createQuestionTemplate = useCallback((type = "single") => ({
    id: Date.now() + Math.floor(Math.random() * 1000),
    text: "",
    type,
    options: type === "bool" ? ["Верно", "Неверно"] : type === "text" ? [] : ["", "", "", ""],
    correct: type === "multiple" ? [] : 0,
    explanation: "",
    correct_text_answers: [],
  }), []);

  const addQuestion = (type = "single") => setQuestions((prev) => [...prev, createQuestionTemplate(type)]);

  const updateQuestionType = (qId, newType) => {
    setQuestions((prev) => prev.map((question) => question.id === qId ? {
      ...question,
      type: newType,
      options: newType === "bool" ? ["Верно", "Неверно"] : newType === "text" ? [] : question.options.length >= 2 ? question.options : ["", "", "", ""],
      correct: newType === "multiple" ? [] : 0,
      correct_text_answers: newType === "text" ? (Array.isArray(question.correct_text_answers) ? question.correct_text_answers : []) : [],
    } : question));
  };

  const addLessonQuizQuestion = useCallback((lessonId, type = "single") => {
    if (!lessonId) return;
    updateLessonById(lessonId, (prev) => ({
      ...prev,
      quizQuestions: [...(Array.isArray(prev?.quizQuestions) ? prev.quizQuestions : []), createQuestionTemplate(type)],
    }));
  }, [updateLessonById, createQuestionTemplate]);

  const updateLessonQuizQuestionType = useCallback((lessonId, questionId, newType) => {
    if (!lessonId || !questionId) return;
    updateLessonById(lessonId, (prev) => ({
      ...prev,
      quizQuestions: (Array.isArray(prev?.quizQuestions) ? prev.quizQuestions : []).map((question) => (
        question.id === questionId
          ? {
            ...question,
            type: newType,
            options: newType === "bool" ? ["Верно", "Неверно"] : newType === "text" ? [] : question.options.length >= 2 ? question.options : ["", "", "", ""],
            correct: newType === "multiple" ? [] : 0,
            correct_text_answers: newType === "text" ? (Array.isArray(question.correct_text_answers) ? question.correct_text_answers : []) : [],
          }
          : question
      )),
    }));
  }, [updateLessonById]);

  const addCombinedQuizQuestion = useCallback((lessonId, type = "single") => {
    if (!lessonId) return;
    updateLessonById(lessonId, (prev) => ({
      ...prev,
      combinedQuizQuestions: [...(Array.isArray(prev?.combinedQuizQuestions) ? prev.combinedQuizQuestions : []), createQuestionTemplate(type)],
    }));
  }, [updateLessonById, createQuestionTemplate]);

  const updateCombinedQuizQuestionType = useCallback((lessonId, questionId, newType) => {
    if (!lessonId || !questionId) return;
    updateLessonById(lessonId, (prev) => ({
      ...prev,
      combinedQuizQuestions: (Array.isArray(prev?.combinedQuizQuestions) ? prev.combinedQuizQuestions : []).map((question) => (
        question.id === questionId
          ? {
            ...question,
            type: newType,
            options: newType === "bool" ? ["Верно", "Неверно"] : newType === "text" ? [] : question.options.length >= 2 ? question.options : ["", "", "", ""],
            correct: newType === "multiple" ? [] : 0,
            correct_text_answers: newType === "text" ? (Array.isArray(question.correct_text_answers) ? question.correct_text_answers : []) : [],
          }
          : question
      )),
    }));
  }, [updateLessonById]);

  const buildDefaultModules = useCallback(() => ([
    {
      id: 1,
      title: "Модуль 1: Введение",
      expanded: true,
      lessons: [
        buildLesson({ id: 1, title: "Вводный урок", type: "video" }),
        buildLesson({ id: 2, title: "Основные концепции", type: "text", durationSeconds: 8 * 60 }),
      ],
    },
  ]), [buildLesson]);

  const buildDefaultQuestions = useCallback(() => ([
    { id: 1, text: "Вопрос 1", type: "single", options: ["Ответ A", "Ответ B", "Ответ C", "Ответ D"], correct: 0, explanation: "" },
  ]), []);

  const buildDefaultSettings = useCallback(() => ({
    title: "",
    description: "",
    category: "",
    mandatory: false,
    passingScore: 80,
    maxAttempts: 3,
    questionsPerTest: 5,
    finalTestTimeLimitMinutes: 20,
    randomOrder: true,
    showExplanations: true,
    coverUrl: "",
    coverBucket: "",
    coverBlobPath: "",
    skills: [],
  }), []);

  const resetBuilderDraft = useCallback(() => {
    setModules(buildDefaultModules());
    setQuestions(buildDefaultQuestions());
    setSettings(buildDefaultSettings());
    setCustomCategories([]);
    setCategoryDropdownOpen(false);
    setNewCategoryInput("");
    setNewSkill("");
    setLessonMaterialLink("");
    setSelectedLessonId(null);
    setBuilderLessonPanelMode("edit");
    setOperatorEditField(null);
    setSaved(false);
    setSaving(false);
    setPublishing(false);
    setEditingCourseId(null);
    setSavedVersionId(null);
    setSavedVersionNumber(null);
    setCourseHistoryVersions([]);
    setHistoryViewVersionId(null);
    setActiveCourseVersionNumber(null);
    setActiveCourseVersionStatus("");
    setCreatedCourseId(null);
    setAssignmentCourseId(null);
    setSelectedLearnerIds([]);
    setAssignmentDueAt("");
    setPublishCertificatesAction("keep");
  }, [buildDefaultModules, buildDefaultQuestions, buildDefaultSettings]);

  const mapApiQuestionToBuilder = useCallback((question, fallbackId = 0) => {
    const qType = mapApiQuestionTypeToView(question?.type);
    const apiOptions = Array.isArray(question?.options) ? question.options : [];
    const optionTexts = apiOptions.map((option) => String(option?.text || "")).filter((text) => text !== "");
    let correct = qType === "multiple" ? [] : 0;
    if (qType === "multiple") {
      correct = apiOptions
        .map((option, optionIndex) => (option?.is_correct ? optionIndex : null))
        .filter((index) => index != null);
    } else if (qType === "single" || qType === "bool") {
      const correctIndex = apiOptions.findIndex((option) => Boolean(option?.is_correct));
      correct = correctIndex >= 0 ? correctIndex : 0;
    }
    return {
      id: Number(question?.id || fallbackId || Date.now() + Math.floor(Math.random() * 1000)),
      text: String(question?.prompt || question?.text || "").trim(),
      type: qType,
      options: qType === "text"
        ? []
        : (optionTexts.length > 0 ? optionTexts : (qType === "bool" ? ["Верно", "Неверно"] : ["", "", "", ""])),
      correct,
      explanation: String(question?.metadata?.explanation || "").trim(),
      correct_text_answers: qType === "text"
        ? (Array.isArray(question?.correct_text_answers) ? question.correct_text_answers.map((item) => String(item || "").trim()).filter(Boolean) : [])
        : [],
    };
  }, []);

  const hydrateBuilderFromCourse = useCallback((coursePayload, options = {}) => {
    const mode = String(options?.mode || "edit").toLowerCase();
    const isHistoryView = mode === "history";
    const resolvedVersionId = Number(options?.versionId || coursePayload?.course_version?.id || 0) || null;
    const loadedVersionStatus = String(coursePayload?.course_version?.status || "").trim().toLowerCase();
    const courseId = Number(coursePayload?.id || 0) || null;
    const modulesPayload = (Array.isArray(coursePayload?.modules) ? coursePayload.modules : [])
      .slice()
      .sort((a, b) => Number(a?.position || 0) - Number(b?.position || 0));
    const testsPayload = Array.isArray(coursePayload?.tests) ? coursePayload.tests : [];

    const mappedModules = modulesPayload.map((moduleItem, moduleIndex) => {
      const lessonsPayload = (Array.isArray(moduleItem?.lessons) ? moduleItem.lessons : [])
        .slice()
        .sort((a, b) => Number(a?.position || 0) - Number(b?.position || 0));
      const mappedLessons = lessonsPayload.map((lessonItem, lessonIndex) => {
        const lessonMaterials = Array.isArray(lessonItem?.materials) ? lessonItem.materials : [];
        const videoMaterial = lessonMaterials.find((material) => String(material?.material_type || material?.type || "").toLowerCase() === "video");
        const textMaterial = lessonMaterials.find((material) => String(material?.material_type || material?.type || "").toLowerCase() === "text");
        const lessonType = inferLessonType({ ...lessonItem, materials: lessonMaterials });
        const normalizedMaterials = lessonMaterials.map((material) => ({
          ...material,
          title: String(material?.title || "").trim() || "Материал",
          material_type: String(material?.material_type || material?.type || "file").toLowerCase(),
          content_url: String(material?.content_url || material?.url || material?.signed_url || "").trim(),
          signed_url: String(material?.signed_url || material?.url || material?.content_url || "").trim(),
          mime_type: String(material?.mime_type || "").trim(),
          bucket: String(material?.bucket || material?.gcs_bucket || "").trim(),
          blob_path: String(material?.blob_path || material?.gcs_blob_path || "").trim(),
          metadata: material?.metadata && typeof material.metadata === "object" ? material.metadata : {},
          position: Number(material?.position || 0) || 0,
        }));
        const combinedBlocks = lessonType === "combined"
          ? (
            Array.isArray(lessonItem?.blocks) && lessonItem.blocks.length > 0
              ? lessonItem.blocks
              : normalizedMaterials.filter((material) => {
                const materialType = String(material?.material_type || material?.type || "").toLowerCase();
                return materialType === "text" || materialType === "video";
              }).map((material, materialIndex) => ({
                id: Number(material?.id || 0) || Date.now() + materialIndex + Math.floor(Math.random() * 1000),
                type: String(material?.material_type || material?.type || "text").toLowerCase(),
                title: String(material?.title || `Блок ${materialIndex + 1}`).trim(),
                contentText: normalizeRichTextValue(material?.content_text || ""),
                contentUrl: String(material?.content_url || material?.signed_url || material?.url || "").trim(),
                signed_url: String(material?.signed_url || material?.url || material?.content_url || "").trim(),
                mime_type: String(material?.mime_type || "").trim(),
                bucket: String(material?.bucket || material?.gcs_bucket || "").trim(),
                blob_path: String(material?.blob_path || material?.gcs_blob_path || "").trim(),
                metadata: material?.metadata && typeof material.metadata === "object" ? material.metadata : {},
                position: Number(material?.position || materialIndex + 1),
              }))
          )
          : [];
        const filteredLessonMaterials = lessonType === "combined"
          ? normalizedMaterials.filter((material) => {
            const materialType = String(material?.material_type || material?.type || "").toLowerCase();
            return materialType !== "text" && materialType !== "video";
          })
          : normalizedMaterials;
        return buildLesson({
          id: Number(lessonItem?.id || `${moduleIndex + 1}${lessonIndex + 1}${Date.now()}`),
          title: String(lessonItem?.title || "").trim() || `Урок ${lessonIndex + 1}`,
          type: lessonType,
          description: normalizeRichTextValue(lessonItem?.description || ""),
          durationSeconds: Number(lessonItem?.duration_seconds || videoMaterial?.metadata?.duration_seconds || 0) || "",
          completionThreshold: Number(lessonItem?.completion_threshold || 95) || 95,
          contentText: normalizeRichTextValue(textMaterial?.content_text || ""),
          materials: filteredLessonMaterials,
          combinedBlocks,
          removedCombinedBlocks: [],
          combinedHasQuiz: false,
          combinedQuizQuestionsPerTest: 5,
          combinedQuizTimeLimitMinutes: "",
          combinedQuizPassingScore: Math.max(1, Math.min(100, Number(coursePayload?.course_version?.pass_threshold || 80))),
          combinedQuizAttemptLimit: Math.max(1, Number(coursePayload?.course_version?.attempt_limit || 3)),
          combinedQuizRandomOrder: true,
          combinedQuizShowExplanations: true,
          combinedQuizQuestions: [],
        });
      });
      return {
        id: Number(moduleItem?.id || moduleIndex + 1),
        title: String(moduleItem?.title || "").trim() || `Модуль ${moduleIndex + 1}`,
        expanded: true,
        lessons: mappedLessons,
      };
    });

    if (mappedModules.length === 0) {
      mappedModules.push({
        id: Date.now(),
        title: "Модуль 1",
        expanded: true,
        lessons: [],
      });
    }

    const moduleById = new Map(mappedModules.map((moduleItem) => [Number(moduleItem.id), moduleItem]));
    const lessonById = new Map();
    mappedModules.forEach((moduleItem) => {
      (Array.isArray(moduleItem?.lessons) ? moduleItem.lessons : []).forEach((lessonItem) => {
        lessonById.set(Number(lessonItem?.id || 0), lessonItem);
      });
    });
    const finalTests = testsPayload.filter((test) => Boolean(test?.is_final));
    const combinedLinkedTests = testsPayload.filter((test) => {
      if (Boolean(test?.is_final)) return false;
      const sourceLessonId = Number(test?.source_lesson_id || 0);
      if (!sourceLessonId) return false;
      const sourceLesson = lessonById.get(sourceLessonId);
      return String(sourceLesson?.type || "").toLowerCase() === "combined";
    });
    const moduleTests = testsPayload.filter((test) => !Boolean(test?.is_final) && !combinedLinkedTests.includes(test));

    moduleTests.forEach((test, testIndex) => {
      const questionsForTest = Array.isArray(test?.questions) ? test.questions : [];
      const mappedQuizQuestions = questionsForTest.map((question, questionIndex) => (
        mapApiQuestionToBuilder(question, Number(`${test?.id || testIndex + 1}${questionIndex + 1}`))
      ));
      const targetModule = moduleById.get(Number(test?.module_id || 0)) || mappedModules[mappedModules.length - 1];
      if (!targetModule) return;
      const timeLimitMinutes = Number(test?.time_limit_minutes || 0);
      targetModule.lessons.push(buildLesson({
        id: Date.now() + Math.floor(Math.random() * 1000) + testIndex,
        title: String(test?.title || "").trim() || `Тест ${testIndex + 1}`,
        type: "quiz",
        description: normalizeRichTextValue(test?.description || ""),
        durationSeconds: timeLimitMinutes > 0 ? timeLimitMinutes * 60 : "",
        completionThreshold: 100,
        quizQuestionsPerTest: Math.max(1, Number(test?.question_count || mappedQuizQuestions.length || 1)),
        quizTimeLimitMinutes: timeLimitMinutes > 0 ? timeLimitMinutes : "",
        quizPassingScore: Math.max(1, Math.min(100, Number(test?.pass_threshold || coursePayload?.course_version?.pass_threshold || 80))),
        quizAttemptLimit: Math.max(1, Number(test?.attempt_limit || coursePayload?.course_version?.attempt_limit || 3)),
        quizRandomOrder: test?.random_order !== false,
        quizShowExplanations: true,
        quizQuestions: mappedQuizQuestions.length > 0 ? mappedQuizQuestions : [createQuestionTemplate("single")],
        materials: [],
      }));
    });

    combinedLinkedTests.forEach((test, testIndex) => {
      const sourceLessonId = Number(test?.source_lesson_id || 0);
      const targetLesson = sourceLessonId > 0 ? lessonById.get(sourceLessonId) : null;
      if (!targetLesson) return;
      const questionsForTest = Array.isArray(test?.questions) ? test.questions : [];
      const mappedQuizQuestions = questionsForTest.map((question, questionIndex) => (
        mapApiQuestionToBuilder(question, Number(`${test?.id || 700 + testIndex}${questionIndex + 1}`))
      ));
      const timeLimitMinutes = Number(test?.time_limit_minutes || 0);
      targetLesson.combinedHasQuiz = true;
      targetLesson.combinedQuizQuestionsPerTest = Math.max(1, Number(test?.question_count || mappedQuizQuestions.length || 1));
      targetLesson.combinedQuizTimeLimitMinutes = timeLimitMinutes > 0 ? timeLimitMinutes : "";
      targetLesson.combinedQuizPassingScore = Math.max(1, Math.min(100, Number(test?.pass_threshold || coursePayload?.course_version?.pass_threshold || 80)));
      targetLesson.combinedQuizAttemptLimit = Math.max(1, Number(test?.attempt_limit || coursePayload?.course_version?.attempt_limit || 3));
      targetLesson.combinedQuizRandomOrder = test?.random_order !== false;
      targetLesson.combinedQuizShowExplanations = true;
      targetLesson.combinedQuizQuestions = mappedQuizQuestions.length > 0 ? mappedQuizQuestions : [createQuestionTemplate("single")];
    });

    const primaryFinalTest = finalTests[0] || null;
    const finalQuestionsPayload = Array.isArray(primaryFinalTest?.questions) ? primaryFinalTest.questions : [];
    const mappedFinalQuestions = finalQuestionsPayload.map((question, questionIndex) => (
      mapApiQuestionToBuilder(question, Number(`9${questionIndex + 1}${Date.now()}`))
    ));

    setModules(mappedModules);
    setQuestions(mappedFinalQuestions.length > 0 ? mappedFinalQuestions : buildDefaultQuestions());
    setSettings((prev) => ({
      ...prev,
      title: String(coursePayload?.title || "").trim(),
      description: normalizeRichTextValue(coursePayload?.description || ""),
      category: String(coursePayload?.category || "").trim(),
      mandatory: false,
      passingScore: Math.max(1, Math.min(100, Number(
        coursePayload?.course_version?.pass_threshold
          || coursePayload?.default_pass_threshold
          || 80
      ))),
      maxAttempts: Math.max(1, Number(
        coursePayload?.course_version?.attempt_limit
          || coursePayload?.default_attempt_limit
          || 3
      )),
      questionsPerTest: Math.max(1, Number(primaryFinalTest?.question_count || mappedFinalQuestions.length || 5)),
      finalTestTimeLimitMinutes: primaryFinalTest?.time_limit_minutes != null
        ? Number(primaryFinalTest.time_limit_minutes || 0)
        : "",
      randomOrder: primaryFinalTest ? primaryFinalTest.random_order !== false : true,
      showExplanations: true,
      coverUrl: String(coursePayload?.course_version?.cover_url || "").trim(),
      coverBucket: String(coursePayload?.course_version?.cover_bucket || "").trim(),
      coverBlobPath: String(coursePayload?.course_version?.cover_blob_path || "").trim(),
      skills: Array.isArray(coursePayload?.course_version?.skills)
        ? coursePayload.course_version.skills.map((item) => String(item || "").trim()).filter(Boolean)
        : [],
    }));
    setSelectedLessonId(mappedModules[0]?.lessons?.[0]?.id || null);
    setEditingCourseId(courseId);
    setCreatedCourseId(courseId);
    setAssignmentCourseId(courseId);
    const editableDraftVersionId = !isHistoryView && loadedVersionStatus === "draft" ? resolvedVersionId : null;
    const editableDraftVersionNumber = !isHistoryView && loadedVersionStatus === "draft"
      ? (Number(coursePayload?.course_version?.version_number || 0) || null)
      : null;
    setSavedVersionId(editableDraftVersionId);
    setSavedVersionNumber(editableDraftVersionNumber);
    setHistoryViewVersionId(isHistoryView ? resolvedVersionId : null);
    setActiveCourseVersionNumber(Number(coursePayload?.course_version?.version_number || 0) || null);
    setActiveCourseVersionStatus(loadedVersionStatus);
    setPublishCertificatesAction("keep");
    setSaved(false);
  }, [buildLesson, buildDefaultQuestions, createQuestionTemplate, mapApiQuestionToBuilder]);

  const loadCourseDraft = useCallback(async (courseId) => {
    const normalizedCourseId = Number(courseId || 0) || null;
    if (!normalizedCourseId) {
      resetBuilderDraft();
      return;
    }
    if (typeof lmsRequest !== "function") {
      emitToast?.("LMS API не подключен", "error");
      return;
    }
    setLoadingCourseDraft(true);
    try {
      const [payload, historyPayload] = await Promise.all([
        lmsRequest(`/api/lms/admin/courses?course_id=${normalizedCourseId}`),
        lmsRequest(`/api/lms/admin/courses/${normalizedCourseId}/history`).catch(() => null),
      ]);
      if (!payload?.course) {
        throw new Error("Курс не найден");
      }
      hydrateBuilderFromCourse(payload.course, { mode: "edit" });
      setCourseHistoryVersions(Array.isArray(historyPayload?.versions) ? historyPayload.versions : []);
    } catch (error) {
      emitToast?.(`Не удалось загрузить курс для редактирования: ${String(error?.message || "ошибка")}`, "error");
    } finally {
      setLoadingCourseDraft(false);
    }
  }, [emitToast, hydrateBuilderFromCourse, lmsRequest, resetBuilderDraft]);

  const loadHistoryVersion = useCallback(async (courseId, versionId) => {
    const normalizedCourseId = Number(courseId || 0) || null;
    const normalizedVersionId = Number(versionId || 0) || null;
    if (!normalizedCourseId || !normalizedVersionId) return;
    if (typeof lmsRequest !== "function") {
      emitToast?.("LMS API не подключен", "error");
      return;
    }

    setLoadingCourseDraft(true);
    try {
      const payload = await lmsRequest(
        `/api/lms/admin/courses/${normalizedCourseId}/history?course_version_id=${normalizedVersionId}`
      );
      if (!payload?.course) {
        throw new Error("Версия курса не найдена");
      }
      setCourseHistoryVersions(Array.isArray(payload?.versions) ? payload.versions : []);
      hydrateBuilderFromCourse(payload.course, { mode: "history", versionId: normalizedVersionId });
    } catch (error) {
      emitToast?.(`Не удалось открыть историческую версию: ${String(error?.message || "ошибка")}`, "error");
    } finally {
      setLoadingCourseDraft(false);
    }
  }, [emitToast, hydrateBuilderFromCourse, lmsRequest]);

  const loadDraftVersionForEditing = useCallback(async (courseId, versionId) => {
    const normalizedCourseId = Number(courseId || 0) || null;
    const normalizedVersionId = Number(versionId || 0) || null;
    if (!normalizedCourseId || !normalizedVersionId) return;
    if (typeof lmsRequest !== "function") {
      emitToast?.("LMS API не подключен", "error");
      return;
    }

    setLoadingCourseDraft(true);
    try {
      const payload = await lmsRequest(
        `/api/lms/admin/courses/${normalizedCourseId}/history?course_version_id=${normalizedVersionId}`
      );
      if (!payload?.course) {
        throw new Error("Черновая версия курса не найдена");
      }
      setCourseHistoryVersions(Array.isArray(payload?.versions) ? payload.versions : []);
      hydrateBuilderFromCourse(payload.course, { mode: "edit", versionId: normalizedVersionId });
    } catch (error) {
      emitToast?.(`Не удалось открыть черновик курса: ${String(error?.message || "ошибка")}`, "error");
    } finally {
      setLoadingCourseDraft(false);
    }
  }, [emitToast, hydrateBuilderFromCourse, lmsRequest]);

  useEffect(() => {
    const normalizedInitialCourseId = Number(initialCourseId || 0) || null;
    const normalizedInitialDraftVersionId = Number(initialDraftVersionId || 0) || null;
    if (!normalizedInitialCourseId) {
      resetBuilderDraft();
      return;
    }
    if (normalizedInitialDraftVersionId) {
      void loadDraftVersionForEditing(normalizedInitialCourseId, normalizedInitialDraftVersionId);
      return;
    }
    void loadCourseDraft(normalizedInitialCourseId);
  }, [initialCourseId, initialDraftVersionId, loadCourseDraft, loadDraftVersionForEditing, resetBuilderDraft]);

  const handleHistoryVersionChange = (rawVersionId) => {
    const targetCourseId = Number(editingCourseId || 0) || null;
    if (!targetCourseId) return;
    const targetVersionId = Number(rawVersionId || 0) || null;
    if (!targetVersionId) {
      void loadCourseDraft(targetCourseId);
      return;
    }
    void loadHistoryVersion(targetCourseId, targetVersionId);
  };

  const removeSkill = (skillToRemove) => {
    setSettings((prev) => ({ ...prev, skills: (prev.skills || []).filter((skill) => skill !== skillToRemove) }));
  };

  const addSkill = () => {
    const nextSkill = String(newSkill || "").trim();
    if (!nextSkill) return;
    setSettings((prev) => {
      const nextSkills = Array.isArray(prev.skills) ? [...prev.skills] : [];
      if (!nextSkills.includes(nextSkill)) nextSkills.push(nextSkill);
      return { ...prev, skills: nextSkills.slice(0, 30) };
    });
    setNewSkill("");
  };

  const uploadSingleMaterial = useCallback(async (file, materialType = "file", options = {}) => {
    if (typeof lmsRequest !== "function") {
      throw new Error("LMS API не подключен");
    }
    const formData = new FormData();
    formData.append("file", file);
    formData.append("material_type", materialType);
    formData.append("title", file?.name || "Материал");
    const replaceBucket = String(options?.replaceBucket || "").trim();
    const replaceBlobPath = String(options?.replaceBlobPath || "").trim();
    if (replaceBucket && replaceBlobPath) {
      formData.append("replace_bucket", replaceBucket);
      formData.append("replace_blob_path", replaceBlobPath);
    }
    const payload = await lmsRequest("/api/lms/admin/materials/upload", {
      method: "POST",
      body: formData,
    });
    const first = Array.isArray(payload?.uploaded) ? payload.uploaded[0] : null;
    if (!first) throw new Error("Файл не загрузился");
    return first;
  }, [lmsRequest]);

  const handleRichTextImageUpload = useCallback(async (file) => {
    try {
      if (!(file instanceof File)) {
        throw new Error("Файл не выбран");
      }
      if (!String(file.type || "").toLowerCase().startsWith("image/")) {
        throw new Error("Можно загружать только изображения");
      }
      const uploaded = await uploadSingleMaterial(file, "file");
      const imageUrl = String(uploaded?.signed_url || uploaded?.content_url || uploaded?.url || "").trim();
      if (!imageUrl) {
        throw new Error("Ссылка на изображение не получена");
      }
      return imageUrl;
    } catch (error) {
      emitToast?.(`Не удалось загрузить изображение: ${String(error?.message || "ошибка")}`, "error");
      throw error;
    }
  }, [uploadSingleMaterial, emitToast]);

  const handleRichTextFileUpload = useCallback(async (file) => {
    try {
      if (!(file instanceof File)) {
        throw new Error("Файл не выбран");
      }
      const detectedType = String(file.type || "").toLowerCase();
      const materialType = detectedType.includes("pdf") ? "pdf" : "file";
      const uploaded = await uploadSingleMaterial(file, materialType);
      const fileUrl = String(uploaded?.signed_url || uploaded?.content_url || uploaded?.url || "").trim();
      if (!fileUrl) {
        throw new Error("Ссылка на файл не получена");
      }
      return {
        url: fileUrl,
        name: String(uploaded?.file_name || file.name || "Файл"),
        mimeType: String(uploaded?.content_type || file.type || ""),
        size: Number(file.size || 0) || 0,
      };
    } catch (error) {
      emitToast?.(`Не удалось загрузить файл: ${String(error?.message || "ошибка")}`, "error");
      throw error;
    }
  }, [uploadSingleMaterial, emitToast]);

  const readVideoDurationSeconds = useCallback((file) => new Promise((resolve) => {
    try {
      if (!(file instanceof File)) {
        resolve(null);
        return;
      }
      const video = document.createElement("video");
      const objectUrl = URL.createObjectURL(file);
      let settled = false;
      const cleanup = () => {
        video.onloadedmetadata = null;
        video.onerror = null;
        URL.revokeObjectURL(objectUrl);
      };
      video.preload = "metadata";
      video.onloadedmetadata = () => {
        if (settled) return;
        settled = true;
        const duration = Number(video.duration || 0);
        cleanup();
        resolve(Number.isFinite(duration) && duration > 0 ? duration : null);
      };
      video.onerror = () => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve(null);
      };
      video.src = objectUrl;
    } catch (_) {
      resolve(null);
    }
  }), []);

  const convertCoverToWebp = useCallback((file) => new Promise((resolve) => {
    try {
      if (!(file instanceof File) || !String(file.type || "").toLowerCase().startsWith("image/")) {
        resolve(file);
        return;
      }
      const objectUrl = URL.createObjectURL(file);
      const img = new Image();
      img.onload = () => {
        try {
          const sourceWidth = Math.max(1, Number(img.naturalWidth || 0));
          const sourceHeight = Math.max(1, Number(img.naturalHeight || 0));
          const targetRatio = 16 / 9;
          let sx = 0;
          let sy = 0;
          let sw = sourceWidth;
          let sh = sourceHeight;

          if (sourceWidth / sourceHeight > targetRatio) {
            sw = Math.max(1, Math.round(sourceHeight * targetRatio));
            sx = Math.max(0, Math.round((sourceWidth - sw) / 2));
          } else {
            sh = Math.max(1, Math.round(sourceWidth / targetRatio));
            sy = Math.max(0, Math.round((sourceHeight - sh) / 2));
          }

          const outputWidth = Math.max(320, Math.min(1600, sw));
          const outputHeight = Math.max(180, Math.round(outputWidth / targetRatio));
          const canvas = document.createElement("canvas");
          canvas.width = outputWidth;
          canvas.height = outputHeight;
          const ctx = canvas.getContext("2d");
          if (!ctx) {
            URL.revokeObjectURL(objectUrl);
            resolve(file);
            return;
          }
          ctx.fillStyle = "#ffffff";
          ctx.fillRect(0, 0, outputWidth, outputHeight);
          ctx.drawImage(img, sx, sy, sw, sh, 0, 0, outputWidth, outputHeight);
          canvas.toBlob((blob) => {
            URL.revokeObjectURL(objectUrl);
            if (!blob) {
              resolve(file);
              return;
            }
            const base = String(file.name || "cover").replace(/\.[^.]+$/, "") || "cover";
            const converted = new File([blob], `${base}.webp`, {
              type: "image/webp",
              lastModified: Date.now(),
            });
            resolve(converted);
          }, "image/webp", 0.9);
        } catch (_) {
          URL.revokeObjectURL(objectUrl);
          resolve(file);
        }
      };
      img.onerror = () => {
        URL.revokeObjectURL(objectUrl);
        resolve(file);
      };
      img.src = objectUrl;
    } catch (_) {
      resolve(file);
    }
  }), []);

  const handleCoverFileChange = async (event) => {
    const file = event?.target?.files?.[0];
    event.target.value = "";
    if (!file) return;
    setCoverUploading(true);
    try {
      const preparedCover = await convertCoverToWebp(file);
      const uploaded = await uploadSingleMaterial(preparedCover, "cover");
      setSettings((prev) => ({
        ...prev,
        coverUrl: uploaded.signed_url || "",
        coverBucket: uploaded.bucket || "",
        coverBlobPath: uploaded.blob_path || "",
      }));
      emitToast?.("Обложка загружена", "success");
    } catch (error) {
      emitToast?.(`Не удалось загрузить обложку: ${String(error?.message || "ошибка")}`, "error");
    } finally {
      setCoverUploading(false);
    }
  };

  const handleLessonVideoUpload = async (event) => {
    const file = event?.target?.files?.[0];
    event.target.value = "";
    if (!file || !selectedLessonModel) return;
    setLessonUploading(true);
    try {
      const replaceBucket = String(selectedLessonVideoMaterial?.bucket || "").trim();
      const replaceBlobPath = String(selectedLessonVideoMaterial?.blob_path || "").trim();
      const detectedDuration = await readVideoDurationSeconds(file);
      const nextDurationSeconds = detectedDuration != null
        ? Math.max(1, Math.round(detectedDuration))
        : Math.max(1, Number(selectedLessonModel?.durationSeconds || 0) || 15 * 60);
      const uploaded = await uploadSingleMaterial(file, "video", { replaceBucket, replaceBlobPath });
      updateLessonById(selectedLessonModel.id, (prev) => {
        const base = Array.isArray(prev?.materials) ? prev.materials.filter((item) => String(item?.material_type || item?.type || "").toLowerCase() !== "video") : [];
        return {
          ...prev,
          type: "video",
          durationSeconds: nextDurationSeconds,
          materials: [
            ...base,
            {
              title: file.name || "Видео",
              type: "video",
              material_type: "video",
              content_url: uploaded.signed_url || "",
              signed_url: uploaded.signed_url || "",
              mime_type: uploaded.content_type || file.type || "video/mp4",
              bucket: uploaded.bucket || "",
              blob_path: uploaded.blob_path || "",
              metadata: {
                uploaded_file_name: uploaded.file_name || file.name || "video",
                duration_seconds: nextDurationSeconds,
              },
            },
          ],
        };
      });
      emitToast?.("Видео прикреплено", "success");
    } catch (error) {
      emitToast?.(`Не удалось прикрепить видео: ${String(error?.message || "ошибка")}`, "error");
    } finally {
      setLessonUploading(false);
    }
  };

  const handleLessonMaterialUpload = async (event) => {
    const files = Array.from(event?.target?.files || []);
    event.target.value = "";
    if (!files.length || !selectedLessonModel) return;
    setLessonUploading(true);
    try {
      const uploadedItems = [];
      for (const file of files) {
        const materialType = String(file?.type || "").toLowerCase().includes("pdf") ? "pdf" : "file";
        const uploaded = await uploadSingleMaterial(file, materialType);
        uploadedItems.push({
          title: file.name || "Материал",
          type: materialType,
          material_type: materialType,
          content_url: uploaded.signed_url || "",
          signed_url: uploaded.signed_url || "",
          mime_type: uploaded.content_type || file.type || "application/octet-stream",
          bucket: uploaded.bucket || "",
          blob_path: uploaded.blob_path || "",
          metadata: { uploaded_file_name: uploaded.file_name || file.name || "file" },
        });
      }
      updateLessonById(selectedLessonModel.id, (prev) => ({
        ...prev,
        materials: [...(Array.isArray(prev?.materials) ? prev.materials : []), ...uploadedItems],
      }));
      emitToast?.("Материалы прикреплены", "success");
    } catch (error) {
      emitToast?.(`Не удалось прикрепить материалы: ${String(error?.message || "ошибка")}`, "error");
    } finally {
      setLessonUploading(false);
    }
  };

  const handleAddMaterialLink = () => {
    if (!selectedLessonModel) return;
    const link = String(lessonMaterialLink || "").trim();
    if (!link) return;
    updateLessonById(selectedLessonModel.id, (prev) => ({
      ...prev,
      materials: [
        ...(Array.isArray(prev?.materials) ? prev.materials : []),
        {
          title: link,
          type: "link",
          material_type: "link",
          content_url: link,
          signed_url: link,
          mime_type: "text/uri-list",
          metadata: {},
        },
      ],
    }));
    setLessonMaterialLink("");
  };

  const handleRemoveLessonMaterial = (materialIndex) => {
    if (!selectedLessonModel) return;
    updateLessonById(selectedLessonModel.id, (prev) => ({
      ...prev,
      materials: (Array.isArray(prev?.materials) ? prev.materials : []).filter((_, idx) => idx !== materialIndex),
    }));
  };

  const mapQuestionsToPayload = useCallback((questionBank = []) => (
    (Array.isArray(questionBank) ? questionBank : [])
      .map((question, questionIndex) => {
        const typeRaw = String(question?.type || "single").toLowerCase();
        const type = typeRaw === "bool" ? "true_false" : typeRaw;
        const options = Array.isArray(question?.options) ? question.options : [];
        const mappedOptions = options
          .map((optionText, optionIndex) => {
            let isCorrect = false;
            if (type === "multiple") {
              const correctList = Array.isArray(question?.correct) ? question.correct : [];
              isCorrect = correctList.includes(optionIndex);
            } else if (type === "single" || type === "true_false") {
              isCorrect = Number(question?.correct) === optionIndex;
            }
            return {
              text: String(optionText || "").trim(),
              is_correct: isCorrect,
              position: optionIndex + 1,
            };
          })
          .filter((optionItem) => optionItem.text);

        return {
          type,
          prompt: String(question?.text || "").trim(),
          position: questionIndex + 1,
          points: 1,
          required: true,
          options: type === "text" ? [] : mappedOptions,
          correct_text_answers: type === "text"
            ? (Array.isArray(question?.correct_text_answers) ? question.correct_text_answers.filter((item) => String(item || "").trim()) : [])
            : [],
          metadata: String(question?.explanation || "").trim() ? { explanation: String(question.explanation).trim() } : {},
        };
      })
      .filter((questionItem) => questionItem.prompt)
  ), []);

  const handleSave = async () => {
    if (!canUseManagerApi) {
      emitToast?.("Недостаточно прав для создания LMS-курса", "error");
      return;
    }
    if (typeof lmsRequest !== "function") {
      emitToast?.("LMS API не подключен", "error");
      return;
    }
    if (Number(historyViewVersionId || 0) > 0) {
      emitToast?.("Историческая версия открыта только для просмотра. Переключитесь на текущую версию.", "error");
      return;
    }

    const title = String(settings.title || "").trim();
    if (!title) {
      emitToast?.("Укажите название курса", "error");
      return;
    }

    const attemptLimitRaw = settings.maxAttempts === "∞" ? 999 : Number(settings.maxAttempts);
    const attemptLimit = Number.isFinite(attemptLimitRaw) ? Math.max(1, attemptLimitRaw) : 5;

    const moduleTestsPayload = [];
    const invalidQuizLessons = [];
    const modulesPayload = modules
      .map((moduleItem, moduleIndex) => {
        const lessons = (Array.isArray(moduleItem?.lessons) ? moduleItem.lessons : [])
          .map((lessonItem, lessonIndex) => {
            const lessonTitle = String(lessonItem?.title || "").trim();
            if (!lessonTitle) return null;
            const rawType = String(lessonItem?.type || "video").toLowerCase();
            const lessonType = rawType === "text"
              ? "text"
              : rawType === "quiz"
                ? "quiz"
                : rawType === "combined"
                  ? "combined"
                  : "video";
            const description = normalizeRichTextValue(lessonItem?.description || "");
            const contentText = normalizeRichTextValue(lessonItem?.contentText || "");

            if (lessonType === "quiz") {
              const quizQuestions = mapQuestionsToPayload(lessonItem?.quizQuestions);
              if (quizQuestions.length > 0) {
                const maxQuizQuestions = Math.max(1, quizQuestions.length);
                const quizQuestionsPerTest = Math.max(1, Math.min(maxQuizQuestions, Number(lessonItem?.quizQuestionsPerTest || maxQuizQuestions)));
                const defaultQuizMinutes = Math.max(1, Math.ceil(quizQuestionsPerTest * 1.5));
                const quizTimeLimitMinutes = Math.max(1, Number(lessonItem?.quizTimeLimitMinutes || defaultQuizMinutes));
                const quizPassingScore = Math.max(1, Math.min(100, Number(lessonItem?.quizPassingScore || settings.passingScore || 80)));
                const lessonAttemptRaw = Number(lessonItem?.quizAttemptLimit || attemptLimit);
                const quizAttemptLimit = Number.isFinite(lessonAttemptRaw) ? Math.max(1, lessonAttemptRaw) : attemptLimit;
                moduleTestsPayload.push({
                  title: lessonTitle,
                  description: description || "Тест урока",
                  pass_threshold: quizPassingScore,
                  attempt_limit: quizAttemptLimit,
                  is_final: false,
                  module_position: moduleIndex + 1,
                  position: lessonIndex + 1,
                  time_limit_minutes: quizTimeLimitMinutes,
                  question_count: quizQuestionsPerTest,
                  random_order: lessonItem?.quizRandomOrder !== false,
                  show_explanations: lessonItem?.quizShowExplanations !== false,
                  metadata: {
                    source_lesson_type: "quiz_lesson",
                    source_lesson_position: lessonIndex + 1,
                    questions_per_test: quizQuestionsPerTest,
                    random_order: lessonItem?.quizRandomOrder !== false,
                    show_explanations: lessonItem?.quizShowExplanations !== false,
                  },
                  questions: quizQuestions,
                });
              } else {
                invalidQuizLessons.push(lessonTitle);
              }
              return null;
            }

            if (lessonType === "combined") {
              const rawCombinedBlocks = Array.isArray(lessonItem?.combinedBlocks) ? lessonItem.combinedBlocks : [];
              let mappedCombinedBlocks = rawCombinedBlocks
                .filter((blockItem) => {
                  const blockType = String(blockItem?.type || blockItem?.material_type || "").toLowerCase();
                  if (blockType !== "text" && blockType !== "video") return false;
                  if (blockType === "text") return Boolean(String(blockItem?.contentText || blockItem?.content_text || "").trim());
                  return Boolean(String(blockItem?.contentUrl || blockItem?.content_url || blockItem?.signed_url || blockItem?.url || "").trim());
                })
                .map((blockItem, blockIndex) => {
                  const blockType = String(blockItem?.type || blockItem?.material_type || "").toLowerCase() === "video" ? "video" : "text";
                  const blockContentText = normalizeRichTextValue(blockItem?.contentText || blockItem?.content_text || "");
                  const blockContentUrl = String(blockItem?.contentUrl || blockItem?.content_url || blockItem?.signed_url || blockItem?.url || "").trim();
                  return {
                    title: String(blockItem?.title || (blockType === "video" ? `Видео блок ${blockIndex + 1}` : `Текстовый блок ${blockIndex + 1}`)).trim(),
                    material_type: blockType,
                    content_url: blockType === "video" ? (blockContentUrl || null) : null,
                    content_text: blockContentText || null,
                    mime_type: String(blockItem?.mime_type || (blockType === "text" ? "text/html" : "video/mp4")).trim() || null,
                    bucket: String(blockItem?.bucket || "").trim() || null,
                    blob_path: String(blockItem?.blob_path || "").trim() || null,
                    metadata: blockItem?.metadata && typeof blockItem.metadata === "object"
                      ? { ...blockItem.metadata, combined_block: true }
                      : { combined_block: true },
                    position: blockIndex + 1,
                  };
                });

              if (mappedCombinedBlocks.length === 0) {
                const fallbackText = normalizeRichTextValue(lessonItem?.contentText || description || "");
                if (fallbackText) {
                  mappedCombinedBlocks = [{
                    title: "Текстовый блок",
                    material_type: "text",
                    content_url: null,
                    content_text: fallbackText,
                    mime_type: "text/html",
                    bucket: null,
                    blob_path: null,
                    metadata: { combined_block: true },
                    position: 1,
                  }];
                }
              }

              const rawMaterials = Array.isArray(lessonItem?.materials) ? lessonItem.materials : [];
              const mappedExtraMaterials = rawMaterials
                .map((materialItem, materialIndex) => {
                  const materialType = String(materialItem?.material_type || materialItem?.type || "file").toLowerCase();
                  if (materialType === "text" || materialType === "video") return null;
                  const safeType = ["pdf", "link", "file"].includes(materialType) ? materialType : "file";
                  const contentUrl = String(materialItem?.content_url || materialItem?.signed_url || materialItem?.url || "").trim();
                  const contentText = String(materialItem?.content_text || "").trim();
                  if (!contentUrl && !contentText) return null;
                  return {
                    title: String(materialItem?.title || "Материал").trim(),
                    material_type: safeType,
                    content_url: contentUrl || null,
                    content_text: contentText || null,
                    mime_type: String(materialItem?.mime_type || "").trim() || null,
                    bucket: String(materialItem?.bucket || "").trim() || null,
                    blob_path: String(materialItem?.blob_path || "").trim() || null,
                    metadata: materialItem?.metadata && typeof materialItem.metadata === "object" ? materialItem.metadata : {},
                    position: mappedCombinedBlocks.length + materialIndex + 1,
                  };
                })
                .filter(Boolean);

              const hasCombinedQuiz = Boolean(lessonItem?.combinedHasQuiz);
              if (hasCombinedQuiz) {
                const combinedQuizQuestions = mapQuestionsToPayload(lessonItem?.combinedQuizQuestions);
                if (combinedQuizQuestions.length > 0) {
                  const maxCombinedQuestions = Math.max(1, combinedQuizQuestions.length);
                  const combinedQuestionsPerTest = Math.max(1, Math.min(maxCombinedQuestions, Number(lessonItem?.combinedQuizQuestionsPerTest || maxCombinedQuestions)));
                  const defaultCombinedMinutes = Math.max(1, Math.ceil(combinedQuestionsPerTest * 1.5));
                  const combinedTimeLimitMinutes = Math.max(1, Number(lessonItem?.combinedQuizTimeLimitMinutes || defaultCombinedMinutes));
                  const combinedPassingScore = Math.max(1, Math.min(100, Number(lessonItem?.combinedQuizPassingScore || settings.passingScore || 80)));
                  const combinedAttemptRaw = Number(lessonItem?.combinedQuizAttemptLimit || attemptLimit);
                  const combinedAttemptLimit = Number.isFinite(combinedAttemptRaw) ? Math.max(1, combinedAttemptRaw) : attemptLimit;
                  moduleTestsPayload.push({
                    title: String(lessonItem?.combinedQuizTitle || `${lessonTitle} — тест`).trim(),
                    description: `Тест комбинированного урока «${lessonTitle}»`,
                    pass_threshold: combinedPassingScore,
                    attempt_limit: combinedAttemptLimit,
                    is_final: false,
                    module_position: moduleIndex + 1,
                    position: lessonIndex + 1,
                    source_lesson_id: lessonItem.id,
                    time_limit_minutes: combinedTimeLimitMinutes,
                    question_count: combinedQuestionsPerTest,
                    random_order: lessonItem?.combinedQuizRandomOrder !== false,
                    show_explanations: lessonItem?.combinedQuizShowExplanations !== false,
                    metadata: {
                      source_lesson_type: "combined_lesson",
                      source_lesson_position: lessonIndex + 1,
                      questions_per_test: combinedQuestionsPerTest,
                      random_order: lessonItem?.combinedQuizRandomOrder !== false,
                      show_explanations: lessonItem?.combinedQuizShowExplanations !== false,
                    },
                    questions: combinedQuizQuestions,
                  });
                } else {
                  invalidQuizLessons.push(`${lessonTitle} (комбинированный)`);
                }
              }

              return {
                id: lessonItem.id,
                title: lessonTitle,
                description,
                lesson_type: "combined",
                position: lessonIndex + 1,
                duration_seconds: Number(lessonItem?.durationSeconds) || 0,
                allow_fast_forward: false,
                completion_threshold: Number(lessonItem?.completionThreshold) || 95,
                content_text: null,
                blocks: mappedCombinedBlocks.map((blockItem, blockIndex) => ({
                  id: blockIndex + 1,
                  type: blockItem.material_type,
                  title: blockItem.title,
                  content_text: blockItem.content_text,
                  content_url: blockItem.content_url,
                  mime_type: blockItem.mime_type,
                  bucket: blockItem.bucket,
                  blob_path: blockItem.blob_path,
                  metadata: blockItem.metadata,
                  position: blockIndex + 1,
                })),
                materials: [...mappedCombinedBlocks, ...mappedExtraMaterials]
                  .map((materialItem, idx) => ({ ...materialItem, position: idx + 1 })),
              };
            }

            const rawMaterials = Array.isArray(lessonItem?.materials) ? lessonItem.materials : [];
            let mappedMaterials = rawMaterials
              .map((materialItem, materialIndex) => {
                const materialType = String(materialItem?.material_type || materialItem?.type || "file").toLowerCase();
                const safeType = ["video", "pdf", "link", "text", "file"].includes(materialType) ? materialType : "file";
                const contentUrl = String(materialItem?.content_url || materialItem?.signed_url || materialItem?.url || "").trim();
                const materialText = safeType === "text"
                  ? normalizeRichTextValue(materialItem?.content_text || contentText || "")
                  : String(materialItem?.content_text || "").trim();
                if (!contentUrl && !materialText) return null;
                return {
                  title: String(materialItem?.title || (safeType === "video" ? "Видео" : "Материал")).trim(),
                  material_type: safeType,
                  content_url: contentUrl || null,
                  content_text: materialText || null,
                  mime_type: String(materialItem?.mime_type || "").trim() || null,
                  bucket: String(materialItem?.bucket || "").trim() || null,
                  blob_path: String(materialItem?.blob_path || "").trim() || null,
                  metadata: materialItem?.metadata && typeof materialItem.metadata === "object" ? materialItem.metadata : {},
                  position: materialIndex + 1,
                };
              })
              .filter(Boolean);

            if (lessonType === "text" && contentText) {
              mappedMaterials = mappedMaterials.filter((item) => item.material_type !== "text");
              mappedMaterials.unshift({
                title: "Текстовый материал",
                material_type: "text",
                content_url: null,
                content_text: contentText,
                mime_type: "text/html",
                bucket: null,
                blob_path: null,
                metadata: {},
                position: 1,
              });
            }

            if (lessonType === "video") {
              mappedMaterials = mappedMaterials.filter((item) => item.material_type !== "text");
              if (contentText) {
                mappedMaterials.unshift({
                  title: "Транскрипт видео",
                  material_type: "text",
                  content_url: null,
                  content_text: contentText,
                  mime_type: "text/html",
                  bucket: null,
                  blob_path: null,
                  metadata: {},
                  position: 1,
                });
              }
            }

            return {
              title: lessonTitle,
              description,
              lesson_type: lessonType,
              position: lessonIndex + 1,
              duration_seconds: Number(lessonItem?.durationSeconds) || 0,
              allow_fast_forward: false,
              completion_threshold: Number(lessonItem?.completionThreshold) || 95,
              content_text: contentText || null,
              materials: mappedMaterials.map((item, idx) => ({ ...item, position: idx + 1 })),
            };
          })
          .filter(Boolean);

        return {
          title: String(moduleItem?.title || "").trim(),
          position: moduleIndex + 1,
          lessons,
        };
      })
      .filter((moduleItem) => moduleItem.title);

    if (invalidQuizLessons.length > 0) {
      emitToast?.(`Добавьте вопросы в тестовые уроки: ${invalidQuizLessons.slice(0, 3).join(", ")}`, "error");
      return;
    }

    const finalQuestionPayload = mapQuestionsToPayload(questions);
    const finalTestsPayload = finalQuestionPayload.length > 0 ? [{
      title: "Итоговый тест",
      description: "Сгенерировано из конструктора LMS",
      pass_threshold: Number(settings.passingScore || 80),
      attempt_limit: attemptLimit,
      is_final: true,
      time_limit_minutes: Number(settings.finalTestTimeLimitMinutes) || 0,
      question_count: Math.max(1, Math.min(finalQuestionPayload.length, Number(settings.questionsPerTest || finalQuestionPayload.length))),
      random_order: settings.randomOrder !== false,
      show_explanations: settings.showExplanations !== false,
      metadata: {
        questions_per_test: Math.max(1, Math.min(finalQuestionPayload.length, Number(settings.questionsPerTest || finalQuestionPayload.length))),
        random_order: settings.randomOrder !== false,
        show_explanations: settings.showExplanations !== false,
      },
      questions: finalQuestionPayload,
    }] : [];

    const testsPayload = [...moduleTestsPayload, ...finalTestsPayload];

    setSaving(true);
    try {
      const payload = await lmsRequest("/api/lms/admin/courses", {
        method: "POST",
        body: {
          ...(editingCourseId ? { course_id: Number(editingCourseId) } : {}),
          title,
          description: normalizeRichTextValue(settings.description || ""),
          category: String(settings.category || "").trim(),
          pass_threshold: Number(settings.passingScore || 80),
          attempt_limit: attemptLimit,
          duration_minutes: null,
          cover_url: String(settings.coverUrl || "").trim() || null,
          cover_bucket: String(settings.coverBucket || "").trim() || null,
          cover_blob_path: String(settings.coverBlobPath || "").trim() || null,
          skills: Array.isArray(settings.skills) ? settings.skills : [],
          modules: modulesPayload,
          tests: testsPayload,
        },
      });
      const nextCourseId = Number(payload?.course_id || 0) || null;
      const nextVersionId = Number(payload?.course_version_id || 0) || null;
      const nextVersionNumber = Number(payload?.version_number || 0) || null;
      setCreatedCourseId(nextCourseId);
      setAssignmentCourseId(nextCourseId);
      setEditingCourseId(nextCourseId);
      setSavedVersionId(nextVersionId);
      setSavedVersionNumber(nextVersionNumber);
      setHistoryViewVersionId(null);
      setActiveCourseVersionNumber(nextVersionNumber);
      setActiveCourseVersionStatus("draft");
      setSaved(true);
      if (nextCourseId) {
        try {
          const historyPayload = await lmsRequest(`/api/lms/admin/courses/${nextCourseId}/history`);
          setCourseHistoryVersions(Array.isArray(historyPayload?.versions) ? historyPayload.versions : []);
        } catch (_) {
          // ignore history refresh errors after save
        }
      }
      if (editingCourseId) {
        emitToast?.(`Версия сохранена${nextVersionNumber ? ` (v${nextVersionNumber})` : ""}. Опубликуйте, когда будете готовы.`, "success");
      } else {
        emitToast?.("Курс сохранен в LMS", "success");
      }
      if (typeof onAfterSave === "function") {
        await onAfterSave();
      }
      setTimeout(() => setSaved(false), 2000);
    } catch (error) {
      emitToast?.(`Не удалось сохранить курс: ${String(error?.message || "ошибка")}`, "error");
    } finally {
      setSaving(false);
    }
  };

  const handlePublishSavedVersion = async () => {
    if (!canUseManagerApi) {
      emitToast?.("Недостаточно прав для публикации версии", "error");
      return;
    }
    if (typeof lmsRequest !== "function") {
      emitToast?.("LMS API не подключен", "error");
      return;
    }
    if (Number(historyViewVersionId || 0) > 0) {
      emitToast?.("Историческая версия открыта только для просмотра. Переключитесь на текущую версию.", "error");
      return;
    }

    const targetCourseId = Number(editingCourseId || createdCourseId || 0) || null;
    const targetVersionId = Number(savedVersionId || 0) || null;
    if (!targetCourseId) {
      emitToast?.("Сначала выберите или сохраните курс", "error");
      return;
    }
    if (!targetVersionId) {
      emitToast?.("Сначала сохраните изменения, чтобы создать новую версию", "error");
      return;
    }

    setPublishing(true);
    try {
      const payload = await lmsRequest(`/api/lms/admin/courses/${targetCourseId}/publish`, {
        method: "POST",
        body: {
          course_version_id: targetVersionId,
          previous_certificates_action: publishCertificatesAction,
        },
      });
      const deletedCount = Number(payload?.deleted_certificates || 0) || 0;
      const suffix = publishCertificatesAction === "delete" ? `, удалено сертификатов: ${deletedCount}` : "";
      emitToast?.(`Версия опубликована${suffix}`, "success");
      setHistoryViewVersionId(null);
      setActiveCourseVersionStatus("published");
      setSavedVersionId(null);
      setSavedVersionNumber(null);
      if (typeof onAfterSave === "function") {
        await onAfterSave();
      }
      await loadCourseDraft(targetCourseId);
    } catch (error) {
      emitToast?.(`Не удалось опубликовать версию: ${String(error?.message || "ошибка")}`, "error");
    } finally {
      setPublishing(false);
    }
  };

  const tabs = [
    { id: "settings", label: "Настройки", icon: Settings },
    { id: "structure", label: "Структура", icon: Layers },
    { id: "quiz", label: "Итоговый тест", icon: HelpCircle },
    { id: "assignment", label: "Назначение", icon: Users },
  ];

  const questionTypes = [
    { id: "single", label: "Один ответ", icon: RadioIcon, color: "text-indigo-600 bg-indigo-50" },
    { id: "multiple", label: "Несколько", icon: CheckSquare, color: "text-violet-600 bg-violet-50" },
    { id: "bool", label: "Верно/Нет", icon: Check, color: "text-amber-600 bg-amber-50" },
    { id: "text", label: "Текст", icon: Type, color: "text-cyan-600 bg-cyan-50" },
  ];

  const safeLearners = Array.isArray(learners) ? learners : [];
  const safeAdminCourses = Array.isArray(adminCourses) ? adminCourses : [];
  const assignableAdminCourses = safeAdminCourses.filter(isAssignableLmsCourse);
  const isEditingExistingCourse = Number(editingCourseId || 0) > 0;
  const isViewingHistoricalVersion = Number(historyViewVersionId || 0) > 0;
  const pendingVersionId = Number(savedVersionId || 0) || null;
  const historyVersionSelectValue = isViewingHistoricalVersion ? String(historyViewVersionId) : "current";
  const historyStatusLabelById = {
    published: "Опубликована",
    archived: "Архив",
    draft: "Черновик",
  };
  const activeVersionStatusLabel = historyStatusLabelById[String(activeCourseVersionStatus || "").toLowerCase()] || "";
  const historyVersionsWithoutCurrent = (Array.isArray(courseHistoryVersions) ? courseHistoryVersions : [])
    .filter((item) => Number(item?.id || 0) > 0 && !Boolean(item?.is_current));
  const isBuilderLoading = loading && safeLearners.length === 0 && safeAdminCourses.length === 0;
  const handleBuilderCourseChange = (rawCourseId) => {
    const nextCourseId = Number(rawCourseId || 0) || null;
    if (!nextCourseId) {
      resetBuilderDraft();
      return;
    }
    void loadCourseDraft(nextCourseId);
  };
  const filteredLearners = safeLearners.filter((item) => {
    const query = assignmentSearch.trim().toLowerCase();
    if (!query) return true;
    const name = String(item?.name || "").toLowerCase();
    const role = String(item?.role || "").toLowerCase();
    return name.includes(query) || role.includes(query);
  });

  const toggleLearner = (userId) => {
    setSelectedLearnerIds((prev) => (
      prev.includes(userId) ? prev.filter((id) => id !== userId) : [...prev, userId]
    ));
  };

  const toggleSelectAllLearners = (checked) => {
    if (checked) {
      setSelectedLearnerIds(filteredLearners.map((item) => Number(item.id)).filter((id) => Number.isFinite(id)));
    } else {
      setSelectedLearnerIds([]);
    }
  };

  const requestedAssignmentCourseId = Number(assignmentCourseId || createdCourseId || 0) || null;
  const hasRequestedAssignableCourse = assignableAdminCourses.some(
    (courseItem) => Number(courseItem?.id || 0) === requestedAssignmentCourseId
  );
  const effectiveAssignmentCourseId = hasRequestedAssignableCourse
    ? requestedAssignmentCourseId
    : (Number(assignableAdminCourses?.[0]?.id || 0) || null);

  const handleAssignSelected = async () => {
    if (!canUseManagerApi) {
      emitToast?.("Недостаточно прав для назначения курса", "error");
      return;
    }
    if (!effectiveAssignmentCourseId) {
      emitToast?.("Назначение доступно только для опубликованных курсов", "error");
      return;
    }
    if (!selectedLearnerIds.length) {
      emitToast?.("Выберите хотя бы одного сотрудника", "error");
      return;
    }
    if (typeof lmsRequest !== "function") {
      emitToast?.("LMS API не подключен", "error");
      return;
    }

    setAssigning(true);
    try {
      const payload = await lmsRequest(`/api/lms/admin/courses/${effectiveAssignmentCourseId}/assignments`, {
        method: "POST",
        body: {
          user_ids: selectedLearnerIds,
          due_at: assignmentDueAt ? `${assignmentDueAt} 23:59:59` : null,
        },
      });
      const assignedCount = Array.isArray(payload?.assigned) ? payload.assigned.length : 0;
      const skippedCount = Array.isArray(payload?.skipped) ? payload.skipped.length : 0;
      emitToast?.(`Назначение выполнено: ${assignedCount} назначено, ${skippedCount} пропущено`, "success");
      setSelectedLearnerIds([]);
      if (typeof onAfterSave === "function") {
        await onAfterSave();
      }
    } catch (error) {
      emitToast?.(`Не удалось назначить курс: ${String(error?.message || "ошибка")}`, "error");
    } finally {
      setAssigning(false);
    }
  };

  const selectedLessonMaterials = Array.isArray(selectedLessonModel?.materials) ? selectedLessonModel.materials : [];
  const selectedLessonVideoMaterial = selectedLessonMaterials.find((item) => String(item?.material_type || item?.type || "").toLowerCase() === "video");
  const selectedLessonVideoUrl = String(
    selectedLessonVideoMaterial?.url || selectedLessonVideoMaterial?.signed_url || selectedLessonVideoMaterial?.content_url || ""
  ).trim();
  const selectedLessonVideoName = selectedLessonVideoMaterial?.metadata?.uploaded_file_name || selectedLessonVideoMaterial?.title || "Видео";
  const selectedLessonVideoDurationSeconds = Math.max(
    0,
    Number(selectedLessonVideoMaterial?.metadata?.duration_seconds || selectedLessonModel?.durationSeconds || 0)
  );
  const selectedLessonQuizQuestions = Array.isArray(selectedLessonModel?.quizQuestions) ? selectedLessonModel.quizQuestions : [];
  const selectedCombinedQuizQuestions = Array.isArray(selectedLessonModel?.combinedQuizQuestions) ? selectedLessonModel.combinedQuizQuestions : [];
  const selectedLessonExtraMaterials = selectedLessonMaterials
    .map((item, originalIndex) => ({ ...item, _originalIndex: originalIndex }))
    .filter((item) => {
      const materialType = String(item?.material_type || item?.type || "").toLowerCase();
      if (selectedLessonModel?.type === "text") return materialType !== "text";
      if (selectedLessonModel?.type === "video") return materialType !== "video" && materialType !== "text";
      if (selectedLessonModel?.type === "combined") return materialType !== "video" && materialType !== "text";
      if (selectedLessonModel?.type === "quiz") return false;
      return materialType !== "video";
    });
  const selectedLessonTextMaterial = selectedLessonMaterials.find((item) => String(item?.material_type || item?.type || "").toLowerCase() === "text" && item?.content_text);
  const selectedLessonDescriptionRich = normalizeRichTextValue(selectedLessonModel?.description || "");
  const selectedLessonTextContentRich = normalizeRichTextValue(selectedLessonModel?.contentText || selectedLessonTextMaterial?.content_text || selectedLessonModel?.description || "");
  const selectedLessonTranscriptRich = normalizeRichTextValue(selectedLessonModel?.contentText || selectedLessonTextMaterial?.content_text || selectedLessonModel?.description || "");
  const selectedLessonLocation = (() => {
    for (let moduleIndex = 0; moduleIndex < modules.length; moduleIndex += 1) {
      const moduleLessons = Array.isArray(modules[moduleIndex]?.lessons) ? modules[moduleIndex].lessons : [];
      for (let lessonIndex = 0; lessonIndex < moduleLessons.length; lessonIndex += 1) {
        if (moduleLessons[lessonIndex]?.id === selectedLessonId) {
          return { moduleNumber: moduleIndex + 1, lessonNumber: lessonIndex + 1 };
        }
      }
    }
    return { moduleNumber: 1, lessonNumber: 1 };
  })();
  const selectedCombinedBlocks = (Array.isArray(selectedLessonModel?.combinedBlocks) ? selectedLessonModel.combinedBlocks : [])
    .slice()
    .sort((left, right) => Number(left?.position || 0) - Number(right?.position || 0));
  const selectedRemovedCombinedBlocks = (Array.isArray(selectedLessonModel?.removedCombinedBlocks) ? selectedLessonModel.removedCombinedBlocks : [])
    .slice()
    .sort((left, right) => Number(left?.removedAt || 0) - Number(right?.removedAt || 0));

  const addCombinedBlock = useCallback((blockType = "text") => {
    if (!selectedLessonModel?.id) return;
    const normalizedType = String(blockType || "").toLowerCase() === "video" ? "video" : "text";
    updateLessonById(selectedLessonModel.id, (prev) => {
      const blocks = Array.isArray(prev?.combinedBlocks) ? prev.combinedBlocks : [];
      const nextPosition = blocks.length + 1;
      return {
        ...prev,
        combinedBlocks: [
          ...blocks,
          {
            id: Date.now() + Math.floor(Math.random() * 1000),
            type: normalizedType,
            title: normalizedType === "video" ? `Видео блок ${nextPosition}` : `Текстовый блок ${nextPosition}`,
            contentText: "",
            contentUrl: "",
            signed_url: "",
            mime_type: normalizedType === "video" ? "video/mp4" : "text/html",
            bucket: "",
            blob_path: "",
            metadata: {},
            position: nextPosition,
          },
        ],
      };
    });
  }, [selectedLessonModel?.id, updateLessonById]);

  const updateCombinedBlock = useCallback((blockId, updater) => {
    if (!selectedLessonModel?.id || !blockId) return;
    updateLessonById(selectedLessonModel.id, (prev) => ({
      ...prev,
      combinedBlocks: (Array.isArray(prev?.combinedBlocks) ? prev.combinedBlocks : []).map((blockItem) => {
        if (String(blockItem?.id) !== String(blockId)) return blockItem;
        return typeof updater === "function" ? updater(blockItem) : { ...blockItem, ...(updater || {}) };
      }),
    }));
  }, [selectedLessonModel?.id, updateLessonById]);

  const removeCombinedBlock = useCallback((blockId) => {
    if (!selectedLessonModel?.id || !blockId) return;
    updateLessonById(selectedLessonModel.id, (prev) => {
      const blocks = Array.isArray(prev?.combinedBlocks) ? prev.combinedBlocks : [];
      const removed = blocks.find((blockItem) => String(blockItem?.id) === String(blockId));
      if (!removed) return prev;
      const nextBlocks = blocks
        .filter((blockItem) => String(blockItem?.id) !== String(blockId))
        .map((blockItem, index) => ({ ...blockItem, position: index + 1 }));
      return {
        ...prev,
        combinedBlocks: nextBlocks,
        removedCombinedBlocks: [
          ...(Array.isArray(prev?.removedCombinedBlocks) ? prev.removedCombinedBlocks : []),
          { ...removed, removedAt: Date.now() },
        ],
      };
    });
  }, [selectedLessonModel?.id, updateLessonById]);

  const restoreCombinedBlock = useCallback((blockId) => {
    if (!selectedLessonModel?.id || !blockId) return;
    updateLessonById(selectedLessonModel.id, (prev) => {
      const removed = Array.isArray(prev?.removedCombinedBlocks) ? prev.removedCombinedBlocks : [];
      const restoring = removed.find((blockItem) => String(blockItem?.id) === String(blockId));
      if (!restoring) return prev;
      const nextBlocks = [...(Array.isArray(prev?.combinedBlocks) ? prev.combinedBlocks : []), { ...restoring, removedAt: undefined }];
      return {
        ...prev,
        combinedBlocks: nextBlocks.map((blockItem, index) => ({ ...blockItem, position: index + 1 })),
        removedCombinedBlocks: removed.filter((blockItem) => String(blockItem?.id) !== String(blockId)),
      };
    });
  }, [selectedLessonModel?.id, updateLessonById]);

  const moveCombinedBlock = useCallback((sourceId, targetId) => {
    if (!selectedLessonModel?.id || !sourceId || !targetId || String(sourceId) === String(targetId)) return;
    updateLessonById(selectedLessonModel.id, (prev) => {
      const blocks = Array.isArray(prev?.combinedBlocks) ? prev.combinedBlocks : [];
      const sourceIndex = blocks.findIndex((blockItem) => String(blockItem?.id) === String(sourceId));
      const targetIndex = blocks.findIndex((blockItem) => String(blockItem?.id) === String(targetId));
      if (sourceIndex < 0 || targetIndex < 0) return prev;
      const nextBlocks = [...blocks];
      const [moved] = nextBlocks.splice(sourceIndex, 1);
      nextBlocks.splice(targetIndex, 0, moved);
      return {
        ...prev,
        combinedBlocks: nextBlocks.map((blockItem, index) => ({ ...blockItem, position: index + 1 })),
      };
    });
  }, [selectedLessonModel?.id, updateLessonById]);

  const handleCombinedBlockVideoUpload = async (blockId, event) => {
    const file = event?.target?.files?.[0];
    event.target.value = "";
    if (!file || !selectedLessonModel?.id || !blockId) return;
    setLessonUploading(true);
    try {
      const uploaded = await uploadSingleMaterial(file, "video");
      const detectedDuration = await readVideoDurationSeconds(file);
      updateCombinedBlock(blockId, (prev) => ({
        ...prev,
        type: "video",
        title: String(prev?.title || file.name || "Видео блок").trim() || "Видео блок",
        contentUrl: uploaded.signed_url || "",
        signed_url: uploaded.signed_url || "",
        mime_type: uploaded.content_type || file.type || "video/mp4",
        bucket: uploaded.bucket || "",
        blob_path: uploaded.blob_path || "",
        metadata: {
          ...(prev?.metadata && typeof prev.metadata === "object" ? prev.metadata : {}),
          uploaded_file_name: uploaded.file_name || file.name || "video",
          duration_seconds: detectedDuration != null ? Math.max(1, Math.round(detectedDuration)) : undefined,
        },
      }));
      emitToast?.("Видео блок обновлен", "success");
    } catch (error) {
      emitToast?.(`Не удалось загрузить видео в блок: ${String(error?.message || "ошибка")}`, "error");
    } finally {
      setLessonUploading(false);
    }
  };

  const applyLessonType = useCallback((lessonId, nextType) => {
    if (!lessonId) return;
    const normalizedType = String(nextType || "video").toLowerCase();
    const maxAttemptsRaw = settings.maxAttempts === "∞" ? 999 : Number(settings.maxAttempts);
    const fallbackAttemptLimit = Number.isFinite(maxAttemptsRaw) ? Math.max(1, maxAttemptsRaw) : 3;
    updateLessonById(lessonId, (prev) => {
      if (normalizedType === "quiz") {
        const currentQuestions = Array.isArray(prev?.quizQuestions) ? prev.quizQuestions : [];
        const defaultMinutes = prev?.quizTimeLimitMinutes || "";
        return {
          ...prev,
          type: "quiz",
          completionThreshold: 100,
          durationSeconds: defaultMinutes ? Number(defaultMinutes) * 60 : "",
          quizQuestionsPerTest: prev?.quizQuestionsPerTest || 5,
          quizTimeLimitMinutes: defaultMinutes,
          quizPassingScore: prev?.quizPassingScore || settings.passingScore || 80,
          quizAttemptLimit: prev?.quizAttemptLimit || fallbackAttemptLimit,
          quizRandomOrder: prev?.quizRandomOrder !== false,
          quizShowExplanations: prev?.quizShowExplanations !== false,
          quizQuestions: currentQuestions.length > 0 ? currentQuestions : [createQuestionTemplate("single")],
          materials: [],
        };
      }
      if (normalizedType === "text") {
        return { ...prev, type: "text", completionThreshold: 100, durationSeconds: prev?.durationSeconds || "" };
      }
      if (normalizedType === "combined") {
        const existingBlocks = Array.isArray(prev?.combinedBlocks) ? prev.combinedBlocks : [];
        const fallbackBlocks = existingBlocks.length > 0 ? existingBlocks : [{
          id: Date.now() + Math.floor(Math.random() * 1000),
          type: "text",
          title: "Текстовый блок 1",
          contentText: "",
          contentUrl: "",
          position: 1,
        }];
        return {
          ...prev,
          type: "combined",
          completionThreshold: Number(prev?.completionThreshold || 95) || 95,
          allowFastForward: false,
          durationSeconds: prev?.durationSeconds || "",
          contentText: "",
          combinedBlocks: fallbackBlocks,
          removedCombinedBlocks: Array.isArray(prev?.removedCombinedBlocks) ? prev.removedCombinedBlocks : [],
          combinedHasQuiz: Boolean(prev?.combinedHasQuiz),
          combinedQuizQuestionsPerTest: prev?.combinedQuizQuestionsPerTest || 5,
          combinedQuizTimeLimitMinutes: prev?.combinedQuizTimeLimitMinutes || "",
          combinedQuizPassingScore: prev?.combinedQuizPassingScore || settings.passingScore || 80,
          combinedQuizAttemptLimit: prev?.combinedQuizAttemptLimit || fallbackAttemptLimit,
          combinedQuizRandomOrder: prev?.combinedQuizRandomOrder !== false,
          combinedQuizShowExplanations: prev?.combinedQuizShowExplanations !== false,
          combinedQuizQuestions: Array.isArray(prev?.combinedQuizQuestions) ? prev.combinedQuizQuestions : [],
        };
      }
      return { ...prev, type: "video", durationSeconds: prev?.durationSeconds || "" };
    });
  }, [updateLessonById, settings.maxAttempts, settings.passingScore, createQuestionTemplate]);

  const updateSelectedLessonQuizQuestion = (questionId, updater) => {
    if (!selectedLessonModel?.id || !questionId) return;
    updateLessonById(selectedLessonModel.id, (prevLesson) => ({
      ...prevLesson,
      quizQuestions: (Array.isArray(prevLesson?.quizQuestions) ? prevLesson.quizQuestions : []).map((question) => {
        if (question?.id !== questionId) return question;
        return typeof updater === "function" ? updater(question) : { ...question, ...(updater || {}) };
      }),
    }));
  };

  const updateSelectedCombinedQuizQuestion = (questionId, updater) => {
    if (!selectedLessonModel?.id || !questionId) return;
    updateLessonById(selectedLessonModel.id, (prevLesson) => ({
      ...prevLesson,
      combinedQuizQuestions: (Array.isArray(prevLesson?.combinedQuizQuestions) ? prevLesson.combinedQuizQuestions : []).map((question) => {
        if (question?.id !== questionId) return question;
        return typeof updater === "function" ? updater(question) : { ...question, ...(updater || {}) };
      }),
    }));
  };

  const renderOperatorRichField = (fieldId, value, onChange, placeholder, minHeight = 150) => {
    if (operatorEditField === fieldId) {
      return (
        <div className="space-y-2">
          <RichTextEditor
            key={`lesson-${selectedLessonId || "none"}-${fieldId}`}
            value={value || ""}
            onChange={onChange}
            placeholder={placeholder}
            onImageUpload={handleRichTextImageUpload}
            onFileUpload={handleRichTextFileUpload}
            minHeight={minHeight}
          />
          <div className="flex justify-end">
            <button
              type="button"
              onClick={() => setOperatorEditField(null)}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-slate-100 text-slate-700 hover:bg-slate-200 transition-colors"
            >
              Готово
            </button>
          </div>
        </div>
      );
    }

    return (
      <div
        role="button"
        tabIndex={0}
        onClick={() => setOperatorEditField(fieldId)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            setOperatorEditField(fieldId);
          }
        }}
        className="w-full text-left rounded-xl border border-dashed border-slate-300 bg-slate-50 hover:border-indigo-300 hover:bg-indigo-50/40 transition-colors p-3 cursor-text"
      >
        <RichTextContent
          value={value}
          className="text-sm text-slate-700 leading-relaxed"
          emptyState={<div className="text-xs text-slate-400">{placeholder}</div>}
        />
        <p className="mt-2 text-[11px] text-indigo-600">Нажмите, чтобы редактировать</p>
      </div>
    );
  };

  const renderOperatorCourseInterface = () => {
    if (!selectedLessonModel) return null;

    const lessonType = String(selectedLessonModel?.type || "video").toLowerCase();
    const lessonDuration = formatDurationLabel(Number(selectedLessonModel?.durationSeconds || selectedLessonVideoDurationSeconds || 0));
    const courseTitle = String(settings?.title || "").trim() || "Новый курс";
    const quizQuestions = Array.isArray(selectedLessonQuizQuestions) ? selectedLessonQuizQuestions : [];

    const toggleCorrectOption = (question, optionIndex) => {
      if (!question?.id) return;
      updateSelectedLessonQuizQuestion(question.id, (prevQuestion) => {
        if (prevQuestion?.type === "multiple") {
          const prevCorrect = Array.isArray(prevQuestion?.correct) ? prevQuestion.correct : [];
          const alreadyChecked = prevCorrect.includes(optionIndex);
          return {
            ...prevQuestion,
            correct: alreadyChecked ? prevCorrect.filter((item) => item !== optionIndex) : [...prevCorrect, optionIndex],
          };
        }
        return { ...prevQuestion, correct: optionIndex };
      });
    };

    return (
      <div className="rounded-2xl border border-slate-200 overflow-hidden bg-white">
        <div className="grid grid-cols-1 xl:grid-cols-[290px_minmax(0,1fr)] 2xl:grid-cols-[340px_minmax(0,1fr)] min-h-[72vh]">
          <aside className="border-r border-slate-200 bg-white">
            <div className="p-4 border-b border-slate-100">
              {operatorEditField === "course-title" ? (
                <input
                  value={settings.title}
                  onChange={(e) => setSettings((prev) => ({ ...prev, title: e.target.value }))}
                  onBlur={() => setOperatorEditField(null)}
                  autoFocus
                  className="w-full text-sm font-semibold text-slate-900 bg-transparent border-b border-indigo-300 focus:outline-none pb-1"
                />
              ) : (
                <button
                  type="button"
                  onClick={() => setOperatorEditField("course-title")}
                  className="w-full text-left"
                >
                  <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1">Интерфейс оператора</p>
                  <p className="text-sm font-semibold text-slate-900 leading-snug">{courseTitle}</p>
                  <p className="text-[11px] text-indigo-600 mt-1">Нажмите, чтобы изменить название курса</p>
                </button>
              )}
            </div>
            <div className="overflow-y-auto max-h-[62vh] custom-scrollbar">
              {modules.map((moduleItem, moduleIndex) => (
                <div key={moduleItem.id}>
                  <div className="px-4 py-3 bg-slate-50 border-b border-slate-100">
                    <p className="text-xs font-semibold text-slate-600">{moduleIndex + 1}. {moduleItem.title || `Модуль ${moduleIndex + 1}`}</p>
                  </div>
                  {(Array.isArray(moduleItem?.lessons) ? moduleItem.lessons : []).map((lessonItem, lessonIndex) => {
                    const Icon = lessonIcons[lessonItem?.type] || BookOpen;
                    const isActive = lessonItem?.id === selectedLessonId;
                    return (
                      <button
                        key={lessonItem.id}
                        type="button"
                        onClick={() => setSelectedLessonId(lessonItem.id)}
                        className={`w-full flex items-center gap-3 px-4 py-3 border-b border-slate-50 text-left transition-colors ${isActive ? "bg-indigo-50 border-l-2 border-l-indigo-500" : "hover:bg-slate-50"}`}
                      >
                        <div className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 ${isActive ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-500"}`}>
                          <Icon size={12} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className={`text-xs font-medium truncate ${isActive ? "text-indigo-700" : "text-slate-700"}`}>{lessonIndex + 1}. {lessonItem?.title || `Урок ${lessonIndex + 1}`}</p>
                          <p className="text-[10px] text-slate-400 mt-0.5">{formatDurationLabel(Number(lessonItem?.durationSeconds || 0))}</p>
                        </div>
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          </aside>

          <div className="bg-slate-50">
            <div className="sticky top-0 z-10 bg-white border-b border-slate-200 px-5 py-3">
              <p className="text-xs text-slate-400">Модуль {selectedLessonLocation.moduleNumber} · Урок {selectedLessonLocation.lessonNumber}</p>
              {operatorEditField === "lesson-title" ? (
                <input
                  value={selectedLessonModel.title || ""}
                  onChange={(e) => updateLessonById(selectedLessonModel.id, { title: e.target.value })}
                  onBlur={() => setOperatorEditField(null)}
                  autoFocus
                  className="w-full mt-1 text-sm font-semibold text-slate-900 bg-transparent border-b border-indigo-300 focus:outline-none pb-1"
                />
              ) : (
                <button type="button" onClick={() => setOperatorEditField("lesson-title")} className="w-full text-left">
                  <p className="text-sm font-semibold text-slate-900 mt-1">{selectedLessonModel.title || "Без названия"}</p>
                  <p className="text-[11px] text-indigo-600 mt-1">Нажмите, чтобы изменить заголовок урока</p>
                </button>
              )}
            </div>

            <div className="p-5 space-y-4">
              <div className="bg-white rounded-2xl border border-slate-200 p-4">
                <div className="flex items-center justify-between gap-3 mb-2">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Описание урока</p>
                  <span className="text-[11px] text-slate-400">{lessonDuration}</span>
                </div>
                {renderOperatorRichField(
                  "lesson-description",
                  selectedLessonDescriptionRich,
                  (nextValue) => updateLessonById(selectedLessonModel.id, { description: nextValue }),
                  "Описание урока пока не заполнено",
                  120
                )}
              </div>

              {lessonType === "text" && (
                <div className="bg-white rounded-2xl border border-slate-200 p-4">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">Материал урока</p>
                  {renderOperatorRichField(
                    "lesson-text-content",
                    selectedLessonTextContentRich,
                    (nextValue) => updateLessonById(selectedLessonModel.id, { contentText: nextValue }),
                    "Текст урока пока не заполнен",
                    220
                  )}
                </div>
              )}

              {lessonType === "video" && (
                <>
                  <div className="bg-white rounded-2xl border border-slate-200 p-4">
                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">Видео</p>
                    {selectedLessonVideoUrl ? (
                      <video
                        key={selectedLessonVideoUrl}
                        src={selectedLessonVideoUrl}
                        controls
                        preload="metadata"
                        playsInline
                        {...LMS_PROTECTED_VIDEO_PROPS}
                        className="w-full max-h-80 bg-black rounded-lg"
                      />
                    ) : (
                      <div className="rounded-xl bg-slate-100 border border-slate-200 text-xs text-slate-500 text-center py-10 px-4">
                        Видео ещё не загружено. Загрузите файл в режиме «Редактор».
                      </div>
                    )}
                  </div>

                  <div className="bg-white rounded-2xl border border-slate-200 p-4">
                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">Транскрипт</p>
                    {renderOperatorRichField(
                      "lesson-video-transcript",
                      selectedLessonTranscriptRich,
                      (nextValue) => updateLessonById(selectedLessonModel.id, { contentText: nextValue }),
                      "Транскрипт пока не заполнен",
                      180
                    )}
                  </div>
                </>
              )}

              {lessonType === "quiz" && (
                <div className="bg-white rounded-2xl border border-slate-200 p-4 space-y-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Параметры теста</p>
                  <div className="grid grid-cols-2 gap-2">
                    {[
                      { id: "quiz-questions-per-test", label: "Вопросов в попытке", value: selectedLessonModel.quizQuestionsPerTest || "", onSave: (next) => updateLessonById(selectedLessonModel.id, { quizQuestionsPerTest: next === "" ? "" : Math.max(1, Number(next)) }) },
                      { id: "quiz-time-limit", label: "Лимит времени (мин)", value: selectedLessonModel.quizTimeLimitMinutes !== undefined ? selectedLessonModel.quizTimeLimitMinutes : "", onSave: (next) => updateLessonById(selectedLessonModel.id, { quizTimeLimitMinutes: next === "" ? "" : Math.max(1, Number(next)), durationSeconds: next === "" ? "" : Math.max(1, Number(next)) * 60 }) },
                      { id: "quiz-passing-score", label: "Проходной балл (%)", value: selectedLessonModel.quizPassingScore || "", onSave: (next) => updateLessonById(selectedLessonModel.id, { quizPassingScore: next === "" ? "" : Math.max(1, Math.min(100, Number(next))) }) },
                      { id: "quiz-attempt-limit", label: "Попыток", value: selectedLessonModel.quizAttemptLimit || "", onSave: (next) => updateLessonById(selectedLessonModel.id, { quizAttemptLimit: next === "" ? "" : Math.max(1, Number(next)) }) },
                    ].map((item) => (
                      <div key={item.id} className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
                        <p className="text-[10px] uppercase tracking-wide text-slate-400">{item.label}</p>
                        {operatorEditField === item.id ? (
                          <input
                            type="number"
                            min={0}
                            value={item.value !== null ? item.value : ""}
                            onChange={(e) => item.onSave(e.target.value)}
                            onBlur={() => setOperatorEditField(null)}
                            autoFocus
                            className="w-full mt-1 text-xs font-semibold text-slate-800 bg-transparent border-b border-indigo-300 focus:outline-none"
                          />
                        ) : (
                          <button type="button" onClick={() => setOperatorEditField(item.id)} className="text-left text-xs font-semibold text-slate-800 mt-1">
                            {item.value || "—"}
                          </button>
                        )}
                      </div>
                    ))}
                  </div>

                  <div className="space-y-2 pt-1">
                    {quizQuestions.length === 0 && (
                      <div className="text-xs text-slate-400 border border-dashed border-slate-300 rounded-xl p-3">
                        Добавьте вопросы в режиме «Редактор», затем редактируйте их кликом здесь.
                      </div>
                    )}
                    {quizQuestions.map((question, questionIndex) => {
                      const questionType = String(question?.type || "single").toLowerCase();
                      const questionOptions = Array.isArray(question?.options) ? question.options : [];
                      const questionCorrect = question?.correct;
                      return (
                        <div key={question.id} className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                          <div className="flex items-start gap-2">
                            <span className="w-5 h-5 rounded-md bg-indigo-100 text-indigo-700 text-[11px] font-bold inline-flex items-center justify-center flex-shrink-0 mt-0.5">{questionIndex + 1}</span>
                            {operatorEditField === `quiz-question-${question.id}` ? (
                              <input
                                value={question.text || ""}
                                onChange={(e) => updateSelectedLessonQuizQuestion(question.id, { text: e.target.value })}
                                onBlur={() => setOperatorEditField(null)}
                                autoFocus
                                className="w-full text-xs font-medium text-slate-800 bg-white border border-indigo-300 rounded-lg px-2 py-1.5 focus:outline-none"
                              />
                            ) : (
                              <button type="button" onClick={() => setOperatorEditField(`quiz-question-${question.id}`)} className="text-left text-xs font-medium text-slate-800 leading-relaxed">
                                {question.text || `Вопрос ${questionIndex + 1}`}
                              </button>
                            )}
                          </div>

                          {questionType !== "text" && (
                            <div className="mt-2 space-y-1.5 pl-7">
                              {questionOptions.map((option, optionIndex) => {
                                const isCorrect = questionType === "multiple"
                                  ? (Array.isArray(questionCorrect) && questionCorrect.includes(optionIndex))
                                  : Number(questionCorrect) === optionIndex;
                                return (
                                  <div key={`${question.id}-${optionIndex}`} className="flex items-center gap-2">
                                    <button
                                      type="button"
                                      onClick={() => toggleCorrectOption(question, optionIndex)}
                                      className={`w-4 h-4 rounded border inline-flex items-center justify-center transition-colors ${isCorrect ? "border-emerald-500 bg-emerald-500 text-white" : "border-slate-300 bg-white text-transparent hover:border-emerald-300"}`}
                                      title="Отметить правильный вариант"
                                    >
                                      <Check size={9} />
                                    </button>
                                    {operatorEditField === `quiz-option-${question.id}-${optionIndex}` ? (
                                      <input
                                        value={option || ""}
                                        onChange={(e) => updateSelectedLessonQuizQuestion(question.id, (prevQuestion) => ({
                                          ...prevQuestion,
                                          options: (Array.isArray(prevQuestion?.options) ? prevQuestion.options : []).map((item, idx) => idx === optionIndex ? e.target.value : item),
                                        }))}
                                        onBlur={() => setOperatorEditField(null)}
                                        autoFocus
                                        className="flex-1 text-xs text-slate-700 bg-white border border-indigo-300 rounded-lg px-2 py-1 focus:outline-none"
                                      />
                                    ) : (
                                      <button
                                        type="button"
                                        onClick={() => setOperatorEditField(`quiz-option-${question.id}-${optionIndex}`)}
                                        className="text-left text-xs text-slate-700"
                                      >
                                        {option || `Вариант ${optionIndex + 1}`}
                                      </button>
                                    )}
                                  </div>
                                );
                              })}
                            </div>
                          )}

                          {questionType === "text" && (
                            <div className="mt-2 pl-7">
                              {operatorEditField === `quiz-keywords-${question.id}` ? (
                                <input
                                  value={Array.isArray(question?.correct_text_answers) ? question.correct_text_answers.join(", ") : ""}
                                  onChange={(e) => updateSelectedLessonQuizQuestion(question.id, { correct_text_answers: e.target.value.split(",").map((item) => item.trim()).filter(Boolean) })}
                                  onBlur={() => setOperatorEditField(null)}
                                  autoFocus
                                  className="w-full text-xs text-slate-700 bg-white border border-indigo-300 rounded-lg px-2 py-1.5 focus:outline-none"
                                  placeholder="Ключевые слова через запятую"
                                />
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => setOperatorEditField(`quiz-keywords-${question.id}`)}
                                  className="text-left text-[11px] text-slate-500"
                                >
                                  {Array.isArray(question?.correct_text_answers) && question.correct_text_answers.length > 0
                                    ? `Ключевые слова: ${question.correct_text_answers.join(", ")}`
                                    : "Нажмите, чтобы добавить ключевые слова"}
                                </button>
                              )}
                            </div>
                          )}

                          <div className="mt-2 pl-7">
                            {operatorEditField === `quiz-explanation-${question.id}` ? (
                              <input
                                value={question.explanation || ""}
                                onChange={(e) => updateSelectedLessonQuizQuestion(question.id, { explanation: e.target.value })}
                                onBlur={() => setOperatorEditField(null)}
                                autoFocus
                                className="w-full text-[11px] text-slate-600 bg-white border border-indigo-300 rounded-lg px-2 py-1.5 focus:outline-none"
                                placeholder="Пояснение к ответу"
                              />
                            ) : (
                              <button type="button" onClick={() => setOperatorEditField(`quiz-explanation-${question.id}`)} className="text-left text-[11px] text-slate-500">
                                {question.explanation ? `Пояснение: ${question.explanation}` : "Нажмите, чтобы добавить пояснение"}
                              </button>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {(lessonType === "video" || lessonType === "text") && (
                <div className="bg-white rounded-2xl border border-slate-200 p-4">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">Дополнительные материалы</p>
                  <div className="space-y-2">
                    {selectedLessonExtraMaterials.length === 0 && (
                      <div className="text-xs text-slate-400">Материалы не добавлены</div>
                    )}
                    {selectedLessonExtraMaterials.map((material, index) => {
                      const label = String(material?.metadata?.uploaded_file_name || material?.title || `Материал ${index + 1}`);
                      const fileUrl = String(material?.url || material?.signed_url || material?.content_url || "").trim();
                      return (
                        <a
                          key={`${material?.id || index}-${label}`}
                          href={fileUrl || "#"}
                          rel="noopener noreferrer"
                          download={label}
                          className="lms-file-link"
                        >
                          <span className="lms-file-icon" aria-hidden="true" />
                          <span className="lms-file-content">
                            <span className="lms-file-title">{label}</span>
                            <span className="lms-file-subtitle">Нажмите, чтобы скачать</span>
                          </span>
                          <span className="lms-file-download">Скачать</span>
                        </a>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  };

  if (isBuilderLoading || loadingCourseDraft) {
    return (
      <div className="lms-shell py-8">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Конструктор курса</h1>
            <p className="text-sm text-slate-500 mt-0.5">Создание и редактирование учебных материалов</p>
          </div>
          <SkeletonBlock className="w-36 h-10" />
        </div>

        <div className="flex items-center gap-1 mb-8 bg-slate-100 p-1 rounded-xl w-fit">
          {Array.from({ length: 4 }).map((_, idx) => (
            <SkeletonBlock key={`builder-tab-skeleton-${idx}`} className="w-28 h-9 rounded-lg" />
          ))}
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 2xl:grid-cols-[1.2fr_1fr] gap-6">
          {Array.from({ length: 2 }).map((_, idx) => (
            <div key={`builder-card-skeleton-${idx}`} className="bg-white rounded-2xl border border-slate-200 p-6 space-y-4">
              <SkeletonBlock className="w-40 h-5" />
              <SkeletonBlock className="w-full h-12" />
              <SkeletonBlock className="w-full h-12" />
              <SkeletonBlock className="w-8/12 h-12" />
              <SkeletonBlock className="w-full h-28" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="lms-shell py-8">
      <div className="flex flex-col gap-4 mb-8">
        <div className="flex flex-col xl:flex-row xl:items-start xl:justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Конструктор курса</h1>
            <p className="text-sm text-slate-500 mt-0.5">Создание, редактирование и выпуск версий LMS-курсов</p>
            {isViewingHistoricalVersion ? (
              <p className="text-xs text-amber-700 mt-1">
                Просмотр исторической версии {activeCourseVersionNumber ? `v${activeCourseVersionNumber}` : ""}
                {activeVersionStatusLabel ? ` (${activeVersionStatusLabel.toLowerCase()})` : ""}. Общий доступ к ней отключен.
              </p>
            ) : isEditingExistingCourse ? (
              <p className="text-xs text-indigo-600 mt-1">
                Редактирование курса ID {editingCourseId}
                {activeCourseVersionNumber ? ` (текущая версия v${activeCourseVersionNumber})` : ""}
              </p>
            ) : (
              <p className="text-xs text-slate-400 mt-1">Режим создания нового курса</p>
            )}
          </div>

          <div className="flex flex-col sm:flex-row gap-2 sm:items-center">
            <button
              onClick={handleSave}
              disabled={saving || loadingCourseDraft || isViewingHistoricalVersion}
              className={`flex items-center gap-2 px-5 py-2.5 rounded-xl font-semibold text-sm transition-all disabled:opacity-60 disabled:cursor-not-allowed ${saved ? "bg-emerald-600 text-white" : "bg-indigo-600 hover:bg-indigo-700 text-white shadow-sm shadow-indigo-200"}`}
            >
              {saving
                ? <><RefreshCw size={15} className="animate-spin" /> Сохранение...</>
                : saved
                  ? <><CheckCircle size={15} /> Сохранено</>
                  : <><Save size={15} /> Сохранить версию</>}
            </button>
            <button
              onClick={handlePublishSavedVersion}
              disabled={publishing || !pendingVersionId || loadingCourseDraft || isViewingHistoricalVersion}
              className="flex items-center gap-2 px-5 py-2.5 rounded-xl font-semibold text-sm transition-all bg-emerald-600 hover:bg-emerald-700 text-white disabled:opacity-60 disabled:cursor-not-allowed"
            >
              {publishing ? <><RefreshCw size={15} className="animate-spin" /> Публикация...</> : <><Rocket size={15} /> Опубликовать версию</>}
            </button>
          </div>
        </div>

        <div className="bg-white rounded-2xl border border-slate-200 p-4">
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
            <div>
              <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Курс для конструктора</label>
              <select
                value={editingCourseId || ""}
                onChange={(event) => handleBuilderCourseChange(event.target.value)}
                disabled={loadingCourseDraft}
                className="w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all disabled:opacity-60"
              >
                <option value="">Новый курс</option>
                {safeAdminCourses.map((courseItem) => (
                  <option key={courseItem.id} value={courseItem.id}>{courseItem.title}</option>
                ))}
              </select>
              <p className="text-[11px] text-slate-400 mt-1">
                {loadingCourseDraft
                  ? "Загружаем структуру курса..."
                  : (isEditingExistingCourse ? "Изменения сохраняются как новая черновая версия." : "После сохранения курс получит первую версию (v1).")}
              </p>
            </div>

            <div>
              <label className="text-xs font-semibold text-slate-600 mb-1.5 block">История версий курса</label>
              <select
                value={historyVersionSelectValue}
                onChange={(event) => handleHistoryVersionChange(event.target.value)}
                disabled={!isEditingExistingCourse || loadingCourseDraft}
                className="w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all disabled:opacity-60"
              >
                <option value="current">Текущая версия (редактирование)</option>
                {historyVersionsWithoutCurrent.map((versionItem) => {
                  const statusKey = String(versionItem?.status || "").toLowerCase();
                  const statusLabel = historyStatusLabelById[statusKey] || statusKey || "версия";
                  const versionNumber = Number(versionItem?.version_number || 0) || 0;
                  return (
                    <option key={versionItem.id} value={versionItem.id}>
                      {`v${versionNumber || "?"} • ${statusLabel}`}
                    </option>
                  );
                })}
              </select>
              <p className="text-[11px] text-slate-400 mt-1">
                {!isEditingExistingCourse
                  ? "Выберите курс, чтобы запросить историю версий."
                  : (historyVersionsWithoutCurrent.length > 0
                    ? "Старые версии доступны только через историю и не попадают в общий доступ."
                    : "Для курса пока нет опубликованных исторических версий.")}
              </p>
            </div>

            <div>
              <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Ранее выданные сертификаты при публикации</label>
              <select
                value={publishCertificatesAction}
                onChange={(event) => setPublishCertificatesAction(event.target.value === "delete" ? "delete" : "keep")}
                disabled={isViewingHistoricalVersion}
                className="w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all disabled:opacity-60"
              >
                <option value="keep">Сохранить сертификаты</option>
                <option value="delete">Удалить сертификаты</option>
              </select>
              <p className="text-[11px] text-slate-400 mt-1">
                {isViewingHistoricalVersion
                  ? "Историческая версия открыта только для просмотра."
                  : pendingVersionId
                  ? `Готова к публикации версия ${savedVersionNumber ? `v${savedVersionNumber}` : `#${pendingVersionId}`}.`
                  : "Сначала сохраните изменения, затем публикуйте."}
              </p>
            </div>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-1 mb-8 bg-slate-100 p-1 rounded-xl w-fit">
        {tabs.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all ${tab === t.id ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}>
            <t.icon size={14} /> {t.label}
          </button>
        ))}
      </div>

      {/* SETTINGS TAB */}
      {tab === "settings" && (
        <div className="grid grid-cols-1 xl:grid-cols-2 2xl:grid-cols-[1.2fr_1fr] gap-6">
          <div className="space-y-5">
            <div className="bg-white rounded-2xl border border-slate-200 p-6">
              <h3 className="text-sm font-semibold text-slate-900 mb-5">Основная информация</h3>
              <div className="space-y-4">
                <div>
                  <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Название курса</label>
                  <input value={settings.title} onChange={e => setSettings(p => ({ ...p, title: e.target.value }))} placeholder="Введите название..." className="w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 transition-all" />
                </div>
                <div>
                  <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Описание</label>
                  <RichTextEditor
                    value={settings.description}
                    onChange={(next) => setSettings((p) => ({ ...p, description: next }))}
                    onImageUpload={handleRichTextImageUpload}
                    onFileUpload={handleRichTextFileUpload}
                    placeholder="Краткое описание курса..."
                    minHeight={140}
                  />
                </div>
                <div className="relative" ref={categoryDropdownRef}>
                  <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Категория</label>
                  <button
                    type="button"
                    onClick={() => setCategoryDropdownOpen((prev) => !prev)}
                    className="w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-sm text-left flex items-center justify-between gap-2 focus:outline-none focus:border-indigo-400 transition-all hover:border-slate-300"
                  >
                    <span className={settings.category ? "text-slate-900" : "text-slate-400"}>
                      {settings.category || "Выберите или создайте категорию..."}
                    </span>
                    <ChevronDown size={14} className={`text-slate-400 transition-transform flex-shrink-0 ${categoryDropdownOpen ? "rotate-180" : ""}`} />
                  </button>
                  {categoryDropdownOpen && (
                    <div className="absolute z-50 left-0 right-0 top-full mt-1.5 bg-white border border-slate-200 rounded-xl shadow-lg overflow-hidden">
                      <div className="max-h-48 overflow-y-auto custom-scrollbar">
                        {customCategories.length === 0 && (
                          <div className="px-4 py-3 text-xs text-slate-400 text-center">Нет категорий — добавьте первую</div>
                        )}
                        {customCategories.map((cat) => (
                          <div key={cat} className="flex items-center gap-1 px-1">
                            <button
                              type="button"
                              onClick={() => { setSettings((p) => ({ ...p, category: cat })); setCategoryDropdownOpen(false); }}
                              className={`flex-1 text-left px-3 py-2.5 text-sm rounded-lg transition-colors ${
                                settings.category === cat
                                  ? "bg-indigo-50 text-indigo-700 font-semibold"
                                  : "text-slate-700 hover:bg-slate-50"
                              }`}
                            >
                              {cat}
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                setCustomCategories((prev) => prev.filter((c) => c !== cat));
                                if (settings.category === cat) setSettings((p) => ({ ...p, category: "" }));
                              }}
                              className="p-1.5 text-slate-300 hover:text-red-500 rounded-lg hover:bg-red-50 transition-colors flex-shrink-0"
                              title="Удалить категорию"
                            >
                              <X size={12} />
                            </button>
                          </div>
                        ))}
                      </div>
                      <div className="border-t border-slate-100 p-2">
                        <div className="flex gap-1.5">
                          <input
                            value={newCategoryInput}
                            onChange={(e) => setNewCategoryInput(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                e.preventDefault();
                                const val = newCategoryInput.trim();
                                if (val && !customCategories.includes(val)) {
                                  setCustomCategories((prev) => [...prev, val]);
                                  setSettings((p) => ({ ...p, category: val }));
                                  setNewCategoryInput("");
                                  setCategoryDropdownOpen(false);
                                }
                              }
                            }}
                            placeholder="Новая категория..."
                            className="flex-1 px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-700 placeholder-slate-400 focus:outline-none focus:border-indigo-400 transition-all"
                          />
                          <button
                            type="button"
                            onClick={() => {
                              const val = newCategoryInput.trim();
                              if (val && !customCategories.includes(val)) {
                                setCustomCategories((prev) => [...prev, val]);
                                setSettings((p) => ({ ...p, category: val }));
                                setNewCategoryInput("");
                                setCategoryDropdownOpen(false);
                              }
                            }}
                            className="px-3 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition-colors text-xs font-semibold flex items-center gap-1"
                          >
                            <Plus size={12} /> Добавить
                          </button>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
                <div>
                  <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Обложка курса</label>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => coverInputRef.current?.click()}
                      disabled={coverUploading}
                      className="px-3 py-2.5 bg-slate-100 hover:bg-slate-200 disabled:opacity-60 disabled:cursor-not-allowed text-slate-700 rounded-xl text-xs font-semibold transition-colors inline-flex items-center gap-2"
                    >
                      {coverUploading ? <RefreshCw size={13} className="animate-spin" /> : <Upload size={13} />}
                      {coverUploading ? "Загрузка..." : "Загрузить файл"}
                    </button>
                    <input
                      value={settings.coverUrl}
                      onChange={(e) => setSettings((prev) => ({ ...prev, coverUrl: e.target.value }))}
                      placeholder="Или вставьте URL обложки"
                      className="flex-1 px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 placeholder-slate-400 focus:outline-none focus:border-indigo-400 transition-all"
                    />
                  </div>
                  <input ref={coverInputRef} type="file" accept="image/*" onChange={handleCoverFileChange} className="hidden" />
                  {settings.coverUrl && (
                    <div className="mt-3 rounded-xl border border-slate-200 overflow-hidden bg-slate-100 aspect-video">
                      <img src={settings.coverUrl} alt="Cover preview" className="w-full h-full object-cover object-center" />
                    </div>
                  )}
                </div>
              </div>
            </div>
            <div className="bg-white rounded-2xl border border-slate-200 p-6">
              <h3 className="text-sm font-semibold text-slate-900 mb-4">Навыки курса</h3>
              <div className="flex flex-wrap gap-2 mb-3">
                {(Array.isArray(settings.skills) ? settings.skills : []).map((skill) => (
                  <span key={skill} className="flex items-center gap-1.5 text-xs bg-indigo-50 text-indigo-700 border border-indigo-100 px-3 py-1.5 rounded-full font-medium">
                    {skill}
                    <button type="button" onClick={() => removeSkill(skill)} className="hover:text-indigo-900"><X size={10} /></button>
                  </span>
                ))}
                {(!Array.isArray(settings.skills) || settings.skills.length === 0) && (
                  <span className="text-xs text-slate-400">Навыки не добавлены</span>
                )}
              </div>
              <div className="flex gap-2">
                <input
                  value={newSkill}
                  onChange={(e) => setNewSkill(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addSkill(); } }}
                  placeholder="Добавить навык..."
                  className="flex-1 px-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm placeholder-slate-400 focus:outline-none focus:border-indigo-400 transition-all"
                />
                <button type="button" onClick={addSkill} className="px-3 py-2.5 bg-indigo-600 text-white rounded-xl hover:bg-indigo-700 transition-colors"><Plus size={16} /></button>
              </div>
            </div>
          </div>

          <div className="space-y-5">
            <div className="bg-white rounded-2xl border border-slate-200 p-6">
              <h3 className="text-sm font-semibold text-slate-900 mb-5">Параметры прохождения</h3>
              <div className="space-y-5">
                <div className="flex items-center justify-between">
                  <div><p className="text-sm font-medium text-slate-800">Обязательный курс</p><p className="text-xs text-slate-400 mt-0.5">Обязателен для всех назначенных</p></div>
                  <button onClick={() => setSettings(p => ({ ...p, mandatory: !p.mandatory }))} className={`w-12 h-6 rounded-full transition-all ${settings.mandatory ? "bg-indigo-600" : "bg-slate-200"} relative`}>
                    <div className={`w-4 h-4 bg-white rounded-full absolute top-1 transition-all ${settings.mandatory ? "left-7" : "left-1"} shadow-sm`} />
                  </button>
                </div>
                <div className="flex items-center justify-between p-3.5 bg-slate-50 border border-slate-200 rounded-xl">
                  <div className="flex items-center gap-2 text-xs text-slate-500">
                    <Clock size={13} className="text-slate-400" />
                    <span>Общая длительность</span>
                  </div>
                  <span className="text-xs font-semibold text-slate-700">Рассчитывается автоматически</span>
                </div>
                <div>
                  <label className="text-xs font-semibold text-slate-600 mb-3 block">Проходной балл: <span className="text-indigo-600">{settings.passingScore}%</span></label>
                  <input type="range" min={50} max={100} value={settings.passingScore} onChange={e => setSettings(p => ({ ...p, passingScore: +e.target.value }))} className="w-full accent-indigo-600" />
                  <div className="flex justify-between text-xs text-slate-400 mt-1"><span>50%</span><span>100%</span></div>
                </div>
                <div>
                  <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Максимум попыток</label>
                  <div className="flex gap-2">
                    {[1, 2, 3, 5, "∞"].map(v => (
                      <button key={v} onClick={() => setSettings(p => ({ ...p, maxAttempts: v }))} className={`flex-1 py-2.5 rounded-xl text-sm font-semibold border-2 transition-all ${settings.maxAttempts === v ? "border-indigo-500 bg-indigo-50 text-indigo-700" : "border-slate-200 text-slate-600 hover:border-slate-300"}`}>{v}</button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* STRUCTURE TAB */}
      {tab === "structure" && (
        <div className="grid grid-cols-1 xl:grid-cols-5 2xl:grid-cols-12 gap-6">
          <div className="xl:col-span-2 2xl:col-span-4 space-y-4">
            {modules.map(mod => (
              <div key={mod.id} className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                <div className="flex items-center gap-3 px-5 py-4 bg-slate-50 border-b border-slate-100">
                  <GripVertical size={16} className="text-slate-300 cursor-grab" />
                  <input value={mod.title} onChange={e => setModules(p => p.map(m => m.id === mod.id ? { ...m, title: e.target.value } : m))} className="text-sm font-semibold text-slate-800 bg-transparent focus:outline-none flex-1" />
                  <div className="flex items-center gap-1">
                    <button onClick={() => addLesson(mod.id)} className="text-xs flex items-center gap-1 text-indigo-600 hover:text-indigo-800 px-2 py-1 rounded-lg hover:bg-indigo-50 transition-colors font-medium"><Plus size={12} /> Урок</button>
                    <button onClick={() => toggleModule(mod.id)} className="p-1.5 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100 transition-colors">{mod.expanded ? <ChevronUp size={15} /> : <ChevronDown size={15} />}</button>
                    <button onClick={() => deleteModule(mod.id)} className="p-1.5 text-slate-300 hover:text-red-500 rounded-lg hover:bg-red-50 transition-colors"><Trash2 size={14} /></button>
                  </div>
                </div>
                {mod.expanded && (
                  <div className="p-4 space-y-2">
                    {mod.lessons.length === 0 && <div className="text-center py-6 text-slate-300 text-xs">Добавьте уроки в этот модуль</div>}
                    {mod.lessons.map(l => {
                      const types = [
                        { id: "video", icon: Video, label: "Видео" },
                        { id: "text", icon: FileText, label: "Текст" },
                        { id: "combined", icon: Layers, label: "Комбо" },
                        { id: "quiz", icon: HelpCircle, label: "Тест" },
                      ];
                      const LIcon = lessonIcons[l.type];
                      return (
                        <div key={l.id} onClick={() => setSelectedLessonId(selectedLessonId === l.id ? null : l.id)} className={`flex items-center gap-3 p-3 rounded-xl border-2 cursor-pointer transition-all ${selectedLessonId === l.id ? "border-indigo-300 bg-indigo-50" : "border-transparent hover:border-slate-200 hover:bg-slate-50"}`}>
                          <GripVertical size={13} className="text-slate-300 cursor-grab flex-shrink-0" />
                          <div className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ${
                            l.type === "video"
                              ? "bg-blue-100 text-blue-600"
                              : l.type === "text"
                                ? "bg-emerald-100 text-emerald-600"
                                : l.type === "combined"
                                  ? "bg-sky-100 text-sky-700"
                                  : "bg-violet-100 text-violet-600"
                          }`}><LIcon size={14} /></div>
                          <input value={l.title} onChange={e => setModules(p => p.map(m => m.id === mod.id ? { ...m, lessons: m.lessons.map(ls => ls.id === l.id ? { ...ls, title: e.target.value } : ls) } : m))} onClick={(e) => e.stopPropagation()} className="flex-1 text-sm font-medium text-slate-800 bg-transparent focus:outline-none" />
                          <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                            {types.map(t => (
                              <button key={t.id} onClick={() => applyLessonType(l.id, t.id)} className={`text-[10px] px-2 py-1 rounded-lg font-semibold transition-colors ${l.type === t.id ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-500 hover:bg-slate-200"}`}>{t.label}</button>
                            ))}
                            <button onClick={() => deleteLesson(mod.id, l.id)} className="p-1.5 text-slate-300 hover:text-red-500 rounded-lg hover:bg-red-50 transition-colors ml-1"><Trash2 size={13} /></button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
            <button onClick={addModule} className="w-full border-2 border-dashed border-slate-200 hover:border-indigo-300 rounded-2xl py-4 text-sm text-slate-500 hover:text-indigo-600 flex items-center justify-center gap-2 transition-all font-medium"><Plus size={16} /> Добавить модуль</button>
          </div>
          <div className="xl:col-span-3 2xl:col-span-8 xl:self-start">
            <div className={`bg-white rounded-2xl border border-slate-200 xl:sticky xl:top-24 xl:max-h-[calc(100vh-7rem)] xl:overflow-y-auto overflow-x-hidden custom-scrollbar ${builderLessonPanelMode === "operator" ? "p-0" : "p-5"}`}>
              {selectedLessonModel ? (
                <>
                  <div className={`${builderLessonPanelMode === "operator" ? "px-4 py-3 border-b border-slate-200 bg-white sticky top-0 z-20" : "mb-4"}`}>
                    <div className="inline-flex items-center gap-1 bg-slate-100 rounded-xl p-1">
                      <button
                        type="button"
                        onClick={() => setBuilderLessonPanelMode("edit")}
                        className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-colors ${builderLessonPanelMode === "edit" ? "bg-white text-slate-900 shadow-sm" : "text-slate-600 hover:text-slate-900"}`}
                      >
                        {"\u0420\u0435\u0434\u0430\u043a\u0442\u043e\u0440"}
                      </button>
                      <button
                        type="button"
                        onClick={() => setBuilderLessonPanelMode("operator")}
                        className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-colors inline-flex items-center gap-1 ${builderLessonPanelMode === "operator" ? "bg-white text-slate-900 shadow-sm" : "text-slate-600 hover:text-slate-900"}`}
                      >
                        <Eye size={12} /> {"\u041a\u0430\u043a \u0443 \u043e\u043f\u0435\u0440\u0430\u0442\u043e\u0440\u0430"}
                      </button>
                    </div>
                  </div>

                  {builderLessonPanelMode === "operator" ? (
                    <div className="p-4 bg-slate-50">
                      <p className="text-xs text-slate-500 mb-3">{"\u041a\u043b\u0438\u043a\u0430\u0439\u0442\u0435 \u043f\u043e \u0437\u0430\u0433\u043e\u043b\u043e\u0432\u043a\u0430\u043c \u0438 \u0442\u0435\u043a\u0441\u0442\u0443, \u0447\u0442\u043e\u0431\u044b \u0440\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u043f\u0440\u044f\u043c\u043e \u0432 \u0438\u043d\u0442\u0435\u0440\u0444\u0435\u0439\u0441\u0435 \u043e\u043f\u0435\u0440\u0430\u0442\u043e\u0440\u0430."}</p>
                      {renderOperatorCourseInterface()}
                    </div>
                  ) : (
                    <>
                      <h3 className="text-sm font-semibold text-slate-900 mb-2">{"\u0420\u0435\u0434\u0430\u043a\u0442\u043e\u0440 \u0443\u0440\u043e\u043a\u0430"}</h3>
                      <div className="space-y-6">
                    <div>
                      <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Описание урока</label>
                      <RichTextEditor
                        key={`lesson-${selectedLessonModel.id}-description`}
                        value={selectedLessonModel.description || ""}
                        onChange={(next) => updateLessonById(selectedLessonModel.id, { description: next })}
                        onImageUpload={handleRichTextImageUpload}
                        onFileUpload={handleRichTextFileUpload}
                        placeholder="Что узнает сотрудник в этом уроке..."
                        minHeight={120}
                      />
                    </div>
                    {selectedLessonModel.type === "quiz" ? (
                      <div className="space-y-4">
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Вопросов в тесте</label>
                            <input
                              type="number"
                              min={1}
                              max={Math.max(1, selectedLessonQuizQuestions.length)}
                              value={selectedLessonModel.quizQuestionsPerTest || ""}
                              onChange={(e) => updateLessonById(selectedLessonModel.id, { quizQuestionsPerTest: e.target.value === "" ? "" : Math.max(1, Number(e.target.value)) })}
                              className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all"
                            />
                          </div>
                          <div>
                            <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Лимит времени (мин)</label>
                            <input
                              type="number"
                              min={1}
                              value={selectedLessonModel.quizTimeLimitMinutes || ""}
                              onChange={(e) => {
                                const nextMinutes = e.target.value === "" ? "" : Math.max(1, Number(e.target.value));
                                updateLessonById(selectedLessonModel.id, { quizTimeLimitMinutes: nextMinutes, durationSeconds: nextMinutes === "" ? "" : nextMinutes * 60 });
                              }}
                              className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all"
                            />
                          </div>
                          <div>
                            <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Проходной балл (%)</label>
                            <input
                              type="number"
                              min={1}
                              max={100}
                              value={selectedLessonModel.quizPassingScore || ""}
                              onChange={(e) => updateLessonById(selectedLessonModel.id, { quizPassingScore: e.target.value === "" ? "" : Math.max(1, Math.min(100, Number(e.target.value))) })}
                              className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all"
                            />
                          </div>
                          <div>
                            <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Максимум попыток</label>
                            <input
                              type="number"
                              min={1}
                              value={selectedLessonModel.quizAttemptLimit || ""}
                              onChange={(e) => updateLessonById(selectedLessonModel.id, { quizAttemptLimit: e.target.value === "" ? "" : Math.max(1, Number(e.target.value)) })}
                              className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all"
                            />
                          </div>
                        </div>

                        <div className="flex items-center gap-5">
                          <label className="flex items-center gap-2 cursor-pointer">
                            <input type="checkbox" className="accent-indigo-600 w-4 h-4" checked={selectedLessonModel.quizRandomOrder !== false} onChange={(e) => updateLessonById(selectedLessonModel.id, { quizRandomOrder: e.target.checked })} />
                            <span className="text-xs text-slate-700">Случайный порядок вопросов</span>
                          </label>
                          <label className="flex items-center gap-2 cursor-pointer">
                            <input type="checkbox" className="accent-indigo-600 w-4 h-4" checked={selectedLessonModel.quizShowExplanations !== false} onChange={(e) => updateLessonById(selectedLessonModel.id, { quizShowExplanations: e.target.checked })} />
                            <span className="text-xs text-slate-700">Показывать пояснения</span>
                          </label>
                        </div>

                        <div className="flex items-center justify-between pt-1">
                          <div>
                            <h4 className="text-sm font-semibold text-slate-900">Банк вопросов урока</h4>
                            <p className="text-xs text-slate-500 mt-0.5">{selectedLessonQuizQuestions.length} вопросов</p>
                          </div>
                          <div className="flex items-center gap-2">
                            {questionTypes.map(t => (
                              <button key={t.id} type="button" onClick={() => addLessonQuizQuestion(selectedLessonModel.id, t.id)} className={`flex items-center gap-1.5 text-xs px-3 py-2 rounded-xl border font-medium transition-colors ${t.color} border-current/20 hover:opacity-80`}>
                                <t.icon size={12} /> {t.label}
                              </button>
                            ))}
                          </div>
                        </div>

                        <div className="space-y-3">
                          {selectedLessonQuizQuestions.length === 0 && (
                            <div className="text-xs text-slate-400">Добавьте вопросы для теста урока</div>
                          )}
                          {selectedLessonQuizQuestions.map((q, qi) => (
                            <div key={q.id} className="bg-slate-50 border border-slate-200 rounded-xl p-3">
                              <div className="flex items-start gap-3 mb-3">
                                <span className="text-[11px] font-bold text-indigo-600 bg-indigo-100 w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0">{qi + 1}</span>
                                <input value={q.text} onChange={e => updateLessonById(selectedLessonModel.id, (prev) => ({ ...prev, quizQuestions: (Array.isArray(prev?.quizQuestions) ? prev.quizQuestions : []).map(x => x.id === q.id ? { ...x, text: e.target.value } : x) }))} placeholder="Текст вопроса..." className="flex-1 text-xs font-medium text-slate-900 bg-transparent focus:outline-none border-b border-transparent focus:border-slate-300 transition-all pb-1" />
                                <div className="flex items-center gap-1">
                                  {questionTypes.map(t => (
                                    <button key={t.id} type="button" onClick={() => updateLessonQuizQuestionType(selectedLessonModel.id, q.id, t.id)} title={t.label} className={`p-1 rounded-lg border transition-all ${q.type === t.id ? `${t.color} border-current/30` : "text-slate-400 border-slate-200 hover:bg-white"}`}>
                                      <t.icon size={12} />
                                    </button>
                                  ))}
                                  <button type="button" onClick={() => updateLessonById(selectedLessonModel.id, (prev) => ({ ...prev, quizQuestions: (Array.isArray(prev?.quizQuestions) ? prev.quizQuestions : []).filter(x => x.id !== q.id) }))} className="p-1 text-slate-300 hover:text-red-500 rounded-lg hover:bg-red-50 transition-colors"><Trash2 size={12} /></button>
                                </div>
                              </div>

                              {q.type !== "text" && (
                                <div className="space-y-1.5 ml-8 mb-2">
                                  {q.options.map((opt, oi) => {
                                    const isCorrect = q.type === "multiple" ? (Array.isArray(q.correct) && q.correct.includes(oi)) : q.correct === oi;
                                    return (
                                      <div key={oi} className="flex items-center gap-2">
                                        <button
                                          type="button"
                                          onClick={() => {
                                            if (q.type === "multiple") {
                                              const prev = Array.isArray(q.correct) ? q.correct : [];
                                              updateLessonById(selectedLessonModel.id, (prevLesson) => ({
                                                ...prevLesson,
                                                quizQuestions: (Array.isArray(prevLesson?.quizQuestions) ? prevLesson.quizQuestions : []).map(x => x.id === q.id ? { ...x, correct: isCorrect ? prev.filter(v => v !== oi) : [...prev, oi] } : x),
                                              }));
                                            } else {
                                              updateLessonById(selectedLessonModel.id, (prevLesson) => ({
                                                ...prevLesson,
                                                quizQuestions: (Array.isArray(prevLesson?.quizQuestions) ? prevLesson.quizQuestions : []).map(x => x.id === q.id ? { ...x, correct: oi } : x),
                                              }));
                                            }
                                          }}
                                          className={`flex-shrink-0 ${q.type === "multiple" ? `w-4 h-4 rounded border-2 flex items-center justify-center ${isCorrect ? "border-emerald-500 bg-emerald-500" : "border-slate-300 hover:border-emerald-400"}` : `w-4 h-4 rounded-full border-2 flex items-center justify-center ${isCorrect ? "border-emerald-500 bg-emerald-500" : "border-slate-300 hover:border-emerald-400"}`}`}
                                        >
                                          {isCorrect && <Check size={9} className="text-white" />}
                                        </button>
                                        <input
                                          value={opt}
                                          onChange={e => updateLessonById(selectedLessonModel.id, (prevLesson) => ({
                                            ...prevLesson,
                                            quizQuestions: (Array.isArray(prevLesson?.quizQuestions) ? prevLesson.quizQuestions : []).map(x => x.id === q.id ? { ...x, options: x.options.map((o, i) => i === oi ? e.target.value : o) } : x),
                                          }))}
                                          readOnly={q.type === "bool"}
                                          placeholder={`Вариант ${oi + 1}`}
                                          className={`flex-1 px-2.5 py-1.5 text-xs rounded-lg border transition-all focus:outline-none ${isCorrect ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-slate-200 bg-white text-slate-700 focus:border-indigo-300"} ${q.type === "bool" ? "cursor-default" : ""}`}
                                        />
                                        {q.type !== "bool" && q.options.length > 2 && (
                                          <button type="button" onClick={() => updateLessonById(selectedLessonModel.id, (prevLesson) => ({ ...prevLesson, quizQuestions: (Array.isArray(prevLesson?.quizQuestions) ? prevLesson.quizQuestions : []).map(x => x.id === q.id ? { ...x, options: x.options.filter((_, i) => i !== oi) } : x) }))} className="p-1 text-slate-300 hover:text-red-400 transition-colors"><X size={11} /></button>
                                        )}
                                      </div>
                                    );
                                  })}
                                  {q.type !== "bool" && q.options.length < 6 && (
                                    <button type="button" onClick={() => updateLessonById(selectedLessonModel.id, (prevLesson) => ({ ...prevLesson, quizQuestions: (Array.isArray(prevLesson?.quizQuestions) ? prevLesson.quizQuestions : []).map(x => x.id === q.id ? { ...x, options: [...x.options, ""] } : x) }))} className="flex items-center gap-1 text-[11px] text-indigo-600 hover:text-indigo-800 transition-colors">
                                      <Plus size={11} /> Добавить вариант
                                    </button>
                                  )}
                                </div>
                              )}

                              {q.type === "text" && (
                                <div className="ml-8 mb-2">
                                  <input
                                    value={Array.isArray(q.correct_text_answers) ? q.correct_text_answers.join(", ") : ""}
                                    onChange={e => updateLessonById(selectedLessonModel.id, (prevLesson) => ({ ...prevLesson, quizQuestions: (Array.isArray(prevLesson?.quizQuestions) ? prevLesson.quizQuestions : []).map(x => x.id === q.id ? { ...x, correct_text_answers: e.target.value.split(",").map(item => item.trim()).filter(Boolean) } : x) }))}
                                    placeholder="Ключевые слова через запятую..."
                                    className="w-full px-2.5 py-2 bg-white border border-slate-200 rounded-lg text-xs text-slate-700 focus:outline-none focus:border-indigo-400 transition-all"
                                  />
                                </div>
                              )}

                              <input value={q.explanation} onChange={e => updateLessonById(selectedLessonModel.id, (prevLesson) => ({ ...prevLesson, quizQuestions: (Array.isArray(prevLesson?.quizQuestions) ? prevLesson.quizQuestions : []).map(x => x.id === q.id ? { ...x, explanation: e.target.value } : x) }))} placeholder="Пояснение к правильному ответу..." className="w-full ml-8 px-2.5 py-1.5 text-[11px] rounded-lg border border-slate-200 bg-white text-slate-600 focus:outline-none focus:border-indigo-400 transition-all" />
                            </div>
                          ))}
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className="grid grid-cols-2 gap-3">
                          <div>
                            <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Длительность (сек)</label>
                            {(selectedLessonModel.type === "text" || selectedLessonModel.type === "combined") ? (
                              <input
                                type="number"
                                min={0}
                                value={selectedLessonModel.durationSeconds !== undefined ? selectedLessonModel.durationSeconds : ""}
                                onChange={(e) => updateLessonById(selectedLessonModel.id, { durationSeconds: e.target.value === "" ? "" : Math.max(0, Number(e.target.value)) })}
                                className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all"
                              />
                            ) : (
                              <div className="px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-xs text-slate-600">
                                {selectedLessonVideoDurationSeconds > 0
                                  ? `Определена автоматически: ${formatDurationLabel(selectedLessonVideoDurationSeconds)}`
                                  : "Будет определена автоматически после загрузки видео"}
                              </div>
                            )}
                          </div>
                          <div>
                            <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Порог завершения (%)</label>
                            <input
                              type="number"
                              min={1}
                              max={100}
                              value={selectedLessonModel.completionThreshold || ""}
                              onChange={(e) => updateLessonById(selectedLessonModel.id, { completionThreshold: e.target.value === "" ? "" : Math.max(1, Math.min(100, Number(e.target.value))) })}
                              className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all"
                            />
                          </div>
                        </div>

                        {selectedLessonModel.type === "combined" && (
                          <div className="space-y-4">
                            <div className="flex items-center justify-between">
                              <div>
                                <h4 className="text-sm font-semibold text-slate-900">Блоки комбинированного урока</h4>
                                <p className="text-xs text-slate-500 mt-0.5">Текст и видео идут в порядке, заданном администратором</p>
                              </div>
                              <div className="hidden">
                                <button
                                  type="button"
                                  onClick={() => addCombinedBlock("text")}
                                  className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl border border-emerald-200 bg-emerald-50 text-emerald-700 text-xs font-semibold hover:bg-emerald-100 transition-colors"
                                >
                                  <FileText size={12} /> Текст
                                </button>
                                <button
                                  type="button"
                                  onClick={() => addCombinedBlock("video")}
                                  className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl border border-blue-200 bg-blue-50 text-blue-700 text-xs font-semibold hover:bg-blue-100 transition-colors"
                                >
                                  <Video size={12} /> Видео
                                </button>
                              </div>
                            </div>

                            <div className="space-y-3">
                              {selectedCombinedBlocks.length === 0 && (
                                <div className="rounded-xl border border-dashed border-slate-300 px-4 py-8 text-center text-xs text-slate-500">
                                  Добавьте блоки текста или видео
                                </div>
                              )}
                              {selectedCombinedBlocks.map((blockItem, blockIndex) => {
                                const blockType = String(blockItem?.type || "text").toLowerCase() === "video" ? "video" : "text";
                                const uploadInputId = `combined-video-upload-${selectedLessonModel.id}-${blockItem.id}`;
                                const blockVideoUrl = String(blockItem?.contentUrl || blockItem?.content_url || blockItem?.signed_url || blockItem?.url || "").trim();
                                return (
                                  <div
                                    key={blockItem.id || `${blockType}-${blockIndex + 1}`}
                                    draggable
                                    onDragStart={() => setDraggedCombinedBlockId(blockItem.id)}
                                    onDragOver={(event) => event.preventDefault()}
                                    onDrop={() => {
                                      moveCombinedBlock(draggedCombinedBlockId, blockItem.id);
                                      setDraggedCombinedBlockId(null);
                                    }}
                                    onDragEnd={() => setDraggedCombinedBlockId(null)}
                                    className={`rounded-xl border bg-white p-3 transition-colors ${draggedCombinedBlockId === blockItem.id ? "border-indigo-300" : "border-slate-200"}`}
                                  >
                                    <div className="flex items-center gap-2 mb-2">
                                      <GripVertical size={14} className="text-slate-400 cursor-grab" />
                                      <span className={`text-[10px] font-semibold px-2 py-1 rounded-full ${blockType === "video" ? "bg-blue-50 text-blue-700" : "bg-emerald-50 text-emerald-700"}`}>
                                        {blockType === "video" ? "Видео" : "Текст"}
                                      </span>
                                      <input
                                        value={blockItem?.title || ""}
                                        onChange={(event) => updateCombinedBlock(blockItem.id, { title: event.target.value })}
                                        placeholder={`Блок ${blockIndex + 1}`}
                                        className="flex-1 px-2 py-1 text-xs rounded-lg border border-slate-200 bg-slate-50 text-slate-700 focus:outline-none focus:border-indigo-400 transition-all"
                                      />
                                      <button
                                        type="button"
                                        onClick={() => removeCombinedBlock(blockItem.id)}
                                        className="p-1.5 text-slate-400 hover:text-red-500 rounded-lg hover:bg-red-50 transition-colors"
                                      >
                                        <Trash2 size={12} />
                                      </button>
                                    </div>

                                    {blockType === "text" ? (
                                      <RichTextEditor
                                        key={`combined-${selectedLessonModel.id}-${blockItem.id}-text`}
                                        value={blockItem?.contentText || blockItem?.content_text || ""}
                                        onChange={(nextValue) => updateCombinedBlock(blockItem.id, { contentText: nextValue })}
                                        onImageUpload={handleRichTextImageUpload}
                                        onFileUpload={handleRichTextFileUpload}
                                        placeholder="Введите текст блока..."
                                        minHeight={140}
                                      />
                                    ) : (
                                      <div className="space-y-2">
                                        <div className="flex gap-2">
                                          <input
                                            value={blockVideoUrl}
                                            onChange={(event) => updateCombinedBlock(blockItem.id, { contentUrl: event.target.value, signed_url: event.target.value })}
                                            placeholder="https://... (ссылка на видео)"
                                            className="flex-1 px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-700 placeholder-slate-400 focus:outline-none focus:border-indigo-400 transition-all"
                                          />
                                          <label
                                            htmlFor={uploadInputId}
                                            className="px-3 py-2 bg-slate-100 hover:bg-slate-200 rounded-lg text-xs font-semibold text-slate-700 transition-colors cursor-pointer inline-flex items-center gap-1.5"
                                          >
                                            {lessonUploading ? <RefreshCw size={12} className="animate-spin" /> : <Upload size={12} />}
                                            Загрузить
                                          </label>
                                          <input
                                            id={uploadInputId}
                                            type="file"
                                            accept="video/*"
                                            className="hidden"
                                            onChange={(event) => { void handleCombinedBlockVideoUpload(blockItem.id, event); }}
                                          />
                                        </div>
                                        {blockVideoUrl ? (
                                          <video
                                            key={`${blockItem.id}-${blockVideoUrl}`}
                                            src={blockVideoUrl}
                                            controls
                                            preload="metadata"
                                            playsInline
                                            {...LMS_PROTECTED_VIDEO_PROPS}
                                            className="w-full max-h-60 rounded-xl bg-slate-950"
                                          />
                                        ) : (
                                          <div className="rounded-xl border border-dashed border-slate-300 px-3 py-6 text-center text-xs text-slate-500">
                                            Видео не прикреплено
                                          </div>
                                        )}
                                        <div>
                                          <label className="text-[11px] font-semibold text-slate-600 mb-1.5 block">Транскрипт видео-блока</label>
                                          <RichTextEditor
                                            key={`combined-${selectedLessonModel.id}-${blockItem.id}-video-transcript`}
                                            value={blockItem?.contentText || blockItem?.content_text || ""}
                                            onChange={(nextValue) => updateCombinedBlock(blockItem.id, { contentText: nextValue })}
                                            onImageUpload={handleRichTextImageUpload}
                                            onFileUpload={handleRichTextFileUpload}
                                            placeholder="Введите транскрипт этого видео..."
                                            minHeight={130}
                                          />
                                        </div>
                                      </div>
                                    )}
                                  </div>
                                );
                              })}
                            </div>

                            <div className="flex items-center justify-end gap-2">
                              <button
                                type="button"
                                onClick={() => addCombinedBlock("text")}
                                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl border border-emerald-200 bg-emerald-50 text-emerald-700 text-xs font-semibold hover:bg-emerald-100 transition-colors"
                              >
                                <FileText size={12} /> {"\u0422\u0435\u043a\u0441\u0442"}
                              </button>
                              <button
                                type="button"
                                onClick={() => addCombinedBlock("video")}
                                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl border border-blue-200 bg-blue-50 text-blue-700 text-xs font-semibold hover:bg-blue-100 transition-colors"
                              >
                                <Video size={12} /> {"\u0412\u0438\u0434\u0435\u043e"}
                              </button>
                            </div>

                            {selectedRemovedCombinedBlocks.length > 0 && (
                              <div className="rounded-xl border border-amber-200 bg-amber-50 p-3">
                                <p className="text-xs font-semibold text-amber-800 mb-2">Удаленные блоки</p>
                                <div className="space-y-1.5">
                                  {selectedRemovedCombinedBlocks.map((blockItem, index) => (
                                    <div key={`removed-block-${blockItem?.id || index}`} className="flex items-center justify-between gap-2 rounded-lg bg-white/80 border border-amber-100 px-2.5 py-2">
                                      <p className="text-xs text-amber-900 truncate">
                                        {String(blockItem?.title || `Блок ${index + 1}`)} · {String(blockItem?.type || "text").toLowerCase() === "video" ? "Видео" : "Текст"}
                                      </p>
                                      <button
                                        type="button"
                                        onClick={() => restoreCombinedBlock(blockItem?.id)}
                                        className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-semibold bg-amber-100 text-amber-800 hover:bg-amber-200 transition-colors"
                                      >
                                        <RotateCcw size={11} /> Вернуть
                                      </button>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}

                            <div className="rounded-xl border border-violet-200 bg-violet-50 p-3 space-y-3">
                              <div className="flex items-center justify-between">
                                <div>
                                  <p className="text-xs font-semibold text-violet-800">Тест в конце комбинированного урока</p>
                                  <p className="text-[11px] text-violet-700 mt-0.5">Тест запускается только после отметки урока как пройденного</p>
                                </div>
                                <button
                                  type="button"
                                  onClick={() => updateLessonById(selectedLessonModel.id, (prev) => {
                                    const enabled = !Boolean(prev?.combinedHasQuiz);
                                    return {
                                      ...prev,
                                      combinedHasQuiz: enabled,
                                      combinedQuizQuestions: enabled
                                        ? (Array.isArray(prev?.combinedQuizQuestions) && prev.combinedQuizQuestions.length > 0
                                          ? prev.combinedQuizQuestions
                                          : (Array.isArray(questions) ? questions.map((questionItem, questionIndex) => ({
                                            ...questionItem,
                                            id: Number(`${selectedLessonModel.id || 0}${questionIndex + 1}${Date.now()}`),
                                          })) : []))
                                        : [],
                                    };
                                  })}
                                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${selectedLessonModel?.combinedHasQuiz ? "bg-violet-600 text-white hover:bg-violet-700" : "bg-white text-violet-700 border border-violet-200 hover:bg-violet-100"}`}
                                >
                                  {selectedLessonModel?.combinedHasQuiz ? "Тест включен" : "Добавить тест"}
                                </button>
                              </div>

                              {selectedLessonModel?.combinedHasQuiz && (
                                <div className="space-y-3">
                                  <div className="grid grid-cols-2 gap-2">
                                    <input
                                      type="number"
                                      min={1}
                                      value={selectedLessonModel?.combinedQuizQuestionsPerTest || ""}
                                      onChange={(event) => updateLessonById(selectedLessonModel.id, { combinedQuizQuestionsPerTest: event.target.value === "" ? "" : Math.max(1, Number(event.target.value)) })}
                                      className="w-full px-2.5 py-2 bg-white border border-violet-200 rounded-lg text-xs text-slate-700 focus:outline-none focus:border-violet-400 transition-all"
                                      placeholder="Вопросов в попытке"
                                    />
                                    <input
                                      type="number"
                                      min={1}
                                      value={selectedLessonModel?.combinedQuizTimeLimitMinutes || ""}
                                      onChange={(event) => updateLessonById(selectedLessonModel.id, { combinedQuizTimeLimitMinutes: event.target.value === "" ? "" : Math.max(1, Number(event.target.value)) })}
                                      className="w-full px-2.5 py-2 bg-white border border-violet-200 rounded-lg text-xs text-slate-700 focus:outline-none focus:border-violet-400 transition-all"
                                      placeholder="Лимит времени (мин)"
                                    />
                                    <input
                                      type="number"
                                      min={1}
                                      max={100}
                                      value={selectedLessonModel?.combinedQuizPassingScore || ""}
                                      onChange={(event) => updateLessonById(selectedLessonModel.id, { combinedQuizPassingScore: event.target.value === "" ? "" : Math.max(1, Math.min(100, Number(event.target.value))) })}
                                      className="w-full px-2.5 py-2 bg-white border border-violet-200 rounded-lg text-xs text-slate-700 focus:outline-none focus:border-violet-400 transition-all"
                                      placeholder="Проходной балл (%)"
                                    />
                                    <input
                                      type="number"
                                      min={1}
                                      value={selectedLessonModel?.combinedQuizAttemptLimit || ""}
                                      onChange={(event) => updateLessonById(selectedLessonModel.id, { combinedQuizAttemptLimit: event.target.value === "" ? "" : Math.max(1, Number(event.target.value)) })}
                                      className="w-full px-2.5 py-2 bg-white border border-violet-200 rounded-lg text-xs text-slate-700 focus:outline-none focus:border-violet-400 transition-all"
                                      placeholder="Попыток"
                                    />
                                  </div>
                                  <div className="flex items-center gap-5">
                                    <label className="flex items-center gap-2 cursor-pointer">
                                      <input
                                        type="checkbox"
                                        className="accent-violet-600 w-4 h-4"
                                        checked={selectedLessonModel?.combinedQuizRandomOrder !== false}
                                        onChange={(event) => updateLessonById(selectedLessonModel.id, { combinedQuizRandomOrder: event.target.checked })}
                                      />
                                      <span className="text-xs text-violet-800">Случайный порядок вопросов</span>
                                    </label>
                                    <label className="flex items-center gap-2 cursor-pointer">
                                      <input
                                        type="checkbox"
                                        className="accent-violet-600 w-4 h-4"
                                        checked={selectedLessonModel?.combinedQuizShowExplanations !== false}
                                        onChange={(event) => updateLessonById(selectedLessonModel.id, { combinedQuizShowExplanations: event.target.checked })}
                                      />
                                      <span className="text-xs text-violet-800">Показывать пояснения</span>
                                    </label>
                                  </div>
                                  <div className="flex items-center gap-2">
                                    <button
                                      type="button"
                                      onClick={() => updateLessonById(selectedLessonModel.id, {
                                        combinedQuizQuestions: (Array.isArray(questions) ? questions : []).map((questionItem, questionIndex) => ({
                                          ...questionItem,
                                          id: Number(`${selectedLessonModel.id || 0}${questionIndex + 1}${Date.now()}`),
                                        })),
                                      })}
                                      className="px-3 py-1.5 rounded-lg text-[11px] font-semibold bg-white border border-violet-200 text-violet-700 hover:bg-violet-100 transition-colors"
                                    >
                                      Скопировать из итогового теста
                                    </button>
                                    <button
                                      type="button"
                                      onClick={() => updateLessonById(selectedLessonModel.id, { combinedQuizQuestions: [] })}
                                      className="px-3 py-1.5 rounded-lg text-[11px] font-semibold bg-white border border-rose-200 text-rose-700 hover:bg-rose-100 transition-colors"
                                    >
                                      Очистить вопросы
                                    </button>
                                    <span className="text-[11px] text-violet-700">
                                      Вопросов: <strong>{selectedCombinedQuizQuestions.length}</strong>
                                    </span>
                                  </div>
                                  <div className="flex items-center justify-between pt-1">
                                    <div>
                                      <h4 className="text-sm font-semibold text-violet-900">Банк вопросов теста</h4>
                                      <p className="text-xs text-violet-700 mt-0.5">{selectedCombinedQuizQuestions.length} вопросов</p>
                                    </div>
                                    <div className="flex items-center gap-2">
                                      {questionTypes.map((t) => (
                                        <button
                                          key={`combined-qtype-${t.id}`}
                                          type="button"
                                          onClick={() => addCombinedQuizQuestion(selectedLessonModel.id, t.id)}
                                          className={`flex items-center gap-1.5 text-xs px-3 py-2 rounded-xl border font-medium transition-colors ${t.color} border-current/20 hover:opacity-80`}
                                        >
                                          <t.icon size={12} /> {t.label}
                                        </button>
                                      ))}
                                    </div>
                                  </div>
                                  <div className="space-y-3">
                                    {selectedCombinedQuizQuestions.length === 0 && (
                                      <div className="text-xs text-violet-700/80 border border-dashed border-violet-200 rounded-xl p-3 bg-white/70">
                                        Добавьте вопросы для теста комбинированного урока
                                      </div>
                                    )}
                                    {selectedCombinedQuizQuestions.map((q, qi) => (
                                      <div key={`combined-q-${q.id}`} className="bg-white border border-violet-200 rounded-xl p-3">
                                        <div className="flex items-start gap-3 mb-3">
                                          <span className="text-[11px] font-bold text-violet-700 bg-violet-100 w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0">{qi + 1}</span>
                                          <input
                                            value={q.text}
                                            onChange={(e) => updateSelectedCombinedQuizQuestion(q.id, { text: e.target.value })}
                                            placeholder="Текст вопроса..."
                                            className="flex-1 text-xs font-medium text-slate-900 bg-transparent focus:outline-none border-b border-transparent focus:border-violet-300 transition-all pb-1"
                                          />
                                          <div className="flex items-center gap-1">
                                            {questionTypes.map((t) => (
                                              <button
                                                key={`combined-q-${q.id}-type-${t.id}`}
                                                type="button"
                                                onClick={() => updateCombinedQuizQuestionType(selectedLessonModel.id, q.id, t.id)}
                                                title={t.label}
                                                className={`p-1 rounded-lg border transition-all ${q.type === t.id ? `${t.color} border-current/30` : "text-slate-400 border-slate-200 hover:bg-white"}`}
                                              >
                                                <t.icon size={12} />
                                              </button>
                                            ))}
                                            <button
                                              type="button"
                                              onClick={() => updateLessonById(selectedLessonModel.id, (prevLesson) => ({
                                                ...prevLesson,
                                                combinedQuizQuestions: (Array.isArray(prevLesson?.combinedQuizQuestions) ? prevLesson.combinedQuizQuestions : []).filter((item) => item.id !== q.id),
                                              }))}
                                              className="p-1 text-slate-300 hover:text-red-500 rounded-lg hover:bg-red-50 transition-colors"
                                            >
                                              <Trash2 size={12} />
                                            </button>
                                          </div>
                                        </div>

                                        {q.type !== "text" && (
                                          <div className="space-y-1.5 ml-8 mb-2">
                                            {(Array.isArray(q.options) ? q.options : []).map((opt, oi) => {
                                              const isCorrect = q.type === "multiple" ? (Array.isArray(q.correct) && q.correct.includes(oi)) : q.correct === oi;
                                              return (
                                                <div key={`combined-opt-${q.id}-${oi}`} className="flex items-center gap-2">
                                                  <button
                                                    type="button"
                                                    onClick={() => {
                                                      if (q.type === "multiple") {
                                                        const prevCorrect = Array.isArray(q.correct) ? q.correct : [];
                                                        updateLessonById(selectedLessonModel.id, (prevLesson) => ({
                                                          ...prevLesson,
                                                          combinedQuizQuestions: (Array.isArray(prevLesson?.combinedQuizQuestions) ? prevLesson.combinedQuizQuestions : []).map((item) => (
                                                            item.id === q.id
                                                              ? { ...item, correct: isCorrect ? prevCorrect.filter((v) => v !== oi) : [...prevCorrect, oi] }
                                                              : item
                                                          )),
                                                        }));
                                                      } else {
                                                        updateLessonById(selectedLessonModel.id, (prevLesson) => ({
                                                          ...prevLesson,
                                                          combinedQuizQuestions: (Array.isArray(prevLesson?.combinedQuizQuestions) ? prevLesson.combinedQuizQuestions : []).map((item) => (
                                                            item.id === q.id ? { ...item, correct: oi } : item
                                                          )),
                                                        }));
                                                      }
                                                    }}
                                                    className={`flex-shrink-0 ${q.type === "multiple" ? `w-4 h-4 rounded border-2 flex items-center justify-center ${isCorrect ? "border-emerald-500 bg-emerald-500" : "border-slate-300 hover:border-emerald-400"}` : `w-4 h-4 rounded-full border-2 flex items-center justify-center ${isCorrect ? "border-emerald-500 bg-emerald-500" : "border-slate-300 hover:border-emerald-400"}`}`}
                                                  >
                                                    {isCorrect && <Check size={9} className="text-white" />}
                                                  </button>
                                                  <input
                                                    value={opt}
                                                    onChange={(e) => updateLessonById(selectedLessonModel.id, (prevLesson) => ({
                                                      ...prevLesson,
                                                      combinedQuizQuestions: (Array.isArray(prevLesson?.combinedQuizQuestions) ? prevLesson.combinedQuizQuestions : []).map((item) => (
                                                        item.id === q.id
                                                          ? { ...item, options: item.options.map((optionValue, idx) => idx === oi ? e.target.value : optionValue) }
                                                          : item
                                                      )),
                                                    }))}
                                                    readOnly={q.type === "bool"}
                                                    placeholder={`Вариант ${oi + 1}`}
                                                    className={`flex-1 px-2.5 py-1.5 text-xs rounded-lg border transition-all focus:outline-none ${isCorrect ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-slate-200 bg-white text-slate-700 focus:border-violet-300"} ${q.type === "bool" ? "cursor-default" : ""}`}
                                                  />
                                                  {q.type !== "bool" && q.options.length > 2 && (
                                                    <button
                                                      type="button"
                                                      onClick={() => updateLessonById(selectedLessonModel.id, (prevLesson) => ({
                                                        ...prevLesson,
                                                        combinedQuizQuestions: (Array.isArray(prevLesson?.combinedQuizQuestions) ? prevLesson.combinedQuizQuestions : []).map((item) => (
                                                          item.id === q.id ? { ...item, options: item.options.filter((_, idx) => idx !== oi) } : item
                                                        )),
                                                      }))}
                                                      className="p-1 text-slate-300 hover:text-red-400 transition-colors"
                                                    >
                                                      <X size={11} />
                                                    </button>
                                                  )}
                                                </div>
                                              );
                                            })}
                                            {q.type !== "bool" && q.options.length < 6 && (
                                              <button
                                                type="button"
                                                onClick={() => updateLessonById(selectedLessonModel.id, (prevLesson) => ({
                                                  ...prevLesson,
                                                  combinedQuizQuestions: (Array.isArray(prevLesson?.combinedQuizQuestions) ? prevLesson.combinedQuizQuestions : []).map((item) => (
                                                    item.id === q.id ? { ...item, options: [...item.options, ""] } : item
                                                  )),
                                                }))}
                                                className="flex items-center gap-1 text-[11px] text-violet-600 hover:text-violet-800 transition-colors"
                                              >
                                                <Plus size={11} /> Добавить вариант
                                              </button>
                                            )}
                                          </div>
                                        )}

                                        {q.type === "text" && (
                                          <div className="ml-8 mb-2">
                                            <input
                                              value={Array.isArray(q.correct_text_answers) ? q.correct_text_answers.join(", ") : ""}
                                              onChange={(e) => updateSelectedCombinedQuizQuestion(q.id, {
                                                correct_text_answers: e.target.value.split(",").map((item) => item.trim()).filter(Boolean),
                                              })}
                                              placeholder="Ключевые слова через запятую..."
                                              className="w-full px-2.5 py-2 bg-white border border-slate-200 rounded-lg text-xs text-slate-700 focus:outline-none focus:border-violet-400 transition-all"
                                            />
                                          </div>
                                        )}

                                        <input
                                          value={q.explanation}
                                          onChange={(e) => updateSelectedCombinedQuizQuestion(q.id, { explanation: e.target.value })}
                                          placeholder="Пояснение к правильному ответу..."
                                          className="w-full ml-8 px-2.5 py-1.5 text-[11px] rounded-lg border border-slate-200 bg-white text-slate-600 focus:outline-none focus:border-violet-400 transition-all"
                                        />
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}
                            </div>
                          </div>
                        )}

                        {selectedLessonModel.type === "text" && (
                          <div>
                            <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Текст урока</label>
                            <RichTextEditor
                              key={`lesson-${selectedLessonModel.id}-text-content`}
                              value={selectedLessonModel.contentText || ""}
                              onChange={(next) => updateLessonById(selectedLessonModel.id, { contentText: next })}
                              onImageUpload={handleRichTextImageUpload}
                              onFileUpload={handleRichTextFileUpload}
                              placeholder="Введите текст урока..."
                              minHeight={220}
                            />
                          </div>
                        )}

                        {selectedLessonModel.type === "video" && (
                          <>
                            <div>
                              <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Транскрипт видео</label>
                              <RichTextEditor
                                key={`lesson-${selectedLessonModel.id}-video-transcript`}
                                value={selectedLessonModel.contentText || ""}
                                onChange={(next) => updateLessonById(selectedLessonModel.id, { contentText: next })}
                                onImageUpload={handleRichTextImageUpload}
                                onFileUpload={handleRichTextFileUpload}
                                placeholder="Введите текст транскрипта видео..."
                                minHeight={170}
                              />
                            </div>
                            <div>
                              <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Видеофайл</label>
                              {selectedLessonVideoMaterial && (
                                <div className="mb-2 rounded-xl border border-slate-200 overflow-hidden bg-slate-950">
                                  {selectedLessonVideoUrl ? (
                                    <video
                                      key={selectedLessonVideoUrl}
                                      src={selectedLessonVideoUrl}
                                      controls
                                      preload="metadata"
                                      playsInline
                                      {...LMS_PROTECTED_VIDEO_PROPS}
                                      className="w-full max-h-72 bg-black"
                                    />
                                  ) : (
                                    <div className="px-3 py-8 text-xs text-slate-300 text-center">
                                      Видео прикреплено, но ссылка для предпросмотра пока недоступна
                                    </div>
                                  )}
                                </div>
                              )}
                              <button
                                type="button"
                                onClick={() => lessonVideoInputRef.current?.click()}
                                disabled={lessonUploading}
                                className="w-full border-2 border-dashed border-slate-200 hover:border-indigo-300 rounded-xl py-4 text-xs text-slate-500 hover:text-indigo-600 flex items-center justify-center gap-2 transition-all disabled:opacity-60 disabled:cursor-not-allowed"
                              >
                                {lessonUploading ? <RefreshCw size={14} className="animate-spin" /> : <Upload size={14} />}
                                {lessonUploading ? "Загрузка..." : selectedLessonVideoMaterial ? "Заменить видео" : "Загрузить видео"}
                              </button>
                              {selectedLessonVideoMaterial && (
                                <div className="mt-2 text-xs text-slate-600 bg-slate-50 border border-slate-200 rounded-xl px-3 py-2">
                                  Прикреплено: {selectedLessonVideoName}
                                </div>
                              )}
                            </div>

                            <div>
                              <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Дополнительные материалы</label>
                              <div className="flex gap-2 mb-2">
                                <button
                                  type="button"
                                  onClick={() => lessonMaterialInputRef.current?.click()}
                                  disabled={lessonUploading}
                                  className="px-3 py-2 bg-slate-100 hover:bg-slate-200 rounded-lg text-xs font-semibold text-slate-700 transition-colors disabled:opacity-60 disabled:cursor-not-allowed inline-flex items-center gap-2"
                                >
                                  {lessonUploading ? <RefreshCw size={12} className="animate-spin" /> : <Upload size={12} />}
                                  Файл
                                </button>
                                <input
                                  value={lessonMaterialLink}
                                  onChange={(e) => setLessonMaterialLink(e.target.value)}
                                  placeholder="https://..."
                                  className="flex-1 px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-700 placeholder-slate-400 focus:outline-none focus:border-indigo-400 transition-all"
                                />
                                <button type="button" onClick={handleAddMaterialLink} className="px-3 py-2 bg-indigo-600 hover:bg-indigo-700 rounded-lg text-xs font-semibold text-white transition-colors inline-flex items-center gap-1">
                                  <Link2 size={12} /> Добавить
                                </button>
                              </div>
                              <div className="space-y-2">
                                {selectedLessonExtraMaterials.length === 0 && (
                                  <div className="text-xs text-slate-400">Пока ничего не прикреплено</div>
                                )}
                                {selectedLessonExtraMaterials.map((material, index) => (
                                  <div key={`${material?.title || "material"}-${index}`} className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-xl px-3 py-2">
                                    <FileCheck size={13} className="text-indigo-500 flex-shrink-0" />
                                    <div className="flex-1 min-w-0">
                                      <div className="text-xs text-slate-700 truncate">{material?.metadata?.uploaded_file_name || material?.title || `Материал ${index + 1}`}</div>
                                      <div className="text-[10px] text-slate-400 uppercase">{String(material?.material_type || material?.type || "file")}</div>
                                    </div>
                                    <button type="button" onClick={() => handleRemoveLessonMaterial(material._originalIndex)} className="p-1 text-slate-400 hover:text-red-500 transition-colors"><Trash2 size={12} /></button>
                                  </div>
                                ))}
                              </div>
                            </div>

                            <input ref={lessonVideoInputRef} type="file" accept="video/*" onChange={handleLessonVideoUpload} className="hidden" />
                            <input ref={lessonMaterialInputRef} type="file" multiple onChange={handleLessonMaterialUpload} className="hidden" />
                          </>
                        )}

                        {selectedLessonModel.type === "text" && (
                          <>
                            <div>
                              <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Дополнительные материалы</label>
                              <div className="flex gap-2 mb-2">
                                <button
                                  type="button"
                                  onClick={() => lessonMaterialInputRef.current?.click()}
                                  disabled={lessonUploading}
                                  className="px-3 py-2 bg-slate-100 hover:bg-slate-200 rounded-lg text-xs font-semibold text-slate-700 transition-colors disabled:opacity-60 disabled:cursor-not-allowed inline-flex items-center gap-2"
                                >
                                  {lessonUploading ? <RefreshCw size={12} className="animate-spin" /> : <Upload size={12} />}
                                  Файл
                                </button>
                                <input
                                  value={lessonMaterialLink}
                                  onChange={(e) => setLessonMaterialLink(e.target.value)}
                                  placeholder="https://..."
                                  className="flex-1 px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg text-xs text-slate-700 placeholder-slate-400 focus:outline-none focus:border-indigo-400 transition-all"
                                />
                                <button type="button" onClick={handleAddMaterialLink} className="px-3 py-2 bg-indigo-600 hover:bg-indigo-700 rounded-lg text-xs font-semibold text-white transition-colors inline-flex items-center gap-1">
                                  <Link2 size={12} /> Добавить
                                </button>
                              </div>
                              <div className="space-y-2">
                                {selectedLessonExtraMaterials.length === 0 && (
                                  <div className="text-xs text-slate-400">Пока ничего не прикреплено</div>
                                )}
                                {selectedLessonExtraMaterials.map((material, index) => (
                                  <div key={`${material?.title || "material"}-${index}`} className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-xl px-3 py-2">
                                    <FileCheck size={13} className="text-indigo-500 flex-shrink-0" />
                                    <div className="flex-1 min-w-0">
                                      <div className="text-xs text-slate-700 truncate">{material?.metadata?.uploaded_file_name || material?.title || `Материал ${index + 1}`}</div>
                                      <div className="text-[10px] text-slate-400 uppercase">{String(material?.material_type || material?.type || "file")}</div>
                                    </div>
                                    <button type="button" onClick={() => handleRemoveLessonMaterial(material._originalIndex)} className="p-1 text-slate-400 hover:text-red-500 transition-colors"><Trash2 size={12} /></button>
                                  </div>
                                ))}
                              </div>
                            </div>
                            <input ref={lessonMaterialInputRef} type="file" multiple onChange={handleLessonMaterialUpload} className="hidden" />
                          </>
                        )}
                      </>
                    )}
                      </div>
                    </>
                  )}
                </>
              ) : (
                <div className="text-center py-10 text-slate-300"><Edit size={28} className="mx-auto mb-3" /><p className="text-sm">Выберите урок для редактирования</p></div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* FINAL TEST TAB */}
      {tab === "quiz" && (
        <div className="space-y-4 max-w-3xl">
          {/* Настройки теста */}
          <div className="bg-white rounded-2xl border border-slate-200 p-5">
            <h3 className="text-sm font-semibold text-slate-900 mb-4">Настройки итогового теста</h3>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Вопросов в тесте</label>
                <input type="number" value={Math.max(1, Number(settings.questionsPerTest || 1))} onChange={e => setSettings(p => ({ ...p, questionsPerTest: Math.max(1, Number(e.target.value || 1)) }))} min={1} max={Math.max(1, questions.length)} className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-center focus:outline-none focus:border-indigo-400 transition-all" />
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Лимит времени (мин)</label>
                <input type="number" value={settings.finalTestTimeLimitMinutes || ""} onChange={e => setSettings(p => ({ ...p, finalTestTimeLimitMinutes: e.target.value === "" ? "" : Math.max(0, Number(e.target.value)) }))} className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-center focus:outline-none focus:border-indigo-400 transition-all" />
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Проходной балл</label>
                <input type="number" value={settings.passingScore} onChange={e => setSettings(p => ({ ...p, passingScore: +e.target.value }))} className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-center focus:outline-none focus:border-indigo-400 transition-all" />
              </div>
            </div>
            <div className="flex items-center gap-6 mt-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" className="accent-indigo-600 w-4 h-4" checked={settings.randomOrder} onChange={e => setSettings(p => ({ ...p, randomOrder: e.target.checked }))} />
                <span className="text-xs text-slate-700">Случайный порядок вопросов</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" className="accent-indigo-600 w-4 h-4" checked={settings.showExplanations} onChange={e => setSettings(p => ({ ...p, showExplanations: e.target.checked }))} />
                <span className="text-xs text-slate-700">Показывать пояснения к ответам</span>
              </label>
            </div>
          </div>

          {/* Добавить вопрос — кнопки по типам */}
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-base font-semibold text-slate-900">Итоговый тест</h3>
              <p className="text-xs text-slate-500 mt-0.5">{questions.length} вопросов · в тест войдёт {Math.min(settings.questionsPerTest, questions.length)}</p>
            </div>
            <div className="flex items-center gap-2">
              {questionTypes.map(t => (
                <button key={t.id} onClick={() => addQuestion(t.id)} className={`flex items-center gap-1.5 text-xs px-3 py-2 rounded-xl border font-medium transition-colors ${t.color} border-current/20 hover:opacity-80`}>
                  <t.icon size={12} /> {t.label}
                </button>
              ))}
            </div>
          </div>

          {questions.map((q, qi) => (
            <div key={q.id} className="bg-white rounded-2xl border border-slate-200 p-5">
              <div className="flex items-start gap-3 mb-4">
                <span className="text-xs font-bold text-indigo-600 bg-indigo-50 w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0">{qi + 1}</span>
                <input value={q.text} onChange={e => setQuestions(p => p.map(x => x.id === q.id ? { ...x, text: e.target.value } : x))} placeholder="Текст вопроса..." className="flex-1 text-sm font-medium text-slate-900 bg-transparent focus:outline-none border-b border-transparent focus:border-slate-300 transition-all pb-1" />
                <div className="flex items-center gap-2 ml-auto flex-shrink-0">
                  {/* Тип вопроса */}
                  <div className="flex items-center gap-1">
                    {questionTypes.map(t => (
                      <button key={t.id} onClick={() => updateQuestionType(q.id, t.id)} title={t.label} className={`p-1.5 rounded-lg border transition-all ${q.type === t.id ? `${t.color} border-current/30` : "text-slate-400 border-slate-200 hover:bg-slate-50"}`}>
                        <t.icon size={13} />
                      </button>
                    ))}
                  </div>
                  <button onClick={() => setQuestions(p => p.filter(x => x.id !== q.id))} className="p-1.5 text-slate-300 hover:text-red-500 rounded-lg hover:bg-red-50 transition-colors"><Trash2 size={14} /></button>
                </div>
              </div>

              {/* Опции для single/bool/multiple */}
              {q.type !== "text" && (
                <div className="space-y-2 ml-10 mb-4">
                  {q.options.map((opt, oi) => {
                    const isCorrect = q.type === "multiple" ? (Array.isArray(q.correct) && q.correct.includes(oi)) : q.correct === oi;
                    return (
                      <div key={oi} className="flex items-center gap-2">
                        <button
                          onClick={() => {
                            if (q.type === "multiple") {
                              const prev = Array.isArray(q.correct) ? q.correct : [];
                              setQuestions(p => p.map(x => x.id === q.id ? { ...x, correct: isCorrect ? prev.filter(v => v !== oi) : [...prev, oi] } : x));
                            } else {
                              setQuestions(p => p.map(x => x.id === q.id ? { ...x, correct: oi } : x));
                            }
                          }}
                          className={`flex-shrink-0 transition-all ${q.type === "multiple" ? `w-5 h-5 rounded border-2 flex items-center justify-center ${isCorrect ? "border-emerald-500 bg-emerald-500" : "border-slate-300 hover:border-emerald-400"}` : `w-5 h-5 rounded-full border-2 flex items-center justify-center ${isCorrect ? "border-emerald-500 bg-emerald-500" : "border-slate-300 hover:border-emerald-400"}`}`}
                        >
                          {isCorrect && <Check size={10} className="text-white" />}
                        </button>
                        <input
                          value={opt}
                          onChange={e => setQuestions(p => p.map(x => x.id === q.id ? { ...x, options: x.options.map((o, i) => i === oi ? e.target.value : o) } : x))}
                          readOnly={q.type === "bool"}
                          placeholder={`Вариант ${oi + 1}`}
                          className={`flex-1 px-3 py-2 text-sm rounded-xl border transition-all focus:outline-none ${isCorrect ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-slate-200 bg-slate-50 text-slate-700 focus:border-indigo-300"} ${q.type === "bool" ? "cursor-default" : ""}`}
                        />
                        {q.type !== "bool" && q.options.length > 2 && (
                          <button onClick={() => setQuestions(p => p.map(x => x.id === q.id ? { ...x, options: x.options.filter((_, i) => i !== oi) } : x))} className="p-1 text-slate-300 hover:text-red-400 transition-colors"><X size={12} /></button>
                        )}
                      </div>
                    );
                  })}
                  {q.type !== "bool" && q.options.length < 6 && (
                    <button onClick={() => setQuestions(p => p.map(x => x.id === q.id ? { ...x, options: [...x.options, ""] } : x))} className="flex items-center gap-1.5 text-xs text-indigo-600 hover:text-indigo-800 transition-colors mt-1">
                      <Plus size={12} /> Добавить вариант
                    </button>
                  )}
                </div>
              )}

              {/* Text type placeholder */}
              {q.type === "text" && (
                <div className="ml-10 mb-4">
                  <p className="text-xs text-slate-400 mb-2">Ключевые слова для проверки ответа (через запятую):</p>
                  <input
                    value={Array.isArray(q.correct_text_answers) ? q.correct_text_answers.join(", ") : ""}
                    onChange={e => setQuestions(p => p.map(x => x.id === q.id ? { ...x, correct_text_answers: e.target.value.split(",").map(item => item.trim()).filter(Boolean) } : x))}
                    placeholder="least privilege, минимальных привилегий, наименьших привилегий..."
                    className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all"
                  />
                </div>
              )}

              {/* Пояснение */}
              <div className="ml-10">
                <input value={q.explanation} onChange={e => setQuestions(p => p.map(x => x.id === q.id ? { ...x, explanation: e.target.value } : x))} placeholder="Пояснение к правильному ответу (опционально)..." className="w-full px-3 py-2 text-xs rounded-xl border border-slate-200 bg-slate-50 text-slate-600 focus:outline-none focus:border-indigo-400 transition-all" />
              </div>
            </div>
          ))}

          <button onClick={() => addQuestion("single")} className="w-full border-2 border-dashed border-slate-200 hover:border-indigo-300 rounded-2xl py-4 text-sm text-slate-500 hover:text-indigo-600 flex items-center justify-center gap-2 transition-all font-medium"><Plus size={16} /> Добавить вопрос</button>
        </div>
      )}

      {/* ASSIGNMENT TAB */}
      {tab === "assignment" && (
        <div className="grid grid-cols-2 gap-6">
          <div className="bg-white rounded-2xl border border-slate-200 p-6">
            <h3 className="text-sm font-semibold text-slate-900 mb-4">Назначение сотрудникам</h3>
            <div className="flex items-center gap-2 mb-4">
              <input
                type="checkbox"
                id="all"
                className="accent-indigo-600 w-4 h-4"
                checked={filteredLearners.length > 0 && selectedLearnerIds.length === filteredLearners.length}
                onChange={(e) => toggleSelectAllLearners(e.target.checked)}
              />
              <label htmlFor="all" className="text-sm font-medium text-slate-800">Назначить всем сотрудникам</label>
            </div>
            <div className="mb-4">
              <div className="relative">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input value={assignmentSearch} onChange={(e) => setAssignmentSearch(e.target.value)} placeholder="Поиск по имени или роли..." className="w-full pl-9 pr-4 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm placeholder-slate-400 focus:outline-none focus:border-indigo-400 transition-all" />
              </div>
            </div>
            <div className="space-y-2 max-h-80 overflow-y-auto custom-scrollbar">
              {filteredLearners.length === 0 && (
                <div className="text-xs text-slate-400 py-6 text-center">Сотрудники не найдены</div>
              )}
              {filteredLearners.map(e => (
                <label key={e.id} className="flex items-center gap-3 p-3 rounded-xl hover:bg-slate-50 cursor-pointer transition-colors">
                  <input type="checkbox" className="accent-indigo-600 w-4 h-4" checked={selectedLearnerIds.includes(Number(e.id))} onChange={() => toggleLearner(Number(e.id))} />
                  <div className="w-8 h-8 rounded-full bg-gradient-to-br from-slate-400 to-slate-600 flex items-center justify-center text-white text-xs font-semibold flex-shrink-0">{String(e.name || "").split(" ").map(w => w[0]).join("").slice(0, 2) || "U"}</div>
                  <div><p className="text-sm font-medium text-slate-800">{e.name}</p><p className="text-xs text-slate-400">{e.role}</p></div>
                </label>
              ))}
            </div>
          </div>
          <div className="space-y-4">
            <div className="bg-white rounded-2xl border border-slate-200 p-6">
              <h3 className="text-sm font-semibold text-slate-900 mb-4">Параметры назначения</h3>
              <div className="space-y-4">
                <div>
                  <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Курс для назначения</label>
                  <select value={effectiveAssignmentCourseId || ""} onChange={(e) => setAssignmentCourseId(Number(e.target.value) || null)} className="w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all">
                    <option value="">Выберите курс</option>
                    {assignableAdminCourses.map((courseItem) => (
                      <option key={courseItem.id} value={courseItem.id}>{courseItem.title}</option>
                    ))}
                  </select>
                  {assignableAdminCourses.length === 0 && (
                    <p className="text-[11px] text-slate-400 mt-1">
                      Назначение доступно только для опубликованных курсов.
                    </p>
                  )}
                </div>
                <div><label className="text-xs font-semibold text-slate-600 mb-1.5 block">Дедлайн для группы</label><input type="date" value={assignmentDueAt} onChange={(e) => setAssignmentDueAt(e.target.value)} className="w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all" /></div>
                {[{ label: "Уведомить сотрудников", sub: "Email при назначении" }, { label: "Напоминания о дедлайне", sub: "За 7 и 3 дня до" }].map(item => (
                  <div key={item.label} className="flex items-center justify-between py-3 border-b border-slate-100 last:border-0">
                    <div><p className="text-sm font-medium text-slate-800">{item.label}</p><p className="text-xs text-slate-400 mt-0.5">{item.sub}</p></div>
                    <button className="w-10 h-5 rounded-full bg-indigo-600 relative flex-shrink-0"><div className="w-3.5 h-3.5 bg-white rounded-full absolute top-0.5 right-0.5 shadow-sm" /></button>
                  </div>
                ))}
              </div>
            </div>
            <button onClick={handleAssignSelected} disabled={assigning || !selectedLearnerIds.length || !effectiveAssignmentCourseId} className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-3 rounded-xl text-sm transition-colors flex items-center justify-center gap-2"><UserCheck size={16} /> {assigning ? "Назначаем..." : "Назначить выбранным"}</button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── MONTH PICKER ─────────────────────────────────────────────────────────────

const MONTH_NAMES_RU = [
  "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
  "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
];

function MonthPicker({ value = "", onChange }) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef(null);

  const parsed = (() => {
    const parts = String(value || "").split("-");
    if (parts.length === 2) {
      const y = parseInt(parts[0], 10);
      const m = parseInt(parts[1], 10);
      if (y >= 2000 && y <= 2100 && m >= 1 && m <= 12) return { year: y, month: m };
    }
    const now = new Date();
    return { year: now.getFullYear(), month: now.getMonth() + 1 };
  })();

  const [viewYear, setViewYear] = useState(parsed.year);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen]);

  // Sync view year when popup opens
  useEffect(() => {
    if (isOpen) {
      setViewYear(parsed.year);
    }
  }, [isOpen, parsed.year]);

  const toValue = (year, month) =>
    `${year}-${String(month).padStart(2, "0")}`;

  const step = (dir) => {
    let { year, month } = parsed;
    month += dir;
    if (month < 1) { month = 12; year -= 1; }
    if (month > 12) { month = 1; year += 1; }
    onChange?.(toValue(year, month));
  };

  const handleSelectMonth = (m) => {
    onChange?.(toValue(viewYear, m));
    setIsOpen(false);
  };

  const now = new Date();
  const isCurrentMonth = parsed.year === now.getFullYear() && parsed.month === now.getMonth() + 1;

  return (
    <div className="relative" ref={dropdownRef}>
      <div className="flex items-center gap-1 bg-slate-100 p-1 rounded-xl select-none">
        <button
          onClick={() => step(-1)}
          className="flex items-center justify-center w-10 h-10 rounded-lg text-slate-500 hover:text-slate-800 hover:bg-white hover:shadow-sm transition-all"
          title="Предыдущий месяц"
        >
          <ChevronLeft size={14} />
        </button>
        <div 
          onClick={() => setIsOpen(!isOpen)}
          className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-white shadow-sm min-w-[148px] justify-center cursor-pointer hover:bg-slate-50 transition-colors"
          title="Выбрать месяц"
        >
          <CalendarDays size={14} className="text-indigo-500 shrink-0" />
          <span className="text-sm font-medium text-slate-800 whitespace-nowrap select-none">
            {MONTH_NAMES_RU[parsed.month - 1]} {parsed.year}
          </span>
        </div>
        <button
          onClick={() => step(1)}
          disabled={isCurrentMonth}
          className={`flex items-center justify-center w-10 h-10 rounded-lg transition-all ${isCurrentMonth ? "text-slate-300 cursor-not-allowed" : "text-slate-500 hover:text-slate-800 hover:bg-white hover:shadow-sm"}`}
          title="Следующий месяц"
        >
          <ChevronRight size={14} />
        </button>
      </div>

      {isOpen && (
        <div className="absolute top-full left-1/2 -translate-x-1/2 mt-2 w-64 bg-white rounded-2xl shadow-xl border border-slate-200 p-4 z-50 animate-in fade-in zoom-in-95 duration-200">
          {/* Year selector */}
          <div className="flex items-center justify-between mb-4">
            <button 
              onClick={() => setViewYear(y => y - 1)}
              className="p-1.5 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors"
            >
              <ChevronLeft size={16} />
            </button>
            <span className="text-sm font-bold text-slate-800">{viewYear}</span>
            <button 
              onClick={() => setViewYear(y => Math.min(now.getFullYear(), y + 1))}
              disabled={viewYear >= now.getFullYear()}
              className={`p-1.5 rounded-lg transition-colors ${viewYear >= now.getFullYear() ? "text-slate-200 cursor-not-allowed" : "text-slate-400 hover:text-indigo-600 hover:bg-indigo-50"}`}
            >
              <ChevronRight size={16} />
            </button>
          </div>
          
          {/* Months grid */}
          <div className="grid grid-cols-3 gap-2">
            {MONTH_NAMES_RU.map((monthName, idx) => {
              const monthNum = idx + 1;
              const isSelected = parsed.year === viewYear && parsed.month === monthNum;
              const isFuture = viewYear === now.getFullYear() && monthNum > now.getMonth() + 1;
              
              return (
                <button
                  key={monthName}
                  onClick={() => !isFuture && handleSelectMonth(monthNum)}
                  disabled={isFuture}
                  className={`
                    py-2 rounded-xl text-xs font-medium transition-all
                    ${isSelected 
                      ? "bg-indigo-600 text-white shadow-md shadow-indigo-200" 
                      : isFuture
                        ? "text-slate-300 cursor-not-allowed"
                        : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                    }
                  `}
                >
                  {monthName.slice(0, 3)}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── ADMIN VIEW ───────────────────────────────────────────────────────────────

function AdminView({
  tab,
  setTab,
  adminCourses = [],
  progressRows = [],
  attempts = [],
  analytics = null,
  loading = false,
  selectedMonth = "",
  onMonthChange,
  onOpenBuilder,
  onOpenCourse,
  onDeleteCourse,
  onArchiveCourse,
  onRestoreCourse,
  onAssignCourseToEmployee,
  canDeleteCourses = true,
  busyCourseId = null,
  isEditorMode = false,
  loadLearningSessions,
}) {
  const [deletingCourseId, setDeletingCourseId] = useState(null);
  const [archivingCourseId, setArchivingCourseId] = useState(null);
  const [restoringCourseId, setRestoringCourseId] = useState(null);
  const [employeeSearch, setEmployeeSearch] = useState("");
  const [employeeAssignedCourseSearch, setEmployeeAssignedCourseSearch] = useState("");
  const [employeeDeptFilter, setEmployeeDeptFilter] = useState("all");
  const [courseSearch, setCourseSearch] = useState("");
  const [courseFilter, setCourseFilter] = useState("all");
  const [editorCourseScope, setEditorCourseScope] = useState("courses");
  const [courseSortBy, setCourseSortBy] = useState("title");
  const [courseGridView, setCourseGridView] = useState(true);
  const [selectedEmployeeId, setSelectedEmployeeId] = useState(null);
  const [selectedEmployeeCourseKey, setSelectedEmployeeCourseKey] = useState(null);
  const [employeeAssignedCourseSortBy, setEmployeeAssignedCourseSortBy] = useState("deadline_asc");
  const [employeeCourseDeadlines, setEmployeeCourseDeadlines] = useState({});
  const [assigningCourseId, setAssigningCourseId] = useState(null);
  const [isAssignCourseModalOpen, setIsAssignCourseModalOpen] = useState(false);
  const [selectedEmployeeLearningSessions, setSelectedEmployeeLearningSessions] = useState([]);
  const [isLoadingSelectedEmployeeLearningSessions, setIsLoadingSelectedEmployeeLearningSessions] = useState(false);

  useEffect(() => {
    if (!isEditorMode) {
      setEditorCourseScope("courses");
    }
  }, [isEditorMode]);

  const tabs = [
    { id: "analytics", label: "Аналитика", icon: BarChart2 },
    { id: "employees", label: "Сотрудники", icon: Users },
    { id: "courses", label: "Курсы", icon: BookOpen },
  ];

  const safeProgressRows = Array.isArray(progressRows) ? progressRows : [];
  const safeAttempts = Array.isArray(attempts) ? attempts : [];
  const safeAdminCourses = Array.isArray(adminCourses) ? adminCourses : [];
  const assignableAdminCourses = safeAdminCourses.filter(isAssignableLmsCourse);
  const isAdminLoading = loading && safeAdminCourses.length === 0 && safeProgressRows.length === 0 && safeAttempts.length === 0;
  const extractCourseSkills = (courseLike) => {
    const directSkills = Array.isArray(courseLike?.skills)
      ? courseLike.skills.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
    const versionSkills = Array.isArray(courseLike?.course_version?.skills)
      ? courseLike.course_version.skills.map((item) => String(item || "").trim()).filter(Boolean)
      : [];
    return Array.from(new Set([...directSkills, ...versionSkills]));
  };
  const compareCourseTitles = (left, right) =>
    String(left?.title || "").localeCompare(String(right?.title || ""), "ru");
  const getCourseDeadlineTime = (courseLike) => {
    const parsed = parseLmsDate(courseLike?.deadline);
    return parsed ? parsed.getTime() : null;
  };
  const compareCoursesByDeadlineAsc = (left, right) => {
    const leftTime = getCourseDeadlineTime(left);
    const rightTime = getCourseDeadlineTime(right);
    if (leftTime == null && rightTime == null) return compareCourseTitles(left, right);
    if (leftTime == null) return 1;
    if (rightTime == null) return -1;
    if (leftTime !== rightTime) return leftTime - rightTime;
    return compareCourseTitles(left, right);
  };
  const compareCoursesByDeadlineDesc = (left, right) => {
    const leftTime = getCourseDeadlineTime(left);
    const rightTime = getCourseDeadlineTime(right);
    if (leftTime == null && rightTime == null) return compareCourseTitles(left, right);
    if (leftTime == null) return 1;
    if (rightTime == null) return -1;
    if (leftTime !== rightTime) return rightTime - leftTime;
    return compareCourseTitles(left, right);
  };

  const attemptAggByUser = new Map();
  safeAttempts.forEach((item) => {
    const userId = Number(item?.user_id || 0);
    if (!userId) return;
    const prev = attemptAggByUser.get(userId) || { count: 0, scoreSum: 0, scoreCount: 0, duration: 0, lastAt: null };
    prev.count += 1;
    if (item?.score_percent != null) {
      prev.scoreSum += Number(item.score_percent) || 0;
      prev.scoreCount += 1;
    }
    prev.duration += Math.max(0, Number(item?.duration_seconds || 0));
    const startedAt = item?.started_at ? new Date(item.started_at) : null;
    if (startedAt && !Number.isNaN(startedAt.getTime()) && (!prev.lastAt || startedAt > prev.lastAt)) {
      prev.lastAt = startedAt;
    }
    attemptAggByUser.set(userId, prev);
  });

  const employeeMap = new Map();
  safeProgressRows.forEach((row) => {
    const userId = Number(row?.user_id || 0);
    if (!userId) return;
    const uiStatus = mapAdminProgressRowToUiStatus(row);
    const progressPercent = clampLmsProgress(row?.progress_percent);
    const startedAt = row?.started_at ? new Date(row.started_at) : null;
    const prev = employeeMap.get(userId) || {
      id: userId,
      name: row?.user_name || `User #${userId}`,
      dept: row?.user_role || "—",
      courses: 0,
      completed: 0,
      progressSum: 0,
      avgScore: 0,
      overdue: 0,
      lastActive: "—",
      testTime: "0ч 00м",
      attempts: 0,
      lastStartedAt: null,
      learningSeconds: 0,
      activeLearningSeconds: 0,
      tabHiddenCount: 0,
      staleGapCount: 0,
      sessionCount: 0,
      lastLearningAt: null,
    };
    prev.courses += 1;
    prev.progressSum += progressPercent;
    if (isCompletedLmsStatus(uiStatus)) prev.completed += 1;
    if (uiStatus === "overdue") prev.overdue += 1;
    if (startedAt && !Number.isNaN(startedAt.getTime()) && (!prev.lastStartedAt || startedAt > prev.lastStartedAt)) {
      prev.lastStartedAt = startedAt;
    }
    prev.learningSeconds += Math.max(0, Number(row?.active_learning_seconds || 0));
    prev.activeLearningSeconds += Math.max(0, Number(row?.active_learning_seconds || 0));
    prev.tabHiddenCount += Math.max(0, Number(row?.learning_tab_hidden_count || 0));
    prev.staleGapCount += Math.max(0, Number(row?.learning_stale_gap_count || 0));
    prev.sessionCount += Math.max(0, Number(row?.session_count || 0));
    const lastLearningAt = row?.last_learning_at ? new Date(row.last_learning_at) : null;
    if (lastLearningAt && !Number.isNaN(lastLearningAt.getTime()) && (!prev.lastLearningAt || lastLearningAt > prev.lastLearningAt)) {
      prev.lastLearningAt = lastLearningAt;
    }
    employeeMap.set(userId, prev);
  });

  let employeeRows = Array.from(employeeMap.values()).map((row) => {
    const agg = attemptAggByUser.get(Number(row.id));
    const avgScore = agg && agg.scoreCount > 0 ? Math.round(agg.scoreSum / agg.scoreCount) : row.avgScore;
    const totalDuration = agg ? agg.duration : 0;
    const hours = Math.floor(totalDuration / 3600);
    const minutes = Math.floor((totalDuration % 3600) / 60);
    const learningSeconds = Math.max(0, Number(row.learningSeconds || 0));
    const latestActivityIso = [
      agg?.lastAt?.toISOString?.() || null,
      row.lastLearningAt?.toISOString?.() || null,
      row.lastStartedAt?.toISOString?.() || null,
    ].filter(Boolean).sort().pop() || null;
    return {
      ...row,
      learningTime: formatDurationLabel(learningSeconds),
      learningSeconds,
      activeLearningSeconds: Math.max(0, Number(row.activeLearningSeconds || 0)),
      sessionCount: Math.max(0, Number(row.sessionCount || 0)),
      tabHiddenCount: Math.max(0, Number(row.tabHiddenCount || 0)),
      staleGapCount: Math.max(0, Number(row.staleGapCount || 0)),
      progress: row.courses > 0 ? Math.round(row.progressSum / row.courses) : 0,
      avgScore,
      attempts: agg ? agg.count : row.attempts,
      testTime: `${hours}ч ${String(minutes).padStart(2, "0")}м`,
      lastActive: latestActivityIso ? toRelativeTime(latestActivityIso) : row.lastActive,
    };
  });
  const courseStatMap = new Map();
  safeProgressRows.forEach((row) => {
    const courseId = Number(row?.course_id || 0);
    if (!courseId) return;
    const uiStatus = mapAdminProgressRowToUiStatus(row);
    const progressPercent = clampLmsProgress(row?.progress_percent);
    const prev = courseStatMap.get(courseId) || {
      total: 0,
      completed: 0,
      completedLate: 0,
      inProgress: 0,
      notStarted: 0,
      overdue: 0,
      progressSum: 0,
      lessons: 0,
      nearestDeadlineAt: null,
      nearestDeadlineAtMs: null,
    };
    prev.total += 1;
    prev.progressSum += progressPercent;
    if (uiStatus === "completed") prev.completed += 1;
    else if (uiStatus === "completed_late") prev.completedLate += 1;
    else if (uiStatus === "in_progress") prev.inProgress += 1;
    else if (uiStatus === "overdue") prev.overdue += 1;
    else prev.notStarted += 1;
    prev.lessons = Math.max(prev.lessons, Number(row?.total_lessons || 0));
    const dueAt = parseLmsDate(row?.due_at);
    if (dueAt && !isCompletedLmsStatus(uiStatus)) {
      const dueAtMs = dueAt.getTime();
      if (
        Number.isFinite(dueAtMs) &&
        (prev.nearestDeadlineAtMs == null || dueAtMs < prev.nearestDeadlineAtMs)
      ) {
        prev.nearestDeadlineAtMs = dueAtMs;
        prev.nearestDeadlineAt = row?.due_at || dueAt.toISOString();
      }
    }
    courseStatMap.set(courseId, prev);
  });

  let courseRows = safeAdminCourses.map((item, index) => {
    const visual = pickCourseVisual(item?.id || index, item?.category || "");
    const stat = courseStatMap.get(Number(item?.id || 0)) || {
      total: 0,
      completed: 0,
      completedLate: 0,
      inProgress: 0,
      notStarted: 0,
      overdue: 0,
      progressSum: 0,
      lessons: 0,
      nearestDeadlineAt: null,
      nearestDeadlineAtMs: null,
    };
    const progressPercent = stat.total > 0 ? Math.round(stat.progressSum / stat.total) : 0;
    const publishStatus = String(item?.status || "").trim().toLowerCase();
    const latestVersionStatus = String(item?.latest_version_status || "").trim().toLowerCase();
    const isDraftOfPublished = publishStatus === "published" && latestVersionStatus === "draft";
    // Длительность автоматически из количества уроков
    const adminDuration = stat.lessons > 0 ? `${stat.lessons} уроков` : "—";
    return {
      id: Number(item?.id || index + 1),
      title: item?.title || `Курс #${item?.id || index + 1}`,
      category: item?.category || "Без категории",
      skills: extractCourseSkills(item),
      cover: visual.cover,
      color: visual.color,
      mandatory: false,
      duration: adminDuration,
      lessons: stat.lessons || 0,
      maxAttempts: Number(item?.default_attempt_limit || 3),
      attemptsUsed: 0,
      rating: 0,
      status: resolveAdminCourseAggregateStatus(stat),
      publishStatus,
      latestVersionStatus,
      latestVersionId: Number(item?.latest_version_id || 0) || null,
      latestVersionNumber: Number(item?.latest_version_number || 0) || null,
      hasDraftVersion: Boolean(item?.has_draft_version),
      latestDraftVersionId: Number(item?.latest_draft_version_id || 0) || null,
      latestDraftVersionNumber: Number(item?.latest_draft_version_number || 0) || null,
      editorDraftLabel: isDraftOfPublished ? "Черновик выпущенного" : (latestVersionStatus === "draft" ? "Черновик" : ""),
      progress: progressPercent,
      deadline: stat.nearestDeadlineAt || null,
    };
  });
  let failStatsRows = safeAttempts
    .filter((item) => item?.score_percent != null)
    .sort((a, b) => Number(a?.score_percent || 0) - Number(b?.score_percent || 0))
    .slice(0, 4)
    .map((item, index) => ({
      questionId: index + 1,
      text: item?.test_title || "Тест",
      failRate: Math.max(0, 100 - Math.round(Number(item?.score_percent || 0))),
      course: item?.course_title || "Курс",
    }));

  let overallProgress = safeProgressRows.length
    ? Math.round(safeProgressRows.reduce((sum, row) => sum + clampLmsProgress(row?.progress_percent), 0) / safeProgressRows.length)
    : 0;
  let avgScore = employeeRows.length ? Math.round(employeeRows.reduce((a, e) => a + Number(e.avgScore || 0), 0) / employeeRows.length) : 0;
  let overdueCount = employeeRows.reduce((a, e) => a + Number(e.overdue || 0), 0);
  const assignmentStatusCounts = safeProgressRows.reduce((acc, row) => {
    const uiStatus = mapAdminProgressRowToUiStatus(row);
    if (isCompletedLmsStatus(uiStatus)) acc.completed += 1;
    else if (uiStatus === "overdue") acc.overdue += 1;
    else if (uiStatus === "in_progress") acc.inProgress += 1;
    else acc.notStarted += 1;
    return acc;
  }, {
    completed: 0,
    inProgress: 0,
    notStarted: 0,
    overdue: 0,
  });
  const completedCoursesCount = assignmentStatusCounts.completed;
  const inProgressCoursesCount = assignmentStatusCounts.inProgress;
  const notStartedCoursesCount = assignmentStatusCounts.notStarted;
  const overdueCoursesCount = assignmentStatusCounts.overdue;
  const courseStatusTotal = Math.max(
    1,
    completedCoursesCount + inProgressCoursesCount + notStartedCoursesCount + overdueCoursesCount
  );
  let courseStatusRows = [
    { label: "Завершены", count: completedCoursesCount, color: "bg-emerald-500" },
    { label: "В процессе", count: inProgressCoursesCount, color: "bg-blue-500" },
    { label: "Не начаты", count: notStartedCoursesCount, color: "bg-slate-300" },
    { label: "Просрочены", count: overdueCoursesCount, color: "bg-red-500" },
  ].map((item) => ({
    ...item,
    pct: Math.round((item.count / courseStatusTotal) * 100),
  }));

  const safeAnalytics = analytics && typeof analytics === "object" ? analytics : null;
  const hasAnalyticsPayload = Boolean(
    safeAnalytics &&
    (
      safeAnalytics.summary ||
      Array.isArray(safeAnalytics.employee_rows) ||
      Array.isArray(safeAnalytics.course_stats) ||
      Array.isArray(safeAnalytics.course_status_rows) ||
      Array.isArray(safeAnalytics.fail_stats)
    )
  );

  if (hasAnalyticsPayload) {
    const analyticsEmployeeRows = Array.isArray(safeAnalytics?.employee_rows) ? safeAnalytics.employee_rows : [];
    const analyticsCourseStats = new Map(
      (Array.isArray(safeAnalytics?.course_stats) ? safeAnalytics.course_stats : [])
        .map((item) => [Number(item?.course_id || 0), item])
        .filter(([courseId]) => courseId > 0)
    );

    employeeRows = analyticsEmployeeRows.map((row) => {
      const totalDuration = Math.max(0, Number(row?.test_duration_seconds || 0));
      const hours = Math.floor(totalDuration / 3600);
      const minutes = Math.floor((totalDuration % 3600) / 60);
      const learningSeconds = Math.max(0, Number(row?.active_learning_seconds || 0));
      return {
        id: Number(row?.id || 0),
        name: String(row?.name || `User #${row?.id || 0}`),
        dept: String(row?.dept || "—"),
        courses: Math.max(0, Number(row?.courses || 0)),
        completed: Math.max(0, Number(row?.completed || 0)),
        learningTime: formatDurationLabel(learningSeconds),
        learningSeconds,
        activeLearningSeconds: Math.max(0, Number(row?.active_learning_seconds || 0)),
        sessionCount: Math.max(0, Number(row?.session_count || 0)),
        tabHiddenCount: Math.max(0, Number(row?.tab_hidden_count || 0)),
        staleGapCount: Math.max(0, Number(row?.stale_gap_count || 0)),
        progress: Math.max(0, Math.min(100, Number(row?.progress || 0))),
        avgScore: Math.max(0, Number(row?.avg_score || 0)),
        overdue: Math.max(0, Number(row?.overdue || 0)),
        attempts: Math.max(0, Number(row?.attempts || 0)),
        testTime: `${hours}ч ${String(minutes).padStart(2, "0")}м`,
        lastActive: row?.last_active_at ? toRelativeTime(row.last_active_at) : "—",
      };
    });

    courseRows = safeAdminCourses.map((item, index) => {
      const visual = pickCourseVisual(item?.id || index, item?.category || "");
      const stat = analyticsCourseStats.get(Number(item?.id || 0)) || {};
      const runtimeStat = courseStatMap.get(Number(item?.id || 0)) || {};
      const lessons = Math.max(0, Number(stat?.lessons || 0));
      const publishStatus = String(item?.status || "").trim().toLowerCase();
      const latestVersionStatus = String(item?.latest_version_status || "").trim().toLowerCase();
      const isDraftOfPublished = publishStatus === "published" && latestVersionStatus === "draft";
      return {
        id: Number(item?.id || index + 1),
        title: item?.title || `Курс #${item?.id || index + 1}`,
        category: item?.category || "Без категории",
        skills: extractCourseSkills(item),
        cover: visual.cover,
        color: visual.color,
        mandatory: false,
        duration: lessons > 0 ? `${lessons} уроков` : "—",
        lessons,
        maxAttempts: Number(item?.default_attempt_limit || 3),
        attemptsUsed: 0,
        rating: 0,
        status: String(stat?.status || "not_started"),
        publishStatus,
        latestVersionStatus,
        latestVersionId: Number(item?.latest_version_id || 0) || null,
        latestVersionNumber: Number(item?.latest_version_number || 0) || null,
        hasDraftVersion: Boolean(item?.has_draft_version),
        latestDraftVersionId: Number(item?.latest_draft_version_id || 0) || null,
        latestDraftVersionNumber: Number(item?.latest_draft_version_number || 0) || null,
        editorDraftLabel: isDraftOfPublished ? "Черновик выпущенного" : (latestVersionStatus === "draft" ? "Черновик" : ""),
        progress: Math.max(0, Math.min(100, Number(stat?.progress || 0))),
        deadline: runtimeStat?.nearestDeadlineAt || null,
      };
    });

    failStatsRows = (Array.isArray(safeAnalytics?.fail_stats) ? safeAnalytics.fail_stats : []).map((item, index) => ({
      questionId: Number(item?.question_id || index + 1),
      text: item?.text || "Тест",
      failRate: Math.max(0, Math.min(100, Number(item?.fail_rate || 0))),
      course: item?.course || "Курс",
    }));

    overallProgress = Math.max(0, Math.min(100, Number(safeAnalytics?.summary?.overall_progress || 0)));
    avgScore = Math.max(0, Math.min(100, Number(safeAnalytics?.summary?.avg_score || 0)));
    overdueCount = Math.max(0, Number(safeAnalytics?.summary?.overdue_count || 0));
    courseStatusRows = (Array.isArray(safeAnalytics?.course_status_rows) ? safeAnalytics.course_status_rows : []).map((item) => ({
      label: item?.label || "",
      count: Math.max(0, Number(item?.count || 0)),
      color: item?.color || "bg-slate-300",
      pct: Math.max(0, Math.min(100, Number(item?.pct || 0))),
    }));
  }

  const normalizedCourseSearch = courseSearch.trim().toLowerCase();
  const editorPublishedCoursesCount = courseRows.filter((courseItem) => {
    const publishStatus = String(courseItem?.publishStatus || "").toLowerCase();
    return publishStatus === "published";
  }).length;
  const editorDraftCoursesCount = courseRows.filter((courseItem) => {
    const latestVersionStatus = String(courseItem?.latestVersionStatus || "").toLowerCase();
    return latestVersionStatus === "draft";
  }).length;
  const editorArchivedCoursesCount = courseRows.filter((courseItem) => {
    const publishStatus = String(courseItem?.publishStatus || "").toLowerCase();
    return publishStatus === "archived";
  }).length;
  const filteredCourseRows = courseRows.filter((courseItem) => {
    if (!courseItem) return false;
    const title = String(courseItem?.title || "").toLowerCase();
    const category = String(courseItem?.category || "").toLowerCase();
    const skills = Array.isArray(courseItem?.skills)
      ? courseItem.skills.map((item) => String(item || "").toLowerCase()).join(" ")
      : "";
    const progressStatus = String(courseItem?.status || "").toLowerCase();
    const publishStatus = String(courseItem?.publishStatus || "").toLowerCase();
    const isCompleted = isCompletedLmsStatus(progressStatus);
    if (normalizedCourseSearch && !(`${title} ${category} ${skills}`).includes(normalizedCourseSearch)) {
      return false;
    }
    if (isEditorMode) {
      const latestVersionStatus = String(courseItem?.latestVersionStatus || "").toLowerCase();
      const isDraftScopeItem = latestVersionStatus === "draft";
      const isArchivedScopeItem = publishStatus === "archived";
      const isPublishedScopeItem = publishStatus === "published";
      if (editorCourseScope === "drafts") return isDraftScopeItem;
      if (editorCourseScope === "archived") return isArchivedScopeItem;
      return isPublishedScopeItem;
    }
    if (courseFilter === "archived") return publishStatus === "archived";
    if (publishStatus === "archived") return false;
    if (courseFilter === "completed") return isCompleted;
    if (courseFilter === "active") return !isCompleted && progressStatus !== "not_started";
    if (courseFilter === "overdue") return progressStatus === "overdue";
    if (courseFilter === "not_started") return progressStatus === "not_started";
    return true;
  });
  const sortedCourseRows = [...filteredCourseRows].sort((left, right) => {
    if (courseSortBy === "deadline_asc") return compareCoursesByDeadlineAsc(left, right);
    if (courseSortBy === "deadline_desc") return compareCoursesByDeadlineDesc(left, right);
    return compareCourseTitles(left, right);
  });
  const groupedCourseRows = Array.from(
    sortedCourseRows.reduce((acc, courseItem) => {
      const categoryLabel = String(courseItem?.category || "Без категории").trim() || "Без категории";
      const bucket = acc.get(categoryLabel) || [];
      bucket.push(courseItem);
      acc.set(categoryLabel, bucket);
      return acc;
    }, new Map())
  )
    .sort((left, right) => String(left[0] || "").localeCompare(String(right[0] || ""), "ru"))
    .map(([category, items]) => ({
      category,
      items: [...items].sort((left, right) => {
        if (courseSortBy === "deadline_asc") return compareCoursesByDeadlineAsc(left, right);
        if (courseSortBy === "deadline_desc") return compareCoursesByDeadlineDesc(left, right);
        return compareCourseTitles(left, right);
      }),
    }));
  const visibleGroupedCourseRows = courseSortBy === "title"
    ? groupedCourseRows
    : [{
      category: courseSortBy === "deadline_desc" ? "По дедлайну: дальние" : "По дедлайну: ближайшие",
      items: sortedCourseRows,
    }];

  const normalizedEmployeeSearch = employeeSearch.trim().toLowerCase();
  const departmentOptions = Array.from(
    new Set(employeeRows.map((item) => String(item?.dept || "—").trim() || "—"))
  ).sort((a, b) => a.localeCompare(b, "ru"));

  const filteredEmployeeRows = employeeRows.filter((employee) => {
    const dept = String(employee?.dept || "—").trim() || "—";
    if (employeeDeptFilter !== "all" && dept !== employeeDeptFilter) return false;
    if (!normalizedEmployeeSearch) return true;
    const haystack = `${String(employee?.name || "")} ${dept}`.toLowerCase();
    return haystack.includes(normalizedEmployeeSearch);
  });

  useEffect(() => {
    if (tab !== "employees") return;
    if (!filteredEmployeeRows.length) {
      if (selectedEmployeeId != null) setSelectedEmployeeId(null);
      return;
    }
    if (selectedEmployeeId != null) {
      const exists = filteredEmployeeRows.some((item) => Number(item?.id || 0) === Number(selectedEmployeeId || 0));
      if (!exists) {
        setSelectedEmployeeId(null);
      }
    }
  }, [tab, filteredEmployeeRows, selectedEmployeeId]);

  const selectedEmployee = filteredEmployeeRows.find((item) => Number(item?.id || 0) === Number(selectedEmployeeId || 0))
    || null;

  const selectedEmployeeProgressRows = selectedEmployee
    ? safeProgressRows.filter((row) => Number(row?.user_id || 0) === Number(selectedEmployee.id))
    : [];

  const selectedEmployeeAssignmentsByCourse = new Map();
  selectedEmployeeProgressRows.forEach((row) => {
    const courseId = Number(row?.course_id || 0);
    if (!courseId) return;
    selectedEmployeeAssignmentsByCourse.set(courseId, row);
  });

  const selectedEmployeeAttemptsByAssignment = new Map();
  if (selectedEmployee) {
    safeAttempts.forEach((attempt) => {
      if (Number(attempt?.user_id || 0) !== Number(selectedEmployee.id)) return;
      const assignmentId = Number(attempt?.assignment_id || 0);
      if (!assignmentId) return;
      const prev = selectedEmployeeAttemptsByAssignment.get(assignmentId) || [];
      prev.push(attempt);
      selectedEmployeeAttemptsByAssignment.set(assignmentId, prev);
    });
  }

  const selectedEmployeeCourseAnalyticsRows = selectedEmployeeProgressRows
    .map((row) => {
      const assignmentId = Number(row?.assignment_id || 0);
      const attemptsForAssignment = assignmentId
        ? (selectedEmployeeAttemptsByAssignment.get(assignmentId) || [])
        : [];

      const testsById = new Map();
      attemptsForAssignment.forEach((attempt) => {
        const explicitTestId = Number(attempt?.test_id || 0);
        const testTitle = String(attempt?.test_title || "Тест").trim() || "Тест";
        const testKey = explicitTestId > 0 ? `id:${explicitTestId}` : `title:${testTitle.toLowerCase()}`;
        const scoreRaw = attempt?.score_percent;
        const hasScore = scoreRaw != null && scoreRaw !== "";
        const score = hasScore ? Math.max(0, Math.min(100, Math.round(Number(scoreRaw) || 0))) : null;
        const startedAt = attempt?.started_at ? new Date(attempt.started_at) : null;
        const startedAtMs = startedAt && !Number.isNaN(startedAt.getTime()) ? startedAt.getTime() : 0;
        const explicitFinal = attempt?.is_final === true || attempt?.is_final === 1;
        const inferredFinal = /итог|финал|final/i.test(testTitle);
        const prev = testsById.get(testKey) || {
          key: testKey,
          testId: explicitTestId || null,
          title: testTitle,
          attempts: 0,
          bestScore: null,
          lastScore: null,
          lastAtMs: 0,
          isFinal: false,
          passed: false,
        };
        prev.attempts += 1;
        prev.isFinal = Boolean(prev.isFinal || explicitFinal || inferredFinal);
        if (Boolean(attempt?.passed)) prev.passed = true;
        if (hasScore) {
          prev.bestScore = prev.bestScore == null ? score : Math.max(prev.bestScore, score);
          if (startedAtMs >= prev.lastAtMs) {
            prev.lastAtMs = startedAtMs;
            prev.lastScore = score;
          }
        }
        testsById.set(testKey, prev);
      });

      const tests = Array.from(testsById.values()).sort((a, b) => {
        if (a.isFinal !== b.isFinal) return a.isFinal ? -1 : 1;
        if (a.lastAtMs !== b.lastAtMs) return b.lastAtMs - a.lastAtMs;
        return String(a.title).localeCompare(String(b.title), "ru");
      });
      const scoredTests = tests.filter((item) => item.bestScore != null);
      const avgTestScore = scoredTests.length
        ? Math.round(scoredTests.reduce((sum, item) => sum + Number(item.bestScore || 0), 0) / scoredTests.length)
        : null;
      const finalTest = tests.find((item) => item.isFinal) || null;
      const totalDurationSeconds = attemptsForAssignment.reduce(
        (sum, item) => sum + Math.max(0, Number(item?.duration_seconds || 0)),
        0
      );
      const activeLearningSeconds = Math.max(0, Number(row?.active_learning_seconds || 0));
      const confirmedLearningSeconds = Math.max(0, Number(row?.confirmed_learning_seconds || 0));
      const derivedCompletedAt = attemptsForAssignment
        .map((item) => parseLmsDate(item?.finished_at || item?.started_at))
        .filter(Boolean)
        .sort((a, b) => b.getTime() - a.getTime())[0];
      const completedAt = row?.completed_at || (derivedCompletedAt ? derivedCompletedAt.toISOString() : null);
      const uiStatus = mapAdminProgressRowToUiStatus({ ...row, completed_at: completedAt });

      return {
        rowKey: `${assignmentId || 0}:${Number(row?.course_id || 0)}`,
        assignmentId,
        courseId: Number(row?.course_id || 0),
        title: String(row?.course_title || `Курс #${row?.course_id || "-"}`),
        status: uiStatus,
        assignedAt: row?.assigned_at || null,
        deadline: row?.due_at || null,
        completedAt,
        progress: clampLmsProgress(row?.progress_percent),
        completedLessons: Math.max(0, Number(row?.completed_lessons || 0)),
        totalLessons: Math.max(0, Number(row?.total_lessons || 0)),
        passedTests: Math.max(0, Number(row?.passed_tests || 0)),
        totalTests: Math.max(0, Number(row?.total_tests || 0)),
        passedIntermediateTests: Math.max(0, Number(row?.passed_intermediate_tests || 0)),
        totalIntermediateTests: Math.max(0, Number(row?.total_intermediate_tests || 0)),
        testDuration: formatDurationLabel(totalDurationSeconds),
        learningDuration: formatDurationLabel(activeLearningSeconds),
        learningDurationSeconds: activeLearningSeconds,
        activeLearningDuration: formatDurationLabel(activeLearningSeconds),
        activeLearningSeconds,
        confirmedLearningDuration: formatDurationLabel(confirmedLearningSeconds),
        confirmedLearningSeconds,
        tabHiddenCount: Math.max(0, Number(row?.learning_tab_hidden_count || 0)),
        staleGapCount: Math.max(0, Number(row?.learning_stale_gap_count || 0)),
        sessionCount: Math.max(0, Number(row?.session_count || 0)),
        lastLearningAt: row?.last_learning_at || null,
        avgTestScore,
        finalTestScore: finalTest?.bestScore ?? null,
        tests,
      };
    });
  const sortedSelectedEmployeeCourseAnalyticsRows = [...selectedEmployeeCourseAnalyticsRows].sort((left, right) => {
    if (employeeAssignedCourseSortBy === "deadline_desc") return compareCoursesByDeadlineDesc(left, right);
    if (employeeAssignedCourseSortBy === "title") return compareCourseTitles(left, right);
    return compareCoursesByDeadlineAsc(left, right);
  });

  useEffect(() => {
    setSelectedEmployeeCourseKey(null);
  }, [selectedEmployee?.id]);

  useEffect(() => {
    if (!selectedEmployeeCourseKey) return;
    const exists = sortedSelectedEmployeeCourseAnalyticsRows.some((item) => item?.rowKey === selectedEmployeeCourseKey);
    if (!exists) {
      setSelectedEmployeeCourseKey(null);
    }
  }, [sortedSelectedEmployeeCourseAnalyticsRows, selectedEmployeeCourseKey]);

  const selectedEmployeeCourseItem = sortedSelectedEmployeeCourseAnalyticsRows.find(
    (item) => item?.rowKey === selectedEmployeeCourseKey
  );
  const selectedEmployeeCourseSessionRequest = useMemo(() => {
    if (!selectedEmployeeCourseItem) return null;
    const courseId = Number(selectedEmployeeCourseItem.courseId || 0);
    const userId = Number(selectedEmployee?.id || 0);
    const assignmentId = Number(selectedEmployeeCourseItem.assignmentId || 0);
    if (!courseId || !userId || !assignmentId) return null;
    return {
      courseId,
      userId,
      assignmentId,
      requestKey: `${userId}:${assignmentId}:${courseId}`,
    };
  }, [
    selectedEmployee?.id,
    selectedEmployeeCourseItem?.assignmentId,
    selectedEmployeeCourseItem?.courseId,
  ]);

  useEffect(() => {
    let cancelled = false;
    if (!selectedEmployeeCourseSessionRequest || typeof loadLearningSessions !== "function") {
      setSelectedEmployeeLearningSessions([]);
      setIsLoadingSelectedEmployeeLearningSessions(false);
      return undefined;
    }
    setIsLoadingSelectedEmployeeLearningSessions(true);
    loadLearningSessions({
      courseId: selectedEmployeeCourseSessionRequest.courseId,
      userId: selectedEmployeeCourseSessionRequest.userId,
      assignmentId: selectedEmployeeCourseSessionRequest.assignmentId,
      limit: 20,
    }).then((sessions) => {
      if (cancelled) return;
      setSelectedEmployeeLearningSessions(Array.isArray(sessions) ? sessions : []);
    }).catch(() => {
      if (cancelled) return;
      setSelectedEmployeeLearningSessions([]);
    }).finally(() => {
      if (cancelled) return;
      setIsLoadingSelectedEmployeeLearningSessions(false);
    });
    return () => {
      cancelled = true;
    };
  }, [loadLearningSessions, selectedEmployeeCourseSessionRequest]);
  const selectedEmployeeCourseStatus = selectedEmployeeCourseItem
    ? (statusConfig[selectedEmployeeCourseItem.status] || statusConfig.not_started)
    : null;
  const selectedEmployeeCourseStatusLabel = (() => {
    if (!selectedEmployeeCourseStatus) return "";
    if (!isCompletedLmsStatus(selectedEmployeeCourseItem?.status)) return selectedEmployeeCourseStatus.label;
    const completedAtLabel = formatDateTimeLabel(selectedEmployeeCourseItem?.completedAt);
    return completedAtLabel
      ? `${selectedEmployeeCourseStatus.label}: ${completedAtLabel}`
      : selectedEmployeeCourseStatus.label;
  })();
  const selectedEmployeeCourseDeadlineInfo = selectedEmployeeCourseItem?.deadline
    ? formatDeadlineForStatus(selectedEmployeeCourseItem.deadline, selectedEmployeeCourseItem.status)
    : null;

  const getEmployeeCourseDeadline = (courseId, assignmentRow) => {
    const employeeId = Number(selectedEmployee?.id || 0);
    const courseKey = Number(courseId || 0);
    if (!employeeId || !courseKey) return "";
    const key = `${employeeId}:${courseKey}`;
    if (Object.prototype.hasOwnProperty.call(employeeCourseDeadlines, key)) {
      return employeeCourseDeadlines[key];
    }
    return toDateInputValue(assignmentRow?.due_at || null);
  };

  const handleEmployeeCourseDeadlineChange = (courseId, value) => {
    const employeeId = Number(selectedEmployee?.id || 0);
    const courseKey = Number(courseId || 0);
    if (!employeeId || !courseKey) return;
    const key = `${employeeId}:${courseKey}`;
    setEmployeeCourseDeadlines((prev) => ({ ...prev, [key]: value }));
  };

  const handleAssignCourseForSelectedEmployee = async (courseLike) => {
    if (!selectedEmployee || typeof onAssignCourseToEmployee !== "function") return;
    const courseId = Number(courseLike?.id || 0);
    if (!courseId) return;
    const assignmentRow = selectedEmployeeAssignmentsByCourse.get(courseId);
    const dueDate = getEmployeeCourseDeadline(courseId, assignmentRow);

    setAssigningCourseId(courseId);
    try {
      await onAssignCourseToEmployee({
        courseId,
        userId: selectedEmployee.id,
        dueDate: dueDate || null,
        employeeName: selectedEmployee.name,
        courseTitle: courseLike?.title,
      });
    } finally {
      setAssigningCourseId((prev) => (prev === courseId ? null : prev));
    }
  };

  const handleDeleteCourse = async (courseItem) => {
    if (!canDeleteCourses) return;
    const courseId = Number(courseItem?.id || 0);
    if (!courseId || typeof onDeleteCourse !== "function") return;

    const title = String(courseItem?.title || `Курс #${courseId}`).trim();
    const confirmationText = `Удалить курс «${title}»?\n\nБудут удалены связанные файлы в GCS.`;
    const isConfirmed = typeof window !== "undefined" ? window.confirm(confirmationText) : true;
    if (!isConfirmed) return;

    setDeletingCourseId(courseId);
    try {
      await onDeleteCourse(courseItem);
    } finally {
      setDeletingCourseId((prev) => (prev === courseId ? null : prev));
    }
  };

  const handleArchiveCourse = async (courseItem) => {
    const courseId = Number(courseItem?.id || 0);
    if (!courseId || typeof onArchiveCourse !== "function") return;
    const publishStatus = String(courseItem?.publishStatus || "").toLowerCase();
    if (publishStatus === "archived") return;

    const title = String(courseItem?.title || `Курс #${courseId}`).trim();
    const isConfirmed = window.confirm(`Архивировать курс «${title}»?\n\nКурс пропадёт из LMS у сотрудников.`);
    if (!isConfirmed) return;

    setArchivingCourseId(courseId);
    try {
      await onArchiveCourse(courseItem);
    } finally {
      setArchivingCourseId((prev) => (prev === courseId ? null : prev));
    }
  };

  const handleRestoreCourse = async (courseItem) => {
    const courseId = Number(courseItem?.id || 0);
    if (!courseId || typeof onRestoreCourse !== "function") return;
    const publishStatus = String(courseItem?.publishStatus || "").toLowerCase();
    if (publishStatus !== "archived") return;

    const title = String(courseItem?.title || `Курс #${courseId}`).trim();
    const isConfirmed = window.confirm(`Вернуть курс «${title}» из архива?\n\nКурс снова появится в LMS у сотрудников.`);
    if (!isConfirmed) return;

    setRestoringCourseId(courseId);
    try {
      await onRestoreCourse(courseItem);
    } finally {
      setRestoringCourseId((prev) => (prev === courseId ? null : prev));
    }
  };

  const resolveBuilderOpenOptions = (courseItem) => {
    if (!isEditorMode || editorCourseScope !== "drafts") return {};
    const latestVersionStatus = String(courseItem?.latestVersionStatus || "").toLowerCase();
    if (latestVersionStatus !== "draft") return {};
    const draftVersionId = Number(courseItem?.latestVersionId || courseItem?.latestDraftVersionId || 0) || null;
    return draftVersionId ? { draftVersionId } : {};
  };

  return (
    <div className="lms-shell py-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Панель администратора</h1>
          <p className="text-sm text-slate-500 mt-0.5">Управление курсами и прогрессом сотрудников</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => onOpenBuilder?.()}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white text-sm px-4 py-2.5 rounded-xl font-medium transition-colors shadow-sm shadow-indigo-200"
          >
            <Plus size={15} /> Создать курс
          </button>
          <button className="flex items-center gap-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm px-4 py-2.5 rounded-xl font-medium transition-colors"><Download size={15} /> Экспорт отчёта</button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 mb-8">
        <div className="flex items-center gap-1 bg-slate-100 p-1 rounded-xl">
          {tabs.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)} className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all ${tab === t.id ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}>
              <t.icon size={14} /> {t.label}
            </button>
          ))}
        </div>
        {typeof onMonthChange === "function" && (
          <div className="flex items-center gap-3">
            <MonthPicker
              value={selectedMonth}
              onChange={onMonthChange}
            />
            {loading && (
              <RefreshCw size={18} className="text-indigo-500 animate-spin shrink-0" />
            )}
          </div>
        )}
      </div>

      {tab === "analytics" && (
        isAdminLoading ? (
          <div className="space-y-6">
            <div className="grid grid-cols-4 gap-4">
              {Array.from({ length: 4 }).map((_, idx) => (
                <div key={`admin-analytics-stat-skeleton-${idx}`} className="bg-white rounded-2xl border border-slate-200 p-5 space-y-3">
                  <div className="flex items-center justify-between">
                    <SkeletonBlock className="w-28 h-3.5" />
                    <SkeletonBlock className="w-9 h-9 rounded-xl" />
                  </div>
                  <SkeletonBlock className="w-20 h-7" />
                  <SkeletonBlock className="w-32 h-3" />
                </div>
              ))}
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-3 2xl:grid-cols-12 gap-6">
              <div className="xl:col-span-2 2xl:col-span-8 bg-white rounded-2xl border border-slate-200 p-6 space-y-4">
                <SkeletonBlock className="w-40 h-4" />
                {Array.from({ length: 5 }).map((_, idx) => (
                  <div key={`admin-analytics-progress-skeleton-${idx}`} className="space-y-2">
                    <div className="flex items-center justify-between">
                      <SkeletonBlock className="w-48 h-3.5" />
                      <SkeletonBlock className="w-10 h-3.5" />
                    </div>
                    <SkeletonBlock className="w-full h-2 rounded-full" />
                  </div>
                ))}
              </div>
              <div className="xl:col-span-1 2xl:col-span-4 bg-white rounded-2xl border border-slate-200 p-6 space-y-3">
                <SkeletonBlock className="w-32 h-4" />
                {Array.from({ length: 4 }).map((_, idx) => (
                  <div key={`admin-analytics-status-skeleton-${idx}`} className="flex items-center gap-3">
                    <SkeletonBlock className="w-2.5 h-2.5 rounded-full" />
                    <SkeletonBlock className="flex-1 h-3.5" />
                    <SkeletonBlock className="w-8 h-3.5" />
                    <SkeletonBlock className="w-16 h-1.5 rounded-full" />
                  </div>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-2 2xl:grid-cols-12 gap-6">
              {Array.from({ length: 2 }).map((_, idx) => (
                <div key={`admin-analytics-list-skeleton-${idx}`} className="2xl:col-span-6 bg-white rounded-2xl border border-slate-200 p-6 space-y-3">
                  <div className="flex items-center gap-2 mb-1">
                    <SkeletonBlock className="w-5 h-5 rounded-md" />
                    <SkeletonBlock className="w-44 h-4" />
                  </div>
                  {Array.from({ length: 4 }).map((__, rowIdx) => (
                    <div key={`admin-analytics-list-row-skeleton-${idx}-${rowIdx}`} className="flex items-center gap-3">
                      <SkeletonBlock className="w-8 h-8 rounded-lg" />
                      <div className="flex-1 space-y-1.5">
                        <SkeletonBlock className="w-8/12 h-3.5" />
                        <SkeletonBlock className="w-6/12 h-3" />
                      </div>
                      <SkeletonBlock className="w-16 h-1.5 rounded-full" />
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        ) : (
        <div>
          <div className="grid grid-cols-4 gap-4 mb-8">
            {[
              { label: "Сотрудников обучается", value: employeeRows.length, sub: "активных пользователей", icon: Users, color: "text-indigo-600 bg-indigo-50" },
              { label: "Средний прогресс", value: `${overallProgress}%`, sub: "завершения назначенных", icon: TrendingUp, color: "text-emerald-600 bg-emerald-50" },
              { label: "Средний балл", value: `${avgScore}%`, sub: "по всем тестам", icon: Target, color: "text-violet-600 bg-violet-50" },
              { label: "Просроченных", value: overdueCount, sub: "требуют внимания", icon: AlertCircle, color: "text-red-600 bg-red-50" },
            ].map(k => (
              <div key={k.label} className="bg-white rounded-2xl border border-slate-200 p-5">
                <div className="flex items-center justify-between mb-3"><p className="text-xs text-slate-500 font-medium">{k.label}</p><div className={`w-9 h-9 rounded-xl flex items-center justify-center ${k.color}`}><k.icon size={17} /></div></div>
                <p className="text-2xl font-bold text-slate-900">{k.value}</p>
                <p className="text-xs text-slate-400 mt-1">{k.sub}</p>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-3 2xl:grid-cols-12 gap-6 mb-6">
            {/* Прогресс по курсам */}
            <div className="xl:col-span-2 2xl:col-span-8 bg-white rounded-2xl border border-slate-200 p-6">
              <h3 className="text-sm font-semibold text-slate-900 mb-5">Прогресс по курсам</h3>
              <div className="space-y-4">
                {courseRows.slice(0, 5).map(c => (
                  <div key={c.id}>
                    <div className="flex items-center justify-between mb-1.5">
                      <p className="text-xs font-medium text-slate-700 truncate max-w-xs">{c.title}</p>
                      <span className="text-xs font-semibold text-slate-700 ml-2 flex-shrink-0">{c.progress}%</span>
                    </div>
                    <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                      <div className={`h-full rounded-full ${c.status === "completed" ? "bg-emerald-500" : c.status === "overdue" ? "bg-red-500" : "bg-indigo-500"}`} style={{ width: `${c.progress}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Статусы */}
            <div className="xl:col-span-1 2xl:col-span-4 bg-white rounded-2xl border border-slate-200 p-6">
              <h3 className="text-sm font-semibold text-slate-900 mb-5">Статусы курсов</h3>
              <div className="space-y-3">
                {courseStatusRows.map(s => (
                  <div key={s.label} className="flex items-center gap-3">
                    <div className={`w-2.5 h-2.5 rounded-full ${s.color} flex-shrink-0`} />
                    <p className="text-xs text-slate-600 flex-1">{s.label}</p>
                    <span className="text-xs font-semibold text-slate-700">{s.count}</span>
                    <div className="w-20 h-1.5 bg-slate-100 rounded-full overflow-hidden"><div className={`h-full ${s.color} rounded-full`} style={{ width: `${s.pct}%` }} /></div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Проблемные вопросы (ТЗ 12) */}
          <div className="grid grid-cols-1 xl:grid-cols-2 2xl:grid-cols-12 gap-6">
            <div className="2xl:col-span-6 bg-white rounded-2xl border border-slate-200 p-6">
              <div className="flex items-center gap-2 mb-5">
                <AlertTriangle size={16} className="text-amber-500" />
                <h3 className="text-sm font-semibold text-slate-900">Вопросы с высоким % ошибок</h3>
              </div>
              <div className="space-y-3">
                {failStatsRows.map(s => (
                  <div key={s.questionId} className="flex items-start gap-3">
                    <div className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 text-xs font-bold ${s.failRate >= 60 ? "bg-red-100 text-red-700" : s.failRate >= 40 ? "bg-amber-100 text-amber-700" : "bg-slate-100 text-slate-600"}`}>{s.failRate}%</div>
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium text-slate-800 truncate">{s.text}</p>
                      <p className="text-[10px] text-slate-400 mt-0.5">{s.course}</p>
                    </div>
                    <div className="w-16 h-1.5 bg-slate-100 rounded-full overflow-hidden self-center"><div className={`h-full rounded-full ${s.failRate >= 60 ? "bg-red-500" : "bg-amber-500"}`} style={{ width: `${s.failRate}%` }} /></div>
                  </div>
                ))}
              </div>
            </div>

            {/* Время и попытки */}
            <div className="2xl:col-span-6 bg-white rounded-2xl border border-slate-200 p-6">
              <div className="flex items-center gap-2 mb-5">
                <Clock size={16} className="text-indigo-500" />
                <h3 className="text-sm font-semibold text-slate-900">Время на тесты и попытки</h3>
              </div>
              <div className="space-y-3">
                {employeeRows.map(e => (
                  <div key={e.id} className="flex items-center gap-3">
                    <div className="w-7 h-7 rounded-full bg-gradient-to-br from-indigo-400 to-violet-500 flex items-center justify-center text-white text-[10px] font-semibold flex-shrink-0">{e.name.split(" ").map(w => w[0]).join("").slice(0, 2)}</div>
                    <div className="flex-1 min-w-0"><p className="text-xs font-medium text-slate-800 truncate">{e.name}</p></div>
                    <div className="flex items-center gap-1.5 text-[10px] text-indigo-600"><Clock size={9} />{e.learningTime}</div>
                    <div className="flex items-center gap-1.5 text-[10px] text-slate-500"><Clock size={9} />{e.testTime}</div>
                    <div className={`flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full ${e.attempts >= 12 ? "bg-red-50 text-red-700" : e.attempts >= 8 ? "bg-amber-50 text-amber-700" : "bg-slate-100 text-slate-600"}`}><RefreshCw size={8} />{e.attempts} поп.</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
        )
      )}

      {tab === "employees" && (
        isAdminLoading ? (
          <div className="space-y-4">
            <div className="bg-white rounded-2xl border border-slate-200 p-5 space-y-3">
              <div className="flex items-center gap-3">
                <SkeletonBlock className="h-9 flex-1 max-w-xs" />
                <SkeletonBlock className="w-36 h-9" />
                <SkeletonBlock className="w-24 h-3.5" />
              </div>
            </div>
            <div className="bg-white rounded-2xl border border-slate-200 p-4 space-y-3">
              {Array.from({ length: 8 }).map((_, idx) => (
                <div key={`admin-employees-row-skeleton-${idx}`} className="flex items-center gap-4 border-b border-slate-100 pb-3 last:border-b-0 last:pb-0">
                  <SkeletonBlock className="w-9 h-9 rounded-full flex-shrink-0" />
                  <div className="flex-1 grid grid-cols-9 gap-3 items-center">
                    <SkeletonBlock className="col-span-2 h-3.5" />
                    <SkeletonBlock className="h-3.5" />
                    <SkeletonBlock className="h-3.5" />
                    <SkeletonBlock className="h-3.5" />
                    <SkeletonBlock className="h-3.5" />
                    <SkeletonBlock className="h-3.5" />
                    <SkeletonBlock className="h-3.5" />
                    <SkeletonBlock className="h-3.5" />
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : (
        <div className="space-y-6">
          {!selectedEmployee ? (
            <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden flex flex-col">
            <div className="px-6 py-4 border-b border-slate-100 flex items-center gap-3">
              <div className="relative flex-1 max-w-xs">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  value={employeeSearch}
                  onChange={(event) => setEmployeeSearch(event.target.value)}
                  placeholder="Поиск сотрудников..."
                  className="w-full pl-9 pr-4 py-2 bg-slate-50 border border-slate-200 rounded-xl text-sm placeholder-slate-400 focus:outline-none focus:border-indigo-400 transition-all"
                />
              </div>
              <select
                value={employeeDeptFilter}
                onChange={(event) => setEmployeeDeptFilter(event.target.value)}
                className="px-3 py-2 bg-slate-50 border border-slate-200 rounded-xl text-xs text-slate-600 focus:outline-none"
              >
                <option value="all">Все отделы</option>
                {departmentOptions.map((dept) => (
                  <option key={dept} value={dept}>{dept}</option>
                ))}
              </select>
              <span className="text-xs text-slate-400">{filteredEmployeeRows.length} сотрудников</span>
            </div>
            <div className="max-h-[600px] overflow-y-auto custom-scrollbar w-full">
              <table className="w-full relative">
                <thead className="sticky top-0 z-10 bg-slate-50 shadow-sm">
                  <tr className="border-b border-slate-100">
                    {["Сотрудник", "Отдел", "Назначено", "Завершено", "Ср. балл", "Попытки", "Время тестов", "Просрочено"].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredEmployeeRows.length === 0 && (
                  <tr>
                    <td colSpan={8} className="px-4 py-8 text-center text-sm text-slate-400">
                      Сотрудники по фильтру не найдены
                    </td>
                  </tr>
                )}
                {filteredEmployeeRows.map((e) => {
                  const isSelected = Number(selectedEmployee?.id || 0) === Number(e.id);
                  return (
                    <tr
                      key={e.id}
                      onClick={() => {
                        setSelectedEmployeeId(Number(e.id) || null);
                        setSelectedEmployeeCourseKey(null);
                      }}
                      className={`border-b border-slate-50 transition-colors cursor-pointer ${isSelected ? "bg-indigo-50/70" : "hover:bg-slate-50"}`}
                    >
                      <td className="px-4 py-4">
                        <div className="flex items-center gap-3">
                          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-400 to-violet-500 flex items-center justify-center text-white text-xs font-semibold flex-shrink-0">
                            {String(e.name || "").split(" ").map((w) => w[0]).join("").slice(0, 2)}
                          </div>
                          <div className="min-w-0">
                            <span className="text-sm font-medium text-slate-800 block truncate">{e.name}</span>
                            <span className="text-[11px] text-slate-400">{e.lastActive || "—"}</span>
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-4"><span className="text-xs bg-slate-100 text-slate-600 px-2.5 py-1 rounded-full font-medium">{e.dept}</span></td>
                      <td className="px-4 py-4"><span className="text-sm font-semibold text-slate-700">{e.courses}</span></td>
                      <td className="px-4 py-4">
                        <div className="flex items-center gap-2">
                          <div className="w-16 h-1.5 bg-slate-100 rounded-full overflow-hidden"><div className="h-full bg-emerald-500 rounded-full" style={{ width: `${e.courses > 0 ? (e.completed / e.courses) * 100 : 0}%` }} /></div>
                          <span className="text-xs font-semibold text-slate-700">{e.completed}/{e.courses}</span>
                        </div>
                      </td>
                      <td className="px-4 py-4"><span className={`text-xs font-bold ${e.avgScore >= 90 ? "text-emerald-600" : e.avgScore >= 75 ? "text-blue-600" : "text-red-600"}`}>{e.avgScore}%</span></td>
                      <td className="px-4 py-4"><span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${e.attempts >= 12 ? "bg-red-50 text-red-700" : e.attempts >= 8 ? "bg-amber-50 text-amber-700" : "bg-slate-100 text-slate-600"}`}>{e.attempts}</span></td>
                      <td className="px-4 py-4"><span className="text-xs text-slate-500">{e.testTime}</span></td>
                      <td className="px-4 py-4">
                        {e.overdue > 0 ? <span className="text-xs font-semibold text-red-600 bg-red-50 px-2.5 py-1 rounded-full">{e.overdue} просрочено</span> : <span className="text-xs text-emerald-600 font-medium">В срок</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
        ) : (
          <div className="space-y-6">
            <div>
              <button
                type="button"
                onClick={() => {
                  setSelectedEmployeeId(null);
                  setSelectedEmployeeCourseKey(null);
                  setEmployeeAssignedCourseSearch("");
                }}
                className="flex items-center gap-2 text-sm font-medium text-slate-500 hover:text-indigo-600 transition-colors bg-white px-4 py-2 border border-slate-200 rounded-xl w-fit shadow-sm"
              >
                <ArrowLeft size={16} /> Назад к списку сотрудников
              </button>
            </div>

            <div className="bg-white rounded-2xl border border-slate-200 p-6 flex flex-col md:flex-row items-start md:items-center justify-between gap-6 shadow-sm">
              <div className="flex items-center gap-4">
                <div className="w-14 h-14 rounded-full bg-gradient-to-br from-indigo-400 to-violet-500 flex items-center justify-center text-white text-xl font-semibold flex-shrink-0">
                  {String(selectedEmployee.name || "").split(" ").map((w) => w[0]).join("").slice(0, 2)}
                </div>
                <div>
                  <h3 className="text-xl font-bold text-slate-900">{selectedEmployee.name}</h3>
                  <p className="text-sm text-slate-500 mt-0.5">{selectedEmployee.dept} • Активность: {selectedEmployee.lastActive || "—"}</p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2 md:justify-end">
                <span className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-2 rounded-xl bg-slate-50 border border-slate-100 text-slate-700"><BookOpen size={14} className="text-indigo-500" /> {selectedEmployee.courses} курсов</span>
                <span className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-2 rounded-xl bg-emerald-50 border border-emerald-100 text-emerald-800"><CheckCircle size={14} className="text-emerald-600" /> {selectedEmployee.completed} завершено</span>
                <span className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-2 rounded-xl bg-violet-50 border border-violet-100 text-violet-800"><Target size={14} className="text-violet-600" /> {selectedEmployee.avgScore}% ср. балл</span>
                <button 
                  onClick={() => setIsAssignCourseModalOpen(true)}
                  className="inline-flex items-center gap-1.5 text-xs font-bold px-4 py-2 rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 transition-colors shadow-sm ml-2"
                >
                  <Plus size={14} /> Назначить курс
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-12 gap-6 items-start">
              <div className="xl:col-span-5 flex flex-col gap-6 sticky top-6">
                <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden flex flex-col shadow-sm">
                  <div className="p-5 border-b border-slate-100 space-y-3">
                    <h3 className="text-sm font-semibold text-slate-900">Назначенные курсы</h3>
                    <div className="relative">
                      <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                      <input
                        value={employeeAssignedCourseSearch}
                        onChange={(e) => setEmployeeAssignedCourseSearch(e.target.value)}
                        placeholder="Поиск по курсам..."
                        className="w-full pl-9 pr-3 py-1.5 bg-slate-50 border border-slate-200 rounded-lg text-xs placeholder-slate-400 focus:outline-none focus:border-indigo-400 transition-all"
                      />
                    </div>
                    <select
                      value={employeeAssignedCourseSortBy}
                      onChange={(event) => setEmployeeAssignedCourseSortBy(event.target.value)}
                      className="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-1.5 text-xs text-slate-600 focus:outline-none focus:border-indigo-400"
                    >
                      <option value="deadline_asc">По дедлайну: ближайшие</option>
                      <option value="deadline_desc">По дедлайну: дальние</option>
                      <option value="title">По названию</option>
                    </select>
                  </div>
                  <div className="h-[600px] xl:h-[750px] flex-1 overflow-y-auto p-5 space-y-3 w-full bg-slate-50/50 custom-scrollbar">
                    {(() => {
                      const lowerSearch = employeeAssignedCourseSearch.trim().toLowerCase();
                      const filteredCourses = sortedSelectedEmployeeCourseAnalyticsRows.filter(c => c.title.toLowerCase().includes(lowerSearch));
                      
                      if (sortedSelectedEmployeeCourseAnalyticsRows.length === 0) {
                        return (
                          <div className="rounded-xl border border-dashed border-slate-200 bg-white px-4 py-8 text-sm text-slate-500 text-center shadow-sm">
                            По сотруднику пока нет назначенных курсов
                          </div>
                        );
                      }
                      
                      if (filteredCourses.length === 0) {
                        return (
                          <div className="rounded-xl border border-dashed border-slate-200 bg-white px-4 py-8 text-sm text-slate-500 text-center shadow-sm">
                            По запросу ничего не найдено
                          </div>
                        );
                      }

                      return filteredCourses.map((courseItem) => {
                      const st = statusConfig[courseItem.status] || statusConfig.not_started;
                      const deadlineInfo = courseItem.deadline ? formatDeadlineForStatus(courseItem.deadline, courseItem.status) : null;
                      const isSelectedCourse = selectedEmployeeCourseKey === courseItem.rowKey;
                      return (
                        <button
                          key={courseItem.rowKey}
                          type="button"
                          onClick={() => setSelectedEmployeeCourseKey(courseItem.rowKey)}
                          className={`w-full text-left rounded-xl border p-4 transition-all shadow-sm ${isSelectedCourse ? "border-indigo-400 bg-indigo-50/60 ring-2 ring-indigo-100" : "border-slate-200 bg-white hover:border-indigo-300"}`}
                        >
                          <div className="flex items-start justify-between gap-3 mb-2">
                            <p className={`text-sm font-semibold truncate flex-1 ${isSelectedCourse ? 'text-indigo-900' : 'text-slate-800'}`}>{courseItem.title}</p>
                            <div className="text-right flex-shrink-0">
                              <p className="text-sm font-bold text-slate-800">{courseItem.progress}%</p>
                            </div>
                          </div>
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-md ${st.bg} ${st.text}`}>{st.label}</span>
                            {deadlineInfo && (
                              <span className={`text-[10px] font-medium px-2 py-0.5 rounded-md ${deadlineInfo.overdue ? "bg-red-50 text-red-600" : deadlineInfo.urgent ? "bg-amber-50 text-amber-700" : "bg-slate-100 text-slate-600"}`}>
                                Дедлайн: {deadlineInfo.label}
                              </span>
                            )}
                          </div>
                        </button>
                      );
                    })})()}
                  </div>
                </div>


              </div>

              <div className="xl:col-span-7 flex flex-col h-full min-h-[400px]">
                {!selectedEmployeeCourseItem ? (
                  <div className="bg-white rounded-2xl border border-slate-200 p-8 h-full min-h-[500px] flex items-center justify-center shadow-sm">
                    <div className="text-center max-w-sm">
                      <div className="w-16 h-16 bg-slate-50 rounded-full flex items-center justify-center mx-auto mb-4 border border-slate-100 shadow-sm">
                        <BarChart2 size={24} className="text-indigo-300" />
                      </div>
                      <h4 className="text-sm font-semibold text-slate-700 mb-1.5">Детализация курса</h4>
                      <p className="text-xs text-slate-500 leading-relaxed">
                        Для просмотра детальной статистики и истории прохождения тестов, выберите курс из списка слева.
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden flex flex-col h-full shadow-sm">
                    <div className="p-6 border-b border-slate-100 bg-white">
                      <p className="text-[10px] font-semibold text-indigo-600 uppercase tracking-widest mb-1.5">Аналитика курса</p>
                      <h3 className="text-lg font-bold text-slate-900 leading-tight mb-3">{selectedEmployeeCourseItem.title}</h3>
                      <div className="flex items-center gap-2 flex-wrap">
                        {selectedEmployeeCourseStatus && (
                          <span className={`text-xs font-semibold px-2.5 py-1 rounded-md ${selectedEmployeeCourseStatus.bg} ${selectedEmployeeCourseStatus.text}`}>
                            {selectedEmployeeCourseStatusLabel}
                          </span>
                        )}
                        {selectedEmployeeCourseDeadlineInfo && (
                          <span className={`text-xs font-medium px-2.5 py-1 rounded-md ${selectedEmployeeCourseDeadlineInfo.overdue ? "bg-red-50 text-red-700 border border-red-100" : selectedEmployeeCourseDeadlineInfo.urgent ? "bg-amber-50 text-amber-800 border border-amber-100" : "bg-slate-50 text-slate-600 border border-slate-200"}`}>
                            Дедлайн: {selectedEmployeeCourseDeadlineInfo.label}
                          </span>
                        )}
                        {selectedEmployeeCourseItem.assignedAt && (
                          <span className="text-xs font-medium px-2.5 py-1 rounded-md bg-blue-50 text-blue-700 border border-blue-100">
                            Назначен: {formatDateTimeLabel(selectedEmployeeCourseItem.assignedAt)}
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="p-6 space-y-6 bg-slate-50/50 flex-1 overflow-y-auto custom-scrollbar">
                      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
                        <div className="bg-white rounded-xl border border-slate-100 p-4 shadow-sm transition-all hover:border-slate-200">
                          <p className="text-[10px] uppercase font-semibold text-slate-400 mb-1.5">Уроки курса</p>
                          <p className="text-sm font-bold text-slate-800">{selectedEmployeeCourseItem.completedLessons} <span className="text-slate-400 font-medium text-xs">/ {selectedEmployeeCourseItem.totalLessons} зав.</span></p>
                        </div>
                        <div className="bg-white rounded-xl border border-slate-100 p-4 shadow-sm transition-all hover:border-slate-200">
                          <p className="text-[10px] uppercase font-semibold text-slate-400 mb-1.5">Все тесты</p>
                          <p className="text-sm font-bold text-slate-800">{selectedEmployeeCourseItem.passedTests} <span className="text-slate-400 font-medium text-xs">/ {selectedEmployeeCourseItem.totalTests} пройд.</span></p>
                        </div>
                        <div className="bg-white rounded-xl border border-slate-100 p-4 shadow-sm transition-all hover:border-slate-200">
                          <p className="text-[10px] uppercase font-semibold text-slate-400 mb-1.5">Промежуточные</p>
                          <p className="text-sm font-bold text-slate-800">{selectedEmployeeCourseItem.passedIntermediateTests} <span className="text-slate-400 font-medium text-xs">/ {selectedEmployeeCourseItem.totalIntermediateTests} пройд.</span></p>
                        </div>
                        <div className="bg-white rounded-xl border border-slate-100 p-4 shadow-sm transition-all hover:border-slate-200">
                          <p className="text-[10px] uppercase font-semibold text-slate-400 mb-1.5">Время на тесты</p>
                          <p className="text-sm font-bold text-slate-800">{selectedEmployeeCourseItem.testDuration}</p>
                        </div>
                        <div className="bg-white rounded-xl border border-slate-100 p-4 shadow-sm transition-all hover:border-slate-200">
                          <p className="text-[10px] uppercase font-semibold text-slate-400 mb-1.5">Real learning time</p>
                          <p className="text-sm font-bold text-slate-800">{selectedEmployeeCourseItem.learningDuration}</p>
                        </div>
                        <div className="bg-white rounded-xl border border-slate-100 p-4 shadow-sm transition-all hover:border-slate-200">
                          <p className="text-[10px] uppercase font-semibold text-slate-400 mb-1.5">Confirmed content</p>
                          <p className="text-sm font-bold text-slate-800">{selectedEmployeeCourseItem.confirmedLearningDuration}</p>
                        </div>
                        <div className="bg-white rounded-xl border border-slate-100 p-4 shadow-sm transition-all hover:border-slate-200">
                          <p className="text-[10px] uppercase font-semibold text-slate-400 mb-1.5">Sessions</p>
                          <p className="text-sm font-bold text-slate-800">{selectedEmployeeCourseItem.sessionCount}</p>
                        </div>
                        <div className="bg-white rounded-xl border border-slate-100 p-4 shadow-sm transition-all hover:border-slate-200">
                          <p className="text-[10px] uppercase font-semibold text-slate-400 mb-1.5">Hidden tabs</p>
                          <p className="text-sm font-bold text-slate-800">{selectedEmployeeCourseItem.tabHiddenCount}</p>
                        </div>
                        <div className="bg-white rounded-xl border border-slate-100 p-4 shadow-sm transition-all hover:border-slate-200">
                          <p className="text-[10px] uppercase font-semibold text-slate-400 mb-1.5">Stale gaps</p>
                          <p className="text-sm font-bold text-slate-800">{selectedEmployeeCourseItem.staleGapCount}</p>
                        </div>
                        <div className="bg-white rounded-xl border border-slate-100 p-4 shadow-sm transition-all hover:border-slate-200">
                          <p className="text-[10px] uppercase font-semibold text-slate-400 mb-1.5">Ср. балл</p>
                          <p className={`text-sm font-bold ${selectedEmployeeCourseItem.avgTestScore >= 80 ? 'text-emerald-600' : 'text-slate-800'}`}>{selectedEmployeeCourseItem.avgTestScore == null ? "—" : `${selectedEmployeeCourseItem.avgTestScore}%`}</p>
                        </div>
                        <div className="bg-white rounded-xl border border-slate-100 p-4 shadow-sm transition-all hover:border-slate-200">
                          <p className="text-[10px] uppercase font-semibold text-slate-400 mb-1.5">Итоговый тест</p>
                          <p className={`text-sm font-bold ${selectedEmployeeCourseItem.finalTestScore >= 80 ? 'text-emerald-600' : 'text-slate-800'}`}>{selectedEmployeeCourseItem.finalTestScore == null ? "—" : `${selectedEmployeeCourseItem.finalTestScore}%`}</p>
                        </div>
                      </div>

                      <div>
                        <h4 className="text-sm font-semibold text-slate-900 mb-3.5 flex items-center gap-2">
                          <Clock size={15} className="text-indigo-500" />
                          Learning Sessions
                        </h4>
                        {isLoadingSelectedEmployeeLearningSessions ? (
                          <div className="rounded-xl border border-slate-100 bg-white px-4 py-6 text-sm text-slate-500 shadow-sm">
                            Loading sessions...
                          </div>
                        ) : selectedEmployeeLearningSessions.length === 0 ? (
                          <div className="rounded-xl border border-slate-100 bg-white px-4 py-6 text-sm text-slate-500 shadow-sm">
                            No learning sessions recorded yet for this assignment.
                          </div>
                        ) : (
                          <div className="space-y-3 mb-6">
                            {selectedEmployeeLearningSessions.map((sessionItem) => (
                              <div key={sessionItem.session_id} className="bg-white rounded-xl border border-slate-100 p-4 flex flex-col lg:flex-row lg:items-center justify-between gap-4 shadow-sm">
                                <div className="min-w-0">
                                  <p className="text-sm font-semibold text-slate-900 truncate">{sessionItem.lesson_title || `Lesson #${sessionItem.lesson_id}`}</p>
                                  <p className="text-xs text-slate-500 mt-1">
                                    {formatDateTimeLabel(sessionItem.started_at)} {sessionItem.ended_at ? `→ ${formatDateTimeLabel(sessionItem.ended_at)}` : "• active"}
                                  </p>
                                </div>
                                <div className="flex flex-wrap items-center gap-2 lg:justify-end">
                                  <span className="text-xs font-semibold px-2.5 py-1 rounded-md bg-indigo-50 text-indigo-700 border border-indigo-100">
                                    Confirmed: {formatDurationLabel(sessionItem.confirmed_seconds)}
                                  </span>
                                  <span className="text-xs font-semibold px-2.5 py-1 rounded-md bg-slate-50 text-slate-700 border border-slate-200">
                                    Active: {formatDurationLabel(sessionItem.active_seconds)}
                                  </span>
                                  <span className="text-xs font-semibold px-2.5 py-1 rounded-md bg-amber-50 text-amber-700 border border-amber-100">
                                    Hidden: {Math.max(0, Number(sessionItem.tab_hidden_count || 0))}
                                  </span>
                                  <span className="text-xs font-semibold px-2.5 py-1 rounded-md bg-rose-50 text-rose-700 border border-rose-100">
                                    Gaps: {Math.max(0, Number(sessionItem.stale_gap_count || 0))}
                                  </span>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>

                      <div>
                        <h4 className="text-sm font-semibold text-slate-900 mb-3.5 flex items-center gap-2">
                          <HelpCircle size={15} className="text-indigo-500" />
                          История прохождения тестов
                        </h4>
                        {selectedEmployeeCourseItem.tests.length === 0 ? (
                          <div className="rounded-xl border border-slate-100 bg-white px-4 py-8 text-center text-sm text-slate-500 shadow-sm">
                            Сотрудник пока не приступал к тестам в этом курсе
                          </div>
                        ) : (
                          <div className="space-y-3">
                            {[...selectedEmployeeCourseItem.tests].reverse().map((testItem) => (
                              <div key={testItem.key} className="bg-white rounded-xl border border-slate-100 p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-4 shadow-sm overflow-hidden relative">
                                {testItem.passed && <div className="absolute top-0 left-0 w-1 h-full bg-emerald-500"></div>}
                                {!testItem.passed && testItem.attempts > 0 && <div className="absolute top-0 left-0 w-1 h-full bg-amber-500"></div>}
                                
                                <div className="flex-1 min-w-0 pl-1">
                                  <div className="flex items-center gap-2 mb-1.5">
                                    <p className="text-sm font-semibold text-slate-900 truncate">{testItem.title}</p>
                                    {testItem.isFinal && <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-violet-100 text-violet-700 uppercase tracking-widest flex-shrink-0">Итоговый</span>}
                                  </div>
                                  <div className="flex items-center gap-3 text-xs">
                                    <span className="text-slate-500">Попыток: <strong className="text-slate-700">{testItem.attempts}</strong></span>
                                    <span className="text-slate-300">•</span>
                                    <span className={testItem.passed ? "text-emerald-600 font-semibold" : "text-amber-600 font-semibold"}>
                                      {testItem.passed ? "Пройден" : "Не пройден"}
                                    </span>
                                  </div>
                                </div>
                                <div className="flex items-center gap-5 bg-slate-50 rounded-lg px-4 py-2.5 border border-slate-100">
                                  <div>
                                    <p className="text-[10px] text-slate-400 mb-0.5 uppercase tracking-wider">Последний</p>
                                    <p className="text-sm font-semibold text-slate-800">{testItem.lastScore == null ? "—" : `${testItem.lastScore}%`}</p>
                                  </div>
                                  <div className="h-7 w-px bg-slate-200"></div>
                                  <div>
                                    <p className="text-[10px] text-slate-400 mb-0.5 uppercase tracking-wider">Лучший</p>
                                    <p className="text-sm font-bold text-indigo-600">{testItem.bestScore == null ? "—" : `${testItem.bestScore}%`}</p>
                                  </div>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                  )}
                </div>
              </div>


            {isAssignCourseModalOpen && (
              <div className="fixed inset-0 z-[100] flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-sm transition-all duration-200" onClick={() => setIsAssignCourseModalOpen(false)}>
                <div className="bg-white rounded-2xl border border-slate-200 shadow-xl w-full max-w-md max-h-[90vh] flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-200" onClick={(e) => e.stopPropagation()}>
                  <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-slate-50 relative z-10">
                    <div>
                      <h3 className="text-sm font-bold text-slate-900">Назначить курс</h3>
                      <p className="text-[11px] text-slate-500 mt-0.5">Обучение для: {selectedEmployee.name}</p>
                    </div>
                    <button onClick={() => setIsAssignCourseModalOpen(false)} className="text-slate-400 hover:text-slate-600 transition-colors bg-white rounded-lg p-2 border border-slate-200 hover:bg-slate-50 shadow-sm">
                      <X size={16} />
                    </button>
                  </div>
                  <div className="p-6 overflow-y-auto custom-scrollbar bg-slate-50/50 flex-1 space-y-3 relative z-0">
                    {assignableAdminCourses.length === 0 && (
                      <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 py-6 text-xs text-slate-500 text-center">
                        Нет опубликованных курсов для назначения
                      </div>
                    )}
                    {assignableAdminCourses.map((courseItem) => {
                      const courseId = Number(courseItem?.id || 0);
                      const assignmentRow = selectedEmployeeAssignmentsByCourse.get(courseId) || null;
                      const assignmentStatus = assignmentRow ? (statusConfig[mapAdminProgressRowToUiStatus(assignmentRow)] || statusConfig.not_started) : null;
                      const deadlineValue = getEmployeeCourseDeadline(courseId, assignmentRow);
                      const isAssigning = assigningCourseId === courseId;
                      return (
                        <div key={courseId} className="rounded-xl border border-slate-100 p-3 bg-white hover:bg-slate-50 transition-colors shadow-sm">
                          <div className="flex items-start justify-between gap-3">
                            <p className="text-xs font-semibold text-slate-800 leading-tight flex-1">{courseItem?.title || `Курс #${courseId}`}</p>
                            {assignmentStatus && <span className={`text-[9px] px-1.5 py-0.5 rounded-md font-semibold flex-shrink-0 ${assignmentStatus.bg} ${assignmentStatus.text}`}>{assignmentStatus.label}</span>}
                          </div>
                          <div className="mt-3 flex items-center gap-2">
                            <input
                              type="date"
                              value={deadlineValue}
                              onChange={(event) => handleEmployeeCourseDeadlineChange(courseId, event.target.value)}
                              className="w-full max-w-[130px] px-2.5 py-1.5 bg-slate-50 border border-slate-200 rounded-lg text-[11px] text-slate-700 focus:outline-none focus:border-indigo-400 transition-all"
                            />
                            <button
                              onClick={() => { void handleAssignCourseForSelectedEmployee(courseItem); }}
                              disabled={isAssigning || !courseId}
                              className="flex-1 px-2.5 py-1.5 bg-indigo-600 hover:bg-indigo-700 disabled:bg-slate-300 disabled:cursor-not-allowed text-white text-[11px] font-semibold rounded-lg transition-colors flex items-center justify-center gap-1.5"
                            >
                              {isAssigning ? "..." : (assignmentRow ? "Обновить" : "Назначить")}
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}
            </div>
          )}
        </div>
        )
      )}

      {tab === "courses" && (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative flex-1 min-w-[240px] max-w-xl">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                value={courseSearch}
                onChange={(event) => setCourseSearch(event.target.value)}
                placeholder="Поиск по названию, категории, навыкам..."
                className="w-full pl-9 pr-4 py-2.5 bg-white border border-slate-200 rounded-xl text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100 transition-all"
              />
            </div>
            {isEditorMode ? (
              <div className="flex items-center gap-2">
                {[
                  { id: "courses", label: "Курсы", count: editorPublishedCoursesCount },
                  { id: "drafts", label: "Черновики", count: editorDraftCoursesCount },
                  { id: "archived", label: "Архив", count: editorArchivedCoursesCount },
                ].map((scopeItem) => (
                  <button
                    key={scopeItem.id}
                    onClick={() => setEditorCourseScope(scopeItem.id)}
                    className={`px-3 py-2 text-xs rounded-xl border font-medium transition-all ${editorCourseScope === scopeItem.id ? "bg-indigo-600 text-white border-indigo-600" : "bg-white text-slate-600 border-slate-200 hover:border-slate-300"}`}
                  >
                    {scopeItem.label} ({scopeItem.count})
                  </button>
                ))}
              </div>
            ) : (
              <div className="flex items-center gap-2">
                {[
                  { id: "all", label: "Все" },
                  { id: "active", label: "В процессе" },
                  { id: "completed", label: "Завершены" },
                  { id: "overdue", label: "Просрочены" },
                  { id: "not_started", label: "Не начаты" },
                  { id: "archived", label: "Архив" },
                ].map((filterItem) => (
                  <button
                    key={filterItem.id}
                    onClick={() => setCourseFilter(filterItem.id)}
                    className={`px-3 py-2 text-xs rounded-xl border font-medium transition-all ${courseFilter === filterItem.id ? "bg-indigo-600 text-white border-indigo-600" : "bg-white text-slate-600 border-slate-200 hover:border-slate-300"}`}
                  >
                    {filterItem.label}
                  </button>
                ))}
              </div>
            )}
            <select
              value={courseSortBy}
              onChange={(event) => setCourseSortBy(event.target.value)}
              className="text-xs bg-white border border-slate-200 rounded-xl px-3 py-2 text-slate-600 focus:outline-none focus:border-indigo-400"
            >
              <option value="title">По названию</option>
              <option value="deadline_asc">По дедлайну: ближайшие</option>
              <option value="deadline_desc">По дедлайну: дальние</option>
            </select>
            <div className="flex items-center gap-1 bg-white border border-slate-200 rounded-xl p-1 ml-auto">
              <button onClick={() => setCourseGridView(true)} className={`p-1.5 rounded-lg transition-colors ${courseGridView ? "bg-slate-100 text-slate-800" : "text-slate-400 hover:text-slate-600"}`}><LayoutGrid size={14} /></button>
              <button onClick={() => setCourseGridView(false)} className={`p-1.5 rounded-lg transition-colors ${!courseGridView ? "bg-slate-100 text-slate-800" : "text-slate-400 hover:text-slate-600"}`}><List size={14} /></button>
            </div>
          </div>

          {isAdminLoading ? (
            courseGridView ? (
              <div className="grid [grid-template-columns:repeat(auto-fill,minmax(290px,1fr))] gap-5">
                {Array.from({ length: 6 }).map((_, idx) => (
                  <div key={`admin-course-card-skeleton-${idx}`} className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
                    <SkeletonBlock className="h-32 w-full rounded-none" />
                    <div className="p-5 space-y-3">
                      <SkeletonBlock className="w-20 h-3" />
                      <SkeletonBlock className="w-10/12 h-4" />
                      <SkeletonBlock className="w-7/12 h-3" />
                      <SkeletonBlock className="w-full h-1.5 rounded-full" />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="space-y-3">
                {Array.from({ length: 6 }).map((_, idx) => (
                  <div key={`admin-course-row-skeleton-${idx}`} className="bg-white rounded-2xl border border-slate-200 p-5 flex items-center gap-5">
                    <SkeletonBlock className="w-12 h-12 rounded-xl flex-shrink-0" />
                    <div className="flex-1 space-y-2">
                      <SkeletonBlock className="w-6/12 h-4" />
                      <SkeletonBlock className="w-8/12 h-3.5" />
                    </div>
                  </div>
                ))}
              </div>
            )
          ) : filteredCourseRows.length === 0 ? (
            <div className="text-center py-16 text-slate-400">
              <BookOpen size={36} className="mx-auto mb-3 opacity-30" />
              <p className="text-sm">По заданным фильтрам курсы не найдены</p>
            </div>
          ) : (
            <div className="space-y-6">
              {visibleGroupedCourseRows.map((groupItem) => (
                <section key={groupItem.category} className="space-y-3">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="text-sm font-semibold text-slate-800">{groupItem.category}</h3>
                    <span className="text-[11px] font-medium text-slate-500 bg-white border border-slate-200 px-2.5 py-1 rounded-full">
                      {groupItem.items.length}
                    </span>
                  </div>
                  {courseGridView ? (
                    <div className="grid [grid-template-columns:repeat(auto-fill,minmax(290px,1fr))] gap-5">
                      {groupItem.items.map((courseItem) => (
                        <CourseCard
                          key={courseItem.id}
                          course={courseItem}
                          managerMode
                          busy={busyCourseId === courseItem.id}
                          onClick={() => onOpenCourse?.(courseItem)}
                          actions={(
                            <>
                              <button
                                onClick={() => onOpenBuilder?.(courseItem.id, resolveBuilderOpenOptions(courseItem))}
                                className="inline-flex items-center justify-center w-8 h-8 rounded-lg bg-white/80 text-slate-500 hover:text-indigo-600 hover:bg-indigo-50 border border-slate-200 transition-colors"
                                title="Редактировать и выпустить новую версию"
                              >
                                <Edit size={14} />
                              </button>
                              {String(courseItem?.publishStatus || "").toLowerCase() === "archived"
                                ? (typeof onRestoreCourse === "function" && (
                                  <button
                                    onClick={() => { void handleRestoreCourse(courseItem); }}
                                    disabled={restoringCourseId === courseItem.id}
                                    title={restoringCourseId === courseItem.id ? "Восстанавливаем..." : "Восстановить курс из архива"}
                                    className="inline-flex items-center justify-center w-8 h-8 rounded-lg bg-white/80 text-slate-500 hover:text-emerald-700 hover:bg-emerald-50 border border-slate-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                  >
                                    <RotateCcw size={14} />
                                  </button>
                                ))
                                : (typeof onArchiveCourse === "function" && (
                                  <button
                                    onClick={() => { void handleArchiveCourse(courseItem); }}
                                    disabled={archivingCourseId === courseItem.id}
                                    title={archivingCourseId === courseItem.id ? "Архивируем..." : "Архивировать курс"}
                                    className="inline-flex items-center justify-center w-8 h-8 rounded-lg bg-white/80 text-slate-500 hover:text-amber-700 hover:bg-amber-50 border border-slate-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                  >
                                    <Archive size={14} />
                                  </button>
                                ))}
                              {canDeleteCourses && (
                                <button
                                  onClick={() => { void handleDeleteCourse(courseItem); }}
                                  disabled={deletingCourseId === courseItem.id}
                                  title={deletingCourseId === courseItem.id ? "Удаляем..." : "Удалить курс"}
                                  className="inline-flex items-center justify-center w-8 h-8 rounded-lg bg-white/80 text-slate-500 hover:text-red-600 hover:bg-red-50 border border-slate-200 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                  {deletingCourseId === courseItem.id ? <Clock size={14} /> : <Trash2 size={14} />}
                                </button>
                              )}
                            </>
                          )}
                        />
                      ))}
                    </div>
                  ) : (
                    <div className="flex flex-col gap-3">
                      {groupItem.items.map((courseItem) => (
                        <CourseListItem
                          key={courseItem.id}
                          course={courseItem}
                          managerMode
                          busy={busyCourseId === courseItem.id}
                          onClick={() => onOpenCourse?.(courseItem)}
                          actions={(
                            <>
                              <button
                                onClick={() => onOpenBuilder?.(courseItem.id, resolveBuilderOpenOptions(courseItem))}
                                className="p-2 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-colors"
                                title="Редактировать и выпустить новую версию"
                              >
                                <Edit size={15} />
                              </button>
                              {String(courseItem?.publishStatus || "").toLowerCase() === "archived"
                                ? (typeof onRestoreCourse === "function" && (
                                  <button
                                    onClick={() => { void handleRestoreCourse(courseItem); }}
                                    disabled={restoringCourseId === courseItem.id}
                                    title={restoringCourseId === courseItem.id ? "Восстанавливаем..." : "Восстановить курс из архива"}
                                    className="p-2 text-slate-400 hover:text-emerald-700 hover:bg-emerald-50 rounded-xl transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                  >
                                    <RotateCcw size={15} />
                                  </button>
                                ))
                                : (typeof onArchiveCourse === "function" && (
                                  <button
                                    onClick={() => { void handleArchiveCourse(courseItem); }}
                                    disabled={archivingCourseId === courseItem.id}
                                    title={archivingCourseId === courseItem.id ? "Архивируем..." : "Архивировать курс"}
                                    className="p-2 text-slate-400 hover:text-amber-700 hover:bg-amber-50 rounded-xl transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                  >
                                    <Archive size={15} />
                                  </button>
                                ))}
                              {canDeleteCourses && (
                                <button
                                  onClick={() => { void handleDeleteCourse(courseItem); }}
                                  disabled={deletingCourseId === courseItem.id}
                                  title={deletingCourseId === courseItem.id ? "Удаляем..." : "Удалить курс"}
                                  className="p-2 text-slate-400 hover:text-red-600 hover:bg-red-50 rounded-xl transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                  {deletingCourseId === courseItem.id ? <Clock size={15} /> : <Trash2 size={15} />}
                                </button>
                              )}
                            </>
                          )}
                        />
                      ))}
                    </div>
                  )}
                </section>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
