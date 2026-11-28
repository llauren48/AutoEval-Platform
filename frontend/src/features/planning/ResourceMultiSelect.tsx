import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown, X } from "lucide-react";
import { matchesSshHost, type SshHost } from "./sshHosts";
import "./ResourceMultiSelect.css";

export function ResourceMultiSelect({ hosts, value, showChips, allowBindingSelection = false, onChange }: {
  hosts: SshHost[];
  showChips: boolean;
  allowBindingSelection?: boolean;
  value: string[];
  onChange: (ids: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  const matches = hosts.filter(host => matchesSshHost(host, query));
  const selected = hosts.filter(host => host.resources.some(resource => value.includes(resource.id)));
  return <div className="resource-multi-select" ref={root} onKeyDown={event => {
    if (event.key === "Escape") { setOpen(false); trigger.current?.focus(); }
  }}>
    <span id={`${id}-label`}>候选 SSH 主机</span>
    <button ref={trigger} type="button" className="resource-multi-trigger select-trigger" aria-label="规划执行资源" aria-expanded={open} aria-controls={`${id}-options`} onClick={() => { setQuery(""); setOpen(current => !current); }}>
      <span>{selected.length ? "调整候选主机" : "请选择 SSH 主机"}</span>
      {selected.length > 0 && <small>已选 {selected.length} 台主机</small>}
      <ChevronDown className="select-chevron" size={18} aria-hidden="true" />
    </button>
    {showChips && !!selected.length && <div className="inline resource-selected">{selected.map(host => <button type="button" className="badge" key={host.key} aria-label={`移除主机 ${host.label}`} onClick={() => onChange(value.filter(id => !host.resources.some(resource => resource.id === id)))}>{host.label} <X size={14} aria-hidden="true" /></button>)}</div>}
    {open && <div id={`${id}-options`} className="resource-multi-menu" role="group" aria-labelledby={`${id}-label`}>
      <div className="resource-multi-toolbar"><span>{hosts.length} 台候选主机</span><button type="button" disabled={!value.length} onClick={() => onChange([])}>清空</button></div>
      {!matches.length && <p className="muted">没有匹配的 SSH 节点。</p>}
      {hosts.length > 6 && <input aria-label="搜索 SSH 主机" placeholder="搜索节点名称或 SSH 地址" value={query} onChange={event => setQuery(event.target.value)} />}
      <div className="resource-multi-options">{matches.map(host => {
        const ids = host.resources.map(resource => resource.id);
        const checked = ids.some(id => value.includes(id));
        const combined = [...new Set([...value, ...ids])];
        const overLimit = combined.length > 16;
        return <div key={host.key}><label>
          <input type="checkbox" checked={checked} disabled={!checked && overLimit}
            onChange={() => onChange(checked ? value.filter(id => !ids.includes(id)) : combined)} />
          <span>{host.label}{host.login && host.login !== host.label && <small>{host.login}</small>}</span>
        </label>
          {(overLimit || (allowBindingSelection && ids.length > 1)) && <details><summary>{overLimit ? "按执行配置选择（最多 16 项）" : "单独绑定执行配置"}</summary>
            {host.resources.map(resource => <label key={resource.id}><input type="checkbox" checked={value.includes(resource.id)}
              disabled={value.length >= 16 && !value.includes(resource.id)}
              onChange={event => onChange(event.target.checked ? [...value, resource.id] : value.filter(id => id !== resource.id))} />
              <span>{resource.binding_key}</span></label>)}
          </details>}
        </div>;
      })}</div>
      <button type="button" className="resource-multi-done" onClick={() => { setOpen(false); trigger.current?.focus(); }}>完成选择</button>
    </div>}
  </div>;
}
