import pytest
from flask import Blueprint, Flask, jsonify
from wazzup import assist


def fixture(generate=lambda action, text: text, denied=False):
    assist._requests.clear()
    app = Flask(__name__)
    bp = Blueprint('draft_assist', __name__)
    def actor():
        return (None, (jsonify(error='denied'), 403)) if denied else ([42], None)
    assist.register_assist_routes(bp, actor, lambda fn: fn, lambda: ('', 204), generate=generate)
    app.register_blueprint(bp)
    return app.test_client()


def body(**updates):
    return dict(account='op', action='rewrite', text='Пришлите документ', **updates)


def test_auth_and_validation_before_provider():
    def never(*args):
        raise AssertionError('provider must not run')
    assert fixture(never, denied=True).post('/assist', json=body()).status_code == 403
    client=fixture(never)
    for data in (None, [], dict(account='potok'), dict(account='op', action='bad', text='text'),
                 dict(account='op', action='rewrite', text='@template: hidden'),
                 dict(account='op', action=[], text='text'), dict(account='op',action={},text='text'),
                 dict(account='op', action='rewrite', text='a'*4097)):
        assert client.post('/assist', json=data).status_code in (400,403)


def test_supported_actions_return_drafts_only_and_limit_frequency():
    calls=[]
    client=fixture(lambda action,text: calls.append(action) or text)
    for action in ('ru','kk','rewrite')*4:
        result=client.post('/assist', json={'account':'op','action':action,'text':'Пример 2500'})
        assert result.status_code == 200 and result.json['text'] == 'Пример 2500'
    assert client.post('/assist', json=body()).status_code == 429
    assert len(calls)==12


def test_preserves_sensitive_facts_and_does_not_expose_provider_errors():
    client=fixture(lambda *args:'Заплатите 5000')
    assert client.post('/assist', json=dict(account='op',action='rewrite',text='Заплатите 2500')).status_code==422
    def fail(*args):
        raise RuntimeError('private-provider-payload')
    result=fixture(fail).post('/assist', json=body())
    assert result.status_code==503
    assert 'private' not in result.text
    assert fixture().post('/assist',json=body()).status_code==200  # semaphore released


@pytest.mark.parametrize('response', ['Заплатите 25000', 'Заплатите 2500, потом 2500', 'Заплатите 500'])
def test_exact_numbers_are_preserved(response):
    client=fixture(lambda *args: response)
    assert client.post('/assist',json=dict(account='op',action='rewrite',text='Заплатите 2500')).status_code==422


def test_concurrent_capacity_rejects_without_waiting():
    assist._slots.acquire(); assist._slots.acquire()
    try:
        assert fixture().post('/assist', json=body()).status_code==429
    finally:
        assist._slots.release(); assist._slots.release()


@pytest.mark.parametrize(('action', 'draft'), [
    ('kk', 'Пришлите, пожалуйста, документы. Мы проверим их и сообщим результат.'),
    ('rewrite', 'кужаттарды жиберши, тексерип жауап беремиз'),
])
def test_kazakh_translation_and_rewrite_send_the_same_service_style_to_provider(monkeypatch, action, draft):
    from wiki.ai import providers
    calls = []
    expected = 'Құжаттарды жіберуіңізді сұраймыз. Тексеріп, жауап береміз.'

    def generate(model, system, text, **limits):
        calls.append((model, system, text, limits))
        return {'text': expected, 'finish': 'STOP'}

    monkeypatch.setattr(providers, '_call_vertex', generate)
    monkeypatch.setenv('WAZZUP_DRAFT_AI_MODEL', 'fixture-model')
    assert assist.transform(action, draft) == expected
    model, prompt, actual_draft, limits = calls[0]
    assert model == 'fixture-model' and actual_draft == draft
    assert limits == {'max_tokens': 2400, 'timeout': 12}
    # Verify the instructions actually sent through the production provider
    # boundary. A model stub does not establish translation quality.
    assert assist._KAZAKH_STYLE in prompt
    assert 'умеренно официальный, спокойный и уважительный деловой тон' in prompt
    assert 'Всегда обращайся к клиенту на «Сіз»' in prompt
    assert 'Құжаттарды жіберуіңізді сұраймыз' in prompt
    assert 'Слово «өтініш» в значении заявления' in prompt
    assert 'редкие, архаичные' in prompt
    assert 'Имена, названия сервисов, номера, суммы, даты, телефоны, ссылки' in prompt
    assert 'Не добавляй приветствия, извинения, обещания' in prompt
    if action == 'rewrite':
        assert 'Не переводи казахское сообщение на русский' in prompt
        assert 'без казахских букв' in prompt
        assert 'применяй только к казахскому исходнику' in prompt
        assert 'Русское сообщение перефразируй на русском по общим правилам' in prompt


def test_russian_translation_keeps_its_existing_instructions(monkeypatch):
    from wiki.ai import providers
    calls = []

    def generate(model, system, text, **limits):
        calls.append((system, text))
        return {'text': 'Пришлите документы', 'finish': 'STOP'}

    monkeypatch.setattr(providers, '_call_vertex', generate)
    assert assist.transform('ru', 'Құжаттарды жіберуіңізді сұраймыз') == 'Пришлите документы'
    assert calls == [(assist._SYSTEM + '\nПереведи сообщение на русский язык.',
                      'Құжаттарды жіберуіңізді сұраймыз')]
    assert assist._KAZAKH_STYLE not in calls[0][0]


@pytest.mark.parametrize('action', ['kk', 'rewrite'])
def test_kazakh_drafts_keep_data_protection_for_dates_amounts_contacts_and_links(action):
    draft = ('Айдос, 12.10.2026 күні 2500 теңге төлеуіңізді сұраймыз. '
             'https://example.invalid/pay/25 support@example.invalid +7 777 123 45 67')
    unchanged = fixture(lambda *_: draft).post('/assist', json={
        'account': 'op', 'action': action, 'text': draft})
    assert unchanged.status_code == 200
    assert unchanged.json['text'] == draft
    for old, new in [('12.10.2026', '13.10.2026'), ('2500', '5000'),
                     ('/pay/25', '/pay/26'), ('support@', 'another@'), ('123 45 67', '123 45 68')]:
        response = fixture(lambda *_, changed=draft.replace(old, new): changed).post('/assist', json={
            'account': 'op', 'action': action, 'text': draft})
        assert response.status_code == 422
        assert 'text' not in response.json
