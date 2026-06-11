# Stock Monitor 股价监控

每日自动抓取美股 / 港股 / A 股指数价格，生成趋势图 + 终端摘要。

## 快速开始

```bash
cd ~/stock-monitor
source .venv/bin/activate
python monitor.py
```

## 设置每日自动运行

```bash
cp com.user.stock-monitor.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.stock-monitor.plist
```

默认每天 22:00（北京时间）运行，美股收盘后约 30 分钟。

## 输出

- 终端打印价格摘要表（带涨跌颜色）
- `charts/` 目录保存每只股票的 90 天趋势图（价格线 + MA5 + MA20）

## 自定义

编辑 `monitor.py` 顶部的 `WATCHLIST` 字典增删股票。
