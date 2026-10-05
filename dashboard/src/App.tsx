import { lazy, Suspense, useEffect, useState } from 'react';
import { Toaster } from 'sonner';
import { ConfirmProvider } from './components/Dialog';
import { EmptyState } from './components/Layout';
import { LinkButton } from './components/Button';
import { AppDetailPage } from './features/apps/AppDetailPage';
import { AppsPage } from './features/apps/AppsPage';
import { ChatPage } from './features/chat/ChatPage';
import { HomePage } from './features/home/HomePage';
import { usePath } from './lib/router';
import { useTheme } from './lib/theme';
import { CommandPalette } from './shell/CommandPalette';
import { MobileBar, Sidebar } from './shell/Sidebar';
import { LoadingScreen, OfflineScreen, ServerErrorScreen, SignedOutScreen } from './shell/StatusScreens';
import { DashboardProvider, useDashboard, useDashboardLoader } from './state/dashboard';

// Heavier pages load on first visit so Home opens quickly.
const CalendarPage = lazy(() => import('./features/calendar/CalendarPage').then((m) => ({ default: m.CalendarPage })));
const SettingsPage = lazy(() => import('./features/settings/SettingsPage').then((m) => ({ default: m.SettingsPage })));

export function Routes() {
  const path = usePath();
  const { data } = useDashboard();
  if (path === '/') return <HomePage />;
  if (path === '/apps' || path === '/apps/discover') return <AppsPage discover={path === '/apps/discover'} />;
  const app = path.match(/^\/apps\/([^/]+)(?:\/([^/]+))?$/);
  if (app && data.services.services.some((service) => service.id === app[1]))
    return <AppDetailPage id={app[1]} tab={app[2] || 'overview'} />;
  if (path === '/chat') return <ChatPage />;
  if (path === '/calendar') return <CalendarPage />;
  const settings = path.match(/^\/settings\/([^/]+)$/);
  if (settings) return <SettingsPage section={settings[1]} />;
  return (
    <EmptyState title="Page not found" action={<LinkButton to="/">Go home</LinkButton>}>
      This page doesn’t exist in Mu3Lab.
    </EmptyState>
  );
}

function Shell() {
  const { data, connection, errorStatus, failedSources, refresh } = useDashboard();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(timer);
  }, []);
  const observed = Date.parse(data.services.observed_at || '');
  const statusStale = !Number.isFinite(observed) || now - observed > 30000;
  const { preference, setPreference } = useTheme();
  const [searchOpen, setSearchOpen] = useState(false);
  const path = usePath();
  if (connection === 'connecting') return <LoadingScreen />;
  if (connection === 'offline') return <OfflineScreen retry={() => void refresh()} />;
  if (connection === 'server_error') return <ServerErrorScreen status={errorStatus} retry={() => void refresh()} />;
  if (connection === 'signed_out') return <SignedOutScreen />;
  return (
    <div className="shell">
      <Sidebar openSearch={() => setSearchOpen(true)} theme={preference} setTheme={setPreference} />
      <MobileBar openSearch={() => setSearchOpen(true)} />
      <main className={path === '/chat' ? 'main main-full' : 'main'}>
        <div className="main-inner">
          {(statusStale || Boolean(failedSources?.length)) && (
            <p className="stale-note" role="status">
              {statusStale
                ? 'Status may be out of date.'
                : 'Some information could not be refreshed and may be out of date.'}{' '}
              <button type="button" className="link-button" onClick={() => void refresh()}>
                Try again
              </button>
            </p>
          )}
          <Suspense fallback={null}>
            <Routes />
          </Suspense>
        </div>
      </main>
      <CommandPalette open={searchOpen} setOpen={setSearchOpen} setTheme={setPreference} />
      <Toaster position="bottom-right" closeButton theme={preference} />
    </div>
  );
}

export default function App() {
  const dashboard = useDashboardLoader();
  return (
    <DashboardProvider value={dashboard}>
      <ConfirmProvider>
        <Shell />
      </ConfirmProvider>
    </DashboardProvider>
  );
}
