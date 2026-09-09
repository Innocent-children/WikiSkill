import { Fragment, useEffect, useState } from "react";
import {
  ArrowRight,
  ChevronRight,
  CircleAlert,
  Clock3,
  Folder,
  Layers3,
  RefreshCw,
  Server,
} from "lucide-react";
import { useLiveSnapshot } from "./api";
import {
  href,
  navigate,
  navigation,
  routeFromHash,
  watchRoute,
  type Route,
} from "./routing";
import { time } from "./presentation";
import { Empty, ErrorMessage, Loading } from "./components";
import { Overview } from "./pages/Overview";
import { Jobs, JobDetail } from "./pages/Jobs";
import { Knowledge } from "./pages/Knowledge";
import { Skills } from "./pages/Skills";
import { System } from "./pages/System";
import { Manage } from "./pages/Manage";

export function App() {
  const live = useLiveSnapshot();
  const [route, setRoute] = useState<Route>(routeFromHash);
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const stopWatching = watchRoute(setRoute);
    const clock = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => {
      stopWatching();
      clearInterval(clock);
    };
  }, []);
  const snapshot = live.data;
  const selectedProject = snapshot?.projects.find(
    (p) => p.id === route.project,
  );
  const title = navigation.find((item) => item.id === route.view)!.label;
  return (
    <div className="app-shell">
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        跳到主要内容
      </a>
      <aside className="sidebar">
        <a className="brand" href={href("manage")}>
          <span className="brand-mark">
            <Layers3 size={23} strokeWidth={1.8} />
          </span>
          <div>
            <strong>WikiSkill</strong>
            <span>个人知识工作室</span>
          </div>
        </a>
        <nav aria-label="主导航">
          {navigation.map((item, index) => (
            <Fragment key={item.id}>
              {[0, 3].includes(index) && (
                <span className="nav-group">
                  {index === 0 ? "工作空间" : "资料与设置"}
                </span>
              )}
              <a
                key={item.id}
                className={route.view === item.id ? "active" : ""}
                href={href(item.id, { project: route.project })}
                aria-current={route.view === item.id ? "page" : undefined}
              >
                <item.icon size={19} />
                <span>{item.label}</span>
                {item.id === "jobs" && !!snapshot?.totals.failed && (
                  <span className="nav-count">{snapshot.totals.failed}</span>
                )}
              </a>
            </Fragment>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span className="local-label">
            <span className="status-dot" />
            LOCAL
          </span>
          <span>项目经验，持续积累。</span>
          <div className="sidebar-worker">
            <Server size={16} />
            <span>
              {snapshot?.worker.status === "online"
                ? "后台在线"
                : snapshot?.worker.status === "stale"
                  ? "后台连接待确认"
                  : snapshot?.worker.status === "stopped"
                    ? "后台已停止"
                    : "等待后台连接"}
            </span>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <span>工作台</span>
            <ChevronRight size={13} />
            <strong>{title}</strong>
          </div>
          <div className="topbar-actions">
            {snapshot && route.view !== "system" && (
              <label className="project-picker">
                <Folder size={16} />
                <select
                  aria-label="选择项目"
                  value={route.project || ""}
                  onChange={(e) =>
                    navigate(route.view, {
                      project: e.target.value,
                      layer: route.layer,
                      ...(route.view === "skills"
                        ? {
                            q: route.q,
                            status: route.status,
                            source: route.source,
                          }
                        : {}),
                    })
                  }
                >
                  <option value="">全部项目</option>
                  {snapshot.projects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <span
              className={`connection ${live.connection === "connected" && !live.error ? "connected" : ""}`}
              role="status"
            >
              <span className="status-dot" />
              {live.error
                ? "读取异常"
                : live.connection === "connected"
                  ? "实时连接"
                  : live.connection === "connecting"
                    ? "正在连接"
                    : "正在重连"}
            </span>
            <button
              className="icon-button"
              aria-label="刷新状态"
              onClick={live.refresh}
            >
              <RefreshCw size={17} />
            </button>
          </div>
        </header>
        <main id="main-content" className="main" tabIndex={-1}>
          {route.view !== "manage" && (
            <div className="page-heading">
              <div>
                <div className="eyebrow">
                  {selectedProject ? selectedProject.name : "我的知识空间"}
                </div>
                <h1>{title}</h1>
              </div>
            </div>
          )}
          <ErrorMessage error={live.error} />
          {live.connection === "reconnecting" && snapshot && (
            <div className="notice warning" role="status">
              <Clock3 size={18} />
              <span>
                连接暂时中断，保留 {time(snapshot.captured_at, true)}{" "}
                的数据，正在重连。
              </span>
            </div>
          )}
          {snapshot?.config_error && (
            <ErrorMessage error={`配置读取失败：${snapshot.config_error}`} />
          )}
          {snapshot &&
            ["stale", "stopped"].includes(snapshot.worker.status) &&
            route.view !== "system" && (
              <div className="notice warning">
                <CircleAlert size={18} />
                <span>
                  {snapshot.worker.status === "stopped"
                    ? "后台已停止。"
                    : "后台心跳已过期。"}
                  批次显示最后一次记录。
                </span>
                <a href={href("system", { project: route.project })}>
                  查看运行信息
                  <ArrowRight size={14} />
                </a>
              </div>
            )}
          {!snapshot ? (
            !live.error && <Loading />
          ) : route.view === "system" ? (
            <System snapshot={snapshot} now={now} />
          ) : route.view === "manage" ? (
            <Manage
              wiki={route.wiki}
              snapshot={snapshot}
              revision={live.revision}
              refresh={live.refresh}
              project={route.project}
              layer={route.layer}
            />
          ) : route.project && !selectedProject ? (
            <Empty title="项目不存在">
              <a href={href("overview")}>返回全部项目</a>
            </Empty>
          ) : !snapshot.projects.length && route.view !== "skills" ? (
            <div className="panel onboarding">
              <Empty
                title={
                  snapshot.initialized
                    ? "等待第一条项目经验"
                    : "开始记录项目经验"
                }
              >
                <p>
                  连接 WikiSkill MCP 后正常使用
                  Codex，新增轨迹会自动出现在知识工作台。
                </p>
                {!snapshot.initialized && (
                  <pre className="code-block">wikiskill-codex init</pre>
                )}
                <span className="muted path">数据目录：{snapshot.root}</span>
              </Empty>
            </div>
          ) : route.view === "overview" ? (
            <Overview
              snapshot={snapshot}
              route={route}
              revision={live.revision}
              now={now}
            />
          ) : route.view === "jobs" ? (
            route.job ? (
              <JobDetail
                key={route.job}
                id={route.job}
                revision={live.revision}
                now={now}
              />
            ) : (
              <Jobs route={route} revision={live.revision} />
            )
          ) : route.view === "knowledge" ? (
            <Knowledge
              key={`${route.project}:${route.raw}:${route.wiki}`}
              snapshot={snapshot}
              route={route}
              revision={live.revision}
            />
          ) : (
            <Skills
              snapshot={snapshot}
              route={route}
              revision={live.revision}
            />
          )}
          <footer className="page-footer">
            <span>WikiSkill · 本地经验管理</span>
            {snapshot && (
              <span>最近同步 {time(snapshot.captured_at, true)}</span>
            )}
          </footer>
        </main>
      </div>
    </div>
  );
}
