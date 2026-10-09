import { useState } from 'react';
import { Alert, Button, Card, Input, Message, Modal, Spin, Tag, Typography } from '@arco-design/web-react';
import { useNavigate } from 'react-router-dom';
import KnowledgeHeader from '@/components/KnowledgeHeader';
import { GatewayKeyStatus, gatewayApi } from '@/app/gatewayApi';
import styles from './index.module.less';

interface SettingsPageProps {
  status: GatewayKeyStatus | null;
  loading: boolean;
  error: string;
  onRefresh: () => Promise<void>;
  onStatusChange: (status: GatewayKeyStatus) => void;
}

export default function SettingsPage({
  status,
  loading,
  error: statusError,
  onRefresh,
  onStatusChange,
}: SettingsPageProps): JSX.Element {
  const [saving, setSaving] = useState(false);
  const [apiKey, setApiKey] = useState('');
  const [operationError, setOperationError] = useState('');
  const [collection, setCollection] = useState('');
  const [newCollection, setNewCollection] = useState('');
  const [model, setModel] = useState('');
  const [chunkSize, setChunkSize] = useState('1000');
  const navigate = useNavigate();

  const openCollection = async (create = false) => {
    const name = create ? newCollection.trim() : collection || status?.selected_collection || '';
    if (!name || (create && !model.trim())) {
      Message.warning('请选择集合，或填写新集合名称和模型。');
      return;
    }
    setSaving(true);
    setOperationError('');
    try {
      const next = create
        ? await gatewayApi.createCollection(name, model.trim(), Number(chunkSize))
        : await gatewayApi.selectCollection(name);
      onStatusChange(next);
      setCollection('');
      navigate('/knowledge-base');
    } catch (requestError) {
      setOperationError(requestError instanceof Error ? requestError.message : '集合操作失败。');
    } finally {
      setSaving(false);
    }
  };

  const save = async () => {
    if (!apiKey.trim()) {
      Message.warning('请输入 Gateway 项目 API Key。');
      return;
    }
    setSaving(true);
    setOperationError('');
    try {
      const next = await gatewayApi.saveKey(apiKey);
      onStatusChange(next);
      setApiKey('');
      setCollection('');
      Message.success('Gateway Key 已验证并加密保存。');
    } catch (requestError) {
      setOperationError(requestError instanceof Error ? requestError.message : 'Gateway Key 验证失败。');
    } finally {
      setSaving(false);
    }
  };

  const remove = () => {
    Modal.confirm({
      title: '移除 Gateway API Key',
      content: '移除后将无法进入知识库页面；已登录的用户仍可管理自己的用户上下文。',
      okText: '移除',
      cancelText: '取消',
      onOk: async () => {
        setSaving(true);
        try {
          await gatewayApi.deleteKey();
          onStatusChange({ configured: false, valid: false });
          Message.success('Gateway Key 已移除。');
        } catch (requestError) {
          Message.error(requestError instanceof Error ? requestError.message : '移除失败。');
          throw requestError;
        } finally {
          setSaving(false);
        }
      },
    });
  };

  return (
    <div className={styles.page}>
      <KnowledgeHeader gatewayAvailable={status?.valid === true && status?.knowledge_ready === true} />
      <main className={styles.content}>
        <div className={styles.heading}>
          <div>
            <Typography.Title heading={3}>设置</Typography.Title>
            <Typography.Text type="secondary">
              配置你有权限使用的 Gateway 项目 Key，以启用知识库管理。
            </Typography.Text>
          </div>
          <Button onClick={() => void onRefresh()} loading={loading}>刷新状态</Button>
        </div>

        <Card className={styles.card} title="Gateway 项目 API Key" bordered={false}>
          {loading ? (
            <div className={styles.loading}><Spin /> 正在验证已保存的 Key…</div>
          ) : (
            <>
              {(operationError || statusError) && <Alert className={styles.alert} type="error" title="操作失败" content={operationError || statusError} />}
              {status?.configured && (
                <Alert
                  className={styles.alert}
                  type={status.valid ? 'success' : 'warning'}
                  title={status.valid ? 'Key 可用' : 'Key 需要处理'}
                  content={
                    status.valid
                      ? `当前 Key 以 ••••${status.last_four} 结尾；向量服务可访问。额度按 Gateway 项目共享。`
                      : status.message || 'Key 已保存，但 Gateway 验证未通过。请重新输入可用的项目 Key。'
                  }
                />
              )}
              {status?.valid && (
                <div className={styles.capabilities}>
                  <Tag color="green">向量数据库可用</Tag>
                  <Tag color={status.collection_exists ? 'green' : 'orange'}>
                    {status.knowledge_ready ? `当前集合：${status.selected_collection}` : '请先选择目标集合'}
                  </Tag>
                </div>
              )}
              <Typography.Paragraph type="secondary">
                Key 只提交到 Knowledge 后端，由服务端加密保存并调用 Gateway；页面不会回显密钥。Key 属于 Gateway 项目级凭据，额度与同项目的其他 Key 共享。
              </Typography.Paragraph>
              <Input.Password
                value={apiKey}
                onChange={setApiKey}
                placeholder={status?.configured ? '输入新 Key 可替换当前 Key' : '粘贴 Gateway 项目 API Key'}
                autoComplete="new-password"
                disabled={saving}
                onPressEnter={() => void save()}
              />
              <div className={styles.actions}>
                <Button type="primary" loading={saving} onClick={() => void save()}>
                  {status?.configured ? '验证并替换' : '验证并保存'}
                </Button>
                {status?.configured && <Button status="danger" disabled={saving} onClick={remove}>移除 Key</Button>}
              </div>
            </>
          )}
        </Card>
        {status?.valid && (
          <Card className={styles.card} title="选择知识库集合" bordered={false}>
            <Typography.Paragraph type="secondary">
              列表来自当前 Key 可访问的 Gateway 集合。选择后进入工作区；排队或嵌入期间不能切换。
            </Typography.Paragraph>
            <label htmlFor="gateway-collection">目标集合</label>
            <select
              id="gateway-collection"
              className={styles.collectionSelect}
              value={collection || status.selected_collection || ''}
              disabled={saving || loading}
              onChange={(event) => setCollection(event.target.value)}
            >
              <option value="" disabled>请选择集合</option>
              {(status.collections || []).map((item) => (
                <option key={item.collection} value={item.collection}>
                  {item.collection}{item.model ? ` · ${item.model}` : ''}
                </option>
              ))}
            </select>
            {status.collections?.length === 0 && <Alert type="info" content="当前项目还没有集合，可以在下方创建。" />}
            <div className={styles.actions}>
              <Button type="primary" loading={saving} onClick={() => void openCollection()}>选择并打开</Button>
            </div>
            <Typography.Title heading={6}>创建新集合</Typography.Title>
            <Input placeholder="集合名称" value={newCollection} onChange={setNewCollection} disabled={saving} />
            <Input placeholder="完整 Embedding 模型名，例如 volc/doubao-embedding-vision" value={model} onChange={setModel} disabled={saving} />
            <Input placeholder="单条文本长度上限" value={chunkSize} onChange={setChunkSize} disabled={saving} />
            <div className={styles.actions}>
              <Button loading={saving} onClick={() => void openCollection(true)}>创建并打开</Button>
            </div>
          </Card>
        )}
      </main>
    </div>
  );
}
