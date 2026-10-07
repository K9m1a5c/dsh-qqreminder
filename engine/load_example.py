# -*- coding: utf-8 -*-
"""QQreminder · 示例数据装载器

把 examples/student.json（虚构数据）灌成一个**完整的演示环境**：

    examples/demo-root/
        qgd/
            data/index.db          ← 索引（表结构与真实索引一致）
            focus.json             ← 示例配置（监工群 / 关注词 / 打分规则）
            authorization.json     ← 授权开关（qq_read: true）

用法：
    python examples/load_example.py                    # 生成/刷新演示环境
    python examples/load_example.py --json xxx.json    # 用别的示例文件
    python examples/load_example.py --print-env        # 打印切换命令

★ 演示环境与真实数据【完全隔离】——切换只需一个环境变量：
      $env:QQREMINDER_ROOT = "<仓库>/examples/demo-root"
  切回来把它删掉即可。真实数据一个字节都不会被动。
"""
import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("QQREMINDER_ROOT") or os.path.join(HERE, "demo-root")
QGD = os.path.join(ROOT, "qgd")

DEFAULT_FOCUS = {
    "_说明": "示例配置。监工群 / 关注词可按自己的场景改。",
    "version": 1,
    "主人": {"qq": "sample-self", "昵称": "ME"},
    "监工群": [],
    "关注词": {
        "选课": 6, "截止": 6, "报名": 5, "考试": 5, "通知": 5, "作业": 5,
        "班会": 4, "面试": 4, "实验室": 4, "项目": 4, "资料": 3, "请假": 4,
    },
    "降权词": {"抽奖": -3, "优惠": -3, "出售": -3, "求购": -3, "私聊": -2, "哈哈": -2},
    "发送者权重": {},
    "身份词": ["老师", "辅导员", "班助", "学长", "学姐", "班长", "学习委员", "社长"],
    "通知词": {"强": ["通知", "公告", "全体成员", "截止", "务必", "重要", "面试安排", "报名"],
               "弱": ["提醒", "注意", "请", "安排"]},
    "遗漏提示词": {"时效": ["截止", "之前", "前完成", "开放", "开始", "明天", "本周"],
                   "行动": ["报名", "提交", "参加", "联系", "填写", "确认"],
                   "疑问": ["有没有", "怎么办", "能不能", "请问"]},
    "打分": {"遗漏区间": [2, 6], "过期天数": 2},
}


def ts_of(s):
    for f in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return int(dt.datetime.strptime(s.strip(), f).timestamp())
        except ValueError:
            continue
    raise SystemExit("时间格式不对：%s" % s)


def build_messages_schema(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS groups (
            group_code TEXT PRIMARY KEY, group_name TEXT, weight REAL DEFAULT 1.0,
            msg_count INTEGER DEFAULT 0, last_ts INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS messages (
            msg_id INTEGER PRIMARY KEY, ts INTEGER, day TEXT, hour INTEGER,
            group_code TEXT, group_name TEXT, sender_uid TEXT, sender_qq TEXT,
            sender_name TEXT, direction TEXT DEFAULT 'in', msg_type INTEGER DEFAULT 0,
            subtype INTEGER DEFAULT 0, kind TEXT DEFAULT 'text', text TEXT,
            reply_seq INTEGER DEFAULT 0);
        CREATE INDEX IF NOT EXISTS idx_msg_group_ts ON messages(group_code, ts);
        CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts);
        CREATE INDEX IF NOT EXISTS idx_msg_text ON messages(text);
        CREATE TABLE IF NOT EXISTS media (msg_id INTEGER, path TEXT, kind TEXT);
    """)


def build_roles(data):
    """群号 + uid → 显示名（优先名片，否则昵称），并带角色后缀"""
    m = {}
    for x in data.get("members", []):
        tag = x.get("角色") or ""
        tag = "" if tag in ("", "成员") else tag
        disp = (x.get("名片") or "").strip() or (x.get("昵称") or "?").strip()
        m[(str(x["群号"]), str(x["uid"]))] = (disp, tag, str(x.get("qq") or ""))
    return m


def link_program_files(repo):
    """★ 把「程序文件」链进演示环境。

    设计教训（2026-10-06）：一开始只往演示环境写数据，结果切过去之后
    【所有按钮都没反应】——因为插件既需要【数据】（索引/配置/授权），
    也需要【程序】（脚本 + venv）。演示环境只该装数据，程序用链接指回仓库：
    零拷贝，而且改了源码立刻同步。
    """
    import subprocess
    made = []

    def junc(tgt, lnk, label):
        if os.path.isdir(tgt) and not os.path.exists(lnk):
            subprocess.run(["cmd", "/c", "mklink", "/J", lnk, tgt], capture_output=True)
            made.append(label)

    def hard(tgt, lnk, label):
        if os.path.isfile(tgt) and not os.path.exists(lnk):
            subprocess.run(["cmd", "/c", "mklink", "/H", lnk, tgt], capture_output=True)
            made.append(label)

    for d in ("venv", "tools", "out"):
        junc(os.path.join(repo, d), os.path.join(ROOT, d), d + "/")
    # qgd/app 故意【不链接】：演示环境不该能从真实 QQ 重建索引
    for f in ("score.py", "make_report.py", "pack_material.py", "autoconfig.py"):
        hard(os.path.join(repo, f), os.path.join(ROOT, f), f)
    return made


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(HERE, "demo-student.json") if
                    os.path.exists(os.path.join(HERE, "demo-student.json"))
                    else os.path.join(HERE, "student.json"))
    ap.add_argument("--data", help="把示例数据生成到哪个数据目录（默认取 QQREMINDER_ROOT）")
    ap.add_argument("--link", action="store_true", help="额外的程序链接（本机开发用；插件版不需要）")
    ap.add_argument("--print-env", action="store_true")
    a = ap.parse_args()

    global ROOT, QGD
    if a.data:
        ROOT = os.path.abspath(a.data)
        QGD = os.path.join(ROOT, "qgd")
    os.makedirs(os.path.join(QGD, "data"), exist_ok=True)

    with open(a.json, encoding="utf-8") as f:
        data = json.load(f)

    print("# QQreminder · 示例环境生成")
    print()
    print("  数据来源：%s" % os.path.basename(a.json))
    print("  ★ %s" % data.get("note", ""))
    print()

    os.makedirs(os.path.join(QGD, "data"), exist_ok=True)
    db = os.path.join(QGD, "data", "index.db")
    if os.path.exists(db):
        os.remove(db)
    con = sqlite3.connect(db)
    build_messages_schema(con)

    roles = build_roles(data)
    cutoff = int(dt.datetime.now().timestamp()) - 600   # 数据截止 = 10 分钟前（永远新鲜）
    me_qq = str(DEFAULT_FOCUS["主人"]["qq"])

    # ★ 时间平移：让演示数据永远落在"最近 N 天"窗口内。
    #   否则写死的日期会随时间滑出窗口，报告越用越空。
    _raw = [ts_of(x["时间"]) for x in data.get("messages", [])]
    _shift = (int(dt.datetime.now().timestamp()) - 3600) - max(_raw) if _raw else 0

    rows, n = [], 0
    for x in data.get("messages", []):
        n += 1
        g = str(x["群号"])
        uid = str(x["发送者"])
        disp, tag, qq = roles.get((g, uid), ("?", "", ""))
        t = ts_of(x["时间"]) + _shift
        rows.append((n, t, dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d"),
                     dt.datetime.fromtimestamp(t).hour, g,
                     next((y["群名"] for y in data["groups"] if str(y["群号"]) == g), g),
                     uid, qq, disp + (("（%s）" % tag) if tag else ""),
                     "in", 0, 0, x.get("类型", "text"), x.get("正文", ""), 0))
    con.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    for y in data["groups"]:
        g = str(y["群号"])
        cnt = sum(1 for r in rows if r[4] == g)
        last = max([r[1] for r in rows if r[4] == g] or [0])
        con.execute("INSERT OR REPLACE INTO groups VALUES (?,?,?,?,?)",
                    (g, y["群名"], y.get("权重", 1.0), cnt, last))

    meta = {"built_at": int(dt.datetime.now().timestamp()), "cutoff_ts": cutoff,
            "window_days": 30, "schema_version": "example-1",
            "scan_rows": len(rows), "groups_scanned": len(data["groups"]),
            "read_errors": 0, "bad_days": "[]", "source": "example"}
    con.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)",
                    [(k, str(v)) for k, v in meta.items()])
    con.commit()
    con.close()

    # focus.json：把示例群按 data 里的权重写进监工群
    focus = dict(DEFAULT_FOCUS)
    focus["监工群"] = [{"群号": str(y["群号"]), "群名": y["群名"],
                        "权重": y.get("权重", 1.0), "备注": "示例"}
                       for y in data["groups"]]
    with open(os.path.join(QGD, "focus.json"), "w", encoding="utf-8") as f:
        json.dump(focus, f, ensure_ascii=False, indent=2)

    with open(os.path.join(QGD, "authorization.json"), "w", encoding="utf-8") as f:
        json.dump({"_说明": "示例环境的授权开关（虚构数据，无隐私风险）。",
                   "qq_read": True, "updated_at": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                   "updated_by": "示例装载器", "scope": {"允许读取": ["示例消息正文"]},
                   "note": "演示环境专用。"}, f, ensure_ascii=False, indent=2)

    print("  群 %d 个 · 成员 %d 人 · 消息 %d 条" % (len(data["groups"]), len(roles), len(rows)))
    print("  数据截止：%s" % dt.datetime.fromtimestamp(cutoff).strftime("%Y-%m-%d %H:%M"))

    # ★ 关键：把程序文件（脚本/venv）链进来，否则切过去之后按钮全都没反应
    repo = os.path.dirname(HERE)
    linked = link_program_files(repo) if a.link else []
    print("  程序链接：%s" % ("、".join(linked) if linked else "（已存在，无需重建）"))
    print("  演示根目录：%s" % ROOT)
    print()
    for y in data["groups"]:
        cnt = sum(1 for r in rows if r[4] == str(y["群号"]))
        print("    ×%-4s %-34s %d 条" % (y.get("权重", 1.0), y["群名"], cnt))
    print()
    print("✅ 生成完毕。切换演示环境：")
    print()
    print('    $env:QQREMINDER_ROOT = "%s"' % ROOT)
    print("    # 想让插件也用演示环境：把上面的环境变量设成【用户级】再重启 DSH")
    print("    # 切回真实数据：删掉这个环境变量（或重启后不设）")


if __name__ == "__main__":
    main()
