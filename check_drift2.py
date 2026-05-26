"""重新算DRIFT的BB值，对比用户看到的0.4351"""
from gate_api import GateAPI
import statistics
from datetime import datetime

api = GateAPI()
ex = api.ex
sym = api.swap_symbol('DRIFT')

# 拉多些K线
ohlcv = ex.fetch_ohlcv(sym, '15m', limit=40)

# 查成交单确定是哪根K线
orders = ex.fetch_closed_orders(sym, limit=5)
for o in orders:
    if o['side'] == 'sell' and o['status'] == 'closed':
        ts_ms = o['timestamp']
        ts = datetime.fromtimestamp(ts_ms/1000)
        fill_px = float(o.get('average', o['price']))
        print(f"订单时间: {ts}  限价=${float(o['price']):.4f}  成交均价=${fill_px:.4f}")
        
        # 找对应K线
        for i, c in enumerate(ohlcv):
            bar_start = datetime.fromtimestamp(c[0]/1000)
            bar_end = datetime.fromtimestamp(ohlcv[i+1][0]/1000) if i+1 < len(ohlcv) else None
            if bar_start <= ts and (bar_end is None or ts < bar_end):
                print(f"所在K线: {bar_start}  开={c[1]:.4f} 高={c[2]:.4f} 低={c[3]:.4f} 收={c[4]:.4f}")
                print(f"\n--- 用该K线前25根收盘价算BB ---")
                if i >= 25:
                    closes25 = [c2[4] for c2 in ohlcv[i-25:i]]
                    mid25 = sum(closes25)/25
                    std25 = statistics.stdev(closes25)
                    upper25 = mid25 + 2*std25
                    lower25 = mid25 - 2*std25
                    print(f"BB25: 中轨={mid25:.4f}  上轨={upper25:.4f}  下轨={lower25:.4f}")
                    print(f"上轨*1.04={upper25*1.04:.4f}")
                
                print(f"\n--- 如果用户用20周期 ---")
                if i >= 20:
                    closes20 = [c2[4] for c2 in ohlcv[i-20:i]]
                    mid20 = sum(closes20)/20
                    std20 = statistics.stdev(closes20)
                    upper20 = mid20 + 2*std20
                    lower20 = mid20 - 2*std20
                    print(f"BB20: 中轨={mid20:.4f}  上轨={upper20:.4f}  下轨={lower20:.4f}")
                    print(f"上轨*1.04={upper20*1.04:.4f}")
                
                print(f"\n--- 最近30根K线收盘价 ---")
                for j in range(max(0,i-30), i+1):
                    bt = datetime.fromtimestamp(ohlcv[j][0]/1000)
                    print(f"  {bt.strftime('%H:%M')} C={ohlcv[j][4]:.4f}")
                break
        break
