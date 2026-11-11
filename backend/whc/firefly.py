"""Translate Firefly III's native transaction CSV export into ledger records.

Format reference: firefly-iii/firefly-iii app/Support/Export/ExportDataGenerator.php.
Financial accounts must already exist: Firefly's generic asset type cannot tell
us whether a Hoard account should be Checking, Brokerage, an IRA, etc.
"""
import csv
import hashlib
import io
import json
from datetime import datetime
from uuid import NAMESPACE_URL, uuid5

from .ledger import list_records
from .models import db, Record
from .validation import ApiError, date_string, decimal, money, timestamp


REQUIRED = {'user_id', 'journal_id', 'updated_at', 'type', 'amount', 'currency_code',
            'description', 'date', 'source_name', 'source_type', 'destination_name', 'destination_type'}
FINANCIAL = {'asset account', 'default account', 'cash account', 'credit card',
             'debt', 'loan', 'mortgage', 'liability credit account'}


def stable_id(kind, *parts):
    return str(uuid5(NAMESPACE_URL, 'hoard-counter/firefly/' + kind + '/' + json.dumps(parts, ensure_ascii=False)))


def unescape(value):
    # Firefly uses League CSV's EscapeFormula formatter, including on numbers.
    if len(value) > 1 and value[0] == "'" and value[1] in '=+-@\t\r':
        return value[1:]
    return value


def parse_firefly(payload):
    try:
        reader = csv.DictReader(io.StringIO(payload.decode('utf-8-sig'), newline=''), strict=True)
        headers = reader.fieldnames or []
        if not REQUIRED <= set(headers):
            raise ValueError('Use a native Firefly III transaction CSV export. Missing columns: ' + ', '.join(sorted(REQUIRED - set(headers))))
        if len(headers) != len(set(headers)):
            raise ValueError('Firefly CSV contains duplicate column headers.')
        rows = []
        for line, row in enumerate(reader, 2):
            if line > 10001:
                raise ValueError('Import at most 10,000 transactions at a time.')
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f'Firefly CSV row {line}: number of fields does not match the header.')
            rows.append((line, {key: unescape(value) for key, value in row.items()}))
    except UnicodeDecodeError as exc:
        raise ValueError('Firefly CSV files must use UTF-8 encoding.') from exc
    except csv.Error as exc:
        raise ValueError('Invalid Firefly CSV: ' + str(exc)) from exc
    if not rows:
        raise ValueError('The Firefly CSV contains no transactions.')

    records, pending, seen = [], {}, set()
    accounts = [record.to_dict() for record in list_records('account')]
    metadata = {kind: [record.to_dict() for record in list_records(kind)] for kind in ('category', 'tag')}

    def named(kind, name, line, stamp, currency=None, account_class=None):
        name = name.strip()
        pool = accounts if kind == 'account' else metadata[kind]
        matches = [item for item in pool if item['name'].casefold() == name.casefold()
                   and (currency is None or item['currency'] == currency)
                   and (account_class is None or item['account_class'] == account_class)]
        if len(matches) > 1:
            raise ValueError(f'Ambiguous {kind} name "{name}"; rename duplicate records before importing.')
        if matches:
            return matches[0]['id']
        if not name:
            raise ValueError(f'{kind.capitalize()} name is required.')
        identity = stable_id(kind, name.casefold(), currency, account_class)
        if db.session.get(Record, identity):
            raise ValueError(f'Previously imported {kind} "{name}" was renamed or deleted; restore it before importing.')
        data = dict(id=identity, name=name, last_modified=stamp)
        if kind == 'account':
            data.update(type='Income Source' if account_class == 'income' else 'Expense Payee',
                        account_class=account_class, currency=currency, opening_balance='0', opening_date='1970-01-01')
        pool.append(data)
        pending[identity] = dict(kind=kind, record=data, line=line)
        return identity

    def resolve_account(name, firefly_type, currency, line, stamp):
        role = firefly_type.strip().casefold()
        if role in FINANCIAL:
            matches = [item for item in accounts if item['name'].casefold() == name.strip().casefold()
                       and item['currency'] == currency and item['account_class'] in ('asset', 'liability')]
            if len(matches) != 1:
                reason = 'Create' if not matches else 'Resolve duplicate names for'
                raise ValueError(f'{reason} a financial account named "{name}" in {currency} before importing.')
            return matches[0]['id']
        classes = {'revenue account': 'income', 'expense account': 'expense'}
        if role not in classes:
            raise ValueError(f'Unsupported Firefly account type "{firefly_type}".')
        return named('account', name, line, stamp, currency, classes[role])

    for line, row in rows:
        try:
            user, journal = row['user_id'].strip(), row['journal_id'].strip()
            if not user or not journal:
                raise ValueError('user_id and journal_id are required for duplicate detection.')
            identity = stable_id('transaction', user, journal)
            if identity in seen:
                raise ValueError('The same Firefly user_id/journal_id appears more than once.')
            seen.add(identity)
            stamp = timestamp(row['updated_at'])
            typ = row['type'].strip().casefold()
            types = {'withdrawal': 'Expense', 'deposit': 'Income', 'transfer': 'Transfer'}
            if typ not in types:
                raise ValueError(f'Unsupported Firefly transaction type "{row["type"]}". Import only Withdrawals, Deposits, and Transfers; enter opening balances and reconciliations separately.')
            try:
                # Keep the exported calendar date rather than shifting midnight to UTC.
                when = datetime.fromisoformat(row['date'].replace('Z', '+00:00')).date().isoformat()
            except ValueError as exc:
                raise ValueError('date must be an ISO 8601 date or timestamp.') from exc
            date_string(when)
            main = abs(decimal(row['amount']))
            main_currency = row['currency_code'].strip().upper()
            foreign = row.get('foreign_amount', '').strip()
            foreign_currency = row.get('foreign_currency_code', '').strip().upper()
            if bool(foreign) != bool(foreign_currency):
                raise ValueError('foreign_amount and foreign_currency_code must be provided together.')
            other = abs(decimal(foreign)) if foreign else main
            other_currency = foreign_currency or main_currency
            # Firefly's Deposit amount belongs to the destination. Withdrawal
            # and Transfer amounts belong to the source.
            source_amount, dest_amount = (other, main) if typ == 'deposit' else (main, other)
            source_currency, dest_currency = (other_currency, main_currency) if typ == 'deposit' else (main_currency, other_currency)
            source = resolve_account(row['source_name'], row['source_type'], source_currency, line, stamp)
            destination = resolve_account(row['destination_name'], row['destination_type'], dest_currency, line, stamp)
            refund = typ == 'deposit' and row['source_type'].strip().casefold() == 'expense account'
            fingerprint = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            data = dict(id=identity, last_modified=stamp, type='Expense' if refund else types[typ],
                        source_account_id=source, destination_account_id=destination, date=when,
                        amount=money(source_amount), destination_amount=money(dest_amount),
                        description=row['description'], notes=row.get('notes', ''), is_refund=refund,
                        category_id=None, tag_ids=[],
                        firefly=dict(user_id=user, journal_id=journal, fingerprint=fingerprint, original=row))
            if row.get('category', '').strip():
                data['category_id'] = named('category', row['category'], line, stamp)
            for tag in row.get('tags', '').split(','):
                if tag.strip():
                    tag_id = named('tag', tag, line, stamp)
                    if tag_id not in data['tag_ids']:
                        data['tag_ids'].append(tag_id)
            records.append(dict(kind='transaction', record=data, line=line))
        except (ApiError, ValueError) as exc:
            raise ValueError(f'Firefly CSV row {line}: {exc}') from exc
    return list(pending.values()) + records
