import {
  Activity,
  BookOpen,
  FileDiff,
  LayoutDashboard,
  Server,
} from "lucide-react";
import { query } from "./presentation";

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
};
export const navigation = [
  { id: "manage", label: "知识工作台", icon: BookOpen },
  { id: "overview", label: "运行总览", icon: LayoutDashboard },
  { id: "jobs", label: "优化批次", icon: Activity },
  { id: "knowledge", label: "旧摘要与 Wiki 历史", icon: BookOpen },
  { id: "skills", label: "Skill 历史", icon: FileDiff },
  { id: "system", label: "设置与运行信息", icon: Server },
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
  window.location.hash = href(view, params);
}
