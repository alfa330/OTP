"""База данных: зарплата и модели расчёта.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    CALCULATION_MODEL_CHAT_MANAGER,
    CALCULATION_MODEL_OPERATOR,
    CALCULATION_MODEL_TEZ_CODES,
    CHAT_MANAGER_ACTION_STATUS_KEYS,
    CHAT_MANAGER_BREAK_STATUS_KEYS,
    CHAT_MANAGER_LOGIN_STATUS_KEYS,
    CHAT_MANAGER_LOGOUT_STATUS_KEYS,
    CHAT_MANAGER_NON_WORK_STATUS_KEYS,
    CHAT_MANAGER_TRAINING_STATUS_KEYS,
    CHAT_MANAGER_WORK_STATUS_KEYS,
    SCHEDULE_AUTO_BREAK_STATUS_KEYS,
    SCHEDULE_AUTO_TALK_STATUS_KEYS,
    SCHEDULE_AUTO_TRAINING_STATUS_KEY,
    SCHEDULE_AUTO_WORK_STATUS_KEYS,
    TEZ_BREAK_STATUS_KEYS,
    TEZ_IGNORED_STATUS_KEYS,
    TEZ_WORK_STATUS_KEYS,
)


class SalaryMixin:
    def _status_profile_for_calculation_model(self, model_code):
        code = self._normalize_calculation_model_code(model_code)
        if code == CALCULATION_MODEL_CHAT_MANAGER:
            return {
                'code': CALCULATION_MODEL_CHAT_MANAGER,
                'work': CHAT_MANAGER_WORK_STATUS_KEYS,
                'talk': set(),
                'break': CHAT_MANAGER_BREAK_STATUS_KEYS,
                'training': CHAT_MANAGER_TRAINING_STATUS_KEYS,
                'late_start': CHAT_MANAGER_WORK_STATUS_KEYS | CHAT_MANAGER_LOGIN_STATUS_KEYS,
                'ignored': CHAT_MANAGER_NON_WORK_STATUS_KEYS | CHAT_MANAGER_LOGOUT_STATUS_KEYS | CHAT_MANAGER_ACTION_STATUS_KEYS,
            }
        if code in CALCULATION_MODEL_TEZ_CODES:
            # Обе TEZ-модели (Линия/ТП и ОП) знают ДВА словаря статусов сразу, и это не
            # перестраховка: 11.09.2026 отдел перешёл с ночной выгрузки кабинета Binotel на
            # события телефона iCORE Phone (TEZ_PHONE_HOURS_SINCE в bot_schedule2).
            #   * до перехода дни размечены словами кабинета: active + work in crm = работа,
            #     break in work = перерыв, inactive = офлайн (игнор);
            #   * после — словами телефона: готов / занят / перезвон = работа, перерыв = перерыв.
            # Оставить только новый словарь значило бы обнулить всю историю отдела, оставить
            # только старый — обнулить каждый день после перехода: незнакомый ключ не попадает
            # ни в работу, ни в перерыв, и час просто исчезает из учёта. Поэтому объединение,
            # а не ветка по дате: день сам приносит с собой тот словарь, которым размечен.
            return {
                'code': code,
                'work': TEZ_WORK_STATUS_KEYS | SCHEDULE_AUTO_WORK_STATUS_KEYS,
                # Разговор у кабинета не виден вовсе (в выгрузке такого состояния нет), а
                # телефон его отмечает сам — поэтому с переходом у ТЭЗ появляется и talk_time,
                # метрика для которого в моделях заведена давно и стояла пустой.
                'talk': SCHEDULE_AUTO_TALK_STATUS_KEYS,
                'break': TEZ_BREAK_STATUS_KEYS | SCHEDULE_AUTO_BREAK_STATUS_KEYS,
                'training': {SCHEDULE_AUTO_TRAINING_STATUS_KEY, 'training', 'study'},
                'late_start': TEZ_WORK_STATUS_KEYS | SCHEDULE_AUTO_WORK_STATUS_KEYS,
                'ignored': TEZ_IGNORED_STATUS_KEYS,
            }
        return {
            'code': CALCULATION_MODEL_OPERATOR,
            'work': SCHEDULE_AUTO_WORK_STATUS_KEYS,
            'talk': SCHEDULE_AUTO_TALK_STATUS_KEYS,
            'break': SCHEDULE_AUTO_BREAK_STATUS_KEYS,
            'training': {SCHEDULE_AUTO_TRAINING_STATUS_KEY, 'training', 'study'},
            'late_start': SCHEDULE_AUTO_WORK_STATUS_KEYS,
            'ignored': set(),
        }
