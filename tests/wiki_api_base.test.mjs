import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync, readdirSync } from 'node:fs';

/* Адрес запроса в разделе «Вики»: `base` УЖЕ содержит `/api/wiki`.
 *
 * Так объявлено в WikiView (`const base = ${apiBaseUrl}/api/wiki`), и все
 * компоненты раздела ходят как `${base}/articles`, `${base}/catalog`. Стоит
 * дописать префикс второй раз — и получается `/api/wiki/api/wiki/...`: сервер
 * отвечает 404, у 404 нет заголовков CORS, браузер показывает «CORS error», а
 * компонент с загрузкой в эффекте начинает долбить запрос по кругу. Со стороны
 * это выглядит как «раздел лагает», и настоящая причина по этой картинке не
 * читается вовсе — именно так и случилось с половиной «Перенос» 24.08.2026.
 *
 * Проверка текстовая, а не через запуск компонента, намеренно: ошибка живёт
 * ровно в строке шаблона, и поймать её надо во всех компонентах раздела разом,
 * включая те, которых ещё нет.
 */

const DIR = new URL('../src/components/wiki/', import.meta.url);

const sources = readdirSync(DIR)
    .filter((name) => name.endsWith('.jsx') || name.endsWith('.js'))
    .map((name) => [name, readFileSync(new URL(name, DIR), 'utf8')]);

test('в разделе есть компоненты — иначе проверка ничего не проверяет', () => {
    assert.ok(sources.length > 10, `нашлось всего ${sources.length} файлов`);
});

test('base не дописывается вторым /api/wiki', () => {
    const bad = [];
    for (const [name, text] of sources) {
        // `${base}/api/...` в любом виде — всегда ошибка: /api уже внутри base.
        const matches = text.match(/\$\{base\}\/api\b[^`'"]*/g) || [];
        matches.forEach((hit) => bad.push(`${name}: \${base}${hit.slice(8)}`));
    }
    assert.deepEqual(bad, [], `двойной префикс /api/wiki:\n  ${bad.join('\n  ')}`);
});

test('base объявлен там, где о нём говорит правило', () => {
    // Если базовый адрес однажды переедет, эта проверка обязана сломаться —
    // иначе тест выше будет сторожить правило, которого больше нет.
    const view = readFileSync(new URL('WikiView.jsx', DIR), 'utf8');
    assert.match(view, /const base = `\$\{apiBaseUrl\}\/api\/wiki`/);
});

test('колбэки родителя не попадают в зависимости загрузчиков', () => {
    /* showToast объявлен обычной функцией в теле App, а onOpenArticle/onReviewed
       приходят инлайновыми стрелками из WikiView — новая идентичность на каждый
       рендер родителя. Попади такая функция в deps useCallback загрузчика, и
       компонент запрашивает данные по кругу: ответ → setState → рендер → новый
       загрузчик → снова запрос. Лечится useStableCallback. */
    const text = readFileSync(new URL('WikiMigration.jsx', DIR), 'utf8');
    assert.match(text, /useStableCallback/,
                 'загрузчик очереди переноса обязан стабилизировать колбэки');
    const deps = text.match(/\}, \[[^\]]*\]\);/g) || [];
    const leaky = deps.filter((d) => /showToast|onReviewed|onOpenArticle/.test(d));
    assert.deepEqual(leaky, [], `нестабильные колбэки в зависимостях: ${leaky}`);
});

/* Журнал открыт «с должности СВ и выше» (решение владельца 25.08.2026), и
 * лестница должностей живёт ТОЛЬКО на сервере (wiki/access.py: may_read_audit),
 * а фронт получает готовый признак can_read_audit из /ping.
 *
 * Проверка текстовая и сторожит ровно одно: чтобы вкладку не начали показывать
 * по своей формуле. Стоит вывести её здесь по роли — и появится второй источник
 * истины: у супервайзера вкладка есть, а роут отвечает 403. Так уже расходился
 * гейт «Аналитики», пока формулу редактора не свели в одно место.
 */
test('вкладка «Журнал» рисуется по признаку сервера, а не по своей лестнице', () => {
    const view = readFileSync(new URL('WikiView.jsx', DIR), 'utf8');
    const tab = view.match(/\{ key: 'audit',[\s\S]{0,200}?\},/);
    assert.ok(tab, 'вкладка журнала не найдена — проверь тест');
    assert.match(tab[0], /canReadAudit/, 'журнал обязан спрашивать can_read_audit');
    assert.doesNotMatch(tab[0], /canManageAccess/,
                        'способность больше не открывает журнал — иначе супервайзер его не увидит');
    assert.match(view, /const canReadAudit = !!state\?\.can_read_audit;/,
                 'признак берётся из ответа /ping, а не выводится по роли');
    // Ролевых слов во фронте быть не должно вовсе: лестница — серверная.
    const audit = view.slice(Math.max(0, view.indexOf("key: 'audit'") - 400),
                             view.indexOf("key: 'audit'") + 400);
    assert.doesNotMatch(audit, /'sv'|super_admin|role_level/,
                        'лестница должностей во фронте — второй источник истины');
});
