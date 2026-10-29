import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";
import DocumentApp from "./documents";

type Goal = {
  id: string;
  intent: string;
  status: string;
  proposal_id?: string;
  decline_reason?: string;
};
type Evidence = { id: string; title?: string; quote: string; version: string };
type Run = {
  id: string;
  conversation_id: string;
  status: string;
  outcome: string | null;
  version: number;
  resume_generation: number;
  question?: string;
  answer?: string;
  goals: Goal[];
  evidence?: Evidence[];
  model_calls: number;
  stop_reason?: string;
  provider?: string;
};
type Proposal = {
  id: string;
  status: string;
  version: number;
  action: string;
  order_id: string;
  item_id?: string;
  quantity?: number;
  amount_cents: number;
  currency: string;
  expires_at: number;
};
type Event = { seq: number; kind: string; payload: unknown };
type Order = {
  id: string;
  status: string;
  paid_cents: number;
  currency: string;
};
const key = () => crypto.randomUUID();
const money = (cents: number, currency: string) =>
  new Intl.NumberFormat("zh-CN", { style: "currency", currency }).format(
    cents / 100,
  );

function App() {
  const [token, setToken] = useState(
    sessionStorage.getItem("shopresolve-token") || "",
  );
  const [account, setAccount] = useState("alice");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [runs, setRuns] = useState<Run[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [selected, setSelected] = useState(
    sessionStorage.getItem("shopresolve-selected-run") || "",
  );
  const [run, setRun] = useState<Run | null>(null);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const cursor = useRef(0);

  useEffect(() => {
    if (selected) sessionStorage.setItem("shopresolve-selected-run", selected);
    else sessionStorage.removeItem("shopresolve-selected-run");
  }, [selected]);

  function clearSession() {
    sessionStorage.removeItem("shopresolve-token");
    sessionStorage.removeItem("shopresolve-selected-run");
    setToken("");
    setSelected("");
    setRun(null);
    setRuns([]);
    setOrders([]);
    setProposals([]);
    setEvents([]);
  }

  async function api(path: string, body?: unknown, idempotency?: string) {
    const response = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(idempotency ? { "Idempotency-Key": idempotency } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 401 && token) {
        clearSession();
      }
      throw new Error(data.message || JSON.stringify(data.detail || data));
    }
    return data;
  }

  async function refresh() {
    if (!token) return;
    const [all, owned] = await Promise.all([
      api("/v1/runs"),
      api("/v1/orders"),
    ]);
    setRuns(all);
    setOrders(owned);
    if (selected) {
      const current: Run = await api(`/v1/runs/${selected}`);
      setRun(current);
      const cards = await Promise.all(
        current.goals
          .filter((g) => g.proposal_id)
          .map((g) => api(`/v1/proposals/${g.proposal_id}`)),
      );
      setProposals(cards);
    }
  }

  useEffect(() => {
    if (!token) return;
    let alive = true;
    const update = () =>
      refresh().catch((e) => {
        if (alive) setError(e.message);
      });
    update();
    const timer = setInterval(update, 1500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [token, selected]);

  useEffect(() => {
    setEvents([]);
    cursor.current = 0;
    setRun(null);
    setProposals([]);
    if (!token || !selected) return;
    const controller = new AbortController();
    async function stream() {
      while (!controller.signal.aborted) {
        try {
          const response = await fetch(`/v1/runs/${selected}/events`, {
            headers: {
              Authorization: `Bearer ${token}`,
              "Last-Event-ID": String(cursor.current),
            },
            signal: controller.signal,
          });
          if (!response.ok || !response.body)
            throw new Error("事件连接暂不可用");
          const reader = response.body.getReader(),
            decoder = new TextDecoder();
          let buffer = "";
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const blocks = buffer.split("\n\n");
            buffer = blocks.pop() || "";
            for (const block of blocks) {
              const line = block
                .split("\n")
                .find((l) => l.startsWith("data: "));
              if (!line) continue;
              const event: Event = JSON.parse(line.slice(6));
              if (event.seq > cursor.current) {
                cursor.current = event.seq;
                setEvents((old) => [...old, event].slice(-200));
              }
            }
          }
        } catch (e) {
          if (controller.signal.aborted) return;
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    }
    stream();
    return () => controller.abort();
  }, [selected, token]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setBusy(true);
    setError("");
    try {
      const waiting =
        run && ["WAITING_USER", "WAITING_APPROVAL"].includes(run.status);
      if (waiting) {
        await api(
          `/v1/runs/${run.id}/revisions`,
          {
            text,
            client_message_id: key(),
            expected_version: run.version,
            wait_generation: run.resume_generation,
          },
          key(),
        );
      } else {
        const conversation = await api("/v1/conversations", {}, key());
        const created = await api(
          `/v1/conversations/${conversation.id}/messages`,
          { text, client_message_id: key() },
          key(),
        );
        setSelected(created.run_id);
      }
      setText("");
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function decision(p: Proposal, value: "approve" | "reject") {
    setBusy(true);
    setError("");
    try {
      await api(
        `/v1/proposals/${p.id}/decision`,
        { decision: value, expected_version: p.version },
        key(),
      );
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!token)
    return (
      <div className="login">
        <div className="brand">
          S<span>R</span>
        </div>
        <h1>ShopResolve</h1>
        <p>电商售后 · 可审计 Agent 工作台</p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              const data = await api("/v1/demo-sessions", {
                account,
                demo_code: code,
              });
              sessionStorage.setItem("shopresolve-token", data.access_token);
              setToken(data.access_token);
              setCode("");
              setError("");
            } catch (e) {
              setError((e as Error).message);
            }
          }}
        >
          <label>
            演示账号
            <select
              value={account}
              onChange={(e) => setAccount(e.target.value)}
            >
              <option>alice</option>
              <option>bob</option>
            </select>
          </label>
          <label>
            本地演示码
            <input
              type="password"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              required
              autoComplete="off"
            />
          </label>
          <button>进入工作台</button>
        </form>
        <p className="error">{error}</p>
        <small>仅本地合成业务，不涉及真实支付。演示码在项目 .env 中。</small>
      </div>
    );

  return (
    <div className="app">
      <aside>
        <div className="logo">
          Shop<span>Resolve</span>
        </div>
        <p className="muted">售后与订单异常处理</p>
        <button
          className="secondary"
          onClick={() => {
            setSelected("");
            setRun(null);
            setText("");
          }}
        >
          ＋ 新建任务
        </button>
        <h3>最近任务</h3>
        <nav>
          {runs.map((r) => (
            <button
              className={"run-item " + (r.id === selected ? "selected" : "")}
              key={r.id}
              onClick={() => setSelected(r.id)}
            >
              <strong>{r.goals[0]?.intent || "正在解析任务"}</strong>
              <small>
                {r.status} · {r.id.slice(0, 8)}
              </small>
            </button>
          ))}
        </nav>
        <div className="aside-bottom">
          <span className="dot" /> 本地模拟环境
          <button
            className="link"
            onClick={() => {
              clearSession();
            }}
          >
            退出
          </button>
        </div>
      </aside>
      <main>
        <header>
          <div>
            <p className="eyebrow">AGENT OPERATIONS</p>
            <h1>让每次操作都有依据</h1>
          </div>
          <span className="badge">{run?.provider || "demo"} · 合成数据</span>
        </header>
        {error && (
          <div className="error-box" role="alert">
            {error}
          </div>
        )}
        <div className="workspace">
          <section className="conversation panel">
            <div className="section-heading">
              <h2>处理工作区</h2>
              {run && <span className="badge">{run.status}</span>}
            </div>
            {!run && (
              <div className="empty">
                <h2>从一个具体售后问题开始</h2>
                <p>先核验订单与政策，再由你明确确认操作。</p>
                <button
                  className="example"
                  onClick={() => setText("帮我取消 order-pending")}
                >
                  取消待发货订单 →
                </button>
                <button
                  className="example"
                  onClick={() =>
                    setText(
                      "申请退货 order-delivered item-delivered 数量1 原因：单侧无声音",
                    )
                  }
                >
                  申请耳机退货 →
                </button>
                <button
                  className="example"
                  onClick={() => setText("取消订单的条件是什么？")}
                >
                  只咨询政策，不执行操作 →
                </button>
              </div>
            )}
            {run && (
              <>
                <div className="run-meta">
                  任务 {run.id.slice(0, 8)} · 模型请求 {run.model_calls} 次
                </div>
                {run.goals.map((g) => (
                  <div className="goal" key={g.id}>
                    <strong>
                      {g.id} · {g.intent}
                    </strong>
                    <span>{g.status}</span>
                    {g.decline_reason && <p>{g.decline_reason}</p>}
                  </div>
                ))}
                {run.question &&
                  ["WAITING_USER", "WAITING_APPROVAL"].includes(run.status) && (
                    <div className="question">{run.question}</div>
                  )}
                {proposals.map((p) => (
                  <article className="proposal" key={p.id}>
                    <span className="eyebrow">受控操作提案 · {p.status}</span>
                    <h3>
                      {p.action === "CANCEL_ORDER"
                        ? "取消订单并登记模拟退款"
                        : "创建退货申请（不立即退款）"}
                    </h3>
                    <dl>
                      <dt>订单</dt>
                      <dd>{p.order_id}</dd>
                      {p.item_id && (
                        <>
                          <dt>商品 / 数量</dt>
                          <dd>
                            {p.item_id} / {p.quantity}
                          </dd>
                        </>
                      )}
                      <dt>金额</dt>
                      <dd className="amount">
                        {money(p.amount_cents, p.currency)}
                      </dd>
                      <dt>有效至</dt>
                      <dd>{new Date(p.expires_at * 1000).toLocaleString()}</dd>
                    </dl>
                    <p className="muted">
                      确认仅授权这份提案。状态变化后需要重新确认。
                    </p>
                    <div className="actions">
                      <button
                        disabled={busy || p.status !== "PENDING"}
                        onClick={() => decision(p, "approve")}
                      >
                        确认这次操作
                      </button>
                      <button
                        className="secondary"
                        disabled={busy || p.status !== "PENDING"}
                        onClick={() => decision(p, "reject")}
                      >
                        拒绝
                      </button>
                    </div>
                  </article>
                ))}
                {run.answer && (
                  <div className="answer">
                    <span className="eyebrow">已核验结果 · {run.outcome}</span>
                    <pre>{run.answer}</pre>
                  </div>
                )}
                {!["SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"].includes(
                  run.status,
                ) && (
                  <button
                    className="link danger"
                    disabled={busy}
                    onClick={async () => {
                      try {
                        await api(`/v1/runs/${run.id}/cancel`, {}, key());
                        await refresh();
                      } catch (e) {
                        setError((e as Error).message);
                      }
                    }}
                  >
                    取消尚未执行的任务
                  </button>
                )}
              </>
            )}
            <form className="composer" onSubmit={submit}>
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="描述问题；等待时可补充信息。批准请使用确认卡。"
                maxLength={4000}
                rows={3}
              />
              <div>
                <small>不连接真实店铺和支付</small>
                <button disabled={busy || !text.trim()}>
                  {busy
                    ? "提交中…"
                    : run &&
                        ["WAITING_USER", "WAITING_APPROVAL"].includes(
                          run.status,
                        )
                      ? "补充 / 修改"
                      : "提交新任务"}
                </button>
              </div>
            </form>
          </section>
          <section className="side-panels">
            <ComparisonPanel token={token} />
            <div className="panel">
              <h2>当前账号订单</h2>
              {orders.map((o) => (
                <div className="order" key={o.id}>
                  <strong>{o.id}</strong>
                  <span>{o.status}</span>
                  <small>{money(o.paid_cents, o.currency)}</small>
                </div>
              ))}
            </div>
            <div className="panel">
              <h2>政策依据</h2>
              {!run?.evidence?.length && (
                <p className="muted">
                  检索后显示原文和版本，不以引用数量代替正确性。
                </p>
              )}
              {run?.evidence?.map((e) => (
                <details key={e.id}>
                  <summary>
                    {e.title || e.id}
                    <small>{e.version}</small>
                  </summary>
                  <p>{e.quote}</p>
                </details>
              ))}
            </div>
            <div className="panel">
              <h2>执行时间线</h2>
              <p className="muted">仅展示可审计事件，不展示模型私有思维链。</p>
              <ol className="timeline">
                {events.map((e) => (
                  <li key={e.seq}>
                    <span>#{e.seq}</span>
                    <strong>{e.kind}</strong>
                    <details>
                      <summary>查看事件</summary>
                      <pre>{JSON.stringify(e.payload, null, 2)}</pre>
                    </details>
                  </li>
                ))}
              </ol>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
function ComparisonPanel({ token }: { token: string }) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [result, setResult] = useState<unknown>(null);
  async function analyze(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setResult(null);
    setMessage("上传并校验 CSV…");
    const headers = {
      Authorization: `Bearer ${token}`,
      "Idempotency-Key": key(),
    };
    try {
      const upload = await fetch(
        `/v1/artifacts?filename=${encodeURIComponent(file.name)}`,
        { method: "POST", headers, body: file },
      );
      const artifact = await upload.json();
      if (!upload.ok) throw new Error(artifact.message || "上传失败");
      const response = await fetch("/v1/analyses", {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ artifact_id: artifact.id }),
      });
      const job = await response.json();
      if (!response.ok) throw new Error(job.message || "沙箱未启动");
      for (let i = 0; i < 50; i++) {
        const poll = await fetch(`/v1/analyses/${job.id}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        const data = await poll.json();
        if (!poll.ok) throw new Error(data.message || "无法读取分析结果");
        setMessage(`隔离任务：${data.status}`);
        if (data.status === "COMPLETED") {
          const output = data.outputs.find(
            (x: { name: string }) => x.name === "comparison.json",
          );
          if (!output) throw new Error("缺少计算结果");
          const bytes = Uint8Array.from(atob(output.base64), (c) =>
            c.charCodeAt(0),
          );
          setResult(JSON.parse(new TextDecoder().decode(bytes)));
          return;
        }
        if (["FAILED", "TIMED_OUT"].includes(data.status))
          throw new Error(`任务${data.status}，未生成有效结果`);
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
      throw new Error("等待超时，请检查沙箱服务");
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="panel">
      <h2>商品表隔离分析</h2>
      <p className="muted">
        独立沙箱模块；当前不是 Agent 自动委派。CSV 列为
        price_cents、quantity，可选 name；上限 2 MiB。
      </p>
      <form onSubmit={analyze}>
        <input
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />
        <button
          className="secondary"
          disabled={busy || !file}
          style={{ marginTop: 12 }}
        >
          {busy ? "处理中…" : "在无网络沙箱中计算"}
        </button>
      </form>
      <p className="muted" role="status">
        {message}
      </p>
      {result !== null && <pre>{JSON.stringify(result, null, 2)}</pre>}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(window.location.pathname === "/commerce" ? <App /> : <DocumentApp />);
