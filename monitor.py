#!/usr/bin/env python3
"""
Stock Price Monitor — Daily price curves + moving averages
Outputs: terminal summary + interactive HTML dashboard with tabs
Run: python monitor.py
"""

import json
import yfinance as yf
import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

# ── CONFIG ──────────────────────────────────────────────

TABS = {
    "美股": {
        "美股持仓": [
            ("SPY",  "标普500 ETF"),
            ("QQQ",  "纳指100 ETF"),
            ("TSLA", "特斯拉"),
            ("NVDA", "英伟达"),
            ("RKLB", "Rocket Lab"),
            ("ASTS", "AST SpaceMobile"),
        ],
        "美股关注": [
            ("MU",   "美光科技"),
            ("SNDK", "闪迪"),
            ("IVV",  "iShares 标普500"),
            ("VOO",  "Vanguard 标普500"),
            ("QQQM", "Invesco 纳指100"),
            ("ONEQ", "Fidelity 纳综"),
            ("SHV",  "短期美债 ETF"),
        ],
    },
    "港股": {
        "港股指数": [
            ("^HSI",      "恒生指数"),
            ("^HSTECH",   "恒生科技"),
            ("000001.SS", "上证指数"),
        ],
    },
    "东方财富": {
        "A股持仓": [
            ("002283.SZ", "天润工业"),
            ("159157.SZ", "有色TH ETF"),
            ("159509.SZ", "纳指科技 ETF"),
            ("159608.SZ", "稀有金属 ETF"),
            ("159611.SZ", "电力ETF"),
            ("513110.SS", "纳指100 ETF"),
        ],
    },
    "基金": {
        "QDII / 海外": [
            ("270023", "广发全球精选(QDII)A"),
            ("012922", "易方达全球成长精选(QDII)C"),
            ("022184", "富国全球科技互联网(QDII)C"),
            ("000043", "嘉实美国成长股票"),
            ("000988", "嘉实全球互联网股票"),
            ("006479", "广发纳斯达克100ETF联接(QDII)C"),
            ("012804", "广发恒生科技ETF联接(QDII)A"),
            ("007721", "天弘标普500(QDII-FOF)A"),
            ("050025", "博时标普500ETF联接A"),
            ("162719", "广发道琼斯石油指数A"),
            ("007844", "华宝标普油气上游股票C"),
        ],
        "A股基金": [
            ("006603", "嘉实互融精选股票A"),
            ("001938", "中欧时代先锋股票A"),
            ("016186", "广发电力ETF联接C"),
            ("007760", "景顺长城沪港深红利低波C"),
        ],
    },
}

FUND_CODES = set()
for groups in TABS.get("基金", {}).values():
    for code, _ in groups:
        FUND_CODES.add(code)

# ── PORTFOLIO (持仓数据) ─────────────────────────────────
# A 股: shares=持仓数, cost=成本价 → 盈亏从实时价格计算
# 基金: invested=本金(元) → 盈亏从最新净值近似计算
# 美股: 补充后填入 shares + cost

PORTFOLIO = {
    # ── 东方财富 A 股 ──
    "002283.SZ": {"shares": 400,  "cost": 10.920},
    "159157.SZ": {"shares": 1000, "cost": 0.894},
    "159509.SZ": {"shares": 100,  "cost": 2.588},
    "159608.SZ": {"shares": 5100, "cost": 1.181},
    "159611.SZ": {"shares": 1000, "cost": 1.168},
    "513110.SS": {"shares": 200,  "cost": 2.437},
    # ── 基金 (本金 = 资产 - 盈亏) ──
    # ── 基金 (天天基金截图 2026-06-10) ──
    "270023": {"asset": 13414.76, "invested": 10900.00, "pnl": 2514.91,  "pnl_pct": 23.73},
    "012922": {"asset": 8715.49,  "invested": 5820.00,  "pnl": 2895.49, "pnl_pct": 50.27},
    "022184": {"asset": 3981.06,  "invested": 3150.00,  "pnl": 831.07,  "pnl_pct": 27.70},
    "000043": {"asset": 8300.25,  "invested": 7750.00,  "pnl": 550.35,  "pnl_pct": 7.24},
    "000988": {"asset": 5980.74,  "invested": 6440.00,  "pnl": -459.26, "pnl_pct": -7.13},
    "006479": {"asset": 1181.70,  "invested": 1090.00,  "pnl": 91.73,   "pnl_pct": 8.65},
    "012804": {"asset": 1898.44,  "invested": 2250.00,  "pnl": -351.56, "pnl_pct": -15.62},
    "007721": {"asset": 2464.99,  "invested": 2400.00,  "pnl": 64.99,   "pnl_pct": 2.71},
    "050025": {"asset": 461.10,   "invested": 450.00,   "pnl": 11.10,   "pnl_pct": 2.47},
    "162719": {"asset": 341.68,   "invested": 300.00,   "pnl": 41.68,   "pnl_pct": 13.89},
    "007844": {"asset": 64.86,    "invested": 60.00,    "pnl": 4.86,    "pnl_pct": 8.10},
    "006603": {"asset": 4425.85,  "invested": 5550.00,  "pnl": -1124.15,"pnl_pct": -20.25},
    "001938": {"asset": 2470.08,  "invested": 2400.00,  "pnl": 70.08,   "pnl_pct": 2.92},
    "016186": {"asset": 1991.51,  "invested": 2000.00,  "pnl": -8.49,   "pnl_pct": -0.45},
    "007760": {"asset": 5598.43,  "invested": 5850.00,  "pnl": -251.21, "pnl_pct": -4.29},
    # ── 美股 (盈立证券) ──
    "SPY":  {"shares": 1, "cost": 740.37},
    "QQQ":  {"shares": 1, "cost": 717.87},
    "TSLA": {"shares": 1, "cost": 388.81},
    "NVDA": {"shares": 1, "cost": 209.33},
    "RKLB": {"shares": 1, "cost": 103.56},
    "ASTS": {"shares": 1, "cost": 86.46},
}

LOOKBACK_DAYS = 90
MA_SHORT = 5
MA_LONG = 20
OUTPUT_DIR = Path(__file__).parent

# ── FETCH ───────────────────────────────────────────────

def fetch(symbol: str, days: int = LOOKBACK_DAYS) -> pd.DataFrame:
    end = datetime.now()
    start = end - timedelta(days=days + 10)
    try:
        df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df.tail(days) if len(df) > days else df
    except Exception as e:
        print(f"  ⚠ {symbol}: {e}")
        return pd.DataFrame()

def fetch_fund(code: str, days: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """Fetch Chinese mutual fund NAV data via akshare."""
    try:
        df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        if df is None or df.empty:
            return pd.DataFrame()
        df.columns = ['Date', 'Close', 'pct']
        df['Date'] = pd.to_datetime(df['Date'])
        df = df.set_index('Date').sort_index()
        df['Open'] = df['Close']
        df['High'] = df['Close']
        df['Low'] = df['Close']
        df['Volume'] = 0
        return df.tail(days)
    except Exception as e:
        print(f"  ⚠ 基金{code}: {e}")
        return pd.DataFrame()

# ── TERMINAL SUMMARY ────────────────────────────────────

def print_summary(group: str, results: list):
    print(f"\n{'─' * 50}")
    print(f"  {group}")
    print(f"{'─' * 50}")
    print(f"  {'标的':<22} {'现价':>10} {'日涨跌':>10} {'5日趋势':>8}")
    print(f"  {'─' * 48}")
    for sym, label, df in results:
        if df.empty:
            print(f"  {label:<20} {'数据获取失败':>30}")
            continue
        close = df['Close'].dropna().values
        if len(close) == 0:
            print(f"  {label:<20} {'无有效数据':>30}")
            continue
        last = close[-1]
        prev = close[-2] if len(close) > 1 else last
        change_pct = (last - prev) / prev * 100 if prev else 0
        week_ago = close[-5] if len(close) >= 5 else close[0]
        week_pct = (last - week_ago) / week_ago * 100 if week_ago else 0
        sign_d = '+' if change_pct >= 0 else ''
        sign_w = '+' if week_pct >= 0 else ''
        c_d = '\033[32m' if change_pct >= 0 else '\033[31m'
        c_w = '\033[32m' if week_pct >= 0 else '\033[31m'
        r = '\033[0m'
        print(f"  {label:<20} {last:>10.2f} "
              f"{c_d}{sign_d}{change_pct:>7.2f}%{r} "
              f"{c_w}{sign_w}{week_pct:>6.1f}%{r}")

# ── HTML DASHBOARD ──────────────────────────────────────

TAB_CURRENCY = {
    "美股": "USD",
    "港股": "HKD",
    "东方财富": "CNY",
    "基金": "CNY",
}
CURRENCY_LABEL = {
    "USD": "美元",
    "HKD": "港币",
    "CNY": "人民币",
}

def safe_float(v, default=0.0):
    f = float(v)
    return default if (np.isnan(f) or np.isinf(f)) else round(f, 3)

def build_tabs_json(all_tab_data: dict) -> str:
    output = {}
    for tab_name, groups in all_tab_data.items():
        tab_groups = {}
        for group_name, results in groups.items():
            stocks = []
            for sym, label, df in results:
                if df.empty:
                    stocks.append({"symbol": sym, "label": label, "error": True})
                    continue

                df_clean = df.dropna(subset=['Close'])
                if df_clean.empty:
                    stocks.append({"symbol": sym, "label": label, "error": True})
                    continue

                close = df_clean['Close'].values
                ohlc = []
                for i, row in df_clean.iterrows():
                    ohlc.append({
                        "date": i.strftime('%Y-%m-%d'),
                        "open": safe_float(row['Open']),
                        "high": safe_float(row['High']),
                        "low": safe_float(row['Low']),
                        "close": safe_float(row['Close']),
                        "volume": int(row['Volume']) if 'Volume' in row and pd.notna(row['Volume']) else 0,
                    })

                last = float(close[-1])
                prev = float(close[-2]) if len(close) > 1 else last
                change = last - prev
                change_pct = (change / prev) * 100 if prev else 0
                week_ago = float(close[-5]) if len(close) >= 5 else float(close[0])
                week_pct = (last - week_ago) / week_ago * 100 if week_ago else 0

                entry = {
                    "symbol": sym,
                    "label": label,
                    "price": safe_float(last),
                    "change": safe_float(change),
                    "changePct": safe_float(change_pct),
                    "weekPct": safe_float(week_pct),
                    "high90": safe_float(df_clean['High'].max()),
                    "low90": safe_float(df_clean['Low'].min()),
                    "ohlc": ohlc,
                }

                pf = PORTFOLIO.get(sym)
                if pf:
                    if "shares" in pf and "cost" in pf:
                        shares = pf["shares"]
                        cost = pf["cost"]
                        mkt_val = last * shares
                        pnl = (last - cost) * shares
                        pnl_pct = (last - cost) / cost * 100 if cost else 0
                        entry["portfolio"] = {
                            "type": "stock",
                            "shares": shares,
                            "cost": round(cost, 3),
                            "mktVal": safe_float(mkt_val),
                            "pnl": safe_float(pnl),
                            "pnlPct": safe_float(pnl_pct),
                        }
                    elif "asset" in pf:
                        entry["portfolio"] = {
                            "type": "fund",
                            "invested": pf["invested"],
                            "asset": pf["asset"],
                            "pnl": pf["pnl"],
                            "pnlPct": pf["pnl_pct"],
                        }

                stocks.append(entry)
            tab_groups[group_name] = stocks
        cur = TAB_CURRENCY.get(tab_name, "CNY")
        output[tab_name] = {
            "currency": CURRENCY_LABEL.get(cur, cur),
            "groups": tab_groups,
        }
    return json.dumps(output, ensure_ascii=False)

def generate_html(all_tab_data: dict, path: Path):
    data_json = build_tabs_json(all_tab_data)
    today = datetime.now().strftime('%Y-%m-%d %H:%M')
    html = HTML_TEMPLATE.replace('__DATA__', data_json).replace('__DATE__', today)
    path.write_text(html, encoding='utf-8')
    print(f"\n🌐 Dashboard 已生成: {path.absolute()}")

HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stock Monitor</title>
<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
  :root {
    --bg: #0a0a0f;
    --card: #12121a;
    --border: #1e1e2a;
    --text: #c8cdd8;
    --text-dim: #606878;
    --accent: #5b8def;
    --up: #ef4444;
    --down: #22c55e;
    --ma5: #ffb040;
    --ma20: #ff5070;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', sans-serif;
    min-height: 100vh;
  }

  /* ── Top bar ── */
  .top-bar {
    display: flex;
    align-items: center;
    gap: 16px;
    padding: 16px 24px;
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    background: var(--bg);
    z-index: 100;
  }
  .top-bar h1 {
    font-size: 20px;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: #e8ecf4;
    white-space: nowrap;
  }
  .top-bar .date {
    font-size: 12px;
    color: var(--text-dim);
    white-space: nowrap;
  }
  .controls {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-left: auto;
  }

  /* ── Tab bar ── */
  .tab-bar {
    display: flex;
    gap: 2px;
    background: #16161f;
    border-radius: 10px;
    padding: 3px;
  }
  .tab-bar button {
    background: none;
    border: none;
    color: var(--text-dim);
    font-size: 13px;
    padding: 6px 18px;
    border-radius: 8px;
    cursor: pointer;
    transition: all 0.15s;
    font-weight: 500;
    white-space: nowrap;
  }
  .tab-bar button.active {
    background: var(--accent);
    color: #fff;
  }
  .tab-bar button:hover:not(.active) {
    color: var(--text);
    background: rgba(255,255,255,0.04);
  }

  /* ── Mode toggle ── */
  .mode-toggle {
    display: flex;
    gap: 2px;
    background: #16161f;
    border-radius: 8px;
    padding: 3px;
  }
  .mode-toggle button {
    background: none;
    border: none;
    color: var(--text-dim);
    font-size: 12px;
    padding: 4px 12px;
    border-radius: 6px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .mode-toggle button.active {
    background: #2a2a3a;
    color: #e0e4f0;
  }

  .legend {
    display: flex;
    gap: 14px;
    font-size: 11px;
    color: var(--text-dim);
    margin-left: 8px;
  }
  .legend span { display: flex; align-items: center; gap: 5px; white-space: nowrap; }
  .legend .dot {
    width: 14px; height: 2px; border-radius: 1px; display: inline-block;
  }

  /* ── Content ── */
  .content { padding: 8px 24px 32px; }

  .group-header {
    display: flex;
    align-items: baseline;
    gap: 16px;
    margin: 24px 0 12px;
  }
  .group-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-dim);
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }
  .group-summary {
    margin-left: auto;
    display: flex;
    gap: 16px;
    font-size: 12px;
    color: var(--text-dim);
    font-feature-settings: 'tnum';
  }
  .group-summary .gs-item { white-space: nowrap; }
  .group-summary .gs-val { color: var(--text); font-weight: 600; }
  .group-summary .gs-pnl { font-weight: 600; }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(370px, 1fr));
    gap: 14px;
  }
  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 14px 16px;
    transition: border-color 0.2s;
  }
  .card:hover { border-color: #2a2a40; }
  .card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }
  .card-head .name {
    font-size: 14px;
    font-weight: 600;
    color: #e0e4f0;
  }
  .card-head .symbol {
    font-size: 11px;
    color: var(--text-dim);
    margin-left: 6px;
    font-weight: 400;
  }
  .card-head .price-block { text-align: right; }
  .card-head .price {
    font-size: 20px;
    font-weight: 700;
    font-feature-settings: 'tnum';
    letter-spacing: -0.02em;
  }
  .card-head .change {
    font-size: 11px;
    font-weight: 500;
    font-feature-settings: 'tnum';
  }
  .stats {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 8px;
    font-size: 11px;
    color: var(--text-dim);
  }
  .stats .stat {
    background: #16161f;
    padding: 3px 8px;
    border-radius: 5px;
  }
  .stats .val { color: var(--text); font-weight: 500; }
  .stats .spacer { flex: 1; }

  .portfolio {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 8px;
    padding: 8px 10px;
    background: linear-gradient(135deg, rgba(91,141,239,0.06), rgba(91,141,239,0.02));
    border: 1px solid rgba(91,141,239,0.1);
    border-radius: 8px;
    font-size: 11px;
    color: var(--text-dim);
  }
  .portfolio .pf-label {
    color: var(--accent);
    font-weight: 600;
    font-size: 10px;
    letter-spacing: 0.04em;
    white-space: nowrap;
  }
  .portfolio .pf-item { white-space: nowrap; }
  .portfolio .pf-val { color: var(--text); font-weight: 500; }
  .portfolio .pf-pnl {
    margin-left: auto;
    font-weight: 600;
    font-size: 12px;
    font-feature-settings: 'tnum';
  }
  .portfolio .pf-delay {
    font-size: 9px;
    opacity: 0.45;
    font-weight: 400;
    margin-left: 4px;
  }
  .chart-box {
    width: 100%;
    height: 200px;
    margin-top: 6px;
    border-radius: 8px;
    overflow: hidden;
    position: relative;
  }
  .reset-btn {
    width: 24px;
    height: 24px;
    border-radius: 5px;
    border: 1px solid var(--border);
    background: #16161f;
    color: var(--text-dim);
    font-size: 14px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: color 0.15s, border-color 0.15s;
    line-height: 1;
    padding: 0;
    flex-shrink: 0;
  }
  .reset-btn:hover {
    color: #e0e4f0;
    border-color: var(--accent);
  }
  .up { color: var(--up); }
  .down { color: var(--down); }
  .error-card {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100px;
    color: var(--text-dim);
    font-size: 13px;
  }
  .empty-tab {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    min-height: 300px;
    color: var(--text-dim);
    font-size: 14px;
    gap: 8px;
  }
  .empty-tab .hint { font-size: 12px; opacity: 0.6; }

  @media (max-width: 820px) {
    .grid { grid-template-columns: 1fr; }
    .content { padding: 8px 14px 24px; }
    .top-bar { padding: 12px 14px; flex-wrap: wrap; }
    .controls { width: 100%; justify-content: space-between; margin-left: 0; margin-top: 8px; }
  }
</style>
</head>
<body>

<div class="top-bar">
  <h1>Stock Monitor</h1>
  <span class="date">__DATE__</span>
  <div class="controls">
    <div class="tab-bar" id="tabBar"></div>
    <div class="mode-toggle">
      <button class="active" onclick="setMode('line')">曲线</button>
      <button onclick="setMode('candle')">K线</button>
    </div>
    <div class="legend">
      <span><i class="dot" style="background:var(--accent)"></i>实际价格</span>
      <span><i class="dot" style="background:var(--ma5)"></i>5日均线</span>
      <span><i class="dot" style="background:var(--ma20)"></i>20日均线</span>
    </div>
  </div>
</div>

<div class="content" id="app"></div>

<script>
const TABS_DATA = __DATA__;
const tabNames = Object.keys(TABS_DATA);
let activeTab = tabNames[0];
let currentMode = 'line';
let chartInstances = [];
const chartMap = {};

function initTabs() {
  const bar = document.getElementById('tabBar');
  bar.innerHTML = '';
  tabNames.forEach(name => {
    const btn = document.createElement('button');
    btn.textContent = name;
    if (name === activeTab) btn.classList.add('active');
    btn.onclick = () => switchTab(name);
    bar.appendChild(btn);
  });
}

function switchTab(name) {
  activeTab = name;
  document.querySelectorAll('.tab-bar button').forEach(b => {
    b.classList.toggle('active', b.textContent === name);
  });
  destroyCharts();
  renderTab();
}

function setMode(mode) {
  currentMode = mode;
  document.querySelectorAll('.mode-toggle button').forEach(b => {
    b.classList.toggle('active', b.textContent === (mode === 'line' ? '曲线' : 'K线'));
  });
  destroyCharts();
  renderTab();
}

function destroyCharts() {
  chartInstances.forEach(c => c.remove());
  chartInstances = [];
  Object.keys(chartMap).forEach(k => delete chartMap[k]);
}

function calcMA(data, period) {
  const result = [];
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) continue;
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += data[j].close;
    result.push({ time: data[i].date, value: parseFloat((sum / period).toFixed(3)) });
  }
  return result;
}

function createChart(container, stock) {
  const chart = LightweightCharts.createChart(container, {
    width: container.clientWidth,
    height: 200,
    layout: {
      background: { type: 'solid', color: '#12121a' },
      textColor: '#606878',
      fontSize: 11,
    },
    grid: {
      vertLines: { color: '#1a1a24' },
      horzLines: { color: '#1a1a24' },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: '#3a3a50', width: 1, style: 2 },
      horzLine: { color: '#3a3a50', width: 1, style: 2 },
    },
    rightPriceScale: { borderColor: '#1e1e2a' },
    timeScale: { borderColor: '#1e1e2a', timeVisible: false },
    handleScroll: true,
    handleScale: true,
  });

  const ohlc = stock.ohlc.map(d => ({
    time: d.date, open: d.open, high: d.high, low: d.low, close: d.close,
  }));

  if (currentMode === 'candle') {
    const s = chart.addCandlestickSeries({
      upColor: '#ef4444', downColor: '#22c55e',
      borderUpColor: '#ef4444', borderDownColor: '#22c55e',
      wickUpColor: '#ef4444', wickDownColor: '#22c55e',
    });
    s.setData(ohlc);
  } else {
    const s = chart.addAreaSeries({
      lineColor: '#5b8def',
      topColor: 'rgba(91,141,239,0.15)',
      bottomColor: 'rgba(91,141,239,0.01)',
      lineWidth: 2,
    });
    s.setData(ohlc.map(d => ({ time: d.time, value: d.close })));
  }

  chart.addLineSeries({ color: '#ffb040', lineWidth: 1, lineStyle: 2 })
       .setData(calcMA(stock.ohlc, 5));
  chart.addLineSeries({ color: '#ff5070', lineWidth: 1, lineStyle: 2 })
       .setData(calcMA(stock.ohlc, 20));

  chart.timeScale().fitContent();

  new ResizeObserver(() => chart.applyOptions({ width: container.clientWidth }))
      .observe(container);

  chartInstances.push(chart);
  const key = container.id.replace('chart-', '');
  chartMap[key] = chart;
}

function resetChart(key) {
  const chart = chartMap[key];
  if (chart) chart.timeScale().fitContent();
}

function renderTab() {
  const app = document.getElementById('app');
  app.innerHTML = '';
  const tabData = TABS_DATA[activeTab];
  if (!tabData) return;
  const currency = tabData.currency || '';
  const groups = tabData.groups || {};

  let hasContent = false;
  for (const [groupName, stocks] of Object.entries(groups)) {
    if (!stocks || stocks.length === 0) continue;
    hasContent = true;

    const csym = {'美元':'$','港币':'HK$','人民币':'¥'}[currency] || '¥';
    let totalVal = 0, totalPnl = 0, hasPf = false;
    for (const st of stocks) {
      if (st.error || !st.portfolio) continue;
      hasPf = true;
      const p = st.portfolio;
      if (p.type === 'stock') {
        totalVal += p.mktVal;
        totalPnl += p.pnl;
      } else {
        totalVal += p.estVal;
        totalPnl += p.pnl;
      }
    }

    const header = document.createElement('div');
    header.className = 'group-header';
    let sumHtml = '';
    if (hasPf) {
      const sCls = totalPnl >= 0 ? 'up' : 'down';
      const sSign = totalPnl >= 0 ? '+' : '';
      sumHtml = `
        <div class="group-summary">
          <span class="gs-item">总市值 <span class="gs-val">${csym}${totalVal.toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g,',')}</span></span>
          <span class="gs-item">总盈亏 <span class="gs-pnl ${sCls}">${sSign}${csym}${Math.abs(totalPnl).toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g,',')}${totalVal > 0 ? ' ('+ sSign + (totalPnl/((totalVal-totalPnl)||1)*100).toFixed(2) +'%)' : ''}</span> <span style="font-size:10px;opacity:0.4">延迟数据</span></span>
        </div>`;
    }
    header.innerHTML = `<div class="group-title">${groupName}</div>${sumHtml}`;
    app.appendChild(header);

    const grid = document.createElement('div');
    grid.className = 'grid';
    app.appendChild(grid);

    for (const stock of stocks) {
      const card = document.createElement('div');
      card.className = 'card';

      if (stock.error) {
        card.innerHTML = `<div class="error-card">${stock.label}（${stock.symbol}）数据获取失败</div>`;
        grid.appendChild(card);
        continue;
      }

      const up = stock.changePct >= 0;
      const cls = up ? 'up' : 'down';
      const sign = up ? '+' : '';
      const wUp = stock.weekPct >= 0;
      const wCls = wUp ? 'up' : 'down';
      const wSign = wUp ? '+' : '';

      const safeSym = stock.symbol.replace(/[^a-zA-Z0-9]/g, '_');
      let pfHtml = '';
      const pf = stock.portfolio;
      if (pf) {
        const csym = {'美元':'$','港币':'HK$','人民币':'¥'}[currency] || '¥';
        const pCls = pf.pnl >= 0 ? 'up' : 'down';
        const pSign = pf.pnl >= 0 ? '+' : '';
        if (pf.type === 'stock') {
          pfHtml = `
            <div class="portfolio">
              <span class="pf-label">我的持仓</span>
              <span class="pf-item">持仓 <span class="pf-val">${pf.shares}股</span></span>
              <span class="pf-item">成本 <span class="pf-val">${csym}${pf.cost.toFixed(2)}</span></span>
              <span class="pf-item">市值 <span class="pf-val">${csym}${pf.mktVal.toFixed(0)}</span></span>
              <span class="pf-pnl ${pCls}">${pSign}${csym}${Math.abs(pf.pnl).toFixed(2)} (${pSign}${pf.pnlPct.toFixed(2)}%)<span class="pf-delay">延迟</span></span>
            </div>`;
        } else {
          pfHtml = `
            <div class="portfolio">
              <span class="pf-label">我的持仓</span>
              <span class="pf-item">本金 <span class="pf-val">${csym}${pf.invested.toFixed(0)}</span></span>
              <span class="pf-item">估值 <span class="pf-val">${csym}${pf.estVal.toFixed(0)}</span></span>
              <span class="pf-pnl ${pCls}">${pSign}${csym}${Math.abs(pf.pnl).toFixed(0)} (${pSign}${pf.pnlPct.toFixed(2)}%)<span class="pf-delay">延迟</span></span>
            </div>`;
        }
      }

      card.innerHTML = `
        <div class="card-head">
          <div><span class="name">${stock.label}<span class="symbol">${stock.symbol}</span></span></div>
          <div class="price-block">
            <div class="price ${cls}">${stock.price.toFixed(2)} <span style="font-size:11px;font-weight:400;opacity:0.5">${currency}</span></div>
            <div class="change ${cls}">${sign}${stock.change.toFixed(2)} (${sign}${stock.changePct.toFixed(2)}%)</div>
          </div>
        </div>
        <div class="chart-box" id="chart-${safeSym}"></div>
        <div class="stats">
          <div class="stat">5日 <span class="val ${wCls}">${wSign}${stock.weekPct.toFixed(1)}%</span></div>
          <div class="stat">90日高 <span class="val">${stock.high90.toFixed(2)}</span></div>
          <div class="stat">90日低 <span class="val">${stock.low90.toFixed(2)}</span></div>
          <div class="spacer"></div>
          <button class="reset-btn" onclick="resetChart('${safeSym}')" title="重置缩放">⟲</button>
        </div>
        ${pfHtml}
      `;
      grid.appendChild(card);

      requestAnimationFrame(() => {
        const el = document.getElementById('chart-' + safeSym);
        if (el) createChart(el, stock);
      });
    }
  }

  if (!hasContent) {
    app.innerHTML = `
      <div class="empty-tab">
        <div>暂无数据</div>
        <div class="hint">在 monitor.py 中添加标的后重新运行</div>
      </div>`;
  }
}

initTabs();
renderTab();
</script>
</body>
</html>
'''

# ── MAIN ────────────────────────────────────────────────

def main():
    today = datetime.now().strftime('%Y-%m-%d')
    print(f"\n📊 股价监控 — {today}")
    print(f"   回看 {LOOKBACK_DAYS} 天 · MA{MA_SHORT} / MA{MA_LONG}")

    all_tab_data = {}
    for tab_name, groups in TABS.items():
        tab_results = {}
        for group_name, stocks in groups.items():
            if not stocks:
                tab_results[group_name] = []
                continue
            results = []
            for symbol, label in stocks:
                print(f"  [{tab_name}] 获取 {label}({symbol})...", end='', flush=True)
                df = fetch_fund(symbol) if symbol in FUND_CODES else fetch(symbol)
                if not df.empty:
                    print(f" ✓ ({len(df)} 天)")
                else:
                    print(f" ✗")
                results.append((symbol, label, df))
            print_summary(group_name, results)
            tab_results[group_name] = results
        all_tab_data[tab_name] = tab_results

    html_path = OUTPUT_DIR / "dashboard.html"
    generate_html(all_tab_data, html_path)

if __name__ == '__main__':
    main()
