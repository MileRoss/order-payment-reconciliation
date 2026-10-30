"""
Interactive view of the reconciliation results (Streamlit).

It only reads the CSV files that reconcile.py wrote; it does not change them.

Usage:
    streamlit run app.py
"""

from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from make_dashboard import TIERS

DATA_FOLDER = Path(__file__).parent / "synthetic_output"  # the folder reconcile.py wrote to
NO_PAYMENT = {"missing_ref", "ref_not_found", "ref_found_no_other_match"}
BLUE, ORANGE = "#2a78d6", "#eb6834"

LEVEL = {key: label.replace("\n", " ") for key, (label, _) in TIERS.items()}

st.set_page_config(page_title="Order vs payment reconciliation", layout="wide")


@st.cache_data
def load(folder: str) -> pd.DataFrame:
    df = pd.read_csv(Path(folder) / "reconciliation_full.csv", parse_dates=["order_date"])
    df["level"] = df["match_tier"].map(LEVEL)
    df["acceptable"] = df["match_tier"] == "full_match"
    return df


def n(x) -> str:
    return f"{int(x):,}"


orders = load(str(DATA_FOLDER))

st.title("Order vs payment reconciliation")
st.caption("Every in-store order is checked against the card-payment records. Use the filters on the left.")
st.info(
    "Demo on **invented data**. The stores, orders and payments are generated, with problems "
    "planted on purpose, so these results say nothing about any real business."
)

# ---- filters -------------------------------------------------------------
with st.sidebar:
    st.header("Filters")
    stores = st.multiselect("Store", sorted(orders["store"].unique()), default=[])
    levels = st.multiselect("Match level", list(LEVEL.values()), default=[])
    first, last = orders["order_date"].min().date(), orders["order_date"].max().date()
    period = st.date_input("Order date", value=(first, last), min_value=first, max_value=last)
    st.caption("Leave Store and Match level empty to include all.")

view = orders
if stores:
    view = view[view["store"].isin(stores)]
if levels:
    view = view[view["level"].isin(levels)]
if len(period) == 2:
    view = view[(view["order_date"].dt.date >= period[0]) & (view["order_date"].dt.date <= period[1])]

if view.empty:
    st.warning("No order matches these filters.")
    st.stop()

# ---- four numbers --------------------------------------------------------
total = len(view)
ok = int(view["acceptable"].sum())
bad = view[~view["acceptable"]]
c1, c2, c3, c4 = st.columns(4)
c1.metric("completed orders checked", n(total))
c2.metric(f"orders acceptable · {ok / total:.1%}", n(ok))
c3.metric(f"orders not acceptable · {len(bad) / total:.1%}", n(len(bad)))
c4.metric("value of those orders", f"€{bad['bill_amount'].sum():,.0f}")

# ---- match levels --------------------------------------------------------
st.subheader("How well each order matches a payment")
st.caption("Only a full match is acceptable. The other six levels show how far an order is from it.")
by_level = (
    view.groupby(["level", "acceptable"]).size().reset_index(name="orders")
    .assign(share=lambda d: d["orders"] / total)
    .sort_values("orders", ascending=False)
)
chart = (
    alt.Chart(by_level)
    .mark_bar()
    .encode(
        y=alt.Y("level:N", sort=list(by_level["level"]), title=None, axis=alt.Axis(labelLimit=320)),
        x=alt.X("orders:Q", title="orders"),
        color=alt.Color(
            "acceptable:N",
            scale=alt.Scale(domain=[True, False], range=[BLUE, ORANGE]),
            legend=alt.Legend(title=None, labelExpr="datum.value ? 'acceptable' : 'not acceptable'"),
        ),
        tooltip=["level", "orders", alt.Tooltip("share:Q", format=".1%")],
    )
    .properties(height=34 * len(by_level) + 20)
)
st.altair_chart(chart, width="stretch")

# ---- by store ------------------------------------------------------------
st.subheader("Orders that are not acceptable, by store")
by_store = (
    view.groupby("store")
    .agg(
        orders=("order_id", "size"),
        not_acceptable=("acceptable", lambda s: int((~s).sum())),
    )
    .join(bad.groupby("store").agg(value=("bill_amount", "sum")))
    .join(bad[bad["match_tier"].isin(NO_PAYMENT)].groupby("store").size().rename("no_payment_found"))
    .fillna(0)
)
by_store["pct"] = by_store["not_acceptable"] / by_store["orders"] * 100
by_store["value"] = by_store["value"].round(0)
by_store = by_store.sort_values("pct", ascending=False).reset_index()
st.dataframe(
    by_store.rename(
        columns={
            "store": "Store",
            "orders": "All orders",
            "not_acceptable": "Not acceptable",
            "pct": "%",
            "value": "Value (€)",
            "no_payment_found": "No payment found",
        }
    )[["Store", "Not acceptable", "All orders", "%", "Value (€)", "No payment found"]],
    hide_index=True,
    width="stretch",
    column_config={
        "Not acceptable": st.column_config.NumberColumn(format="localized"),
        "All orders": st.column_config.NumberColumn(format="localized"),
        "%": st.column_config.NumberColumn(format="%.1f%%"),
        "Value (€)": st.column_config.NumberColumn(format="localized"),
        "No payment found": st.column_config.NumberColumn(format="localized"),
    },
)

# ---- the orders ----------------------------------------------------------
st.subheader("Orders to follow up")
st.caption("Not acceptable orders in the current filters, largest amount first. Download them as a CSV.")
follow_up = bad.sort_values("bill_amount", ascending=False)[
    ["order_id", "store", "order_date", "level", "bill_amount", "payment_ref", "ref_reused"]
].rename(
    columns={
        "order_id": "Order",
        "store": "Store",
        "order_date": "Order date",
        "level": "Match level",
        "bill_amount": "Amount (€)",
        "payment_ref": "Payment reference",
        "ref_reused": "Reference reused",
    }
)
st.dataframe(follow_up, hide_index=True, width="stretch", height=380)
st.download_button(
    "Download these orders (CSV)",
    follow_up.to_csv(index=False).encode("utf-8"),
    file_name="orders_to_follow_up.csv",
    mime="text/csv",
)
