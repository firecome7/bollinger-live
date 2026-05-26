"""查NIL入场单的挂单价vs成交价"""
from gate_api import GateAPI
import statistics
from datetime import datetime

api = GateAPI()
ex = api.ex
sym = api.swap_symbol('NIL')

# 1. 查NIL的历史订单（最近成交）
print("=== NIL 最近订单 ===")
try:
    orders = ex.fetch_closed_orders(sym, limit=10)
    for o in orders:
        ts = datetime.fromtimestamp(o['timestamp']/1000)
        print(f'{ts.strftime("%m-%d %H:%M:%S")} {o["side"]:>5} {o["type"]:>6} '
              f'limit=${float(o["price"]):.4f} filled={o["filled"]} '
              f'剩={o["remaining"]} status={o["status"]}')
except Exception as e:
    print(f"fetch_closed_orders failed: {e}")

# 2. 查当前挂单
print("\n=== NIL 当前挂单 ===")
try:
    open_orders = ex.fetch_open_orders(sym)
    for o in open_orders:
        print(f'  {o["side"]} {o["type"]} @ ${float(o["price"]):.4f} amount={o["amount"]}')
    if not open_orders:
        print("  无挂单")
except Exception as e:
    print(f"fetch_open_orders failed: {e}")

# 3. 算一下如果做多限价单成交，应该是什么价
print("\n=== BB值验证 ===")
ohlcv = ex.fetch_ohlcv(sym, '15m', limit=30)
# 看信号可能触发的时间
for i in [25, 26, 27]:
    closes = [c[4] for c in ohlcv[i-25:i]]
    mid = sum(closes)/25
    std = statistics.stdev(closes) if len(closes)>1 else 0
    upper = mid + 2*std
    lower = mid - 2*std
    ts = datetime.fromtimestamp(ohlcv[i][0]/1000)
    print(f'  Bar {ts.strftime("%H:%M")}: 中轨={mid:.4f} 上轨={upper:.4f} 下轨={lower:.4f}')
    print(f'    做多挂单价(下轨*0.96)={lower*0.96:.4f} 做空(上轨*1.04)={upper*1.04:.4f}')

# 4. 查一下引擎下单时实际用的价格 - 从日志看信号触发时的BB
print("\n=== 日志查询 ===")
import subprocess
r = subprocess.run(['grep', '-n', 'NIL', '/home/admin/live_bollinger/bollinger_live.log'], capture_output=True, text=True, timeout=5)
for line in r.stdout.split('\n')[:5]:
    if line.strip():
        print(line[:200])
