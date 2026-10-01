import { useCallback, useEffect, useRef, useState } from "preact/hooks";

const TOKEN_KEY = "coldchain_token";
const USER_KEY = "coldchain_user";

function verdictClass(v, status) {
  if (v === "合格") return "tag pass";
  if (v === "超温") return "tag fail";
  if (status === "pending" || status === "processing") return "tag wait";
  return "tag wait";
}

function displayVerdict(row) {
  if (row.verdict) return row.verdict;
  if (row.status === "pending") return "待处理";
  if (row.status === "processing") return "处理中";
  return "—";
}

function fmtTime(iso) {
  if (!iso) return "—";
  return iso.replace("T", " ").replace(/\.\d+.*$/, "");
}

export function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(USER_KEY) || "null");
    } catch {
      return null;
    }
  });
  const [view, setView] = useState("desk");
  const [loginForm, setLoginForm] = useState({ username: "logger", password: "log123456" });
  const [submitForm, setSubmitForm] = useState({ probe_id: "", temp_c: "" });
  const [rows, setRows] = useState([]);
  const [quota, setQuota] = useState(null);
  const [ledger, setLedger] = useState([]);
  const [quotaInput, setQuotaInput] = useState("");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [quotaErr, setQuotaErr] = useState("");
  const [quotaMsg, setQuotaMsg] = useState("");
  const [loading, setLoading] = useState(false);
  const [savingQuota, setSavingQuota] = useState(false);
  const quotaInputSynced = useRef(false);

  const authHeaders = useCallback(() => {
    const h = { "Content-Type": "application/json" };
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }, [token]);

  const loadReadings = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/readings", { headers: authHeaders() });
    if (!res.ok) {
      setError("加载列表失败，请重新登录");
      return;
    }
    setRows(await res.json());
  }, [token, authHeaders]);

  const loadQuota = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/quota", { headers: authHeaders() });
    if (res.ok) {
      setQuota(await res.json());
    }
  }, [token, authHeaders]);

  const loadLedger = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/quota/ledger", { headers: authHeaders() });
    if (res.ok) setLedger(await res.json());
  }, [token, authHeaders]);

  useEffect(() => {
    loadReadings();
    loadQuota();
    if (!token) return undefined;
    const t = setInterval(() => {
      loadReadings();
      loadQuota();
    }, 3000);
    return () => clearInterval(t);
  }, [loadReadings, loadQuota, token]);

  // 只在首次拿到配额时同步输入框，轮询刷新不覆盖记录员正在编辑的值
  useEffect(() => {
    if (quota && !quotaInputSynced.current) {
      setQuotaInput(quota.quota_limit == null ? "" : String(quota.quota_limit));
      quotaInputSynced.current = true;
    }
  }, [quota]);

  useEffect(() => {
    if (token && view === "quota") loadLedger();
  }, [view, token, loadLedger]);

  async function onLogin(e) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(loginForm),
      });
      if (!res.ok) {
        setError("用户名或密码错误");
        return;
      }
      const data = await res.json();
      localStorage.setItem(TOKEN_KEY, data.access_token);
      localStorage.setItem(
        USER_KEY,
        JSON.stringify({ username: data.username, role: data.role })
      );
      setToken(data.access_token);
      setUser({ username: data.username, role: data.role });
    } finally {
      setLoading(false);
    }
  }

  function logout() {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    setToken(null);
    setUser(null);
    setRows([]);
    setQuota(null);
    setLedger([]);
    setView("desk");
  }

  async function onSubmit(e) {
    e.preventDefault();
    setError("");
    setMsg("");
    setLoading(true);
    try {
      const res = await fetch("/api/readings", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          probe_id: submitForm.probe_id,
          temp_c: parseFloat(submitForm.temp_c),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(data.detail || "提交失败");
        await loadQuota();
        return;
      }
      setMsg(data.message || "已提交");
      setSubmitForm({ probe_id: "", temp_c: "" });
      if (data.quota) setQuota(data.quota);
      await loadReadings();
    } finally {
      setLoading(false);
    }
  }

  async function onSaveQuota(e) {
    e.preventDefault();
    setQuotaErr("");
    setQuotaMsg("");
    const value = Number(quotaInput);
    if (!Number.isInteger(value) || value < 0 || quotaInput.trim() === "") {
      setQuotaErr("本日可交条数必须是不小于 0 的整数");
      return;
    }
    setSavingQuota(true);
    try {
      const res = await fetch("/api/quota", {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify({ quota_limit: value }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setQuotaErr(data.detail || "保存配额失败");
        return;
      }
      setQuota(data);
      setQuotaMsg(`已设置本日可交 ${data.quota_limit} 条，仅影响之后的提交`);
    } finally {
      setSavingQuota(false);
    }
  }

  if (!token) {
    return (
      <div class="wrap">
        <h1>冷链探头超温台</h1>
        <p class="sub">记录员提交探头编号与摄氏温度，后台工人认领后判定合格或超温。</p>
        <div class="card">
          <form onSubmit={onLogin}>
            <div class="row">
              <label>
                用户名
                <input
                  value={loginForm.username}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, username: e.target.value })
                  }
                />
              </label>
              <label>
                密码
                <input
                  type="password"
                  value={loginForm.password}
                  onInput={(e) =>
                    setLoginForm({ ...loginForm, password: e.target.value })
                  }
                />
              </label>
              <button type="submit" disabled={loading}>
                登录
              </button>
            </div>
            {error && <p class="err">{error}</p>}
          </form>
          <p class="sub" style={{ marginBottom: 0 }}>
            记录员 logger / log123456（logger2 / log2123456） · 值班员 watcher / watch123456
          </p>
        </div>
      </div>
    );
  }

  const isWriter = user?.role === "writer";
  const remainingText = !quota || quota.remaining == null ? "不限" : `${quota.remaining} 条`;

  return (
    <div class="wrap">
      <div class="topbar">
        <div>
          <h1>冷链探头超温台</h1>
          <nav class="nav">
            <a class={view === "desk" ? "active" : ""} onClick={() => setView("desk")}>
              超温台
            </a>
            <a class={view === "quota" ? "active" : ""} onClick={() => setView("quota")}>
              条数配额
            </a>
          </nav>
        </div>
        <div class="user">
          {user?.username}（{isWriter ? "记录员" : "值班员"}）
          <button type="button" class="secondary" style={{ marginLeft: "0.5rem" }} onClick={logout}>
            退出
          </button>
        </div>
      </div>

      {view === "desk" && (
        <>
          {isWriter && (
            <div class="card">
              <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>提交读数</h2>
              {quota && (
                <p class="sub" style={{ marginBottom: "0.75rem", marginTop: "-0.25rem" }}>
                  {quota.quota_limit == null
                    ? `今日已交 ${quota.used_count} 条，未设条数配额`
                    : `今日已交 ${quota.used_count} 条，配额 ${quota.quota_limit} 条，还剩 ${Math.max(0, quota.quota_limit - quota.used_count)} 条`}
                </p>
              )}
              <form onSubmit={onSubmit}>
                <div class="row">
                  <label>
                    探头编号
                    <input
                      required
                      value={submitForm.probe_id}
                      onInput={(e) =>
                        setSubmitForm({ ...submitForm, probe_id: e.target.value })
                      }
                      placeholder="例如 探头C03"
                    />
                  </label>
                  <label>
                    温度（℃）
                    <input
                      required
                      type="number"
                      step="0.1"
                      value={submitForm.temp_c}
                      onInput={(e) =>
                        setSubmitForm({ ...submitForm, temp_c: e.target.value })
                      }
                    />
                  </label>
                  <button type="submit" disabled={loading}>
                    提交
                  </button>
                </div>
                {error && <p class="err">{error}</p>}
                {msg && <p class="ok">{msg}</p>}
              </form>
            </div>
          )}

          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>读数列表</h2>
            <table>
              <thead>
                <tr>
                  <th>编号</th>
                  <th>探头</th>
                  <th>温度℃</th>
                  <th>结论</th>
                  <th>说明</th>
                  <th>状态</th>
                  <th>提交人</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td>{r.id}</td>
                    <td>{r.probe_id}</td>
                    <td>{r.temp_c}</td>
                    <td>
                      <span class={verdictClass(r.verdict, r.status)}>
                        {displayVerdict(r)}
                      </span>
                    </td>
                    <td>{r.reason || "—"}</td>
                    <td>{r.status}</td>
                    <td>{r.created_by}</td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr>
                    <td colspan="7">暂无数据</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {view === "quota" && (
        <>
          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>
              条数配额{quota ? `（${quota.day}）` : ""}
            </h2>
            <div class="stats">
              <div class="stat">
                <div class="stat-num">{quota ? quota.used_count : "—"}</div>
                <div class="stat-label">今日已交（条）</div>
              </div>
              <div class="stat">
                <div class="stat-num">{remainingText}</div>
                <div class="stat-label">还剩</div>
              </div>
              <div class="stat">
                <div class="stat-num">{quota && quota.quota_limit != null ? `${quota.quota_limit}` : "不限"}</div>
                <div class="stat-label">本日可交配额</div>
              </div>
            </div>

            <form onSubmit={onSaveQuota} class="quota-form">
              <label>
                本日可交条数
                <input
                  type="number"
                  min="0"
                  step="1"
                  value={quotaInput}
                  disabled={!isWriter}
                  onInput={(e) => setQuotaInput(e.target.value)}
                  placeholder={isWriter ? "例如 1" : ""}
                />
              </label>
              {isWriter ? (
                <button type="submit" disabled={savingQuota}>
                  保存配额
                </button>
              ) : (
                <p class="sub" style={{ margin: 0 }}>
                  值班员为观察账号，可查看已交条数与清零流水，不能修改配额。
                </p>
              )}
            </form>
            {quotaErr && <p class="err">{quotaErr}</p>}
            {quotaMsg && <p class="ok">{quotaMsg}</p>}
            {isWriter && (
              <p class="sub" style={{ marginBottom: 0 }}>
                配额调整只影响之后的提交，不改动今日已交条数；次日零点自动清零并承接本配额，同时记一条清零流水。
              </p>
            )}
          </div>

          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>清零流水</h2>
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>事件</th>
                  <th>当日</th>
                  <th>承自</th>
                  <th>上日已交</th>
                  <th>承接配额</th>
                </tr>
              </thead>
              <tbody>
                {ledger.map((r) => (
                  <tr key={r.id}>
                    <td>{fmtTime(r.created_at)}</td>
                    <td>切日清零</td>
                    <td>{r.day}</td>
                    <td>{r.prev_day || "—"}</td>
                    <td>{r.prev_used == null ? "—" : `${r.prev_used} 条`}</td>
                    <td>{r.carried_limit == null ? "不限" : `${r.carried_limit} 条`}</td>
                  </tr>
                ))}
                {ledger.length === 0 && (
                  <tr>
                    <td colspan="6">暂无清零流水（跨日后第一次提交或设额时生成）</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
