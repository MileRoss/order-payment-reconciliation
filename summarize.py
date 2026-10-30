"""
Plain-language summary of the reconciliation results (optional step).

Turns the three small aggregate tables written by reconcile.py into a short
paragraph a store manager can read.

What the model sees: only the tier counts, the per-store totals and the most
reused payment references. It never sees individual orders, staff e-mails or card digits.
It does not decide which orders need follow-up; reconcile.py already did that
with fixed rules. The model only turns the numbers into sentences.

Usage:
    python summarize.py --outdir output/

With the environment variable ANTHROPIC_API_KEY set (and `pip install anthropic`)
the summary is written by the model to output/manager_summary.md. Without a key
the prompt is saved to output/summary_prompt.txt and nothing is sent anywhere.
"""

import argparse
import os
from pathlib import Path

import pandas as pd

MODEL = "claude-sonnet-5-5"


def build_prompt(outdir: Path) -> str:
    tiers = pd.read_csv(outdir / "summary_by_tier.csv")
    stores = pd.read_csv(outdir / "summary_by_store.csv").head(8)
    refs = pd.read_csv(outdir / "top_reused_refs.csv").head(5)

    return (
        "You are helping an investigator report on a review of in-store orders against card payments. "
        "Each order was checked against card-payment records by fixed rules.\n\n"
        "Write one short paragraph (at most 120 words) for a store manager. "
        "Use only the figures below. Do not name or accuse any person. "
        "Only a full match (payment reference, amount and date) is acceptable; every other order is flagged for follow-up, "
        "which does not mean theft. "
        "Say which stores have the most value in orders that are not acceptable, "
        "and mention the payment references that were reused. "
        "Do not estimate anything that is not in the tables.\n\n"
        "Orders by evidence tier (amount_and_date_other_ref means a payment of the same "
        "amount exists on the same day but the payment reference does not match; the payment export has "
        "no store information, so that payment may belong to another store):\n"
        f"{tiers.to_string(index=False)}\n\n"
        "Stores, sorted by the euro value of not acceptable orders "
        "(not acceptable = anything other than a full match):\n"
        f"{stores.to_string(index=False)}\n\n"
        "Payment references cited by more than one order:\n"
        f"{refs.to_string(index=False)}\n"
    )


def run(outdir: str):
    outdir = Path(outdir)
    prompt = build_prompt(outdir)
    (outdir / "summary_prompt.txt").write_text(prompt, encoding="utf-8")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("No ANTHROPIC_API_KEY set: prompt saved to summary_prompt.txt, nothing sent.")
        return

    import anthropic  # imported here so the rest of the project works without it

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    reply = client.messages.create(
        model=MODEL,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    text = reply.content[0].text
    (outdir / "manager_summary.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outdir", default="output")
    args = parser.parse_args()
    run(args.outdir)
