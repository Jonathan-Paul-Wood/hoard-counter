# Accounting, forecasts, and portable records

## Account balances and transaction types

Each account has a financial type and an accounting class. Assets and liabilities belong to the household; income, expense, and equity counterparties complete balanced journals. A liability's displayed balance is the amount owed. Household net worth is assets minus liabilities.

Income moves from an income source into a household account. Expense moves from a household account to an expense payee. Transfer moves between household accounts, including credit-card payments and loan principal. Expense, Income, and Transfer amounts must be positive. Return records an investment gain (positive) or loss (negative), with the same investment account as both source and destination; zero is not a gain or loss. Eligible account types are Brokerage, Roth IRA, Traditional IRA, Roth 401K, Traditional 401K, HSA, FSA, 529, and Crypto Cold Wallet. A Return changes that account's balance once and uses the built-in income counterparty to balance the journal. Refund is an Expense marked as a refund, with the payee as source and the household account as destination; it reduces spending on the refund date. Record loan interest separately as an expense. Each transaction has one category.

Opening balances and reconciliations have explicit equity counterparts. An account balance is computed from postings; it is never an independently editable cached total. Transactions can be edited or voided, with revisions and attributed before/after audit events retained. System-generated valuation/opening journals are edited through their source records.

Current account balances and latest valuations use today's date in the configured household timezone. Future-dated transactions and valuations remain saved but do not change current balances until their date arrives.

Amounts are exact decimal strings. Account currencies include major fiat currencies, BTC, and ETH. Conversion rates are USD per native unit and apply from their date until superseded. Foreign transfers preserve both actual native amounts; any USD conversion difference is balanced against the currency gain/loss counterpart. Record identifiable fees separately.

Transaction, opening-balance, and valuation conversion snapshots retain historical values when the rate table changes. Revalued account totals use the latest rate on or before the reporting date. Missing rates are surfaced; incomplete totals are labeled. New rates do not rewrite prior spending.

## Subscriptions, rules, and forecasts

Subscriptions are expectations, not ledger transactions. Recurrence preserves the original day anchor when a short month clamps a due date. Creation rules run by priority, then occurrence matching uses accounts, currency, amount tolerance, and the due-date window. Explicit links win; ambiguous matches are suggested for manual selection. One active transaction may fulfill an occurrence.

Expense forecasts cover the next calendar month, quarter, or year. They add unfulfilled recurring expense occurrences to the average nonrecurring category expense from up to three previous complete calendar months. Subscription-linked spending and transfers are excluded from that historical component; refunds reduce it. Forecasts show the actual history coverage and do not use budgets as expected spending. Dated manual FX rates available as of the forecast date value future non-USD subscriptions.

Monthly category budgets compare their target with actual expenses less refunds. They do not allocate account cash to envelopes and do not roll funds forward automatically.

## Investment valuations and projections

A valuation is an absolute end-of-day account market value, including cash. Ordinary transactions on that date are processed before the valuation. A generated balanced adjustment brings the account to that target. Backdated edits replay subsequent valuation adjustments instead of accumulating another gain.

Historical performance is an estimate using Modified Dietz between actual valuations, geometrically chained in USD. Contributions and withdrawals are external flows. Return transactions and valuation adjustments are performance; an Expense explicitly marked as an investment fee reduces performance. For an interval of D days, a flow d days after its opening valuation receives weight `(D-d)/D`. The return is `(ending value - starting value - flows) / (starting value + weighted flows)`.

The application does not infer performance from raw balance growth. An invalid weighted-capital denominator or insufficient history produces an unavailable state. Annualized historical projection mode requires at least 365 days of valid coverage, using valuation intervals within the latest three years. Individual securities, trades inside an aggregate investment account, tax lots, and taxable realized-gain accounting are outside this version.

Projection assumptions are annual USD total returns. Planned flows are signed USD amounts applied at end of day. Manual assumptions default to a clearly displayed 0% for accounts without a supplied assumption. Do not add expected reinvested dividends again: total return already includes earnings. Non-reinvested payouts can be modeled as planned withdrawals. Forecasts and projections never create actual postings.

## CSV and full archive exchange

Download editable accounts/transactions templates from Settings. Record UUIDs and UTC `last_modified` timestamps are portable identifiers. Newer timestamps replace entire records; older records are skipped; equal timestamps with different content create conflicts. Rows without UUIDs receive new UUIDs during preview. Re-importing such an original UUID-less file creates new records, so use its subsequent export for stable deduplication.

Account CSVs contain opening balances separately from current balances. Current balance is informational and ignored during import. Keep `_data` and `_escaped_fields` columns for exact application-generated CSV round trips; visible columns override their corresponding stored fields. `_escaped_fields` tracks spreadsheet formula escaping, which is reversed on import without changing the original description. Categories, tags, and subscription references use UUIDs. Import referenced accounts/metadata first or use a full archive.

CSV exports contain user-entered accounts or ordinary transactions. A complete ZIP archive also contains rates, valuations, budgets, rules, scenarios, reconciliation journals, void markers, attachment metadata/files, and audit provenance. Derived opening and valuation journals are reconstructed from their source records. Archives contain household financial data, not login credentials or application secrets; administrator imports can include household settings. Machine backups preserve the complete SQLite database, including users.

Imports first validate and simulate their complete effects. Review changes, rule effects, and conflicts before committing. Previews belong to the requesting user, expire after 30 minutes, and become stale when the ledger changes. Archive imports preserve classifications; new CSV transactions run creation rules. Commits are atomic for the database, and uploaded attachment filenames are immutable.

### Firefly Import

In Settings, choose **Firefly Import** and upload Firefly III's native UTF-8 transaction CSV export. The translator follows [Firefly III's export format](https://github.com/firefly-iii/firefly-iii/blob/main/app/Support/Export/ExportDataGenerator.php). Create financial accounts in Hoard Counter first, using the same names and currencies. Names match without regard to case; duplicate matches must be resolved. Firefly's generic asset account type cannot determine the appropriate Hoard financial account type.

Withdrawals become positive Expenses, Deposits become positive Income, and Transfers remain positive Transfers. A Deposit from an expense account becomes an Expense refund. The exported source and destination determine the direction; Firefly's negative Withdrawal amounts are converted to positive magnitudes. Each split journal becomes a separate transaction. Both native amounts are retained for foreign-currency transactions; enter dated USD exchange rates in Hoard Counter before importing non-USD accounts. The exported calendar date is retained even when the CSV contains a timestamp with an offset.

Matching income sources, expense payees, categories, and tags are reused; missing ones are created only on commit and are listed in the preview. Descriptions and multiline notes are preserved. The original CSV fields, including budget, bill, and external references, are retained under transaction `firefly` metadata; they do not create budgets or subscriptions. Spreadsheet formula escaping is reversed. Firefly `user_id` and `journal_id` produce stable transaction UUIDs, and `updated_at` controls replacement. Reimporting the same source row skips it, including after a manual Hoard edit; changed rows follow the normal timestamp conflict rules. Use this importer for one Firefly installation, since independent installations may reuse user/journal IDs.

Opening balance, Reconciliation, and Liability credit rows are not ordinary transactions in Hoard Counter. Enter opening balances and reconciliations separately and remove those rows from this transaction CSV. Unsupported types, missing or ambiguous accounts, duplicate journal IDs, and malformed values block the import with an actionable row error; no records are partially committed. Firefly has no native Return transaction type, so Deposits are not inferred to be investment Returns.

## Reports and history

Screen, CSV, and PDF transaction summaries share the same backend calculations. Transaction filters apply to cash-flow summaries and transaction detail. Account/type/currency filters also restrict account balances. Category/tag/subscription filters do not allocate balances to categories. Monthly budget comparisons explicitly use complete calendar months; audit sections use the dates changes were recorded, which can differ from transaction dates.

Reports offers separate CSV downloads for transactions, summary totals, monthly cash flow, budgets, balances, net-worth history, and audit events. Transaction CSV `net_expense_usd` is signed: purchases are positive, refunds negative, and other transaction types zero. Summing that column reproduces reported spending. The audit CSV includes the full before/after values as JSON cells. CSV exports escape text that spreadsheet software could interpret as a formula.

Voids preserve audit records and attachment bytes needed by past revisions and backups. This version retains immutable uploads rather than performing automatic attachment garbage collection.
