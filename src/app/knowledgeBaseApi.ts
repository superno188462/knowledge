/** Types and authenticated API calls for the Gateway backed knowledge base. */
import { KNOWLEDGE_API_HOST } from '@/config';

export interface KnowledgeBaseStatus {
  id?: string | null;
  description?: string | null;
  created_at?: string | null;
  collection_chunk_overlap?: number | null;
  configured: boolean;
  exists: boolean;
  collection?: string;
  target_fingerprint?: string | null;
  model?: string;
  vector_dimensions?: number | null;
  chunk_size?: number | null;
  chunk_overlap?: number | null;
}

export interface KnowledgeDocumentInput {
  document_id?: string;
  directory?: string;
  name?: string;
  source_type?: 'file' | 'text';
  file_name?: string;
  title: string;
  content: string;
  metadata: Record<string, unknown>;
  chunking_config?: ChunkingRule | null;
}

export interface ChunkingRule {
  strategy: 'structure' | 'fixed' | 'delimiter';
  chunk_size: number;
  overlap: number;
  delimiter?: string;
  hierarchy_enabled?: boolean;
  parent_chunk_size?: number;
  parent_heading_level?: number;
}

export interface KnowledgeDirectory {
  path: string;
  chunking_config: ChunkingRule | null;
}

export interface KnowledgeDocumentDetail extends KnowledgeDocumentResult {
  content: string | null;
  previous_content?: string | null;
  metadata: Record<string, unknown>;
  chunking_config: ChunkingRule | null;
  has_content?: boolean;
}

export interface KnowledgeDocumentResult {
  document_id: string;
  path?: string | null;
  directory?: string;
  name?: string;
  source_type?: 'file' | 'text';
  title?: string;
  file_name?: string | null;
  sync_status?: string;
  embedded_target_fingerprint?: string | null;
  sync_error_code?: string | null;
  chunk_count?: number;
  result?: {
    external_id?: string;
    chunks?: number;
    vectors?: number;
    action?: 'created' | 'updated' | 'unchanged' | 'draft_saved';
  };
}

/** 持久化嵌入任务；succeeded 表示写入且发布确认，cleanup_pending 表示旧数据仍待清理。 */
export interface KnowledgeEmbeddingJob {
  job_id: string;
  document_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  stage: 'queued' | 'checking' | 'chunking' | 'saving_parents' | 'embedding' | 'publishing' | 'cleanup_pending' | 'deleting_old' | 'succeeded' | 'failed';
  completed_chunks: number;
  total_chunks: number | null;
  error_code: string | null;
  attempts: number;
  created_at: string | null;
  started_at: string | null;
  heartbeat_at: string | null;
  finished_at: string | null;
}

export interface KnowledgeActivityLog {
  id: string;
  event_type: string;
  status: 'info' | 'success' | 'error';
  document_id: string | null;
  job_id: string | null;
  path: string | null;
  stage: string | null;
  chunk_index: number | null;
  completed_chunks: number | null;
  total_chunks: number | null;
  error_code: string | null;
  gateway_status: number | null;
  gateway_trace_id: string | null;
  upstream_code: string | null;
  created_at: string;
}

export interface KnowledgeActivityPage {
  logs: KnowledgeActivityLog[];
  total: number;
  page: number;
  page_size: number;
}

export interface KnowledgeHit {
  chunk_id: string;
  document_id: string;
  title: string;
  content: string;
  context_content?: string;
  context_status?: 'available' | 'missing';
  parent_id?: string | null;
  score: number | null;
  metadata: Record<string, unknown>;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${KNOWLEDGE_API_HOST}${path}`, {
      ...init,
      credentials: 'include',
      headers: {
        'content-type': 'application/json',
        ...init?.headers,
      },
    });
  } catch {
    throw new Error('无法连接项目后端，请确认 Python 服务已启动。');
  }

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload?.detail;
    const message =
      typeof detail?.message === 'string' ? detail.message : '知识库操作失败，请稍后重试。';
    throw new Error(message);
  }
  return payload as T;
}

export const knowledgeBaseApi = {
  getStatus: () => request<KnowledgeBaseStatus>('/api/knowledge-base'),
  initialize: (description: string, embeddingModel: string, chunkSize: number) =>
    request<Record<string, unknown>>('/api/knowledge-base/initialize', {
      method: 'POST',
      body: JSON.stringify({ description, embedding_model: embeddingModel, chunk_size: chunkSize }),
    }),
  listDocuments: () =>
    request<{ documents: KnowledgeDocumentResult[] }>('/api/knowledge-base/documents'),
  listEmbeddingJobs: () =>
    request<{ jobs: KnowledgeEmbeddingJob[] }>('/api/knowledge-base/embedding-jobs'),
  listActivityLogs: (page: number, eventType?: string, pathQuery?: string) => {
    const query = new URLSearchParams({ page: String(page), page_size: '25' });
    if (eventType) query.set('event_type', eventType);
    if (pathQuery) query.set('path_query', pathQuery);
    return request<KnowledgeActivityPage>(`/api/knowledge-base/activity-logs?${query}`);
  },
  getTree: () =>
    request<{ directories: KnowledgeDirectory[]; documents: KnowledgeDocumentResult[] }>(
      '/api/knowledge-base/tree'
    ),
  getDocument: (documentId: string) =>
    request<KnowledgeDocumentDetail>(
      `/api/knowledge-base/documents/${encodeURIComponent(documentId)}`
    ),
  createDirectory: (path: string) =>
    request<{ path: string }>('/api/knowledge-base/directories', {
      method: 'POST',
      body: JSON.stringify({ path }),
    }),
  deleteDirectory: (path: string) =>
    request<{ deleted: boolean }>(
      `/api/knowledge-base/directories?${new URLSearchParams({ path })}`,
      {
        method: 'DELETE',
      }
    ),
  setDirectoryRule: (path: string, rule: ChunkingRule | null) =>
    request<{
      affected: number;
      documents: Array<{ document_id: string; status: string; error_code?: string; job?: KnowledgeEmbeddingJob }>;
    }>('/api/knowledge-base/directories/rule', {
      method: 'PUT',
      body: JSON.stringify({ path, rule }),
    }),
  setDocumentRule: (documentId: string, rule: ChunkingRule | null) =>
    request<KnowledgeEmbeddingJob>(
      `/api/knowledge-base/documents/${encodeURIComponent(documentId)}/chunking-rule`,
      {
        method: 'PUT',
        body: JSON.stringify({ rule }),
      }
    ),
  reindexDocument: (documentId: string) =>
    request<KnowledgeEmbeddingJob>(
      `/api/knowledge-base/documents/${encodeURIComponent(documentId)}/reindex`,
      {
        method: 'POST',
      }
    ),
  moveDocument: (documentId: string, targetPath: string) =>
    request<KnowledgeEmbeddingJob>(
      `/api/knowledge-base/documents/${encodeURIComponent(documentId)}/move`,
      {
        method: 'PUT',
        body: JSON.stringify({ target_path: targetPath }),
      }
    ),
  upsertDocuments: (documents: KnowledgeDocumentInput[]) =>
    request<{ documents: KnowledgeDocumentResult[] }>('/api/knowledge-base/documents/batch', {
      method: 'POST',
      body: JSON.stringify({ documents }),
    }),
  saveDrafts: (documents: KnowledgeDocumentInput[]) =>
    request<{ documents: KnowledgeDocumentResult[] }>('/api/knowledge-base/documents/drafts', {
      method: 'POST',
      body: JSON.stringify({ documents }),
    }),
  deleteDocument: (documentId: string) =>
    request<Record<string, unknown>>(
      `/api/knowledge-base/documents?${new URLSearchParams({ document_id: documentId })}`,
      { method: 'DELETE' }
    ),
  search: (query: string, topK: number, metadata?: Record<string, unknown>) =>
    request<{ results: KnowledgeHit[] }>('/api/knowledge-base/search', {
      method: 'POST',
      body: JSON.stringify({ query, top_k: topK, metadata }),
    }),
};
