"""Public endpoint coverage and schema-shape regressions."""
import json
from pathlib import Path
import re

from whc import create_app
from whc.contract import specification
from whc.models import db
from whc.resources import RESOURCE_KINDS


METHODS = {'get', 'post', 'patch', 'put', 'delete', 'head', 'options', 'trace'}


def walk(value):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def test_openapi31_references_and_path_parameters_resolve():
    spec = specification()
    assert spec['openapi'] == '3.1.0'
    assert spec['servers'] == [{'url': '/api/v1'}]
    for value in walk(spec):
        if not isinstance(value, dict):
            continue
        if '$ref' in value:
            assert value['$ref'].startswith('#/')
            resolved = spec
            for segment in value['$ref'][2:].split('/'):
                resolved = resolved[segment.replace('~1', '/').replace('~0', '~')]
            assert isinstance(resolved, dict)
        if value.get('type') == 'object':
            assert set(value.get('required', [])) <= set(value.get('properties', {}))
    for route, item in spec['paths'].items():
        expected = set(re.findall(r'\{([^}]+)\}', route))
        for method, operation in item.items():
            if method not in METHODS:
                continue
            parameters = item.get('parameters', []) + operation.get('parameters', [])
            actual = {parameter['name'] for parameter in parameters if parameter['in'] == 'path' and parameter.get('required')}
            assert actual == expected, (route, method)
            assert operation['responses']
            assert all(re.fullmatch(r'[1-5][0-9]{2}|default', code) for code in operation['responses'])


def test_contract_covers_every_public_flask_operation(tmp_path):
    app = create_app({'TESTING': True, 'COOKIE_SECURE': False, 'DATA_DIR': str(tmp_path),
                      'UPLOAD_DIR': str(tmp_path / 'uploads'), 'BACKUP_DIR': str(tmp_path / 'backups'),
                      'DATABASE_URL': 'sqlite:///:memory:'})
    documented = {(path, method) for path, operations in specification()['paths'].items()
                  for method in operations if method in METHODS}
    actual = set()
    for route in app.url_map.iter_rules():
        if route.rule == '/api/health':
            paths = ['/health']
        elif route.rule.startswith('/api/v1/'):
            path = route.rule[len('/api/v1'):]
            paths = [path.replace('<resource>', resource) for resource in RESOURCE_KINDS] if '<resource>' in path else [path]
            paths = [re.sub(r'<[^>]+>', '{id}', value) for value in paths]
        else:
            continue
        actual.update((path, method.lower()) for path in paths for method in route.methods - {'OPTIONS', 'HEAD'})
    assert actual == documented, {'missing': actual - documented, 'nonexistent': documented - actual}
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def test_administration_and_auth_schemas_match_nonrevisioned_users():
    spec = specification()
    schemas, paths = spec['components']['schemas'], spec['paths']
    for name in ('User', 'UserInput', 'UserPatch', 'SettingsPatch'):
        assert 'revision' not in schemas[name]['properties']
    assert paths['/users']['post']['requestBody']['content']['application/json']['schema']['$ref'].endswith('/UserInput')
    assert '201' in paths['/users']['post']['responses']
    assert 'requestBody' not in paths['/users/{id}']['delete']
    assert paths['/auth/session']['get']['security'] == []
    assert paths['/auth/setup']['post']['security'] == []
    assert '201' in paths['/auth/setup']['post']['responses']
    assert '200' in paths['/auth/login']['post']['responses']
    assert 'setup_required' not in schemas['AuthResult']['required']
    assert 'minLength' not in schemas['LoginCredentials']['properties']['password']
    assert schemas['Credentials']['properties']['password']['minLength'] == 12
    for method in ('patch', 'delete'):
        assert any(p['name'] == 'X-CSRF-Token' and p['required'] for p in paths['/users/{id}'][method]['parameters'])


def test_files_currency_definitions_and_action_payloads_are_accurate():
    spec = specification()
    schemas, paths = spec['components']['schemas'], spec['paths']
    currency_item = paths['/currencies']['get']['responses']['200']['content']['application/json']['schema']['properties']['items']['items']
    assert currency_item['$ref'].endswith('/CurrencyDefinition')
    assert 'revision' not in schemas['CurrencyDefinition']['required']
    assert schemas['CurrencyDefinition']['properties']['id'] == {'type': 'string', 'description': 'Built-in three-letter code or custom-record UUID.'}
    assert schemas['AccountInput']['required'] == ['name', 'type']
    assert '201' in paths['/attachments']['post']['responses']
    multipart = paths['/attachments']['post']['requestBody']['content']['multipart/form-data']['schema']
    assert multipart['required'] == ['file', 'transaction_id']
    assert 'application/pdf' in paths['/attachments/{id}']['get']['responses']['200']['content']
    assert paths['/accounts/{id}/reconcile']['post']['requestBody']['content']['application/json']['schema']['$ref'].endswith('/ReconciliationInput')
    assert schemas['ReconciliationInput']['required'] == ['balance', 'date', 'revision']
    assert paths['/health']['servers'] == [{'url': '/api'}]


def test_committed_openapi_artifact_matches_source():
    root = Path(__file__).resolve().parents[2]
    assert json.loads((root / 'openapi.json').read_text(encoding='utf-8')) == specification()
