"""查NIL在13:30那根K线附近BB值，验证入场价是否距离下轨4%"""
from gate_api import GateAPI
import statistics
from datetime import datetime

api = GateAPI()
ex = api.ex

ohlcv = ex.fetch_ohlcv('NIL/USDT:USDT', '15m', limit=30)

print("=== NIL 近30根15m K线 ===")
for i, c in enumerate(ohlcv):
    ts = datetime.fromtimestamp(c[0]/1000)
    print(f'{i:>2} {ts.strftime("%m-%d %H:%M")} O={c[1]:.4f} H={c[2]:.4f} L={c[3]:.4f} C={c[4]:.4f} V={c[5]:.0f}')

# 计算每根K线开始的BB值（用前25根收盘价）
print("\n=== 每根K线基于前25根收盘价算的BB值 ===")
entry_price = 0.074960  # 13:37成交价
for i in range(25, len(ohlcv)):
    closes = [c[4] for c in ohlcv[i-25:i]]  # 前25根收盘价
    mid = sum(closes) / 25
    std = statistics.stdev(closes) if len(closes) > 1 else 0
    upper = mid + 2 * std
    lower = mid - 2 * std
    ts = datetime.fromtimestamp(ohlcv[i][0]/1000)
    
    long_offset_pct = (entry_price - lower) / lower * 100
    short_offset_pct = (entry_price - upper) / upper * 100
    
    print(f'{ts.strftime("%m-%d %H:%M")} 中轨={mid:.4f} 上轨={upper:.4f} 下轨={lower:.4f}')
    print(f'  做多挂单=下轨*0.96={lower*0.96:.4f} 做空挂单=上轨*1.04={upper*1.04:.4f}')
    print(f'  实际成交价={entry_price:.4f} 距下轨={long_offset_pct:+.2f}% 距上轨={short_offset_pct:+.2f}%')
