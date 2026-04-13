"""
Complete Arbitrage Trading Algorithm for IMC Prosperity 4
Includes datamodel classes for standalone testing
"""

import json
from typing import Dict, List

# ============================================
# DATA MODEL CLASSES (from datamodel.py)
# ============================================

class Order:
    def __init__(self, symbol: str, price: int, quantity: int):
        self.symbol = symbol
        self.price = price
        self.quantity = quantity

    def __str__(self):
        return f"({self.symbol}, {self.price}, {self.quantity})"
    
    def __repr__(self):
        return self.__str__()


class OrderDepth:
    def __init__(self):
        self.buy_orders: Dict[int, int] = {}
        self.sell_orders: Dict[int, int] = {}


class Trade:
    def __init__(self, symbol: str, price: int, quantity: int, buyer: str = None, seller: str = None, timestamp: int = 0):
        self.symbol = symbol
        self.price = price
        self.quantity = quantity
        self.buyer = buyer
        self.seller = seller
        self.timestamp = timestamp


class Listing:
    def __init__(self, symbol: str, product: str, denomination: str):
        self.symbol = symbol
        self.product = product
        self.denomination = denomination


class TradingState:
    def __init__(self, traderData: str, timestamp: int, listings: Dict, order_depths: Dict,
                 own_trades: Dict, market_trades: Dict, position: Dict, observations: Dict):
        self.traderData = traderData
        self.timestamp = timestamp
        self.listings = listings
        self.order_depths = order_depths
        self.own_trades = own_trades
        self.market_trades = market_trades
        self.position = position
        self.observations = observations


# ============================================
# ARBITRAGE TRADING ALGORITHM
# ============================================

class Trader:
    POSITION_LIMITS = {"TOMATOES": 250, "EMERALDS": 20}
    EOD_TIMESTAMP = 999900
    EMA_ALPHA = 0.2
    STOP_LOSS_LIMIT = -450
    
    def __init__(self):
        self.fair_values = {}
        self.ema_prices = {}
        self.cumulative_pnl = 0

    def calculate_vwap(self, order_depth: OrderDepth):
        total_value, total_vol = 0, 0
        for price, vol in list(order_depth.buy_orders.items()) + list(order_depth.sell_orders.items()):
            total_value += price * abs(vol)
            total_vol += abs(vol)
        return total_value / total_vol if total_vol > 0 else 0

    def run(self, state: TradingState):
        if state.traderData:
            try:
                data = json.loads(state.traderData)
                self.ema_prices = data.get("ema_prices", {})
                self.cumulative_pnl = data.get("pnl", 0)
            except json.JSONDecodeError:
                pass

        if self.cumulative_pnl < self.STOP_LOSS_LIMIT:
            return {}, 0, state.traderData

        result: Dict[str, List[Order]] = {}

        for product in state.order_depths:
            depth = state.order_depths[product]
            pos = state.position.get(product, 0)
            limit = self.POSITION_LIMITS.get(product, 20)
            
            current_vwap = self.calculate_vwap(depth)
            if product not in self.ema_prices:
                self.ema_prices[product] = current_vwap
            
            self.ema_prices[product] = (self.EMA_ALPHA * current_vwap) + ((1 - self.EMA_ALPHA) * self.ema_prices[product])
            fair_value = self.ema_prices[product]

            orders: List[Order] = []
            best_bid = max(depth.buy_orders.keys()) if depth.buy_orders else fair_value - 2
            best_ask = min(depth.sell_orders.keys()) if depth.sell_orders else fair_value + 2

            if best_ask < fair_value - 1 and pos < limit:
                buy_qty = min(-depth.sell_orders[best_ask], limit - pos)
                orders.append(Order(product, best_ask, buy_qty))

            if best_bid > fair_value + 1 and pos > -limit:
                sell_qty = min(depth.buy_orders[best_bid], pos + limit)
                orders.append(Order(product, best_bid, -sell_qty))

            if state.timestamp >= self.EOD_TIMESTAMP - 1000:
                orders = []
                if pos > 0 and depth.buy_orders:
                    orders.append(Order(product, max(depth.buy_orders.keys()), -pos))
                elif pos < 0 and depth.sell_orders:
                    orders.append(Order(product, min(depth.sell_orders.keys()), -pos))

            result[product] = orders

        trader_data = json.dumps({
            "ema_prices": self.ema_prices,
            "pnl": self.cumulative_pnl
        })

        return result, 0, trader_data



# ============================================
# TESTING SECTION
# ============================================

def test_scenario_1():
    """
    Test Scenario 1: Clear arbitrage opportunity
    - Sellers asking 10, Buyers offering 12
    - Fair value = 11
    - Should: BUY at 10
    """
    print("\n" + "🔬 TEST SCENARIO 1: Clear Arbitrage Opportunity")
    print("="*60)
    
    trader = Trader()
    
    # Create market state
    order_depths = {"PRODUCT1": OrderDepth()}
    order_depths["PRODUCT1"].buy_orders = {12: 5, 11: 3}      # Buyers
    order_depths["PRODUCT1"].sell_orders = {10: -4, 11: -2}   # Sellers (negative)
    
    state = TradingState(
        traderData="",
        timestamp=1000,
        listings={"PRODUCT1": Listing("PRODUCT1", "PRODUCT1", "XIRECS")},
        order_depths=order_depths,
        own_trades={"PRODUCT1": []},
        market_trades={"PRODUCT1": []},
        position={"PRODUCT1": 0},
        observations={}
    )
    
    result, conversions, trader_data = trader.run(state)
    
    print("\nExpected: BUY order at price 10")
    print(f"Actual: {result['PRODUCT1']}")
    print("\n✅ Test complete\n")


def test_scenario_2():
    """
    Test Scenario 2: Multiple products with different opportunities
    """
    print("\n" + "🔬 TEST SCENARIO 2: Multiple Products")
    print("="*60)
    
    trader = Trader()
    
    # Product 1: Buy opportunity
    od1 = OrderDepth()
    od1.buy_orders = {100: 10}
    od1.sell_orders = {95: -5}  # Undervalued - buy!
    
    # Product 2: Sell opportunity
    od2 = OrderDepth()
    od2.buy_orders = {200: -3}  # Overvalued - sell!
    od2.sell_orders = {210: -8}
    
    state = TradingState(
        traderData="",
        timestamp=1000,
        listings={
            "PRODUCT1": Listing("PRODUCT1", "PRODUCT1", "XIRECS"),
            "PRODUCT2": Listing("PRODUCT2", "PRODUCT2", "XIRECS"),
        },
        order_depths={"PRODUCT1": od1, "PRODUCT2": od2},
        own_trades={"PRODUCT1": [], "PRODUCT2": []},
        market_trades={"PRODUCT1": [], "PRODUCT2": []},
        position={"PRODUCT1": 5, "PRODUCT2": -3},
        observations={}
    )
    
    result, conversions, trader_data = trader.run(state)
    
    print("\nExpected: BUY order for PRODUCT1, SELL order for PRODUCT2")
    print(f"Actual: {result}")
    print("\n✅ Test complete\n")


def test_scenario_3():
    """
    Test Scenario 3: Position limit constraints
    """
    print("\n" + "🔬 TEST SCENARIO 3: Position Limits")
    print("="*60)
    
    trader = Trader()
    
    od = OrderDepth()
    od.buy_orders = {100: 10}
    od.sell_orders = {95: -20}  # Sellers offering 20 units
    
    state = TradingState(
        traderData="",
        timestamp=1000,
        listings={"PRODUCT1": Listing("PRODUCT1", "PRODUCT1", "XIRECS")},
        order_depths={"PRODUCT1": od},
        own_trades={"PRODUCT1": []},
        market_trades={"PRODUCT1": []},
        position={"PRODUCT1": 18},  # Already holding 18/20 limit
        observations={}
    )
    
    result, conversions, trader_data = trader.run(state)
    
    print("\nExpected: BUY only 2 units (limited by position)")
    if result['PRODUCT1']:
        print(f"Actual: {result['PRODUCT1'][0].quantity} units")
    print("\n✅ Test complete\n")


if __name__ == "__main__":
    print("\n🚀 Arbitrage Algorithm Testing Suite\n")
    
    test_scenario_1()
    test_scenario_2()
    test_scenario_3()
    
    print("\n" + "="*60)
    print("All tests complete! ✅")
    print("="*60 + "\n")
    
    print("Next steps:")
    print("1. Update position limits for your specific round")
    print("2. Test with real sample data from Prosperity")
    print("3. Add optimizations (trend detection, volatility, etc.)")
    print("4. Submit to platform and monitor results\n")
