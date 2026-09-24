/* Перенос статьи из одного раздела в другой: что можно и что при этом будет.
 *
 * Чистые функции, а не часть панели, и по той же причине, что и sectionPicker.js
 * рядом: «а если статья лежит в трёх разделах», «а если раздел закрыт правами»,
 * «а если человек выбрал тот же раздел» — это арифметика над двумя списками, и
 * проверить её тестом (tests/wiki_article_move.test.mjs) дешевле, чем браузером.
 *
 * ПОЧЕМУ ПЕРЕНОС — ЭТО НЕ «СМЕНИТЬ РАЗДЕЛ». Статья лежит сразу в нескольких
 * разделах — обычное дело (см. articleWhere в WikiCatalog), и заменить весь
 * набор одним выбранным значило бы молча отвязать её от соседних веток: у
 * соседнего отдела регламент пропал бы, и никто бы не понял, куда он ушёл.
 * Поэтому перенос всегда адресный: ИЗ одного раздела В другой, остальные
 * остаются. Ровно это и считает сервер (POST /articles/<id>/move).
 */

/** Сколько разделов в дереве панели включают поиск. */
export const SEARCH_FROM = 10;

/** Разделы статьи числами: section_ids приходит из API как есть. */
export function currentSectionIds(article) {
    return (article?.section_ids || []).map(Number).filter((id) => !Number.isNaN(id));
}

/**
 * Разделы статьи, которые ЭТОМУ человеку показывают, — из них выбирают источник.
 *
 * names — карта «id → название» из каталога, то есть только видимые разделы
 * текущего пространства. Разделы вне неё отбрасываем молча, как и подпись «где
 * лежит статья»: статья бывает и в закрытой правами ветке, и в соседней вике, а
 * назвать их значит рассказать о содержимом чужого раздела.
 *
 * allowed — вправе ли человек ЗАБРАТЬ статью отсюда. Право то же самое, что и у
 * раздела-получателя, и это не перестраховка: сервер проверяет симметричную
 * разность разделов (wiki/routes_edit.py), потому что убрать статью из чужого
 * раздела — то же распоряжение его содержимым, только в другую сторону.
 */
export function moveSources(article, sections, names = null) {
    const byId = new Map((sections || []).map((s) => [Number(s.id), s]));
    const known = rightsAreKnown(sections);
    return currentSectionIds(article)
        .filter((id) => (names ? names.has(id) : byId.has(id)))
        .map((id) => {
            const section = byId.get(id);
            return {
                id,
                name: (names ? names.get(id) : null) || section?.name || `раздел ${id}`,
                allowed: !!section && mayPutArticle(section, known),
            };
        });
}

/**
 * Есть ли смысл предлагать перенос этой статьи.
 *
 * Статья вне дерева (наследие импорта) переносится всегда: забирать её не из
 * чего, спрашивается право только на раздел-получатель. У статьи с разделами
 * нужен хотя бы один, откуда её вправе забрать, — иначе пункт меню открывал бы
 * панель, в которой любое нажатие заканчивается отказом сервера.
 */
export function mayMove(article, sections, names = null) {
    if (!currentSectionIds(article).length) return true;
    return moveSources(article, sections, names).some((source) => source.allowed);
}

/**
 * Приехали ли с разделами права на них.
 *
 * Ответ /structure несёт по разделу permissions, ответ /catalog — нет. Пока
 * права неизвестны, гасить строки нельзя: пустое дерево хуже лишней строки, и
 * отказ всё равно скажет сервер. Ровно это правило уже держит выпадашка раздела
 * в редакторе (WikiEditor: creatableSections).
 */
export function rightsAreKnown(sections) {
    return (sections || []).some((s) => s.permissions);
}

/**
 * Вправе ли человек класть статьи в этот раздел.
 *
 * can_create в правиле раздела — то самое право, которое спрашивает сервер
 * (wiki/routes_edit.py: _target_section). Предлагать ветку, на которую он
 * ответит 403, значит выдавать отказ за поломку.
 */
export function mayPutArticle(section, known = true) {
    if (!known) return true;
    return !!section?.permissions?.can_create;
}

/**
 * Что произойдёт при переносе: откуда, куда и что останется.
 *
 * ready — можно ли спрашивать подтверждение. Не «выбрано ли что-то»: тот же
 * раздел и раздел, в котором статья уже лежит, выбрать нельзя (сервер на них
 * отвечает 409), и подтверждение под ними обещало бы работу, которой не будет.
 *
 * kept — разделы, которые статья сохранит, с именами; keptHidden — сколько из
 * них назвать нельзя (закрыты правами или лежат в соседней вике). Считаем и те
 * и другие: «статья останется ещё в двух разделах» — это ровно тот факт,
 * который отличает перенос от переезда, и умолчать о нём нельзя.
 */
export function movePlan(sections, article, fromId, toId) {
    const byId = new Map((sections || []).map((s) => [Number(s.id), s]));
    const known = rightsAreKnown(sections);
    const current = currentSectionIds(article);
    const from = fromId ? byId.get(Number(fromId)) || null : null;
    const to = toId ? byId.get(Number(toId)) || null : null;
    const others = current.filter(
        (id) => id !== Number(fromId) && id !== Number(toId));
    const kept = others.map((id) => byId.get(id)).filter(Boolean);
    /* Права спрашиваем и здесь, а не только у строк дерева. Строки погашены, но
       выбор живёт в состоянии экрана: раздел мог закрыться правами уже после
       того, как его выбрали, — и кнопка «Переместить» осталась бы живой над
       заведомым отказом сервера. */
    const mayTake = !fromId || (!!from && mayPutArticle(from, known));
    return {
        from,
        to,
        kept,
        keptHidden: others.length - kept.length,
        ready: !!to
            && mayTake
            && mayPutArticle(to, known)
            && Number(toId) !== Number(fromId)
            && !current.includes(Number(toId)),
    };
}

/** Строка «что останется» или null, когда статья лежит в одном разделе.
 *
 * Названием, а не числом, пока раздел один и его можно назвать: «останется ещё
 * в «Клиент»» отвечает на вопрос сразу, а «в 1 разделе» заставляет его задать.
 */
export function keptNote(plan) {
    const kept = plan?.kept || [];
    const total = kept.length + (plan?.keptHidden || 0);
    if (!total) return null;
    if (total === 1) {
        return kept.length === 1
            ? `Статья останется ещё в разделе «${kept[0].name}».`
            : 'Статья останется ещё в одном разделе.';
    }
    return `Статья останется ещё в ${total} разделах.`;
}

/* ── Одна статья в нескольких разделах ────────────────────────────────────
 *
 * Статью показывают в нескольких местах не копиями, а привязками: у текста один
 * источник, правка видна везде, картинки грузятся один раз. «Добавить в раздел»
 * кладёт ещё одну привязку (POST /articles/<id>/adopt), «Убрать из раздела»
 * снимает одну (POST /articles/<id>/detach). Сама статья ни там, ни там не
 * трогается, и последнюю привязку снять нельзя: статья без раздела не видна
 * никому, кроме автора. Права те же, что у переноса, — can_create в разделе.
 */

/** Есть ли хоть один раздел, куда статью можно ДОБАВИТЬ: разрешённый и не её. */
export function mayAdd(article, sections) {
    const known = rightsAreKnown(sections);
    const current = currentSectionIds(article);
    return (sections || []).some((s) => s.status !== 'archived'
        && !current.includes(Number(s.id))
        && mayPutArticle(s, known));
}

/**
 * Что будет при добавлении: куда, и где статья остаётся.
 *
 * kept здесь — ВСЕ нынешние разделы статьи: добавление ничего не забирает, и
 * сказать об этом надо прямо — иначе «добавить» читается как «переложить».
 */
export function addPlan(sections, article, toId) {
    const byId = new Map((sections || []).map((s) => [Number(s.id), s]));
    const known = rightsAreKnown(sections);
    const current = currentSectionIds(article);
    const to = toId ? byId.get(Number(toId)) || null : null;
    const kept = current.map((id) => byId.get(id)).filter(Boolean);
    return {
        from: null,
        to,
        kept,
        keptHidden: current.length - kept.length,
        ready: !!to && mayPutArticle(to, known) && !current.includes(Number(toId)),
    };
}

/**
 * Разделы, из которых статью можно УБРАТЬ, — источники переноса с одной оговоркой.
 *
 * Статья в единственном разделе не убирается ниоткуда: для «убрать отовсюду»
 * есть архив. Считаем по ВСЕМ её разделам, включая те, что человеку не
 * показывают: статья в «Клиенте» и в закрытой ветке соседнего отдела лежит в
 * двух местах, и убрать её из «Клиента» можно — она останется там.
 */
export function detachSources(article, sections, names = null) {
    const several = currentSectionIds(article).length > 1;
    return moveSources(article, sections, names)
        .map((source) => ({ ...source, allowed: several && source.allowed }));
}

/** Есть ли смысл предлагать «Убрать из раздела». */
export function mayDetach(article, sections, names = null) {
    return detachSources(article, sections, names).some((source) => source.allowed);
}

/**
 * Что будет при снятии с раздела: откуда, и где статья остаётся.
 *
 * ready — только если раздел выбран, он всё ещё раздел статьи и человек вправе
 * из него убирать. Остаток считается так же, как у переноса (keptNote).
 */
export function detachPlan(sections, article, fromId) {
    const byId = new Map((sections || []).map((s) => [Number(s.id), s]));
    const known = rightsAreKnown(sections);
    const current = currentSectionIds(article);
    const from = fromId ? byId.get(Number(fromId)) || null : null;
    const others = current.filter((id) => id !== Number(fromId));
    const kept = others.map((id) => byId.get(id)).filter(Boolean);
    return {
        from,
        to: null,
        kept,
        keptHidden: others.length - kept.length,
        ready: !!from
            && current.length > 1
            && current.includes(Number(fromId))
            && mayPutArticle(from, known),
    };
}

/**
 * Подпись «также в …» у строки внутри раздела.
 *
 * Без неё одна и та же статья в двух разделах выглядит как две одинаковые — и
 * её «на всякий случай» правят дважды или заводят третью. Называем только
 * видимые человеку разделы (names), как и подпись «где лежит»: чужую закрытую
 * ветку называть нельзя.
 */
export function alsoIn(article, names, hereId) {
    const others = currentSectionIds(article)
        .filter((id) => id !== Number(hereId))
        .map((id) => names?.get(id))
        .filter(Boolean);
    if (others.length === 0) return null;
    return others.length > 1
        ? `Также в «${others[0]}» и ещё ${others.length - 1}`
        : `Также в «${others[0]}»`;
}
