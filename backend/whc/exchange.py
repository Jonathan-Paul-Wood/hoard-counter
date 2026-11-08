"""Portable, previewed data exchange with UUID/last-modified conflict handling."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import shutil
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED, BadZipFile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, current_app, g, jsonify, request, send_file

from .auth import login_required
from .models import db, Record, AuditEvent, utcnow
from .ledger import (save_record, list_records, add_audit, get_record, _replace_postings,
                     system_id, rebuild_account_valuations)
from .validation import ApiError, timestamp, identifier, amount, decimal, date_string, money
from .files import attachment_path, identify, MAX_FILE, safe_filename

bp = Blueprint('exchange', __name__)
VERSION = 1
LIMIT = 100 * 1024 * 1024
ORDER = ['currency', 'rate', 'category', 'tag', 'account', 'subscription', 'rule', 'budget', 'transaction', 'valuation', 'scenario', 'attachment', 'setting']
ACCOUNT_FIELDS = ['id', 'name', 'type', 'account_class', 'currency', 'opening_balance', 'opening_date', 'balance', 'last_modified', 'deleted']
TRANSACTION_FIELDS = ['id', 'type', 'source_account_id', 'destination_account_id', 'date', 'amount', 'destination_amount', 'description', 'category_id', 'tag_ids', 'notes', 'subscription_id', 'subscription_date', 'is_refund', 'investment_fee', 'last_modified', 'deleted']
READONLY = {'id', 'revision', 'last_modified', 'deleted', 'balance', 'balance_usd', 'signed_balance', 'signed_balance_usd', 'postings', 'missing_rate'}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def snapshot_stamp():
    rows = db.session.execute(db.select(Record.id, Record.revision, Record.last_modified).order_by(Record.id)).all()
    return hashlib.sha256(canonical([list(r) for r in rows]).encode()).hexdigest()


def exported_records():
    records = []
    for row in db.session.scalars(db.select(Record).order_by(Record.kind, Record.id)):
        if row.kind not in ORDER or (row.kind == 'account' and row.data.get('system')):
            continue
        if row.kind == 'transaction' and row.data.get('system') in ('opening', 'valuation'):
            continue
        records.append(dict(kind=row.kind, record=row.to_dict()))
    return records


def export_csv(kind, template=False):
    fields = ACCOUNT_FIELDS if kind == 'account' else TRANSACTION_FIELDS
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=fields + ['_data', '_escaped_fields'])
    writer.writeheader()
    if not template:
        from .ledger import public_record
        for record in list_records(kind, include_deleted=True):
            if record.data.get('system'):
                continue
            item = public_record(record)
            row, escaped = {}, []
            for field in fields:
                value = item.get(field, '')
                if value is None:
                    value = ''
                elif isinstance(value, (list, dict, bool)):
                    value = canonical(value)
                else:
                    value = str(value)
                if field not in ('amount', 'destination_amount', 'opening_balance', 'balance') and value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
                    value = "'" + value
                    escaped.append(field)
                row[field] = value
            row['_data'] = canonical(record.data)
            row['_escaped_fields'] = canonical(escaped)
            writer.writerow(row)
    return out.getvalue().encode('utf-8-sig')


def export_archive():
    buffer = io.BytesIO()
    records = exported_records()
    files = {}
    with ZipFile(buffer, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('records.json', canonical(records))
        audit = [r.to_dict() for r in db.session.scalars(db.select(AuditEvent).order_by(AuditEvent.created_at))]
        archive.writestr('audit.json', canonical(audit))
        for entry in records:
            if entry['kind'] != 'attachment':
                continue
            data = entry['record']
            path = attachment_path(data['path'])
            if not path.is_file():
                raise ApiError(f"Attachment is missing: {data.get('filename', data['path'])}", 409)
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            if digest != data.get('sha256'):
                raise ApiError('Attachment checksum does not match its record.', 409)
            name = 'uploads/' + data['path']
            if name not in files:
                archive.writestr(name, payload)
                files[name] = digest
        archive.writestr('manifest.json', canonical(dict(format='hoard-counter', version=VERSION, created_at=utcnow(), files=files)))
    buffer.seek(0)
    return buffer


@bp.get('/exports')
@login_required
def export():
    fmt = request.args.get('format', 'archive')
    if fmt == 'archive':
        return send_file(export_archive(), mimetype='application/zip', as_attachment=True, download_name='hoard-counter.zip')
    if fmt not in ('accounts', 'transactions'):
        raise ValueError('Export format must be archive, accounts, or transactions.')
    return send_file(io.BytesIO(export_csv(fmt[:-1], request.args.get('template') == 'true')), mimetype='text/csv', as_attachment=True, download_name=fmt + '.csv')


def parse_csv(payload, kind):
    try:
        reader = csv.DictReader(io.StringIO(payload.decode('utf-8-sig'), newline=''))
    except UnicodeDecodeError:
        raise ValueError('CSV files must use UTF-8 encoding.')
    if not reader.fieldnames:
        raise ValueError('The CSV is empty or missing column headers.')
    fields = ACCOUNT_FIELDS if kind == 'account' else TRANSACTION_FIELDS
    result = []
    for index, row in enumerate(reader, 2):
        if index > 10001:
            raise ValueError('Import at most 10,000 records at a time.')
        if None in row:
            raise ValueError(f'CSV row {index} contains unexpected extra columns.')
        data = json.loads(row.get('_data') or '{}', parse_float=str)
        if not isinstance(data, dict):
            raise ValueError(f'CSV row {index}: _data must contain a JSON object.')
        escaped = json.loads(row.get('_escaped_fields') or '[]')
        if not isinstance(escaped, list) or any(not isinstance(field, str) for field in escaped):
            raise ValueError(f'CSV row {index}: _escaped_fields must contain a list of field names.')
        for field in fields:
            if field not in row or field == 'balance':
                continue
            value = row[field] or ''
            if field in escaped and value.startswith("'"):
                value = value[1:]
            if field in ('id', 'last_modified') and not value:
                continue
            if field in ('deleted', 'is_refund', 'investment_fee'):
                if value.lower() not in ('', 'true', 'false', '0', '1'):
                    raise ValueError(f'CSV row {index}: {field} must be true or false.')
                if value or field in data:
                    data[field] = value.lower() in ('true', '1')
            elif field == 'tag_ids':
                data[field] = json.loads(value or '[]')
            elif field in ('category_id', 'subscription_id', 'subscription_date'):
                if value or field in data:
                    data[field] = value or None
            elif value or field in data or field in ('description', 'name'):
                data[field] = value
        data.setdefault('id', str(uuid4()))
        data.setdefault('last_modified', utcnow())
        result.append(dict(kind=kind, record=data, line=index))
    return result


def parse_archive(payload, staging):
    try:
        archive = ZipFile(io.BytesIO(payload))
    except BadZipFile:
        raise ValueError('Select a valid Hoard Counter ZIP archive.')
    with archive:
        entries = archive.infolist()
        names = [e.filename for e in entries]
        if len(entries) > 20010 or len(set(names)) != len(names) or sum(e.file_size for e in entries) > LIMIT:
            raise ValueError('Archive is too large or contains duplicate entries.')
        if any(entry.flag_bits & 1 for entry in entries):
            raise ValueError('Encrypted archives are not supported.')
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name or path.as_posix() != name:
                raise ValueError('Archive contains an unsafe file path.')
        if not {'manifest.json', 'records.json'}.issubset(names):
            raise ValueError('Archive is missing its manifest or records.')
        manifest = json.loads(archive.read('manifest.json'))
        if not isinstance(manifest, dict) or manifest.get('format') != 'hoard-counter' or manifest.get('version') != VERSION:
            raise ValueError('Unsupported archive format or version.')
        records = json.loads(archive.read('records.json'), parse_float=str)
        normalize_records(records)
        declared = manifest.get('files', {})
        if not isinstance(declared, dict) or set(names) != {'manifest.json', 'records.json', *({'audit.json'} if 'audit.json' in names else set()), *declared}:
            raise ValueError('Archive entries do not match its file manifest.')
        staged_files = {}
        for name, digest in declared.items():
            if name not in names or not name.startswith('uploads/'):
                raise ValueError('Archive file manifest is invalid.')
            attachment_path(name[8:])
            blob = archive.read(name)
            if len(blob) > MAX_FILE or hashlib.sha256(blob).hexdigest() != digest:
                raise ValueError('An archived attachment failed checksum verification.')
            suffix, mime = identify(blob)
            target = staging / 'files' / str(uuid4())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
            staged_files[name[8:]] = dict(staged_name=target.name, sha256=digest, size=len(blob), suffix=suffix, mime_type=mime)
        audits = json.loads(archive.read('audit.json')) if 'audit.json' in names else []
        validate_audits(audits)
        referenced = set()
        for row in records:
            if row.get('kind') == 'attachment':
                data = row['record']
                if data.get('path') not in staged_files or staged_files[data['path']]['sha256'] != data.get('sha256'):
                    raise ValueError('An attachment record is missing its verified file.')
                actual = staged_files[data['path']]
                if data['path'] != data['id'] + actual['suffix']:
                    raise ValueError('Attachment file names must match their record UUID and content type.')
                if data.get('size') != actual['size'] or isinstance(data.get('size'), bool) or data.get('mime_type') != actual['mime_type']:
                    raise ValueError('Attachment metadata does not match the verified file.')
                if data.get('kind') not in ('receipt', 'item'):
                    raise ValueError('Attachment kind must be receipt or item.')
                data['filename'] = safe_filename(data.get('filename'), data['path'])
                referenced.add(data['path'])
        if referenced != set(staged_files):
            raise ValueError('Every archived upload must belong to an attachment record.')
        return records, staged_files, audits


def normalize_records(records):
    if not isinstance(records, list) or len(records) > 10000:
        raise ValueError('Import at most 10,000 records at a time.')
    seen = set()
    for entry in records:
        if not isinstance(entry, dict) or entry.get('kind') not in ORDER or not isinstance(entry.get('record'), dict):
            raise ValueError('Unsupported record type in import.')
        data = entry['record']
        data['id'] = identifier(data.get('id') or str(uuid4()))
        data['last_modified'] = timestamp(data.get('last_modified') or utcnow())
        if not isinstance(data.get('deleted', False), bool):
            raise ValueError('deleted must be a JSON boolean.')
        if data['id'] in seen:
            raise ValueError('The import contains the same UUID more than once.')
        seen.add(data['id'])


def validate_audits(audits):
    if not isinstance(audits, list):
        raise ValueError('Archive audit history must be a list.')
    seen = set()
    for item in audits:
        if not isinstance(item, dict):
            raise ValueError('Invalid archived audit event.')
        item['id'] = identifier(item.get('id'))
        item['created_at'] = timestamp(item.get('created_at'))
        if item['id'] in seen:
            raise ValueError('The archive contains duplicate audit event UUIDs.')
        seen.add(item['id'])
        if item.get('record_id'):
            item['record_id'] = identifier(item['record_id'])
        for key in ('kind', 'action'):
            if not isinstance(item.get(key), str) or not item[key] or len(item[key]) > 32:
                raise ValueError('Invalid archived audit event ' + key + '.')


def comparable(data):
    return {k: v for k, v in data.items() if k not in READONLY} | {'deleted': bool(data.get('deleted', False))}


def classify(kind, data):
    identity = identifier(data['id'])
    changed = timestamp(data.get('last_modified', utcnow()))
    data['last_modified'] = changed
    existing = db.session.get(Record, identity)
    if not existing:
        return 'create', None
    if existing.kind != kind:
        return 'conflict', existing
    if changed < existing.last_modified:
        return 'skip', existing
    if changed == existing.last_modified:
        return ('skip' if comparable(data) == comparable(existing.to_dict()) else 'conflict'), existing
    return 'update', existing


def import_special(kind, data, existing, actor):
    identity = identifier(data['id'])
    before = existing.to_dict() if existing else None
    clean = {k: v for k, v in data.items() if k not in READONLY}
    if kind == 'attachment':
        get_record('transaction', clean.get('transaction_id'), include_deleted=True)
        attachment_path(clean['path'])
        if not isinstance(clean.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', clean['sha256']):
            raise ValueError('Attachment checksum is missing.')
    elif kind == 'setting':
        # Settings can only be changed by an administrator, including imports.
        if actor.role != 'admin':
            raise ApiError('Only administrators can import hoard settings.', 403)
        if clean.get('default_currency', 'USD') != 'USD':
            raise ValueError('The hoard reporting currency must be USD.')
        if not isinstance(clean.get('household_name'), str) or not clean['household_name'].strip() or len(clean['household_name']) > 100:
            raise ValueError('Enter a hoard name of at most 100 characters.')
        try:
            ZoneInfo(clean.get('timezone', current_app.config['HOUSEHOLD_TIMEZONE']))
        except (ZoneInfoNotFoundError, TypeError, ValueError):
            raise ValueError('Select a valid IANA timezone.')
        if not data.get('deleted') and any(row.id != identity for row in list_records('setting')):
            raise ValueError('Hoard settings already exist with another UUID.')
    elif kind == 'transaction':
        if clean.get('system') != 'reconciliation':
            raise ValueError('Derived journal rows must not be imported.')
        account = get_record('account', clean['account_id'], include_deleted=bool(data.get('deleted')))
        if account.data['account_class'] not in ('asset', 'liability'):
            raise ValueError('Reconciliation applies to asset or liability accounts.')
        clean['date'] = date_string(clean['date'])
        native = decimal(amount(clean['amount'], account.data['currency']))
        rate = decimal(clean['fx_rate'])
        if rate <= 0:
            raise ValueError('Reconciliation conversion rate must be positive.')
        clean['usd_amount'] = money(native * rate)
    else:
        raise ValueError('Unsupported special import record.')
    record = existing or Record(id=identity, kind=kind)
    record.data = clean
    record.revision = existing.revision + 1 if existing else 1
    record.last_modified = data['last_modified']
    record.deleted = bool(data.get('deleted', False))
    if not existing:
        db.session.add(record)
    db.session.flush()
    if kind == 'transaction':
        entries = [] if record.deleted else [(account.id, native, native * rate), (system_id('reconciliation'), -native * rate, -native * rate)]
        _replace_postings(record, entries)
        rebuild_account_valuations(account.id, actor)
        if before and before.get('account_id') != account.id:
            rebuild_account_valuations(before['account_id'], actor)
    add_audit(kind, identity, 'import', actor, 'import', before, record.to_dict())
    return record


def apply_import(records, mode, actor):
    normalize_records(records)
    summary = dict(create=0, update=0, skip=0, conflict=0)
    changes, conflicts, seen = [], [], set()
    ordered = sorted(records, key=lambda r: (ORDER.index(r.get('kind')) if r.get('kind') in ORDER else 999, r.get('record', {}).get('date', '')))
    for entry in ordered:
        kind, incoming = entry.get('kind'), deepcopy(entry.get('record'))
        if kind not in ORDER or not isinstance(incoming, dict):
            raise ValueError('Unsupported record type in import.')
        incoming.setdefault('id', str(uuid4()))
        if incoming['id'] in seen:
            raise ValueError('The import contains the same UUID more than once.')
        seen.add(incoming['id'])
        action, existing = classify(kind, incoming)
        summary[action] += 1
        change = dict(kind=kind, id=incoming['id'], action=action, description=incoming.get('description', incoming.get('name', kind)), line=entry.get('line'))
        if action == 'conflict':
            conflicts.append({**change, 'message': 'UUID type or equal-timestamp content differs; resolve before importing.'})
        elif action != 'skip':
            if kind == 'currency' and actor.role != 'admin':
                raise ApiError('Only administrators can import currency definitions.', 403)
            if incoming.get('system') and not (kind == 'transaction' and incoming['system'] == 'reconciliation'):
                raise ValueError('System accounts and derived journals cannot be imported.')
            if existing and incoming.get('system') and existing.data.get('system') != incoming.get('system'):
                raise ValueError('An ordinary transaction cannot become a system journal during import.')
            if existing and existing.data.get('system') and not (mode == 'archive' and kind == 'transaction' and incoming.get('system') == 'reconciliation'):
                raise ValueError('System accounts and derived journals cannot be overwritten.')
            if kind in ('attachment', 'setting') or incoming.get('system'):
                if mode != 'archive':
                    raise ValueError('Special records require a full archive.')
                record = import_special(kind, incoming, existing, actor)
            else:
                record = save_record(kind, incoming, actor, record_id=incoming['id'], expected_revision=existing.revision if existing else None, origin='import', apply_rules=mode != 'archive', preserve_modified=True)
            change['after'] = record.to_dict()
        changes.append(change)
    db.session.flush()
    return summary, changes, conflicts


@bp.post('/imports/preview')
@login_required
def preview():
    file = request.files.get('file')
    mode = request.form.get('kind', 'archive')
    if not file or mode not in ('accounts', 'transactions', 'archive'):
        raise ValueError('Select an accounts CSV, transactions CSV, or full archive.')
    payload = file.read(LIMIT + 1)
    if len(payload) > LIMIT:
        raise ValueError('Imports must be 100 MiB or smaller.')
    token = str(uuid4())
    staging = Path(current_app.config['DATA_DIR']) / 'imports' / token
    staging.mkdir(parents=True, exist_ok=True)
    try:
        records, files, audit = (parse_archive(payload, staging) if mode == 'archive' else (parse_csv(payload, mode[:-1]), {}, []))
        normalize_records(records)
    except (BadZipFile, RuntimeError, NotImplementedError) as error:
        shutil.rmtree(staging)
        raise ValueError('Archive is corrupt or uses unsupported encryption/compression.') from error
    except Exception:
        shutil.rmtree(staging)
        raise
    stamp = snapshot_stamp()
    errors, changes, conflicts = [], [], []
    summary = {}
    nested = db.session.begin_nested()
    try:
        summary, changes, conflicts = apply_import(records, mode, g.user)
    except (ApiError, ValueError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    finally:
        nested.rollback()
    pending = dict(owner=g.user.id, created_at=utcnow(), stamp=stamp, records=records, files=files, audit=audit, mode=mode, valid=not errors and not conflicts)
    (staging / 'preview.json').write_text(canonical(pending), encoding='utf-8')
    if not pending['valid'] and (staging / 'files').exists():
        shutil.rmtree(staging / 'files')
    return jsonify(token=token, summary=summary, changes=changes, conflicts=conflicts, errors=errors)


@bp.post('/imports/commit')
@login_required
def commit():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError('Request body must be a JSON object.')
    token = identifier(body.get('token'))
    staging = Path(current_app.config['DATA_DIR']) / 'imports' / token
    path = staging / 'preview.json'
    if not path.exists():
        raise ApiError('Import preview not found. Preview the file again.', 404)
    pending = json.loads(path.read_text(encoding='utf-8'))
    if pending['owner'] != g.user.id:
        raise ApiError('This import belongs to another user.', 403)
    if datetime.fromisoformat(pending['created_at'].replace('Z', '+00:00')) < datetime.now(timezone.utc) - timedelta(minutes=30):
        raise ApiError('Import preview expired. Preview the file again.', 409)
    if not pending['valid']:
        raise ApiError('Resolve the preview errors and conflicts before importing.', 409)
    if snapshot_stamp() != pending['stamp']:
        raise ApiError('The ledger changed after preview. Preview again before importing.', 409)
    summary, changes, conflicts = apply_import(pending['records'], pending['mode'], g.user)
    if conflicts:
        raise ApiError('Import conflicts detected. Preview again.', 409)
    ready_files = []
    for relative, metadata in pending['files'].items():
        blob = (staging / 'files' / metadata['staged_name']).read_bytes()
        if hashlib.sha256(blob).hexdigest() != metadata['sha256']:
            raise ValueError('A staged file changed after preview.')
        destination = attachment_path(relative)
        if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != metadata['sha256']:
            raise ApiError('An existing attachment has a different checksum.', 409)
        ready_files.append((destination, blob))
    # Preserve original audit events as imported provenance, never as local actors.
    for item in pending['audit']:
        identity = identifier(item['id'])
        if not db.session.get(AuditEvent, identity):
            db.session.add(AuditEvent(id=identity, record_id=item.get('record_id'), kind=item.get('kind', 'import'), action=item.get('action', 'import'), actor_id=None, origin='imported-history', before=item.get('before'), after={'original_actor_id': item.get('actor_id'), 'original_origin': item.get('origin'), 'record': item.get('after')}, created_at=timestamp(item['created_at'])))
    created_files = []
    try:
        for destination, blob in ready_files:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                with destination.open('xb') as output:
                    created_files.append(destination)
                    output.write(blob)
        db.session.commit()
    except Exception:
        db.session.rollback()
        for destination in created_files:
            destination.unlink(missing_ok=True)
        raise
    try:
        shutil.rmtree(staging)
    except OSError:
        current_app.logger.warning('Committed import staging could not be removed: %s', token)
    return jsonify(summary=summary, changes=changes)
