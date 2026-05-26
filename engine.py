"""核心交易引擎"""
from __future__ import annotations

import time
import logging
from typing import Optional
from dataclasses import dataclass, field

from config import (
    FIXED_POSITION_VALUE, LEVERAGE, MAX_POSITIONS,
    STOP_LOSS_PCT, TAKE_PROFIT_PCT, ORDER_LIFETIME_BARS,
    TIMEOUT_SECONDS, TAKER_FEE, MAKER_FEE, DRY_RUN,
)
from signals import (
    calc_bollinger, check_long_signal, check_short_signal, check_exit_triggered
)
from gate_api import GateAPI

logger = logging.getLogger('engine')


@dataclass
class CoinState:
    """单个币的状态"""
    symbol: str

    # 布林带
    bb_mid: float = 0.0
    bb_upper: float = 0.0
    bb_lower: float = 0.0

    # 本根K线
    bar_open: float = 0.0
    bar_ts: int = 0
    bar_high: float = 0.0
    bar_low: float = 0.0

    # 入场限价挂单
    entry_order_id: Optional[str] = None
    entry_price: float = 0.0
    entry_side: str = ''
    entry_bar_count: int = 0

    # 持仓状态
    holding: bool = False
    position_side: str = ''
    position_size: float = 0.0
    entry_fill_price: float = 0.0

    # 出场限价单
    stop_order_id: Optional[str] = None
    take_order_id: Optional[str] = None
    stop_price: float = 0.0
    take_price: float = 0.0

    # 触价倒计时
    exit_triggered_at: Optional[float] = None
    exit_triggered_type: str = ''

    # 同K线保护：本根K线内已止损止盈的，不再入场
    stopped_out_this_bar: bool = False

    @property
    def status(self) -> str:
        if self.holding:
            return (f"持仓{self.position_side} 入场${self.entry_fill_price:.6f} "
                    f"止盈${self.take_price:.6f} 止损${self.stop_price:.6f} "
                    f"张数{self.position_size}")
        if self.entry_order_id:
            return f"挂单{self.entry_side} ${self.entry_price:.6f} 已等{self.entry_bar_count}K线"
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
        self.active_coins: set[str] = set()
        self._tick_count = 0

        self._sync_positions()
        self._sync_orders()

        n_hold = sum(1 for s in self.state.values() if s.holding)
        n_pend = sum(1 for s in self.state.values() if s.pending_entry)
        logger.info(f"引擎启动 ✅  监控{len(coins)}币  持仓{n_hold}  挂单{n_pend}")

    # ═══════════════ 同步 ═══════════════

    def _sync_positions(self):
        """启动时从交易所同步已有持仓"""
        positions = self.api.fetch_positions()
        logger.info(f"交易所持仓同步: 共{len(positions)}个活跃持仓")
        for p in positions:
            coin = p['coin']
            if coin in self.state:
                cs = self.state[coin]
                cs.holding = True
                cs.position_side = p['side']
                cs.position_size = p['size']
                cs.entry_fill_price = p['entry_price']
                if cs.position_side == 'long':
                    cs.stop_price = cs.entry_fill_price * (1 - STOP_LOSS_PCT)
                    cs.take_price = cs.entry_fill_price * (1 + TAKE_PROFIT_PCT)
                else:
                    cs.stop_price = cs.entry_fill_price * (1 + STOP_LOSS_PCT)
                    cs.take_price = cs.entry_fill_price * (1 - TAKE_PROFIT_PCT)
                self.active_coins.add(coin)
                logger.info(f"  [{coin}] 恢复持仓 {cs.position_side} "
                            f"入场${cs.entry_fill_price:.4f} "
                            f"止盈${cs.take_price:.4f} 止损${cs.stop_price:.4f}")
            else:
                logger.warning(f"  [{coin}] 交易所持仓但不在监控列表，跳过")

    def _sync_orders(self):
        """启动时从交易所同步未成交订单"""
        orders = self.api.fetch_open_orders()
        n_entry = 0
        n_exit = 0
        for o in orders:
            coin = o['coin']
            if coin in self.state:
                cs = self.state[coin]
                if not o['reduce_only']:
                    cs.entry_order_id = o['id']
                    cs.entry_price = o['price']
                    cs.entry_side = o['side']
                    cs.entry_bar_count = 0
                    self.active_coins.add(coin)
                    n_entry += 1
                else:
                    # 出场限价单（止盈/止损）
                    if cs.holding:
                        if o['side'] == ('sell' if cs.position_side == 'long' else 'buy'):
                            cs.stop_order_id = o['id']
                        else:
                            cs.take_order_id = o['id']
                    n_exit += 1
        logger.info(f"订单同步: {n_entry}个入场挂单, {n_exit}个出场限价单")

    # ═══════════════ K线更新 ═══════════════

    def on_new_bar(self, coin: str, bar_ts: int, bar_open: float, candles: list[list]):
        """每根新K线开始时调用"""
        cs = self.state[coin]

        # 如果当前有挂单且前一根K线走完都没成交，记一下
        old_bb = (cs.bb_lower, cs.bb_upper)

        cs.bar_ts = bar_ts
        cs.bar_open = bar_open
        cs.bar_high = 0.0
        cs.bar_low = 999999.0
        cs.stopped_out_this_bar = False  # 新K线，重置同K线保护

        bb = calc_bollinger(candles)
        cs.bb_mid = bb['mid'] or 0.0
        cs.bb_upper = bb['upper'] or 0.0
        cs.bb_lower = bb['lower'] or 0.0

        if cs.bb_mid > 0:
            logger.debug(f"[{coin}] 新K线 BB更新: 中轨${cs.bb_mid:.4f} "
                         f"上轨${cs.bb_upper:.4f} 下轨${cs.bb_lower:.4f} "
                         f"开盘${bar_open:.4f}")

        # 挂单超时检测
        if cs.pending_entry and not cs.holding:
            cs.entry_bar_count += 1
            logger.debug(f"[{coin}] 入场挂单已等{cs.entry_bar_count}/{ORDER_LIFETIME_BARS}K线")
            if cs.entry_bar_count >= ORDER_LIFETIME_BARS:
                old_id = cs.entry_order_id
                ok = self.api.cancel_order(coin, cs.entry_order_id)
                logger.info(f"[{coin}] 📋 入场挂单超时{ORDER_LIFETIME_BARS}K线 "
                            f"撤单{'✅' if ok else '❌'} (ID={old_id})")
                cs.entry_order_id = None
                cs.entry_price = 0.0
                cs.entry_bar_count = 0
                self.active_coins.discard(coin)

    # ═══════════════ Tick实时价格 ═══════════════

    def on_tick(self, coin: str, price: float) -> list[dict]:
        """实时价格更新（每15秒轮询）"""
        cs = self.state[coin]
        self._tick_count += 1

        # 更新本根K线的最高最低（日志用）
        if price > cs.bar_high:
            cs.bar_high = price
        if price < cs.bar_low:
            cs.bar_low = price

        actions = []

        if cs.holding:
            actions.extend(self._check_exit(coin, price))

        if cs.pending_exit:
            actions.extend(self._check_timeout(coin, price))

        if not cs.holding and not cs.pending_entry:
            if cs.bb_lower > 0 and cs.bb_upper > 0:
                action = self._check_entry(coin, price, cs.bar_open)
                if action:
                    actions.append(action)

        return actions

    # ═══════════════ 入场 ═══════════════

    def _check_entry(self, coin: str, price: float, bar_open: float) -> Optional[dict]:
        """检查入场信号"""
        cs = self.state[coin]

        # 同K线保护：本根K线已止损止盈，不再入场
        if cs.stopped_out_this_bar:
            logger.debug(f"[{coin}] 跳过信号: 本根K线已止损/止盈")
            return None

        # 仓位上限
        holding_count = sum(1 for s in self.state.values() if s.holding)
        if holding_count >= MAX_POSITIONS:
            logger.debug(f"[{coin}] 跳过信号: 已达上限{MAX_POSITIONS}/{MAX_POSITIONS}")
            return None

        # 计算信号
        is_green = bar_open < price
        is_red = bar_open > price

        # 做多检测
        price_below_lower = price < cs.bb_lower
        if price_below_lower and is_red:
            has_long, limit_p = check_long_signal(price, cs.bb_lower, bar_open)
            if has_long:
                logger.info(f"[{coin}] 📈 做多信号! "
                            f"价格${price:.4f}<下轨${cs.bb_lower:.4f} "
                            f"阴线(is_green={is_green}) "
                            f"限价${limit_p:.4f} (下轨×{1-0.04:.2f})")
                return self._place_entry(coin, 'buy', limit_p, 'long')
        elif price_below_lower and not is_red:
            logger.debug(f"[{coin}] 未做多: 跌破下轨但阳线(is_green={is_green}) "
                         f"price=${price:.4f} bb_lower=${cs.bb_lower:.4f}")

        # 做空检测
        price_above_upper = price > cs.bb_upper
        if price_above_upper and is_green:
            has_short, limit_p = check_short_signal(price, cs.bb_upper, bar_open)
            if has_short:
                logger.info(f"[{coin}] 📉 做空信号! "
                            f"价格${price:.4f}>上轨${cs.bb_upper:.4f} "
                            f"阳线(is_green={is_green}) "
                            f"限价${limit_p:.4f} (上轨×{1+0.04:.2f})")
                return self._place_entry(coin, 'sell', limit_p, 'short')
        elif price_above_upper and not is_green:
            logger.debug(f"[{coin}] 未做空: 突破上轨但阴线(is_green={is_green}) "
                         f"price=${price:.4f} bb_upper=${cs.bb_upper:.4f}")

        return None

    def _place_entry(self, coin: str, side: str, limit_price: float,
                     pos_side: str) -> Optional[dict]:
        """挂入场限价单"""
        cs = self.state[coin]

        if DRY_RUN:
            logger.info(f"[{coin}] 🔍 [验证模式] 检测到{pos_side}信号 "
                        f"限价${limit_price:.6f} 不下单")
            return {
                'time': time.time(),
                'coin': coin,
                'type': 'dry_run_signal',
                'side': pos_side,
                'price': limit_price,
            }

        order = self.api.create_limit_entry(coin, side, FIXED_POSITION_VALUE, limit_price)
        if order is None:
            logger.warning(f"[{coin}] ❌ 入场挂单失败（金额太小或精度问题）")
            return None

        cs.entry_order_id = order['id']
        cs.entry_price = limit_price
        cs.entry_side = side
        cs.entry_bar_count = 0
        self.active_coins.add(coin)

        logger.info(f"[{coin}] ✅ 入场挂单成功 方向{pos_side} "
                    f"${limit_price:.6f} "
                    f"名义价值${FIXED_POSITION_VALUE:.0f} "
                    f"ID={order['id']}")

        return {
            'time': time.time(),
            'coin': coin,
            'type': 'entry_order',
            'side': pos_side,
            'price': limit_price,
            'order_id': order['id'],
        }

    # ═══════════════ 出场 ═══════════════

    def _check_exit(self, coin: str, price: float) -> list[dict]:
        """检查是否触发出场条件"""
        cs = self.state[coin]
        if cs.pending_exit:
            return []

        # 当前价 vs 止损/止盈 明细
        if cs.position_side == 'long':
            stop_hit = price <= cs.stop_price
            take_hit = price >= cs.take_price
            logger.debug(f"[{coin}] 出场检查: "
                         f"price=${price:.4f} stop=${cs.stop_price:.4f}(hit={stop_hit}) "
                         f"take=${cs.take_price:.4f}(hit={take_hit})")
        else:
            stop_hit = price >= cs.stop_price
            take_hit = price <= cs.take_price
            logger.debug(f"[{coin}] 出场检查: "
                         f"price=${price:.4f} stop=${cs.stop_price:.4f}(hit={stop_hit}) "
                         f"take=${cs.take_price:.4f}(hit={take_hit})")

        result = check_exit_triggered(price, cs.entry_fill_price,
                                       cs.position_side, STOP_LOSS_PCT, TAKE_PROFIT_PCT)
        if result == 'none':
            return []

        cs.exit_triggered_at = time.time()
        cs.exit_triggered_type = result

        # 计算盈亏
        if cs.position_side == 'long':
            pnl_pct = (price / cs.entry_fill_price - 1) * 100
        else:
            pnl_pct = (1 - price / cs.entry_fill_price) * 100
        pnl_usd = pnl_pct / 100 * FIXED_POSITION_VALUE

        logger.info(f"[{coin}] 🚨 {result}触发! "
                    f"当前${price:.4f} 入场${cs.entry_fill_price:.4f} "
                    f"盈亏{'+' if pnl_usd>=0 else ''}${pnl_usd:.2f}({pnl_pct:+.2f}%) "
                    f"等待{TIMEOUT_SECONDS}s限价成交")

        return [{
            'time': time.time(),
            'coin': coin,
            'type': result,
            'price': price,
            'pnl_pct': round(pnl_pct, 2),
            'pnl_usd': round(pnl_usd, 2),
            'entry_price': cs.entry_fill_price,
        }]

    def _check_timeout(self, coin: str, price: float) -> list[dict]:
        """检查15秒超时→转市价"""
        cs = self.state[coin]
        if not cs.pending_exit:
            return []

        # 持仓已被清掉（出场单成交/sync处理过了）
        if not cs.holding:
            logger.debug(f"[{coin}] 倒计时取消: 持仓已平")
            cs.exit_triggered_at = None
            cs.exit_triggered_type = ''
            return []

        elapsed = time.time() - cs.exit_triggered_at
        remaining = TIMEOUT_SECONDS - elapsed

        if remaining > 0:
            logger.debug(f"[{coin}] ⏳ 倒计时中: {elapsed:.0f}s/{TIMEOUT_SECONDS}s "
                         f"剩余{remaining:.0f}s")
            return []

        # 15秒到，市价强平
        logger.info(f"[{coin}] ⏰ 限价{TIMEOUT_SECONDS}s未成交，执行市价平仓 "
                    f"方向{cs.position_side} 张数{cs.position_size}")

        close_side = 'sell' if cs.position_side == 'long' else 'buy'
        try:
            order = self.api.create_market_close(coin, close_side, cs.position_size)
            if order:
                logger.info(f"[{coin}] ✅ 市价平仓成功 ID={order.get('id','?')}")
            else:
                logger.warning(f"[{coin}] ⚠️ 市价平仓返回空（可能已平）")
        except Exception as e:
            logger.error(f"[{coin}] ❌ 市价平仓异常: {e}")

        self.clear_position(coin, reason=f"market_close_{cs.exit_triggered_type}")
        return [{
            'time': time.time(),
            'coin': coin,
            'type': f'{cs.exit_triggered_type}_market',
            'price': price,
        }]

    # ═══════════════ 订单成交 ═══════════════

    def on_order_filled(self, coin: str, order_id: str, fill_price: float,
                        filled_amount: float, side: str, reduce_only: bool):
        """处理订单成交"""
        cs = self.state[coin]

        if reduce_only:
            # 出场单成交
            exit_type = '止盈' if cs.exit_triggered_type == 'take_profit' else '止损'
            logger.info(f"[{coin}] ✅ {exit_type}限价单成交 @ ${fill_price:.6f} "
                        f"ID={order_id}")
            self.clear_position(coin, reason=f"limit_{cs.exit_triggered_type}")
            return

        # 入场单成交
        pos_side = 'long' if side == 'buy' else 'short'
        cs.holding = True
        cs.position_side = pos_side
        cs.position_size = filled_amount
        cs.entry_fill_price = fill_price
        cs.entry_order_id = None

        # 计算止盈止损价
        if cs.position_side == 'long':
            cs.stop_price = fill_price * (1 - STOP_LOSS_PCT)
            cs.take_price = fill_price * (1 + TAKE_PROFIT_PCT)
        else:
            cs.stop_price = fill_price * (1 + STOP_LOSS_PCT)
            cs.take_price = fill_price * (1 - TAKE_PROFIT_PCT)

        close_side = 'sell' if cs.position_side == 'long' else 'buy'

        # 挂止盈限价单
        tp_order = self.api.create_limit_close(coin, close_side, cs.position_size, cs.take_price)
        if tp_order:
            cs.take_order_id = tp_order['id']
            logger.info(f"[{coin}] 止盈限价单已挂: ${cs.take_price:.6f} ID={tp_order['id']}")
        else:
            logger.warning(f"[{coin}] ⚠️ 止盈限价单挂单失败 @ ${cs.take_price:.6f}")

        # 挂止损限价单
        sl_order = self.api.create_limit_close(coin, close_side, cs.position_size, cs.stop_price)
        if sl_order:
            cs.stop_order_id = sl_order['id']
            logger.info(f"[{coin}] 止损限价单已挂: ${cs.stop_price:.6f} ID={sl_order['id']}")
        else:
            logger.warning(f"[{coin}] ⚠️ 止损限价单挂单失败 @ ${cs.stop_price:.6f}")

        holding_count = sum(1 for s in self.state.values() if s.holding)
        logger.info(f"[{coin}] ✅ 入场成交 {pos_side} "
                    f"@${fill_price:.6f} "
                    f"名义${FIXED_POSITION_VALUE:.0f}/{FIXED_POSITION_VALUE:.0f} "
                    f"止盈${cs.take_price:.6f} 止损${cs.stop_price:.6f} "
                    f"当前持仓{holding_count}/{MAX_POSITIONS}")

    def clear_position(self, coin: str, reason: str = ''):
        """清空持仓状态"""
        cs = self.state[coin]
        was_holding = cs.holding
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
        cs.entry_price = 0.0
        cs.entry_bar_count = 0
        if was_holding:
            cs.stopped_out_this_bar = True  # 本根K线不再入场
            self.active_coins.discard(coin)
            logger.info(f"[{coin}] 🗑️ 清空持仓 [{reason}]")

    def sync_positions(self, exchange_positions: list[dict]):
        """从交易所持仓同步引擎状态"""
        exchange_coins = {p['coin'] for p in exchange_positions}
        cleared = 0
        for coin, cs in self.state.items():
            if cs.holding and coin not in exchange_coins:
                logger.info(f"[{coin}] 🔄 同步检测：交易所已无持仓，清空引擎状态")
                self.clear_position(coin, reason='sync_cleared')
                cleared += 1
        if cleared > 0:
            logger.info(f"持仓同步: 清理了{cleared}个已平仓的引擎状态")

    # ═══════════════ 状态输出 ═══════════════

    def get_summary(self) -> str:
        holding = sum(1 for s in self.state.values() if s.holding)
        pending = sum(1 for s in self.state.values() if s.pending_entry)
        timing = sum(1 for s in self.state.values() if s.pending_exit)
        lines = [
            f"持仓 {holding}/{MAX_POSITIONS}  "
            f"挂单入场 {pending}  倒计时 {timing}"
        ]
        if self._tick_count > 0:
            lines[-1] += f"  ticks {self._tick_count}"
        for coin in sorted(self.active_coins):
            cs = self.state[coin]
            lines.append(f"  {coin:<12} {cs.status}")
        return '\n'.join(lines)

    def get_ticks(self) -> int:
        return self._tick_count
