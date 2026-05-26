"""查DRIFT入场单 vs BB值"""
from gate_api import GateAPI
import statistics
from datetime import datetime

api = GateAPI()
ex = api.ex
sym = api.swap_symbol('DRIFT')

# 1. 查历史订单
print("=== DRIFT 最近订单 ===")
orders = ex.fetch_closed_orders(sym, limit=10)
for o in orders:
    ts = datetime.fromtimestamp(o['timestamp']/1000)
    fill_px = float(o.get('average', o['price']))
    limit_px = float(o['price'])
    print(f'{ts.strftime("%m-%d %H:%M:%S")} {o["side"]:>5} {o["type"]:>6} '
          f'限价=${limit_px:.4f} 成交均价=${fill_px:.4f} filled={o["filled"]} '
          f'status={o["status"]}')

# 2. 找最近一个做空入场单的成交时间，算当时BB
print("\n=== 找做空入场单 ===")
for o in orders:
    if o['side'] == 'sell' and o['status'] == 'closed':
        ts_ms = o['timestamp']
        ts = datetime.fromtimestamp(ts_ms/1000)
        print(f'\n订单时间: {ts.strftime("%m-%d %H:%M:%S")}')
        fill_px = float(o.get('average', o['price']))
        limit_px = float(o['price'])
        print(f'限价: ${limit_px:.4f}, 成交均价: ${fill_px:.4f}')
        
        # 拉订单时间附近的K线算BB
        ohlcv = ex.fetch_ohlcv(sym, '15m', limit=30)
        for i, c in enumerate(ohlcv):
            bar_ts = datetime.fromtimestamp(c[0]/1000)
            next_ts = datetime.fromtimestamp(ohlcv[i+1][0]/1000) if i+1 < len(ohlcv) else None
            if bar_ts <= ts and (next_ts is None or ts < next_ts):
                # 这个bar包含信号时间
                closes = [c2[4] for c2 in ohlcv[i-25:i]] if i >= 25 else []
                if len(closes) == 25:
                    mid = sum(closes)/25
                    std = statistics.stdev(closes)
                    upper = mid + 2*std
                    lower = mid - 2*std
                    print(f'信号bar: {bar_ts.strftime("%m-%d %H:%M")}')
                    print(f'  中轨={mid:.4f} 上轨={upper:.4f} 下轨={lower:.4f}')
                    print(f'  做空挂单(上轨*1.04)={upper*1.04:.4f}')
                    print(f'  做空挂单(中轨*1.04)={mid*1.04:.4f}')
                    print(f'  实际限价=${limit_px:.4f}')
                    print(f'  限价/上轨={limit_px/upper*100:.2f}% (偏移={limit_px/upper*100-100:+.2f}%)')
                    print(f'  限价/中轨={limit_px/mid*100:.2f}%')
                break
        break
