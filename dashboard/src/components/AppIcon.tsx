import {
  AudioLines,
  Baby,
  BookOpenText,
  Bot,
  Compass,
  Flame,
  Gamepad2,
  KeyRound,
  ListChecks,
  type LucideIcon,
  MapPinned,
  MessagesSquare,
  Network,
  Route,
  ShieldCheck,
} from 'lucide-react';
import type { CSSProperties } from 'react';
import {
  siActualbudget,
  siAudiobookshelf,
  siAuthentik,
  siCaddy,
  siImmich,
  siGrocy,
  siMealie,
  siNextcloud,
  siOllama,
  siOpenstreetmap,
  siOutline,
  type SimpleIcon,
  siPaperlessngx,
  siTailscale,
  siVaultwarden,
} from 'simple-icons';

const brands: Record<string, SimpleIcon> = {
  ingress: siCaddy,
  authentik: siAuthentik,
  vaultwarden: siVaultwarden,
  ollama: siOllama,
  'actual-budget': siActualbudget,
  immich: siImmich,
  mealie: siMealie,
  grocy: siGrocy,
  'paperless-ngx': siPaperlessngx,
  nextcloud: siNextcloud,
  tailscale: siTailscale,
  audiobookshelf: siAudiobookshelf,
  outline: siOutline,
  photon: siOpenstreetmap,
};

const fallbacks: Record<string, { icon: LucideIcon; color: string }> = {
  lobehub: { icon: Bot, color: '#8b5cf6' },
  litellm: { icon: Route, color: '#0ea5e9' },
  freellmapi: { icon: KeyRound, color: '#14b8a6' },
  firecrawl: { icon: Flame, color: '#f97316' },
  adventurelog: { icon: Compass, color: '#16a34a' },
  surfsense: { icon: BookOpenText, color: '#6366f1' },
  'baby-buddy': { icon: Baby, color: '#ec4899' },
  'beaver-habits': { icon: ListChecks, color: '#b45309' },
  romm: { icon: Gamepad2, color: '#7c3aed' },
  dawarich: { icon: MapPinned, color: '#0891b2' },
  'open-webui': { icon: MessagesSquare, color: '#64748b' },
  speaches: { icon: AudioLines, color: '#db2777' },
  tailnet: { icon: Network, color: '#64748b' },
};

// Near-black brand colors would vanish in dark mode; those glyphs follow the text color.
const readable = (hex: string) => (parseInt(hex, 16) < 0x333333 ? undefined : `#${hex}`);

export function AppIcon({ id, size = 'md' }: { id: string; size?: 'sm' | 'md' | 'lg' | 'xl' }) {
  const brand = brands[id];
  const fallback = fallbacks[id] || { icon: ShieldCheck, color: '#64748b' };
  const color = brand ? readable(brand.hex) : fallback.color;
  const style = { '--icon-color': color } as CSSProperties;
  const Icon = fallback.icon;
  return (
    <span className={`app-icon app-icon-${size}`} style={style} aria-hidden="true">
      {brand ? (
        <svg viewBox="0 0 24 24" focusable="false">
          <path d={brand.path} />
        </svg>
      ) : (
        <Icon strokeWidth={1.8} />
      )}
    </span>
  );
}
