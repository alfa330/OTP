# -*- coding: utf-8 -*-
"""Точечная обработка базы: какие загрузки (файлы базы месяца) операторы обзванивают сейчас.

Владелец, 17.11.2026: «надо добавить удобный выбор загруженных баз на точечную обработку, с возможностью
выбрать несколько и отображать прогресс по каждой базе». Решения владельца в тот же день: в обоих отделах
раздела (Тез КЦ и Удалённый КЦ); операторы получают водителей ТОЛЬКО из отмеченных файлов, а когда ничего
не отмечено — вся база месяца, как было.

Отметка — dial_list_lead_batches.focused. Одно условие для всех мест, где видно «кому звонить»:

  * пул выдачи (_POOL_SQL): новые списки операторам, «доступно сейчас», «в базе ещё» у телефона, проверка
    подписания и поездок перед выдачей — они берут пул тем же запросом;
  * этап журнала: водитель неотмеченного файла, которому звонить ещё есть смысл, — «База на паузе», а не
    «В очереди» (фильтр «В очереди» обязан показывать ровно то, что выдаст пул);
  * сводка: сколько водителей неотмеченных файлов можно было бы выдать (предупреждение «операторы ждут»).

Чего отметка НЕ трогает (решение 17.11.2026, сказано владельцу в вопросе): уже выданные операторам списки
они дорабатывают; обещанный водителю звонок — свой срок оператора у Тез (перезвон, «дожать»: due_kind и
owner_call_at) — идёт мимо отметок целиком: и к своему оператору (_AUTO_OWN_DUE_SQL), и, когда своё окно
прошло, общим пулом к любому (ревью 17.11.2026: иначе звонок ждал бы, пока файл отметят); «Позвонить ещё
раз» — тоже мимо пула.

Водитель относится к файлу, если пришёл с ним впервые (first_batch_id), если файл — его последняя загрузка
(last_batch_id) или если был в файле по записи загрузки (dial_list_batch_leads — пишется с 17.11.2026 при
каждой загрузке; до того ни один водитель не загружался трижды, так что first и last покрывают всё).

Условия — без параметров и без «%» и фигурных скобок: вставляются в любой запрос как есть, в том числе в
строки, которые потом проходят через str.format (_POOL_SQL — «{lock}»). Исключения названы в своих docstring:
file_members_sql (именованные %(department_id)s и %(period)s) и in_batch_sql (плейсхолдер вызывающего).
"""


def in_work_sql(alias="l"):
    """SQL-условие «водителя строки `alias` (dial_list_leads) сейчас можно выдавать по отметкам файлов»:
    в его месяце отдела не отмечен ни один файл, или отмечен один из его файлов, или у него обещанный звонок
    (свой срок оператора у Тез). Месяц и отдел — самой строки, поэтому условие годится и для выборки по всем
    месяцам (журнал «Все месяцы»).

    Всегда TRUE или FALSE, не NULL (водитель без файла при отметках — FALSE): журнал берёт NOT от условия.
    «Отмечен один из его файлов» — IN по НЕкоррелированным подзапросам: Postgres строит хэш отмеченных файлов
    и их водителей один раз на запрос (EXISTS с id файла водителя шёл бы отдельным подзапросом на каждую
    строку: пул из 45 тыс. водителей — 132 мс вместо 68). Отмеченные файлы других отделов в хэше не мешают:
    id файла уникален, а файл водителя — всегда его отдела и месяца."""
    return (
        f"(NOT EXISTS (SELECT 1 FROM dial_list_lead_batches bf_any"
        f" WHERE bf_any.department_id = {alias}.department_id AND bf_any.period = {alias}.period"
        f" AND bf_any.focused)"
        f" OR COALESCE({alias}.first_batch_id IN (SELECT bf_first.id FROM dial_list_lead_batches bf_first"
        f" WHERE bf_first.focused), FALSE)"
        f" OR COALESCE({alias}.last_batch_id IN (SELECT bf_last.id FROM dial_list_lead_batches bf_last"
        f" WHERE bf_last.focused), FALSE)"
        f" OR {alias}.id IN (SELECT bf_m.lead_id FROM dial_list_batch_leads bf_m"
        f" JOIN dial_list_lead_batches bf_mb ON bf_mb.id = bf_m.batch_id WHERE bf_mb.focused)"
        f" OR ({alias}.due_kind IN ('callback', 'online') AND {alias}.owner_call_at IS NOT NULL))"
    )


def file_members_sql():
    """Запрос «пары (файл, водитель) базы месяца отдела»: первая и последняя загрузки водителя и записи загрузок
    (UNION убирает повторы: у водителя, загруженного одним файлом, это одна пара). Колонки — batch_id, lead_id;
    параметры — %(department_id)s и %(period)s. Одним объединением, а не подзапросом на каждого водителя:
    прогресс 20 файлов базы в 45 тыс. водителей — 250 мс против 360 (замер 17.11.2026)."""
    return (
        "SELECT ml.first_batch_id AS batch_id, ml.id AS lead_id FROM dial_list_leads ml"
        " WHERE ml.department_id = %(department_id)s AND ml.period = %(period)s AND ml.first_batch_id IS NOT NULL"
        " UNION SELECT ml.last_batch_id, ml.id FROM dial_list_leads ml"
        " WHERE ml.department_id = %(department_id)s AND ml.period = %(period)s AND ml.last_batch_id IS NOT NULL"
        " UNION SELECT bl.batch_id, bl.lead_id FROM dial_list_batch_leads bl"
        " JOIN dial_list_lead_batches mb ON mb.id = bl.batch_id"
        " WHERE mb.department_id = %(department_id)s AND mb.period = %(period)s"
    )


def in_batch_sql(alias, batch_param):
    """SQL-условие «водитель строки `alias` был в файле `batch_param`» (`batch_param` — готовый плейсхолдер
    запроса, например «%(batch_id)s::uuid»): фильтр «Файл» журнала по тем же правилам, что прогресс файла."""
    return (
        f"({alias}.first_batch_id = {batch_param} OR {alias}.last_batch_id = {batch_param}"
        f" OR EXISTS (SELECT 1 FROM dial_list_batch_leads bl_in WHERE bl_in.batch_id = {batch_param}"
        f" AND bl_in.lead_id = {alias}.id))"
    )
