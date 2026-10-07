import { Bot, CalendarDays, KeyRound, type LucideIcon, Mic, Plug, ServerCog, ShieldCheck, Users } from 'lucide-react';

export interface SettingsSection {
  id: string;
  label: string;
  icon: LucideIcon;
  /** Only administrators see it. */
  adminOnly?: boolean;
}

export const SETTINGS_SECTIONS: SettingsSection[] = [
  { id: 'ai', label: 'AI providers', icon: Bot },
  { id: 'integrations', label: 'Chat integrations', icon: Plug },
  { id: 'sign-in', label: 'Sign-in', icon: KeyRound },
  { id: 'calendar', label: 'Calendar', icon: CalendarDays },
  { id: 'voice', label: 'Voice key', icon: Mic },
  { id: 'people', label: 'People', icon: Users, adminOnly: true },
  { id: 'security', label: 'Security & backups', icon: ShieldCheck },
  { id: 'system', label: 'System', icon: ServerCog, adminOnly: true },
];

export const settingsSectionsFor = (isAdmin: boolean) =>
  SETTINGS_SECTIONS.filter((section) => isAdmin || !section.adminOnly);
