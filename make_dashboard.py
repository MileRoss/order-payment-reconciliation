"""
Build a single static page (dashboard.html) from the reports reconcile.py writes.

No server and no JavaScript: the page is plain HTML and CSS, so it can be hosted
on GitHub Pages or simply opened in a browser. Every number on it comes straight
from a CSV in the output folder.

Usage:
    python make_dashboard.py --outdir synthetic_output --html dashboard.html
"""

import argparse
from html import escape
from pathlib import Path

import pandas as pd

# tier -> (plain-language label, is it not acceptable?). Only a full match is acceptable.
TIERS = {
    "full_match": ("Reference, amount and date all match", False),
    "ref_and_amount_only": ("Reference and amount match,\ndate differs", True),
    "ref_and_date_only": ("Reference and date match,\namount differs", True),
    "amount_and_date_other_ref": ("Amount and date match a payment,\nreference differs", True),
    "ref_found_no_other_match": ("Reference exists,\nnothing else matches", True),
    "ref_not_found": ("Reference not in the payment records", True),
    "missing_ref": ("No payment reference on the order", True),
}


def n(x) -> str:
    return f"{int(x):,}"


def eur(x) -> str:
    return f"€{x:,.0f}"


def chart(headers, cols, rows):
    """headers: names of the number columns. cols: CSS widths (label, bar, numbers...).
    rows: list of (label, value, max_value, is_orange, [numbers as text]). Returns HTML."""
    style = f'style="--cols:{cols}"'
    out = [f'<div class="row head" {style}><div></div><div></div>'
           + "".join(f'<div class="num">{escape(h)}</div>' for h in headers) + "</div>"]
    for label, value, top, orange, cells in rows:
        width = max(value / top * 100, 0.4) if top and value > 0 else 0
        cls = "bar warn" if orange else "bar"
        label_html = "<br>".join(escape(part) for part in label.split("\n"))
        nums = "".join(f'<div class="num" data-h="{escape(h)}">{escape(c)}</div>' for h, c in zip(headers, cells))
        out.append(
            f'<div class="row" {style} title="{escape(label.replace(chr(10), " "))}">'
            f'<div class="lab">{label_html}</div>'
            f'<div class="track"><div class="{cls}" style="width:{width:.1f}%"></div></div>'
            f"{nums}</div>"
        )
    return "\n".join(out)


def table(headers, body_rows):
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in r) + "</tr>" for r in body_rows)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def build(outdir: Path) -> str:
    tiers = pd.read_csv(outdir / "summary_by_tier.csv").set_index("match_tier")
    stores = pd.read_csv(outdir / "summary_by_store.csv")
    refs = pd.read_csv(outdir / "top_reused_refs.csv").head(8)
    flagged = pd.read_csv(outdir / "top_200_flagged_orders.csv").head(10)
    notes = pd.read_csv(outdir / "run_notes.csv").set_index("item")["value"]

    total = int(tiers["orders"].sum())
    bad_tiers = [t for t, (_, bad) in TIERS.items() if bad]
    bad = tiers.reindex(bad_tiers).fillna(0)
    bad_orders, bad_value = int(bad["orders"].sum()), bad["total_value"].sum()
    full_matches = int(tiers.loc["full_match", "orders"]) if "full_match" in tiers.index else 0

    # chart 1: orders by evidence tier
    top = tiers["orders"].max()
    tier_rows = []
    for key, (label, bad) in TIERS.items():
        count = int(tiers["orders"].get(key, 0))
        pct = count / total * 100
        tier_rows.append((label, count, top, bad, [n(count), f"{pct:.1f}%"]))
    tier_rows.sort(key=lambda r: r[1], reverse=True)

    # chart 2: share of not acceptable orders per store
    stores = stores.sort_values("not_acceptable_pct", ascending=False)
    top_pct = stores["not_acceptable_pct"].max()
    store_rows = [
        (
            r.store,
            r.not_acceptable_pct,
            top_pct,
            True,
            [f"{n(r.not_acceptable_orders)} / {n(r.orders)}", f"{r.not_acceptable_pct:.1f}%", eur(r.not_acceptable_value), n(r.no_payment_found_orders)],
        )
        for r in stores.itertuples()
    ]

    ref_table = table(
        ["Payment reference", "Orders citing it", "Value at stake", "Store"],
        [(r.payment_ref, n(r.distinct_orders), eur(r.total_value_at_stake), r.stores) for r in refs.itertuples()],
    )
    flagged_table = table(
        ["Order", "Store", "Evidence", "Amount", "Reference reused"],
        [
            (r.order_id, r.store, TIERS[r.match_tier][0].replace("\n", " "), f"€{r.bill_amount:,.2f}", "yes" if r.ref_reused else "no")
            for r in flagged.itertuples()
        ],
    )

    summary_file = outdir / "manager_summary.md"
    summary_block = ""
    if summary_file.exists():
        summary_block = (
            '<section class="card"><h2>Plain-language summary</h2>'
            "<p>Written by Claude through the API from the summary tables only, then saved. "
            "It is a fixed text; nothing on this page calls the API.</p>"
            f"<p style=\"color:var(--ink)\">{escape(summary_file.read_text(encoding='utf-8').strip())}</p></section>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Order vs payment reconciliation</title>
<style>
:root {{
  color-scheme: light dark;
  --surface: #fcfcfb; --card: #ffffff; --line: #e4e3df;
  --ink: #0b0b0b; --ink2: #52514e; --track: #efeeea;
  --blue: #2a78d6; --orange: #eb6834;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --surface: #1a1a19; --card: #222221; --line: #383835;
    --ink: #f0efec; --ink2: #c3c2b7; --track: #2d2d2b;
    --blue: #3987e5; --orange: #d95926;
  }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--surface); color: var(--ink);
  font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 960px; margin: 0 auto; padding: 32px 16px 56px; }}
h1 {{ font-size: 1.6rem; margin: 0 0 4px; }}
h2 {{ font-size: 1.05rem; margin: 0 0 4px; }}
p.sub {{ margin: 0 0 20px; color: var(--ink2); }}
.banner {{ border: 1px solid var(--line); border-left: 4px solid var(--orange); background: var(--card);
  padding: 10px 14px; border-radius: 6px; margin-bottom: 24px; color: var(--ink2); font-size: .92rem; }}
.kpis {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin-bottom: 24px; }}
.kpi {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; }}
.kpi b {{ display: block; font-size: 1.7rem; line-height: 1.2; }}
.kpi b small {{ font-size: .95rem; font-weight: 400; color: var(--ink2); }}
.kpi span {{ color: var(--ink2); font-size: .88rem; }}
section.card {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 18px 16px; margin-bottom: 20px; }}
section.card > p {{ margin: 0 0 14px; color: var(--ink2); font-size: .92rem; }}
.row {{ display: grid; grid-template-columns: var(--cols); gap: 12px; align-items: center; padding: 5px 0; }}
.row.head {{ padding-bottom: 2px; }}
.row.head .num {{ font-size: .78rem; line-height: 1.2; }}
.num {{ text-align: right; font-size: .88rem; color: var(--ink2); font-variant-numeric: tabular-nums; }}
.lab {{ font-size: .92rem; }}
.track {{ background: var(--track); height: 14px; border-radius: 0 4px 4px 0; }}
.bar {{ height: 14px; background: var(--blue); border-radius: 0 4px 4px 0; }}
.bar.warn {{ background: var(--orange); }}
.legend {{ display: flex; gap: 18px; font-size: .85rem; color: var(--ink2); margin-bottom: 8px; }}
.legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; background: var(--blue); }}
.legend i.warn {{ background: var(--orange); }}
.scroll {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: .9rem; }}
th, td {{ text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; }}
th {{ color: var(--ink2); font-weight: 600; }}
ul {{ margin: 0; padding-left: 20px; color: var(--ink2); font-size: .92rem; }}
footer {{ color: var(--ink2); font-size: .85rem; margin-top: 28px; }}
@media (max-width: 680px) {{
  .row {{ grid-template-columns: repeat(auto-fit, minmax(90px, 1fr)); gap: 3px 12px; padding: 8px 0; }}
  .row.head {{ display: none; }}
  .lab, .track {{ grid-column: 1 / -1; }}
  .num {{ text-align: left; }}
  .num::before {{ content: attr(data-h); display: block; font-size: .72rem; color: var(--ink2); }}
}}
</style>
</head>
<body>
<main>
<h1>Order vs payment reconciliation</h1>
<p class="sub">Every in-store order is checked against the card-payment records. This page shows what the pipeline found.</p>
<div class="banner">Demo on <b>invented data</b>. The stores, orders and payments are generated, with problems planted on purpose, so these results say nothing about any real business.</div>

<div class="kpis">
  <div class="kpi"><b>{n(total)}</b><span>completed orders checked</span></div>
  <div class="kpi"><b>{n(full_matches)} <small>({full_matches / total * 100:.1f}%)</small></b><span>orders acceptable</span></div>
  <div class="kpi"><b>{n(bad_orders)} <small>({bad_orders / total * 100:.1f}%)</small></b><span>orders not acceptable</span></div>
  <div class="kpi"><b>{eur(bad_value)}</b><span>value of those orders</span></div>
</div>

{summary_block}
<section class="card">
  <h2>How well each order matches a payment</h2>
  <p>Only a full match is acceptable. The other six levels show how far an order is from it, down to no payment reference at all.</p>
  <div class="legend"><span><i></i>acceptable</span><span><i class="warn"></i>not acceptable</span></div>
  {chart(["Orders", "%"], "250px 1fr 70px 56px", tier_rows)}
</section>

<section class="card">
  <h2>Orders that are not acceptable, by store</h2>
  <p>Share of each store's completed orders where reference, amount and date do not all match, with the value at stake.</p>
  {chart(["Not acceptable / all orders", "%", "Value", "No payment found"], "130px 1fr 112px 56px 64px 78px", store_rows)}
</section>

<section class="card">
  <h2>Payment references cited by more than one order</h2>
  <p>One card payment can only pay for one order, so a reference cited many times cannot be verified by itself. It is not proof of theft: staff sometimes reused a reference because the browser kept suggesting it.</p>
  {ref_table}
</section>

<section class="card">
  <h2>Ten orders to follow up first</h2>
  <p>Not acceptable orders, largest amount first. The full ranked list is in <code>reconciliation_full.csv</code>.</p>
  {flagged_table}
</section>

<section class="card">
  <h2>Left out of the count, and why</h2>
  <ul>
    <li>{n(notes["non_completed_orders_not_scored"])} canceled, pending or stock-changed orders: no payment is expected. {n(notes["non_completed_with_payment_check_refund"])} of them cite a real payment and are listed separately so a refund can be checked.</li>
    <li>{n(notes["orders_outside_payment_period"])} orders outside the period covered by the payment records ({escape(str(notes["payment_period_start"])[:10])} to {escape(str(notes["payment_period_end"])[:10])}): they cannot be checked, so calling them not acceptable would be wrong.</li>
    <li>{n(notes["sandbox_orders_dropped"])} orders from a test store.</li>
  </ul>
</section>

<footer>Generated by make_dashboard.py from the CSV reports in the output folder.</footer>
</main>
</body>
</html>
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", default="output")
    parser.add_argument("--html", default="dashboard.html")
    args = parser.parse_args()
    Path(args.html).write_text(build(Path(args.outdir)), encoding="utf-8")
    print(f"Wrote {args.html}")
