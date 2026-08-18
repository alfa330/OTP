"""Расчёт итогового правила для сотрудника: общий порог, персональный, выключение."""

from oktell_guard import queries


def settings(**kwargs):
    base = {
        'enabled': True, 'dry_run': False, 'oktell_url': 'https://oktell.example.local/',
        'cert_spki': 'ОТПЕЧАТОК', 'threshold_s': 180, 'warn_before_s': 30,
        'recall_reason_id': 2, 'call_state_strings': ['talk'], 'heartbeat_interval_s': 60,
    }
    base.update(kwargs)
    return base


def test_personal_threshold_wins():
    rule = queries.effective_rule(settings(threshold_s=180), {'threshold_s': 300})
    assert rule['threshold_s'] == 300


def test_empty_personal_means_common():
    """NULL в персональном пороге — это «как у всех», а не ноль."""
    assert queries.effective_rule(settings(threshold_s=240), {'threshold_s': None})['threshold_s'] == 240
    assert queries.effective_rule(settings(threshold_s=240), None)['threshold_s'] == 240


def test_disabled_globally_disables_everyone():
    rule = queries.effective_rule(settings(enabled=False), {'threshold_s': 120, 'enabled': True})
    assert rule['enabled'] is False


def test_disabled_personally():
    rule = queries.effective_rule(settings(enabled=True), {'enabled': False})
    assert rule['enabled'] is False


def test_threshold_is_clamped():
    """Порог 5 секунд — это дёрганье людей, 10 часов — выключенный ограничитель."""
    assert queries.effective_rule(settings(), {'threshold_s': 5})['threshold_s'] == 30
    assert queries.effective_rule(settings(), {'threshold_s': 99999})['threshold_s'] == 3600
    assert queries.effective_rule(settings(), {'threshold_s': 'мусор'})['threshold_s'] == 180


def test_agent_payload_carries_cert_pin():
    payload = queries.agent_config_payload(settings(cert_spki='ABC='), None)
    assert payload['browser']['extra_args'] == ['--ignore-certificate-errors-spki-list=ABC=']
    assert payload['oktell_url'] == 'https://oktell.example.local/'
    assert payload['in_window_rule']['threshold_s'] == 180


def test_agent_payload_without_pin_has_no_flag():
    payload = queries.agent_config_payload(settings(cert_spki=''), None)
    assert payload['browser']['extra_args'] == []


def test_message_matches_threshold():
    rule = queries.effective_rule(settings(threshold_s=300), None)
    assert '5 мин' in rule['message']


def test_clamp_helper():
    assert queries.clamp_threshold(None, default=90) == 90
    assert queries.clamp_threshold('180') == 180


def test_server_does_not_force_the_window_open():
    """Закрыл окно — значит закрыл. Возвращать его силой означает спорить с
    человеком; открыть заново он может ярлыком."""
    payload = queries.agent_config_payload(settings(), None)
    assert payload['browser']['keep_open'] is False
    assert payload['browser']['launch_on_start'] is False
