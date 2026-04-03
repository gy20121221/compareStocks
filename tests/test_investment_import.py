"""
Tests for IBKR investment import normalization.

Code version: v0.1.2
"""

from __future__ import annotations

import unittest

from app.services.investment_import import build_investment_payload_from_ibkr_csvs


class InvestmentImportTests(unittest.TestCase):
    def test_import_canonicalizes_share_class_ticker_to_hyphen(self) -> None:
        transactions_csv = "\n".join([
            "Statement,Header,Field Name,Field Value",
            "Statement,Data,Title,Transaction History",
            "Summary,Header,Field Name,Field Value",
            "Summary,Data,Starting Cash,0",
            "Summary,Data,Ending Cash,478.50",
            "Transaction History,Header,Date,Account,Description,Transaction Type,Symbol,Quantity,Price,Price Currency,Gross Amount ,Commission,Net Amount",
            "Transaction History,Data,2026-04-01,U***TEST,BERKSHIRE HATHAWAY INC-CL B,Buy,BRK B,1,478.50,USD,-478.50,-0.35,-478.85",
        ]) + "\n"
        positions_csv = "\n".join([
            "Statement,Header,Field Name,Field Value",
            "Statement,Data,Title,Realized Summary",
            "Realized & Unrealized Performance Summary,Header,Asset Category,Symbol,Cost Adj.,Realized S/T Profit,Realized S/T Loss,Realized L/T Profit,Realized L/T Loss,Realized Total,Unrealized S/T Profit,Unrealized S/T Loss,Unrealized L/T Profit,Unrealized L/T Loss,Unrealized Total,Total,Code",
            "Realized & Unrealized Performance Summary,Data,Stocks,BRK B,0,0,0,0,0,0,0,0,0,0,0,0,",
            "Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Open,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code",
            "Open Positions,Data,Summary,Stocks,USD,BRK B,,1,1,478.50,478.50,478.50,478.50,0,",
        ]) + "\n"

        payload = build_investment_payload_from_ibkr_csvs(
            transactions_csv.encode("utf-8"),
            positions_csv.encode("utf-8"),
        )

        self.assertEqual(payload["transactions"][0]["ticker"], "BRK-B")
        self.assertIn("BRK-B", payload["position_snapshot"])
        self.assertIn("BRK-B", payload["performance_snapshot"])

    def test_import_preserves_unknown_deposit_currency_and_forex_component_currency(self) -> None:
        transactions_csv = "\n".join([
            "Statement,Header,Field Name,Field Value",
            "Statement,Data,Title,Transaction History",
            "Summary,Header,Field Name,Field Value",
            "Summary,Data,Starting Cash,0",
            "Summary,Data,Ending Cash,312.14",
            "Transaction History,Header,Date,Account,Description,Transaction Type,Symbol,Quantity,Price,Price Currency,Gross Amount ,Commission,Net Amount",
            "Transaction History,Data,2025-10-31,U***87176,Electronic Fund Transfer,Deposit,-,-,-,-,314.18505289999996,-,314.18505289999996",
            "Transaction History,Data,2025-10-31,U***87176,Net Amount in Base from Forex Trade: 314.09 USD.HKD,Forex Trade Component,USD.HKD,314.09,7.77165,HKD,-2.041974516,-2,-2.041974516",
        ]) + "\n"
        positions_csv = "\n".join([
            "Statement,Header,Field Name,Field Value",
            "Statement,Data,Title,Realized Summary",
            "Realized & Unrealized Performance Summary,Header,Asset Category,Symbol,Cost Adj.,Realized S/T Profit,Realized S/T Loss,Realized L/T Profit,Realized L/T Loss,Realized Total,Unrealized S/T Profit,Unrealized S/T Loss,Unrealized L/T Profit,Unrealized L/T Loss,Unrealized Total,Total,Code",
            "Realized & Unrealized Performance Summary,Data,Forex,USD.HKD,0,0,0,0,0,0,0,0,0,0,0,0,",
            "Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Open,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code",
            "Open Positions,Total,,Stocks,USD,,,,,,0,,0,0,",
        ]) + "\n"

        payload = build_investment_payload_from_ibkr_csvs(
            transactions_csv.encode("utf-8"),
            positions_csv.encode("utf-8"),
        )

        deposit, forex_component = payload["transactions"]
        self.assertIsNone(deposit["currency"])
        self.assertEqual(deposit["type"], "deposit")
        self.assertEqual(forex_component["currency"], "HKD")
        self.assertEqual(forex_component["type"], "forex_trade_component")
        self.assertEqual(forex_component["ticker"], "USD.HKD")
        self.assertEqual(forex_component["price_raw"], "7.77165")
        self.assertEqual(forex_component["quantity_raw"], "314.09")

    def test_import_detects_usd_credit_interest_currency_from_description(self) -> None:
        transactions_csv = "\n".join([
            "Statement,Header,Field Name,Field Value",
            "Statement,Data,Title,Transaction History",
            "Summary,Header,Field Name,Field Value",
            "Summary,Data,Starting Cash,0",
            "Summary,Data,Ending Cash,0.68",
            "Transaction History,Header,Date,Account,Description,Transaction Type,Symbol,Quantity,Price,Price Currency,Gross Amount ,Commission,Net Amount",
            "Transaction History,Data,2025-10-03,U***TEST,USD Credit Interest for Sep-2025,Credit Interest,-,-,-,-,0.68,-,0.68",
        ]) + "\n"
        positions_csv = "\n".join([
            "Statement,Header,Field Name,Field Value",
            "Statement,Data,Title,Realized Summary",
            "Realized & Unrealized Performance Summary,Header,Asset Category,Symbol,Cost Adj.,Realized S/T Profit,Realized S/T Loss,Realized L/T Profit,Realized L/T Loss,Realized Total,Unrealized S/T Profit,Unrealized S/T Loss,Unrealized L/T Profit,Unrealized L/T Loss,Unrealized Total,Total,Code",
            "Realized & Unrealized Performance Summary,Data,Cash,USD,0,0,0,0,0,0,0,0,0,0,0,0,",
            "Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Open,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code",
            "Open Positions,Total,,Stocks,USD,,,,,,0,,0,0,",
        ]) + "\n"

        payload = build_investment_payload_from_ibkr_csvs(
            transactions_csv.encode("utf-8"),
            positions_csv.encode("utf-8"),
        )

        self.assertEqual(payload["transactions"][0]["type"], "credit_interest")
        self.assertEqual(payload["transactions"][0]["currency"], "USD")

    def test_import_detects_dividend_currency_from_description(self) -> None:
        transactions_csv = "\n".join([
            "Statement,Header,Field Name,Field Value",
            "Statement,Data,Title,Transaction History",
            "Summary,Header,Field Name,Field Value",
            "Summary,Data,Starting Cash,0",
            "Summary,Data,Ending Cash,33.67",
            "Transaction History,Header,Date,Account,Description,Transaction Type,Symbol,Quantity,Price,Price Currency,Gross Amount ,Commission,Net Amount",
            "Transaction History,Data,2025-10-08,U***TEST,L9025R513(LU0052767562) Cash Dividend USD 0.033 per Share (Ordinary Dividend),Dividend,-,-,-,-,33.67,-,33.67",
        ]) + "\n"
        positions_csv = "\n".join([
            "Statement,Header,Field Name,Field Value",
            "Statement,Data,Title,Realized Summary",
            "Realized & Unrealized Performance Summary,Header,Asset Category,Symbol,Cost Adj.,Realized S/T Profit,Realized S/T Loss,Realized L/T Profit,Realized L/T Loss,Realized Total,Unrealized S/T Profit,Unrealized S/T Loss,Unrealized L/T Profit,Unrealized L/T Loss,Unrealized Total,Total,Code",
            "Realized & Unrealized Performance Summary,Data,Cash,USD,0,0,0,0,0,0,0,0,0,0,0,0,",
            "Open Positions,Header,DataDiscriminator,Asset Category,Currency,Symbol,Open,Quantity,Mult,Cost Price,Cost Basis,Close Price,Value,Unrealized P/L,Code",
            "Open Positions,Total,,Stocks,USD,,,,,,0,,0,0,",
        ]) + "\n"

        payload = build_investment_payload_from_ibkr_csvs(
            transactions_csv.encode("utf-8"),
            positions_csv.encode("utf-8"),
        )

        self.assertEqual(payload["transactions"][0]["type"], "dividend")
        self.assertEqual(payload["transactions"][0]["currency"], "USD")


if __name__ == "__main__":
    unittest.main()
