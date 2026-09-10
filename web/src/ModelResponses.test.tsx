import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ModelResponses } from './ModelResponses';

afterEach(() => {cleanup(); vi.unstubAllGlobals();});
it('shows retained model responses on demand without starting generation', async () => {
  const fetcher=vi.fn(async (_url: string, _options?: RequestInit) => Response.json({items:[{id:'attempt-0001',stage:'raw',provider:'ollama',call:1,created:1,
    response:{message:{content:'not JSON <script>doSomething()</script>',thinking:'separate reasoning'},done_reason:'stop'}}],next_offset:null}));
  vi.stubGlobal('fetch',fetcher);
  const {container}=render(<ModelResponses job="job" revision={0}/>);
  expect(fetcher).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('模型响应原文与工具调用'));
  await screen.findByText(/not JSON/);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe('/api/jobs/job/model-responses?offset=0');
  expect(container.querySelector('script')).toBeNull();
});
