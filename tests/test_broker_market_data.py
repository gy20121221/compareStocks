"""
Tests for broker-backed market data normalization.

Code version: v0.3.0
"""

from __future__ import annotations

from types import SimpleNamespace
import unittest

import pandas as pd

from app.infrastructure.broker_market_data import (
    _candlestick_rows_to_frame,
    _parse_longbridge_timestamp,
    normalize_one_minute_store_frame,
)


class BrokerMarketDataTests(unittest.TestCase):
    def test_parse_longbridge_naive_timestamp_as_hong_kong_time(self) -> None:
        parsed = _parse_longbridge_timestamp("2026-03-27 21:30:00")

        self.assertEqual(str(parsed.tzinfo), "Asia/Hong_Kong")
        self.assertEqual(parsed.strftime("%Y-%m-%d %H:%M"), "2026-03-27 21:30")

    def test_candlestick_rows_convert_hkt_regular_session_to_new_york_dst(self) -> None:
        frame = _candlestick_rows_to_frame(
            [
                SimpleNamespace(
                    timestamp="2026-03-27 21:30:00",
                    open=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.5,
                    volume=1_000,
                    turnover=100_500,
                ),
                SimpleNamespace(
                    timestamp="2026-03-28 04:00:00",
                    open=101.0,
                    high=102.0,
                    low=100.0,
                    close=101.5,
                    volume=900,
                    turnover=91_350,
                ),
            ]
        )

        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["Date"], pd.Timestamp("2026-03-27 09:30:00"))

    def test_candlestick_rows_convert_hkt_regular_session_to_new_york_standard_time(self) -> None:
        frame = _candlestick_rows_to_frame(
            [
                SimpleNamespace(
                    timestamp="2026-11-20 22:30:00",
                    open=100.0,
                    high=101.0,
                    low=99.0,
                    close=100.5,
                    volume=1_000,
                    turnover=100_500,
                )
            ]
        )

        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["Date"], pd.Timestamp("2026-11-20 09:30:00"))

    def test_normalize_one_minute_store_frame_migrates_hkt_wall_time_cache(self) -> None:
        dataset = pd.DataFrame(
            {
                "Date": pd.to_datetime(["2026-03-27 21:30:00", "2026-03-27 21:31:00", "2026-03-28 04:00:00"]),
                "Open": [100.0, 101.0, 102.0],
                "High": [101.0, 102.0, 103.0],
                "Low": [99.0, 100.0, 101.0],
                "Close": [100.5, 101.5, 102.5],
                "Volume": [1_000, 1_100, 1_200],
                "Turnover": [100_500, 111_650, 123_000],
            }
        )

        normalized = normalize_one_minute_store_frame(dataset)

        self.assertEqual(normalized["Date"].tolist(), [pd.Timestamp("2026-03-27 09:30:00"), pd.Timestamp("2026-03-27 09:31:00")])


if __name__ == "__main__":
    unittest.main()
