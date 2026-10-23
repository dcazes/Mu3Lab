import {
  Bot,
  KeyRound,
  type LucideIcon,
  Mic,
  Plug,
  Rocket,
  ServerCog,
  ShieldCheck,
  Sparkles,
  Users,
} from 'lucide-react';

export interface SettingsSection {
  id: string;
  label: string;
  icon: LucideIcon;
  /** Only administrators see it. */
  adminOnly?: boolean;
  /** Not in the Settings menu: reached from this section's page (and search), which stays highlighted. */
  within?: string;
  /** The heading it sits under in the Settings menu. */
  group: SettingsGroup;
}

export type SettingsGroup = 'AI' | 'Household' | 'Server' | 'Help';

/** The menu in groups a person can scan: what AI does, who uses Mu3Lab, the server itself, then help. */
export const SETTINGS_SECTIONS: SettingsSection[] = [
  { id: 'ai', label: 'AI', icon: Bot, group: 'AI' },
  { id: 'integrations', label: 'Chat integrations', icon: Plug, group: 'AI' },
  { id: 'voice', label: 'Voice assistant', icon: Mic, group: 'AI' },
  { id: 'people', label: 'People', icon: Users, adminOnly: true, group: 'Household' },
  { id: 'sign-in', label: 'Sign-in', icon: KeyRound, group: 'Household' },
  { id: 'security', label: 'Security & backups', icon: ShieldCheck, group: 'Server' },
  { id: 'system', label: 'System', icon: ServerCog, adminOnly: true, group: 'Server' },
  // Linked from System's Mu3Lab updates card and the user menu.
  { id: 'whats-new', label: "What's new", icon: Sparkles, adminOnly: true, within: 'system', group: 'Server' },
  // First-run help; once done it is rarely opened, so it comes last.
  { id: 'get-started', label: 'Get started', icon: Rocket, group: 'Help' },
];

export const settingsSectionsFor = (isAdmin: boolean) =>
  SETTINGS_SECTIONS.filter((section) => isAdmin || !section.adminOnly);
