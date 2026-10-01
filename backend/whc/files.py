"""Authenticated, immutable receipt storage. Paths never come from clients."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from uuid import UUID, uuid4

from flask import Blueprint, current_app, g, jsonify, request, send_file

from .auth import login_required
from .models import db, Record
from .ledger import get_record, save_record, add_audit

bp = Blueprint('files', __name__)
MAX_FILE = 20 * 1024 * 1024


def identify(payload):
    if payload.startswith(b'%PDF-'):
        return '.pdf', 'application/pdf'
    if payload.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png', 'image/png'
    if payload.startswith(b'\xff\xd8\xff'):
        return '.jpg', 'image/jpeg'
    if payload.startswith(b'RIFF') and payload[8:12] == b'WEBP':
        return '.webp', 'image/webp'
    raise ValueError('Upload a PDF, PNG, JPEG, or WebP file.')


def attachment_path(relative):
    if not isinstance(relative, str) or not re.fullmatch(r'[0-9a-f-]{36}\.(?:pdf|png|jpg|webp)', relative):
        raise ValueError('Invalid attachment path.')
    if str(UUID(relative.rsplit('.', 1)[0])) != relative.rsplit('.', 1)[0]:
        raise ValueError('Invalid attachment path.')
    root = Path(current_app.config['UPLOAD_DIR']).resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError('Invalid attachment path.')
    return candidate


def safe_filename(value, fallback):
    name = str(value or fallback).replace('\\', '/').rsplit('/', 1)[-1]
    name = ''.join(character for character in name if ord(character) >= 32 and ord(character) != 127)
    return name[:255] if name and name not in ('.', '..') else fallback


@bp.post('/attachments')
@login_required
def upload():
    file = request.files.get('file')
    if not file:
        raise ValueError('Select a file to upload.')
    transaction = get_record('transaction', request.form.get('transaction_id', ''))
    if transaction.data.get('system'):
        raise ValueError('Attachments belong to ordinary transactions.')
    payload = file.read(MAX_FILE + 1)
    if len(payload) > MAX_FILE:
        raise ValueError('Each attachment must be 20 MiB or smaller.')
    suffix, mime = identify(payload)
    kind = request.form.get('kind', 'receipt')
    if kind not in ('receipt', 'item'):
        raise ValueError('Attachment kind must be receipt or item.')
    identity = str(uuid4())
    relative = identity + suffix
    target = attachment_path(relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('xb') as output:
        output.write(payload)
    # Files are immutable; a failed commit removes only this newly-created file.
    record = Record(id=identity, kind='attachment', data=dict(transaction_id=transaction.id, path=relative, filename=safe_filename(file.filename, relative), mime_type=mime, size=len(payload), sha256=hashlib.sha256(payload).hexdigest(), kind=kind))
    try:
        db.session.add(record)
        db.session.flush()
        add_audit('attachment', record.id, 'create', g.user, 'manual', None, record.to_dict())
        db.session.commit()
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return jsonify(record.to_dict()), 201


@bp.get('/attachments')
@login_required
def attachments():
    query = db.select(Record).where(Record.kind == 'attachment', Record.deleted.is_(False))
    rows = db.session.scalars(query).all()
    if request.args.get('transaction_id'):
        rows = [r for r in rows if r.data.get('transaction_id') == request.args['transaction_id']]
    return jsonify(items=[r.to_dict() for r in rows], total=len(rows))


@bp.get('/attachments/<identity>')
@login_required
def download(identity):
    record = get_record('attachment', identity)
    path = attachment_path(record.data['path'])
    if not path.is_file():
        return jsonify(error='Attachment file is missing. Restore it from a backup.'), 404
    response = send_file(path, mimetype=record.data['mime_type'], as_attachment=True, download_name=record.data['filename'])
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'private, no-store'
    return response


@bp.delete('/attachments/<identity>')
@login_required
def remove(identity):
    record = get_record('attachment', identity)
    revision = (request.get_json(silent=True) or {}).get('revision')
    if revision != record.revision:
        return jsonify(error='This attachment changed. Refresh before removing it.'), 409
    before = record.to_dict()
    record.deleted = True
    record.revision += 1
    from .models import utcnow
    record.last_modified = utcnow()
    add_audit('attachment', record.id, 'void', g.user, 'manual', before, record.to_dict())
    db.session.commit()
    return jsonify(record.to_dict())
