"""База данных: пользователи и сессии.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    Any,
    datetime,
    List,
    logging,
    normalize_proxy_status_value,
    Optional,
    pbkdf2_sha256,
    psycopg2,
    timedelta,
    uuid,
)


class UsersMixin:
    def create_user(
        self,
        telegram_id,
        name,
        role,
        direction_id=None,
        rate=None,
        hire_date=None,
        supervisor_id=None,
        login=None,
        password=None,
        hours_table_url=None,
        gender=None,
        birth_date=None,
        phone=None,
        email=None,
        personal_email=None,
        instagram=None,
        telegram_nick=None,
        company_name=None,
        employment_type=None,
        has_proxy=None,
        proxy_card_number=None,
        proxy_status=None,
        has_driver_license=None,
        sip_number=None,
        study_place=None,
        study_specialty=None,
        study_course=None,
        study_completed=None,
        study_completion_year=None,
        close_contact_1_relation=None,
        close_contact_1_full_name=None,
        close_contact_1_phone=None,
        close_contact_2_relation=None,
        close_contact_2_full_name=None,
        close_contact_2_phone=None,
        card_number=None,
        city=None,
        job_title=None,
        internship_in_company=None,
        front_office_training=None,
        front_office_training_date=None,
        taxipro_id=None,
        department_id=None
    ):
        if login is None:
            base_login = f"user_{str(uuid.uuid4())[:8]}"
            with self._get_cursor() as cursor:
                while True:
                    cursor.execute("SELECT id FROM users WHERE login = %s", (base_login,))
                    if not cursor.fetchone():
                        login = base_login
                        break
                    base_login = f"user_{str(uuid.uuid4())[:8]}"
        else:
            login = login or str(telegram_id)

        password = password or "123456"
        password_hash = pbkdf2_sha256.hash(password)
        role_norm = str(role or '').strip().lower()
        phone = str(phone).strip() if phone is not None else ""
        email = str(email).strip() if email is not None else ""
        personal_email = str(personal_email).strip() if personal_email is not None else ""
        instagram = str(instagram).strip() if instagram is not None else ""
        telegram_nick = str(telegram_nick).strip() if telegram_nick is not None else ""
        company_name = str(company_name).strip() if company_name is not None else ""
        employment_type = str(employment_type).strip().lower() if employment_type is not None else ""
        proxy_card_number = str(proxy_card_number).strip() if proxy_card_number is not None else ""
        proxy_status = normalize_proxy_status_value(proxy_status)
        sip_number = str(sip_number).strip() if sip_number is not None else ""
        study_place = str(study_place).strip() if study_place is not None else ""
        study_specialty = str(study_specialty).strip() if study_specialty is not None else ""
        study_course = str(study_course).strip() if study_course is not None else ""
        study_completion_year_raw = str(study_completion_year).strip() if study_completion_year is not None else ""
        close_contact_1_relation = str(close_contact_1_relation).strip() if close_contact_1_relation is not None else ""
        close_contact_1_full_name = str(close_contact_1_full_name).strip() if close_contact_1_full_name is not None else ""
        close_contact_1_phone = str(close_contact_1_phone).strip() if close_contact_1_phone is not None else ""
        close_contact_2_relation = str(close_contact_2_relation).strip() if close_contact_2_relation is not None else ""
        close_contact_2_full_name = str(close_contact_2_full_name).strip() if close_contact_2_full_name is not None else ""
        close_contact_2_phone = str(close_contact_2_phone).strip() if close_contact_2_phone is not None else ""
        card_number = str(card_number).strip() if card_number is not None else ""
        city = str(city).strip() if city is not None else ""
        job_title = str(job_title).strip() if job_title is not None else ""
        taxipro_id = str(taxipro_id).strip() if taxipro_id is not None else ""

        phone = phone or None
        email = email or None
        personal_email = personal_email or None
        instagram = instagram or None
        telegram_nick = telegram_nick or None
        company_name = company_name or None
        employment_type = employment_type or None
        proxy_card_number = proxy_card_number or None
        sip_number = sip_number or None
        study_place = study_place or None
        study_specialty = study_specialty or None
        # Специальность — уточнение к месту учёбы: без места учёбы её не храним,
        # иначе значение осталось бы висеть в базе и в выгрузках, не показываясь
        # в карточке (поле там появляется только при заполненном месте учёбы).
        if not study_place:
            study_specialty = None
        study_course = study_course or None
        if study_completion_year_raw:
            if not study_completion_year_raw.isdigit():
                raise ValueError("Invalid study_completion_year")
            study_completion_year = int(study_completion_year_raw)
            if study_completion_year < 1900 or study_completion_year > 2100:
                raise ValueError("Invalid study_completion_year")
        else:
            study_completion_year = None
        close_contact_1_relation = close_contact_1_relation or None
        close_contact_1_full_name = close_contact_1_full_name or None
        close_contact_1_phone = close_contact_1_phone or None
        close_contact_2_relation = close_contact_2_relation or None
        close_contact_2_full_name = close_contact_2_full_name or None
        close_contact_2_phone = close_contact_2_phone or None
        card_number = card_number or None
        city = city or None
        job_title = job_title or None
        taxipro_id = taxipro_id or None
        if employment_type not in (None, 'gph', 'of', 'smz'):
            raise ValueError("Invalid employment_type")
        has_proxy_value = None if has_proxy is None else bool(has_proxy)
        if not has_proxy_value:
            proxy_card_number = None
        has_driver_license_value = None if has_driver_license is None else bool(has_driver_license)
        study_completed_value = None if study_completed is None else bool(study_completed)
        internship_in_company_value = None if internship_in_company is None else bool(internship_in_company)
        front_office_training_value = None if front_office_training is None else bool(front_office_training)
        if not front_office_training_value:
            front_office_training_date = None
        if role_norm == 'trainee':
            has_proxy_value = False
            proxy_card_number = None
        if role_norm == 'trainer':
            direction_id = None
            supervisor_id = None
            sip_number = None

        with self._get_cursor() as cursor:
            def _clear_trainer_links(target_user_id):
                if role_norm == 'trainer' and target_user_id is not None:
                    cursor.execute(
                        "UPDATE users SET direction_id = NULL, supervisor_id = NULL WHERE id = %s",
                        (target_user_id,)
                    )

            def _sync_profiles(uid):
                # Assign department (default to szov if not specified)
                if department_id:
                    cursor.execute("UPDATE users SET department_id = %s WHERE id = %s AND department_id IS NULL", (department_id, uid))
                else:
                    cursor.execute("""
                        UPDATE users SET department_id = (SELECT id FROM departments WHERE code = 'szov' LIMIT 1)
                        WHERE id = %s AND department_id IS NULL
                    """, (uid,))

                # HR profile (all users)
                cursor.execute("""
                    INSERT INTO user_hr_profiles (
                        user_id, phone, email, personal_email, instagram, telegram_nick,
                        company_name, employment_type,
                        study_place, study_specialty, study_course, study_completed, study_completion_year,
                        close_contact_1_relation, close_contact_1_full_name, close_contact_1_phone,
                        close_contact_2_relation, close_contact_2_full_name, close_contact_2_phone,
                        card_number, city, job_title
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        phone = COALESCE(EXCLUDED.phone, user_hr_profiles.phone),
                        email = COALESCE(EXCLUDED.email, user_hr_profiles.email),
                        personal_email = COALESCE(EXCLUDED.personal_email, user_hr_profiles.personal_email),
                        instagram = COALESCE(EXCLUDED.instagram, user_hr_profiles.instagram),
                        telegram_nick = COALESCE(EXCLUDED.telegram_nick, user_hr_profiles.telegram_nick),
                        company_name = COALESCE(EXCLUDED.company_name, user_hr_profiles.company_name),
                        employment_type = COALESCE(EXCLUDED.employment_type, user_hr_profiles.employment_type),
                        study_place = COALESCE(EXCLUDED.study_place, user_hr_profiles.study_place),
                        study_specialty = COALESCE(EXCLUDED.study_specialty, user_hr_profiles.study_specialty),
                        study_course = COALESCE(EXCLUDED.study_course, user_hr_profiles.study_course),
                        study_completed = EXCLUDED.study_completed,
                        study_completion_year = COALESCE(EXCLUDED.study_completion_year, user_hr_profiles.study_completion_year),
                        close_contact_1_relation = COALESCE(EXCLUDED.close_contact_1_relation, user_hr_profiles.close_contact_1_relation),
                        close_contact_1_full_name = COALESCE(EXCLUDED.close_contact_1_full_name, user_hr_profiles.close_contact_1_full_name),
                        close_contact_1_phone = COALESCE(EXCLUDED.close_contact_1_phone, user_hr_profiles.close_contact_1_phone),
                        close_contact_2_relation = COALESCE(EXCLUDED.close_contact_2_relation, user_hr_profiles.close_contact_2_relation),
                        close_contact_2_full_name = COALESCE(EXCLUDED.close_contact_2_full_name, user_hr_profiles.close_contact_2_full_name),
                        close_contact_2_phone = COALESCE(EXCLUDED.close_contact_2_phone, user_hr_profiles.close_contact_2_phone),
                        card_number = COALESCE(EXCLUDED.card_number, user_hr_profiles.card_number),
                        city = COALESCE(EXCLUDED.city, user_hr_profiles.city),
                        job_title = COALESCE(EXCLUDED.job_title, user_hr_profiles.job_title)
                """, (
                    uid, phone, email, personal_email, instagram, telegram_nick,
                    company_name, employment_type,
                    study_place, study_specialty, study_course,
                    (study_completed_value if study_completed_value is not None else False),
                    study_completion_year,
                    close_contact_1_relation, close_contact_1_full_name, close_contact_1_phone,
                    close_contact_2_relation, close_contact_2_full_name, close_contact_2_phone,
                    card_number, city, job_title
                ))

                # Operator profile (only for operator/trainee)
                if role_norm in ('operator', 'trainee'):
                    cursor.execute("""
                        INSERT INTO operator_profiles (
                            user_id, direction_id, supervisor_id, is_active, rate,
                            sip_number, has_proxy, proxy_card_number, proxy_status, has_driver_license,
                            internship_in_company, front_office_training, front_office_training_date, taxipro_id
                        ) VALUES (%s, %s, %s, FALSE, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (user_id) DO UPDATE SET
                            direction_id = COALESCE(EXCLUDED.direction_id, operator_profiles.direction_id),
                            supervisor_id = COALESCE(EXCLUDED.supervisor_id, operator_profiles.supervisor_id),
                            rate = COALESCE(EXCLUDED.rate, operator_profiles.rate),
                            sip_number = COALESCE(EXCLUDED.sip_number, operator_profiles.sip_number),
                            has_proxy = EXCLUDED.has_proxy,
                            proxy_card_number = COALESCE(EXCLUDED.proxy_card_number, operator_profiles.proxy_card_number),
                            proxy_status = COALESCE(EXCLUDED.proxy_status, operator_profiles.proxy_status),
                            has_driver_license = EXCLUDED.has_driver_license,
                            internship_in_company = EXCLUDED.internship_in_company,
                            front_office_training = EXCLUDED.front_office_training,
                            front_office_training_date = COALESCE(EXCLUDED.front_office_training_date, operator_profiles.front_office_training_date),
                            taxipro_id = COALESCE(EXCLUDED.taxipro_id, operator_profiles.taxipro_id)
                    """, (
                        uid, direction_id, supervisor_id, rate,
                        sip_number,
                        (has_proxy_value if has_proxy_value is not None else False),
                        proxy_card_number,
                        proxy_status,
                        (has_driver_license_value if has_driver_license_value is not None else False),
                        (internship_in_company_value if internship_in_company_value is not None else False),
                        (front_office_training_value if front_office_training_value is not None else False),
                        front_office_training_date, taxipro_id
                    ))

                # Admin profile (only for admin/super_admin)
                if role_norm in ('admin', 'super_admin'):
                    cursor.execute("""
                        INSERT INTO admin_profiles (user_id, feedback_telegram_report_enabled)
                        VALUES (%s, FALSE)
                        ON CONFLICT (user_id) DO NOTHING
                    """, (uid,))

            cursor.execute("SAVEPOINT before_insert")
            try:
                cursor.execute("""
                    INSERT INTO users (
                        telegram_id, name, role, direction_id, rate, hire_date, supervisor_id,
                        login, password_hash, hours_table_url, gender, birth_date, phone, email, personal_email,
                        instagram, telegram_nick, company_name, employment_type, has_proxy, proxy_card_number, proxy_status, has_driver_license, sip_number,
                        study_place, study_specialty, study_course, study_completed, study_completion_year,
                        close_contact_1_relation, close_contact_1_full_name, close_contact_1_phone,
                        close_contact_2_relation, close_contact_2_full_name, close_contact_2_phone,
                        card_number, city, job_title, internship_in_company, front_office_training, front_office_training_date, taxipro_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (
                    telegram_id, name, role, direction_id, rate, hire_date, supervisor_id,
                    login, password_hash, hours_table_url, gender, birth_date, phone, email, personal_email,
                    instagram, telegram_nick, company_name, employment_type,
                    (has_proxy_value if has_proxy_value is not None else False),
                    proxy_card_number,
                    proxy_status,
                    (has_driver_license_value if has_driver_license_value is not None else False),
                    sip_number,
                    study_place, study_specialty, study_course,
                    (study_completed_value if study_completed_value is not None else False),
                    study_completion_year,
                    close_contact_1_relation, close_contact_1_full_name, close_contact_1_phone,
                    close_contact_2_relation, close_contact_2_full_name, close_contact_2_phone,
                    card_number,
                    city,
                    job_title,
                    (internship_in_company_value if internship_in_company_value is not None else False),
                    (front_office_training_value if front_office_training_value is not None else False),
                    front_office_training_date,
                    taxipro_id
                ))
                created_user_id = cursor.fetchone()[0]
                _clear_trainer_links(created_user_id)
                _sync_profiles(created_user_id)
                return created_user_id
            except psycopg2.IntegrityError as e:
                cursor.execute("ROLLBACK TO SAVEPOINT before_insert")
                if 'unique_name_role' in str(e):
                    cursor.execute("""
                        UPDATE users
                        SET direction_id = COALESCE(%s, direction_id),
                            supervisor_id = COALESCE(%s, supervisor_id),
                            hours_table_url = COALESCE(%s, hours_table_url),
                            gender = COALESCE(%s, gender),
                            birth_date = COALESCE(%s, birth_date),
                            phone = COALESCE(%s, phone),
                            email = COALESCE(%s, email),
                            personal_email = COALESCE(%s, personal_email),
                            instagram = COALESCE(%s, instagram),
                            telegram_nick = COALESCE(%s, telegram_nick),
                            company_name = COALESCE(%s, company_name),
                            employment_type = COALESCE(%s, employment_type),
                            has_proxy = COALESCE(%s, has_proxy),
                            proxy_card_number = COALESCE(%s, proxy_card_number),
                            proxy_status = COALESCE(%s, proxy_status),
                            has_driver_license = COALESCE(%s, has_driver_license),
                            sip_number = COALESCE(%s, sip_number),
                            study_place = COALESCE(%s, study_place),
                            study_specialty = COALESCE(%s, study_specialty),
                            study_course = COALESCE(%s, study_course),
                            study_completed = COALESCE(%s, study_completed),
                            study_completion_year = COALESCE(%s, study_completion_year),
                            close_contact_1_relation = COALESCE(%s, close_contact_1_relation),
                            close_contact_1_full_name = COALESCE(%s, close_contact_1_full_name),
                            close_contact_1_phone = COALESCE(%s, close_contact_1_phone),
                            close_contact_2_relation = COALESCE(%s, close_contact_2_relation),
                            close_contact_2_full_name = COALESCE(%s, close_contact_2_full_name),
                            close_contact_2_phone = COALESCE(%s, close_contact_2_phone),
                            card_number = COALESCE(%s, card_number),
                            city = COALESCE(%s, city),
                            job_title = COALESCE(%s, job_title),
                            internship_in_company = COALESCE(%s, internship_in_company),
                            front_office_training = COALESCE(%s, front_office_training),
                            front_office_training_date = COALESCE(%s, front_office_training_date),
                            taxipro_id = COALESCE(%s, taxipro_id)
                        WHERE name = %s AND role = %s
                        RETURNING id
                    """, (
                        direction_id, supervisor_id, hours_table_url, gender, birth_date,
                        phone, email, personal_email, instagram, telegram_nick, company_name, employment_type, has_proxy_value, proxy_card_number, proxy_status, has_driver_license_value, sip_number,
                        study_place, study_specialty, study_course, study_completed_value, study_completion_year,
                        close_contact_1_relation, close_contact_1_full_name, close_contact_1_phone,
                        close_contact_2_relation, close_contact_2_full_name, close_contact_2_phone,
                        card_number, city, job_title, internship_in_company_value, front_office_training_value, front_office_training_date, taxipro_id,
                        name, role
                    ))
                    result = cursor.fetchone()
                    if result:
                        updated_user_id = result[0]
                        _clear_trainer_links(updated_user_id)
                        _sync_profiles(updated_user_id)
                        return updated_user_id
                    else:
                        raise ValueError("User with this name and role not found")
                elif 'telegram_id' in str(e):
                    cursor.execute("""
                        UPDATE users
                        SET name = %s,
                            role = %s,
                            direction_id = COALESCE(%s, direction_id),
                            hire_date = COALESCE(%s, hire_date),
                            supervisor_id = COALESCE(%s, supervisor_id),
                            login = %s,
                            password_hash = %s,
                            hours_table_url = COALESCE(%s, hours_table_url),
                            gender = COALESCE(%s, gender),
                            birth_date = COALESCE(%s, birth_date),
                            phone = COALESCE(%s, phone),
                            email = COALESCE(%s, email),
                            personal_email = COALESCE(%s, personal_email),
                            instagram = COALESCE(%s, instagram),
                            telegram_nick = COALESCE(%s, telegram_nick),
                            company_name = COALESCE(%s, company_name),
                            employment_type = COALESCE(%s, employment_type),
                            has_proxy = COALESCE(%s, has_proxy),
                            proxy_card_number = COALESCE(%s, proxy_card_number),
                            proxy_status = COALESCE(%s, proxy_status),
                            has_driver_license = COALESCE(%s, has_driver_license),
                            sip_number = COALESCE(%s, sip_number),
                            study_place = COALESCE(%s, study_place),
                            study_specialty = COALESCE(%s, study_specialty),
                            study_course = COALESCE(%s, study_course),
                            study_completed = COALESCE(%s, study_completed),
                            study_completion_year = COALESCE(%s, study_completion_year),
                            close_contact_1_relation = COALESCE(%s, close_contact_1_relation),
                            close_contact_1_full_name = COALESCE(%s, close_contact_1_full_name),
                            close_contact_1_phone = COALESCE(%s, close_contact_1_phone),
                            close_contact_2_relation = COALESCE(%s, close_contact_2_relation),
                            close_contact_2_full_name = COALESCE(%s, close_contact_2_full_name),
                            close_contact_2_phone = COALESCE(%s, close_contact_2_phone),
                            card_number = COALESCE(%s, card_number),
                            city = COALESCE(%s, city),
                            job_title = COALESCE(%s, job_title),
                            internship_in_company = COALESCE(%s, internship_in_company),
                            front_office_training = COALESCE(%s, front_office_training),
                            front_office_training_date = COALESCE(%s, front_office_training_date),
                            taxipro_id = COALESCE(%s, taxipro_id)
                        WHERE telegram_id = %s
                        RETURNING id
                    """, (
                        name, role, direction_id, hire_date, supervisor_id, login, password_hash, hours_table_url,
                        gender, birth_date, phone, email, personal_email, instagram, telegram_nick, company_name, employment_type,
                        has_proxy_value, proxy_card_number, proxy_status, has_driver_license_value, sip_number,
                        study_place, study_specialty, study_course, study_completed_value, study_completion_year,
                        close_contact_1_relation, close_contact_1_full_name, close_contact_1_phone,
                        close_contact_2_relation, close_contact_2_full_name, close_contact_2_phone,
                        card_number, city, job_title, internship_in_company_value, front_office_training_value, front_office_training_date, taxipro_id,
                        telegram_id
                    ))
                    updated_user_id = cursor.fetchone()[0]
                    _clear_trainer_links(updated_user_id)
                    _sync_profiles(updated_user_id)
                    return updated_user_id
                else:
                    raise

    def update_telegram_id(self, user_id, telegram_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users 
                SET telegram_id = %s
                WHERE id = %s
                RETURNING telegram_id
            """, (telegram_id, user_id))
            result = cursor.fetchone()
            if result:
                return result[0]
            return None

    def get_user_hr_card(self, user_id):
        """Должность и название отдела сотрудника — для карточки «Профиль».

        Отдельным запросом, а не колонками в get_user: тот возвращает КОРТЕЖ и
        читается по индексам в десятках мест, и любое расширение там — риск
        ради двух полей одного экрана.
        """
        if not user_id:
            return {"job_title": None, "department_name": None}
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT u.job_title, d.name
                  FROM users u
                  LEFT JOIN departments d ON d.id = u.department_id
                 WHERE u.id = %s
            """, (user_id,))
            row = cursor.fetchone()
            if not row:
                return {"job_title": None, "department_name": None}
            return {"job_title": row[0], "department_name": row[1]}

    def _load_dismissal_dates_tx(self, cursor, operator_ids):
        """Дата увольнения операторов: {operator_id: date}. Приоритет — start_date периода
        'dismissal' (фактическая дата), фолбэк — дата смены статуса на fired/dismissal в
        user_history. Формула та же, что в отчёте по сотрудникам (dismissal_date_value).
        Нужна «Учёту часов», чтобы отличать «уволен В этом месяце» (остаётся в активных,
        месяц отработан частично) от «уволен РАНЬШЕ» (место во вкладке «Уволенные»)."""
        ids = []
        for _raw in (operator_ids or []):
            try:
                ids.append(int(_raw))
            except (TypeError, ValueError):
                continue
        if not ids:
            return {}
        cursor.execute(
            """
            SELECT u.id, COALESCE(dp.start_date, dh.changed_at::date) AS dismissal_date
            FROM users u
            LEFT JOIN LATERAL (
                SELECT p.start_date
                FROM operator_schedule_status_periods p
                WHERE p.operator_id = u.id
                  AND p.status_code = 'dismissal'
                ORDER BY
                    COALESCE(p.end_date, DATE '9999-12-31') DESC,
                    p.start_date DESC,
                    p.id DESC
                LIMIT 1
            ) dp ON TRUE
            LEFT JOIN LATERAL (
                SELECT uh.changed_at
                FROM user_history uh
                WHERE uh.user_id = u.id
                  AND uh.field_changed = 'status'
                  AND LOWER(TRIM(COALESCE(uh.new_value, ''))) IN ('fired', 'dismissal')
                ORDER BY uh.changed_at DESC, uh.id DESC
                LIMIT 1
            ) dh ON TRUE
            WHERE u.id = ANY(%s)
            """,
            (ids,),
        )
        dismissal_dates = {}
        for _uid, _dismissed_on in cursor.fetchall() or []:
            if _dismissed_on is not None:
                dismissal_dates[int(_uid)] = _dismissed_on
        return dismissal_dates

    @staticmethod
    def _dismissal_date_iso(dismissal_dates, operator_id):
        """ISO-строка даты увольнения для payload (фронт сравнивает 'YYYY-MM' лексикографически)."""
        value = (dismissal_dates or {}).get(int(operator_id))
        if value is None:
            return None
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    def update_user_password(self, user_id, new_password):
        password_hash = pbkdf2_sha256.hash(new_password)
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users SET password_hash = %s
                WHERE id = %s
                RETURNING id
            """, (password_hash, user_id))
            return cursor.fetchone() is not None
    
    def update_operator_login(self, operator_id, supervisor_id, new_login):
        """Обновить логин оператора с проверкой принадлежности"""
        if supervisor_id:
            with self._get_cursor() as cursor:
                cursor.execute("""
                    UPDATE users SET login = %s
                    WHERE id = %s AND supervisor_id = %s
                    RETURNING id
                """, (new_login, operator_id, supervisor_id))
                return cursor.fetchone() is not None
        else:
            with self._get_cursor() as cursor:
                cursor.execute("""
                    UPDATE users SET login = %s
                    WHERE id = %s
                    RETURNING id
                """, (new_login, operator_id))
                return cursor.fetchone() is not None

    def get_user(self, **kwargs):
        from psycopg2 import sql
        conditions = []
        params = []
        for key, value in kwargs.items():
            if key == 'direction':
                conditions.append(sql.SQL("d.name = %s"))
                params.append(value)
            else:
                # Use sql.Identifier to safely quote the column name
                conditions.append(sql.SQL("u.{} = %s").format(sql.Identifier(key)))
                params.append(value)
        
        query = sql.SQL("""
            SELECT u.id, u.telegram_id, u.name, u.role, d.name, u.hire_date, u.supervisor_id, u.login, u.hours_table_url, u.scores_table_url, u.is_active, u.status, u.rate, u.gender, u.birth_date, u.avatar_bucket, u.avatar_blob_path, u.avatar_content_type, u.avatar_file_size, u.avatar_updated_at
            FROM users u
            LEFT JOIN directions d ON u.direction_id = d.id
            WHERE {}
        """).format(sql.SQL(' AND ').join(conditions))
        
        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()
        
    def update_user_table(self, user_id, hours_table_url=None, scores_table_url=None):
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users 
                SET hours_table_url = COALESCE(%s, hours_table_url),
                    scores_table_url = COALESCE(%s, scores_table_url)
                WHERE id = %s
            """, (hours_table_url, scores_table_url, user_id))

    def get_activity_logs(self, operator_id, date_str=None):
        # Если date_str не указан, используем текущую дату
        if date_str is None:
            date_str = datetime.now().strftime('%Y-%m-%d')
        try:
            log_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            raise ValueError("Invalid date format. Use YYYY-MM-DD")
        
        with self._get_cursor() as cursor:
            # Берём последний статус до указанной даты
            cursor.execute("""
                SELECT change_time, is_active
                FROM operator_activity_logs
                WHERE operator_id = %s
                  AND change_time < %s
                ORDER BY change_time DESC
                LIMIT 1
            """, (operator_id, log_date))
            prev_log = cursor.fetchone()
    
            # Берём все записи за указанную дату
            cursor.execute("""
                SELECT change_time, is_active 
                FROM operator_activity_logs 
                WHERE operator_id = %s 
                  AND change_time::date = %s 
                ORDER BY change_time ASC
            """, (operator_id, log_date))
            logs = cursor.fetchall()
    
        result = []
        if prev_log:
            result.append({"change_time": prev_log[0].isoformat(), "is_active": prev_log[1]})
        result.extend([
            {"change_time": row[0].isoformat(), "is_active": row[1]}
            for row in logs
        ])
        return result

    def get_user_by_login(self, login):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, telegram_id, name, role, direction_id, hire_date, supervisor_id, login, password_hash, hours_table_url, scores_table_url, avatar_bucket, avatar_blob_path, avatar_content_type, avatar_file_size, avatar_updated_at, gender
                FROM users WHERE login = %s
            """, (login,))
            return cursor.fetchone()

    def get_user_by_name(self, name):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT id, telegram_id, name, role, direction_id, hire_date, supervisor_id, login
                FROM users WHERE LOWER(name) = LOWER(%s) LIMIT 1
            """, (name,))
            return cursor.fetchone()

    def verify_password(self, user_id, password):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT password_hash FROM users WHERE id = %s
            """, (user_id,))
            result = cursor.fetchone()
            if result and result[0]:
                return pbkdf2_sha256.verify(password, result[0])
            return False

    def create_user_session(
        self,
        session_id,
        user_id,
        refresh_token_hash,
        expires_at,
        user_agent=None,
        ip_address=None
    ):
        with self._get_cursor() as cursor:
            cursor.execute("""
                INSERT INTO user_sessions (
                    session_id,
                    user_id,
                    refresh_token_hash,
                    user_agent,
                    ip_address,
                    expires_at
                )
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (session_id, user_id, refresh_token_hash, user_agent, ip_address, expires_at))

    def get_user_session(self, session_id, user_id=None):
        with self._get_cursor() as cursor:
            if user_id is None:
                cursor.execute("""
                    SELECT
                        session_id::text,
                        user_id,
                        refresh_token_hash,
                        previous_refresh_token_hash,
                        previous_refresh_valid_until,
                        user_agent,
                        ip_address,
                        created_at,
                        last_seen_at,
                        expires_at,
                        revoked_at,
                        sensitive_data_unlocked,
                        sensitive_data_unlocked_at
                    FROM user_sessions
                    WHERE session_id = %s
                """, (session_id,))
            else:
                cursor.execute("""
                    SELECT
                        session_id::text,
                        user_id,
                        refresh_token_hash,
                        previous_refresh_token_hash,
                        previous_refresh_valid_until,
                        user_agent,
                        ip_address,
                        created_at,
                        last_seen_at,
                        expires_at,
                        revoked_at,
                        sensitive_data_unlocked,
                        sensitive_data_unlocked_at
                    FROM user_sessions
                    WHERE session_id = %s AND user_id = %s
                """, (session_id, user_id))

            row = cursor.fetchone()
            if not row:
                return None
            return {
                "session_id": row[0],
                "user_id": row[1],
                "refresh_token_hash": row[2],
                "previous_refresh_token_hash": row[3],
                "previous_refresh_valid_until": row[4],
                "user_agent": row[5],
                "ip_address": row[6],
                "created_at": row[7],
                "last_seen_at": row[8],
                "expires_at": row[9],
                "revoked_at": row[10],
                "sensitive_data_unlocked": bool(row[11]),
                "sensitive_data_unlocked_at": row[12]
            }

    def get_user_session_with_user(self, session_id, user_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    us.session_id::text,
                    us.user_id,
                    us.refresh_token_hash,
                    us.previous_refresh_token_hash,
                    us.previous_refresh_valid_until,
                    us.user_agent,
                    us.ip_address,
                    us.created_at,
                    us.last_seen_at,
                    us.expires_at,
                    us.revoked_at,
                    us.sensitive_data_unlocked,
                    us.sensitive_data_unlocked_at,
                    u.id,
                    u.telegram_id,
                    u.name,
                    u.role,
                    d.name,
                    u.hire_date,
                    u.supervisor_id,
                    u.login,
                    u.hours_table_url,
                    u.scores_table_url,
                    u.is_active,
                    u.status,
                    u.rate,
                    u.gender,
                    u.birth_date,
                    u.avatar_bucket,
                    u.avatar_blob_path,
                    u.avatar_content_type,
                    u.avatar_file_size,
                    u.avatar_updated_at
                FROM user_sessions us
                JOIN users u ON u.id = us.user_id
                LEFT JOIN directions d ON d.id = u.direction_id
                WHERE us.session_id = %s
                  AND us.user_id = %s
            """, (session_id, user_id))
            row = cursor.fetchone()
            if not row:
                return None, None
            session = {
                "session_id": row[0],
                "user_id": row[1],
                "refresh_token_hash": row[2],
                "previous_refresh_token_hash": row[3],
                "previous_refresh_valid_until": row[4],
                "user_agent": row[5],
                "ip_address": row[6],
                "created_at": row[7],
                "last_seen_at": row[8],
                "expires_at": row[9],
                "revoked_at": row[10],
                "sensitive_data_unlocked": bool(row[11]),
                "sensitive_data_unlocked_at": row[12],
            }
            return session, tuple(row[13:])

    def rotate_user_session_token(
        self,
        session_id,
        user_id,
        refresh_token_hash,
        expires_at,
        previous_refresh_valid_until=None
    ):
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE user_sessions
                SET previous_refresh_token_hash = CASE
                        WHEN %s IS NULL THEN NULL
                        ELSE refresh_token_hash
                    END,
                    previous_refresh_valid_until = %s,
                    refresh_token_hash = %s,
                    expires_at = %s,
                    last_seen_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                WHERE session_id = %s
                  AND user_id = %s
                  AND revoked_at IS NULL
                RETURNING session_id
            """, (
                previous_refresh_valid_until,
                previous_refresh_valid_until,
                refresh_token_hash,
                expires_at,
                session_id,
                user_id
            ))
            return cursor.fetchone() is not None

    def touch_user_session(self, session_id, user_id=None, ip_address=None, user_agent=None):
        with self._get_cursor() as cursor:
            if user_id is None:
                cursor.execute("""
                    UPDATE user_sessions
                    SET last_seen_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                        ip_address = COALESCE(%s, ip_address),
                        user_agent = COALESCE(%s, user_agent)
                    WHERE session_id = %s
                      AND revoked_at IS NULL
                    RETURNING session_id
                """, (ip_address, user_agent, session_id))
            else:
                cursor.execute("""
                    UPDATE user_sessions
                    SET last_seen_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'),
                        ip_address = COALESCE(%s, ip_address),
                        user_agent = COALESCE(%s, user_agent)
                    WHERE session_id = %s
                      AND user_id = %s
                      AND revoked_at IS NULL
                    RETURNING session_id
                """, (ip_address, user_agent, session_id, user_id))
            return cursor.fetchone() is not None

    def revoke_user_session(self, session_id, user_id=None):
        with self._get_cursor() as cursor:
            if user_id is None:
                cursor.execute("""
                    UPDATE user_sessions
                    SET revoked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE session_id = %s
                      AND revoked_at IS NULL
                    RETURNING session_id
                """, (session_id,))
            else:
                cursor.execute("""
                    UPDATE user_sessions
                    SET revoked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE session_id = %s
                      AND user_id = %s
                      AND revoked_at IS NULL
                    RETURNING session_id
                """, (session_id, user_id))
            return cursor.fetchone() is not None

    def revoke_user_sessions_bulk(self, session_ids):
        if not session_ids:
            return 0
        with self._get_cursor() as cursor:
            # Type casting to uuid array to prevent type errors
            cursor.execute("""
                UPDATE user_sessions
                SET revoked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                WHERE session_id::text = ANY(%s)
                  AND revoked_at IS NULL
            """, (session_ids,))
            return cursor.rowcount

    def revoke_all_user_sessions(self, user_id, except_session_id=None):
        with self._get_cursor() as cursor:
            if except_session_id:
                cursor.execute("""
                    UPDATE user_sessions
                    SET revoked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE user_id = %s
                      AND session_id <> %s
                      AND revoked_at IS NULL
                """, (user_id, except_session_id))
            else:
                cursor.execute("""
                    UPDATE user_sessions
                    SET revoked_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                    WHERE user_id = %s
                      AND revoked_at IS NULL
                """, (user_id,))
            return cursor.rowcount

    def cleanup_expired_user_sessions(self, grace_days=30):
        """Удаляет давно протухшие сессии — таблица только росла.

        Отзыв сессии (revoke_user_sessions/*) ставит revoked_at, но строку не
        убирает, поэтому к моменту ввода этой чистки 6136 из 8022 строк были
        мертвы. Удаляем только то, что не может быть использовано ни при каком
        раскладе: истёкшие больше grace_days назад. Запас в 30 дней сознательно
        велик — expires_at пишется в UTC, а created_at/last_seen_at в Asia/Almaty,
        и при таком зазоре расхождение часовых поясов роли не играет.
        Активные и недавно истёкшие сессии (их ещё видно в «Мои устройства»)
        не трогаем.
        """
        days = max(int(grace_days), 7)
        with self._get_cursor() as cursor:
            cursor.execute("""
                DELETE FROM user_sessions
                 WHERE expires_at < (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
                                    - make_interval(days => %s)
            """, (days,))
            deleted = cursor.rowcount
        if deleted:
            logging.info(
                "user_sessions retention: удалено %s сессий, истёкших более %s дней назад",
                deleted, days)
        return {'deleted_sessions': deleted, 'grace_days': days}

    def set_session_sensitive_access(self, session_id, user_id, unlocked=True,
                                     actor_id=None, actor_role=None,
                                     ip_address=None, user_agent=None):
        """Открыть/закрыть чувствительные данные в сессии.

        actor_* — кто выполнил действие: подтвердивший QR админ/СВ/глава отдела
        при выдаче, сам оператор при отзыве. Пишется и в саму сессию (кто открыл
        сейчас), и в журнал `session_access_events` (вся история выдач). Оба
        запроса идут на ОДНОМ курсоре: карточка сессии обещает, что запись в
        журнале есть ровно тогда, когда доступ реально переключился.
        """
        unlocked = bool(unlocked)
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE user_sessions
                SET sensitive_data_unlocked = %s,
                    sensitive_data_unlocked_at = CASE
                        WHEN %s THEN (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                        ELSE NULL
                    END,
                    sensitive_data_unlocked_by = CASE
                        WHEN %s THEN %s
                        ELSE NULL
                    END
                WHERE session_id = %s
                  AND user_id = %s
                  AND revoked_at IS NULL
                RETURNING session_id
            """, (unlocked, unlocked, unlocked, actor_id, session_id, user_id))
            if cursor.fetchone() is None:
                return False
            cursor.execute("""
                INSERT INTO session_access_events
                    (session_id, user_id, action, actor_id, actor_role, ip_address, user_agent)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                session_id,
                user_id,
                'granted' if unlocked else 'revoked',
                actor_id,
                (actor_role or None),
                (ip_address or None),
                (user_agent or None)
            ))
            return True

    def is_session_sensitive_access_unlocked(self, session_id, user_id, cursor=None):
        """Подтверждена ли сессия QR-кодом.

        cursor — уже открытый курсор вызывающего. Нужен разделам «Вики» и
        «Обращения»: они проверяют подтверждение внутри своего запроса, и свой
        коннект здесь означал бы ДВА слота пула на один запрос. Пул общий и
        небольшой (MAX_CONN), его же делит SSE аукциона смен, и именно на таком
        удержании двух слотов он однажды и захлебнулся.
        """
        if cursor is not None:
            cursor.execute(self.SENSITIVE_ACCESS_SQL, (session_id, user_id))
            row = cursor.fetchone()
            return bool(row[0]) if row else False
        with self._get_cursor() as own_cursor:
            own_cursor.execute(self.SENSITIVE_ACCESS_SQL, (session_id, user_id))
            row = own_cursor.fetchone()
            return bool(row[0]) if row else False

    def list_user_sessions(self, user_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    session_id::text,
                    user_agent,
                    ip_address,
                    created_at,
                    last_seen_at,
                    expires_at,
                    revoked_at
                FROM user_sessions
                WHERE user_id = %s
                ORDER BY created_at DESC
            """, (user_id,))
            rows = cursor.fetchall()
            return [
                {
                    "session_id": row[0],
                    "user_agent": row[1],
                    "ip_address": row[2],
                    "created_at": row[3],
                    "last_seen_at": row[4],
                    "expires_at": row[5],
                    "revoked_at": row[6]
                }
                for row in rows
            ]

    @classmethod
    def _active_session_device_sql(cls, alias: str = "LOWER(COALESCE(us.user_agent, ''))"):
        """Выражение вида CASE ... END, дающее тип устройства строкой."""
        bot = cls._UA_BOT_SQL.replace('ua', alias)
        tablet = cls._UA_TABLET_SQL.replace('ua ', alias + ' ')
        mobile = cls._UA_MOBILE_SQL.replace('ua', alias)
        return (
            f"CASE WHEN {alias} = '' THEN 'unknown' "
            f"WHEN {bot} THEN 'bot' "
            f"WHEN {tablet} THEN 'tablet' "
            f"WHEN {mobile} THEN 'mobile' "
            f"ELSE 'desktop' END"
        )

    # Живые сессии с их владельцем и типом устройства. Тип считается ОДИН раз
    # здесь и дальше переиспользуется фильтром, плашками и счётчиками человека:
    # прежняя сводка гоняла десяток регулярок по каждой строке заново.
    def _live_sessions_cte(self, materialized: bool = True):
        """Живые сессии с владельцем и типом устройства.

        materialized=True нужен СПИСКУ: без него Postgres встраивает CTE, и
        разбор user-agent пересчитывается на КАЖДЫЙ счётчик устройства — пять
        раз по десятку регулярок на строку (на боевых 1444 сессиях это 183 мс
        против 40 мс).

        КАРТОЧКЕ одного человека он, наоборот, вреден: MATERIALIZED запрещает
        пробросить `WHERE user_id = %s` внутрь CTE, и вместо точечного попадания
        по `idx_user_sessions_user_id` строится весь набор живых сессий ради
        трёх строк. Поэтому там CTE обычный.
        """
        return f"""
            live AS {'MATERIALIZED ' if materialized else ''}(
                SELECT
                    us.session_id::text AS session_id,
                    us.user_id,
                    us.user_agent,
                    us.ip_address,
                    us.created_at,
                    us.last_seen_at,
                    us.expires_at,
                    us.sensitive_data_unlocked,
                    us.sensitive_data_unlocked_at,
                    us.sensitive_data_unlocked_by,
                    unlocker.name AS unlocked_by_name,
                    unlocker.role AS unlocked_by_role,
                    u.name AS user_name,
                    LOWER(COALESCE(u.role, '')) AS user_role,
                    u.login AS user_login,
                    u.supervisor_id,
                    sv.name AS supervisor_name,
                    u.department_id,
                    d.name AS department_name,
                    u.avatar_bucket,
                    u.avatar_blob_path,
                    ({self._active_session_device_sql()}) AS device
                FROM user_sessions us
                JOIN users u ON u.id = us.user_id
                LEFT JOIN users sv ON sv.id = u.supervisor_id
                LEFT JOIN departments d ON d.id = u.department_id
                LEFT JOIN users unlocker ON unlocker.id = us.sensitive_data_unlocked_by
                WHERE us.revoked_at IS NULL
                  AND us.expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
            )
        """

    @staticmethod
    def _live_search_predicate(search: Optional[str]):
        """Поиск идёт и по человеку, и по любой его сессии.

        Найти сотрудника по IP или по ID сессии — обычный сценарий раздела,
        поэтому строка сравнивается с обоими уровнями сразу, а человек попадает
        в выдачу, если совпала хотя бы одна его сессия.
        """
        text = (search or '').strip()
        if not text:
            return '', []
        pattern = f'%{text}%'
        return (
            """
                (
                    session_id ILIKE %s
                    OR COALESCE(ip_address, '') ILIKE %s
                    OR COALESCE(user_agent, '') ILIKE %s
                    OR COALESCE(user_name, '') ILIKE %s
                    OR COALESCE(user_login, '') ILIKE %s
                    OR COALESCE(supervisor_name, '') ILIKE %s
                )
            """,
            [pattern] * 6
        )

    @classmethod
    def _people_row(cls, row):
        item = dict(zip(cls._PEOPLE_COLUMNS, row))
        item['sessions_count'] = int(item['sessions_count'] or 0)
        item['ip_count'] = int(item['ip_count'] or 0)
        item['sensitive_open_count'] = int(item['sensitive_open_count'] or 0)
        item['device_counts'] = {
            'desktop': int(item.pop('desktop_count') or 0),
            'mobile': int(item.pop('mobile_count') or 0),
            'tablet': int(item.pop('tablet_count') or 0),
            'bot': int(item.pop('bot_count') or 0),
            'unknown': int(item.pop('unknown_count') or 0)
        }
        return item

    def _people_filter_sql(self, role: Optional[str] = None, device: Optional[str] = None,
                           department=None, alias: str = 'p'):
        """Фильтры применяются к ЧЕЛОВЕКУ, а не к отдельной сессии.

        «Телефон» означает «у человека есть живая сессия с телефона»: список
        показывает людей, и строка не может исчезнуть наполовину.
        """
        clauses = []
        params: List[Any] = []
        prefix = f'{alias}.' if alias else ''

        role_key = (role or '').strip().lower()
        if role_key in self.ACTIVE_SESSION_ROLE_FILTERS:
            clauses.append(f'{prefix}user_role = ANY(%s)')
            params.append(list(self._ROLE_FILTER_VALUES[role_key]))

        device_key = (device or '').strip().lower()
        if device_key in self.ACTIVE_SESSION_DEVICE_FILTERS:
            clauses.append(f'{prefix}{device_key}_count > 0')

        department_key = self._department_filter(department)
        if department_key == self.ACTIVE_SESSION_DEPARTMENT_NONE:
            clauses.append(f'{prefix}department_id IS NULL')
        elif department_key is not None:
            clauses.append(f'{prefix}department_id = %s')
            params.append(department_key)

        return (' AND '.join(clauses) if clauses else 'TRUE'), params

    def _people_order_by(self, sort_key: Optional[str] = None, sort_dir: Optional[str] = None,
                         alias: str = 'p'):
        """Сортировка считается на сервере: клиент видит только одну страницу."""
        prefix = f'{alias}.' if alias else ''
        column = self.ACTIVE_SESSION_SORT_KEYS.get((sort_key or '').strip())
        direction = 'ASC' if (sort_dir or '').strip().lower() == 'asc' else 'DESC'
        if not column:
            column, direction = 'p.last_seen_at', 'DESC'
        column = prefix + column.split('.', 1)[-1]
        nulls = 'NULLS LAST' if direction == 'DESC' else 'NULLS FIRST'
        # user_id в хвосте — устойчивый порядок между страницами, иначе люди с
        # одинаковым значением прыгают между пачками при догрузке.
        return f'ORDER BY {column} {direction} {nulls}, {prefix}user_id ASC'

    @classmethod
    def _summary_from_row(cls, row):
        row = row or (0,) * cls._SUMMARY_HEAD_COLUMNS
        return {
            'total_people': int(row[0] or 0),
            'total_sessions': int(row[1] or 0),
            'role_counts': {
                'admin': int(row[2] or 0),
                'sv': int(row[3] or 0),
                'operator': int(row[4] or 0)
            },
            'device_counts': {
                'desktop': int(row[5] or 0),
                'mobile': int(row[6] or 0),
                'tablet': int(row[7] or 0),
                'bot': int(row[8] or 0),
                'unknown': int(row[9] or 0)
            },
            'sensitive_people': int(row[10] or 0),
            'sensitive_sessions': int(row[11] or 0),
            'matched_people': int(row[12] or 0),
            'matched_sessions': int(row[13] or 0),
            'departments': cls._departments_from_json(row[14] if len(row) > 14 else None)
        }

    def get_active_session_people_page(self, limit: int = 50, offset: int = 0,
                                       search: Optional[str] = None, role: Optional[str] = None,
                                       device: Optional[str] = None, department=None,
                                       sort_key: Optional[str] = None,
                                       sort_dir: Optional[str] = None):
        """Страница списка людей и сводка — ОДНИМ запросом.

        Раздел показывает по строке на человека, а не на сессию: у одного
        сотрудника их бывает несколько десятков, и список из них ничего не
        сообщал.

        Почему один оператор, а не два. Сводка и страница строятся на одной и
        той же цепочке live → searched → people, и раздельные execute гоняли
        весь разбор живых сессий ДВАЖДЫ на каждое нажатие клавиши в поиске.
        Здесь `people` упоминается трижды в одном операторе и считается один раз.

        Почему счётчик выборки идёт отдельной строкой, а не COUNT(*) OVER ().
        Оконная функция не возвращает НИ ОДНОЙ строки, если OFFSET вырезал
        пустой хвост, и «сколько всего нашли» молча превращалось в ноль на
        догрузке. Агрегат `matched` есть всегда, даже когда страница пуста.

        Плашки считаются по поисковому запросу, но БЕЗ выбранного фильтра:
        иначе, нажав «Админы», пользователь увидел бы нули у остальных плашек и
        не смог бы переключиться обратно. По тому же правилу считается и список
        отделов для пикера — `summary['departments']`.
        """
        limit = max(1, int(limit))
        offset = max(0, int(offset))

        search_sql, search_params = self._live_search_predicate(search)
        # Поиск отбирает ЛЮДЕЙ, а не сессии: нашли человека по IP одной его
        # сессии — показываем его целиком, со всеми остальными. Иначе строка
        # списка сообщала бы «1 сессия» тому, у кого их три.
        searched_cte = (
            """
            searched AS (
                SELECT l.*
                FROM live l
                JOIN (SELECT DISTINCT user_id FROM live WHERE """ + search_sql + """) m
                  ON m.user_id = l.user_id
            )
            """
            if search_sql else
            """
            searched AS (SELECT * FROM live)
            """
        )

        filter_sql, filter_params = self._people_filter_sql(
            role=role, device=device, department=department, alias='p')
        window_order = self._people_order_by(sort_key, sort_dir, alias='p')
        # Выбранный отдел нужен списку отделов, а не фильтру: по нему вторая
        # половина UNION возвращает его в пикер даже без людей в выборке.
        department_key = self._department_filter(department)
        selected_department_id = department_key if isinstance(department_key, int) else None

        query = f"""
            WITH {self._live_sessions_cte()},
            {searched_cte},
            {self._PEOPLE_CTE},
            {self._DEPARTMENTS_CTE},
            summary AS (SELECT {self._SUMMARY_COLUMNS_SQL} FROM people),
            filtered AS (
                SELECT p.*, ROW_NUMBER() OVER ({window_order}) AS rn
                FROM people p
                WHERE {filter_sql}
            ),
            matched AS (
                SELECT COUNT(*) AS matched_people,
                       COALESCE(SUM(sessions_count), 0) AS matched_sessions
                FROM filtered
            )
            SELECT
                s.total_people, s.total_sessions,
                s.admin_people, s.sv_people, s.operator_people,
                s.desktop_people, s.mobile_people, s.tablet_people,
                s.bot_people, s.unknown_people,
                s.sensitive_people, s.sensitive_sessions,
                m.matched_people, m.matched_sessions,
                {self._DEPARTMENTS_JSON_SQL} AS departments,
                {', '.join('f.' + name for name in self._PEOPLE_COLUMNS)}
            FROM summary s
            CROSS JOIN matched m
            LEFT JOIN filtered f ON f.rn > %s AND f.rn <= %s
            ORDER BY f.rn
        """

        params = (tuple(search_params) + (selected_department_id,)
                  + tuple(filter_params) + (offset, offset + limit))
        with self._get_cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        summary = self._summary_from_row(rows[0] if rows else None)
        head = self._SUMMARY_HEAD_COLUMNS
        people = [
            self._people_row(row[head:])
            for row in rows
            if row[head] is not None  # строка-заглушка от LEFT JOIN, когда страница пуста
        ]
        return {
            'people': people,
            'summary': summary,
            'matched_people': summary['matched_people'],
            'matched_sessions': summary['matched_sessions'],
            'has_more': (offset + len(people)) < summary['matched_people']
        }

    def get_active_session_user_detail(self, user_id):
        """Карточка человека: он сам, все его живые сессии и журнал выдач.

        Журнал берётся одним запросом на ВСЕ его сессии, а не по одной на
        сессию: у сотрудника их бывает четыре десятка, и цикл превратил бы
        открытие карточки в сорок походов в базу.
        """
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return None

        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT
                    u.id,
                    u.name,
                    LOWER(COALESCE(u.role, '')) AS role,
                    u.login,
                    u.supervisor_id,
                    sv.name AS supervisor_name,
                    u.department_id,
                    d.name AS department_name,
                    u.avatar_bucket,
                    u.avatar_blob_path,
                    u.status,
                    u.telegram_id
                FROM users u
                LEFT JOIN users sv ON sv.id = u.supervisor_id
                LEFT JOIN departments d ON d.id = u.department_id
                WHERE u.id = %s
            """, (user_id,))
            row = cursor.fetchone()
            if not row:
                return None

            user = {
                'user_id': row[0],
                'user_name': row[1],
                'user_role': row[2],
                'user_login': row[3],
                'supervisor_id': row[4],
                'supervisor_name': row[5],
                'department_id': row[6],
                'department_name': row[7],
                'avatar_bucket': row[8],
                'avatar_blob_path': row[9],
                'user_status': row[10],
                'has_telegram': bool(row[11])
            }

            cursor.execute(f"""
                WITH {self._live_sessions_cte(materialized=False)}
                SELECT
                    session_id, user_id, user_agent, ip_address, created_at, last_seen_at,
                    expires_at, sensitive_data_unlocked, sensitive_data_unlocked_at,
                    sensitive_data_unlocked_by,
                    unlocked_by_name AS sensitive_data_unlocked_by_name,
                    unlocked_by_role AS sensitive_data_unlocked_by_role,
                    device AS device_type
                FROM live
                WHERE user_id = %s
                ORDER BY last_seen_at DESC NULLS LAST, session_id ASC
            """, (user_id,))
            sessions = [dict(zip(self._SESSION_COLUMNS, item)) for item in cursor.fetchall()]

            cursor.execute("""
                SELECT
                    COUNT(*) FILTER (
                        WHERE revoked_at IS NULL
                          AND expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
                    ) AS active_sessions,
                    COUNT(*) AS total_sessions
                FROM user_sessions
                WHERE user_id = %s
            """, (user_id,))
            counts = cursor.fetchone() or (0, 0)
            user['active_sessions'] = int(counts[0] or 0)
            user['total_sessions'] = int(counts[1] or 0)

            cursor.execute("""
                SELECT
                    e.id,
                    e.session_id::text,
                    e.action,
                    e.actor_id,
                    a.name AS actor_name,
                    COALESCE(e.actor_role, a.role) AS actor_role,
                    e.ip_address,
                    e.created_at
                FROM session_access_events e
                LEFT JOIN users a ON a.id = e.actor_id
                WHERE e.user_id = %s
                ORDER BY e.created_at DESC, e.id DESC
                LIMIT 100
            """, (user_id,))
            events = [
                {
                    'id': item[0],
                    'session_id': item[1],
                    'action': item[2],
                    'actor_id': item[3],
                    'actor_name': item[4],
                    'actor_role': item[5],
                    'ip_address': item[6],
                    'created_at': item[7]
                }
                for item in cursor.fetchall()
            ]

        by_session = {}
        for event in events:
            by_session.setdefault(event['session_id'], []).append(event)
        for session in sessions:
            session['access_events'] = by_session.get(session['session_id'], [])

        user['avatar_bucket'] = user.get('avatar_bucket')
        return {'user': user, 'sessions': sessions, 'access_events': events}

    def list_active_session_ids_for_users(self, user_ids):
        """ID живых сессий перечисленных людей — для кнопки «прервать все».

        Собирает их СЕРВЕР, одним запросом. Клиент знает только тех, кто попал
        в загруженную страницу, и цикл «по человеку за запрос» молча пропускал
        выбранных со второй страницы, а на полусотне выбранных превращался в
        полсотни HTTP-запросов подряд.
        """
        ids = []
        for value in (user_ids or []):
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                continue
        if not ids:
            return []
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT session_id::text
                FROM user_sessions
                WHERE user_id = ANY(%s)
                  AND revoked_at IS NULL
                  AND expires_at > (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')
            """, (ids,))
            return [row[0] for row in cursor.fetchall()]

    def list_active_session_ids_for_user(self, user_id):
        """ID живых сессий одного человека."""
        return self.list_active_session_ids_for_users([user_id])

    def update_user(self, user_id, field, value, changed_by=None):
        allowed_fields = [
            'direction_id',
            'supervisor_id',
            'department_id',
            'status',
            'rate',
            'hire_date',
            'name',
            'gender',
            'birth_date',
            'phone',
            'email',
            'personal_email',
            'instagram',
            'telegram_nick',
            'company_name',
            'employment_type',
            'has_proxy',
            'proxy_card_number',
            'proxy_status',
            'has_driver_license',
            'sip_number',
            'study_place',
            'study_specialty',
            'study_course',
            'study_completed',
            'study_completion_year',
            'close_contact_1_relation',
            'close_contact_1_full_name',
            'close_contact_1_phone',
            'close_contact_2_relation',
            'close_contact_2_full_name',
            'close_contact_2_phone',
            'card_number',
            'city',
            'job_title',
            'internship_in_company',
            'front_office_training',
            'front_office_training_date',
            'taxipro_id'
        ]
        if field not in allowed_fields:
            raise ValueError("Invalid field to update")
        
        with self._get_cursor() as cursor:
            # Fetch old value
            cursor.execute(f"SELECT role, {field} FROM users WHERE id = %s", (user_id,))
            row = cursor.fetchone()
            if not row:
                return False
            current_role = str(row[0] or '').strip().lower()
            old_field_value = row[1]
            if current_role == 'trainer' and field in ('direction_id', 'supervisor_id'):
                value = None
            if current_role == 'trainee' and field in ('has_proxy', 'proxy_card_number'):
                value = False if field == 'has_proxy' else None
            if field == 'proxy_status':
                value = normalize_proxy_status_value(value)
            if field == 'employment_type':
                value = str(value).strip().lower() if value is not None else ''
                value = value or None
                if value not in (None, 'gph', 'of', 'smz'):
                    raise ValueError("Invalid employment_type")
            old_value = str(old_field_value) if old_field_value is not None else None

            # When an operator is manually returned from dismissal via "Employees",
            # close active dismissal schedule period at (change_day - 1) to prevent
            # automatic schedule status sync from rolling status back to fired.
            if field == 'status':
                value = str(value or '').strip().lower()
                if current_role == 'operator' and value not in ('fired', 'dismissal'):
                    self._interrupt_dismissal_period_by_work_day_tx(
                        cursor=cursor,
                        operator_id=user_id,
                        work_date=datetime.now().date()
                    )
            
            # Update users table (kept for backward compatibility)
            cursor.execute(f"UPDATE users SET {field} = %s WHERE id = %s RETURNING id", (value, user_id))
            updated = cursor.fetchone() is not None
            
            # Sync to profile tables
            if updated:
                if field in self._OPERATOR_PROFILE_FIELDS and current_role in ('operator', 'trainee'):
                    from psycopg2 import sql as _sql
                    cursor.execute(
                        _sql.SQL("UPDATE operator_profiles SET {} = %s WHERE user_id = %s").format(
                            _sql.Identifier(field)
                        ),
                        (value, user_id)
                    )
                elif field in self._HR_PROFILE_FIELDS:
                    from psycopg2 import sql as _sql
                    cursor.execute(
                        _sql.SQL("UPDATE user_hr_profiles SET {} = %s WHERE user_id = %s").format(
                            _sql.Identifier(field)
                        ),
                        (value, user_id)
                    )

            # Log history if updated
            if updated:
                cursor.execute("""
                    INSERT INTO user_history (user_id, changed_by, field_changed, old_value, new_value)
                    VALUES (%s, %s, %s, %s, %s)
                """, (user_id, changed_by, field, old_value, str(value)))

            # Специальность живёт только вместе с местом учёбы (задача #279).
            # Стёрли место учёбы — стираем и специальность: в карточке поле
            # специальности скрывается, и оставленное значение было бы невидимым
            # для человека, но продолжало бы попадать в выгрузку.
            if updated and field == 'study_place' and not value:
                cursor.execute("SELECT study_specialty FROM users WHERE id = %s", (user_id,))
                specialty_row = cursor.fetchone()
                old_specialty = specialty_row[0] if specialty_row else None
                if old_specialty:
                    cursor.execute("UPDATE users SET study_specialty = NULL WHERE id = %s", (user_id,))
                    cursor.execute(
                        "UPDATE user_hr_profiles SET study_specialty = NULL WHERE user_id = %s",
                        (user_id,)
                    )
                    cursor.execute("""
                        INSERT INTO user_history (user_id, changed_by, field_changed, old_value, new_value)
                        VALUES (%s, %s, 'study_specialty', %s, %s)
                    """, (user_id, changed_by, str(old_specialty), None))

            # При смене ставки фиксируем помесячную ставку (work_hours.rate):
            #  - прошлые месяцы без явной ставки «замораживаем» на прежнем значении,
            #    чтобы перенос (carry-forward) не переписал историю задним числом;
            #  - текущий месяц якорим на новой ставке — перенос распространит её на
            #    последующие месяцы до очередного изменения.
            # norm_hours при этом не трогаем.
            if updated and field == 'rate':
                try:
                    new_rate = float(value)
                except (TypeError, ValueError):
                    new_rate = None
                if new_rate is not None:
                    try:
                        old_rate = float(old_value) if old_value is not None else None
                    except (TypeError, ValueError):
                        old_rate = None
                    if old_rate is not None:
                        cursor.execute("""
                            UPDATE work_hours
                            SET rate = %s
                            WHERE operator_id = %s
                              AND rate IS NULL
                              AND month < TO_CHAR(CURRENT_DATE, 'YYYY-MM')
                        """, (old_rate, user_id))
                    cursor.execute("""
                        INSERT INTO work_hours (operator_id, month, rate)
                        VALUES (%s, TO_CHAR(CURRENT_DATE, 'YYYY-MM'), %s)
                        ON CONFLICT (operator_id, month)
                        DO UPDATE SET rate = EXCLUDED.rate
                    """, (user_id, new_rate))

            return updated

    def get_user_avatar_storage(self, user_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT avatar_bucket, avatar_blob_path, avatar_original_blob_path, avatar_content_type, avatar_file_size, avatar_updated_at
                FROM users
                WHERE id = %s
            """, (user_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "avatar_bucket": row[0],
                "avatar_blob_path": row[1],
                "avatar_original_blob_path": row[2],
                "avatar_content_type": row[3],
                "avatar_file_size": int(row[4]) if row[4] is not None else None,
                "avatar_updated_at": row[5].isoformat() if row[5] else None
            }

    def set_user_avatar(self, user_id, bucket_name, blob_path, original_blob_path=None, content_type=None, file_size=None):
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users
                SET avatar_bucket = %s,
                    avatar_blob_path = %s,
                    avatar_original_blob_path = %s,
                    avatar_content_type = %s,
                    avatar_file_size = %s,
                    avatar_updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING avatar_bucket, avatar_blob_path, avatar_original_blob_path, avatar_content_type, avatar_file_size, avatar_updated_at
            """, (bucket_name, blob_path, original_blob_path, content_type, file_size, user_id))
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "avatar_bucket": row[0],
                "avatar_blob_path": row[1],
                "avatar_original_blob_path": row[2],
                "avatar_content_type": row[3],
                "avatar_file_size": int(row[4]) if row[4] is not None else None,
                "avatar_updated_at": row[5].isoformat() if row[5] else None
            }

    def clear_user_avatar(self, user_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users
                SET avatar_bucket = NULL,
                    avatar_blob_path = NULL,
                    avatar_original_blob_path = NULL,
                    avatar_content_type = NULL,
                    avatar_file_size = NULL,
                    avatar_updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING id
            """, (user_id,))
            row = cursor.fetchone()
            return bool(row)

    def set_user_active(self, user_id, status):
        # допустимые статусы
        allowed_statuses = {"active", "break", "training", "inactive", "tech", "iesigning"}
        if status not in allowed_statuses:
            return False  # неверный статус
    
        # преобразуем статус в boolean
        is_active = True if status == "active" else False
    
        with self._get_cursor() as cursor:
            cursor.execute("""
                UPDATE users 
                SET is_active = %s
                WHERE id = %s AND role = 'operator'
                RETURNING id
            """, (is_active, user_id))
            result = cursor.fetchone()
            if result:
                cursor.execute(
                    "UPDATE operator_profiles SET is_active = %s WHERE user_id = %s",
                    (is_active, user_id)
                )
                return True
            return False

    
    def log_activity(self, operator_id, status):
        allowed_statuses = {"active", "break", "training", "inactive","tech","iesigning"}
        if status not in allowed_statuses:
            return False
    
        try:
            with self._get_cursor() as cursor:
                cursor.execute("""
                    INSERT INTO operator_activity_logs (operator_id, is_active)
                    VALUES (%s, %s)
                """, (operator_id, status))
            return True
        except Exception as e:
            logging.error(f"Error logging activity: {e}")
            return False
            
    def get_last_activity_status(self, operator_id):
        try:
            with self._get_cursor() as cursor:
                cursor.execute("""
                    SELECT is_active
                    FROM operator_activity_logs
                    WHERE operator_id = %s
                    ORDER BY change_time DESC
                    LIMIT 1
                """, (operator_id,))
                row = cursor.fetchone()
                return row[0] if row else None
        except Exception as e:
            logging.error(f"Error fetching last activity status: {e}")
            return None
    
    def get_user_history(self, user_id):
        with self._get_cursor() as cursor:
            cursor.execute("""
                SELECT uh.id, uh.field_changed, uh.old_value, uh.new_value, uh.changed_at, u.name AS changed_by_name
                FROM user_history uh
                LEFT JOIN users u ON uh.changed_by = u.id
                WHERE uh.user_id = %s
                ORDER BY uh.changed_at DESC
            """, (user_id,))
            return [
                {
                    "id": row[0],
                    "field": row[1],
                    "old_value": row[2],
                    "new_value": row[3],
                    "changed_at": row[4].strftime('%Y-%m-%d %H:%M:%S'),
                    "changed_by": row[5] or "System"
                } for row in cursor.fetchall()
            ]

    def _resolve_user_field_as_of_tx(self, cursor, op_ids, field_changed, ref_dt):
        """Значение поля пользователя (status/role/...) на момент ref_dt из user_history.
        Возвращает {user_id: value}. Если изменений до ref_dt не было — самое первое old_value.
        Та же логика, что для статуса в get_daily_hours_by_supervisor_month."""
        out = {}
        if not op_ids:
            return out
        cursor.execute("""
            SELECT user_id, old_value, new_value, changed_at
            FROM user_history
            WHERE field_changed = %s AND user_id = ANY(%s)
            ORDER BY user_id, changed_at
        """, (field_changed, op_ids))
        changes, first_old = {}, {}
        for uid, oldv, newv, at in cursor.fetchall() or []:
            if uid not in changes:
                changes[uid] = []
                first_old[uid] = oldv
            changes[uid].append((at, newv))
        for uid, chs in changes.items():
            eff = None
            for at, newv in chs:
                if at is not None and at < ref_dt:
                    eff = newv
                else:
                    break
            if eff is None:
                eff = first_old.get(uid)
            if eff is not None:
                out[int(uid)] = eff
        return out

    def _interrupt_dismissal_period_by_work_day_tx(self, cursor, operator_id, work_date):
        """
        Если на рабочую дату приходится статус "увольнение", обрываем его:
        - если увольнение начинается в этот день -> период остаётся: дата увольнения
          и есть последний рабочий день, смена за него законна
        - если началось раньше -> ставим конец на день раньше
        """
        operator_id = int(operator_id)
        work_date_obj = self._normalize_schedule_date(work_date)

        cursor.execute("""
            SELECT id, start_date, end_date, COALESCE(is_blacklist, FALSE)
            FROM operator_schedule_status_periods
            WHERE operator_id = %s
              AND status_code = 'dismissal'
              AND start_date <= %s
              AND COALESCE(end_date, DATE '9999-12-31') >= %s
            ORDER BY start_date, id
        """, (operator_id, work_date_obj, work_date_obj))
        rows = cursor.fetchall()

        for period_id, start_date_value, end_date_value, is_blacklist_value in rows:
            if start_date_value >= work_date_obj:
                # Смена в день начала увольнения — это последняя отработанная смена,
                # а не возврат на работу. Раньше период здесь удалялся целиком, и
                # ручное восстановление смены стирало дату и причину увольнения.
                continue
            if bool(is_blacklist_value):
                raise ValueError("ЧС-увольнение нельзя прервать сменой")

            new_end_date = work_date_obj - timedelta(days=1)
            cursor.execute("""
                UPDATE operator_schedule_status_periods
                SET end_date = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
            """, (new_end_date, period_id))
