# Stock_Agent_v3

三个模块串联：先用 TradingAgents 生成研究/交易决策，再用 Visual 可视化查看结果，最后用 Action_Alpaca 把决策转成下单指令。

## 三步启动

1. TradingAgents：生成结果

```bash
cd TradingAgents
# 按照 TradingAgents/README.md 配好依赖和 API Key
python -m cli.main
```

运行完成后会在 `TradingAgents/results/<ticker>/<date>/reports/` 生成报告文件。

2. Visual：浏览结果

```bash
cd Visual
python server.py
```

打开浏览器访问 `http://127.0.0.1:8008` 查看结果。

3. Action_Alpaca：转成下单（默认 dry-run）

```bash
cd Action_Alpaca
python send_order.py \
  --ticker SPY \
  --date 2025-12-15 \
  --notional 1000 \
  --results-root ../TradingAgents/results
```

需要真实下单时加 `--submit`，并提前配置 Alpaca 的环境变量。
