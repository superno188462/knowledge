import { useState } from 'react';
import { Alert, Button, Card, Input, Message, Modal, Spin, Tag, Typography } from '@arco-design/web-react';
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
      <KnowledgeHeader gatewayAvailable={status?.valid === true} />
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
                    {status.collection_exists ? '知识库集合已存在' : '集合尚未创建，可在知识库页面初始化'}
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
      </main>
    </div>
  );
}
