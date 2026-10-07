# -*- coding: utf-8 -*-
"""QQ 群摘要 · 材料打包器（给大模型看的"备料"）

定位：
  本地脚本只做【客观统计 + 抽样】，**不做任何语义判断**；
  判断"哪些群重要、该关注什么词"交给云端 AI。

输出：
  ① out/待分析材料.md   —— 紧凑文本，AI 直接读（省 token）
  ② out/待分析材料.json —— 结构化，面板展示统计用

用法：
  python pack_material.py --days 7 --max-groups 30 --per-group 8
"""
import argparse
import collections
import json
import os
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

def _resolve_root():
    """数据根目录解析：环境变量给【根目录】（含 qgd 子目录）；也兼容直接给 qgd。"""
    r = os.environ.get("QQREMINDER_ROOT") or os.environ.get("QQDIGEST_ROOT")
    if not r:
        r = os.path.join(os.path.expanduser("~"), "QQreminder")
    return r if os.path.basename(r.rstrip("/\\")) == "qgd" else os.path.join(r, "qgd")


ROOT = _resolve_root()
OUTDIR = os.path.dirname(ROOT.rstrip("/\\")) + "/out"
INDEX = os.path.join(ROOT, "data", "index.db")
GI = os.path.join(ROOT, "data", "plain", "group_info.db")
NM = os.path.join(ROOT, "data", "plain", "nt_msg.db")
FOCUS = os.path.join(ROOT, "focus.json")

NOTICE = ["通知", "公告", "@全体", "全体成员", "报名", "截止", "考试", "选课", "答辩", "补考",
          "重修", "注册", "务必", "提交", "作业", "安排", "开会", "班会", "领取", "面试", "考核",
          "项目", "需求", "客户", "汇报", "周报", "报销", "排期", "会议", "方案", "评审"]
IDENT = ["老师", "导员", "辅导员", "班助", "班主任", "教授", "主任", "队长", "学长", "学姐"]


def fmt(ts):
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


def load_me():
    try:
        with open(FOCUS, encoding="utf-8") as f:
            return str(json.load(f).get("主人", {}).get("qq") or "").strip()
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--max-groups", type=int, default=30)
    ap.add_argument("--per-group", type=int, default=8, help="每群抽样条数")
    ap.add_argument("--max-chars", type=int, default=220, help="单条正文截断长度")
    ap.add_argument("--min-msgs", type=int, default=5, help="低于此消息量的群不进材料")
    a = ap.parse_args()

    me = load_me()
    since = time.time() - a.days * 86400
    con = sqlite3.connect(INDEX)
    con.row_factory = sqlite3.Row
    meta = dict(con.execute("SELECT key,value FROM meta").fetchall())
    rows = [dict(r) for r in con.execute("SELECT * FROM messages WHERE ts >= ? ORDER BY ts", (since,))]
    con.close()

    # 群成员角色
    roles = {}
    if os.path.exists(GI):
        c = sqlite3.connect(GI)
        try:
            for g, uid, qq, card, r17, r35, nick in c.execute(
                    'SELECT "60001","1000","1002","64003","64017","64035","20002" FROM group_member3'):
                role = "群主" if (r17 == 1 or r35 == 3) else ("管理员" if r17 == 2 else None)
                roles[(g, uid)] = {"role": role, "card": (card or "").strip(), "nick": (nick or "").strip()}
        except sqlite3.DatabaseError:
            pass
        finally:
            c.close()

    at_me = set()
    if os.path.exists(NM):
        c = sqlite3.connect(NM)
        try:
            at_me = {int(x[0]) for x in c.execute("SELECT 40001 FROM group_at_me_msg")}
        except sqlite3.DatabaseError:
            pass
        finally:
            c.close()

    # 按群归集
    byg = collections.defaultdict(list)
    for r in rows:
        byg[r["group_code"]].append(r)

    groups_out = []
    for gcode, rs in byg.items():
        # ★ 用户自己发过言的群【无条件保留】—— 哪怕群里只有 1 条消息。
        #   理由：一个人在某群说过话，就说明这个群跟他有关系；
        #   按"群内消息量"一刀切会把它误杀（实测 Q1 C805 用户发了 152 条却被滤掉）。
        me_spoke = sum(1 for r in rs if me and str(r.get("sender_qq")) == str(me))
        if len(rs) < a.min_msgs and me_spoke == 0:
            continue
        name = rs[0]["group_name"] or str(gcode)
        auth = notice = atm = 0
        speaker = collections.Counter()
        who = {}
        for r in rs:
            t = r["text"] or ""
            info = roles.get((gcode, r["sender_uid"])) or {}
            disp = (info.get("card") or r["sender_name"] or "?")
            tag = info.get("role")
            label = disp + ("（%s）" % tag if tag else "")
            speaker[label] += 1
            who[label] = (r["sender_uid"], r["sender_qq"])
            if tag:
                auth += 1
            if any(w in t for w in NOTICE):
                notice += 1
            if int(r["msg_id"]) in at_me:
                atm += 1

        # ── 抽样：全程只用客观规则（权威 / 通知词 / 长度），不做语义判断 ──
        def rank(r):
            t = r["text"] or ""
            info = roles.get((gcode, r["sender_uid"])) or {}
            s = 0
            if info.get("role"):
                s += 4
            if any(w in t for w in NOTICE):
                s += 3
            if len(t) >= 200:
                s += 2
            elif len(t) >= 80:
                s += 1
            if int(r["msg_id"]) in at_me:
                s += 5
            if r["kind"] == "forward":
                s += 1
            return (-s, -r["ts"])

        picked = sorted(rs, key=rank)[: a.per_group]
        picked.sort(key=lambda r: r["ts"])
        samples = []
        for r in picked:
            info = roles.get((gcode, r["sender_uid"])) or {}
            disp = info.get("card") or r["sender_name"] or "?"
            tag = info.get("role")
            t = (r["text"] or "").replace("\n", " ⏎ ")
            if len(t) > a.max_chars:
                t = t[: a.max_chars] + "…"
            samples.append({"ts": r["ts"], "时间": fmt(r["ts"]),
                            "发送者": disp + ("（%s）" % tag if tag else ""), "正文": t})

        groups_out.append({
            "群号": str(gcode), "群名": name, "消息量": len(rs), "发言人数": len(speaker),
            "权威条数": auth, "通知类条数": notice, "at我条数": atm,
            "主要发言者": [{"人": k, "条数": v} for k, v in speaker.most_common(5)],
            "我发言条数": me_spoke,
            "因我发言保留": me_spoke > 0 and len(rs) < a.min_msgs,
            "抽样消息": samples,
        })

    # ── ★主人自己的发言 + 上下文（独立专区，不参与抽样竞争）──────────────
    # 设计教训（2026-10-06 主人指出）：按「权威/通知词/长文」抽样会把主人
    #   自己的发言漏掉 —— 而"主人问什么"恰恰暴露了他关心什么，
    #   "别人怎么答"就是答案。所以必须【独立采样】，且必须带前后文。
    mine = [r for r in rows if me and str(r.get("sender_qq")) == str(me)]
    mine.sort(key=lambda r: (r["group_code"], r["ts"]))
    # 先按「同群 + 30 分钟内」聚成会话段，避免上下文重复取样（否则材料会翻倍）
    clusters = []
    for r in mine:
        if clusters and clusters[-1]["code"] == r["group_code"] and r["ts"] - clusters[-1]["last"] <= 1800:
            clusters[-1]["items"].append(r)
            clusters[-1]["last"] = r["ts"]
        else:
            clusters.append({"code": r["group_code"], "name": r["group_name"],
                             "first": r["ts"], "last": r["ts"], "items": [r]})

    mine_sections = []
    for c in clusters:
        ctx = [x for x in rows if x["group_code"] == c["code"]
               and c["first"] - 600 <= x["ts"] <= c["last"] + 600]
        ctx.sort(key=lambda x: x["ts"])
        lines = []
        for x in ctx[: a.per_group + 14]:
            is_me = str(x.get("sender_qq")) == str(me)
            info = roles.get((c["code"], x["sender_uid"])) or {}
            disp = info.get("card") or x["sender_name"] or "?"
            tag = info.get("role")
            t = (x["text"] or "").replace("\n", " ⏎ ")
            if len(t) > a.max_chars:
                t = t[: a.max_chars] + "…"
            lines.append("    %s [%s] %s%s: %s" % ("★我" if is_me else "  ", fmt(x["ts"]),
                                                   disp, ("（%s）" % tag if tag else ""), t))
        mine_sections.append({
            "群名": c["name"], "群号": str(c["code"]),
            "时间": "%s ~ %s" % (fmt(c["first"]), fmt(c["last"])),
            "ts": c["first"],
            "我说": " ｜ ".join((r["text"] or "（图片）").replace("\n", " ⏎ ") for r in c["items"]),
            "我的话数": len(c["items"]),
            "上下文": lines,
        })

    # 排序：有实质文字的段优先（纯图片/文件段往后 —— 主人把某些群当云盘用）
    def seg_text_len(m):
        return len([c for c in m["我说"] if "\u4e00" <= c <= "\u9fff"])

    for m in mine_sections:
        m["实质文字数"] = seg_text_len(m)
    mine_sections.sort(key=lambda m: (0 if m["实质文字数"] >= 6 else 1, m["ts"]))

    groups_out.sort(key=lambda x: (-(x["权威条数"] * 3 + x["通知类条数"] * 2 + x["at我条数"] * 4), -x["消息量"]))

    total_chars = sum(len(s["正文"]) for g in groups_out[: a.max_groups] for s in g["抽样消息"])
    result = {
        "generated_at": int(time.time()), "window_days": a.days,
        "cutoff_ts": int(meta.get("cutoff_ts") or 0), "me": me,
        "totals": {"全部群": len(byg), "进材料的群": min(len(groups_out), a.max_groups),
                   "消息总数": len(rows), "抽样条数": sum(len(g["抽样消息"]) for g in groups_out[: a.max_groups]),
                   "抽样字符数": total_chars},
        "groups": groups_out[: a.max_groups],
        "主人发言": mine_sections,
    }

    # ── 给 AI 读的紧凑文本 ─────────────────────────────
    L = []
    L.append("# QQ 群初始化材料（供 AI 做语义分析）")
    L.append("")
    L.append("数据截止 %s · 窗口 %.0f 天 · 全部群 %d 个 / 消息 %d 条 · 本材料含 %d 个群 / 抽样 %d 条（约 %d 字）"
             % (fmt(result["cutoff_ts"]), a.days, len(byg), len(rows),
                result["totals"]["进材料的群"], result["totals"]["抽样条数"], total_chars))
    L.append("")
    L.append("> 说明：以下是**客观统计 + 抽样原文**，不含任何语义判断。")
    L.append("> 请据此判断：① 哪些群值得监工、建议权重；② 该给这个人群配置哪些关注词、为什么。")
    L.append("")
    L.append("## ★★ 第一部分：主人自己说过的话 + 上下文（最高优先级，必须逐条看）")
    L.append("")
    L.append("> 主人问什么 = 主人关心什么；别人的回答就是答案。**这一节不许跳过。**")
    L.append("")
    if not mine_sections:
        L.append("（本窗口内主人没有发言）")
    for i, m in enumerate(mine_sections, 1):
        L.append("### 我%d. 【%s】%s" % (i, m["群名"], m["时间"]))
        L.append("  **我说**：%s" % m["我说"])
        for ln in m["上下文"]:
            L.append(ln)
        L.append("")
    L.append("")
    L.append("## 第二部分：各群概况与抽样（用于判断哪些群值得监工）")
    L.append("")
    for i, g in enumerate(result["groups"], 1):
        L.append("---")
        L.append("## %d. %s（%s）" % (i, g["群名"], g["群号"]))
        L.append("消息 %d · 发言 %d 人 · 权威 %d · 通知类 %d · @我 %d%s"
                 % (g["消息量"], g["发言人数"], g["权威条数"], g["通知类条数"], g["at我条数"],
                    ("　★我在这里说过 %d 句（消息虽少但与我有关）" % g["我发言条数"])
                    if g.get("因我发言保留") else
                    (("　· 我说过 %d 句" % g["我发言条数"]) if g.get("我发言条数") else "")))
        L.append("主要发言者：" + " · ".join("%s %d条" % (x["人"], x["条数"]) for x in g["主要发言者"]))
        L.append("抽样原文：")
        for s in g["抽样消息"]:
            L.append("  [%s] %s: %s" % (s["时间"], s["发送者"], s["正文"]))
        L.append("")

    md = "\n".join(L)
    os.makedirs(OUTDIR, exist_ok=True)
    md_path = os.path.join(OUTDIR, "待分析材料.md")
    js_path = os.path.join(OUTDIR, "待分析材料.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(js_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    sys.stdout.write(md)
    sys.stderr.write("\n[已写入] %s\n[已写入] %s\n" % (md_path, js_path))


if __name__ == "__main__":
    main()
