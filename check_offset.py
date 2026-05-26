"""验证实盘挂单的4%偏移计算方式"""
from gate_api import GateAPI
import statistics

api = GateAPI()
orders = api.fetch_open_orders()
ex = api.ex

coins = ['WLD', 'SKYAI', 'BSB', 'FIL', 'NIL']
for sym in coins:
    try:
        ohlcv = ex.fetch_ohlcv(sym + '/USDT:USDT', '15m', limit=26)
        if len(ohlcv) < 25:
            continue
        closes = [c[4] for c in ohlcv[:-1]]
        current = ohlcv[-1][4]
        bb_mid = sum(closes[-25:]) / 25
        bb_std = statistics.stdev(closes[-25:]) if len(closes[-25:]) > 1 else 0
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        
        long_entry = bb_lower * 0.96
        short_entry = bb_upper * 1.04
        
        print(f'{sym}:')
        print(f'  中轨={bb_mid:.4f} 上轨={bb_upper:.4f} 下轨={bb_lower:.4f}')
        print(f'  当前价={current:.4f}')
        print(f'  做多挂单价 = 下轨*0.96 = {long_entry:.4f}')
        print(f'  做空挂单价 = 上轨*1.04 = {short_entry:.4f}')
        for o in orders:
            if o['coin'] == sym:
                print(f'  实盘挂单: {"做多" if o["side"]=="buy" else "做空"} @ ${o["price"]:.4f}')
        print()
    except Exception as e:
        print(f'{sym}: {e}\n')
