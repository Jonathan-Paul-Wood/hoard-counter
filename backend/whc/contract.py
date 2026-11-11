"""Versioned public interface; also the source for generated TypeScript types."""
from copy import deepcopy


def string(**extra):
    return {'type': 'string', **extra}


def ref(name):
    return {'$ref': '#/components/schemas/' + name}


def obj(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required)}


MONEY = string(pattern=r'^-?\d+(\.\d+)?$', description='Exact decimal string. Never send a JSON floating-point number.')
ID = string(format='uuid')
DAY = string(format='date')
META = {'id': ID, 'revision': {'type': 'integer', 'minimum': 1}, 'last_modified': string(format='date-time'), 'deleted': {'type': 'boolean'}}
SCHEMAS = {
    'EntityMeta': obj(META, ['id', 'revision', 'last_modified']),
    'Account': obj({**META, 'name': string(), 'type': string(), 'account_class': string(enum=['asset', 'liability', 'income', 'expense', 'equity']), 'currency': string(), 'opening_balance': MONEY, 'opening_date': DAY, 'opening_fx_rate': MONEY, 'balance': MONEY, 'balance_usd': {'anyOf': [MONEY, {'type': 'null'}]}, 'signed_balance': MONEY, 'signed_balance_usd': {'anyOf': [MONEY, {'type': 'null'}]}, 'as_of': DAY, 'valuation_date': {'anyOf': [DAY, {'type': 'null'}]}, 'missing_rate': {'type': 'boolean'}}, ['id', 'revision', 'last_modified', 'name', 'type', 'account_class', 'currency', 'balance']),
    'Transaction': obj({**META, 'source_account_id': ID, 'destination_account_id': ID, 'date': DAY, 'amount': MONEY, 'destination_amount': MONEY, 'description': string(), 'type': string(enum=['Income', 'Expense', 'Transfer', 'Return']), 'currency': string(), 'usd_amount': MONEY, 'fx_rate': MONEY, 'destination_fx_rate': MONEY, 'category_id': {'anyOf': [ID, {'type': 'null'}]}, 'tag_ids': {'type': 'array', 'items': ID}, 'notes': string(), 'subscription_id': {'anyOf': [ID, {'type': 'null'}]}, 'subscription_date': {'anyOf': [DAY, {'type': 'null'}]}, 'is_refund': {'type': 'boolean'}, 'investment_fee': {'type': 'boolean'}}, ['id', 'revision', 'last_modified', 'source_account_id', 'destination_account_id', 'date', 'amount', 'description', 'type']),
    'Category': obj({**META, 'name': string()}, ['id', 'revision', 'last_modified', 'name']),
    'Tag': obj({**META, 'name': string()}, ['id', 'revision', 'last_modified', 'name']),
    'Subscription': obj({**META, 'name': string(), 'type': string(), 'source_account_id': ID, 'destination_account_id': ID, 'amount': MONEY, 'currency': string(), 'start_date': DAY, 'end_date': {'anyOf': [DAY, {'type': 'null'}]}, 'frequency': string(enum=['daily', 'weekly', 'monthly', 'yearly']), 'interval': {'type': 'integer', 'minimum': 1}, 'category_id': {'anyOf': [ID, {'type': 'null'}]}, 'amount_tolerance': MONEY, 'day_tolerance': {'type': 'integer'}, 'active': {'type': 'boolean'}}, ['id', 'revision', 'last_modified', 'name', 'source_account_id', 'destination_account_id', 'amount', 'start_date', 'frequency']),
    'RuleCondition': obj({'field': string(), 'operator': string(enum=['eq', 'neq', 'contains', 'gte', 'lte']), 'value': {}}, ['field', 'operator', 'value']),
    'Rule': obj({**META, 'name': string(), 'priority': {'type': 'integer'}, 'enabled': {'type': 'boolean'}, 'conditions': {'type': 'array', 'items': ref('RuleCondition')}, 'actions': obj({'category_id': ID, 'tag_ids': {'type': 'array', 'items': ID}, 'notes': string(), 'subscription_id': ID})}, ['id', 'revision', 'last_modified', 'name', 'conditions', 'actions']),
    'Budget': obj({**META, 'month': string(pattern=r'^\d{4}-\d{2}$'), 'category_id': ID, 'amount': MONEY}, ['id', 'revision', 'last_modified', 'month', 'category_id', 'amount']),
    'Rate': obj({**META, 'currency': string(), 'date': DAY, 'rate': MONEY}, ['id', 'revision', 'last_modified', 'currency', 'date', 'rate']),
    'Valuation': obj({**META, 'account_id': ID, 'date': DAY, 'amount': MONEY, 'fx_rate': MONEY, 'usd_amount': MONEY}, ['id', 'revision', 'last_modified', 'account_id', 'date', 'amount']),
    'Scenario': obj({**META, 'name': string(), 'date': DAY, 'assumptions': {'type': 'array', 'items': ref('ProjectionAssumption')}, 'flows': {'type': 'array', 'items': ref('PlannedFlow')}}, ['id', 'revision', 'last_modified', 'name']),
    'Currency': obj({**META, 'code': string(pattern='^[A-Z]{3}$'), 'name': string(), 'precision': {'type': 'integer', 'minimum': 0, 'maximum': 18}}, ['id', 'revision', 'last_modified', 'code', 'name', 'precision']),
    'ProjectionAssumption': obj({'account_id': ID, 'mode': string(enum=['manual', 'historical']), 'annual_rate': MONEY}, ['account_id', 'mode']),
    'PlannedFlow': obj({'account_id': ID, 'date': DAY, 'amount': MONEY}, ['account_id', 'date', 'amount']),
    'ProjectionRequest': obj({'date': DAY, 'assumptions': {'type': 'array', 'items': ref('ProjectionAssumption')}, 'flows': {'type': 'array', 'items': ref('PlannedFlow')}}, ['date']),
    'User': obj({'id': ID, 'username': string(), 'role': string(enum=['admin', 'member']), 'active': {'type': 'boolean'}}, ['id', 'username', 'role']),
    'Session': obj({'user': {'anyOf': [ref('User'), {'type': 'null'}]}, 'csrf_token': string(), 'setup_required': {'type': 'boolean'}}, ['user', 'csrf_token', 'setup_required']),
    'Credentials': obj({'username': string(), 'password': string(minLength=12)}, ['username', 'password']),
    'Error': obj({'error': obj({'code': string(), 'message': string(), 'details': {}}), 'message': string()}),
    'Summary': obj({'income': MONEY, 'expenses': MONEY, 'returns': MONEY, 'net': MONEY}, ['income', 'expenses', 'returns', 'net']),
    'Attachment': obj({**META, 'transaction_id': ID, 'filename': string(), 'mime_type': string(), 'kind': string(enum=['receipt', 'item']), 'size': {'type': 'integer'}, 'sha256': string()}, ['id', 'revision', 'last_modified', 'transaction_id', 'filename', 'kind']),
}

# Administrative and action interfaces are intentionally distinct from revisioned records.
SCHEMAS.update({
    'LoginCredentials': obj({'username': string(), 'password': string(maxLength=256)}, ['username', 'password']),
    'AuthResult': obj({'user': ref('User'), 'csrf_token': string()}, ['user', 'csrf_token']),
    'UserInput': obj({'username': string(minLength=2, maxLength=80), 'password': string(minLength=12, maxLength=256), 'role': string(enum=['admin', 'member'], default='member')}, ['username', 'password']),
    'UserPatch': obj({'username': string(minLength=2, maxLength=80), 'password': string(minLength=12, maxLength=256), 'role': string(enum=['admin', 'member']), 'active': {'type': 'boolean'}}),
    'Settings': obj({'household_name': string(), 'timezone': string(), 'default_currency': string(enum=['USD']), 'version': string()}, ['household_name', 'timezone', 'default_currency', 'version']),
    'SettingsPatch': {**obj({'household_name': string(minLength=1, maxLength=100), 'timezone': string(description='IANA timezone, for example America/New_York.')}), 'additionalProperties': False},
    'CurrencyDefinition': obj({'id': string(description='Built-in three-letter code or custom-record UUID.'), 'code': string(), 'name': string(), 'precision': {'type': 'integer', 'minimum': 0, 'maximum': 18}, 'builtin': {'type': 'boolean'}, 'revision': {'type': 'integer'}, 'last_modified': string(format='date-time'), 'deleted': {'type': 'boolean'}}, ['id', 'code', 'name', 'precision', 'builtin']),
    'RevisionInput': obj({'revision': {'type': 'integer', 'minimum': 1}}, ['revision']),
    'ReconciliationInput': obj({'balance': MONEY, 'date': DAY, 'revision': {'type': 'integer', 'minimum': 1}}, ['balance', 'date', 'revision']),
    'SystemJournal': obj({**META, 'system': string(enum=['opening', 'valuation', 'reconciliation']), 'account_id': ID, 'date': DAY, 'description': string(), 'amount': MONEY, 'usd_amount': MONEY, 'fx_rate': MONEY, 'valuation_id': ID}, ['id', 'revision', 'last_modified', 'system', 'account_id', 'date', 'amount']),
    'Journal': {'anyOf': [ref('Transaction'), ref('SystemJournal')]},
    'ReconciliationResult': obj({'account': ref('Account'), 'adjustment': ref('SystemJournal')}, ['account', 'adjustment']),
    'RulePreviewInput': obj({'transaction': {'type': 'object', 'description': 'Transaction fields to evaluate without saving; partial examples are accepted.'}}),
    'RulePreviewResult': obj({'before': {'type': 'object'}, 'after': {'type': 'object'}}, ['before', 'after']),
    'RuleReapplyInput': obj({'transaction_ids': {'type': 'array', 'items': ID, 'minItems': 1, 'maxItems': 500}}, ['transaction_ids']),
    'SubscriptionOccurrence': obj({'date': DAY, 'transaction_id': {'anyOf': [ID, {'type': 'null'}]}, 'fulfilled': {'type': 'boolean'}}, ['date', 'transaction_id', 'fulfilled']),
    'AuditEvent': obj({'id': ID, 'record_id': {'anyOf': [ID, {'type': 'null'}]}, 'kind': string(), 'action': string(), 'actor_id': {'anyOf': [ID, {'type': 'null'}]}, 'origin': string(), 'before': {}, 'after': {}, 'created_at': string(format='date-time')}, ['id', 'kind', 'action', 'origin', 'created_at']),
    'BackupStatus': obj({'last_success': {'anyOf': [string(format='date-time'), {'type': 'null'}]}, 'last_error': {'anyOf': [string(), {'type': 'null'}]}, 'destination': string(), 'schedule': string(), 'timezone': string(), 'status': string(enum=['ok', 'error']), 'last_attempt': string(format='date-time'), 'archive': string()}, ['last_success', 'last_error', 'destination', 'schedule', 'timezone']),
    'ImportSummary': obj({key: {'type': 'integer', 'minimum': 0} for key in ('create', 'update', 'skip', 'conflict')}),
    'ImportChange': obj({'kind': string(), 'id': ID, 'action': string(enum=['create', 'update', 'skip', 'conflict']), 'description': string(), 'line': {'anyOf': [{'type': 'integer'}, {'type': 'null'}]}, 'after': {'type': 'object'}, 'message': string()}, ['kind', 'id', 'action']),
    'ImportPreview': obj({'token': ID, 'summary': ref('ImportSummary'), 'changes': {'type': 'array', 'items': ref('ImportChange')}, 'conflicts': {'type': 'array', 'items': ref('ImportChange')}, 'errors': {'type': 'array', 'items': string()}}, ['token', 'summary', 'changes', 'conflicts', 'errors']),
    'ImportResult': obj({'summary': ref('ImportSummary'), 'changes': {'type': 'array', 'items': ref('ImportChange')}}, ['summary', 'changes']),
})
SCHEMAS['Credentials']['properties']['username'] = string(minLength=1, maxLength=80)
SCHEMAS['Credentials']['properties']['password']['maxLength'] = 256
SCHEMAS['User']['properties']['created_at'] = string(format='date-time')
SCHEMAS['User']['required'].append('active')
SCHEMAS['Rule']['properties']['match'] = string(enum=['all', 'any'], default='all')
for field in ('category_id', 'subscription_id'):
    SCHEMAS['Rule']['properties']['actions']['properties'][field] = {'anyOf': [ID, {'type': 'null'}]}
SCHEMAS['Transaction']['properties']['notes'] = {'anyOf': [string(maxLength=10000), {'type': 'null'}]}
SCHEMAS['Attachment']['properties']['path'] = string(description='Private immutable UUID filename; download through the authenticated attachment endpoint.')
SCHEMAS['Error']['properties']['error'] = {'anyOf': [string(), obj({'code': string(), 'message': string(), 'details': {}})]}


def specification():
    schemas = deepcopy(SCHEMAS)
    paths = {}
    errors = {str(code): {'description': description, 'content': {'application/json': {'schema': ref('Error')}}} for code, description in [(400, 'Invalid input'), (401, 'Sign in required'), (403, 'Not authorized or CSRF check failed'), (404, 'Record not found'), (409, 'Revision or import conflict'), (413, 'Request body exceeds upload limit'), (422, 'Required exchange rate missing'), (428, 'Editing revision is required'), (503, 'Maintenance or database unavailable')]}
    def operation(summary, schema=None, request_schema=None, method='get', status=None):
        value = {'summary': summary, 'responses': {status or ('201' if method == 'post' and summary.startswith('Create ') else '200'): {'description': 'Success', 'content': {'application/json': {'schema': schema or {'type': 'object'}}}}, **deepcopy(errors)}}
        if request_schema:
            value['requestBody'] = {'required': True, 'content': {'application/json': {'schema': request_schema}}}
        if method != 'get':
            value['parameters'] = [{'in': 'header', 'name': 'X-CSRF-Token', 'required': True, 'schema': string()}]
        return value
    for plural, name in [('accounts', 'Account'), ('transactions', 'Transaction'), ('categories', 'Category'), ('tags', 'Tag'), ('subscriptions', 'Subscription'), ('rules', 'Rule'), ('budgets', 'Budget'), ('rates', 'Rate'), ('valuations', 'Valuation'), ('scenarios', 'Scenario'), ('currencies', 'Currency')]:
        writable = deepcopy(schemas[name])
        for key in ('id', 'revision', 'last_modified', 'deleted', 'balance', 'balance_usd', 'signed_balance', 'signed_balance_usd', 'as_of', 'valuation_date', 'missing_rate', 'usd_amount', 'opening_fx_rate', 'fx_rate', 'destination_fx_rate'):
            writable['properties'].pop(key, None)
        writable['required'] = [key for key in writable['required'] if key in writable['properties']]
        if name == 'Account':
            writable['required'] = ['name', 'type']
        elif name == 'Subscription':
            writable['required'] = ['name', 'source_account_id', 'destination_account_id', 'amount', 'start_date']
        elif name == 'Transaction':
            writable['properties'].pop('currency', None)
        schemas[name + 'Input'] = writable
        item_schema = ref('CurrencyDefinition' if name == 'Currency' else 'Journal' if name == 'Transaction' else name)
        paths['/' + plural] = {'get': operation('List ' + plural, obj({'items': {'type': 'array', 'items': item_schema}, 'total': {'type': 'integer'}, 'page': {'type': 'integer'}, 'page_size': {'type': 'integer'}}, ['items', 'total', 'page', 'page_size'])), 'post': operation('Create ' + name.lower(), ref(name), ref(name + 'Input'), 'post')}
        paths['/' + plural]['get']['parameters'] = [{'name': k, 'in': 'query', 'schema': {'type': 'integer'} if k in ('page', 'page_size') else string()} for k in ('page', 'page_size', 'q', 'start', 'end', 'account_id', 'category_id', 'tag_id', 'currency', 'type', 'subscription_id', 'month')]
        patch = deepcopy(writable)
        patch['properties']['revision'] = {'type': 'integer'}
        patch['required'] = ['revision']
        paths['/' + plural + '/{id}'] = {'parameters': [{'name': 'id', 'in': 'path', 'required': True, 'schema': ID}], 'get': operation('Read ' + name.lower(), ref('Journal') if name == 'Transaction' else ref(name)), 'patch': operation('Edit ' + name.lower(), ref(name), patch, 'patch'), 'delete': operation('Void ' + name.lower(), ref(name), ref('RevisionInput'), 'delete')}
        if name == 'Account':
            paths['/' + plural + '/{id}']['delete']['responses']['200']['content']['application/json']['schema'] = ref('EntityMeta')
        if name == 'Currency':
            paths['/' + plural]['get']['parameters'] = []
            for method in ('post',):
                paths['/' + plural][method]['description'] = 'Administrator access is required. Built-in currency definitions are read-only.'
            for method in ('patch', 'delete'):
                paths['/' + plural + '/{id}'][method]['description'] = 'Administrator access is required. Built-in currency definitions are read-only.'
    paths['/transactions']['get']['parameters'].append({'name': 'include_system', 'in': 'query', 'schema': {'type': 'boolean', 'default': False}, 'description': 'Include opening, valuation, and reconciliation journals.'})
    paths['/auth/session'] = {'get': operation('Read session and CSRF token', ref('Session'))}
    paths['/auth/session']['get']['security'] = []
    for name in ('setup', 'login'):
        op = operation(name.title(), ref('AuthResult'), ref('Credentials' if name == 'setup' else 'LoginCredentials'), 'post', status='201' if name == 'setup' else '200')
        op['security'] = []
        op.pop('parameters', None)
        paths['/auth/' + name] = {'post': op}
    paths['/auth/logout'] = {'post': operation('Sign out', obj({'message': string(), 'csrf_token': string()}, ['message', 'csrf_token']), method='post')}
    for route in ('meta', 'dashboard', 'budget-summary', 'forecast', 'performance', 'reports/data', 'audit', 'settings', 'users', 'backups/status', 'attachments'):
        paths['/' + route] = {'get': operation('Read ' + route)}
    for route, schema in [('budgets/copy', obj({'source_month': string(), 'target_month': string()}, ['source_month', 'target_month'])), ('projections', ref('ProjectionRequest')), ('imports/commit', obj({'token': ID}, ['token']))]:
        paths['/' + route] = {'post': operation(route, request_schema=schema, method='post')}
    for route, mime in [('reports/csv', 'text/csv'), ('reports/pdf', 'application/pdf'), ('exports', 'application/zip')]:
        paths['/' + route] = {'get': {'summary': 'Download ' + route, 'responses': {'200': {'description': 'File download', 'content': {mime: {'schema': string(format='binary')}}}}, 'parameters': [{'name': key, 'in': 'query', 'schema': string()} for key in ('start', 'end', 'account_id', 'type', 'category_id', 'tag_id', 'subscription_id', 'currency', 'account_type', 'format')]}}
    for route, properties in [('imports/preview', {'file': string(format='binary'), 'kind': string(enum=['accounts', 'transactions', 'archive', 'firefly'])}), ('attachments', {'file': string(format='binary'), 'transaction_id': ID, 'kind': string(enum=['receipt', 'item'])})]:
        op = operation(route, ref('ImportPreview' if route == 'imports/preview' else 'Attachment'), method='post', status='200' if route == 'imports/preview' else '201')
        op['requestBody'] = {'required': True, 'content': {'multipart/form-data': {'schema': obj(properties, ['file'] if route == 'imports/preview' else ['file', 'transaction_id'])}}}
        paths.setdefault('/' + route, {})['post'] = op

    def listing(name, paginated=False):
        properties = {'items': {'type': 'array', 'items': ref(name)}, 'total': {'type': 'integer'}}
        if paginated:
            properties.update(page={'type': 'integer'}, page_size={'type': 'integer'})
        return obj(properties, properties)

    def query(name, schema=None, required=False):
        return {'name': name, 'in': 'query', 'required': required, 'schema': schema or string()}

    id_parameter = {'name': 'id', 'in': 'path', 'required': True, 'schema': ID}
    paths['/users'] = {
        'get': operation('List hoard users (administrator)', listing('User')),
        'post': operation('Create hoard user (administrator)', ref('User'), ref('UserInput'), 'post'),
    }
    paths['/users/{id}'] = {'parameters': [id_parameter],
        'patch': operation('Edit hoard user (administrator)', ref('User'), ref('UserPatch'), 'patch'),
        'delete': operation('Deactivate hoard user (administrator)', ref('User'), method='delete'),
    }
    paths['/users/{id}']['delete']['description'] = 'Marks active=false without deleting audit history. Administrators cannot deactivate themselves. No revision or request body is required.'
    paths['/settings'] = {'get': operation('Read hoard settings', ref('Settings')),
                          'patch': operation('Edit hoard settings (administrator)', ref('Settings'), ref('SettingsPatch'), 'patch')}
    paths['/attachments']['get'] = operation('List attachments', listing('Attachment'))
    paths['/attachments']['get']['parameters'] = [query('transaction_id', ID)]
    download = operation('Download private attachment')
    download['responses']['200'] = {'description': 'Attachment download', 'content': {mime: {'schema': string(format='binary')} for mime in ('application/pdf', 'image/png', 'image/jpeg', 'image/webp')}}
    paths['/attachments/{id}'] = {'parameters': [id_parameter], 'get': download,
                                 'delete': operation('Void attachment', ref('Attachment'), ref('RevisionInput'), 'delete')}
    paths['/rules/{id}/preview'] = {'parameters': [id_parameter], 'post': operation('Preview rule without saving', ref('RulePreviewResult'), ref('RulePreviewInput'), 'post')}
    paths['/rules/{id}/reapply'] = {'parameters': [id_parameter], 'post': operation('Reapply rule to selected transactions', listing('Transaction'), ref('RuleReapplyInput'), 'post')}
    paths['/accounts/{id}/reconcile'] = {'parameters': [id_parameter], 'post': operation('Record balance reconciliation', ref('ReconciliationResult'), ref('ReconciliationInput'), 'post')}
    occurrences = operation('List expected subscription occurrences', obj({'items': {'type': 'array', 'items': ref('SubscriptionOccurrence')}}, ['items']))
    occurrences['parameters'] = [query('start', DAY), query('end', DAY)]
    paths['/subscriptions/{id}/occurrences'] = {'parameters': [id_parameter], 'get': occurrences}
    paths['/imports/commit'] = {'post': operation('Commit a validated import preview', ref('ImportResult'), obj({'token': ID}, ['token']), 'post')}
    paths['/imports/preview']['post']['description'] = 'Preview is atomic and does not save imported records. Inspect errors and conflicts before committing the token; previews expire after 30 minutes and reject ledger changes since preview.'
    paths['/backups/status'] = {'get': operation('Read backup status (administrator)', ref('BackupStatus'))}
    paths['/audit'] = {'get': operation('Read audit history', listing('AuditEvent', paginated=True))}
    paths['/audit']['get']['parameters'] = [query('page', {'type': 'integer', 'minimum': 1}), query('page_size', {'type': 'integer', 'minimum': 1, 'maximum': 200}), query('record_id', ID), query('kind')]
    paths['/meta'] = {'get': operation('Read available account types, currencies, and hoard defaults', obj({'account_types': {'type': 'array', 'items': string()}, 'investment_account_types': {'type': 'array', 'items': string()}, 'transaction_types': {'type': 'array', 'items': string()}, 'account_classes': {'type': 'array', 'items': string()}, 'currencies': {'type': 'array', 'items': ref('CurrencyDefinition')}, 'timezone': string(), 'default_currency': string(enum=['USD'])}, ['account_types', 'investment_account_types', 'transaction_types', 'account_classes', 'currencies', 'timezone', 'default_currency']))}
    paths['/openapi.json'] = {'get': operation('Read this OpenAPI contract')}
    paths['/health'] = {'servers': [{'url': '/api'}], 'get': {'summary': 'Check schema and database readiness', 'security': [], 'responses': {'200': {'description': 'Ready', 'content': {'application/json': {'schema': obj({'status': string(enum=['ok']), 'schema': string()}, ['status', 'schema'])}}}, '503': {'description': 'Migration required or database unavailable', 'content': {'application/json': {'schema': obj({'status': string(enum=['unhealthy']), 'reason': string()}, ['status', 'reason'])}}}}}}
    filters = [query(key, DAY if key in ('start', 'end') else ID if key.endswith('_id') else string()) for key in ('start', 'end', 'account_id', 'type', 'category_id', 'tag_id', 'subscription_id', 'currency', 'account_type')]
    for route in ('dashboard', 'reports/data', 'reports/csv', 'reports/pdf'):
        paths['/' + route]['get']['parameters'] = deepcopy(filters)
    paths['/reports/csv']['get']['parameters'].append(query('section', string(enum=['transactions', 'summary', 'cash-flow', 'budgets', 'balances', 'net-worth', 'audit'], default='transactions')))
    paths['/budget-summary']['get']['parameters'] = [query('month', string(pattern=r'^\d{4}-\d{2}$'))]
    paths['/forecast']['get']['parameters'] = [query('horizon', string(enum=['month', 'quarter', 'year'], default='month'))]
    paths['/performance']['get']['parameters'] = [query('account_id', ID, required=True)]
    paths['/exports']['get']['parameters'] = [query('format', string(enum=['archive', 'accounts', 'transactions'], default='archive')), query('template', {'type': 'boolean', 'default': False})]
    paths['/exports']['get']['responses']['200']['content']['text/csv'] = {'schema': string(format='binary')}
    return {'openapi': '3.1.0', 'info': {'title': 'Hoard Counter', 'version': '1.0.0', 'description': 'One hoard, exact decimal amounts, optimistic revisions, authenticated session cookies and CSRF protection.'}, 'servers': [{'url': '/api/v1'}], 'security': [{'sessionCookie': []}], 'paths': paths, 'components': {'schemas': schemas, 'securitySchemes': {'sessionCookie': {'type': 'apiKey', 'in': 'cookie', 'name': 'session'}}}}
