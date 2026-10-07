/**
 * dsh-qqreminder · 宿主侧插件
 *
 * 只做一件事：把本地的 QQreminder 能力通过 HTTP 暴露给 DSH Web UI（含未来的面板）。
 * 所有读取都在本机完成；不联网上传；不修改 QQ 数据目录。
 *
 * 安全：
 *   - 所有端点只接受本机回环地址的请求
 *   - 写操作额外要求自定义头 `x-dsh-qqreminder: 1`（阻止跨站 simple-request / CSRF）
 *
 * 降级：
 *   - 拿不到 webServer 服务时【不抛错】，只记日志并静默退出，绝不破坏插件树启动
 */
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile, writeFile, stat } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";
import { join, dirname } from "node:path";

const execFileAsync = promisify(execFile);

export const name = "dsh-qqreminder";

/**
 * 声明依赖的服务：cordis 会先启动它们，再执行 apply()。
 *   webServer —— 提供 HTTP 端点（面板用）
 *   skills    —— 让插件【自带技能】，这样别人的 AI 一装上就知道怎么用
 *   tools     —— 让插件注册【可被 AI 调用的工具】，AI 能自己发现"我能查本机 QQ"
 * 任何一个拿不到，都只是少一项能力，绝不破坏插件树启动。
 */
export const inject = ["webServer", "skills", "tools"];

/**
 * 数据根目录解析顺序：
 *   ① 环境变量 QQREMINDER_ROOT（推荐，跨平台）
 *   ② 插件配置 root
 *   ③ 兜底：用户主目录下的 QQreminder（中性，不含任何开发者信息）
 */
const DEFAULT_ROOT = process.env.QQREMINDER_ROOT || join(homedir(), "QQreminder");

/* ── 自带技能（照 DSH 官方 skill-badge 插件的 provider 契约）──── */
const SKILL_PROVIDER = "qqreminder";
const SKILL_URL = new URL("../assets/qqreminder.md", import.meta.url);
const SKILL_RANK = 300; // custom 档（官方档位：100 项目 / 200 .agents / 300 custom / 400 dshHome / 500 用户 / 600 内置）

const SKILL_CANDIDATE = {
  name: "qq-reminder",
  description:
    "在本机 QQ 群聊记录里检索话题、生成摘要、找出「你可能漏了」的重要消息。" +
    "当用户说「翻一下群」「看看群里有什么」「今天群里说了啥」「帮我找群里关于 X 的信息」" +
    "「有没有我漏掉的」「群消息统计」时使用。全程本地、只读。",
  invocation: { modelInvocable: true, userInvocable: true },
  provider: SKILL_PROVIDER,
  source: "plugin",
  resourceBase: { kind: "directory", path: fileURLToPath(new URL("../assets/", import.meta.url)) },
  rank: SKILL_RANK,
  locator: SKILL_URL,
};

function makeSkillProvider() {
  return {
    name: SKILL_PROVIDER,
    list: () => Promise.resolve([SKILL_CANDIDATE]),
    async get() {
      const content = await readFile(SKILL_URL, "utf8");
      return {
        name: SKILL_CANDIDATE.name,
        description: SKILL_CANDIDATE.description,
        invocation: SKILL_CANDIDATE.invocation,
        provider: SKILL_CANDIDATE.provider,
        source: SKILL_CANDIDATE.source,
        resourceBase: SKILL_CANDIDATE.resourceBase,
        content,
      };
    },
  };
}

/**
 * 注册可被 AI 调用的工具。
 * DSH 的工具定义契约（`parameters` 是字段 DSL，`execute` 是执行函数）在不同版本上
 * 略有差异，所以这里【探测式注册】：任何一种写法成功就够，全都失败也只记日志。
 */
function registerTools(ctx, P) {
  const tools = ctx.get("tools");
  if (tools === undefined || tools === null) {
    ctx.logger?.warn?.("[qqreminder] tools 服务不可用，跳过工具注册（面板仍可用）");
    return;
  }
  const methods = ["defineTool", "define", "create", "register"].filter((m) => typeof tools[m] === "function");
  ctx.logger?.info?.("[qqreminder] tools 服务可用方法：" + methods.join(", "));

  const def = (spec) => (typeof tools.defineTool === "function" ? tools.defineTool(spec) : spec);

  const specs = [
    {
      name: "qq_search",
      description:
        "在本机 QQ 群聊记录里按关键词检索消息，返回「消息 + 前后文」。" +
        "当用户想知道「群里有没有关于某个话题的信息」时调用。全程本地、只读。",
      parameters: {
        query: { type: "string", required: true, description: "要检索的关键词，例如「卓越班」「选课」" },
        days: { type: "number", required: false, description: "只搜最近 N 天，默认 7；传 0 表示不限" },
        limit: { type: "number", required: false, description: "最多返回多少条，默认 40" },
      },
      async execute(args = {}) {
        const q = String(args.query || "").trim();
        if (!q) return { ok: false, error: "query 不能为空" };
        return await runPy(P.py, P.search,
          ["--query", q, "--days", String(args.days ?? 7), "--limit", String(args.limit ?? 40)],
          { timeout: 120000 });
      },
    },
    {
      name: "qq_digest",
      description:
        "把本机 QQ 群聊整理成摘要：最重要的 N 条 + 「你可能漏了」的时效性消息，并声明数据截止时间。" +
        "当用户说「翻一下群」「今天群里说了啥」时调用。全程本地、只读。",
      parameters: {
        days: { type: "number", required: false, description: "时间窗天数，默认 1" },
        top: { type: "number", required: false, description: "最重要的条数，默认 12" },
      },
      async execute(args = {}) {
        return await runPy(P.py, P.score,
          ["--days", String(args.days ?? 1), "--top", String(args.top ?? 12),
           "--out", join(P.out, "摘要-工具.md"), "--json", join(P.out, "摘要-工具.json")],
          { timeout: 180000 });
      },
    },
  ];

  for (const spec of specs) {
    try {
      tools.register(def(spec));
      ctx.logger?.info?.("[qqreminder] 已注册工具：" + spec.name);
    } catch (e) {
      ctx.logger?.warn?.("[qqreminder] 工具 " + spec.name + " 注册失败：" + String(e && e.message ? e.message : e));
    }
  }
}

/* ★ 分析引擎【随插件走】（包内 engine/），与用户数据彻底分离 ——
   这样别人装上插件就自带引擎，只需要提供自己的数据目录。 */
const ENGINE = fileURLToPath(new URL("../engine/", import.meta.url)).replace(/\\/g, "/");

function paths(dataRoot) {
  const r = (dataRoot || DEFAULT_ROOT).replace(/\\/g, "/");
  const qgd = r + "/qgd";
  return {
    // —— 数据（用户目录，可切换）——
    root: r,
    qgd,
    out: r + "/out",
    auth: qgd + "/authorization.json",
    focus: qgd + "/focus.json",
    index: qgd + "/data/index.db",
    // 可选：QQ 建索引脚本（不属于开源分析层，有则能用"刷新索引"）
    build: qgd + "/app/scripts/ntqq_build.py",
    // —— 引擎（随插件，固定）——
    engine: ENGINE,
    py: null, // 运行时自动探测（venv → python3 → python → py）
    score: ENGINE + "score.py",
    autoconfig: ENGINE + "autoconfig.py",
    pack: ENGINE + "pack_material.py",
    report: ENGINE + "make_report.py",
    search: ENGINE + "search_topic.py",
    loader: ENGINE + "load_example.py",
    demoJson: ENGINE + "demo-student.json",
  };
}

/* ── HTTP 小工具 ─────────────────────────────────────────── */

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  res.end(body);
}

function isLocal(req) {
  const a = (req.socket && req.socket.remoteAddress) || "";
  return a === "127.0.0.1" || a === "::1" || a === "::ffff:127.0.0.1" || a.endsWith("127.0.0.1");
}

function trusted(req) {
  return isLocal(req) && req.headers["x-dsh-qqreminder"] === "1";
}

async function readJsonFile(p, fallback = null) {
  try {
    return JSON.parse(await readFile(p, "utf8"));
  } catch {
    return fallback;
  }
}

async function readBody(req, limit = 256 * 1024) {
  return new Promise((resolve, reject) => {
    let n = 0;
    const chunks = [];
    req.on("data", (c) => {
      n += c.length;
      if (n > limit) {
        reject(new Error("payload too large"));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on("end", () => {
      const s = Buffer.concat(chunks).toString("utf8");
      if (!s) return resolve({});
      try {
        resolve(JSON.parse(s));
      } catch {
        reject(new Error("invalid json body"));
      }
    });
    req.on("error", reject);
  });
}

/** 当前数据根目录（apply 时设置），会作为 QQREMINDER_ROOT 传给引擎脚本 */
let DATA_ROOT = null;

/** 找可用的 Python 3：优先 venv，其次 PATH 里的 python3 / python / py —— 不强制依赖 venv */
let _pyExe = null;
async function findPython(preferred) {
  if (preferred && existsSync(preferred)) return preferred;
  if (_pyExe) return _pyExe;
  for (const c of ["python3", "python", "py"]) {
    try {
      await execFileAsync(c, ["-c", "import sys;raise SystemExit(0 if sys.version_info[0]==3 else 1)"],
        { timeout: 8000, windowsHide: true });
      _pyExe = c;
      return c;
    } catch {
      /* 试下一个 */
    }
  }
  throw new Error("找不到可用的 Python 3 —— 请安装 Python 3.8+ 并确保它在 PATH 中（引擎只用到标准库）。");
}

/** 跑 python 脚本，返回 {ok, code, stdout, stderr} */
async function runPy(py, script, args, { timeout = 120000 } = {}) {
  try {
    const exe = await findPython(py);
    if (script && !existsSync(script)) {
      return { ok: false, code: -2, stdout: "",
               stderr: "找不到引擎脚本：" + script + "\n（开源版自带 engine/；若已删除，请重新安装插件。）" };
    }
    const { stdout, stderr } = await execFileAsync(exe, [script, ...args], {
      timeout,
      windowsHide: true,
      maxBuffer: 32 * 1024 * 1024,
      env: {
        ...process.env,
        PYTHONIOENCODING: "utf-8",
        ...(DATA_ROOT ? { QQREMINDER_ROOT: DATA_ROOT } : {}),
      },
    });
    return { ok: true, code: 0, stdout: String(stdout), stderr: String(stderr) };
  } catch (e) {
    return {
      ok: false,
      code: typeof e?.code === "number" ? e.code : -1,
      stdout: String(e?.stdout ?? ""),
      stderr: String(e?.stderr ?? e?.message ?? "unknown error"),
    };
  }
}

/* ── 插件主体 ────────────────────────────────────────────── */

export function apply(ctx, config = {}) {
  const P = paths(config.root);
  DATA_ROOT = P.root;
  ctx.logger?.info?.("[qqreminder] 数据目录=" + P.root + "　引擎目录=" + P.engine);

  /* ① 自带技能：让别人的 AI 装上就知道怎么用（拿不到 skills 也不影响面板） */
  try {
    const skills = ctx.get("skills");
    if (skills && typeof skills.registerProvider === "function") {
      ctx.effect(() => skills.registerProvider(() => makeSkillProvider()));
      ctx.logger?.info?.("[qqreminder] 已注册自带技能 qq-reminder");
    } else {
      ctx.logger?.warn?.("[qqreminder] skills 服务不可用，跳过技能注册");
    }
  } catch (e) {
    ctx.logger?.warn?.("[qqreminder] 技能注册失败：" + String((e && e.message) || e));
  }

  /* ② 注册可被 AI 调用的工具 */
  try {
    registerTools(ctx, P);
  } catch (e) {
    ctx.logger?.warn?.("[qqreminder] 工具注册失败：" + String((e && e.message) || e));
  }


  const webServer = ctx.get("webServer");
  if (!webServer || typeof webServer.register !== "function") {
    ctx.logger?.warn?.("[qqreminder] webServer 服务不可用，跳过 API 注册（不影响启动）");
    return;
  }

  const reg = (route) => {
    const dispose = webServer.register(route);
    if (typeof dispose === "function") {
      ctx.effect(() => dispose, `dsh-qqreminder: ${route.path}`);
    }
  };

  /* ① 状态：授权 / 索引 / 关注画像 / 群列表 */
  reg({
    kind: "exact",
    path: "/api/qqreminder/status",
    handler: async (req, res) => {
      if (req.method !== "GET") return sendJson(res, 405, { error: "method not allowed" });
      if (!isLocal(req)) return sendJson(res, 403, { error: "local only" });

      const auth = (await readJsonFile(P.auth)) || { qq_read: false, _missing: true };
      const focus = (await readJsonFile(P.focus)) || null;

      let index = null;
      try {
        const { stdout } = await execFileAsync(
          P.py,
          [
            "-c",
            "import sqlite3,json,sys;c=sqlite3.connect(sys.argv[1]);print(json.dumps({" +
              "'meta':dict(c.execute(\"SELECT key,value FROM meta\").fetchall())," +
              "'groups':[{'code':r[0],'name':r[1],'n':r[4]} for r in c.execute('SELECT group_code,group_name,first_ts,last_ts,n FROM groups ORDER BY n DESC')]}))",
            P.index,
          ],
          { timeout: 20000, windowsHide: true }
        );
        index = JSON.parse(String(stdout));
      } catch (e) {
        index = { error: String(e?.message ?? e).slice(0, 200) };
      }

      return sendJson(res, 200, {
        ok: true, root: P.root,
        authorization: auth,
        focus: focus
          ? {
              updated_at: focus.updated_at,
              watch_groups: focus["监工群"] ?? [],
              keywords: Object.keys(focus["关注词"] ?? {}).filter((k) => !k.startsWith("_")),
              demote: Object.keys(focus["降权词"] ?? {}).filter((k) => !k.startsWith("_")),
            }
          : null,
        index,
      });
    },
  });

  /* ② 授权开关 */
  reg({
    kind: "exact",
    path: "/api/qqreminder/auth",
    handler: async (req, res) => {
      if (req.method !== "POST") return sendJson(res, 405, { error: "method not allowed" });
      if (!trusted(req)) return sendJson(res, 403, { error: "forbidden" });
      let body;
      try {
        body = await readBody(req);
      } catch (e) {
        return sendJson(res, 400, { error: String(e.message) });
      }
      if (typeof body.qq_read !== "boolean") {
        return sendJson(res, 400, { error: "qq_read must be boolean" });
      }
      const prev = (await readJsonFile(P.auth)) || {};
      const next = {
        ...prev,
        qq_read: body.qq_read,
        updated_at: new Date().toISOString(),
        updated_by: "你在 DSH 面板上操作",
      };
      try {
        await writeFile(P.auth, JSON.stringify(next, null, 2), "utf8");
      } catch (e) {
        return sendJson(res, 500, { error: String(e?.message ?? e) });
      }
      return sendJson(res, 200, { ok: true, root: P.root, authorization: next });
    },
  });

  /* ③ 关注画像：改关键词 / 降权词 / 监工群权重 */
  reg({
    kind: "exact",
    path: "/api/qqreminder/focus",
    handler: async (req, res) => {
      if (req.method !== "POST") return sendJson(res, 405, { error: "method not allowed" });
      if (!trusted(req)) return sendJson(res, 403, { error: "forbidden" });
      let body;
      try {
        body = await readBody(req);
      } catch (e) {
        return sendJson(res, 400, { error: String(e.message) });
      }
      const cur = (await readJsonFile(P.focus)) || {};
      if (body.keywords && typeof body.keywords === "object") {
        cur["关注词"] = { ...(cur["关注词"] ?? {}), ...body.keywords };
      }
      if (body.demote && typeof body.demote === "object") {
        cur["降权词"] = { ...(cur["降权词"] ?? {}), ...body.demote };
      }
      if (Array.isArray(body.watch_groups)) {
        cur["监工群"] = body.watch_groups;
      }
      cur.updated_at = new Date().toISOString().slice(0, 10);
      try {
        await writeFile(P.focus, JSON.stringify(cur, null, 2), "utf8");
      } catch (e) {
        return sendJson(res, 500, { error: String(e?.message ?? e) });
      }
      return sendJson(res, 200, { ok: true });
    },
  });

  /* ④ 刷新索引（不解密、不读正文，只更新滚动索引） */
  reg({
    kind: "exact",
    path: "/api/qqreminder/refresh",
    handler: async (req, res) => {
      if (req.method !== "POST") return sendJson(res, 405, { error: "method not allowed" });
      if (!trusted(req)) return sendJson(res, 403, { error: "forbidden" });
      // 数据目录里没有建索引脚本时，给出人话解释（不是服务器错误）
      if (!existsSync(P.build)) {
        const isDemo = /demo-root|[\\/]examples[\\/]/i.test(P.root);
        return sendJson(res, 200, {
          ok: false, code: -3,
          hint: isDemo
            ? "当前用的是【示例数据】，不需要刷新索引 —— 示例数据是虚构的，不从 QQ 读取。想刷新真实数据，请把数据目录切回你自己的数据根目录。"
            : "这个数据目录里没有建索引脚本（qgd/app/scripts/ntqq_build.py）。如果在用示例数据，这是正常的；想从 QQ 建立索引需要自行准备脚本（本插件不含解密功能，见 README）。",
          lines: [],
        });
      }
      const r = await runPy(P.py, P.build, [], { timeout: 180000 });
      const lines = (r.stdout + "\n" + r.stderr)
        .split(/\r?\n/)
        .filter((l) => /^\[(key|index|groups|names|schema|decrypt)\]/.test(l.trim()))
        .map((l) => l.trim());
      return sendJson(res, r.ok ? 200 : 500, { ok: r.ok, code: r.code, lines });
    },
  });

  /* ⑤ 生成摘要：跑打分器，回 JSON + Markdown */
  reg({
    kind: "exact",
    path: "/api/qqreminder/run",
    handler: async (req, res) => {
      if (req.method !== "POST") return sendJson(res, 405, { error: "method not allowed" });
      if (!trusted(req)) return sendJson(res, 403, { error: "forbidden" });

      const auth = (await readJsonFile(P.auth)) || { qq_read: false };
      if (auth.qq_read !== true) {
        return sendJson(res, 403, {
          error: "authorization off",
          hint: "你尚未打开「QQ 内容读取」开关；关闭状态下只允许输出统计与元数据。",
        });
      }

      let body;
      try {
        body = await readBody(req);
      } catch (e) {
        return sendJson(res, 400, { error: String(e.message) });
      }
      const days = Number.isFinite(+body.days) ? +body.days : 1;
      const top = Number.isFinite(+body.top) ? +body.top : 12;

      const jsonOut = join(P.out, "摘要-api.json");
      const mdOut = join(P.out, "摘要-api.md");
      const r = await runPy(
        P.py,
        P.score,
        ["--days", String(days), "--top", String(top), "--out", mdOut, "--json", jsonOut],
        { timeout: 180000 }
      );
      if (!r.ok) {
        return sendJson(res, 500, { ok: false, code: r.code, stderr: r.stderr.slice(-2000) });
      }
      const data = (await readJsonFile(jsonOut)) || null;
      let markdown = "";
      try {
        markdown = await readFile(mdOut, "utf8");
      } catch {
        /* 尽力而为 */
      }
      return sendJson(res, 200, { ok: true, root: P.root, window: data?.window ?? null, cutoff: data?.cutoff ?? null, data, markdown });
    },
  });

  /* ⑥ 一键初始化分析：扫所有群/成员/消息，给出推荐（不写任何配置） */
  reg({
    kind: "exact",
    path: "/api/qqreminder/autoconfig",
    handler: async (req, res) => {
      if (req.method !== "GET") return sendJson(res, 405, { error: "method not allowed" });
      if (!isLocal(req)) return sendJson(res, 403, { error: "local only" });
      const jsonOut = join(P.out, "初始化建议.json");
      const r = await runPy(P.py, P.autoconfig, ["--days", "7", "--json", jsonOut], { timeout: 240000 });
      if (!r.ok) {
        return sendJson(res, 500, { ok: false, code: r.code, stderr: String(r.stderr || "").slice(-2000) });
      }
      const data = await readJsonFile(jsonOut);
      if (!data) return sendJson(res, 500, { ok: false, error: "分析结果读取失败" });
      return sendJson(res, 200, Object.assign({ ok: true }, data));
    },
  });

  /* ⑦ 应用推荐：把你勾选的群/词写进 focus.json */
  reg({
    kind: "exact",
    path: "/api/qqreminder/apply",
    handler: async (req, res) => {
      if (req.method !== "POST") return sendJson(res, 405, { error: "method not allowed" });
      if (!trusted(req)) return sendJson(res, 403, { error: "forbidden" });
      let body;
      try {
        body = await readBody(req);
      } catch (e) {
        return sendJson(res, 400, { error: String(e.message) });
      }
      const cur = (await readJsonFile(P.focus)) || {};

      if (Array.isArray(body.groups) && body.groups.length) {
        const existing = new Map((cur["监工群"] || []).map((g) => [String(g["群号"]), g]));
        for (const g of body.groups) {
          const code = String(g["群号"] != null ? g["群号"] : g.code != null ? g.code : "");
          if (!code) continue;
          const prev = existing.get(code);
          existing.set(code, {
            "群号": code,
            "群名": g["群名"] != null ? g["群名"] : g.name != null ? g.name : (prev && prev["群名"]) || code,
            "权重": Number(g["建议权重"] != null ? g["建议权重"] : g.weight != null ? g.weight : (prev && prev["权重"]) || 1.0),
            "备注": (prev && prev["备注"]) || "一键初始化推荐",
          });
        }
        cur["监工群"] = [...existing.values()];
      }

      if (body.keywords && typeof body.keywords === "object") {
        const kw = Object.assign({}, cur["关注词"] || {});
        for (const k of Object.keys(body.keywords)) {
          if (k && k.charAt(0) !== "_") kw[k] = Number(body.keywords[k]) || 4;
        }
        cur["关注词"] = kw;
      }

      if (Array.isArray(body.people) && body.people.length) {
        cur["重要人物"] = body.people.slice(0, 30);
      }

      cur.updated_at = new Date().toISOString().slice(0, 10);
      try {
        await writeFile(P.focus, JSON.stringify(cur, null, 2), "utf8");
      } catch (e) {
        return sendJson(res, 500, { error: String(e && e.message ? e.message : e) });
      }
      return sendJson(res, 200, {
        ok: true, root: P.root,
        groups: (cur["监工群"] || []).length,
        keywords: Object.keys(cur["关注词"] || {}).length,
      });
    },
  });

  /* ⑧ 备料：打包给"云端 AI"看的材料（本地只做客观统计+抽样，不做语义判断） */
  reg({
    kind: "exact",
    path: "/api/qqreminder/prepare",
    handler: async (req, res) => {
      if (req.method !== "POST") return sendJson(res, 405, { error: "method not allowed" });
      if (!trusted(req)) return sendJson(res, 403, { error: "forbidden" });
      let body = {};
      try {
        body = await readBody(req);
      } catch (e) {
        return sendJson(res, 400, { error: String(e.message) });
      }
      const days = Number.isFinite(+body.days) ? +body.days : 7;
      const maxGroups = Number.isFinite(+body.maxGroups) ? +body.maxGroups : 30;
      const perGroup = Number.isFinite(+body.perGroup) ? +body.perGroup : 8;

      const r = await runPy(
        P.py,
        P.pack,
        ["--days", String(days), "--max-groups", String(maxGroups), "--per-group", String(perGroup)],
        { timeout: 180000 }
      );
      if (!r.ok) {
        return sendJson(res, 500, { ok: false, code: r.code, stderr: String(r.stderr || "").slice(-2000) });
      }
      const data = await readJsonFile(join(P.out, "待分析材料.json"));
      const totals = (data && data.totals) || {};
      return sendJson(res, 200, {
        ok: true, root: P.root,
        totals,
        cutoff: data ? data.cutoff_ts : null,
        ready: true,
        materialMd: join(P.out, "待分析材料.md").replace(/\\/g, "/"),
        materialJson: join(P.out, "待分析材料.json").replace(/\\/g, "/"),
        // 交给 AI 的指令（面板会展示，你一键复制或直接照说）
        instruction:
          "分析一下 QQ 群初始化材料（out/待分析材料.md）：" +
          "① 哪些群值得监工、建议权重与理由；" +
          "② 该关注哪些关键词、为什么；" +
          "③ 哪些群可以不管。",
      });
    },
  });

  /* ⑨ 生成 HTML 报告（起始时间可自定义） */
  reg({
    kind: "exact",
    path: "/api/qqreminder/report",
    handler: async (req, res) => {
      if (req.method !== "POST") return sendJson(res, 405, { error: "method not allowed" });
      if (!trusted(req)) return sendJson(res, 403, { error: "forbidden" });
      let body = {};
      try {
        body = await readBody(req);
      } catch (e) {
        return sendJson(res, 400, { error: String(e.message) });
      }
      const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 13);
      const name = "报告-" + stamp + ".html";
      const outPath = join(P.out, name);
      const args = ["--out", outPath, "--top", String(+body.top || 12)];
      if (body.since) args.push("--since", String(body.since));
      if (body.until) args.push("--until", String(body.until));
      if (!body.since) args.push("--days", String(+body.days || 7));

      const r = await runPy(P.py, P.report, args, { timeout: 180000 });
      if (!r.ok) {
        return sendJson(res, 500, { ok: false, code: r.code, stderr: String(r.stderr || "").slice(-1500) });
      }
      let size = 0;
      try {
        size = (await stat(outPath)).size;
      } catch {
        /* ignore */
      }
      return sendJson(res, 200, {
        ok: true, root: P.root,
        name,
        size,
        path: outPath.replace(/\\/g, "/"),
        url: "/api/qqreminder/report-file?name=" + encodeURIComponent(name),
      });
    },
  });

  /* ⑩ 取报告文件（只允许 out 目录下的 .html，禁止路径穿越） */
  reg({
    kind: "exact",
    path: "/api/qqreminder/report-file",
    handler: async (req, res) => {
      if (req.method !== "GET") return sendJson(res, 405, { error: "method not allowed" });
      if (!isLocal(req)) return sendJson(res, 403, { error: "local only" });
      let name = "";
      try {
        name = new URL(req.url, "http://x").searchParams.get("name") || "";
      } catch {
        name = "";
      }
      if (!/^[\w\u4e00-\u9fff.\-]+\.html$/.test(name)) {
        return sendJson(res, 400, { error: "bad name" });
      }
      const full = join(P.out, name);
      const outDir = P.out.replace(/\\/g, "/").toLowerCase();
      if (!full.replace(/\\/g, "/").toLowerCase().startsWith(outDir + "/")) {
        return sendJson(res, 400, { error: "outside out dir" });
      }
      let content;
      try {
        content = await readFile(full, "utf8");
      } catch {
        return sendJson(res, 404, { error: "not found" });
      }
      res.writeHead(200, { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" });
      res.end(content);
    },
  });

  /* ⑪ 用系统默认浏览器打开报告（文件本来就在本地，不需要"下载"） */
  reg({
    kind: "exact",
    path: "/api/qqreminder/open-report",
    handler: async (req, res) => {
      if (req.method !== "POST") return sendJson(res, 405, { error: "method not allowed" });
      if (!trusted(req)) return sendJson(res, 403, { error: "forbidden" });
      let body = {};
      try {
        body = await readBody(req);
      } catch (e) {
        return sendJson(res, 400, { error: String(e.message) });
      }
      const name = String(body.name || "");
      if (!/^[\w\u4e00-\u9fff.\-]+\.html$/.test(name)) {
        return sendJson(res, 400, { error: "bad name" });
      }
      const full = join(P.out, name);
      const outDir = P.out.replace(/\\/g, "/").toLowerCase();
      if (!full.replace(/\\/g, "/").toLowerCase().startsWith(outDir + "/")) {
        return sendJson(res, 400, { error: "outside out dir" });
      }
      if (!existsSync(full)) return sendJson(res, 404, { error: "not found" });
      try {
        // Windows：cmd /c start "" "<file>" 会用系统默认程序（浏览器）打开
        await new Promise((resolve, reject) => {
          execFile("cmd.exe", ["/c", "start", "", full], { windowsHide: true }, (err) =>
            err ? reject(err) : resolve()
          );
        });
      } catch (e) {
        return sendJson(res, 500, { error: String((e && e.message) || e) });
      }
      return sendJson(res, 200, { ok: true, root: P.root, opened: full.replace(/\\/g, "/") });
    },
  });

  /* ⑫ ★ 首次使用引导：一键加载示例数据（把引擎自带的虚构示例灌进数据目录） */
  reg({
    kind: "exact",
    path: "/api/qqreminder/init-demo",
    handler: async (req, res) => {
      if (req.method !== "POST") return sendJson(res, 405, { error: "method not allowed" });
      if (!trusted(req)) return sendJson(res, 403, { error: "forbidden" });

      // 已有数据就不覆盖（除非显式 force）
      let body = {};
      try {
        body = await readBody(req);
      } catch {
        /* 允许空 body */
      }
      if (existsSync(P.index) && !body.force) {
        return sendJson(res, 200, {
          ok: false, already: true,
          message: "数据目录里已经有索引了。要覆盖成示例数据，请传 {\"force\": true}（会覆盖 index.db，请先备份）。",
        });
      }

      const r = await runPy(P.py, P.loader, ["--data", P.root], { timeout: 120000 });
      if (!r.ok) {
        return sendJson(res, 500, {
          ok: false, code: r.code,
          stderr: String(r.stderr || "").slice(-1500),
          hint: r.code === -2 ? "插件自带的 engine/ 缺失，请重新安装插件。" : undefined,
        });
      }
      const out = String(r.stdout || "").split(/\r?\n/).filter((l) => l.trim()).slice(-8);
      return sendJson(res, 200, { ok: true, root: P.root, lines: out });
    },
  });

  ctx.logger?.info?.("[qqreminder] 已注册 12 个 API 端点：/api/qqreminder/{status,auth,focus,refresh,run,autoconfig,apply,prepare,report,report-file,open-report,init-demo}");
}
