// Кому показывается переделанный «Профиль» (ProfileView), а кому — прежний
// (LegacyProfileView).
//
// Владелец 25.09.2026: «данные изменения должны применяться только к СЗоВ, ОП
// и Тез КЦ». Правило — по отделу, а не по роли: стажёр СЗоВ тоже видит новый
// вид, а фронт-офис, Бухгалтерия, HR, Маркетинг и прочие — прежний.
//
// Расширение в импорте обязательно: модуль грузит напрямую Node в
// tests/profile_view.test.mjs.
import { departmentCodeOf } from '../../utils/departmentViews.js';

export const PROFILE_REDESIGN_DEPARTMENT_CODES = ['szov', 'op', 'tez'];

export const usesRedesignedProfile = (user) => (
    PROFILE_REDESIGN_DEPARTMENT_CODES.includes(departmentCodeOf(user) || '')
);
