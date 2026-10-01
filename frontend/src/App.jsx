import { useCallback, useEffect, useState } from "preact/hooks";

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

function ledgerEventLabel(event) {
  if (event === "reset") return "切日清零";
  if (event === "set_quota") return "改配额";
  return event;
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
  const [loginForm, setLoginForm] = useState({ username: "logger", password: "log123456" });
  const [submitForm, setSubmitForm] = useState({ probe_id: "", temp_c: "" });
  const [rows, setRows] = useState([]);
  const [page, setPage] = useState("readings");
  const [quota, setQuota] = useState(null);
  const [ledger, setLedger] = useState([]);
  const [quotaInput, setQuotaInput] = useState("");
  const [quotaMsg, setQuotaMsg] = useState("");
  const [quotaErr, setQuotaErr] = useState("");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [loading, setLoading] = useState(false);

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
    if (res.ok) setQuota(await res.json());
  }, [token, authHeaders]);

  const loadLedger = useCallback(async () => {
    if (!token) return;
    const res = await fetch("/api/quota/ledger", { headers: authHeaders() });
    if (res.ok) setLedger(await res.json());
  }, [token, authHeaders]);

  useEffect(() => {
    loadReadings();
    loadQuota();
    if (page === "quota") loadLedger();
    if (!token) return undefined;
    const t = setInterval(() => {
      loadReadings();
      loadQuota();
      if (page === "quota") loadLedger();
    }, 3000);
    return () => clearInterval(t);
  }, [loadReadings, loadQuota, loadLedger, token, page]);

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
    setPage("readings");
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
      await loadReadings();
      await loadQuota();
    } finally {
      setLoading(false);
    }
  }

  async function onSaveQuota(e) {
    e.preventDefault();
    setQuotaErr("");
    setQuotaMsg("");
    setLoading(true);
    try {
      const res = await fetch("/api/quota", {
        method: "PUT",
        headers: authHeaders(),
        body: JSON.stringify({ daily_quota: Number(quotaInput) }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setQuotaErr(data.detail || "保存配额失败");
        return;
      }
      setQuota(data);
      setQuotaMsg(`已设本日可交 ${data.daily_quota} 条，只影响之后提交`);
      setQuotaInput("");
      await loadLedger();
    } finally {
      setLoading(false);
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
            记录员 logger / log123456 · logger2 / log123456 · 值班员 watcher / watch123456
          </p>
        </div>
      </div>
    );
  }

  const isWriter = user?.role === "writer";

  return (
    <div class="wrap">
      <div class="topbar">
        <div>
          <h1>冷链探头超温台</h1>
          <p class="sub">温度不超过 8℃ 为合格，否则为超温。</p>
        </div>
        <div class="user">
          {user?.username}（{isWriter ? "记录员" : "值班员"}）
          <button
            type="button"
            class="secondary"
            style={{ marginLeft: "0.5rem" }}
            onClick={() => setPage(page === "quota" ? "readings" : "quota")}
          >
            {page === "quota" ? "返回列表" : "条数配额"}
          </button>
          <button type="button" class="secondary" style={{ marginLeft: "0.5rem" }} onClick={logout}>
            退出
          </button>
        </div>
      </div>

      {page === "quota" ? (
        <>
          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>条数配额</h2>
            {quota ? (
              <>
                <p class="sub" style={{ marginBottom: "0.75rem" }}>
                  自然日 {quota.day}（{quota.tz}）· 今日已交{" "}
                  <b>{quota.used}</b> 条 · 还剩 <b>{quota.remaining}</b> 条
                </p>
                {isWriter ? (
                  <form onSubmit={onSaveQuota}>
                    <div class="row">
                      <label>
                        本日可交条数
                        <input
                          required
                          type="number"
                          min="0"
                          step="1"
                          value={quotaInput}
                          onInput={(e) => setQuotaInput(e.target.value)}
                          placeholder={`当前 ${quota.daily_quota} 条`}
                        />
                      </label>
                      <button type="submit" disabled={loading}>
                        保存配额
                      </button>
                    </div>
                    <p class="sub" style={{ marginBottom: 0 }}>
                      当前配额 {quota.daily_quota} 条；改配额只影响之后提交，今日已交不清零。
                    </p>
                    {quotaErr && <p class="err">{quotaErr}</p>}
                    {quotaMsg && <p class="ok">{quotaMsg}</p>}
                  </form>
                ) : (
                  <p class="sub" style={{ marginBottom: 0 }}>
                    当前配额 {quota.daily_quota} 条。观察账号可翻已交与流水，不能改配额。
                  </p>
                )}
              </>
            ) : (
              <p class="sub" style={{ marginBottom: 0 }}>加载中…</p>
            )}
          </div>

          <div class="card">
            <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>清零流水</h2>
            <table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>事件</th>
                  <th>自然日</th>
                  <th>说明</th>
                  <th>操作人</th>
                </tr>
              </thead>
              <tbody>
                {ledger.map((r) => (
                  <tr key={r.id}>
                    <td>{r.created_at ? new Date(r.created_at).toLocaleString() : "—"}</td>
                    <td>{ledgerEventLabel(r.event)}</td>
                    <td>{r.day}</td>
                    <td>{r.detail}</td>
                    <td>{r.actor}</td>
                  </tr>
                ))}
                {ledger.length === 0 && (
                  <tr>
                    <td colspan="5">暂无流水</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <>
      {isWriter && (
        <div class="card">
          <h2 style={{ marginTop: 0, fontSize: "1.1rem" }}>提交读数</h2>
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
            {quota && (
              <p class="sub" style={{ marginBottom: 0 }}>
                今日已交 {quota.used} 条 / 配额 {quota.daily_quota} 条，还剩 {quota.remaining} 条
              </p>
            )}
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
    </div>
  );
}
