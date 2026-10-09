import { Button, Message } from '@arco-design/web-react';
import { NavLink, useNavigate } from 'react-router-dom';
import Logo from '@/assets/img/Logo.svg';
import { useAuth } from '@/auth/AuthContext';
import styles from './KnowledgeHeader.module.less';

export default function KnowledgeHeader({ gatewayAvailable = true }: { gatewayAvailable?: boolean }): JSX.Element {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    try {
      await logout();
      navigate('/login', { replace: true });
    } catch {
      Message.error('退出失败，请稍后重试。');
    }
  };

  return (
    <header className={styles.header}>
      <div className={styles.brand}>
        <img src={Logo} alt="" />
        <span>知识库数据中台</span>
        <nav className={styles.navigation} aria-label="主导航">
          {gatewayAvailable && (
            <NavLink to="/knowledge-base" className={({ isActive }) => isActive ? styles.active : undefined}>知识库</NavLink>
          )}
          <NavLink to="/knowledge-base/logs" className={({ isActive }) => isActive ? styles.active : undefined}>操作日志</NavLink>
          <NavLink to="/user-context" className={({ isActive }) => isActive ? styles.active : undefined}>用户上下文</NavLink>
          <NavLink to="/settings" className={({ isActive }) => isActive ? styles.active : undefined}>设置</NavLink>
        </nav>
      </div>
      {user && (
        <div className={styles.account}>
          <span>{user.username}</span>
          <Button type="text" onClick={handleLogout}>退出登录</Button>
        </div>
      )}
    </header>
  );
}
