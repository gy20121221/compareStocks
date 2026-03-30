"""
Tests for route stability across refactored web runtime branches.

Code version: v0.3.0
"""

from __future__ import annotations

import unittest

from app import create_app


class MorePageTests(unittest.TestCase):
    def test_more_timing_page_renders_after_storage_refactor(self) -> None:
        client = create_app().test_client()

        response = client.get("/more/timing")

        self.assertEqual(response.status_code, 200)

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


if __name__ == "__main__":
    unittest.main()
