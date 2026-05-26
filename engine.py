"""核心交易引擎"""
from __future__ import annotations

import time
import logging
from typing import Optional
from dataclasses import dataclass, field

from config import (
    FIXED_POSITION_VALUE, LEVERAGE, MAX_POSITIONS,
    STOP_LOSS_PCT, TAKE_PROFIT_PCT, ORDER_LIFETIME_BARS,
    TIMEOUT_SECONDS, TAKER_FEE, MAKER_FEE,
)
from signals import (
    calc_bollinger, check_long_signal, check_short_signal, check_exit_triggered
)
from gate_api import GateAPI

logger = logging.getLogger('engine')


@dataclass
class CoinState:
    """单个币的状态"""
    symbol: str  # 币名 e.g. PEPE

    # 布林带（每根新K线更新）
    bb_mid: float = 0.0
    bb_upper: float = 0.0
    bb_lower: float = 0.0

    # 本根K线开盘价（实时跟踪）
    bar_open: float = 0.0
    bar_ts: int = 0          # 当前K线的时间戳ms

    # 入场限价挂单
    entry_order_id: Optional[str] = None
    entry_price: float = 0.0
    entry_side: str = ''     # 'buy'(long) 或 'sell'(short)
    entry_bar_count: int = 0  # 挂了多少根K线

    # 持仓状态
    holding: bool = False
    position_side: str = ''   # 'long' / 'short'
    position_size: float = 0.0  # 持仓张数
    entry_fill_price: float = 0.0

    # 出场限价单ID（止损/止盈）
    stop_order_id: Optional[str] = None
    take_order_id: Optional[str] = None
    stop_price: float = 0.0
    take_price: float = 0.0

    # 触价后的15秒倒计时
    exit_triggered_at: Optional[float] = None  # time.time()
    exit_triggered_type: str = ''  # 'stop_loss' 或 'take_profit'

    # 状态描述
    @property
    def status(self) -> str:
        if self.holding:
            return f"持仓{self.position_side} 入场{self.entry_fill_price:.6f} 止盈{self.take_price:.6f} 止损{self.stop_price:.6f}"
        if self.entry_order_id:
            return f"挂单{self.entry_side} {self.entry_price:.6f} 已等{self.entry_bar_count}K线"
        return "等待"

    @property
    def pending_entry(self) -> bool:
        return self.entry_order_id is not None

    @property
    def pending_exit(self) -> bool:
        return self.exit_triggered_at is not None


class TradingEngine:
    """交易引擎主类"""

    def __init__(self, api: GateAPI, coins: list[str]):
        self.api = api
        self.state: dict[str, CoinState] = {c: CoinState(symbol=c) for c in coins}
        # 活跃币种（有挂单或持仓的）
        self.active_coins: set[str] = set()

        # 启动时同步交易所状态
        self._sync_positions()
        self._sync_orders()

        logger.info(f"引擎启动: {len(coins)} 个币监控, "
                     f"当前持仓 {sum(1 for s in self.state.values() if s.holding)} 个, "
                     f"当前挂单 {sum(1 for s in self.state.values() if s.pending_entry)} 个")

    # ── 同步 ──

    def _sync_positions(self):
        """启动时从交易所同步已有持仓"""
        positions = self.api.fetch_positions()
        for p in positions:
            coin = p['coin']
            if coin in self.state:
                cs = self.state[coin]
                cs.holding = True
                cs.position_side = p['side']
                cs.position_size = p['size']
                cs.entry_fill_price = p['entry_price']
                # 恢复止盈止损价（从内置数据计算而不是从交易所读）
                if cs.position_side == 'long':
                    cs.stop_price = cs.entry_fill_price * (1 - STOP_LOSS_PCT)
                    cs.take_price = cs.entry_fill_price * (1 + TAKE_PROFIT_PCT)
                else:
                    cs.stop_price = cs.entry_fill_price * (1 + STOP_LOSS_PCT)
                    cs.take_price = cs.entry_fill_price * (1 - TAKE_PROFIT_PCT)
                self.active_coins.add(coin)

    def _sync_orders(self):
        """启动时从交易所同步未成交订单"""
        orders = self.api.fetch_open_orders()
        for o in orders:
            coin = o['coin']
            if coin in self.state:
                cs = self.state[coin]
                if not o['reduce_only']:
                    # 入场挂单
                    cs.entry_order_id = o['id']
                    cs.entry_price = o['price']
                    cs.entry_side = o['side']
                    cs.entry_bar_count = 0
                    self.active_coins.add(coin)

    # ── K线更新 ──

    def on_new_bar(self, coin: str, bar_ts: int, bar_open: float, candles: list[list]):
        """每根新K线开始时调用"""
        cs = self.state[coin]

        # 更新当前K线信息
        cs.bar_ts = bar_ts
        cs.bar_open = bar_open

        # 重新计算布林带
        bb = calc_bollinger(candles)
        cs.bb_mid = bb['mid'] or 0.0
        cs.bb_upper = bb['upper'] or 0.0
        cs.bb_lower = bb['lower'] or 0.0

        # 挂单超时检测
        if cs.pending_entry and not cs.holding:
            cs.entry_bar_count += 1
            if cs.entry_bar_count >= ORDER_LIFETIME_BARS:
                logger.info(f"[{coin}] 挂单超时{ORDER_LIFETIME_BARS}K线，撤单")
                self.api.cancel_order(coin, cs.entry_order_id)
                cs.entry_order_id = None
                cs.entry_price = 0.0
                cs.entry_bar_count = 0
                self.active_coins.discard(coin)

    # ── Tick更新（实时价格）──

    def on_tick(self, coin: str, price: float) -> list[dict]:
        """实时价格更新（每15秒轮询）
        返回: [action_log, ...] 给日志使用
        """
        cs = self.state[coin]
        actions = []

        # ── 有持仓：检查出场 ──
        if cs.holding:
            actions.extend(self._check_exit(coin, price))

        # ── 在15秒倒计时中 ──
        if cs.pending_exit:
            actions.extend(self._check_timeout(coin, price))

        # ── 无持仓无挂单：检查入场信号 ──
        if not cs.holding and not cs.pending_entry:
            if cs.bb_lower > 0 and cs.bb_upper > 0:
                action = self._check_entry(coin, price, cs.bar_open)
                if action:
                    actions.append(action)

        return actions

    # ── 入场 ──

    def _check_entry(self, coin: str, price: float, bar_open: float) -> Optional[dict]:
        """检查入场信号"""
        cs = self.state[coin]

        # 仓位上限
        holding_count = sum(1 for s in self.state.values() if s.holding)
        if holding_count >= MAX_POSITIONS:
            return None

        # 做多信号
        has_long, limit_p = check_long_signal(price, cs.bb_lower, bar_open)
        if has_long:
            return self._place_entry(coin, 'buy', limit_p, 'long')

        # 做空信号
        has_short, limit_p = check_short_signal(price, cs.bb_upper, bar_open)
        if has_short:
            return self._place_entry(coin, 'sell', limit_p, 'short')

        return None

    def _place_entry(self, coin: str, side: str, limit_price: float,
                     pos_side: str) -> Optional[dict]:
        """挂入场限价单"""
        cs = self.state[coin]

        order = self.api.create_limit_entry(coin, side, FIXED_POSITION_VALUE, limit_price)
        if order is None:
            logger.warning(f"[{coin}] 入场挂单失败（金额太小）")
            return None

        cs.entry_order_id = order['id']
        cs.entry_price = limit_price
        cs.entry_side = side
        cs.entry_bar_count = 0
        self.active_coins.add(coin)

        logger.info(f"[{coin}] 入场挂单: {side} @ {limit_price:.6f} (ID={order['id']})")
        return {
            'time': time.time(),
            'coin': coin,
            'type': 'entry_order',
            'side': pos_side,
            'price': limit_price,
            'order_id': order['id'],
        }

    # ── 出场 ──

    def _check_exit(self, coin: str, price: float) -> list[dict]:
        """检查是否触发出场条件"""
        cs = self.state[coin]
        if cs.pending_exit:
            return []  # 已经在倒计时

        result = check_exit_triggered(price, cs.entry_fill_price,
                                       cs.position_side, STOP_LOSS_PCT, TAKE_PROFIT_PCT)
        if result == 'none':
            return []

        # 标记触发出场→进入15秒倒计时
        cs.exit_triggered_at = time.time()
        cs.exit_triggered_type = result

        logger.info(f"[{coin}] {result} 触发! 当前价{price:.6f} "
                     f"入场{cs.entry_fill_price:.6f}")

        # 按之前商定的流程：触价先等15秒让限价单成交
        # 限价单已经在交易所挂着（通过open orders管理）
        # 这里不用额外操作，等_check_timeout处理
        
        return [{
            'time': time.time(),
            'coin': coin,
            'type': result,
            'price': price,
            'entry_price': cs.entry_fill_price,
        }]

    def _check_timeout(self, coin: str, price: float) -> list[dict]:
        """检查15秒超时→转市价"""
        cs = self.state[coin]
        if not cs.pending_exit:
            return []

        # 如果持仓已经被清掉了（sync_positions检测到出场单成交），取消倒计时
        if not cs.holding:
            cs.exit_triggered_at = None
            cs.exit_triggered_type = ''
            return []

        elapsed = time.time() - cs.exit_triggered_at
        if elapsed < TIMEOUT_SECONDS:
            return []  # 还在倒计时内

        # 15秒到了，限价单还没成交 → 市价强平
        logger.info(f"[{coin}] {cs.exit_triggered_type} 限价15秒未成交，转市价平仓")
        close_side = 'sell' if cs.position_side == 'long' else 'buy'
        try:
            order = self.api.create_market_close(coin, close_side, cs.position_size)
            if order:
                logger.info(f"[{coin}] 市价平仓完成: {order.get('id','?')}")
            else:
                logger.warning(f"[{coin}] 市价平仓返回空，可能已平仓")
        except Exception as e:
            logger.warning(f"[{coin}] 市价平仓异常（可能已平）: {e}")

        self.clear_position(coin)
        return [{
            'time': time.time(),
            'coin': coin,
            'type': f'{cs.exit_triggered_type}_market',
            'price': price,
        }]

    # ── 订单成交回调 ──

    def on_order_filled(self, coin: str, order_id: str, fill_price: float,
                        filled_amount: float, side: str, reduce_only: bool):
        """处理订单成交"""
        cs = self.state[coin]

        if reduce_only:
            # 出场单成交了
            self.clear_position(coin)
            logger.info(f"[{coin}] 平仓成交 @ {fill_price:.6f}")
            return

        # 入场单成交了
        cs.holding = True
        cs.position_side = 'long' if side == 'buy' else 'short'
        cs.position_size = filled_amount
        cs.entry_fill_price = fill_price
        cs.entry_order_id = None

        # 计算止损止盈价
        if cs.position_side == 'long':
            cs.stop_price = fill_price * (1 - STOP_LOSS_PCT)
            cs.take_price = fill_price * (1 + TAKE_PROFIT_PCT)
        else:
            cs.stop_price = fill_price * (1 + STOP_LOSS_PCT)
            cs.take_price = fill_price * (1 - TAKE_PROFIT_PCT)

        # 挂出场限价单（止损+止盈同时挂）
        close_side = 'sell' if cs.position_side == 'long' else 'buy'
        opposite = 'buy' if cs.position_side == 'long' else 'sell'

        # 挂止盈
        tp_order = self.api.create_limit_close(coin, close_side, cs.position_size, cs.take_price)
        if tp_order:
            cs.take_order_id = tp_order['id']

        # 挂止损
        sl_order = self.api.create_limit_close(coin, close_side, cs.position_size, cs.stop_price)
        if sl_order:
            cs.stop_order_id = sl_order['id']

        logger.info(f"[{coin}] 入场成交 @ {fill_price:.6f} "
                     f"方向{cs.position_side} 仓位${FIXED_POSITION_VALUE:.0f} "
                     f"止盈{cs.take_price:.6f} 止损{cs.stop_price:.6f}")

    def clear_position(self, coin: str):
        """清空持仓状态（外部可调用）"""
        cs = self.state[coin]
        cs.holding = False
        cs.position_side = ''
        cs.position_size = 0.0
        cs.entry_fill_price = 0.0
        cs.stop_order_id = None
        cs.take_order_id = None
        cs.stop_price = 0.0
        cs.take_price = 0.0
        cs.exit_triggered_at = None
        cs.exit_triggered_type = ''
        cs.entry_order_id = None
        self.active_coins.discard(coin)

    def sync_positions(self, exchange_positions: list[dict]):
        """从交易所持仓列表同步引擎状态
        处理出场限价单成交但没有经过引擎检测的情况
        """
        exchange_coins = {p['coin'] for p in exchange_positions}
        for coin, cs in self.state.items():
            if cs.holding and coin not in exchange_coins:
                logger.info(f"[{coin}] 检测到持仓已平（限价单成交）")
                self.clear_position(coin)

    # ── 状态输出 ──

    def get_summary(self) -> str:
        """当前状态摘要"""
        lines = [f"持仓: {sum(1 for s in self.state.values() if s.holding)}/{MAX_POSITIONS}  "
                 f"挂单入场: {sum(1 for s in self.state.values() if s.pending_entry)}"]
        for coin in sorted(self.active_coins):
            cs = self.state[coin]
            lines.append(f"  {coin:<12} {cs.status}")
        return '\n'.join(lines)
