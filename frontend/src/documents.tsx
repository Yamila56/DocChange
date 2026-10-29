import React, { useEffect, useState } from "react";
import "./documents.css";
type File = { content: string; role: "document" | "source" | "archive" };
type Workspace = {
  id: string;
  name: string;
  revision: number;
  documents?: Record<string, File>;
  active_revision?: number;
};
type Evidence = {
  id: string;
  path: string;
  quote: string;
  version: string;
  start_line: number;
};
type Run = {
  id: string;
  domain: string;
  status: string;
  answer?: string;
  question?: string;
  changeset_id?: string;
  provider: string;
  model_calls: number;
  stop_reason?: string;
  evidence: Evidence[];
  version: number;
  resume_generation: number;
};
type Change = {
  id: string;
  status: string;
  payload_hash: string;
  summary: string;
  diffs: Record<string, string>;
  validation: unknown;
  published_revision?: number;
};
const uid = () => crypto.randomUUID();
const samples: Record<string, File> = {
  "guide.md": {
    role: "document",
    content:
      "# 接入指南\n\n使用 user_id 指定账户。\n\n调用示例：GET /accounts?user_id=42\n\n参考 [接口](api.md)。\n",
  },
  "api.md": {
    role: "document",
    content: "# API\n\n参数 user_id 为必填字符串。\n\n超时：30 秒。\n",
  },
  "release-v2.md": {
    role: "source",
    content:
      "# V2 发布说明\n\n新版本统一将 user_id 更名为 account_id。\n其他行为与超时保持不变。\n历史版本文档不需要修改。\n",
  },
  "archive-v1.md": {
    role: "archive",
    content: "# V1 历史文档\n\nV1 使用 user_id，保留此历史说明。\n",
  },
};

export default function DocumentApp() {
  const [token, setToken] = useState(
    sessionStorage.getItem("shopresolve-token") || "",
  );
  const [code, setCode] = useState("");
  const [account, setAccount] = useState("alice");
  const [spaces, setSpaces] = useState<Workspace[]>([]);
  const [wid, setWid] = useState(
    sessionStorage.getItem("docchange-workspace") || "",
  );
  const [space, setSpace] = useState<Workspace | null>(null);
  const [path, setPath] = useState("");
  const [runs, setRuns] = useState<Run[]>([]);
  const [rid, setRid] = useState(sessionStorage.getItem("docchange-run") || "");
  const [run, setRun] = useState<Run | null>(null);
  const [change, setChange] = useState<Change | null>(null);
  const [events, setEvents] = useState<{ seq: number; kind: string }[]>([]);
  const [mode, setMode] = useState("update");
  const [text, setText] = useState(
    "根据 V2 发布说明，将当前文档中的 user_id 更新为 account_id，保留历史文档与超时。",
  );
  const [oldText, setOld] = useState("user_id");
  const [newText, setNew] = useState("account_id");
  const [query, setQuery] = useState("");
  const [reply, setReply] = useState("");
  const [hits, setHits] = useState<Evidence[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [sandboxResult, setSandboxResult] = useState<unknown>(null);
  const [importing, setImporting] = useState(false);
  const [bundle, setBundle] = useState(JSON.stringify(samples, null, 2));
  async function api(url: string, body?: unknown) {
    const r = await fetch(url, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        ...(body !== undefined
          ? { "Content-Type": "application/json", "Idempotency-Key": uid() }
          : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok)
      throw new Error(
        data.message || JSON.stringify(data.detail) || r.statusText,
      );
    return data;
  }
  async function act(f: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await f();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function refresh() {
    const available: Workspace[] = await api("/v1/documents/workspaces");
    setSpaces(available);
    setWid((current) =>
      available.some((w) => w.id === current)
        ? current
        : available[0]?.id || "",
    );
    setRuns(
      (await api("/v1/runs")).filter((r: Run) => r.domain === "document"),
    );
  }
  async function refreshSpace() {
    if (wid) setSpace(await api(`/v1/documents/workspaces/${wid}`));
  }
  async function loadRun() {
    if (!rid) return;
    const v = await api(`/v1/runs/${rid}`);
    setRun(v);
    setRuns((items) => items.map((item) => (item.id === v.id ? v : item)));
    setEvents(await api(`/v1/documents/runs/${rid}/timeline`));
    setChange(
      v.changeset_id
        ? await api(`/v1/documents/changesets/${v.changeset_id}`)
        : null,
    );
  }
  useEffect(() => {
    if (token) void act(refresh);
  }, [token]);
  useEffect(() => {
    sessionStorage.setItem("docchange-workspace", wid);
    if (token && wid) void act(refreshSpace);
  }, [wid, token]);
  useEffect(() => {
    sessionStorage.setItem("docchange-run", rid);
    setRun(null);
    setChange(null);
    if (!token || !rid) return;
    void act(loadRun);
    const t = setInterval(
      () => void loadRun().catch((e) => setError(String(e))),
      1500,
    );
    return () => clearInterval(t);
  }, [rid, token]);
  useEffect(() => {
    if (space?.documents && !space.documents[path])
      setPath(Object.keys(space.documents)[0] || "");
  }, [space]);
  if (!token)
    return (
      <div className="doc-login">
        <div className="doc-card">
          <span className="doc-eyebrow">DOCCHANGE / LOCAL WORKSPACE</span>
          <h1>让文档变更有据可查</h1>
          <p>检索资料、审核修改、发布版本。</p>
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
            />
          </label>
          <button
            disabled={busy}
            onClick={() =>
              act(async () => {
                const d = await api("/v1/demo-sessions", {
                  account,
                  demo_code: code,
                });
                sessionStorage.setItem("shopresolve-token", d.access_token);
                setToken(d.access_token);
              })
            }
          >
            进入工作台
          </button>
          {error && <p role="alert">{error}</p>}
        </div>
      </div>
    );
  return (
    <div className="doc-app">
      <header className="doc-header">
        <div>
          <span className="doc-eyebrow">DOCCHANGE</span>
          <h1>版本化文档工作台</h1>
        </div>
        <div className="doc-actions">
          <a href="/commerce">旧业务</a>
          <button onClick={() => act(refresh)}>刷新</button>
          <button
            onClick={() => {
              sessionStorage.removeItem("shopresolve-token");
              setToken("");
            }}
          >
            退出
          </button>
        </div>
      </header>
      {error && (
        <div className="doc-error" role="alert">
          {error}
        </div>
      )}
      <div className="doc-toolbar">
        <select
          aria-label="工作区"
          value={wid}
          onChange={(e) => setWid(e.target.value)}
        >
          <option value="">选择工作区</option>
          {spaces.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name} · R{w.revision}
            </option>
          ))}
        </select>
        <button
          disabled={busy}
          onClick={() =>
            act(async () => {
              const w = await api("/v1/documents/workspaces", {
                name: "接口文档示例",
                documents: samples,
              });
              await refresh();
              setWid(w.id);
            })
          }
        >
          创建示例工作区
        </button>
        <button onClick={() => setImporting(!importing)}>导入文档 JSON</button>
        <span className="doc-muted">当前快照 R{space?.revision || "—"}</span>
        {space && (
          <select
            aria-label="文档版本"
            value={space.revision}
            onChange={(e) =>
              act(async () => {
                setSpace(
                  await api(
                    `/v1/documents/workspaces/${wid}?revision=${e.target.value}`,
                  ),
                );
              })
            }
          >
            {Array.from(
              { length: space.active_revision || space.revision },
              (_, i) => i + 1,
            ).map((n) => (
              <option key={n} value={n}>
                快照 R{n}
              </option>
            ))}
          </select>
        )}
      </div>
      {importing && (
        <section className="doc-panel">
          <h2>导入工作区</h2>
          <p>路径对应 content 与 role（document、source、archive）。</p>
          <textarea
            aria-label="文档 JSON"
            rows={8}
            value={bundle}
            onChange={(e) => setBundle(e.target.value)}
          />
          <button
            onClick={() =>
              act(async () => {
                const w = await api("/v1/documents/workspaces", {
                  name: "导入文档",
                  documents: JSON.parse(bundle),
                });
                await refresh();
                setWid(w.id);
                setImporting(false);
              })
            }
          >
            导入
          </button>
        </section>
      )}
      <div className="doc-grid">
        <aside className="doc-panel">
          <h2>文档</h2>
          {Object.entries(space?.documents || {}).map(([p, d]) => (
            <button
              className={`doc-file ${p === path ? "selected" : ""}`}
              key={p}
              onClick={() => setPath(p)}
            >
              {p}
              <small>{d.role}</small>
            </button>
          ))}
          <h2>任务记录</h2>
          {runs.map((r) => (
            <button
              className={`doc-file ${r.id === rid ? "selected" : ""}`}
              key={r.id}
              onClick={() => {
                setRid(r.id);
              }}
            >
              {r.status}
              <small>{r.id.slice(0, 8)}</small>
            </button>
          ))}
        </aside>
        <main className="doc-main">
          <section className="doc-panel">
            <div className="doc-panel-head">
              <h2>{path || "文档预览"}</h2>
              <span className="doc-muted">R{space?.revision || "—"}</span>
            </div>
            <pre className="doc-content">
              {space?.documents?.[path]?.content || "选择一篇文档"}
            </pre>
            <div className="doc-search">
              <input
                aria-label="检索"
                placeholder="检索工作区资料"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <button
                disabled={!wid}
                onClick={() =>
                  act(async () =>
                    setHits(
                      await api(
                        `/v1/documents/workspaces/${wid}/search?revision=${space?.revision || 1}&query=${encodeURIComponent(query)}`,
                      ),
                    ),
                  )
                }
              >
                检索
              </button>
            </div>
            {hits.map((h, i) => (
              <blockquote key={i}>
                <b>
                  {h.path}:{h.start_line}
                </b>
                <p>{h.quote}</p>
              </blockquote>
            ))}
          </section>
          <section className="doc-panel">
            <h2>新任务</h2>
            <div className="doc-actions">
              <button
                className={mode === "update" ? "primary" : ""}
                onClick={() => setMode("update")}
              >
                修改文档
              </button>
              <button
                className={mode === "query" ? "primary" : ""}
                onClick={() => setMode("query")}
              >
                查询资料
              </button>
            </div>
            <textarea
              aria-label="任务要求"
              rows={3}
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
            {mode === "update" && (
              <>
                <div className="doc-replace">
                  <label>
                    演示原文
                    <input
                      value={oldText}
                      onChange={(e) => setOld(e.target.value)}
                    />
                  </label>
                  <label>
                    演示新文
                    <input
                      value={newText}
                      onChange={(e) => setNew(e.target.value)}
                    />
                  </label>
                </div>
                <p className="doc-muted">
                  免费演示按明确原文替换；模型模式根据任务与来源自主生成提案。
                </p>
              </>
            )}
            <button
              className="primary"
              disabled={!wid || busy}
              onClick={() =>
                act(async () => {
                  const r = await api(`/v1/documents/workspaces/${wid}/tasks`, {
                    text,
                    mode,
                    old: oldText,
                    new: newText,
                    revision: space?.revision,
                  });
                  setRid(r.run_id);
                  await refresh();
                })
              }
            >
              提交任务
            </button>
          </section>
          {run && (
            <section className="doc-panel">
              <div className="doc-panel-head">
                <h2>任务结果</h2>
                <span className="doc-badge">{run.status}</span>
              </div>
              <p className="doc-muted">
                {run.provider === "demo"
                  ? "演示控制器（非 LLM）"
                  : run.provider}{" "}
                · {run.model_calls} 次决策调用
              </p>
              <p className="doc-answer">
                {run.answer || run.question || "正在处理…"}
              </p>
              {run.stop_reason && <p>{run.stop_reason}</p>}
              {run.status === "WAITING_USER" && (
                <div className="doc-search">
                  <input
                    aria-label="补充信息"
                    placeholder="补充要求或明确选择后继续"
                    value={reply}
                    onChange={(e) => setReply(e.target.value)}
                  />
                  <button
                    disabled={busy || !reply.trim()}
                    onClick={() =>
                      act(async () => {
                        await api(`/v1/runs/${rid}/revisions`, {
                          text: reply,
                          client_message_id: uid(),
                          expected_version: run.version,
                          wait_generation: run.resume_generation,
                        });
                        setReply("");
                        await loadRun();
                      })
                    }
                  >
                    继续任务
                  </button>
                </div>
              )}
              {!["SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED"].includes(
                run.status,
              ) && (
                <button
                  onClick={() =>
                    act(async () => {
                      await api(`/v1/runs/${rid}/cancel`, {});
                      await loadRun();
                    })
                  }
                >
                  取消任务
                </button>
              )}
              {change && (
                <>
                  <h3>{change.summary}</h3>
                  <div className="doc-actions">
                    <button
                      disabled={busy}
                      onClick={() =>
                        act(async () =>
                          setSandboxResult(
                            await api(
                              `/v1/documents/changesets/${change.id}/checks`,
                              {},
                            ),
                          ),
                        )
                      }
                    >
                      沙箱复核
                    </button>
                    <button
                      onClick={() => {
                        const link = document.createElement("a");
                        link.href = URL.createObjectURL(
                          new Blob([JSON.stringify(change, null, 2)], {
                            type: "application/json",
                          }),
                        );
                        link.download = `changeset-${change.id}.json`;
                        link.click();
                        URL.revokeObjectURL(link.href);
                      }}
                    >
                      导出修改记录
                    </button>
                  </div>
                  {sandboxResult !== null && (
                    <pre className="doc-content">
                      {JSON.stringify(sandboxResult, null, 2)}
                    </pre>
                  )}
                  {Object.entries(change.diffs).map(([p, diff]) => (
                    <div key={p}>
                      <b>{p}</b>
                      <pre className="doc-diff">{diff}</pre>
                    </div>
                  ))}
                  <details>
                    <summary>程序检查结果</summary>
                    <pre>{JSON.stringify(change.validation, null, 2)}</pre>
                  </details>
                  {change.status === "PENDING" &&
                    run.status === "WAITING_APPROVAL" && (
                      <div className="doc-actions">
                        <button
                          className="primary"
                          disabled={busy}
                          onClick={() =>
                            act(async () => {
                              await api(
                                `/v1/documents/changesets/${change.id}/decision`,
                                {
                                  approve: true,
                                  expected_hash: change.payload_hash,
                                },
                              );
                              await loadRun();
                              await refreshSpace();
                              await refresh();
                            })
                          }
                        >
                          批准并发布
                        </button>
                        <button
                          disabled={busy}
                          onClick={() =>
                            act(async () => {
                              await api(
                                `/v1/documents/changesets/${change.id}/decision`,
                                {
                                  approve: false,
                                  expected_hash: change.payload_hash,
                                },
                              );
                              await loadRun();
                            })
                          }
                        >
                          拒绝修改
                        </button>
                      </div>
                    )}
                </>
              )}
              <details>
                <summary>依据 · {run.evidence.length}</summary>
                {run.evidence.map((e) => (
                  <blockquote key={e.id}>
                    <b>
                      {e.path}:{e.start_line}
                    </b>
                    <small> {e.version}</small>
                    <p>{e.quote}</p>
                  </blockquote>
                ))}
              </details>
              <details>
                <summary>执行时间线 · {events.length}</summary>
                {events.map((e) => (
                  <p key={e.seq}>
                    {e.seq} · {e.kind}
                  </p>
                ))}
              </details>
            </section>
          )}
        </main>
      </div>
      <footer>不可变快照 · 审批绑定差异 · 发布与索引一致</footer>
    </div>
  );
}
