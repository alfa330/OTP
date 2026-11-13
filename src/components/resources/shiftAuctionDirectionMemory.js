/*
 * Какой аукцион смен открыть управляющему (#124).
 *
 * Последнее направление переживает перезагрузку, а у каждого отдела помнится
 * своё: суперадмин, ушедший из «СЗоВ · Чат» в Тез КЦ, по пункту «СЗоВ» меню
 * сайдбара возвращается в чат, а не на первую кнопку. Модуль общий для сайдбара
 * (App.jsx) и раздела: Ctrl-клик по пункту меню открывает новую вкладку, и
 * выбранный отдел она находит здесь же — просьбы из меню у неё нет.
 */
import {
  SHIFT_AUCTION_MODE_CHAT,
  SHIFT_AUCTION_MODE_LINE,
  SHIFT_AUCTION_MODE_TEZ_OP,
  SHIFT_AUCTION_MODE_TEZ_TP,
  normalizeShiftAuctionDirectionMode,
} from './shiftAuctionParticipants.js';

export const AUCTION_DIRECTION_STORAGE_KEY = 'otp_shift_auction_direction_v1';
export const AUCTION_DEPARTMENT_DIRECTION_STORAGE_KEY = 'otp_shift_auction_department_direction_v1';

// Отделы аукциона в порядке пунктов меню и их прогоны; первый — по умолчанию.
// Пары те же, что SHIFT_AUCTION_MODE_DEPARTMENT_CODE в db/core.py.
export const AUCTION_DEPARTMENTS = [
  { code: 'szov', label: 'СЗоВ', directions: [SHIFT_AUCTION_MODE_LINE, SHIFT_AUCTION_MODE_CHAT] },
  { code: 'tez', label: 'Тез КЦ', directions: [SHIFT_AUCTION_MODE_TEZ_TP, SHIFT_AUCTION_MODE_TEZ_OP] },
];

export const auctionDepartmentOfDirection = (direction) => {
  const key = normalizeShiftAuctionDirectionMode(direction);
  return AUCTION_DEPARTMENTS.find((item) => item.directions.includes(key))?.code || '';
};

const readDepartmentDirections = () => {
  if (typeof window === 'undefined') return {};
  try {
    const parsed = JSON.parse(window.localStorage.getItem(AUCTION_DEPARTMENT_DIRECTION_STORAGE_KEY) || '{}');
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
};

export const readStoredAuctionDirection = () => {
  if (typeof window === 'undefined') return SHIFT_AUCTION_MODE_LINE;
  try {
    return normalizeShiftAuctionDirectionMode(window.localStorage.getItem(AUCTION_DIRECTION_STORAGE_KEY));
  } catch {
    return SHIFT_AUCTION_MODE_LINE;
  }
};

export const storeAuctionDirection = (direction) => {
  if (typeof window === 'undefined') return;
  const key = normalizeShiftAuctionDirectionMode(direction);
  try {
    window.localStorage.setItem(AUCTION_DIRECTION_STORAGE_KEY, key);
    const department = auctionDepartmentOfDirection(key);
    if (!department) return;
    const byDepartment = readDepartmentDirections();
    byDepartment[department] = key;
    window.localStorage.setItem(AUCTION_DEPARTMENT_DIRECTION_STORAGE_KEY, JSON.stringify(byDepartment));
  } catch {
    /* приватный режим — выбор просто не переживёт перезагрузку */
  }
};

// С какого направления открыть отдел: последнее выбранное в нём, иначе первое.
// allowed — прогоны отдела из ответа сервера, когда он уже пришёл; до него —
// справочник выше. Отдел не из справочника — пустая строка.
export const auctionDirectionForDepartment = (departmentCode, allowed = null) => {
  const code = String(departmentCode || '').trim().toLowerCase();
  if (!code) return '';
  const directions = Array.isArray(allowed) && allowed.length
    ? allowed
    : (AUCTION_DEPARTMENTS.find((item) => item.code === code)?.directions || []);
  if (!directions.length) return '';
  const remembered = readDepartmentDirections()[code];
  return directions.includes(remembered) ? remembered : directions[0];
};
