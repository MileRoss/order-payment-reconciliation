"""
Order-to-payment reconciliation
===============================

Checks every in-store order against the card payments the payment processor
received, and flags the orders that no matching payment backs up.

The checks follow a manual investigation first done by hand in Google Sheets.
For each order:
    1. Does the order carry a payment reference (the number staff type in from
       the card receipt) at all?
    2. If yes, does that reference exist in the payment export?
    3. Does the order's amount match the payment amount?
    4. Does the order's date match the payment date?
    5. Is the same reference cited by several different orders?
       (one card payment cannot legitimately cover several orders)
    6. If the reference is missing or wrong, is there still a payment of the
       same amount on the same day?
       Still not acceptable: only a full match is. See the README.

The script reproduces that logic at full scale, and ranks the flagged orders
so an investigator can read the largest amounts first.

Usage:
    Put orders.csv, payments.csv and columns.xlsx in the folder `input/`, then:
        python reconcile.py
    Other folders or file names:
        python reconcile.py --input my_folder --outdir results
        python reconcile.py --orders a.csv --payments b.csv --config columns.xlsx
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

from loading import InputError, find_input_files, load_config, load_orders, load_payments


# ---------------------------------------------------------------------------
# Matching / classification
# ---------------------------------------------------------------------------

AMOUNT_TOLERANCE = 0.01  # euros


def classify(orders: pd.DataFrame, payments: pd.DataFrame) -> pd.DataFrame:
    """Classify each order by how well it's corroborated by a card payment."""

    payments_by_ref = payments.groupby("payment_ref")

    def match_row(order_row):
        ref = order_row["payment_ref"]

        if pd.isna(ref) or str(ref).strip() == "":
            return pd.Series({"match_tier": "missing_ref", "payment_amount": None, "payment_date": None})

        if ref not in payments_by_ref.groups:
            return pd.Series({"match_tier": "ref_not_found", "payment_amount": None, "payment_date": None})

        # Every reference maps to exactly one payment in this
        # dataset (verified: no reference is ever shared by more than one
        # payment row). Assert that instead of silently handling multiple
        # candidates — if a future export ever violates it, this should
        # fail loudly, not quietly pick one and guess.
        candidates = payments_by_ref.get_group(ref)
        assert len(candidates) == 1, f"reference {ref!r} matched {len(candidates)} payment rows, expected exactly 1"
        candidate = candidates.iloc[0]

        amount_matches = abs(candidate["txn_amount"] - order_row["bill_amount"]) <= AMOUNT_TOLERANCE
        date_matches = pd.notna(order_row["order_date"]) and candidate["txn_date"].date() == order_row["order_date"].date()

        if amount_matches and date_matches:
            tier = "full_match"
        elif amount_matches:
            tier = "ref_and_amount_only"
        elif date_matches:
            tier = "ref_and_date_only"
        else:
            tier = "ref_found_no_other_match"

        return pd.Series({"match_tier": tier, "payment_amount": candidate["txn_amount"], "payment_date": candidate["txn_date"]})

    matched = orders.join(orders.apply(match_row, axis=1))

    # Reference reuse: how many DISTINCT orders cite this reference (excluding
    # missing ones). More than one means the same payment is being used to
    # back several orders.
    reuse_counts = (
        matched.loc[matched["payment_ref"].notna()]
        .groupby("payment_ref")["order_id"]
        .nunique()
        .rename("orders_citing_this_ref")
    )
    matched = matched.join(reuse_counts, on="payment_ref")
    matched["orders_citing_this_ref"] = matched["orders_citing_this_ref"].fillna(0).astype(int)
    matched["ref_reused"] = matched["orders_citing_this_ref"] > 1

    return matched


# Orders the reference could not vouch for. For these only, look for a
# payment of the same amount on the same day, whatever its reference. This is
# weaker evidence than a reference match, so it gets its own tier.
#
# One payment can only vouch for one order (the investigation's own rule: a
# single card payment cannot cover several orders). So payments already
# claimed by a reference match are not available, and within one day and amount
# each remaining payment is given to one order only; further orders with the
# same day and amount stay in their own, weaker tier.
REF_FAILED_TIERS = {"missing_ref", "ref_not_found", "ref_found_no_other_match"}


def add_value_and_date_match(matched: pd.DataFrame, payments: pd.DataFrame) -> pd.DataFrame:
    matched = matched.copy()

    claimed_refs = set(matched.loc[~matched["match_tier"].isin(REF_FAILED_TIERS), "payment_ref"])
    free = payments[~payments["payment_ref"].isin(claimed_refs)]
    free_per_day_amount = free.groupby(
        [free["txn_date"].dt.date, free["txn_amount"].round(2)]
    ).size()

    candidates = matched[matched["match_tier"].isin(REF_FAILED_TIERS) & matched["order_date"].notna()]
    candidates = candidates.sort_values("order_date").assign(
        day=lambda d: d["order_date"].dt.date,
        amount=lambda d: d["bill_amount"].round(2),
    )
    # 0 for the first order of that day+amount, 1 for the second, ...
    rank_in_group = candidates.groupby(["day", "amount"]).cumcount()
    keys = list(zip(candidates["day"], candidates["amount"]))

    for idx, key, rank in zip(candidates.index, keys, rank_in_group):
        if rank < free_per_day_amount.get(key, 0):
            matched.loc[idx, "match_tier"] = "amount_and_date_other_ref"
    return matched


def rank_by_value(df: pd.DataFrame) -> pd.DataFrame:
    """Not acceptable orders first, the largest amount first; acceptable orders last."""
    df = df.copy()
    df["_acceptable"] = df["match_tier"] == "full_match"
    return df.sort_values(["_acceptable", "bill_amount"], ascending=[True, False]).drop(columns="_acceptable")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def summarize(df: pd.DataFrame) -> pd.DataFrame:
    summary = (
        df.groupby("match_tier")
        .agg(orders=("order_id", "count"), total_value=("bill_amount", "sum"))
        .reset_index()
        .sort_values("orders", ascending=False)
    )
    summary["pct_of_orders"] = (summary["orders"] / len(df) * 100).round(2)
    return summary


def top_reused_refs(df: pd.DataFrame, n: int = 20) -> pd.DataFrame:
    reused = df[df["ref_reused"]]
    return (
        reused.groupby("payment_ref")
        .agg(
            distinct_orders=("order_id", "nunique"),
            total_value_at_stake=("bill_amount", "sum"),
            stores=("store", lambda s: ", ".join(sorted(set(s)))),
        )
        .reset_index()
        .sort_values("distinct_orders", ascending=False)
        .head(n)
    )


def by_store(df: pd.DataFrame) -> pd.DataFrame:
    """Per store: how many orders are not acceptable. Only a full match is acceptable."""
    df = df.copy()
    df["not_acceptable"] = df["match_tier"] != "full_match"
    # of those, the ones where no payment of that ref, amount or day was found at all
    df["no_payment_found"] = df["match_tier"].isin(
        {"missing_ref", "ref_not_found", "ref_found_no_other_match"}
    )
    out = (
        df.groupby("store")
        .agg(
            orders=("order_id", "count"),
            not_acceptable_orders=("not_acceptable", "sum"),
            not_acceptable_value=("bill_amount", lambda s: s[df.loc[s.index, "not_acceptable"]].sum()),
            no_payment_found_orders=("no_payment_found", "sum"),
        )
        .reset_index()
    )
    out["not_acceptable_pct"] = (out["not_acceptable_orders"] / out["orders"] * 100).round(1)
    return out.sort_values("not_acceptable_value", ascending=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(orders_path, payments_path, config: dict, outdir: str, sandbox: set):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    orders = load_orders(orders_path, config)
    payments = load_payments(payments_path, config)
    completed_status = config["orders"]["completed_status"]

    # A sandbox store is used for testing, not a real store. Its orders
    # (dummy references like "X12345") must not be counted.
    is_sandbox = orders["store"].isin(sandbox)
    print(f"Test-store orders dropped ({', '.join(sorted(sandbox)) or 'none configured'}): {int(is_sandbox.sum())}")
    orders = orders[~is_sandbox].copy()

    # The payment export only covers a period. An order outside it cannot be
    # checked, so it is set aside instead of being called "not found".
    first_txn, last_txn = payments["txn_date"].min(), payments["txn_date"].max()
    outside = (orders["order_date"] < first_txn) | (orders["order_date"] > last_txn)
    print(f"Orders outside the payment period ({first_txn} to {last_txn}), not scored: {int(outside.sum())}")
    orders = orders[~outside].copy()

    # Only completed orders were actually paid for and delivered: a canceled,
    # pending or changed order having no matching payment is expected, so not
    # a problem. Running those through the same fraud logic
    # as completed orders produces false positives, so they're set aside
    # and reported separately instead of scored.
    completed = orders[orders["final_status"] == completed_status].copy()
    excluded = orders[orders["final_status"] != completed_status].copy()

    matched = classify(completed, payments)
    matched = add_value_and_date_match(matched, payments)
    ranked = rank_by_value(matched)

    ranked.to_csv(outdir / "reconciliation_full.csv", index=False)
    ranked.head(200).to_csv(outdir / "top_200_flagged_orders.csv", index=False)

    summary = summarize(matched)
    summary.to_csv(outdir / "summary_by_tier.csv", index=False)

    reused = top_reused_refs(matched)
    reused.to_csv(outdir / "top_reused_refs.csv", index=False)

    store = by_store(matched)
    store.to_csv(outdir / "summary_by_store.csv", index=False)

    # Non-completed orders that still cite a payment reference: not a theft question, but
    # worth checking whether the payment was refunded. No score.
    canceled_with_ref = classify(excluded[excluded["payment_ref"].notna()], payments)
    canceled_with_ref = canceled_with_ref[canceled_with_ref["match_tier"] != "ref_not_found"]
    canceled_with_ref.to_csv(outdir / "canceled_with_payment_check_refund.csv", index=False)

    excluded_summary = (
        excluded.groupby("final_status")
        .agg(orders=("order_id", "count"), total_value=("bill_amount", "sum"))
        .reset_index()
    )
    excluded_summary.to_csv(outdir / "excluded_non_completed_orders.csv", index=False)

    pd.DataFrame(
        [
            ("orders_scored", len(completed)),
            ("sandbox_orders_dropped", int(is_sandbox.sum())),
            ("orders_outside_payment_period", int(outside.sum())),
            ("non_completed_orders_not_scored", len(excluded)),
            ("non_completed_with_payment_check_refund", len(canceled_with_ref)),
            ("payment_period_start", str(first_txn)),
            ("payment_period_end", str(last_txn)),
        ],
        columns=["item", "value"],
    ).to_csv(outdir / "run_notes.csv", index=False)

    print(f"Orders scored (completed only): {len(completed)}")
    print(f"Orders excluded (not completed, reported separately): {len(excluded)}")
    print(f"  of which cite a reference that exists in the payments (check refund): {len(canceled_with_ref)}")
    print(f"Accepted card payments loaded: {len(payments)}")
    print()
    print("=== Match tier summary (completed orders only) ===")
    print(summary.to_string(index=False))
    print()
    print("=== Excluded, non-completed orders (not scored) ===")
    print(excluded_summary.to_string(index=False))
    print()
    print("=== Top 10 stores by value of not acceptable orders ===")
    print(store.head(10).to_string(index=False))
    print()
    print(f"Reports written to {outdir}/")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", default="input",
                        help="folder holding orders.csv, payments.csv and columns.xlsx (default: input)")
    parser.add_argument("--orders", help="orders file, if not in the input folder")
    parser.add_argument("--payments", help="payments file, if not in the input folder")
    parser.add_argument("--config", help="columns file, if not in the input folder")
    parser.add_argument("--outdir", default="output", help="where the reports go (default: output)")
    parser.add_argument("--sandbox",
                        help="comma-separated names of test stores to ignore "
                             "(default: the test stores in the columns file)")
    args = parser.parse_args()
    try:
        orders_path, payments_path, config_path = find_input_files(
            args.input, args.orders, args.payments, args.config)
        config = load_config(config_path)
        print(f"Using: {orders_path}, {payments_path}, {config_path}")
        sandbox = set(args.sandbox.split(",")) if args.sandbox else set(config.get("test_stores", []))
        run(orders_path, payments_path, config, args.outdir, sandbox)
    except InputError as e:
        sys.exit(f"\nCannot run: {e}")


if __name__ == "__main__":
    main()
