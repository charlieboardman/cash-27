# Glossary

One name per concept on screen. Code, API, and database names may differ;
they are listed so the mapping is findable. Exports and the bookkeeper report
keep their own column names (Category, Class, Calc USD) because they are
written for the bookkeeper.

| On screen | Code / API | Notes |
|---|---|---|
| Transaction | `transactions` row, `txn_id` | Shown as "Transaction #123". Not "cash record" or "row". |
| Direct expense | `direct_expense` voucher | Cash paid straight to a vendor. Always one payment, however many receipts support it. |
| Also file as a receipt | `use_voucher_as_receipt` | A direct expense's signed voucher also serves as a receipt. It keeps the voucher's own number. |
| Signout | `signout` voucher | Cash given to a person who returns receipts later. |
| Recipient | `recipient` | The person holding a signout's cash. Employee pages say "you". |
| Vendor | `recipient` on a direct expense, `vendor` | |
| Addendum | `addenda` | Additional cash or a cash return on a signout. |
| Currency exchange | `exchange` | |
| Bank withdrawal | `bank_withdrawal`, `withdrawal` | |
| Donation | `donation` | |
| Starting cash | `opening` | |
| Adjustment | `adjustment`, `correction` | Includes safe-count corrections. |
| Expense report | `/FY##-NNNN`, `expense_report_drafts` | A signout's rows, shared by recipient and admin. |
| Row | draft row (`kind`) | A receipt, a cash return, or a reimbursement request. |
| Receipt | `expense` row; `receipts` once approved | Older reports stored these as `claim`s in `package`s. |
| Receipt number | `receipt_id` (`FY27-0042-R1`) | Numbers each separate receipt filed on a voucher. On a direct expense it carries no amount. |
| Split | `splits` | Parts of one receipt or payment with their own amounts and codes: one movement, several ledger lines. |
| Receipt total | `receipt_total` | Only for rows whose USD and MXN have opposite signs. |
| Submitted | `submitted` | Sent for approval; locked for the recipient. |
| Approved | `approved` | Posted to the ledger. |
| Settlement | `closeout` | Confirming the cash returned or reimbursed. |
| Account code | `expense_account` | "Category" in the bookkeeper report. |
| Class code | `class_code` | "Class" in the bookkeeper report. |
| Receipt value | `face_usd`, `receipt_face_*` | The purchase total on a receipt (the ledger's term). |
| USD value | basis, `basis_usd` | The recorded USD value of MXN held. |
| Calculated USD | `calc_usd` | "Calc USD" in exports. |
| Document inbox | documents with `state='inbox'` | |
| Capture | `/capture` | Turning photos and files into inbox documents. |
| Void / Replaced | `void` / `superseded` | |

Status labels live in `cash/static/js/common.js` (`cashLabels`).
