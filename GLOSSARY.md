# Glossary

One name per concept on screen. Code, API, and database names may differ;
they are listed so the mapping is findable. Exports and the bookkeeper report
keep their own column names (Category, Class, Calc USD) because they are
written for the bookkeeper.

| On screen | Code / API | Notes |
|---|---|---|
| Transaction | `transactions` row, `txn_id` | Shown as "Transaction #123". Not "cash record" or "row". |
| Direct expense | `direct_expense` voucher | Cash paid straight to a vendor. |
| Signout | `signout` voucher | Cash given to a person who returns receipts later. |
| Recipient | `recipient` | The person holding a signout's cash. Employee pages say "you". |
| Vendor | `recipient` on a direct expense, `vendor` | |
| Addendum | `addenda` | Additional cash or a cash return on a signout. |
| Currency exchange | `exchange` | |
| Bank withdrawal | `bank_withdrawal`, `withdrawal` | |
| Donation | `donation` | |
| Starting cash | `opening` | |
| Adjustment | `adjustment`, `correction` | Includes safe-count corrections. |
| Receipt | `claim` (employee receipts) | |
| Receipt submission | `package` | A batch of receipts sent for review. |
| Checked | `verified` | An admin has coded and checked one receipt. |
| Approved | `accepted` | A receipt submission posted to the ledger. |
| Expense report | signout report (`/FY##-NNNN`) | The recipient's receipts and cash-left declaration. |
| Settlement | `closeout` | Confirming the cash returned or reimbursed. |
| Account code | `expense_account` | "Category" in the bookkeeper report. |
| Class code | `class_code` | "Class" in the bookkeeper report. |
| Receipt value | `face_usd`, `receipt_face_*` | The purchase total printed on the receipt. |
| USD value | basis, `basis_usd` | The recorded USD value of MXN held. |
| Calculated USD | `calc_usd` | "Calc USD" in exports. |
| Document inbox | documents with `state='inbox'` | |
| Capture | `/capture` | Turning photos and files into inbox documents. |
| Void / Replaced | `void` / `superseded` | |

Status labels live in `cash/static/js/common.js` (`cashLabels`).
