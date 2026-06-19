from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FourYouAccessControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
        cls.api_source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        cls.db_source = (ROOT / "database.py").read_text(encoding="utf-8-sig")
        cls.lenta_source = (ROOT / "src" / "components" / "four_you" / "lenta.jsx").read_text(encoding="utf-8-sig")
        cls.lenta_css = (ROOT / "src" / "components" / "four_you" / "lenta.css").read_text(encoding="utf-8-sig")

    def test_frontend_access_is_bound_to_the_two_user_ids(self):
        self.assertIn("const FOUR_YOU_ADMIN_USER_ID = 2;", self.app_source)
        self.assertIn("const FOUR_YOU_VIEWER_USER_ID = 241;", self.app_source)
        self.assertIn("Number(userLike?.id) === FOUR_YOU_ADMIN_USER_ID", self.app_source)
        self.assertIn("Number(userLike?.id) === FOUR_YOU_VIEWER_USER_ID", self.app_source)
        self.assertIn("normalizeRole(userLike?.role) === 'super_admin'", self.app_source)
        self.assertIn('title="Раздел временно недоступен"', self.app_source)

    def test_backend_access_is_bound_to_id_and_admin_role(self):
        self.assertIn("FOUR_YOU_ADMIN_USER_ID = int(os.getenv('FOUR_YOU_ADMIN_USER_ID', '2'))", self.api_source)
        self.assertIn("FOUR_YOU_VIEWER_USER_ID = int(os.getenv('FOUR_YOU_VIEWER_USER_ID', '241') or 241)", self.api_source)
        self.assertIn("requester_role == 'super_admin' and requester_id == FOUR_YOU_ADMIN_USER_ID", self.api_source)
        self.assertIn("requester_id == int(viewer_user_id)", self.api_source)
        self.assertNotIn("тукеев", self.api_source.lower())

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


if __name__ == "__main__":
    unittest.main()
