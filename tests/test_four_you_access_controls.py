from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FourYouAccessControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_source = (ROOT / "src" / "App.jsx").read_text(encoding="utf-8-sig")
        cls.api_source = (ROOT / "bot_schedule2.py").read_text(encoding="utf-8-sig")
        cls.db_source = (ROOT / "database.py").read_text(encoding="utf-8-sig")

    def test_frontend_access_is_bound_to_the_two_user_ids(self):
        self.assertIn("const FOUR_YOU_ADMIN_USER_ID = 2;", self.app_source)
        self.assertIn("const FOUR_YOU_VIEWER_USER_ID = 241;", self.app_source)
        self.assertIn("Number(userLike?.id) === FOUR_YOU_ADMIN_USER_ID", self.app_source)
        self.assertIn("Number(userLike?.id) === FOUR_YOU_VIEWER_USER_ID", self.app_source)
        self.assertIn("normalizeRole(userLike?.role) === 'super_admin'", self.app_source)

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


if __name__ == "__main__":
    unittest.main()
