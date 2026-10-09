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
    print(f"Updated {LATEST}: {len(items)} items, {len(last7)} in last 7 days, history {len(history)} snapshots.")
    print('Signal:', signal)
    if failures: print(f'{len(failures)} source query failures; see log above.')

if __name__ == '__main__':
    main()
