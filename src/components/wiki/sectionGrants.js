/* Три измерения выдачи доступа — то, чем ограничен раздающий.
 *
 * Потолок должности (grant_ceiling) и граница отдела (grant_departments) в
 * интерфейсе были с самого начала, а третье — КАКИЕ права человек вправе
 * поставить — появилось 21.08.2026 вместе с проверкой WIKI_GRANT_BEYOND_SELF
 * на сервере (wiki/routes_structure.py). Без него супервайзер ставил галочку
 * «Удалять», жал «Сохранить» и получал 403 на заполненной форме.
 *
 * Вынесено из WikiSectionAccess.jsx отдельным модулем ради теста: сам экран —
 * модалка с загрузкой по сети, и серверный рендер до этих галочек не доходит.
 */

/* Подписи способностей — зеркало CAPABILITY_TITLES из wiki/schema.py. Живут
   здесь, а не в WikiView, потому что читают их двое: витрина раздела и экран
   «Что человек видит в вики и почему». Две копии разошлись бы. */
export const CAPABILITY_LABELS = {
    can_read: 'Читать',
    can_create: 'Создавать',
    can_edit: 'Редактировать',
    can_delete: 'Удалять',
    can_publish: 'Публиковать',
    can_approve: 'Согласовывать',
    can_manage_users: 'Управлять людьми',
    can_manage_structure: 'Управлять структурой',
    can_manage_access: 'Управлять доступами',
};

/** Права правила — в том же порядке, что PERMISSION_COLUMNS на сервере. */
export const PERMISSION_KEYS = [
    'can_read', 'can_create', 'can_edit', 'can_publish', 'can_approve', 'can_delete',
];

/**
 * Проверка «эту галочку ставить можно».
 *
 * grantable — список из GET /access/section-rules. Не приехал или пуст (старый
 * ответ сервера) — ничего не гасим: онемевшая форма хуже лишней галочки, отказ
 * по ней хотя бы объясняет причину.
 */
export const grantableCheck = (grantable) => (key) => (
    !grantable || !grantable.length || grantable.includes(key));

/** Пресет целиком по силам раздающему? Иначе предлагать его нечестно. */
export const presetIsGrantable = (preset, mayGrant) => (
    Object.keys(preset.permissions || {}).every(mayGrant));
