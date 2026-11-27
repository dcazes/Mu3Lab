import {
  ArrowLeftRight,
  Bookmark,
  CalendarDays,
  Cctv,
  Clock,
  CloudSun,
  Gauge,
  Group,
  LayoutGrid,
  type LucideIcon,
  MessageCircle,
  Newspaper,
  Search,
  Thermometer,
  TrendingUp,
  TrendingUpDown,
} from 'lucide-react';
import type { ComponentType } from 'react';
import type { HomeWidget, HomeWidgetKind } from '../../../api';
import { AppIcon } from '../../../components/AppIcon';
import { AppWidget, AppWidgetSettings } from './AppWidget';
import { AppsWidget } from './AppsWidget';
import { GroupSettings } from './GroupSettings';
import { AskWidget } from './AskWidget';
import { CalendarSettings, CalendarWidget } from './CalendarWidget';
import { CameraWidget, CameraWidgetSettings } from './CameraWidget';
import { SearchWidget } from './SearchWidget';
import { StocksSettings, StocksWidget } from './StocksWidget';
import { ThermostatWidget } from './ThermostatWidget';
import { WeatherSettings, WeatherWidget } from './WeatherWidget';
import { ClockSettings, ClockWidget } from './ClockWidget';
import { BookmarksSettings, BookmarksWidget } from './BookmarksWidget';
import { ConvertSettings, ConvertWidget } from './ConvertWidget';
import { RssSettings, RssWidget } from './RssWidget';
import { ResourcesWidget } from './ResourcesWidget';
import { MoversSettings, MoversWidget } from './MoversWidget';
import { NotesSettings, NotesWidget } from './NotesWidget';
import { SlideshowSettings, SlideshowWidget } from './SlideshowWidget';

export interface WidgetProps {
  widget: HomeWidget;
  /** Open this widget's settings (switching to edit mode), for a widget not set up yet. */
  onConfigure: () => void;
}

export interface SettingsProps {
  widget: HomeWidget;
  onChange: (settings: object) => void;
}

export interface WidgetInfo {
  title: string;
  description: string;
  icon: LucideIcon | ComponentType<{ className?: string }>;
  Component: ComponentType<WidgetProps>;
  Settings?: ComponentType<SettingsProps>;
  /** Open the settings as soon as it is added (default: whenever it has settings). */
  setUpOnAdd?: boolean;
  /** Where its title links to, when it has a page of its own. */
  to?: string;
  /** An app's widget: its title opens the app, and the Add list groups it under the app. */
  appId?: string;
  appName?: string;
}

/** Mu3Lab's own widgets; apps add theirs (``widgetInfo``). */
export const WIDGETS: Record<string, WidgetInfo> = {
  weather: {
    title: 'Weather',
    description: 'Now and the next few days, for a place you choose.',
    icon: CloudSun,
    Component: WeatherWidget,
    Settings: WeatherSettings,
  },
  calendar: {
    title: 'Calendar',
    description: 'Your upcoming events, from the same calendar as the Calendar page.',
    icon: CalendarDays,
    Component: CalendarWidget,
    Settings: CalendarSettings,
    to: '/calendar',
  },
  ask: {
    title: 'Ask',
    description: 'A quick question to the Mu3Lab assistant, answered right here.',
    icon: MessageCircle,
    Component: AskWidget,
  },
  search: {
    title: 'Search',
    description: 'A private web search, or Firecrawl to read a page or search deeper.',
    icon: Search,
    Component: SearchWidget,
  },
  apps: {
    title: 'Apps',
    description: 'How many apps are running, with the running ones a click away.',
    icon: LayoutGrid,
    Component: AppsWidget,
    to: '/apps',
  },
  stocks: {
    title: 'Stocks',
    description: 'Prices and today’s change for the tickers you follow (Yahoo Finance).',
    icon: TrendingUp,
    Component: StocksWidget,
    Settings: StocksSettings,
  },
  thermostat: {
    title: 'Thermostat',
    description: 'The temperature at home. Administrators can change it.',
    icon: Thermometer,
    Component: ThermostatWidget,
  },
  group: {
    title: 'Group',
    description: 'A titled box that keeps several widgets together. Drag widgets into it; they move with it.',
    icon: Group,
    Component: () => null,
    Settings: GroupSettings,
  },
  clock: {
    title: 'Clock',
    description: 'The time here, and in up to six other places.',
    icon: Clock,
    Component: ClockWidget,
    Settings: ClockSettings,
    setUpOnAdd: false,
  },
  bookmarks: {
    title: 'Links',
    description: 'Your bookmarks as tiles or a list, a click away.',
    icon: Bookmark,
    Component: BookmarksWidget,
    Settings: BookmarksSettings,
  },
  convert: {
    title: 'Convert',
    description: 'Quick conversions: length, weight, temperature, cooking, and currency at today’s rates.',
    icon: ArrowLeftRight,
    Component: ConvertWidget,
    Settings: ConvertSettings,
    setUpOnAdd: false,
  },
  rss: {
    title: 'News',
    description: 'The newest stories from the news feeds (RSS or Atom) you choose.',
    icon: Newspaper,
    Component: RssWidget,
    Settings: RssSettings,
  },
  resources: {
    title: 'Server',
    description: 'This server’s processor, memory, graphics memory and drives at a glance.',
    icon: Gauge,
    Component: ResourcesWidget,
  },
  movers: {
    title: 'Top movers',
    description: 'A ticker of today’s biggest stock gainers, losers or most traded, or your own tickers.',
    icon: TrendingUpDown,
    Component: MoversWidget,
    Settings: MoversSettings,
    setUpOnAdd: false,
  },
  camera: {
    title: 'Front door',
    description: 'The front-door camera, live (administrators only). Adding it sets up what it needs.',
    icon: Cctv,
    Component: CameraWidget,
    Settings: CameraWidgetSettings,
  },
};

const fromApps = new Map<string, WidgetInfo>();

/**
 * What the board needs to draw any widget: Mu3Lab's own from ``WIDGETS``, an app's from what the
 * server says about it (``kinds``). Kept per kind, so each keeps one component and one icon.
 */
export function widgetInfo(kind: string, kinds: readonly HomeWidgetKind[]): WidgetInfo {
  const own = WIDGETS[kind];
  if (own) return own;
  const entry = kinds.find((item) => item.kind === kind);
  const key = `${kind}|${entry?.title ?? ''}|${entry?.refresh_seconds ?? ''}|${entry?.view ?? ''}`;
  const known = fromApps.get(key);
  if (known) return known;
  const appId = entry?.app_id ?? kind.split(':')[0];
  const info: WidgetInfo = {
    title: entry?.title ?? 'App widget',
    description: entry?.description ?? '',
    icon: function Icon({ className }: { className?: string }) {
      return (
        <span className={className}>
          <AppIcon id={appId} size="sm" />
        </span>
      );
    },
    // Most app widgets show figures and a list; a few are drawn their own way (manifest ``view``).
    Component:
      entry?.view === 'notes'
        ? function Widget(props: WidgetProps) {
            return <NotesWidget {...props} kind={entry} />;
          }
        : entry?.view === 'slideshow'
          ? function Widget(props: WidgetProps) {
              return <SlideshowWidget {...props} kind={entry} />;
            }
          : function Widget(props: WidgetProps) {
              return <AppWidget {...props} kind={entry} />;
            },
    Settings:
      entry?.view === 'notes'
        ? NotesSettings
        : entry?.view === 'slideshow'
          ? SlideshowSettings
          : function Settings(props: SettingsProps) {
              return <AppWidgetSettings {...props} kind={entry} />;
            },
    // A widget that takes a key, or a view that needs a choice (which document), opens its settings when added.
    setUpOnAdd: Boolean(entry?.key_label) || entry?.view === 'notes',
    appId,
    appName: entry?.app_name ?? undefined,
  };
  fromApps.set(key, info);
  return info;
}
