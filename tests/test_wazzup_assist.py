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
