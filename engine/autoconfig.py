# -*- coding: utf-8 -*-
"""QQ 群摘要 · 一键初始化分析引擎（autoconfig）

目的：解决【冷启动】—— 第一次用的人，面板是空的。本脚本扫一遍本地数据，
自动给出三份推荐：
  ① 值得监工的群（含建议权重 + 理由）
  ② 重要人物（群主 / 管理员 / 名片带身份词；已排除"我"与官方机器人）
  ③ 候选关注词（从"通知类消息"里挖，n-gram + IDF + 跨群验证 + 黑名单过滤）

纯 Python 标准库，不依赖 jieba 等外部分词（开源友好）。

v2 修订（2026-10-06，据首次实测）：
  · 群权重改为「权威且带通知」才算信号 + 贝叶斯平滑 + 连续映射（不再撞顶）
  · 人物榜排除主人自己与官方机器人
  · 关键词加黑名单（通知词/套话/碎片刻度）+ 跨群加分 + 更严格去重
"""
import argparse
import collections
import json
import math
import os
import re
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
INDEX = os.path.join(ROOT, "data", "index.db")
GI = os.path.join(ROOT, "data", "plain", "group_info.db")
NM = os.path.join(ROOT, "data", "plain", "nt_msg.db")
FOCUS = os.path.join(ROOT, "focus.json")

NOTICE_STRONG = ["通知", "公告", "@全体", "全体成员", "报名", "截止", "考试", "选课", "答辩",
                 "补考", "重修", "注册", "务必", "提交", "作业", "安排", "开会", "班会", "领取"]
IDENT = ["老师", "导员", "辅导员", "班助", "班主任", "教授", "主任", "队长", "学长", "学姐"]

# 官方/机器人账号（不计入"重要人物"）
BOT_NAMES = {"q群管家", "qq", "微信", "系统消息", "群助手", "腾讯新闻", "qq安全中心"}

# n-gram 黑名单：通知词本身 + 套话 + 通用虚词（完全匹配即丢）
KW_BLACK = set(NOTICE_STRONG) | {
    "全体成员", "同学们", "的同学", "希望大家", "大家", "注意", "务必", "记得", "请大家",
    "永久", "久使用", "室设计", "个月", "时间", "地点", "安排", "相关", "内容", "情况",
    "工作", "要求", "以下", "如下", "注意事", "意事项", "事项", "请各位", "各位",
    "我们", "你们", "他们", "自己", "什么", "怎么", "这个", "那个", "可以", "不是",
    "因为", "所以", "但是", "如果", "还是", "已经", "应该", "可能", "现在", "时候",
    # 通用词（看着像关键词，其实跟"主人的关切"无关）
    "同学", "学生", "参与", "完成", "具体", "正式", "不要", "希望", "上课", "需要",
    "进行", "之后", "之前", "一起", "一下", "各位", "相关", "方式", "规定", "注意",
    "重要", "积极", "认真", "及时", "提前", "自行", "尽快", "统一", "负责", "组织",
    "学院", "学校", "老师", "班级", "年级", "大家",
    # 实测第二轮残留的通用词
    "基础", "所有", "说明", "结束", "发言", "生活", "位同学", "各位同", "下学期", "本学期",
    "相关事", "关事项", "的具体", "体的", "新的", "其他", "如果", "这样",
}
# 用于"字集包含"过滤：n-gram 的所有字都落在一个长黑名单词里 → 判为碎片
BLACK_CHARSETS = [set(w) for w in KW_BLACK if len(w) >= 3]
# 通知集中度门槛：太低说明这词在闲聊里也满天飞，不像是"要关注的事"
MIN_RATIO = 0.55
STOP = set("""
的 了 是 在 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 这
我们 你们 他们 什么 怎么 这个 那个 这样 那样 可以 不是 就是 但是 因为 所以 如果 还是
现在 时候 已经 应该 可能 知道 觉得 感觉 真的 好像 然后 而且 虽然 只是
哈哈 哈哈哈 嘿嘿 emmm 嗯 哦 啊 吧 呢 吗 呀 嘛 啦 咯 哇 唉 诶
图片 表情 转发 聊天记录 消息 收到 谢谢 感谢 好的 行 行吧 对 对的 没事 问题 有点 直接
""".split())

CJK = re.compile(r"[\u4e00-\u9fff]+")


def load_me():
    """主人自己的 QQ（用于从人物榜里排除）"""
    try:
        with open(FOCUS, encoding="utf-8") as f:
            return str(json.load(f).get("主人", {}).get("qq") or "").strip()
    except Exception:
        return ""


def build_members(me_qq):
    by_uid, by_qq, persons = {}, {}, {}
    if not os.path.exists(GI):
        return by_uid, by_qq, persons
    con = sqlite3.connect(GI)
    try:
        rows = con.execute(
            'SELECT "60001","1000","1002","64003","64017","64035","20002" FROM group_member3'
        ).fetchall()
    except sqlite3.DatabaseError:
        return by_uid, by_qq, persons
    finally:
        con.close()
    for g, uid, qq, card, r17, r35, nick in rows:
        role = "群主" if (r17 == 1 or r35 == 3) else ("管理员/老师" if r17 == 2 else None)
        info = {"role": role, "card": (card or "").strip(), "nick": (nick or "").strip()}
        if uid:
            by_uid[(g, uid)] = info
        if qq:
            by_qq[(g, qq)] = info
        if role or any(w in info["card"] for w in IDENT):
            key = str(qq or uid or "")
            if not key:
                continue
            # 排除主人自己与官方机器人
            if me_qq and str(qq) == me_qq:
                continue
            if (info["nick"] or "").lower() in BOT_NAMES:
                continue
            p = persons.setdefault(key, {"qq": qq, "nick": info["nick"], "card": info["card"],
                                         "groups": set(), "roles": set()})
            p["groups"].add(g)
            if role:
                p["roles"].add(role)
            if info["card"]:
                p["card"] = info["card"]
            if info["nick"]:
                p["nick"] = info["nick"]
    return by_uid, by_qq, persons


def is_fragment(g):
    """判碎片：n-gram 的所有字都落在同一个长黑名单词里（如 全体成 ⊂ 全体成员）"""
    gs = set(g)
    for cs in BLACK_CHARSETS:
        if gs <= cs:
            return True
    return False


def extract_ngrams(text):
    out = []
    for run in CJK.findall(text or ""):
        n = len(run)
        for size in (2, 3, 4):
            for i in range(n - size + 1):
                g = run[i:i + size]
                if g in STOP or g in KW_BLACK or is_fragment(g):
                    continue
                out.append(g)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--min-msgs", type=int, default=20, help="群消息量低于此值不推荐监工")
    ap.add_argument("--top-keywords", type=int, default=24)
    ap.add_argument("--json")
    a = ap.parse_args()

    me_qq = load_me()
    since = time.time() - a.days * 86400
    con = sqlite3.connect(INDEX)
    con.row_factory = sqlite3.Row
    meta = dict(con.execute("SELECT key,value FROM meta").fetchall())
    rows = [dict(r) for r in con.execute("SELECT * FROM messages WHERE ts >= ?", (since,))]
    con.close()

    by_uid, by_qq, persons = build_members(me_qq)

    at_me = set()
    if os.path.exists(NM):
        c2 = sqlite3.connect(NM)
        try:
            at_me = {int(x[0]) for x in c2.execute("SELECT 40001 FROM group_at_me_msg")}
        except sqlite3.DatabaseError:
            pass
        finally:
            c2.close()

    notice_of = {}
    for r in rows:
        t = r["text"] or ""
        notice_of[r["msg_id"]] = any(w in t for w in NOTICE_STRONG)

    # ── ① 群信号密度 ────────────────────────────────────────
    gstat = {}
    for r in rows:
        g = r["group_code"]
        s = gstat.setdefault(g, {"code": g, "name": r["group_name"], "n": 0, "at_me": 0,
                                 "auth": 0, "auth_notice": 0, "notice": 0, "long": 0,
                                 "senders": set(), "first": r["ts"], "last": r["ts"]})
        s["n"] += 1
        s["first"] = min(s["first"], r["ts"])
        s["last"] = max(s["last"], r["ts"])
        s["senders"].add(r["sender_uid"] or r["sender_qq"])
        t = r["text"] or ""
        is_notice = notice_of.get(r["msg_id"], False)
        if int(r["msg_id"]) in at_me:
            s["at_me"] += 1
        info = by_uid.get((g, r["sender_uid"])) or by_qq.get((g, r["sender_qq"]))
        is_auth = bool(info and info["role"])
        if is_auth:
            s["auth"] += 1
        if is_auth and is_notice:
            # ★ 关键修正：只有"权威 + 通知"才是有价值信号（群主发闲聊不算）
            s["auth_notice"] += 1
        if is_notice:
            s["notice"] += 1
        if len(t) >= 200 and is_notice:
            s["long"] += 1

    # 打分：绝对信号量(log1p) × 占比修正 —— 兼顾"通知多"和"闲聊少"
    #   · 只用占比：水群里 10 条通知会被 130 条闲聊稀释掉（实测活跃群被压到 ×0.9）
    #   · 只用绝对量：大群无脑胜出
    groups = []
    for s in gstat.values():
        n = max(1, s["n"])
        signal_abs = s["auth_notice"] * 4 + s["notice"] * 2 + s["at_me"] * 5 + s["long"] * 1
        ratio = signal_abs / n
        # 小样本收缩：消息量太少时占比不可信
        conf = n / (n + 20.0)
        score = math.log1p(signal_abs) * (0.45 + 0.55 * min(1.0, ratio * 3.0)) * conf
        weight = round(0.6 + 1.4 * min(1.0, score / 4.0), 1)
        reasons = []
        if s["auth_notice"]:
            reasons.append("权威发的通知 %d 条" % s["auth_notice"])
        if s["at_me"]:
            reasons.append("@我 %d 条" % s["at_me"])
        if s["notice"]:
            reasons.append("通知类共 %d 条" % s["notice"])
        if s["long"]:
            reasons.append("通知型长文 %d 条" % s["long"])
        groups.append({
            "群号": str(s["code"]), "群名": s["name"], "消息量": s["n"],
            "发言人数": len(s["senders"]),
            "权威条数": s["auth"], "权威通知条数": s["auth_notice"],
            "通知条数": s["notice"], "at我条数": s["at_me"], "长文条数": s["long"],
            "信号密度": round(score, 3), "建议权重": weight,
            # 纯通知群豁免 min-msgs：7 条里 5 条是权威通知，比 200 条水群有价值得多
            "推荐": (
                (s["n"] >= a.min_msgs and (s["auth_notice"] >= 2 or s["notice"] >= 5 or s["at_me"] >= 1))
                or (s["n"] >= 3 and s["auth_notice"] >= 3 and s["auth_notice"] / n >= 0.4)
            ),
            "理由": "、".join(reasons) or "基本是闲聊，建议不监工",
        })
    groups.sort(key=lambda x: -x["信号密度"])

    # ── ② 重要人物 ─────────────────────────────────────────
    people = []
    for key, p in persons.items():
        people.append({
            "QQ": p["qq"], "昵称": p["nick"], "名片": p["card"],
            "在几个群有身份": len(p["groups"]), "角色": "、".join(sorted(p["roles"])) or "名片含身份词",
        })
    people.sort(key=lambda x: (-x["在几个群有身份"], x["昵称"]))

    # ── ③ 候选关注词 ───────────────────────────────────────
    texts = [r for r in rows if len((r["text"] or "")) >= 4]
    notice_texts = [r for r in texts if notice_of.get(r["msg_id"])]
    n_all = max(1, len(texts))

    df_all, groups_of = collections.Counter(), collections.defaultdict(set)
    for r in texts:
        for g in set(extract_ngrams(r["text"])):
            df_all[g] += 1
            groups_of[g].add(r["group_code"])
    tf_notice, df_notice = collections.Counter(), collections.Counter()
    for r in notice_texts:
        for g in extract_ngrams(r["text"]):
            tf_notice[g] += 1
        for g in set(extract_ngrams(r["text"])):
            df_notice[g] += 1

    cand = []
    for g, tf in tf_notice.items():
        if tf < 3:
            continue
        dfa, dfn = df_all.get(g, 0), df_notice.get(g, 0)
        if dfa < 3:
            continue
        ratio = dfn / max(1, dfa)
        if ratio < MIN_RATIO:            # ★ 通知集中度太低 → 闲聊里也满天飞，不算关注点
            continue
        idf = math.log((n_all + 1) / (dfa + 1)) + 1.0
        xg = len(groups_of[g])           # 跨群出现 → 更像通用关注点
        score = tf * ratio * idf * (1 + 0.18 * (len(g) - 2)) * (1 + 0.22 * (xg - 1))
        cand.append({"词": g, "得分": round(score, 1), "通知内频次": tf,
                     "出现消息数": dfa, "跨群数": xg, "通知集中度": round(ratio, 2)})
    cand.sort(key=lambda x: -x["得分"])

    picked, seen = [], []
    for c in cand:
        w = c["词"]
        if any(w != s and w in s for s in seen):     # 被已选长词包含 → 丢
            continue
        if any(s != w and s in w for s in seen):     # 包含已选短词 → 丢（长词更具体）
            continue
        picked.append(c)
        seen.append(w)
        if len(picked) >= a.top_keywords:
            break

    result = {
        "generated_at": int(time.time()), "window_days": a.days,
        "cutoff_ts": int(meta.get("cutoff_ts") or 0), "scanned_messages": len(rows),
        "me": me_qq, "groups": groups, "people": people[:20], "keywords": picked,
    }

    L = ["# QQ 群摘要 · 一键初始化建议", "",
         "> 窗口 %.0f 天 · 扫描 %d 条消息 · 共 %d 个群 · 已排除本人与官方机器人" % (a.days, len(rows), len(groups)),
         "", "## ① 推荐监工的群（按信号密度排序）", "",
         "| 推荐 | 群名 | 消息量 | 权威通知 | 通知共 | @我 | 信号密度 | 建议权重 | 理由 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for g in groups[:18]:
        L.append("| %s | %s | %d | %d | %d | %d | %.3f | ×%.1f | %s |" % (
            "✅" if g["推荐"] else "—", g["群名"], g["消息量"], g["权威通知条数"],
            g["通知条数"], g["at我条数"], g["信号密度"], g["建议权重"], g["理由"]))
    L += ["", "## ② 重要人物（跨群有身份，已排除本人与机器人）", "",
          "| 昵称 | 名片 | 角色 | 覆盖群数 |", "|---|---|---|---|"]
    for p in people[:15]:
        L.append("| %s | %s | %s | %d |" % (p["昵称"] or "?", p["名片"] or "—", p["角色"], p["在几个群有身份"]))
    L += ["", "## ③ 候选关注词", "",
          "| 词 | 得分 | 通知内频次 | 出现消息数 | 跨群数 | 通知集中度 |", "|---|---|---|---|---|---|"]
    for k in picked:
        L.append("| **%s** | %.1f | %d | %d | %d | %.2f |" % (
            k["词"], k["得分"], k["通知内频次"], k["出现消息数"], k["跨群数"], k["通知集中度"]))
    L.append("")

    out = "\n".join(L)
    print(out)
    if a.json:
        d = os.path.dirname(os.path.abspath(a.json))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        sys.stderr.write("[已写入] %s\n" % a.json)


if __name__ == "__main__":
    main()
