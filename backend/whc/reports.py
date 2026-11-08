"""CSV and paginated PDF reports use the same aggregates as the dashboard."""
from __future__ import annotations

import csv
import io
import json
from datetime import date
from decimal import Decimal
from xml.sax.saxutils import escape

from flask import Blueprint, jsonify, request, send_file
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
from reportlab.graphics.shapes import Drawing, Rect, String, Line

from .auth import login_required
from .models import db, AuditEvent, User
from .ledger import list_records
from .analytics import transaction_records, summarize, balance_summary, budget_summary, today, month_start, add_months, dec

bp = Blueprint('reports', __name__)


def report_data(filters):
    filters = dict(filters)
    filters.setdefault('start', month_start(today()).isoformat())
    filters.setdefault('end', today().isoformat())
    start, end = date.fromisoformat(filters['start']), date.fromisoformat(filters['end'])
    if start > end:
        raise ValueError('Report start must be on or before its end.')
    if (end - start).days > 36600:
        raise ValueError('Reports support at most 100 years.')
    rows = transaction_records(filters)
    account_records = {r.id: r.data for r in list_records('account', include_deleted=True)}
    accounts = {key: value.get('name', '') for key, value in account_records.items()}
    categories = {r.id: r.data.get('name', '') for r in list_records('category')}
    for row in rows:
        row.update(source_name=accounts.get(row.get('source_account_id'), ''), destination_name=accounts.get(row.get('destination_account_id'), ''), category_name=categories.get(row.get('category_id'), 'Uncategorized'))
    summary = summarize(rows)
    balance = balance_summary(end, filters)
    event_query = db.select(AuditEvent).where(AuditEvent.created_at >= filters['start'], AuditEvent.created_at < (end.fromordinal(end.toordinal() + 1)).isoformat()).order_by(AuditEvent.created_at)
    events = db.session.scalars(event_query).all()
    if any(filters.get(k) for k in ('account_id', 'type', 'category_id', 'tag_id', 'subscription_id', 'currency', 'account_type')):
        def relevant(event):
            for state in (event.before, event.after):
                if not isinstance(state, dict):
                    continue
                state = state.get('record', state)
                if not isinstance(state, dict):
                    continue
                pair = {state.get('account_id'), state.get('source_account_id'), state.get('destination_account_id')}
                if event.kind == 'account':
                    pair.add(event.record_id)
                if filters.get('account_id') and filters['account_id'] not in pair:
                    continue
                if filters.get('account_type') and not any(account_records.get(key, {}).get('type') == filters['account_type'] for key in pair):
                    continue
                if filters.get('currency') and not any(account_records.get(key, {}).get('currency') == filters['currency'] for key in pair):
                    continue
                if any(filters.get(key) and state.get(key) != filters[key] for key in ('type', 'category_id', 'subscription_id')):
                    continue
                if filters.get('tag_id') and filters['tag_id'] not in state.get('tag_ids', []):
                    continue
                return True
            return False
        events = [event for event in events if relevant(event)]
    users = {user.id: user.username for user in db.session.scalars(db.select(User))}
    audit_rows = [{**event.to_dict(), 'actor_name': users.get(event.actor_id, 'Imported history' if event.origin == 'imported-history' else 'System')} for event in events]
    budgets, net_worth_trend = [], []
    cursor = month_start(start)
    while cursor <= end:
        budgets.append(budget_summary(cursor.strftime('%Y-%m')))
        mark_date = min(add_months(cursor, 1).fromordinal(add_months(cursor, 1).toordinal() - 1), end)
        mark = balance_summary(mark_date, filters)
        net_worth_trend.append(dict(date=mark_date.isoformat(), net_worth=mark['net_worth'], incomplete=mark['incomplete']))
        cursor = add_months(cursor, 1)
    return dict(**summary, transactions=rows, accounts=balance['accounts'], balance_summary={k: v for k, v in balance.items() if k != 'accounts'}, audit=audit_rows, budgets=budgets, net_worth_trend=net_worth_trend, start=filters['start'], end=filters['end'], filters=filters, currency='USD')


def csv_safe(value):
    value = str(value if value is not None else '')
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else value


@bp.get('/reports/data')
@login_required
def data():
    return jsonify(report_data(request.args.to_dict()))


@bp.get('/reports/csv')
@login_required
def csv_report():
    report = report_data(request.args.to_dict())
    output = io.StringIO(newline='')
    section = request.args.get('section', 'transactions')
    numeric_fields = {'amount', 'usd_amount', 'net_expense_usd', 'income', 'expenses', 'returns', 'net', 'assets', 'liabilities', 'net_worth', 'budget', 'actual', 'remaining', 'balance', 'balance_usd'}
    if section == 'transactions':
        fields = ['date', 'type', 'source_name', 'destination_name', 'description', 'category_name', 'amount', 'currency', 'usd_amount', 'net_expense_usd', 'is_refund', 'notes', 'id', 'last_modified']
        rows = [{**row, 'net_expense_usd': format(dec(row['usd_amount']) * (-1 if row.get('is_refund') else 1), 'f') if row['type'] == 'Expense' else '0'} for row in report['transactions']]
    elif section == 'summary':
        fields = ['start', 'end', 'currency', 'income', 'expenses', 'returns', 'net', 'assets', 'liabilities', 'net_worth', 'incomplete']
        rows = [{**report['summary'], **report['balance_summary'], 'start': report['start'], 'end': report['end'], 'currency': 'USD'}]
    elif section == 'cash-flow':
        fields = ['month', 'income', 'expenses', 'returns', 'net', 'currency']
        rows = [{**row, 'currency': 'USD'} for row in report['monthly_trend']]
    elif section == 'budgets':
        fields = ['month', 'category_id', 'name', 'budget', 'actual', 'remaining', 'currency', 'scope']
        rows = [{**row, 'month': month['month'], 'currency': 'USD', 'scope': 'Complete calendar month; all transactions'} for month in report['budgets'] for row in month['items']]
    elif section == 'balances':
        fields = ['id', 'name', 'type', 'account_class', 'currency', 'balance', 'balance_usd', 'missing_rate', 'as_of']
        rows = [{**row, 'as_of': report['end']} for row in report['accounts']]
    elif section == 'net-worth':
        fields = ['date', 'net_worth', 'incomplete', 'currency']
        rows = [{**row, 'currency': 'USD'} for row in report['net_worth_trend']]
    elif section == 'audit':
        fields = ['id', 'created_at', 'kind', 'record_id', 'action', 'actor_id', 'actor_name', 'origin', 'before', 'after']
        rows = [{**row, 'before': json.dumps(row['before'], ensure_ascii=False), 'after': json.dumps(row['after'], ensure_ascii=False)} for row in report['audit']]
    else:
        raise ValueError('Select transactions, summary, cash-flow, budgets, balances, net-worth, or audit for the CSV section.')
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow({k: (row.get(k, '') if k in numeric_fields else csv_safe(row.get(k, ''))) for k in fields})
    return send_file(io.BytesIO(output.getvalue().encode('utf-8-sig')), mimetype='text/csv', as_attachment=True, download_name=f"hoard-{section}-{report['start']}-{report['end']}.csv")


def dollars(value):
    return f"${dec(value):,.2f}"


def build_pdf(report):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=(8.5 * inch, 11 * inch), rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40, title='Hoard Counter - Hoard Report', author='Hoard Counter')
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='SmallCell', fontName='Helvetica', fontSize=8, leading=11, spaceAfter=0, textColor=colors.HexColor('#26382f')))
    styles.add(ParagraphStyle(name='MoneyCell', parent=styles['SmallCell'], alignment=TA_RIGHT))
    styles['Title'].textColor = colors.HexColor('#214d3b')
    styles['Heading2'].textColor = colors.HexColor('#214d3b')
    styles['Normal'].fontSize = 9
    styles['Normal'].leading = 13
    def p(value, style='SmallCell'):
        return Paragraph(escape(str(value if value is not None else '')), styles[style])
    def table(rows, widths, header=True):
        result = Table([[p(c) for c in row] for row in rows], colWidths=widths, repeatRows=1 if header else 0, hAlign='LEFT')
        result.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e8eee8')), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f6f7f3')]), ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 7), ('RIGHTPADDING', (0, 0), (-1, -1), 7), ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7), ('LINEBELOW', (0, 0), (-1, 0), 0.5, colors.HexColor('#c6d3c9'))]))
        return result
    story = [p('Hoard Counter', 'Title'), p('Hoard financial report', 'Heading2'), p(f"{report['start']} through {report['end']}  |  Reporting currency: USD", 'Normal'), Spacer(1, 18)]
    summary = report['summary']
    story.append(table([['Income', 'Spending', 'Cash returns', 'Net cash flow'], [dollars(summary[k]) for k in ('income', 'expenses', 'returns', 'net')]], [133] * 4))
    story.extend([Spacer(1, 14), p('Balances at period end', 'Heading2')])
    if report['balance_summary'].get('warnings'):
        story.append(p('Incomplete valuations: ' + '; '.join(report['balance_summary']['warnings']), 'Normal'))
    balances = [['Account', 'Currency', 'Native balance', 'USD value']]
    for account in report['accounts']:
        balances.append([account['name'] + (' (debt owed)' if account.get('account_class') == 'liability' else ''), account['currency'], account['balance'], dollars(account['balance_usd']) if account['balance_usd'] is not None else 'Missing rate'])
    if len(balances) > 1:
        story.append(table(balances, [230, 60, 120, 122]))
    story.extend([Spacer(1, 12), p('Net worth: ' + dollars(report['balance_summary']['net_worth']), 'Heading3')])
    categories = report['category_spending']
    if categories:
        chart = Drawing(532, min(len(categories), 10) * 24 + 30)
        visible = categories[:10]
        maximum = max([abs(float(dec(r['amount']))) for r in visible] + [1])
        for index, row in enumerate(visible):
            y = chart.height - 25 - index * 24
            amount = float(dec(row['amount']))
            label = row['name'] if len(row['name']) <= 26 else row['name'][:23] + '...'
            chart.add(String(0, y + 3, label, fontName='Helvetica', fontSize=9))
            chart.add(Rect(165, y, abs(amount) / maximum * 265, 14, fillColor=colors.HexColor('#497a5d' if amount >= 0 else '#b67d4c'), strokeColor=None))
            chart.add(String(528, y + 3, dollars(row['amount']), fontName='Helvetica', fontSize=8, textAnchor='end'))
        story.extend([Spacer(1, 10), KeepTogether([p('Spending by category', 'Heading2'), chart])])
        story.append(table([['Category', 'Net spending']] + [[r['name'], dollars(r['amount'])] for r in categories], [380, 152]))
    if report['monthly_trend']:
        story.extend([Spacer(1, 12), p('Monthly cash flow', 'Heading2'), table([['Month', 'Income', 'Spending', 'Returns', 'Net']] + [[r['month']] + [dollars(r[k]) for k in ('income', 'expenses', 'returns', 'net')] for r in report['monthly_trend']], [92, 110, 110, 110, 110])])
    trend = report.get('net_worth_trend', [])
    if len(trend) > 1:
        chart = Drawing(532, 180)
        exact_values = [dec(point['net_worth']) for point in trend]
        values = [float(value) for value in exact_values]
        low, high = min(values), max(values)
        span = high - low or max(abs(high), 1)
        coords = [(70 + index / max(len(trend) - 1, 1) * 450, 35 + (value - low) / span * 115) for index, value in enumerate(values)]
        chart.add(Line(70, 30, 520, 30, strokeColor=colors.HexColor('#c6d3c9')))
        for index, (left, right) in enumerate(zip(coords, coords[1:])):
            if not trend[index]['incomplete'] and not trend[index + 1]['incomplete']:
                chart.add(Line(*left, *right, strokeColor=colors.HexColor('#497a5d'), strokeWidth=2))
        chart.add(String(0, 147, dollars(max(exact_values)), fontSize=8, fillColor=colors.HexColor('#68766a')))
        chart.add(String(0, 32, dollars(min(exact_values)), fontSize=8, fillColor=colors.HexColor('#68766a')))
        chart.add(String(70, 12, trend[0]['date'], fontSize=8))
        chart.add(String(520, 12, trend[-1]['date'], textAnchor='end', fontSize=8))
        story.extend([Spacer(1, 12), KeepTogether([p('Net worth through the period', 'Heading2'), chart])])
        if any(point['incomplete'] for point in trend):
            story.append(p('The chart has gaps where dated currency rates are missing. Those dates cannot be fully valued.', 'Normal'))
    budget_rows = [['Month / category', 'Budget', 'Actual', 'Remaining']]
    for month in report['budgets']:
        for row in month['items']:
            budget_rows.append([month['month'] + ' / ' + row['name']] + [dollars(row[k]) for k in ('budget', 'actual', 'remaining')])
    if len(budget_rows) > 1:
        story.extend([Spacer(1, 12), p('Monthly budget comparisons', 'Heading2'), p('Budget comparisons cover complete calendar months; other report filters do not change monthly actuals.', 'Normal'), Spacer(1, 5), table(budget_rows, [232, 100, 100, 100])])
    story.extend([Spacer(1, 12), p('Transactions', 'Heading2')])
    if report['transactions']:
        rows = [['Date / type', 'Description / accounts', 'Category', 'USD']]
        for row in report['transactions']:
            rows.append([row['date'] + ' / ' + row['type'] + (' refund' if row.get('is_refund') else ''), row['description'] + '\n' + row['source_name'] + ' -> ' + row['destination_name'], row['category_name'], dollars(-dec(row['usd_amount']) if row.get('is_refund') else row['usd_amount'])])
        story.append(table(rows, [87, 245, 110, 90]))
    else:
        story.append(p('No transactions match these filters.', 'Normal'))
    if report['audit']:
        story.extend([Spacer(1, 12), p('Change history', 'Heading2')])
        rows = [['Time (UTC)', 'Record / action', 'Actor / origin']]
        for event in report['audit']:
            rows.append([event['created_at'], event['kind'] + ' / ' + event['action'] + '\n' + str(event.get('record_id') or ''), event.get('actor_name', 'System') + ' / ' + event.get('origin', '')])
        story.append(table(rows, [140, 240, 152]))
    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#68766a'))
        canvas.drawString(40, 22, 'Hoard Counter | Hoard financial report | USD')
        canvas.drawRightString(572, 22, f'Page {document.page}')
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    buffer.seek(0)
    return buffer


@bp.get('/reports/pdf')
@login_required
def pdf_report():
    report = report_data(request.args.to_dict())
    return send_file(build_pdf(report), mimetype='application/pdf', as_attachment=True, download_name=f"hoard-{report['start']}-{report['end']}.pdf")
