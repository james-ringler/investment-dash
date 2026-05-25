import os
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, jsonify, request
import yfinance as yf
import pandas as pd

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

LOGO_DOMAINS = {
    "AAPL": "apple.com", "AMD": "amd.com", "GOOGL": "alphabet.com",
    "GOOG": "alphabet.com", "DKNG": "draftkings.com", "HOOD": "robinhood.com",
    "FJPCX": "fidelity.com", "DBB": "invesco.com", "REMX": "vaneck.com",
}

def _logo_url(sym, info):
    url = info.get("logo_url", "")
    if url:
        return url
    domain = LOGO_DOMAINS.get(sym)
    return f"https://logo.clearbit.com/{domain}" if domain else ""

HOLDINGS = [
    {"symbol": "AAPL",  "name": "Apple Inc",                   "shares": 980,     "cost": 20800.38, "type": "equity"},
    {"symbol": "AMD",   "name": "Advanced Micro Devices",       "shares": 38,      "cost": 5946.14,  "type": "equity"},
    {"symbol": "GOOGL", "name": "Alphabet Inc Cl A",            "shares": 7,       "cost": 1194.90,  "type": "equity"},
    {"symbol": "GOOG",  "name": "Alphabet Inc Cl C",            "shares": 2,       "cost": 371.88,   "type": "equity"},
    {"symbol": "DKNG",  "name": "DraftKings Inc",               "shares": 150,     "cost": 5985.75,  "type": "equity"},
    {"symbol": "HOOD",  "name": "Robinhood Markets",            "shares": 127,     "cost": 5088.69,  "type": "equity"},
    {"symbol": "FRCB",  "name": "First Republic Bank",          "shares": 37,      "cost": 3040.00,  "type": "equity"},
    {"symbol": "FJPCX", "name": "Fidelity Adv Japan C",         "shares": 382.344, "cost": 6824.79,  "type": "mutual_fund"},
    {"symbol": "DBB",   "name": "Invesco DB Base Metals",       "shares": 410,     "cost": 9995.65,  "type": "etf"},
    {"symbol": "REMX",  "name": "VanEck Rare Earth/Strategic",  "shares": 55,      "cost": 4944.18,  "type": "etf"},
    {"symbol": "BDPS",  "name": "Bank Deposit Program",         "shares": 1,       "cost": 917.57,   "type": "cash"},
]

CHART_SYMBOLS = ["AAPL", "AMD", "GOOGL", "GOOG", "DKNG", "HOOD", "FJPCX", "DBB", "REMX"]
EPS_SYMBOLS   = ["AAPL", "AMD", "GOOGL", "GOOG", "DKNG", "HOOD"]
NEWS_SYMBOLS  = ["AAPL", "AMD", "GOOGL", "DKNG", "HOOD", "DBB", "REMX"]

PERIOD_CFG = {
    "1m":  (30,   "1d"),
    "3m":  (91,   "1d"),
    "6m":  (182,  "1d"),
    "1y":  (365,  "1d"),
    "3y":  (1095, "1wk"),
    "5y":  (1825, "1wk"),
    "all": (4800, "1wk"),
}

BENCH_COLORS = {
    "SPY": "#60a5fa", "BTC-USD": "#f59e0b", "NVDA": "#a78bfa",
    "QQQ": "#f97316", "BTC": "#f59e0b",
}
EXTRA_COLORS = ["#ec4899", "#14b8a6", "#f43f5e", "#8b5cf6", "#06b6d4"]

_cache = {}

def _get(key, fn, ttl=300):
    entry = _cache.get(key)
    if entry and time.time() - entry[1] < ttl:
        return entry[0]
    result = fn()
    _cache[key] = (result, time.time())
    return result

def _safe_price(ticker_obj, info):
    for field in ("currentPrice", "regularMarketPrice", "navPrice", "previousClose"):
        v = info.get(field)
        if v and v > 0:
            return v
    try:
        fi = ticker_obj.fast_info
        v = getattr(fi, "last_price", None) or getattr(fi, "regular_market_price", None)
        if v and v > 0:
            return v
    except Exception:
        pass
    return 0

def _parse_news(item, symbol):
    try:
        content = item.get("content", item)
        title = content.get("title") or item.get("title", "")
        url_obj = content.get("canonicalUrl", {})
        url = (url_obj.get("url") if isinstance(url_obj, dict) else None) or item.get("link", "")
        provider = content.get("provider", {})
        publisher = (provider.get("displayName") if isinstance(provider, dict) else None) or item.get("publisher", "")
        pub = content.get("pubDate") or item.get("providerPublishTime", "")
        if isinstance(pub, (int, float)):
            pub = datetime.fromtimestamp(pub).isoformat()
        if title and url:
            return {"symbol": symbol, "title": title, "url": url, "publisher": publisher or "", "time": str(pub)}
    except Exception:
        pass
    return None

@app.route("/")
def index():
    return render_template("index.html")

def _fetch_one_holding(h):
    if h["type"] == "cash":
        return {**h, "price": 1.0, "market_value": h["cost"],
                "gain": 0, "gain_pct": 0, "today_change": 0, "today_change_pct": 0}
    try:
        t = yf.Ticker(h["symbol"])
        info = t.info
        price = _safe_price(t, info)
        mv = price * h["shares"]
        gain = mv - h["cost"]
        gain_pct = (gain / h["cost"] * 100) if h["cost"] else 0
        chg = info.get("regularMarketChange", 0) or 0
        chg_pct = info.get("regularMarketChangePercent", 0) or 0
        return {**h, "price": round(price, 4), "market_value": round(mv, 2),
                "gain": round(gain, 2), "gain_pct": round(gain_pct, 2),
                "today_change": round(chg * h["shares"], 2),
                "today_change_pct": round(chg_pct, 2),
                "logo_url": _logo_url(h["symbol"], info),
                "sector": info.get("sector", ""),
                "beta": round(float(info.get("beta") or 0), 2),
                "week52_high": info.get("fiftyTwoWeekHigh"),
                "week52_low": info.get("fiftyTwoWeekLow"),
                "dividend_rate": round(float(info.get("dividendRate") or 0), 4)}
    except Exception as e:
        return {**h, "price": 0, "market_value": 0, "gain": -h["cost"],
                "gain_pct": -100, "today_change": 0, "today_change_pct": 0, "error": str(e)}

@app.route("/api/holdings")
def api_holdings():
    def fetch():
        results = [None] * len(HOLDINGS)
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(_fetch_one_holding, h): i for i, h in enumerate(HOLDINGS)}
            for f in as_completed(futures):
                results[futures[f]] = f.result()
        return results
    return jsonify(_get("holdings", fetch, ttl=90))

@app.route("/api/chart")
def api_chart():
    period = request.args.get("period", "1y")
    compare_raw = request.args.get("compare", "SPY,BTC-USD,NVDA")
    compare = [s.strip().upper() for s in compare_raw.split(",") if s.strip()]
    key = f"chart:{period}:{','.join(sorted(compare))}"

    def fetch():
        days, interval = PERIOD_CFG.get(period, (365, "1d"))
        start = datetime.now() - timedelta(days=days)
        result = {}

        shares_map = {h["symbol"]: h["shares"] for h in HOLDINGS if h["symbol"] in CHART_SYMBOLS}
        all_syms = list(shares_map.keys()) + compare

        def _fetch_hist(sym):
            try:
                hist = yf.Ticker(sym).history(start=start, interval=interval)
                if not hist.empty:
                    hist.index = hist.index.tz_localize(None) if hist.index.tz else hist.index
                    return sym, hist["Close"]
            except Exception:
                pass
            return sym, None

        raw = {}
        with ThreadPoolExecutor(max_workers=10) as ex:
            for sym, series in ex.map(_fetch_hist, all_syms):
                if series is not None:
                    raw[sym] = series

        port_series = {sym: raw[sym] * shares for sym, shares in shares_map.items() if sym in raw}
        if port_series:
            port_df = pd.DataFrame(port_series).ffill()
            port_total = port_df.sum(axis=1).dropna()
            if not port_total.empty:
                first = port_total.iloc[0]
                result["Portfolio"] = {
                    "dates": port_total.index.strftime("%Y-%m-%d").tolist(),
                    "values": (port_total / first * 100).round(2).tolist(),
                    "color": "#4ade80",
                }

        cidx = 0
        for sym in compare:
            if sym not in raw:
                continue
            s = raw[sym]
            first = s.iloc[0]
            label = sym.replace("-USD", "").replace("^", "")
            color = BENCH_COLORS.get(sym, EXTRA_COLORS[cidx % len(EXTRA_COLORS)])
            if sym not in BENCH_COLORS:
                cidx += 1
            result[label] = {
                "dates": s.index.strftime("%Y-%m-%d").tolist(),
                "values": (s / first * 100).round(2).tolist(),
                "color": color,
            }

        return result

    return jsonify(_get(key, fetch, ttl=300))

@app.route("/api/eps")
def api_eps():
    def _fetch_eps(sym):
        try:
            info = yf.Ticker(sym).info
            return {"symbol": sym, "name": info.get("shortName", sym),
                    "logo_url": _logo_url(sym, info),
                    "price": info.get("currentPrice") or info.get("regularMarketPrice"),
                    "trailing_eps": info.get("trailingEps"), "forward_eps": info.get("forwardEps"),
                    "trailing_pe": info.get("trailingPE"), "forward_pe": info.get("forwardPE"),
                    "peg_ratio": info.get("pegRatio"), "revenue_growth": info.get("revenueGrowth"),
                    "earnings_growth": info.get("earningsGrowth")}
        except Exception:
            return {"symbol": sym, "name": sym, "error": True}

    def fetch():
        all_syms = EPS_SYMBOLS + ["SPY"]
        with ThreadPoolExecutor(max_workers=7) as ex:
            all_results = list(ex.map(_fetch_eps, all_syms))
        spy = all_results[-1]
        return {
            "holdings": all_results[:-1],
            "market_pe": {"trailing": spy.get("trailing_pe"), "forward": spy.get("forward_pe")},
        }
    return jsonify(_get("eps", fetch, ttl=3600))

@app.route("/api/news")
def api_news():
    def fetch():
        all_news, seen = [], set()
        for sym in NEWS_SYMBOLS:
            try:
                items = yf.Ticker(sym).news or []
                for item in items[:10]:
                    parsed = _parse_news(item, sym)
                    if parsed and parsed["url"] not in seen:
                        seen.add(parsed["url"])
                        all_news.append(parsed)
            except Exception:
                pass
        all_news.sort(key=lambda x: x.get("time", ""), reverse=True)
        return all_news[:60]
    return jsonify(_get("news", fetch, ttl=1800))

@app.route("/api/calendar")
def api_calendar():
    def _fetch_cal(sym):
        try:
            cal = yf.Ticker(sym).calendar
            if not cal:
                return None
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date", [])
                if not dates:
                    return None
                d = dates[0] if isinstance(dates, (list, tuple)) else dates
                if hasattr(d, "strftime"):
                    return {"symbol": sym, "date": d.strftime("%Y-%m-%d")}
                return {"symbol": sym, "date": str(d)[:10]}
        except Exception:
            pass
        return None

    def fetch():
        with ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(_fetch_cal, EPS_SYMBOLS))
        items = [r for r in results if r]
        items.sort(key=lambda x: x["date"])
        return items

    return jsonify(_get("calendar", fetch, ttl=3600))

@app.route("/api/research")
def api_research():
    def fetch():
        results = []
        for sym in EPS_SYMBOLS:
            try:
                t = yf.Ticker(sym)
                info = t.info

                # Recent upgrades/downgrades
                recent_recs = []
                try:
                    ud = t.upgrades_downgrades
                    if ud is not None and not ud.empty:
                        ud = ud.sort_index(ascending=False).head(5)
                        ud.index = ud.index.tz_localize(None) if ud.index.tz else ud.index
                        for idx, row in ud.iterrows():
                            recent_recs.append({
                                "date": idx.strftime("%Y-%m-%d"),
                                "firm": row.get("Firm", ""),
                                "to_grade": row.get("ToGrade", ""),
                                "action": row.get("Action", ""),
                            })
                except Exception:
                    pass

                price = info.get("currentPrice") or info.get("regularMarketPrice")
                target = info.get("targetMeanPrice")
                results.append({
                    "symbol": sym,
                    "name": info.get("shortName", sym),
                    "logo_url": _logo_url(sym, info),
                    "price": price,
                    "target_mean": target,
                    "target_high": info.get("targetHighPrice"),
                    "target_low": info.get("targetLowPrice"),
                    "upside": (target - price) / price * 100 if target and price else None,
                    "recommendation": info.get("recommendationKey", "").replace("_", " ").title(),
                    "num_analysts": info.get("numberOfAnalystOpinions"),
                    "recent_recs": recent_recs,
                })
            except Exception:
                results.append({"symbol": sym, "error": True})
        return results
    return jsonify(_get("research", fetch, ttl=3600))

@app.route("/api/quote/<symbol>")
def api_quote(symbol):
    sym = symbol.upper()
    def fetch():
        try:
            info = yf.Ticker(sym).info
            return {"symbol": sym, "name": info.get("shortName", sym),
                    "price": info.get("currentPrice") or info.get("regularMarketPrice"),
                    "change_pct": info.get("regularMarketChangePercent")}
        except Exception as e:
            return {"error": str(e)}
    return jsonify(_get(f"quote:{sym}", fetch, ttl=60))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5051))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_ENV") != "production")
