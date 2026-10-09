#!/usr/bin/env python3
"""Fetch public Google News RSS results and maintain a static JSON history."""
import json, re, hashlib, time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LATEST = DATA / "latest.json"
HISTORY = DATA / "history.json"
BEIJING = timezone(timedelta(hours=8))
QUERIES = [
    ('国家队官方','("中央汇金" OR "证金公司") (增持 OR 减持 OR 持仓 OR 公告)'),
    ('宽基ETF','(沪深300ETF OR 上证50ETF OR 中证500ETF OR 中证1000ETF) (份额 OR 申购 OR 赎回 OR 资金)'),
    ('国家队市场','("国家队" OR "中央汇金") (A股 OR ETF OR 股市)'),
    ('科技板块','(半导体 OR 存储芯片 OR AI算力) (国家队 OR ETF OR 资金 OR 机构持仓)')
]
DOMAINS = [
    ('证监会','site:csrc.gov.cn ("中央汇金" OR "证金公司" OR ETF)'),
    ('上交所','site:sse.com.cn ("中央汇金" OR ETF 份额)'),
    ('深交所','site:szse.cn ("中央汇金" OR ETF 份额)'),
    ('巨潮资讯','site:cninfo.com.cn ("中央汇金" OR 机构持仓)')
]
UA = 'Mozilla/5.0 (compatible; PublicMarketMonitor/1.0; +https://github.com/)'

def parse_date(s):
    if not s: return None
    try:
        d = parsedate_to_datetime(s)
        if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).isoformat()
    except Exception: return None

def clean(s):
    return re.sub(r'\s+', ' ', (s or '')).strip()

def rss_query(q):
    url = 'https://news.google.com/rss/search?q=' + quote(q) + '&hl=zh-CN&gl=CN&ceid=CN:zh-Hans'
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=25) as response:
        raw = response.read()
    root = ET.fromstring(raw)
    found = []
    for node in root.findall('./channel/item'):
        title = clean(node.findtext('title'))
        link = clean(node.findtext('link'))
        summary = clean(re.sub('<[^>]+>', ' ', node.findtext('description') or ''))
        source_node = node.find('source')
        source = clean(source_node.text if source_node is not None else '')
        published = parse_date(node.findtext('pubDate'))
        if not title or not link: continue
        key = hashlib.sha256((title.lower() + '|' + link.split('&')[0]).encode()).hexdigest()[:20]
        found.append({'id':key,'title':title,'url':link,'summary':summary[:1200],'source':source,'published':published,'query':q})
    return found

def classify(item):
    text = (item.get('title','')+' '+item.get('summary','')+' '+item.get('source','')).lower()
    official = any(x in item.get('url','').lower() for x in ['csrc.gov.cn','sse.com.cn','szse.cn','cninfo.com.cn','huijin-inv.cn'])
    tech = bool(re.search(r'半导体|存储|芯片|ai算力|人工智能|科技股|英伟达|兆易创新|佰维|海光|寒武纪', text, re.I))
    etf = bool(re.search(r'etf|沪深300|上证50|中证500|中证1000|份额|申购|赎回', text, re.I))
    research = bool(re.search(r'研报|研究报告|测算|机构观点|分析师', text))
    direct = official and bool(re.search(r'增持|减持|持有|持仓|股份|公告|披露', text))
    if official: category = 'official'
    elif tech: category = 'tech'
    elif etf: category = 'etf'
    elif research: category = 'research'
    else: category = 'news'
    if re.search(r'减持|卖出|赎回|下降|减少', item.get('title','')+' '+item.get('summary','')): direction = '出场线索'
    elif re.search(r'增持|买入|申购|增加|增持计划', item.get('title','')+' '+item.get('summary','')): direction = '进场线索'
    else: direction = '中性线索'
    item.update({'category':category,'tech':tech,'etf_related':etf,'direct_evidence_candidate':direct,'direction':direction})
    return item


# ETF shares history: best-effort public data-center query. Do not infer shares from price/volume/AUM.
ETF_LIST = [
    ("588000", "科创50ETF 华夏", "科创50"),
    ("588200", "科创芯片ETF 嘉实", "科创芯片"),
    ("588170", "科创半导体ETF 华夏", "科创半导体"),
    ("588760", "科创人工智能ETF 广发", "科创人工智能"),
    ("588050", "科创50ETF 工银", "科创50"),
    ("159819", "人工智能ETF 易方达", "人工智能"),
    ("159995", "芯片ETF 国泰", "芯片"),
    ("159801", "芯片ETF 广发", "芯片"),
    ("159852", "软件ETF 嘉实", "软件"),
    ("159915", "创业板ETF 易方达", "创业板科技成长"),
]

def _eastmoney_json(url):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'Referer': 'https://data.eastmoney.com/',
        'Accept': 'application/json, text/plain, */*'
    })
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode('utf-8', errors='replace'))

def _extract_rows(payload):
    result = payload.get('result') or payload.get('data') or {}
    if isinstance(result, dict):
        rows = result.get('data') or result.get('list') or []
    elif isinstance(result, list):
        rows = result
    else:
        rows = []
    return rows if isinstance(rows, list) else []

def _first_field(row, names):
    for name in names:
        if name in row and row[name] not in (None, ''):
            return row[name]
    return None

def _normal_date(value):
    if not value: return None
    s = str(value)[:10]
    return s if re.match(r'20\d{2}-\d{2}-\d{2}', s) else None

def _normal_number(value):
    try:
        if value is None: return None
        return float(str(value).replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def update_etf_share_history():
    """Fetch ETF shares from official SSE/SZSE data via AKShare."""
    import akshare as ak

    path = DATA / "etf_shares.json"
    try:
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        old = {}

    today = datetime.now(timezone.utc).date()
    target = today - timedelta(days=365)
    warnings = []
    funds = []

    # Normalize exchange results into (date, shares) records.
    def parse_rows(df, code, date_col, code_col, shares_col, multiplier=1):
        result = []
        if df is None or df.empty:
            return result
        for _, row in df.iterrows():
            try:
                fund_code = str(row[code_col]).strip().zfill(6)
                if fund_code != code:
                    continue
                d = _normal_date(row[date_col])
                shares = _normal_number(row[shares_col])
                if d and shares is not None and shares > 0:
                    result.append((d, float(shares) * multiplier))
            except Exception:
                continue
        return result

    # SSE returns all listed ETF share amounts for one specified date.
    sse_cache = {}

    def sse_on_date(day, code):
        key = day.strftime("%Y%m%d")
        if key not in sse_cache:
            try:
                sse_cache[key] = ak.fund_etf_scale_sse(date=key)
            except Exception as exc:
                warnings.append(f"SSE {key}: {type(exc).__name__}")
                sse_cache[key] = None
        df = sse_cache[key]
        if df is None or df.empty:
            return []
        return parse_rows(
            df, code, "统计日期", "基金代码", "基金份额",
            multiplier=1
        )

    # SZSE can return all ETF records over a date interval.
    szse_cache = {}

    def szse_range(start, end, code):
        key = (start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        if key not in szse_cache:
            try:
                szse_cache[key] = ak.fund_scale_daily_szse(
                    start_date=key[0],
                    end_date=key[1],
                    symbol="ETF"
                )
            except Exception as exc:
                warnings.append(f"SZSE {key[0]}-{key[1]}: {type(exc).__name__}")
                szse_cache[key] = None
        df = szse_cache[key]
        if df is None or df.empty:
            return []
        return parse_rows(df, code, "日期", "基金代码", "基金份额")

    def find_sse(code, around, backwards_days=20):
        for offset in range(backwards_days + 1):
            day = around - timedelta(days=offset)
            rows = sse_on_date(day, code)
            if rows:
                return sorted(rows)[-1]
        return None

    def find_szse(code, around, backwards_days=20):
        start = around - timedelta(days=backwards_days)
        end = around
        rows = szse_range(start, end, code)
        eligible = [x for x in rows if x[0] <= around.isoformat()]
        return max(eligible, key=lambda x: x[0]) if eligible else None

    for code, name, index_name in ETF_LIST:
        is_sse = code.startswith(("5", "6"))
        try:
            if is_sse:
                latest = find_sse(code, today)
                prior = find_sse(code, target)
            else:
                latest = find_szse(code, today)
                prior = find_szse(code, target)

            if latest and prior and prior[1] > 0:
                delta = latest[1] - prior[1]
                pct = delta / prior[1] * 100
                status, label = "ok", "已取得历史份额"
            elif latest:
                delta, pct = None, None
                status, label = "partial", "历史区间不足"
            else:
                delta, pct = None, None
                status, label = "missing", "接口未返回可验证份额"

            funds.append({
                "code": code,
                "name": name,
                "index": index_name,
                "latest_date": latest[0] if latest else None,
                "year_ago_date": prior[0] if prior else None,
                "year_ago_shares": prior[1] if prior else None,
                "latest_shares": latest[1] if latest else None,
                "change_shares": delta,
                "change_pct": pct,
                "status": status,
                "status_label": label,
                "source_report": "SSE official via AKShare" if is_sse
                    else "SZSE official via AKShare"
            })
        except Exception as exc:
            warnings.append(f"{code}: {type(exc).__name__}: {exc}")
            funds.append({
                "code": code,
                "name": name,
                "index": index_name,
                "latest_date": None,
                "year_ago_date": None,
                "year_ago_shares": None,
                "latest_shares": None,
                "change_shares": None,
                "change_pct": None,
                "status": "missing",
                "status_label": "采集失败，请检查日志",
                "source_report": None
            })

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Shanghai and Shenzhen Stock Exchange public ETF share data via AKShare",
        "period_days": 365,
        "note": "份额变化率以约一年前最近可用交易日为基准；缺失数据不会用成交额或基金规模替代。",
        "fetch_warnings": warnings[:80],
        "funds": funds
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    ok_count = sum(1 for item in funds if item["status"] == "ok")
    print(f"ETF shares history: {ok_count}/{len(funds)} funds have valid one-year share comparisons.")


# Eastmoney market-flow estimates (not actual trades of Central Huijin or other state funds).
FLOW_INDICES = [('上证指数','1.000001'),('深证成指','0.399001'),('创业板指','0.399006')]
def update_market_flow():
    """Fetch market-flow estimates from Eastmoney with browser-like headers and safe diagnostics."""
    import urllib.parse
    path = DATA / 'market_flow.json'
    output = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'source': '东方财富公开行情接口（估算数据；需在线验证）',
        'unit': '亿元人民币',
        'note': '主力资金流向是行情供应商按成交规则估算，不是国家队账户交易；近5/20日为可用交易日之和。',
        'indices': [], 'warnings': []
    }

    # Eastmoney's push2his endpoint may disconnect requests without a browser-like
    # User-Agent/Origin/Referer. Try the historical endpoint first, then the
    # alternate push2 endpoint. Do not fabricate values if both fail.
    endpoints = [
        'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?',
        'https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get?'
    ]
    for name, secid in FLOW_INDICES:
        record = {
            'name': name, 'secid': secid, 'date': None, 'day': None,
            'days5': None, 'days20': None, 'signal': '数据不足',
            'status': 'missing'
        }
        params = {
            'secid': secid, 'lmt': '30', 'klt': '101',
            'fields1': 'f1,f2,f3,f7',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63',
            'ut': '7eea3edcaed734bea9cbfc24409ed989',
            '_': str(int(time.time() * 1000))
        }
        errors = []
        for base in endpoints:
            url = base + urllib.parse.urlencode(params)
            try:
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
                    'Referer': 'https://data.eastmoney.com/zjlx/',
                    'Origin': 'https://data.eastmoney.com',
                    'Accept': 'application/json, text/plain, */*',
                    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                    'Connection': 'close'
                })
                with urllib.request.urlopen(req, timeout=20) as response:
                    body = response.read().decode('utf-8', errors='replace').strip()
                if not body:
                    raise ValueError('empty response')
                data = json.loads(body)
                raw = (data.get('data') or {}).get('klines') or []
                series = []
                for line in raw:
                    fields = str(line).split(',')
                    if len(fields) < 2:
                        continue
                    d = _normal_date(fields[0])
                    v = _normal_number(fields[1])
                    if d and v is not None:
                        series.append((d, v))
                series.sort()
                if not series:
                    errors.append(f'{base.split("/")[2]}: no klines')
                    continue
                record.update(
                    date=series[-1][0],
                    day=round(series[-1][1] / 1e8, 3),
                    days5=round(sum(v for _, v in series[-5:]) / 1e8, 3) if len(series) >= 5 else None,
                    days20=round(sum(v for _, v in series[-20:]) / 1e8, 3) if len(series) >= 20 else None,
                    status='ok' if len(series) >= 20 else 'partial'
                )
                if record['days5'] is not None and record['days20'] is not None:
                    a, b = record['days5'], record['days20']
                    record['signal'] = '偏利好' if a > 0 and b > 0 else '偏利空' if a < 0 and b < 0 else '资金分歧 / 中性'
                break
            except Exception as exc:
                errors.append(f'{base.split("/")[2]}: {type(exc).__name__}: {str(exc)[:120]}')
            time.sleep(0.5)
        if record['status'] == 'missing':
            output['warnings'].append(f'{name}: ' + ' | '.join(errors))
        output['indices'].append(record)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Market flow valid:', sum(x['status'] != 'missing' for x in output['indices']), '/', len(output['indices']))

def main():
    DATA.mkdir(exist_ok=True)
    all_items = {}
    failures = []
    for label, query in QUERIES + DOMAINS:
        try:
            for item in rss_query(query):
                item['query_group'] = label
                all_items[item['id']] = classify(item)
            time.sleep(1.0)
        except Exception as exc:
            failures.append(f'{label}: {type(exc).__name__}: {exc}')
            print(f'WARNING {failures[-1]}')
    old = {}
    if LATEST.exists():
        try:
            old = json.loads(LATEST.read_text(encoding='utf-8'))
        except Exception: pass
    # Preserve older items up to 180 days so the static site has a modest searchable archive.
    for item in old.get('items', []):
        if item.get('id') and item['id'] not in all_items:
            all_items[item['id']] = item
    cutoff = datetime.now(timezone.utc) - timedelta(days=180)
    def recent_enough(x):
        dt = x.get('published')
        if not dt: return True
        try: return datetime.fromisoformat(dt.replace('Z','+00:00')) >= cutoff
        except Exception: return True
    items = [x for x in all_items.values() if recent_enough(x)]
    items.sort(key=lambda x: x.get('published') or '', reverse=True)
    now = datetime.now(timezone.utc)
    last7 = []
    for x in items:
        try:
            d = datetime.fromisoformat((x.get('published') or '').replace('Z','+00:00'))
            if now-d <= timedelta(days=7): last7.append(x)
        except Exception: pass
    score = sum(1 if x['direction']=='进场线索' else -1 if x['direction']=='出场线索' else 0 for x in last7)
    if len(last7) >= 5 and score >= 3: signal = '新闻线索偏向进场（待验证）'
    elif len(last7) >= 5 and score <= -3: signal = '新闻线索偏向出场（待验证）'
    elif len(last7) >= 3: signal = '信号混合 / 等待验证'
    else: signal = '证据不足，无法确认'
    history = []
    if HISTORY.exists():
        try: history = json.loads(HISTORY.read_text(encoding='utf-8'))
        except Exception: pass
    history.append({
        'date': now.astimezone(BEIJING).date().isoformat(),
        'updated_at': now.isoformat(),
        'count': len(last7),
        'direct_count': sum(1 for x in last7 if x.get('direct_evidence_candidate')),
        'tech_count': sum(1 for x in last7 if x.get('tech')),
        'signal': signal
    })
    # One snapshot per China date; reruns replace that day's snapshot.
    by_date = {h['date']:h for h in history if h.get('date')}
    by_date[history[-1]['date']] = history[-1]
    history = sorted(by_date.values(), key=lambda h:h['date'])[-365:]
    payload = {'updated_at':now.isoformat(),'source':'Google News RSS public search results','update_interval_hours':6,'items':items[:1500],'history':history,'fetch_warnings':failures}
    LATEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    HISTORY.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding='utf-8')
    update_etf_share_history()
    update_market_flow()
    print(f"Updated {LATEST}: {len(items)} items, {len(last7)} in last 7 days, history {len(history)} snapshots.")
    print('Signal:', signal)
    if failures: print(f'{len(failures)} source query failures; see log above.')

if __name__ == '__main__':
    main()
