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
import type { HomeWidget } from '../../../api';
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
  icon: LucideIcon;
  Component: ComponentType<WidgetProps>;
  Settings?: ComponentType<SettingsProps>;
  /** Where its title links to, when it has a page of its own. */
  to?: string;
}

export const WIDGETS: Record<HomeWidget['kind'], WidgetInfo> = {
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
