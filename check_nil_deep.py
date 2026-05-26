"""查NIL信号触发时的BB值 - 深入"""
from gate_api import GateAPI
import statistics
from datetime import datetime

api = GateAPI()
ex = api.ex
sym = api.swap_symbol('NIL')

# 拉更多K线（100根 = 25小时）
ohlcv = ex.fetch_ohlcv(sym, '15m', limit=100)
print(f"K线数量: {len(ohlcv)}, 最早: {datetime.fromtimestamp(ohlcv[0][0]/1000)}, 最晚: {datetime.fromtimestamp(ohlcv[-1][0]/1000)}")

# 找11:15附近的bar
print("\n=== 11:00~11:30附近的K线 ===")
for i, c in enumerate(ohlcv):
    ts = datetime.fromtimestamp(c[0]/1000)
    if ts.hour == 11 and ts.minute in [0, 15, 30]:
        print(f'{i:>3} {ts.strftime("%H:%M")} O={c[1]:.4f} H={c[2]:.4f} L={c[3]:.4f} C={c[4]:.4f}')

# 在11:15 bar开始时，前25根bar算的BB值
# 找到11:15 bar的索引
target_idx = None
for i, c in enumerate(ohlcv):
    ts = datetime.fromtimestamp(c[0]/1000)
    if ts.hour == 11 and ts.minute == 15:
        target_idx = i
        break

if target_idx and target_idx >= 25:
    # 前25根 = indices target_idx-25 到 target_idx-1
    closes = [c[4] for c in ohlcv[target_idx-25:target_idx]]
    p25 = ohlcv[target_idx-25][4]
    mid = sum(closes)/25
    std = statistics.stdev(closes) if len(closes)>1 else 0
    upper = mid + 2*std
    lower = mid - 2*std
    print(f'\n=== 11:15 bar 开始的BB值 ===')
    print(f'前25根收盘价范围: {min(closes):.4f} ~ {max(closes):.4f}')
    print(f'中轨={mid:.4f} 上轨={upper:.4f} 下轨={lower:.4f}')
    print(f'做多挂单(下轨*0.96)={lower*0.96:.4f}')
    print(f'做多挂单(中轨*0.96)={mid*0.96:.4f}')
    print(f'实际限价=0.0750')
    print(f'0.0750是下轨的{0.0750/lower*100:.2f}%')
    print(f'0.0750是中轨的{0.0750/mid*100:.2f}%')
    
    # 也查一下如果按中轨算会是什么
    print(f'\n=== 如果按中轨算4% ===')
    print(f'中轨*0.96 = {mid*0.96:.4f}')
    print(f'中轨*0.94 = {mid*0.94:.4f} (6%偏移)')

# 也查一下13:30 bar的BB值
print('\n=== 13:30 bar 开始的BB值 ===')
for i, c in enumerate(ohlcv):
    ts = datetime.fromtimestamp(c[0]/1000)
    if ts.hour == 13 and ts.minute == 30:
        target_idx = i
        break
if target_idx and target_idx >= 25:
    closes = [c[4] for c in ohlcv[target_idx-25:target_idx]]
    mid = sum(closes)/25
    std = statistics.stdev(closes) if len(closes)>1 else 0
    upper = mid + 2*std
    lower = mid - 2*std
    print(f'前25根 中轨={mid:.4f} 上轨={upper:.4f} 下轨={lower:.4f}')
    print(f'做多(下轨*0.96)={lower*0.96:.4f}')
    print(f'0.0750是下轨的{0.0750/lower*100:.2f}%')
