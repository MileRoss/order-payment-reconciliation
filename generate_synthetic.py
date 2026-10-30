"""
Generate a fully invented dataset in the format the pipeline expects.

Stores, orders, amounts and payments are random (fixed seed, so the result
is always the same). The problems the pipeline is meant to find are planted on
purpose, at rates chosen by hand, not measured. Results on this dataset say
nothing about any real business; they only show that the pipeline recovers
what was planted (see ground_truth.csv).

Usage:
    python generate_synthetic.py --outdir synthetic_data/
Then:
    python reconcile.py --input synthetic_data --outdir synthetic_output/
"""

import argparse
import shutil
import string
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
START = pd.Timestamp("2025-01-01")
DAYS = 365
PAYMENTS_END = START + pd.Timedelta(days=DAYS - 10)  # the payments file stops 10 days early

# orders per day, and the share of orders that are planted problems, per store
STORES = {
    "store1": {"per_day": 5, "bad": 0.03},
    "store2": {"per_day": 3, "bad": 0.06},
    "store3": {"per_day": 4, "bad": 0.35},  # the store with the most planted problems
    "store4": {"per_day": 2, "bad": 0.02},
    "store5": {"per_day": 6, "bad": 0.04},
}
SANDBOX = "test store"


def main(outdir: str):
    rng = np.random.default_rng(SEED)
    used_refs = set()

    def new_ref():
        while True:
            code = "S" + "".join(rng.choice(list(string.ascii_uppercase + string.digits), 9))
            if code not in used_refs:
                used_refs.add(code)
                return code

    def amount():
        return round(float(np.clip(rng.lognormal(2.4, 0.7), 1.5, 120)), 2)

    orders, truth, payments = [], [], []
    order_id = 0

    def add_order(store, when, ref, value, status, kind):
        nonlocal order_id
        order_id += 1
        for _ in range(int(rng.integers(1, 5))):  # one row per product line
            orders.append({
                "order_id": order_id,
                "store": store,
                "order_date": when.strftime("%d.%m.%Y %H:%M:%S"),
                "payment_ref": ref,
                "status": status,
                "amount": f"{value:.2f}".replace(".", ","),
            })
        truth.append({"order_id": order_id, "store": store, "final_status": status, "planted": kind})

    def add_payment(ref, when, value, status="accepted"):
        payments.append({
            "date": when.strftime("%Y-%m-%d %H:%M:%S"),
            "payment_ref": ref,
            "type": "sale",
            "status": status,
            "amount": value,
        })

    reused_ref = {}  # one real payment later cited by many orders, per store
    for store, cfg in STORES.items():
        reused_ref[store] = new_ref()

    for store, cfg in STORES.items():
        for day in range(DAYS):
            for _ in range(rng.poisson(cfg["per_day"])):
                when = START + pd.Timedelta(days=day, seconds=int(rng.integers(8 * 3600, 22 * 3600)))
                value = amount()
                roll = rng.random()

                if roll < 0.07:  # canceled: normally no ref, no payment
                    has_ref = rng.random() < 0.03
                    ref = new_ref() if has_ref else None
                    if has_ref:
                        add_payment(ref, when, value)  # paid, then canceled: refund question
                    add_order(store, when, ref, value, "Canceled", "canceled_with_payment" if has_ref else "canceled")
                    continue
                if roll < 0.075:
                    add_order(store, when, None, value, str(rng.choice(["Pending", "StockChanged"])), "other_status")
                    continue

                if rng.random() < cfg["bad"]:
                    kind = rng.choice(["no_payment_wrong_ref", "reused_ref", "failed_payment", "no_ref"],
                                      p=[0.4, 0.4, 0.1, 0.1])
                    if kind == "no_payment_wrong_ref":
                        add_order(store, when, new_ref(), value, "Completed", kind)
                    elif kind == "reused_ref":
                        add_order(store, when, reused_ref[store], value, "Completed", kind)
                    elif kind == "failed_payment":
                        ref = new_ref()
                        add_payment(ref, when, value, status="failed")
                        add_order(store, when, ref, value, "Completed", kind)
                    else:
                        add_order(store, when, None, value, "Completed", kind)
                    continue

                pay_time = when + pd.Timedelta(minutes=int(rng.integers(0, 6)))
                if rng.random() < 0.30:  # a payment exists, but the reference typed on the order is wrong
                    add_payment(new_ref(), pay_time, value)
                    add_order(store, when, new_ref(), value, "Completed", "wrong_ref_real_payment")
                else:  # honest order
                    ref = new_ref()
                    add_payment(ref, pay_time, value)
                    typed = ref.lower() if rng.random() < 0.02 else ref  # typed in lowercase
                    add_order(store, when, typed, value, "Completed", "honest")

    # The reused references need a real payment behind them (one genuine sale each).
    for store, ref in reused_ref.items():
        add_payment(ref, START + pd.Timedelta(days=30, hours=11), amount())

    # Sandbox store with dummy references: must be ignored.
    for _ in range(15):
        add_order(SANDBOX, START + pd.Timedelta(days=int(rng.integers(0, 300)), hours=10), "X12345",
                  amount(), "Completed", "sandbox")

    orders = pd.DataFrame(orders)
    payments = pd.DataFrame(payments)
    payments = payments[pd.to_datetime(payments["date"]) <= PAYMENTS_END]  # the file ends early

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    orders.to_csv(out / "orders.tsv", sep="\t", index=False)
    payments.sort_values("date").to_csv(out / "payments.csv", index=False)
    pd.DataFrame(truth).to_csv(out / "ground_truth.csv", index=False)
    shutil.copy(Path(__file__).with_name("columns_example.xlsx"), out / "columns.xlsx")
    print(f"{orders['order_id'].nunique()} orders, {len(payments)} payments written to {out}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", default="synthetic_data")
    main(parser.parse_args().outdir)
