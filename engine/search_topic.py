# -*- coding: utf-8 -*-
"""QQreminder · 话题检索：在本机 QQ 群聊索引里按关键词找消息，并带上前后文。

给 AI 用的工具实现。全程本地、只读。

用法：
  python search_topic.py --query 卓越班
  python search_topic.py --query 选课 --days 30 --limit 60
"""
import argparse
import datetime as dt
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")

def _resolve_root():
    """数据根目录解析：环境变量给【根目录】（含 qgd 子目录）；也兼容直接给 qgd。"""
    r = os.environ.get("QQREMINDER_ROOT") or os.environ.get("QQDIGEST_ROOT")
    if not r:
        r = os.path.join(os.path.expanduser("~"), "QQreminder")
    return r if os.path.basename(r.rstrip("/\\")) == "qgd" else os.path.join(r, "qgd")


ROOT = _resolve_root()
INDEX = os.path.join(ROOT, "data", "index.db")
AUTH = os.path.join(ROOT, "authorization.json")
CTX = 3  # 每条命中带前后各几条上下文


def fmt(ts, f="%m-%d %H:%M"):
    return dt.datetime.fromtimestamp(ts).strftime(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("--days", type=float, default=7, help="只搜最近 N 天；0=不限")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--groups", default="", help="只搜这些群号，逗号分隔")
    a = ap.parse_args()

    import json
    authorized = True
    try:
        with open(AUTH, encoding="utf-8") as f:
            authorized = bool(json.load(f).get("qq_read"))
    except Exception:
        authorized = False

    if not os.path.exists(INDEX):
        print("✗ 找不到索引：%s\n  请先在面板点【刷新索引】，或运行 ntqq_build.py。" % INDEX)
        return 2

    con = sqlite3.connect(INDEX)
    con.row_factory = sqlite3.Row
    meta = dict(con.execute("SELECT key,value FROM meta").fetchall())
    cutoff = int(meta.get("cutoff_ts") or 0)
    now = dt.datetime.now().timestamp()
    age_min = max(0, int((now - cutoff) / 60))

    kw = a.query.strip()
    where = ["text LIKE ?"]
    args = ["%" + kw + "%"]
    if a.days and a.days > 0:
        where.append("ts >= ?")
        args.append(now - a.days * 86400)
    if a.groups:
        codes = [x.strip() for x in a.groups.split(",") if x.strip()]
        where.append("group_code IN (%s)" % ",".join("?" * len(codes)))
        args.extend(codes)

    hits = [dict(r) for r in con.execute(
        "SELECT * FROM messages WHERE " + " AND ".join(where) + " ORDER BY ts DESC LIMIT ?",
        args + [a.limit])]

    window = ("最近 %.0f 天" % a.days) if a.days and a.days > 0 else "全部已索引范围"
    print("# 话题检索：%s" % kw)
    print()
    print("数据截止 **%s**（距现在 %d 分钟）· 窗口 %s · 命中 **%d** 条"
          % (fmt(cutoff), age_min, window, len(hits)))
    if age_min > 120:
        print()
        print("> ⚠️ 数据已过去 %.1f 小时 —— 要最新内容，请先登录 QQ 再刷新索引。" % (age_min / 60))
    if not authorized:
        print()
        print("> 🔒 读取开关是关的：只能给出统计与元数据，不展示消息正文。")
        con.close()
        return 0
    if not hits:
        print()
        print("没有命中。可以试试：换同义词（换同义词或简称）、放宽 --days。")
        con.close()
        return 0

    print()
    for h in reversed(hits):
        print("---")
        info = con.execute(
            "SELECT COUNT(*) FROM messages WHERE group_code=? AND ts BETWEEN ? AND ?",
            (h["group_code"], h["ts"] - 600, h["ts"] + 600)).fetchone()
        print("【%s】%s · %s%s" % (h["group_name"], h["sender_name"] or "?", fmt(h["ts"]),
                                  ("　（该群 ±10 分钟内共 %d 条）" % info[0]) if info else ""))
        near = [dict(r) for r in con.execute(
            "SELECT * FROM messages WHERE group_code=? AND ts BETWEEN ? AND ? ORDER BY ts",
            (h["group_code"], h["ts"] - 600, h["ts"] + 600))]
        for r in near[: CTX * 2 + 1]:
            mk = "★" if r["msg_id"] == h["msg_id"] else " "
            t = (r["text"] or "").replace("\n", " ⏎ ")
            if len(t) > 200:
                t = t[:200] + "…"
            print("  %s[%s] %s: %s" % (mk, fmt(r["ts"]), r["sender_name"] or "?", t))
        print()

    # 分布小结
    per = {}
    for h in hits:
        per[h["group_name"]] = per.get(h["group_name"], 0) + 1
    print("---")
    print("**命中最多的群**：" + " · ".join("%s %d" % (k, v)
          for k, v in sorted(per.items(), key=lambda x: -x[1])[:6]))
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
