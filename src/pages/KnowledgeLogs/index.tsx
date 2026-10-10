import { useCallback, useEffect, useState } from 'react';
import { Button, Input, Select, Switch } from '@arco-design/web-react';
import { KnowledgeActivityLog, KnowledgeActivityPage, KnowledgeEmbeddingJob, knowledgeBaseApi } from '@/app/knowledgeBaseApi';
import KnowledgeHeader from '@/components/KnowledgeHeader';
import styles from './index.module.less';

const labels: Record<string, string> = {
  directory_created: '创建目录', directory_deleted: '删除目录',
  directory_rule_changed: '修改目录切片规则', document_draft_saved: '保存文档草稿',
  document_rule_changed: '修改文件切片规则', document_moved: '移动或重命名文档',
  document_deleted: '删除文档', embedding_queued: '加入嵌入队列',
  embedding_recovered: '中断后重新排队', embedding_started: '开始处理',
  embedding_checking: '检查版本', embedding_chunking: '切片',
  embedding_embedding: '开始向量写入', embedding_chunk_completed: '切片写入完成',
  embedding_deleting_old: '清理旧向量', embedding_succeeded: '嵌入成功',
  embedding_failed: '嵌入失败',
  embedding_saving_parents: '保存父块正文', embedding_publishing: '发布新版本',
  embedding_cleanup_pending: '已发布，旧数据待清理',
};

function progress(log: KnowledgeActivityLog): string {
  if (log.total_chunks === null) return '—';
  const count = `${log.completed_chunks ?? 0} / ${log.total_chunks}`;
  return log.chunk_index === null ? count : `#${log.chunk_index} · ${count}`;
}

export default function KnowledgeLogsPage(): JSX.Element {
  const [page, setPage] = useState(1);
  const [eventType, setEventType] = useState('');
  const [pathInput, setPathInput] = useState('');
  const [pathQuery, setPathQuery] = useState('');
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [data, setData] = useState<KnowledgeActivityPage | null>(null);
  const [jobs, setJobs] = useState<KnowledgeEmbeddingJob[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [result, currentJobs] = await Promise.all([
        knowledgeBaseApi.listActivityLogs(page, eventType, pathQuery),
        knowledgeBaseApi.listEmbeddingJobs(),
      ]);
      setData(result);
      setJobs(currentJobs.jobs);
      setError('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '读取日志失败。');
    } finally {
      setLoading(false);
    }
  }, [page, eventType, pathQuery]);

  useEffect(() => {
    void refresh();
    if (!autoRefresh) return undefined;
    const timer = window.setInterval(() => { void refresh(); }, 5000);
    return () => window.clearInterval(timer);
  }, [refresh, autoRefresh]);

  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / 25));
  const recentJobs = jobs.slice(0, 5);
  return (
    <>
      <KnowledgeHeader />
      <main className={styles.page}>
        <div className={styles.heading}>
          <div><h1>操作日志</h1><p>查看文档变更与每个切片的嵌入进度。日志按当前登录用户隔离。</p></div>
          <div className={styles.actions}><span>自动刷新</span><Switch checked={autoRefresh} onChange={setAutoRefresh} /><Button loading={loading} onClick={() => void refresh()}>刷新</Button></div>
        </div>
        <section className={styles.jobs} aria-label="最近嵌入任务">
          <strong>最近嵌入任务</strong>
          {recentJobs.length === 0 ? <span>暂无嵌入任务</span> : recentJobs.map((job) => {
            const waitingLong = job.status === 'queued' && !!job.created_at &&
              Date.now() - new Date(job.created_at).getTime() > 2 * 60 * 1000;
            const heartbeatLate = job.status === 'running' && !!job.heartbeat_at &&
              Date.now() - new Date(job.heartbeat_at).getTime() > 30 * 1000;
            return (
              <div key={job.job_id} className={styles.job}>
                <span title={job.document_id}>{job.document_id}</span>
                <span>{job.status === 'queued' ? '排队中' : `${job.status === 'failed' ? '失败' : job.status === 'succeeded' ? '成功' : job.stage} · ${job.completed_chunks}/${job.total_chunks ?? '?'}`}</span>
                {job.error_code && <span className={styles.failed}>{job.error_code}</span>}
                {waitingLong && <span className={styles.failed}>等待超过 2 分钟，请检查嵌入 worker</span>}
                {heartbeatLate && <span className={styles.failed}>worker 心跳中断，任务将在租约过期后重新领取</span>}
              </div>
            );
          })}
        </section>
        <div className={styles.filters}>
          <Select value={eventType} onChange={(value) => { setEventType(String(value)); setPage(1); }} style={{ width: 220 }}>
            <Select.Option value="">全部事件</Select.Option>
            {Object.entries(labels).map(([value, label]) => <Select.Option key={value} value={value}>{label}</Select.Option>)}
          </Select>
          <Input value={pathInput} onChange={setPathInput} placeholder="按文件路径或名称筛选" style={{ width: 300 }} onPressEnter={() => { setPathQuery(pathInput.trim()); setPage(1); }} />
          <Button onClick={() => { setPathQuery(pathInput.trim()); setPage(1); }}>筛选</Button>
          {pathQuery && <Button onClick={() => { setPathInput(''); setPathQuery(''); setPage(1); }}>清除路径筛选</Button>}
        </div>
        {error && <div className={styles.error}>{error}</div>}
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead><tr><th>时间</th><th>事件</th><th>文件 / 目录</th><th>切片进度</th><th>状态</th><th>任务 / 错误</th></tr></thead>
            <tbody>
              {(data?.logs ?? []).map((log) => (
                <tr key={log.id}>
                  <td>{new Date(log.created_at).toLocaleString('zh-CN')}</td>
                  <td>{labels[log.event_type] ?? log.event_type}</td>
                  <td title={log.path ?? log.document_id ?? ''}>{log.path ?? log.document_id ?? '—'}</td>
                  <td>{progress(log)}</td>
                  <td className={log.status === 'error' ? styles.failed : log.status === 'success' ? styles.success : ''}>{log.status === 'error' ? '失败' : log.status === 'success' ? '成功' : log.event_type === 'embedding_queued' || log.event_type === 'embedding_recovered' ? '排队中' : '进行中'}</td>
                  <td title={log.gateway_trace_id ?? log.job_id ?? ''}>
                    {log.error_code ? `${log.error_code}${log.gateway_status ? ` · HTTP ${log.gateway_status}` : ''}` : log.job_id ? log.job_id.slice(0, 8) : '—'}
                    {log.gateway_trace_id && <small className={styles.trace}>Trace: {log.gateway_trace_id}</small>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!loading && data?.logs.length === 0 && <div className={styles.empty}>暂无符合条件的日志</div>}
        </div>
        <div className={styles.footer}>
          <span>共 {data?.total ?? 0} 条 · 第 {page} / {totalPages} 页</span>
          <div><Button disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</Button><Button disabled={page >= totalPages} onClick={() => setPage(page + 1)}>下一页</Button></div>
        </div>
      </main>
    </>
  );
}
