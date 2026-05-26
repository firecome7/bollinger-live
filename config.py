"""策略参数"""
from __future__ import annotations

import os, json

# ====== 交易参数 ======
INITIAL_CAPITAL = 100.0            # USDT
FIXED_POSITION_VALUE = 100.0       # 每笔名义价值 USDT
LEVERAGE = 50                      # 杠杆倍数
MAX_POSITIONS = 20                 # 最大同时持仓币数

# ====== 策略参数 ======
STOP_LOSS_PCT = 0.01               # 止损 1%
TAKE_PROFIT_PCT = 0.05             # 止盈 5%
BOLL_PERIOD = 25                   # 布林带周期
BOLL_STD = 2                       # 布林带标准差倍数
ENTRY_OFFSET = 0.04                # 挂单在布林带外4%
ORDER_LIFETIME_BARS = 10           # 挂单最多等10根K线
TIMEFRAME = '15m'                  # 时间周期
TIMEOUT_SECONDS = 15               # 触价后等15秒，不成交转市价

# ====== 费用 ======
MAKER_FEE = 0.0002                 # 挂单费率 0.02%
TAKER_FEE = 0.0005                 # 市价费率 0.05%

# ====== 币种列表（排除前10）=====
# 回测用的88个币。实盘启动时从交易所同步实际可用币种
DEFAULT_SYMBOLS = None  # None = 启动时自动检测可用USDT永续合约

# ====== OKX API配置 ======
def load_api_keys() -> dict:
    """从环境变量或配置文件加载 Gate.io API key"""
    api_key = os.environ.get('GATE_API_KEY')
    api_secret = os.environ.get('GATE_API_SECRET')
    if api_key and api_secret:
        return {'apiKey': api_key, 'secret': api_secret}
    # 尝试从文件读取
    key_file = os.path.join(os.path.dirname(__file__), 'gate_keys.json')
    if os.path.exists(key_file):
        with open(key_file) as f:
            return json.load(f)
    raise RuntimeError(
        "请设置环境变量 GATE_API_KEY, GATE_API_SECRET\n"
        "或创建 gate_keys.json: {\"apiKey\":\"...\",\"secret\":\"...\"}"
    )


# ====== 验证模式 ======
DRY_RUN = False  # True=只扫信号不下单，False=实盘
