"""验证新逻辑效果"""
from gate_api import GateAPI
import statistics

api = GateAPI()
ex = api.ex

for sym in ['WLD', 'SKYAI', 'BSB', 'NIL']:
    try:
        ohlcv = ex.fetch_ohlcv(sym + '/USDT:USDT', '15m', limit=26)
        closes = [c[4] for c in ohlcv[:-1]]
        mid = sum(closes)/25
        std = statistics.stdev(closes)
        upper = mid + 2*std
        lower = mid - 2*std
        
        old_long = lower * 0.96
        old_short = upper * 1.04
        new_long = mid * 0.96
        new_short = mid * 1.04
        
        # 新逻辑：如果在轨道内，收到轨道线
        final_long = lower if new_long > lower else new_long
        final_short = upper if new_short < upper else new_short
        
        long_clamp = "(->下轨)" if final_long == lower else "(保持4%)"
        short_clamp = "(->上轨)" if final_short == upper else "(保持4%)"
        
        print(f'{sym}:')
        print(f'  中轨={mid:.4f} 上轨={upper:.4f} 下轨={lower:.4f}')
        print(f'  旧做多(下轨*0.96)={old_long:.4f}')
        print(f'  新做多(中轨*0.96)={new_long:.4f} {long_clamp} → {final_long:.4f}')
        print(f'  旧做空(上轨*1.04)={old_short:.4f}')
        print(f'  新做空(中轨*1.04)={new_short:.4f} {short_clamp} → {final_short:.4f}')
        print()
    except Exception as e:
        print(f'{sym}: {e}\n')
