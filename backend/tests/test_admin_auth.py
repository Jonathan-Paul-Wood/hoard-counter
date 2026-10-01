import pytest

from whc import create_app
from whc.models import db, Record

PASSWORD = 'correct horse battery staple'


@pytest.fixture
def household(tmp_path):
    app = create_app({'TESTING': True, 'COOKIE_SECURE': False, 'DATABASE_URL': 'sqlite:///:memory:',
                      'DATA_DIR': str(tmp_path), 'UPLOAD_DIR': str(tmp_path / 'uploads'),
                      'BACKUP_DIR': str(tmp_path / 'backups')})
    admin = app.test_client()
    setup = admin.post('/api/v1/auth/setup', json={'username': 'owner', 'password': PASSWORD})
    assert setup.status_code == 201
    admin_id = setup.json['user']['id']
    admin_headers = {'X-CSRF-Token': setup.json['csrf_token']}
    created = admin.post('/api/v1/users', json={'username': 'member', 'password': PASSWORD, 'role': 'member'}, headers=admin_headers)
    assert created.status_code == 201
    member_id = created.json['id']
    member = app.test_client()
    logged_in = member.post('/api/v1/auth/login', json={'username': 'member', 'password': PASSWORD})
    assert logged_in.status_code == 200
    member_headers = {'X-CSRF-Token': logged_in.json['csrf_token']}
    yield app, admin, admin_headers, admin_id, member, member_headers, member_id
    with app.app_context():
        db.session.remove()
        db.drop_all()
        db.engine.dispose()


def test_member_forbidden_admin_routes_but_can_use_household(household):
    app, admin, ah, admin_id, member, mh, member_id = household
    assert member.get('/api/v1/users').status_code == 403
    assert member.get('/api/v1/backups/status').status_code == 403
    cases = [
        ('POST', '/api/v1/users', {'username': 'forbidden', 'password': PASSWORD, 'role': 'admin'}),
        ('PATCH', '/api/v1/users/' + member_id, {'role': 'admin'}),
        ('DELETE', '/api/v1/users/' + admin_id, {}),
        ('PATCH', '/api/v1/settings', {'household_name': 'Hijacked'}),
        ('POST', '/api/v1/currencies', {'code': 'PLN', 'name': 'Zloty', 'precision': 2}),
    ]
    for method, path, data in cases:
        assert member.open(path, method=method, json=data, headers=mh).status_code == 403
    assert member.get('/api/v1/settings').status_code == 200
    assert member.get('/api/v1/accounts').status_code == 200
    assert member.post('/api/v1/categories', json={'name': 'Shared food'}, headers=mh).status_code == 201


def test_deactivation_revokes_existing_session_and_prevents_login(household):
    app, admin, ah, admin_id, member, mh, member_id = household
    assert member.get('/api/v1/accounts').status_code == 200
    response = admin.delete('/api/v1/users/' + member_id, json={}, headers=ah)
    assert response.status_code == 200 and response.json['active'] is False
    assert member.get('/api/v1/accounts').status_code == 401
    assert member.get('/api/v1/auth/session').json['user'] is None
    assert member.post('/api/v1/auth/login', json={'username': 'member', 'password': PASSWORD}).status_code == 401


def test_admin_cannot_remove_own_access(household):
    app, admin, ah, admin_id, member, mh, member_id = household
    for payload in ({'role': 'member'}, {'active': False}, {'role': 'member', 'active': False}):
        response = admin.patch('/api/v1/users/' + admin_id, json=payload, headers=ah)
        assert response.status_code == 400
    assert admin.delete('/api/v1/users/' + admin_id, json={}, headers=ah).status_code == 400
    assert admin.get('/api/v1/auth/session').json['user']['role'] == 'admin'
    assert admin.get('/api/v1/users').status_code == 200


def test_role_change_takes_effect_on_existing_session(household):
    app, admin, ah, admin_id, member, mh, member_id = household
    assert admin.patch('/api/v1/users/' + member_id, json={'role': 'admin'}, headers=ah).status_code == 200
    assert member.get('/api/v1/users').status_code == 200
    assert admin.patch('/api/v1/users/' + member_id, json={'role': 'member'}, headers=ah).status_code == 200
    assert member.get('/api/v1/users').status_code == 403


def test_invalid_active_flags_rejected_and_disabled_creation_honored(household):
    app, admin, ah, admin_id, member, mh, member_id = household
    for invalid in ('false', 'true', 0, 1, None, [], {}):
        assert admin.patch('/api/v1/users/' + member_id, json={'active': invalid}, headers=ah).status_code == 400
        assert admin.post('/api/v1/users', json={'username': 'new-member', 'password': PASSWORD, 'active': invalid}, headers=ah).status_code == 400
    created = admin.post('/api/v1/users', json={'username': 'disabled', 'password': PASSWORD, 'active': False}, headers=ah)
    assert created.status_code == 201 and created.json['active'] is False
    fresh = app.test_client()
    assert fresh.post('/api/v1/auth/login', json={'username': 'disabled', 'password': PASSWORD}).status_code == 401


def test_settings_types_runtime_version_and_timezone(household, monkeypatch):
    app, admin, ah, admin_id, member, mh, member_id = household
    invalid_settings = [[], True, {'household_name': None}, {'household_name': []},
                        {'household_name': '  '}, {'household_name': 'x' * 101},
                        {'timezone': True}, {'timezone': []}, {'timezone': 'Not/AZone'},
                        {'timezone': '../UTC'}, {'version': 'fake'}, {'default_currency': 'EUR'}]
    for payload in invalid_settings:
        response = admin.patch('/api/v1/settings', json=payload, headers=ah)
        assert response.status_code == 400, (payload, response.json)
    with app.app_context():
        db.session.add(Record(kind='setting', data={'household_name': 'Stored household', 'timezone': 'UTC',
                                                   'version': 'stored-old', 'default_currency': 'EUR'}))
        db.session.commit()
    monkeypatch.setenv('APP_VERSION', 'release-test-2')
    settings = admin.get('/api/v1/settings').json
    assert settings['version'] == 'release-test-2'
    assert settings['default_currency'] == 'USD'
    saved = admin.patch('/api/v1/settings', json={'household_name': '  New household  ', 'timezone': 'Europe/London'}, headers=ah)
    assert saved.status_code == 200
    assert saved.json['household_name'] == 'New household'
    assert saved.json['version'] == 'release-test-2'
    assert member.get('/api/v1/meta').json['timezone'] == 'Europe/London'
    monkeypatch.setenv('APP_VERSION', 'release-test-3')
    assert admin.get('/api/v1/settings').json['version'] == 'release-test-3'


def test_malformed_auth_payloads_are_controlled_errors(household):
    app, admin, ah, admin_id, member, mh, member_id = household
    fresh = app.test_client()
    for payload in ([{'username': 'member'}], True, 'string', 42):
        assert fresh.post('/api/v1/auth/login', json=payload).status_code == 400
    assert fresh.post('/api/v1/auth/setup', json={'username': 'another', 'password': PASSWORD}).status_code == 409
    assert admin.get('/api/v1/users').status_code == 200
    audit = admin.get('/api/v1/audit?kind=user').json['items']
    for event in audit:
        for state in (event.get('before'), event.get('after')):
            assert not state or ('password' not in state and 'password_hash' not in state)
