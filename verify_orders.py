#!/usr/bin/env python3.12
"""核对26个挂单 vs 当前行情"""
import sys
sys.path.insert(0, '/home/admin/live_bollinger')
from gate_api import GateAPI
from config import FIXED_POSITION_VALUE, ENTRY_OFFSET, BOLL_PERIOD

api = GateAPI()
tickers = api.fetch_tickers_all()
orders = api.fetch_open_orders()
candles_cache = {}

print(f"{'币':<10} {'方向':<6} {'限价':<12} {'现价':<12} {'差%':<8} {'行情vs限价':<16} {'结论':<12}")
print("-" * 80)

for o in orders:
    coin = o['coin']
    side = o['side']  # 'buy' = 做多, 'sell' = 做空
    limit = o['price']
    ticker = tickers.get(coin, {})
    last = float(ticker.get('last', 0) or 0)
    
    if last == 0:
        continue
    
    # 计算差%
    diff_pct = (last - limit) / limit * 100
    
    if side == 'buy':
        # 做多: 限价低于现价时正常(等价格跌下来成交)
        if limit < last:
            verdict = "✅ 等价格下跌成交"
        elif limit >= last:
            verdict = "⚠️ 应该已经成交了"
    else:
        # 做空: 限价高于现价时正常(等价格涨上去成交)
        if limit > last:
            verdict = "✅ 等价格上涨成交"
        elif limit <= last:
            verdict = "⚠️ 应该已经成交了"
    
    print(f"{coin:<10} {'做多' if side=='buy' else '做空':<6} "
          f"{limit:<12.6f} {last:<12.6f} {diff_pct:<+8.2f} "
          f"{'低于现价' if limit<last else '高于现价':<16} "
          f"{verdict:<12}")

# 额外抽查几个已成交的订单
print(f"\n{'='*60}")
print("抽查: 已成交的ZEST订单回溯验证")
print(f"{'='*60}")

# ZEST做空: 信号价$0.1735突破上轨$0.1713 → 挂$0.1781
# 验证: 0.1713 × 1.04 = 0.178152 ≈ 0.17811 ✅
expected_zest = 0.1713 * 1.04
print(f"ZEST做空信号: 上轨$0.1713 × 1.04 = ${expected_zest:.4f}")
print(f"实际挂单价: $0.17811  ✅ 误差{abs(0.17811-expected_zest)/expected_zest*100:.2f}%")
