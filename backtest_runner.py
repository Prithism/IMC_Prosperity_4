"""
Backtesting harness for arbitrage_trader_complete.py
Replays prices_round_0_day_*.csv through Trader.run() and reports P&L.

WHY THE ORIGINAL STRATEGY NEVER TRADES
---------------------------------------
The Trader calculates fair_value = (best_bid + best_ask) // 2 (the mid-price).
By definition:
  best_ask >= mid  → ask < fair_value is NEVER true  → no BUY signal
  best_bid <= mid  → bid > fair_value is NEVER true  → no SELL signal

Fix: supply product-specific KNOWN fair value anchors derived from the
historical data so the trader can actually detect mispricings.
"""

import csv
import contextlib
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Import the trader under test
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from arbitrage_trader_complete import (
    Order,
    OrderDepth,
    TradingState,
    Listing,
    Trader,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATA_DIR = SCRIPT_DIR

CSV_FILES = [
    "prices_round_0_day_-2.csv",
    "prices_round_0_day_-1.csv",
]

SEPARATOR = ";"

# Position limits matching the data products
POSITION_LIMITS: Dict[str, int] = {
    "TOMATOES": 250,
    "EMERALDS": 20,
}
DEFAULT_POSITION_LIMIT = 20

# Known fair-value anchors derived from historical observation.
# EMERALDS oscillate tightly around 10000; TOMATOES drift but anchor ~5000.
# These replace the broken mid-price calculation so signals actually fire.
KNOWN_FAIR_VALUES: Dict[str, int] = {
    "EMERALDS": 10000,
    "TOMATOES": 5000,
}


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def _parse_int(value: str) -> Optional[int]:
    """Return int or None for missing/empty cells."""
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return int(float(stripped))
    except ValueError:
        return None


def build_order_depth(row: dict) -> OrderDepth:
    """
    Reconstruct an OrderDepth from up to 3 bid/ask levels.
    Sell-order volumes are stored NEGATIVE (Prosperity convention).
    """
    depth = OrderDepth()
    for level in range(1, 4):
        bid_price = _parse_int(row.get(f"bid_price_{level}", ""))
        bid_vol   = _parse_int(row.get(f"bid_volume_{level}", ""))
        ask_price = _parse_int(row.get(f"ask_price_{level}", ""))
        ask_vol   = _parse_int(row.get(f"ask_volume_{level}", ""))

        if bid_price is not None and bid_vol is not None:
            depth.buy_orders[bid_price] = bid_vol
        if ask_price is not None and ask_vol is not None:
            depth.sell_orders[ask_price] = -ask_vol   # negative for sells

    return depth


def read_ticks(csv_path: Path) -> Dict[Tuple[int, int], Dict[str, dict]]:
    """Group CSV rows by (day, timestamp) → {product: row}."""
    ticks: Dict[Tuple[int, int], Dict[str, dict]] = {}
    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh, delimiter=SEPARATOR)
        for row in reader:
            key = (int(row["day"]), int(row["timestamp"]))
            ticks.setdefault(key, {})[row["product"].strip()] = row
    return ticks


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TradeRecord:
    """Single filled order."""
    day: int
    timestamp: int
    product: str
    price: int
    quantity: int          # positive = bought, negative = sold


@dataclass
class BacktestResult:
    ticks_processed: int = 0
    total_trades: int = 0
    trade_log: List[TradeRecord] = field(default_factory=list)
    final_positions: Dict[str, int] = field(default_factory=dict)
    last_mid: Dict[str, float] = field(default_factory=dict)          # for MTM
    pnl_by_product: Dict[str, float] = field(default_factory=dict)
    total_pnl: float = 0.0
    position_history: Dict[str, List[Tuple[int, int]]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Monkey-patch: override calculate_fair_value to use known anchors
# ---------------------------------------------------------------------------

# No patching needed — the fix is now in arbitrage_trader_complete.py itself


# ---------------------------------------------------------------------------
# Backtester
# ---------------------------------------------------------------------------

class Backtester:
    """
    Feeds historical tick data into Trader.run() one timestamp at a time
    and tracks cash flow, positions, and P&L.
    """

    def __init__(self) -> None:
        self._trader      = Trader()
        self._trader_data = ""
        self._positions: Dict[str, int]   = {}
        self._cash:      Dict[str, float] = {}   # cash per product
        self._result = BacktestResult()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run_all(self) -> BacktestResult:
        """Replay all CSV files and return the aggregated result."""
        for csv_file in CSV_FILES:
            path = DATA_DIR / csv_file
            if not path.exists():
                print(f"[WARN] Not found: {path}")
                continue
            ticks = read_ticks(path)
            for key in sorted(ticks.keys()):
                day, ts = key
                self._process_tick(day, ts, ticks[key])

        self._finalise()
        return self._result

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _process_tick(
        self, day: int, timestamp: int, product_rows: Dict[str, dict]
    ) -> None:
        """Build TradingState, call Trader.run(), fill any resulting orders."""
        self._result.ticks_processed += 1

        order_depths: Dict[str, OrderDepth] = {}
        listings:     Dict[str, Listing]    = {}

        for product, row in product_rows.items():
            order_depths[product] = build_order_depth(row)
            listings[product]     = Listing(product, product, "XIRECS")
            # Track last mid price for MTM valuation
            mid = _parse_int(row.get("mid_price", ""))
            if mid is not None:
                self._result.last_mid[product] = float(mid)

        state = TradingState(
            traderData=self._trader_data,
            timestamp=timestamp,
            listings=listings,
            order_depths=order_depths,
            own_trades={p: [] for p in order_depths},
            market_trades={p: [] for p in order_depths},
            position=dict(self._positions),
            observations={},
        )

        # Run the trader silently
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            try:
                result, _conversions, self._trader_data = self._trader.run(state)
            except Exception as exc:  # noqa: BLE001
                print(f"[ERROR] tick day={day} ts={timestamp}: {exc}")
                return

        self._fill_orders(day, timestamp, result, order_depths)

    def _fill_orders(
        self,
        day: int,
        timestamp: int,
        result: Dict[str, List[Order]],
        depths: Dict[str, OrderDepth],
    ) -> None:
        """Simulate fills against best available book price."""
        for product, orders in result.items():
            depth = depths.get(product)
            limit = POSITION_LIMITS.get(product, DEFAULT_POSITION_LIMIT)
            current = self._positions.get(product, 0)

            for order in orders:
                qty = order.quantity

                # Enforce position limits
                if qty > 0:
                    qty = min(qty, limit - current)
                else:
                    qty = max(qty, -(current + limit))

                if qty == 0:
                    continue

                fill_price = order.price
                if fill_price == 0:
                    # Resolve from book as safety fallback
                    if qty > 0 and depth and depth.sell_orders:
                        fill_price = min(depth.sell_orders.keys())
                    elif qty < 0 and depth and depth.buy_orders:
                        fill_price = max(depth.buy_orders.keys())
                    else:
                        continue

                # Update state
                current += qty
                self._positions[product] = current
                self._cash[product] = self._cash.get(product, 0.0) - qty * fill_price

                self._result.trade_log.append(
                    TradeRecord(day, timestamp, product, fill_price, qty)
                )
                self._result.total_trades += 1
                self._result.position_history.setdefault(product, []).append(
                    (timestamp, current)
                )

    def _finalise(self) -> None:
        """Compute final P&L including MTM on open inventory."""
        self._result.final_positions = dict(self._positions)
        total = 0.0
        for product, cash in self._cash.items():
            pos   = self._positions.get(product, 0)
            mid   = self._result.last_mid.get(product, 0.0)
            mtm   = pos * mid          # mark remaining inventory at last mid
            pnl   = cash + mtm
            self._result.pnl_by_product[product] = pnl
            total += pnl
        self._result.total_pnl = total


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _pnl_str(v: float) -> str:
    return f"{'+' if v >= 0 else ''}{v:,.1f}"


def _signal_analysis() -> None:
    """
    Show exactly why the original strategy produces zero signals,
    demonstrated on real data from the CSV.
    """
    print("\n" + "─" * 60)
    print("  SIGNAL DIAGNOSTIC (why the original strategy never trades)")
    print("─" * 60)
    path = DATA_DIR / "prices_round_0_day_-1.csv"
    ticks = read_ticks(path)
    first_key = sorted(ticks.keys())[0]
    for product, row in ticks[first_key].items():
        best_bid = _parse_int(row["bid_price_1"])
        best_ask = _parse_int(row["ask_price_1"])
        if best_bid is None or best_ask is None:
            continue
        mid = (best_bid + best_ask) // 2
        anchor = KNOWN_FAIR_VALUES.get(product, mid)
        print(f"\n  {product}")
        print(f"    best_bid={best_bid}  best_ask={best_ask}  mid={mid}")
        print(f"    ── Original (fair=mid={mid}) ──")
        print(f"       BUY  cond: ask({best_ask}) < fair({mid})  → {best_ask < mid}  (never true)")
        print(f"       SELL cond: bid({best_bid}) > fair({mid})  → {best_bid > mid}  (never true)")
        print(f"    ── Patched (fair=anchor={anchor}) ──")
        print(f"       BUY  cond: ask({best_ask}) < fair({anchor})  → {best_ask < anchor}")
        print(f"       SELL cond: bid({best_bid}) > fair({anchor})  → {best_bid > anchor}")
    print()


def print_report(result: BacktestResult) -> None:
    """Pretty-print the full backtest summary."""
    SEP = "=" * 60

    print(f"\n{SEP}")
    print("  BACKTEST RESULTS  (with known fair-value anchors)")
    print(SEP)
    print(f"  Ticks processed  : {result.ticks_processed:,}")
    print(f"  Orders filled    : {result.total_trades:,}")
    print()

    print(f"  {'Product':<15} {'Cash Flow':>12}  {'MTM PnL':>12}  {'Final Pos':>10}")
    print(f"  {'-'*15} {'-'*12}  {'-'*12}  {'-'*10}")

    for product in sorted(result.pnl_by_product.keys()):
        pnl   = result.pnl_by_product[product]
        pos   = result.final_positions.get(product, 0)
        cash  = -(sum(
            t.price * t.quantity
            for t in result.trade_log if t.product == product
        ))
        mtm   = result.last_mid.get(product, 0.0) * pos
        print(f"  {product:<15} {_pnl_str(cash):>12}  {_pnl_str(mtm):>12}  {pos:>10}")

    print()
    print(f"  Net P&L (cash + open MTM): {_pnl_str(result.total_pnl)}")

    open_pos = {p: v for p, v in result.final_positions.items() if v != 0}
    if open_pos:
        print(f"\n  ⚠  Open positions: {open_pos}")
        print("     MTM value IS included in Net P&L above.")

    print(f"\n{SEP}\n")

    # Trade sample
    SAMPLE = 20
    if result.trade_log:
        print(f"  Sample of first {min(SAMPLE, len(result.trade_log))} filled orders:")
        print(f"  {'Day':>4} {'Time':>8} {'Product':<12} {'Side':<5} {'Qty':>4}  {'Price':>8}  {'Net':>10}")
        print(f"  {'-'*4} {'-'*8} {'-'*12} {'-'*5} {'-'*4}  {'-'*8}  {'-'*10}")
        for t in result.trade_log[:SAMPLE]:
            side = "BUY " if t.quantity > 0 else "SELL"
            net  = -t.quantity * t.price
            print(
                f"  {t.day:>4} {t.timestamp:>8} {t.product:<12} {side} "
                f"{abs(t.quantity):>4}  {t.price:>8}  {_pnl_str(net):>10}"
            )
        if len(result.trade_log) > SAMPLE:
            print(f"  … and {len(result.trade_log) - SAMPLE} more orders.")
    else:
        print("  No orders were filled.")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  IMC Prosperity Backtest Runner")
    print("=" * 60)
    print(f"  Data dir : {DATA_DIR}")
    print(f"  Files    : {CSV_FILES}")
    print(f"  Anchors  : {KNOWN_FAIR_VALUES}")

    _signal_analysis()

    print("  Running backtest …")
    backtester = Backtester()
    result = backtester.run_all()
    print_report(result)
