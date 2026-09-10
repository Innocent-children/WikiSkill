import { useState } from 'react';
import { useResource } from './api';
import { ErrorMessage, Json, Loading, Pager } from './components';
import { time } from './presentation';
import type { Page } from './types';

type Reply = {id: string; stage: string; provider: string; call: number; created: number; response: unknown};
export function ModelResponses({job, revision}: {job: string; revision: number}) {
  const [open, setOpen] = useState(false), [offset, setOffset] = useState(0);
  const data = useResource<Page<Reply>>(open ? `/api/jobs/${encodeURIComponent(job)}/model-responses?offset=${offset}` : null, revision);
  return <details className="panel document-panel" onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>模型响应原文与工具调用</summary>
    <p className="muted small">按执行尝试保存收到的响应，包括正文、思考字段、工具参数和结束原因。只读取本地记录，不重新调用模型。</p>
    <ErrorMessage error={data.error}/>{data.loading && <Loading/>}
    {data.data?.items.map(item => <article key={item.id}>
      <h4>{item.provider} · {item.stage === 'raw' ? 'Wiki Maintainer' : 'Skill Proposer'} · 请求 {item.call}</h4>
      <p className="muted small">{time(item.created)} · {item.id}</p><Json value={item.response}/>
    </article>)}
    {data.data && !data.data.items.length && <p>暂无响应记录。连接失败时模型可能未返回内容，旧批次也可能没有记录原文。</p>}
    {data.data && <Pager offset={offset} next={data.data.next_offset} onPage={setOffset}/>}
  </details>;
}
