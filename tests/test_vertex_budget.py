"""Дневной лимит Vertex AI (vertex_budget): тарифы, день счёта Google, сверка со счётом и
решения сторожа. Без сети: Google подменён."""
from datetime import datetime, timedelta, timezone

import pytest

from vertex_budget import guard, prices

UTC = timezone.utc
OWN = 'flask-gcs-uploader@analog-delight-464908-g8.iam.gserviceaccount.com'

# Токены за дни счёта Google (сутки по PT) из Cloud Monitoring — дни, когда вторая машина
# проверяла ударения в роликах моделью gemini-3.1-pro-preview. Счёт в консоли: Vertex AI плюс
# Cloud Text-to-Speech — туда Google относит Gemini TTS, вызванный через Vertex.
TOKENS_18_11 = {
    'gemini-2.5-flash|input': 59905, 'gemini-2.5-flash|output': 71657,
    'gemini-2.5-pro|input': 147980, 'gemini-2.5-pro|output': 308603,
    'gemini-3-flash-preview|input': 926896, 'gemini-3-flash-preview|output': 36293,
    'gemini-3.1-flash-tts-preview|input': 12024, 'gemini-3.1-flash-tts-preview|output': 28743,
    'gemini-3.1-pro-preview|input': 220974, 'gemini-3.1-pro-preview|output': 4186023,
    'gemini-3.8-flash|input': 62799, 'gemini-3.8-flash|output': 23274,
    'text-multilingual-embedding-002|input': 214617, 'text-multilingual-embedding-002|output': 0,
}
TOKENS_19_11 = {
    'gemini-2.5-flash|input': 35474, 'gemini-2.5-flash|output': 7950,
    'gemini-2.5-pro|input': 30507, 'gemini-2.5-pro|output': 62355,
    'gemini-3-flash-preview|input': 799835, 'gemini-3-flash-preview|output': 34467,
    'gemini-3.1-flash-tts-preview|input': 1730, 'gemini-3.1-flash-tts-preview|output': 2573,
    'gemini-3.1-pro-preview|input': 62697, 'gemini-3.1-pro-preview|output': 2574497,
    'gemini-3.8-flash|input': 67875, 'gemini-3.8-flash|output': 28460,
    'text-multilingual-embedding-002|input': 178892, 'text-multilingual-embedding-002|output': 0,
}


def _tokens(flat):
    return {tuple(key.split('|')): value for key, value in flat.items()}


def _pro_output(dollars):
    """Столько выходных токенов gemini-3.1-pro-preview стоят dollars."""
    return {('gemini-3.1-pro-preview', 'output'): int(dollars / 12.0 * 1_000_000)}


@pytest.mark.parametrize('flat, billed', [
    (TOKENS_18_11, 54.90 + 0.59),   # Vertex AI + Cloud TTS, 18.11.2026
    (TOKENS_19_11, 32.36 + 0.05),   # 19.11.2026
])
def test_day_cost_matches_google_bill(flat, billed):
    spent, per_model, unknown = guard.day_cost(_tokens(flat))
    assert not unknown
    assert abs(spent - billed) / billed < 0.01
    assert max(per_model, key=per_model.get) == 'gemini-3.1-pro-preview'


def test_prices_by_model_name():
    assert prices.price_for('gemini-3.1-pro-preview') == ((2.0, 12.0), True)
    assert prices.price_for('gemini-2.5-pro') == ((1.25, 10.0), True)
    assert prices.price_for('gemini-3-flash-preview') == ((0.5, 3.0), True)
    assert prices.price_for('gemini-3.8-flash') == ((1.5, 7.5), True)
    assert prices.price_for('gemini-3.5-flash-lite')[0] == (0.30, 2.50)
    assert prices.price_for('gemini-2.5-flash-lite')[0] == (0.10, 0.40)
    assert prices.price_for('gemini-2.5-flash')[0] == (0.30, 2.50)
    # Начинаются как Flash, а стоят иначе: голос, картинка, Live.
    assert prices.price_for('gemini-3.1-flash-tts-preview')[0] == (1.0, 20.0)
    assert prices.price_for('gemini-2.5-flash-image')[0] == (0.5, 60.0)
    assert prices.price_for('gemini-3-pro-image-preview')[0] == (2.0, 120.0)
    assert prices.price_for('gemini-3.1-flash-live-preview')[0] == (3.0, 12.0)
    assert prices.price_for('text-multilingual-embedding-002')[0] == (0.025, 0.0)


def test_unknown_model_counts_as_pro():
    assert prices.price_for('gemini-9-ultra') == (prices.DEFAULT, False)
    assert prices.cost_usd('gemini-9-ultra', 'output', 1_000_000) == prices.DEFAULT[1]
    assert guard.day_cost({('gemini-9-ultra', 'input'): 10})[2] == {'gemini-9-ultra'}


def test_kind_other_than_input_is_priced_as_output():
    assert prices.cost_usd('gemini-2.5-pro', 'cached_input', 1_000_000) == 10.0
    assert prices.cost_usd('gemini-2.5-pro', 'input', 1_000_000) == 1.25


def test_billing_day_follows_google_pacific_day():
    # 10:13 по Алматы 23.11 — у Google ещё 22.11 (начался в 13:00 по Алматы).
    start, next_start = guard.billing_day(datetime(2026, 11, 23, 5, 13, tzinfo=UTC))
    assert start == datetime(2026, 11, 22, 8, tzinfo=UTC)
    assert next_start == datetime(2026, 11, 23, 8, tzinfo=UTC)
    # Летом у Калифорнии UTC−7: сброс в 12:00 по Алматы.
    start, next_start = guard.billing_day(datetime(2026, 7, 1, 12, tzinfo=UTC))
    assert (start, next_start) == (datetime(2026, 7, 1, 7, tzinfo=UTC), datetime(2026, 7, 2, 7, tzinfo=UTC))


def test_billing_day_on_clock_change_lasts_25_hours():
    start, next_start = guard.billing_day(datetime(2026, 11, 1, 12, tzinfo=UTC))
    assert start == datetime(2026, 11, 1, 7, tzinfo=UTC)
    assert next_start - start == timedelta(hours=25)


def test_limit_and_switch_from_env():
    assert guard.limit_usd({}) == 5.0
    assert guard.limit_usd({'VERTEX_DAILY_LIMIT_USD': '7,5'}) == 7.5
    assert guard.limit_usd({'VERTEX_DAILY_LIMIT_USD': 'много'}) == 5.0
    assert guard.limit_usd({'VERTEX_DAILY_LIMIT_USD': '0'}) == 5.0
    assert guard.is_enabled({})
    assert not guard.is_enabled({'VERTEX_BUDGET_GUARD_ENABLED': 'false'})


# ── Решения сторожа ─────────────────────────────────────────────────────────────────────────
# 12:30 по Алматы 19.11 — день счёта Google начался 18.11 в 13:00 по Алматы.
NOW = datetime(2026, 11, 19, 7, 30, tzinfo=UTC)
DAY_START = datetime(2026, 11, 18, 8, tzinfo=UTC)


class FakeCloud:
    email = OWN

    def __init__(self, totals=None, recent=None, state='ENABLED', enabled=None, disabled=None,
                 fail_disable=None, fail_enable=None, fail_audit=None):
        self.totals, self.recent = totals or {}, recent or {}
        self.state = state
        self.toggles = {True: enabled, False: disabled}
        self.fail_disable, self.fail_enable, self.fail_audit = fail_disable, fail_enable, fail_audit
        self.switched = []
        self.window = None

    def usage(self, start, end):
        self.window = (start, end)
        return self.totals, self.recent

    def service_state(self):
        return self.state

    def set_service(self, enabled):
        failure = self.fail_enable if enabled else self.fail_disable
        if failure:
            raise guard.GuardError(failure)
        self.switched.append(enabled)
        self.state = 'ENABLED' if enabled else 'DISABLED'

    def last_toggle(self, enabled, since):
        if self.fail_audit:
            raise guard.GuardError(self.fail_audit)
        entry = self.toggles[enabled]
        return entry if entry and entry[1] >= since else None


@pytest.fixture(autouse=True)
def _fresh_guard_state(monkeypatch):
    monkeypatch.setattr(guard, '_override_logged_day', None)
    monkeypatch.setattr(guard, '_failure_notified_day', None)
    monkeypatch.setattr(guard, '_enable_failure_day', None)
    monkeypatch.setattr(guard, '_unknown_logged', set())


def _run(cloud, limit=5.0):
    sent = []
    result = guard.run_once(cloud, now=NOW, limit=limit, notify=sent.append)
    return result, sent


def test_under_limit_does_nothing():
    cloud = FakeCloud(totals=_pro_output(3.0))
    assert _run(cloud) == (None, [])
    assert cloud.switched == []
    assert cloud.window == (DAY_START, NOW)


def test_over_limit_switches_vertex_off_and_tells_owner():
    cloud = FakeCloud(totals=_pro_output(5.2))
    result, sent = _run(cloud)
    assert result == 'disabled' and cloud.switched == [False]
    assert len(sent) == 1
    text = sent[0]
    assert 'Vertex AI выключен: дневной лимит 5,00 $ исчерпан.' in text
    assert 'Потрачено за день ≈ 5,20 $' in text
    assert 'gemini-3.1-pro-preview — 5,20 $' in text
    assert 'Включится сам сегодня в 13:00 по Алматы' in text
    assert 'консоли Google Cloud' in text


def test_last_ten_minutes_count_as_margin_for_late_metrics():
    burst = _pro_output(2.6)
    cloud = FakeCloud(totals=_pro_output(2.6), recent=burst)
    result, sent = _run(cloud)
    assert result == 'disabled'
    assert 'из них за последние 10 минут ≈ 2,60 $' in sent[0]


def test_person_switched_vertex_on_today_means_limit_lifted():
    cloud = FakeCloud(totals=_pro_output(9.0), enabled=('owner@example.com', NOW - timedelta(hours=1)))
    assert _run(cloud) == ('override', [])
    assert cloud.switched == []


def test_script_switched_vertex_on_again_is_switched_off_again():
    cloud = FakeCloud(totals=_pro_output(9.0), enabled=(OWN, NOW - timedelta(minutes=5)),
                      disabled=(OWN, NOW - timedelta(minutes=40)))
    result, sent = _run(cloud)
    assert result == 'disabled' and cloud.switched == [False]
    assert sent[0].startswith('Vertex AI снова выключен: его включили ключом проекта')


def test_guard_morning_switch_on_is_not_reported_as_script():
    # Утром сторож сам включил Vertex (новый день), к обеду расход дошёл до лимита — обычный текст.
    cloud = FakeCloud(totals=_pro_output(9.0), enabled=(OWN, DAY_START + timedelta(minutes=1)),
                      disabled=(OWN, DAY_START - timedelta(hours=20)))
    result, sent = _run(cloud)
    assert result == 'disabled'
    assert sent[0].startswith('Vertex AI выключен: дневной лимит 5,00 $ исчерпан.')


def test_person_override_from_yesterday_does_not_lift_todays_limit():
    cloud = FakeCloud(totals=_pro_output(9.0), enabled=('owner@example.com', DAY_START - timedelta(hours=2)))
    assert _run(cloud)[0] == 'disabled'


def test_new_day_switches_vertex_back_on_when_guard_switched_it_off():
    cloud = FakeCloud(state='DISABLED', disabled=(OWN, DAY_START - timedelta(hours=3)))
    result, sent = _run(cloud)
    assert result == 'enabled' and cloud.switched == [True]
    assert sent == ['Vertex AI снова включён: у Google начался новый день, лимит 5,00 $ обнулился.']


def test_switched_off_today_stays_off_when_margin_drops():
    # Выключили на всплеске по запасу; всплеск ушёл из последних 10 минут — всё равно до сброса.
    cloud = FakeCloud(totals=_pro_output(3.0), state='DISABLED', disabled=(OWN, NOW - timedelta(minutes=20)))
    assert _run(cloud) == (None, [])
    assert cloud.switched == []


def test_vertex_switched_off_by_person_is_left_alone():
    cloud = FakeCloud(state='DISABLED', disabled=('owner@example.com', DAY_START - timedelta(days=2)))
    assert _run(cloud) == (None, [])
    assert cloud.switched == []


def test_vertex_switched_off_by_other_account_is_left_alone():
    other = 'otp-dev@analog-delight-464908-g8.iam.gserviceaccount.com'
    cloud = FakeCloud(state='DISABLED', disabled=(other, DAY_START - timedelta(days=1)))
    assert _run(cloud) == (None, [])


def test_failed_switch_off_is_reported_once_a_day():
    cloud = FakeCloud(totals=_pro_output(6.0), fail_disable='disable Vertex AI API: HTTP 403 denied')
    sent = []
    for _ in range(3):
        with pytest.raises(guard.GuardError):
            guard.run_once(cloud, now=NOW, limit=5.0, notify=sent.append)
    assert len(sent) == 1
    assert 'выключить Vertex AI API не вышло' in sent[0] and 'HTTP 403' in sent[0]


def test_unreadable_audit_log_does_not_stop_the_limit():
    cloud = FakeCloud(totals=_pro_output(6.0), fail_audit='журнал аудита: HTTP 503')
    result, sent = _run(cloud)
    assert result == 'disabled' and cloud.switched == [False]
    assert sent[0].startswith('Vertex AI выключен')


def test_failed_switch_on_in_new_day_is_reported_once_a_day():
    cloud = FakeCloud(state='DISABLED', disabled=(OWN, DAY_START - timedelta(hours=3)),
                      fail_enable='enable Vertex AI API: HTTP 500')
    sent = []
    for _ in range(3):
        with pytest.raises(guard.GuardError):
            guard.run_once(cloud, now=NOW, limit=5.0, notify=sent.append)
    assert len(sent) == 1 and 'включить Vertex AI обратно не вышло' in sent[0]


def test_notify_failure_does_not_break_the_pass():
    def broken(_text):
        raise RuntimeError('telegram down')

    cloud = FakeCloud(totals=_pro_output(6.0))
    assert guard.run_once(cloud, now=NOW, limit=5.0, notify=broken) == 'disabled'


# ── Разбор ответа Cloud Monitoring ──────────────────────────────────────────────────────────
class _Response:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status
        self.text, self.content = str(body), b'x'

    def json(self):
        return self._body


class _Session:
    def __init__(self, pages):
        self.pages, self.calls = list(pages), []

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or []))
        return _Response(self.pages.pop(0))


def _series(model, kind, points):
    return {'resource': {'labels': {'model_user_id': model}}, 'metric': {'labels': {'type': kind}},
            'points': [{'interval': {'endTime': end}, 'value': {'int64Value': str(value)}}
                       for end, value in points]}


def test_usage_sums_buckets_and_keeps_last_ten_minutes_apart():
    end = datetime(2026, 11, 19, 7, 30, 15, tzinfo=UTC)
    session = _Session([
        {'timeSeries': [_series('gemini-3.1-pro-preview', 'output',
                                [('2026-11-19T07:30:15Z', 700), ('2026-11-19T07:20:15Z', 300)])],
         'nextPageToken': 'p2'},
        {'timeSeries': [_series('gemini-3-flash-preview', 'input', [('2026-11-19T06:00:15Z', 50)])]},
    ])
    cloud = guard.GoogleCloud(session, 'analog-delight-464908-g8', OWN)
    totals, recent = cloud.usage(DAY_START, end)
    assert totals == {('gemini-3.1-pro-preview', 'output'): 1000, ('gemini-3-flash-preview', 'input'): 50}
    assert recent == {('gemini-3.1-pro-preview', 'output'): 700}
    first = session.calls[0]
    assert first['aggregation.alignmentPeriod'] == '600s'
    assert first['interval.startTime'] == '2026-11-18T08:00:00Z'
    assert session.calls[1]['pageToken'] == 'p2'


def test_usage_right_after_reset_is_empty():
    cloud = guard.GoogleCloud(_Session([]), 'p', OWN)
    assert cloud.usage(DAY_START, DAY_START + timedelta(seconds=30)) == ({}, {})


def test_google_refusal_becomes_guard_error():
    cloud = guard.GoogleCloud(_Session([{'error': 'denied'}]), 'p', OWN)
    cloud.session.get = lambda *a, **k: _Response({'error': 'denied'}, status=403)
    with pytest.raises(guard.GuardError, match='HTTP 403'):
        cloud.service_state()


def test_loop_stops_quietly_on_key_without_rights(monkeypatch, caplog):
    class NoRights(FakeCloud):
        def usage(self, start, end):
            raise guard.GuardError('метрики Vertex: HTTP 403 denied', status=403)

    monkeypatch.setattr(guard.time, 'sleep', lambda _seconds: None)
    with caplog.at_level('ERROR', logger='vertex_budget.guard'):
        guard._loop(NoRights(), None, interval=0, first_delay=0)   # вернулся — значит остановился
    assert sum('нет прав' in record.getMessage() for record in caplog.records) == 1


def test_loop_keeps_going_on_temporary_failure(monkeypatch):
    passes = []

    class Flaky(FakeCloud):
        def usage(self, start, end):
            passes.append(1)
            if len(passes) >= 3:
                raise SystemExit   # выход из бесконечного цикла для теста
            raise guard.GuardError('метрики Vertex: HTTP 503 unavailable', status=503)

    monkeypatch.setattr(guard.time, 'sleep', lambda _seconds: None)
    with pytest.raises(SystemExit):
        guard._loop(Flaky(), None, interval=0, first_delay=0)
    assert len(passes) == 3


def test_audit_time_with_nanoseconds_is_parsed():
    assert guard._parse_time('2026-11-12T05:59:00.123456789Z') == datetime(2026, 11, 12, 5, 59, 0, 123456, tzinfo=UTC)


def test_worker_does_not_start_without_key_or_when_switched_off(monkeypatch):
    monkeypatch.setattr(guard, '_worker', None)
    assert guard.start_worker(cloud_factory=lambda: None) is None
    monkeypatch.setenv('VERTEX_BUDGET_GUARD_ENABLED', 'false')
    assert guard.start_worker(cloud_factory=lambda: FakeCloud()) is None
