import io
import json
from urllib.error import HTTPError

import pytest

from whc import statement_import as importer
from whc.validation import ApiError
from test_admin_auth import household


@pytest.mark.parametrize('status,code,debug', [
    (413, 'page_limit', {'stage': 'document_read', 'document': 2, 'limit': 'pages', 'actual': 31, 'maximum': 30}),
    (502, 'provider_error', {'stage': 'model_request', 'provider_http_status': 401, 'hint': 'Check LLM_API_KEY.'}),
])
def test_processor_details_survive_adapter(monkeypatch, status, code, debug):
    payload = {'error': {'code': code, 'message': 'Processor diagnosis.', 'details': debug}}
    error = HTTPError('http://processor/v1/extract', status, 'failure', {'X-Request-ID': 'ocr-request-123'},
                      io.BytesIO(json.dumps(payload).encode()))

    class Opener:
        def open(self, *args, **kwargs):
            raise error

    monkeypatch.setattr(importer, 'build_opener', lambda handler: Opener())
    with pytest.raises(ApiError) as raised:
        importer.processor_call({'ocr_url': 'http://processor', 'ocr_token': 'secret-token'}, '/v1/extract', b'x')
    assert raised.value.code == 'ocr_service_error'
    assert raised.value.details == {'processor_http_status': status, 'processor_request_id': 'ocr-request-123',
                                   'processor_error': payload['error']}
    if status == 413:
        assert raised.value.message == 'OCR Processor document limits were exceeded.'


@pytest.mark.parametrize('body,reason', [(b'', 'empty'), (b'<html>secret-token</html>', 'unreadable_or_non_json'),
                                      (b'x' * (64 * 1024 + 1), 'too_large'), (b'[]', 'unrecognized_envelope')],
                         ids=['empty', 'html', 'oversized', 'unrecognized'])
def test_non_json_and_large_errors_have_safe_fallback(body, reason):
    error = HTTPError('secret-url', 502, 'secret-token', {}, io.BytesIO(body))
    details = importer.processor_error_details(error, {'ocr_token': 'secret-token'})
    assert details == {'processor_http_status': 502, 'error_response': reason}


def test_processor_diagnostics_redact_token_and_bound_values():
    payload = {'error': {'code': 'failure', 'message': 'secret-token',
                         'details': {'nested': ['secret-token'], 'long': 'x' * 3000}}, 'raw_text': 'private statement'}
    error = HTTPError('secret-url', 502, 'secret-token', {'X-Request-ID': 'secret-token'},
                      io.BytesIO(json.dumps(payload).encode()))
    details = importer.processor_error_details(error, {'ocr_token': 'secret-token'})
    assert 'secret-token' not in json.dumps(details)
    assert 'private statement' not in json.dumps(details)
    assert len(details['processor_error']['details']['long']) == 2000


def test_api_response_includes_processor_details(household, monkeypatch):
    _, client, headers, *_ = household
    assert client.patch('/api/v1/settings', json={'ocr_url': 'http://processor:8081', 'ocr_token': 'secret-token'},
                        headers=headers).status_code == 200
    payload = {'error': {'code': 'provider_error', 'message': 'Model provider rejected the request.',
                         'details': {'stage': 'model_request', 'provider_http_status': 401, 'request_id': 'ocr-123'}}}

    class Opener:
        def open(self, *args, **kwargs):
            raise HTTPError('http://processor:8081/v1/capabilities', 502, 'failure', {'X-Request-ID': 'ocr-123'},
                            io.BytesIO(json.dumps(payload).encode()))

    monkeypatch.setattr(importer, 'build_opener', lambda handler: Opener())
    response = client.post('/api/v1/ocr/test', headers=headers)
    assert response.status_code == 502
    assert response.json['error']['code'] == 'ocr_service_error'
    assert response.json['error']['details']['processor_error'] == payload['error']
    assert response.json['error']['details']['processor_request_id'] == 'ocr-123'
    assert 'secret-token' not in response.get_data(as_text=True)
