import { Camera, Search, Thermometer, Trash2 } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { toast } from 'sonner';
import {
  api,
  type CameraSettings,
  deleteApi,
  type HomeKitDevices,
  postJsonApi,
  putJsonApi,
  type ThermostatStatus,
} from '../../api';
import { Button, LinkButton } from '../../components/Button';
import { Callout, Card, PageHeader } from '../../components/Layout';
import { Badge } from '../../components/Status';
import { errorText } from '../../lib/format';
import { isInstalled, isRunning } from '../../lib/services';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';

function ThermostatCard() {
  const status = useApi<ThermostatStatus>('/api/v1/home/thermostat');
  const [found, setFound] = useState<HomeKitDevices['devices'] | null>(null);
  const [searching, setSearching] = useState(false);
  const [picked, setPicked] = useState('');
  const [code, setCode] = useState('');
  const [pairing, setPairing] = useState(false);

  const search = async () => {
    setSearching(true);
    setFound(null);
    try {
      const result = await api<HomeKitDevices>('/api/v1/home/devices/thermostat');
      setFound(result.devices);
      setPicked(result.devices.find((device) => !device.paired)?.id ?? '');
    } catch (cause) {
      toast.error(errorText(cause));
    } finally {
      setSearching(false);
    }
  };
  const pair = async (event: FormEvent) => {
    event.preventDefault();
    setPairing(true);
    try {
      const result = await postJsonApi<ThermostatStatus>('/api/v1/home/devices/thermostat', {
        device_id: picked,
        code,
      });
      toast.success(`${result.name || 'The thermostat'} is paired. Add the Thermostat widget on Home.`);
      setFound(null);
      setCode('');
      await status.reload();
    } catch (cause) {
      toast.error(errorText(cause));
    } finally {
      setPairing(false);
    }
  };
  const remove = async () => {
    try {
      await deleteApi('/api/v1/home/devices/thermostat');
      toast.success('The thermostat was removed from Mu3Lab.');
      await status.reload();
    } catch (cause) {
      toast.error(errorText(cause));
    }
  };

  const paired = status.data?.paired;
  return (
    <Card
      title={
        <span className="device-title">
          <Thermometer aria-hidden /> Thermostat
        </span>
      }
      description="Mu3Lab talks to the thermostat directly on your home network through HomeKit: no cloud account, no Home Assistant. Everyone sees it on Home; administrators change it."
      actions={
        paired ? (
          <Button variant="ghost" size="sm" icon={Trash2} onClick={() => void remove()}>
            Remove
          </Button>
        ) : undefined
      }
    >
      {paired ? (
        <p>
          <Badge tone="green">Paired</Badge> {status.data?.name}
        </p>
      ) : status.error ? (
        <Callout tone="warning" title="The thermostat did not answer">
          {status.error}
        </Callout>
      ) : (
        <>
          <ol className="device-steps">
            <li>
              On the thermostat, open <b>Menu › Settings › HomeKit</b> (ecobee) to show its 8-digit HomeKit code.
            </li>
            <li>A thermostat can be in one home only: if it is in Apple Home, remove it there first.</li>
            <li>Find it below, type the code and choose Pair.</li>
          </ol>
          <div>
            <Button variant="secondary" icon={Search} loading={searching} onClick={() => void search()}>
              {searching ? 'Looking on your network…' : 'Find thermostats'}
            </Button>
          </div>
          {found && !found.length && (
            <Callout tone="warning" title="No HomeKit thermostat found">
              Check the thermostat is on the same Wi-Fi as this server (not a guest or separate devices network) and
              that its HomeKit code is showing, then look again.
            </Callout>
          )}
          {found && found.length > 0 && (
            <form className="device-form" onSubmit={(event) => void pair(event)}>
              <label className="field">
                <span>Thermostat</span>
                <select value={picked} onChange={(event) => setPicked(event.target.value)} required>
                  {found.map((device) => (
                    <option key={device.id} value={device.id} disabled={device.paired}>
                      {device.name}
                      {device.model ? ` (${device.model})` : ''}
                      {device.paired ? ' — paired elsewhere' : ''}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                <span>HomeKit code</span>
                <input
                  inputMode="numeric"
                  autoComplete="off"
                  placeholder="123-45-678"
                  value={code}
                  onChange={(event) => setCode(event.target.value)}
                  required
                />
              </label>
              <div>
                <Button type="submit" variant="primary" loading={pairing} disabled={!picked}>
                  Pair
                </Button>
              </div>
            </form>
          )}
        </>
      )}
    </Card>
  );
}

const BLANK = { name: 'Front door', host: '', port: 554, username: '', password: '', sd_path: '', hd_path: '' };

function CameraCard() {
  const { data: dashboard } = useDashboard();
  const settings = useApi<CameraSettings>('/api/v1/home/devices/camera');
  const [draft, setDraft] = useState<typeof BLANK | null>(null);
  const [saving, setSaving] = useState(false);
  const [picture, setPicture] = useState('');
  const [testing, setTesting] = useState(false);
  const app = dashboard.services.services.find((service) => service.id === 'go2rtc');
  const ready = app && isInstalled(app) && isRunning(app);

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
      toast.success('Camera saved.');
      setDraft(null);
      setPicture('');
      await settings.reload();
    } catch (cause) {
      toast.error(errorText(cause));
    } finally {
      setSaving(false);
    }
  };
  const test = async () => {
    setTesting(true);
    try {
      const response = await fetch(`/api/v1/home/camera/snapshot.jpg?t=${Date.now()}`);
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as { error?: string };
        throw new Error(body.error || 'The camera did not send a picture.');
      }
      setPicture(URL.createObjectURL(await response.blob()));
    } catch (cause) {
      toast.error(errorText(cause));
    } finally {
      setTesting(false);
    }
  };
  const remove = async () => {
    try {
      await deleteApi('/api/v1/home/devices/camera');
      toast.success('The camera was removed from Mu3Lab.');
      setDraft(null);
      setPicture('');
      await settings.reload();
    } catch (cause) {
      toast.error(errorText(cause));
    }
  };

  return (
    <Card
      title={
        <span className="device-title">
          <Camera aria-hidden /> Front-door camera
        </span>
      }
      description="A live view on Home for administrators, at home or away. Nothing is recorded; the camera is used only while someone watches."
      actions={
        settings.data?.configured ? (
          <Button variant="ghost" size="sm" icon={Trash2} onClick={() => void remove()}>
            Remove
          </Button>
        ) : undefined
      }
    >
      {!ready && (
        <Callout tone="warning" title="Install go2rtc first">
          go2rtc turns the camera’s stream into one a browser can play.{' '}
          <LinkButton variant="ghost" size="sm" to={app ? '/apps/go2rtc' : '/apps/discover'}>
            {app && isInstalled(app) ? 'Start go2rtc' : 'Install go2rtc'}
          </LinkButton>
        </Callout>
      )}
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
            <input
              autoComplete="off"
              value={form.username}
              onChange={(event) => set({ username: event.target.value })}
            />
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
          {settings.data?.configured && (
            <Button
              variant="secondary"
              loading={testing}
              disabled={!ready || draft !== null}
              onClick={() => void test()}
            >
              Test picture
            </Button>
          )}
        </div>
      </form>
      {picture && <img className="device-picture" src={picture} alt="A picture from the camera" />}
    </Card>
  );
}

/** Settings > Home devices: the thermostat and the front-door camera Home can show. */
export function HomeDevicesSettings() {
  return (
    <>
      <PageHeader
        title="Home devices"
        description="Devices in your home that Mu3Lab can show on the Home page. They stay on your home network."
      />
      <ThermostatCard />
      <CameraCard />
    </>
  );
}
