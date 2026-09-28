import { Download, Laptop, Smartphone } from 'lucide-react';
import type { Service } from '../../api';
import { ExternalButton } from '../../components/Button';
import { CopyField } from '../../components/CopyField';
import { Card } from '../../components/Layout';
import { QrCode } from '../../components/QrCode';
import { companionFor } from '../../lib/companions';

export function DevicesTab({ service, address }: { service: Service; address: string }) {
  const companion = companionFor(service.id);
  return (
    <div className="stack">
      <Card
        title="Server address"
        description={
          companion?.serverHint || `Open this address on any device connected to your tailnet to use ${service.name}.`
        }
      >
        <div className="device-address">
          <div className="stack">
            <CopyField value={address} label="Server address" />
            <p className="muted">
              Devices must be signed in to Tailscale to reach this address. Scan the code with your phone’s camera.
            </p>
          </div>
          <QrCode value={address} label={`QR code for ${address}`} />
        </div>
      </Card>
      {companion ? (
        <Card title="Official apps" description={companion.note}>
          <div className="button-row">
            {companion.apps.map((app) => (
              <ExternalButton key={app.platform} icon={Download} href={app.url}>
                {app.platform}
              </ExternalButton>
            ))}
          </div>
        </Card>
      ) : (
        <Card
          title="Install as an app"
          description={`${service.name} has no official mobile app, but it installs from your browser and opens in its own window.`}
        >
          <ul className="steps">
            <li>
              <Laptop />
              <span>
                <b>Computer (Chrome or Edge)</b>Open {service.name}, then choose the install icon in the address bar.
              </span>
            </li>
            <li>
              <Smartphone />
              <span>
                <b>Android</b>Open it in Chrome, tap ⋮, then <i>Add to Home screen</i>.
              </span>
            </li>
            <li>
              <Smartphone />
              <span>
                <b>iPhone or iPad</b>Open it in Safari, tap Share, then <i>Add to Home Screen</i>.
              </span>
            </li>
          </ul>
        </Card>
      )}
    </div>
  );
}
