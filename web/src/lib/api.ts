const BASE = import.meta.env.VITE_API_BASE ?? '/rabota/api';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'same-origin',
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
    ...init,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* the error body was not JSON; the status code is all we have */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<any>('/healthz'),
  dashboard: () => request<any>('/dashboard'),
  activity: () => request<any[]>('/dashboard/activity'),

  jobs: (params: Record<string, string | number | boolean> = {}) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== '' && v !== null && v !== undefined && v !== false) qs.set(k, String(v));
    }
    return request<any>(`/jobs?${qs}`);
  },
  facets: () => request<any>('/jobs/facets'),
  rescan: () => request<any>('/jobs/rescan', { method: 'POST' }),
  job: (id: number) => request<any>(`/jobs/${id}`),
  skipJob: (id: number, reason_code: string, note?: string) =>
    request<any>(`/jobs/${id}/skip`, {
      method: 'POST',
      body: JSON.stringify({ reason_code, note }),
    }),
  draftJob: (id: number) => request<any>(`/jobs/${id}/draft`, { method: 'POST' }),

  drafts: (status?: string) =>
    request<any[]>(`/drafts${status ? `?status=${status}` : ''}`),
  draft: (id: number) => request<any>(`/drafts/${id}`),
  editDraft: (id: number, body: string) =>
    request<any>(`/drafts/${id}`, { method: 'PATCH', body: JSON.stringify({ body }) }),
  approveDraft: (id: number) => request<any>(`/drafts/${id}/approve`, { method: 'POST' }),
  markSent: (id: number, channel: string) =>
    request<any>(`/drafts/${id}/sent`, {
      method: 'POST',
      body: JSON.stringify({ channel }),
    }),
  discardDraft: (id: number) => request<any>(`/drafts/${id}/discard`, { method: 'POST' }),
  docxUrl: (id: number) => `${BASE}/drafts/${id}/docx`,

  applications: (stage?: string) =>
    request<any[]>(`/applications${stage ? `?stage=${stage}` : ''}`),
  advance: (id: number, stage: string, note?: string) =>
    request<any>(`/applications/${id}/advance`, {
      method: 'POST',
      body: JSON.stringify({ stage, note }),
    }),

  sources: () => request<any[]>('/sources'),
  patchSource: (id: number, patch: Record<string, unknown>) =>
    request<any>(`/sources/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  runSource: (id: number) => request<any>(`/sources/${id}/run`, { method: 'POST' }),

  profile: () => request<any>('/profile'),
  searchFilters: () => request<any>('/profile/search-filters'),
  saveSearchFilters: (patch: Record<string, unknown>) =>
    request<any>('/profile/search-filters', {
      method: 'PUT',
      body: JSON.stringify(patch),
    }),
  saveProfile: (patch: Record<string, unknown>) =>
    request<any>('/profile', { method: 'PUT', body: JSON.stringify(patch) }),
  saveVariant: (id: number, body: Record<string, unknown>) =>
    request<any>(`/profile/variants/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  createFact: (body: Record<string, unknown>) =>
    request<any>('/profile/facts', { method: 'POST', body: JSON.stringify(body) }),
  updateFact: (id: number, body: Record<string, unknown>) =>
    request<any>(`/profile/facts/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteFact: (id: number) => request<any>(`/profile/facts/${id}`, { method: 'DELETE' }),
};
