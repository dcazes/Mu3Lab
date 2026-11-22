import { Camera, Search, Thermometer, Trash2 } from 'lucide-react';
import { type FormEvent, useState } from 'react';
import { toast } from 'sonner';
import {
  api,
  type CameraSettings,
  deleteApi,
  type HomeKitDevices,
  postJsonApi,
  type ThermostatStatus,
} from '../../api';
import { Button } from '../../components/Button';
import { Callout, Card, PageHeader } from '../../components/Layout';
import { Badge } from '../../components/Status';
import { errorText } from '../../lib/format';
import { isInstalled } from '../../lib/services';
import { useApi } from '../../lib/useApi';
import { CameraForm, helperBusy, useStreamHelper } from './CameraForm';

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

function CameraCard() {
  const settings = useApi<CameraSettings>('/api/v1/home/devices/camera');
  const helper = useStreamHelper();
  const [picture, setPicture] = useState('');
  const [testing, setTesting] = useState(false);

  const test = async () => {
    setTesting(true);
    try {
      // The stream helper starts for the picture if it is stopped, so the first one can take a few seconds.
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
      setPicture('');
      await settings.reload();
    } catch (cause) {
      toast.error(errorText(cause));
    }
  };
  const ready = Boolean(helper && isInstalled(helper));

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
      {helperBusy(helper) && (
        <Callout tone="info" title="Getting the live view ready">
          Mu3Lab is installing the small program that plays the camera in a browser. It runs only while someone watches.
        </Callout>
      )}
      <CameraForm onSaved={() => setPicture('')}>
        {settings.data?.configured && (
          <Button variant="secondary" loading={testing} disabled={!ready} onClick={() => void test()}>
            Test picture
          </Button>
        )}
      </CameraForm>
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
