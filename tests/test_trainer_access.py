"""Гейт раздела «Тренажёр»: только супер-админ, и проверка стоит на сервере.

Раздел тестовый, но раздаёт браузеру ключи к платным внешним сервисам, поэтому
цена ошибки в правах здесь не «увидел лишний экран», а «потратил чужую квоту».
Спрятанный пункт меню доступом не является — раздел открывается прямым адресом,
и отвечать «нет» обязан сервер.
"""

import contextlib
import unittest


class Cursor:
    """Курсор-заглушка: отдаёт заранее заданного пользователя, запросы копит."""

    def __init__(self, user_row):
        self.user_row = user_row
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append(' '.join(str(sql).split()))

    def fetchone(self):
        return self.user_row

    def fetchall(self):
        return []


class Db:
    def __init__(self, user_row):
        self.cursor = Cursor(user_row)

    def _get_cursor(self):
        @contextlib.contextmanager
        def cm():
            yield self.cursor
        return cm()


def client_for(role, *, requester_id=7):
    from flask import Flask
    from voice_trainer.routes import build_trainer_blueprint

    app = Flask(__name__)
    app.register_blueprint(build_trainer_blueprint(
        db=Db((requester_id, 'Тест', role)),
        require_api_key=lambda f: f,
        build_cors_preflight_response=lambda: ('', 204),
        resolve_requester=lambda: (requester_id, None, None),
        # Та же нормализация, что в монолите: 'superadmin' и 'super admin' —
        # это тот же super_admin.
        is_super_admin_role=lambda value: str(value or '').strip().lower().replace(
            '-', '_').replace(' ', '_') == 'super_admin',
        env=lambda key, default=None: {'SONIOX_API_KEY': 'x',
                                       'GEMINI_API_KEY': 'y'}.get(key, default),
    ))
    return app.test_client()


class AccessTest(unittest.TestCase):
    def test_operator_gets_403_on_every_entry_point(self):
        client = client_for('operator')
        for method, path in (('get', '/api/trainer/ping'),
                             ('get', '/api/trainer/scenarios'),
                             ('get', '/api/trainer/sessions'),
                             ('post', '/api/trainer/tokens')):
            response = getattr(client, method)(path)
            self.assertEqual(403, response.status_code, f'{method} {path}')
            self.assertEqual('TRAINER_FORBIDDEN', response.get_json().get('code'))

    def test_admin_is_not_enough(self):
        """Обычный админ раздел не открывает: он тратит платные квоты."""
        response = client_for('admin').get('/api/trainer/scenarios')
        self.assertEqual(403, response.status_code)

    def test_super_admin_passes(self):
        response = client_for('super_admin').get('/api/trainer/scenarios')
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.get_json()['scenarios'])

    def test_role_is_delegated_verbatim_to_the_shared_check(self):
        """Раздел НЕ трактует роль сам, а отдаёт её проверке из монолита.

        Своя копия нормализации разошлась бы с общей молча: в проекте
        'superadmin', 'super-admin' и 'super admin' — это тот же super_admin, и
        знать об этом обязана одна функция, а не каждый раздел.
        """
        from flask import Flask
        from voice_trainer.routes import build_trainer_blueprint

        seen = []

        def spy(role):
            seen.append(role)
            return True

        app = Flask(__name__)
        app.register_blueprint(build_trainer_blueprint(
            db=Db((7, 'Тест', 'Super Admin')),
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (7, None, None),
            is_super_admin_role=spy,
            env=lambda key, default=None: default,
        ))
        response = app.test_client().get('/api/trainer/scenarios')
        self.assertEqual(200, response.status_code)
        self.assertEqual(['Super Admin'], seen)

    def test_auth_error_is_not_turned_into_403(self):
        """Ошибку авторизации нельзя подменять отказом в правах: иначе
        протухший токен выглядит как «раздел вам не положен»."""
        from flask import Flask
        from voice_trainer.routes import build_trainer_blueprint

        app = Flask(__name__)
        app.register_blueprint(build_trainer_blueprint(
            db=Db(None),
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (None, None, ('Unauthorized', 401)),
            is_super_admin_role=lambda value: True,
            env=lambda key, default=None: default,
        ))
        response = app.test_client().get('/api/trainer/scenarios')
        self.assertEqual(401, response.status_code)

    def test_unknown_user_is_404_not_500(self):
        from flask import Flask
        from voice_trainer.routes import build_trainer_blueprint

        app = Flask(__name__)
        app.register_blueprint(build_trainer_blueprint(
            db=Db(None),
            require_api_key=lambda f: f,
            build_cors_preflight_response=lambda: ('', 204),
            resolve_requester=lambda: (999, None, None),
            is_super_admin_role=lambda value: True,
            env=lambda key, default=None: default,
        ))
        response = app.test_client().get('/api/trainer/scenarios')
        self.assertEqual(404, response.status_code)


class PingTest(unittest.TestCase):
    def test_ping_reports_missing_keys_instead_of_failing_silently(self):
        """Раздел зависит от трёх внешних сервисов, и молчаливый отказ одного
        выглядит как «оно не работает». Пусть видно, чего именно нет."""
        response = client_for('super_admin').get('/api/trainer/ping')
        body = response.get_json()
        self.assertEqual(200, response.status_code)
        self.assertTrue(body['keys']['soniox'])
        self.assertTrue(body['keys']['gemini'])
        self.assertFalse(body['keys']['claude'])      # в env заглушки его нет
        self.assertEqual(['driver', 'mentor'], body['modes'])
        self.assertIn('stt_per_min', body['rates'])


class ScenarioTest(unittest.TestCase):
    def test_every_scenario_hides_its_cause(self):
        """Смысл тренажёра в том, что водитель НЕ помогает. Если сценарий
        выкладывает причину сразу, тренировать нечего."""
        from voice_trainer import scenarios

        for key, scenario in scenarios.SCENARIOS.items():
            self.assertIn('ТОЛЬКО если спрос', scenario['persona'],
                          f'сценарий {key} не прячет детали')
            self.assertTrue(scenario['expected'], f'у сценария {key} нет критериев разбора')

    def test_common_rules_forbid_helping(self):
        from voice_trainer import scenarios

        self.assertIn('НИКОГДА не помогаешь', scenarios.COMMON)



class VoiceTest(unittest.TestCase):
    """Голос собеседника — мужской у ВСЕХ персонажей.

    Пол голосов Gemini в документации не указан, поэтому набор отобран замером
    основного тона (22.08.2026). Проверка была не лишней: Fenrir при мужском
    имени дал 229 Гц, а прежний дефолт Kore — 167 Гц, то есть водитель говорил
    женским голосом.
    """

    def test_every_scenario_speaks_with_a_male_voice(self):
        from voice_trainer import scenarios

        for key, scenario in scenarios.SCENARIOS.items():
            voice = scenario.get('voice')
            self.assertIn(voice, scenarios.MALE_VOICES,
                          f'у сценария {key} голос {voice} не из мужского набора')

    def test_mentor_voice_is_male_too(self):
        from voice_trainer import scenarios

        self.assertIn(scenarios.MENTOR_VOICE, scenarios.MALE_VOICES)

    def test_male_set_stays_below_the_female_range(self):
        """Порог, а не список имён: если кто-то добавит голос с высоким тоном,
        тест обязан упасть, а не молча пропустить его в набор."""
        from voice_trainer import scenarios

        for voice, f0 in scenarios.MALE_VOICES.items():
            self.assertLess(f0, 160, f'{voice}: {f0} Гц — это не мужской диапазон')

    def test_scenarios_use_distinct_voices(self):
        """Персонажи должны различаться на слух, иначе сценарии сливаются."""
        from voice_trainer import scenarios

        voices = [s['voice'] for s in scenarios.SCENARIOS.values()]
        self.assertEqual(len(voices), len(set(voices)))


if __name__ == '__main__':
    unittest.main()
