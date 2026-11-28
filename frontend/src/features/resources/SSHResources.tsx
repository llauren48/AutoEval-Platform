import { Badge } from "../../shared/ui/Badge";
import { Card } from "../../shared/ui/Card";
import { TableWrap } from "../../shared/ui/TableWrap";
import { REGISTRY_PAGE_SIZE } from "../../shared/constants";
import { OffsetPagination } from "../../shared/ui/OffsetPagination";
import { useEffect, useState } from "react";
import { apiGetOptions, apiPost } from "../../shared/api";

import type { NodeRegistration } from "../evaluation/types";
import { groupSshHosts, matchesSshHost } from "../planning/sshHosts";

type Resource = NodeRegistration & { id: string; binding_key: string; host: string; port: number; username: string; fingerprint: string; workdir: string; native_tool_root: string | null; native_bridge_sha256: string | null; native_lock_sha256: string | null; native_tool_revision: string | null };

const initialFields = { binding_key: "", host: "", port: "22", username: "", fingerprint: "", workdir: "" };

type Check = { passed: boolean; detail: string; time: string };

export function SSHResources({ adding: routeAdding, onAdd, onCancel, onSaved, onBack }: {
  adding?: boolean;
  onAdd?: () => void;
  onCancel?: () => void;
  onSaved?: () => void;
  onBack?: () => void;
} = {}) {
  const [localAdding, setLocalAdding] = useState(false);
  const adding = routeAdding ?? localAdding;
  const [query, setQuery] = useState("");
  const [checks, setChecks] = useState<Record<string, Check>>({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [credentialRef, setCredentialRef] = useState("");
  const [createdId, setCreatedId] = useState("");

  const [fields, setFields] = useState(initialFields);
  const [kind, setKind] = useState("password");
  const [toolRoot, setToolRoot] = useState("");
  const [secret, setSecret] = useState("");
  const [rows, setRows] = useState<Resource[]>([]);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (adding) return;
    const controller = new AbortController();
    setLoading(true);
    setLoadError("");
    apiGetOptions<Resource[]>("/ssh-resources?nodes=true", controller.signal)
      .then(({ data }) => { if (!controller.signal.aborted) setRows(data); })
      .catch(() => { if (!controller.signal.aborted) setLoadError("SSH 节点加载失败。"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [revision, adding]);
  const save = async () => {
    setBusy(true); setMessage("");
    try {
      let reference = credentialRef;
      if (!reference) {
        const credential = await apiPost<{ credential_ref: string }>("/ssh-resources/credentials", { auth_kind: kind, value: secret });
        reference = credential.credential_ref;
        setCredentialRef(reference);
        setSecret("");
      }
      const row = await apiPost<Resource>("/ssh-resources?node=true", { ...fields, port: Number(fields.port), auth_kind: kind, credential_ref: reference, native_tool_root: toolRoot.trim() || null });
      setCredentialRef("");
      setCreatedId(row.id);
      setMessage("节点已登记，可以验证 SSH 连接。");
    } catch (error) { setMessage(error instanceof Error ? error.message : "SSH资源登记失败。"); }
    finally { setBusy(false); }
  };
  const validate = async (ids: string[]) => {
    setBusy(true); setMessage("");
    for (const id of ids) {
      let status: Check;
      try {
        const result = await apiPost<{ evaluation_ready: boolean }>(`/ssh-resources/${id}/validate`);
        const detail = result.evaluation_ready
          ? "主机指纹、Python 3、SFTP 与原生 AISBench 已通过检查。"
          : "主机指纹、Python 3 和 SFTP 已通过检查。";
        status = { passed: true, detail, time: new Date().toLocaleString() };
      } catch (error) {
        status = { passed: false, detail: error instanceof Error ? error.message : "SSH 连接验证失败。", time: new Date().toLocaleString() };
      }
      setChecks(previous => ({ ...previous, [id]: status }));
    }
    setBusy(false);
  };
  const labels: Record<keyof typeof fields, string> = { binding_key: "节点名称", host: "主机地址", port: "端口", username: "用户名", fingerprint: "已批准主机指纹", workdir: "SSH 执行目录" };
  const resetForm = () => {
    setCreatedId("");
    setMessage("");
    setFields(initialFields);
    setKind("password");
    setToolRoot("");
    setSecret("");
    setCredentialRef("");
  };
  const finish = () => {
    resetForm();
    setOffset(0);
    setRevision(value => value + 1);
    if (onSaved) onSaved();
    else setLocalAdding(false);
  };
  if (adding) return <Card className="stack ssh-pool">
    {createdId ? <>
      <h2>{fields.binding_key}</h2>
      <p role="status">{message || checks[createdId]?.detail}</p>
      <div className="inline">
        <button disabled={busy} onClick={() => void validate([createdId])}>验证 SSH 连接</button>
        <button className="primary" disabled={busy} onClick={finish}>{onBack ? "返回控制台" : "返回资源池"}</button>
      </div>
    </> : <form className="stack" onSubmit={event => { event.preventDefault(); void save(); }}>
      <h2>节点信息</h2>
      <div className="ssh-pool-fields">
        {Object.entries(labels).map(([key, label]) => <label className={["fingerprint", "workdir"].includes(key) ? "field-wide" : ""} key={key}>{label}<input required type={key === "port" ? "number" : "text"} min={key === "port" ? 1 : undefined} max={key === "port" ? 65535 : undefined} value={fields[key as keyof typeof fields]} onChange={event => setFields({ ...fields, [key]: event.target.value })} /></label>)}
        <label>SSH 凭据类型<select value={kind} onChange={event => { setKind(event.target.value); setSecret(""); setCredentialRef(""); }}><option value="password">密码</option><option value="private_key">私钥</option></select></label>
        <label className="field-wide">SSH 凭据{kind === "password" ? <input type="password" autoComplete="new-password" value={secret} onChange={event => { setSecret(event.target.value); setCredentialRef(""); }} /> : <textarea value={secret} onChange={event => { setSecret(event.target.value); setCredentialRef(""); }} rows={5} />}</label>
        {credentialRef && <p className="muted field-wide">凭据已保存，可修正节点信息后重试登记。</p>}
      </div>
      <h2>工具能力</h2>
      <p className="muted">容器镜像、挂载模板和并发容量由服务端配置。测评所需工具版本在控制台按数据集预检。</p>
      <label>原生工具目录（可选）<input value={toolRoot} onChange={event => setToolRoot(event.target.value)} /></label>
      {message && <p role="alert">{message}</p>}
      <div className="inline">
        <button type="button" disabled={busy} onClick={() => { resetForm(); if (onCancel) onCancel(); else setLocalAdding(false); }}>取消</button>
        <button className="primary" disabled={busy || (!secret && !credentialRef) || Object.values(fields).some(value => !value.trim())}>保存节点</button>
      </div>
    </form>}
  </Card>;

  const filtered = groupSshHosts(rows).filter(host => matchesSshHost(host, query));
  const page = filtered.slice(offset, offset + REGISTRY_PAGE_SIZE);
  return <Card className="stack ssh-pool">
    <div className="spread"><h2>SSH 节点</h2><div className="inline">
      {onBack && <button onClick={onBack}>返回控制台</button>}
      <button className="primary" onClick={() => onAdd ? onAdd() : setLocalAdding(true)}>添加节点</button>
    </div></div>
    <p className="muted">连接验证检查 SSH 基础环境。容器工具和数据目录按测评任务预检。</p>
    {loading ? <p role="status">正在加载节点…</p> : loadError ? <div className="inline"><p role="alert">{loadError}</p><button onClick={() => setRevision(value => value + 1)}>重新加载</button></div> : !rows.length ? <p>当前资源池为空。添加节点后即可回控制台选择。</p> : <>
      <input type="search" aria-label="搜索节点名称或 SSH 地址" placeholder="搜索节点名称或 SSH 地址" value={query} onChange={event => { setQuery(event.target.value); setOffset(0); }} />
      {page.length ? <TableWrap><table><thead><tr><th>节点名称</th><th>SSH 连接</th><th>执行环境</th><th>操作</th></tr></thead><tbody>
        {page.map(host => {
          const [row] = host.resources;
          const grouped = host.resources.length > 1;
          const checked = host.resources.map(resource => checks[resource.id]).filter(Boolean);
          const failed = checked.filter(check => !check.passed).length;
          const passed = !failed && checked.length === host.resources.length;
          const status = failed ? (grouped ? `${failed} 项验证失败` : "验证失败") : passed ? "验证通过" : checked.length ? `已验证 ${checked.length}/${host.resources.length}` : "未验证";
          return <tr key={host.key}>
            <td>{host.label}{host.login && host.login !== host.label && <small>{host.login}</small>}</td>
            <td><Badge tone={failed ? "red" : passed ? "green" : "gray"}>{status}</Badge>
              {!grouped && checked[0] && <details><summary>{checked[0].time}</summary><p>{checked[0].detail}</p></details>}
            </td>
            <td>{grouped ? <details><summary>{host.resources.length} 项执行配置</summary>
              {host.resources.map(resource => <div key={resource.id} className="ssh-binding-detail">
                <strong>{resource.binding_key}</strong><small>工作目录 {resource.workdir}</small>
                {resource.native_tool_revision && <small>AISBench 原生 · {resource.native_tool_revision.slice(0, 12)}</small>}
                {checks[resource.id] && <small>{checks[resource.id].time} · {checks[resource.id].detail}</small>}
              </div>)}
            </details> : row.shared_image ? <details>
              <summary>AISBench · {row.shared_image.version} · {Object.keys(row.datasets || {}).length} 个数据集</summary>
              {Object.entries(row.datasets || {}).map(([dataset, registration]) => <div key={dataset}>
                <strong>{dataset}</strong><small>已登记{registration.last_passed_at && ` · 最近预检通过 ${new Date(registration.last_passed_at).toLocaleString()}`}</small>
                {registration.mounts.map(mount => <small key={mount.target}>{mount.source} → {mount.target}</small>)}
              </div>)}
            </details> : row.native_tool_revision ? <span title={row.native_tool_revision}>AISBench 原生 · {row.native_tool_revision.slice(0, 12)}</span> : "按服务端配置"}</td>
            <td><button disabled={busy} onClick={() => void validate(host.resources.map(resource => resource.id))}>
              {grouped ? `验证 ${host.resources.length} 项连接` : "验证 SSH 连接"}</button></td>
          </tr>;
        })}
      </tbody></table></TableWrap> : <p>没有匹配的节点。</p>}
      <OffsetPagination label="SSH 节点" offset={offset} nextOffset={offset + REGISTRY_PAGE_SIZE < filtered.length ? offset + REGISTRY_PAGE_SIZE : null} onChange={setOffset} disabled={busy} />
    </>}
  </Card>;
}
