"""Live presentation names. Object paths and document IDs never change."""
from collections import defaultdict
from urllib.parse import quote

from . import repo


def canonical_filenames(conn):
    candidates = defaultdict(set)
    numbers = repo.receipt_numbers(conn)
    # The legacy helper includes historical count-sheet associations; only
    # active filings should supply a public name.
    for row in conn.execute("SELECT document_id FROM documents WHERE doc_type='physical_count'"):
        numbers.pop(row['document_id'], None)
    numbers.update({row['document_id']: f"Txn-{row['txn_id']}" for row in conn.execute(
        "SELECT td.document_id, MIN(t.txn_id) AS txn_id FROM transaction_documents td"
        " JOIN documents d USING(document_id) JOIN transactions t USING(txn_id)"
        " WHERE d.doc_type='physical_count' AND t.posting_state='active' GROUP BY td.document_id")})
    for row in conn.execute(
            "SELECT td.document_id, td.role, t.* FROM transaction_documents td"
            " JOIN transactions t USING(txn_id) WHERE t.posting_state='active'"):
        reference = repo.document_reference(row, row['role'], row['document_id'], numbers)
        candidates[row['document_id']].add((row['role'] != 'voucher', reference))
    # Direct-expense receipts need not have transaction_documents rows.
    for row in conn.execute(
            "SELECT r.document_id, r.receipt_id, r.voucher_id, d.doc_type"
            " FROM receipts r JOIN documents d USING(document_id) WHERE r.status!='void'"):
        reference = (f"{row['voucher_id']}-log" if row['doc_type'] == 'purchase_log'
                     else row['receipt_id'] + ('x' if row['doc_type'] == 'statement' else ''))
        candidates[row['document_id']].add((True, reference))
    for row in conn.execute(
            "SELECT document_id, addendum_id FROM addenda"
            " WHERE status!='void' AND document_id IS NOT NULL"):
        candidates[row['document_id']].add((False, row['addendum_id']))
    return {did: min(refs, key=lambda item: (item[0], len(item[1]), item[1]))[1] + '.pdf'
            for did, refs in candidates.items()}


def filename(document_id, names):
    return names.get(document_id, document_id + '.pdf')


def document_url(document_id, names, voucher_id=None):
    prefix = f'/{quote(voucher_id, safe="")}/documents' if voucher_id else '/api/documents'
    return f'{prefix}/{quote(document_id, safe="")}/{quote(filename(document_id, names), safe="")}'
