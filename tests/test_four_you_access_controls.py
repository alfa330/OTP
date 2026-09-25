import ast
import re
from pathlib import Path
import unittest

from tests import source_cache


ROOT = Path(__file__).resolve().parents[1]


class FourYouAccessControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
        cls.department_views_source = (ROOT / "src" / "utils" / "departmentViews.js").read_text(encoding="utf-8-sig")
        cls.api_source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        cls.db_source = (ROOT / "database.py").read_text(encoding="utf-8-sig")
        cls.lenta_source = (ROOT / "src" / "components" / "four_you" / "lenta.jsx").read_text(encoding="utf-8-sig")
        cls.lenta_css = (ROOT / "src" / "components" / "four_you" / "lenta.css").read_text(encoding="utf-8-sig")

    def test_frontend_access_is_bound_to_the_admin_only(self):
        self.assertIn("const FOUR_YOU_ADMIN_USER_ID = 2;", self.app_source)
        self.assertIn("Number(userLike?.id) === FOUR_YOU_ADMIN_USER_ID", self.app_source)
        self.assertIn("normalizeRole(userLike?.role) === 'super_admin'", self.app_source)
        # Смотреть раздел может только тот, кто его ведёт.
        self.assertIn(
            "const canAccessFourYouForUser = (userLike) => canManageFourYouForUser(userLike);",
            self.app_source,
        )
        self.assertNotIn('title="Раздел временно недоступен"', self.app_source)
        self.assertIn("onClick={(e) => handleSidebarViewNavigation(e, 'four_you')}", self.app_source)

    def test_viewer_access_is_removed_everywhere(self):
        """Доступ «читателя» (id 241) снят целиком 25.09.2026 — владелец: «убрать
        раздел 4you с доступов полностью». В прошлый раз (a32882ed) id обнулили,
        но оставили и константу, и переменную окружения на бэкенде, — доступ
        вернули одной правкой. Теперь не должно остаться ни того, ни другого."""
        for label, source in (
            ("src/App.jsx", self.app_source),
            ("src/utils/departmentViews.js", self.department_views_source),
            ("bot_schedule2.py", self.api_source),
        ):
            self.assertTrue("FOUR_YOU_VIEWER" not in source, f"{label}: читатель 4 You вернулся")
            self.assertTrue("_four_you_viewer" not in source, f"{label}: читатель 4 You вернулся")
        # Пункт меню «4 You» был только у читателя; тот, кто ведёт раздел,
        # входит через строку в шапке (см. test_mobile_shell).
        self.assertTrue("canAccessFourYouSection && !canManageFourYouSection" not in self.app_source,
                        "пункт меню читателя 4 You вернулся")
        # Имя в подписи комментариев тоже убрано: в карте только тот, кто ведёт раздел.
        annotations = (ROOT / "src" / "components" / "four_you" / "annotations.js").read_text(encoding="utf-8-sig")
        self.assertIn("export const FOUR_YOU_USER_NAMES = { 2: 'Руслан' };", annotations)
        self.assertNotIn("тукеев", self.api_source.lower())

    def test_backend_access_is_bound_to_id_and_admin_role(self):
        self.assertIn("FOUR_YOU_ADMIN_USER_ID = int(os.getenv('FOUR_YOU_ADMIN_USER_ID', '2'))", self.api_source)
        self.assertIn("requester_role == 'super_admin' and requester_id == FOUR_YOU_ADMIN_USER_ID", self.api_source)
        self.assertIn("return can_upload, can_upload", self.api_source)
        # Колокол решает по той же функции, что и раздел, — иначе бейдж «4 You»
        # показывался бы тому, кого в раздел не пускают.
        self.assertIn("can_see_four_you, _ = _four_you_access_for_requester(requester_id, requester)", self.api_source)

    def test_backend_access_function_lets_in_only_the_admin(self):
        """Настоящая функция бэкенда, а не поиск строки: её же зовут гард ручек
        и колокол уведомлений (_notifications_viewer_context)."""
        bot_path = ROOT / "bot_schedule2.py"
        nodes = [
            source_cache.function_copy(bot_path, "_normalize_user_role"),
            source_cache.function_copy(bot_path, "_four_you_access_for_requester"),
        ]
        namespace = {"FOUR_YOU_ADMIN_USER_ID": 2}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(bot_path), "exec"), namespace)
        access = namespace["_four_you_access_for_requester"]

        def requester(role, status="working"):
            # Форма строки как у get_user: role — [3], status — [11].
            row = [None] * 12
            row[3], row[11] = role, status
            return tuple(row)

        self.assertEqual((True, True), access(2, requester("super_admin")))
        self.assertEqual((True, True), access("2", requester("superadmin")))
        # Бывший читатель — ни с какой ролью, в том числе с правами админа.
        for role in ("operator", "trainer", "admin", "super_admin"):
            self.assertEqual((False, False), access(241, requester(role)), role)
        self.assertEqual((False, False), access(2, requester("admin")))
        self.assertEqual((False, False), access(2, requester("super_admin", "fired")))
        self.assertEqual((False, False), access(None, None))

    def test_department_guard_has_no_four_you_exceptions(self):
        self.assertNotIn("viewKey === 'four_you'", self.department_views_source)
        self.assertNotIn("'four_you'", self.department_views_source.split("export const DEPARTMENT_VIEW_ALLOWLIST", 1)[1].split("};", 1)[0])

    def test_every_image_route_requires_authenticated_guard(self):
        list_route = "@app.route('/api/four_you/images', methods=['GET', 'POST', 'OPTIONS'])\n@require_auth"
        delete_route = "@app.route('/api/four_you/images/<image_id>', methods=['DELETE', 'OPTIONS'])\n@require_auth"
        self.assertIn(list_route, self.api_source)
        self.assertIn(delete_route, self.api_source)
        self.assertIn("require_upload=request.method == 'POST'", self.api_source)
        self.assertIn("_four_you_route_guard(require_upload=True)", self.api_source)

    def test_gallery_uses_private_optimized_storage(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS four_you_images", self.db_source)
        self.assertIn("preview_blob_path", self.db_source)
        self.assertIn("display_blob_path", self.db_source)
        self.assertIn("format='WEBP'", self.api_source)
        self.assertIn("max-age=31536000, immutable", self.api_source)

    def test_lenta_preserves_original_motion_parameters(self):
        for expected in (
            "perspective: 4000",
            "step: 160",
            "dirX: 160",
            "dirY: 40",
            "dirZ: -45",
            "selectedZ: 620",
            "leftDownX: -2600",
            "leftDownY: 1750",
            "rightUpX: 2600",
            "rightUpY: -1750",
            "splitZ: -220",
            "expandMixRef.current, expandTarget, 0.036",
            "selectedMixRef.current, expandTarget, 0.13",
        ):
            self.assertIn(expected, self.lenta_source)
        # Размер привязан к экрану (высота ограничивает открытую карточку),
        # пропорции 470x630 (≈1.34) сохраняются — без искажений.
        self.assertIn("--panel-h: clamp(360px, min(64vh, 94vw), 570px);", self.lenta_css)
        self.assertIn("--panel-w: calc(var(--panel-h) / 1.34);", self.lenta_css)
        self.assertIn("window.innerWidth * 0.5", self.lenta_source)
        self.assertIn("window.innerHeight * 0.5", self.lenta_source)

    def test_bulk_delete_route_is_admin_guarded(self):
        batch_route = "@app.route('/api/four_you/images/delete_batch', methods=['POST', 'OPTIONS'])\n@require_auth"
        self.assertIn(batch_route, self.api_source)
        self.assertIn("_four_you_route_guard(require_upload=True)", self.api_source)
        self.assertIn("def delete_four_you_images(self, image_ids)", self.db_source)
        # Пакетное удаление в коде идёт через параметризованный массив (без SQL-инъекций).
        self.assertIn("WHERE id = ANY(%s::uuid[])", self.db_source)

    def test_feed_is_randomized_and_optimized(self):
        # Случайный порядок фото при каждом открытии.
        self.assertIn("const shuffle = (input)", self.lenta_source)
        self.assertIn("shuffle(Array.isArray(response?.data?.images)", self.lenta_source)
        # Оптимизация без потери анимации: куллинг за экраном + пропуск кадров в покое.
        self.assertIn("cullRadius", self.lenta_source)
        self.assertIn("const isSettled", self.lenta_source)
        self.assertIn("needsRenderRef", self.lenta_source)

    def test_higher_quality_variant_loads_seamlessly(self):
        # Превью всегда снизу; полноразмерный вариант проявляется поверх по onLoad —
        # апгрейд качества незаметен (без моргания/пустого кадра).
        self.assertIn("lenta-card-photo-hi", self.lenta_source)
        self.assertIn("classList.add('is-ready')", self.lenta_source)
        self.assertIn("lenta-card-photo-hi.is-ready", self.lenta_css)


    def test_annotations_storage_and_routes(self):
        self.assertIn(
            "ADD COLUMN IF NOT EXISTS annotations JSONB NOT NULL DEFAULT '{}'::jsonb",
            self.db_source,
        )
        self.assertIn("def set_four_you_annotations", self.db_source)
        self.assertIn("def list_four_you_annotations_changed_since", self.db_source)
        self.assertIn(
            "@app.route('/api/four_you/images/<image_id>/annotations', methods=['PUT', 'POST', 'OPTIONS'])",
            self.api_source,
        )
        self.assertIn(
            "@app.route('/api/four_you/annotations/poll', methods=['GET', 'OPTIONS'])",
            self.api_source,
        )
        self.assertIn("def save_four_you_annotations(image_id):", self.api_source)
        self.assertIn("_sanitize_four_you_annotations", self.api_source)
        # Разметку правит любой пользователь с доступом к 4 You (require_upload=False в гарде), а не только uploader.
        self.assertIn("requester_id, _, guard_response, guard_status = _four_you_route_guard()", self.api_source)

    def test_collab_editing_frontend_present(self):
        root_components = ROOT / "src" / "components" / "four_you"
        editor = (root_components / "PhotoEditor.jsx").read_text(encoding="utf-8-sig")
        (root_components / "AnnotationLayer.jsx").read_text(encoding="utf-8-sig")
        (root_components / "Backgrounds.jsx").read_text(encoding="utf-8-sig")
        for token in ("Рисунок", "Стикеры", "Текст", "Коммент", "Фон"):
            self.assertIn(token, editor)
        self.assertIn("annotations/poll", self.lenta_source)        # near-real-time поллинг
        self.assertIn("PhotoEditor", self.lenta_source)             # редактор подключён
        self.assertIn("revealRef", self.lenta_source)               # появление по загрузке фото
        self.assertIn("loadedRef.current[image.id] = true", self.lenta_source)
        self.assertIn("loopRef.current = images.length >= 2 * cullRadius", self.lenta_source)  # зацикливание
        self.assertIn("fy-bg-hearts", self.lenta_css)               # анимированный фон «сердечки»

    def test_new_photo_sidebar_badge_wiring(self):
        # Бейдж новых фото 4 You (как у «Ивентов»): last-seen таблица + методы +
        # роуты seen/unread_count + фронтовое состояние и сброс при открытии.
        self.assertIn("CREATE TABLE IF NOT EXISTS four_you_reads", self.db_source)
        self.assertIn("def mark_four_you_seen", self.db_source)
        self.assertIn("def count_unread_four_you_images", self.db_source)
        self.assertIn("@app.route('/api/four_you/seen', methods=['POST', 'OPTIONS'])", self.api_source)
        self.assertIn("@app.route('/api/four_you/unread_count', methods=['GET', 'OPTIONS'])", self.api_source)
        self.assertIn("fourYouUnreadCount", self.app_source)
        # Само число фронт больше не запрашивает отдельно: его приносит общий
        # ответ центра уведомлений (см. test_badges_come_from_one_request).
        # Открытие раздела гасит счётчик и серверный seen. Проверяем сам факт,
        # а не написание строки: тест, прибитый к точной строке, ломается на
        # любом рефакторинге и охраняет форму вместо гарантии.
        self.assertTrue("setFourYouUnreadCount(0)" in self.app_source,
                        "заход в раздел обязан гасить бейдж")
        self.assertTrue("markBellSourceRead('four_you')" in self.app_source,
                        "колокол обязан узнать, что раздел прочитан")
        self.assertIn("/api/four_you/seen", self.lenta_source)
        # Комментарии видны на карточке и без её выбора (не только при activeIndex).
        self.assertNotIn("activeIndex === index && image.annotations?.comments?.length > 0", self.lenta_source)
        self.assertIn("{!selectMode && image.annotations?.comments?.length > 0 && (", self.lenta_source)

    def test_badges_come_from_one_request(self):
        """Бейджи «Ивенты» и «4 You» питаются из общего ответа колокола.

        Раньше каждый ходил за своим числом сам, и тест сторожил, чтобы эти
        запросы хотя бы не повторялись по таймеру. Теперь запросов нет вовсе —
        счётчики приходят из /api/notifications вместе с остальными, — поэтому
        сторожим уже отсутствие собственных запросов, а не их количество.
        """
        start = self.app_source.find("const stableNotificationsCounts = useCallback(")
        self.assertNotEqual(-1, start, "обработчик counts колокола пропал")
        end = self.app_source.find("}, []);", start)
        self.assertNotEqual(-1, end, "не найден конец обработчика counts")
        block = self.app_source[start:end]
        body = block
        for setter in ("setEventsUnreadCount", "setFourYouUnreadCount"):
            self.assertTrue(setter in body,
                            "%s обязан питаться из ответа колокола" % setter)
        # Ключ, которого в ответе нет, не должен трактоваться как ноль: ответ без
        # источников означает, что сводку собрать не удалось.
        self.assertTrue("in counts" in body,
                        "бейдж обновляется только по реально пришедшим ключам")
        # Именно форма ВЫЗОВА, а не упоминание: имена эндпоинтов остались в
        # комментарии, объясняющем, почему фронт их больше не зовёт.
        # assertNotIn на файле в 50k строк вывалил бы его целиком в отчёт,
        # поэтому проверяем булевым условием с коротким сообщением.
        for gone in ("${API_BASE_URL}/api/events/unread_count",
                     "${API_BASE_URL}/api/four_you/unread_count",
                     "fetchEventsUnreadRef", "fetchFourYouUnreadRef"):
            self.assertTrue(gone not in self.app_source,
                            "%s: бейдж снова ходит за числом сам" % gone)

    def test_no_background_polling_of_badges(self):
        """Опрос по таймеру не должен вернуться ни в каком виде."""
        self.assertNotIn("setInterval(pollIfActive, 45000)", self.app_source)
        self.assertNotIn("document.addEventListener('visibilitychange', pollIfActive);",
                         self.app_source)
        bell = (ROOT / "src" / "components" / "notifications" / "NotificationsBell.jsx"
                ).read_text(encoding="utf-8")
        self.assertNotIn("setInterval", bell,
                         "колокол обязан обновляться по возврату фокуса, а не по таймеру")
        self.assertIn("REFRESH_GAP_MS", bell, "у обновления должен быть троттлинг")


if __name__ == "__main__":
    unittest.main()
