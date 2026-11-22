import { type FormEvent, useState } from 'react';
import { toast } from 'sonner';
import { type CameraSettings, postJsonApi, putJsonApi, type Service } from '../../api';
import { Button } from '../../components/Button';
import { errorText } from '../../lib/format';
import { isInstalled, isWorking } from '../../lib/services';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';
import './cameraForm.css';

const BLANK = { name: 'Front door', host: '', port: 554, username: '', password: '', sd_path: '', hd_path: '' };

/** The app that plays the camera in a browser: a helper for the Camera widget, kept out of Apps. */
export function useStreamHelper(): Service | undefined {
  const { data } = useDashboard();
  return data.services.services.find((service) => service.serves_widget === 'camera');
}

/** The helper is being installed (or set up) right now. */
export const helperBusy = (helper: Service | undefined) => Boolean(helper && isWorking(helper));

/**
 * The camera's address and login. Saving also installs the stream helper (go2rtc) the first
 * time, quietly: it then runs only while someone watches.
 */
export function CameraForm({ onSaved, children }: { onSaved?: () => void; children?: React.ReactNode }) {
  const settings = useApi<CameraSettings>('/api/v1/home/devices/camera');
  const { refresh } = useDashboard();
  const helper = useStreamHelper();
  const [draft, setDraft] = useState<typeof BLANK | null>(null);
  const [saving, setSaving] = useState(false);

  const form =
    draft ??
    (settings.data
      ? {
          ...BLANK,
          name: settings.data.name,
          host: settings.data.host,
          port: settings.data.port,
          username: settings.data.username,
          sd_path: settings.data.sd_path,
          hd_path: settings.data.hd_path,
        }
      : BLANK);
  const set = (patch: Partial<typeof BLANK>) => setDraft({ ...form, ...patch });

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      await putJsonApi<CameraSettings>('/api/v1/home/devices/camera', form);
      if (helper && !isInstalled(helper) && !isWorking(helper)) {
        await postJsonApi(`/api/v1/services/${helper.id}/actions`, { action: 'install' });
        toast.success('Camera saved. Getting the live view ready; this takes about a minute the first time.');
        void refresh();
      } else toast.success('Camera saved.');
      setDraft(null);
      await settings.reload();
      onSaved?.();
    } catch (cause) {
      toast.error(errorText(cause));
    } finally {
      setSaving(false);
    }
  };

  return (
    <form className="device-form" onSubmit={(event) => void save(event)}>
      <label className="field">
        <span>Camera address</span>
        <input
          placeholder="192.168.1.40"
          value={form.host}
          onChange={(event) => set({ host: event.target.value })}
          required
        />
      </label>
      <div className="device-row">
        <label className="field">
          <span>Username</span>
          <input autoComplete="off" value={form.username} onChange={(event) => set({ username: event.target.value })} />
        </label>
        <label className="field">
          <span>Password {settings.data?.has_password && <em>saved; leave blank to keep it</em>}</span>
          <input
            type="password"
            autoComplete="new-password"
            value={form.password}
            onChange={(event) => set({ password: event.target.value })}
          />
        </label>
      </div>
      <details className="device-advanced">
        <summary>Advanced: stream port and paths</summary>
        <p className="muted">
          Reolink’s usual RTSP paths are filled in. Some models (and those set to H.265) use Preview_01_sub and
          Preview_01_main instead.
        </p>
        <div className="device-row">
          <label className="field">
            <span>RTSP port</span>
            <input
              inputMode="numeric"
              value={form.port}
              onChange={(event) => set({ port: Number(event.target.value) || 554 })}
            />
          </label>
          <label className="field">
            <span>Light stream path</span>
            <input
              value={form.sd_path}
              placeholder="h264Preview_01_sub"
              onChange={(event) => set({ sd_path: event.target.value })}
            />
          </label>
          <label className="field">
            <span>HD stream path</span>
            <input
              value={form.hd_path}
              placeholder="h264Preview_01_main"
              onChange={(event) => set({ hd_path: event.target.value })}
            />
          </label>
        </div>
      </details>
      <p className="muted device-hint">
        In the Reolink app, switch on RTSP under <b>Settings › Network › Advanced › Server settings</b>. A separate
        camera user that can only view is safest.
      </p>
      <div className="device-actions">
        <Button type="submit" variant="primary" loading={saving}>
          Save
        </Button>
        {children}
      </div>
    </form>
  );
}
