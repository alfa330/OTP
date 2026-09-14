# -*- coding: utf-8 -*-
"""Выход из аккаунта не ждёт сервер (жалоба 14.09.2026: «экран просто зависает»).

Окно подтверждения выхода в App.jsx рисуется ВМЕСТО всего приложения, а сессию
гасили только после ответа /api/logout без таймаута. Любая пауза сервера —
деплой, перезапуск, провал связи на телефоне — превращалась в застывший экран с
живой кнопкой. Здесь сторожится, чтобы ожидание не вернулось, и чтобы вместе с
ним не потерялся отзыв сессии: торопясь, легко стереть токен раньше, чем запрос
его унесёт, и сервер оставит сессию живой.

Проверки текстовые: App.jsx на пятьдесят тысяч строк без сборки не отрендерить.
"""

import unittest
from pathlib import Path

APP = (Path(__file__).resolve().parents[1] / "src" / "App.jsx").read_text(encoding="utf-8-sig")


def _block(start_marker, end_marker):
    start = APP.index(start_marker)
    return APP[start:APP.index(end_marker, start)]


class LogoutFlowTests(unittest.TestCase):
    def setUp(self):
        self.confirm = _block("const confirmLogout = ", "const fetchSvList = ")
        self.revoke = _block("const revokeServerSessionInBackground = ", "const confirmLogout = ")

    def test_screen_does_not_wait_for_the_network(self):
        self.assertNotIn("await", self.confirm)
        self.assertNotRegex(self.confirm, r"const confirmLogout = async")

    def test_request_leaves_before_tokens_are_cleared(self):
        # Иначе запрос уйдёт без токена и сервер не узнает, какую сессию закрыть.
        self.assertLess(self.confirm.index("revokeServerSessionInBackground()"),
                        self.confirm.index("clearAuthTokens()"))
        self.assertLess(self.revoke.index("withAccessTokenHeader("), self.revoke.index("fetch("))

    def test_revoke_bypasses_the_axios_interceptor(self):
        # Перехватчик axios асинхронный и перечитывает токен из уже пустого хранилища.
        self.assertNotIn("axios", self.revoke)

    def test_request_survives_the_page(self):
        self.assertIn("keepalive: true", self.revoke)

    def test_revoke_is_never_aborted(self):
        # Экран запрос не ждёт, поэтому обрыв ничего не ускоряет — он только
        # отменяет отзыв, который ещё мог дойти, и сессия остаётся живой.
        self.assertNotIn("abort", self.revoke.lower())
        self.assertNotIn("signal:", self.revoke)

    def test_logout_endpoint_has_a_single_caller(self):
        # Второй путь выхода со старым ожиданием вернул бы дефект в обход правки.
        self.assertEqual(APP.count("/api/logout`"), 1)


if __name__ == "__main__":
    unittest.main()
