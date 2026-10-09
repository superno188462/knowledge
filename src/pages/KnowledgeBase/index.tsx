import { ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Empty,
  Input,
  Message,
  Modal,
  Spin,
  Tag,
  Typography,
} from '@arco-design/web-react';
import KnowledgeHeader from '@/components/KnowledgeHeader';
import {
  ChunkingRule,
  KnowledgeBaseStatus,
  KnowledgeDirectory,
  KnowledgeDocumentDetail,
  KnowledgeDocumentInput,
  KnowledgeDocumentResult,
  KnowledgeEmbeddingJob,
  KnowledgeHit,
  knowledgeBaseApi,
} from '@/app/knowledgeBaseApi';
import styles from './index.module.less';

const MAX_DOCUMENT_CHARS = 200_000;

function parseJsonObject(value: string): Record<string, unknown> {
  if (!value.trim()) return {};
  const parsed: unknown = JSON.parse(value);
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('Metadata 必须是 JSON 对象。');
  }
  return parsed as Record<string, unknown>;
}

function parentDirectory(path: string): string {
  const index = path.lastIndexOf('/');
  return index < 0 ? '' : path.slice(0, index);
}

function directoryName(path: string): string {
  return path.split('/').filter(Boolean).pop() || '根目录';
}

function defaultOverlap(chunkSize: number): number {
  return Math.min(120, Math.floor(chunkSize / 5));
}

function syncFailureLabel(code?: string | null): string {
  if (code === 'SOURCE_REQUIRED') return '需重新上传原文';
  if (code?.includes('QUOTA')) return 'Embedding 额度不足';
  if (code?.includes('AUTH')) return 'Gateway Key 无效';
  if (code?.includes('TIMEOUT')) return '请求超时，写入结果未确认';
  return '嵌入失败，可重试';
}

function syncStatusLabel(status?: string): string {
  if (status === 'ready') return '已嵌入';
  if (status === 'failed') return '嵌入失败';
  if (status === 'pending') return '待手动嵌入';
  if (status === 'target_changed') return '当前网关待嵌入';
  if (status === 'queued') return '排队中';
  if (status === 'syncing') return '嵌入中';
  if (status === 'needs_upload') return '需重新上传原文';
  return status || '待嵌入';
}

function visibleSyncStatus(
  document: KnowledgeDocumentResult,
  gatewayStatus: KnowledgeBaseStatus | null
): string | undefined {
  if (
    document.sync_status === 'ready' &&
    gatewayStatus &&
    (!gatewayStatus.exists || document.embedded_target_fingerprint !== gatewayStatus.target_fingerprint)
  ) {
    return 'target_changed';
  }
  return document.sync_status;
}

function jobStageLabel(job: KnowledgeEmbeddingJob): string {
  if (job.status === 'succeeded') return '全部完成';
  if (job.status === 'failed') return syncFailureLabel(job.error_code);
  const labels: Record<string, string> = {
    queued: '排队中',
    checking: '校验是否需要嵌入',
    chunking: '切片中',
    embedding: '生成并保存新向量',
    deleting_old: '清理旧向量',
  };
  const progress = job.total_chunks ? ` ${job.completed_chunks}/${job.total_chunks}` : '';
  return `${labels[job.stage] || '处理中'}${progress}`;
}

function findInheritedRule(path: string, directories: KnowledgeDirectory[]): ChunkingRule | null {
  const rules = new Map(directories.map((item) => [item.path, item.chunking_config]));
  const parts = path.split('/').filter(Boolean);
  for (let count = parts.length; count > 0; count -= 1) {
    const rule = rules.get(parts.slice(0, count).join('/'));
    if (rule) return rule;
  }
  return rules.get('') || null;
}

function buildRule(
  strategy: string,
  size: string,
  overlap: string,
  delimiter: string,
  hierarchyEnabled: boolean,
  parentChunkSize: string,
  parentHeadingLevel: string
): ChunkingRule {
  const chunkSize = Number(size);
  const parsedOverlap = Number(overlap);
  if (!Number.isInteger(chunkSize) || chunkSize < 1) throw new Error('切片长度必须是正整数。');
  if (!Number.isInteger(parsedOverlap) || parsedOverlap < 0 || parsedOverlap >= chunkSize) {
    throw new Error('重叠长度必须是非负整数并小于切片长度。');
  }
  if (strategy === 'delimiter' && !delimiter) throw new Error('自定义分隔符不能为空。');
  const parentSize = Number(parentChunkSize);
  const headingLevel = Number(parentHeadingLevel);
  if (hierarchyEnabled && (!Number.isInteger(parentSize) || parentSize < 1 || parentSize > 20000)) {
    throw new Error('父块长度必须在 1 到 20000 字符之间。');
  }
  if (hierarchyEnabled && (!Number.isInteger(headingLevel) || headingLevel < 1 || headingLevel > 6)) {
    throw new Error('Markdown 父级标题必须在 1 到 6 级之间。');
  }
  return {
    strategy: strategy as ChunkingRule['strategy'],
    chunk_size: chunkSize,
    overlap: parsedOverlap,
    ...(strategy === 'delimiter' ? { delimiter } : {}),
    hierarchy_enabled: hierarchyEnabled,
    ...(hierarchyEnabled
      ? { parent_chunk_size: parentSize, parent_heading_level: headingLevel }
      : {}),
  };
}

export default function KnowledgeBasePage(): JSX.Element {
  const fileInput = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<KnowledgeBaseStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [statusError, setStatusError] = useState('');
  const [embeddingModel, setEmbeddingModel] = useState('');
  const [collectionSize, setCollectionSize] = useState('1000');
  const [documents, setDocuments] = useState<KnowledgeDocumentResult[]>([]);
  const [embeddingJobs, setEmbeddingJobs] = useState<KnowledgeEmbeddingJob[]>([]);
  const [directories, setDirectories] = useState<KnowledgeDirectory[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedDirectory, setSelectedDirectory] = useState('');
  const [selectedDocumentId, setSelectedDocumentId] = useState('');
  const [document, setDocument] = useState<KnowledgeDocumentDetail | null>(null);
  const [documentLoading, setDocumentLoading] = useState(false);
  const [newDocument, setNewDocument] = useState(false);
  const [editorPath, setEditorPath] = useState('');
  const [editorContent, setEditorContent] = useState('');
  const [editorMetadata, setEditorMetadata] = useState('{}');
  const [saving, setSaving] = useState(false);
  const [ruleSaving, setRuleSaving] = useState(false);
  const [ruleStrategy, setRuleStrategy] = useState('structure');
  const [ruleSize, setRuleSize] = useState('1000');
  const [ruleOverlap, setRuleOverlap] = useState('120');
  const [ruleDelimiter, setRuleDelimiter] = useState('\\n\\n');
  const [hierarchyEnabled, setHierarchyEnabled] = useState(false);
  const [parentChunkSize, setParentChunkSize] = useState('4000');
  const [parentHeadingLevel, setParentHeadingLevel] = useState('2');
  const [ruleOverride, setRuleOverride] = useState(false);
  const [directoryInput, setDirectoryInput] = useState('');
  const [creatingDirectory, setCreatingDirectory] = useState(false);
  const [deletingId, setDeletingId] = useState('');
  const [searchOpen, setSearchOpen] = useState(false);
  const [collectionConfigOpen, setCollectionConfigOpen] = useState(false);
  const directoriesRef = useRef(directories);
  const statusRef = useRef(status);
  directoriesRef.current = directories;
  statusRef.current = status;
  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState('5');
  const [searching, setSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<KnowledgeHit[] | null>(null);

  const refreshWorkspace = useCallback(async () => {
    setStatusLoading(true);
    setStatusError('');
    // Gateway status can be slow while it embeds. Keep the local file tree usable.
    void knowledgeBaseApi.getStatus().then(setStatus).catch((error) => {
      setStatus(null);
      setStatusError(error instanceof Error ? error.message : '读取知识库状态失败。');
    });
    try {
      const tree = await knowledgeBaseApi.getTree();
      setDocuments(tree.documents);
      setDirectories(tree.directories);
      setExpanded((current) => new Set([...current, ...tree.directories.map((item) => item.path)]));
      return tree;
    } catch (error) {
      setStatusError(error instanceof Error ? error.message : '读取知识库失败。');
      setStatus(null);
      return null;
    } finally {
      setStatusLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshWorkspace();
  }, [refreshWorkspace]);

  useEffect(() => {
    let cancelled = false;
    let hadActive = false;
    let lastTreeRefresh = 0;
    let timer: number | undefined;
    const poll = async () => {
      try {
        const response = await knowledgeBaseApi.listEmbeddingJobs();
        if (cancelled) return;
        setEmbeddingJobs(response.jobs);
        const active = response.jobs.some((job) => job.status === 'queued' || job.status === 'running');
        const now = Date.now();
        if ((active || hadActive) && (active !== hadActive || now - lastTreeRefresh >= 10_000)) {
          const tree = await knowledgeBaseApi.getTree();
          lastTreeRefresh = now;
          if (!cancelled) {
            setDocuments(tree.documents);
            if (selectedDocumentId) {
              const detail = await knowledgeBaseApi.getDocument(selectedDocumentId);
              if (!cancelled) setDocument(detail);
            }
          }
        }
        hadActive = active;
      } catch {
        // Keep the last known state; the next poll will retry.
      } finally {
        if (!cancelled) timer = window.setTimeout(() => void poll(), 5000);
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [selectedDocumentId]);

  useEffect(() => {
    let cancelled = false;
    if (!selectedDocumentId) {
      setDocument(null);
      return () => {
        cancelled = true;
      };
    }
    setDocumentLoading(true);
    knowledgeBaseApi
      .getDocument(selectedDocumentId)
      .then((value) => {
        if (cancelled) return;
        setDocument(value);
        setEditorPath(value.path || '');
        setEditorContent(value.content || '');
        setEditorMetadata(JSON.stringify(value.metadata || {}, null, 2));
        const directRule = value.chunking_config;
        const rule =
          directRule ||
          (value.metadata.chunking_rule as ChunkingRule | undefined) ||
          findInheritedRule(value.path || '', directoriesRef.current);
        setRuleOverride(Boolean(directRule));
        setRuleStrategy(rule?.strategy || 'structure');
        setRuleSize(String(rule?.chunk_size || statusRef.current?.chunk_size || 1000));
        setRuleOverlap(
          String(rule?.overlap ?? defaultOverlap(rule?.chunk_size || statusRef.current?.chunk_size || 1000))
        );
        setRuleDelimiter(rule?.delimiter || '\\n\\n');
        setHierarchyEnabled(Boolean(rule?.hierarchy_enabled));
        setParentChunkSize(String(rule?.parent_chunk_size || 4000));
        setParentHeadingLevel(String(rule?.parent_heading_level || 2));
      })
      .catch((error: unknown) => {
        if (!cancelled) Message.error(error instanceof Error ? error.message : '打开文件失败。');
      })
      .finally(() => {
        if (!cancelled) setDocumentLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedDocumentId]);

  useEffect(() => {
    if (selectedDocumentId || newDocument) return;
    const direct =
      directories.find((item) => item.path === selectedDirectory)?.chunking_config || null;
    const rule = direct || findInheritedRule(selectedDirectory, directories);
    setRuleOverride(Boolean(direct));
    setRuleStrategy(rule?.strategy || 'structure');
    setRuleSize(String(rule?.chunk_size || status?.chunk_size || 1000));
    setRuleOverlap(
      String(rule?.overlap ?? defaultOverlap(rule?.chunk_size || status?.chunk_size || 1000))
    );
    setRuleDelimiter(rule?.delimiter || '\\n\\n');
    setHierarchyEnabled(Boolean(rule?.hierarchy_enabled));
    setParentChunkSize(String(rule?.parent_chunk_size || 4000));
    setParentHeadingLevel(String(rule?.parent_heading_level || 2));
  }, [selectedDirectory, selectedDocumentId, directories, newDocument, status?.chunk_size]);

  const isReady = Boolean(status?.configured && status.exists);
  const currentSyncStatus = document ? visibleSyncStatus(document, status) : undefined;
  const selectedFolderRule = useMemo(
    () => directories.find((item) => item.path === selectedDirectory)?.chunking_config || null,
    [directories, selectedDirectory]
  );
  const activeDirectory = selectedDocumentId ? parentDirectory(editorPath) : selectedDirectory;

  const selectFolder = (path: string) => {
    setSelectedDocumentId('');
    setSelectedDirectory(path);
    setNewDocument(false);
    const direct = directories.find((item) => item.path === path)?.chunking_config || null;
    const rule = direct || findInheritedRule(path, directories);
    setRuleOverride(Boolean(direct));
    setRuleStrategy(rule?.strategy || 'structure');
    setRuleSize(String(rule?.chunk_size || status?.chunk_size || 1000));
    setRuleOverlap(
      String(rule?.overlap ?? defaultOverlap(rule?.chunk_size || status?.chunk_size || 1000))
    );
    setRuleDelimiter(rule?.delimiter || '\\n\\n');
    setHierarchyEnabled(Boolean(rule?.hierarchy_enabled));
    setParentChunkSize(String(rule?.parent_chunk_size || 4000));
    setParentHeadingLevel(String(rule?.parent_heading_level || 2));
  };

  const beginNewDocument = () => {
    const targetDirectory = selectedDocumentId ? parentDirectory(editorPath) : selectedDirectory;
    setSelectedDocumentId('');
    setSelectedDirectory(targetDirectory);
    setDocument(null);
    setNewDocument(true);
    setEditorPath([targetDirectory, '新建文档.md'].filter(Boolean).join('/'));
    setEditorContent('');
    setEditorMetadata('{}');
    setRuleOverride(false);
  };

  const createFolder = async () => {
    const leaf = directoryInput.trim().replace(/^\/+|\/+$/g, '');
    if (!leaf) return;
    const path = [activeDirectory, leaf].filter(Boolean).join('/');
    setSaving(true);
    try {
      await knowledgeBaseApi.createDirectory(path);
      setDirectoryInput('');
      setCreatingDirectory(false);
      setSelectedDirectory(path);
      setExpanded((current) => new Set(current).add(path));
      await refreshWorkspace();
      Message.success(`已创建目录 ${path}`);
    } catch (error) {
      Message.error(error instanceof Error ? error.message : '创建目录失败。');
    } finally {
      setSaving(false);
    }
  };

  const handleUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    if (!files.length) return;
    if (files.length > 64) return Message.error('一次最多上传 64 个文件。');
    const fileNames = files.map((file) => file.name.normalize('NFC'));
    if (new Set(fileNames).size !== fileNames.length) {
      return Message.error('所选文件中有同名项，请分开重命名后上传。');
    }
    setSaving(true);
    try {
      const metadata = {};
      const payloads: KnowledgeDocumentInput[] = await Promise.all(
        files.map(async (file) => {
          if (!/\.(txt|md)$/i.test(file.name))
            throw new Error(`${file.name} 不是 .txt 或 .md 文件。`);
          const content = await file.text();
          if (!content.trim()) throw new Error(`${file.name} 没有文本内容。`);
          if (content.length > MAX_DOCUMENT_CHARS)
            throw new Error(`${file.name} 超过 200,000 个字符。`);
          return {
            directory: activeDirectory,
            name: file.name,
            source_type: 'file' as const,
            file_name: file.name,
            title: file.name.replace(/\.(txt|md)$/i, ''),
            content,
            metadata,
            chunking_config: null,
          };
        })
      );
      const result = await knowledgeBaseApi.saveDrafts(payloads);
      await refreshWorkspace();
      const firstId = result.documents[0]?.document_id;
      if (firstId) {
        setSelectedDocumentId(firstId);
        setSelectedDirectory('');
      }
      Message.success(`已保存 ${payloads.length} 个文件草稿。选择文件后点击“嵌入”才会调用 Gateway。`);
    } catch (error) {
      await refreshWorkspace();
      Message.error(error instanceof Error ? error.message : '文件上传失败。');
    } finally {
      setSaving(false);
    }
  };

  const saveDocumentDraft = async (
    manageSaving = true,
    notifySaved = true
  ): Promise<string | null> => {
    const normalizedPath = editorPath
      .trim()
      .replace(/\\/g, '/')
      .replace(/^\/+|\/+$/g, '');
    if (!normalizedPath || !editorContent.trim()) {
      Message.warning('文件路径和正文不能为空。');
      return null;
    }
    if (editorContent.length > MAX_DOCUMENT_CHARS) {
      Message.error('正文不能超过 200,000 个字符。');
      return null;
    }
    let metadata: Record<string, unknown>;
    try {
      metadata = parseJsonObject(editorMetadata);
    } catch (error) {
      Message.error(error instanceof Error ? error.message : 'Metadata JSON 无效。');
      return null;
    }
    if (manageSaving) setSaving(true);
    try {
      const parts = normalizedPath.split('/');
      const name = parts.pop() as string;
      const currentRule = ruleOverride
        ? buildRule(
            ruleStrategy,
            ruleSize,
            ruleOverlap,
            ruleDelimiter,
            hierarchyEnabled,
            parentChunkSize,
            parentHeadingLevel
          )
        : null;
      const payload: KnowledgeDocumentInput = {
        document_id: selectedDocumentId || undefined,
        directory: parts.join('/'),
        name,
        source_type: document?.metadata.source_type === 'file' ? 'file' : 'text',
        file_name: document?.metadata.source_type === 'file' ? name : undefined,
        title: name.replace(/\.(txt|md)$/i, ''),
        content: editorContent,
        metadata,
        chunking_config: currentRule,
      };
      const response = await knowledgeBaseApi.saveDrafts([payload]);
      const id = response.documents[0]?.document_id;
      await refreshWorkspace();
      setNewDocument(false);
      if (id) {
        setSelectedDocumentId(id);
        const saved = await knowledgeBaseApi.getDocument(id);
        setDocument(saved);
        setEditorPath(saved.path || '');
        setEditorContent(saved.content || '');
        setEditorMetadata(JSON.stringify(saved.metadata || {}, null, 2));
      }
      if (notifySaved) Message.success('草稿已保存，尚未调用 Embedding。');
      return id || null;
    } catch (error) {
      const tree = await refreshWorkspace();
      if (tree) {
        if (selectedDocumentId) {
          try {
            const refreshed = await knowledgeBaseApi.getDocument(selectedDocumentId);
            setDocument(refreshed);
            setEditorContent(refreshed.content || '');
          } catch {
            /* The error notification below is the recovery path. */
          }
        } else {
          const failed = tree.documents.find((item) => item.path === normalizedPath);
          if (failed) {
            setNewDocument(false);
            setSelectedDirectory('');
            setSelectedDocumentId(failed.document_id);
          }
        }
      }
      Message.error(error instanceof Error ? error.message : '保存草稿失败。');
      return null;
    } finally {
      if (manageSaving) setSaving(false);
    }
  };

  const saveDocument = () => void saveDocumentDraft();

  const embedDocument = async () => {
    if (!isReady) return Message.warning('请先创建知识库并配置 Gateway API Key。');
    setSaving(true);
    let targetDocumentId = selectedDocumentId;
    try {
      const id = await saveDocumentDraft(false, false);
      if (!id) return;
      targetDocumentId = id;
      await knowledgeBaseApi.reindexDocument(id);
      await refreshWorkspace();
      setSelectedDocumentId(id);
      const refreshed = await knowledgeBaseApi.getDocument(id);
      setDocument(refreshed);
      Message.success('已加入嵌入队列，后台会继续处理。');
    } catch (error) {
      await refreshWorkspace();
      if (targetDocumentId) {
        try {
          const refreshed = await knowledgeBaseApi.getDocument(targetDocumentId);
          setDocument(refreshed);
        } catch {
          /* Keep the existing editor state if the detail refresh also fails. */
        }
      }
      Message.error(error instanceof Error ? error.message : '嵌入失败，旧向量记录仍会保留。');
    } finally {
      setSaving(false);
    }
  };

  const saveRule = async () => {
    let rule: ChunkingRule | null;
    try {
      rule = ruleOverride
        ? buildRule(
            ruleStrategy,
            ruleSize,
            ruleOverlap,
            ruleDelimiter,
            hierarchyEnabled,
            parentChunkSize,
            parentHeadingLevel
          )
        : null;
      const maxSize = status?.chunk_size || 1000;
      if (rule && rule.chunk_size > maxSize)
        throw new Error(`切片长度不能超过 Gateway 上限 ${maxSize}。`);
    } catch (error) {
      Message.error(error instanceof Error ? error.message : '切片规则无效。');
      return;
    }
    setRuleSaving(true);
    try {
      if (selectedDocumentId) {
        await knowledgeBaseApi.setDocumentRule(selectedDocumentId, rule);
        Message.success('文件规则已保存，已加入嵌入队列。');
      } else {
        const response = await knowledgeBaseApi.setDirectoryRule(selectedDirectory, rule);
        const failed = response.documents.filter((item) => item.status === 'failed' || item.status === 'needs_upload').length;
        const quotaFailures = response.documents.filter((item) =>
          item.error_code?.includes('QUOTA')
        ).length;
        const notice = `目录规则已保存，${response.affected - failed} 个文件已排队${
          failed ? `，其中 ${failed} 个失败或需重新上传` : ''
        }${quotaFailures ? `；${quotaFailures} 个因 Embedding 额度不足失败` : ''}。`;
        if (failed) Message.warning(notice);
        else Message.success(notice);
      }
      await refreshWorkspace();
      if (selectedDocumentId) {
        const refreshed = await knowledgeBaseApi.getDocument(selectedDocumentId);
        setDocument(refreshed);
        setEditorContent(refreshed.content || '');
      }
    } catch (error) {
      await refreshWorkspace();
      if (selectedDocumentId) {
        try {
          const refreshed = await knowledgeBaseApi.getDocument(selectedDocumentId);
          setDocument(refreshed);
          setEditorContent(refreshed.content || '');
        } catch {
          /* Keep the selected item and show the original request error. */
        }
      }
      Message.error(error instanceof Error ? error.message : '保存规则或重新嵌入失败。');
    } finally {
      setRuleSaving(false);
    }
  };

  const deleteDocument = (item: KnowledgeDocumentResult) => {
    Modal.confirm({
      title: '删除文件',
      content: `删除 ${item.path || item.name} 以及对应的 Gateway 向量记录？`,
      okText: '删除',
      cancelText: '取消',
      onOk: async () => {
        setDeletingId(item.document_id);
        try {
          await knowledgeBaseApi.deleteDocument(item.document_id);
          if (selectedDocumentId === item.document_id) {
            setSelectedDocumentId('');
            setDocument(null);
            setNewDocument(false);
          }
          await refreshWorkspace();
          Message.success('文件已删除。');
        } catch (error) {
          Message.error(error instanceof Error ? error.message : '删除文件失败。');
          throw error;
        } finally {
          setDeletingId('');
        }
      },
    });
  };

  const deleteDirectory = (path: string) => {
    Modal.confirm({
      title: '删除空目录',
      content: `确定删除目录“${path}”？目录必须为空。`,
      okText: '删除',
      cancelText: '取消',
      onOk: async () => {
        try {
          await knowledgeBaseApi.deleteDirectory(path);
          setSelectedDirectory('');
          await refreshWorkspace();
        } catch (error) {
          Message.error(error instanceof Error ? error.message : '目录删除失败。');
          throw error;
        }
      },
    });
  };

  const renderDirectory = (path: string, depth: number): JSX.Element => {
    const childDirectories = directories.filter(
      (item) => parentDirectory(item.path) === path && item.path !== path
    );
    const childDocuments = documents.filter((item) => (item.directory || '') === path);
    const isExpanded = expanded.has(path);
    return (
      <div key={path || '__root__'}>
        <div
          className={`${styles.treeRow} ${
            !selectedDocumentId && selectedDirectory === path ? styles.selected : ''
          }`}
          style={{ paddingLeft: 10 + depth * 16 }}
        >
          <button
            type="button"
            className={styles.treeToggle}
            aria-label={isExpanded ? '收起目录' : '展开目录'}
            onClick={() =>
              setExpanded((current) => {
                const next = new Set(current);
                if (next.has(path)) next.delete(path);
                else next.add(path);
                return next;
              })
            }
          >
            {isExpanded ? '⌄' : '›'}
          </button>
          <button type="button" className={styles.treeLabel} onClick={() => selectFolder(path)}>
            <span>{path ? '📁' : '⌂'}</span>
            <span>{directoryName(path)}</span>
          </button>
          {path && (
            <button
              type="button"
              className={styles.rowAction}
              title="删除空目录"
              onClick={() => deleteDirectory(path)}
            >
              ⋯
            </button>
          )}
        </div>
        {isExpanded && (
          <div>
            {childDirectories.map((item) => renderDirectory(item.path, depth + 1))}
            {childDocuments.map((item) => (
              <div
                className={`${styles.treeRow} ${
                  selectedDocumentId === item.document_id ? styles.selected : ''
                }`}
                style={{ paddingLeft: 28 + depth * 16 }}
                key={item.document_id}
              >
                <button
                  type="button"
                  className={styles.treeLabel}
                  onClick={() => {
                    setSelectedDirectory('');
                    setNewDocument(false);
                    setSelectedDocumentId(item.document_id);
                  }}
                >
                  <span>▤</span>
                  <span>{item.name || item.title || '文档'}</span>
                </button>
                <Tag
                  size="small"
                  color={
                    visibleSyncStatus(item, status) === 'ready'
                      ? 'green'
                      : visibleSyncStatus(item, status) === 'failed'
                      ? 'red'
                      : 'orange'
                  }
                >
                  {visibleSyncStatus(item, status) === 'ready'
                    ? '已嵌入'
                    : visibleSyncStatus(item, status) === 'needs_upload'
                    ? syncFailureLabel(item.sync_error_code)
                    : visibleSyncStatus(item, status) === 'failed'
                    ? syncFailureLabel(item.sync_error_code)
                    : syncStatusLabel(visibleSyncStatus(item, status))}
                </Tag>
                <button
                  type="button"
                  className={styles.rowAction}
                  title="删除文件"
                  disabled={deletingId === item.document_id}
                  onClick={() => deleteDocument(item)}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };

  const handleSearch = async () => {
    if (!query.trim()) return Message.warning('请输入检索内容。');
    setSearching(true);
    try {
      const response = await knowledgeBaseApi.search(
        query.trim(),
        Math.max(1, Math.min(20, Number(topK) || 5)),
        {
          app_id: 'knowledge-platform',
          record_type: 'knowledge_chunk',
        }
      );
      setSearchResults(response.results);
    } catch (error) {
      Message.error(error instanceof Error ? error.message : '检索失败。');
    } finally {
      setSearching(false);
    }
    return undefined;
  };

  const displayedRule = selectedDocumentId
    ? document?.chunking_config ||
      (document?.metadata.chunking_rule as ChunkingRule | undefined) ||
      findInheritedRule(document?.path || '', directories)
    : selectedFolderRule || findInheritedRule(selectedDirectory, directories);

  return (
    <div className={styles.page}>
      <KnowledgeHeader />
      <main className={styles.workspace}>
        <header className={styles.topbar}>
          <div className={styles.brand}>
            <Typography.Title heading={5}>知识库工作区</Typography.Title>
            <Tag color={status?.exists ? 'green' : 'orange'}>
              {status?.exists ? '已连接' : '未就绪'}
            </Tag>
            {status?.exists && (
              <Typography.Text type="secondary">
                {status.collection} · {status.model} · 单片上限 {status.chunk_size}
              </Typography.Text>
            )}
          </div>
          <div className={styles.toolbar}>
            <Button disabled={!status?.exists} onClick={() => setCollectionConfigOpen(true)}>
              集合配置
            </Button>
            <Button onClick={() => void refreshWorkspace()} loading={statusLoading}>
              刷新
            </Button>
            <Button
              disabled={saving}
              onClick={() => setCreatingDirectory((value) => !value)}
            >
              新建目录
            </Button>
            <Button disabled={saving} onClick={beginNewDocument}>
              新建文本
            </Button>
            <Button disabled={saving} onClick={() => fileInput.current?.click()}>
              上传文件
            </Button>
            <Button disabled={!isReady} onClick={() => setSearchOpen((value) => !value)}>
              检索
            </Button>
            <input
              ref={fileInput}
              className={styles.hiddenInput}
              type="file"
              accept=".txt,.md,text/plain,text/markdown"
              multiple
              onChange={(event) => void handleUpload(event)}
            />
          </div>
        </header>

        <Modal
          title="集合配置"
          visible={collectionConfigOpen}
          footer={null}
          onCancel={() => setCollectionConfigOpen(false)}
        >
          <dl style={{ overflowWrap: 'anywhere' }}>
            <dt>集合名称</dt><dd>{status?.collection || '—'}</dd>
            <dt>集合 ID</dt><dd>{status?.id || '—'}</dd>
            <dt>Embedding 模型（Gateway 返回的完整名称）</dt><dd>{status?.model || '—'}</dd>
            <dt>向量维度</dt><dd>{status?.vector_dimensions ?? '首次成功写入后确定'}</dd>
            <dt>单条向量文本上限</dt><dd>{status?.chunk_size ?? '—'} 字符</dd>
            <dt>集合重叠参数</dt><dd>{status?.collection_chunk_overlap ?? '—'} 字符（仅用于 Gateway 文档兼容接口，本项目不使用）</dd>
            <dt>描述</dt><dd>{status?.description || '未填写'}</dd>
            <dt>创建时间</dt><dd>{status?.created_at ? new Date(status.created_at).toLocaleString('zh-CN') : '—'}</dd>
          </dl>
          <Alert
            type="info"
            title="集合配置由 Gateway 固定"
            content="当前 Gateway 没有集合修改接口。更换模型或提高字符上限需要创建新集合并重新嵌入。切片方式、重叠长度和父子层级可在根目录、目录或文件的检查器中修改，切片长度不能超过集合上限。"
          />
        </Modal>

        {statusError && <Alert type="error" title="读取失败" content={statusError} />}
        {!status?.configured && !statusLoading && (
          <Alert
            type="warning"
            title="Gateway 尚未配置"
            content="请在设置中验证 Gateway 项目 API Key。密钥由后端保存和使用。"
          />
        )}
        {status?.configured && !status.exists && (
          <section className={styles.setup}>
            <strong>创建知识库集合</strong>
            <Input
              value={embeddingModel}
              onChange={setEmbeddingModel}
              placeholder="Gateway Embedding 模型名"
            />
            <Input
              value={collectionSize}
              onChange={setCollectionSize}
              inputMode="numeric"
              placeholder="单片字符上限"
            />
            <Button
              type="primary"
              loading={saving}
              onClick={async () => {
                try {
                  await knowledgeBaseApi.initialize(
                    '共享知识库数据中台',
                    embeddingModel,
                    Number(collectionSize)
                  );
                  await refreshWorkspace();
                  Message.success('集合已创建。');
                } catch (error) {
                  Message.error(error instanceof Error ? error.message : '创建集合失败。');
                }
              }}
            >
              创建集合
            </Button>
          </section>
        )}

        <section className={styles.workbench}>
          <aside className={styles.explorer}>
            <div className={styles.panelTitle}>
              <span>资源管理器</span>
              <span>{documents.length} 个文件</span>
            </div>
            {creatingDirectory && (
              <div className={styles.newFolder}>
                <Input
                  autoFocus
                  value={directoryInput}
                  onChange={setDirectoryInput}
                  placeholder="目录名称"
                  onPressEnter={() => void createFolder()}
                />
                <Button
                  type="primary"
                  size="small"
                  loading={saving}
                  onClick={() => void createFolder()}
                >
                  创建
                </Button>
              </div>
            )}
            {statusLoading ? (
              <div className={styles.center}>
                <Spin />
              </div>
            ) : (
              <div className={styles.tree}>{renderDirectory('', 0)}</div>
            )}
            {embeddingJobs.length > 0 && (
              <div className={styles.queuePanel} aria-live="polite">
                <strong>嵌入队列</strong>
                {embeddingJobs.slice(0, 8).map((job) => (
                  <div className={styles.queueRow} key={job.job_id}>
                    <span title={documents.find((item) => item.document_id === job.document_id)?.path || job.document_id}>
                      {documents.find((item) => item.document_id === job.document_id)?.name || job.document_id.slice(0, 8)}
                    </span>
                    <Tag size="small" color={job.status === 'succeeded' ? 'green' : job.status === 'failed' ? 'red' : 'orange'}>
                      {jobStageLabel(job)}
                    </Tag>
                  </div>
                ))}
              </div>
            )}
            <div className={styles.explorerFooter}>
              <Typography.Text type="secondary">
                规则按 collection → 目录 → 文件继承
              </Typography.Text>
            </div>
          </aside>

          <section className={styles.editorPanel}>
            {newDocument ? (
              <>
                <div className={styles.editorHeader}>
                  <Input
                    value={editorPath}
                    onChange={setEditorPath}
                    placeholder="相对路径，例如 产品/安装指南.md"
                  />
                  <Tag color="arcoblue">新文件</Tag>
                </div>
                <Input.TextArea
                  className={styles.editor}
                  value={editorContent}
                  onChange={setEditorContent}
                  placeholder="输入或粘贴 Markdown / 文本内容"
                />
                <details className={styles.metadataDetails}>
                  <summary>Metadata JSON</summary>
                  <Input.TextArea
                    value={editorMetadata}
                    onChange={setEditorMetadata}
                    autoSize={{ minRows: 3, maxRows: 8 }}
                  />
                </details>
                <div className={styles.editorFooter}>
                  <Typography.Text type="secondary">
                    原文安全保存在 Knowledge；每片 metadata 的 source 为相对路径。
                  </Typography.Text>
                  <Button
                    loading={saving}
                    onClick={() => void saveDocument()}
                  >
                    保存草稿
                  </Button>
                  <Button
                    type="primary"
                    loading={saving}
                    disabled={!isReady}
                    onClick={() => void embedDocument()}
                  >
                    保存并嵌入
                  </Button>
                </div>
              </>
            ) : selectedDocumentId ? (
              documentLoading ? (
                <div className={styles.center}>
                  <Spin tip="打开文档…" />
                </div>
              ) : document ? (
                <>
                  <div className={styles.editorHeader}>
                    <Input value={editorPath} onChange={setEditorPath} aria-label="文件相对路径" />
                    <Tag
                      color={
                        currentSyncStatus === 'ready'
                          ? 'green'
                          : currentSyncStatus === 'failed'
                          ? 'red'
                          : 'orange'
                      }
                    >
                      {currentSyncStatus === 'ready'
                        ? `${document.chunk_count || 0} 片已嵌入`
                        : syncStatusLabel(currentSyncStatus)}
                    </Tag>
                    {(currentSyncStatus === 'failed' || currentSyncStatus === 'pending' || currentSyncStatus === 'target_changed') && (
                      <Button
                        disabled={!isReady || saving}
                        onClick={async () => {
                          setSaving(true);
                          try {
                            await knowledgeBaseApi.reindexDocument(document.document_id);
                            await refreshWorkspace();
                            const refreshed = await knowledgeBaseApi.getDocument(document.document_id);
                            setDocument(refreshed);
                            Message.success('已加入嵌入队列。');
                          } catch (error) {
                            try {
                              const refreshed = await knowledgeBaseApi.getDocument(document.document_id);
                              setDocument(refreshed);
                            } catch {
                              /* Keep the editor state if detail refresh fails. */
                            }
                            Message.error(
                              error instanceof Error ? error.message : '重新嵌入失败。'
                            );
                          } finally {
                            setSaving(false);
                          }
                        }}
                      >
                        {currentSyncStatus === 'failed' ? '重试已保存草稿' : '嵌入已保存草稿'}
                      </Button>
                    )}
                  </div>
                  {!document.content && (
                    <Alert
                      type="warning"
                      title="需要重新上传原文"
                      content="这是旧目录项，Knowledge 未保存原文，无法使用新切片规则；请上传源文件或粘贴正文以更新。"
                    />
                  )}
                  {currentSyncStatus === 'failed' && (
                    <Alert
                      type="error"
                      title={syncFailureLabel(document.sync_error_code)}
                      content={
                        document.previous_content
                          ? '当前编辑区是待处理的新版本；此前成功嵌入的版本仍保留，可在下方查看。修复额度或网络后重试。'
                          : '该文档尚无已嵌入版本；修复额度或网络后可以重试。'
                      }
                    />
                  )}
                  {currentSyncStatus === 'pending' && (
                    <Alert
                      type="info"
                      title="草稿尚未嵌入"
                      content={
                        document.previous_content
                          ? '当前修改已保存在 Knowledge。检索仍使用上一个成功嵌入版本；点击“嵌入”后才会提交新版本。'
                          : '原文已保存在 Knowledge，但尚未调用 Gateway Embedding。点击“嵌入”后才会创建向量记录。'
                      }
                    />
                  )}
                  {currentSyncStatus === 'target_changed' && (
                    <Alert
                      type="warning"
                      title="当前 Gateway 集合尚未确认嵌入此文档"
                      content="此前的嵌入目标与当前集合不同，或旧记录缺少目标标识。点击“嵌入已保存草稿”后，后台会为当前集合重新生成切片向量。"
                    />
                  )}
                  {(currentSyncStatus === 'queued' || currentSyncStatus === 'syncing') && (
                    <Alert type="info" title={syncStatusLabel(currentSyncStatus)} content="后台正在处理；可继续查看其他文件，完成后此处会自动更新。" />
                  )}
                  <Input.TextArea
                    className={styles.editor}
                    value={editorContent}
                    onChange={setEditorContent}
                    placeholder="此旧文件没有本地原文，请重新上传或粘贴内容"
                    disabled={!document.content && !editorContent}
                  />
                  {document.previous_content && (
                    <details className={styles.metadataDetails}>
                      <summary>查看上一个已嵌入版本</summary>
                      <Input.TextArea
                        value={document.previous_content}
                        readOnly
                        autoSize={{ minRows: 5, maxRows: 14 }}
                      />
                    </details>
                  )}
                  <details className={styles.metadataDetails}>
                    <summary>Metadata JSON</summary>
                    <Input.TextArea
                      value={editorMetadata}
                      onChange={setEditorMetadata}
                      autoSize={{ minRows: 3, maxRows: 8 }}
                    />
                  </details>
                  <div className={styles.editorFooter}>
                    <Typography.Text type="secondary">
                      保存草稿不会调用 Embedding；点击“保存并嵌入”会加入后台队列，全部确认后替换旧版本。
                    </Typography.Text>
                    <Button
                      loading={saving}
                      disabled={currentSyncStatus === 'queued' || currentSyncStatus === 'syncing'}
                      onClick={() => void saveDocument()}
                    >
                      保存草稿
                    </Button>
                    <Button
                      type="primary"
                      loading={saving}
                      disabled={!isReady || currentSyncStatus === 'queued' || currentSyncStatus === 'syncing'}
                      onClick={() => void embedDocument()}
                    >
                      保存并嵌入
                    </Button>
                  </div>
                </>
              ) : (
                <Empty description="文件不存在或无权访问" />
              )
            ) : (
              <div className={styles.welcome}>
                <div className={styles.welcomeIcon}>▤</div>
                <Typography.Title heading={4}>
                  {selectedDirectory ? directoryName(selectedDirectory) : '选择或创建一个文件'}
                </Typography.Title>
                <Typography.Text type="secondary">
                  在左侧管理目录和文件，在右侧设置该层切片规则。
                </Typography.Text>
                {selectedDirectory && (
                  <Tag color={selectedFolderRule ? 'arcoblue' : 'gray'}>
                    {selectedFolderRule ? '目录规则已覆盖' : '继承上级规则'}
                  </Tag>
                )}
              </div>
            )}
          </section>

          <aside className={styles.inspector}>
            <div className={styles.panelTitle}>
              <span>检查器</span>
              <span>{selectedDocumentId ? '文件' : selectedDirectory ? '目录' : '未选择'}</span>
            </div>
            <div className={styles.rulePanel}>
              <Typography.Text bold>切片规则</Typography.Text>
              <Typography.Paragraph type="secondary" className={styles.ruleHelp}>
                {selectedDocumentId
                  ? '设置此文件专属规则，或继承最近的目录规则。'
                  : '目录规则应用于后代文件；更深目录或文件规则优先。'}
              </Typography.Paragraph>
              <div className={styles.inheritance}>
                <span>当前有效</span>
                {displayedRule
                  ? `${
                      displayedRule.strategy === 'structure'
                        ? '结构优先'
                        : displayedRule.strategy === 'fixed'
                        ? '固定长度'
                        : '自定义分隔符'
                    } · ${
                      displayedRule.chunk_size
                    } 字符${displayedRule.hierarchy_enabled ? ' · 父子层级' : ''}`
                  : '结构优先 · collection 默认值'}
              </div>
              <label className={styles.overrideToggle}>
                <input
                  type="checkbox"
                  checked={ruleOverride}
                  onChange={(event) => setRuleOverride(event.target.checked)}
                />
                在此级别覆盖规则
              </label>
              {ruleOverride && (
                <>
                  <label className={styles.fieldLabel}>切片方式</label>
                  <select
                    className={styles.nativeSelect}
                    value={ruleStrategy}
                    onChange={(event) => setRuleStrategy(event.target.value)}
                    aria-label="切片方式"
                  >
                    <option value="structure">结构优先</option>
                    <option value="fixed">固定长度</option>
                    <option value="delimiter">自定义标识符分割</option>
                  </select>
                  {ruleStrategy === 'structure' && (
                    <Typography.Text type="secondary" className={styles.ruleNote}>
                      Markdown 按标题、段落和代码块切分；纯文本按段落、句子切分，超长内容再按长度兜底。
                    </Typography.Text>
                  )}
                  <label className={styles.fieldLabel}>
                    单片最大字符数（≤ {status?.chunk_size || 1000}）
                  </label>
                  <Input value={ruleSize} onChange={setRuleSize} inputMode="numeric" />
                  <label className={styles.fieldLabel}>重叠字符数</label>
                  <Input value={ruleOverlap} onChange={setRuleOverlap} inputMode="numeric" />
                  <label className={styles.overrideToggle}>
                    <input
                      type="checkbox"
                      checked={hierarchyEnabled}
                      onChange={(event) => setHierarchyEnabled(event.target.checked)}
                    />
                    启用父子层级上下文
                  </label>
                  {hierarchyEnabled && (
                    <>
                      <label className={styles.fieldLabel}>父块最大长度（本地保存，不向量化）</label>
                      <Input value={parentChunkSize} onChange={setParentChunkSize} inputMode="numeric" />
                      <label className={styles.fieldLabel}>Markdown 父级标题（纯文本按段落分组）</label>
                      <select
                        className={styles.nativeSelect}
                        value={parentHeadingLevel}
                        onChange={(event) => setParentHeadingLevel(event.target.value)}
                        aria-label="Markdown 父级标题层级"
                      >
                        {[1, 2, 3, 4, 5, 6].map((level) => (
                          <option key={level} value={String(level)}>
                            {`H${level}（${'#'.repeat(level)}）`}
                          </option>
                        ))}
                      </select>
                    </>
                  )}
                  {ruleStrategy === 'delimiter' && (
                    <>
                      <label className={styles.fieldLabel}>
                        自定义分隔符（按字面匹配，\n 表示换行）
                      </label>
                      <Input
                        value={ruleDelimiter}
                        onChange={setRuleDelimiter}
                        placeholder="例如 \n## "
                      />
                    </>
                  )}
                </>
              )}
              <Button
                type="primary"
                long
                className={styles.ruleSave}
                loading={ruleSaving}
                onClick={() => void saveRule()}
              >
                保存规则并重新嵌入
              </Button>
              <Typography.Text type="secondary" className={styles.ruleNote}>
                文本超出 Gateway
                上限时会继续按长度拆分。目录规则修改只重嵌入有效规则发生变化的文件。
              </Typography.Text>
            </div>
            {document && (
              <div className={styles.metaSummary}>
                <div>
                  <span>相对路径</span>
                  <code>{document.path || '—'}</code>
                </div>
                <div>
                  <span>文档 ID</span>
                  <code>{document.document_id}</code>
                </div>
                <div>
                  <span>活动切片</span>
                  <strong>{document.chunk_count ?? 0}</strong>
                </div>
              </div>
            )}
          </aside>
        </section>

        {searchOpen && (
          <section className={styles.searchPanel}>
            <div className={styles.searchHeader}>
              <Typography.Text bold>检索测试</Typography.Text>
              <Button type="text" onClick={() => setSearchOpen(false)}>
                收起
              </Button>
            </div>
            <div className={styles.searchControls}>
              <Input.TextArea
                value={query}
                onChange={setQuery}
                placeholder="输入问题，检索当前用户知识库"
                autoSize={{ minRows: 1, maxRows: 3 }}
              />
              <Input value={topK} onChange={setTopK} style={{ width: 90 }} />
              <Button
                type="primary"
                loading={searching}
                disabled={!isReady}
                onClick={() => void handleSearch()}
              >
                检索
              </Button>
            </div>
            {searchResults?.map((hit) => (
              <article className={styles.searchHit} key={hit.chunk_id}>
                <div>
                  <strong>{hit.title || '知识片段'}</strong>
                  <Tag color="arcoblue">{hit.score?.toFixed(3) || '—'}</Tag>
                </div>
                <p>{hit.content}</p>
                {hit.parent_id && hit.context_content && (
                  <details className={styles.parentContext}>
                    <summary>查看父块上下文</summary>
                    <p>{hit.context_content}</p>
                  </details>
                )}
                <Typography.Text type="secondary">
                  来源：{String(hit.metadata.source || hit.metadata.path || '未知')}
                </Typography.Text>
              </article>
            ))}
            {searchResults?.length === 0 && <Empty description="没有匹配片段" />}
          </section>
        )}
      </main>
    </div>
  );
}
