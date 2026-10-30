import { Plus, RefreshCw, Settings } from 'lucide-react';
import { useState } from 'react';
import { Button, ExternalButton } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { PageHeader } from '../../components/Layout';
import { useDashboard } from '../../state/dashboard';
import { CalendarSettings } from './CalendarSettings';
import { CalendarView } from './CalendarView';

export function CalendarPage() {
  const { data } = useDashboard();
  const [settings, setSettings] = useState(false);
  const nextcloud = data.services.services.find((service) => service.id === 'nextcloud');
  const openUrl = nextcloud?.ui?.state === 'ready' && nextcloud.ui.url ? `${nextcloud.ui.url}/apps/calendar/` : '';

  return (
    <div className="page">
      <CalendarView
        nextcloud={nextcloud}
        openSettings={() => setSettings(true)}
        header={({ result, loading, ready, reload, newEvent, status }) => (
          <PageHeader
            title={result?.calendar?.name || 'Calendar'}
            description={status}
            actions={
              <>
                {/* Quiet tools first; the one main action, adding an event, sits last and stands out. */}
                <Button
                  variant="ghost"
                  icon={RefreshCw}
                  loading={loading}
                  onClick={() => void reload()}
                  aria-label="Refresh"
                  title="Refresh"
                />
                <Button
                  variant="ghost"
                  icon={Settings}
                  onClick={() => setSettings(true)}
                  aria-label="Calendar settings"
                  title="Calendar settings"
                />
                {openUrl && <ExternalButton href={openUrl}>Open in Nextcloud</ExternalButton>}
                {ready && (
                  <Button variant="primary" icon={Plus} onClick={newEvent}>
                    New event
                  </Button>
                )}
              </>
            }
          />
        )}
      />
      <Dialog open={settings} onClose={() => setSettings(false)} title="Calendar settings" size="sheet">
        <CalendarSettings />
      </Dialog>
    </div>
  );
}
