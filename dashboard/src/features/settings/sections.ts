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
}

export const SETTINGS_SECTIONS: SettingsSection[] = [
  { id: 'get-started', label: 'Get started', icon: Rocket },
  { id: 'ai', label: 'AI', icon: Bot },
  { id: 'integrations', label: 'Chat integrations', icon: Plug },
  { id: 'voice', label: 'Voice Assistant', icon: Mic },
  { id: 'people', label: 'People', icon: Users, adminOnly: true },
  { id: 'security', label: 'Security & backups', icon: ShieldCheck },
  { id: 'system', label: 'System', icon: ServerCog, adminOnly: true },
  // Rarely needed once set up, so it sits near the end.
  { id: 'sign-in', label: 'Sign-in', icon: KeyRound },
  // Linked from System's Mu3Lab updates card and the user menu.
  { id: 'whats-new', label: "What's new", icon: Sparkles, adminOnly: true, within: 'system' },
];

export const settingsSectionsFor = (isAdmin: boolean) =>
  SETTINGS_SECTIONS.filter((section) => isAdmin || !section.adminOnly);
