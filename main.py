"""主入口 — Bollinger策略实盘"""
#!/usr/bin/env python3.12
from __future__ import annotations

import time
import logging
import sys
from datetime import datetime

from config import (
    load_api_keys, TIMEFRAME, TIMEOUT_SECONDS, FIXED_POSITION_VALUE, MAX_POSITIONS
)
from gate_api import GateAPI
from engine import TradingEngine

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bollinger_live.log'),
    ]
)
logger = logging.getLogger('main')

# 15m = 900秒
BAR_SECONDS = 900
POLL_INTERVAL = 15  # 15秒轮询一次行情


class LiveTrader:
    """实盘主循环"""

    def __init__(self):
        self.api = GateAPI()
        self.coins = self._get_target_coins()
        self.engine = TradingEngine(self.api, self.coins)
        self.last_bar_ts = 0  # 上次处理的K线时间戳
        self.last_log_ts = 0  # 上次打日志时间
        self.last_order_check_ts = 0  # 上次检查订单状态

    def _get_target_coins(self) -> list[str]:
        """获取目标币种列表"""
        all_swaps = self.api.get_available_swaps()
        logger.info(f"Gate.io可用USDT永续: {len(all_swaps)} 个")

        # 排除前10大市值币种（BTC/ETH等）
        exclude = {'BTC', 'ETH', 'XRP', 'BNB', 'SOL', 'DOGE', 'ADA',
                    'USDC', 'TRX', 'LINK', 'AVAX', 'TON', 'DOT', 'MATIC'}
        coins = [c for c in all_swaps if c not in exclude]
        coins.sort()
        logger.info(f"目标币种: {len(coins)} 个 (排除前10)")

        # 初始化K线数据
        self._init_candles(coins)
        return coins

    def _init_candles(self, coins: list[str]):
        """启动时获取所有币的初始K线"""
        for i, coin in enumerate(coins):
            try:
                candles = self.api.fetch_ohlcv(coin, limit=50)
                if not candles or len(candles) < 26:
                    continue
                # 用最后两根K线初始化
                last = candles[-1]
                prev = candles[-2]
                ts = last[0]
                bar_open = last[1]
                self.engine.on_new_bar(coin, ts, bar_open, candles[:-1])
            except Exception as e:
                logger.warning(f"[{coin}] 初始化失败: {e}")

            if (i + 1) % 20 == 0:
                logger.info(f"  初始化进度: {i+1}/{len(coins)}")

    # ── 主循环 ──

    def run(self):
        """开始运行"""
        logger.info("=" * 50)
        logger.info("Bollinger策略实盘启动")
        logger.info(f"  K线周期: {TIMEFRAME}")
        logger.info(f"  每笔${FIXED_POSITION_VALUE:.0f}, 最多{MAX_POSITIONS}仓")
        logger.info(f"  轮询间隔: {POLL_INTERVAL}s")
        logger.info(f"  触价超时转市价: {TIMEOUT_SECONDS}s")
        logger.info("=" * 50)

        while True:
            try:
                self._tick()
            except KeyboardInterrupt:
                logger.info("用户停止")
                break
            except Exception as e:
                logger.error(f"主循环异常: {e}", exc_info=True)
                time.sleep(60)  # 异常后等一分钟再试

            time.sleep(POLL_INTERVAL)

    def _tick(self):
        """单次轮询"""
        now = time.time()
        now_dt = datetime.now()
        bar_ts = self._current_bar_ts()

        # ── 1. 新K线检测 ──
        if bar_ts != self.last_bar_ts:
            self._on_new_bar(bar_ts)
            self.last_bar_ts = bar_ts

        # ── 2. 行情轮询 ──
        self._poll_tickers()

        # ── 3. 订单状态检查（每5秒）──
        if now - self.last_order_check_ts >= 5:
            self._check_orders()
            self.last_order_check_ts = now

        # ── 4. 持仓同步（每30秒）──
        if int(now) % 30 < POLL_INTERVAL:
            try:
                positions = self.api.fetch_positions()
                self.engine.sync_positions(positions)
            except Exception as e:
                logger.warning(f"持仓同步失败: {e}")

        # ── 5. 定期日志（每60秒）──
        if now - self.last_log_ts >= 60:
            bal = self.api.fetch_balance()
            print(f"\n--- {now_dt.strftime('%H:%M:%S')} | "
                  f"总权益${bal['total']:.2f} | 可用${bal['free']:.2f} | 抵扣${bal['used']:.2f}")
            print(self.engine.get_summary())
            self.last_log_ts = now

    # ── K线处理 ──

    def _current_bar_ts(self) -> int:
        """当前15mK线的时间戳(毫秒)"""
        now = int(time.time())
        bar_start = now - (now % BAR_SECONDS)
        return bar_start * 1000

    def _on_new_bar(self, bar_ts: int):
        """新K线到"""
        for coin in self.coins:
            try:
                candles = self.api.fetch_ohlcv(coin, limit=50)
                if not candles:
                    continue
                last = candles[-1]
                # 确保K线时间戳匹配
                bar_open = last[1]
                self.engine.on_new_bar(coin, bar_ts, bar_open, candles[:-1])
            except Exception as e:
                logger.warning(f"[{coin}] 新K线更新失败: {e}")

    # ── 行情轮询 ──

    def _poll_tickers(self):
        """轮询所有币的实时价格"""
        try:
            tickers = self.api.fetch_tickers_all()
        except Exception as e:
            logger.warning(f"获取行情失败: {e}")
            return

        for coin, ticker in tickers.items():
            if coin not in self.engine.state:
                continue
            last_price = ticker.get('last')
            if last_price is None or last_price <= 0:
                continue
            try:
                self.engine.on_tick(coin, float(last_price))
            except Exception as e:
                logger.warning(f"[{coin}] tick处理失败: {e}")

    # ── 订单状态检查 ──

    def _check_orders(self):
        """检查订单状态（用于检测是否成交）"""
        try:
            orders = self.api.fetch_open_orders()
        except Exception as e:
            logger.warning(f"获取订单失败: {e}")
            return

        # 构建当前未成交订单ID集合
        open_ids = set()
        for o in orders:
            open_ids.add(o['id'])
            coin = o['coin']
            if coin not in self.engine.state:
                continue
            cs = self.engine.state[coin]

        # 检查我跟踪的订单是否已不在open列表中→可能成交了
        for coin, cs in self.engine.state.items():
            # 检查入场挂单
            if cs.entry_order_id and cs.entry_order_id not in open_ids:
                # 查成交记录确认
                self._check_entry_fill(coin, cs)

    def _check_entry_fill(self, coin: str, cs):
        """检查入场单是否成交"""
        sym = self.api.swap_symbol(coin)
        try:
            # 用fetch_orders查历史订单
            order_info = self.api.ex.fetch_order(cs.entry_order_id, sym)
            if order_info['status'] == 'closed' and float(order_info['filled']) > 0:
                fill_price = float(order_info['price'] or order_info['average'])
                filled = float(order_info['filled'])
                logger.info(f"[{coin}] 检测到入场单成交: {filled}张 @ {fill_price}")
                self.engine.on_order_filled(
                    coin, cs.entry_order_id, fill_price, filled,
                    order_info['side'], order_info.get('reduceOnly', False)
                )
            elif order_info['status'] == 'canceled':
                # 被取消了，清理状态
                cs.entry_order_id = None
                cs.entry_price = 0.0
                self.engine.active_coins.discard(coin)
        except Exception as e:
            logger.warning(f"[{coin}] 查成交失败: {e}")


if __name__ == '__main__':
    trader = LiveTrader()
    trader.run()
