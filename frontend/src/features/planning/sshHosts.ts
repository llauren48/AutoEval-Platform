import type { Resource } from "../evaluation/types";

export type SshHost<T extends Resource = Resource> = { key: string; label: string; login: string; resources: T[] };

export function groupSshHosts<T extends Resource>(resources: T[]): SshHost<T>[] {
  const hosts = new Map<string, SshHost<T>>();
  for (const resource of resources) {
    const address = resource.host?.trim().toLowerCase().replace(/^\[|\]$/g, "").replace(/\.$/, "");
    const port = resource.port ?? 22;
    const key = address && !resource.shared_image
      ? JSON.stringify([address, port, resource.username, resource.fingerprint, resource.auth_kind])
      : resource.id;
    let group = hosts.get(key);
    if (!group) {
      const endpoint = address ? `${address.includes(":") ? `[${address}]` : address}:${port}` : "";
      const login = endpoint && resource.username ? `${resource.username}@${endpoint}` : endpoint;
      group = { key, login, label: "", resources: [] };
      hosts.set(key, group);
    }
    group.resources.push(resource);
  }
  for (const host of hosts.values()) {
    const [first] = host.resources;
    host.label = first.display_name || (host.resources.length === 1 ? first.binding_key : host.login);
  }
  return [...hosts.values()];
}

export function matchesSshHost(host: SshHost, query: string) {
  const term = query.trim().toLowerCase();
  return [host.label, host.login, ...host.resources.map(resource => resource.binding_key)]
    .some(text => text.toLowerCase().includes(term));
}
