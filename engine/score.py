# -*- coding: utf-8 -*-
"""QQ 群摘要 · 多信号打分器（分析层）

数据源（全部本地只读）：
  1) <root>/data/index.db            —— 工具建好的滚动索引（messages / groups / meta）
  2) <root>/data/plain/group_info.db —— group_member3：群成员角色、群名片
  3) <root>/data/plain/nt_msg.db     —— group_at_me_msg：@我的消息

打分信号：
  关注词命中（按权重累加，同类取最高） / 通知词 / @我 / 群主 / 管理员或老师 /
  名片含身份词 / 长度 / 转发消息；最后乘以群权重。

输出：Markdown —— 「最重要的 N 条」+「你可能漏了的 M 条」，每条都带「为什么」。

用法：
  python score.py                        # 今天，前 8 条
  python score.py --days 3 --top 12
  python score.py --all-groups           # 不限于监工群
  python score.py --out report.md
  python score.py --json report.json
"""
import argparse
import datetime as dt
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
FOCUS = os.path.join(ROOT, "focus.json")
INDEX = os.path.join(ROOT, "data", "index.db")
GI = os.path.join(ROOT, "data", "plain", "group_info.db")
NM = os.path.join(ROOT, "data", "plain", "nt_msg.db")


def fmt_ts(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def load_focus():
    with open(FOCUS, encoding="utf-8") as f:
        return json.load(f)


def build_roles(focus):
    """(group_code, uid) / (group_code, qq) -> 角色信息"""
    by_uid, by_qq = {}, {}
    if not os.path.exists(GI):
        return by_uid, by_qq
    con = sqlite3.connect(GI)
    try:
        rows = con.execute(
            'SELECT "60001","1000","1002","64003","64017","64035","20002","64023" FROM group_member3'
        ).fetchall()
    except sqlite3.DatabaseError:
        return by_uid, by_qq
    finally:
        con.close()
    for g, uid, qq, card, r17, r35, nick, title in rows:
        role = None
        if r17 == 1 or r35 == 3:
            role = "群主"
        elif r17 == 2:
            role = "管理员/老师"
        info = {"role": role, "card": (card or "").strip(), "nick": (nick or "").strip(),
                "title": (title or "").strip()}
        if uid:
            by_uid[(g, uid)] = info
        if qq:
            by_qq[(g, qq)] = info
    return by_uid, by_qq


def load_at_me():
    """@我的 msg_id 集合"""
    s = set()
    if not os.path.exists(NM):
        return s
    con = sqlite3.connect(NM)
    try:
        for (mid,) in con.execute("SELECT 40001 FROM group_at_me_msg"):
            s.add(int(mid))
    except sqlite3.DatabaseError:
        pass
    finally:
        con.close()
    return s


def score_one(row, focus, roles_by_uid, roles_by_qq, at_me):
    text = row["text"] or ""
    reasons, score = [], 0.0

    # ── 关注词（同类取最高，不同类累加）─────────────────────────
    hit_focus = [(w, v) for w, v in focus["关注词"].items()
                 if not w.startswith("_") and w in text]
    if hit_focus:
        top = max(hit_focus, key=lambda x: x[1])
        score += top[1]
        reasons.append("命中关注词「%s」+%d" % (top[0], top[1]))

    # ── 降权词 ────────────────────────────────────────────────
    for w, v in focus["降权词"].items():
        if not w.startswith("_") and w in text:
            score += v
            reasons.append("命中降权词「%s」%d" % (w, v))

    # ── 通知词（强 +3 / 弱 +1，只取最强一档）─────────────────────
    nt = focus["通知词"]
    hit_strong = [w for w in nt.get("强", []) if w in text]
    hit_weak = [w for w in nt.get("弱", []) if w in text]
    if hit_strong:
        score += 3
        reasons.append("通知类强词「%s」+3" % "」「".join(hit_strong[:3]))
    elif hit_weak:
        score += 1
        reasons.append("通知类弱词「%s」+1" % "」「".join(hit_weak[:3]))

    # ── @我 ──────────────────────────────────────────────────
    if int(row["msg_id"]) in at_me:
        score += 5
        reasons.append("@我 +5")

    # ── 发送者身份 ────────────────────────────────────────────
    info = roles_by_uid.get((row["group_code"], row["sender_uid"])) or \
           roles_by_qq.get((row["group_code"], row["sender_qq"]))
    who = (row["sender_name"] or "?").strip()
    if info:
        # 优先展示群名片（更接近真名）；没有则用昵称
        card = (info.get("card") or "").strip()
        base = card if card else who
        tag = ""
        if info["role"] == "群主":
            score += 4
            reasons.append("群主发的 +4")
            tag = "群主"
        elif info["role"] == "管理员/老师":
            score += 4
            reasons.append("管理员/老师发的 +4")
            tag = "管理员"
        ident = [w for w in focus["身份词"] if w in card or w in who]
        if ident and not info["role"]:
            score += 3
            reasons.append("名片含身份词「%s」+3" % "」「".join(ident[:2]))
            tag = ident[0]
        who = base if not tag else "%s（%s）" % (base, tag)
        if card and card != who and row["sender_name"] and card != row["sender_name"]:
            who += "｜昵称:%s" % row["sender_name"]

    # ── 长度 / 转发 ───────────────────────────────────────────
    n = len(text)
    if n >= 200:
        score += 2
        reasons.append("长文 %d 字 +2" % n)
    elif n >= 80:
        score += 1
        reasons.append("较长 %d 字 +1" % n)
    if row["kind"] == "forward":
        score += 1
        reasons.append("转发消息 +1")

    # ── 群权重 ────────────────────────────────────────────────
    gw = 1.0
    for g in focus["监工群"]:
        if str(g["群号"]) == str(row["group_code"]):
            gw = float(g.get("权重", 1.0))
            break
    final = round(score * gw, 1)
    if gw != 1.0:
        reasons.append("× 群权重 %.1f" % gw)
    return final, reasons, who


def is_missed(row, focus):
    """时效性判断：含时限/行动/疑问词"""
    t = row["text"] or ""
    k = focus["遗漏提示词"]
    for cat in ("时效", "行动", "疑问"):
        hit = [w for w in k.get(cat, []) if w in t]
        if hit:
            return cat, hit[:3]
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=0, help="回溯天数；0 表示仅今天")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--missed", type=int, default=6)
    ap.add_argument("--all-groups", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--json")
    a = ap.parse_args()

    focus = load_focus()
    now = time.time()
    if a.days and a.days > 0:
        since = now - a.days * 86400
        win_desc = "最近 %.1f 天" % a.days
    else:
        since = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        win_desc = "今天"

    con = sqlite3.connect(INDEX)
    con.row_factory = sqlite3.Row
    meta = dict(con.execute("SELECT key,value FROM meta").fetchall())
    watch = [str(g["群号"]) for g in focus["监工群"]]
    rows = [r for r in con.execute(
        "SELECT * FROM messages WHERE ts >= ? ORDER BY ts", (since,))]
    if not a.all_groups and watch:
        rows = [r for r in rows if str(r["group_code"]) in watch]
    con.close()

    roles_uid, roles_qq = build_roles(focus)
    at_me = load_at_me()

    scored = []
    for r in rows:
        s, why, who = score_one(r, focus, roles_uid, roles_qq, at_me)
        if s <= 0:
            continue
        scored.append((s, r, why, who))
    scored.sort(key=lambda x: (-x[0], -x[1]["ts"]))

    top = scored[:a.top]
    lo, hi = focus["打分"]["遗漏区间"]
    missed = []
    for s, r, why, who in scored:
        if not (lo <= s <= hi):
            continue
        cat, hits = is_missed(r, focus)
        if cat:
            missed.append((s, r, who, cat, hits))
    missed = missed[:a.missed]

    cutoff = int(meta.get("cutoff_ts") or 0)
    built = int(meta.get("built_at") or 0)
    L = []
    L.append("# QQ 群摘要 · %s" % dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    if cutoff:
        L.append("> 数据截止 **%s**（索引构建于 %s，距现在 %d 分钟）· 窗口：%s · 监工群 %d 个 · 有效消息 %d 条"
                 % (fmt_ts(cutoff), fmt_ts(built), max(0, int((now - built) / 60)), win_desc, len(watch), len(rows)))
    L.append("")

    L.append("## 📌 %s最重要 %d 条" % (win_desc, len(top)))
    L.append("")
    if not top:
        L.append("*（这个窗口内没有达到阈值的消息）*")
    for i, (s, r, why, who) in enumerate(top, 1):
        L.append("### %d. 【%.1f 分】%s" % (i, s, r["group_name"] or r["group_code"]))
        L.append("**%s** · %s" % (who, fmt_ts(r["ts"])))
        L.append("")
        text = (r["text"] or "").replace("\n", " ⏎ ")
        if len(text) > 400:
            text = text[:400] + "…"
        if r["kind"] == "image":
            text = "🖼 [图片] " + text
        L.append("> " + text)
        L.append("")
        L.append("`★ 因为：` " + " ＋ ".join(why))
        L.append("")

    L.append("## ⚠️ 你可能漏了 %d 条" % len(missed))
    L.append("")
    if not missed:
        L.append("*（没有检测到「分数中等但有时效」的消息）*")
    for s, r, who, cat, hits in missed:
        stale = ""
        age_days = (time.time() - r["ts"]) / 86400.0
        if cat == "时效" and age_days > 2:
            stale = " ⚠️**已过期 %.0f 天**" % age_days
        L.append("- **【%.1f 分】%s** · %s · %s%s" % (s, r["group_name"] or r["group_code"], who, fmt_ts(r["ts"]), stale))
        text = (r["text"] or "").replace("\n", " ⏎ ")
        if len(text) > 150:
            text = text[:150] + "…"
        L.append("  > " + text)
        L.append("  ⏰ 含「%s」（%s）" % ("」「".join(hits), cat))
    L.append("")

    # 概览
    per = {}
    for r in rows:
        per[r["group_name"] or r["group_code"]] = per.get(r["group_name"] or r["group_code"], 0) + 1
    L.append("## 📊 本期概览")
    L.append("")
    L.append("| 群 | 消息量 |")
    L.append("|---|---|")
    for k, v in sorted(per.items(), key=lambda x: -x[1]):
        L.append("| %s | %d |" % (k, v))
    L.append("")

    out = "\n".join(L)
    print(out)
    if a.out:
        d = os.path.dirname(os.path.abspath(a.out))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(out)
        print("\n[已写入] " + a.out, file=sys.stderr)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({
                "window": win_desc,
                "cutoff": cutoff,
                "top": [{"score": s, "group": r["group_name"], "who": w,
                         "ts": r["ts"], "text": r["text"], "why": y} for s, r, y, w in top],
                "missed": [{"score": s, "group": r["group_name"], "who": w,
                            "ts": r["ts"], "text": r["text"], "cat": c, "hits": h}
                           for s, r, w, c, h in missed],
            }, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
