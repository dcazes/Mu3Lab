import { CalendarDays, Link2, Unlink } from 'lucide-react';
import { useEffect, useState } from 'react';
import { type CalendarAuthorization, type CalendarConnection, deleteApi, postApi, putJsonApi } from '../../api';
import { Button, ExternalButton, LinkButton } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { Callout, Card, EmptyState, Facts, PageHeader } from '../../components/Layout';
import { StateBadge } from '../../components/Status';
import { errorText } from '../../lib/format';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';
import { useDashboard } from '../../state/dashboard';
import { toast } from 'sonner';

export function CalendarSettings() {
  const { data } = useDashboard();
  const nextcloud = data.services.services.find((service) => service.id === 'nextcloud');
  const connection = useApi<CalendarConnection>('/api/v1/calendar/connection');
  const [authorization, setAuthorization] = useState<CalendarAuthorization | null>(null);
  const { pending, run } = useAction();
  const confirm = useConfirm();
  const installed = nextcloud && nextcloud.installed;
  const state = connection.data?.state;
  const openUrl = nextcloud?.ui?.state === 'ready' && nextcloud.ui.url ? `${nextcloud.ui.url}/apps/calendar/` : '';

  const authId = authorization?.authorization_id;
  const authState = authorization?.state;
  const pollAfter = authorization?.poll_after_ms;
  const reload = connection.reload;
  useEffect(() => {
    if (!authId || !['awaiting_user', 'pending'].includes(authState || '')) return;
    const timer = window.setInterval(() => {
      postApi<CalendarAuthorization>(`/api/v1/calendar/authorization/${authId}/poll`)
        .then((result) => {
          setAuthorization(result);
          if (result.state === 'connected') {
            toast.success('Calendar connected');
            void reload();
          }
          if (result.state === 'failed') toast.error(result.error || 'Access was not approved in Nextcloud.');
          if (result.state === 'expired') toast.error('The approval request expired. Try again when ready.');
        })
        .catch((error) => toast.error(errorText(error)));
    }, pollAfter || 2000);
    return () => window.clearInterval(timer);
  }, [authId, authState, pollAfter, reload]);

  const connect = async () => {
    // Opening the tab during the click keeps the browser from blocking it as a popup.
    const approval = window.open('', '_blank');
    const result = await run('connect', () => postApi<CalendarAuthorization>('/api/v1/calendar/authorization'));
    if (!result) {
      approval?.close();
      return;
    }
    setAuthorization(result);
    if (result.login_url && approval) {
      approval.opener = null;
      approval.location.href = result.login_url;
    } else if (result.login_url) window.open(result.login_url, '_blank', 'noopener,noreferrer');
  };
  const disconnect = async () => {
    if (
      !(await confirm({
        title: 'Disconnect your calendar?',
        description: 'The device credential Mu3Lab created in Nextcloud is revoked.',
        confirmLabel: 'Disconnect',
        tone: 'danger',
      }))
    )
      return;
    await run('disconnect', () => deleteApi('/api/v1/calendar/connection'), 'Calendar disconnected');
    setAuthorization(null);
    void connection.reload();
  };
  const waiting = authorization && ['awaiting_user', 'pending'].includes(authorization.state);
  const connected = state === 'connected' || state === 'authentication_expired';

  return (
    <>
      <PageHeader
        title="Calendar"
        description="Connect a Nextcloud calendar to your Home agenda and manage its events in Mu3Lab."
      />
      {!installed ? (
        <EmptyState
          icon={CalendarDays}
          title="Nextcloud isn’t installed"
          action={<LinkButton to="/apps/nextcloud">View Nextcloud</LinkButton>}
        >
          Your calendar lives in Nextcloud.
        </EmptyState>
      ) : (
        <Card
          title="Nextcloud calendar"
          description="Choose one Nextcloud calendar to display and edit. Mu3Lab stores an encrypted app credential for calendar access; you do not need to enter your Nextcloud password."
          actions={connection.data && <StateBadge state={state === 'connected' ? 'connected' : state || 'unknown'} />}
        >
          {state === 'service_stopped' && (
            <Callout
              tone="warning"
              title="Nextcloud is stopped"
              action={<LinkButton to="/apps/nextcloud">Open Nextcloud</LinkButton>}
            >
              Start it to connect the calendar.
            </Callout>
          )}
          {state === 'sso_not_ready' && (
            <Callout
              tone="warning"
              title="Nextcloud sign-in needs repair"
              action={<LinkButton to="/settings/sign-in">Fix sign-in</LinkButton>}
            >
              Repair sign-in before connecting the calendar.
            </Callout>
          )}
          {state === 'authentication_expired' && (
            <Callout tone="danger" title="Access expired">
              Nextcloud rejected the saved authorization. Reconnect to keep syncing.
            </Callout>
          )}
          {connected && connection.data && (
            <Facts
              items={[
                { label: 'Account', value: connection.data.username_hint || '—' },
                {
                  label: 'Calendar',
                  value:
                    connection.data.calendars.length > 1 ? (
                      <select
                        className="select-sm"
                        aria-label="Calendar"
                        value={connection.data.selected_calendar_id}
                        disabled={Boolean(pending)}
                        onChange={async (event) => {
                          const value = event.target.value;
                          await run(
                            'select',
                            () => putJsonApi('/api/v1/calendar/connection', { calendar_id: value }),
                            'Calendar changed',
                          );
                          void connection.reload();
                        }}
                      >
                        {connection.data.calendars.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.name}
                          </option>
                        ))}
                      </select>
                    ) : (
                      connection.data.calendars[0]?.name || '—'
                    ),
                },
              ]}
            />
          )}
          {waiting && (
            <Callout
              tone="info"
              title="Waiting for your approval in Nextcloud"
              action={
                authorization.login_url && <ExternalButton href={authorization.login_url}>Continue</ExternalButton>
              }
            >
              {authorization.expires_at &&
                `This request expires at ${new Date(authorization.expires_at).toLocaleTimeString()}.`}
            </Callout>
          )}
          <div className="button-row">
            {(!connected || state === 'authentication_expired') && !waiting && (
              <Button
                variant="primary"
                icon={Link2}
                loading={pending === 'connect'}
                disabled={state === 'service_stopped' || state === 'sso_not_ready'}
                onClick={() => void connect()}
              >
                {state === 'authentication_expired' ? 'Reconnect' : 'Connect calendar'}
              </Button>
            )}
            {waiting && (
              <Button
                onClick={async () => {
                  await run('cancel', () =>
                    deleteApi(`/api/v1/calendar/authorization/${authorization.authorization_id}`),
                  );
                  setAuthorization(null);
                }}
              >
                Cancel
              </Button>
            )}
            {connected && (
              <Button
                icon={Unlink}
                className="danger-text"
                loading={pending === 'disconnect'}
                onClick={() => void disconnect()}
              >
                Disconnect
              </Button>
            )}
            {openUrl && <ExternalButton href={openUrl}>Open in Nextcloud</ExternalButton>}
          </div>
        </Card>
      )}
    </>
  );
}
