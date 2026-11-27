"""Optional backend-only statement extraction and reviewed ledger creation."""
from copy import deepcopy
import csv
from io import StringIO
from datetime import date
from decimal import InvalidOperation
import hashlib
from http.client import HTTPException
import json
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from uuid import uuid4

from flask import Blueprint, g, jsonify, request

from .auth import login_required, admin_required
from .models import db, Record
from .ledger import (list_records, get_record, save_record, system_id, apply_transaction_rules,
                     _validate_transaction, _validate_archived_transaction, _link_subscription)
from .validation import ApiError, decimal, identifier, currency_definitions
from .files import safe_filename

bp = Blueprint('statement_import', __name__)
MAX_UPLOAD = 20 * 1024 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
FIELDS = ('type', 'date', 'description', 'source_account_id', 'destination_account_id',
          'amount', 'currency', 'destination_amount', 'destination_currency',
          'category_id', 'tag_ids', 'notes', 'is_refund', 'investment_fee', 'subscription_id', 'subscription_date')


def validate_url(value):
    if not isinstance(value, str) or len(value) > 2048 or not value.isascii() or '\\' in value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        raise ValueError('Enter an HTTP(S) OCR Processor URL.')
    if not value:
        return ''
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise ValueError('Enter a valid OCR Processor URL.') from None
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('Use an HTTP(S) URL without credentials, query, or fragment.')
    return value.rstrip('/')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def processor_error_details(error, settings):
    """Preserve bounded structured diagnostics, never a raw HTML/response body."""
    token = settings.get('ocr_token') or ''

    def clean(value, depth=0):
        if depth > 8:
            return '[truncated]'
        if isinstance(value, str):
            return (value.replace(token, '[redacted]') if token else value)[:2000]
        if isinstance(value, dict):
            return {clean(str(key), depth + 1): clean(item, depth + 1)
                    for key, item in list(value.items())[:64]}
        if isinstance(value, list):
            return [clean(item, depth + 1) for item in value[:64]]
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float) and value == value and abs(value) != float('inf'):
            return value
        return None

    details = {'processor_http_status': error.code}
    request_id = error.headers.get('X-Request-ID') if error.headers else None
    if request_id:
        details['processor_request_id'] = clean(request_id)
    try:
        payload = error.read(64 * 1024 + 1)
        if not payload:
            details['error_response'] = 'empty'
        elif len(payload) > 64 * 1024:
            details['error_response'] = 'too_large'
        else:
            result = json.loads(payload)
            upstream = result.get('error') if isinstance(result, dict) else None
            if isinstance(upstream, dict):
                details['processor_error'] = clean({key: upstream[key] for key in ('code', 'message', 'details') if key in upstream})
            else:
                details['error_response'] = 'unrecognized_envelope'
    except (ValueError, TypeError, AttributeError, RecursionError, OSError, HTTPException):
        details['error_response'] = 'unreadable_or_non_json'
    finally:
        error.close()
    return details


def processor_call(settings, path, body=None, content_type=None):
    headers = {'Accept': 'application/json'}
    if settings.get('ocr_token'):
        headers['Authorization'] = 'Bearer ' + settings['ocr_token']
    if content_type:
        headers['Content-Type'] = content_type
    target = validate_url(settings.get('ocr_url', ''))
    if not target:
        raise ApiError('Configure OCR Processor in Settings first.', 409, 'ocr_not_configured')
    req = Request(target + path, data=body, headers=headers)
    try:
        with build_opener(NoRedirect()).open(req, timeout=1830 if body else 30) as response:
            payload = response.read(MAX_RESPONSE + 1)
        if len(payload) > MAX_RESPONSE:
            raise ApiError('OCR Processor returned an oversized response.', 502, 'ocr_response_invalid')
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except HTTPError as exc:
        messages = {401: 'OCR Processor rejected the bearer token.', 413: 'OCR Processor document limits were exceeded.',
                    415: 'OCR Processor does not support this document format.', 422: 'OCR Processor could not read these documents.',
                    429: 'OCR Processor is busy. Wait a few seconds and try again.',
                    503: 'OCR Processor extraction is not configured.', 504: 'OCR Processor timed out.'}
        raise ApiError(messages.get(exc.code, 'OCR Processor failed to analyze the documents. Check its configuration.'),
                       429 if exc.code == 429 else 502, 'ocr_service_error',
                       details=processor_error_details(exc, settings)) from None
    except (socket.timeout, TimeoutError):
        raise ApiError('OCR Processor timed out. Try a smaller batch.', 504, 'ocr_timeout') from None
    except URLError:
        raise ApiError('Cannot reach OCR Processor. Check its URL and that it is running.', 503, 'ocr_unavailable') from None
    except (HTTPException, OSError):
        raise ApiError('OCR Processor connection was interrupted. Try a smaller batch or check the service.', 502, 'ocr_connection_failed') from None
    except (ValueError, UnicodeError):
        raise ApiError('OCR Processor returned an invalid response.', 502, 'ocr_response_invalid') from None


def configuration():
    from .admin import private_settings
    settings = private_settings()
    if not settings.get('ocr_url'):
        raise ApiError('Configure OCR Processor in Settings first.', 409, 'ocr_not_configured')
    return settings


def capabilities_for(settings):
    capabilities = processor_call(settings, '/v1/capabilities')
    limits = capabilities.get('limits', {})
    if type(capabilities.get('transaction_extraction_configured')) is not bool or not isinstance(limits, dict) or any(
        type(value) is not int or value < 1 for key, value in limits.items() if key in ('files', 'upload_bytes', 'pages', 'timeout_seconds')):
        raise ApiError('OCR Processor returned invalid capabilities.', 502, 'ocr_response_invalid')
    if not capabilities['transaction_extraction_configured']:
        raise ApiError('OCR Processor is reachable, but SystemOne transaction extraction is not configured.', 503, 'ocr_not_ready')
    return capabilities


@bp.post('/ocr/test')
@admin_required
def test_connection():
    settings = configuration()
    db.session.rollback()
    capabilities = capabilities_for(settings)
    return jsonify(capabilities)


def extraction_schema():
    nullable_text = {'type': ['string', 'null']}
    properties = {field: dict(nullable_text) for field in (
        'date', 'description', 'amount', 'currency', 'account_id', 'account_number',
        'source_account_id', 'destination_account_id', 'category_id', 'review_notes')}
    properties.update(direction={'type': ['string', 'null'], 'enum': ['outgoing', 'incoming', None]},
                      is_refund={'type': 'boolean'},
                      document={'type': 'integer', 'minimum': 1}, page={'type': 'integer', 'minimum': 1})
    return {'type': 'object', 'additionalProperties': False, 'required': ['transactions'],
            'properties': {'transactions': {'type': 'array', 'items': {'type': 'object',
                'additionalProperties': False, 'properties': properties, 'required': list(properties)}}}}


def multipart(files, context):
    boundary = 'hoard-' + uuid4().hex
    chunks = []
    for name, value in [('context', json.dumps(context)), ('output_format', json.dumps(extraction_schema()))]:
        chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for index, payload in enumerate(files):
        chunks.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="documents"; filename="document-{index}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode(), payload, b'\r\n'])
    chunks.append(f'--{boundary}--\r\n'.encode())
    return b''.join(chunks), 'multipart/form-data; boundary=' + boundary


def valid_csv(payload):
    try:
        encoding = 'utf-16' if payload.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
        text = payload.decode(encoding)
        if any(ord(char) < 32 and char not in '\r\n\t' for char in text):
            return False
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t')
        rows = [row for row in csv.reader(StringIO(text), dialect, strict=True) if any(cell.strip() for cell in row)]
        return (len(rows) >= 2 and len(rows[0]) >= 2 and len(set(rows[0])) == len(rows[0])
                and all(len(row) == len(rows[0]) for row in rows))
    except (UnicodeError, csv.Error):
        return False


def match_account(value, accounts):
    if not isinstance(value, str) or not value:
        return None
    if value in accounts:
        return value
    normalized = re.sub(r'[^a-zA-Z0-9*]', '', value).lower()
    exact = [key for key, account in accounts.items() if account.get('account_number') and
             re.sub(r'[^a-zA-Z0-9*]', '', account['account_number']).lower() == normalized]
    if len(exact) == 1:
        return exact[0]
    suffix = re.search(r'(\d{4,})$', normalized)
    if suffix and (re.search(r'[*x]', normalized) or len(normalized) == 4):
        candidates = [key for key, account in accounts.items() if
                      re.sub(r'\D', '', account.get('account_number') or '').endswith(suffix[1])]
        if len(candidates) == 1:
            return candidates[0]
    return None


def legs(transaction, accounts):
    result = []
    try:
        for field, direction, amount_field, currency_field in (
            ('source_account_id', 'outgoing', 'amount', 'currency'),
            ('destination_account_id', 'incoming', 'destination_amount', 'destination_currency')):
            account_id = transaction.get(field)
            account = accounts.get(account_id, {})
            if account.get('account_class') in ('asset', 'liability'):
                result.append((account_id, direction, transaction.get(currency_field) or transaction.get('currency'),
                               decimal(transaction.get(amount_field) or transaction.get('amount')), transaction.get('date')))
    except (ApiError, InvalidOperation):
        return []
    return result


def within_transfer_window(left, right):
    try:
        return abs((date.fromisoformat(left) - date.fromisoformat(right)).days) <= 30
    except (ValueError, TypeError):
        return False


def transfer_match(left, right, accounts):
    if left.get('type') == 'Transfer' or right.get('type') == 'Transfer':
        return False
    left_legs, right_legs = legs(left, accounts), legs(right, accounts)
    if len(left_legs) != 1 or len(right_legs) != 1:
        return False
    a, b = left_legs[0], right_legs[0]
    return (a[0] != b[0] and a[1] != b[1] and a[2:4] == b[2:4]
            and a[3] > 0 and within_transfer_window(a[4], b[4]))


def existing_candidate(record, accounts):
    if record.deleted or record.data.get('system') or record.data.get('type') not in ('Income', 'Expense'):
        return None
    bank_legs = legs(record.data, accounts)
    if len(bank_legs) != 1 or accounts[bank_legs[0][0]].get('closed_on'):
        return None
    leg = bank_legs[0]
    # Express an incoming entry in its financial account's currency/amount,
    # which may differ from its original income counterparty's currency.
    transaction = {key: deepcopy(value) for key, value in record.data.items() if key in FIELDS}
    transaction['notes'] = transaction.get('notes') or ''
    transaction.update(amount=format(leg[3], 'f'), currency=leg[2],
                       destination_amount=format(leg[3], 'f'), destination_currency=leg[2])
    return dict(id=record.id, revision=record.revision, transaction=transaction,
                sources=deepcopy(record.data.get('import_sources', [])))


def annotate(rows, accounts):
    records = [record for record in list_records('transaction') if not record.data.get('system')]
    existing = [(record.id, set(legs(record.data, accounts)), record.data.get('type') == 'Transfer') for record in records]
    candidates = [candidate for record in records if (candidate := existing_candidate(record, accounts))]
    seen = {}
    for row in rows:
        for leg in legs(row['transaction'], accounts):
            seen.setdefault(leg, []).append(row['id'])
    for row in rows:
        row['duplicate_ids'] = []
        row['transfer_candidates'] = []
        row['existing_transfer_candidates'] = [candidate for candidate in candidates
            if transfer_match(row['transaction'], candidate['transaction'], accounts)]
        for leg in legs(row['transaction'], accounts):
            row['duplicate_ids'].extend(identity for identity, values, completed_transfer in existing
                if identity != row.get('existing_transaction_id') and (leg in values or
                    (completed_transfer and any(leg[:4] == saved[:4] and within_transfer_window(leg[4], saved[4]) for saved in values))))
            row['duplicate_ids'].extend(identity for identity in seen.get(leg, []) if identity != row['id'])
        row['duplicate_ids'] = sorted(set(row['duplicate_ids']))
    for i, left in enumerate(rows):
        for right in rows[i + 1:]:
            if transfer_match(left['transaction'], right['transaction'], accounts):
                left['transfer_candidates'].append(right['id'])
                right['transfer_candidates'].append(left['id'])
    return rows


def validate_existing_transfer(transaction, record, accounts):
    candidate = existing_candidate(record, accounts)
    old_legs = legs(record.data, accounts)
    normalized = deepcopy(transaction)
    _validate_transaction(normalized)
    new_legs = legs(normalized, accounts)
    if (not candidate or transaction.get('type') != 'Transfer' or len(new_legs) != 2
            or any(leg[2:4] != old_legs[0][2:4] for leg in new_legs)
            or not any(leg[:4] == old_legs[0][:4] for leg in new_legs)
            or not within_transfer_window(transaction.get('date'), record.data.get('date'))):
        raise ApiError('The saved transaction no longer matches this transfer. Undo the match and analyze again.', 409, 'transfer_conflict')


def row_errors(transaction):
    try:
        if not transaction.get('currency'):
            return ['Choose a currency.']
        clean = deepcopy(transaction)
        _validate_transaction(clean)
        _validate_archived_transaction(clean, None)
        if clean.get('subscription_id'):
            if not clean.get('subscription_date'):
                return ['Choose a subscription occurrence date.']
            _link_subscription(clean, None)
        return []
    except (ApiError, ValueError, TypeError, KeyError) as exc:
        return [exc.message if isinstance(exc, ApiError) else 'Complete the date, accounts, and amounts.']


@bp.post('/transactions/import/preview')
@login_required
def preview():
    settings = configuration()
    accounts = {record.id: record.to_dict() for record in list_records('account')}
    categories = [record.to_dict() for record in list_records('category')]
    currencies = currency_definitions()
    context = {
        'accounts': [{key: value.get(key) for key in ('id', 'name', 'description', 'account_number', 'type', 'currency', 'account_class')} for value in accounts.values()],
        'categories': [{key: value.get(key, '') for key in ('id', 'name', 'description')} for value in categories],
        'rules': [record.to_dict() for record in list_records('rule') if record.data.get('enabled', True)],
        'currencies': [{key: value[key] for key in ('code', 'precision')} for value in currencies],
        'default_currency': 'USD',
    }
    if len(json.dumps(context)) > 64000 or len(accounts) > 254 or len(categories) > 254:
        raise ApiError('Account/category/rule context exceeds the processor limit. Reduce context before importing.', 413, 'ocr_context_limit')
    # No database transaction spans capability/extraction network requests.
    db.session.rollback()
    capabilities = capabilities_for(settings)
    limits = capabilities.get('limits', {})
    try:
        max_files = min(5, int(limits.get('files', 5)))
        max_bytes = min(MAX_UPLOAD, int(limits.get('upload_bytes', MAX_UPLOAD)))
    except (ValueError, TypeError):
        raise ApiError('OCR Processor returned invalid capabilities.', 502)
    uploads = request.files.getlist('documents')
    if not 1 <= len(uploads) <= max_files:
        raise ApiError(f'Choose between 1 and {max_files} documents.')
    files, names, total = [], [], 0
    for upload in uploads:
        payload = upload.read(max_bytes + 1)
        total += len(payload)
        if not payload or total > max_bytes:
            raise ApiError('Documents are empty or exceed the total upload limit.', 413)
        if not (payload.startswith((b'%PDF-', b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff', b'II*\x00', b'MM\x00*')) or
                (payload.startswith(b'RIFF') and payload[8:12] == b'WEBP') or valid_csv(payload)):
            raise ApiError('Choose PDF, CSV, PNG, JPEG, TIFF, or WebP documents.', 415)
        files.append(payload)
        names.append(safe_filename(upload.filename, 'Document'))
    body, content_type = multipart(files, context)
    if len(body) > max_bytes:
        raise ApiError('Documents plus account/category/rule context exceed the processor request limit.', 413)
    result = processor_call(settings, '/v1/transactions', body, content_type)
    extracted = result.get('data', {}).get('transactions') if isinstance(result.get('data'), dict) else None
    if not isinstance(extracted, list) or len(extracted) > 500:
        raise ApiError('OCR Processor returned invalid transactions or more than 500 rows. Use a smaller batch.', 502)
    category_ids = {item['id'] for item in categories}
    pages = result.get('pages')
    if not isinstance(pages, list) or any(not isinstance(p, dict) or type(p.get('document')) is not int or type(p.get('page')) is not int for p in pages):
        raise ApiError('OCR Processor returned malformed page references.', 502)
    page_refs = {(p['document'], p['page']) for p in pages}
    rows = []
    schema_fields = extraction_schema()['properties']['transactions']['items']['properties']
    for item in extracted:
        if not isinstance(item, dict) or set(item) != set(schema_fields) or any(
            item[key] is not None and not isinstance(item[key], str) for key in schema_fields if key not in ('document', 'page', 'is_refund')):
            raise ApiError('OCR Processor returned a malformed transaction.', 502)
        document, page = item['document'], item['page']
        if type(document) is not int or not 1 <= document <= len(names) or type(page) is not int or page < 1 or type(item['is_refund']) is not bool or item['direction'] not in (None, 'incoming', 'outgoing'):
            raise ApiError('OCR Processor returned invalid source references or direction.', 502)
        if (document, page) not in page_refs:
            raise ApiError('OCR Processor referenced a page absent from the extracted documents.', 502)
        own = match_account(item['account_id'], accounts) or match_account(item['account_number'], accounts)
        source = match_account(item['source_account_id'], accounts)
        destination = match_account(item['destination_account_id'], accounts)
        incoming = item['direction'] == 'incoming'
        # Preserve statement legs for explicit cross-document combination, even
        # when the model recognizes both endpoints of a mirrored payment.
        if own and item['direction'] is not None:
            source, destination = (None, own) if incoming else (own, None)
        financial = lambda identity: accounts.get(identity, {}).get('account_class') in ('asset', 'liability')
        if not source and not incoming:
            source = own
        if not destination and incoming:
            destination = own
        transfer = financial(source) and financial(destination)
        refund = item['is_refund'] and incoming and not transfer
        kind = 'Transfer' if transfer else '' if item['direction'] is None else 'Expense' if refund or not incoming else 'Income'
        if not transfer:
            if incoming:
                source = source or system_id('expense' if refund else 'income')
            else:
                destination = destination or system_id('expense')
        transaction = dict(type=kind, date=item['date'] or '', description=item['description'] or '',
            amount=item['amount'] or '', currency=item['currency'] or '', source_account_id=source or '',
            destination_account_id=destination or '', category_id=item['category_id'] if item['category_id'] in category_ids else None,
            is_refund=refund, tag_ids=[], notes='')
        # Resolve rules now, before the user edits. Approval never reapplies them.
        try:
            transaction = apply_transaction_rules(transaction)
            if transaction.get('subscription_id'):
                _link_subscription(transaction, None)
        except (ApiError, ValueError, TypeError, KeyError):
            pass
        rows.append(dict(id=str(uuid4()), transaction=transaction,
            sources=[dict(filename=names[document - 1], document=document, page=page)],
            warnings=[item['review_notes']] if item['review_notes'] else [], errors=row_errors(transaction)))
    annotate(rows, accounts)
    for row in rows:
        row['selected'] = not row['duplicate_ids']
    warnings = result.get('warnings', [])
    if not isinstance(warnings, list) or any(not isinstance(value, str) for value in warnings):
        raise ApiError('OCR Processor returned malformed warnings.', 502)
    return jsonify(batch_id=str(uuid4()), rows=rows, warnings=warnings)


@bp.post('/transactions/import/commit')
@login_required
def commit():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError('Provide an approval object.')
    batch_id = identifier(payload.get('batch_id'))
    rows = payload.get('rows')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
        raise ApiError('Select between 1 and 500 rows.')
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    receipt = db.session.get(Record, batch_id)
    if receipt:
        if receipt.kind != 'statement_import' or receipt.data.get('actor_id') != g.user.id or receipt.data.get('digest') != digest:
            raise ApiError('This batch was already approved with a different payload.', 409, 'import_conflict')
        return jsonify(receipt.data['result'])
    accounts = {record.id: record.to_dict() for record in list_records('account')}
    clean_rows = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('transaction'), dict):
            raise ApiError('Each row needs a transaction.')
        identity = identifier(row.get('id'))
        acknowledged = row.get('acknowledged_duplicates', [])
        if not isinstance(acknowledged, list) or len(acknowledged) > 1000 or any(not isinstance(value, str) for value in acknowledged):
            raise ApiError('Duplicate acknowledgments must be a list of identifiers.')
        sources = row.get('sources', [])
        if not isinstance(sources, list) or len(sources) > 10 or any(
            not isinstance(source, dict) or not isinstance(source.get('filename'), str) or len(source['filename']) > 255 or
            type(source.get('page')) is not int or source['page'] < 1 or type(source.get('document')) is not int or source['document'] < 1
            for source in sources):
            raise ApiError('Invalid source references.')
        transaction = {key: value for key, value in row['transaction'].items() if key in FIELDS}
        for field, value in transaction.items():
            if field in ('is_refund', 'investment_fee'):
                valid = type(value) is bool
            elif field == 'tag_ids':
                valid = isinstance(value, list) and all(isinstance(tag, str) for tag in value)
            else:
                valid = isinstance(value, str) or (field in ('category_id', 'subscription_id', 'subscription_date') and value is None)
            if not valid:
                raise ApiError(f'Invalid {field} in reviewed transaction.')
        existing = None
        if row.get('existing_transaction_id') is not None:
            existing_id = identifier(row['existing_transaction_id'])
            existing = get_record('transaction', existing_id)
            if type(row.get('existing_revision')) is not int or row['existing_revision'] != existing.revision:
                raise ApiError('The saved transaction changed since preview. Undo the match and analyze again.', 409,
                    'revision_conflict', {'row_id': identity, 'errors': ['The saved transaction changed since preview. Undo the match and analyze again.']})
            try:
                validate_existing_transfer(transaction, existing, accounts)
            except ApiError as exc:
                raise ApiError(exc.message, exc.status, exc.code, {'row_id': identity, 'errors': [exc.message]}) from None
        elif row.get('existing_revision') is not None:
            raise ApiError('A saved transaction ID is required with its revision.')
        errors = row_errors(transaction)
        if errors:
            raise ApiError('Correct the highlighted rows before approving.', 400, 'import_validation', {'row_id': identity, 'errors': errors})
        sources = [dict(filename=safe_filename(source['filename'], 'Document'), document=source['document'], page=source['page']) for source in sources]
        clean_rows.append(dict(id=identity, transaction=transaction, sources=sources,
            existing_transaction_id=existing.id if existing else None, existing_revision=existing.revision if existing else None))
    if len({row['id'] for row in clean_rows}) != len(clean_rows):
        raise ApiError('Duplicate row identifiers.')
    updated_ids = [row['existing_transaction_id'] for row in clean_rows if row['existing_transaction_id']]
    if len(set(updated_ids)) != len(updated_ids):
        raise ApiError('A saved transaction can only be matched once in a batch.', 409, 'transfer_conflict')
    annotate(clean_rows, accounts)
    conflicts = [dict(row_id=row['id'], duplicate_ids=row['duplicate_ids']) for row in clean_rows
                 if set(row['duplicate_ids']) - set(next((r.get('acknowledged_duplicates', []) for r in rows if r['id'] == row['id']), []))]
    if conflicts:
        raise ApiError('Possible duplicates need explicit approval.', 409, 'import_duplicates', {'rows': conflicts})
    created, updated = [], []
    for row in clean_rows:
        existing_id = row['existing_transaction_id']
        if existing_id:
            original_sources = get_record('transaction', existing_id).data.get('import_sources', [])
            row['sources'] = list({json.dumps(source, sort_keys=True): source
                                  for source in original_sources + row['sources']}.values())
        transaction = {**row['transaction'], 'import_sources': row['sources'], 'import_batch_id': batch_id}
        try:
            record = save_record('transaction', transaction, g.user, record_id=existing_id,
                expected_revision=row['existing_revision'], origin='statement-import', apply_rules=False)
        except ApiError as exc:
            raise ApiError('An approved row could not be saved. Review the highlighted row.', exc.status,
                           'import_validation', {'row_id': row['id'], 'errors': [exc.message]}) from None
        (updated if existing_id else created).append(record.id)
    result = dict(transaction_ids=created + updated, total=len(created) + len(updated),
                  created_count=len(created), updated_count=len(updated), updated_transaction_ids=updated)
    db.session.add(Record(id=batch_id, kind='statement_import', data=dict(actor_id=g.user.id, digest=digest, result=result)))
    db.session.commit()
    return jsonify(result)
