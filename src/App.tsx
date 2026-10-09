/**
 * Copyright 2025 Beijing Volcano Engine Technology Co., Ltd. All Rights Reserved.
 * SPDX-license-identifier: BSD-3-Clause
 */

import { useCallback, useEffect, useState } from 'react';
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import { AuthProvider, useAuth } from './auth/AuthContext';
import RequireAuth from './auth/RequireAuth';
import AuthLoading from './auth/AuthLoading';
import LoginPage from './pages/Login';
import KnowledgeBasePage from './pages/KnowledgeBase';
import KnowledgeLogsPage from './pages/KnowledgeLogs';
import SettingsPage from './pages/Settings';
import UserContextPage from './pages/UserContext/index';
import { GatewayKeyStatus, gatewayApi } from './app/gatewayApi';
import '@arco-design/web-react/dist/css/arco.css';

function AppRoutes() {
  const { user, loading } = useAuth();
  const [gatewayStatus, setGatewayStatus] = useState<GatewayKeyStatus | null>(null);
  const [gatewayLoading, setGatewayLoading] = useState(true);
  const [gatewayError, setGatewayError] = useState('');
  const userId = user?.id;

  const refreshGatewayStatus = useCallback(async () => {
    if (!userId) {
      setGatewayStatus(null);
      setGatewayError('');
      setGatewayLoading(false);
      return;
    }
    setGatewayLoading(true);
    setGatewayError('');
    try {
      setGatewayStatus(await gatewayApi.getKeyStatus());
    } catch (error) {
      setGatewayStatus(null);
      setGatewayError(error instanceof Error ? error.message : '无法验证 Gateway Key。');
    } finally {
      setGatewayLoading(false);
    }
  }, [userId]);

  const updateGatewayStatus = useCallback((status: GatewayKeyStatus) => {
    setGatewayStatus(status);
    setGatewayError('');
  }, []);

  useEffect(() => {
    void refreshGatewayStatus();
  }, [refreshGatewayStatus]);

  console.warn('运行问题可参考 README 内容进行排查');
  if (loading) return <AuthLoading />;
  return (
    <Routes>
      <Route path="/login" element={user ? <Navigate to="/" replace /> : <LoginPage />} />
      <Route path="/" element={<Navigate to={user && gatewayStatus?.valid ? '/knowledge-base' : user ? '/user-context' : '/login'} replace />} />
      <Route
        path="/settings"
        element={
          <RequireAuth>
            <SettingsPage
              status={gatewayStatus}
              loading={gatewayLoading}
              error={gatewayError}
              onRefresh={refreshGatewayStatus}
              onStatusChange={updateGatewayStatus}
            />
          </RequireAuth>
        }
      />
      <Route
        path="/knowledge-base"
        element={
          <RequireAuth>
            {gatewayLoading ? <AuthLoading /> : gatewayStatus?.valid ? <KnowledgeBasePage /> : <Navigate to="/settings" replace />}
          </RequireAuth>
        }
      />
      <Route
        path="/knowledge-base/logs"
        element={
          <RequireAuth>
            <KnowledgeLogsPage />
          </RequireAuth>
        }
      />
      <Route
        path="/user-context"
        element={
          <RequireAuth>
            <UserContextPage />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to={user ? '/' : '/login'} replace />} />
    </Routes>
  );
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
