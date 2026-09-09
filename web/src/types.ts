export type Page<T> = { items: T[]; next_offset: number | null };
export type Queue = {
  pending: number;
  batched: number;
  waiting: number;
  threshold: number | null;
  reason: string;
  job_id: string | null;
  job_project: string | null;
};
export type Skill = {
  id: string;
  name: string;
  path: string;
  owned: number;
  enabled: boolean | null;
  project_count: number;
  wiki: Queue;
  version_count: number;
};
export type Project = {
  id: string;
  name: string;
  path: string;
  raw: Queue;
  raw_count: number;
  observation_count: number;
  wiki_pages: number;
  skills: Skill[];
  updated_at: number | null;
  active_jobs: number;
  failed_jobs: number;
};
export type Worker = {
  status: "unknown" | "online" | "stale" | "stopped";
  pid?: number;
  started?: number;
  heartbeat?: number;
  job_id?: string | null;
  last_activity?: number;
  error?: string | null;
};
export type Config = {
  raw_threshold: number;
  wiki_threshold: number;
  raw_auto: boolean;
  wiki_auto: boolean;
  codex_home: string;
  install_directory: string;
  executor: string;
  api_provider: string;
  api_url: string;
  api_model: string;
  api_key_configured: boolean;
  max_tokens: number;
  context_window: number;
  model: string | null;
  timeout_seconds: number;
  poll_seconds: number;
  auto_start: boolean;
  codex_command: string[];
};
export type Snapshot = {
  initialized: boolean;
  root: string;
  captured_at: number;
  cursor: number;
  config: Config | null;
  config_error: string | null;
  worker: Worker;
  projects: Project[];
  totals: { projects: number; skills: number; active: number; failed: number };
};
export type Job = {
  id: string;
  project: string;
  project_name: string;
  stage: "raw" | "skill";
  skill: string | null;
  skill_path: string | null;
  state: string;
  created: number;
  thread_id: string | null;
  error: string | null;
  report_sent: number;
  report_error: string | null;
  input_count: number;
  outcome: string | null;
  summary: string | null;
  publication_state: string | null;
  version_id: string | null;
  phase: string | null;
  updated_at: number | null;
  retry_count: number;
  running_confirmed: boolean;
  last_activity: number | null;
  content_status: string;
  report_status: string;
};
export type JobDetail = Job & {
  inputs: number[];
  result: Record<string, unknown> | null;
  report:
    | (Record<string, unknown> & { pages?: { name: string; body: string }[] })
    | null;
  previous_report: Record<string, unknown> | null;
  started_at: number | null;
  finished_at: number | null;
};
export type Event = {
  seq: number;
  kind: string;
  created: number;
  payload: Record<string, unknown>;
};
export type Observation = {
  problem: string;
  action: string;
  outcome: string;
  lesson: string;
};
export type Input = {
  id: number;
  body: string | Observation;
  name?: string;
  raw_id?: string;
  source_id?: string;
  job_id?: string;
};
export type Raw = {
  id: string;
  source_id: string;
  created: number;
  submitted: number;
  title: string | null;
  added: number;
};
export type RawDetail = {
  id: string;
  source_id: string;
  created: number;
  payload: { observations: Observation[]; metadata: Record<string, unknown> };
  observations: {
    id: number | null;
    body: Observation;
    duplicate: boolean;
    canonical_raw_id: string | null;
    consumed_by: string | null;
    batch: { id: string; state: string } | null;
  }[];
};
export type Wiki = {
  name: string;
  digest: string;
  excerpt: string;
  characters: number;
  revisions: number;
};
export type WikiChange = {
  diff?: string;
  id: number;
  body: string;
  digest: string;
  source_job: string | null;
  consumers: { id: string; path: string; consumed_by: string | null }[];
};
export type WikiDetail = {
  name: string;
  body: string;
  digest: string;
  metadata: Record<string, unknown>;
  changes: Page<WikiChange>;
};
export type WikiImportPreview = {
  preview_token: string;
  counts: { added: number; modified: number; unchanged: number };
  pages: {
    name: string;
    status: "added" | "modified" | "unchanged";
    body: string;
    before: string | null;
    expected_digest: string | null;
    diff: string;
  }[];
};
export type VersionSummary = {
  id: string;
  job_id: string;
  state: string;
  created: number;
  diff_size: number;
};
export type SkillDetail = {
  id: string;
  name: string;
  path: string;
  owned: number;
  enabled: boolean | null;
  projects: { id: string; name: string; path: string }[];
  versions: Page<VersionSummary>;
  published_text: string | null;
  disk_text: string | null;
  disk_error: string | null;
};
export type Bundle = {
  skill_md: string;
  files: { path: string; type: string; mode: string; bytes: number }[];
};
export type Version = VersionSummary & {
  skill: string;
  diff: string;
  source_job: string | null;
  before: Bundle;
  after: Bundle;
};
