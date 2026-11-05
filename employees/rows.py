# -*- coding: utf-8 -*-
"""Строки страницы «Учета сотрудников» — в той же форме, что у полных ручек.

Таблицы, карточка сотрудника и окна правки читают поля строки по именам,
поэтому строка страницы обязана совпадать со строкой полного списка:
  • «users» — как get_admin_users (/api/admin/users) в bot_schedule2.py;
  • «supervisors» — как полный профиль get_sv_list (/api/admin/sv_list).
Совпадение набора полей сторожит tests/test_employees_page.py: поле,
добавленное в полную ручку, должно появиться и здесь.
"""

USERS_SQL = """
    SELECT
        u.id,
        u.name,
        d.name as direction,
        s.name as supervisor_name,
        u.direction_id,
        u.supervisor_id,
        u.role,
        u.status,
        u.rate,
        u.hire_date,
        u.gender,
        u.birth_date,
        u.avatar_bucket,
        u.avatar_blob_path,
        u.avatar_updated_at,
        sp.status_code as status_period_status_code,
        sp.start_date as status_period_start_date,
        sp.end_date as status_period_end_date,
        sp.dismissal_reason as status_period_dismissal_reason,
        COALESCE(sp.is_blacklist, FALSE) as status_period_is_blacklist,
        sp.comment as status_period_comment,
        u.phone,
        u.email,
        u.personal_email,
        u.instagram,
        u.telegram_nick,
        u.company_name,
        u.employment_type,
        COALESCE(u.has_proxy, FALSE) as has_proxy,
        u.proxy_card_number,
        u.proxy_status,
        COALESCE(u.has_driver_license, FALSE) as has_driver_license,
        u.sip_number,
        u.study_place,
        u.study_course,
        u.close_contact_1_relation,
        u.close_contact_1_full_name,
        u.close_contact_1_phone,
        u.close_contact_2_relation,
        u.close_contact_2_full_name,
        u.close_contact_2_phone,
        u.card_number,
        COALESCE(u.internship_in_company, FALSE) as internship_in_company,
        COALESCE(u.front_office_training, FALSE) as front_office_training,
        u.front_office_training_date,
        u.taxipro_id,
        COALESCE(u.study_completed, FALSE) as study_completed,
        u.study_completion_year,
        u.department_id,
        grp.group_id,
        grp.group_name,
        u.city,
        u.job_title,
        u.study_specialty
    FROM users u
    LEFT JOIN directions d ON u.direction_id = d.id
    LEFT JOIN users s ON u.supervisor_id = s.id
    LEFT JOIN LATERAL (
        SELECT gom.group_id, g.name AS group_name
        FROM group_operator_memberships gom
        JOIN groups g ON g.id = gom.group_id
        WHERE gom.operator_id = u.id AND gom.end_date IS NULL
        ORDER BY gom.start_date DESC, gom.id DESC
        LIMIT 1
    ) grp ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            p.status_code,
            p.start_date,
            p.end_date,
            p.dismissal_reason,
            p.is_blacklist,
            p.comment
        FROM operator_schedule_status_periods p
        WHERE p.operator_id = u.id
          AND p.status_code = (
              CASE
                  WHEN u.status = 'fired' THEN 'dismissal'
                  WHEN u.status = 'dismissal' THEN 'dismissal'
                  WHEN u.status = 'unpaid_leave' THEN 'bs'
                  ELSE u.status
              END
          )
        ORDER BY
            CASE
                WHEN p.start_date <= CURRENT_DATE
                 AND COALESCE(p.end_date, DATE '9999-12-31') >= CURRENT_DATE
                THEN 0
                ELSE 1
            END,
            p.start_date DESC,
            p.id DESC
        LIMIT 1
    ) sp ON TRUE
    WHERE u.id = ANY(%(ids)s)
"""


def user_row(row, *, avatar_url, proxy_labels):
    return {
        "id": row[0],
        "name": row[1],
        "direction": row[2],
        "supervisor_name": row[3],
        "direction_id": row[4],
        "supervisor_id": row[5],
        "role": row[6],
        "status": row[7],
        "rate": float(row[8]),
        "hire_date": row[9].strftime('%d-%m-%Y') if row[9] else None,
        "gender": row[10],
        "birth_date": row[11].strftime('%d-%m-%Y') if row[11] else None,
        "avatar_url": avatar_url(row[12], row[13]),
        "avatar_updated_at": row[14].isoformat() if row[14] else None,
        "status_period_status_code": row[15],
        "status_period_start_date": row[16].strftime('%Y-%m-%d') if row[16] else None,
        "status_period_end_date": row[17].strftime('%Y-%m-%d') if row[17] else None,
        "status_period_dismissal_reason": row[18] or "",
        "status_period_is_blacklist": bool(row[19]) if row[19] is not None else False,
        "status_period_comment": row[20] or "",
        "phone": row[21] or "",
        "email": row[22] or "",
        "personal_email": row[23] or "",
        "instagram": row[24] or "",
        "telegram_nick": row[25] or "",
        "company_name": row[26] or "",
        "employment_type": row[27] or "",
        "has_proxy": bool(row[28]) if row[28] is not None else False,
        "proxy_card_number": row[29] or "",
        "proxy_status": row[30] or "",
        "proxy_status_label": proxy_labels.get(str(row[30] or '').strip().lower(), ""),
        "has_driver_license": bool(row[31]) if row[31] is not None else False,
        "sip_number": row[32] or "",
        "study_place": row[33] or "",
        "study_course": row[34] or "",
        "close_contact_1_relation": row[35] or "",
        "close_contact_1_full_name": row[36] or "",
        "close_contact_1_phone": row[37] or "",
        "close_contact_2_relation": row[38] or "",
        "close_contact_2_full_name": row[39] or "",
        "close_contact_2_phone": row[40] or "",
        "card_number": row[41] or "",
        "internship_in_company": bool(row[42]) if row[42] is not None else False,
        "front_office_training": bool(row[43]) if row[43] is not None else False,
        "front_office_training_date": row[44].strftime('%Y-%m-%d') if row[44] else None,
        "taxipro_id": row[45] or "",
        "study_completed": bool(row[46]) if row[46] is not None else False,
        "study_completion_year": int(row[47]) if row[47] is not None else None,
        "department_id": row[48],
        "group_id": row[49],
        "group_name": row[50] or "",
        "city": row[51] or "",
        "job_title": row[52] or "",
        "study_specialty": row[53] or "",
    }


SUPERVISORS_SQL = """
    SELECT
        id,
        name,
        hours_table_url,
        role,
        hire_date,
        status,
        gender,
        birth_date,
        avatar_bucket,
        avatar_blob_path,
        avatar_updated_at,
        phone,
        email,
        personal_email,
        instagram,
        telegram_nick,
        company_name,
        employment_type,
        study_place,
        study_course,
        study_completed,
        study_completion_year,
        close_contact_1_relation,
        close_contact_1_full_name,
        close_contact_1_phone,
        close_contact_2_relation,
        close_contact_2_full_name,
        close_contact_2_phone,
        card_number,
        COALESCE(has_proxy, FALSE) as has_proxy,
        proxy_card_number,
        proxy_status,
        internship_in_company,
        front_office_training,
        front_office_training_date,
        taxipro_id,
        rate,
        direction_id,
        supervisor_id,
        department_id,
        city,
        job_title,
        study_specialty
    FROM users
    WHERE id = ANY(%(ids)s)
"""


def supervisor_row(sv, *, avatar_url, proxy_labels, normalize_role):
    return {
        "id": sv[0],
        "name": sv[1],
        "table": sv[2],
        "role": normalize_role(sv[3]),
        "hire_date": sv[4].strftime('%d-%m-%Y') if sv[4] else None,
        "status": sv[5],
        "gender": sv[6],
        "birth_date": sv[7].strftime('%d-%m-%Y') if sv[7] else None,
        "avatar_url": avatar_url(sv[8], sv[9]),
        "avatar_updated_at": sv[10].isoformat() if sv[10] else None,
        "phone": sv[11] or "",
        "email": sv[12] or "",
        "personal_email": sv[13] or "",
        "instagram": sv[14] or "",
        "telegram_nick": sv[15] or "",
        "company_name": sv[16] or "",
        "employment_type": sv[17] or "",
        "study_place": sv[18] or "",
        "study_course": sv[19] or "",
        "study_completed": bool(sv[20]) if sv[20] is not None else False,
        "study_completion_year": int(sv[21]) if sv[21] is not None else None,
        "close_contact_1_relation": sv[22] or "",
        "close_contact_1_full_name": sv[23] or "",
        "close_contact_1_phone": sv[24] or "",
        "close_contact_2_relation": sv[25] or "",
        "close_contact_2_full_name": sv[26] or "",
        "close_contact_2_phone": sv[27] or "",
        "card_number": sv[28] or "",
        "has_proxy": bool(sv[29]) if sv[29] is not None else False,
        "proxy_card_number": sv[30] or "",
        "proxy_status": sv[31] or "",
        "proxy_status_label": proxy_labels.get(str(sv[31] or '').strip().lower(), ""),
        "internship_in_company": bool(sv[32]) if sv[32] is not None else False,
        "front_office_training": bool(sv[33]) if sv[33] is not None else False,
        "front_office_training_date": sv[34].strftime('%Y-%m-%d') if sv[34] else None,
        "taxipro_id": sv[35] or "",
        "rate": float(sv[36]) if sv[36] is not None else 1.0,
        "direction_id": sv[37],
        "supervisor_id": sv[38],
        "department_id": sv[39],
        "city": sv[40] or "",
        "job_title": sv[41] or "",
        "study_specialty": sv[42] or "",
    }


def build_row_loader(*, avatar_url, proxy_labels, normalize_role):
    """load_rows(cursor, list, ids) для page.fetch_page."""
    def load_rows(cursor, list_name, ids):
        if list_name == 'supervisors':
            cursor.execute(SUPERVISORS_SQL, {'ids': list(ids)})
            return [supervisor_row(row, avatar_url=avatar_url, proxy_labels=proxy_labels,
                                   normalize_role=normalize_role) for row in cursor.fetchall()]
        cursor.execute(USERS_SQL, {'ids': list(ids)})
        return [user_row(row, avatar_url=avatar_url, proxy_labels=proxy_labels) for row in cursor.fetchall()]
    return load_rows
