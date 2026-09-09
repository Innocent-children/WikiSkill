import {
  Activity,
  BookOpen,
  FileDiff,
  LayoutDashboard,
  Server,
} from "lucide-react";
import { query } from "./presentation";
import { confirmDiscardChanges } from "./unsaved";

export type View =
  "manage" | "overview" | "jobs" | "knowledge" | "skills" | "system";
export type Route = {
  view: View;
  project?: string;
  job?: string;
  raw?: string;
  wiki?: string;
  skill?: string;
  version?: string;
  layer?: string;
  state?: string;
  stage?: string;
  offset?: string;
  q?: string;
  status?: string;
  source?: string;
};
export const navigation = [
  { id: "manage", label: "知识工作台", icon: BookOpen },
  { id: "overview", label: "运行概览", icon: LayoutDashboard },
  { id: "jobs", label: "执行记录", icon: Activity },
  { id: "knowledge", label: "知识历史", icon: BookOpen },
  { id: "skills", label: "Skill 历史", icon: FileDiff },
  { id: "system", label: "设置", icon: Server },
] as const;

export function routeFromHash(): Route {
  const [path, search] = window.location.hash.replace(/^#\/?/, "").split("?");
  const view = navigation.some((item) => item.id === path)
    ? (path as View)
    : "manage";
  return { ...Object.fromEntries(new URLSearchParams(search)), view } as Route;
}

export function href(view: View, params: Omit<Route, "view"> = {}): string {
  return `#/${view}${query(params)}`;
}

export function navigate(view: View, params: Omit<Route, "view"> = {}) {
  const hash = href(view, params);
  if (activeNavigation) activeNavigation(hash);
  else window.location.hash = hash;
}

let activeNavigation: ((hash: string) => void) | null = null;
const historyKey = "wikiskillRouteIndex";

export function watchRoute(onChange: (route: Route) => void): () => void {
  let hash = window.location.hash;
  let index: number = window.history.state?.[historyKey] ?? 0;
  let restoring = false;
  const remember = () =>
    window.history.replaceState(
      { ...window.history.state, [historyKey]: index }, "", hash,
    );
  remember();

  const go = (nextHash: string) => {
    if (restoring || nextHash === hash || !confirmDiscardChanges()) return;
    hash = nextHash;
    index += 1;
    window.history.pushState({ [historyKey]: index }, "", hash);
    onChange(routeFromHash());
  };
  activeNavigation = go;

  const clicked = (event: MouseEvent) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey ||
        event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target instanceof Element
      ? event.target.closest<HTMLAnchorElement>("a[href]") : null;
    if (!link || link.hasAttribute("download") ||
        (link.target && link.target !== "_self")) return;
    const url = new URL(link.href);
    if (url.origin !== location.origin || url.pathname !== location.pathname ||
        url.search !== location.search || !url.hash.startsWith("#/")) return;
    event.preventDefault();
    go(url.hash);
  };

  const changed = () => {
    if (restoring) {
      if (window.location.hash === hash) restoring = false;
      return;
    }
    if (window.location.hash === hash) return;
    const nextIndex = window.history.state?.[historyKey];
    if (!confirmDiscardChanges()) {
      // 已标记的历史项通过原历史位置恢复；旧链接保留当前地址与编辑器。
      if (typeof nextIndex === "number" && nextIndex !== index) {
        restoring = true;
        window.history.go(index - nextIndex);
      } else {
        remember();
      }
      return;
    }
    hash = window.location.hash;
    index = typeof nextIndex === "number" ? nextIndex : index + 1;
    remember();
    onChange(routeFromHash());
  };
  document.addEventListener("click", clicked);
  window.addEventListener("hashchange", changed);
  return () => {
    if (activeNavigation === go) activeNavigation = null;
    document.removeEventListener("click", clicked);
    window.removeEventListener("hashchange", changed);
  };
}
