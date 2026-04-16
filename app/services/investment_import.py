"""
IBKR investment import service.

Code version: v0.2.4
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO, TextIOWrapper
from typing import Any

from app.infrastructure.storage import normalize_ticker


SCHEMA_VERSION = "3.0.0"
DEFAULT_CONVENTION_TIME = "20:00:00"
DEFAULT_CONVENTION_TIMEZONE = "America/New_York"
ZERO = Decimal("0")
CURRENCY_CODE_PATTERN = re.compile(r"\b([A-Z]{3})\b")

TYPE_MAPPING = {
    "Deposit": "deposit",
    "Buy": "buy",
    "Sell": "sell",
    "Dividend": "dividend",
    "Foreign Tax Withholding": "foreign_tax_withholding",
    "Payment in Lieu": "payment_in_lieu",
    "Debit Interest": "debit_interest",
    "Credit Interest": "credit_interest",
    "Dividend Reinvestment": "dividend_reinvestment",
    "Adjustment": "adjustment",
    "Forex Trade Component": "forex_trade_component",
}

GRANT_PATTERN = re.compile(
    r"^(?P<grant_date>\d{4}-\d{2}-\d{2})\s+\(Vesting:\s+(?P<vesting_date>\d{4}-\d{2}-\d{2})\)$"
)


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_text(value: str | None) -> str:
    return str(value or "").strip()


def _decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _parse_decimal(
    value: str | None,
    field_name: str,
    row_number: int,
    warnings: list[str],
) -> Decimal | None:
    raw = _normalize_text(value)
    if raw in {"", "-"}:
        return None
    try:
        return Decimal(raw.replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        warnings.append(
            f"Row {row_number}: invalid decimal in field '{field_name}': {value!r}"
        )
        return None


def _build_convention_datetime(date_str: str) -> str:
    return f"{date_str} {DEFAULT_CONVENTION_TIME}"


def _classify_transaction_type(
    transaction_type: str,
    description: str,
    unknown_types: set[str],
) -> str:
    if "fx translations p&l" in description.lower():
        return "fx_translation_pnl"
    mapped = TYPE_MAPPING.get(transaction_type)
    if mapped is not None:
        return mapped
    normalized = transaction_type.strip().lower().replace(" ", "_")
    unknown_types.add(transaction_type)
    return normalized


def _detect_currency(
    transaction_type: str,
    price_currency: str,
    description: str,
    symbol: str,
) -> str | None:
    description_upper = description.upper()
    description_currency_match = CURRENCY_CODE_PATTERN.search(description_upper)
    description_currency = (
        description_currency_match.group(1) if description_currency_match else None
    )
    if description_currency == "TAX":
        description_currency = None
    if "fx translations p&l" in description.lower():
        return "USD"
    if transaction_type == "Deposit":
        return None
    if transaction_type in {"Credit Interest", "Debit Interest", "Dividend", "Foreign Tax Withholding"}:
        if description_currency is not None:
            return description_currency
    normalized_price_currency = price_currency.strip()
    if normalized_price_currency and normalized_price_currency != "-":
        return normalized_price_currency
    if symbol.endswith(".HK"):
        return "HKD"
    if "us tax" in description.lower():
        return "USD"
    return None


def _build_normalized_view(
    mapped_type: str,
    quantity_dec: Decimal | None,
    price_dec: Decimal | None,
    gross_amount_dec: Decimal | None,
    commission_dec: Decimal | None,
    net_amount_dec: Decimal | None,
    *,
    is_cash_flow_override: bool | None = None,
    side_override: str | None = None,
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}

    side = side_override
    if side is None:
        if mapped_type in {"buy", "grant"}:
            side = "buy"
        elif mapped_type == "sell":
            side = "sell"
    if side:
        normalized["side"] = side

    if quantity_dec is not None:
        normalized["position_quantity"] = _decimal_to_str(quantity_dec)
        normalized["display_quantity"] = _decimal_to_str(abs(quantity_dec))

    if price_dec is not None:
        normalized["unit_price"] = _decimal_to_str(price_dec)

    if gross_amount_dec is not None:
        normalized["gross_amount"] = _decimal_to_str(gross_amount_dec)
        if mapped_type in {"buy", "sell", "grant"}:
            normalized["display_amount"] = _decimal_to_str(abs(gross_amount_dec))
        else:
            normalized["display_amount"] = _decimal_to_str(gross_amount_dec)

    if commission_dec is not None:
        normalized["commission"] = _decimal_to_str(commission_dec)
        normalized["commission_display"] = _decimal_to_str(abs(commission_dec))

    if net_amount_dec is not None:
        normalized["net_amount"] = _decimal_to_str(net_amount_dec)

    is_cash_flow = (
        is_cash_flow_override
        if is_cash_flow_override is not None
        else mapped_type not in {"fx_translation_pnl", "grant"}
    )
    normalized["is_cash_flow"] = is_cash_flow

    if net_amount_dec is not None:
        amount_key = "cash_flow_amount" if is_cash_flow else "accounting_adjustment_amount"
        normalized[amount_key] = _decimal_to_str(net_amount_dec)

    return normalized


def _iter_csv_rows(payload: bytes) -> list[list[str]]:
    with BytesIO(payload) as buffer, TextIOWrapper(
        buffer,
        encoding="utf-8-sig",
        newline="",
    ) as text_stream:
        return list(csv.reader(text_stream))


def _build_transaction_record(
    row: list[str],
    row_number: int,
    warnings: list[str],
    unknown_types: set[str],
) -> dict[str, Any] | None:
    if len(row) < 2 or row[0] != "Transaction History" or row[1] != "Data":
        return None
    if len(row) < 13:
        warnings.append(
            f"Row {row_number}: Transaction History row has fewer than 13 columns"
        )
        return None

    date_str = _normalize_text(row[2])
    description = _normalize_text(row[4])
    transaction_type = _normalize_text(row[5])
    symbol = _normalize_text(row[6])
    quantity_dec = _parse_decimal(row[7], "quantity", row_number, warnings)
    price_dec = _parse_decimal(row[8], "price", row_number, warnings)
    price_currency = _normalize_text(row[9])
    gross_amount_dec = _parse_decimal(row[10], "gross_amount", row_number, warnings)
    commission_dec = _parse_decimal(row[11], "commission", row_number, warnings)
    net_amount_dec = _parse_decimal(row[12], "net_amount", row_number, warnings)

    mapped_type = _classify_transaction_type(transaction_type, description, unknown_types)
    record: dict[str, Any] = {
        "date": date_str,
        "datetime": _build_convention_datetime(date_str),
        "type": mapped_type,
        "currency": _detect_currency(transaction_type, price_currency, description, symbol),
        "description": description,
        "source": {
            "file_kind": "transactions",
            "row_number": row_number,
            "transaction_type_raw": transaction_type,
        },
    }

    if symbol and symbol != "-":
        record["ticker"] = normalize_ticker(symbol)
    if quantity_dec is not None:
        record["quantity_raw"] = _decimal_to_str(quantity_dec)
        record["quantity_abs"] = _decimal_to_str(abs(quantity_dec))
    if price_dec is not None:
        record["price_raw"] = _decimal_to_str(price_dec)
    if gross_amount_dec is not None:
        record["gross_amount_raw"] = _decimal_to_str(gross_amount_dec)
    if commission_dec is not None:
        record["commission_raw"] = _decimal_to_str(commission_dec)
        record["commission_abs"] = _decimal_to_str(abs(commission_dec))
    if net_amount_dec is not None:
        record["net_amount_raw"] = _decimal_to_str(net_amount_dec)

    record["normalized"] = _build_normalized_view(
        mapped_type,
        quantity_dec,
        price_dec,
        gross_amount_dec,
        commission_dec,
        net_amount_dec,
    )
    return record


def _build_grant_record(
    row: list[str],
    row_number: int,
    warnings: list[str],
) -> dict[str, Any] | None:
    if len(row) < 15:
        return None
    if row[0] != "Open Positions" or row[1] != "Data" or row[2] != "Lot":
        return None

    open_field = _normalize_text(row[6])
    match = GRANT_PATTERN.match(open_field)
    if not match:
        return None

    symbol = _normalize_text(row[5])
    currency = _normalize_text(row[4]) or "USD"
    quantity_dec = _parse_decimal(row[7], "grant_quantity", row_number, warnings)
    price_dec = _parse_decimal(row[9], "grant_cost_price", row_number, warnings)
    if not symbol or quantity_dec is None or price_dec is None:
        warnings.append(
            f"Row {row_number}: unable to synthesize stock grant from Open Positions lot"
        )
        return None

    grant_date = match.group("grant_date")
    vesting_date = match.group("vesting_date")
    return {
        "date": grant_date,
        "datetime": _build_convention_datetime(grant_date),
        "type": "grant",
        "currency": currency,
        "description": f"Unvested shares from stock grant: {symbol}",
        "ticker": normalize_ticker(symbol),
        "quantity_raw": _decimal_to_str(quantity_dec),
        "quantity_abs": _decimal_to_str(abs(quantity_dec)),
        "price_raw": _decimal_to_str(price_dec),
        "gross_amount_raw": "0",
        "net_amount_raw": "0",
        "vesting_date": _build_convention_datetime(vesting_date),
        "source": {
            "file_kind": "positions",
            "row_number": row_number,
            "transaction_type_raw": "Stock Grant",
        },
        "normalized": _build_normalized_view(
            "grant",
            quantity_dec,
            price_dec,
            ZERO,
            None,
            ZERO,
            is_cash_flow_override=False,
            side_override="buy",
        ),
    }


def _extract_summary_fields(
    rows: list[list[str]],
    warnings: list[str],
) -> tuple[dict[str, Decimal | None], str | None]:
    result: dict[str, Decimal | None] = {
        "starting_cash": None,
        "ending_cash": None,
    }
    account: str | None = None

    for row_number, row in enumerate(rows, start=1):
        if len(row) >= 4 and row[0] == "Summary" and row[1] == "Data":
            field_name = _normalize_text(row[2])
            if field_name == "Starting Cash":
                result["starting_cash"] = _parse_decimal(
                    row[3], "Starting Cash", row_number, warnings
                )
            elif field_name == "Ending Cash":
                result["ending_cash"] = _parse_decimal(
                    row[3], "Ending Cash", row_number, warnings
                )
        if len(row) >= 4 and row[0] == "Transaction History" and row[1] == "Data":
            account = _normalize_text(row[3]) or account
            break

    return result, account


def _extract_open_position_summaries(
    rows: list[list[str]],
    warnings: list[str],
) -> dict[str, dict[str, str]]:
    snapshots: dict[str, dict[str, str]] = {}
    for row_number, row in enumerate(rows, start=1):
        if len(row) < 15:
            continue
        if row[0] != "Open Positions" or row[1] != "Data" or row[2] != "Summary":
            continue
        symbol = normalize_ticker(_normalize_text(row[5]))
        if not symbol:
            continue
        snapshots[symbol] = {
            "asset_category": _normalize_text(row[3]),
            "currency": _normalize_text(row[4]) or "USD",
            "quantity": _decimal_to_str(_parse_decimal(row[7], "open_quantity", row_number, warnings)) or "0",
            "cost_price": _decimal_to_str(_parse_decimal(row[9], "cost_price", row_number, warnings)) or "0",
            "cost_basis": _decimal_to_str(_parse_decimal(row[10], "cost_basis", row_number, warnings)) or "0",
            "close_price": _decimal_to_str(_parse_decimal(row[11], "close_price", row_number, warnings)) or "0",
            "value": _decimal_to_str(_parse_decimal(row[12], "value", row_number, warnings)) or "0",
            "unrealized_pl": _decimal_to_str(_parse_decimal(row[13], "unrealized_pl", row_number, warnings)) or "0",
        }
    return snapshots


def _extract_performance_summaries(
    rows: list[list[str]],
    warnings: list[str],
) -> dict[str, dict[str, str]]:
    snapshots: dict[str, dict[str, str]] = {}
    for row_number, row in enumerate(rows, start=1):
        if len(row) < 17:
            continue
        if row[0] != "Realized & Unrealized Performance Summary" or row[1] != "Data":
            continue
        asset_category = _normalize_text(row[2])
        symbol = normalize_ticker(_normalize_text(row[3]))
        if not symbol or asset_category.startswith("Total"):
            continue
        snapshots[symbol] = {
            "asset_category": asset_category,
            "realized_total": _decimal_to_str(_parse_decimal(row[9], "realized_total", row_number, warnings)) or "0",
            "unrealized_total": _decimal_to_str(_parse_decimal(row[14], "unrealized_total", row_number, warnings)) or "0",
            "total": _decimal_to_str(_parse_decimal(row[15], "total", row_number, warnings)) or "0",
            "code": _normalize_text(row[16]),
        }
    return snapshots


def _transaction_quantity_for_replay(record: dict[str, Any]) -> Decimal | None:
    if "quantity_abs" in record:
        quantity_abs = Decimal(str(record["quantity_abs"]))
        if record.get("type") == "sell":
            return quantity_abs
    if "quantity_raw" in record:
        return Decimal(str(record["quantity_raw"]))
    normalized_quantity = record.get("normalized", {}).get("display_quantity")
    if normalized_quantity is None:
        return None
    return Decimal(str(normalized_quantity))


def _replay_holdings(transactions: list[dict[str, Any]]) -> dict[str, Decimal]:
    holdings: dict[str, Decimal] = {}
    for record in transactions:
        ticker = _normalize_text(record.get("ticker"))
        if not ticker:
            continue
        quantity_dec = _transaction_quantity_for_replay(record)
        if quantity_dec is None:
            continue
        normalized_type = _normalize_text(record.get("type")).lower()
        holdings.setdefault(ticker, ZERO)
        if normalized_type in {"buy", "dividend_reinvestment", "grant"}:
            holdings[ticker] += quantity_dec
        elif normalized_type == "sell":
            holdings[ticker] -= abs(quantity_dec)
        if holdings[ticker] == ZERO:
            holdings.pop(ticker, None)
    return holdings


def _validate_holdings(
    transactions: list[dict[str, Any]],
    open_position_snapshots: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    replayed = _replay_holdings(transactions)
    mismatches: list[dict[str, str]] = []
    for symbol in sorted(set(replayed) | set(open_position_snapshots)):
        replayed_quantity = replayed.get(symbol, ZERO)
        snapshot = open_position_snapshots.get(symbol)
        snapshot_quantity = Decimal(snapshot["quantity"]) if snapshot else ZERO
        if replayed_quantity != snapshot_quantity:
            mismatches.append(
                {
                    "ticker": symbol,
                    "replayed_quantity": _decimal_to_str(replayed_quantity) or "0",
                    "open_positions_quantity": _decimal_to_str(snapshot_quantity) or "0",
                }
            )
    return mismatches


def _sort_transactions(transactions: list[dict[str, Any]]) -> None:
    transactions.sort(
        key=lambda item: (
            item.get("date", ""),
            _normalize_text(item.get("source", {}).get("file_kind")),
            int(item.get("source", {}).get("row_number", 0)),
        )
    )


def _normalize_snapshot_keys(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}

    normalized_snapshot: dict[str, Any] = {}
    for raw_ticker, payload in snapshot.items():
        normalized_ticker = normalize_ticker(str(raw_ticker or ""))
        if not normalized_ticker:
            continue
        if normalized_ticker in normalized_snapshot and isinstance(normalized_snapshot[normalized_ticker], dict) and isinstance(payload, dict):
            normalized_snapshot[normalized_ticker] = {
                **normalized_snapshot[normalized_ticker],
                **payload,
            }
            continue
        normalized_snapshot[normalized_ticker] = payload
    return normalized_snapshot


def normalize_investment_payload_tickers(payload: dict[str, Any]) -> dict[str, Any]:
    transactions = payload.get("transactions")
    if isinstance(transactions, list):
        for txn in transactions:
            if not isinstance(txn, dict):
                continue
            raw_ticker = txn.get("ticker")
            if raw_ticker:
                txn["ticker"] = normalize_ticker(str(raw_ticker))

    payload["position_snapshot"] = _normalize_snapshot_keys(payload.get("position_snapshot"))
    payload["performance_snapshot"] = _normalize_snapshot_keys(payload.get("performance_snapshot"))

    summary = payload.get("summary")
    if isinstance(summary, dict):
        holdings_validation = summary.get("holdings_validation")
        mismatches = holdings_validation.get("mismatches") if isinstance(holdings_validation, dict) else None
        if isinstance(mismatches, list):
            for mismatch in mismatches:
                if not isinstance(mismatch, dict):
                    continue
                raw_ticker = mismatch.get("ticker")
                if raw_ticker:
                    mismatch["ticker"] = normalize_ticker(str(raw_ticker))

    return payload


def _ensure_expected_sections(
    transaction_rows: list[list[str]],
    positions_rows: list[list[str]],
) -> None:
    has_transaction_history = any(
        len(row) >= 2 and row[0] == "Transaction History" and row[1] == "Data"
        for row in transaction_rows
    )
    has_summary_cash = any(
        len(row) >= 4 and row[0] == "Summary" and row[1] == "Data" and row[2] in {"Starting Cash", "Ending Cash"}
        for row in transaction_rows
    )
    has_realized_summary = any(
        len(row) >= 2 and row[0] == "Realized & Unrealized Performance Summary" and row[1] == "Data"
        for row in positions_rows
    )
    has_open_positions = any(
        len(row) >= 2 and row[0] == "Open Positions" and row[1] in {"Data", "Total"}
        for row in positions_rows
    )
    if not has_transaction_history or not has_summary_cash:
        raise ValueError(
            "The first CSV does not look like the IBKR Transaction History export."
        )
    if not has_realized_summary or not has_open_positions:
        raise ValueError(
            "The second CSV does not look like the IBKR Realized Summary statement export."
        )


def build_investment_payload_from_ibkr_csvs(
    transaction_csv_bytes: bytes,
    positions_csv_bytes: bytes,
) -> dict[str, Any]:
    """Build the investment payload entirely in memory."""
    transaction_rows = _iter_csv_rows(transaction_csv_bytes)
    positions_rows = _iter_csv_rows(positions_csv_bytes)
    _ensure_expected_sections(transaction_rows, positions_rows)

    warnings: list[str] = []
    unknown_types: set[str] = set()
    summary_fields, account = _extract_summary_fields(transaction_rows, warnings)

    transactions = [
        record
        for row_number, row in enumerate(transaction_rows, start=1)
        for record in [_build_transaction_record(row, row_number, warnings, unknown_types)]
        if record is not None
    ]
    grants = [
        record
        for row_number, row in enumerate(positions_rows, start=1)
        for record in [_build_grant_record(row, row_number, warnings)]
        if record is not None
    ]

    open_position_snapshots = _extract_open_position_summaries(positions_rows, warnings)
    performance_snapshots = _extract_performance_summaries(positions_rows, warnings)
    transactions.extend(grants)
    _sort_transactions(transactions)

    holdings_mismatches = _validate_holdings(transactions, open_position_snapshots)

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generator": {
            "name": "ibkr_csv_to_investment_json",
            "version": SCHEMA_VERSION,
            "generated_at": _now_iso(),
        },
        "broker": "ibkr",
        "account": account,
        "datetime_policy": {
            "date_field_meaning": "Original trading date from CSV",
            "datetime_field_meaning": (
                "Business-convention datetime derived from date "
                f"with default time {DEFAULT_CONVENTION_TIME}"
            ),
            "timezone": DEFAULT_CONVENTION_TIMEZONE,
            "source_has_intraday_timestamp": False,
        },
        "summary": {
            "starting_cash_raw": _decimal_to_str(summary_fields["starting_cash"]),
            "ending_cash_raw": _decimal_to_str(summary_fields["ending_cash"]),
            "transaction_count": len(transactions) - len(grants),
            "grant_count": len(grants),
            "total_record_count": len(transactions),
            "unknown_transaction_type_count": len(unknown_types),
            "unknown_transaction_types": sorted(unknown_types),
            "warning_count": len(warnings),
            "warnings": warnings,
            "holdings_validation": {
                "matched": not holdings_mismatches,
                "mismatch_count": len(holdings_mismatches),
                "mismatches": holdings_mismatches,
            },
            "open_position_count": len(open_position_snapshots),
            "performance_symbol_count": len(performance_snapshots),
        },
        "starting_cash": _decimal_to_str(summary_fields["starting_cash"]),
        "ending_cash": _decimal_to_str(summary_fields["ending_cash"]),
        "position_snapshot": open_position_snapshots,
        "performance_snapshot": performance_snapshots,
        "transactions": transactions,
    }
    normalize_investment_payload_tickers(payload)
    payload["summary"]["json_size_bytes"] = len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    )
    return payload
