# -*- coding: utf-8 -*-
"""QQ 群摘要 · HTML 报告生成器（一键出报告 · 两级可点）

设计目标：**简洁、好看、有层次、可点进去**；单文件、内联 CSS/JS、零外部依赖（可离线打开/分享）。

两级结构：
  ① 总览页：统计卡 + 关键群卡片网格（点卡片进该群）+ 跨群"最重要 N 条"+ 遗漏 + 分布
  ② 群详情页：该群全部条目（按分数排序）+ 返回按钮

用法：
  python make_report.py --days 7
  python make_report.py --since 2026-10-01 --until 2026-10-06
"""
import argparse
import datetime as dt
import html
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import score  # noqa: E402  复用打分器

OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#18181b;--muted:#71717a;--line:#e7e7ea;
      --brand:#2563eb;--warn:#d97706;--radius:12px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
     font:15px/1.75 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
     -webkit-font-smoothing:antialiased}
.wrap{max-width:940px;margin:0 auto;padding:40px 20px 90px}
h1{font-size:25px;margin:0 0 8px;letter-spacing:-.02em}
h2{font-size:17px;margin:38px 0 14px;padding-bottom:9px;border-bottom:1px solid var(--line);
   display:flex;align-items:center;gap:8px;flex-wrap:wrap}
h2 .cnt{color:var(--muted);font-weight:400;font-size:14px}
.sub{color:var(--muted);font-size:13.5px}
.warnline{color:#b45309;font-size:13.5px;margin-top:6px}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px;margin:24px 0 6px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px}
.stat b{display:block;font-size:23px;line-height:1.25;font-variant-numeric:tabular-nums}
.stat span{color:var(--muted);font-size:12.5px}
.stat.clickable{cursor:pointer;position:relative;transition:.15s}
.stat.clickable:hover{border-color:var(--brand);transform:translateY(-1px);box-shadow:0 3px 12px rgba(37,99,235,.10)}
.stat.clickable .jump{position:absolute;right:12px;top:11px;color:var(--brand);font-size:12px;opacity:0;transition:.15s}
.stat.clickable:hover .jump{opacity:1}
.flash{animation:fl 1.1s ease-out}
@keyframes fl{0%{background:#dbeafe}100%{background:transparent}}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(215px,1fr));gap:12px}
.gcard{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
       padding:14px 16px 26px;cursor:pointer;transition:.15s;position:relative}
.gcard:hover{border-color:var(--brand);transform:translateY(-1px);box-shadow:0 3px 12px rgba(37,99,235,.10)}
.gcard .nm{font-weight:600;font-size:14px;line-height:1.45;margin-bottom:8px;
           display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.gcard .kv{color:var(--muted);font-size:12.5px;display:flex;flex-wrap:wrap;gap:4px 10px}
.gcard .kv b{color:var(--fg);font-variant-numeric:tabular-nums}
.gcard .go{position:absolute;right:14px;bottom:10px;color:var(--brand);font-size:12.5px}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin:28px 0 4px}
.tab{background:var(--card);border:1px solid var(--line);border-radius:999px;padding:5px 13px;
     font-size:13px;cursor:pointer;color:var(--muted);max-width:250px;overflow:hidden;
     text-overflow:ellipsis;white-space:nowrap;transition:.15s;font-family:inherit}
.tab:hover{border-color:#c7c7cc;color:var(--fg)}
.tab.active{background:var(--brand);border-color:var(--brand);color:#fff}
.page{display:none}
.page.active{display:block}
.item{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
      padding:15px 17px;margin-bottom:11px;transition:border-color .15s}
.item:hover{border-color:#d4d4d8}
.item.miss{border-left:3px solid var(--warn);border-radius:4px var(--radius) var(--radius) 4px}
.top{display:flex;justify-content:space-between;gap:12px;align-items:baseline;flex-wrap:wrap}
.who{font-weight:600}
.grp{color:var(--muted);font-size:12.5px;font-weight:400;margin-left:6px}
.meta{color:var(--muted);font-size:12.5px;white-space:nowrap}
.score{font-variant-numeric:tabular-nums;font-weight:700;color:var(--brand);font-size:14px}
.body{margin:11px 0 12px;white-space:pre-wrap;word-break:break-word;color:#27272a}
.why{display:flex;flex-wrap:wrap;gap:6px}
.tag{background:#eff6ff;color:#1d4ed8;border-radius:6px;padding:2px 8px;font-size:12px;white-space:nowrap}
.tag.warn{background:#fffbeb;color:#b45309}
.tag.stale{background:#fef2f2;color:#b91c1c}
.bars{margin-top:6px}
.bar{display:flex;align-items:center;gap:10px;margin:7px 0;font-size:13px;cursor:pointer}
.bar:hover .n{color:var(--brand)}
.bar .n{width:230px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:0 0 auto}
.bar .fill{height:13px;background:linear-gradient(90deg,#3b82f6,#2563eb);border-radius:7px;min-width:3px}
.bar .v{color:var(--muted);font-variant-numeric:tabular-nums;font-size:12.5px}
.back{margin:18px 0 4px}
.back button{background:none;border:none;color:var(--brand);cursor:pointer;font-size:13.5px;padding:0;font-family:inherit}
.empty{color:var(--muted);font-size:14px;padding:14px 0}
footer{margin-top:52px;color:var(--muted);font-size:12.5px;border-top:1px solid var(--line);padding-top:18px;
       display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
@media(max-width:600px){.bar .n{width:110px}.wrap{padding:26px 14px 60px}}
"""

JS = (
    "function go(id){"
    "var ps=document.querySelectorAll('.page');"
    "for(var i=0;i<ps.length;i++)ps[i].classList.toggle('active',ps[i].id===id);"
    "var ts=document.querySelectorAll('.tab');"
    "for(var j=0;j<ts.length;j++)ts[j].classList.toggle('active',ts[j].getAttribute('data-id')===id);"
    "window.scrollTo(0,0);}"
    # 顶部统计卡点击 → 回总览页并滚到对应区块（带一次高亮闪烁）
    "function jump(id){go('all');setTimeout(function(){"
    "var el=document.getElementById(id);if(!el)return;"
    "el.scrollIntoView({behavior:'smooth',block:'start'});"
    "el.classList.remove('flash');void el.offsetWidth;el.classList.add('flash');"
    "},40);}"
)


def esc(s):
    return html.escape(str(s or ""))


def fmt(ts, f="%m-%d %H:%M"):
    return dt.datetime.fromtimestamp(ts).strftime(f)


def build(since, until, top_n, missed_n):
    focus = score.load_focus()
    con = sqlite3.connect(score.INDEX)
    con.row_factory = sqlite3.Row
    meta = dict(con.execute("SELECT key,value FROM meta").fetchall())
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM messages WHERE ts >= ? AND ts <= ? ORDER BY ts", (since, until))]
    con.close()

    watch = [str(g["群号"]) for g in focus["监工群"]]
    if watch:
        rows = [r for r in rows if str(r["group_code"]) in watch]

    roles_uid, roles_qq = score.build_roles(focus)
    at_me = score.load_at_me()

    scored = []
    for r in rows:
        s, why, who = score.score_one(r, focus, roles_uid, roles_qq, at_me)
        if s > 0:
            scored.append((s, r, why, who))
    scored.sort(key=lambda x: (-x[0], -x[1]["ts"]))
    top = scored[:top_n]

    lo, hi = focus["打分"]["遗漏区间"]
    missed = []
    for s, r, why, who in scored:
        if not (lo <= s <= hi):
            continue
        cat, hits = score.is_missed(r, focus)
        if cat:
            missed.append((s, r, who, cat, hits))
    missed = missed[:missed_n]

    byg = {}
    for r in rows:
        k = r["group_name"] or str(r["group_code"])
        byg.setdefault(k, {"items": [], "n": 0})
        byg[k]["n"] += 1
    for s, r, why, who in scored:
        k = r["group_name"] or str(r["group_code"])
        byg[k]["items"].append((s, r, why, who))
    gorder = sorted(byg.items(), key=lambda kv: (-len(kv[1]["items"]), -kv[1]["n"]))
    gidx = {name: "g%d" % i for i, (name, _) in enumerate(gorder)}

    cutoff = int(meta.get("cutoff_ts") or 0)
    now = time.time()
    pmax = max([v["n"] for _, v in gorder] or [1])

    H = []
    A = H.append

    def item_html(s, r, why, who, show_group=True, miss=False):
        A("<div class='%s'>" % ("item miss" if miss else "item"))
        gtag = ("<span class='grp'>%s</span>" % esc(r["group_name"])) if show_group else ""
        A("<div class='top'><div class='who'>%s%s</div>"
          "<div><span class='score'>%.1f 分</span> <span class='meta'>%s</span></div></div>"
          % (esc(who), gtag, s, fmt(r["ts"])))
        t = r["text"] or ""
        lim = 220 if miss else 500
        if len(t) > lim:
            t = t[:lim] + "…"
        if r["kind"] == "image":
            t = "🖼 [图片] " + t
        A("<div class='body'>%s</div>" % esc(t))
        A("<div class='why'>" + "".join("<span class='tag'>%s</span>" % esc(x) for x in why) + "</div>")
        A("</div>")

    A("<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>")
    A("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    A("<title>QQ 群摘要 · %s ~ %s</title>" % (fmt(since, "%Y-%m-%d"), fmt(until, "%Y-%m-%d")))
    A("<style>%s</style></head><body><div class='wrap'>" % CSS)

    A("<h1>QQ 群摘要</h1>")
    A("<div class='sub'>%s ~ %s &nbsp;·&nbsp; 数据截止 <b>%s</b>（距现在 %d 分钟）</div>"
      % (fmt(since, "%Y-%m-%d %H:%M"), fmt(until, "%Y-%m-%d %H:%M"), fmt(cutoff),
         max(0, int((now - cutoff) / 60))))
    if now - cutoff > 2 * 3600:
        A("<div class='warnline'>⚠️ 数据已过去 %.1f 小时 —— 要最新内容请先登录 QQ 再刷新索引。</div>"
          % ((now - cutoff) / 3600))

    A("<div class='summary'>")
    for val, lab, tgt, tip in (
        (len(rows), "有效消息", "sec-bars", "看群消息分布"),
        (len(gorder), "涉及群", "sec-groups", "看关键群卡片"),
        (len(top), "重点条目", "sec-top", "看最重要条目"),
        (len(missed), "疑似遗漏", "sec-missed", "看遗漏提醒"),
        (len(set(r["sender_name"] for r in rows)), "发言人数", "sec-bars", "看群消息分布"),
    ):
        A("<div class='stat clickable' onclick=\"jump('%s')\" title='%s'>"
          "<span class='jump'>↓</span><b>%s</b><span>%s</span></div>" % (tgt, esc(tip), val, lab))
    A("</div>")

    A("<div class='tabs'>")
    A("<button class='tab active' data-id='all' onclick=\"go('all')\">总览</button>")
    for name, _ in gorder:
        A("<button class='tab' data-id='%s' onclick=\"go('%s')\" title='%s'>%s</button>"
          % (gidx[name], gidx[name], esc(name), esc(name)))
    A("</div>")

    # ── 总览 ────────────────────────────────────────────
    A("<section id='all' class='page active'>")
    A("<h2 id='sec-groups'>🗂 关键群 <span class='cnt'>点卡片进入该群</span></h2>")
    A("<div class='grid'>")
    for name, g in gorder:
        A("<div class='gcard' onclick=\"go('%s')\">" % gidx[name])
        A("<div class='nm'>%s</div>" % esc(name))
        A("<div class='kv'><span>消息 <b>%d</b></span><span>重点 <b>%d</b></span></div>"
          % (g["n"], len(g["items"])))
        A("<div class='go'>查看 →</div></div>")
    A("</div>")

    A("<h2 id='sec-top'>📌 最重要 <span class='cnt'>%d 条（跨群）</span></h2>" % len(top))
    if not top:
        A("<div class='empty'>这个时间窗内没有达到阈值的消息。</div>")
    for s, r, why, who in top:
        item_html(s, r, why, who)

    A("<h2 id='sec-missed'>⚠️ 你可能漏了 <span class='cnt'>%d 条</span></h2>" % len(missed))
    if not missed:
        A("<div class='empty'>没有检测到「分数中等但有时效」的消息。</div>")
    for s, r, who, cat, hits in missed:
        stale = ""
        age = (now - r["ts"]) / 86400
        if cat == "时效" and age > 2:
            stale = "<span class='tag stale'>已过期 %.0f 天</span>" % age
        A("<div class='item miss'>")
        A("<div class='top'><div class='who'>%s<span class='grp'>%s</span></div>"
          "<div><span class='score'>%.1f 分</span> <span class='meta'>%s</span></div></div>"
          % (esc(who), esc(r["group_name"]), s, fmt(r["ts"])))
        t = r["text"] or ""
        if len(t) > 220:
            t = t[:220] + "…"
        A("<div class='body'>%s</div>" % esc(t))
        A("<div class='why'><span class='tag warn'>含「%s」（%s）</span>%s</div>"
          % (esc("」「".join(hits)), esc(cat), stale))
        A("</div>")

    A("<h2 id='sec-bars'>📊 群消息分布 <span class='cnt'>点条目进入该群</span></h2><div class='bars'>")
    for name, g in gorder:
        pct = max(2, int(g["n"] / pmax * 100))
        A("<div class='bar' onclick=\"go('%s')\"><div class='n'>%s</div>"
          "<div class='fill' style='width:%d%%'></div><div class='v'>%d</div></div>"
          % (gidx[name], esc(name), pct, g["n"]))
    A("</div></section>")

    # ── 群详情 ──────────────────────────────────────────
    for name, g in gorder:
        A("<section id='%s' class='page'>" % gidx[name])
        A("<div class='back'><button onclick=\"go('all')\">← 返回总览</button></div>")
        A("<h2>%s <span class='cnt'>窗口内 %d 条 · 其中重点 %d 条</span></h2>"
          % (esc(name), g["n"], len(g["items"])))
        if not g["items"]:
            A("<div class='empty'>这个群里没有达到阈值的消息（多半是纯闲聊）。</div>")
        for s, r, why, who in g["items"]:
            item_html(s, r, why, who, show_group=False)
        A("</section>")

    A("<footer><div>由 dsh-qq-digest 生成 · 全程本地、只读</div>"
      "<div>生成于 %s</div></footer>" % dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    A("</div><script>%s</script></body></html>" % JS)
    return "\n".join(H)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--missed", type=int, default=8)
    ap.add_argument("--out")
    a = ap.parse_args()

    def parse(s, default, end_of_day=False):
        if not s:
            return default
        for f in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                t = dt.datetime.strptime(s.strip(), f).timestamp()
            except ValueError:
                continue
            # ★ 只给到"日"时：作为结束应含当天整天，否则会漏掉当天的消息
            if f == "%Y-%m-%d" and end_of_day:
                t += 86399.999
            return t
        raise SystemExit("时间格式不对：%s（用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM）" % s)

    until = parse(a.until, time.time(), end_of_day=True)
    since = parse(a.since, until - a.days * 86400)
    if since >= until:
        raise SystemExit("起始时间必须早于结束时间")

    doc = build(since, until, a.top, a.missed)
    os.makedirs(OUTDIR, exist_ok=True)
    out = a.out or os.path.join(OUTDIR, "报告-%s.html" % dt.datetime.now().strftime("%Y%m%d-%H%M"))
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    print(out)


if __name__ == "__main__":
    main()
