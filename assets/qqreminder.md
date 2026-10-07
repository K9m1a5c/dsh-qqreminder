---
name: qq-reminder
description: 在本机 QQ 群聊记录里检索话题、生成摘要、找出"你可能漏了"的重要消息。当用户说"翻一下群""看看群里有什么""今天群里说了啥""帮我找群里关于 X 的信息""有没有我漏掉的""群消息统计"时使用。
---

# QQreminder —— 本机 QQ 群聊助手

把用户本机的 QQ 群聊，变成「**今天有什么我该知道的**」+「**我可能漏了什么**」。
**全程本地、只读、不联网上传。**

## 0. 铁律（必须遵守）

1. ★**先查授权开关**：读 `<ROOT>/authorization.json`。
   若 `qq_read != true`，**只能输出统计与元数据**（群名、消息量、时间跨度），
   **绝不输出任何群消息正文**，并明确告诉用户"读取开关是关的"。
2. ★**每次输出都必须声明数据截止时间**，写在第一句：
   `数据截止 MM-DD HH:MM（距现在 N 分钟）`。这个值来自索引 `meta.cutoff_ts`，**照抄，不要自己算**。
   - 距今 **> 2 小时** → 主动提醒：「数据是 N 小时前的，要最新的话请先登录一下 QQ 再刷新。」
   - 距今 **> 24 小时** → 强烈提醒：「中间的消息看不到。」
3. ★**取不到就明说，绝不编造。** 索引里没有的消息，不许"推测"出来。
   **原理**：读的是「QQ 写在硬盘上的数据库文件」，不是连着 QQ ——
   QQ 关着 → 文件冻结在最后关闭那一刻；QQ 没登录过 → 服务器上的新消息**根本没同步到本地**。
4. ★**密钥不写进对话**：只汇报"校验通过/失败"，不贴密钥文件内容。
5. ★**QQ 数据目录只读**：一个字节都不许改。
6. **问「刚刚/现在/今天」之前必须先刷新索引**，否则拿旧索引答"刚刚"等于在赌。
7. **不要只看命中的那一条，要看上下文**：群聊是一来一回的，孤立的关键词会骗人。

## 1. 环境

- 数据根目录 `<ROOT>`：默认取环境变量 `QQREMINDER_ROOT`（插件配置 `root` 也生效）
- 索引：`<ROOT>/qgd/data/index.db`（SQLite，表 `messages` / `groups` / `media` / `meta`）
- 授权：`<ROOT>/qgd/authorization.json`
- 配置：`<ROOT>/qgd/focus.json`（监工群 / 关注词 / 打分规则）
- 脚本目录：`<ROOT>`（`score.py` / `make_report.py` / `pack_material.py` / `autoconfig.py`）

`messages` 字段：`msg_id, ts, day, hour, group_code, group_name, sender_uid, sender_qq,
sender_name, direction, msg_type, subtype, kind, text, reply_seq`

### 1.1 如果 `index.db` 不存在 —— 怎么帮用户建起来

**本插件只做「分析」。** 索引 `index.db` 需要用户自己准备 —— 本仓库不含任何解密代码。
当用户说"翻一下群"但 `<ROOT>/qgd/data/index.db` 不存在时，按下面四步帮他：

**第 1 步 · 先问清楚**
> "你有一个【已经解密的】QQ 消息数据库吗？通常在 `nt_msg.db` 这样的文件里。"

- 有 → 直接跳到第 3 步
- 没有 → 第 2 步

**第 2 步 · 指路（★ 不要替用户做，也不要替用户判断安全性）**

QQ 的本地数据库是加密的，**解密不在本插件范围内**。如实告诉用户：
社区里有专门做这件事的开源项目，例如 `QQBackup/QQDecrypt`、`NapNeko/qq_dump_db`。

- ★ **必须说清**：这些工具与本插件**没有任何关联**，可能涉及逆向工程，
  **请用户自行评估合规性与风险**。
- ★ **不要**说"保证安全""官方推荐"这类话。
- ★ **不要**帮用户下载或运行这些工具。

**第 3 步 · 从明文库建索引**

明文库通常是 SQLite（表名可能是 `group_msg_table` 之类，**随 QQ 版本变化**）。
先让用户确认文件路径，然后**写一个一次性的转换脚本**（跑完即可丢弃，不必保存）。

要生成的三张表（与 `engine/load_example.py` 的写法完全一致）：

```sql
messages(msg_id INTEGER PRIMARY KEY, ts INTEGER, day TEXT, hour INTEGER,
         group_code TEXT, group_name TEXT, sender_uid TEXT, sender_qq TEXT,
         sender_name TEXT, direction TEXT, msg_type INTEGER, subtype INTEGER,
         kind TEXT, text TEXT, reply_seq INTEGER)

groups(group_code TEXT PRIMARY KEY, group_name TEXT, weight REAL,
       msg_count INTEGER, last_ts INTEGER)

meta(key TEXT PRIMARY KEY, value TEXT)
```

★ 五个必须做对的点：
1. **`ts` 是秒级 Unix 时间戳**（不是毫秒，不是字符串）
2. **`meta` 里必须有 `cutoff_ts`** —— 报告和摘要靠它声明"数据截止到几点"，缺了会报错
3. **`group_name` 填群名**（不是群号）；`sender_name` 填昵称
4. **`direction`**：自己发的填 `out`，别人发的填 `in`
5. **`msg_id` 要唯一**（用原始库的 id 或自增都行）

★ **先摸清明文库的结构再动手**：把它的表名和 `CREATE TABLE` 语句列出来看一遍，
不要照搬上面的字段名 —— 原库的字段名几乎肯定不一样，**要做的是映射**。

**第 4 步 · 验证**

```bash
python <插件目录>/engine/score.py --days 7 --top 3
```

- 能出「QQ 群摘要」 → 成功，去查 `<ROOT>/qgd/authorization.json` 把 `qq_read` 设为 `true`
- 报 "找不到索引" → 路径不对，检查 `<ROOT>/qgd/data/index.db`
- 出摘要但内容为空 → `cutoff_ts` 或 `ts` 单位错了

★ **每写完一步都真跑一次**，不要写完一堆再一起试。

## 2. 三个常用动作

### ① 话题检索（"帮我找群里关于 X 的信息"）

直接查索引（最灵活）：
```sql
SELECT ts, group_name, sender_name, text FROM messages
WHERE text LIKE '%关键词%' ORDER BY ts;
```
**必须带上前后文**（同群、时间相邻的消息），否则孤立的关键词会骗人。

### ② 生成摘要 / 找出遗漏

```bash
python score.py --days 1 --top 12 --out out/摘要.md --json out/摘要.json
```
输出：📌 最重要 N 条（带「因为」标签）+ ⚠️ 你可能漏了（score 中等但有时效/行动/疑问的）。

### ③ 生成 HTML 报告（可点进每个群）

```bash
python make_report.py --days 7                    # 或 --since 2026-10-01 --until 2026-10-06
```

## 3. 打分信号（`score.py`）

关注词命中 +6 · @我 +5 · 群主/管理员发的 +4 · 名片含身份词 +3 ·
通知强词 +3 / 弱词 +1 · 长文 +1~2 · 转发 +1 —— 最后 **× 群权重**。

「遗漏提醒」= 分数中等（2~6）但含**时效/行动/疑问**词的消息；超过 2 天标「已过期」。

## 4. 排查

| 现象 | 先查 |
|---|---|
| 输出全是旧消息 | `meta.cutoff_ts`；让用户登录 QQ 后刷新索引 |
| 说"没有数据" | 索引是否建好；`authorization.json` 的 `qq_read` |
| 某个群没进去 | `focus.json` 的「监工群」列表 |
| 读取报错 | `meta.read_errors` 是否 > 0 |

## 5. 边界（要如实告诉用户）

- **看不到 QQ 没同步下来的消息**，也不会自动知道"还有新消息"。
- 只读已落盘的本地数据；**不登录、不碰协议端、不连腾讯服务器**。
- 涉及真人的姓名、私事，**只在用户自己的报告里出现，不要外传、不要写进公开产物**。
