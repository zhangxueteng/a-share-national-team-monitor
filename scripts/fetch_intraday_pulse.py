#!/usr/bin/env python3
"""Lightweight intraday ETF pulse collector using public Eastmoney quote data.

This is a market-activity proxy, not a feed of Central Huijin's actual trades.
"""
import json
import statistics
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT = DATA_DIR / "intraday_pulse.json"
CN = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (compatible; AShareIntradayPulse/1.0)"

# Broad-market ETFs and tech ETFs. Category is a monitoring group, not a claim
# that any of these funds is held or traded by Central Huijin.
ETF_LIST = [
    ("510300", "沪深300ETF 华泰柏瑞", "宽基"),
    ("510310", "沪深300ETF 易方达", "宽基"),
    ("510050", "上证50ETF 华夏", "宽基"),
    ("510500", "中证500ETF 南方", "宽基"),
    ("512100", "中证1000ETF 南方", "宽基"),
    ("159919", "沪深300ETF 嘉实", "宽基"),
    ("159915", "创业板ETF 易方达", "科技"),
    ("159949", "创业板50ETF 华夏", "科技"),
    ("588000", "科创50ETF 华夏", "科技"),
    ("588080", "科创板50ETF 易方达", "科技"),
    ("588200", "科创芯片ETF 嘉实", "科技"),
    ("588170", "科创半导体ETF 华夏", "科技"),
    ("159995", "芯片ETF 国泰", "科技"),
    ("159819", "人工智能ETF 易方达", "科技"),
    ("159801", "芯片ETF 广发", "科技"),
    ("159852", "软件ETF 嘉实", "科技"),
    ("512480", "半导体ETF 国联安", "科技"),
    ("159845", "中证1000ETF 华夏", "宽基"),
]

def local_now():
    return datetime.now(CN)

def is_market_session(now):
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return (9 * 60 + 25 <= t <= 11 * 60 + 30) or (13 * 60 <= t <= 15 * 60)

def load_old():
    try:
        obj = json.loads(OUT.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return {"snapshots": []}

def fetch_quotes():
    secids = []
    for code, _, _ in ETF_LIST:
        market = "1" if code.startswith(("5", "6")) else "0"
        secids.append(f"{market}.{code}")
    params = {
        "fltt": "2", "invt": "2",
        "fields": "f12,f13,f14,f2,f3,f4,f5,f6,f15,f16,f17,f18",
        "secids": ",".join(secids),
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
    }
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "application/json, text/plain, */*",
    })
    with urllib.request.urlopen(req, timeout=18) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    data = payload.get("data") or {}
    rows = data.get("diff") or []
    if not isinstance(rows, list) or len(rows) < 5:
        raise RuntimeError(f"quote endpoint returned too few rows: {len(rows) if isinstance(rows, list) else 'invalid'}")
    parsed = {}
    for row in rows:
        code = str(row.get("f12", "")).zfill(6)
        if code not in {x[0] for x in ETF_LIST}:
            continue
        def num(field, signed=False):
            try:
                v = float(row.get(field))
                return v if signed or v >= 0 else None
            except (TypeError, ValueError):
                return None
        raw_price = num("f2")
        raw_prev = num("f18")
        raw_change = num("f3", signed=True)
        raw_amount = num("f6")
        if raw_price is None or raw_price <= 0:
            continue
        parsed[code] = {
            "code": code,
            "name": str(row.get("f14") or next(x[1] for x in ETF_LIST if x[0] == code)),
            "category": next(x[2] for x in ETF_LIST if x[0] == code),
            "price": round(raw_price / 1000, 4),
            "change_pct": round(raw_change / 100, 3) if raw_change is not None else None,
            "prev_close": round(raw_prev / 1000, 4) if raw_prev else None,
            "amount_yi": round(raw_amount / 100000000, 4) if raw_amount is not None else None,
        }
    if len(parsed) < 5:
        raise RuntimeError(f"parsed only {len(parsed)} usable ETF quotes")
    return parsed

def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    old = load_old()
    snapshots = old.get("snapshots", [])
    if not isinstance(snapshots, list):
        snapshots = []
    now = local_now()
    stamp = now.isoformat(timespec="seconds")
    session = is_market_session(now)

    # Outside trading sessions, preserve the last good quote snapshot and only
    # update collection status; this avoids treating unchanged prices as signals.
    if not session:
        old["status"] = "market_closed"
        old["status_label"] = "非交易时段，保留最近有效行情"
        old["checked_at"] = stamp
        old["source"] = "Eastmoney public quote endpoint; no direct state-fund transaction data"
        OUT.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Outside A-share trading session; preserved the latest valid snapshot.")
        return

    quotes = fetch_quotes()
    current = {
        "timestamp": stamp,
        "date": now.date().isoformat(),
        "funds": quotes,
    }

    # Compare cumulative turnover with the previous snapshot and about one hour
    # earlier. Only compare snapshots from the same China trading date.
    same_day = [s for s in snapshots if isinstance(s, dict) and s.get("date") == current["date"] and isinstance(s.get("funds"), dict)]
    previous = same_day[-1] if same_day else None
    one_hour_ago = same_day[-4] if len(same_day) >= 4 else (same_day[0] if same_day else None)

    for code, q in quotes.items():
        prev_q = (previous or {}).get("funds", {}).get(code, {})
        hour_q = (one_hour_ago or {}).get("funds", {}).get(code, {})
        prev_amount = prev_q.get("amount_yi")
        hour_amount = hour_q.get("amount_yi")
        interval_minutes = None
        if previous and previous.get("timestamp"):
            try:
                prev_time = datetime.fromisoformat(previous["timestamp"])
                interval_minutes = (now - prev_time).total_seconds() / 60
            except (TypeError, ValueError):
                pass
        # If GitHub delayed the run or the previous observation was before lunch,
        # do not mislabel a long interval as a normal short-interval spike.
        interval_ok = interval_minutes is not None and 0 < interval_minutes <= 35
        q["amount_delta_interval_yi"] = round(max(0, q["amount_yi"] - prev_amount), 4) if interval_ok and q.get("amount_yi") is not None and prev_amount is not None else None
        q["amount_delta_hour_yi"] = round(max(0, q["amount_yi"] - hour_amount), 4) if q.get("amount_yi") is not None and hour_amount is not None else None
        prev_price = prev_q.get("price")
        q["change_interval_pct"] = round((q["price"] / prev_price - 1) * 100, 3) if prev_price else None

        # Build a baseline from recent observed interval turnover increments.
        deltas = []
        for s in same_day[-9:]:
            old_q = (s.get("funds") or {}).get(code, {})
            d = old_q.get("amount_delta_interval_yi")
            if isinstance(d, (int, float)) and d >= 0:
                deltas.append(float(d))
        baseline = statistics.median(deltas) if deltas else None
        threshold = 0.5 if q["category"] == "宽基" else 0.15
        q["turnover_baseline_yi"] = round(baseline, 4) if baseline is not None else None
        q["turnover_spike"] = bool(
            interval_ok
            and q.get("amount_delta_interval_yi") is not None
            and q.get("amount_delta_interval_yi", 0) >= threshold
            and (baseline is None or q["amount_delta_interval_yi"] >= max(threshold, baseline * 2.5))
        )
        q["interval_minutes"] = round(interval_minutes, 1) if interval_ok else None

    snapshots.append(current)
    snapshots = snapshots[-800:]
    spike_funds = [q for q in quotes.values() if q.get("turnover_spike")]
    broad = [q for q in quotes.values() if q["category"] == "宽基"]
    broad_spikes = sum(bool(q.get("turnover_spike")) for q in broad)
    tech_spikes = sum(bool(q.get("turnover_spike")) for q in quotes.values() if q["category"] == "科技")
    if broad_spikes >= 3:
        signal = "宽基 ETF 同步异常放量"
        signal_level = "watch"
        reason = "多只宽基 ETF 的区间成交额高于自身近期基线；只能视为大资金活动线索，不能确认买方身份或净买入。"
    elif tech_spikes >= 3:
        signal = "科技 ETF 出现同步放量"
        signal_level = "watch"
        reason = "多只科技 ETF 的区间成交额高于自身近期基线；需结合价格、份额变化及后续走势核验。"
    elif spike_funds:
        signal = "个别 ETF 成交额异动"
        signal_level = "mild"
        reason = "仅部分 ETF 出现成交额异动，证据较弱，不能推断为汇金操作。"
    else:
        signal = "暂未发现同步异常放量"
        signal_level = "neutral"
        reason = "未达到当前观察阈值；阈值只用于筛查，不代表没有大资金交易。"

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "snapshot_timestamp": stamp,
        "status": "ok",
        "status_label": "交易时段行情已更新",
        "source": "Eastmoney public ETF quotes",
        "fund_count": len(quotes),
        "signal": signal,
        "signal_level": signal_level,
        "signal_reason": reason,
        "broad_spikes": broad_spikes,
        "tech_spikes": tech_spikes,
        "spike_count": len(spike_funds),
        "spike_funds": sorted(spike_funds, key=lambda q: q.get("amount_delta_interval_yi") or 0, reverse=True),
        "funds": sorted(quotes.values(), key=lambda q: (q["category"] != "宽基", q["code"])),
        "snapshots": snapshots,
        "note": "成交额是行情供应商数据；ETF成交额不是申购净流入，更不能识别中央汇金交易。每15分钟的定时任务可能延迟，所有信号均为线索而非确认。",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Intraday pulse updated: {len(quotes)} funds; spikes={len(spike_funds)}; signal={signal}")

if __name__ == "__main__":
    main()
