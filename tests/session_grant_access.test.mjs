import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const modal = readFileSync(join(here, '..', 'src', 'components', 'sessions', 'SessionUserModal.jsx'), 'utf8');
const app = readFileSync(join(here, '..', 'src', 'App.jsx'), 'utf8');

/**
 * Кнопка «Открыть доступ» у отдельной сессии.
 *
 * QR показывает сам оператор со своего экрана, и когда показать его нечем —
 * телефон разряжен, человек на удалёнке — доступ упирался в устройство, а не в
 * решение. Кнопка снимает это, но открывает персональные данные водителей,
 * поэтому её видимость и её проводка сторожатся, а не проверяются глазами.
 */

test('кнопка стоит слева от «Прервать» и обе гасят друг друга', () => {
    const grant = modal.indexOf("'Открыть доступ'");
    const revoke = modal.indexOf("'Прервать'");
    assert.ok(grant > 0 && revoke > 0, 'разметка изменилась — проверь тест');
    assert.ok(grant < revoke, 'менее опасное действие идёт первым, как в подвале модалки');

    // Пока идёт одна операция, вторая обязана быть недоступна: выдать доступ
    // сессии, которую в этот момент прерывают, — гонка с видимым результатом.
    // Сторожим участие обоих флагов, а не порядок операндов: порядок в `||`
    // смысла не несёт, и тест ломался бы от безобидного переписывания.
    const disabledAttrs = modal.match(/disabled=\{[^}]*\}/g) || [];
    const guarded = disabledAttrs.filter((a) => a.includes('isGranting') && a.includes('isRevoking'));
    assert.equal(guarded.length, 2, 'обе кнопки обязаны гаснуть на время любой из двух операций');
});

test('роль не участвует в видимости кнопки — ни в каком виде', () => {
    // Это буквально жалоба, с которой раздел переделывали дважды: кнопки не
    // было у тех, у кого гейт спрашивает QR. Условия соединяет сервер одним
    // флагом, потому что список гейтованных ролей уже расширяли на бэк-офис, и
    // любая копия здесь разошлась бы молча.
    const decl = modal.slice(modal.indexOf('const canGrantAccess'));
    const end = decl.indexOf(';');
    assert.ok(end > 0, 'объявление canGrantAccess изменилось — проверь тест');
    const body = decl.slice(0, end + 1);

    assert.ok(body.includes('can_grant_sensitive_access'), 'флаг сервера пропал');
    assert.ok(!/user_role|role|operator|hr_manager|accounting_manager|sensitive_access_required/.test(body),
        `роль снова участвует в видимости кнопки: ${body}`);

    // Проверяем самого стража подделкой: без этого «зелено» ничего не значит.
    const tampered = "const canGrantAccess = Boolean(detail?.user?.can_grant_sensitive_access) && detail?.user?.user_role !== 'sv';";
    assert.ok(/user_role|role|operator/.test(tampered), 'страж не поймал бы возврат роли');

    // Ролей нет и в текстах: их расширение уже делало формулировку враньём.
    const handler = app.slice(app.indexOf('const handleGrantAdminSessionAccess'));
    const texts = handler.slice(0, handler.indexOf('await axios.post'));
    assert.ok(texts.length > 50, 'обработчик изменился — проверь тест');
    assert.ok(!/оператор|супервайзер|бэк-офис|админ/i.test(texts),
        `роль зашита в текст подтверждения: ${texts}`);
});

test('открытой сессии кнопки нет — про неё говорит янтарная полоса', () => {
    assert.ok(modal.includes('canGrantAccess && !accessOpen'),
        'второй ответ на тот же вопрос — шум');
});

test('состояние выдачи — по конкретной сессии, а не одно на карточку', () => {
    // Одно булево на всю карточку показало бы «Открываем…» на всех десяти
    // сессиях сразу.
    assert.match(modal, /isGranting=\{grantingSessionId === session\.session_id\}/);
    assert.match(app, /const \[grantingSessionId, setGrantingSessionId\] = useState\(''\);/);
});

test('после выдачи карточка перечитывается', () => {
    // «Выдал» и «когда» приезжают только из ответа сервера: без перечитывания
    // сессия так и выглядела бы закрытой.
    const start = app.indexOf('const grantAccessFromDetail');
    assert.ok(start > 0, 'обёртка выдачи исчезла — проверь тест');
    const wrapper = app.slice(start);
    const end = wrapper.indexOf('const revokeAllForPerson');
    // Без этой проверки indexOf вернул бы −1, slice(0, −1) растянул бы окно до
    // конца файла, и тест зеленел бы, ничего не проверяя.
    assert.ok(end > 0, 'маркер границы обработчика исчез — проверь тест');
    const body = wrapper.slice(0, end);
    assert.ok(body.includes('onFetchAdminSessionUser(detailUserId)'));
    assert.ok(body.includes('setDetail(data)'));
});

test('проводка пропов сквозная и без обрывов', () => {
    // Два перегона: App → SessionsPanel и SessionsPanel → SessionUserModal.
    // Пункт меню в этом проекте уже терялся ровно так — объявлен в одной ветке,
    // забыт в другой.
    assert.equal(app.split('grantingSessionId={grantingSessionId}').length - 1, 2);
    for (const prop of ['onGrantAccess={grantAccessFromDetail}',
                        'handleGrantAdminSessionAccess={handleGrantAdminSessionAccess}']) {
        assert.equal(app.split(prop).length - 1, 1, `${prop} — ровно один раз`);
    }
    assert.ok(modal.includes('onGrantAccess={onGrantAccess}'));
});
