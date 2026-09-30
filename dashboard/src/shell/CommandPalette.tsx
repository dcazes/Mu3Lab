import { Command } from 'cmdk';
import {
  ArrowUpRight,
  CalendarDays,
  Home,
  LayoutGrid,
  MessageSquare,
  Monitor,
  Moon,
  PackagePlus,
  Settings,
  Sun,
} from 'lucide-react';
import { useEffect } from 'react';
import { AppIcon } from '../components/AppIcon';
import { navigate } from '../lib/router';
import { isInstalled, launchTarget } from '../lib/services';
import type { ThemePreference } from '../lib/theme';
import { useDashboard } from '../state/dashboard';
import { useIsAdmin } from '../state/dashboard';
import { settingsSectionsFor } from '../features/settings/sections';

export function CommandPalette({
  open,
  setOpen,
  setTheme,
}: {
  open: boolean;
  setOpen: (open: boolean) => void;
  setTheme: (theme: ThemePreference) => void;
}) {
  const isAdmin = useIsAdmin();
  const { data } = useDashboard();
  useEffect(() => {
    const toggle = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() === 'k' && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        setOpen(!open);
      }
    };
    window.addEventListener('keydown', toggle);
    return () => window.removeEventListener('keydown', toggle);
  }, [open, setOpen]);

  const go = (path: string) => {
    setOpen(false);
    navigate(path);
  };
  const installed = data.services.services.filter(isInstalled);
  const launchable = installed
    .map((service) => ({ service, target: launchTarget(service) }))
    .filter((item) => item.target);

  return (
    <Command.Dialog
      open={open}
      onOpenChange={setOpen}
      label="Command menu"
      className="command"
      overlayClassName="command-overlay"
      contentClassName="command-content"
    >
      <Command.Input placeholder="Search apps, pages, and actions…" />
      <Command.List>
        <Command.Empty>No results.</Command.Empty>
        <Command.Group heading="Open app">
          {launchable.map(({ service, target }) => (
            <Command.Item
              key={`open-${service.id}`}
              value={`open ${service.name}`}
              onSelect={() => {
                setOpen(false);
                window.open(target!.url, '_blank', 'noopener,noreferrer');
              }}
            >
              <AppIcon id={service.id} size="sm" />
              {service.name}
              <ArrowUpRight className="command-trailing" />
            </Command.Item>
          ))}
        </Command.Group>
        <Command.Group heading="Go to">
          <Command.Item onSelect={() => go('/')}>
            <Home /> Home
          </Command.Item>
          <Command.Item onSelect={() => go('/apps')}>
            <LayoutGrid /> Apps
          </Command.Item>
          <Command.Item onSelect={() => go('/apps/discover')}>
            <PackagePlus /> Discover apps
          </Command.Item>
          <Command.Item onSelect={() => go('/chat')}>
            <MessageSquare /> Chat
          </Command.Item>
          <Command.Item onSelect={() => go('/calendar')}>
            <CalendarDays /> Calendar
          </Command.Item>
          {settingsSectionsFor(isAdmin).map((section) => (
            <Command.Item
              key={section.id}
              value={`settings ${section.label}`}
              onSelect={() => go(`/settings/${section.id}`)}
            >
              <Settings /> Settings: {section.label}
            </Command.Item>
          ))}
        </Command.Group>
        <Command.Group heading="Manage app">
          {installed.map((service) => (
            <Command.Item
              key={`manage-${service.id}`}
              value={`manage ${service.name}`}
              onSelect={() => go(`/apps/${service.id}`)}
            >
              <AppIcon id={service.id} size="sm" />
              {service.name} settings and status
            </Command.Item>
          ))}
        </Command.Group>
        <Command.Group heading="Appearance">
          <Command.Item onSelect={() => (setTheme('light'), setOpen(false))}>
            <Sun /> Light theme
          </Command.Item>
          <Command.Item onSelect={() => (setTheme('dark'), setOpen(false))}>
            <Moon /> Dark theme
          </Command.Item>
          <Command.Item onSelect={() => (setTheme('system'), setOpen(false))}>
            <Monitor /> Match system theme
          </Command.Item>
        </Command.Group>
      </Command.List>
    </Command.Dialog>
  );
}
