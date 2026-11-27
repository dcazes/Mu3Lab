import type { HomeWidget } from '../../../api';
import type { SettingsProps } from './widgets';

/** A group's name, shown in its title bar. */
export const groupTitle = (widget: HomeWidget) => String((widget.settings as { title?: string }).title || 'Group');

/** A group's own setting: its title. */
export function GroupSettings({ widget, onChange }: SettingsProps) {
  return (
    <label className="widget-setting">
      <span className="widget-setting-label">Title</span>
      <input
        className="widget-input"
        maxLength={40}
        value={String((widget.settings as { title?: string }).title ?? '')}
        placeholder="Group"
        onChange={(event) => onChange({ ...widget.settings, title: event.target.value })}
      />
    </label>
  );
}
