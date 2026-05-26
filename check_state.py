#!/usr/bin/env python3.12
"""检查当前Gate.io订单和持仓状态"""
import json, sys, os
sys.path.insert(0, '/home/admin/live_bollinger')
from gate_api import GateAPI

api = GateAPI()
orders = api.fetch_open_orders()
print(f"总未成交订单: {len(orders)}")
entry = 0
stop = 0
for o in orders:
    coin = o['coin']
    ro = o['reduce_only']
    tp = o['type']
    price = o['price']
    oid = o['id']
    print(f"  {coin:<10} {o['side']:<5} {'出场' if ro else '入场':<4} "
          f"price={price:<10} ID={oid}")
    if ro:
        stop += 1
    else:
        entry += 1
print(f"入场挂单: {entry}")
print(f"出场挂单: {stop}")

positions = api.fetch_positions()
print(f"持仓: {len(positions)}")
for p in positions:
    print(f"  {p['coin']:<10} {p['side']:<5} 张数={p['size']} 入场={p['entry_price']}")

bal = api.fetch_balance()
print(f"余额: 总{bal['total']:.2f} 可用{bal['free']:.2f} 冻结{bal['used']:.2f}")
