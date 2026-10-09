import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  Message,
  Modal,
  Select,
  Spin,
  Tabs,
  Tag,
  Typography,
} from '@arco-design/web-react';
import KnowledgeHeader from '@/components/KnowledgeHeader';
import { useAuth } from '@/auth/AuthContext';
import { UserMemory, UserSessionMessage, gatewayApi } from '@/app/gatewayApi';
import styles from './index.module.less';

const PAGE_SIZE = 50;

function parseObject(value: string, label: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error(`${label} 必须是 JSON 对象。`);
  }
  return parsed as Record<string, unknown>;
}

function parseTags(value: string): string[] {
  return value.split(',').map((item) => item.trim()).filter(Boolean);
}

export default function UserContextPage(): JSX.Element {
  const { user } = useAuth();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [profileDraft, setProfileDraft] = useState('{}');
  const [profileVersion, setProfileVersion] = useState<number | null>(null);
  const [savingProfile, setSavingProfile] = useState(false);
  const [memories, setMemories] = useState<UserMemory[]>([]);
  const [memoryPage, setMemoryPage] = useState(1);
  const [memoryTotal, setMemoryTotal] = useState(0);
  const [newContent, setNewContent] = useState('');
  const [newTags, setNewTags] = useState('');
  const [newMetadata, setNewMetadata] = useState('{}');
  const [savingMemory, setSavingMemory] = useState(false);
  const [editingMemory, setEditingMemory] = useState<UserMemory | null>(null);
  const [editingContent, setEditingContent] = useState('');
  const [sessionIdInput, setSessionIdInput] = useState('');
  const [loadedSessionId, setLoadedSessionId] = useState('');
  const [sessionMessages, setSessionMessages] = useState<UserSessionMessage[]>([]);
  const [loadingSession, setLoadingSession] = useState(false);
  const [recentSessionIds, setRecentSessionIds] = useState<string[]>([]);
  const [activeSection, setActiveSection] = useState('sessions');

  useEffect(() => {
    if (!user?.id) {
      setRecentSessionIds([]);
      return;
    }
    try {
      const stored = JSON.parse(localStorage.getItem(`knowledge-context-sessions:${user.id}`) || '[]');
      setRecentSessionIds(Array.isArray(stored) ? stored.filter((item): item is string => typeof item === 'string') : []);
    } catch {
      setRecentSessionIds([]);
    }
  }, [user?.id]);

  const refresh = useCallback(async (page = 1) => {
    setLoading(true);
    setError('');
    try {
      const [profile, memoryResult] = await Promise.all([
        gatewayApi.getProfile(),
        gatewayApi.listMemories(page, PAGE_SIZE),
      ]);
      setProfileDraft(JSON.stringify(profile.profile, null, 2));
      setProfileVersion(profile.version);
      setMemories(memoryResult.items);
      setMemoryPage(memoryResult.page);
      setMemoryTotal(memoryResult.total);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '读取用户上下文失败。');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh(1);
  }, [refresh]);

  const saveProfile = async () => {
    setSavingProfile(true);
    setError('');
    try {
      const result = await gatewayApi.saveProfile(parseObject(profileDraft, '用户画像'), profileVersion);
      setProfileDraft(JSON.stringify(result.profile, null, 2));
      setProfileVersion(result.version);
      Message.success('用户画像已保存。');
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : '保存用户画像失败。';
      setError(message);
      Message.error(message);
    } finally {
      setSavingProfile(false);
    }
  };

  const createMemory = async () => {
    if (!newContent.trim()) {
      Message.warning('请输入记忆内容。');
      return;
    }
    setSavingMemory(true);
    setError('');
    try {
      await gatewayApi.createMemory(newContent.trim(), parseTags(newTags), parseObject(newMetadata, 'Metadata'));
      setNewContent('');
      setNewTags('');
      setNewMetadata('{}');
      setMemoryPage(1);
      await refresh(1);
      Message.success('长期记忆已创建。');
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : '创建长期记忆失败。';
      setError(message);
      Message.error(message);
    } finally {
      setSavingMemory(false);
    }
  };

  const saveMemory = async () => {
    if (!editingMemory || !editingContent.trim()) return;
    setSavingMemory(true);
    try {
      await gatewayApi.updateMemory(editingMemory, editingContent.trim());
      setEditingMemory(null);
      await refresh(memoryPage);
      Message.success('长期记忆已更新。');
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : '更新长期记忆失败。';
      setError(message);
      Message.error(message);
    } finally {
      setSavingMemory(false);
    }
  };

  const deleteMemory = (memory: UserMemory) => {
    Modal.confirm({
      title: '删除长期记忆',
      content: '删除后无法恢复，是否继续？',
      okText: '删除',
      cancelText: '取消',
      onOk: async () => {
        try {
          await gatewayApi.deleteMemory(memory);
          await refresh(memoryPage);
          Message.success('长期记忆已删除。');
        } catch (requestError) {
          const message = requestError instanceof Error ? requestError.message : '删除长期记忆失败。';
          setError(message);
          Message.error(message);
          throw requestError;
        }
      },
    });
  };

  const loadSession = async () => {
    const sessionId = sessionIdInput.trim();
    if (!sessionId) {
      Message.warning('请输入其他业务系统提供的会话 ID。');
      return;
    }
    setLoadingSession(true);
    setError('');
    try {
      const result = await gatewayApi.getSessionMessages(sessionId);
      setLoadedSessionId(result.session_id);
      setSessionMessages(result.messages);
      if (user?.id) {
        setRecentSessionIds((current) => {
          const next = [sessionId, ...current.filter((item) => item !== sessionId)].slice(0, 30);
          localStorage.setItem(`knowledge-context-sessions:${user.id}`, JSON.stringify(next));
          return next;
        });
      }
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '读取短期会话失败。');
    } finally {
      setLoadingSession(false);
    }
  };

  const clearSession = () => {
    if (!loadedSessionId) return;
    Modal.confirm({
      title: '清除短期会话',
      content: `将清除会话 ${loadedSessionId} 的全部短期消息，不会删除长期记忆或用户画像。`,
      okText: '清除消息',
      cancelText: '取消',
      onOk: async () => {
        try {
          await gatewayApi.deleteSessionMessages(loadedSessionId);
          setSessionMessages([]);
          Message.success('短期消息已清除。');
        } catch (requestError) {
          const message = requestError instanceof Error ? requestError.message : '清除短期会话失败。';
          setError(message);
          Message.error(message);
          throw requestError;
        }
      },
    });
  };

  return (
    <div className={styles.page}>
      <KnowledgeHeader gatewayAvailable={false} />
      <main className={styles.content}>
        <div className={styles.heading}>
          <div>
            <Typography.Title heading={3}>用户上下文</Typography.Title>
            <Typography.Text type="secondary">
              仅查看和管理当前身份中心账号在当前 Gateway 项目中的用户画像与长期记忆。
            </Typography.Text>
          </div>
          <Button onClick={() => void refresh()} loading={loading}>刷新</Button>
        </div>

        {error && <Alert className={styles.error} type="error" title="请求未完成" content={error} />}
        {loading ? (
          <div className={styles.loading}><Spin /> 正在读取 Gateway 上下文…</div>
        ) : (
            <Tabs activeTab={activeSection} onChange={setActiveSection}>
            <Tabs.TabPane key="profile" title="用户画像">
            <Card className={styles.card} title="用户画像" bordered={false}>
              <Typography.Paragraph type="secondary">
                画像 Schema 由使用该上下文的业务项目定义。保存时使用版本号检测并发更新。
                {profileVersion !== null ? ` 当前版本：${profileVersion}` : ' 当前尚无已保存版本。'}
              </Typography.Paragraph>
              <Input.TextArea
                value={profileDraft}
                onChange={setProfileDraft}
                autoSize={{ minRows: 8, maxRows: 18 }}
                spellCheck={false}
                aria-label="用户画像 JSON"
              />
              <div className={styles.actions}>
                <Button type="primary" loading={savingProfile} onClick={() => void saveProfile()}>
                  保存画像
                </Button>
              </div>
            </Card>
            </Tabs.TabPane>

            <Tabs.TabPane key="sessions" title="短期会话">
            <Card className={styles.card} title="短期会话" bordered={false}>
              <Typography.Paragraph type="secondary">
                Gateway 暂无历史会话列表接口。可从业务系统粘贴会话 ID；成功打开的 ID 会保存在本浏览器的最近列表。后端只读取或清除当前登录用户的对应会话。
              </Typography.Paragraph>
              <div className={styles.sessionSearch}>
                <Input
                  value={sessionIdInput}
                  onChange={setSessionIdInput}
                  placeholder="输入 session_id"
                  maxLength={200}
                  onPressEnter={() => void loadSession()}
                />
                <Select
                  value={recentSessionIds.includes(sessionIdInput) ? sessionIdInput : undefined}
                  placeholder={recentSessionIds.length ? '选择最近打开的会话' : '暂无最近会话'}
                  options={recentSessionIds.map((sessionId) => ({ label: sessionId, value: sessionId }))}
                  onChange={(value) => setSessionIdInput(String(value || ''))}
                  showSearch
                  allowClear
                />
                <Button type="primary" loading={loadingSession} onClick={() => void loadSession()}>
                  读取会话
                </Button>
              </div>
              {loadedSessionId && (
                <div className={styles.sessionHeading}>
                  <Typography.Text>会话：{loadedSessionId}（{sessionMessages.length} 条消息）</Typography.Text>
                  <Button status="danger" onClick={clearSession}>清除短期消息</Button>
                </div>
              )}
              {!!sessionMessages.length && (
                <div className={styles.sessionMessages}>
                  {sessionMessages.map((message) => (
                    <article key={message.id} className={styles.sessionMessage}>
                      <div><Tag>{message.role}</Tag><Typography.Text type="secondary">序号 {message.sequence}</Typography.Text></div>
                      <p>{message.content}</p>
                    </article>
                  ))}
                </div>
              )}
            </Card>
            </Tabs.TabPane>

            <Tabs.TabPane key="memories" title="长期记忆">
            <Card className={styles.card} title="长期记忆" bordered={false}>
              <Typography.Paragraph type="secondary">
                共 {memoryTotal} 条。内容和标签直接保存到 Gateway；更新和删除使用记录版本进行冲突检查。
              </Typography.Paragraph>
              <div className={styles.createForm}>
                <Input.TextArea
                  value={newContent}
                  onChange={setNewContent}
                  placeholder="输入一条明确、稳定且未来有用的记忆"
                  autoSize={{ minRows: 3, maxRows: 7 }}
                  maxLength={20000}
                  showWordLimit
                />
                <Input value={newTags} onChange={setNewTags} placeholder="标签，以逗号分隔（可选）" />
                <Input.TextArea
                  value={newMetadata}
                  onChange={setNewMetadata}
                  placeholder='Metadata JSON，例如：{"memory_type":"semantic","confidence":0.9}'
                  autoSize={{ minRows: 2, maxRows: 5 }}
                  spellCheck={false}
                />
                <div className={styles.actions}>
                  <Button type="primary" loading={savingMemory} onClick={() => void createMemory()}>
                    添加记忆
                  </Button>
                </div>
              </div>
              <div className={styles.memoryList}>
                {!memories.length ? (
                  <Empty description="当前没有长期记忆" />
                ) : memories.map((memory) => (
                  <article className={styles.memory} key={memory.id}>
                    <div className={styles.memoryHeading}>
                      <Typography.Text type="secondary">版本 {memory.version}</Typography.Text>
                      <div className={styles.memoryActions}>
                        <Button
                          type="text"
                          onClick={() => {
                            setEditingMemory(memory);
                            setEditingContent(memory.content);
                          }}
                        >编辑</Button>
                        <Button type="text" status="danger" onClick={() => deleteMemory(memory)}>删除</Button>
                      </div>
                    </div>
                    <div className={styles.memoryContent}>{memory.content}</div>
                    {!!memory.tags.length && (
                      <div className={styles.tags}>{memory.tags.map((tag) => <Tag key={tag}>{tag}</Tag>)}</div>
                    )}
                    <details>
                      <summary>查看 Metadata</summary>
                      <pre>{JSON.stringify(memory.metadata, null, 2)}</pre>
                    </details>
                    <Typography.Text className={styles.memoryId} type="secondary">ID：{memory.id}</Typography.Text>
                  </article>
                ))}
              </div>
              {memoryTotal > PAGE_SIZE && (
                <div className={styles.paging}>
                  <Button disabled={memoryPage <= 1 || loading} onClick={() => void refresh(memoryPage - 1)}>上一页</Button>
                  <span>{memoryPage} / {Math.ceil(memoryTotal / PAGE_SIZE)}</span>
                  <Button
                    disabled={memoryPage >= Math.ceil(memoryTotal / PAGE_SIZE) || loading}
                    onClick={() => void refresh(memoryPage + 1)}
                  >下一页</Button>
                </div>
              )}
            </Card>
            </Tabs.TabPane>
            </Tabs>
        )}
      </main>
      <Modal
        title="编辑长期记忆"
        visible={Boolean(editingMemory)}
        confirmLoading={savingMemory}
        onOk={() => void saveMemory()}
        onCancel={() => setEditingMemory(null)}
        okText="保存"
        cancelText="取消"
      >
        <Input.TextArea
          value={editingContent}
          onChange={setEditingContent}
          autoSize={{ minRows: 4, maxRows: 10 }}
          maxLength={20000}
          showWordLimit
        />
        <Typography.Paragraph type="secondary">
          标签和 Metadata 保持不变；如果记录已在 Gateway 被修改，版本冲突时请刷新后再编辑。
        </Typography.Paragraph>
      </Modal>
    </div>
  );
}
