import {
  CalendarDays,
  Cctv,
  CloudSun,
  LayoutGrid,
  type LucideIcon,
  MessageCircle,
  Thermometer,
  TrendingUp,
} from 'lucide-react';
import type { ComponentType } from 'react';
import type { HomeWidget, HomeWidgetKind } from '../../../api';
import { AppIcon } from '../../../components/AppIcon';
import { AppWidget, KeySettings } from './AppWidget';
import { AppsWidget } from './AppsWidget';
import { AskWidget } from './AskWidget';
import { CalendarSettings, CalendarWidget } from './CalendarWidget';
import { CameraWidget, CameraWidgetSettings } from './CameraWidget';
import { StocksSettings, StocksWidget } from './StocksWidget';
import { ThermostatWidget } from './ThermostatWidget';
import { WeatherSettings, WeatherWidget } from './WeatherWidget';

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
  const key = `${kind}|${entry?.title ?? ''}|${entry?.refresh_seconds ?? ''}`;
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
    Component: function Widget(props: WidgetProps) {
      return <AppWidget {...props} kind={entry} />;
    },
    Settings: entry?.key_label
      ? function Settings(props: SettingsProps) {
          return <KeySettings {...props} kind={entry} />;
        }
      : undefined,
    appId,
    appName: entry?.app_name ?? undefined,
  };
  fromApps.set(key, info);
  return info;
}
