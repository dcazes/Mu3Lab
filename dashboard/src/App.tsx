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

// The worker re-checks every five minutes while idle; allow for one late check.
const STATUS_STALE_MS = 7 * 60 * 1000;

// Heavier pages load after Home so it opens quickly; preloadPages fetches them soon after.
const loadCalendar = () => import('./features/calendar/CalendarPage');
const loadSettings = () => import('./features/settings/SettingsPage');
const CalendarPage = lazy(() => loadCalendar().then((m) => ({ default: m.CalendarPage })));
const SettingsPage = lazy(() => loadSettings().then((m) => ({ default: m.SettingsPage })));

/** Fetch the lazy pages' code in the background, so opening one never flashes an empty page. */
function preloadPages() {
  const timer = window.setTimeout(() => {
    void loadCalendar();
    void loadSettings();
  }, 2000);
  return () => window.clearTimeout(timer);
}

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
  useEffect(preloadPages, []);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, []);
  const observed = Date.parse(data.services.observed_at || '');
  // While nobody is busy the worker checks every five minutes, so only older than that is out of date.
  const statusStale = !Number.isFinite(observed) || now - observed > STATUS_STALE_MS;
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
      <MobileBar openSearch={() => setSearchOpen(true)} theme={preference} setTheme={setPreference} />
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
      <Toaster position="bottom-right" closeButton duration={4000} theme={preference} />
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
