"""
Reading the input files.

This is the only place that knows what the user's files look like. It finds the
files in the input folder, reads the columns file (a plain two-column table that
says which column of the user's files is which), works out how the files are
written (separator, encoding, date order, decimal mark), and returns two clean
tables with fixed internal column names:

    orders:    order_id, store, payment_ref, order_date, final_status, bill_amount
    payments:  payment_ref, txn_amount, txn_date

The matching logic in reconcile.py only ever sees those names, so it does not
change when the input layout does.

Every problem with the input (missing file, missing column, unreadable date)
stops the run with a plain message that says what to fix.
"""

import difflib
import re
from pathlib import Path

import pandas as pd


class InputError(Exception):
    """A problem with the user's files or columns file, with a plain message."""


# ---------------------------------------------------------------------------
# What the script needs, in plain words. These labels are the left-hand column
# of the columns table, and they are used in error messages.
# ---------------------------------------------------------------------------

# (section, field) -> label shown to the user
LABELS = {
    ("orders", "order_id"): "Order number",
    ("orders", "store"): "Store",
    ("orders", "order_date"): "Order date",
    ("orders", "payment_ref"): "Payment reference on the order",
    ("orders", "status"): "Order status",
    ("orders", "amount"): "Order amount",
    ("orders", "completed_status"): "Status value of a finished, paid order",
    ("payments", "payment_ref"): "Payment reference in the payments file",
    ("payments", "date"): "Payment date",
    ("payments", "amount"): "Payment amount",
    ("optional", "test_stores"): "Test stores to ignore",
    ("optional", "filter1_column"): "Payments filter 1: column",
    ("optional", "filter1_value"): "Payments filter 1: value",
    ("optional", "filter2_column"): "Payments filter 2: column",
    ("optional", "filter2_value"): "Payments filter 2: value",
    ("advanced", "orders_date_first"): "Order dates: day first or month first",
    ("advanced", "payments_date_first"): "Payment dates: day first or month first",
}
REQUIRED = [k for k in LABELS if k[0] in ("orders", "payments")]
HEADINGS = ["orders file", "payments file", "values to type", "optional", "advanced"]
HEADER_ROW = "the script needs"

ORDER_FILE_NAMES = ["orders.csv", "orders.tsv", "orders.txt", "orders.xlsx"]
PAYMENT_FILE_NAMES = ["payments.csv", "payments.tsv", "payments.txt", "payments.xlsx"]
CONFIG_FILE_NAMES = ["columns.xlsx", "columns.csv"]

DELIMITER_WORDS = {"\t": "tab", ",": "comma", ";": "semicolon", "|": "pipe"}


def _norm(text) -> str:
    """Lower-case letters and digits only, so small typing differences do not matter."""
    return re.sub(r"[^a-z0-9]", "", str(text).casefold())


LABEL_LOOKUP = {_norm(label): key for key, label in LABELS.items()}


# ---------------------------------------------------------------------------
# Finding the files
# ---------------------------------------------------------------------------

def _find_one(folder: Path, names: list, what: str) -> Path:
    found = [folder / n for n in names if (folder / n).is_file()]
    if not found:
        raise InputError(f"No {what} file in '{folder}'. Put a file named one of: {', '.join(names)}.")
    if len(found) > 1:
        raise InputError(
            f"More than one {what} file in '{folder}': {', '.join(p.name for p in found)}. Keep only one."
        )
    return found[0]


def find_input_files(folder: str, orders=None, payments=None, config=None):
    """Return (orders_path, payments_path, config_path). Explicit paths win."""
    folder = Path(folder)
    if not (orders and payments and config) and not folder.is_dir():
        raise InputError(
            f"Input folder '{folder}' not found. Create it and put in three files: an orders file, "
            "a payments file and the columns file (columns.xlsx). See the README."
        )
    orders_path = Path(orders) if orders else _find_one(folder, ORDER_FILE_NAMES, "orders")
    payments_path = Path(payments) if payments else _find_one(folder, PAYMENT_FILE_NAMES, "payments")
    if config:
        config_path = Path(config)
    else:
        try:
            config_path = _find_one(folder, CONFIG_FILE_NAMES, "columns")
        except InputError as e:
            if "No columns file" in str(e):
                raise InputError(
                    f"No columns file in '{folder}'. It tells the script which column of your files is which. "
                    "Copy columns_example.xlsx into the folder, rename it columns.xlsx, "
                    "and fill in the right-hand column."
                )
            raise
    for p in (orders_path, payments_path, config_path):
        if not p.is_file():
            raise InputError(f"File not found: {p}")
    return orders_path, payments_path, config_path


# ---------------------------------------------------------------------------
# Reading the columns file
# ---------------------------------------------------------------------------

def load_config(path) -> dict:
    """Read the columns file (.xlsx or .csv) into one settings dictionary."""
    path = Path(path)
    if path.suffix.lower() not in (".xlsx", ".csv", ".txt"):
        raise InputError(f"The columns file '{path.name}' must be .xlsx or .csv.")
    config = _config_from_table(path)
    config["_source"] = path.name
    return config


def _config_from_table(path: Path) -> dict:
    if path.suffix.lower() == ".xlsx":
        try:
            table = pd.read_excel(path, sheet_name=0, header=None, dtype=str)
        except Exception as e:
            raise InputError(f"Cannot open {path.name}: {e}")
    else:
        text, encoding = _decode(path)
        first = text.splitlines()[0] if text else ""
        delimiter = max(",;\t", key=first.count) if any(d in first for d in ",;\t") else ","
        table = pd.read_csv(path, sep=delimiter, encoding=encoding, header=None, dtype=str)

    values = {}
    for i, row in table.iterrows():
        left = row.iloc[0] if len(row) > 0 else None
        right = row.iloc[1] if len(row) > 1 else None
        if pd.isna(left) or not str(left).strip():
            continue
        norm = _norm(left)
        if norm.startswith(tuple(_norm(h) for h in HEADINGS)) or norm == _norm(HEADER_ROW):
            continue
        key = LABEL_LOOKUP.get(norm)
        if key is None:
            raise InputError(
                f"{path.name}, row {i + 1}: \"{str(left).strip()}\" is not one of the rows the script knows. "
                "Start from columns_example.xlsx and only change the right-hand column."
            )
        if key in values:
            raise InputError(f"{path.name}: the row \"{LABELS[key]}\" appears twice.")
        values[key] = None if pd.isna(right) or not str(right).strip() else str(right).strip()

    for key in REQUIRED:
        if key not in values or values[key] is None:
            raise InputError(
                f"{path.name}: please fill in \"{LABELS[key]}\" in the right-hand column "
                + ("(the name of that column in your file)." if key[1] != "completed_status"
                   else "(the status your finished orders have, for example Completed).")
            )

    def date_first(key):
        v = values.get(key)
        if v is None:
            return None
        n = _norm(v)
        if n.startswith("day"):
            return "day"
        if n.startswith("month"):
            return "month"
        raise InputError(f"{path.name}: \"{LABELS[key]}\" must be \"day first\" or \"month first\" (or left empty).")

    keep_rows = {}
    for n in ("1", "2"):
        col, val = values.get(("optional", f"filter{n}_column")), values.get(("optional", f"filter{n}_value"))
        if col and val is None or val and not col:
            raise InputError(f"{path.name}: payments filter {n} needs both a column and a value, or neither.")
        if col:
            keep_rows[col] = val

    stores = values.get(("optional", "test_stores"))
    return {
        "orders": {
            "format": {"date_first": date_first(("advanced", "orders_date_first"))},
            "columns": {f: values[("orders", f)] for f in ("order_id", "store", "order_date", "payment_ref", "status", "amount")},
            "completed_status": values[("orders", "completed_status")],
        },
        "payments": {
            "format": {"date_first": date_first(("advanced", "payments_date_first"))},
            "columns": {f: values[("payments", f)] for f in ("payment_ref", "date", "amount")},
            "keep_rows": keep_rows,
        },
        "test_stores": [s.strip() for s in re.split(r"[;]", stores) if s.strip()] if stores else [],
    }


# ---------------------------------------------------------------------------
# Working out how a file is written
# ---------------------------------------------------------------------------

def _decode(path: Path):
    """Return (text, encoding) for a text file, trying the usual encodings."""
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16"
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-sig"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), "cp1252"


def _read_table(path: Path, label: str):
    """Read a file as text. Returns (table, description of how it was read)."""
    if path.suffix.lower() == ".xlsx":
        try:
            return pd.read_excel(path, sheet_name=0, dtype=str), "Excel"
        except ValueError as e:
            raise InputError(f"Cannot read the {label} file '{path.name}': {e}")

    text, encoding = _decode(path)
    header = text.lstrip("\ufeff").splitlines()[0] if text.strip() else ""
    delimiter = max(",;\t|", key=header.count) if any(d in header for d in ",;\t|") else ","
    df = pd.read_csv(path, sep=delimiter, encoding=encoding, dtype=str)
    return df, f"{DELIMITER_WORDS.get(delimiter, repr(delimiter))}-separated, {encoding}"


def _pick_columns(df: pd.DataFrame, mapping: dict, path: Path, section: str, label: str, source: str) -> pd.DataFrame:
    """Check every named column exists (ignoring capitals and stray spaces), then rename."""
    by_norm = {str(c).strip().casefold(): c for c in df.columns}
    rename = {}
    for field, column in mapping.items():
        actual = column if column in df.columns else by_norm.get(str(column).strip().casefold())
        if actual is None:
            close = difflib.get_close_matches(str(column), [str(c) for c in df.columns], n=1, cutoff=0.6)
            hint = f" Did you mean \"{close[0]}\"?" if close else ""
            raise InputError(
                f"The {label} file '{path.name}' has no column \"{column}\" (you wrote it next to "
                f"\"{LABELS[(section, field)]}\" in {source}).{hint} "
                f"Columns found: {', '.join(map(str, df.columns))}."
            )
        rename[actual] = field
    return df.rename(columns=rename)


# ---------------------------------------------------------------------------
# Amounts and dates
# ---------------------------------------------------------------------------

def _parse_amount(series: pd.Series, path: Path, label: str):
    """Return (numbers, the decimal mark used). Handles 1.234,56 and 1,234.56 alike."""
    text = series.astype(str).str.replace(r"[\s €$£]", "", regex=True)
    has_comma = text.str.contains(r",\d{1,2}$")
    has_point = text.str.contains(r"\.\d{1,2}$")
    if has_comma.any() and has_point.any():
        example_comma, example_point = text[has_comma].iloc[0], text[has_point].iloc[0]
        raise InputError(
            f"The amounts in the {label} file '{path.name}' mix two styles "
            f"(for example '{example_comma}' and '{example_point}'). Make the column consistent."
        )
    decimal = "," if has_comma.any() else "."
    thousands = "." if decimal == "," else ","
    text = text.str.replace(rf"(?<=\d)\{thousands}(?=\d{{3}}(?!\d))", "", regex=True)
    if decimal != ".":
        text = text.str.replace(decimal, ".", regex=False)
    values = pd.to_numeric(text, errors="coerce")
    bad = series[values.isna()]
    if len(bad):
        examples = ", ".join(repr(v) for v in bad.head(3))
        raise InputError(
            f"{len(bad)} amounts in the {label} file '{path.name}' are not numbers (for example {examples})."
        )
    return values.astype(float), ("comma" if decimal == "," else "point")


_SEPARATORS = ["-", "/", ".", " "]
_ORDERS = {"ymd": "%Y{s}%m{s}%d", "dmy": "%d{s}%m{s}%Y", "mdy": "%m{s}%d{s}%Y"}
_TIMES = ["", " %H:%M:%S", " %H:%M", "T%H:%M:%S", "T%H:%M"]


def _date_candidates():
    for order, pattern in _ORDERS.items():
        for sep in _SEPARATORS:
            for tail in _TIMES:
                yield order, pattern.format(s=sep) + tail


def _words(fmt: str) -> str:
    return (fmt.replace("%Y", "year").replace("%m", "month").replace("%d", "day")
               .replace("%H", "hh").replace("%M", "mm").replace("%S", "ss").replace("T", " "))


def _parse_dates(series: pd.Series, date_first, path: Path, label: str, source: str = "the columns file"):
    """Return (dates, the format used). Works out the format from the values."""
    values = series.dropna().astype(str).str.strip().unique()
    if not len(values):
        return pd.to_datetime(series, errors="coerce"), "none"
    first = {"day": "dmy", "month": "mdy"}.get(date_first)
    ok = []
    for order, candidate in _date_candidates():
        if first and order != first and order != "ymd":
            continue
        if pd.to_datetime(pd.Series(values), format=candidate, errors="coerce").notna().all():
            ok.append((order, candidate))
    orders = {o for o, _ in ok}
    if not ok:
        raise InputError(
            f"The dates in the {label} file '{path.name}' are written in a way the script does not "
            f"recognise (first value: {values[0]!r}). Usual styles such as 31.12.2025, 31/12/2025 "
            "14:30 or 2025-12-31 are understood. If yours looks different, change the date column "
            "in your file to one of those."
        )
    if "dmy" in orders and "mdy" in orders:
        raise InputError(
            f"Cannot tell whether dates like '{values[0]}' in the {label} file '{path.name}' are day/month "
            f"or month/day. In {source}, fill in the row \"{LABELS[('advanced', label + '_date_first')]}\" "
            "with \"day first\" or \"month first\"."
        )
    used = ok[0][1]
    dates = pd.to_datetime(series.astype(str).str.strip().where(series.notna()), format=used, errors="coerce")
    n_bad = int(dates.isna().sum()) - int(series.isna().sum())
    if n_bad > 0:
        print(f"Warning: {n_bad} dates in the {label} file '{path.name}' could not be read.")
    return dates, _words(used)


# ---------------------------------------------------------------------------
# The two loaders
# ---------------------------------------------------------------------------

def _same_text(series: pd.Series, wanted: str) -> pd.Series:
    return series.astype(str).str.strip().str.casefold() == str(wanted).strip().casefold()


def load_orders(path, config: dict) -> pd.DataFrame:
    """One row per order. Input rows can repeat an order (one row per product line):
    the first value per order is kept, so an order total repeated on every line is
    counted once, never summed."""
    path = Path(path)
    sec, source = config["orders"], config.get("_source", "the columns file")
    df, how = _read_table(path, "orders")
    df = _pick_columns(df, sec["columns"], path, "orders", "orders", source)

    df["bill_amount"], decimal = _parse_amount(df["amount"], path, "orders")
    df["order_date"], date_words = _parse_dates(df["order_date"], sec["format"].get("date_first"), path, "orders", source)
    print(f"  orders: {path.name} ({how}); dates {date_words}; amounts with decimal {decimal}")

    # A status written with different capitals or spaces still counts as the finished status.
    done = sec["completed_status"]
    is_done = _same_text(df["status"], done)
    if not is_done.any():
        found = df["status"].value_counts().head(8)
        listing = ", ".join(f'"{v}" ({n} rows)' for v, n in found.items())
        raise InputError(
            f"No order has the status \"{done}\" (the value you wrote next to "
            f"\"{LABELS[('orders', 'completed_status')]}\" in {source}). "
            f"The status column of '{path.name}' contains: {listing}. "
            "Type the one that marks a finished, paid order."
        )
    df["status"] = df["status"].where(~is_done, done)

    # Order numbers sort as numbers when they are numbers.
    try:
        df["order_id"] = pd.to_numeric(df["order_id"])
    except (ValueError, TypeError):
        pass

    orders = (
        df.groupby("order_id")
        .agg(
            store=("store", "first"),
            payment_ref=("payment_ref", "first"),
            order_date=("order_date", "first"),
            final_status=("status", "first"),
            bill_amount=("bill_amount", "first"),
        )
        .reset_index()
    )
    # References are compared ignoring letter case and stray spaces. Missing ones stay missing.
    orders["payment_ref"] = orders["payment_ref"].str.strip().str.upper()
    return orders


def load_payments(path, config: dict) -> pd.DataFrame:
    """Keep only the rows that count as an accepted card payment."""
    path = Path(path)
    sec, source = config["payments"], config.get("_source", "the columns file")
    df, how = _read_table(path, "payments")

    # Optional filter, for exports that also list transfers, refunds and failed attempts.
    for column, wanted in (sec.get("keep_rows") or {}).items():
        by_norm = {str(c).strip().casefold(): c for c in df.columns}
        actual = column if column in df.columns else by_norm.get(str(column).strip().casefold())
        if actual is None:
            raise InputError(
                f"The payments file '{path.name}' has no column \"{column}\" (used in the payments "
                f"filter in {source}). Columns found: {', '.join(map(str, df.columns))}."
            )
        df = df[_same_text(df[actual], wanted)]
    if df.empty:
        raise InputError(
            f"No payment left in '{path.name}' after the payments filter in {source}. "
            "Check the values are spelled as in the file."
        )

    df = _pick_columns(df.copy(), sec["columns"], path, "payments", "payments", source)
    amounts, decimal = _parse_amount(df["amount"], path, "payments")
    dates, date_words = _parse_dates(df["date"], sec["format"].get("date_first"), path, "payments", source)
    print(f"  payments: {path.name} ({how}); dates {date_words}; amounts with decimal {decimal}")
    payments = pd.DataFrame({
        "payment_ref": df["payment_ref"].astype(str).str.strip().str.upper(),
        "txn_amount": amounts,
        "txn_date": dates,
    })

    dup = payments["payment_ref"][payments["payment_ref"].duplicated()]
    if len(dup):
        raise InputError(
            f"{dup.nunique()} payment references appear more than once in '{path.name}' "
            f"(for example {dup.iloc[0]!r}). Each reference must identify one payment: remove "
            "duplicates, or use the payments filter to keep only the payments."
        )
    return payments
