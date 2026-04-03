"""
Tests for route stability across refactored web runtime branches.

Code version: v0.3.2
"""

from __future__ import annotations

import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from app import create_app
from app.infrastructure.storage import INVESTMENT_STORE_PATH, history_store_path_for


class MorePageTests(unittest.TestCase):
    def _build_sample_transactions_csv(self) -> str:
        return "\n".join([
            "Statement,Header,Field Name,Field Value",
            "Statement,Data,Title,Transaction History",
            "Summary,Header,Field Name,Field Value",
            "Summary,Data,Starting Cash,1000.00",
            "Summary,Data,Ending Cash,899.00",
            "Transaction History,Header,Date,Account,Description,Transaction Type,Symbol,Quantity,Price,Price Currency,Gross Amount ,Commission,Net Amount",
            "Transaction History,Data,2026-03-01,U***TEST,Example Buy,Buy,QQQ,1,100,USD,-100,-1,-101",
        ]) + "\n"

    def _build_sample_positions_csv(self) -> str:
        return "\n".join([
            "Statement,Header,Field Name,Field Value",
            "Statement,Data,Title,Realized Summary",
            "Realized & Unrealized Performance Summary,Header,Asset Category,Symbol,Cost Adj.,Realized S/T Profit,Realized S/T Loss,Realized L/T Profit,Realized L/T Loss,Realized Total,Unrealized S/T Profit,Unrealized S/T Loss,Unrealized L/T Profit,Unrealized L/T Loss,Unrealized Total,Total,Code",
            "Realized & Unrealized Performance Summary,Data,Stocks,QQQ,0,0,0,0,0,0,5,0,0,0,5,5,",
            "Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Open,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code",
            "Open Positions,Data,Summary,Stocks,USD,QQQ,-,1,1,100,100,105,105,5,",
            "Open Positions,Total,,Stocks,USD,,,,,,100,,105,5,",
        ]) + "\n"

    def test_more_timing_page_renders_after_storage_refactor(self) -> None:
        client = create_app().test_client()

        response = client.get("/more/timing")

        self.assertEqual(response.status_code, 200)

    def test_more_investment_page_renders_from_more_section(self) -> None:
        client = create_app().test_client()

        response = client.get("/more/investment")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)

        self.assertIn("Investment", body)
        self.assertIn('<article class="report-card trade-performance-card investment-report-card">', body)
        self.assertIn('<article class="chart-surface investment-view-surface" id="investment_view_surface"', body)
        self.assertIn('<article class="chart-surface investment-history-surface" id="investment_history_surface"', body)

    def test_more_investment_page_exposes_dual_csv_import_form(self) -> None:
        client = create_app().test_client()

        response = client.get("/more/investment")
        body = response.get_data(as_text=True)

        self.assertIn('id="transactions_csv"', body)
        self.assertIn('id="positions_csv"', body)
        self.assertIn('enctype="multipart/form-data"', body)
        self.assertIn('Your original CSV files are processed in memory only', body)
        self.assertIn('id="investment_import_feedback_message"', body)
        self.assertIn('notice-floating-banner', body)
        self.assertIn('id="investment_import_submit_button"', body)

    def test_more_investment_page_exposes_markdown_export_button(self) -> None:
        client = create_app().test_client()

        response = client.get("/more/investment")
        body = response.get_data(as_text=True)

        self.assertIn('class="export-transactions-button"', body)
        self.assertIn('id="export_transactions_button"', body)
        self.assertIn('title="Export Transactions"', body)

    def test_legacy_invest_routes_redirect_to_more_investment(self) -> None:
        client = create_app().test_client()

        response = client.get("/invest")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/more/investment")

        alias_response = client.get("/more/invest")

        self.assertEqual(alias_response.status_code, 302)
        self.assertEqual(alias_response.headers["Location"], "/more/investment")

    def test_primary_workspace_pages_render_after_runtime_split(self) -> None:
        client = create_app().test_client()

        responses = {
            "/compare?ticker=QQQ&ticker=AAPL&period=3y&dividends=1": client.get(
                "/compare?ticker=QQQ&ticker=AAPL&period=3y&dividends=1"
            ),
            "/portfolio?ticker=QQQ&ticker=AAPL&weight=60&weight=40&period=3y&dividends=1": client.get(
                "/portfolio?ticker=QQQ&ticker=AAPL&weight=60&weight=40&period=3y&dividends=1"
            ),
            "/backtest?ticker=QQQ&strategy=buy-and-hold&period=1y&capital=10000": client.get(
                "/backtest?ticker=QQQ&strategy=buy-and-hold&period=1y&capital=10000"
            ),
            "/settings/network": client.get("/settings/network"),
        }

        self.assertEqual(
            {path: response.status_code for path, response in responses.items()},
            {path: 200 for path in responses},
        )

    def test_exact_range_markup_exposes_shared_date_roles(self) -> None:
        client = create_app().test_client()

        responses = {
            "/compare": client.get("/compare?ticker=QQQ&ticker=AAPL&range=exact&from=2026-03-27&to=2026-03-28"),
            "/portfolio": client.get(
                "/portfolio?ticker=QQQ&ticker=AAPL&weight=60&weight=40&range=exact&from=2026-03-27&to=2026-03-28"
            ),
            "/backtest": client.get("/backtest?ticker=QQQ&strategy=buy-and-hold&range=exact&from=2026-03-27&to=2026-03-28"),
        }

        for response in responses.values():
            body = response.get_data(as_text=True)
            self.assertIn('data-date-role="start"', body)
            self.assertIn('data-date-role="end"', body)

    def test_refactored_runtime_apis_respond_successfully(self) -> None:
        client = create_app().test_client()

        responses = {
            "/api/date-constraints?period=1y&interval=1d": client.get("/api/date-constraints?period=1y&interval=1d"),
            "/api/trade-strategy-fields?strategy=buy-and-hold": client.get(
                "/api/trade-strategy-fields?strategy=buy-and-hold"
            ),
            "/api/settings/network-status": client.get("/api/settings/network-status"),
            "/api/settings/local-market-store/page-data?page=1": client.get(
                "/api/settings/local-market-store/page-data?page=1"
            ),
            "/api/market-store/presence?ticker=AAPL": client.get("/api/market-store/presence?ticker=AAPL"),
        }

        self.assertEqual(
            {path: response.status_code for path, response in responses.items()},
            {path: 200 for path in responses},
        )

    def test_ibkr_csv_import_rebuilds_investment_store(self) -> None:
        client = create_app().test_client()
        original_bytes = INVESTMENT_STORE_PATH.read_bytes() if INVESTMENT_STORE_PATH.exists() else None

        try:
            with patch("app.web.runtime.ensure_latest_daily_caches", return_value=[]) as mocked_refresh:
                response = client.post(
                    "/api/investment/transactions",
                    data={
                        "transactions_csv": (
                            io.BytesIO(self._build_sample_transactions_csv().encode("utf-8")),
                            "sample.TRANSACTIONS.1Y.csv",
                        ),
                        "positions_csv": (
                            io.BytesIO(self._build_sample_positions_csv().encode("utf-8")),
                            "sample_20260301_20260331.csv",
                        ),
                    },
                    content_type="multipart/form-data",
                )

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertTrue(payload["success"])
            self.assertIn("does not store your original CSV files", payload["message"])
            self.assertIn("investment", payload)
            self.assertIn("freshness_refresh_failures", payload)
            mocked_refresh.assert_called_once_with(["QQQ"])

            stored = json.loads(INVESTMENT_STORE_PATH.read_text(encoding="utf-8"))
            self.assertEqual(stored["summary"]["total_record_count"], 1)
            self.assertEqual(stored["starting_cash"], "1000.00")
            self.assertEqual(stored["transactions"][0]["ticker"], "QQQ")
        finally:
            if original_bytes is None:
                if INVESTMENT_STORE_PATH.exists():
                    INVESTMENT_STORE_PATH.unlink()
            else:
                INVESTMENT_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
                INVESTMENT_STORE_PATH.write_bytes(original_bytes)

    def test_import_prewarms_all_investment_tickers_not_only_open_tickers(self) -> None:
        client = create_app().test_client()
        original_bytes = INVESTMENT_STORE_PATH.read_bytes() if INVESTMENT_STORE_PATH.exists() else None

        transactions_csv = "\n".join([
            "Statement,Header,Field Name,Field Value",
            "Statement,Data,Title,Transaction History",
            "Summary,Header,Field Name,Field Value",
            "Summary,Data,Starting Cash,1000.00",
            "Summary,Data,Ending Cash,1000.00",
            "Transaction History,Header,Date,Account,Description,Transaction Type,Symbol,Quantity,Price,Price Currency,Gross Amount ,Commission,Net Amount",
            "Transaction History,Data,2026-03-01,U***TEST,Open Position,Buy,QQQ,1,100,USD,-100,-1,-101",
            "Transaction History,Data,2026-03-02,U***TEST,Closed Position,Buy,DRAM,1,50,USD,-50,-1,-51",
            "Transaction History,Data,2026-03-03,U***TEST,Closed Position Exit,Sell,DRAM,-1,55,USD,55,-1,54",
        ]) + "\n"

        try:
            with patch("app.web.runtime.ensure_latest_daily_caches", return_value=[]) as mocked_refresh:
                response = client.post(
                    "/api/investment/transactions",
                    data={
                        "transactions_csv": (
                            io.BytesIO(transactions_csv.encode("utf-8")),
                            "sample.TRANSACTIONS.1Y.csv",
                        ),
                        "positions_csv": (
                            io.BytesIO(self._build_sample_positions_csv().encode("utf-8")),
                            "sample_20260301_20260331.csv",
                        ),
                    },
                    content_type="multipart/form-data",
                )

            self.assertEqual(response.status_code, 200)
            mocked_refresh.assert_called_once_with(["DRAM", "QQQ"])
        finally:
            if original_bytes is None:
                if INVESTMENT_STORE_PATH.exists():
                    INVESTMENT_STORE_PATH.unlink()
            else:
                INVESTMENT_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
                INVESTMENT_STORE_PATH.write_bytes(original_bytes)

    def test_investment_transactions_refresh_open_tickers_via_shared_freshness_helper(self) -> None:
        client = create_app().test_client()
        original_bytes = INVESTMENT_STORE_PATH.read_bytes() if INVESTMENT_STORE_PATH.exists() else None

        payload = {
            "starting_cash": "100.00",
            "transactions": [
                {
                    "date": "2026-03-01",
                    "type": "buy",
                    "ticker": "QQQ",
                    "quantity_raw": "1",
                    "quantity_abs": "1",
                    "price_raw": "100",
                    "net_amount_raw": "-100",
                    "normalized": {"display_quantity": "1", "net_amount": "-100"},
                }
            ],
        }

        try:
            INVESTMENT_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            INVESTMENT_STORE_PATH.write_text(json.dumps(payload), encoding="utf-8")
            with patch("app.web.runtime.ensure_latest_daily_caches", return_value=[]) as mocked_refresh:
                response = client.get("/api/investment/transactions")

            self.assertEqual(response.status_code, 200)
            mocked_refresh.assert_called_once_with(["QQQ"])
        finally:
            if original_bytes is None:
                if INVESTMENT_STORE_PATH.exists():
                    INVESTMENT_STORE_PATH.unlink()
            else:
                INVESTMENT_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
                INVESTMENT_STORE_PATH.write_bytes(original_bytes)

    def test_investment_transactions_attempts_profile_fetch_when_logo_asset_is_missing(self) -> None:
        client = create_app().test_client()
        original_bytes = INVESTMENT_STORE_PATH.read_bytes() if INVESTMENT_STORE_PATH.exists() else None

        payload = {
            "starting_cash": "100.00",
            "transactions": [
                {
                    "date": "2026-03-01",
                    "type": "buy",
                    "ticker": "SNDK",
                    "quantity_raw": "1",
                    "quantity_abs": "1",
                    "price_raw": "100",
                    "net_amount_raw": "-100",
                    "normalized": {"display_quantity": "1", "net_amount": "-100"},
                }
            ],
        }

        mock_profile = type(
            "MockQuoteProfile",
            (),
            {"company_name": "Sandisk", "logo_url": "/market-store/logos/SNDK.png"},
        )()

        try:
            INVESTMENT_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            INVESTMENT_STORE_PATH.write_text(json.dumps(payload), encoding="utf-8")
            with patch("app.web.runtime.ensure_latest_daily_caches", return_value=[]), \
                    patch("app.web.runtime.has_logo_asset", return_value=False), \
                    patch("app.web.runtime.load_profile_record", return_value={}), \
                    patch("app.web.runtime.fetch_quote_profile", return_value=mock_profile) as mocked_fetch:
                response = client.get("/api/investment/transactions")

            self.assertEqual(response.status_code, 200)
            body = response.get_json()
            self.assertEqual(body["ticker_profiles"]["SNDK"]["company_name"], "Sandisk")
            self.assertEqual(body["ticker_profiles"]["SNDK"]["logo_url"], "/market-store/logos/SNDK.png")
            mocked_fetch.assert_called_once_with("SNDK", force_refresh=False)
        finally:
            if original_bytes is None:
                if INVESTMENT_STORE_PATH.exists():
                    INVESTMENT_STORE_PATH.unlink()
            else:
                INVESTMENT_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
                INVESTMENT_STORE_PATH.write_bytes(original_bytes)

    def test_investment_parquet_fetches_missing_history_via_shared_market_data_path(self) -> None:
        client = create_app().test_client()
        ticker = "DRAM"
        path = history_store_path_for(ticker)
        original_exists = path.exists()
        original_bytes = path.read_bytes() if original_exists else None

        try:
            if path.exists():
                path.unlink()

            def _mock_fetch_history(requested_ticker: str, include_dividends: bool) -> pd.DataFrame:
                self.assertEqual(requested_ticker, ticker)
                self.assertFalse(include_dividends)
                path.parent.mkdir(parents=True, exist_ok=True)
                pd.DataFrame(
                    {
                        "Date": pd.to_datetime(["2026-03-31"]),
                        "Close": [42.0],
                    }
                ).to_parquet(path, index=False)
                return pd.DataFrame({"Date": pd.to_datetime(["2026-03-31"]), "Close": [42.0]})

            with patch("app.web.runtime.fetch_history", side_effect=_mock_fetch_history) as mocked_fetch:
                response = client.get(f"/api/investment/parquet?ticker={ticker}")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertTrue(payload["success"])
            self.assertEqual(payload["ticker"], ticker)
            self.assertEqual(payload["prices"][0]["close"], 42.0)
            mocked_fetch.assert_called_once()
        finally:
            if original_exists and original_bytes is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(original_bytes)
            elif path.exists():
                path.unlink()

    def test_investment_transactions_skip_money_market_freshness_refresh(self) -> None:
        client = create_app().test_client()
        original_bytes = INVESTMENT_STORE_PATH.read_bytes() if INVESTMENT_STORE_PATH.exists() else None

        payload = {
            "starting_cash": "100.00",
            "transactions": [
                {
                    "date": "2026-03-01",
                    "type": "buy",
                    "ticker": "QQQ",
                    "quantity_raw": "1",
                    "quantity_abs": "1",
                    "price_raw": "100",
                    "net_amount_raw": "-100",
                    "normalized": {"display_quantity": "1", "net_amount": "-100"},
                },
                {
                    "date": "2026-03-02",
                    "type": "buy",
                    "ticker": "005276756",
                    "quantity_raw": "1",
                    "quantity_abs": "1",
                    "price_raw": "1",
                    "net_amount_raw": "-1",
                    "normalized": {"display_quantity": "1", "net_amount": "-1"},
                },
            ],
        }

        try:
            INVESTMENT_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
            INVESTMENT_STORE_PATH.write_text(json.dumps(payload), encoding="utf-8")
            with patch("app.web.runtime.ensure_latest_daily_caches", return_value=[]) as mocked_refresh:
                response = client.get("/api/investment/transactions")

            self.assertEqual(response.status_code, 200)
            mocked_refresh.assert_called_once_with(["QQQ"])
        finally:
            if original_bytes is None:
                if INVESTMENT_STORE_PATH.exists():
                    INVESTMENT_STORE_PATH.unlink()
            else:
                INVESTMENT_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
                INVESTMENT_STORE_PATH.write_bytes(original_bytes)

    def test_investment_parquet_skips_money_market_refresh_when_local_data_exists(self) -> None:
        client = create_app().test_client()
        ticker = "005276756"
        path = history_store_path_for(ticker)
        original_exists = path.exists()
        original_bytes = path.read_bytes() if original_exists else None

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                {
                    "Date": pd.to_datetime(["2026-03-31"]),
                    "Close": [1.0],
                }
            ).to_parquet(path, index=False)

            with patch("app.web.runtime.ensure_latest_daily_caches") as mocked_refresh:
                response = client.get(f"/api/investment/parquet?ticker={ticker}")

            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertTrue(payload["success"])
            self.assertEqual(payload["ticker"], ticker)
            self.assertEqual(payload["prices"][0]["close"], 1.0)
            mocked_refresh.assert_not_called()
        finally:
            if original_exists and original_bytes is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(original_bytes)
            elif path.exists():
                path.unlink()


if __name__ == "__main__":
    unittest.main()
