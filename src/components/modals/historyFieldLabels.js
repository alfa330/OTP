// Подписи полей в «Истории изменений» сотрудника.
//
// В user_history поле записано ключом колонки — «card_number», «study_course».
// СВ и выше читают историю в «Учете сотрудников», в том числе правки, которые
// оператор сделал сам в «Моих данных» (задача #357), поэтому ключ показываем
// теми же словами, что и в столбцах таблицы сотрудников. Незнакомый ключ
// остаётся как есть: лучше сырое имя, чем пустая строка.
export const HISTORY_FIELD_LABELS = {
    name: 'Имя',
    role: 'Роль',
    status: 'Статус',
    rate: 'Ставка',
    department_id: 'Отдел',
    direction_id: 'Направление',
    supervisor_id: 'Супервайзер',
    hire_date: 'Дата найма',
    birth_date: 'Дата рождения',
    gender: 'Пол',
    job_title: 'Должность',
    city: 'Город',
    phone: 'Телефон',
    email: 'Почта',
    personal_email: 'Личный Email',
    instagram: 'Инстаграм',
    telegram_nick: 'Telegram',
    card_number: 'Номер карты',
    study_place: 'Место учебы',
    study_specialty: 'Специальность',
    study_course: 'Курс',
    study_completed: 'Учеба завершена',
    study_completion_year: 'Год окончания учебы',
    company_name: 'ТОО/ИП',
    employment_type: 'Оформлен как',
    has_proxy: 'Прокси',
    proxy_card_number: 'Номер прокси',
    proxy_status: 'Статус прокси',
    has_driver_license: 'Вод. права',
    sip_number: 'SIP',
    taxipro_id: 'ID таксипро',
    internship_in_company: 'Практика',
    front_office_training: 'Обучение ФО',
    front_office_training_date: 'Дата обучения',
    close_contact_1_relation: 'Близкий контакт 1 — кто',
    close_contact_1_full_name: 'Близкий контакт 1 — ФИО',
    close_contact_1_phone: 'Близкий контакт 1 — телефон',
    close_contact_2_relation: 'Близкий контакт 2 — кто',
    close_contact_2_full_name: 'Близкий контакт 2 — ФИО',
    close_contact_2_phone: 'Близкий контакт 2 — телефон',
};

export const historyFieldLabel = (field) => HISTORY_FIELD_LABELS[field] || field || '';
