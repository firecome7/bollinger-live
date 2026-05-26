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

BAR_SECONDS = 900  # 15m
POLL_INTERVAL = 15


class LiveTrader:
    """实盘主循环"""

    def __init__(self):
        t0 = time.time()
        self.api = GateAPI()
        logger.info(f"Gate.io API连接完成 ({time.time()-t0:.1f}s)")
        self.coins = self._get_target_coins()
        self.engine = TradingEngine(self.api, self.coins)
        self._init_candles(self.coins)  # 移到 engine 创建后
        self.last_bar_ts = 0
        self.last_log_ts = 0
        self.last_order_check_ts = 0
        self._tick_count = 0

    def _get_target_coins(self) -> list[str]:
        """获取目标币种列表，按成交量过滤选前150"""
        t0 = time.time()
        all_swaps = self.api.get_available_swaps()
        logger.info(f"Gate.io可用USDT永续合约: {len(all_swaps)} 个")

        # 排除大市值
        exclude = {'BTC', 'ETH', 'XRP', 'BNB', 'SOL', 'DOGE', 'ADA',
                    'USDC', 'TRX', 'LINK', 'AVAX', 'TON', 'DOT', 'MATIC'}
        filtered = [c for c in all_swaps if c not in exclude]
        logger.info(f"排除{len(exclude)}个大市值后剩: {len(filtered)} 个")

        # 拉全量行情，按24h成交量过滤+排序
        tickers = self.api.fetch_tickers_all()
        vol_list = []
        for coin in filtered:
            t = tickers.get(coin)
            if t is None:
                continue
            vol = float(t.get('quoteVolume', 0) or 0)
            if vol >= 2_000_000:  # 成交量≥200万USDT
                vol_list.append((coin, vol))

        vol_list.sort(key=lambda x: -x[1])  # 按成交量降序
        n = min(150, len(vol_list))
        coins = [c for c, _ in vol_list[:n]]
        coins.sort()  # 最终按字母排序，方便日志
        logger.info(f"成交量≥$200万的币: {len(vol_list)} 个, 实际取{n}个")
        if coins:
            logger.info(f"  最小成交额: ${vol_list[:n][-1][1]:,.0f} ({coins[0]})")
            logger.info(f"  最大成交额: ${vol_list[:n][0][1]:,.0f} ({coins[-1]})")
        logger.info(f"  (耗时{time.time()-t0:.1f}s)")

        # 对选中的币设置杠杆+双向持仓
        self.api.setup_coins(coins)

        return coins

    def _init_candles(self, coins: list[str]):
        """启动时获取所有币的初始K线"""
        n_ok = 0
        n_skip = 0
        t0 = time.time()
        logger.info(f"开始初始化K线数据 ({len(coins)}个币, 约{len(coins)*0.18:.0f}s)...")

        for i, coin in enumerate(coins):
            try:
                candles = self.api.fetch_ohlcv(coin, limit=50)
                if not candles or len(candles) < 26:
                    n_skip += 1
                    logger.debug(f"[{coin}] K线不足{len(candles) if candles else 0}根,跳过")
                    continue
                last = candles[-1]
                ts = last[0]
                bar_open = last[1]
                self.engine.on_new_bar(coin, ts, bar_open, candles[:-1])
                n_ok += 1
            except Exception as e:
                n_skip += 1
                logger.warning(f"[{coin}] 初始化失败: {e}")

            if (i + 1) % 20 == 0:
                logger.info(f"  初始化进度: {i+1}/{len(coins)} ({n_ok}成功/{n_skip}跳过)")

            time.sleep(0.15)

        logger.info(f"K线初始化完成: {n_ok}个币成功, {n_skip}个跳过 ({time.time()-t0:.0f}s)")

    # ── 主循环 ──

    def run(self):
        logger.info("=" * 50)
        logger.info("Bollinger策略实盘 启动")
        logger.info(f"  K线周期: {TIMEFRAME}")
        logger.info(f"  每笔${FIXED_POSITION_VALUE:.0f} 最多{MAX_POSITIONS}仓 杠杆50x")
        logger.info(f"  行情轮询: 每{POLL_INTERVAL}s")
        logger.info(f"  订单检查: 每5s")
        logger.info(f"  持仓同步: 每30s")
        logger.info(f"  触价超时: {TIMEOUT_SECONDS}s后转市价")
        logger.info("=" * 50)

        while True:
            try:
                self._tick()
            except KeyboardInterrupt:
                logger.info("🛑 用户手动停止")
                break
            except Exception as e:
                logger.error(f"❌ 主循环异常: {e}", exc_info=True)
                logger.warning("等待60s后自动恢复...")
                time.sleep(60)

            time.sleep(POLL_INTERVAL)

    def _tick(self):
        """单次轮询"""
        self._tick_count += 1
        now = time.time()
        bar_ts = self._current_bar_ts()

        # ── 1. 新K线检测 ──
        if bar_ts != self.last_bar_ts:
            bar_dt = datetime.fromtimestamp(bar_ts / 1000).strftime('%H:%M')
            logger.info(f"⏰ ── 新K线 {bar_dt} (tick#{self._tick_count}) ──")
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
                t0 = time.time()
                positions = self.api.fetch_positions()
                self.engine.sync_positions(positions)
            except Exception as e:
                logger.warning(f"持仓同步失败: {e}")

        # ── 5. 定期日志（每60秒）──
        if now - self.last_log_ts >= 60:
            try:
                bal = self.api.fetch_balance()
                n_hold = sum(1 for s in self.engine.state.values() if s.holding)
                n_pend = sum(1 for s in self.engine.state.values() if s.pending_entry)
                n_timer = sum(1 for s in self.engine.state.values() if s.pending_exit)
                logger.info(f"── 状态 [{datetime.now().strftime('%H:%M:%S')}] ── "
                            f"权益${bal['total']:.2f}(可用${bal['free']:.2f}) "
                            f"持仓{n_hold}/{MAX_POSITIONS} 挂单{n_pend} 倒计时{n_timer}")
            except Exception as e:
                logger.warning(f"状态日志失败: {e}")
            self.last_log_ts = now

    # ── K线处理 ──

    def _current_bar_ts(self) -> int:
        now = int(time.time())
        bar_start = now - (now % BAR_SECONDS)
        return bar_start * 1000

    def _on_new_bar(self, bar_ts: int):
        """新K线：更新所有币的布林带 + 超时撤单"""
        n_ok = 0
        n_fail = 0
        t0 = time.time()

        for coin in self.coins:
            try:
                candles = self.api.fetch_ohlcv(coin, limit=50)
                if not candles:
                    n_fail += 1
                    continue
                last = candles[-1]
                bar_open = last[1]
                self.engine.on_new_bar(coin, bar_ts, bar_open, candles[:-1])
                n_ok += 1
            except Exception as e:
                n_fail += 1
                logger.debug(f"[{coin}] K线更新失败: {e}")
            time.sleep(0.15)

        elapsed = time.time() - t0
        logger.info(f"K线更新: {n_ok}个币布林带已刷, {n_fail}个失败 ({elapsed:.0f}s)")

    # ── 行情轮询 ──

    def _poll_tickers(self):
        """轮询所有币的实时价格"""
        t0 = time.time()
        try:
            tickers = self.api.fetch_tickers_all()
        except Exception as e:
            logger.warning(f"获取行情失败: {e}")
            return

        n_processed = 0
        for coin, ticker in tickers.items():
            if coin not in self.engine.state:
                continue
            last_price = ticker.get('last')
            if last_price is None or last_price <= 0:
                continue
            try:
                self.engine.on_tick(coin, float(last_price))
                n_processed += 1
            except Exception as e:
                logger.debug(f"[{coin}] tick处理失败: {e}")

        if self._tick_count % 10 == 0:
            logger.debug(f"行情轮询: {len(tickers)}币获取, {n_processed}处理 ({time.time()-t0:.2f}s)")

    # ── 订单状态检查 ──

    def _check_orders(self):
        """检查订单状态（检测入场/出场挂单是否成交）"""
        try:
            orders = self.api.fetch_open_orders()
        except Exception as e:
            logger.warning(f"获取订单失败: {e}")
            return

        open_ids = {o['id'] for o in orders}
        n_entry_checked = 0
        n_exit_checked = 0
        n_entry_filled = 0
        n_exit_filled = 0

        for coin, cs in self.engine.state.items():
            # 入场挂单检测
            if cs.entry_order_id and cs.entry_order_id not in open_ids:
                n_entry_checked += 1
                if self._check_entry_fill(coin, cs):
                    n_entry_filled += 1

            # 出场挂单检测
            if cs.holding:
                for oid in (cs.stop_order_id, cs.take_order_id):
                    if oid and oid not in open_ids:
                        n_exit_checked += 1
                        if self._check_exit_fill(coin, cs, oid):
                            n_exit_filled += 1

        if n_entry_filled > 0 or n_exit_filled > 0:
            logger.info(f"订单检查: 入场+n_entry_filled成交 / 出场+n_exit_filled成交 "
                        f"(当前未成交{len(open_ids)}笔)")

    def _check_exit_fill(self, coin: str, cs, order_id: str) -> bool:
        """检查出场限价单是否成交，返回True=已成交"""
        sym = self.api.swap_symbol(coin)
        try:
            order_info = self.api.ex.fetch_order(order_id, sym)
            if order_info['status'] == 'closed' and float(order_info['filled']) > 0:
                fill_p = float(order_info.get('price', 0) or order_info.get('average', 0))
                otype = '止盈' if order_id == cs.take_order_id else '止损'
                logger.info(f"[{coin}] ✅ {otype}限价单成交 @ ${fill_p:.6f} ID={order_id}")
                self.engine.clear_position(coin, reason=f'limit_{otype}')
                return True
            elif order_info['status'] == 'canceled':
                logger.warning(f"[{coin}] ⚠️ 出场限价单被取消 ID={order_id}")
                return False
        except Exception as e:
            logger.debug(f"[{coin}] 查出场成交失败: {e}")
        return False

    def _check_entry_fill(self, coin: str, cs) -> bool:
        """检查入场单是否成交，返回True=已成交"""
        sym = self.api.swap_symbol(coin)
        try:
            order_info = self.api.ex.fetch_order(cs.entry_order_id, sym)
            if order_info['status'] == 'closed' and float(order_info['filled']) > 0:
                fill_price = float(order_info['price'] or order_info['average'])
                filled = float(order_info['filled'])
                logger.info(f"[{coin}] ✅ 入场单成交: {filled}张 @ ${fill_price:.6f} "
                            f"ID={cs.entry_order_id}")
                self.engine.on_order_filled(
                    coin, cs.entry_order_id, fill_price, filled,
                    order_info['side'], order_info.get('reduceOnly', False)
                )
                return True
            elif order_info['status'] == 'canceled':
                logger.info(f"[{coin}] 入场挂单被取消 ID={cs.entry_order_id}")
                cs.entry_order_id = None
                cs.entry_price = 0.0
                self.engine.active_coins.discard(coin)
                return False
        except Exception as e:
            logger.warning(f"[{coin}] 查入场成交失败: {e}")
        return False


if __name__ == '__main__':
    trader = LiveTrader()
    trader.run()
