import {
  ArchiveRestore,
  AudioLines,
  Baby,
  BookOpenText,
  Boxes,
  Bot,
  Cctv,
  Compass,
  Eraser,
  Flame,
  Gamepad2,
  KeyRound,
  ListChecks,
  type LucideIcon,
  MapPinned,
  MessageCircle,
  MessagesSquare,
  Music,
  Network,
  Plug,
  Popcorn,
  Route,
  SearchCheck,
  ShieldCheck,
  WalletCards,
} from 'lucide-react';
import type { CSSProperties } from 'react';
import {
  siActualbudget,
  siAudiobookshelf,
  siAuthentik,
  siCaddy,
  siImmich,
  siGrocy,
  siJellyfin,
  siLivekit,
  siMealie,
  siN8n,
  siNextcloud,
  siOllama,
  siSearxng,
  siOutline,
  type SimpleIcon,
  siPaperlessngx,
  siQbittorrent,
  siRadarr,
  siSonarr,
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
  searxng: siSearxng,
  qbittorrent: siQbittorrent,
  livekit: siLivekit,
  jellyfin: siJellyfin,
  sonarr: siSonarr,
  radarr: siRadarr,
  n8n: siN8n,
};

const fallbacks: Record<string, { icon: LucideIcon; color: string }> = {
  lobehub: { icon: Bot, color: '#8b5cf6' },
  librechat: { icon: MessageCircle, color: '#0d9488' },
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
  'kopia-ui': { icon: ArchiveRestore, color: '#2563eb' },
  navidrome: { icon: Music, color: '#0284c7' },
  contextforge: { icon: Plug, color: '#0f62fe' },
  prowlarr: { icon: SearchCheck, color: '#e66001' },
  cleanuparr: { icon: Eraser, color: '#0e7490' },
  seerr: { icon: Popcorn, color: '#6d28d9' },
  homebox: { icon: Boxes, color: '#6c4a2e' },
  wallos: { icon: WalletCards, color: '#007bff' },
  go2rtc: { icon: Cctv, color: '#0f766e' },
};

// A near-black brand color would vanish in dark mode and a near-white one (LiveKit's) in light mode;
// those glyphs follow the text color.
const readable = (hex: string) => {
  const [r, g, b] = [0, 2, 4].map((at) => parseInt(hex.slice(at, at + 2), 16));
  const lightness = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return lightness < 51 || lightness > 225 ? undefined : `#${hex}`;
};

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
