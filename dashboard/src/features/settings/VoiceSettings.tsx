import { Mic } from 'lucide-react';
import type { AiModels, Service, VoicePage } from '../../api';
import { ExternalButton, LinkButton } from '../../components/Button';
import { Callout, PageHeader } from '../../components/Layout';
import { Dot } from '../../components/Status';
import { JOB_POLL_MS } from '../../lib/jobs';
import { launchTarget, stateTone, statusText } from '../../lib/services';
import { useApi } from '../../lib/useApi';
import { useDashboard, useIsAdmin } from '../../state/dashboard';
import { VoiceAppsCard } from './VoiceAppsCard';
import { VoiceConversationCard } from './VoiceConversationCard';
import { VoiceEngineCard } from './VoiceEngineCard';
import { VoiceKeyCard } from './VoiceKeyCard';
import { voiceSourceLabel, VoiceModelCard } from './VoiceModelCard';
import { VoiceSoundCard } from './VoiceSoundCard';

const VOICE_APP = 'livekit';

/** One line saying how the voice assistant stands now: running, where answers come from, its voice. */
function VoiceStatus({ app, ai, page }: { app?: Service; ai: AiModels | null; page: VoicePage | null }) {
  if (!app || !ai || !page) return null;
  const tone = stateTone(app.display_state);
  const voice = page.voices.find((item) => item.id === (page.settings.voice || page.speech.default_voice));
  const parts =
    ai.voice_engine.engine === 'duplex'
      ? [`Full duplex · ${ai.voice_engine.model}`]
      : [
          voiceSourceLabel(ai),
          voice ? `Voice ${voice.name}` : '',
          page.settings.search && page.search_installed ? 'Web search on' : '',
        ];
  return (
    <p className="status-line">
      <Dot tone={tone} />
      <strong>{statusText(app)}</strong>
      {parts.filter(Boolean).map((part) => (
        <span key={part}>· {part}</span>
      ))}
    </p>
  );
}

export function VoiceSettings() {
  const isAdmin = useIsAdmin();
  const { data: dashboard } = useDashboard();
  // Followed while a settings change runs, so the cards unlock when it ends.
  const ai = useApi<AiModels>('/api/v1/ai/models', {
    interval: (current) => (current?.active_job_id ? JOB_POLL_MS : 0),
  });
  const page = useApi<VoicePage>('/api/v1/ai/voice/settings', {
    interval: (current) => (current?.active_job_id ? JOB_POLL_MS : 0),
  });
  const app = dashboard.services.services.find((service) => service.id === VOICE_APP);
  const talk = app ? launchTarget(app) : null;
  return (
    <>
      <PageHeader
        title="Voice assistant"
        description="Talk to your apps and the web. Choose where its answers come from, how it sounds and listens, and what it can use."
        actions={
          talk && (
            <ExternalButton variant="primary" icon={Mic} href={talk.url}>
              Open Talk
            </ExternalButton>
          )
        }
      />
      <VoiceStatus app={app} ai={ai.data} page={page.data} />
      {page.data && !page.data.installed && (
        <Callout
          tone="info"
          title="LiveKit isn’t installed"
          action={<LinkButton to={`/apps/${VOICE_APP}`}>View LiveKit</LinkButton>}
        >
          The voice assistant runs in LiveKit. You can choose its settings now; they apply once it is installed.
        </Callout>
      )}
      <VoiceModelCard api={ai} readOnly={!isAdmin} />
      <VoiceSoundCard api={page} readOnly={!isAdmin} />
      <VoiceConversationCard api={page} readOnly={!isAdmin} />
      <VoiceAppsCard page={page} readOnly={!isAdmin} />
      <VoiceEngineCard api={ai} readOnly={!isAdmin} />
      <VoiceKeyCard />
    </>
  );
}
