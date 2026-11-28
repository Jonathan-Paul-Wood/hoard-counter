"""Persistent review inbox and browser-independent OCR collection."""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import time
from uuid import NAMESPACE_URL, uuid4, uuid5

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import text

from .auth import login_required
from .backup import mutation_lock
from .ledger import list_records, add_audit
from .models import db, Record, PendingTransaction, utcnow
from .statement_import import (configuration, capabilities_for, multipart, processor_call, extraction_schema,
    valid_csv, build_preview, annotate, apply_approval, row_errors, FIELDS, MAX_UPLOAD)
from .files import safe_filename
from .validation import ApiError, currency_definitions, identifier

bp = Blueprint('pending_imports', __name__)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def import_context():
    return dict(accounts=[{key: value.get(key) for key in ('id', 'name', 'description', 'account_number', 'type', 'currency', 'account_class')}
                          for value in (record.to_dict() for record in list_records('account'))],
                categories=[{key: record.to_dict().get(key, '') for key in ('id', 'name', 'description')} for record in list_records('category')],
                rules=[record.to_dict() for record in list_records('rule') if record.data.get('enabled', True)],
                currencies=[{key: value[key] for key in ('code', 'precision')} for value in currency_definitions()], default_currency='USD')


def public_job(record):
    data = record.data
    state = data['state']
    if state == 'processing' and time.time() - data['accepted_at'] >= 360:
        state = 'delayed'
    return dict(id=record.id, state=state, files=data.get('files', []), error=data.get('error'),
                warnings=data.get('warnings', []), created_at=data['accepted_at'])


def validated_status(response, expected):
    stages = {'processing_file', 'extracting_transactions', 'identifying_accounts', 'determining_categories', 'completed'}
    if not isinstance(response.get('ready'), bool) or not isinstance(response.get('jobs'), list) or len(response['jobs']) != len(expected['files']):
        raise ApiError('OCR Processor returned malformed progress.', 502, 'ocr_response_invalid')
    for index, (job, original) in enumerate(zip(response['jobs'], expected['files'])):
        if (not isinstance(job, dict) or job.get('job_id') != original['job_id'] or job.get('run_id') != original['run_id']
                or job.get('status') not in stages or job.get('state') not in {'queued', 'running', 'succeeded', 'failed'}
                or type(job.get('completed')) is not int or job['completed'] < 0
                or (job.get('total') is not None and (type(job['total']) is not int or job['total'] < job['completed']))):
            raise ApiError('OCR Processor returned malformed file progress.', 502, 'ocr_response_invalid')
        job['filename'] = expected['manifest'][index]['filename']
    return response


@bp.post('/transactions/import/jobs')
@login_required
def submit():
    settings, context = configuration(), import_context()
    if len(json.dumps(context)) > 64000 or len(context['accounts']) > 254 or len(context['categories']) > 254:
        raise ApiError('Account/category/rule context exceeds the processor limit.', 413)
    actor = g.user.id
    db.session.rollback()
    capabilities = capabilities_for(settings)
    if not capabilities.get('transaction_jobs_configured'):
        raise ApiError('Update OCR Processor to enable background import jobs.', 503)
    limits = capabilities.get('limits', {})
    max_bytes = min(MAX_UPLOAD, int(limits.get('upload_bytes', MAX_UPLOAD)))
    uploads = request.files.getlist('documents')
    if not 1 <= len(uploads) <= min(5, int(limits.get('files', 5))):
        raise ApiError('Choose between 1 and 5 files.')
    files, manifest, total = [], [], 0
    for upload in uploads:
        payload = upload.read(max_bytes + 1)
        total += len(payload)
        if not payload or total > max_bytes:
            raise ApiError('Documents are empty or exceed the total upload limit.', 413)
        if not (payload.startswith((b'%PDF-', b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'II*\x00', b'MM\x00*')) or
                (payload.startswith(b'RIFF') and payload[8:12] == b'WEBP') or valid_csv(payload)):
            raise ApiError('Choose PDF, CSV, PNG, JPEG, TIFF, or WebP documents.', 415)
        job_id = hashlib.sha256(payload).hexdigest()
        if any(item['job_id'] == job_id for item in manifest):
            continue
        files.append(payload)
        manifest.append(dict(job_id=job_id, filename=safe_filename(upload.filename, 'Document')))
    context_hash = fingerprint(dict(context=context, schema=extraction_schema(), model=capabilities.get('transaction_model'),
                                    version=capabilities.get('transaction_extraction_version')))
    # Receipts outlive pending rows, acceptance, deletion, and OCR cleanup.
    retained_files, retained_manifest = [], []
    for payload, item in zip(files, manifest):
        receipt = db.session.get(Record, str(uuid5(NAMESPACE_URL, 'ocr-file:' + item['job_id'] + ':' + context_hash)))
        if receipt is None:
            retained_files.append(payload)
            retained_manifest.append(item)
    db.session.rollback()
    if not retained_files:
        return jsonify(already_received=True), 200
    body, content_type = multipart(retained_files, context, retained_manifest)
    if len(body) > max_bytes:
        raise ApiError('Documents plus context exceed the processor upload limit.', 413)
    response = processor_call(settings, '/v1/transaction-jobs', body, content_type, timeout=60)
    try:
        upstream_id = identifier(response['submission_id'])
        instance = identifier(response['service_instance_id'])
        jobs = response['jobs']
        if not isinstance(jobs, list) or len(jobs) != len(retained_manifest):
            raise ValueError()
        for job, item in zip(jobs, retained_manifest):
            identifier(job['run_id'])
            if job['job_id'] != item['job_id']:
                raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise ApiError('OCR Processor returned invalid job references.', 502) from None
    now = time.time()
    record = Record(kind='ocr_import_job', data=dict(state='processing', accepted_at=now, next_check=now + 1, poll_exponent=0,
        lease_until=0, upstream_id=upstream_id, instance=instance, files=jobs, manifest=retained_manifest,
        context_hash=context_hash, actor_id=actor, endpoint=settings['ocr_url'], warnings=[]))
    with collector_write():
        db.session.add(record)
    return jsonify(public_job(record)), 202


@bp.get('/transactions/import/review')
@login_required
def review():
    pending = db.session.scalars(db.select(PendingTransaction).order_by(PendingTransaction.created_at, PendingTransaction.id)).all()
    rows = [item.to_dict() for item in pending]
    for row in rows:
        row['errors'] = row_errors(row['transaction'])
    accounts = {record.id: record.to_dict() for record in list_records('account')}
    annotate(rows, accounts)
    jobs = [public_job(record) for record in list_records('ocr_import_job')]
    return jsonify(rows=rows, pending_count=len(rows), jobs=jobs)


@bp.get('/transactions/import/summary')
@login_required
def summary():
    count = db.session.scalar(db.select(db.func.count()).select_from(PendingTransaction))
    return jsonify(pending_count=count, jobs=[public_job(record) for record in list_records('ocr_import_job')])


@bp.post('/transactions/import/jobs/<identity>/cancel')
@login_required
def cancel(identity):
    record = db.session.get(Record, identifier(identity))
    if record is None or record.kind != 'ocr_import_job':
        raise ApiError('Import not found.', 404)
    if record.data['state'] not in ('processing', 'delayed') or time.time() - record.data['accepted_at'] < 360:
        raise ApiError('Only delayed imports can stop checks.', 409)
    record.data = {**record.data, 'state': 'cancelled', 'lease_until': 0}
    db.session.commit()
    return jsonify(public_job(record))


def check_revision(item, row):
    if item is None:
        raise ApiError('A pending transaction was already saved or deleted. Reload Review Imports.', 409, 'revision_conflict')
    if type(row.get('revision')) is not int or row['revision'] != item.revision:
        raise ApiError('A pending transaction changed. Reload Review Imports.', 409, 'revision_conflict')


@bp.post('/transactions/import/review/save')
@login_required
def save_review():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError('Provide pending transaction changes.')
    operation_id = identifier(payload.get('operation_id'))
    digest = fingerprint(payload)
    receipt = db.session.get(Record, operation_id)
    if receipt:
        if receipt.kind != 'pending_save' or receipt.data.get('digest') != digest:
            raise ApiError('Save request conflicts with an earlier request.', 409)
        return jsonify(receipt.data['result'])
    rows, deleted = payload.get('rows', []), payload.get('deleted', [])
    if not isinstance(rows, list) or not isinstance(deleted, list) or not 1 <= len(rows) + len(deleted) <= 1000:
        raise ApiError('Provide between 1 and 1000 pending transaction changes.')
    identities = [identifier(row.get('id')) for row in rows + deleted if isinstance(row, dict)]
    if len(identities) != len(rows) + len(deleted) or len(set(identities)) != len(identities):
        raise ApiError('Each pending transaction must appear once.')
    items = {}
    for row in rows + deleted:
        item = db.session.get(PendingTransaction, row['id'])
        check_revision(item, row)
        items[item.id] = item
    approved = []
    consumed = set()
    for row in rows:
        item = items[row['id']]
        transaction = row.get('transaction')
        if not isinstance(transaction, dict) or set(transaction) - set(FIELDS) or len(json.dumps(transaction)) > 64000:
            raise ApiError('Invalid transaction draft.')
        if type(row.get('selected', False)) is not bool:
            raise ApiError('Accepted must be true or false.')
        acknowledged = row.get('acknowledged_duplicates', [])
        if not isinstance(acknowledged, list) or len(acknowledged) > 1000 or any(not isinstance(value, str) for value in acknowledged):
            raise ApiError('Duplicate acknowledgments must be a list of identifiers.')
        for field, value in transaction.items():
            valid = (type(value) is bool if field in ('is_refund', 'investment_fee') else
                     isinstance(value, list) and all(isinstance(tag, str) for tag in value) if field == 'tag_ids' else
                     isinstance(value, str) or (field in ('category_id', 'subscription_id', 'subscription_date') and value is None))
            if not valid:
                raise ApiError(f'Invalid {field} in draft.')
        # Provenance is server owned; combinations can only consume revision-checked drafts.
        combined = row.get('combined_ids', [])
        if not isinstance(combined, list) or any(not isinstance(identity, str) or identity not in {entry['id'] for entry in deleted} for identity in combined):
            raise ApiError('Combined rows must be included in deleted revisions.')
        if len(combined) != len(set(combined)) or consumed.intersection(combined):
            raise ApiError('A pending row can only be combined once.')
        consumed.update(combined)
        sources = deepcopy(item.data['sources'])
        for identity in combined:
            sources.extend(items[identity].data['sources'])
        data = {**item.data, 'transaction': transaction, 'sources': sources,
                'acknowledged_duplicates': acknowledged}
        if row.get('existing_transaction_id'):
            identifier(row['existing_transaction_id'])
            if type(row.get('existing_revision')) is not int or row['existing_revision'] < 1:
                raise ApiError('Provide the matched transaction revision.')
            data.update(existing_transaction_id=row['existing_transaction_id'], existing_revision=row.get('existing_revision'))
        else:
            data.pop('existing_transaction_id', None)
            data.pop('existing_revision', None)
        if row.get('selected'):
            approved.append(dict(**data, id=item.id))
        else:
            item.data = data
            item.accepted = False
            item.revision += 1
            item.updated_at = utcnow()
    result = dict(total=0, transaction_ids=[])
    if approved:
        # Ledger changes and pending removals share this transaction.
        result = apply_approval(dict(batch_id=str(uuid5(NAMESPACE_URL, 'pending-approval:' + operation_id)), rows=approved), g.user, commit=False)
    for row in deleted + [row for row in rows if row.get('selected')]:
        item = items[row['id']]
        add_audit('pending_transaction', item.id, 'accept' if row in rows else 'delete', g.user, 'statement-import', item.data, None)
        db.session.delete(item)
    db.session.add(Record(id=operation_id, kind='pending_save', data=dict(digest=digest, result=result)))
    db.session.commit()
    return jsonify(result)


@contextmanager
def collector_write():
    db.session.rollback()
    with mutation_lock(current_app.config['DATA_DIR']):
        if Path(current_app.config['DATA_DIR'], '.maintenance').exists():
            raise RuntimeError('Maintenance is active')
        if db.engine.dialect.name == 'sqlite':
            db.session.execute(text('BEGIN IMMEDIATE'))
        try:
            yield
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise


def collect_one(now=None):
    now = time.time() if now is None else now
    lease = str(uuid4())
    with collector_write():
        job = next((record for record in list_records('ocr_import_job') if record.data['state'] in ('processing', 'delayed', 'ack_pending')
                    and record.data.get('next_check', 0) <= now and record.data.get('lease_until', 0) <= now), None)
        if job is None:
            return False
        identity = job.id
        job.data = {**job.data, 'lease_until': now + 90, 'lease': lease}
        data = deepcopy(job.data)
        settings = configuration()
        # Do not accidentally collect a stored ID from a different service after settings change.
        settings['ocr_url'] = data['endpoint']
    base = '/v1/transaction-jobs/' + data['upstream_id']
    error = None
    response = None
    ack = data['state'] == 'ack_pending'
    try:
        if ack:
            processor_call(settings, base + '/ack', b'{}', 'application/json', timeout=10)
        else:
            response = processor_call(settings, base, timeout=10)
            if response.get('service_instance_id') != data['instance']:
                raise ApiError('OCR Processor restarted. Upload the files again.', 404)
            validated_status(response, data)
            if response.get('ready'):
                response = processor_call(settings, base + '/result', timeout=10)
                validated_status(response, data)
    except ApiError as exc:
        error = exc
    with collector_write():
        job = db.session.get(Record, identity)
        if job.data.get('lease') != lease or job.data['state'] == 'cancelled':
            return True
        update = deepcopy(job.data)
        update['lease_until'] = 0
        if error:
            update['error'] = error.message
            upstream_status = (error.details or {}).get('processor_http_status')
            if error.status in (401, 403, 404) or upstream_status in (401, 403, 404) or error.code == 'ocr_response_invalid':
                # An ack may have succeeded before the connection broke; pending rows are already safe.
                update['state'] = 'completed' if ack and upstream_status == 404 else 'failed'
        elif ack:
            update.update(state='completed', error=None)
        else:
            update.update(files=response.get('jobs', data['files']), error=None)
            if response.get('ready'):
                try:
                    with db.session.begin_nested():
                        warnings = ingest(job, response)
                    update.update(state='ack_pending', warnings=warnings)
                except (ApiError, ValueError, KeyError, TypeError):
                    update.update(state='failed', error='OCR result validation failed; no partial result was saved.')
        exponent = min(7, update.get('poll_exponent', 0) + 1)
        update['poll_exponent'] = exponent
        update['next_check'] = time.time() + (1 if update['state'] == 'ack_pending' else 128 if time.time() - update['accepted_at'] >= 360 else 2 ** exponent)
        job.data = update
    return True


def ingest(job, response):
    files = response.get('files')
    expected = job.data['files']
    if not isinstance(files, list) or len(files) != len(expected):
        raise ApiError('Invalid aggregate OCR result.', 502)
    accounts = {record.id: record.to_dict() for record in list_records('account')}
    categories = [record.to_dict() for record in list_records('category')]
    total = 0
    warnings = []
    for index, (file, reference) in enumerate(zip(files, expected), 1):
        if file['job_id'] != reference['job_id'] or file['run_id'] != reference['run_id']:
            raise ApiError('OCR result referenced another file.', 502)
        if file.get('error'):
            if not isinstance(file['error'], dict) or not isinstance(file['error'].get('message'), str):
                raise ApiError('Invalid file error in OCR result.', 502)
            warnings.append(reference['filename'] + ': ' + file['error']['message'])
            continue
        receipt_id = str(uuid5(NAMESPACE_URL, 'ocr-file:' + reference['job_id'] + ':' + job.data['context_hash']))
        if db.session.get(Record, receipt_id):
            continue
        preview = build_preview(file['result'], [reference['filename']], accounts, categories)
        warnings.extend(reference['filename'] + ': ' + warning for warning in preview['warnings'])
        total += len(preview['rows'])
        if total > 500:
            raise ApiError('Import contains more than 500 transactions.', 502)
        for number, row in enumerate(preview['rows']):
            row['transaction'] = {key: value for key, value in row['transaction'].items() if key in FIELDS}
            for source in row['sources']:
                source['document'] = index
            identity = str(uuid5(NAMESPACE_URL, reference['run_id'] + ':' + str(number)))
            row.pop('id', None)
            row.pop('selected', None)
            db.session.add(PendingTransaction(id=identity, submission_id=job.id, run_id=reference['run_id'], candidate_id=str(number),
                uploader_id=job.data['actor_id'], data=row, accepted=False))
        db.session.add(Record(id=receipt_id, kind='ocr_ingestion', data=dict(job_id=reference['job_id'], run_id=reference['run_id'])))
    return warnings
