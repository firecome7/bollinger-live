#!/usr/bin/env python3.12
"""30分钟状态汇总 — 输出到微信需要的内容"""
import sys, os
sys.path.insert(0, '/home/admin/live_bollinger')
from gate_api import GateAPI
from datetime import datetime

OUTPUT = '/home/admin/live_bollinger/.status_output'

try:
    api = GateAPI()
    bal = api.fetch_balance()
    orders = api.fetch_open_orders()
    positions = api.fetch_positions()
    
    entry = sum(1 for o in orders if not o['reduce_only'])
    exit_o = len(orders) - entry
    holding = len(positions)
    
    with open(OUTPUT, 'w') as f:
        f.write(f"=== Bollinger策略 状态 [{datetime.now().strftime('%m-%d %H:%M')}] ===\n")
        f.write(f"余额: 总${bal['total']:.2f} 可用${bal['free']:.2f}\n")
        f.write(f"持仓: {holding}/20  挂单: {entry}入场+{exit_o}出场\n")
        if holding > 0:
            for p in positions:
                f.write(f"  {p['coin']:<12} {p['side']} 入场${p['entry_price']:.4f}\n")
        if entry > 0:
            f.write(f"入场挂单(前5):\n")
            for o in orders[:5]:
                if not o['reduce_only']:
                    f.write(f"  {o['coin']:<12} {'做多' if o['side']=='buy' else '做空'} @ ${o['price']:.4f}\n")
        f.write(f"注意: 跳空可能打穿限价单，15秒后转市价\n")
    print("OK")
except Exception as e:
    with open(OUTPUT, 'w') as f:
        f.write(f"[ERROR] {e}\n")
    print(f"FAIL: {e}")
