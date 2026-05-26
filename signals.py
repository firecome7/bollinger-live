"""布林带计算 + 信号判断"""
from __future__ import annotations

import pandas as pd
import numpy as np
from config import BOLL_PERIOD, BOLL_STD, ENTRY_OFFSET


def calc_bollinger(candles: list[list]) -> dict[str, float | None]:
    """从OHLCV列表计算布林带
    candles: ccxt OHLCV格式 [[ts, open, high, low, close, volume], ...]
    返回: {mid, upper, lower} (从前一根收盘价计算，无未来数据)
    """
    if len(candles) < BOLL_PERIOD + 1:
        return {'mid': None, 'upper': None, 'lower': None}

    df = pd.DataFrame(candles, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
    # 取最近BOLL_PERIOD根的前收盘价（shift(1)保证无未来数据）
    closes = df['c'].values[-(BOLL_PERIOD + 1):-1]  # 排除最后一根
    mid = float(np.mean(closes))
    std = float(np.std(closes, ddof=0))
    upper = mid + std * BOLL_STD
    lower = mid - std * BOLL_STD
    return {'mid': mid, 'upper': upper, 'lower': lower}


def calc_bollinger_from_df(df: pd.DataFrame) -> dict[str, float | None]:
    """从已有DataFrame计算布林带（回测验证用）"""
    if len(df) < BOLL_PERIOD + 1:
        return {'mid': None, 'upper': None, 'lower': None}
    closes = df['c'].values[-(BOLL_PERIOD + 1):-1]
    mid = float(np.mean(closes))
    std = float(np.std(closes, ddof=0))
    upper = mid + std * BOLL_STD
    lower = mid - std * BOLL_STD
    return {'mid': mid, 'upper': upper, 'lower': lower}


def check_long_signal(price: float, bb_lower: float, open_price: float,
                      is_new_bar: bool = False) -> tuple[bool, float | None]:
    """做多信号检测
    条件：价格跌破下轨 + 当前K线阴线(open > price)
    返回: (有信号, 挂单价)
    挂单价 = bb_lower × (1 - ENTRY_OFFSET)
    """
    if price < bb_lower and open_price > price:
        limit_price = bb_lower * (1 - ENTRY_OFFSET)
        return True, limit_price
    return False, None


def check_short_signal(price: float, bb_upper: float, open_price: float,
                       is_new_bar: bool = False) -> tuple[bool, float | None]:
    """做空信号检测
    条件：价格突破上轨 + 当前K线阳线(open < price)
    返回: (有信号, 挂单价)
    挂单价 = bb_upper × (1 + ENTRY_OFFSET)
    """
    if price > bb_upper and open_price < price:
        limit_price = bb_upper * (1 + ENTRY_OFFSET)
        return True, limit_price
    return False, None


def check_exit_triggered(current_price: float, entry_price: float,
                         direction: str, stop_pct: float,
                         take_pct: float) -> str:
    """检查是否触发出场条件
    返回: 'stop_loss' | 'take_profit' | 'none'
    """
    if direction == 'long':
        if current_price <= entry_price * (1 - stop_pct):
            return 'stop_loss'
        if current_price >= entry_price * (1 + take_pct):
            return 'take_profit'
    else:  # short
        if current_price >= entry_price * (1 + stop_pct):
            return 'stop_loss'
        if current_price <= entry_price * (1 - take_pct):
            return 'take_profit'
    return 'none'
