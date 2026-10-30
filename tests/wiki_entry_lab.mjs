/*
 * Стенд для тестов входа в «Вики» (wiki_entry_first_frame, wiki_entry_after_answer)
 * и шарика помощника над ней (assistant_orb_wiki_tab).
 *
 * Не тест: имя без `.test.`, под шаблон `tests/*.test.mjs` файл не попадает.
 *
 * В проекте нет ни jsdom, ни testing-library — только react-dom/server и esbuild.
 * Серверный рендер эффектов не запускает, и этого хватает ровно на первый кадр.
 * Всё, что случается ПОСЛЕ него — кто и с каким пространством уходит за
 * данными, что делает ответ и отказ /ping, поздний ответ, таймер, — либо
 * держать текстом исходника (такие проверки зелены и на мёртвом коде), либо
 * дать компоненту прожить несколько кадров. Здесь второе:
 *
 *   • запросы axios никуда не уходят — ложатся в список, отвечает на них тест;
 *   • в «лабораторной» сборке модуль react подменён прослойкой: у КОРНЕВОГО
 *     компонента состояние хранится между рендерами, а эффекты выполняются
 *     после каждого рендера по своим зависимостям. Дочерние компоненты рисует
 *     обычный серверный рендер — своих эффектов они не выполняют, поэтому
 *     витрина проверяется отдельно, корнем.
 *
 * Граница приёма: обработчики нажатий недоступны (разметка статическая) —
 * «Обновить» и щелчки по вкладкам проверяются только сменой пропсов и адресом.
 */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
export const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const ROOT = new URL('../', import.meta.url);
export const path = (relative) => new URL(relative, ROOT).pathname.replace(/^\/([A-Za-z]:)/, '$1');
export const read = (relative) => readFileSync(path(relative), 'utf8');

// Сборка кладётся ВНУТРЬ node_modules: из системного temp внешние react,
// lucide-react и framer-motion не резолвятся. Каталог .cache не отслеживается git.
const OUT_DIR = path('node_modules/.cache/otp-wiki-entry-test/');
mkdirSync(OUT_DIR, { recursive: true });

/* Запросы не уходят никуда: каждый ложится в список, отвечает на него тест. */
const AXIOS_STUB = `
  const calls = (globalThis.__wikiEntryCalls ??= []);
  const request = (method) => (url, a, b) => new Promise((resolve, reject) => {
    const config = (method === 'get' || method === 'delete' ? a : b) || {};
    calls.push({
      method, url, params: config.params || {},
      answer: (data) => resolve({ data }),
      // Отказ — ответ сервера с кодом; сбой — ответа нет вовсе (сеть).
      refuse: (status = 403) => reject({ response: { status, data: { error: 'Отказ стенда' } } }),
      fail: () => reject(new Error('Network Error')),
    });
  });
  export default { get: request('get'), post: request('post'), patch: request('patch'),
                   put: request('put'), delete: request('delete') };
`;

/* Поиск в шапке всегда рисует выдачу через createPortal, а серверный рендер
   порталов не умеет. К входу в раздел он отношения не имеет (место его группы в
   шапке проверяется по самой группе), поэтому в сборке вместо него пустышка с
   теми же именами. */
const SEARCH_STUB = `
  export default function WikiSearch() { return null; }
  export const ArticleStamp = () => null;
  export const markedWord = () => null;
`;

/* Прослойка вместо react. Вне корня (lab.active === false) всё уходит в
   настоящий React; в корне шесть хуков живут на ячейках lab.slots — по порядку
   вызова, как в самом React. */
const HOOKED = ['useState', 'useRef', 'useMemo', 'useCallback', 'useEffect', 'useLayoutEffect'];
const passThrough = Object.keys(React)
    .filter((name) => /^[A-Za-z_$][\w$]*$/.test(name) && name !== 'default' && !HOOKED.includes(name))
    .map((name) => `export const ${name} = R.${name};`).join('\n');
const REACT_SHIM = `
  import { createRequire } from 'node:module';
  const R = createRequire(${JSON.stringify(path('package.json'))})('react');
  ${passThrough}
  const lab = () => { const L = globalThis.__wikiEntryLab; return L && L.active ? L : null; };
  const cell = (L, make) => { const i = L.cursor; L.cursor += 1; if (!L.slots[i]) L.slots[i] = make(); return L.slots[i]; };
  const differ = (a, b) => !a || !b || a.length !== b.length || a.some((x, i) => !Object.is(x, b[i]));

  export function useState(initial) {
    const L = lab();
    if (!L) return R.useState(initial);
    const slot = cell(L, () => {
      const made = { kind: 'state', value: typeof initial === 'function' ? initial() : initial };
      made.set = (next) => {
        const value = typeof next === 'function' ? next(made.value) : next;
        if (!Object.is(value, made.value)) { made.value = value; L.dirty = true; }
      };
      return made;
    });
    return [slot.value, slot.set];
  }
  export function useRef(initial) {
    const L = lab();
    if (!L) return R.useRef(initial);
    return cell(L, () => ({ kind: 'ref', ref: { current: initial } })).ref;
  }
  export function useMemo(make, deps) {
    const L = lab();
    if (!L) return R.useMemo(make, deps);
    const slot = cell(L, () => ({ kind: 'memo', fresh: true }));
    if (slot.fresh || differ(slot.deps, deps)) { slot.value = make(); slot.deps = deps; slot.fresh = false; }
    return slot.value;
  }
  export function useCallback(fn, deps) {
    const L = lab();
    if (!L) return R.useCallback(fn, deps);
    return useMemo(() => fn, deps);
  }
  const effect = (real) => (fn, deps) => {
    const L = lab();
    if (!L) return real(fn, deps);
    const slot = cell(L, () => ({ kind: 'effect', fresh: true }));
    if (slot.fresh || !deps || differ(slot.deps, deps)) { slot.fn = fn; L.queue.push(slot); }
    slot.deps = deps;
    slot.fresh = false;
    return undefined;
  };
  export const useEffect = effect(R.useEffect);
  export const useLayoutEffect = effect(R.useLayoutEffect);
  export default { ...R, useState, useRef, useMemo, useCallback, useEffect, useLayoutEffect };
`;

/* Раздел и его память входа — ОДНОЙ сборкой: у памяти состояние на уровне
   модуля, и тест обязан писать в тот же экземпляр, из которого читает раздел.
   Шарик помощника — туда же: он висит над разделом и гаснет по вкладке,
   которую раздел сообщает (WikiView: onTabShown). Его панель шарик грузит
   лениво, и в его кадрах её нет — она отдельным корнем. */
const ENTRY = `
  export { default as WikiView } from ${JSON.stringify(path('src/components/wiki/WikiView.jsx'))};
  export { default as WikiLibrary } from ${JSON.stringify(path('src/components/wiki/WikiLibrary.jsx'))};
  export { default as AssistantOrb } from ${JSON.stringify(path('src/components/assistant/AssistantOrb.jsx'))};
  export { default as AssistantPanel } from ${JSON.stringify(path('src/components/assistant/AssistantPanel.jsx'))};
  export { default as useAssistantChat } from ${JSON.stringify(path('src/components/assistant/useAssistantChat.js'))};
  export * as memory from ${JSON.stringify(path('src/components/wiki/entryMemory.js'))};
`;

/** Сборка раздела. lab: true — с прослойкой вместо react (кадры после первого). */
export async function loadSection({ lab = false } = {}) {
    /* Имена с номером процесса: два тестовых файла идут параллельно, а тесты из
       двух копий кода с общим node_modules (рабочая копия, снимок для проверки)
       иначе могли бы подхватить сборку друг друга. Всё временное убираем сразу:
       сборка весит 2,5 МБ и с именем на каждый процесс копилась бы в кэше без
       конца (за один день правки набралось 1,5 ГБ). */
    const own = `${lab ? 'lab' : 'frame'}-${process.pid}`;
    const axiosPath = join(OUT_DIR, `axios-stub-${own}.mjs`);
    const reactPath = join(OUT_DIR, `react-shim-${own}.mjs`);
    const outPath = join(OUT_DIR, `wiki-entry-${own}.mjs`);
    writeFileSync(axiosPath, AXIOS_STUB);
    writeFileSync(reactPath, REACT_SHIM);
    try {
        return await buildAndImport({ lab, axiosPath, reactPath, outPath });
    } finally {
        for (const file of [axiosPath, reactPath, outPath]) rmSync(file, { force: true });
    }
}

async function buildAndImport({ lab, axiosPath, reactPath, outPath }) {
    const result = await esbuild.build({
        stdin: { contents: ENTRY, resolveDir: path('.'), loader: 'js' },
        bundle: true,
        write: false,
        format: 'esm',
        platform: 'node',
        target: 'node18',
        jsx: 'transform',
        loader: { '.jsx': 'jsx', '.js': 'jsx', '.css': 'empty', '.png': 'empty', '.svg': 'empty', '.webp': 'empty' },
        // Библиотеки — снаружи: их ленивые половины (редактор, 3D-тренажёр) при
        // входе не нужны, и собирать их ради теста незачем.
        packages: 'external',
        alias: lab ? { axios: axiosPath, react: reactPath } : { axios: axiosPath },
        plugins: [{
            name: 'header-search-stub',
            setup(build) {
                build.onLoad({ filter: /[\\/]wiki[\\/]WikiSearch\.jsx$/ },
                    () => ({ contents: SEARCH_STUB, loader: 'jsx' }));
            },
        }],
        logLevel: 'silent',
    });
    writeFileSync(outPath, result.outputFiles[0].text);
    // Модуль после import живёт в памяти — файл больше не нужен.
    return import(`${pathToFileURL(outPath).href}?t=${Date.now()}`);
}

/* useLayoutEffect на сервере не выполняется, и React пишет об этом в консоль на
   каждый такой хук (их в разделе три десятка). Для этих тестов так и задумано —
   предупреждение глушим, остальное в консоль проходит как было. */
const consoleError = console.error;
console.error = (...args) => {
    if (String(args[0]).includes('useLayoutEffect does nothing on the server')) return;
    consoleError(...args);
};

/* Раздел читает адрес и localStorage прямо при первом рендере. */
export const storage = new Map();
globalThis.localStorage = {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => { storage.set(key, String(value)); },
    removeItem: (key) => { storage.delete(key); },
};

/** Адресная строка на время проверки: раздел читает из неё вкладку и статью. */
export async function withAddress(search, run) {
    globalThis.window = {
        location: { search, pathname: '/', hash: '', href: `http://portal.test/${search}` },
        history: { state: null, replaceState: () => {} },
    };
    try { return await run(); } finally { delete globalThis.window; }
}

/* ── Запросы ─────────────────────────────────────────────────────────────── */

export const API = 'http://api.test';
export const BASE = `${API}/api/wiki`;
export const HEADERS = {};
export const withAccessTokenHeader = () => HEADERS;   // стабильная ссылка, как в App.jsx

export const calls = (globalThis.__wikiEntryCalls ??= []);

/* Запросы, ушедшие с отметки `from`: «/ping», «/home?space_id=1». */
export const sent = (from = 0) => calls.slice(from).map((call) => {
    const space = call.params.space_id;
    return `${call.url.replace(BASE, '')}${space ? `?space_id=${space}` : ''}`;
});
/** Последний запрос по адресу с отметки `from`. */
export const pending = (suffix, from = 0) => {
    const found = calls.slice(from).filter((call) => call.url === `${BASE}${suffix}`);
    assert.ok(found.length > 0, `запрос ${suffix} не уходил`);
    return found[found.length - 1];
};

/* ── Что на экране ───────────────────────────────────────────────────────── */

/* Подпись вкладки — текст внутри кнопки ряда; имя в шапке или в тексте
   страницы под это не попадает. */
export const tabLabels = (html) => [...html.matchAll(/<button type="button" class="flex shrink-0 items-center gap-1\.5 rounded-xl px-3\.5 py-2 text-\[13px\][^"]*"><svg[\s\S]*?<\/svg>\s*([^<]+)<\/button>/g)]
    .map((match) => match[1].trim());

export const activeTab = (html) => {
    const match = html.match(/<button type="button" class="flex shrink-0 items-center gap-1\.5 rounded-xl px-3\.5 py-2 text-\[13px\] font-medium transition bg-white text-slate-900 shadow-sm"><svg[\s\S]*?<\/svg>\s*([^<]+)<\/button>/);
    return match ? match[1].trim() : '';
};

export const SKELETON = /aria-label="Загружаем вики"/;
export const TABS_SKELETON = /<div aria-hidden="true" class="flex gap-1 overflow-x-auto rounded-2xl bg-slate-100 p-1">/;
export const HEADER_HIDDEN = /sm:ml-auto sm:w-auto invisible"/;
export const HOME_SHIMMER = /h-\[220px\] overflow-hidden"><div class="sk-shimmer/;

/* ── Ответы сервера ──────────────────────────────────────────────────────── */

export const ALL_CAPS = {
    can_create: true, can_edit: true, can_publish: true, can_delete: true,
    can_manage_structure: true, can_manage_access: true,
};

export const pingAnswer = (over = {}) => ({
    ok: true,
    schema_ready: true,
    capabilities: ALL_CAPS,
    role_capabilities: ALL_CAPS,
    access_mode: 'auto',
    wiki_roles: [],
    grant_ceiling: 50,
    can_read_audit: true,
    can_grant_guest: true,
    guest_access: [],
    subjects: {},
    counters: { spaces: 1, sections: 4, articles_published: 21, articles_total: 233 },
    spaces: [{ id: 1, name: 'Таксопарки', code: 'igroup', icon: null, description: '', features: {}, guest_only: false }],
    ...over,
});

/* Читатель: ни способностей, ни права раздавать и публиковать. */
export const READER = {
    capabilities: {}, role_capabilities: {}, grant_ceiling: null,
    can_read_audit: false, can_grant_guest: false,
};

/* Пространство, где справочников и помощника нет вовсе. */
export const HR_SPACE = {
    id: 12, name: 'Кадры', code: 'hr', icon: null, description: '', guest_only: false,
    features: { parks: false, offices: false, cities: false, assistant: false,
                overview: false, library_park_rail: false },
};

export const PARK = {
    id: 5, slug: 'tt', name: 'Тест Такси', status: 'active', logo_url: null,
    commission: 3, promotions_count: 0,
};

/** Запомненный ответ о парках — тем же видом, каким его кладёт витрина. */
export const parksAnswer = (space, items = [], over = {}) => ({
    space, items, canManage: false, at: Date.now(), ...over,
});

export const sectionProps = (user, over = {}) => ({
    apiBaseUrl: API, withAccessTokenHeader, showToast: () => {}, user, ...over,
});

export const libraryProps = (over = {}) => ({
    base: BASE, headers: HEADERS, showToast: () => {}, structure: null, catalog: null,
    canCreate: false, canEdit: false, features: null, spaceId: 1, userId: 900,
    ready: true, ...over,
});

/** Первый кадр настоящим React: эффекты не выполняются. */
export const firstFrame = (Component, props) => renderToStaticMarkup(React.createElement(Component, props));

/* ── Корень, который живёт несколько кадров (только для сборки lab: true) ── */

const tick = () => new Promise((resolve) => { setImmediate(resolve); });

export function mount(Component, props) {
    const lab = { slots: [], cursor: 0, active: false, dirty: false, queue: [], html: '' };
    let current = props;
    const Root = (p) => {
        lab.cursor = 0;
        lab.active = true;
        try { return Component(p); } finally { lab.active = false; }
    };
    const frame = () => {
        globalThis.__wikiEntryLab = lab;
        lab.dirty = false;
        lab.queue = [];
        lab.html = renderToStaticMarkup(React.createElement(Root, current));
        for (const slot of lab.queue) {
            if (typeof slot.cleanup === 'function') slot.cleanup();
            slot.cleanup = slot.fn();
        }
    };
    /* Дать отработать ответам и перерисовывать, пока состояние меняется. */
    const settle = async () => {
        for (let round = 0; round < 40; round += 1) {
            await tick();
            if (!lab.dirty) return;
            frame();
        }
        throw new Error('корень не успокоился за 40 кадров — эффекты гоняют состояние по кругу');
    };
    frame();
    return {
        get html() { return lab.html; },
        // Лежит ли объект в состоянии корня (сам, не равный ему).
        holds: (value) => lab.slots.some((slot) => slot.kind === 'state' && slot.value === value),
        settle,
        update: (next) => { current = { ...current, ...next }; frame(); return settle(); },
        // Ушли из раздела: снимаются все эффекты корня, как при размонтировании.
        unmount: () => {
            for (const slot of lab.slots) {
                if (slot.kind === 'effect' && typeof slot.cleanup === 'function') slot.cleanup();
            }
        },
    };
}

/* Таймеры корня — в список: две секунды ожидания рельса тест проживает сам. */
export function withTimers(run) {
    const real = { set: globalThis.setTimeout, clear: globalThis.clearTimeout };
    const timers = [];
    globalThis.setTimeout = (fn, ms) => { const t = { fn, ms, cleared: false }; timers.push(t); return t; };
    globalThis.clearTimeout = (t) => { if (t && typeof t === 'object') t.cleared = true; else real.clear(t); };
    return Promise.resolve(run(timers)).finally(() => {
        globalThis.setTimeout = real.set;
        globalThis.clearTimeout = real.clear;
    });
}
