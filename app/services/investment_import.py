"""
IBKR investment import service.

Code version: v0.3.0
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
    r"^(?P<grant_date>\d{4}-\d{2}-\d{2})"
    r"(?:,\s+\d{2}:\d{2}:\d{2})?"
    r"\s+\(Vesting:\s+"
    r"(?P<vesting_date>\d{4}-\d{2}-\d{2})"
    r"(?:,\s+\d{2}:\d{2}:\d{2})?"
    r"\)$"
)


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_text(value: str | None) -> str:
    return str(value or "").strip()


def _normalize_whitespace(value: str | None) -> str:
    return " ".join(_normalize_text(value).split())


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


def _build_grant_candidate(
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
            f"Row {row_number}: unable to parse stock grant lot from Open Positions"
        )
        return None

    grant_date = match.group("grant_date")
    vesting_date = match.group("vesting_date")
    return {
        "grant_date": grant_date,
        "vesting_date": vesting_date,
        "currency": currency,
        "ticker": normalize_ticker(symbol),
        "price_raw": _decimal_to_str(price_dec),
        "source": {
            "file_kind": "positions",
            "row_number": row_number,
            "transaction_type_raw": "Stock Grant",
        },
        "lot_quantity_raw": _decimal_to_str(quantity_dec),
    }


def _build_grant_record_from_candidate(
    candidate: dict[str, Any],
    quantity_dec: Decimal,
) -> dict[str, Any]:
    price_dec = Decimal(str(candidate["price_raw"]))
    grant_date = str(candidate["grant_date"])
    vesting_date = str(candidate["vesting_date"])
    symbol = str(candidate["ticker"])
    return {
        "date": grant_date,
        "datetime": _build_convention_datetime(grant_date),
        "type": "grant",
        "currency": candidate["currency"],
        "description": f"Unvested shares from stock grant: {symbol}",
        "ticker": symbol,
        "quantity_raw": _decimal_to_str(quantity_dec),
        "quantity_abs": _decimal_to_str(abs(quantity_dec)),
        "price_raw": _decimal_to_str(price_dec),
        "gross_amount_raw": "0",
        "net_amount_raw": "0",
        "vesting_date": _build_convention_datetime(vesting_date),
        "source": candidate["source"],
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


def _synthesize_grant_records(
    grant_candidates: list[dict[str, Any]],
    transactions: list[dict[str, Any]],
    open_position_snapshots: dict[str, dict[str, str]],
    warnings: list[str],
) -> list[dict[str, Any]]:
    if not grant_candidates:
        return []

    replayed_without_grants = _replay_holdings(transactions)
    candidates_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for candidate in grant_candidates:
        ticker = str(candidate.get("ticker") or "").strip()
        if not ticker:
            continue
        candidates_by_ticker.setdefault(ticker, []).append(candidate)

    grants: list[dict[str, Any]] = []
    for ticker, ticker_candidates in candidates_by_ticker.items():
        snapshot = open_position_snapshots.get(ticker)
        snapshot_quantity = Decimal(snapshot["quantity"]) if snapshot else ZERO
        known_quantity = replayed_without_grants.get(ticker, ZERO)
        remaining_quantity = snapshot_quantity - known_quantity
        if remaining_quantity <= ZERO:
            warnings.append(
                f"Ticker {ticker}: skipped stock grant synthesis because the inferred missing quantity is {remaining_quantity}."
            )
            continue

        ordered_candidates = sorted(
            ticker_candidates,
            key=lambda item: (
                str(item.get("grant_date") or ""),
                int(item.get("source", {}).get("row_number", 0)),
            ),
        )
        for index, candidate in enumerate(ordered_candidates):
            if remaining_quantity <= ZERO:
                break
            lot_quantity = Decimal(str(candidate.get("lot_quantity_raw") or "0"))
            if index == len(ordered_candidates) - 1:
                grant_quantity = remaining_quantity
            else:
                grant_quantity = min(lot_quantity, remaining_quantity)
            if grant_quantity <= ZERO:
                continue
            if grant_quantity != lot_quantity:
                warnings.append(
                    f"Ticker {ticker}: inferred stock grant quantity {grant_quantity} differs from open lot quantity {lot_quantity}."
                )
            grants.append(_build_grant_record_from_candidate(candidate, grant_quantity))
            remaining_quantity -= grant_quantity

    return grants


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


def _transaction_identity_key(record: dict[str, Any]) -> tuple[str, ...]:
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    normalized_type = _normalize_text(record.get("type")).lower()
    ticker = normalize_ticker(_normalize_text(record.get("ticker"))) if record.get("ticker") else ""
    return (
        _normalize_text(record.get("date")),
        normalized_type,
        ticker,
        _normalize_text(record.get("currency")).upper(),
        _normalize_whitespace(record.get("description")),
        _normalize_text(record.get("quantity_raw")),
        _normalize_text(record.get("price_raw")),
        _normalize_text(record.get("gross_amount_raw")),
        _normalize_text(record.get("commission_raw")),
        _normalize_text(record.get("net_amount_raw")),
        _normalize_text(record.get("vesting_date")),
        _normalize_text(source.get("transaction_type_raw")),
    )


def _is_missing_merge_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple, set)):
        return not value
    return False


def _merge_transaction_records(current: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(current)
    for key, incoming_value in incoming.items():
        current_value = merged.get(key)
        if isinstance(current_value, dict) and isinstance(incoming_value, dict):
            nested = dict(current_value)
            for nested_key, nested_incoming_value in incoming_value.items():
                nested_current_value = nested.get(nested_key)
                if _is_missing_merge_value(nested_current_value) and not _is_missing_merge_value(nested_incoming_value):
                    nested[nested_key] = nested_incoming_value
                elif not _is_missing_merge_value(nested_incoming_value):
                    nested[nested_key] = nested_incoming_value
            merged[key] = nested
            continue
        if _is_missing_merge_value(current_value) and not _is_missing_merge_value(incoming_value):
            merged[key] = incoming_value
        elif not _is_missing_merge_value(incoming_value):
            merged[key] = incoming_value
    return merged


def _merge_non_grant_transactions(
    existing_transactions: list[dict[str, Any]],
    incoming_transactions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    merged_by_key: dict[tuple[tuple[str, ...], int], dict[str, Any]] = {}
    duplicate_count = 0
    existing_occurrences: dict[tuple[str, ...], int] = {}
    for record in existing_transactions:
        if _normalize_text(record.get("type")).lower() == "grant":
            continue
        identity_key = _transaction_identity_key(record)
        occurrence_index = existing_occurrences.get(identity_key, 0)
        existing_occurrences[identity_key] = occurrence_index + 1
        merged_by_key[(identity_key, occurrence_index)] = dict(record)

    incoming_occurrences: dict[tuple[str, ...], int] = {}
    for record in incoming_transactions:
        if _normalize_text(record.get("type")).lower() == "grant":
            continue
        identity_key = _transaction_identity_key(record)
        occurrence_index = incoming_occurrences.get(identity_key, 0)
        incoming_occurrences[identity_key] = occurrence_index + 1
        composite_key = (identity_key, occurrence_index)
        existing_record = merged_by_key.get(composite_key)
        if existing_record is None:
            merged_by_key[composite_key] = dict(record)
            continue
        duplicate_count += 1
        merged_by_key[composite_key] = _merge_transaction_records(existing_record, record)

    merged_transactions = list(merged_by_key.values())
    _sort_transactions(merged_transactions)
    return merged_transactions, duplicate_count


def _payload_transactions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_transactions = payload.get("transactions")
    if not isinstance(raw_transactions, list):
        return []
    return [txn for txn in raw_transactions if isinstance(txn, dict)]


def _payload_transaction_dates(payload: dict[str, Any]) -> list[str]:
    return [
        _normalize_text(txn.get("date"))
        for txn in _payload_transactions(payload)
        if _normalize_text(txn.get("date"))
    ]


def _payload_sort_key(payload: dict[str, Any]) -> tuple[str, int, int, int, str]:
    transaction_dates = _payload_transaction_dates(payload)
    position_snapshot = payload.get("position_snapshot")
    performance_snapshot = payload.get("performance_snapshot")
    generator = payload.get("generator")
    return (
        max(transaction_dates) if transaction_dates else "",
        len(position_snapshot) if isinstance(position_snapshot, dict) else 0,
        len(performance_snapshot) if isinstance(performance_snapshot, dict) else 0,
        len(_payload_transactions(payload)),
        _normalize_text(generator.get("generated_at")) if isinstance(generator, dict) else "",
    )


def _payload_earliest_sort_key(payload: dict[str, Any]) -> tuple[str, int, str]:
    transaction_dates = _payload_transaction_dates(payload)
    generator = payload.get("generator")
    return (
        min(transaction_dates) if transaction_dates else "9999-12-31",
        -len(_payload_transactions(payload)),
        _normalize_text(generator.get("generated_at")) if isinstance(generator, dict) else "",
    )


def _pick_latest_payload(existing_payload: dict[str, Any], incoming_payload: dict[str, Any]) -> dict[str, Any]:
    return incoming_payload if _payload_sort_key(incoming_payload) >= _payload_sort_key(existing_payload) else existing_payload


def _pick_earliest_payload(existing_payload: dict[str, Any], incoming_payload: dict[str, Any]) -> dict[str, Any]:
    return incoming_payload if _payload_earliest_sort_key(incoming_payload) < _payload_earliest_sort_key(existing_payload) else existing_payload


def _summary_list(summary: dict[str, Any] | None, key: str) -> list[str]:
    if not isinstance(summary, dict):
        return []
    raw_value = summary.get(key)
    if not isinstance(raw_value, list):
        return []
    return [str(item) for item in raw_value if str(item).strip()]


def _summary_text(summary: dict[str, Any] | None, key: str) -> str | None:
    if not isinstance(summary, dict):
        return None
    value = summary.get(key)
    text = _normalize_text(value)
    return text or None


def _unique_preserving_order(values: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized_value = _normalize_text(value)
        if not normalized_value or normalized_value in seen:
            continue
        seen.add(normalized_value)
        ordered.append(normalized_value)
    return ordered


def _build_summary(
    *,
    transactions: list[dict[str, Any]],
    warnings: list[str],
    unknown_types: list[str],
    holdings_mismatches: list[dict[str, str]],
    open_position_snapshots: dict[str, dict[str, str]],
    performance_snapshots: dict[str, dict[str, str]],
    starting_cash: str | None,
    ending_cash: str | None,
    merge_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    grant_count = sum(
        1
        for record in transactions
        if _normalize_text(record.get("type")).lower() == "grant"
    )
    summary: dict[str, Any] = {
        "starting_cash_raw": starting_cash,
        "ending_cash_raw": ending_cash,
        "transaction_count": len(transactions) - grant_count,
        "grant_count": grant_count,
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
    }
    if merge_details:
        summary["incremental_import"] = merge_details
    return summary


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
    grant_candidates = [
        record
        for row_number, row in enumerate(positions_rows, start=1)
        for record in [_build_grant_candidate(row, row_number, warnings)]
        if record is not None
    ]

    open_position_snapshots = _extract_open_position_summaries(positions_rows, warnings)
    performance_snapshots = _extract_performance_summaries(positions_rows, warnings)
    grants = _synthesize_grant_records(
        grant_candidates,
        transactions,
        open_position_snapshots,
        warnings,
    )
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
        "summary": _build_summary(
            transactions=transactions,
            warnings=warnings,
            unknown_types=sorted(unknown_types),
            holdings_mismatches=holdings_mismatches,
            open_position_snapshots=open_position_snapshots,
            performance_snapshots=performance_snapshots,
            starting_cash=_decimal_to_str(summary_fields["starting_cash"]),
            ending_cash=_decimal_to_str(summary_fields["ending_cash"]),
        ),
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


def merge_investment_payloads(
    existing_payload: dict[str, Any] | None,
    incoming_payload: dict[str, Any],
) -> dict[str, Any]:
    normalized_incoming = normalize_investment_payload_tickers(incoming_payload)
    if not existing_payload:
        return normalized_incoming

    normalized_existing = normalize_investment_payload_tickers(existing_payload)
    existing_broker = _normalize_text(normalized_existing.get("broker")).lower()
    incoming_broker = _normalize_text(normalized_incoming.get("broker")).lower()
    if existing_broker and incoming_broker and existing_broker != incoming_broker:
        raise ValueError(
            "The existing local investment store belongs to a different broker import format."
        )

    existing_account = _normalize_text(normalized_existing.get("account"))
    incoming_account = _normalize_text(normalized_incoming.get("account"))
    if existing_account and incoming_account and existing_account != incoming_account:
        raise ValueError(
            "The uploaded CSV files belong to a different IBKR account than the current local investment store."
        )

    latest_payload = _pick_latest_payload(normalized_existing, normalized_incoming)
    earliest_payload = _pick_earliest_payload(normalized_existing, normalized_incoming)
    latest_grants = [
        dict(txn)
        for txn in _payload_transactions(latest_payload)
        if _normalize_text(txn.get("type")).lower() == "grant"
    ]
    merged_non_grant_transactions, duplicate_count = _merge_non_grant_transactions(
        _payload_transactions(normalized_existing),
        _payload_transactions(normalized_incoming),
    )
    merged_transactions = merged_non_grant_transactions + latest_grants
    _sort_transactions(merged_transactions)

    open_position_snapshots = _normalize_snapshot_keys(latest_payload.get("position_snapshot"))
    performance_snapshots = _normalize_snapshot_keys(latest_payload.get("performance_snapshot"))
    holdings_mismatches = _validate_holdings(merged_transactions, open_position_snapshots)

    existing_summary = normalized_existing.get("summary") if isinstance(normalized_existing.get("summary"), dict) else {}
    incoming_summary = normalized_incoming.get("summary") if isinstance(normalized_incoming.get("summary"), dict) else {}
    warnings = _unique_preserving_order(
        _summary_list(existing_summary, "warnings") + _summary_list(incoming_summary, "warnings")
    )
    unknown_types = _unique_preserving_order(
        _summary_list(existing_summary, "unknown_transaction_types")
        + _summary_list(incoming_summary, "unknown_transaction_types")
    )

    starting_cash = (
        _summary_text(earliest_payload.get("summary"), "starting_cash_raw")
        or _normalize_text(earliest_payload.get("starting_cash"))
        or None
    )
    ending_cash = (
        _summary_text(latest_payload.get("summary"), "ending_cash_raw")
        or _normalize_text(latest_payload.get("ending_cash"))
        or None
    )
    added_record_count = max(len(merged_transactions) - len(_payload_transactions(normalized_existing)), 0)
    merge_details = {
        "mode": "incremental_union",
        "existing_record_count": len(_payload_transactions(normalized_existing)),
        "imported_record_count": len(_payload_transactions(normalized_incoming)),
        "added_record_count": added_record_count,
        "duplicate_record_count": duplicate_count,
        "snapshot_source": (
            "incoming"
            if latest_payload is normalized_incoming
            else "existing"
        ),
        "account_verified": not (
            existing_account and incoming_account and existing_account != incoming_account
        ),
    }

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generator": {
            "name": "ibkr_csv_to_investment_json",
            "version": SCHEMA_VERSION,
            "generated_at": _now_iso(),
        },
        "broker": incoming_broker or existing_broker or "ibkr",
        "account": incoming_account or existing_account or None,
        "datetime_policy": (
            normalized_incoming.get("datetime_policy")
            or normalized_existing.get("datetime_policy")
            or {
                "date_field_meaning": "Original trading date from CSV",
                "datetime_field_meaning": (
                    "Business-convention datetime derived from date "
                    f"with default time {DEFAULT_CONVENTION_TIME}"
                ),
                "timezone": DEFAULT_CONVENTION_TIMEZONE,
                "source_has_intraday_timestamp": False,
            }
        ),
        "summary": _build_summary(
            transactions=merged_transactions,
            warnings=warnings,
            unknown_types=unknown_types,
            holdings_mismatches=holdings_mismatches,
            open_position_snapshots=open_position_snapshots,
            performance_snapshots=performance_snapshots,
            starting_cash=starting_cash,
            ending_cash=ending_cash,
            merge_details=merge_details,
        ),
        "starting_cash": starting_cash,
        "ending_cash": ending_cash,
        "position_snapshot": open_position_snapshots,
        "performance_snapshot": performance_snapshots,
        "transactions": merged_transactions,
    }
    normalize_investment_payload_tickers(payload)
    payload["summary"]["json_size_bytes"] = len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    )
    return payload
