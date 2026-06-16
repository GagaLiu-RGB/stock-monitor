#!/usr/bin/env python3
"""
Stock Monitor — Live Dashboard Server
Run: python server.py
Open: http://localhost:8888
"""

import json
import time
import threading
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from flask import Flask, jsonify, Response, request
import warnings
warnings.filterwarnings('ignore')

# ── CONFIG ───────────────────────────────────────────────

import os
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')

def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    tabs = {}
    for tab_name, groups in cfg.get("tabs", {}).items():
        tabs[tab_name] = {}
        for grp_name, stocks in groups.items():
            tabs[tab_name][grp_name] = [tuple(s) for s in stocks]
    return tabs, cfg.get("portfolio", {}), cfg.get("tab_currency", {}), cfg.get("descriptions", {})

def save_config(tabs, portfolio, tab_currency, descriptions):
    cfg = {"tabs": {}, "portfolio": portfolio, "tab_currency": tab_currency, "descriptions": descriptions}
    for tab_name, groups in tabs.items():
        cfg["tabs"][tab_name] = {}
        for grp_name, stocks in groups.items():
            cfg["tabs"][tab_name][grp_name] = [list(s) for s in stocks]
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

TABS, PORTFOLIO, TAB_CURRENCY, DESCRIPTIONS = load_config()

LOOKBACK_DAYS = 90
CACHE_TTL = 300

# ── DATA LAYER ──────────────────────────────────────────

cache = {"data": None, "news": [], "updated": None, "updated_ts": 0, "loading": False}

def safe_float(v, default=0.0):
    f = float(v)
    return default if (np.isnan(f) or np.isinf(f)) else round(f, 3)

def fetch_yf(symbol, days=LOOKBACK_DAYS):
    end = datetime.now()
    start = end - timedelta(days=days + 10)
    try:
        df = yf.download(symbol, start=start, end=end, progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df.tail(days) if len(df) > days else df
    except:
        return pd.DataFrame()

def fetch_realtime_price(symbol):
    """Get latest price via fast_info (includes pre/post market when available)."""
    try:
        fi = yf.Ticker(symbol).fast_info
        price = float(fi.last_price)
        prev = float(fi.previous_close)
        if np.isnan(price) or np.isinf(price):
            return None, None
        return price, prev
    except:
        return None, None


def build_json():
    output = {}
    for tab_name, groups in TABS.items():
        tab_groups = {}
        for group_name, stocks in groups.items():
            items = []
            for sym, label in stocks:
                df = fetch_yf(sym)
                if df.empty:
                    items.append({"symbol": sym, "label": label, "error": True})
                    continue
                df_clean = df.dropna(subset=['Close'])
                if df_clean.empty:
                    items.append({"symbol": sym, "label": label, "error": True})
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

                last_ohlc_date = df_clean.index[-1].strftime('%m/%d')

                rt_price, rt_prev = fetch_realtime_price(sym)
                if rt_price is not None:
                    last = rt_price
                    prev = rt_prev if rt_prev is not None else (float(close[-2]) if len(close) > 1 else last)
                else:
                    last = float(close[-1])
                    prev = float(close[-2]) if len(close) > 1 else last

                change = last - prev
                change_pct = (change / prev) * 100 if prev else 0

                entry = {
                    "symbol": sym, "label": label,
                    "desc": DESCRIPTIONS.get(sym, ""),
                    "price": safe_float(last),
                    "change": safe_float(change),
                    "changePct": safe_float(change_pct),
                    "high90": safe_float(df_clean['High'].max()),
                    "low90": safe_float(df_clean['Low'].min()),
                    "ohlc": ohlc,
                    "dataDate": last_ohlc_date,
                    "isRealtime": rt_price is not None,
                }

                pf = PORTFOLIO.get(sym)
                if pf:
                    if "shares" in pf and "cost" in pf:
                        shares, cost = pf["shares"], pf["cost"]
                        mkt_val = last * shares
                        pnl = (last - cost) * shares
                        pnl_pct = (last - cost) / cost * 100 if cost else 0
                        entry["portfolio"] = {
                            "type": "stock", "shares": shares,
                            "cost": round(cost, 3),
                            "mktVal": safe_float(mkt_val),
                            "pnl": safe_float(pnl),
                            "pnlPct": safe_float(pnl_pct),
                            "purchases": pf.get("purchases", []),
                        }

                items.append(entry)
            tab_groups[group_name] = items
        output[tab_name] = {"currency": TAB_CURRENCY.get(tab_name, "人民币"), "groups": tab_groups}
    return output

def batch_translate(texts, target='zh-CN'):
    """Translate a list of texts via Google Translate free endpoint."""
    if not texts:
        return []
    try:
        combined = '\n'.join(texts)
        q = urllib.parse.quote(combined)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl={target}&dt=t&q={q}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh)'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        translated = ''.join(s[0] for s in data[0] if s[0])
        parts = translated.split('\n')
        while len(parts) < len(texts):
            parts.append('')
        return parts[:len(texts)]
    except Exception as e:
        print(f"[翻译] 失败: {e}", flush=True)
        return [''] * len(texts)

RSS_FEEDS = [
    ("https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY,QQQ,TSLA,NVDA,AAPL&region=US&lang=en-US", "Yahoo US"),
    ("https://feeds.finance.yahoo.com/rss/2.0/headline?s=0700.HK,9988.HK&region=HK&lang=en-US", "Yahoo HK"),
    ("https://finance.yahoo.com/news/rss", "Yahoo Finance"),
]

def fetch_news():
    """Fetch news from RSS feeds (no API key, built-in libs only)."""
    all_news = []
    seen = set()
    headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'}
    for url, source in RSS_FEEDS:
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                xml_data = resp.read()
            root = ET.fromstring(xml_data)
            for item in root.iter('item'):
                title = (item.findtext('title') or '').strip()
                if not title or title in seen:
                    continue
                seen.add(title)
                link = item.findtext('link', '')
                pub_str = item.findtext('pubDate', '')
                ts = 0
                time_str = ''
                if pub_str:
                    try:
                        dt = parsedate_to_datetime(pub_str)
                        ts = int(dt.timestamp())
                        time_str = dt.strftime('%m/%d %H:%M')
                    except:
                        pass
                all_news.append({
                    "title": title,
                    "publisher": source,
                    "link": link,
                    "time": ts,
                    "timeStr": time_str,
                    "tickers": [],
                })
        except Exception as e:
            print(f"[RSS] {source} failed: {e}", flush=True)
    all_news.sort(key=lambda x: x['time'], reverse=True)
    all_news = all_news[:30]
    # Translate titles
    en_titles = [n['title'] for n in all_news]
    zh_titles = batch_translate(en_titles)
    for i, n in enumerate(all_news):
        n['titleZh'] = zh_titles[i] if i < len(zh_titles) else ''
    return all_news

def build_analysis(data):
    """Generate market analysis bullets from existing stock data."""
    items = []
    all_stocks = {}
    for tab_name, tab_info in (data or {}).items():
        for grp in (tab_info.get("groups") or {}).values():
            for s in grp:
                if not s.get("error"):
                    s["_tab"] = tab_name
                    all_stocks[s["symbol"]] = s

    idx_map = {"^HSI": "恒指", "000001.SS": "上证", "SPY": "标普500", "QQQ": "纳指100"}
    idx_list = [all_stocks[k] for k in idx_map if k in all_stocks]

    # 1. Overall market
    if idx_list:
        up = sum(1 for s in idx_list if s["changePct"] >= 0)
        down = len(idx_list) - up
        avg_chg = sum(s["changePct"] for s in idx_list) / len(idx_list)
        worst = min(idx_list, key=lambda s: s["changePct"])
        best = max(idx_list, key=lambda s: s["changePct"])
        if down > up and avg_chg < -0.5:
            items.append({"type": "warning", "title": "全球市场承压",
                "text": f"{down}/{len(idx_list)} 大盘指数下跌，均值 {avg_chg:.1f}%。{idx_map.get(worst['symbol'], worst['label'])}跌幅最大（{worst['changePct']:+.2f}%），避险情绪升温，建议控制仓位。"})
        elif up > down and avg_chg > 0.5:
            items.append({"type": "positive", "title": "全球市场偏强",
                "text": f"{up}/{len(idx_list)} 大盘指数上涨，均值 +{avg_chg:.1f}%。{idx_map.get(best['symbol'], best['label'])}涨幅最大（{best['changePct']:+.2f}%）。"})
        else:
            items.append({"type": "neutral", "title": "市场分化震荡",
                "text": f"涨跌参半，均值变动 {avg_chg:+.1f}%，方向不明确，观望为主。"})

    # 2. Per-market summary
    for tab, label in [("美股","美股"), ("港股","港股"), ("东方财富","A股")]:
        tab_info = (data or {}).get(tab, {})
        grps = tab_info.get("groups", {})
        tab_stocks = [s for g in grps.values() for s in g if not s.get("error")]
        if not tab_stocks:
            continue
        tavg = sum(s["changePct"] for s in tab_stocks) / len(tab_stocks)
        tup = sum(1 for s in tab_stocks if s["changePct"] >= 0)
        best_t = max(tab_stocks, key=lambda s: s["changePct"])
        worst_t = min(tab_stocks, key=lambda s: s["changePct"])
        if tup == len(tab_stocks):
            mood = "全线飘红"
        elif tup == 0:
            mood = "全线飘绿"
        else:
            mood = f"{tup}涨{len(tab_stocks)-tup}跌"
        items.append({"type": "info", "title": f"{label}：{mood}",
            "text": f"均值 {tavg:+.1f}% | 最强 {best_t['label']}（{best_t['changePct']:+.1f}%） | 最弱 {worst_t['label']}（{worst_t['changePct']:+.1f}%）"})

    # 3. Portfolio alerts
    for sym, s in all_stocks.items():
        pf = s.get("portfolio")
        if not pf:
            continue
        if pf["pnlPct"] < -10:
            items.append({"type": "warning", "title": f"持仓预警：{s['label']} 浮亏 {pf['pnlPct']:.1f}%",
                "text": f"成本 {pf['cost']:.2f} → 现价 {s['price']:.2f}，浮亏较大，关注是否需要止损。"})
        elif pf["pnlPct"] > 20:
            items.append({"type": "positive", "title": f"持仓盈利：{s['label']} 浮盈 +{pf['pnlPct']:.1f}%",
                "text": f"成本 {pf['cost']:.2f} → 现价 {s['price']:.2f}，可考虑部分止盈锁定利润。"})

    # 4. Technical signals (golden/death cross) — merged into one or two cards
    golden, death = [], []
    for sym, s in all_stocks.items():
        ohlc = s.get("ohlc", [])
        if len(ohlc) < 21:
            continue
        c = [d["close"] for d in ohlc]
        ma5 = sum(c[-5:]) / 5
        ma20 = sum(c[-20:]) / 20
        pma5 = sum(c[-6:-1]) / 5
        pma20 = sum(c[-21:-1]) / 20
        if pma5 <= pma20 and ma5 > ma20:
            golden.append(s['label'])
        elif pma5 >= pma20 and ma5 < ma20:
            death.append(s['label'])
    if golden:
        items.append({"type": "positive", "title": f"金叉信号（{len(golden)}）",
            "text": "、".join(golden) + " — 5日上穿20日均线，短期趋势转强。"})
    if death:
        items.append({"type": "warning", "title": f"死叉信号（{len(death)}）",
            "text": "、".join(death) + " — 5日下穿20日均线，短期趋势走弱。"})

    return items

def build_market_pulse(data):
    """Generate a simple market overview from existing tab data."""
    indices = [
        ("^HSI", "恒指"), ("000001.SS", "上证"), ("SPY", "标普500"), ("QQQ", "纳指100"),
    ]
    pulse = []
    all_stocks = {}
    for tab_info in (data or {}).values():
        for grp in (tab_info.get("groups") or {}).values():
            for s in grp:
                all_stocks[s.get("symbol")] = s
    for sym, name in indices:
        s = all_stocks.get(sym)
        if s and not s.get("error"):
            pulse.append({"symbol": sym, "name": name, "price": s["price"],
                          "changePct": s["changePct"], "change": s["change"]})
    up = sum(1 for p in pulse if p["changePct"] >= 0)
    down = len(pulse) - up
    if down > up:
        mood = "偏弱"
        moodCls = "down"
    elif up > down:
        mood = "偏强"
        moodCls = "up"
    else:
        mood = "震荡"
        moodCls = "neutral"
    return {"indices": pulse, "mood": mood, "moodCls": moodCls}

def ensure_data(force=False):
    """Fetch data if cache is stale or empty. Returns cached data."""
    now = time.time()
    if not force and cache["data"] and (now - cache["updated_ts"] < CACHE_TTL):
        return
    if cache["loading"]:
        return
    try:
        cache["loading"] = True
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 拉取数据...", flush=True)
        data = build_json()
        cache["data"] = data
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 拉取资讯...", flush=True)
        cache["news"] = fetch_news()
        cache["pulse"] = build_market_pulse(data)
        cache["analysis"] = build_analysis(data)
        cache["updated"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cache["updated_ts"] = time.time()
        cache["loading"] = False
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 完成", flush=True)
    except Exception as e:
        cache["loading"] = False
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 失败: {e}", flush=True)

# ── FLASK APP ───────────────────────────────────────────

app = Flask(__name__)
app.json.sort_keys = False

@app.route('/')
def index():
    resp = Response(HTML_PAGE, mimetype='text/html')
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return resp

@app.route('/api/data')
def api_data():
    ensure_data()
    return jsonify({
        "data": cache["data"],
        "news": cache.get("news", []),
        "pulse": cache.get("pulse", {}),
        "analysis": cache.get("analysis", []),
        "updated": cache["updated"],
        "loading": cache["loading"],
    })

@app.route('/api/debug')
def api_debug():
    """Check portfolio data."""
    ensure_data()
    d = cache.get("data") or {}
    result = {}
    for tab, info in d.items():
        groups = info.get("groups", {})
        for gname, stocks in groups.items():
            for s in stocks:
                sym = s.get("symbol", "?")
                has_pf = "portfolio" in s
                result[sym] = {"has_portfolio": has_pf, "portfolio": s.get("portfolio")}
    return jsonify(result)

@app.route('/api/history')
def api_history():
    """Fetch extended OHLC history for a single symbol.
    Params: symbol (required), days OR start+end dates.
    """
    symbol = request.args.get('symbol', '').strip()
    if not symbol:
        return jsonify({"error": "missing symbol"}), 400

    days_param = request.args.get('days')
    start_param = request.args.get('start')
    end_param = request.args.get('end')

    end_dt = datetime.now()
    if start_param and end_param:
        try:
            start_dt = datetime.strptime(start_param, '%Y-%m-%d')
            end_dt = datetime.strptime(end_param, '%Y-%m-%d')
        except ValueError:
            return jsonify({"error": "invalid date format, use YYYY-MM-DD"}), 400
    elif days_param:
        try:
            days = int(days_param)
        except ValueError:
            return jsonify({"error": "invalid days param"}), 400
        start_dt = end_dt - timedelta(days=days + 10)
    else:
        return jsonify({"error": "provide days or start+end"}), 400

    try:
        df = yf.download(symbol, start=start_dt, end=end_dt, progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if days_param:
            days = int(days_param)
            df = df.tail(days) if len(df) > days else df
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if df.empty:
        return jsonify({"ohlc": [], "symbol": symbol})

    df_clean = df.dropna(subset=['Close'])
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

    return jsonify({"ohlc": ohlc, "symbol": symbol})

@app.route('/api/refresh')
def api_refresh():
    """Force refresh, ignore cache TTL."""
    t = threading.Thread(target=ensure_data, args=(True,), daemon=True)
    t.start()
    return jsonify({"status": "refreshing"})

def build_single_stock(sym, label):
    """Build complete card data for a single stock (used by add-stock API)."""
    df = fetch_yf(sym)
    if df.empty:
        return {"symbol": sym, "label": label, "error": True}
    df_clean = df.dropna(subset=['Close'])
    if df_clean.empty:
        return {"symbol": sym, "label": label, "error": True}

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

    last_ohlc_date = df_clean.index[-1].strftime('%m/%d')
    rt_price, rt_prev = fetch_realtime_price(sym)
    if rt_price is not None:
        last = rt_price
        prev = rt_prev if rt_prev is not None else (float(close[-2]) if len(close) > 1 else last)
    else:
        last = float(close[-1])
        prev = float(close[-2]) if len(close) > 1 else last

    change = last - prev
    change_pct = (change / prev) * 100 if prev else 0

    entry = {
        "symbol": sym, "label": label,
        "desc": DESCRIPTIONS.get(sym, ""),
        "price": safe_float(last),
        "change": safe_float(change),
        "changePct": safe_float(change_pct),
        "high90": safe_float(df_clean['High'].max()),
        "low90": safe_float(df_clean['Low'].min()),
        "ohlc": ohlc,
        "dataDate": last_ohlc_date,
        "isRealtime": rt_price is not None,
    }

    pf = PORTFOLIO.get(sym)
    if pf and "shares" in pf and "cost" in pf:
        shares_val, cost_val = pf["shares"], pf["cost"]
        mkt_val = last * shares_val
        pnl = (last - cost_val) * shares_val
        pnl_pct = (last - cost_val) / cost_val * 100 if cost_val else 0
        entry["portfolio"] = {
            "type": "stock", "shares": shares_val,
            "cost": round(cost_val, 3),
            "mktVal": safe_float(mkt_val),
            "pnl": safe_float(pnl),
            "pnlPct": safe_float(pnl_pct),
            "purchases": pf.get("purchases", []),
        }

    return entry

@app.route('/api/add-stock', methods=['POST'])
def api_add_stock():
    global TABS, PORTFOLIO, TAB_CURRENCY, DESCRIPTIONS
    d = request.get_json(force=True)
    symbol = (d.get('symbol') or '').strip().upper()
    label = (d.get('label') or '').strip()
    desc = (d.get('desc') or '').strip()
    market = d.get('market', '美股')
    group = d.get('group', '关注')
    shares = d.get('shares')
    cost = d.get('cost')
    if not symbol or not label:
        return jsonify({"error": "代码和名称不能为空"}), 400

    if symbol.endswith('.US'):
        symbol = symbol[:-3]

    suffix_map = {"A股": ".SZ", "港股": ".HK"}
    if market in suffix_map and not any(symbol.endswith(s) for s in ['.SZ','.SS','.HK']):
        symbol = symbol + suffix_map[market]

    tab_map = {"美股": "美股", "港股": "港股", "A股": "东方财富"}
    tab_name = tab_map.get(market, market)
    if tab_name not in TABS:
        TABS[tab_name] = {}

    grp_name = group
    if '持仓' in group:
        existing_pf = [g for g in TABS[tab_name] if '持仓' in g]
        grp_name = existing_pf[0] if existing_pf else group
    else:
        existing_watch = [g for g in TABS[tab_name] if '持仓' not in g and '指数' not in g]
        grp_name = existing_watch[0] if existing_watch else group

    if grp_name not in TABS[tab_name]:
        TABS[tab_name][grp_name] = []

    for grp_stocks in TABS[tab_name].values():
        for s in grp_stocks:
            if s[0] == symbol:
                return jsonify({"error": f"{symbol} 已存在"}), 400

    try:
        test_price, _ = fetch_realtime_price(symbol)
        if test_price is None:
            return jsonify({"error": f"无法获取 {symbol} 的行情数据，请检查代码是否正确"}), 400
    except Exception:
        return jsonify({"error": f"验证 {symbol} 失败，请检查代码是否正确"}), 400

    TABS[tab_name][grp_name].append((symbol, label))

    if desc:
        DESCRIPTIONS[symbol] = desc

    if shares is not None and cost is not None:
        try:
            PORTFOLIO[symbol] = {"shares": float(shares), "cost": float(cost)}
        except:
            pass

    save_config(TABS, PORTFOLIO, TAB_CURRENCY, DESCRIPTIONS)

    stock_entry = build_single_stock(symbol, label)

    cache["updated_ts"] = 0
    t = threading.Thread(target=ensure_data, args=(True,), daemon=True)
    t.start()
    return jsonify({"ok": True, "symbol": symbol, "tab": tab_name, "group": grp_name, "stock": stock_entry})

@app.route('/api/remove-stock', methods=['POST'])
def api_remove_stock():
    global TABS, PORTFOLIO, TAB_CURRENCY, DESCRIPTIONS
    d = request.get_json(force=True)
    symbol = (d.get('symbol') or '').strip()
    if not symbol:
        return jsonify({"error": "缺少代码"}), 400
    removed = False
    for tab_name, groups in TABS.items():
        for grp_name, stocks in groups.items():
            TABS[tab_name][grp_name] = [s for s in stocks if s[0] != symbol]
            if len(TABS[tab_name][grp_name]) < len(stocks):
                removed = True
    PORTFOLIO.pop(symbol, None)
    DESCRIPTIONS.pop(symbol, None)
    if removed:
        save_config(TABS, PORTFOLIO, TAB_CURRENCY, DESCRIPTIONS)
        cache["updated_ts"] = 0
    return jsonify({"ok": removed})

@app.route('/api/update-portfolio', methods=['POST'])
def api_update_portfolio():
    global PORTFOLIO, TAB_CURRENCY, DESCRIPTIONS
    d = request.get_json(force=True)
    symbol = (d.get('symbol') or '').strip()
    action = d.get('action', 'set')
    shares = d.get('shares')
    cost = d.get('cost')
    if not symbol:
        return jsonify({"error": "缺少代码"}), 400

    old = PORTFOLIO.get(symbol, {"shares": 0, "cost": 0})

    if action == 'buy' and shares and cost:
        new_shares = old["shares"] + float(shares)
        new_cost = (old["shares"] * old["cost"] + float(shares) * float(cost)) / new_shares if new_shares else 0
        purchases = list(old.get("purchases", []))
        purchase_date = d.get('purchase_date', datetime.now().strftime('%Y-%m-%d'))
        purchases.append({"date": purchase_date, "shares": float(shares), "cost": float(cost)})
        PORTFOLIO[symbol] = {"shares": round(new_shares, 4), "cost": round(new_cost, 4), "purchases": purchases}
    elif action == 'sell' and shares:
        new_shares = old["shares"] - float(shares)
        if new_shares <= 0:
            PORTFOLIO.pop(symbol, None)
        else:
            PORTFOLIO[symbol] = {"shares": round(new_shares, 4), "cost": old["cost"], "purchases": list(old.get("purchases", []))}
    elif action == 'set':
        if shares is not None and cost is not None:
            purchase_date = d.get('purchase_date', datetime.now().strftime('%Y-%m-%d'))
            purchases = [{"date": purchase_date, "shares": float(shares), "cost": float(cost)}]
            PORTFOLIO[symbol] = {"shares": float(shares), "cost": float(cost), "purchases": purchases}
        elif shares is not None:
            PORTFOLIO[symbol] = {"shares": float(shares), "cost": old.get("cost", 0), "purchases": list(old.get("purchases", []))}
    else:
        return jsonify({"error": "参数不完整"}), 400

    save_config(TABS, PORTFOLIO, TAB_CURRENCY, DESCRIPTIONS)
    cache["updated_ts"] = 0
    return jsonify({"ok": True, "portfolio": PORTFOLIO.get(symbol)})

# ── HTML (self-contained) ──────────────────────────────

HTML_PAGE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stock Monitor</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📈</text></svg>">
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
    background: var(--bg); color: var(--text);
    font-family: -apple-system, 'SF Pro Text', 'PingFang SC', 'Helvetica Neue', sans-serif;
    min-height: 100vh;
  }
  .top-bar {
    display: flex; align-items: center; gap: 16px;
    padding: 16px 24px; border-bottom: 1px solid var(--border);
    position: sticky; top: 0; background: var(--bg); z-index: 100;
    flex-wrap: wrap;
  }
  .top-bar h1 { font-size: 20px; font-weight: 600; letter-spacing: -0.02em; color: #e8ecf4; white-space: nowrap; }
  .top-bar .date { font-size: 12px; color: var(--text-dim); white-space: nowrap; }
  .controls { display: flex; align-items: center; gap: 8px; margin-left: auto; }
  .tab-bar {
    display: flex; gap: 2px; background: #16161f;
    border-radius: 10px; padding: 3px;
  }
  .tab-bar button {
    background: none; border: none; color: var(--text-dim);
    font-size: 13px; padding: 6px 18px; border-radius: 8px;
    cursor: pointer; transition: all 0.15s; font-weight: 500; white-space: nowrap;
  }
  .tab-bar button.active { background: var(--accent); color: #fff; }
  .tab-bar button:hover:not(.active) { color: var(--text); background: rgba(255,255,255,0.04); }
  .mode-toggle {
    display: flex; gap: 2px; background: #16161f;
    border-radius: 8px; padding: 3px;
  }
  .mode-toggle button {
    background: none; border: none; color: var(--text-dim);
    font-size: 12px; padding: 4px 12px; border-radius: 6px;
    cursor: pointer; transition: all 0.15s;
  }
  .mode-toggle button.active { background: #2a2a3a; color: #e0e4f0; }
  .legend { display: flex; gap: 14px; font-size: 11px; color: var(--text-dim); }
  .legend span { display: flex; align-items: center; gap: 5px; white-space: nowrap; }
  .legend .dot { width: 14px; height: 2px; border-radius: 1px; display: inline-block; }
  .content { padding: 8px 24px 32px; }
  .group-header { display: flex; align-items: baseline; gap: 16px; margin: 24px 0 12px; }
  .group-title {
    font-size: 13px; font-weight: 600; color: var(--text-dim);
    text-transform: uppercase; letter-spacing: 0.08em;
  }
  .group-summary {
    margin-left: auto; display: flex; gap: 16px;
    font-size: 12px; color: var(--text-dim); font-feature-settings: 'tnum';
  }
  .group-summary .gs-item { white-space: nowrap; }
  .group-summary .gs-val { color: var(--text); font-weight: 600; }
  .group-summary .gs-pnl { font-weight: 600; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(370px, 1fr)); gap: 14px; }
  .card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 12px; padding: 14px 16px; transition: border-color 0.2s;
    position: relative;
  }
  .card:hover { border-color: #2a2a40; }
  .card-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
  .card-head .name { font-size: 14px; font-weight: 600; color: #e0e4f0; }
  .card-head .symbol { font-size: 11px; color: var(--text-dim); margin-left: 6px; font-weight: 400; }
  .card-head .desc { font-size: 11px; color: #666; font-weight: 400; margin-top: 2px; }
  .card-head .price-block { text-align: right; }
  .card-head .price { font-size: 20px; font-weight: 700; font-feature-settings: 'tnum'; letter-spacing: -0.02em; }
  .card-head .change { font-size: 11px; font-weight: 500; font-feature-settings: 'tnum'; }
  .stats {
    display: flex; align-items: center; gap: 10px;
    margin-top: 8px; font-size: 11px; color: var(--text-dim);
  }
  .stats .stat { background: #16161f; padding: 3px 8px; border-radius: 5px; }
  .stats .val { color: var(--text); font-weight: 500; }
  .stats .spacer { flex: 1; }
  .reset-btn {
    width: 24px; height: 24px; border-radius: 5px;
    border: 1px solid var(--border); background: #16161f;
    color: var(--text-dim); font-size: 14px; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: color 0.15s, border-color 0.15s;
    line-height: 1; padding: 0; flex-shrink: 0; opacity: 0.4;
  }
  .card:hover .reset-btn { opacity: 0.8; }
  .reset-btn:hover { color: #e0e4f0; border-color: var(--accent); }
  .chart-box { width: 100%; height: 200px; margin-top: 6px; border-radius: 8px; overflow: hidden; position: relative; }
  .up { color: var(--up); }
  .down { color: var(--down); }
  .error-card { display: flex; align-items: center; justify-content: center; min-height: 100px; color: var(--text-dim); font-size: 13px; }
  .empty-tab { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 300px; color: var(--text-dim); font-size: 14px; gap: 8px; }
  .empty-tab .hint { font-size: 12px; opacity: 0.6; }
  .portfolio {
    display: flex; align-items: center; gap: 12px;
    margin-top: 8px; padding: 8px 10px;
    background: linear-gradient(135deg, rgba(91,141,239,0.06), rgba(91,141,239,0.02));
    border: 1px solid rgba(91,141,239,0.1); border-radius: 8px;
    font-size: 11px; color: var(--text-dim);
  }
  .portfolio .pf-label { color: var(--accent); font-weight: 600; font-size: 10px; letter-spacing: 0.04em; white-space: nowrap; }
  .data-src { font-size: 9px; opacity: 0.35; margin-left: 6px; }
  .portfolio .pf-item { white-space: nowrap; }
  .portfolio .pf-val { color: var(--text); font-weight: 500; }
  .portfolio .pf-pnl { margin-left: auto; font-weight: 600; font-size: 12px; font-feature-settings: 'tnum'; }
  .portfolio .pf-delay { font-size: 9px; opacity: 0.45; font-weight: 400; margin-left: 4px; }
  .period-toggle {
    display: flex; gap: 2px; background: #16161f;
    border-radius: 8px; padding: 3px; flex-wrap: wrap;
  }
  .period-toggle button {
    background: none; border: none; color: var(--text-dim);
    font-size: 12px; padding: 4px 10px; border-radius: 6px;
    cursor: pointer; transition: all 0.15s;
  }
  .period-toggle button.active { background: #2a2a3a; color: #e0e4f0; }
  .period-toggle button.custom-active { background: var(--accent); color: #fff; }
  .custom-range-picker {
    display: none; align-items: center; gap: 8px;
    margin-top: 6px; padding: 8px 12px;
    background: #16161f; border-radius: 8px;
    border: 1px solid var(--border);
  }
  .custom-range-picker.show { display: flex; flex-wrap: wrap; flex-basis: 100%; }
  .custom-range-picker label { font-size: 11px; color: var(--text-dim); white-space: nowrap; }
  .custom-range-picker input[type="date"] {
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 6px; color: var(--text); font-size: 12px;
    padding: 4px 8px; outline: none; transition: border-color 0.15s;
  }
  .custom-range-picker input[type="date"]:focus { border-color: var(--accent); }
  .custom-range-picker .apply-btn {
    background: var(--accent); color: #fff; border: none;
    border-radius: 6px; font-size: 11px; padding: 5px 12px;
    cursor: pointer; font-weight: 500; transition: opacity 0.15s;
  }
  .custom-range-picker .apply-btn:hover { opacity: 0.85; }
  .recommendation {
    display: flex; align-items: center; gap: 8px;
    margin-top: 8px; padding: 7px 10px;
    background: #16161f; border-radius: 6px;
    font-size: 11px;
  }
  .rec-signal {
    padding: 2px 8px; border-radius: 4px;
    font-weight: 600; font-size: 11px;
    white-space: nowrap; flex-shrink: 0;
  }
  .rec-buy { background: rgba(239,68,68,0.12); color: var(--up); }
  .rec-watch { background: rgba(96,104,120,0.18); color: var(--text-dim); }
  .rec-sell { background: rgba(34,197,94,0.12); color: var(--down); }
  .rec-nobuy { background: rgba(255,176,64,0.12); color: #ffb040; }
  .rec-reason { color: var(--text-dim); line-height: 1.4; }

  /* ── News Tab ── */
  .pulse-bar {
    display: flex; gap: 12px; flex-wrap: wrap;
    padding: 16px 0; border-bottom: 1px solid var(--border); margin-bottom: 20px;
  }
  .pulse-item {
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 12px 18px; min-width: 140px; flex: 1;
  }
  .pulse-name { font-size: 11px; color: var(--text-dim); margin-bottom: 4px; }
  .pulse-price { font-size: 18px; font-weight: 700; font-feature-settings: 'tnum'; }
  .pulse-change { font-size: 12px; font-weight: 500; margin-top: 2px; }
  .pulse-mood {
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 12px 24px; display: flex; flex-direction: column;
    align-items: center; justify-content: center; min-width: 100px;
  }
  .pulse-mood-label { font-size: 11px; color: var(--text-dim); margin-bottom: 4px; }
  .pulse-mood-val { font-size: 22px; font-weight: 700; }
  .pulse-mood-val.up { color: var(--up); }
  .pulse-mood-val.down { color: var(--down); }
  .pulse-mood-val.neutral { color: var(--text-dim); }
  .news-list { display: flex; flex-direction: column; gap: 1px; }
  .news-item {
    display: flex; gap: 14px; padding: 14px 16px;
    background: var(--card); border-radius: 10px;
    transition: background 0.15s; cursor: pointer;
    text-decoration: none; color: inherit;
    border: 1px solid var(--border);
    margin-bottom: 8px;
  }
  .news-item:hover { background: #1a1a26; border-color: #2a2a40; }
  .news-meta { flex-shrink: 0; min-width: 72px; text-align: right; }
  .news-time { font-size: 11px; color: var(--text-dim); }
  .news-pub { font-size: 10px; color: #4a4a5a; margin-top: 2px; }
  .news-body { flex: 1; }
  .news-title { font-size: 13px; font-weight: 500; color: #e0e4f0; line-height: 1.5; }
  .news-tickers { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
  .news-ticker {
    font-size: 10px; padding: 1px 6px; border-radius: 4px;
    background: rgba(91,141,239,0.1); color: var(--accent); font-weight: 500;
  }
  .news-section-title {
    font-size: 13px; font-weight: 600; color: var(--text-dim);
    text-transform: uppercase; letter-spacing: 0.08em;
    margin: 20px 0 12px;
  }
  .analysis-grid {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 8px; margin-bottom: 20px;
  }
  .analysis-item {
    display: flex; gap: 8px; padding: 10px 12px;
    background: var(--card); border-radius: 8px;
    border-left: 3px solid var(--text-dim);
  }
  .analysis-item.full { grid-column: 1 / -1; }
  .analysis-item.a-positive { border-left-color: var(--up); }
  .analysis-item.a-warning { border-left-color: var(--down); }
  .analysis-item.a-info { border-left-color: var(--accent); }
  .analysis-item.a-neutral { border-left-color: #ffb040; }
  .analysis-icon { font-size: 14px; flex-shrink: 0; line-height: 1.4; }
  .analysis-body { flex: 1; min-width: 0; }
  .analysis-title { font-size: 12px; font-weight: 600; color: #e0e4f0; }
  .analysis-text { font-size: 11px; color: var(--text-dim); line-height: 1.5; margin-top: 2px; }

  /* ── FAB + Modal ── */
  .fab {
    position: fixed; bottom: 28px; right: 28px; z-index: 200;
    width: 52px; height: 52px; border-radius: 50%;
    background: var(--accent); border: none; color: #fff;
    font-size: 28px; cursor: pointer; display: flex;
    align-items: center; justify-content: center;
    box-shadow: 0 4px 20px rgba(91,141,239,0.35);
    transition: transform 0.2s, box-shadow 0.2s;
  }
  .fab:hover { transform: scale(1.1); box-shadow: 0 6px 28px rgba(91,141,239,0.5); }
  .modal-overlay {
    position: fixed; inset: 0; z-index: 300;
    background: rgba(0,0,0,0.6); backdrop-filter: blur(4px);
    display: none; align-items: center; justify-content: center;
  }
  .modal-overlay.show { display: flex; }
  .modal {
    background: #16161f; border: 1px solid var(--border);
    border-radius: 16px; padding: 28px 32px; width: 420px; max-width: 90vw;
  }
  .modal h2 { font-size: 18px; font-weight: 600; color: #e0e4f0; margin-bottom: 20px; }
  .modal label { display: block; font-size: 12px; color: var(--text-dim); margin-bottom: 6px; margin-top: 14px; }
  .modal input[type="text"], .modal input[type="number"], .modal input[type="date"] {
    width: 100%; padding: 9px 12px; border-radius: 8px;
    border: 1px solid var(--border); background: var(--bg);
    color: var(--text); font-size: 13px; outline: none;
    transition: border-color 0.15s;
  }
  .modal input:focus { border-color: var(--accent); }
  .modal input[type="date"] { color-scheme: dark; }
  .pos-purchases { margin-bottom: 16px; }
  .pos-purchases-title { font-size: 12px; color: var(--text-dim); margin-bottom: 8px; }
  .pos-purchases-list {
    background: var(--bg); border-radius: 8px; padding: 10px 12px;
    font-size: 11px; color: var(--text);
  }
  .pos-purchase-row {
    display: flex; justify-content: space-between; padding: 4px 0;
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }
  .pos-purchase-row:last-child { border-bottom: none; }
  .pos-total {
    margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border);
    font-weight: 600; display: flex; justify-content: space-between;
  }
  .modal .radio-group { display: flex; gap: 8px; flex-wrap: wrap; }
  .modal .radio-btn {
    padding: 6px 14px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text-dim); font-size: 12px;
    cursor: pointer; transition: all 0.15s;
  }
  .modal .radio-btn.active { border-color: var(--accent); color: var(--accent); background: rgba(91,141,239,0.08); }
  .modal .pf-fields { display: none; gap: 12px; margin-top: 8px; }
  .modal .pf-fields.show { display: flex; }
  .modal .pf-fields input { width: 50%; }
  .modal .actions { display: flex; gap: 10px; margin-top: 22px; }
  .modal .btn {
    flex: 1; padding: 10px; border-radius: 8px; border: none;
    font-size: 13px; font-weight: 500; cursor: pointer; transition: opacity 0.15s;
  }
  .modal .btn-primary { background: var(--accent); color: #fff; }
  .modal .btn-cancel { background: #2a2a36; color: var(--text-dim); }
  .modal .btn:hover { opacity: 0.85; }
  .modal .hint { font-size: 11px; color: #4a4a5a; margin-top: 4px; }
  .modal .msg { font-size: 12px; margin-top: 12px; padding: 8px 10px; border-radius: 6px; display: none; }
  .modal .msg.err { display: block; background: rgba(239,68,68,0.1); color: var(--up); }
  .modal .msg.ok { display: block; background: rgba(91,141,239,0.1); color: var(--accent); }
  .card-del {
    position: absolute; top: 10px; right: 10px;
    width: 22px; height: 22px; border-radius: 5px; border: none;
    background: transparent; color: var(--text-dim); font-size: 13px;
    cursor: pointer; opacity: 0; transition: opacity 0.15s, color 0.15s;
    display: flex; align-items: center; justify-content: center;
    z-index: 10;
  }
  .card:hover .card-del { opacity: 0.4; }
  .card-del:hover { opacity: 1 !important; color: var(--down); background: rgba(34,197,94,0.12); }
  .portfolio { cursor: pointer; position: relative; }
  .portfolio:hover { border-color: var(--accent); }
  .portfolio .pf-edit-hint {
    font-size: 9px; opacity: 0; transition: opacity 0.15s; color: var(--accent);
    margin-left: auto; padding-left: 8px;
  }
  .portfolio:hover .pf-edit-hint { opacity: 0.7; }
  .edit-modal {
    position: fixed; inset: 0; z-index: 310;
    background: rgba(0,0,0,0.6); backdrop-filter: blur(4px);
    display: none; align-items: center; justify-content: center;
  }
  .edit-modal.show { display: flex; }
  .edit-modal .modal { width: 380px; }
  .edit-modal .action-tabs { display: flex; gap: 4px; margin-bottom: 14px; }
  .edit-modal .action-tab {
    flex: 1; padding: 8px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--bg); color: var(--text-dim); font-size: 13px; font-weight: 500;
    cursor: pointer; text-align: center; transition: all 0.15s;
  }
  .edit-modal .action-tab.active { border-color: var(--accent); color: var(--accent); background: rgba(91,141,239,0.08); }
  .edit-modal .action-tab.sell.active { border-color: var(--down); color: var(--down); background: rgba(34,197,94,0.08); }
  .news-title-zh { font-size: 12px; color: var(--text-dim); margin-top: 3px; line-height: 1.4; }

  /* ── Nav sections & dividers ── */
  .nav-section { display: flex; align-items: center; gap: 6px; }
  .nav-label {
    font-size: 10px; color: var(--text-dim); opacity: 0.6;
    letter-spacing: 0.06em; white-space: nowrap;
  }
  .nav-divider {
    width: 1px; height: 24px; background: #2a2a3a;
    margin: 0 4px; flex-shrink: 0;
  }
  .period-more { position: relative; }
  .period-more-btn {
    background: none; border: none; color: var(--text-dim);
    font-size: 12px; padding: 4px 10px; border-radius: 6px;
    cursor: pointer; transition: all 0.15s; white-space: nowrap;
  }
  .period-more-btn.active { background: #2a2a3a; color: #e0e4f0; }
  .period-more-btn.custom-active { background: var(--accent); color: #fff; }
  .period-dropdown {
    display: none; position: absolute; top: calc(100% + 4px); left: 50%;
    transform: translateX(-50%);
    background: #1a1a26; border: 1px solid var(--border);
    border-radius: 8px; padding: 4px; z-index: 200;
    min-width: 100px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  }
  .period-more:hover .period-dropdown { display: flex; flex-direction: column; }
  .period-dropdown button {
    background: none; border: none; color: var(--text-dim);
    font-size: 12px; padding: 6px 14px; border-radius: 6px;
    cursor: pointer; transition: all 0.15s; text-align: left;
    white-space: nowrap;
  }
  .period-dropdown button:hover { background: rgba(255,255,255,0.06); color: var(--text); }
  .period-dropdown button.active { color: var(--accent); }

  @media (max-width: 820px) {
    .grid { grid-template-columns: 1fr; }
    .content { padding: 8px 14px 24px; }
    .top-bar { padding: 12px 14px; }
    .controls { width: 100%; justify-content: flex-start; flex-wrap: wrap; margin-left: 0; margin-top: 8px; gap: 6px; }
    .nav-divider { height: 20px; }
    .nav-label { display: none; }
  }
</style>
</head>
<body>

<div class="top-bar">
  <h1>Stock Monitor</h1>
  <span class="date"><span id="updateTime">加载中...</span></span>
  <div class="controls">
    <div class="nav-section">
      <span class="nav-label">标签</span>
      <div class="tab-bar" id="tabBar"></div>
    </div>
    <div class="nav-divider"></div>
    <div class="nav-section">
      <span class="nav-label">周期</span>
      <div class="period-toggle" id="periodBar">
        <button onclick="setPeriod(1)">当日</button>
        <button onclick="setPeriod(7)">7日</button>
        <div class="period-more">
          <button class="period-more-btn active" id="moreBtn">90日 ▾</button>
          <div class="period-dropdown" id="periodDropdown">
            <button onclick="setPeriodFromMore(30)">30日</button>
            <button onclick="setPeriodFromMore(60)">60日</button>
            <button class="active" onclick="setPeriodFromMore(90)">90日</button>
            <button onclick="setPeriodFromMore(180)">180日</button>
            <button onclick="setPeriodFromMore(365)">1年</button>
            <button onclick="setPeriodFromMore(730)">2年</button>
            <button onclick="setPeriodFromMore(1095)">3年</button>
            <button onclick="setPeriodFromMore(1825)">5年</button>
            <button onclick="setPeriodFromMore(9999)">Max</button>
            <button onclick="toggleCustomRange()" id="customBtn">自定义</button>
          </div>
        </div>
      </div>
    </div>
    <div class="nav-divider"></div>
    <div class="nav-section">
      <span class="nav-label">图表</span>
      <div class="mode-toggle">
        <button class="active" onclick="setMode('line')">曲线</button>
        <button onclick="setMode('candle')">K线</button>
      </div>
    </div>
    <div class="nav-divider"></div>
    <div class="nav-section">
      <span class="nav-label">图例</span>
      <div class="legend">
        <span><i class="dot" style="background:var(--accent)"></i>实际价格</span>
        <span><i class="dot" style="background:var(--ma5)"></i>5日均线</span>
        <span><i class="dot" style="background:var(--ma20)"></i>20日均线</span>
      </div>
    </div>
  </div>
  <div class="custom-range-picker" id="customRangePicker">
    <label>从</label>
    <input type="date" id="customStart">
    <label>至</label>
    <input type="date" id="customEnd">
    <button class="apply-btn" onclick="applyCustomRange()">应用</button>
  </div>
</div>

<div class="content" id="app"><div class="empty-tab"><div>正在加载数据...</div></div></div>

<button class="fab" onclick="openModal()" title="添加标的">+</button>

<div class="edit-modal" id="editOverlay" onclick="if(event.target===this)closeEdit()">
  <div class="modal">
    <h2 id="edit-title">调整持仓</h2>
    <div class="action-tabs">
      <div class="action-tab active" onclick="setEditAction('buy')">加仓</div>
      <div class="action-tab sell" onclick="setEditAction('sell')">减仓</div>
      <div class="action-tab" onclick="setEditAction('set')">直接设置</div>
    </div>
    <div id="edit-fields"></div>
    <div class="msg" id="edit-msg"></div>
    <div class="actions">
      <button class="btn btn-cancel" onclick="closeEdit()">取消</button>
      <button class="btn btn-primary" id="edit-submit" onclick="submitEdit()">确认</button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="positionOverlay" onclick="if(event.target===this)closePositionModal()">
  <div class="modal" style="width:480px">
    <h2 id="pos-title">录入持仓</h2>
    <div id="pos-existing"></div>
    <label>购买时间</label>
    <input type="date" id="pos-date">
    <label>数量（股）</label>
    <input type="number" id="pos-shares" placeholder="买入股数">
    <label>成本价（每股）</label>
    <input type="number" id="pos-cost" step="0.01" placeholder="每股买入价">
    <div class="msg" id="pos-msg"></div>
    <div class="actions">
      <button class="btn btn-cancel" onclick="closePositionModal()">取消</button>
      <button class="btn btn-primary" onclick="submitPosition()">确认</button>
    </div>
  </div>
</div>

<div class="modal-overlay" id="modalOverlay" onclick="if(event.target===this)closeModal()">
  <div class="modal">
    <h2>添加标的</h2>
    <label>股票代码</label>
    <input type="text" id="m-symbol" placeholder="例：AAPL、0700、002283">
    <div class="hint">美股直接输入代码，港股/A股输入数字部分即可</div>

    <label>中文名称</label>
    <input type="text" id="m-label" placeholder="例：苹果、腾讯">

    <label>一句话描述 <span style="opacity:0.5;font-size:10px">（可选）</span></label>
    <input type="text" id="m-desc" placeholder="例：CPU/GPU 芯片设计">

    <label>市场</label>
    <div class="radio-group" id="m-market">
      <div class="radio-btn active" data-val="美股">美股</div>
      <div class="radio-btn" data-val="港股">港股</div>
      <div class="radio-btn" data-val="A股">A股</div>
    </div>

    <label>分组</label>
    <div class="radio-group" id="m-group">
      <div class="radio-btn active" data-val="关注">关注</div>
      <div class="radio-btn" data-val="我的持仓">持仓</div>
    </div>

    <div class="pf-fields" id="m-pf">
      <div style="flex:1"><label style="margin-top:0">持仓数量</label><input type="number" id="m-shares" placeholder="股数"></div>
      <div style="flex:1"><label style="margin-top:0">成本价</label><input type="number" id="m-cost" step="0.01" placeholder="每股成本"></div>
    </div>

    <div class="msg" id="m-msg"></div>

    <div class="actions">
      <button class="btn btn-cancel" onclick="closeModal()">取消</button>
      <button class="btn btn-primary" id="m-submit" onclick="submitAdd()">添加</button>
    </div>
  </div>
</div>

<script>
let TABS_DATA = {};
let NEWS_DATA = [];
let PULSE_DATA = {};
let ANALYSIS_DATA = [];
let tabNames = [];
let activeTab = '资讯';
let currentMode = 'line';
let currentPeriod = 90;
let customDateRange = null; // {start, end} when custom range is active
const PERIOD_LABELS = {1:'当日',7:'7日',30:'30日',60:'60日',90:'90日',180:'180日',365:'1年',730:'2年',1095:'3年',1825:'5年',9999:'Max'};
let chartInstances = [];
const chartMap = {};
let extendedOhlcCache = {}; // {symbol: {days: ohlcArray}}

function setPeriod(days) {
  currentPeriod = days;
  customDateRange = null;
  document.getElementById('customRangePicker').classList.remove('show');
  document.querySelectorAll('#periodBar > button').forEach(b => {
    b.classList.remove('active');
    if (b.textContent === PERIOD_LABELS[days]) b.classList.add('active');
  });
  const moreBtn = document.getElementById('moreBtn');
  moreBtn.textContent = '更多 ▾';
  moreBtn.classList.remove('active', 'custom-active');
  document.querySelectorAll('#periodDropdown button').forEach(b => b.classList.remove('active'));
  destroyCharts();
  if (days > 90) {
    fetchExtendedAndRender(days);
  } else {
    renderTab();
  }
}

function setPeriodFromMore(days) {
  currentPeriod = days;
  customDateRange = null;
  document.getElementById('customRangePicker').classList.remove('show');
  document.querySelectorAll('#periodBar > button').forEach(b => b.classList.remove('active'));
  const moreBtn = document.getElementById('moreBtn');
  moreBtn.textContent = (PERIOD_LABELS[days] || days+'日') + ' ▾';
  moreBtn.classList.add('active');
  moreBtn.classList.remove('custom-active');
  document.querySelectorAll('#periodDropdown button').forEach(b => {
    b.classList.toggle('active', b.textContent === PERIOD_LABELS[days]);
  });
  destroyCharts();
  if (days > 90) {
    fetchExtendedAndRender(days);
  } else {
    renderTab();
  }
}

function toggleCustomRange() {
  const picker = document.getElementById('customRangePicker');
  const btn = document.getElementById('customBtn');
  const showing = picker.classList.toggle('show');
  if (showing) {
    const today = new Date().toISOString().slice(0, 10);
    const oneYearAgo = new Date(Date.now() - 365*86400000).toISOString().slice(0, 10);
    if (!document.getElementById('customEnd').value) document.getElementById('customEnd').value = today;
    if (!document.getElementById('customStart').value) document.getElementById('customStart').value = oneYearAgo;
  }
}

function applyCustomRange() {
  const start = document.getElementById('customStart').value;
  const end = document.getElementById('customEnd').value;
  if (!start || !end) return;
  if (start >= end) { alert('开始日期必须早于结束日期'); return; }
  customDateRange = { start, end };
  currentPeriod = 'custom';
  document.querySelectorAll('#periodBar > button').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('#periodDropdown button').forEach(b => {
    b.classList.toggle('active', b.id === 'customBtn');
  });
  const moreBtn = document.getElementById('moreBtn');
  moreBtn.textContent = '自定义 ▾';
  moreBtn.classList.remove('active');
  moreBtn.classList.add('custom-active');
  destroyCharts();
  fetchExtendedAndRender(null, start, end);
}

async function fetchExtendedAndRender(days, start, end) {
  const tabData = TABS_DATA[activeTab];
  if (!tabData || activeTab === '资讯') { renderTab(); return; }
  const groups = tabData.groups || {};
  const symbols = [];
  for (const stocks of Object.values(groups)) {
    for (const s of stocks) {
      if (!s.error) symbols.push(s.symbol);
    }
  }

  const fetches = symbols.map(async sym => {
    const cacheKey = days ? `${sym}_${days}` : `${sym}_${start}_${end}`;
    if (extendedOhlcCache[cacheKey]) return;
    let url = `/api/history?symbol=${encodeURIComponent(sym)}`;
    if (days) url += `&days=${days}`;
    else url += `&start=${start}&end=${end}`;
    try {
      const resp = await fetch(url);
      const json = await resp.json();
      if (json.ohlc) extendedOhlcCache[cacheKey] = json.ohlc;
    } catch(e) { console.error('History fetch failed:', sym, e); }
  });

  await Promise.all(fetches);
  renderTab();
}

function getEffectiveOhlc(stock) {
  if (customDateRange) {
    const key = `${stock.symbol}_${customDateRange.start}_${customDateRange.end}`;
    return extendedOhlcCache[key] || stock.ohlc;
  }
  if (currentPeriod > 90) {
    const key = `${stock.symbol}_${currentPeriod}`;
    return extendedOhlcCache[key] || stock.ohlc;
  }
  return stock.ohlc;
}

function getPeriodData(ohlc, calendarDays, realPrice, customRange) {
  if (!ohlc || ohlc.length === 0) return { changePct:0, high:0, low:0 };
  const now = realPrice || ohlc[ohlc.length-1].close;

  if (customRange) {
    const f = ohlc.filter(d => d.date >= customRange.start && d.date <= customRange.end);
    if (f.length === 0) return { changePct:0, high:now, low:now };
    const first = f[0].close;
    return { changePct: first ? ((now-first)/first*100) : 0, high: Math.max(now, ...f.map(d=>d.high)), low: Math.min(now, ...f.map(d=>d.low)) };
  }

  if (calendarDays <= 1) {
    const d = ohlc[ohlc.length - 1];
    const base = d.open || d.close;
    return { changePct: base ? ((now - base) / base * 100) : 0, high: Math.max(d.high, now), low: Math.min(d.low, now) };
  }
  if (calendarDays >= 9999) {
    const first = ohlc[0].close;
    return { changePct: first ? ((now-first)/first*100) : 0, high: Math.max(now, ...ohlc.map(d=>d.high)), low: Math.min(now, ...ohlc.map(d=>d.low)) };
  }
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - calendarDays);
  const cs = cutoff.toISOString().slice(0,10);
  const f = ohlc.filter(d => d.date >= cs);
  if (f.length === 0) { return { changePct:0, high:now, low:now }; }
  const first = f[0].close;
  return { changePct: first ? ((now-first)/first*100) : 0, high: Math.max(now, ...f.map(d=>d.high)), low: Math.min(now, ...f.map(d=>d.low)) };
}

function getRecommendation(ohlc) {
  if (!ohlc || ohlc.length < 20) return null;
  const closes = ohlc.map(d => d.close);
  const n = closes.length, last = closes[n-1];
  const avg = a => a.reduce((s,v)=>s+v,0)/a.length;
  const ma5 = avg(closes.slice(-5)), ma20 = avg(closes.slice(-20));
  const pMa5 = n>=6 ? avg(closes.slice(-6,-1)) : ma5;
  const pMa20 = n>=21 ? avg(closes.slice(-21,-1)) : ma20;
  const golden = pMa5<=pMa20 && ma5>ma20;
  const death = pMa5>=pMa20 && ma5<ma20;
  const m5 = n>=6 ? ((last-closes[n-6])/closes[n-6]*100) : 0;
  const up = ma5>ma20;
  if (golden) return {signal:'建议购买',cls:'rec-buy',reason:'5日均线上穿20日均线形成金叉，短期趋势转强'};
  if (death) return {signal:'观望',cls:'rec-watch',reason:'5日均线下穿20日均线形成死叉，短期趋势走弱'};
  if (up && m5>3) return {signal:'建议购买',cls:'rec-buy',reason:'多头排列，近5日涨'+m5.toFixed(1)+'%，上升趋势明确'};
  if (!up && m5<-5) return {signal:'抛掉',cls:'rec-sell',reason:'空头排列，近5日跌'+Math.abs(m5).toFixed(1)+'%，建议止损离场'};
  if (!up && m5<-3) return {signal:'不买',cls:'rec-nobuy',reason:'空头排列，近5日跌'+Math.abs(m5).toFixed(1)+'%，下行压力较大'};
  if (up) return {signal:'观望',cls:'rec-watch',reason:'均线多头但涨幅有限，等待回调或突破确认'};
  return {signal:'观望',cls:'rec-watch',reason:'均线交织震荡，方向不明确，等待趋势确认'};
}
async function loadData() {
  document.getElementById('updateTime').textContent = '加载中...';
  try {
    const resp = await fetch('/api/data');
    const json = await resp.json();
    if (json.loading && !json.data) {
      setTimeout(loadData, 2000);
      return;
    }
    if (!json.data) return;
    TABS_DATA = json.data;
    NEWS_DATA = json.news || [];
    PULSE_DATA = json.pulse || {};
    ANALYSIS_DATA = json.analysis || [];
    tabNames = ['资讯', ...Object.keys(TABS_DATA)];
    if (!activeTab || !tabNames.includes(activeTab)) activeTab = tabNames[0];
    document.getElementById('updateTime').textContent = (json.updated || '—') + (json.loading ? ' · 更新中...' : '');
    initTabs();
    destroyCharts();
    renderTab();
  } catch (e) {
    document.getElementById('updateTime').textContent = '加载失败';
    console.error(e);
  }
}

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
    width: container.clientWidth, height: 200,
    layout: { background: { type: 'solid', color: '#12121a' }, textColor: '#606878', fontSize: 11 },
    grid: { vertLines: { color: '#1a1a24' }, horzLines: { color: '#1a1a24' } },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: '#3a3a50', width: 1, style: 2 },
      horzLine: { color: '#3a3a50', width: 1, style: 2 },
    },
    rightPriceScale: { borderColor: '#1e1e2a' },
    timeScale: { borderColor: '#1e1e2a', timeVisible: false },
    handleScroll: true, handleScale: true,
  });

  const ohlc = stock.ohlc.map(d => ({ time: d.date, open: d.open, high: d.high, low: d.low, close: d.close }));

  if (currentMode === 'candle') {
    const s = chart.addCandlestickSeries({
      upColor: '#ef4444', downColor: '#22c55e',
      borderUpColor: '#ef4444', borderDownColor: '#22c55e',
      wickUpColor: '#ef4444', wickDownColor: '#22c55e',
    });
    s.setData(ohlc);
  } else {
    const s = chart.addAreaSeries({
      lineColor: '#5b8def', topColor: 'rgba(91,141,239,0.15)',
      bottomColor: 'rgba(91,141,239,0.01)', lineWidth: 2,
    });
    s.setData(ohlc.map(d => ({ time: d.time, value: d.close })));
  }

  chart.addLineSeries({ color: '#ffb040', lineWidth: 1, lineStyle: 2 }).setData(calcMA(stock.ohlc, 5));
  chart.addLineSeries({ color: '#ff5070', lineWidth: 1, lineStyle: 2 }).setData(calcMA(stock.ohlc, 20));
  chart.timeScale().fitContent();
  new ResizeObserver(() => chart.applyOptions({ width: container.clientWidth })).observe(container);
  chartInstances.push(chart);
  chartMap[container.id.replace('chart-', '')] = chart;
}

function resetChart(key) {
  const chart = chartMap[key];
  if (chart) chart.timeScale().fitContent();
}

function renderNews() {
  const app = document.getElementById('app');
  app.innerHTML = '';

  // Market Pulse
  const pulse = PULSE_DATA;
  if (pulse && pulse.indices && pulse.indices.length > 0) {
    const bar = document.createElement('div');
    bar.className = 'pulse-bar';
    for (const idx of pulse.indices) {
      const up = idx.changePct >= 0;
      const cls = up ? 'up' : 'down';
      const sign = up ? '+' : '-';
      const item = document.createElement('div');
      item.className = 'pulse-item';
      item.innerHTML = `
        <div class="pulse-name">${idx.name}</div>
        <div class="pulse-price ${cls}">${idx.price.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})}</div>
        <div class="pulse-change ${cls}">${sign}${Math.abs(idx.change).toFixed(2)} (${sign}${Math.abs(idx.changePct).toFixed(2)}%)</div>
      `;
      bar.appendChild(item);
    }
    const mood = document.createElement('div');
    mood.className = 'pulse-mood';
    mood.innerHTML = `<div class="pulse-mood-label">市场情绪</div><div class="pulse-mood-val ${pulse.moodCls||''}">${pulse.mood||'—'}</div>`;
    bar.appendChild(mood);
    app.appendChild(bar);
  }

  // Analysis
  if (ANALYSIS_DATA && ANALYSIS_DATA.length > 0) {
    const aTitle = document.createElement('div');
    aTitle.className = 'news-section-title';
    aTitle.textContent = '市场分析';
    app.appendChild(aTitle);

    const aList = document.createElement('div');
    aList.className = 'analysis-grid';
    const icons = {positive:'📈', warning:'⚠️', info:'📊', neutral:'↔️'};
    for (let i = 0; i < ANALYSIS_DATA.length; i++) {
      const a = ANALYSIS_DATA[i];
      const div = document.createElement('div');
      const fullCls = i === 0 ? ' full' : '';
      div.className = `analysis-item a-${a.type||'info'}${fullCls}`;
      div.innerHTML = `
        <div class="analysis-icon">${icons[a.type]||'📊'}</div>
        <div class="analysis-body">
          <div class="analysis-title">${a.title}</div>
          <div class="analysis-text">${a.text}</div>
        </div>
      `;
      aList.appendChild(div);
    }
    app.appendChild(aList);
  }

  // News
  if (!NEWS_DATA || NEWS_DATA.length === 0) {
    app.innerHTML += '<div class="empty-tab"><div>暂无资讯</div><div class="hint">数据加载中或网络不可用</div></div>';
    return;
  }

  const title = document.createElement('div');
  title.className = 'news-section-title';
  title.textContent = '最新资讯';
  app.appendChild(title);

  const list = document.createElement('div');
  list.className = 'news-list';
  for (const n of NEWS_DATA) {
    const a = document.createElement('a');
    a.className = 'news-item';
    a.href = n.link || '#';
    a.target = '_blank';
    a.rel = 'noopener';
    const tickerHtml = (n.tickers||[]).map(t => `<span class="news-ticker">${t}</span>`).join('');
    const zhLine = n.titleZh ? `<div class="news-title-zh">${n.titleZh}</div>` : '';
    a.innerHTML = `
      <div class="news-body">
        <div class="news-title">${n.title}</div>
        ${zhLine}
        ${tickerHtml ? `<div class="news-tickers">${tickerHtml}</div>` : ''}
      </div>
      <div class="news-meta">
        <div class="news-time">${n.timeStr || ''}</div>
        <div class="news-pub">${n.publisher || ''}</div>
      </div>
    `;
    list.appendChild(a);
  }
  app.appendChild(list);
}

function renderTab() {
  const app = document.getElementById('app');
  app.innerHTML = '';

  if (activeTab === '资讯') { renderNews(); return; }

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
      totalVal += p.mktVal || 0;
      totalPnl += p.pnl;
    }

    const header = document.createElement('div');
    header.className = 'group-header';
    let sumHtml = '';
    if (hasPf) {
      const sCls = totalPnl >= 0 ? 'up' : 'down';
      const sSign = totalPnl >= 0 ? '+' : '-';
      const fmt = n => Math.abs(n).toFixed(0).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
      const pct = totalVal > 0 ? Math.abs(totalPnl / ((totalVal - totalPnl) || 1) * 100).toFixed(2) : '0';
      const firstDate = stocks.find(s => s.dataDate)?.dataDate || '';
      const allRt = stocks.every(s => s.isRealtime);
      const srcLabel = allRt ? '' : `截至 ${firstDate} 收盘`;
      sumHtml = `<div class="group-summary">
        <span class="gs-item">总市值 <span class="gs-val">${csym}${fmt(totalVal)}</span></span>
        <span class="gs-item">总盈亏 <span class="gs-pnl ${sCls}">${sSign}${csym}${fmt(totalPnl)} (${sSign}${pct}%)</span>
        <span style="font-size:10px;opacity:0.4">${srcLabel}</span></span>
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
      const sign = up ? '+' : '-';
      const safeSym = stock.symbol.replace(/[^a-zA-Z0-9]/g, '_');

      const effectiveOhlc = getEffectiveOhlc(stock);
      const pd = getPeriodData(effectiveOhlc, currentPeriod === 'custom' ? 0 : currentPeriod, stock.price, customDateRange);
      const pdLabel = customDateRange ? '区间' : (PERIOD_LABELS[currentPeriod] || currentPeriod+'日');
      const pdUp = pd.changePct >= 0;
      const pdCls = pdUp ? 'up' : 'down';
      const pdSign = pdUp ? '+' : '-';

      const rec = getRecommendation(effectiveOhlc);
      let recHtml = '';
      if (rec) {
        recHtml = `<div class="recommendation"><span class="rec-signal ${rec.cls}">${rec.signal}</span><span class="rec-reason">${rec.reason}</span></div>`;
      }

      let pfHtml = '';
      const pf = stock.portfolio;
      if (pf) {
        const pCls = pf.pnl >= 0 ? 'up' : 'down';
        const pSign = pf.pnl >= 0 ? '+' : '-';
        if (pf.type === 'stock') {
          pfHtml = `<div class="portfolio" onclick="openEdit('${stock.symbol}','${stock.label}',${pf.shares},${pf.cost})">
            <span class="pf-label">我的持仓</span>
            <span class="pf-item">持仓 <span class="pf-val">${pf.shares}股</span></span>
            <span class="pf-item">成本 <span class="pf-val">${csym}${pf.cost.toFixed(2)}</span></span>
            <span class="pf-item">市值 <span class="pf-val">${csym}${pf.mktVal.toFixed(0)}</span></span>
            <span class="pf-pnl ${pCls}">${pSign}${csym}${Math.abs(pf.pnl).toFixed(2)} (${pSign}${Math.abs(pf.pnlPct).toFixed(2)}%)<span class="pf-delay">延迟</span></span>
            <span class="pf-edit-hint">点击调整</span>
          </div>`;
        }
      }

      const srcTag = stock.isRealtime ? '' : `<span class="data-src">截至 ${stock.dataDate||''} 收盘</span>`;

      const descHtml = stock.desc ? `<div class="desc">${stock.desc}</div>` : '';
      card.innerHTML = `
        <button class="card-del" onclick="event.stopPropagation();deleteStock('${stock.symbol}','${stock.label}')" title="删除">✕</button>
        <div class="card-head">
          <div><span class="name">${stock.label}<span class="symbol">${stock.symbol}</span></span>${descHtml}</div>
          <div class="price-block">
            <div class="price ${cls}">${stock.price.toFixed(2)} <span style="font-size:11px;font-weight:400;opacity:0.5">${currency}</span>${srcTag}</div>
            <div class="change ${cls}">${sign}${Math.abs(stock.change).toFixed(2)} (${sign}${Math.abs(stock.changePct).toFixed(2)}%)</div>
          </div>
        </div>
        <div class="chart-box" id="chart-${safeSym}"></div>
        <div class="stats">
          <div class="stat">${pdLabel} <span class="val ${pdCls}">${pdSign}${Math.abs(pd.changePct).toFixed(1)}%</span></div>
          <div class="stat">${pdLabel}高 <span class="val">${pd.high.toFixed(2)}</span></div>
          <div class="stat">${pdLabel}低 <span class="val">${pd.low.toFixed(2)}</span></div>
          <div class="spacer"></div>
          <button class="reset-btn" onclick="openPositionModal('${stock.symbol}','${stock.label}')" title="录入持仓">+</button>
          <button class="reset-btn" onclick="resetChart('${safeSym}')" title="重置缩放">⟲</button>
        </div>
        ${recHtml}
        ${pfHtml}
      `;
      grid.appendChild(card);
      const chartStock = Object.assign({}, stock, { ohlc: effectiveOhlc });
      requestAnimationFrame(() => {
        const el = document.getElementById('chart-' + safeSym);
        if (el) createChart(el, chartStock);
      });
    }
  }

  if (!hasContent) {
    app.innerHTML = `<div class="empty-tab"><div>暂无数据</div><div class="hint">在 server.py 中添加标的后重启服务</div></div>`;
  }
}

// ── Delete ──
async function deleteStock(symbol, label) {
  if (!confirm(`确定删除「${label}」(${symbol})？`)) return;
  try {
    const resp = await fetch('/api/remove-stock', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({symbol}) });
    const json = await resp.json();
    if (json.ok) loadData();
  } catch(e) { alert('删除失败: ' + e.message); }
}

// ── Edit Portfolio ──
let editSymbol = '', editLabel = '', editAction = 'buy';

function openEdit(symbol, label, shares, cost) {
  editSymbol = symbol; editLabel = label; editAction = 'buy';
  document.getElementById('editOverlay').classList.add('show');
  document.getElementById('edit-title').textContent = `调整持仓 · ${label}`;
  document.getElementById('edit-msg').className = 'msg';
  document.querySelectorAll('.action-tab').forEach(t => t.classList.remove('active'));
  document.querySelector('.action-tab').classList.add('active');
  renderEditFields(shares, cost);
}

function closeEdit() { document.getElementById('editOverlay').classList.remove('show'); }

function setEditAction(action) {
  editAction = action;
  document.querySelectorAll('.action-tab').forEach(t => {
    const isActive = t.textContent.includes(action==='buy'?'加仓':action==='sell'?'减仓':'设置');
    t.classList.toggle('active', isActive);
  });
  renderEditFields();
}

function renderEditFields(shares, cost) {
  const f = document.getElementById('edit-fields');
  if (editAction === 'buy') {
    f.innerHTML = `<label>加仓数量（股）</label><input type="number" id="e-shares" placeholder="新买入股数">
      <label>买入价格</label><input type="number" id="e-cost" step="0.01" placeholder="每股买入价">
      <div class="hint">自动计算新的均价</div>`;
  } else if (editAction === 'sell') {
    f.innerHTML = `<label>卖出数量（股）</label><input type="number" id="e-shares" placeholder="卖出股数">
      <div class="hint">卖出后成本价不变，全部卖出则移除持仓</div>`;
  } else {
    f.innerHTML = `<label>总股数</label><input type="number" id="e-shares" placeholder="当前持仓总数" value="${shares||''}">
      <label>成本价</label><input type="number" id="e-cost" step="0.01" placeholder="每股成本" value="${cost||''}">`;
  }
}

async function submitEdit() {
  const msg = document.getElementById('edit-msg');
  const body = { symbol: editSymbol, action: editAction };
  const sharesEl = document.getElementById('e-shares');
  const costEl = document.getElementById('e-cost');
  if (sharesEl) body.shares = parseFloat(sharesEl.value);
  if (costEl) body.cost = parseFloat(costEl.value);

  if (!body.shares && editAction !== 'set') { msg.className='msg err'; msg.textContent='请填写数量'; return; }
  if (editAction === 'buy' && !body.cost) { msg.className='msg err'; msg.textContent='请填写买入价'; return; }

  try {
    const resp = await fetch('/api/update-portfolio', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
    const json = await resp.json();
    if (json.error) { msg.className='msg err'; msg.textContent=json.error; }
    else { msg.className='msg ok'; msg.textContent='已更新'; setTimeout(()=>{ closeEdit(); loadData(); }, 800); }
  } catch(e) { msg.className='msg err'; msg.textContent='请求失败'; }
}

// ── Add Modal logic ──
document.querySelectorAll('.radio-group').forEach(g => {
  g.querySelectorAll('.radio-btn').forEach(btn => {
    btn.onclick = () => {
      g.querySelectorAll('.radio-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      updatePfFields();
    };
  });
});

function getRadioVal(id) {
  const el = document.querySelector(`#${id} .radio-btn.active`);
  return el ? el.dataset.val : '';
}

function updatePfFields() {
  const grp = getRadioVal('m-group');
  document.getElementById('m-pf').classList.toggle('show', grp.includes('持仓'));
}

function openModal() {
  document.getElementById('modalOverlay').classList.add('show');
  document.getElementById('m-symbol').value = '';
  document.getElementById('m-label').value = '';
  document.getElementById('m-desc').value = '';
  document.getElementById('m-shares').value = '';
  document.getElementById('m-cost').value = '';
  document.getElementById('m-msg').className = 'msg';
  document.getElementById('m-msg').textContent = '';
  document.getElementById('m-symbol').focus();
  updatePfFields();
}

function closeModal() {
  document.getElementById('modalOverlay').classList.remove('show');
}

async function submitAdd() {
  const symbol = document.getElementById('m-symbol').value.trim();
  const label = document.getElementById('m-label').value.trim();
  const desc = document.getElementById('m-desc').value.trim();
  const market = getRadioVal('m-market');
  const group = getRadioVal('m-group');
  const msg = document.getElementById('m-msg');

  if (!symbol || !label) {
    msg.className = 'msg err';
    msg.textContent = '请填写代码和名称';
    return;
  }

  const body = { symbol, label, market, group };
  if (desc) body.desc = desc;

  if (group.includes('持仓')) {
    const shares = document.getElementById('m-shares').value;
    const cost = document.getElementById('m-cost').value;
    if (shares) body.shares = parseFloat(shares);
    if (cost) body.cost = parseFloat(cost);
  }

  const btn = document.getElementById('m-submit');
  btn.textContent = '正在验证...';
  btn.disabled = true;
  msg.className = 'msg';
  msg.textContent = '';

  try {
    const resp = await fetch('/api/add-stock', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
    const json = await resp.json();
    if (!resp.ok || json.error) {
      msg.className = 'msg err';
      msg.textContent = json.error || '添加失败，请重试';
      btn.textContent = '添加';
      btn.disabled = false;
      return;
    }

    btn.textContent = '获取数据中...';
    msg.className = 'msg ok';
    msg.textContent = '添加成功！';

    if (json.stock && !json.stock.error && json.tab && json.group) {
      const tabName = json.tab;
      const grpName = json.group;
      if (!TABS_DATA[tabName]) {
        TABS_DATA[tabName] = { currency: '', groups: {} };
        tabNames = ['资讯', ...Object.keys(TABS_DATA)];
        initTabs();
      }
      const grps = TABS_DATA[tabName].groups;
      if (!grps[grpName]) grps[grpName] = [];
      grps[grpName].push(json.stock);

      setTimeout(() => {
        closeModal();
        switchTab(tabName);
      }, 600);
    } else {
      setTimeout(() => {
        closeModal();
        loadData();
      }, 800);
    }
  } catch(e) {
    msg.className = 'msg err';
    msg.textContent = '请求失败：' + e.message;
  }
  btn.textContent = '添加';
  btn.disabled = false;
}

// ── Position Modal ──
let positionSymbol = '', positionLabel = '';

function findStockData(symbol) {
  for (const tabData of Object.values(TABS_DATA)) {
    for (const stocks of Object.values(tabData.groups || {})) {
      for (const s of stocks) {
        if (s.symbol === symbol) return s;
      }
    }
  }
  return null;
}

function openPositionModal(symbol, label) {
  positionSymbol = symbol;
  positionLabel = label;
  document.getElementById('positionOverlay').classList.add('show');
  document.getElementById('pos-title').textContent = '录入持仓 · ' + label;
  document.getElementById('pos-msg').className = 'msg';
  document.getElementById('pos-msg').textContent = '';
  document.getElementById('pos-date').value = new Date().toISOString().slice(0, 10);
  document.getElementById('pos-shares').value = '';
  document.getElementById('pos-cost').value = '';

  const stock = findStockData(symbol);
  const existing = document.getElementById('pos-existing');
  const pf = stock && stock.portfolio;
  if (pf && pf.purchases && pf.purchases.length > 0) {
    const rows = pf.purchases.map(p =>
      '<div class="pos-purchase-row"><span>' + p.date + '</span><span>' + p.shares + '股 × ' + p.cost.toFixed(2) + '</span></div>'
    ).join('');
    existing.innerHTML =
      '<div class="pos-purchases">' +
        '<div class="pos-purchases-title">已有买入记录</div>' +
        '<div class="pos-purchases-list">' + rows +
          '<div class="pos-total"><span>合计</span><span>' + pf.shares + '股, 均价 ' + pf.cost.toFixed(2) + '</span></div>' +
        '</div>' +
      '</div>';
  } else if (pf && pf.shares) {
    existing.innerHTML =
      '<div class="pos-purchases">' +
        '<div class="pos-purchases-title">当前持仓</div>' +
        '<div class="pos-purchases-list">' +
          '<div class="pos-total"><span>合计</span><span>' + pf.shares + '股, 均价 ' + pf.cost.toFixed(2) + '</span></div>' +
        '</div>' +
      '</div>';
  } else {
    existing.innerHTML = '';
  }
}

function closePositionModal() {
  document.getElementById('positionOverlay').classList.remove('show');
}

async function submitPosition() {
  const msg = document.getElementById('pos-msg');
  const date = document.getElementById('pos-date').value;
  const shares = parseFloat(document.getElementById('pos-shares').value);
  const cost = parseFloat(document.getElementById('pos-cost').value);

  if (!shares || shares <= 0) { msg.className = 'msg err'; msg.textContent = '请填写有效的数量'; return; }
  if (!cost || cost <= 0) { msg.className = 'msg err'; msg.textContent = '请填写有效的成本价'; return; }
  if (!date) { msg.className = 'msg err'; msg.textContent = '请选择购买时间'; return; }

  try {
    const resp = await fetch('/api/update-portfolio', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        symbol: positionSymbol,
        action: 'buy',
        shares: shares,
        cost: cost,
        purchase_date: date
      })
    });
    const json = await resp.json();
    if (json.error) {
      msg.className = 'msg err'; msg.textContent = json.error;
    } else {
      msg.className = 'msg ok'; msg.textContent = '持仓已更新';
      setTimeout(function() { closePositionModal(); loadData(); }, 800);
    }
  } catch(e) {
    msg.className = 'msg err'; msg.textContent = '请求失败: ' + e.message;
  }
}

loadData();
</script>
</body>
</html>
'''

# ── ENTRY POINT ─────────────────────────────────────────

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8888))
    print("\n📊 Stock Monitor — Dashboard")
    print(f"   http://localhost:{port}")
    print(f"   数据缓存 {CACHE_TTL}s，刷新页面时按需更新\n")
    app.run(host='0.0.0.0', port=port, debug=False)
