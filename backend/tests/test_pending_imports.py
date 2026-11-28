from copy import deepcopy
import io
import time
from uuid import uuid4

import pytest

from test_admin_auth import household
from test_statement_import import configured, extracted
from whc import pending_imports as imports
from whc.models import db, Record, PendingTransaction
from whc.ledger import list_records
from whc.validation import ApiError


@pytest.fixture
def jobs(configured, monkeypatch):
    app, client, headers, member, mh, (account, other, category) = configured
    state = dict(ready=False, calls=[], submissions={}, instance=str(uuid4()), run=str(uuid4()), ack_error=False)

    def call(settings, path, body=None, content_type=None, timeout=None):
        assert not db.session().in_transaction(), 'No transaction during network calls'
        state['calls'].append(path)
        if path == '/v1/capabilities':
            return dict(transaction_extraction_configured=True, transaction_jobs_configured=True, transaction_model='kev', limits={})
        if path == '/v1/transaction-jobs':
            identity = str(uuid4())
            import hashlib
            files = [dict(job_id=hashlib.sha256(b'Date,Amount\n2026-11-15,12.50\n').hexdigest(), run_id=state['run'], filename='rows.csv',
                          state='running', status='processing_file', completed=0, total=1)]
            state['submissions'][identity] = files
            return dict(submission_id=identity, service_instance_id=state['instance'], jobs=files)
        identity = path.split('/')[3]
        files = state['submissions'][identity]
        if path.endswith('/ack'):
            assert db.session.scalar(db.select(db.func.count()).select_from(PendingTransaction)) is not None
            db.session.rollback()
            if state['ack_error']:
                raise ApiError('Network interrupted', 503)
            return dict(acknowledged=True)
        response = dict(service_instance_id=state['instance'], jobs=files, ready=state['ready'])
        if path.endswith('/result'):
            if state.get('on_result'):
                state['on_result']()
            result = dict(pages=[dict(document=1, page=1)], warnings=[], data=dict(transactions=[extracted(account, category_id=category)]))
            if state.get('bad'):
                result['data']['transactions'][0]['document'] = 2
            response['files'] = [dict(job_id=files[0]['job_id'], run_id=files[0]['run_id'], result=result, error=None)]
            if state.get('file_failed'):
                response['jobs'] = [{**file, 'state': 'failed', 'status': 'completed'} for file in files]
                response['files'][0].update(result=None, error=dict(message='Extraction failed'))
        return response
    monkeypatch.setattr(imports, 'processor_call', call)
    # capabilities_for belongs to the synchronous module.
    monkeypatch.setattr('whc.statement_import.processor_call', call)
    return configured, state


def submit(client, headers):
    response = client.post('/api/v1/transactions/import/jobs', headers=headers,
        data={'documents': (io.BytesIO(b'Date,Amount\n2026-11-15,12.50\n'), 'rows.csv')})
    assert response.status_code == 202, response.json
    return response.json['id']


def collect(app, identity):
    with app.app_context():
        record = db.session.get(Record, identity)
        record.data = {**record.data, 'next_check': 0, 'lease_until': 0}
        db.session.commit()
        assert imports.collect_one()


def review(client):
    response = client.get('/api/v1/transactions/import/review')
    assert response.status_code == 200
    return response.json


def save(client, headers, rows=None, deleted=None, operation_id=None):
    return client.post('/api/v1/transactions/import/review/save', headers=headers,
        json=dict(operation_id=operation_id or str(uuid4()), rows=rows or [], deleted=deleted or []))


def test_background_collection_save_accept_and_idempotency(jobs):
    (app, client, headers, member, mh, _), state = jobs
    identity = submit(client, headers)
    collect(app, identity)
    assert review(client)['rows'] == []
    state['ready'] = True
    collect(app, identity)
    row, = review(member)['rows']  # Shared household inbox
    assert not row['selected']
    assert client.get('/api/v1/transactions/import/summary').json['pending_count'] == 1
    assert not any(path.endswith('/ack') for path in state['calls'])
    collect(app, identity)
    assert state['calls'][-1].endswith('/ack')
    row['transaction']['description'] = 'Edited draft'
    draft_response = save(member, mh, [row])
    assert draft_response.status_code == 200, draft_response.json
    stale = save(client, headers, [row])
    assert stale.status_code == 409
    row, = review(client)['rows']
    assert row['transaction']['description'] == 'Edited draft'
    row['selected'] = True
    operation = str(uuid4())
    response = save(client, headers, [row], operation_id=operation)
    assert response.status_code == 200, response.json
    assert response.json['total'] == 1
    assert save(client, headers, [row], operation_id=operation).json == response.json
    assert review(client)['rows'] == []
    with app.app_context():
        assert len([record for record in list_records('transaction') if not record.data.get('system')]) == 1
    response = client.post('/api/v1/transactions/import/jobs', headers=headers,
        data={'documents': (io.BytesIO(b'Date,Amount\n2026-11-15,12.50\n'), 'renamed.csv')})
    assert response.json == {'already_received': True}


def test_ack_retry_cannot_recreate_deleted_drafts(jobs):
    (app, client, headers, _, _, _), state = jobs
    identity = submit(client, headers)
    state.update(ready=True, ack_error=True)
    collect(app, identity)
    row, = review(client)['rows']
    assert save(client, headers, deleted=[dict(id=row['id'], revision=row['revision'])]).status_code == 200
    collect(app, identity)
    assert review(client)['rows'] == []
    state['ack_error'] = False
    collect(app, identity)
    assert review(client)['jobs'][0]['state'] == 'completed'


def test_delay_cancel_stops_collection(jobs):
    (app, client, headers, _, _, _), state = jobs
    identity = submit(client, headers)
    assert client.post(f'/api/v1/transactions/import/jobs/{identity}/cancel', headers=headers).status_code == 409
    with app.app_context():
        record = db.session.get(Record, identity)
        record.data = {**record.data, 'accepted_at': time.time() - 361}
        db.session.commit()
    assert review(client)['jobs'][0]['state'] == 'delayed'
    assert client.post(f'/api/v1/transactions/import/jobs/{identity}/cancel', headers=headers).status_code == 200
    before = len(state['calls'])
    with app.app_context():
        assert not imports.collect_one()
    assert len(state['calls']) == before


def test_malformed_result_never_acknowledged_or_partially_saved(jobs):
    (app, client, headers, _, _, _), state = jobs
    identity = submit(client, headers)
    state.update(ready=True, bad=True)
    collect(app, identity)
    result = review(client)
    assert result['rows'] == []
    assert result['jobs'][0]['state'] == 'failed'
    assert not any(path.endswith('/ack') for path in state['calls'])


@pytest.mark.parametrize('file_failed', [False, True])
def test_clear_failed_import_removes_persisted_job(jobs, file_failed):
    (app, client, headers, member, mh, _), state = jobs
    identity = submit(client, headers)
    state.update(ready=True, bad=not file_failed, file_failed=file_failed)
    collect(app, identity)
    if file_failed:
        collect(app, identity)
    assert review(client)['jobs'][0]['state'] == ('completed' if file_failed else 'failed')
    assert review(client)['rows'] == []
    response = member.delete(f'/api/v1/transactions/import/jobs/{identity}', headers=mh)
    assert response.status_code == 200, response.json
    assert response.json == {'cleared': True}
    assert review(client)['jobs'] == []
    assert client.get('/api/v1/transactions/import/summary').json['jobs'] == []
    with app.app_context():
        assert db.session.get(Record, identity) is None
        assert not imports.collect_one()
    # Failed files have no ingestion receipt preventing a retry.
    submit(client, headers)


def test_clear_completed_job_preserves_drafts_and_ingestion_receipts(jobs):
    (app, client, headers, _, _, _), state = jobs
    identity = submit(client, headers)
    state['ready'] = True
    collect(app, identity)
    collect(app, identity)
    rows = review(client)['rows']
    assert client.delete(f'/api/v1/transactions/import/jobs/{identity}', headers=headers).status_code == 200
    assert review(client)['rows'] == rows
    with app.app_context():
        assert db.session.get(Record, identity) is None
        assert len(list_records('ocr_ingestion')) == 1


@pytest.mark.parametrize('job_state', ['processing', 'delayed', 'ack_pending'])
def test_cannot_clear_unfinished_import(jobs, job_state):
    (app, client, headers, _, _, _), state = jobs
    identity = submit(client, headers)
    with app.app_context():
        record = db.session.get(Record, identity)
        record.data = {**record.data, 'state': job_state}
        db.session.commit()
    assert client.delete(f'/api/v1/transactions/import/jobs/{identity}', headers=headers).status_code == 409
    assert len(review(client)['jobs']) == 1


def test_clear_cancelled_job_while_collection_is_in_flight(jobs):
    (app, client, headers, _, _, _), state = jobs
    identity = submit(client, headers)
    state['ready'] = True
    def clear_in_flight():
        record = db.session.get(Record, identity)
        record.data = {**record.data, 'state': 'cancelled'}
        db.session.commit()
        assert client.delete(f'/api/v1/transactions/import/jobs/{identity}', headers=headers).status_code == 200
    state['on_result'] = clear_in_flight
    collect(app, identity)
    assert review(client)['jobs'] == []
    assert review(client)['rows'] == []


def test_clear_job_requires_csrf_and_rejects_unrelated_records(jobs):
    (app, client, headers, _, _, (account, _, _)), state = jobs
    assert client.delete(f'/api/v1/transactions/import/jobs/{account}').status_code == 403
    assert client.delete(f'/api/v1/transactions/import/jobs/{account}', headers=headers).status_code == 404
    assert client.delete(f'/api/v1/transactions/import/jobs/{uuid4()}', headers=headers).status_code == 404


def test_invalid_accepted_row_does_not_delete_draft(jobs):
    (app, client, headers, _, _, _), state = jobs
    identity = submit(client, headers)
    state['ready'] = True
    collect(app, identity)
    row, = review(client)['rows']
    row['transaction']['amount'] = ''
    # Incomplete drafts can be saved.
    draft_response = save(client, headers, [row])
    assert draft_response.status_code == 200, draft_response.json
    row, = review(client)['rows']
    row['selected'] = True
    assert save(client, headers, [row]).status_code == 400
    assert len(review(client)['rows']) == 1


def test_cancel_wins_race_with_result_delivery(jobs):
    (app, client, headers, _, _, _), state = jobs
    identity = submit(client, headers)
    state['ready'] = True
    def cancel_in_flight():
        record = db.session.get(Record, identity)
        record.data = {**record.data, 'state': 'cancelled', 'lease_until': 0}
        db.session.commit()
    state['on_result'] = cancel_in_flight
    collect(app, identity)
    assert review(client)['rows'] == []
    assert review(client)['jobs'][0]['state'] == 'cancelled'
    assert not any(path.endswith('/ack') for path in state['calls'])


def test_exponential_backoff_caps_and_survives_new_app_context(jobs, monkeypatch):
    (app, client, headers, _, _, _), state = jobs
    identity = submit(client, headers)
    with app.app_context():
        record = db.session.get(Record, identity)
        accepted = record.data['accepted_at']
        assert not imports.collect_one(now=accepted + .5)
    for exponent in range(1, 10):
        with app.app_context():
            record = db.session.get(Record, identity)
            due = record.data['next_check']
            db.session.rollback()
            monkeypatch.setattr(imports.time, 'time', lambda: due)
            assert imports.collect_one(now=due)
            record = db.session.get(Record, identity)
            assert record.data['next_check'] == due + 2 ** min(7, exponent)


def test_mixed_accept_and_delete_rolls_back_on_invalid_acceptance(jobs):
    (app, client, headers, _, _, _), state = jobs
    identity = submit(client, headers)
    state['ready'] = True
    collect(app, identity)
    original, = review(client)['rows']
    with app.app_context():
        item = db.session.get(PendingTransaction, original['id'])
        duplicate = PendingTransaction(submission_id=item.submission_id, run_id=str(uuid4()), candidate_id='other',
            uploader_id=item.uploader_id, data=deepcopy(item.data))
        db.session.add(duplicate)
        db.session.commit()
        other_id = duplicate.id
    rows = review(client)['rows']
    original = next(row for row in rows if row['id'] == original['id'])
    other = next(row for row in rows if row['id'] == other_id)
    original['selected'] = True
    original['transaction']['amount'] = 'invalid'
    response = save(client, headers, [original], [dict(id=other['id'], revision=other['revision'])])
    assert response.status_code == 400
    assert len(review(client)['rows']) == 2
