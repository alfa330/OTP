"""База данных: SIP и прокси.

Методы класса Database, вынесено из database.py скриптом scripts/split_database_mixins.py, 02.11.2026,
без изменений тел. Модульные помощники и константы — в db/core.py."""
from db.core import (  # noqa: F401
    build_sip_password,
    execute_values,
    json,
    logging,
    normalize_sip_identifier,
    normalize_sip_provider,
    Optional,
    parse_sip_delay_tri,
    parse_sip_flag,
    parse_sip_flag_tri,
    parse_sip_ring_timeout_rings,
    resolve_sip_auto_answer,
    resolve_sip_auto_answer_delay,
    resolve_sip_ring_timeout_rings,
    SIP_PROVIDER_DEFAULT,
    sip_ring_timeout_seconds,
)


class SipMixin:
    def get_user_sip_number(self, user_id):
        """users.sip_number (внутренний номер = internalNumber в Binotel) или None."""
        if not user_id:
            return None
        with self._get_cursor() as cursor:
            cursor.execute("SELECT sip_number FROM users WHERE id = %s", (user_id,))
            r = cursor.fetchone()
        sip = (str(r[0]).strip() if r and r[0] is not None else "")
        return sip or None

    def get_sip_config(self) -> dict:
        """Глобальные настройки SIP для iCORE Phone (singleton id=1)."""
        with self._get_cursor() as cur:
            cur.execute("""
                SELECT s.sip_server, s.base_password, s.autodial_code, s.autodial_server,
                       s.autodial_base_password, s.updated_by, s.updated_at, u.name
                FROM sip_config s
                LEFT JOIN users u ON u.id = s.updated_by
                WHERE s.id = 1
            """)
            r = cur.fetchone()
        if not r:
            return {"sip_server": "", "base_password": "", "autodial_code": "", "autodial_server": "",
                    "autodial_base_password": "",
                    "updated_by": None, "updated_by_name": None, "updated_at": None}
        return {
            "sip_server": r[0] or "",
            "base_password": r[1] or "",
            "autodial_code": r[2] or "",
            "autodial_server": r[3] or "",
            "autodial_base_password": r[4] or "",
            "updated_by": r[5],
            "updated_by_name": r[7],
            "updated_at": r[6].isoformat() if r[6] else None,
        }

    @staticmethod
    def _mask_sip_secret(value) -> str:
        """Пароль для истории: короткие прячем целиком, длинные — с краями."""
        value = value or ""
        if len(value) <= 6:
            return "*" * len(value)
        return value[:3] + "*" * (len(value) - 5) + value[-2:]

    def update_sip_config(self, payload: dict, user_id: Optional[int] = None) -> dict:
        """Обновить общие настройки SIP + записать изменение в историю (пароль маскируется)."""
        current = self.get_sip_config()
        sip_server = current["sip_server"]
        base_password = current["base_password"]
        autodial_code = current["autodial_code"]
        payload = payload or {}
        if payload.get('sip_server') is not None:
            sip_server = str(payload['sip_server']).strip()
        if payload.get('base_password') is not None:
            base_password = str(payload['base_password'])
        if payload.get('autodial_code') is not None:
            autodial_code = normalize_sip_identifier(payload['autodial_code'], field="Код автодозвона")
        autodial_server = current["autodial_server"]
        if payload.get('autodial_server') is not None:
            autodial_server = str(payload['autodial_server']).strip()
        autodial_base = current["autodial_base_password"]
        if payload.get('autodial_base_password') is not None:
            autodial_base = str(payload['autodial_base_password']).strip()

        snapshot = json.dumps({
            "sip_server": sip_server,
            "base_password": self._mask_sip_secret(base_password),
            "autodial_code": autodial_code,
            "autodial_server": autodial_server,
            "autodial_base_password": self._mask_sip_secret(autodial_base),
        })
        with self._get_cursor() as cur:
            cur.execute("""
                UPDATE sip_config
                SET sip_server = %s, base_password = %s, autodial_code = %s, autodial_server = %s,
                    autodial_base_password = %s,
                    updated_by = %s, updated_at = (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty')
                WHERE id = 1
            """, (sip_server, base_password, autodial_code, autodial_server, autodial_base, user_id))
            cur.execute("""
                INSERT INTO sip_config_history (changed_by, settings)
                VALUES (%s, %s::jsonb)
            """, (user_id, snapshot))
        return self.get_sip_config()

    def get_sip_config_history(self, limit: int = 100) -> list:
        """История изменений SIP: общие настройки, отделы и персональные аккаунты."""
        limit = max(1, min(int(limit or 100), 500))
        with self._get_cursor() as cur:
            cur.execute("""
                SELECT h.changed_at, h.changed_by, u.name, h.settings,
                       h.target_user_id, t.name, h.department_id, d.name
                FROM sip_config_history h
                LEFT JOIN users u ON u.id = h.changed_by
                LEFT JOIN users t ON t.id = h.target_user_id
                LEFT JOIN departments d ON d.id = h.department_id
                ORDER BY h.changed_at DESC, h.id DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
        out = []
        for r in rows:
            out.append({
                "changed_at": r[0].isoformat() if r[0] else None,
                "changed_by": r[1],
                "changed_by_name": r[2],
                "settings": r[3] or {},
                "target_user_id": r[4],
                "target_user_name": r[5],
                "department_id": r[6],
                "department_name": r[7],
                "scope": "operator" if r[4] else ("department" if r[6] else "global"),
            })
        return out

    def get_sip_department_configs(self, department_ids=None) -> list:
        """Настройки SIP по отделам + сколько в отделе сотрудников с телефоном.

        Отдел без своей строки наследовать больше нечего (общий ярус снят) — он
        приходит с configured=False и пустыми полями, чтобы раздел показал
        «телефония не настроена», а не подставил чужую АТС.
        """
        clauses = ["COALESCE(d.is_active, TRUE)"]
        params = []
        if department_ids is not None:
            clauses.append("d.id = ANY(%s)")
            params.append([int(x) for x in department_ids])
        with self._get_cursor() as cur:
            cur.execute(f"""
                SELECT d.id, d.name, d.code,
                       COALESCE(c.sip_server, ''), COALESCE(c.base_password, ''),
                       COALESCE(c.autodial_code, ''), COALESCE(c.autodial_server, ''),
                       COALESCE(c.autodial_base_password, ''),
                       COALESCE(c.provider, 'asterisk') AS provider,
                       (c.department_id IS NOT NULL) AS configured,
                       c.updated_at, u.name,
                       COALESCE(ops.cnt, 0),
                       -- Ярус отдела для автопринятия: NULL = отдел его не
                       -- задавал, и тогда действует выключенное состояние.
                       -- COALESCE здесь нельзя: он схлопнул бы «не задано» с
                       -- «выключено», а это разные вещи для карточки отдела.
                       c.auto_answer, c.auto_answer_delay,
                       COALESCE(c.binotel_cabinet_url, ''),
                       COALESCE(own.cnt, 0),
                       -- Автосброс исходящего — тоже сырым: NULL = «не задано»
                       -- (действуют 5 гудков, сброс включён), 0 = не сбрасывать.
                       -- COALESCE схлопнул бы противоположности.
                       c.ring_timeout_rings
                FROM departments d
                LEFT JOIN sip_department_config c ON c.department_id = d.id
                LEFT JOIN users u ON u.id = c.updated_by
                LEFT JOIN LATERAL (
                    SELECT COUNT(*) AS cnt
                    FROM users o
                    WHERE o.department_id = d.id
                      AND o.role = ANY(%s)
                      AND LOWER(COALESCE(o.status, '')) <> ALL(%s)
                ) ops ON TRUE
                -- Сколько человек отдела автопринятие себе ЗАДАЛИ сами: на них
                -- общая настройка не действует, и панель обязана это показать —
                -- иначе «включил общий автоприём, а у половины не сработало».
                LEFT JOIN LATERAL (
                    SELECT COUNT(*) AS cnt
                    FROM users o
                    JOIN user_sip_settings s ON s.user_id = o.id
                    WHERE o.department_id = d.id
                      AND o.role = ANY(%s)
                      AND LOWER(COALESCE(o.status, '')) <> ALL(%s)
                      AND (s.auto_answer IS NOT NULL OR s.auto_answer_delay IS NOT NULL)
                ) own ON TRUE
                WHERE {' AND '.join(clauses)}
                ORDER BY d.name
            """, tuple([list(self._SIP_OPERATOR_ROLES), list(self._SIP_INACTIVE_STATUSES),
                        list(self._SIP_OPERATOR_ROLES), list(self._SIP_INACTIVE_STATUSES)] + params))
            rows = cur.fetchall()
        return [{
            "department_id": r[0],
            "department_name": r[1] or "",
            "department_code": r[2] or "",
            "sip_server": r[3],
            "base_password": r[4],
            "autodial_code": r[5],
            "autodial_server": r[6],
            "autodial_base_password": r[7],
            "provider": r[8] or SIP_PROVIDER_DEFAULT,
            "configured": bool(r[9]),
            "updated_at": r[10].isoformat() if r[10] else None,
            "updated_by_name": r[11],
            "operators_count": int(r[12] or 0),
            # None — отдел автопринятие не настраивал (не то же самое, что «выключено»).
            "auto_answer": None if r[13] is None else bool(r[13]),
            "auto_answer_delay": None if r[14] is None else int(r[14]),
            "binotel_cabinet_url": r[15] or "",
            "own_auto_answer_count": int(r[16] or 0),
            # None — отдел не задавал (действует умолчание), 0 — сброс выключен.
            "ring_timeout_rings": None if r[17] is None else int(r[17]),
        } for r in rows]

    def update_sip_department_config(self, department_id: int, payload: dict, user_id=None) -> dict:
        """Сохранить настройки отдела.

        Строку убираем, только если отдел на локальной АТС и все её поля пусты:
        у отдела на Binotel полей отдела нет вообще (всё персональное), и удаление
        строки потеряло бы сам признак провайдера."""
        department_id = int(department_id)
        payload = payload or {}
        current = next(
            (row for row in self.get_sip_department_configs([department_id])
             if row["department_id"] == department_id),
            None,
        )
        if current is None:
            raise ValueError("Отдел не найден")

        def _field(key):
            if payload.get(key) is None:
                return current[key]
            return str(payload[key]).strip()

        sip_server = _field('sip_server')
        base_password = _field('base_password')
        autodial_code = normalize_sip_identifier(_field('autodial_code'), field="Код автодозвона")
        autodial_server = _field('autodial_server')
        autodial_base = _field('autodial_base_password')
        provider = normalize_sip_provider(payload.get('provider'), current['provider'])
        cabinet_url = _field('binotel_cabinet_url')
        # Автопринятие отдела трёхпозиционное, как и персональное: «не задано»
        # (None) — это не «выключено», а «ярус не используется».
        auto_answer = parse_sip_flag_tri(payload.get('auto_answer'), current['auto_answer'])
        auto_answer_delay = parse_sip_delay_tri(
            payload.get('auto_answer_delay'), current['auto_answer_delay'])
        # Автосброс исходящего. «Не прислали» здесь — отсутствие КЛЮЧА, а не None:
        # пустота у этого поля занята под «по умолчанию», и форма, которая поля не
        # знает (старая вкладка сайта), иначе молча вернула бы отделу снятый ноль.
        if 'ring_timeout_rings' in payload:
            ring_timeout_rings = parse_sip_ring_timeout_rings(
                payload['ring_timeout_rings'], current.get('ring_timeout_rings'))
        else:
            ring_timeout_rings = current.get('ring_timeout_rings')
        # Ноль у автосброса — настройка («не сбрасывать»), а не пустое поле:
        # отдел на локальной АТС, у которого задан только он, строку не теряет.
        filled = (sip_server, base_password, autodial_code, autodial_server, autodial_base,
                  cabinet_url, auto_answer is not None, auto_answer_delay is not None,
                  ring_timeout_rings is not None)
        # Binotel описывается одним признаком провайдера, поэтому его строка
        # «непустая» даже без единого заполненного поля.
        keep_row = any(filled) or provider != SIP_PROVIDER_DEFAULT
        unchanged = (
            sip_server == current['sip_server']
            and base_password == current['base_password']
            and autodial_code == current['autodial_code']
            and autodial_server == current['autodial_server']
            and autodial_base == current['autodial_base_password']
            and provider == current['provider']
            and cabinet_url == current['binotel_cabinet_url']
            and auto_answer == current['auto_answer']
            and auto_answer_delay == current['auto_answer_delay']
            and ring_timeout_rings == current.get('ring_timeout_rings')
        )
        if unchanged and current['configured'] == keep_row:
            return current

        with self._get_cursor() as cur:
            if keep_row:
                cur.execute("""
                    INSERT INTO sip_department_config (
                        department_id, sip_server, base_password, autodial_code, autodial_server,
                        autodial_base_password, provider, binotel_cabinet_url,
                        auto_answer, auto_answer_delay, ring_timeout_rings, updated_by, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
                    ON CONFLICT (department_id) DO UPDATE SET
                        sip_server = EXCLUDED.sip_server,
                        base_password = EXCLUDED.base_password,
                        autodial_code = EXCLUDED.autodial_code,
                        autodial_server = EXCLUDED.autodial_server,
                        autodial_base_password = EXCLUDED.autodial_base_password,
                        provider = EXCLUDED.provider,
                        binotel_cabinet_url = EXCLUDED.binotel_cabinet_url,
                        auto_answer = EXCLUDED.auto_answer,
                        auto_answer_delay = EXCLUDED.auto_answer_delay,
                        ring_timeout_rings = EXCLUDED.ring_timeout_rings,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = EXCLUDED.updated_at
                """, (department_id, sip_server, base_password, autodial_code, autodial_server,
                      autodial_base, provider, cabinet_url, auto_answer, auto_answer_delay,
                      ring_timeout_rings, user_id))
            else:
                cur.execute("DELETE FROM sip_department_config WHERE department_id = %s", (department_id,))
            cur.execute("""
                INSERT INTO sip_config_history (changed_by, department_id, settings)
                VALUES (%s, %s, %s::jsonb)
            """, (user_id, department_id, json.dumps({
                "provider": provider,
                "sip_server": sip_server,
                "base_password": self._mask_sip_secret(base_password),
                "autodial_code": autodial_code,
                "autodial_server": autodial_server,
                "autodial_base_password": self._mask_sip_secret(autodial_base),
                "binotel_cabinet_url": cabinet_url,
                "auto_answer": auto_answer,
                "auto_answer_delay": auto_answer_delay,
                # Выключенный автосброс меняет звонки всему отделу — в истории
                # его видно так же, как общий автоприём.
                "ring_timeout_rings": ring_timeout_rings,
            })))
        return next(
            row for row in self.get_sip_department_configs([department_id])
            if row["department_id"] == department_id
        )

    @staticmethod
    def _sip_operator_row(row) -> dict:
        return {
            "id": row[0],
            "name": row[1] or "",
            "role": row[2] or "",
            "status": row[3] or "",
            "department_id": row[4],
            "department_name": row[5] or "",
            "supervisor_id": row[6],
            "group_name": row[7] or "",
            "sip_number": row[8] or "",
            "sip_password": row[9] or "",
            "sip_domain": row[10] or "",
            "autodial_number": row[11] or "",
            "autodial_password": row[12] or "",
            "autodial_domain": row[13] or "",
            "updated_at": row[14].isoformat() if row[14] else None,
            "updated_by_name": row[15] or None,
            # Настройки отдела — пустые, если отдел их не задавал (тогда общие).
            "department_sip_server": row[16] or "",
            "department_base_password": row[17] or "",
            "department_autodial_code": row[18] or "",
            "department_autodial_server": row[19] or "",
            "department_autodial_base_password": row[20] or "",
            # Строки в user_sip_settings у большинства нет — COALESCE в SELECT
            # уже отдал TRUE, поэтому здесь достаточно приведения к bool.
            "fop2_enabled": bool(row[21]),
            # Провайдер отдела: решает, какие поля вообще имеют смысл у сотрудника.
            "department_provider": row[22] or SIP_PROVIDER_DEFAULT,
            # SIP-логин Binotel («sip-fixture-912»); у локальной АТС пусто = логин равен номеру.
            "sip_login": row[23] or "",
            "binotel_cabinet_login": row[24] or "",
            "binotel_employee_id": row[25] or "",
            "binotel_cabinet_url": row[26] or "",
            # Автопринятие: None = «как у отдела». Эффективное значение считают
            # resolve_sip_auto_answer* — одним правилом на панель и на телефон,
            # чтобы копии не разъехались.
            "auto_answer": None if row[27] is None else bool(row[27]),
            "auto_answer_delay": None if row[28] is None else int(row[28]),
            "department_auto_answer": None if row[29] is None else bool(row[29]),
            "department_auto_answer_delay": None if row[30] is None else int(row[30]),
            "department_binotel_cabinet_url": row[31] or "",
            # Направление: id для фильтра, имя для подписи. Имя не уникально
            # (разные отделы, архивные версии), поэтому ключ — всегда id.
            "direction_id": row[32],
            "direction_name": row[33] or "",
            "has_binotel_cabinet_password": bool(row[34]),
            # Кабинет Oktell: его логин и пароль клиент Oktell получает от нас
            # после входа по учётке iCORE, поэтому логин показываем в карточке,
            # а пароль — никогда, только признаком «задан».
            "oktell_cabinet_login": row[35] or "",
            "has_oktell_cabinet_password": bool(row[36]),
            "department_code": row[37] or "",
            # Автосброс исходящего: эффективное значение считает
            # resolve_sip_ring_timeout_rings (не задано — 5 гудков).
            # Сдвигается вместе с любой новой колонкой: это ПОСЛЕДНИЙ индекс.
            "department_ring_timeout_rings": None if row[38] is None else int(row[38]),
        }

    def get_sip_operators(self, department_ids=None, supervisor_id=None,
                          include_inactive: bool = False) -> list:
        """Сотрудники с телефоном для раздела «Настройки SIP».

        department_ids=None — без ограничения по отделам (админ). Иначе показываем
        сотрудников этих отделов плюс тех, кого запросивший ведёт лично
        (supervisor_id) — так операторы без отдела не выпадают из списка.
        """
        clauses = ["u.role = ANY(%s)"]
        params = [list(self._SIP_OPERATOR_ROLES)]
        if not include_inactive:
            clauses.append("LOWER(COALESCE(u.status, '')) <> ALL(%s)")
            params.append(list(self._SIP_INACTIVE_STATUSES))
        dept_ids = [int(d) for d in (department_ids or []) if d is not None]
        if department_ids is not None:
            scope = ["u.department_id = ANY(%s)"]
            params.append(dept_ids)
            if supervisor_id:
                scope.append("u.supervisor_id = %s")
                params.append(int(supervisor_id))
            clauses.append("(" + " OR ".join(scope) + ")")
        sql = f"{self._SIP_OPERATOR_SELECT} WHERE {' AND '.join(clauses)} ORDER BY u.name"
        with self._get_cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [self._sip_operator_row(r) for r in rows]

    def get_sip_operator(self, user_id: int) -> Optional[dict]:
        """Одна карточка сотрудника с его SIP-параметрами (или None)."""
        with self._get_cursor() as cur:
            cur.execute(f"{self._SIP_OPERATOR_SELECT} WHERE u.id = %s", (int(user_id),))
            row = cur.fetchone()
        return self._sip_operator_row(row) if row else None

    def get_sip_operators_by_ids(self, user_ids) -> list:
        """Карточки нескольких сотрудников одним запросом (массовые операции)."""
        ids = sorted({int(u) for u in (user_ids or [])})
        if not ids:
            return []
        with self._get_cursor() as cur:
            cur.execute(f"{self._SIP_OPERATOR_SELECT} WHERE u.id = ANY(%s) ORDER BY u.name", (ids,))
            rows = cur.fetchall()
        return [self._sip_operator_row(r) for r in rows]

    def find_sip_number_owners(self, entries, exclude_user_ids=None) -> dict:
        """{(номер, домен): {'user_id', 'name', 'kind', 'domain'}} — кто уже занял пару.

        Номер уникален В ПРЕДЕЛАХ ДОМЕНА: на разных АТС одинаковые внутренние
        номера — норма, а вот два оператора на одном домене с одним номером
        ломают привязку звонков. Поэтому сверяем пару «номер + эффективный
        домен», где пустой персональный домен подставляется из общих настроек.
        Проверяем оба аккаунта: основной и автодозвон.
        """
        numbers, domains = [], []
        for number, domain in (entries or []):
            number = str(number or '').strip()
            if not number:
                continue
            numbers.append(number)
            domains.append(self.normalize_sip_domain(domain))
        if not numbers:
            return {}
        exclude = sorted({int(u) for u in (exclude_user_ids or []) if u is not None})
        with self._get_cursor() as cur:
            cur.execute("""
                WITH wanted AS (
                    SELECT w.num, w.dom
                    FROM unnest(%s::text[], %s::text[]) AS w(num, dom)
                ), taken AS (
                    -- Домен сотрудника: персональный → домен его отдела.
                    -- Общего яруса больше нет: отдел без настроек даёт пустой
                    -- домен, а пустые пары в сравнении не участвуют (иначе все
                    -- ненастроенные отделы конфликтовали бы друг с другом).
                    SELECT TRIM(u.sip_number) AS num,
                           COALESCE(
                               NULLIF(LOWER(TRIM(COALESCE(s.sip_domain, ''))), ''),
                               NULLIF(LOWER(TRIM(COALESCE(dc.sip_server, ''))), ''),
                               '') AS dom,
                           u.id, u.name, 'main'::text AS kind
                    FROM users u
                    LEFT JOIN user_sip_settings s ON s.user_id = u.id
                    LEFT JOIN sip_department_config dc ON dc.department_id = u.department_id
                    WHERE NULLIF(TRIM(COALESCE(u.sip_number, '')), '') IS NOT NULL
                      -- Уволенный номер за собой не держит: иначе освободившийся
                      -- добавочный не выдать новому сотруднику, а список раздела
                      -- ушедших и так не показывает.
                      AND LOWER(COALESCE(u.status, '')) <> ALL(%s)
                    UNION ALL
                    -- У автодозвона может быть своя АТС: его домен отдела,
                    -- иначе домен основного аккаунта того же отдела.
                    SELECT TRIM(s.autodial_number),
                           COALESCE(
                               NULLIF(LOWER(TRIM(COALESCE(s.autodial_domain, ''))), ''),
                               NULLIF(LOWER(TRIM(COALESCE(dc.autodial_server, ''))), ''),
                               NULLIF(LOWER(TRIM(COALESCE(dc.sip_server, ''))), ''),
                               ''),
                           u.id, u.name, 'autodial'::text
                    FROM user_sip_settings s
                    JOIN users u ON u.id = s.user_id
                    LEFT JOIN sip_department_config dc ON dc.department_id = u.department_id
                    WHERE NULLIF(TRIM(COALESCE(s.autodial_number, '')), '') IS NOT NULL
                      AND LOWER(COALESCE(u.status, '')) <> ALL(%s)
                )
                SELECT w.num, w.dom, t.id, t.name, t.kind
                FROM wanted w
                JOIN taken t ON t.num = w.num AND t.dom = w.dom
                WHERE w.dom <> '' AND t.id <> ALL(%s)
            """, (numbers, domains, list(self._SIP_INACTIVE_STATUSES),
                  list(self._SIP_INACTIVE_STATUSES), exclude))
            rows = cur.fetchall()
        owners = {}
        for number, domain, owner_id, owner_name, kind in rows:
            owners.setdefault((number, domain), {
                "user_id": owner_id, "name": owner_name or "", "kind": kind, "domain": domain,
            })
        return owners

    def find_sip_login_owner(self, sip_login, exclude_user_ids=None) -> Optional[dict]:
        """Кто уже занял этот SIP-логин, или None.

        В отличие от номера логин Binotel уникален ГЛОБАЛЬНО, без привязки к
        домену: это учётка на стороне провайдера, и два оператора с одним
        логином отбирали бы регистрацию друг у друга."""
        sip_login = str(sip_login or '').strip()
        if not sip_login:
            return None
        exclude = sorted({int(u) for u in (exclude_user_ids or []) if u is not None})
        with self._get_cursor() as cur:
            cur.execute("""
                SELECT u.id, u.name
                FROM user_sip_settings s
                JOIN users u ON u.id = s.user_id
                WHERE LOWER(TRIM(s.sip_login)) = LOWER(%s)
                  AND u.id <> ALL(%s)
                LIMIT 1
            """, (sip_login, exclude))
            row = cur.fetchone()
        return {"user_id": row[0], "name": row[1] or ""} if row else None

    def save_user_sip_settings(self, user_id: int, payload: dict, changed_by=None) -> dict:
        """Сохранить SIP-аккаунты сотрудника одной транзакцией.

        Основной номер уходит в users.sip_number (+ operator_profiles и
        user_history, как в карточке сотрудника), персональные пароль/домен и
        аккаунт автодозвона — в user_sip_settings. Если персональных значений
        не осталось, строку удаляем, чтобы не копить пустышки.
        """
        user_id = int(user_id)
        payload = payload or {}
        current = self.get_sip_operator(user_id)
        if not current:
            raise ValueError("Сотрудник не найден")

        def _field(key, default):
            if payload.get(key) is None:
                return default
            return str(payload[key]).strip()

        def _flag(key, default):
            # Разбор вынесен в parse_sip_flag: тем же правилам подчиняется
            # массовое изменение, а два разных «что считать выключенным»
            # разошлись бы при первой же правке.
            return parse_sip_flag(payload.get(key), default)

        provider = current.get('department_provider') or SIP_PROVIDER_DEFAULT
        binotel = provider == 'binotel'

        sip_number = normalize_sip_identifier(_field('sip_number', current['sip_number']), field="SIP-номер")
        sip_password = _field('sip_password', current['sip_password'])
        sip_domain = _field('sip_domain', current['sip_domain'])
        # SIP-логин Binotel через normalize_sip_identifier не гоняем: тот режет
        # «@» и «:» и рубит на 64 символах, а логин здесь — выданная провайдером
        # непрозрачная строка, и портить её нельзя.
        sip_login = _field('sip_login', current['sip_login'])[:128]
        # Автопринятие разбираем ВНЕ ветки провайдера, в отличие от FOP2 ниже:
        # это поведение самого телефона (окно звонка и таймер ответа), АТС о нём
        # не знает — значит настройка одинаково нужна и таксопаркам, и Тезу.
        # Трёхпозиционно: None = «как у отдела». Через _flag/parse_sip_delay
        # нельзя — у них нет состояния «наследовать», и снять персональную
        # настройку было бы нечем (см. parse_sip_flag_tri).
        auto_answer = parse_sip_flag_tri(
            payload.get('auto_answer'), current.get('auto_answer'))
        auto_answer_delay = parse_sip_delay_tri(
            payload.get('auto_answer_delay'), current.get('auto_answer_delay'))
        if binotel:
            # Автодозвон — механика локальной АТС (второй номер, код, свой FOP2).
            # В Binotel его нет, и пустые поля тут не «наследование», а отсутствие.
            autodial_number = autodial_password = autodial_domain = ''
            # FOP2 в Binotel не существует: телефон не должен даже пытаться.
            fop2_enabled = False
        else:
            autodial_number = normalize_sip_identifier(
                _field('autodial_number', current['autodial_number']), field="Номер автодозвона")
            autodial_password = _field('autodial_password', current['autodial_password'])
            autodial_domain = _field('autodial_domain', current['autodial_domain'])
            # Флаг относится только к основному аккаунту: у автодозвона свой FOP2.
            fop2_enabled = _flag('fop2_enabled', current.get('fop2_enabled', True))
            sip_login = ''

        # Занятость номера считается в пределах домена: одинаковые номера на
        # разных АТС конфликтом не считаются. Домен «по умолчанию» для этого
        # сотрудника — его отдела, а если отдел не настроен — общий. У
        # автодозвона своя цепочка: он часто живёт на отдельной АТС.
        # Только домен отдела: общего яруса больше нет (одна унификация на все
        # отделы опасна). У отдела без своих настроек домен пуст — и пара
        # «номер + домен» тогда не проверяется, см. ниже.
        common_domain = self.normalize_sip_domain(current.get('department_sip_server'))
        autodial_common = self.normalize_sip_domain(
            current.get('department_autodial_server')) or common_domain

        def _effective(domain):
            return self.normalize_sip_domain(domain) or common_domain

        def _effective_autodial(domain):
            return self.normalize_sip_domain(domain) or autodial_common

        main_pair = (sip_number, _effective(sip_domain))
        autodial_pair = (autodial_number, _effective_autodial(autodial_domain))
        if sip_number and autodial_number and main_pair == autodial_pair:
            raise ValueError("Номер автодозвона должен отличаться от основного (или быть на другом домене)")

        was = {
            (current['sip_number'], _effective(current['sip_domain'])),
            (current['autodial_number'], _effective_autodial(current['autodial_domain'])),
        }
        # Пара без домена ни о чём не говорит: у отдела без настроек домен пуст,
        # и такие пары схлопнулись бы в «номер@» — все отделы стали бы конфликтовать
        # друг с другом. Уникальность номера проверяем только там, где домен известен.
        changed_pairs = [p for p in (main_pair, autodial_pair) if p[0] and p[1] and p not in was]
        owners = self.find_sip_number_owners(changed_pairs, exclude_user_ids=[user_id])
        if owners:
            (number, domain), owner = next(iter(owners.items()))
            raise ValueError(f"Номер {number} на домене {domain or '—'} уже занят: {owner['name']}")
        # SIP-логин Binotel уникален глобально: он и есть учётка на стороне провайдера,
        # два оператора с одним логином отобрали бы регистрацию друг у друга.
        if sip_login and sip_login != current['sip_login']:
            owner = self.find_sip_login_owner(sip_login, exclude_user_ids=[user_id])
            if owner:
                raise ValueError(f"SIP-логин {sip_login} уже занят: {owner['name']}")
        # Линию Binotel может держать и сотрудник ДРУГОГО отдела, посаженный на неё в
        # разделе «Удаленный КЦ»: его привязка лежит отдельно от SIP-настроек, и обе
        # проверки выше её не видят. Два телефона на одной учётке перебивали бы
        # регистрацию друг другу, поэтому занятую так линию здесь не отдаём.
        if binotel:
            new_login = sip_login if sip_login != current['sip_login'] else ''
            new_number, new_domain = main_pair if main_pair in changed_pairs else ('', '')
            if new_login or new_number:
                holder = self.find_dial_list_line_holder(
                    sip_login=new_login, number=new_number, domain=new_domain,
                    exclude_user_ids=[user_id])
                if holder:
                    raise ValueError(
                        f"Линия {holder['internal_number']} занята: на ней сидит {holder['name']}")

        # Выключенный FOP2 — тоже персональная настройка: без него строка с
        # одним лишь fop2_enabled=FALSE ушла бы в DELETE ниже, и флаг молча
        # вернулся бы к «включён» (COALESCE отдаёт TRUE при отсутствии строки).
        # У автопринятия теперь есть ярус отдела, поэтому строку держит сам ФАКТ
        # персонального значения, а не его истинность: личное «выключено» при
        # общем «включено» — это настройка, и терять её при чистке нельзя
        # (прямое требование владельца: своё у оператора сохраняется).
        has_overrides = any((sip_password, sip_domain, sip_login, autodial_number,
                             autodial_password, autodial_domain, not fop2_enabled,
                             auto_answer is not None,
                             auto_answer_delay is not None))

        # Учётка кабинета: пустой пароль = «не менять» (так же ведёт себя сам
        # кабинет), поэтому «не передали» и «передали пусто» здесь одно и то же.
        cab_login = _field('binotel_cabinet_login', current['binotel_cabinet_login'])
        cab_url = _field('binotel_cabinet_url', current['binotel_cabinet_url'])
        cab_employee = _field('binotel_employee_id', current['binotel_employee_id'])[:32]
        cab_password_raw = payload.get('binotel_cabinet_password')
        cab_password = None if cab_password_raw is None else str(cab_password_raw).strip()
        if not binotel:
            # У локальной АТС полей кабинета нет. Игнорируем их, но и не стираем:
            # отдел могли перевести на Binotel и обратно.
            cab_login = current['binotel_cabinet_login']
            cab_url = current['binotel_cabinet_url']
            cab_employee = current['binotel_employee_id']
            cab_password = None
        cabinet_changed = binotel and any((
            cab_login != current['binotel_cabinet_login'],
            cab_url != current['binotel_cabinet_url'],
            cab_employee != current['binotel_employee_id'],
            bool(cab_password),
        ))

        # Учётка кабинета Oktell — ровно те же правила, но у ДРУГОГО провайдера:
        # это отделы локальной АТС («Таксопарки»), а не Binotel. Пустой пароль
        # снова значит «не менять»: иначе сохранение карточки ради телефона
        # стирало бы человеку доступ в АТС, а заметил бы он это только на смене.
        ok_login = _field('oktell_cabinet_login', current['oktell_cabinet_login'])
        ok_password_raw = payload.get('oktell_cabinet_password')
        ok_password = None if ok_password_raw is None else str(ok_password_raw).strip()
        if binotel:
            # У Теза кабинета Oktell нет. Поля игнорируем, но не стираем: отдел
            # могли перевести с одной АТС на другую и обратно.
            ok_login = current['oktell_cabinet_login']
            ok_password = None
        oktell_changed = (not binotel) and any((
            ok_login != current['oktell_cabinet_login'],
            bool(ok_password),
        ))

        changed = any((
            sip_number != current['sip_number'],
            sip_password != current['sip_password'],
            sip_domain != current['sip_domain'],
            sip_login != current['sip_login'],
            autodial_number != current['autodial_number'],
            autodial_password != current['autodial_password'],
            autodial_domain != current['autodial_domain'],
            # Без этой строки PUT с одним только флагом уходил бы в ранний return.
            fop2_enabled != bool(current.get('fop2_enabled', True)),
            # Сравнение трёхпозиционное: None ≠ False, иначе возврат настройки
            # к «как у отдела» уходил бы в ранний return и молча не сохранялся.
            auto_answer != current.get('auto_answer'),
            auto_answer_delay != current.get('auto_answer_delay'),
            cabinet_changed,
            oktell_changed,
        ))
        if not changed:
            return current

        with self._get_cursor() as cur:
            if sip_number != current['sip_number']:
                cur.execute(
                    "UPDATE users SET sip_number = %s WHERE id = %s",
                    (sip_number or None, user_id))
                cur.execute(
                    "UPDATE operator_profiles SET sip_number = %s WHERE user_id = %s",
                    (sip_number or None, user_id))
                cur.execute("""
                    INSERT INTO user_history (user_id, changed_by, field_changed, old_value, new_value)
                    VALUES (%s, %s, 'sip_number', %s, %s)
                """, (user_id, changed_by, current['sip_number'] or None, sip_number or None))
            if has_overrides:
                cur.execute("""
                    INSERT INTO user_sip_settings (
                        user_id, sip_password, sip_domain, sip_login,
                        autodial_number, autodial_password, autodial_domain,
                        fop2_enabled, auto_answer, auto_answer_delay,
                        updated_by, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
                    ON CONFLICT (user_id) DO UPDATE SET
                        sip_password = EXCLUDED.sip_password,
                        sip_domain = EXCLUDED.sip_domain,
                        sip_login = EXCLUDED.sip_login,
                        autodial_number = EXCLUDED.autodial_number,
                        autodial_password = EXCLUDED.autodial_password,
                        autodial_domain = EXCLUDED.autodial_domain,
                        fop2_enabled = EXCLUDED.fop2_enabled,
                        auto_answer = EXCLUDED.auto_answer,
                        auto_answer_delay = EXCLUDED.auto_answer_delay,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = EXCLUDED.updated_at
                """, (user_id, sip_password, sip_domain, sip_login, autodial_number,
                      autodial_password, autodial_domain, fop2_enabled,
                      auto_answer, auto_answer_delay, changed_by))
            else:
                cur.execute("DELETE FROM user_sip_settings WHERE user_id = %s", (user_id,))
            if cabinet_changed:
                # Пароль пишем только когда его прислали: COALESCE(NULLIF(...))
                # оставляет прежний, поэтому «сохранить без пароля» не стирает его.
                cur.execute("""
                    INSERT INTO binotel_user_accounts (
                        user_id, cabinet_login, cabinet_password, employee_id, cabinet_url,
                        updated_by, updated_at)
                    VALUES (%s, %s, COALESCE(%s, ''), %s, %s, %s,
                            (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
                    ON CONFLICT (user_id) DO UPDATE SET
                        cabinet_login = EXCLUDED.cabinet_login,
                        cabinet_password = COALESCE(
                            NULLIF(EXCLUDED.cabinet_password, ''),
                            binotel_user_accounts.cabinet_password),
                        employee_id = EXCLUDED.employee_id,
                        cabinet_url = EXCLUDED.cabinet_url,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = EXCLUDED.updated_at
                """, (user_id, cab_login, cab_password, cab_employee, cab_url, changed_by))
            if oktell_changed:
                # Тот же приём, что у кабинета Binotel: пароль переписывается
                # только когда его прислали, иначе остаётся прежний.
                cur.execute("""
                    INSERT INTO oktell_user_accounts (
                        user_id, cabinet_login, cabinet_password, updated_by, updated_at)
                    VALUES (%s, %s, COALESCE(%s, ''), %s,
                            (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))
                    ON CONFLICT (user_id) DO UPDATE SET
                        cabinet_login = EXCLUDED.cabinet_login,
                        cabinet_password = COALESCE(
                            NULLIF(EXCLUDED.cabinet_password, ''),
                            oktell_user_accounts.cabinet_password),
                        updated_by = EXCLUDED.updated_by,
                        updated_at = EXCLUDED.updated_at
                """, (user_id, ok_login, ok_password, changed_by))
            cur.execute("""
                INSERT INTO sip_config_history (changed_by, target_user_id, settings)
                VALUES (%s, %s, %s::jsonb)
            """, (changed_by, user_id, json.dumps({
                "provider": provider,
                "sip_number": sip_number,
                "sip_login": sip_login,
                "sip_password": self._mask_sip_secret(sip_password),
                "sip_domain": sip_domain,
                "binotel_cabinet_login": cab_login,
                "binotel_employee_id": cab_employee,
                # Пароль кабинета в историю не попадает — только факт замены.
                "binotel_cabinet_password_set": bool(cab_password),
                "oktell_cabinet_login": ok_login,
                # Пароль кабинета Oktell в историю не попадает — только факт замены.
                "oktell_cabinet_password_set": bool(ok_password),
                "autodial_number": autodial_number,
                "autodial_password": self._mask_sip_secret(autodial_password),
                "autodial_domain": autodial_domain,
                # Не секрет — пишем как есть, иначе на вкладке «История» не
                # видно, кто и когда отключил оператору вход в FOP2.
                "fop2_enabled": fop2_enabled,
                # Автопринятие — тем более: настройка меняет поведение телефона
                # на глазах у клиента, и «кто включил» спросят первым делом.
                "auto_answer": auto_answer,
                "auto_answer_delay": auto_answer_delay,
            })))
        return self.get_sip_operator(user_id)

    def bulk_update_user_sip_overrides(self, user_ids, payload: dict, changed_by=None) -> list:
        """Массово проставить пароль/домен и вход в FOP2 выбранным сотрудникам.

        Меняем только переданные поля — остальные (в том числе номера, они
        уникальны у каждого) остаются как есть. Пустое значение = «вернуть
        общие»; если персональных значений не осталось, строку удаляем.
        Всё одной транзакцией и батчами, без запроса на каждого сотрудника.
        """
        ids = sorted({int(u) for u in (user_ids or [])})
        if not ids:
            raise ValueError("Не выбран ни один сотрудник")
        payload = payload or {}
        fields = {
            key: str(payload[key]).strip()
            for key in self._SIP_OVERRIDE_FIELDS
            if payload.get(key) is not None
        }
        # Флаг идёт мимо текстового разбора: у него своя семантика пустоты
        # (см. parse_sip_flag), а «не передали» по-прежнему значит «не меняем».
        if payload.get('fop2_enabled') is not None:
            fields['fop2_enabled'] = parse_sip_flag(payload['fop2_enabled'])
        # Автопринятие — так же мимо, но трёхпозиционно: кроме «включить» и
        # «выключить» массово просят и «вернуть как у отдела» (SIP_INHERIT_TOKEN),
        # иначе снять настройку с пачки было бы нечем.
        if payload.get('auto_answer') is not None:
            fields['auto_answer'] = parse_sip_flag_tri(payload['auto_answer'])
        if payload.get('auto_answer_delay') is not None:
            fields['auto_answer_delay'] = parse_sip_delay_tri(payload['auto_answer_delay'])
        if not fields:
            raise ValueError("Не выбрано ни одного поля для изменения")

        # Общего яруса нет: домен по умолчанию — только отдела сотрудника.
        global_domain = ''
        global_autodial = ''

        with self._get_cursor() as cur:
            cur.execute("""
                SELECT u.id, u.name,
                       COALESCE(s.sip_password, ''), COALESCE(s.sip_domain, ''),
                       COALESCE(s.autodial_number, ''), COALESCE(s.autodial_password, ''),
                       COALESCE(s.autodial_domain, ''), COALESCE(u.sip_number, ''),
                       COALESCE(dc.sip_server, ''), COALESCE(dc.autodial_server, ''),
                       COALESCE(s.fop2_enabled, TRUE), COALESCE(s.sip_login, ''),
                       -- Сырыми, без COALESCE: NULL = «как у отдела», и схлопнуть
                       -- его с «выключено» значило бы стереть наследование у всей
                       -- выбранной пачки при правке любого другого поля.
                       s.auto_answer,
                       s.auto_answer_delay
                FROM users u
                LEFT JOIN user_sip_settings s ON s.user_id = u.id
                LEFT JOIN sip_department_config dc ON dc.department_id = u.department_id
                WHERE u.id = ANY(%s)
            """, (ids,))
            current, names, numbers, dept_domain, dept_autodial = {}, {}, {}, {}, {}
            for r in cur.fetchall():
                current[r[0]] = {
                    "sip_password": r[2], "sip_domain": r[3], "autodial_number": r[4],
                    "autodial_password": r[5], "autodial_domain": r[6],
                    # Флаг обязан ехать в current даже когда его не меняют: иначе
                    # чистка пароля удалила бы строку и молча вернула FOP2 тем,
                    # кому его выключали (COALESCE отдаёт TRUE без строки).
                    "fop2_enabled": bool(r[10]),
                    # Логин Binotel массовые операции не меняют, но обязаны его
                    # сохранить: иначе чистка пароля отправит строку в DELETE и
                    # оператор потеряет учётку у провайдера.
                    "sip_login": r[11],
                    # Автопринятие — по той же причине: без него массовая правка
                    # пароля сочла бы строку пустой и выключила бы настройку тем,
                    # кому её включали. None здесь — «как у отдела».
                    "auto_answer": None if r[12] is None else bool(r[12]),
                    "auto_answer_delay": None if r[13] is None else int(r[13]),
                }
                names[r[0]] = r[1] or ""
                numbers[r[0]] = r[7]
                # Домен «по умолчанию» у каждого свой: отдела, иначе общий.
                # У автодозвона — своя цепочка (может быть отдельная АТС).
                dept_domain[r[0]] = self.normalize_sip_domain(r[8]) or global_domain
                dept_autodial[r[0]] = (
                    self.normalize_sip_domain(r[9]) or global_autodial or dept_domain[r[0]])

        def _effective(domain, user_id):
            return self.normalize_sip_domain(domain) or dept_domain.get(user_id, global_domain)

        def _effective_autodial(domain, user_id):
            return self.normalize_sip_domain(domain) or dept_autodial.get(user_id, global_autodial)

        # Смена домена переносит номера на другую АТС — там они могут быть заняты.
        # Считаем итоговые пары «номер + домен» и сверяем их и между выбранными,
        # и с остальными сотрудниками. Делаем это до записи и вне курсора записи,
        # чтобы не держать два коннекта пула разом.
        def _pairs_of(state, user_id):
            """Обе пары «номер + домен» сотрудника; пустые номера отбрасываем."""
            return [pair for pair in (
                (numbers.get(user_id, ''), _effective(state['sip_domain'], user_id)),
                (state['autodial_number'], _effective_autodial(state['autodial_domain'], user_id)),
            ) if pair[0]]

        # Считаем в два прохода. Сначала занимаем пары теми выбранными, кто с
        # места не двинется: find_sip_number_owners исключает всю выборку
        # целиком, поэтому без этого переезд одного встал бы ровно на номер
        # другого выбранного — молча и с 200 в ответ.
        targets, movers, seen_pairs = {}, {}, {}
        for user_id in ids:
            before = current.get(user_id)
            if before is None:
                continue
            after = {**before, **fields}
            if after != before:
                targets[user_id] = after
            if any(after[key] != before[key] for key in self._SIP_OVERRIDE_FIELDS):
                movers[user_id] = after
                continue
            # Выключатель FOP2 номеров не касается: у оставшегося на месте
            # сверять нечего — пара, давно задвоенная в данных, иначе завалила
            # бы всю пачку сообщением про чужой номер. Но занятой она остаётся,
            # и двое стоящих на одной паре друг другу не мешают: было и было.
            for pair in _pairs_of(before, user_id):
                seen_pairs.setdefault(pair, user_id)

        planned = []
        for user_id, after in movers.items():
            for pair in _pairs_of(after, user_id):
                twin = seen_pairs.get(pair)
                if twin is not None and twin != user_id:
                    raise ValueError(
                        f"Номер {pair[0]} на домене {pair[1] or '—'} достанется двоим: "
                        f"{names.get(twin, '')} и {names.get(user_id, '')}")
                seen_pairs[pair] = user_id
                planned.append(pair)

        owners = self.find_sip_number_owners(planned, exclude_user_ids=ids)
        if owners:
            (number, domain), owner = next(iter(owners.items()))
            raise ValueError(f"Номер {number} на домене {domain or '—'} уже занят: {owner['name']}")

        with self._get_cursor() as cur:
            upserts, deletes, history = [], [], []
            for user_id, after in targets.items():
                # Выключенный FOP2 — тоже повод хранить строку, поэтому считаем
                # не any(after.values()): там лежит и fop2_enabled=True, который
                # сам по себе строку не оправдывает (это состояние по умолчанию).
                # Правило то же, что в save_user_sip_settings: строку держит сам
                # факт персонального автопринятия, а не его истинность — личное
                # «выключено» поверх общего «включено» обязано пережить чистку.
                if any((after['sip_password'], after['sip_domain'], after['sip_login'],
                        after['autodial_number'], after['autodial_password'],
                        after['autodial_domain'], not after['fop2_enabled'],
                        after['auto_answer'] is not None,
                        after['auto_answer_delay'] is not None)):
                    upserts.append((
                        user_id, after['sip_password'], after['sip_domain'], after['sip_login'],
                        after['autodial_number'], after['autodial_password'],
                        after['autodial_domain'], after['fop2_enabled'],
                        after['auto_answer'], after['auto_answer_delay'], changed_by,
                    ))
                else:
                    deletes.append(user_id)
                history.append((changed_by, user_id, json.dumps({
                    "sip_password": self._mask_sip_secret(after['sip_password']),
                    "sip_domain": after['sip_domain'],
                    "autodial_number": after['autodial_number'],
                    "autodial_password": self._mask_sip_secret(after['autodial_password']),
                    "autodial_domain": after['autodial_domain'],
                    "fop2_enabled": after['fop2_enabled'],
                    "auto_answer": after['auto_answer'],
                    "auto_answer_delay": after['auto_answer_delay'],
                    "bulk": True,
                })))

            if upserts:
                execute_values(cur, """
                    INSERT INTO user_sip_settings (
                        user_id, sip_password, sip_domain, sip_login,
                        autodial_number, autodial_password, autodial_domain,
                        fop2_enabled, auto_answer, auto_answer_delay,
                        updated_by, updated_at)
                    VALUES %s
                    ON CONFLICT (user_id) DO UPDATE SET
                        sip_password = EXCLUDED.sip_password,
                        sip_domain = EXCLUDED.sip_domain,
                        sip_login = EXCLUDED.sip_login,
                        autodial_number = EXCLUDED.autodial_number,
                        autodial_password = EXCLUDED.autodial_password,
                        autodial_domain = EXCLUDED.autodial_domain,
                        fop2_enabled = EXCLUDED.fop2_enabled,
                        auto_answer = EXCLUDED.auto_answer,
                        auto_answer_delay = EXCLUDED.auto_answer_delay,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = EXCLUDED.updated_at
                """, upserts,
                    template="(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, (CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty'))")
            if deletes:
                cur.execute("DELETE FROM user_sip_settings WHERE user_id = ANY(%s)", (deletes,))
            if history:
                execute_values(cur, """
                    INSERT INTO sip_config_history (changed_by, target_user_id, settings)
                    VALUES %s
                """, history, template="(%s, %s, %s::jsonb)")

        return self.get_sip_operators_by_ids(ids)

    def _sip_department_ring_timeout_rings(self, department_id):
        """Ярус автосброса исходящего у отдела: число гудков или None («не задано»).

        Нужен сидящему на линии другого отдела: строка из _SIP_OPERATOR_SELECT
        несёт ярус ЕГО отдела, а звонит он на учётке линии — значит, и сброс по
        правилу отдела линии. Сбой чтения — None, то есть умолчание: исключение
        отсюда _dial_list_line_account молча превратил бы в регистрацию на
        телефонии собственного отдела человека, то есть увёл бы его с линии.
        """
        if department_id is None:
            return None
        try:
            with self._get_cursor() as cur:
                cur.execute(
                    "SELECT ring_timeout_rings FROM sip_department_config WHERE department_id = %s",
                    (int(department_id),))
                row = cur.fetchone()
        except Exception:
            logging.exception("Не удалось прочитать автосброс исходящего отдела %s", department_id)
            return None
        return None if not row or row[0] is None else int(row[0])

    def get_user_sip_account(self, user_id: int, line_member: Optional[dict] = None) -> dict:
        """Готовые данные регистрации для iCORE Phone: основной аккаунт + автодозвон.

        Значения берутся по цепочке «персональное → отдела»; глобального яруса
        больше нет. У Binotel персональны логин и пароль — их «на отдел» не
        задать; сервер с задачи #309 наследуется, как и автопринятие.

        line_member — привязка сотрудника к линии Binotel ДРУГОГО отдела (раздел
        «Удаленный КЦ», dial_list.service.line_member). Пока она действует, телефон
        работает на этой линии: учётка, сервер и ярусы автоприёма и автосброса
        исходящих берутся у линии, а телефония собственного отдела человека (номер,
        автодозвон, FOP2, кабинет) не применяется — и остаётся нетронутой до снятия
        с линии."""
        row = self.get_sip_operator(int(user_id)) or {}
        on_line = bool(line_member) and (line_member.get("provider") or '') == 'binotel'
        if on_line:
            row = dict(
                row,
                department_provider='binotel',
                sip_number=line_member.get("internal_number") or "",
                sip_login=line_member.get("sip_login") or "",
                sip_password=line_member.get("sip_password") or "",
                # Персональный домен человека — от АТС его отдела, к линии не относится.
                sip_domain="",
                department_sip_server=line_member.get("sip_server") or "",
                department_auto_answer=line_member.get("auto_answer"),
                department_auto_answer_delay=line_member.get("auto_answer_delay"),
                # Автосброс исходящего — тоже по отделу линии. Привязка его пока не
                # несёт (dial_list.service._LINE_MEMBER_SQL), поэтому без ключа
                # читаем строку отдела линии сами.
                department_ring_timeout_rings=(
                    line_member.get("ring_timeout_rings") if "ring_timeout_rings" in line_member
                    else self._sip_department_ring_timeout_rings(line_member.get("department_id"))),
            )
        provider = row.get("department_provider") or SIP_PROVIDER_DEFAULT
        sip_number = (row.get("sip_number") or "").strip()
        # Автопринятие считаем ОДИН раз и выше развилки провайдера: ниже два
        # независимых return'а, и правило, посчитанное внутри ветки, стало бы
        # двумя копиями — на этом поле уже обжигались.
        auto_answer = resolve_sip_auto_answer(
            row.get("auto_answer"), row.get("department_auto_answer"))
        auto_answer_delay = resolve_sip_auto_answer_delay(
            row.get("auto_answer_delay"), row.get("department_auto_answer_delay"))
        # Автосброс исходящего без ответа — по тому же правилу «один раз до
        # развилки». Ярус только отдела: не задано — 5 гудков, 0 — не сбрасывать.
        # Секунды — для обычного исходящего телефона, отсчёт от первого 180/183.
        ring_timeout_rings = resolve_sip_ring_timeout_rings(
            row.get("department_ring_timeout_rings"))
        ring_timeout_sec = sip_ring_timeout_seconds(ring_timeout_rings)

        if provider == 'binotel':
            login = (row.get("sip_login") or "").strip()
            password = row.get("sip_password") or ""
            # Сервер у Binotel тоже наследуется: пустое поле сотрудника значит
            # «общий сервер отдела» — иначе каждому новому пришлось бы вбивать
            # sip52.binotel.com руками (задача #309).
            server = ((row.get("sip_domain") or "").strip()
                      or (row.get("department_sip_server") or "").strip())
            main = None
            if login and password and server:
                main = {
                    # Регистрируемся логином провайдера, а не номером.
                    "username": login,
                    "password": password,
                    "server": server,
                    "transport": "UDP",
                    "domain": server,
                    "auth_id": login,
                    # Внутренний номер — только для показа и матчинга звонков.
                    "number": sip_number,
                }
            return {
                "provider": provider,
                "main": main,
                # Автодозвон — механика локальной АТС, в Binotel его нет.
                "autodial": None,
                "autodial_code": "",
                # FOP2 у Binotel не существует: телефон не должен и пытаться.
                "fop2_enabled": False,
                # Автопринятие, наоборот, живёт целиком в телефоне и провайдера
                # не касается — отдаём его обеим ветками одинаково, посчитанным
                # выше развилки (персональное → отдела → выключено).
                "auto_answer": auto_answer,
                "auto_answer_delay": auto_answer_delay,
                # Автосброс исходящего кладёт трубку сам телефон — провайдер тут
                # тоже ни при чём, значение посчитано выше развилки.
                "ring_timeout_rings": ring_timeout_rings,
                "ring_timeout_sec": ring_timeout_sec,
                # У сидящего на линии другого отдела учётка кабинета (если есть) — от
                # кабинета ЕГО отдела: телефон менял бы статус не в той компании.
                "binotel": None if on_line else self.get_binotel_account(user_id),
            }

        server = (row.get("department_sip_server") or "").strip()
        # Автодозвон может стоять на отдельной АТС; если её не задали — тот же сервер.
        autodial_server = (row.get("department_autodial_server") or "").strip() or server
        base = row.get("department_base_password") or ""
        # У автодозвона своя база: пароль = база автодозвона + номер автодозвона.
        autodial_base = row.get("department_autodial_base_password") or base
        autodial_code = row.get("department_autodial_code") or ""
        autodial_number = (row.get("autodial_number") or "").strip()

        def _account(number, password, domain, fallback_server, fallback_base):
            if not number:
                return None
            resolved = (domain or fallback_server)
            return {
                "username": number,
                "password": password or build_sip_password(fallback_base, number),
                "server": resolved,
                "transport": "UDP",
                # Явные поля рядом с плоскими: у локальной АТС логин и есть номер,
                # но телефон один и тот же код применяет к обоим провайдерам.
                "domain": resolved,
                "auth_id": number,
                "number": number,
            }

        return {
            "provider": provider,
            "main": _account(sip_number, row.get("sip_password"), row.get("sip_domain"), server, base),
            "autodial": _account(autodial_number, row.get("autodial_password"),
                                 row.get("autodial_domain"), autodial_server, autodial_base),
            "autodial_code": autodial_code,
            # Выключатель входа в FOP2 для основного аккаунта. Режим автодозвона
            # логинится в свой FOP2 и этим флагом не управляется.
            "fop2_enabled": bool(row.get("fop2_enabled", True)),
            # Автопринятие входящего в самом телефоне — та же настройка, что и у
            # Binotel выше: провайдер на неё не влияет, и значение здесь ровно то
            # же, посчитанное один раз до развилки.
            "auto_answer": auto_answer,
            "auto_answer_delay": auto_answer_delay,
            "ring_timeout_rings": ring_timeout_rings,
            "ring_timeout_sec": ring_timeout_sec,
            "binotel": None,
        }
