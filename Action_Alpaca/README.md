# Action_Alpaca

将 TradingAgents 的决策结果（尤其是推荐路径和三套风险方案）转成 Alpaca 订单的轻量脚本。默认不会直接下单，只有传入 `--submit` 时才会提交。

## 准备
1. 安装依赖（不需要在 TradingAgents 环境内运行）：
   ```bash
   pip install -r requirements.txt
   ```
2. 配置环境变量（建议使用 Paper 端点）：
   ```bash
   export ALPACA_API_KEY_ID=YOUR_KEY_ID
   export ALPACA_API_SECRET=YOUR_SECRET
   export ALPACA_API_BASE_URL=https://paper-api.alpaca.markets  # 或生产 https://api.alpaca.markets
   ```

## 使用
假设 TradingAgents 输出在 `../TradingAgents/results/<ticker>/<date>/reports/`：
```bash
python send_order.py \
  --ticker SPY \
  --date 2025-12-15 \
  --notional 1000 \
  --results-root ../TradingAgents/results \
  --submit          # 不加则为 dry-run
```

行为说明：
- 读取 `recommended_path.md`（或风险计划 JSON）解析决策：Buy/Sell/Hold。
- Hold 则不下单；Buy/Sell 会生成市价单，数量默认从 `--qty`（优先）或 `--notional` 推算（若无法获取价格则退化为 1 股）。
- 只在传入 `--submit` 时调用 Alpaca 下单，否则打印将要下单的 payload。

## 注意
- **不要**硬编码真实密钥；全部从环境变量读取。
- `ALPACA_API_BASE_URL` 默认为 Paper 端，生产请显式修改。
- 风险控制、仓位 sizing 仅示例化，真实生产请按策略扩展。
