#!/usr/bin/env python3
import os, sys, json, time, logging
from datetime import datetime, timezone
from pathlib import Path
import requests
from dateutil import parser as dateparser

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("aggregator")

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "03bbe5b69f134d26b0d900115afbb6ca")
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

REPO_QUERIES = [
    "clash subscription nodes free in:readme,description",
    "v2ray free nodes subscribe in:readme,description",
    "shadowsocks free nodes list in:readme,description",
    "trojan free proxy nodes in:readme,description",
    "hysteria2 free nodes in:readme,description",
    "xray free nodes subscribe in:readme,description",
    "免费节点 订阅 clash in:readme,description",
    "free proxy list v2ray clash in:readme,description",
]
TYPE_MAP = {"clash":"Clash","v2ray":"V2Ray","xray":"V2Ray","shadowsocks":"Shadowsocks",
            "trojan":"Trojan","hysteria":"Hysteria","vmess":"V2Ray","vless":"V2Ray"}
TYPE_EMOJI = {"Clash":"⚡","V2Ray":"🔵","Shadowsocks":"🟣","Trojan":"🔴","Hysteria":"🟠","Other":"⚪"}

def gh_headers():
    h = {"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2022-11-28"}
    if GITHUB_TOKEN: h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h

def search_repos(query, n=10):
    try:
        r = requests.get("https://api.github.com/search/repositories",
            headers=gh_headers(), params={"q":query,"sort":"updated","order":"desc","per_page":n}, timeout=15)
        r.raise_for_status(); return r.json().get("items",[])
    except Exception as e:
        log.warning(f"搜索失败: {e}"); return []

def detect_type(repo):
    text = " ".join([repo.get("name",""), repo.get("description","") or "", " ".join(repo.get("topics",[]))]).lower()
    for kw, t in TYPE_MAP.items():
        if kw in text: return t
    return "Other"

def collect_all_repos():
    seen, repos = set(), []
    for i, q in enumerate(REPO_QUERIES):
        log.info(f"[{i+1}/{len(REPO_QUERIES)}] {q[:60]}")
        for item in search_repos(q):
            if item["id"] in seen: continue
            seen.add(item["id"])
            updated = item.get("updated_at","")
            try: updated_str = dateparser.parse(updated).strftime("%Y-%m-%d") if updated else "未知"
            except: updated_str = updated[:10] if updated else "未知"
            raw = f"https://raw.githubusercontent.com/{item['full_name']}/{item.get('default_branch','main')}"
            repos.append({"name":item["full_name"],"url":item["html_url"],
                "description":(item.get("description") or "")[:120],"type":detect_type(item),
                "stars":item.get("stargazers_count",0),"updated":updated_str,
                "subscribe_hint":f"{raw}/sub.txt","topics":item.get("topics",[])})
        if i < len(REPO_QUERIES)-1: time.sleep(1.5)
    repos.sort(key=lambda x: x["stars"], reverse=True)
    log.info(f"共 {len(repos)} 个项目"); return repos

def categorize(repos):
    cats = {}
    for r in repos: cats.setdefault(r["type"],[]).append(r)
    return cats

def build_report(repos):
    now = datetime.now(timezone.utc)
    cats = categorize(repos)
    return {"title":f"GitHub 节点聚合报告 {now.strftime('%Y-%m-%d')}","collected_at":now.isoformat(),
            "total":len(repos),"categories":{k:len(v) for k,v in cats.items()},
            "repos":repos,"repos_by_type":cats}

def notion_headers():
    return {"Authorization":f"Bearer {NOTION_TOKEN}","Content-Type":"application/json","Notion-Version":"2022-06-28"}

def _t(s, bold=False, color="default"):
    t = {"type":"text","text":{"content":s[:2000]}}
    if bold or color!="default":
        t["annotations"] = {}
        if bold: t["annotations"]["bold"]=True
        if color!="default": t["annotations"]["color"]=color
    return t

def _h(lvl,txt): k=f"heading_{lvl}"; return {"object":"block","type":k,k:{"rich_text":[_t(txt)]}}
def _p(txt,**kw): return {"object":"block","type":"paragraph","paragraph":{"rich_text":[_t(txt,**kw)]}}
def _div(): return {"object":"block","type":"divider","divider":{}}
def _bullet(txt, children=None):
    b = {"object":"block","type":"bulleted_list_item","bulleted_list_item":{"rich_text":[_t(txt)]}}
    if children: b["bulleted_list_item"]["children"]=children
    return b
def _callout(txt,emoji="📋"):
    return {"object":"block","type":"callout","callout":{"rich_text":[_t(txt)],"icon":{"type":"emoji","emoji":emoji},"color":"gray_background"}}

def build_blocks(report):
    blocks, now_str = [], datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    cats_str = "  |  ".join(f"{k} {v}" for k,v in report["categories"].items())
    blocks += [_callout(f"采集时间：{now_str}    总计：{report['total']} 个项目\n{cats_str}"), _div()]
    for node_type, repos in report["repos_by_type"].items():
        blocks.append(_h(2, f"{TYPE_EMOJI.get(node_type,'⚪')} {node_type}  ({len(repos)} 个)"))
        for r in repos:
            stars = f"⭐{r['stars']}" if r["stars"] else ""
            ch = []
            if r["description"]: ch.append(_p(r["description"]))
            ch += [_p(f"🔗 {r['url']}"), _p(f"📡 订阅参考: {r['subscribe_hint']}")]
            if r["topics"]: ch.append(_p("标签: " + " · ".join(r["topics"][:6])))
            blocks.append(_bullet(f"{r['name']}  {stars}  [{r['updated']}]", children=ch))
        blocks.append(_div())
    blocks.append(_p(f"⚙ 由 GitHub Actions 自动聚合 · {now_str}", color="gray"))
    return blocks

def append_blocks(page_id, blocks):
    for i in range(0, len(blocks), 100):
        r = requests.patch(f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=notion_headers(), json={"children":blocks[i:i+100]}, timeout=30)
        if not r.ok: log.error(f"append error: {r.status_code} {r.text[:200]}"); r.raise_for_status()
        time.sleep(0.3)

def create_notion_page(report):
    cats = report.get("categories",{})
    blocks = build_blocks(report)
    payload = {"parent":{"database_id":NOTION_DATABASE_ID},
        "properties":{
            "报告标题":{"title":[{"text":{"content":report["title"]}}]},
            "节点总数":{"number":report.get("total",0)},
            "Clash 数量":{"number":cats.get("Clash",0)},
            "V2Ray 数量":{"number":cats.get("V2Ray",0)},
            "Shadowsocks 数量":{"number":cats.get("Shadowsocks",0)},
            "Trojan 数量":{"number":cats.get("Trojan",0)},
            "其他协议":{"number":cats.get("Hysteria",0)+cats.get("Other",0)},
            "状态":{"select":{"name":"✅ 成功"}},
            "采集日期":{"date":{"start":datetime.now().strftime("%Y-%m-%d")}},
        },"children":blocks[:100]}
    r = requests.post("https://api.notion.com/v1/pages", headers=notion_headers(), json=payload, timeout=30)
    r.raise_for_status()
    page = r.json(); page_id = page["id"]
    page_url = page.get("url", f"https://notion.so/{page_id.replace('-','')}")
    requests.patch(f"https://api.notion.com/v1/pages/{page_id}",
        headers=notion_headers(), json={"properties":{"报告链接":{"url":page_url}}}, timeout=15)
    if len(blocks) > 100: append_blocks(page_id, blocks[100:])
    return page_url

def main():
    log.info("="*50); log.info("GitHub Node Aggregator 启动"); log.info("="*50)
    repos = collect_all_repos()
    if not repos: log.error("无结果"); sys.exit(1)
    report = build_report(repos)
    log.info(f"分类: {report['categories']}")
    date_str = datetime.now().strftime("%Y-%m-%d")
    path = OUTPUT_DIR / f"nodes_{date_str}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    log.info(f"本地已保存: {path}")
    if not NOTION_TOKEN: log.warning("无 NOTION_TOKEN"); return
    try:
        url = create_notion_page(report)
        log.info(f"✅ Notion: {url}")
        (OUTPUT_DIR / "notion_url.txt").write_text(url)
        s = os.environ.get("GITHUB_STEP_SUMMARY")
        if s:
            with open(s,"a") as f:
                f.write(f"## ✅ 聚合完成\n- 总计：**{report['total']}**\n")
                for k,v in report["categories"].items(): f.write(f"- {TYPE_EMOJI.get(k,'⚪')} {k}：{v}\n")
                f.write(f"\n📄 [Notion 报告]({url})\n")
    except Exception as e:
        log.error(f"Notion 失败: {e}"); sys.exit(1)
    log.info("="*50); log.info("完成"); log.info("="*50)

if __name__ == "__main__": main()
