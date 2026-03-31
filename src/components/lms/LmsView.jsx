import { useState, useEffect, useRef, useCallback } from "react";
import {
  BookOpen, Play, CheckCircle, Clock, Award, Bell, Search, ChevronRight,
  ChevronDown, BarChart2, Plus, Trash2, Edit, Settings, Lock, Star, Download,
  X, Check, AlertCircle, ArrowLeft, Video, FileText, HelpCircle, Upload,
  Users, TrendingUp, Shield, Target, GripVertical, Filter, Calendar,
  PlayCircle, AlignLeft, Layers, ChevronLeft, Eye,
  BookMarked, Zap, ToggleLeft, ToggleRight, LayoutGrid, List, Percent,
  UserCheck, RefreshCw, ClipboardList, PlusCircle, LogOut, ChevronUp,
  Save, Image, Link2, FileCheck, Pause, Volume2, Maximize, AlertTriangle,
  XCircle, CheckSquare, Square, Type, ToggleRight as RadioIcon
} from "lucide-react";

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

const statusConfig = {
  completed: { label: "Завершён", bg: "bg-emerald-50", text: "text-emerald-700", border: "border-emerald-200", dot: "bg-emerald-500" },
  completed_late: { label: "Завершён с опозданием", bg: "bg-orange-50", text: "text-orange-700", border: "border-orange-200", dot: "bg-orange-500" },
  in_progress: { label: "В процессе", bg: "bg-blue-50", text: "text-blue-700", border: "border-blue-200", dot: "bg-blue-500" },
  not_started: { label: "Не начат", bg: "bg-slate-50", text: "text-slate-600", border: "border-slate-200", dot: "bg-slate-400" },
  overdue: { label: "Просрочен", bg: "bg-red-50", text: "text-red-700", border: "border-red-200", dot: "bg-red-500" },
  waiting_test: { label: "Ожидает тест", bg: "bg-violet-50", text: "text-violet-700", border: "border-violet-200", dot: "bg-violet-500" },
  test_failed: { label: "Тест не пройден", bg: "bg-red-50", text: "text-red-700", border: "border-red-200", dot: "bg-red-500" },
};

const lessonIcons = { video: Video, text: FileText, quiz: HelpCircle };

const formatDeadline = (date) => {
  if (!date) return null;
  const d = new Date(date);
  const now = new Date();
  const diff = Math.ceil((d - now) / (1000 * 60 * 60 * 24));
  if (diff < 0) return { label: `Просрочен на ${Math.abs(diff)} дн`, urgent: true, overdue: true };
  if (diff <= 7) return { label: `${diff} дней`, urgent: true, overdue: false };
  return { label: d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" }), urgent: false, overdue: false };
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

const inferLessonType = (lessonLike) => {
  const explicit = String(lessonLike?.lesson_type || lessonLike?.type || "").trim().toLowerCase();
  if (explicit === "quiz") return "quiz";
  if (explicit === "text") return "text";
  if (explicit === "video") return "video";
  const materials = Array.isArray(lessonLike?.materials) ? lessonLike.materials : [];
  if (!materials.length) return "text";
  if (materials.some((m) => String(m?.material_type || "").toLowerCase() === "video")) return "video";
  if (materials.every((m) => TEXT_MATERIAL_TYPES.has(String(m?.material_type || "").toLowerCase()))) return "text";
  return "video";
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

const mapHomeCourseToView = (course) => {
  const courseId = Number(course?.course_id || 0);
  const visual = pickCourseVisual(courseId, course?.category);
  const totalLessons = Math.max(0, Number(course?.total_lessons || 0));
  const completedLessons = Math.max(0, Number(course?.completed_lessons || 0));
  const progress = Math.max(0, Math.min(100, Math.round(Number(course?.progress_percent || 0))));
  const coverUrl = String(course?.cover_url || "").trim();
  const skills = Array.isArray(course?.skills)
    ? course.skills.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  return {
    id: courseId,
    assignmentId: Number(course?.assignment_id || 0) || null,
    courseVersionId: Number(course?.course_version_id || 0) || null,
    title: String(course?.title || "Без названия"),
    category: String(course?.category || "Без категории"),
    cover: visual.cover,
    coverUrl,
    color: visual.color,
    description: String(course?.description || ""),
    skills,
    duration: formatDurationLabel(totalLessons * 15 * 60),
    lessons: totalLessons,
    modules: 0,
    progress,
    completedLessons,
    deadline: course?.due_at || null,
    mandatory: false,
    status: mapAssignmentStatusToUi(course?.status, course?.deadline_status),
    rating: course?.best_score ? Math.max(1, Math.min(5, Number(course.best_score) / 20)) : 0,
    reviews: 0,
    passingScore: 80,
    maxAttempts: 0,
    attemptsUsed: 0,
    hasCourseAttemptLimit: false,
    modules_data: [],
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
  testsRaw.forEach((test) => {
    const key = test?.module_id == null ? "__course__" : String(test.module_id);
    const prev = testsByModule.get(key) || [];
    prev.push(test);
    testsByModule.set(key, prev);
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
    const configuredMinutes = Math.max(0, Number(test?.time_limit_minutes || test?.time_limit || 0));
    const configuredSeconds = Math.max(0, Number(test?.time_limit_seconds || 0));
    const timeLimitSeconds = configuredSeconds > 0 ? configuredSeconds : (configuredMinutes > 0 ? configuredMinutes * 60 : 0);
    const fallbackMinutes = Math.max(10, Math.ceil((Number(test?.question_count || 0) || 1) * 1.5));
    const displayMinutes = timeLimitSeconds > 0 ? Math.max(1, Math.round(timeLimitSeconds / 60)) : fallbackMinutes;
    let testStatus = "not_started";
    if (passedAny) testStatus = "completed";
    else if (attemptsUsed > 0) testStatus = attemptsUsed >= attemptLimit ? "test_failed" : "in_progress";
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
      isFinal: Boolean(test?.is_final),
      passingScore: Number(test?.pass_threshold || coursePayload?.course_version?.pass_threshold || coursePayload?.default_pass_threshold || 80),
      questionCount: Math.max(0, Number(test?.question_count || 0)),
      moduleId: test?.module_id == null ? null : Number(test.module_id),
      _position: Number(test?.position || test?.id || 0),
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
        const duration = formatDurationLabel(Number(lessonItem?.duration_seconds || 0));

        lessons.push({
          id: lessonId,
          apiLessonId: lessonId,
          title: String(lessonItem?.title || `Урок ${lessonIndex + 1}`),
          description: String(lessonItem?.description || ""),
          type: lessonType,
          duration,
          durationSeconds: Number(lessonItem?.duration_seconds || 0),
          status,
          locked: isLocked,
          completionRatio,
          allowFastForward: Boolean(lessonItem?.allow_fast_forward),
          completionThreshold: Number(lessonItem?.completion_threshold || 0),
          materials: Array.isArray(lessonItem?.materials) ? lessonItem.materials : [],
          moduleId,
          _position: Number(lessonItem?.position || lessonIndex + 1),
        });

        regularLessonsTotal += 1;
        progressItemsTotal += 1;
        durationSeconds += Math.max(0, Number(lessonItem?.duration_seconds || 0));
        if (status === "completed") {
          regularLessonsCompleted += 1;
          progressItemsCompleted += 1;
        }
        if (status !== "completed") progressionLocked = true;
      });

      const moduleTests = (testsByModule.get(String(moduleId)) || []).slice();
      moduleTests.forEach((test) => {
        const moduleHasIncompleteLesson = lessons.some((item) => item.type !== "quiz" && item.status !== "completed");
        const mappedTest = mapTestLesson(test, progressionLocked || moduleHasIncompleteLesson);
        lessons.push(mappedTest);
        if (!mappedTest.isFinal) {
          progressItemsTotal += 1;
          if (mappedTest.status === "completed") progressItemsCompleted += 1;
        }
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

  const unboundTests = (testsByModule.get("__course__") || []).slice();
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
      if (!mappedTest.isFinal) {
        progressItemsTotal += 1;
        if (mappedTest.status === "completed") progressItemsCompleted += 1;
      }
      if (mappedTest.status !== "completed") progressionLocked = true;
    });
  }

  const isAssignmentCompleted = String(assignment?.status || "").toLowerCase() === "completed";
  const progressPercent = isAssignmentCompleted
    ? 100
    : (progressItemsTotal > 0 ? Math.round((progressItemsCompleted / progressItemsTotal) * 100) : 0);

  let status = mapAssignmentStatusToUi(assignment?.status, assignment?.deadline_status);
  if (status !== "completed" && courseAttemptTests.length > 0 && regularLessonsTotal > 0 && regularLessonsCompleted >= regularLessonsTotal) {
    const hasFailedRequiredTest = courseAttemptTests.some((test) => {
      const testState = testProgress?.[test.id] || testProgress?.[String(test.id)] || {};
      const passedAny = Boolean(testState?.passed_any);
      const attemptsUsed = Math.max(0, Number(testState?.attempts_used || 0));
      const attemptLimit = Math.max(1, Number(test?.attempt_limit || coursePayload?.course_version?.attempt_limit || coursePayload?.default_attempt_limit || 3));
      return !passedAny && attemptsUsed >= attemptLimit;
    });
    status = hasFailedRequiredTest ? "test_failed" : "waiting_test";
  }

  const hasCourseAttemptLimit = courseAttemptTests.length > 0;
  const attemptsUsedTotal = hasCourseAttemptLimit
    ? courseAttemptTests.reduce((sum, test) => {
      const testState = testProgress?.[test.id] || testProgress?.[String(test.id)] || {};
      return sum + Math.max(0, Number(testState?.attempts_used || 0));
    }, 0)
    : 0;
  const maxAttempts = hasCourseAttemptLimit
    ? Math.max(...courseAttemptTests.map((test) => Math.max(1, Number(test?.attempt_limit || coursePayload?.course_version?.attempt_limit || coursePayload?.default_attempt_limit || 3))))
    : 0;

  const lessonsCountWithTests = modulesData.reduce((sum, mod) => sum + (Array.isArray(mod?.lessons) ? mod.lessons.length : 0), 0);

  return {
    id: courseId,
    assignmentId: Number(assignment?.id || fallbackCourse?.assignmentId || 0) || null,
    courseVersionId: Number(coursePayload?.course_version?.id || fallbackCourse?.courseVersionId || 0) || null,
    title: String(coursePayload?.title || fallbackCourse?.title || "Без названия"),
    category: String(coursePayload?.category || fallbackCourse?.category || "Без категории"),
    cover: visual.cover,
    coverUrl: versionCoverUrl || String(fallbackCourse?.coverUrl || "").trim(),
    color: visual.color,
    description: String(coursePayload?.description || fallbackCourse?.description || ""),
    skills: versionSkills.length ? versionSkills : (Array.isArray(fallbackCourse?.skills) ? fallbackCourse.skills : []),
    duration: formatDurationLabel(durationSeconds),
    lessons: lessonsCountWithTests,
    modules: modulesData.length,
    progress: Math.max(0, Math.min(100, progressPercent)),
    deadline: assignment?.due_at || fallbackCourse?.deadline || null,
    mandatory: Boolean(fallbackCourse?.mandatory),
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

export default function LmsView({ user, apiBaseUrl, withAccessTokenHeader, showToast }) {
  const role = normalizeLmsRole(user?.role);
  const canUseLearnerApi = role === "operator" || role === "trainee";
  const canUseManagerApi = role === "sv" || role === "trainer" || role === "admin" || role === "super_admin";
  const canGoCatalog = canUseLearnerApi;
  const apiRoot = String(apiBaseUrl || "").trim().replace(/\/+$/, "");

  const [view, setView] = useState(canUseLearnerApi ? "catalog" : "admin");
  const [selectedCourse, setSelectedCourse] = useState(null);
  const [selectedLesson, setSelectedLesson] = useState(null);
  const [catalogTab, setCatalogTab] = useState("available");
  const [searchQuery, setSearchQuery] = useState("");
  const [isAdmin, setIsAdmin] = useState(canUseManagerApi);
  const [adminTab, setAdminTab] = useState("analytics");
  const [quizView, setQuizView] = useState("intro");
  const [quizAnswers, setQuizAnswers] = useState({});
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const [courses, setCourses] = useState([]);
  const [certificates, setCertificates] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [adminCourses, setAdminCourses] = useState([]);
  const [adminProgressRows, setAdminProgressRows] = useState([]);
  const [adminAttempts, setAdminAttempts] = useState([]);
  const [learners, setLearners] = useState([]);
  const [loadingHome, setLoadingHome] = useState(false);
  const [loadingAdmin, setLoadingAdmin] = useState(false);
  const [busyCourseId, setBusyCourseId] = useState(null);
  const [homeError, setHomeError] = useState("");
  const [apiMode, setApiMode] = useState(false);
  const showToastRef = useRef(showToast);
  const withAccessTokenHeaderRef = useRef(withAccessTokenHeader);
  const homeLoadPromiseRef = useRef(null);
  const adminLoadPromiseRef = useRef(null);

  useEffect(() => {
    setIsAdmin(canUseManagerApi);
    if (!canUseLearnerApi) {
      setView("admin");
    }
  }, [canUseLearnerApi, canUseManagerApi]);

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

  const loadLearnerDashboard = useCallback(async () => {
    if (!apiRoot || !canUseLearnerApi) return;
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
      setApiMode(true);
    } catch (error) {
      setHomeError(String(error?.message || "Не удалось загрузить данные LMS"));
      emitToast(`LMS: ${String(error?.message || "ошибка загрузки")}`, "error");
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
  }, [apiRoot, canUseLearnerApi, lmsRequest, user?.name, user?.login, emitToast]);

  const loadAdminData = useCallback(async () => {
    if (!apiRoot || !canUseManagerApi) return;
    if (adminLoadPromiseRef.current) {
      return adminLoadPromiseRef.current;
    }

    const loadPromise = (async () => {
      setLoadingAdmin(true);
      try {
      const [coursesRes, progressRes, attemptsRes, learnersRes] = await Promise.all([
        lmsRequest("/api/lms/admin/courses"),
        lmsRequest("/api/lms/admin/progress"),
        lmsRequest("/api/lms/admin/attempts?limit=400"),
        lmsRequest("/api/lms/admin/learners"),
      ]);
      setAdminCourses(Array.isArray(coursesRes?.courses) ? coursesRes.courses : []);
      setAdminProgressRows(Array.isArray(progressRes?.rows) ? progressRes.rows : []);
      setAdminAttempts(Array.isArray(attemptsRes?.attempts) ? attemptsRes.attempts : []);
      setLearners(Array.isArray(learnersRes?.learners) ? learnersRes.learners : []);
      setApiMode(true);
    } catch (error) {
      emitToast(`LMS admin: ${String(error?.message || "ошибка загрузки")}`, "error");
      } finally {
        setLoadingAdmin(false);
      }
    })();

    adminLoadPromiseRef.current = loadPromise;
    try {
      return await loadPromise;
    } finally {
      if (adminLoadPromiseRef.current === loadPromise) {
        adminLoadPromiseRef.current = null;
      }
    }
  }, [apiRoot, canUseManagerApi, lmsRequest, emitToast]);

  useEffect(() => {
    loadLearnerDashboard();
  }, [loadLearnerDashboard]);

  useEffect(() => {
    if (view === "admin" || view === "builder") {
      loadAdminData();
    }
  }, [view, loadAdminData]);

  const handleDeleteAdminCourse = useCallback(async (courseLike) => {
    if (!canUseManagerApi) {
      emitToast("Недостаточно прав для удаления курса", "error");
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
      setAdminCourses((prev) => prev.filter((item) => Number(item?.id || 0) !== courseId));
      setAdminProgressRows((prev) => prev.filter((item) => Number(item?.course_id || 0) !== courseId));
      setAdminAttempts((prev) => prev.filter((item) => Number(item?.course_id || 0) !== courseId));
      emitToast("Курс и его файлы в GCS удалены", "success");
      await loadAdminData();
      return true;
    } catch (error) {
      emitToast(`Не удалось удалить курс: ${String(error?.message || "ошибка")}`, "error");
      return false;
    }
  }, [canUseManagerApi, lmsRequest, emitToast, loadAdminData]);

  const markNotificationRead = useCallback(async (notificationId) => {
    setNotifications((prev) => prev.map((item) => (item.id === notificationId ? { ...item, read: true } : item)));
    if (!apiRoot || !canUseLearnerApi) return;
    try {
      await lmsRequest(`/api/lms/notifications/${notificationId}/read`, { method: "POST" });
    } catch (error) {
      emitToast(String(error?.message || "Не удалось отметить уведомление"), "error");
    }
  }, [apiRoot, canUseLearnerApi, lmsRequest, emitToast]);

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

  const openCourse = useCallback(async (course) => {
    if (!course?.id) return;
    setBusyCourseId(course.id);
    try {
      let nextCourse = course;
      if (apiRoot && canUseLearnerApi) {
        try {
          await lmsRequest(`/api/lms/courses/${course.id}/start`, { method: "POST" });
        } catch (_) {
          // already started or not required
        }
        const detail = await lmsRequest(`/api/lms/courses/${course.id}`);
        if (detail?.course) {
          nextCourse = mapCourseDetailToView(detail.course, course);
          setCourses((prev) => prev.map((item) => (item.id === nextCourse.id ? { ...item, ...nextCourse } : item)));
        }
      }
      setSelectedCourse(nextCourse);
      setView("course");
    } catch (error) {
      emitToast(`Не удалось открыть курс: ${String(error?.message || "ошибка")}`, "error");
    } finally {
      setBusyCourseId(null);
    }
  }, [apiRoot, canUseLearnerApi, lmsRequest, emitToast]);

  const refreshSelectedCourse = useCallback(async () => {
    if (!apiRoot || !canUseLearnerApi || !selectedCourse?.id) return null;
    const detail = await lmsRequest(`/api/lms/courses/${selectedCourse.id}`);
    if (detail?.course) {
      const mapped = mapCourseDetailToView(detail.course, selectedCourse);
      setSelectedCourse(mapped);
      setCourses((prev) => prev.map((item) => (item.id === mapped.id ? { ...item, ...mapped } : item)));
      return mapped;
    }
    return null;
  }, [apiRoot, canUseLearnerApi, selectedCourse, lmsRequest]);

  const openLesson = useCallback(async (lesson) => {
    if (!lesson) return;
    setSelectedLesson(lesson);
    setView("lesson");
    setQuizView("intro");
    setQuizAnswers({});

    if (!apiRoot || !canUseLearnerApi || !lesson?.apiLessonId || lesson?.type === "quiz") return;
    try {
      const detail = await lmsRequest(`/api/lms/lessons/${lesson.apiLessonId}`);
      const lessonPayload = detail?.lesson || {};
      const progressPayload = detail?.progress || {};
      setSelectedLesson((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          title: lessonPayload?.title || prev.title,
          description: lessonPayload?.description || prev.description,
          duration: formatDurationLabel(lessonPayload?.duration_seconds || prev.durationSeconds || 0),
          durationSeconds: Number(lessonPayload?.duration_seconds || prev.durationSeconds || 0),
          materials: Array.isArray(detail?.materials) ? detail.materials : (prev.materials || []),
          completionRatio: Number(progressPayload?.completion_ratio || prev.completionRatio || 0),
          status: String(progressPayload?.status || prev.status || "not_started"),
          apiProgress: progressPayload,
          apiSession: detail?.session || null,
          antiCheat: detail?.anti_cheat || null,
        };
      });
    } catch (error) {
      emitToast(`Не удалось загрузить урок: ${String(error?.message || "ошибка")}`, "error");
    }
  }, [apiRoot, canUseLearnerApi, lmsRequest, emitToast]);

  const handleCompleteLesson = useCallback(async (lesson) => {
    if (!apiRoot || !canUseLearnerApi || !lesson?.apiLessonId) return false;
    try {
      await lmsRequest(`/api/lms/lessons/${lesson.apiLessonId}/complete`, { method: "POST" });
      emitToast("Урок отмечен как завершенный", "success");
      setSelectedLesson((prev) => (prev ? { ...prev, status: "completed", completionRatio: 100 } : prev));
      const refreshedCourse = await refreshSelectedCourse();
      await loadLearnerDashboard();
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
  }, [apiRoot, canUseLearnerApi, lmsRequest, emitToast, refreshSelectedCourse, loadLearnerDashboard]);

  const handleQuizFinished = useCallback(async () => {
    try {
      await refreshSelectedCourse();
      await loadLearnerDashboard();
    } catch (_) {
      // ignore refresh errors here
    }
  }, [refreshSelectedCourse, loadLearnerDashboard]);

  const goBack = () => {
    if (view === "lesson") {
      setView("course");
      setSelectedLesson(null);
    } else if (view === "course") {
      setView(canGoCatalog ? "catalog" : "admin");
      setSelectedCourse(null);
    } else if (view === "builder") {
      setView(canGoCatalog ? "catalog" : "admin");
    } else if (view === "admin") {
      setView(canGoCatalog ? "catalog" : "admin");
    }
  };

  const navToAdmin = () => {
    if (!canUseManagerApi) return;
    setView("admin");
    setAdminTab("analytics");
  };

  return (
    <div className="min-h-screen bg-slate-50 font-sans" style={{ fontFamily: "'DM Sans', system-ui, sans-serif" }}>
      <TopNav
        view={view}
        goBack={goBack}
        isAdmin={isAdmin}
        setIsAdmin={setIsAdmin}
        navToAdmin={navToAdmin}
        canToggleAdmin={canUseManagerApi}
        canGoCatalog={canGoCatalog}
      />
      <main className="pt-16">
        {homeError && canUseLearnerApi && (
          <div className="max-w-screen-xl mx-auto px-6 py-4">
            <div className="rounded-xl border border-amber-200 bg-amber-50 text-amber-800 text-sm px-4 py-3">
              {homeError}
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
            onOpenBuilder={() => setView("builder")}
            courses={courses}
            certificates={certificates}
            notifications={notifications}
            loading={loadingHome}
            busyCourseId={busyCourseId}
            onNotificationRead={markNotificationRead}
            onCertificateDownload={downloadCertificate}
            onRefresh={loadLearnerDashboard}
          />
        )}
        {view === "course" && selectedCourse && (
          <CourseDetail course={selectedCourse} onStartLesson={openLesson} />
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
            onCompleteLesson={handleCompleteLesson}
            onQuizFinished={handleQuizFinished}
            emitToast={emitToast}
          />
        )}
        {view === "builder" && (
          <CourseBuilder
            onBack={goBack}
            lmsRequest={lmsRequest}
            canUseManagerApi={canUseManagerApi}
            learners={learners}
            adminCourses={adminCourses}
            emitToast={emitToast}
            onAfterSave={loadAdminData}
          />
        )}
        {view === "admin" && (
          <AdminView
            tab={adminTab}
            setTab={setAdminTab}
            adminCourses={adminCourses}
            progressRows={adminProgressRows}
            attempts={adminAttempts}
            loading={loadingAdmin}
            onOpenBuilder={() => setView("builder")}
            onDeleteCourse={handleDeleteAdminCourse}
          />
        )}
      </main>
    </div>
  );
}

// ─── TOP NAVIGATION ───────────────────────────────────────────────────────────

function TopNav({ view, goBack, isAdmin, setIsAdmin, navToAdmin, canToggleAdmin = true, canGoCatalog = true }) {
  const showBack = ["course", "lesson", "builder"].includes(view) || (view === "admin" && canGoCatalog);
  const backLabels = { course: "Все курсы", lesson: "Курс", builder: "Все курсы", admin: "Все курсы" };
  return (
    <header className="fixed top-0 left-0 right-0 z-40 bg-white border-b border-slate-200 h-16">
      <div className="max-w-screen-xl mx-auto px-6 h-full flex items-center justify-between gap-4">
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
          {!showBack && canToggleAdmin && (
            <button onClick={navToAdmin} className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 transition-colors px-3 py-1.5 rounded-lg hover:bg-slate-100">
              <BarChart2 size={15} /> Аналитика
            </button>
          )}
          <button
            onClick={() => {
              if (canToggleAdmin) setIsAdmin(!isAdmin);
            }}
            disabled={!canToggleAdmin}
            className={`flex items-center gap-2 text-xs px-3 py-1.5 rounded-full border transition-all ${isAdmin ? "bg-indigo-50 border-indigo-200 text-indigo-700" : "bg-slate-50 border-slate-200 text-slate-600 hover:border-slate-300"} ${!canToggleAdmin ? "opacity-70 cursor-default" : ""}`}
          >
            <Shield size={12} /> {canToggleAdmin ? (isAdmin ? "Режим админа" : "Сотрудник") : "LMS"}
          </button>
          <div className="relative">
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-violet-600 flex items-center justify-center text-white text-xs font-semibold">АИ</div>
            <div className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 bg-emerald-500 rounded-full border-2 border-white" />
          </div>
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
    const matchesFilter = filter === "all" || (filter === "mandatory" && c.mandatory) || c.status === filter;
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
    <div className="max-w-screen-xl mx-auto px-6 py-8">
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
        {[
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
        ))}
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
        />
      )}
      {tab === "notifications" && (
        <NotificationsView
          notifications={safeNotifications}
          onRead={onNotificationRead}
        />
      )}

      {(tab === "available" || tab === "completed") && (
        <>
          <div className="flex items-center gap-3 mb-6">
            <div className="relative flex-1 max-w-md">
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

          {filteredCourses.length === 0 ? (
            <div className="text-center py-20 text-slate-400"><BookOpen size={40} className="mx-auto mb-3 opacity-30" /><p className="text-sm">Курсы не найдены</p></div>
          ) : gridView ? (
            <div className="grid grid-cols-3 gap-5">
              {filteredCourses.map(c => (
                <CourseCard
                  key={c.id}
                  course={c}
                  busy={busyCourseId === c.id}
                  onClick={() => onOpenCourse(c)}
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
                  onClick={() => onOpenCourse(c)}
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

function CourseCard({ course, onClick, busy = false }) {
  const st = statusConfig[course.status] || statusConfig.not_started;
  const dl = course.deadline ? formatDeadline(course.deadline) : null;
  const attemptsLeft = Math.max(0, Number(course.maxAttempts || 0) - Number(course.attemptsUsed || 0));

  return (
    <div onClick={() => !busy && onClick?.()} className={`bg-white rounded-2xl border border-slate-200 overflow-hidden transition-all group ${busy ? "opacity-70 cursor-wait" : "cursor-pointer hover:shadow-md hover:border-slate-300"}`}>
      <div className={`h-32 bg-gradient-to-br ${course.color} flex items-center justify-center relative overflow-hidden`}>
        {course.coverUrl ? (
          <img src={course.coverUrl} alt={course.title} className="absolute inset-0 w-full h-full object-cover object-center" />
        ) : (
          <span className="text-5xl">{course.cover}</span>
        )}
        <div className="absolute inset-0 bg-black/10" />
        {course.mandatory && (
          <div className="absolute top-3 left-3 bg-white/20 backdrop-blur-sm text-white text-[10px] font-semibold px-2 py-1 rounded-full border border-white/30 z-10">Обязательный</div>
        )}
        {course.status === "completed" && (
          <div className="absolute top-3 right-3 w-8 h-8 bg-white rounded-full flex items-center justify-center shadow-md z-10"><CheckCircle size={16} className="text-emerald-600" /></div>
        )}
        {course.status === "overdue" && (
          <div className="absolute top-3 right-3 w-8 h-8 bg-red-500 rounded-full flex items-center justify-center shadow-md z-10"><AlertCircle size={16} className="text-white" /></div>
        )}
      </div>
      <div className="p-5">
        <div className="flex items-start justify-between gap-2 mb-2">
          <span className="text-[10px] font-semibold text-indigo-600 uppercase tracking-wider">{course.category}</span>
          <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${st.bg} ${st.text}`}>{st.label}</span>
        </div>
        <h3 className="text-sm font-semibold text-slate-900 leading-snug mb-3 group-hover:text-indigo-700 transition-colors line-clamp-2">{course.title}</h3>
        <div className="flex items-center gap-3 text-xs text-slate-500 mb-4">
          <span className="flex items-center gap-1"><Clock size={11} /> {course.duration}</span>
          <span className="flex items-center gap-1"><BookOpen size={11} /> {course.lessons} уроков</span>
          <span className="flex items-center gap-1"><Star size={11} className="text-amber-400 fill-amber-400" /> {course.rating}</span>
        </div>
        {course.status !== "completed" && course.status !== "not_started" && (
          <div className="mb-3">
            <div className="flex justify-between text-xs text-slate-500 mb-1.5"><span>Прогресс</span><span className="font-semibold text-slate-700">{course.progress}%</span></div>
            <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
              <div className={`h-full rounded-full transition-all ${course.status === "overdue" ? "bg-red-500" : "bg-indigo-500"}`} style={{ width: `${course.progress}%` }} />
            </div>
          </div>
        )}
        {/* Попытки */}
        {course.hasCourseAttemptLimit && Number(course.maxAttempts || 0) > 0 && course.status !== "completed" && course.status !== "completed_late" && (
          <div className={`flex items-center gap-1 text-[10px] mb-3 ${attemptsLeft <= 1 ? "text-red-600" : "text-slate-500"}`}>
            <RefreshCw size={10} />
            <span>Попыток осталось: <strong>{attemptsLeft <= 0 ? "нет" : attemptsLeft}</strong> из {course.maxAttempts}</span>
          </div>
        )}
        <div className="flex items-center justify-between">
          {dl && (
            <div className={`flex items-center gap-1 text-xs ${dl.overdue ? "text-red-600" : dl.urgent ? "text-amber-600" : "text-slate-500"}`}>
              <Calendar size={11} />
              {dl.overdue ? `Просрочен ${Math.abs(Math.ceil((new Date(course.deadline) - new Date()) / 86400000))} дн` : `До ${dl.label}`}
            </div>
          )}
          <button className={`ml-auto text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors ${course.status === "completed" || course.status === "completed_late" ? "bg-emerald-50 text-emerald-700 hover:bg-emerald-100" : "bg-indigo-600 text-white hover:bg-indigo-700"}`}>
            {busy ? "Загрузка..." : (course.status === "completed" || course.status === "completed_late") ? "Просмотр" : course.status === "not_started" ? "Начать" : "Продолжить"}
          </button>
        </div>
      </div>
    </div>
  );
}

function CourseListItem({ course, onClick, busy = false }) {
  const st = statusConfig[course.status] || statusConfig.not_started;
  return (
    <div onClick={() => !busy && onClick?.()} className={`bg-white rounded-2xl border border-slate-200 p-5 flex items-center gap-5 transition-all group ${busy ? "opacity-70 cursor-wait" : "cursor-pointer hover:shadow-sm hover:border-slate-300"}`}>
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
          {course.mandatory && <span className="text-[10px] font-semibold text-amber-600 bg-amber-50 px-2 py-0.5 rounded-full">Обязательный</span>}
        </div>
        <h3 className="text-sm font-semibold text-slate-900 group-hover:text-indigo-700 transition-colors truncate">{course.title}</h3>
        <div className="flex items-center gap-3 text-xs text-slate-500 mt-1">
          <span className="flex items-center gap-1"><Clock size={11} /> {course.duration}</span>
          <span className="flex items-center gap-1"><BookOpen size={11} /> {course.lessons} уроков</span>
          {course.hasCourseAttemptLimit && (
            <span className="flex items-center gap-1"><RefreshCw size={11} /> {Math.max(0, Number(course.maxAttempts || 0) - Number(course.attemptsUsed || 0))} поп.</span>
          )}
        </div>
      </div>
      {course.status !== "completed" && course.status !== "not_started" && (
        <div className="w-32">
          <div className="flex justify-between text-xs text-slate-500 mb-1"><span>Прогресс</span><span>{course.progress}%</span></div>
          <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
            <div className="h-full bg-indigo-500 rounded-full" style={{ width: `${course.progress}%` }} />
          </div>
        </div>
      )}
      <span className={`text-xs font-semibold px-2.5 py-1 rounded-full ${st.bg} ${st.text}`}>{st.label}</span>
      <ChevronRight size={16} className="text-slate-400 flex-shrink-0" />
    </div>
  );
}

// ─── COURSE DETAIL ────────────────────────────────────────────────────────────

function CourseDetail({ course, onStartLesson }) {
  const [openModules, setOpenModules] = useState([course?.modules_data?.[0]?.id || 1]);
  const toggleModule = (id) => setOpenModules(p => p.includes(id) ? p.filter(x => x !== id) : [...p, id]);
  const dl = course.deadline ? formatDeadline(course.deadline) : null;
  const firstLesson = course.modules_data[0]?.lessons[0];
  const attemptsLeft = Math.max(0, Number(course.maxAttempts || 0) - Number(course.attemptsUsed || 0));

  useEffect(() => {
    setOpenModules(course?.modules_data?.[0]?.id ? [course.modules_data[0].id] : []);
  }, [course?.id]);

  return (
    <div className="max-w-screen-xl mx-auto px-6 py-8">
      <div className={`rounded-3xl bg-gradient-to-br ${course.color} p-8 mb-8 relative overflow-hidden`}>
        {course.coverUrl ? (
          <img src={course.coverUrl} alt={course.title} className="absolute inset-0 w-full h-full object-cover object-center opacity-30" />
        ) : (
          <div className="absolute right-8 top-8 text-8xl opacity-20">{course.cover}</div>
        )}
        <div className="relative z-10 max-w-2xl">
          <div className="flex items-center gap-2 mb-4">
            <span className="text-xs font-semibold text-white/70 uppercase tracking-wider">{course.category}</span>
            {course.mandatory && <span className="text-xs font-semibold bg-white/20 text-white px-2.5 py-1 rounded-full">Обязательный</span>}
          </div>
          <h1 className="text-3xl font-bold text-white mb-4 leading-tight tracking-tight">{course.title}</h1>
          <p className="text-white/80 text-sm leading-relaxed mb-6">{course.description}</p>
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
          <button onClick={() => firstLesson && onStartLesson(firstLesson)} className="bg-white text-slate-900 font-semibold px-6 py-3 rounded-xl hover:bg-white/90 transition-colors text-sm shadow-lg">
            {course.status === "not_started" ? "Начать курс" : course.status === "completed" ? "Повторить" : "Продолжить обучение"}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-8">
        <div className="col-span-2 space-y-6">
          <div className="bg-white rounded-2xl border border-slate-200 p-6">
            <h2 className="text-base font-semibold text-slate-900 mb-4">Приобретаемые навыки</h2>
            <div className="flex flex-wrap gap-2">
              {course.skills.map(s => <span key={s} className="text-xs font-medium bg-indigo-50 text-indigo-700 border border-indigo-100 px-3 py-1.5 rounded-full">{s}</span>)}
              {course.skills.length === 0 && <span className="text-xs text-slate-400">Навыки не добавлены</span>}
            </div>
          </div>

          {course.modules_data.length > 0 && (
            <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
              <div className="px-6 py-5 border-b border-slate-100">
                <h2 className="text-base font-semibold text-slate-900">Программа курса</h2>
                <p className="text-xs text-slate-500 mt-1">{course.modules} модуля · {course.lessons} уроков</p>
              </div>
              {course.modules_data.map(mod => (
                <div key={mod.id} className="border-b border-slate-100 last:border-0">
                  <button onClick={() => toggleModule(mod.id)} className="w-full flex items-center justify-between px-6 py-4 hover:bg-slate-50 transition-colors">
                    <div className="flex items-center gap-3">
                      <div className="w-7 h-7 rounded-lg bg-indigo-50 text-indigo-600 flex items-center justify-center text-xs font-bold">{mod.id}</div>
                      <span className="text-sm font-semibold text-slate-800">{mod.title}</span>
                      <span className="text-xs text-slate-400">{mod.lessons.length} уроков</span>
                    </div>
                    {openModules.includes(mod.id) ? <ChevronUp size={16} className="text-slate-400" /> : <ChevronDown size={16} className="text-slate-400" />}
                  </button>
                  {openModules.includes(mod.id) && (
                    <div className="px-6 pb-4 space-y-1">
                      {mod.lessons.map(l => {
                        const Icon = lessonIcons[l.type];
                        return (
                          <div key={l.id} onClick={() => !l.locked && onStartLesson(l)} className={`flex items-center gap-3 p-3 rounded-xl transition-all ${l.locked ? "opacity-50 cursor-not-allowed" : "cursor-pointer hover:bg-indigo-50 group"}`}>
                            <div className={`w-8 h-8 rounded-lg flex items-center justify-center ${l.status === "completed" ? "bg-emerald-50" : l.locked ? "bg-slate-100" : "bg-indigo-50"}`}>
                              {l.status === "completed" ? <CheckCircle size={14} className="text-emerald-600" /> : l.locked ? <Lock size={14} className="text-slate-400" /> : <Icon size={14} className="text-indigo-600" />}
                            </div>
                            <div className="flex-1">
                              <p className={`text-xs font-medium ${l.locked ? "text-slate-400" : "text-slate-800 group-hover:text-indigo-700"}`}>{l.title}</p>
                              <p className="text-[10px] text-slate-400 mt-0.5">{l.type === "video" ? "Видеоурок" : l.type === "text" ? "Текстовый материал" : "Тест"} · {l.duration}</p>
                            </div>
                            {l.requiresTest && !l.locked && <span className="text-[10px] bg-violet-50 text-violet-600 px-1.5 py-0.5 rounded-full font-medium">Тест</span>}
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

        <div className="space-y-4">
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
  onCompleteLesson,
  onQuizFinished,
  emitToast,
}) {
  const isQuiz = lesson.type === "quiz";
  const isTextLesson = lesson.type === "text";
  const lessonAttemptLimit = Math.max(0, Number(lesson?.maxAttempts ?? course?.maxAttempts ?? 0));
  const lessonAttemptsUsed = Math.max(0, Number(lesson?.attemptsUsed ?? course?.attemptsUsed ?? 0));
  const lessonAttemptsLeft = Math.max(0, lessonAttemptLimit - lessonAttemptsUsed);

  return (
    <div className="flex h-[calc(100vh-64px)]">
      {/* Sidebar */}
      <div className={`${sidebarOpen ? "w-80" : "w-0"} flex-shrink-0 transition-all duration-300 overflow-hidden bg-white border-r border-slate-200`}>
        <div className="p-4 border-b border-slate-100">
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Программа курса</p>
          <p className="text-sm font-semibold text-slate-900 leading-tight">{course.title}</p>
          <div className="mt-3">
            <div className="flex justify-between text-xs text-slate-400 mb-1"><span>Прогресс</span><span>{course.progress}%</span></div>
            <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden"><div className="h-full bg-indigo-500 rounded-full" style={{ width: `${course.progress}%` }} /></div>
          </div>
        </div>
        <div className="overflow-y-auto h-full pb-20">
          {course.modules_data.map(mod => (
            <div key={mod.id}>
              <div className="px-4 py-3 bg-slate-50 border-b border-slate-100">
                <p className="text-xs font-semibold text-slate-600">{mod.id}. {mod.title}</p>
              </div>
              {mod.lessons.map(l => {
                const Icon = lessonIcons[l.type];
                const isActive = l.id === lesson.id;
                const dl = course.deadline ? formatDeadline(course.deadline) : null;
                return (
                  <button key={l.id} onClick={() => !l.locked && onSelectLesson(l)} className={`w-full flex items-start gap-3 px-4 py-3 border-b border-slate-50 transition-colors text-left ${isActive ? "bg-indigo-50 border-l-2 border-l-indigo-500" : l.locked ? "opacity-50 cursor-not-allowed" : "hover:bg-slate-50"}`}>
                    <div className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5 ${isActive ? "bg-indigo-600" : l.status === "completed" ? "bg-emerald-50" : l.locked ? "bg-slate-100" : "bg-slate-100"}`}>
                      {isActive ? <Play size={11} className="text-white ml-0.5" /> : l.status === "completed" ? <CheckCircle size={13} className="text-emerald-600" /> : l.locked ? <Lock size={11} className="text-slate-400" /> : <Icon size={12} className="text-slate-500" />}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className={`text-xs font-medium leading-snug ${isActive ? "text-indigo-700" : "text-slate-700"}`}>{l.title}</p>
                      <div className="flex items-center gap-1.5 mt-1 flex-wrap">
                        <span className="text-[10px] text-slate-400">{l.duration}</span>
                        {l.type === "quiz" && <span className="text-[10px] bg-violet-50 text-violet-600 px-1.5 py-0.5 rounded-full font-medium">Тест</span>}
                        {l.status === "in_progress" && <span className="text-[10px] bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded-full font-medium">В процессе</span>}
                        {l.requiresTest && l.status !== "completed" && !l.locked && <span className="text-[10px] bg-violet-50 text-violet-600 px-1.5 py-0.5 rounded-full font-medium flex items-center gap-0.5"><HelpCircle size={8} /> Тест</span>}
                        {dl?.overdue && l.status !== "completed" && <span className="text-[10px] bg-red-50 text-red-600 px-1.5 py-0.5 rounded-full font-medium">Просрочен</span>}
                        {l.status === "completed" && <span className="text-[10px] bg-emerald-50 text-emerald-600 px-1.5 py-0.5 rounded-full font-medium">✓ Завершён</span>}
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
      <div className="flex-1 overflow-y-auto bg-slate-50">
        <div className="sticky top-0 z-10 bg-white border-b border-slate-200 px-6 py-3 flex items-center gap-3">
          <button onClick={() => setSidebarOpen(!sidebarOpen)} className="p-2 rounded-lg hover:bg-slate-100 text-slate-500 transition-colors"><AlignLeft size={16} /></button>
          <div className="flex-1">
            <p className="text-xs text-slate-400">Модуль 1 · Урок {lesson.id}</p>
            <p className="text-sm font-semibold text-slate-900">{lesson.title}</p>
          </div>
          <div className="flex items-center gap-3">
            {lesson.type === "quiz" && lessonAttemptLimit > 0 && (
              <div className="flex items-center gap-1.5 text-xs text-slate-500 bg-slate-50 border border-slate-200 px-3 py-1.5 rounded-lg">
                <RefreshCw size={12} />
                <span>Попыток: <strong className={lessonAttemptsLeft <= 1 ? "text-red-600" : "text-slate-700"}>{lessonAttemptsLeft}</strong> / {lessonAttemptLimit}</span>
              </div>
            )}
            <div className="flex items-center gap-2 text-xs text-slate-500"><Clock size={13} /> {lesson.duration}</div>
          </div>
        </div>

        <div className="max-w-4xl mx-auto px-8 py-8">
          {isQuiz ? (
            apiMode && Number(lesson?.apiTestId) > 0 ? (
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
          ) : isTextLesson ? (
            <TextLesson
              lesson={lesson}
              onCompleteLesson={onCompleteLesson}
            />
          ) : (
            <VideoLesson
              lesson={lesson}
              apiMode={apiMode}
              lmsRequest={lmsRequest}
              onCompleteLesson={onCompleteLesson}
              emitToast={emitToast}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function TextLesson({ lesson, onCompleteLesson }) {
  const [completing, setCompleting] = useState(false);
  const [completed, setCompleted] = useState(String(lesson?.status || "").toLowerCase() === "completed");
  const materials = Array.isArray(lesson?.materials) ? lesson.materials : [];
  const transcriptMaterial = materials.find((item) => String(item?.material_type || "").toLowerCase() === "text" && item?.content_text);
  const content = String(transcriptMaterial?.content_text || lesson?.description || "").trim();
  const lessonFiles = materials.filter((item) => {
    const type = String(item?.material_type || "").toLowerCase();
    if (type === "text") return false;
    return Boolean(item?.url || item?.signed_url || item?.content_url);
  });

  useEffect(() => {
    setCompleted(String(lesson?.status || "").toLowerCase() === "completed");
  }, [lesson?.id, lesson?.status]);

  const handleComplete = async () => {
    if (completed || completing) return;
    if (typeof onCompleteLesson !== "function") return;
    setCompleting(true);
    try {
      const ok = await onCompleteLesson(lesson);
      if (ok) setCompleted(true);
    } finally {
      setCompleting(false);
    }
  };

  const handleOpenMaterial = (material) => {
    const url = material?.url || material?.signed_url || material?.content_url;
    if (!url) return;
    window.open(url, "_blank", "noopener,noreferrer");
  };

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-2xl border border-slate-200 p-6">
        <h3 className="text-sm font-semibold text-slate-900 mb-3">Материал урока</h3>
        {content ? (
          <div className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap">{content}</div>
        ) : (
          <div className="text-xs text-slate-400">Текст урока не заполнен</div>
        )}
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
            const size = material?.mime_type ? String(material.mime_type) : "Файл";
            return (
              <button
                key={`${material?.id || idx}-${label}`}
                type="button"
                onClick={() => handleOpenMaterial(material)}
                className="w-full flex items-center gap-3 p-3 bg-slate-50 rounded-xl hover:bg-slate-100 transition-colors"
              >
                <div className="w-9 h-9 bg-indigo-100 rounded-lg flex items-center justify-center">
                  <FileCheck size={16} className="text-indigo-600" />
                </div>
                <div className="flex-1 text-left">
                  <p className="text-xs font-medium text-slate-800">{label}</p>
                  <p className="text-[10px] text-slate-400">{size}</p>
                </div>
                <Download size={14} className="text-slate-400" />
              </button>
            );
          })}
        </div>
      </div>

      {!completed && (
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

function VideoLesson({ lesson, apiMode, lmsRequest, onCompleteLesson, emitToast }) {
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(Math.max(0, Math.min(100, Number(lesson?.completionRatio ?? (String(lesson?.status || "").toLowerCase() === "completed" ? 100 : 0)))));
  const [activeTab, setActiveTab] = useState("transcript");
  const [tabHidden, setTabHidden] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [completed, setCompleted] = useState(String(lesson?.status || "").toLowerCase() === "completed");
  const [totalSeconds, setTotalSeconds] = useState(Math.max(1, Number(lesson?.durationSeconds || 18 * 60)));
  const [displayCurrentSeconds, setDisplayCurrentSeconds] = useState(0);
  const heartbeatRef = useRef(null);
  const videoRef = useRef(null);
  const progressRef = useRef(progress);
  const visibleRef = useRef(typeof document !== "undefined" ? !document.hidden : true);
  const maxAllowedSecondsRef = useRef(0);
  const seekToastAtRef = useRef(0);
  const autoCompleteTriggeredRef = useRef(false);

  const lessonId = Number(lesson?.apiLessonId || 0);
  const canTrack = apiMode && typeof lmsRequest === "function" && lessonId > 0;
  const completionThreshold = Math.max(1, Math.min(100, Number(lesson?.completionThreshold || 95)));
  const canSeekForward = Boolean(lesson?.allowFastForward) || completed || progress >= completionThreshold;
  const materials = Array.isArray(lesson?.materials) ? lesson.materials : [];
  const videoMaterial = materials.find((item) => {
    const type = String(item?.material_type || item?.type || "").toLowerCase();
    const url = item?.url || item?.signed_url || item?.content_url;
    return type === "video" && Boolean(url);
  });
  const videoDurationMeta = Number(videoMaterial?.metadata?.duration_seconds || 0);
  const videoUrl = videoMaterial?.url || videoMaterial?.signed_url || videoMaterial?.content_url || "";
  const safeTotalSeconds = Math.max(1, Number(totalSeconds || 0));
  const currentSeconds = Math.max(0, Math.floor(Number(displayCurrentSeconds || 0)));
  const transcriptMaterial = materials.find((item) => String(item?.material_type || "").toLowerCase() === "text" && item?.content_text);
  const transcriptText = String(transcriptMaterial?.content_text || lesson?.description || "").trim();
  const lessonFiles = materials.filter((item) => {
    const type = String(item?.material_type || item?.type || "").toLowerCase();
    if (type === "video") return false;
    return Boolean(item?.url || item?.signed_url || item?.content_url);
  });

  useEffect(() => {
    const next = Math.max(0, Math.min(100, Number(lesson?.completionRatio ?? (String(lesson?.status || "").toLowerCase() === "completed" ? 100 : 0))));
    const fallbackDuration = Math.max(1, Number(videoDurationMeta || lesson?.durationSeconds || 18 * 60));
    setProgress(next);
    setTotalSeconds(fallbackDuration);
    setDisplayCurrentSeconds(Math.max(0, (next / 100) * fallbackDuration));
    setCompleted(String(lesson?.status || "").toLowerCase() === "completed");
    setPlaying(false);
    maxAllowedSecondsRef.current = Math.max(0, (next / 100) * fallbackDuration);
    if (videoRef.current) {
      try {
        videoRef.current.pause();
      } catch (_) {
        // ignore
      }
    }
    autoCompleteTriggeredRef.current = false;
  }, [lesson?.id, lesson?.apiLessonId, lesson?.completionRatio, lesson?.status, lesson?.durationSeconds, videoDurationMeta]);

  useEffect(() => {
    progressRef.current = progress;
  }, [progress]);

  const sendHeartbeat = useCallback(async () => {
    if (!canTrack) return null;
    const positionSecondsFromVideo = Number(videoRef.current?.currentTime || 0);
    const localSeconds = Number.isFinite(positionSecondsFromVideo) && positionSecondsFromVideo >= 0
      ? positionSecondsFromVideo
      : Math.floor((progressRef.current * safeTotalSeconds) / 100);
    const payload = await lmsRequest(`/api/lms/lessons/${lessonId}/heartbeat`, {
      method: "POST",
      body: {
        position_seconds: Math.max(0, Math.floor(localSeconds)),
        tab_visible: visibleRef.current,
        client_ts: new Date().toISOString(),
      },
    });
    if (payload?.position_seconds != null && Number.isFinite(Number(payload.position_seconds))) {
      const serverPosition = Math.max(0, Number(payload.position_seconds));
      const nextProgress = Math.max(0, Math.min(100, (serverPosition / safeTotalSeconds) * 100));
      setProgress(nextProgress);
      progressRef.current = nextProgress;
      maxAllowedSecondsRef.current = Math.max(maxAllowedSecondsRef.current, serverPosition);
      if (!videoRef.current || videoRef.current.paused) {
        setDisplayCurrentSeconds(Math.min(safeTotalSeconds, serverPosition));
      }
    }
    return payload || null;
  }, [canTrack, lmsRequest, lessonId, safeTotalSeconds]);

  useEffect(() => {
    const sendVisibilityEvent = async (isVisible) => {
      if (!canTrack || !lessonId) return;
      try {
        await lmsRequest(`/api/lms/lessons/${lessonId}/event`, {
          method: "POST",
          body: {
            event_type: "visibility",
            payload: { is_visible: Boolean(isVisible) },
            client_ts: new Date().toISOString(),
          },
        });
      } catch (_) {
        // silent: non-blocking anti-cheat event
      }
    };

    const handleVisibilityChange = () => {
      const isVisible = !document.hidden;
      visibleRef.current = isVisible;
      if (!isVisible && playing) {
        setPlaying(false);
        if (videoRef.current && !videoRef.current.paused) {
          videoRef.current.pause();
        }
        setTabHidden(true);
        setTimeout(() => setTabHidden(false), 3000);
      }
      sendVisibilityEvent(isVisible);
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, [canTrack, lmsRequest, lessonId, playing]);

  useEffect(() => {
    if (!playing || !canTrack || !lessonId) {
      clearInterval(heartbeatRef.current);
      return undefined;
    }

    heartbeatRef.current = setInterval(async () => {
      try {
        await sendHeartbeat();
      } catch (_) {
        // non-fatal
      }
    }, 5000);

    return () => clearInterval(heartbeatRef.current);
  }, [playing, canTrack, lessonId, sendHeartbeat]);

  const syncHeartbeatBeforeComplete = useCallback(async () => {
    if (!canTrack) return;
    await sendHeartbeat();
  }, [canTrack, sendHeartbeat]);

  const handleLoadedMetadata = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    const mediaDuration = Number(video.duration || 0);
    if (!Number.isFinite(mediaDuration) || mediaDuration <= 0) return;
    const safeDuration = Math.max(1, mediaDuration);
    setTotalSeconds((prev) => (Math.abs(prev - safeDuration) >= 0.25 ? safeDuration : prev));
    const resumePosition = Math.max(0, Math.min(mediaDuration, (progressRef.current / 100) * mediaDuration));
    if (resumePosition > 0 && Math.abs(Number(video.currentTime || 0) - resumePosition) > 1.5) {
      video.currentTime = resumePosition;
    }
    const normalizedProgress = Math.max(0, Math.min(100, Number(progressRef.current || 0)));
    const restoredAllowed = Math.max(0, (normalizedProgress / 100) * safeDuration);
    maxAllowedSecondsRef.current = Math.max(Number(video.currentTime || 0), restoredAllowed);
    setDisplayCurrentSeconds(Math.max(0, Number(video.currentTime || resumePosition || 0)));
  }, []);

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
    const nextProgress = Math.max(0, Math.min(100, (currentTime / safeDuration) * 100));
    setProgress(nextProgress);
    progressRef.current = nextProgress;
  }, [safeTotalSeconds, totalSeconds]);

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
      const correctedProgress = Math.max(0, Math.min(100, (restoredSeconds / duration) * 100));
      setProgress(correctedProgress);
      progressRef.current = correctedProgress;
      notifySeekBlocked();
      return;
    }
    video.currentTime = boundedSeconds;
    setDisplayCurrentSeconds(boundedSeconds);
    const nextProgress = Math.max(0, Math.min(100, (boundedSeconds / duration) * 100));
    setProgress(nextProgress);
    progressRef.current = nextProgress;
    if (canSeekForward) {
      maxAllowedSecondsRef.current = Math.max(maxAllowedSecondsRef.current, boundedSeconds);
    }
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
      const correctedProgress = Math.max(0, Math.min(100, (restoredSeconds / Math.max(1, Number(video.duration || safeTotalSeconds || 0))) * 100));
      setProgress(correctedProgress);
      progressRef.current = correctedProgress;
      notifySeekBlocked();
    }
  };

  const handleVideoPlay = () => {
    setPlaying(true);
  };

  const handleVideoPause = () => {
    setPlaying(false);
    void sendHeartbeat().catch(() => {});
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

  const formatVideoClock = (seconds) => {
    const safe = Math.max(0, Math.floor(Number(seconds || 0)));
    const minutes = Math.floor(safe / 60);
    const secs = safe % 60;
    return `${minutes}:${String(secs).padStart(2, "0")}`;
  };

  const handleComplete = useCallback(async () => {
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
      }
    } finally {
      setCompleting(false);
    }
  }, [completed, completing, onCompleteLesson, lesson, syncHeartbeatBeforeComplete, safeTotalSeconds]);

  useEffect(() => {
    if (completed || completing) return;
    if (progress < completionThreshold) return;
    if (autoCompleteTriggeredRef.current) return;
    autoCompleteTriggeredRef.current = true;
    handleComplete();
  }, [progress, completionThreshold, completed, completing, handleComplete]);

  const handleOpenMaterial = (material) => {
    const url = material?.url || material?.signed_url || material?.content_url;
    if (!url) return;
    window.open(url, "_blank", "noopener,noreferrer");
  };

  return (
    <div>
      {tabHidden && (
        <div className="mb-4 bg-amber-50 border border-amber-200 rounded-xl p-3 flex items-center gap-2 text-xs text-amber-700">
          <AlertTriangle size={14} /> Видео поставлено на паузу — вы переключили вкладку браузера
        </div>
      )}

      <div className="bg-slate-900 rounded-2xl overflow-hidden mb-6 relative aspect-video">
        {videoUrl ? (
          <>
            <video
              ref={videoRef}
              src={videoUrl}
              className="w-full h-full bg-black cursor-pointer"
              controls={false}
              playsInline
              preload="metadata"
              disablePictureInPicture
              controlsList="nodownload noplaybackrate nofullscreen"
              onClick={togglePlayback}
              onLoadedMetadata={handleLoadedMetadata}
              onTimeUpdate={handleTimeUpdate}
              onSeeking={handleSeeking}
              onPlay={handleVideoPlay}
              onPause={handleVideoPause}
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
            <div className="absolute left-0 right-0 bottom-0 p-3 bg-gradient-to-t from-black/80 via-black/45 to-transparent" onClick={(event) => event.stopPropagation()}>
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

      <div className="flex items-center gap-4 mb-6 p-4 bg-white rounded-xl border border-slate-200">
        <button
          type="button"
          onClick={togglePlayback}
          disabled={!videoUrl}
          className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 disabled:opacity-50 disabled:cursor-not-allowed text-xs font-semibold text-slate-700 transition-colors"
        >
          {playing ? <Pause size={13} /> : <Play size={13} />}
          {playing ? "Пауза" : "Воспроизвести"}
        </button>
        <div className="flex items-center gap-2 text-xs text-slate-500"><Eye size={13} /> Просмотрено: {Math.round(progress)}%</div>
        <div className="flex items-center gap-2 text-xs text-emerald-600 font-medium"><CheckCircle size={13} /> Засчитывается только реальное время</div>
        <div className="ml-auto text-xs text-slate-500 flex items-center gap-2"><Clock size={13} /> {formatVideoClock(currentSeconds)} / {formatVideoClock(safeTotalSeconds)}</div>
        {completed && <div className="flex items-center gap-1.5 text-xs text-emerald-700 bg-emerald-50 px-2.5 py-1 rounded-full font-semibold"><CheckCircle size={12} /> Урок завершён</div>}
      </div>

      {!completed && (
        <div className="mb-6 flex justify-end">
          <button
            onClick={handleComplete}
            disabled={completing || progress < completionThreshold}
            className="inline-flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold px-4 py-2.5 rounded-xl transition-colors"
          >
            {completing ? <RefreshCw size={14} className="animate-spin" /> : <CheckCircle size={14} />}
            {completing ? "Сохранение..." : "Отметить как завершенный"}
          </button>
        </div>
      )}

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
            <div className="text-sm text-slate-600 leading-relaxed space-y-3">
              {transcriptText ? (
                <p>{transcriptText}</p>
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
                const size = material?.mime_type ? String(material.mime_type) : "Файл";
                return (
                  <div key={`${material?.id || idx}-${label}`} onClick={() => handleOpenMaterial(material)} className="flex items-center gap-3 p-3 bg-slate-50 rounded-xl hover:bg-slate-100 transition-colors cursor-pointer">
                    <div className="w-9 h-9 bg-indigo-100 rounded-lg flex items-center justify-center"><FileCheck size={16} className="text-indigo-600" /></div>
                    <div className="flex-1"><p className="text-xs font-medium text-slate-800">{label}</p><p className="text-[10px] text-slate-400">{size}</p></div>
                    <Download size={14} className="text-slate-400" />
                  </div>
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
    const requests = questions
      .map((question) => {
        const userAnswer = answers[question.id];
        const hasAnswer = question.type === "multiple"
          ? Array.isArray(userAnswer) && userAnswer.length > 0
          : question.type === "text"
            ? Boolean(String(userAnswer || "").trim())
            : userAnswer !== undefined && userAnswer !== null && userAnswer !== "";
        if (!hasAnswer) return null;
        return lmsRequest(`/api/lms/tests/attempts/${attempt.id}/answer`, {
          method: "PATCH",
          body: {
            question_id: question.id,
            answer_payload: buildAnswerPayloadForApi(question, userAnswer),
          },
        });
      })
      .filter(Boolean);
    if (requests.length === 0) return;
    await Promise.all(requests);
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
    return (
      <div className="bg-white rounded-2xl border border-slate-200 p-8 text-center max-w-xl mx-auto">
        <div className="w-16 h-16 bg-violet-100 rounded-2xl flex items-center justify-center mx-auto mb-5"><HelpCircle size={28} className="text-violet-600" /></div>
        <h2 className="text-xl font-bold text-slate-900 mb-2">{lesson?.title || "Тест"}</h2>
        <p className="text-sm text-slate-500 mb-6">Тест будет загружен из LMS API</p>
        <div className={`flex items-center justify-center gap-2 text-sm mb-6 p-3 rounded-xl ${attemptsLeft <= 1 ? "bg-red-50 text-red-700" : "bg-slate-50 text-slate-600"}`}>
          <RefreshCw size={14} />
          <span>Доступно попыток: <strong>{attemptsLeft}</strong> из {lesson?.maxAttempts ?? course?.maxAttempts ?? 3}</span>
        </div>
        <button onClick={startApiQuiz} disabled={attemptsLeft <= 0 || loadingStart} className="w-full bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-3 rounded-xl text-sm transition-colors">
          {loadingStart ? "Подготовка..." : attemptsLeft <= 0 ? "Попытки исчерпаны" : "Начать тест"}
        </button>
      </div>
    );
  }

  if (quizView === "result" && result) {
    const scorePercent = Number(result?.score_percent || 0);
    const passed = scorePercent >= passThreshold;
    return (
      <div className="space-y-5 max-w-2xl mx-auto">
        <div className="bg-white rounded-2xl border border-slate-200 p-8 text-center">
          {autoFinished && <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 mb-5 text-xs text-amber-700">Тест завершён автоматически по времени</div>}
          <h2 className="text-3xl font-bold text-slate-900 mb-1">{Math.round(scorePercent)}%</h2>
          <p className={`text-sm font-semibold mb-1 ${passed ? "text-emerald-600" : "text-red-600"}`}>{passed ? "Тест пройден" : "Тест не пройден"}</p>
          <p className="text-sm text-slate-500">Порог: {Math.round(passThreshold)}%</p>
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
            {q.options.map((opt) => {
              const selected = Array.isArray(answers[q.id]) && answers[q.id].includes(opt.id);
              return (
                <button key={opt.id} onClick={() => { setAnswers((prev) => { const oldValue = Array.isArray(prev[q.id]) ? prev[q.id] : []; const nextValue = oldValue.includes(opt.id) ? oldValue.filter((id) => id !== opt.id) : [...oldValue, opt.id]; return { ...prev, [q.id]: nextValue }; }); }} className={`w-full text-left flex items-center gap-4 px-5 py-4 rounded-xl border-2 transition-all text-sm ${selected ? "border-indigo-500 bg-indigo-50 text-indigo-800" : "border-slate-200 hover:border-indigo-200 hover:bg-indigo-50/50 text-slate-700"}`}>
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
      <div className="space-y-5 max-w-2xl mx-auto">
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
          <div className="flex gap-3">
            {!passed && attemptsLeft > 1 && (
              <button onClick={() => { setAnswers({}); setCurrentQ(0); setTimeLeft(20*60); setAutoFinished(false); setTextInput(""); setQuizView("active"); }} className="flex-1 bg-slate-100 hover:bg-slate-200 text-slate-700 font-semibold py-3 rounded-xl text-sm transition-colors flex items-center justify-center gap-2">
                <RefreshCw size={14} /> Пересдать
              </button>
            )}
            {!passed && attemptsLeft <= 1 && (
              <div className="flex-1 bg-red-50 text-red-700 font-semibold py-3 rounded-xl text-sm flex items-center justify-center gap-2">
                <XCircle size={14} /> Попытки исчерпаны
              </div>
            )}
            <button className={`flex-1 font-semibold py-3 rounded-xl text-sm transition-colors ${passed ? "bg-indigo-600 hover:bg-indigo-700 text-white" : "bg-indigo-50 text-indigo-700 hover:bg-indigo-100"}`}>
              {passed ? "Следующий урок →" : "Просмотр ошибок"}
            </button>
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
            <p className="text-xs text-slate-400 mb-2">Выберите все правильные ответы</p>
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

function CertificatesView({ certificates = [], onDownload }) {
  const safeCertificates = Array.isArray(certificates) ? certificates : [];
  return (
    <div>
      {safeCertificates.length === 0 ? (
        <div className="text-center py-20 text-slate-400"><Award size={40} className="mx-auto mb-3 opacity-30" /><p className="text-sm">Сертификатов пока нет</p></div>
      ) : (
        <div className="grid grid-cols-2 gap-5">
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

function NotificationsView({ notifications = [], onRead }) {
  const iconMap = { deadline: AlertCircle, completed: CheckCircle, assigned: BookOpen, certificate: Award };
  const colorMap = { deadline: "text-amber-600 bg-amber-50", completed: "text-emerald-600 bg-emerald-50", assigned: "text-indigo-600 bg-indigo-50", certificate: "text-violet-600 bg-violet-50" };
  const safeNotifications = Array.isArray(notifications) ? notifications : [];
  return (
    <div className="space-y-3 max-w-2xl">
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

function CourseBuilder({ onBack, lmsRequest, canUseManagerApi, learners = [], adminCourses = [], emitToast, onAfterSave }) {
  const buildLesson = useCallback((overrides = {}) => ({
    id: Date.now() + Math.floor(Math.random() * 1000),
    title: "Новый урок",
    type: "video",
    description: "",
    durationSeconds: 15 * 60,
    completionThreshold: 95,
    contentText: "",
    materials: [],
    quizQuestionsPerTest: 5,
    quizTimeLimitMinutes: 20,
    quizPassingScore: 80,
    quizAttemptLimit: 3,
    quizRandomOrder: true,
    quizShowExplanations: true,
    quizQuestions: [],
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
    category: "Безопасность",
    mandatory: false,
    passingScore: 80,
    maxAttempts: 3,
    deadline: "",
    questionsPerTest: 5,
    finalTestTimeLimitMinutes: 20,
    randomOrder: true,
    showExplanations: true,
    coverUrl: "",
    coverBucket: "",
    coverBlobPath: "",
    skills: [],
  });
  const [newSkill, setNewSkill] = useState("");
  const [lessonMaterialLink, setLessonMaterialLink] = useState("");
  const [selectedLessonId, setSelectedLessonId] = useState(null);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
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

  const uploadSingleMaterial = useCallback(async (file, materialType = "file") => {
    if (typeof lmsRequest !== "function") {
      throw new Error("LMS API не подключен");
    }
    const formData = new FormData();
    formData.append("file", file);
    formData.append("material_type", materialType);
    formData.append("title", file?.name || "Материал");
    const payload = await lmsRequest("/api/lms/admin/materials/upload", {
      method: "POST",
      body: formData,
    });
    const first = Array.isArray(payload?.uploaded) ? payload.uploaded[0] : null;
    if (!first) throw new Error("Файл не загрузился");
    return first;
  }, [lmsRequest]);

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
      const detectedDuration = await readVideoDurationSeconds(file);
      const nextDurationSeconds = detectedDuration != null
        ? Math.max(30, Math.round(detectedDuration))
        : Math.max(30, Number(selectedLessonModel?.durationSeconds || 0) || 15 * 60);
      const uploaded = await uploadSingleMaterial(file, "video");
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
            const lessonType = rawType === "text" ? "text" : rawType === "quiz" ? "quiz" : "video";
            const description = String(lessonItem?.description || "").trim();
            const contentText = String(lessonItem?.contentText || "").trim();

            if (lessonType === "quiz") {
              const quizQuestions = mapQuestionsToPayload(lessonItem?.quizQuestions);
              if (quizQuestions.length > 0) {
                const maxQuizQuestions = Math.max(1, quizQuestions.length);
                const quizQuestionsPerTest = Math.max(1, Math.min(maxQuizQuestions, Number(lessonItem?.quizQuestionsPerTest || maxQuizQuestions)));
                const defaultQuizMinutes = Math.max(10, Math.ceil(quizQuestionsPerTest * 1.5));
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

            const rawMaterials = Array.isArray(lessonItem?.materials) ? lessonItem.materials : [];
            let mappedMaterials = rawMaterials
              .map((materialItem, materialIndex) => {
                const materialType = String(materialItem?.material_type || materialItem?.type || "file").toLowerCase();
                const safeType = ["video", "pdf", "link", "text", "file"].includes(materialType) ? materialType : "file";
                const contentUrl = String(materialItem?.content_url || materialItem?.signed_url || materialItem?.url || "").trim();
                const materialText = safeType === "text"
                  ? String(materialItem?.content_text || contentText || "").trim()
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
                mime_type: "text/plain",
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
                  mime_type: "text/plain",
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
              duration_seconds: Math.max(30, Number(lessonItem?.durationSeconds || (lessonType === "video" ? 15 * 60 : 8 * 60))),
              allow_fast_forward: false,
              completion_threshold: Math.max(1, Math.min(100, Number(lessonItem?.completionThreshold || 95))),
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
      time_limit_minutes: Math.max(1, Number(settings.finalTestTimeLimitMinutes || 20)),
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
          title,
          description: String(settings.description || "").trim(),
          category: String(settings.category || "").trim(),
          pass_threshold: Number(settings.passingScore || 80),
          attempt_limit: attemptLimit,
          cover_url: String(settings.coverUrl || "").trim() || null,
          cover_bucket: String(settings.coverBucket || "").trim() || null,
          cover_blob_path: String(settings.coverBlobPath || "").trim() || null,
          skills: Array.isArray(settings.skills) ? settings.skills : [],
          modules: modulesPayload,
          tests: testsPayload,
        },
      });
      const nextCourseId = Number(payload?.course_id || 0) || null;
      setCreatedCourseId(nextCourseId);
      setAssignmentCourseId(nextCourseId);
      setSaved(true);
      emitToast?.("Курс сохранен в LMS", "success");
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

  const effectiveAssignmentCourseId = Number(assignmentCourseId || createdCourseId || adminCourses?.[0]?.id || 0) || null;

  const handleAssignSelected = async () => {
    if (!canUseManagerApi) {
      emitToast?.("Недостаточно прав для назначения курса", "error");
      return;
    }
    if (!effectiveAssignmentCourseId) {
      emitToast?.("Сначала сохраните курс или выберите курс из списка", "error");
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
  const selectedLessonVideoDurationSeconds = Math.max(
    0,
    Number(selectedLessonVideoMaterial?.metadata?.duration_seconds || selectedLessonModel?.durationSeconds || 0)
  );
  const selectedLessonQuizQuestions = Array.isArray(selectedLessonModel?.quizQuestions) ? selectedLessonModel.quizQuestions : [];
  const selectedLessonExtraMaterials = selectedLessonMaterials
    .map((item, originalIndex) => ({ ...item, _originalIndex: originalIndex }))
    .filter((item) => {
      const materialType = String(item?.material_type || item?.type || "").toLowerCase();
      if (selectedLessonModel?.type === "text") return materialType !== "text";
      if (selectedLessonModel?.type === "video") return materialType !== "video" && materialType !== "text";
      if (selectedLessonModel?.type === "quiz") return false;
      return materialType !== "video";
    });

  const applyLessonType = useCallback((lessonId, nextType) => {
    if (!lessonId) return;
    const normalizedType = String(nextType || "video").toLowerCase();
    const maxAttemptsRaw = settings.maxAttempts === "∞" ? 999 : Number(settings.maxAttempts);
    const fallbackAttemptLimit = Number.isFinite(maxAttemptsRaw) ? Math.max(1, maxAttemptsRaw) : 3;
    updateLessonById(lessonId, (prev) => {
      if (normalizedType === "quiz") {
        const currentQuestions = Array.isArray(prev?.quizQuestions) ? prev.quizQuestions : [];
        const defaultMinutes = Math.max(1, Number(prev?.quizTimeLimitMinutes || 20));
        return {
          ...prev,
          type: "quiz",
          completionThreshold: 100,
          durationSeconds: Math.max(60, defaultMinutes * 60),
          quizQuestionsPerTest: Math.max(1, Number(prev?.quizQuestionsPerTest || 5)),
          quizTimeLimitMinutes: defaultMinutes,
          quizPassingScore: Math.max(1, Math.min(100, Number(prev?.quizPassingScore || settings.passingScore || 80))),
          quizAttemptLimit: Math.max(1, Number(prev?.quizAttemptLimit || fallbackAttemptLimit)),
          quizRandomOrder: prev?.quizRandomOrder !== false,
          quizShowExplanations: prev?.quizShowExplanations !== false,
          quizQuestions: currentQuestions.length > 0 ? currentQuestions : [createQuestionTemplate("single")],
          materials: [],
        };
      }
      if (normalizedType === "text") {
        return { ...prev, type: "text", completionThreshold: 100 };
      }
      return { ...prev, type: "video" };
    });
  }, [updateLessonById, settings.maxAttempts, settings.passingScore, createQuestionTemplate]);

  return (
    <div className="max-w-screen-xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Конструктор курса</h1>
          <p className="text-sm text-slate-500 mt-0.5">Создание и редактирование учебных материалов</p>
        </div>
        <button onClick={handleSave} disabled={saving} className={`flex items-center gap-2 px-5 py-2.5 rounded-xl font-semibold text-sm transition-all disabled:opacity-60 disabled:cursor-not-allowed ${saved ? "bg-emerald-600 text-white" : "bg-indigo-600 hover:bg-indigo-700 text-white shadow-sm shadow-indigo-200"}`}>
          {saving ? <><RefreshCw size={15} className="animate-spin" /> Сохранение...</> : saved ? <><CheckCircle size={15} /> Сохранено</> : <><Save size={15} /> Сохранить</>}
        </button>
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
        <div className="grid grid-cols-2 gap-6">
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
                  <textarea rows={4} value={settings.description} onChange={e => setSettings(p => ({ ...p, description: e.target.value }))} className="w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:border-indigo-400 transition-all resize-none" placeholder="Краткое описание курса..." />
                </div>
                <div>
                  <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Категория</label>
                  <select value={settings.category} onChange={e => setSettings(p => ({ ...p, category: e.target.value }))} className="w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all">
                    <option>Безопасность</option><option>Менеджмент</option><option>Soft Skills</option><option>Финансы</option><option>Аналитика</option>
                  </select>
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
                <div>
                  <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Дедлайн</label>
                  <input type="date" value={settings.deadline} onChange={e => setSettings(p => ({ ...p, deadline: e.target.value }))} className="w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all" />
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
        <div className="grid grid-cols-5 gap-6">
          <div className="col-span-3 space-y-4">
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
                      const types = [{ id: "video", icon: Video, label: "Видео" }, { id: "text", icon: FileText, label: "Текст" }, { id: "quiz", icon: HelpCircle, label: "Тест" }];
                      const LIcon = lessonIcons[l.type];
                      return (
                        <div key={l.id} onClick={() => setSelectedLessonId(selectedLessonId === l.id ? null : l.id)} className={`flex items-center gap-3 p-3 rounded-xl border-2 cursor-pointer transition-all ${selectedLessonId === l.id ? "border-indigo-300 bg-indigo-50" : "border-transparent hover:border-slate-200 hover:bg-slate-50"}`}>
                          <GripVertical size={13} className="text-slate-300 cursor-grab flex-shrink-0" />
                          <div className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ${l.type === "video" ? "bg-blue-100 text-blue-600" : l.type === "text" ? "bg-emerald-100 text-emerald-600" : "bg-violet-100 text-violet-600"}`}><LIcon size={14} /></div>
                          <input value={l.title} onChange={e => setModules(p => p.map(m => m.id === mod.id ? { ...m, lessons: m.lessons.map(ls => ls.id === l.id ? { ...ls, title: e.target.value } : ls) } : m))} onClick={e => e.stopPropagation()} className="flex-1 text-sm font-medium text-slate-800 bg-transparent focus:outline-none" />
                          <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
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
          <div className="col-span-2">
            <div className="bg-white rounded-2xl border border-slate-200 p-5 sticky top-24">
              {selectedLessonModel ? (
                <>
                  <h3 className="text-sm font-semibold text-slate-900 mb-4">Редактор урока</h3>
                  <div className="space-y-4">
                    <div>
                      <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Описание урока</label>
                      <textarea
                        rows={3}
                        value={selectedLessonModel.description || ""}
                        onChange={(e) => updateLessonById(selectedLessonModel.id, { description: e.target.value })}
                        className="w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:border-indigo-400 transition-all resize-none"
                        placeholder="Что узнает сотрудник в этом уроке..."
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
                              value={Math.max(1, Number(selectedLessonModel.quizQuestionsPerTest || 1))}
                              onChange={(e) => updateLessonById(selectedLessonModel.id, { quizQuestionsPerTest: Math.max(1, Number(e.target.value || 1)) })}
                              className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all"
                            />
                          </div>
                          <div>
                            <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Лимит времени (мин)</label>
                            <input
                              type="number"
                              min={1}
                              value={Math.max(1, Number(selectedLessonModel.quizTimeLimitMinutes || 20))}
                              onChange={(e) => {
                                const nextMinutes = Math.max(1, Number(e.target.value || 20));
                                updateLessonById(selectedLessonModel.id, { quizTimeLimitMinutes: nextMinutes, durationSeconds: Math.max(60, nextMinutes * 60) });
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
                              value={Math.max(1, Math.min(100, Number(selectedLessonModel.quizPassingScore || settings.passingScore || 80)))}
                              onChange={(e) => updateLessonById(selectedLessonModel.id, { quizPassingScore: Math.max(1, Math.min(100, Number(e.target.value || 80))) })}
                              className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all"
                            />
                          </div>
                          <div>
                            <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Максимум попыток</label>
                            <input
                              type="number"
                              min={1}
                              value={Math.max(1, Number(selectedLessonModel.quizAttemptLimit || 1))}
                              onChange={(e) => updateLessonById(selectedLessonModel.id, { quizAttemptLimit: Math.max(1, Number(e.target.value || 1)) })}
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
                            <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Длительность</label>
                            {selectedLessonModel.type === "text" ? (
                              <input
                                type="number"
                                min={30}
                                value={Math.max(30, Number(selectedLessonModel.durationSeconds || 0))}
                                onChange={(e) => updateLessonById(selectedLessonModel.id, { durationSeconds: Math.max(30, Number(e.target.value || 0)) })}
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
                              value={Math.max(1, Math.min(100, Number(selectedLessonModel.completionThreshold || 95)))}
                              onChange={(e) => updateLessonById(selectedLessonModel.id, { completionThreshold: Math.max(1, Math.min(100, Number(e.target.value || 95))) })}
                              className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-700 focus:outline-none focus:border-indigo-400 transition-all"
                            />
                          </div>
                        </div>

                        {selectedLessonModel.type === "text" && (
                          <div>
                            <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Текст урока</label>
                            <textarea
                              rows={7}
                              value={selectedLessonModel.contentText || ""}
                              onChange={(e) => updateLessonById(selectedLessonModel.id, { contentText: e.target.value })}
                              className="w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:border-indigo-400 transition-all resize-y"
                              placeholder="Введите текст урока..."
                            />
                          </div>
                        )}

                        {selectedLessonModel.type === "video" && (
                          <>
                            <div>
                              <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Транскрипт видео</label>
                              <textarea
                                rows={5}
                                value={selectedLessonModel.contentText || ""}
                                onChange={(e) => updateLessonById(selectedLessonModel.id, { contentText: e.target.value })}
                                className="w-full px-4 py-3 bg-slate-50 border border-slate-200 rounded-xl text-sm text-slate-900 placeholder-slate-400 focus:outline-none focus:border-indigo-400 transition-all resize-y"
                                placeholder="Введите текст транскрипта видео..."
                              />
                            </div>
                            <div>
                              <label className="text-xs font-semibold text-slate-600 mb-1.5 block">Видеофайл</label>
                              <button
                                type="button"
                                onClick={() => lessonVideoInputRef.current?.click()}
                                disabled={lessonUploading}
                                className="w-full border-2 border-dashed border-slate-200 hover:border-indigo-300 rounded-xl py-4 text-xs text-slate-500 hover:text-indigo-600 flex items-center justify-center gap-2 transition-all disabled:opacity-60 disabled:cursor-not-allowed"
                              >
                                {lessonUploading ? <RefreshCw size={14} className="animate-spin" /> : <Upload size={14} />}
                                {lessonUploading ? "Загрузка..." : "Загрузить видео"}
                              </button>
                              {selectedLessonVideoMaterial && (
                                <div className="mt-2 text-xs text-slate-600 bg-slate-50 border border-slate-200 rounded-xl px-3 py-2">
                                  Прикреплено: {selectedLessonVideoMaterial?.metadata?.uploaded_file_name || selectedLessonVideoMaterial?.title || "Видео"}
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
                <input type="number" value={Math.max(1, Number(settings.finalTestTimeLimitMinutes || 20))} onChange={e => setSettings(p => ({ ...p, finalTestTimeLimitMinutes: Math.max(1, Number(e.target.value || 20)) }))} className="w-full px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-sm text-center focus:outline-none focus:border-indigo-400 transition-all" />
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
            <div className="space-y-2 max-h-80 overflow-y-auto">
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
                    {(Array.isArray(adminCourses) ? adminCourses : []).map((courseItem) => (
                      <option key={courseItem.id} value={courseItem.id}>{courseItem.title}</option>
                    ))}
                  </select>
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

// ─── ADMIN VIEW ───────────────────────────────────────────────────────────────

function AdminView({ tab, setTab, adminCourses = [], progressRows = [], attempts = [], loading = false, onOpenBuilder, onDeleteCourse }) {
  const [deletingCourseId, setDeletingCourseId] = useState(null);
  const tabs = [
    { id: "analytics", label: "Аналитика", icon: BarChart2 },
    { id: "employees", label: "Сотрудники", icon: Users },
    { id: "courses", label: "Курсы", icon: BookOpen },
  ];

  const safeProgressRows = Array.isArray(progressRows) ? progressRows : [];
  const safeAttempts = Array.isArray(attempts) ? attempts : [];
  const safeAdminCourses = Array.isArray(adminCourses) ? adminCourses : [];

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
    const prev = employeeMap.get(userId) || {
      id: userId,
      name: row?.user_name || `User #${userId}`,
      dept: row?.user_role || "—",
      courses: 0,
      completed: 0,
      avgScore: 0,
      overdue: 0,
      lastActive: "—",
      testTime: "0ч 00м",
      attempts: 0,
    };
    prev.courses += 1;
    if (String(row?.status || "").toLowerCase() === "completed") prev.completed += 1;
    if (String(row?.deadline_status || "").toLowerCase() === "red" && String(row?.status || "").toLowerCase() !== "completed") prev.overdue += 1;
    employeeMap.set(userId, prev);
  });

  let employeeRows = Array.from(employeeMap.values()).map((row) => {
    const agg = attemptAggByUser.get(Number(row.id));
    const avgScore = agg && agg.scoreCount > 0 ? Math.round(agg.scoreSum / agg.scoreCount) : row.avgScore;
    const totalDuration = agg ? agg.duration : 0;
    const hours = Math.floor(totalDuration / 3600);
    const minutes = Math.floor((totalDuration % 3600) / 60);
    return {
      ...row,
      avgScore,
      attempts: agg ? agg.count : row.attempts,
      testTime: `${hours}ч ${String(minutes).padStart(2, "0")}м`,
      lastActive: agg?.lastAt ? toRelativeTime(agg.lastAt.toISOString()) : row.lastActive,
    };
  });
  const courseStatMap = new Map();
  safeProgressRows.forEach((row) => {
    const courseId = Number(row?.course_id || 0);
    if (!courseId) return;
    const prev = courseStatMap.get(courseId) || { total: 0, completed: 0, lessons: 0 };
    prev.total += 1;
    if (String(row?.status || "").toLowerCase() === "completed") prev.completed += 1;
    prev.lessons = Math.max(prev.lessons, Number(row?.total_lessons || 0));
    courseStatMap.set(courseId, prev);
  });

  let courseRows = safeAdminCourses.map((item, index) => {
    const visual = pickCourseVisual(item?.id || index, item?.category || "");
    const stat = courseStatMap.get(Number(item?.id || 0)) || { total: 0, completed: 0, lessons: 0 };
    const progressPercent = stat.total > 0 ? Math.round((stat.completed / stat.total) * 100) : 0;
    return {
      id: Number(item?.id || index + 1),
      title: item?.title || `Курс #${item?.id || index + 1}`,
      category: item?.category || "Без категории",
      cover: visual.cover,
      color: visual.color,
      mandatory: false,
      duration: stat.lessons > 0 ? `${stat.lessons} уроков` : "—",
      lessons: stat.lessons || 0,
      maxAttempts: Number(item?.default_attempt_limit || 3),
      attemptsUsed: 0,
      rating: 0,
      status: progressPercent >= 100 ? "completed" : (progressPercent > 0 ? "in_progress" : "not_started"),
      progress: progressPercent,
    };
  });
  const failStatsRows = safeAttempts
    .filter((item) => item?.score_percent != null)
    .sort((a, b) => Number(a?.score_percent || 0) - Number(b?.score_percent || 0))
    .slice(0, 4)
    .map((item, index) => ({
      questionId: index + 1,
      text: item?.test_title || "Тест",
      failRate: Math.max(0, 100 - Math.round(Number(item?.score_percent || 0))),
      course: item?.course_title || "Курс",
    }));

  const overallComplete = employeeRows.length ? Math.round(employeeRows.reduce((a, e) => a + (e.courses ? (e.completed / e.courses) : 0), 0) / employeeRows.length * 100) : 0;
  const avgScore = employeeRows.length ? Math.round(employeeRows.reduce((a, e) => a + Number(e.avgScore || 0), 0) / employeeRows.length) : 0;
  const overdueCount = employeeRows.reduce((a, e) => a + Number(e.overdue || 0), 0);
  const completedCoursesCount = courseRows.filter((item) => item.status === "completed" || item.status === "completed_late").length;
  const inProgressCoursesCount = courseRows.filter((item) => ["in_progress", "waiting_test", "test_failed"].includes(item.status)).length;
  const notStartedCoursesCount = courseRows.filter((item) => item.status === "not_started").length;
  const overdueCoursesCount = courseRows.filter((item) => item.status === "overdue").length;
  const courseStatusTotal = Math.max(
    1,
    completedCoursesCount + inProgressCoursesCount + notStartedCoursesCount + overdueCoursesCount
  );
  const courseStatusRows = [
    { label: "Завершены в срок", count: completedCoursesCount, color: "bg-emerald-500" },
    { label: "В процессе", count: inProgressCoursesCount, color: "bg-blue-500" },
    { label: "Не начаты", count: notStartedCoursesCount, color: "bg-slate-300" },
    { label: "Просрочены", count: overdueCoursesCount, color: "bg-red-500" },
  ].map((item) => ({
    ...item,
    pct: Math.round((item.count / courseStatusTotal) * 100),
  }));

  const handleDeleteCourse = async (courseItem) => {
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

  return (
    <div className="max-w-screen-xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Панель администратора</h1>
          <p className="text-sm text-slate-500 mt-0.5">Управление курсами и прогрессом сотрудников</p>
          {loading && <p className="text-xs text-indigo-600 mt-1">Обновление данных...</p>}
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

      <div className="flex items-center gap-1 mb-8 bg-slate-100 p-1 rounded-xl w-fit">
        {tabs.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all ${tab === t.id ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}>
            <t.icon size={14} /> {t.label}
          </button>
        ))}
      </div>

      {tab === "analytics" && (
        <div>
          <div className="grid grid-cols-4 gap-4 mb-8">
            {[
              { label: "Сотрудников обучается", value: employeeRows.length, sub: "активных пользователей", icon: Users, color: "text-indigo-600 bg-indigo-50" },
              { label: "Средний прогресс", value: `${overallComplete}%`, sub: "завершения назначенных", icon: TrendingUp, color: "text-emerald-600 bg-emerald-50" },
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

          <div className="grid grid-cols-3 gap-6 mb-6">
            {/* Прогресс по курсам */}
            <div className="col-span-2 bg-white rounded-2xl border border-slate-200 p-6">
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
            <div className="bg-white rounded-2xl border border-slate-200 p-6">
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
          <div className="grid grid-cols-2 gap-6">
            <div className="bg-white rounded-2xl border border-slate-200 p-6">
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
            <div className="bg-white rounded-2xl border border-slate-200 p-6">
              <div className="flex items-center gap-2 mb-5">
                <Clock size={16} className="text-indigo-500" />
                <h3 className="text-sm font-semibold text-slate-900">Время на тесты и попытки</h3>
              </div>
              <div className="space-y-3">
                {employeeRows.map(e => (
                  <div key={e.id} className="flex items-center gap-3">
                    <div className="w-7 h-7 rounded-full bg-gradient-to-br from-indigo-400 to-violet-500 flex items-center justify-center text-white text-[10px] font-semibold flex-shrink-0">{e.name.split(" ").map(w => w[0]).join("").slice(0, 2)}</div>
                    <div className="flex-1 min-w-0"><p className="text-xs font-medium text-slate-800 truncate">{e.name}</p></div>
                    <div className="flex items-center gap-1.5 text-[10px] text-slate-500"><Clock size={9} />{e.testTime}</div>
                    <div className={`flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full ${e.attempts >= 12 ? "bg-red-50 text-red-700" : e.attempts >= 8 ? "bg-amber-50 text-amber-700" : "bg-slate-100 text-slate-600"}`}><RefreshCw size={8} />{e.attempts} поп.</div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {tab === "employees" && (
        <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-100 flex items-center gap-3">
            <div className="relative flex-1 max-w-xs"><Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" /><input placeholder="Поиск сотрудников..." className="w-full pl-9 pr-4 py-2 bg-slate-50 border border-slate-200 rounded-xl text-sm placeholder-slate-400 focus:outline-none focus:border-indigo-400 transition-all" /></div>
            <select className="px-3 py-2 bg-slate-50 border border-slate-200 rounded-xl text-xs text-slate-600 focus:outline-none"><option>Все отделы</option><option>HR</option><option>Разработка</option><option>Финансы</option></select>
          </div>
          <table className="w-full">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-100">
                {["Сотрудник", "Отдел", "Назначено", "Завершено", "Ср. балл", "Попытки", "Время тестов", "Просрочено"].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {employeeRows.map(e => (
                <tr key={e.id} className="border-b border-slate-50 hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-4">
                    <div className="flex items-center gap-3">
                      <div className="w-8 h-8 rounded-full bg-gradient-to-br from-indigo-400 to-violet-500 flex items-center justify-center text-white text-xs font-semibold flex-shrink-0">{e.name.split(" ").map(w => w[0]).join("").slice(0, 2)}</div>
                      <span className="text-sm font-medium text-slate-800">{e.name}</span>
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
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === "courses" && (
        <div className="space-y-3">
          {courseRows.map(c => {
            const st = statusConfig[c.status] || statusConfig.not_started;
            return (
              <div key={c.id} className="bg-white rounded-2xl border border-slate-200 p-5 flex items-center gap-5">
                <div className={`w-12 h-12 rounded-xl bg-gradient-to-br ${c.color} flex items-center justify-center text-xl flex-shrink-0`}>{c.cover}</div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <h3 className="text-sm font-semibold text-slate-900 truncate">{c.title}</h3>
                    {c.mandatory && <span className="text-[10px] font-semibold text-amber-600 bg-amber-50 px-2 py-0.5 rounded-full flex-shrink-0">Обязательный</span>}
                  </div>
                  <div className="flex items-center gap-3 text-xs text-slate-500">
                    <span>{c.duration}</span><span>·</span><span>{c.lessons} уроков</span><span>·</span>
                    <span className="flex items-center gap-1"><RefreshCw size={10} /> до {c.maxAttempts} попыток</span><span>·</span>
                    <span className="flex items-center gap-1"><Star size={10} className="text-amber-400 fill-amber-400" />{c.rating}</span>
                  </div>
                </div>
                <div className="flex items-center gap-4">
                  <div className="text-right"><p className="text-xs text-slate-400 mb-0.5">Прогресс</p><p className="text-sm font-bold text-slate-800">{c.progress}%</p></div>
                  <span className={`text-xs font-semibold px-2.5 py-1 rounded-full ${st.bg} ${st.text}`}>{st.label}</span>
                  <div className="flex items-center gap-1">
                    <button className="p-2 text-slate-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-xl transition-colors"><Edit size={15} /></button>
                    <button
                      onClick={() => { void handleDeleteCourse(c); }}
                      disabled={deletingCourseId === c.id}
                      title={deletingCourseId === c.id ? "Удаляем..." : "Удалить курс"}
                      className="p-2 text-slate-400 hover:text-red-600 hover:bg-red-50 rounded-xl transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {deletingCourseId === c.id ? <Clock size={15} /> : <Trash2 size={15} />}
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
