# Bollinger 策略实盘程序

Gate.io 永续合约布林带突破策略

## 策略逻辑

- 15分钟K线，布林带25周期
- 价格跌破下轨 + 阴线 → 限价做多（挂单在带外4%）
- 价格突破上轨 + 阳线 → 限价做空（挂单在带外4%）
- 固定1%止损，4%止盈（限价出场，15秒不成交转市价）
- 每笔$100名义价值，50倍杠杆，最多20个币同时持仓

## 使用

```bash
# 配置API key（gate_keys.json 或环境变量）
export GATE_API_KEY=xxx
export GATE_API_SECRET=xxx

# 启动
python3.12 main.py
```

## 文件

| 文件 | 说明 |
|------|------|
| `config.py` | 参数配置 |
| `gate_api.py` | Gate.io ccxt API封装 |
| `signals.py` | 布林带计算 + 信号检测 |
| `engine.py` | 核心交易引擎（状态机） |
| `main.py` | 主循环入口 |

## 回测参考

回测代码：[bollinger-strategy](https://github.com/firecome7/bollinger-strategy)  
$100 → $897 (+797%)，最大回撤41%，88个币OKX 15m数据
