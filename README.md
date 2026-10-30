# Order vs payment reconciliation

A customer orders in a store and pays by card. The store's system records the
order, with a payment reference that staff type in from the card receipt. The
payment processor records the card payment separately. This project checks, for
every order, whether the two agree, and flags the orders that cannot be verified.

Any business that takes card payments in a store and records orders in a separate
system has this problem: retail, restaurants, pharmacies, any counter service where
a reference is entered by hand.

**[Demo page](https://mileross.github.io/order-payment-reconciliation/dashboard.html)**, built from invented data.

This project is my exercise in automation and in working with an AI coding
assistant ("vibe coding"): I define the problem and the checks, the assistant
writes the code, and I review what comes out.

## Where this comes from

In 2023, at Getir, a store manager asked me to look into suspected theft by staff
at one store. Stock kept going missing, and the other known causes of missing stock
were already being tracked. The in-store orders were a newer process that nobody
had monitored yet, so that is where I looked.

Customers placed their order on a tablet in the store, paid by card, and waited a
few minutes while staff prepared it. Staff then entered the payment's slip ID (what
I call the payment reference here) into the order. I checked it by hand in Google
Sheets: for each order, is there a card payment with the same reference, the same
amount and the same day? My part ended at which orders could not be verified. Who
placed them was a question for the store manager, who could match the order times
against staff shift patterns, data I had no access to.

This repo reimplements that method as a script that runs on every store at once. It
runs on **invented data**.

**Who did what.** I specified the checks, and I reviewed the results and caught
several of the mistakes listed below. The code was written with an AI assistant
(Claude). I can explain every rule and why it is there. I did not write the pandas
code unaided, and this repo does not claim that.

## As an internal control

Reconciling recorded sales to the payment processor's records is a standard
revenue control. It answers one question: was each recorded order actually paid
for? The design follows the habits of an audit test:

- **A defined population.** Completed orders inside the period the payment export
  covers. Everything else is set aside, and each exclusion is counted in
  `run_notes.csv`.
- **A written acceptance rule.** Only a full match is acceptable, with the reasoning
  for each level that fails (below).
- **An exception report, not a conclusion.** Failing orders are listed and ranked
  for follow-up. The script does not decide who is at fault.
- **Tested against known exceptions.** The invented dataset comes with a file of
  what was planted, and the pipeline recovers it exactly.
- **Assumptions in one place.** The columns file records which columns and which
  status value were used, so a run can be reviewed and repeated.
- **Repeatable.** A fixed random seed and no hidden steps: the same input gives the
  same output.
- **Stated limits.** See Limitations.

This is a small reconciliation test, not an audit engagement.

## Quick start (invented data)

    pip install -r requirements.txt
    python generate_synthetic.py --outdir synthetic_data
    python reconcile.py --input synthetic_data --outdir synthetic_output
    python make_dashboard.py --outdir synthetic_output --html dashboard.html

For the interactive view, with filters by store, match level and date, install
Streamlit (`pip install streamlit`) and run `streamlit run app.py`.

## The invented dataset

`generate_synthetic.py` builds five stores and about 7,200 orders over a year,
with a fixed random seed, and writes the matching `columns.xlsx`. Problems are
planted on purpose: one store where about a third of orders have no payment, a reused reference in every store, wrong
references with a real payment behind them, lowercase typos, a failed payment,
paid-then-canceled orders, a test store, and a payment export that ends ten days
early.

`synthetic_data/ground_truth.csv` records what was planted in each order, and
the pipeline recovers it exactly: honest orders are all `full_match`, wrong
references with a real payment are `amount_and_date_other_ref`, reused references
are `ref_found_no_other_match`, and so on. That shows the rules do what they say.
The problem rates are **chosen by hand**, so the results on this data prove
nothing about any real business.

## Use it on your own data

1. Put your orders file and your card-payments file in the `input/` folder, named
   `orders.csv` (or `.tsv`, `.xlsx`) and `payments.csv`. Your files stay on your
   computer; nothing is uploaded.
2. Copy `columns_example.xlsx` into `input/` and rename it `columns.xlsx`. Open it
   in Excel. It is a plain table: on the left, what the script needs; on the right
   (yellow cells), you write the name of the matching column in your own files.
3. Run `python reconcile.py`, then `python make_dashboard.py --html my_dashboard.html`.

Use your own name for the dashboard file: `dashboard.html` is the demo page and
would be overwritten.

(An example of a filled-in table.) 

| The script needs | Your column name |
|---|---|
| Order number | `Sale No` |
| Store | `Branch` |
| Order date | `Sold at` |
| Payment reference on the order | `Receipt Code` |
| Order status | `State` |
| Order amount | `Total` |
| Payment reference in the payments file | `Transaction` |
| Payment date | `Paid on` |
| Payment amount | `Euros` |

Two rows ask for a value, not a column name: the status value that marks a
finished, paid order (for example `Paid`, a word found inside the `State` column)
and the test stores to ignore.

Capitals and stray spaces in the names do not
matter. The optional rows cover test stores to ignore and a filter for payment
exports that also list transfers or failed attempts.

The script works out how each file is written: separator, text encoding, the
order of day and month in dates, and the decimal mark in amounts (`1.234,56` and
`1,234.56` both work). It prints what it found, so you can check it. If it cannot
tell, for example `03/04/2025` could be 3 April or 4 March, it stops and says
which row of the table to fill in. If something is wrong it stops and says what to
fix, for example `The orders file 'orders.csv' has no column "amout" (you wrote it
next to "Order amount" in columns.xlsx). Did you mean "amount"?`.

An order can span several rows (one per product line) if the order values repeat
on each row: the first row is used, so an order total is never summed twice.

## Output

`reconcile.py` writes these files to a folder called `output/`, next to the scripts. Start with `reconciliation_full.csv`, then open your dashboard file in a browser for the overview:

- `reconciliation_full.csv`: every order, ranked; opens in Excel.
- `top_200_flagged_orders.csv`: the 200 orders to look at first.
- `summary_by_tier.csv`: orders and value per match level.
- `summary_by_store.csv`: not acceptable orders per store.
- `top_reused_refs.csv`: references cited by the most orders.
- `canceled_with_payment_check_refund.csv`: canceled orders whose reference matches a real payment, a refund question that is not scored.
- `excluded_non_completed_orders.csv`: the orders left out of the count.
- `run_notes.csv`: counts for the run (orders scored, set aside, test store, payment period).

## Optional: plain-language summary

`summarize.py` turns three small summary tables into a short paragraph for a
store manager, using the Claude API. The model sees only totals, never
individual orders, e-mails or card digits, and it decides nothing.

It runs on my own computer with my own API key (an environment variable that is
never committed). The output is saved as `manager_summary.md` and shown on the
dashboard page, which is static: visitors read a saved text and cannot trigger
any API call. Without a key the script only saves the prompt (`summary_prompt.txt`).

## How an order is judged

Only **Completed** orders are scored. A canceled order has no payment, and that
is expected, so scoring it would produce false alarms. Orders from the test
store are dropped (`--sandbox "name"` sets its name; the default is `test store`).
Orders placed after the payment export ends are set aside, since no payment
could be found for them whatever happened.

Each remaining order gets one of seven levels of evidence. **Only the first is
acceptable.** The other six are all flagged; they differ in how far the order is
from a full match:

| Level | Meaning |
|---|---|
| `full_match` | Reference, amount and date all agree with a card payment |
| `amount_and_date_other_ref` | The reference is wrong or missing, but an unclaimed payment of the same amount exists the same day |
| `ref_and_date_only` | Reference and date agree, amount differs |
| `ref_and_amount_only` | Reference and amount agree, date differs |
| `ref_found_no_other_match` | The reference exists but belongs to some other payment, usually a reference reused on an unrelated order |
| `ref_not_found` | The reference is not in the payment records |
| `missing_ref` | No reference on the order |

Rules that matter:

- **One payment pays for one order.** A reference cited by several orders is
  flagged as reused. For the same-amount-same-day match, a payment already claimed
  by a reference match is unavailable, and each remaining payment is given to one
  order only.
- Only **accepted** payments count. Failed and canceled card attempts do not.
- References are compared ignoring letter case and spaces, because staff
  sometimes typed a real reference in lowercase.
- Orders are ranked by amount, largest first, with the not acceptable ones before
  the rest.

The script flags orders for a human to look at. It does not decide who did what.

**Next step, outside this project:** every order in `reconciliation_full.csv` has
its date and time. To go further, take the orders that are not acceptable and
compare their times with the staff shift schedule of that store. The schedule is
not part of this project; it sits with the store manager. A match with someone's
shifts is a lead for the manager to follow up.

## Why only a full match is acceptable

An in-store order is a customer who orders and pays in the store, then waits a few
minutes while it is prepared. There is no delivery and no next-day pickup, unlike
click-and-collect. So the payment, the reference and the order should all agree,
on the same day, for the same amount. Anything less cannot be verified, and
finance or compliance would not sign off on it. The reasoning, level by level:

- **Reference and amount match, date differs.** Rare. The payment may have been
  taken but the order never completed in the system. An in-store payment happens
  within minutes of the order, so a different day needs an explanation.
- **Reference and date match, amount differs.** Staff may have typed the wrong
  amount, or charged the customer more or less than the bill. It also depends on
  whether the amount in the system was fixed or typed in by hand, which is a
  question for the product team.
- **Amount and date match a payment, reference differs.** The payment export has no
  store information, so a payment of the same amount on the same day may have been
  taken in another store. Two different bills can also share an amount and a day.
  It is the second most frequent case, and it is not acceptable.
- **Reference exists but nothing else matches, reference not found, or no
  reference.** The order has no payment that can be tied to it.

**A flag does not mean theft.** I first called these orders "suspicious" and
softened it to "not acceptable": it means the order cannot be verified from the
data. Many problems come from the process or the product, not from the person.
The clearest example is references reused many times. Staff said they did not
know a reference had to be unique, and the browser kept suggesting the same one
in the field, which the system accepted. Choosing the suggestion was easier than
typing the reference each time. This was later raised with the product manager,
and references were required to be unique. The script cannot tell a shortcut
from a theft, so it flags both and leaves the follow-up to a person.

## Mistakes found while building

| What went wrong | Effect | How it was caught |
|---|---|---|
| An order's total was summed over its product lines, but the field repeats the order total on every line | Multi-item orders inflated, so far fewer matches | Checking one order by hand |
| Canceled orders were scored like live ones | Canceled orders appeared among the top flagged orders | I read the output and asked why |
| References compared case-sensitively | References typed in lowercase counted as "not found" | Reviewing results against the original method |
| The test store was scored as a real store | Dummy orders in the results | Domain knowledge |
| Orders with a wrong or missing reference but a payment of the same amount on the same day were all called "not found" | They were mixed with orders that have no payment at all (about 30% of orders) | Re-reading the original method, which kept them as a separate, weaker group |
| Orders after the payment export ended were scored | Hundreds of false "not found" | Reviewing results by date |

## Lessons

- **Look at the process before the people.** When a control keeps failing the same
  way, the cause may be the product (the browser suggesting the same reference).
- Check a known case before trusting a total.
- Read the output like a reviewer, not like its author.
- Re-read the original method before defending a rewrite.
- A number that disagrees with an earlier result is a question, and either side
  may be wrong.

## Limitations

- Matching is reference-first. The same-amount-same-day match is weaker evidence:
  the payment export has no store information, so two sales of the same amount on
  one day, possibly in different stores, can be confused.
- References are compared as typed (apart from case and spaces). A reference with
  a typo counts as "not found".
- The original method also used a stock-movement report; it is not built.
- Any layout works if the columns table can describe it, with these limits: one
  payment row per reference (duplicates are refused), one currency, and dates
  compared by calendar day with no time-zone handling. Date styles outside the usual
  ones (such as `31 Dec 2025`) are not detected; reformat the date column first.
