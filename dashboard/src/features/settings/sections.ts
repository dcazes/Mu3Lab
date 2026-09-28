import { Bot, CalendarDays, KeyRound, type LucideIcon, Plug, ServerCog, ShieldCheck } from 'lucide-react';

export interface SettingsSection {
  id: string;
  label: string;
  icon: LucideIcon;
}

export const SETTINGS_SECTIONS: SettingsSection[] = [
  { id: 'ai', label: 'AI providers', icon: Bot },
  { id: 'integrations', label: 'Chat integrations', icon: Plug },
  { id: 'sign-in', label: 'Sign-in', icon: KeyRound },
  { id: 'calendar', label: 'Calendar', icon: CalendarDays },
  { id: 'security', label: 'Security & backups', icon: ShieldCheck },
  { id: 'system', label: 'System', icon: ServerCog },
];
