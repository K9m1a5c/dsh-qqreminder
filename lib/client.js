/**
 * dsh-qqreminder · 客户端面板
 *
 * 挂在 settings.section（设置页整页入口）。
 * 全部门面数据走 host 侧 HTTP API：/api/qqreminder/*
 *
 * ★ 必须导出 inject，否则 cordis fiber 不会等待服务就位，
 *   apply 里 slots 仍是 undefined → 【静默吞掉整个插件，且不报错】。
 */
window.__ModuleLoader__.load({
  id: "dsh-qqreminder",
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
    let react = require("react");
    const h = react.createElement;

    /* ── API ───────────────────────────────────────────── */
    const API = "/api/qqreminder/";
    async function api(path, body) {
      const opt = {
        method: body === undefined ? "GET" : "POST",
        headers: { "x-dsh-qqreminder": "1" },
      };
      if (body !== undefined) {
        opt.headers["content-type"] = "application/json";
        opt.body = JSON.stringify(body);
      }
      const r = await fetch(API + path, opt);
      let data = null;
      try {
        data = await r.json();
      } catch {
        data = { error: "响应不是 JSON" };
      }
      return { status: r.status, ok: r.ok, data };
    }

    /* ── 样式 ──────────────────────────────────────────── */
    const S = {
      wrap: { padding: "20px 24px", maxWidth: "880px", fontSize: "13px", lineHeight: 1.7 },
      h1: { fontSize: "17px", fontWeight: 600, margin: "0 0 4px" },
      sub: { color: "var(--dsw-alias-label-secondary, #888)", fontSize: "12px", marginBottom: "16px" },
      card: {
        border: "1px solid var(--dsw-alias-border-l2, #e3e3e3)",
        borderRadius: "10px",
        padding: "14px 16px",
        marginBottom: "14px",
      },
      row: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px" },
      label: { fontWeight: 600, marginBottom: "2px" },
      hint: { color: "var(--dsw-alias-label-secondary, #888)", fontSize: "12px" },
      sw: (on) => ({
        width: "46px", height: "26px", borderRadius: "13px", border: "none", cursor: "pointer",
        background: on ? "var(--dsw-alias-brand-primary, #2563eb)" : "#c9ccd1",
        position: "relative", transition: "background .18s", flex: "0 0 auto",
      }),
      knob: (on) => ({
        position: "absolute", top: "3px", left: on ? "23px" : "3px",
        width: "20px", height: "20px", borderRadius: "50%", background: "#fff",
        transition: "left .18s",
      }),
      btn: {
        padding: "7px 14px", borderRadius: "8px", cursor: "pointer", fontSize: "13px",
        border: "1px solid var(--dsw-alias-border-l2, #d5d5d5)",
        background: "var(--dsw-alias-bg-base, #fff)",
      },
      btnPrimary: {
        padding: "7px 14px", borderRadius: "8px", cursor: "pointer", fontSize: "13px",
        border: "none", color: "#fff", background: "var(--dsw-alias-brand-primary, #2563eb)",
      },
      tag: {
        display: "inline-block", padding: "1px 7px", margin: "2px 4px 2px 0",
        borderRadius: "5px", fontSize: "12px",
        background: "var(--dsw-alias-bg-l2, #f2f3f5)",
      },
      mono: { fontFamily: "ui-monospace, Consolas, monospace", fontSize: "12px" },
      pre: {
        whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: "420px", overflow: "auto",
        background: "var(--dsw-alias-bg-l2, #f7f8fa)", borderRadius: "8px", padding: "12px",
        fontSize: "12.5px", lineHeight: 1.65, margin: 0,
      },
    };

    /* ── 面板 ──────────────────────────────────────────── */
    function Panel() {
      const [st, setSt] = react.useState(null);
      const [busy, setBusy] = react.useState("");
      const [msg, setMsg] = react.useState("");
      const [prep, setPrep] = react.useState(null);   // AI 备料结果
      const [copied, setCopied] = react.useState(false);

      /* ★ 主入口：为「云端 AI 分析」备料（本地只做客观统计 + 抽样） */
      const doPrepare = async () => {
        setBusy("prepare");
        setMsg("正在打包材料（本地，约 1 秒）…");
        setCopied(false);
        const r = await api("prepare", { days: 7, maxGroups: 30, perGroup: 8 });
        setBusy("");
        if (!r.ok) {
          setMsg("✗ 备料失败：" + JSON.stringify(r.data).slice(0, 260));
          return;
        }
        setPrep(r.data);
        setMsg("✅ 材料已就绪 —— 交给 AI 做语义分析即可");
      };

      const copyInstruction = async () => {
        if (!prep) return;
        const text = prep.instruction || "";
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
          } else {
            const ta = document.createElement("textarea");
            ta.value = text;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand("copy");
            document.body.removeChild(ta);
          }
          setCopied(true);
        } catch (e) {
          setCopied(false);
          setMsg("复制失败，请手动选中下面那句话");
        }
      };

      /* ★ 生成 HTML 报告：日期【区间】可自定义，快捷按钮会同步两端 */
      const [rep, setRep] = react.useState(null);
      const [showRep, setShowRep] = react.useState(false);
      const ymd = (d) => {
        const p = (n) => String(n).padStart(2, "0");
        return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
      };
      const [since, setSince] = react.useState(() => {
        const d = new Date();
        d.setDate(d.getDate() - 7);
        return ymd(d);
      });
      const [until, setUntil] = react.useState(() => ymd(new Date()));

      /* 📝 生成摘要：时间窗 + 一句会自动复制到剪贴板的指令 */
      const [sumDays, setSumDays] = react.useState(1);
      const [sumCopied, setSumCopied] = react.useState(false);
      const sumPrompt =
        "翻一下群，把最近 " + sumDays + " 天的重要信息整理成摘要：" +
        "① 最重要的几条，并说明为什么重要；② 我可能漏掉的事，标注时效。";
      const copySummary = async () => {
        try {
          await navigator.clipboard.writeText(sumPrompt);
          setSumCopied(true);
          setMsg("✅ 指令已复制到剪贴板，去粘贴给 AI 就行了");
        } catch (e) {
          setSumCopied(false);
          setMsg("复制失败，请手动选中上面那句话");
        }
      };

      /* ★ 首次使用：一键加载示例数据（虚构，零隐私风险） */
      const doInitDemo = async () => {
        setBusy("demo");
        setMsg("正在生成示例数据…");
        const r = await api("init-demo", {});
        setBusy("");
        if (r.ok && r.data && r.data.ok) {
          setMsg("✅ 示例数据已就绪 —— 现在可以试【生成报告】【用 AI 分析】了");
          load();
        } else {
          setMsg("✗ " + String((r.data && (r.data.message || r.data.stderr)) || JSON.stringify(r.data)).slice(0, 240));
        }
      };

      /* 快捷天数：★ 起始与结束【一起同步】 */
      const pickDays = (days) => {
        const u = new Date();
        const s = new Date(u.getTime() - days * 86400000);
        setSince(ymd(s));
        setUntil(ymd(u));
      };

      const doReport = async () => {
        if (!since || !until) {
          setMsg("请先选好「从」和「到」两个日期");
          return;
        }
        if (since > until) {
          setMsg("起始日期不能晚于结束日期");
          return;
        }
        setBusy("report");
        setMsg("正在生成报告…");
        setRep(null);
        setShowRep(false);
        const r = await api("report", { since: since, until: until });
        setBusy("");
        if (!r.ok) {
          setMsg("✗ 生成失败：" + JSON.stringify(r.data).slice(0, 260));
          return;
        }
        setRep(r.data);
        setMsg("✅ 报告已生成：" + (r.data.name || ""));
      };

      const load = react.useCallback(async () => {
        const r = await api("status");
        setSt(r.data);
      }, []);

      react.useEffect(() => {
        load();
      }, [load]);

      const toggleAuth = async () => {
        if (!st) return;
        setBusy("auth");
        setMsg("");
        const next = !(st.authorization && st.authorization.qq_read);
        const r = await api("auth", { qq_read: next });
        setBusy("");
        if (r.ok) {
          setMsg(next ? "✅ 已开启：AI 可以读取群消息正文了" : "🔒 已关闭：只能输出统计与元数据");
          load();
        } else {
          setMsg("✗ 操作失败：" + (r.data && r.data.error));
        }
      };

      const doRefresh = async () => {
        setBusy("refresh");
        setMsg("正在刷新索引…");
        const r = await api("refresh", {});
        setBusy("");
        if (r.ok) {
          setMsg("✅ 索引已刷新：" + (r.data.lines || []).slice(-2).join(" · "));
          load();
        } else {
          setMsg("✗ 刷新失败：" + JSON.stringify(r.data).slice(0, 200));
        }
      };



      if (!st) {
        return h("div", { style: S.wrap }, h("div", { style: S.hint }, "正在加载 QQreminder…"));
      }

      const auth = st.authorization || {};
      const idx = st.index || {};
      const meta = idx.meta || {};
      const groups = idx.groups || [];
      const watch = (st.focus && st.focus.watch_groups) || [];
      const kws = (st.focus && st.focus.keywords) || [];
      const on = auth.qq_read === true;

      const ts = (v) => {
        const n = Number(v);
        if (!n) return "—";
        const d = new Date(n * 1000);
        const p = (x) => String(x).padStart(2, "0");
        return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
      };

      return h(
        "div",
        { style: S.wrap },
        h("div", { style: S.h1 }, "QQreminder"),

        /* ★ 首次使用引导：只在【还没有数据】时出现 */
        (!st.index || st.index.error) &&
          h(
            "div",
            { style: Object.assign({}, S.card, { borderLeft: "3px solid #2563eb" }) },
            h("div", { style: S.label }, "👋 第一次用？还没有数据"),
            h(
              "div",
              { style: S.hint },
              "可以先用【示例数据】看看它能做什么 —— 一份虚构的大学群聊（人物、学校、对话全是编的），随时能换成你自己的数据。"
            ),
            st.index && st.index.error
              ? h("div", { style: Object.assign({}, S.hint, S.mono, { marginTop: "6px" }) }, String(st.index.error).slice(0, 160))
              : null,
            h(
              "div",
              { style: { marginTop: "10px" } },
              h(
                "button",
                { style: S.btnPrimary, onClick: doInitDemo, disabled: !!busy },
                busy === "demo" ? "生成中…" : "📦 加载示例数据"
              )
            )
          ),
        h(
          "div",
          { style: S.sub },
          "数据截止 " + ts(meta.cutoff_ts) + " · 索引构建于 " + ts(meta.built_at) +
            " · 窗口 " + (meta.window_days || "?") + " 天 · 扫描 " + (meta.scan_rows || 0) +
            " 行 / 读取错误 " + (meta.read_errors ?? "?") + " 次 · schema " + (meta.schema_version || "?")
        ),

        /* 授权开关 */
        h(
          "div",
          { style: S.card },
          h(
            "div",
            { style: S.row },
            h(
              "div",
              null,
              h("div", { style: S.label }, "QQ 内容读取"),
              h(
                "div",
                { style: S.hint },
                on
                  ? "已开启 —— AI 可以读取群消息正文，并据此生成摘要与遗漏提醒"
                  : "已关闭 —— AI 只能输出统计与元数据，不会读到任何群消息正文"
              )
            ),
            h(
              "button",
              { style: S.sw(on), onClick: toggleAuth, disabled: busy === "auth", title: on ? "点击关闭" : "点击开启" },
              h("span", { style: S.knob(on) })
            )
          )
        ),

        /* 📄 一键生成 HTML 报告 */
        h(
          "div",
          { style: S.card },
          h("div", { style: S.label }, "📄 生成报告"),
          h(
            "div",
            { style: S.hint },
            "把这段时间的群消息整理成一份 HTML 报告（可点进每个群、可离线打开或分享）。日期区间可自定义。"
          ),
          h(
            "div",
            { style: Object.assign({}, S.row, { justifyContent: "flex-start", gap: "8px", marginTop: "10px", flexWrap: "wrap" }) },
            h("span", { style: S.hint }, "从"),
            h("input", {
              type: "date",
              value: since,
              onChange: (e) => setSince(e.target.value),
              style: {
                padding: "6px 8px", borderRadius: "8px", fontSize: "13px",
                border: "1px solid var(--dsw-alias-border-l2, #d5d5d5)",
                background: "var(--dsw-alias-bg-base, #fff)", color: "inherit",
              },
            }),
            h("span", { style: S.hint }, "到"),
            h("input", {
              type: "date",
              value: until,
              onChange: (e) => setUntil(e.target.value),
              style: {
                padding: "6px 8px", borderRadius: "8px", fontSize: "13px",
                border: "1px solid var(--dsw-alias-border-l2, #d5d5d5)",
                background: "var(--dsw-alias-bg-base, #fff)", color: "inherit",
              },
            }),
            h(
              "button",
              { style: S.btnPrimary, onClick: doReport, disabled: !!busy },
              busy === "report" ? "生成中…" : "生成报告"
            )
          ),
          h(
            "div",
            { style: Object.assign({}, S.row, { justifyContent: "flex-start", gap: "8px", marginTop: "8px", flexWrap: "wrap" }) },
            h("span", { style: S.hint }, "快捷（会同时改上面两个日期）："),
            [1, 3, 7, 30].map((d) =>
              h("button", { key: d, style: S.btn, onClick: () => pickDays(d), disabled: !!busy }, "近 " + d + " 天")
            )
          ),
          rep &&
            h(
              "div",
              { style: { marginTop: "12px", padding: "12px", background: "var(--dsw-alias-bg-l2, #f7f8fa)", borderRadius: "8px" } },
              h("div", { style: S.label }, "✅ 报告已生成"),
              h("div", { style: S.hint }, (rep.name || "") + "　" + Math.round(((rep.size || 0) / 1024) * 10) / 10 + " KB"),
              h(
                "div",
                { style: { marginTop: "8px", display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" } },
                h(
                  "button",
                  { style: S.btnPrimary, onClick: () => setShowRep(!showRep) },
                  showRep ? "收起报告" : "在面板里查看"
                ),
                h(
                  "button",
                  {
                    style: S.btn,
                    onClick: async () => {
                      const r2 = await api("open-report", { name: rep.name });
                      setMsg(r2.ok ? "✅ 已用系统浏览器打开" : "✗ 打开失败：" + JSON.stringify(r2.data).slice(0, 160));
                    },
                  },
                  "用系统浏览器打开"
                ),
                h(
                  "button",
                  { style: S.btn, onClick: () => { try { navigator.clipboard.writeText(rep.path || ""); setMsg("已复制报告路径"); } catch (e) { setMsg(rep.path || ""); } } },
                  "复制路径"
                )
              ),
              showRep &&
                h("iframe", {
                  src: rep.url,
                  title: "报告预览",
                  style: {
                    width: "100%", height: "640px", marginTop: "10px",
                    border: "1px solid var(--dsw-alias-border-l2, #e3e3e3)",
                    borderRadius: "10px", background: "#fff",
                  },
                }),
              h(
                "div",
                { style: Object.assign({}, S.hint, S.mono, { marginTop: "6px", wordBreak: "break-all" }) },
                rep.path || ""
              )
            )
        ),

        /* 初始化：AI 分析为主入口 */
        h(
          "div",
          { style: S.card },
          h("div", { style: S.label }, "初始化（第一次用请点这里）"),
          h(
            "div",
            { style: S.hint },
            "本地只负责「备料」（客观统计 + 抽样原文，不做判断）；真正的判断交给云端大模型（AI）。"
          ),

          /* ★ 必须讲清楚「哪个按钮会改配置」—— 否则用户一脸懵 */
          h(
            "div",
            {
              style: {
                marginTop: "10px", padding: "10px 12px",
                background: "var(--dsw-alias-bg-l2, #f7f8fa)",
                borderLeft: "3px solid #2563eb", borderRadius: "6px",
                fontSize: "12.5px", lineHeight: 1.7,
              },
            },
            h("div", { style: { fontWeight: 600, marginBottom: "4px" } }, "⚠️ 先看清楚：哪些按钮会改你的配置"),
            h("div", null, "· 【✨ 用 AI 分析】只准备材料，", h("b", null, "不改任何配置"), "；备好后把一句话发给 AI，AI 给建议，你确认了才写。"),
            h("div", null, "· 想改监工群或关注词，", h("b", null, "直接跟 AI 说"), "就行，或改 focus.json。")
          ),

          h(
            "div",
            { style: Object.assign({}, S.row, { justifyContent: "flex-start", gap: "10px", marginTop: "10px" }) },
            h(
              "button",
              { style: S.btnPrimary, onClick: doPrepare, disabled: !!busy, title: "只备料，不改配置" },
              busy === "prepare" ? "打包中…" : "✨ 用 AI 分析"
            )
          ),

          prep &&
            h(
              "div",
              {
                style: {
                  marginTop: "12px", padding: "12px",
                  background: "var(--dsw-alias-bg-l2, #f7f8fa)", borderRadius: "8px",
                },
              },
              h("div", { style: S.label }, "✅ 材料已就绪"),
              h(
                "div",
                { style: S.hint },
                ((prep.totals && prep.totals["进材料的群"]) || 0) + " 个群 · 抽样 " +
                  ((prep.totals && prep.totals["抽样条数"]) || 0) + " 条 · 约 " +
                  Math.round((((prep.totals && prep.totals["抽样字符数"]) || 0) / 1.5 / 1000) * 10) / 10 + "k token"
              ),
              h("div", { style: S.hint }, prep.materialMd || ""),
              h(
                "div",
                { style: Object.assign({}, S.hint, { marginTop: "10px" }) },
                "下一步：复制下面这句话 → 粘到对话框发给 AI"
              ),
              h(
                "div",
                {
                  style: Object.assign({}, S.mono, {
                    marginTop: "6px", padding: "8px 10px", background: "#fff",
                    border: "1px solid var(--dsw-alias-border-l2, #e3e3e3)",
                    borderRadius: "6px", userSelect: "all",
                  }),
                },
                prep.instruction || ""
              ),
              h(
                "div",
                { style: { marginTop: "8px" } },
                h("button", { style: S.btn, onClick: copyInstruction }, copied ? "✅ 已复制" : "复制这句话")
              )
            )
        ),

        /* 监工群 */
        h(
          "div",
          { style: S.card },
          h("div", { style: S.label }, "监工群（" + watch.length + "）"),
          h(
            "div",
            { style: S.hint },
            "只统计这些群；改名单请编辑 focus.json 的「监工群」"
          ),
          h(
            "div",
            { style: { marginTop: "8px" } },
            watch.map((g, i) => {
              const hit = groups.find((x) => String(x.code) === String(g["群号"]));
              return h(
                "div",
                { key: i, style: { display: "flex", justifyContent: "space-between", padding: "3px 0" } },
                h("span", null, g["群名"] || g["群号"]),
                h(
                  "span",
                  { style: { ...S.hint, ...S.mono } },
                  "×" + (g["权重"] ?? 1) + (hit ? "　" + hit.n + " 条" : "　（窗口内无消息）")
                )
              );
            })
          )
        ),

        /* 关注词 */
        h(
          "div",
          { style: S.card },
          h("div", { style: S.label }, "我的关注词（" + kws.length + "）"),
          h("div", { style: S.hint }, "命中任意一个都会加权；按你自己的关注点增删"),
          h(
            "div",
            { style: { marginTop: "8px" } },
            kws.map((k, i) => h("span", { key: i, style: S.tag }, k))
          )
        ),

        /* 操作：只留刷新索引 */
        h(
          "div",
          { style: { ...S.row, justifyContent: "flex-start", gap: "10px", marginBottom: "10px" } },
          h("button", { style: S.btn, onClick: doRefresh, disabled: !!busy },
            busy === "refresh" ? "刷新中…" : "刷新索引")
        ),

        /* 📝 生成摘要：自动复制一句指令，粘贴给 AI */
        h(
          "div",
          { style: S.card },
          h("div", { style: S.label }, "📝 生成摘要"),
          h(
            "div",
            { style: S.hint },
            "点下面的按钮会把一句指令【自动复制到剪贴板】；粘贴给 AI，AI 就会帮你把群消息整理成摘要与「你可能漏了」。"
          ),
          h(
            "div",
            { style: Object.assign({}, S.row, { justifyContent: "flex-start", gap: "8px", marginTop: "10px", flexWrap: "wrap" }) },
            h("span", { style: S.hint }, "时间窗："),
            [1, 3, 7, 30].map((d) =>
              h(
                "button",
                { key: d, style: d === sumDays ? S.btnPrimary : S.btn, onClick: () => { setSumDays(d); setSumCopied(false); } },
                "近 " + d + " 天"
              )
            )
          ),
          h(
            "div",
            {
              style: Object.assign({}, S.mono, {
                marginTop: "10px", padding: "9px 11px", background: "var(--dsw-alias-bg-base, #fff)",
                border: "1px solid var(--dsw-alias-border-l2, #e3e3e3)",
                borderRadius: "6px", userSelect: "all",
              }),
            },
            sumPrompt
          ),
          h(
            "div",
            { style: { marginTop: "8px", display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" } },
            h(
              "button",
              { style: S.btnPrimary, onClick: copySummary, disabled: !on },
              sumCopied ? "✅ 已复制，去粘贴给 AI" : "📋 自动复制指令"
            ),
            !on && h("span", { style: S.hint }, "（需先打开上面的「QQ 内容读取」开关）")
          )
        ),

        msg && h("div", { style: Object.assign({}, S.card, { padding: "10px 14px" }) }, msg),

        h(
          "div",
          { style: Object.assign({}, S.hint, { marginTop: "16px" }) },
          "数据根目录：" + (st.index && st.index.error ? "索引读取失败：" + st.index.error : (st.root || "（由插件配置 root 决定）")) +
            "　·　本插件只读，不修改 QQ 任何数据"
        )
      );
    }

    /* ── 注册 ──────────────────────────────────────────── */
    const inject = ["slots", "locale"];

    function apply(ctx) {
      const slots = ctx.get("slots");
      if (slots === undefined) return; // 服务未就位就静默退出（inject 已保证不会发生）

      slots.inject("settings.section", () =>
        slots.register(
          { name: "settings.section", id: "qqreminder", order: 6, label: () => "QQreminder" },
          () => h(Panel)
        )
      );
    }

    exports.apply = apply;
    exports.inject = inject;
    return module.exports;
  },
});
