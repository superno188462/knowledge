/** Gateway settings and per-user project-context API contracts. */
import { KNOWLEDGE_API_HOST } from '@/config';

export interface GatewayKeyStatus {
  configured: boolean;
  valid: boolean;
  last_four?: string;
  updated_at?: string;
  vector_access?: boolean;
  collection_exists?: boolean;
  error_code?: string;
  message?: string;
}

export interface UserProfile {
  profile: Record<string, unknown>;
  version: number | null;
}

export interface UserMemory {
  id: string;
  content: string;
  tags: string[];
  metadata: Record<string, unknown>;
  version: number;
}

export interface UserMemoryPage {
  items: UserMemory[];
  page: number;
  page_size: number;
  total: number;
}

export interface UserSessionMessage {
  id: string;
  sequence: number;
  role: string;
  content: string;
  metadata: Record<string, unknown>;
}

export interface UserSessionMessages {
  session_id: string;
  messages: UserSessionMessage[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${KNOWLEDGE_API_HOST}${path}`, {
      ...init,
      credentials: 'include',
      headers: {
        ...(init?.body ? { 'content-type': 'application/json' } : {}),
        ...init?.headers,
      },
    });
  } catch {
    throw new Error('无法连接 Knowledge 后端，请确认服务已启动。');
  }

  if (response.status === 204) return undefined as T;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(
      typeof payload?.detail?.message === 'string'
        ? payload.detail.message
        : 'Gateway 操作失败，请检查设置后重试。'
    );
  }
  return payload as T;
}

export const gatewayApi = {
  getKeyStatus: () => request<GatewayKeyStatus>('/api/settings/gateway-key'),
  saveKey: (apiKey: string) =>
    request<GatewayKeyStatus>('/api/settings/gateway-key', {
      method: 'PUT',
      body: JSON.stringify({ api_key: apiKey }),
    }),
  deleteKey: () => request<void>('/api/settings/gateway-key', { method: 'DELETE' }),
  getSessionMessages: (sessionId: string, limit = 50) =>
    request<UserSessionMessages>(
      `/api/user-context/sessions/${encodeURIComponent(sessionId)}/messages?limit=${limit}`
    ),
  deleteSessionMessages: (sessionId: string) =>
    request<void>(`/api/user-context/sessions/${encodeURIComponent(sessionId)}/messages`, {
      method: 'DELETE',
    }),
  getProfile: () => request<UserProfile>('/api/user-context/profile'),
  saveProfile: (profile: Record<string, unknown>, expectedVersion: number | null) =>
    request<UserProfile>('/api/user-context/profile', {
      method: 'PUT',
      body: JSON.stringify({ profile, expected_version: expectedVersion ?? undefined }),
    }),
  listMemories: (page: number, pageSize = 50) =>
    request<UserMemoryPage>(
      `/api/user-context/memories?${new URLSearchParams({
        page: String(page),
        page_size: String(pageSize),
      })}`
    ),
  createMemory: (content: string, tags: string[], metadata: Record<string, unknown>) =>
    request<UserMemory>('/api/user-context/memories', {
      method: 'POST',
      body: JSON.stringify({ content, tags, metadata }),
    }),
  updateMemory: (memory: UserMemory, content: string) =>
    request<UserMemory>(`/api/user-context/memories/${encodeURIComponent(memory.id)}`, {
      method: 'PATCH',
      body: JSON.stringify({
        content,
        tags: memory.tags,
        metadata: memory.metadata,
        expected_version: memory.version,
      }),
    }),
  deleteMemory: (memory: UserMemory) =>
    request<void>(
      `/api/user-context/memories/${encodeURIComponent(memory.id)}?expected_version=${memory.version}`,
      { method: 'DELETE' }
    ),
};
