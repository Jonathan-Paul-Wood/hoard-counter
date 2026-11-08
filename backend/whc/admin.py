"""Hoard administration and audit views."""
from __future__ import annotations

import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, current_app, jsonify, request, g
from werkzeug.security import generate_password_hash

from .auth import login_required, admin_required
from .models import db, Record, User, AuditEvent
from .ledger import add_audit

bp = Blueprint('admin', __name__)


@bp.get('/openapi.json')
@login_required
def openapi_contract():
    from .contract import specification
    return jsonify(specification())


def user_dict(user):
    return dict(id=user.id, username=user.username, role=user.role, active=getattr(user, 'active', True))


@bp.get('/users')
@admin_required
def users():
    rows = db.session.scalars(db.select(User)).all()
    return jsonify(items=[user_dict(u) for u in rows], total=len(rows))


def validate_user(data):
    if not isinstance(data, dict):
        raise ValueError('Provide a user object.')
    if not isinstance(data.get('username'), str) or not 2 <= len(data['username'].strip()) <= 80:
        raise ValueError('Username must contain 2-80 characters.')
    if data.get('role', 'member') not in ('member', 'admin'):
        raise ValueError('Role must be member or admin.')
    if data.get('password') is not None and (not isinstance(data['password'], str) or not 12 <= len(data['password']) <= 256):
        raise ValueError('Passwords must contain 12-256 characters.')
    if 'active' in data and not isinstance(data['active'], bool):
        raise ValueError('Active must be true or false.')


@bp.post('/users')
@admin_required
def create_user():
    data = request.get_json()
    validate_user(data)
    if not data.get('password'):
        raise ValueError('A password is required.')
    name = data['username'].strip()
    if db.session.scalar(db.select(User).where(User.username == name)):
        raise ValueError('That username is already in use.')
    user = User(username=name, role=data.get('role', 'member'), active=data.get('active', True), password_hash=generate_password_hash(data['password']))
    db.session.add(user)
    db.session.flush()
    add_audit('user', user.id, 'create', g.user, 'manual', None, user_dict(user))
    db.session.commit()
    return jsonify(user_dict(user)), 201


@bp.patch('/users/<identity>')
@admin_required
def update_user(identity):
    user = db.session.get(User, identity)
    if not user:
        return jsonify(error='User not found.'), 404
    body = request.get_json()
    if not isinstance(body, dict):
        raise ValueError('Provide a user object.')
    data = {**user_dict(user), **body}
    validate_user(data)
    if user.id == g.user.id and (data.get('role') != 'admin' or not data.get('active', True)):
        raise ValueError('You cannot remove your own administrator access.')
    duplicate = db.session.scalar(db.select(User).where(User.username == data['username'].strip(), User.id != identity))
    if duplicate:
        raise ValueError('That username is already in use.')
    before = user_dict(user)
    user.username = data['username'].strip()
    user.role = data['role']
    if hasattr(user, 'active'):
        user.active = bool(data.get('active', True))
    if body.get('password'):
        user.password_hash = generate_password_hash(body['password'])
    add_audit('user', user.id, 'update', g.user, 'manual', before, user_dict(user))
    db.session.commit()
    return jsonify(user_dict(user))


@bp.delete('/users/<identity>')
@admin_required
def deactivate_user(identity):
    user = db.session.get(User, identity)
    if not user:
        return jsonify(error='User not found.'), 404
    if user.id == g.user.id:
        raise ValueError('You cannot deactivate your own administrator account.')
    before = user_dict(user)
    user.active = False
    add_audit('user', user.id, 'deactivate', g.user, 'manual', before, user_dict(user))
    db.session.commit()
    return jsonify(user_dict(user))


@bp.get('/audit')
@login_required
def audit():
    page = max(1, int(request.args.get('page', 1)))
    size = min(200, max(1, int(request.args.get('page_size', 50))))
    query = db.select(AuditEvent)
    if request.args.get('record_id'):
        query = query.where(AuditEvent.record_id == request.args['record_id'])
    if request.args.get('kind'):
        query = query.where(AuditEvent.kind == request.args['kind'])
    count = db.session.scalar(db.select(db.func.count()).select_from(query.subquery()))
    rows = db.session.scalars(query.order_by(AuditEvent.created_at.desc()).offset((page - 1) * size).limit(size)).all()
    return jsonify(items=[r.to_dict() for r in rows], total=count, page=page, page_size=size)


def settings_dict():
    record = db.session.scalar(db.select(Record).where(Record.kind == 'setting', Record.deleted.is_(False)))
    return {**dict(household_name='Our hoard', timezone=current_app.config.get('HOUSEHOLD_TIMEZONE', 'America/New_York')), **(record.data if record else {}), 'default_currency': 'USD', 'version': os.environ.get('APP_VERSION', '1.0.0')}


@bp.get('/settings')
@login_required
def get_settings():
    return jsonify(settings_dict())


@bp.patch('/settings')
@admin_required
def update_settings():
    body = request.get_json()
    if not isinstance(body, dict):
        raise ValueError('Provide a settings object.')
    if any(k not in ('household_name', 'timezone') for k in body):
        raise ValueError('Only hoard name and timezone can be updated.')
    data = {**settings_dict(), **body}
    if not isinstance(data['household_name'], str) or not data['household_name'].strip() or len(data['household_name']) > 100:
        raise ValueError('Enter a hoard name of at most 100 characters.')
    data['household_name'] = data['household_name'].strip()
    if not isinstance(data['timezone'], str):
        raise ValueError('Select a valid IANA timezone.')
    try:
        ZoneInfo(data['timezone'])
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Select a valid IANA timezone.')
    record = db.session.scalar(db.select(Record).where(Record.kind == 'setting', Record.deleted.is_(False)))
    before = record.to_dict() if record else None
    if not record:
        record = Record(kind='setting', data=data)
        db.session.add(record)
    else:
        record.data = data
        record.revision += 1
        from .models import utcnow
        record.last_modified = utcnow()
    db.session.flush()
    add_audit('setting', record.id, 'update', g.user, 'manual', before, record.to_dict())
    db.session.commit()
    current_app.config['HOUSEHOLD_TIMEZONE'] = data['timezone']
    return jsonify(data)


@bp.get('/backups/status')
@admin_required
def backup_status():
    root = Path(current_app.config['BACKUP_DIR'])
    result = dict(last_success=None, last_error=None, destination=str(root), schedule='Sunday 03:00', timezone=current_app.config.get('HOUSEHOLD_TIMEZONE', 'America/New_York'))
    for path in (root / 'status.json', root / 'backup-status.json', Path(current_app.config['DATA_DIR']) / 'backup-status.json'):
        if path.exists():
            try:
                result.update(json.loads(path.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                result['last_error'] = 'Backup status could not be read.'
            break
    return jsonify(result)
