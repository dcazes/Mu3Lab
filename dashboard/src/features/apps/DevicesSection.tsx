import { Download, Laptop, QrCode as QrIcon, Smartphone } from 'lucide-react';
import { useState } from 'react';
import type { MobileClient, Service } from '../../api';
import { Button, ExternalButton } from '../../components/Button';
import { CopyField } from '../../components/CopyField';
import { Card } from '../../components/Layout';
import { QrCode } from '../../components/QrCode';
import { Badge } from '../../components/Status';

type Platform = 'ios' | 'android' | 'desktop';

const TAILSCALE = {
  ios: 'https://apps.apple.com/app/tailscale/id1470499037',
  android: 'https://play.google.com/store/apps/details?id=com.tailscale.ipn',
};

function currentPlatform(): Platform {
  const agent = navigator.userAgent.toLowerCase();
  if (/iphone|ipad|ipod/.test(agent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1))
    return 'ios';
  if (/android/.test(agent)) return 'android';
  return 'desktop';
}

const STORE: Record<string, string> = { ios: 'App Store', android: 'Google Play', web: 'Website' };
const SUPPORT: Record<MobileClient['support'], { label: string; tone: 'green' | 'gray' | 'amber' }> = {
  official: { label: 'Official', tone: 'green' },
  community: { label: 'Community', tone: 'gray' },
  experimental: { label: 'Experimental', tone: 'amber' },
};
const KIND: Record<MobileClient['kind'], string> = { native: 'App', pwa: 'Web app', web: 'Mobile site' };

function homeScreenHint(platform: Platform) {
  if (platform === 'ios') return 'In Safari, tap Share, then Add to Home Screen.';
  if (platform === 'android') return 'In Chrome, tap ⋮, then Install app or Add to Home screen.';
  return 'On your phone: in Safari tap Share → Add to Home Screen; in Chrome tap ⋮ → Install app.';
}

function StoreLinks({ client, platform }: { client: MobileClient; platform: Platform }) {
  const [shown, setShown] = useState('');
  const links = Object.entries(client.install).sort(([a]) => (a === platform ? -1 : 1));
  if (!links.length) return null;
  return (
    <div className="stack">
      <div className="button-row">
        {links.map(([target, url]) => (
          <span key={target} className="button-row">
            <ExternalButton icon={Download} href={url} variant={target === platform ? 'primary' : undefined}>
              {STORE[target] || target}
            </ExternalButton>
            {platform === 'desktop' && (
              <Button
                icon={QrIcon}
                aria-label={`Show a QR code for ${client.name} on ${STORE[target]}`}
                onClick={() => setShown(shown === url ? '' : url)}
              />
            )}
          </span>
        ))}
      </div>
      {shown && <QrCode value={shown} label={`${client.name} store link`} />}
    </div>
  );
}

function ClientCard({ client, platform }: { client: MobileClient; platform: Platform }) {
  return (
    <Card
      title={
        <span className="title-with-badges">
          {client.name}
          <Badge tone={SUPPORT[client.support].tone}>{SUPPORT[client.support].label}</Badge>
          <Badge>{KIND[client.kind]}</Badge>
        </span>
      }
      description={client.summary}
    >
      {client.caveat && <p className="muted">{client.caveat}</p>}
      <StoreLinks client={client} platform={platform} />
      <ol className="numbered-steps">
        {client.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
        {client.kind === 'pwa' && <li>{homeScreenHint(platform)}</li>}
      </ol>
    </Card>
  );
}

function BrowserShortcuts({ service }: { service: Service }) {
  return (
    <Card
      title="Browser shortcuts"
      description={`Add a shortcut to ${service.name} for convenient access. Browser installation and window behavior depend on the app, browser, and device.`}
    >
      <ul className="steps">
        <li>
          <Laptop />
          <span>
            <b>Computer (Chrome or Edge)</b>Open {service.name} and use the browser’s install option if available, or
            bookmark the page.
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
  );
}

export function DevicesSection({ service, address }: { service: Service; address: string }) {
  const platform = currentPlatform();
  const clients = [...(service.mobile?.clients || [])].sort(
    (a, b) =>
      Number(a.fallback) - Number(b.fallback) ||
      Number(b.id === service.mobile?.primary) - Number(a.id === service.mobile?.primary),
  );
  const asksForAddress = clients.some((client) => client.setup === 'server_url');
  return (
    <>
      <Card
        title="Server address"
        description={
          asksForAddress
            ? `This is the server address the ${service.name} app asks for. Copy it, or scan the QR code with your phone.`
            : `Open this address on a device with access to your Tailscale network to use ${service.name}.`
        }
      >
        <div className="device-address">
          <div className="stack">
            <CopyField value={address} label="Server address" />
            <p className="muted">
              Your phone needs the Tailscale app, signed in to your network, to reach this address:{' '}
              <a href={TAILSCALE.ios} target="_blank" rel="noreferrer">
                iPhone
              </a>{' '}
              ·{' '}
              <a href={TAILSCALE.android} target="_blank" rel="noreferrer">
                Android
              </a>
              .
            </p>
          </div>
          <QrCode value={address} label={`QR code for ${address}`} />
        </div>
      </Card>
      {clients.length ? (
        clients.map((client) => <ClientCard key={client.id} client={client} platform={platform} />)
      ) : (
        <BrowserShortcuts service={service} />
      )}
    </>
  );
}
