import type { AiModels } from '../../api';
import { LinkButton } from '../../components/Button';
import { Callout, Card } from '../../components/Layout';
import type { ApiState } from '../../lib/useApi';

/**
 * Where the voice assistant's answers come from, in words for the person. Voice always follows chat's
 * setting and model (Settings, AI), so the model runner never holds two chat models at once.
 */
export function whereVoiceRuns(data: Pick<AiModels, 'settings' | 'chat_models'>): string {
  const local = data.chat_models.find((model) => model.id === data.settings.chat_model);
  const name = local ? local.name : 'the local chat model';
  const [first, second] = data.settings.sources;
  if (first === 'local' && !second) return `Answers come from ${name}, only on this server.`;
  if (first === 'local')
    return `Answers come from ${name} on this server. If it is slow or stopped, your outside providers answer instead, so the text of what you say can leave this server.`;
  return 'Answers come from your outside providers. The text of what you say leaves this server.';
}

/** The answer source in two or three words, for the page's status line. */
export function voiceSourceLabel(data: Pick<AiModels, 'settings' | 'chat_models'>): string {
  const local = data.chat_models.find((model) => model.id === data.settings.chat_model);
  const [first, second] = data.settings.sources;
  if (first !== 'local') return 'Outside providers';
  const where = second ? 'Local first' : 'Local only';
  return local ? `${where} · ${local.name}` : where;
}

/** The voice assistant's answers: the same as chat's, changed in Settings, AI. */
export function VoiceModelCard({ api }: { api: ApiState<AiModels> }) {
  const { data, error } = api;
  const title = 'Answers';
  const description =
    'The voice assistant answers with the same model as chat. Your speech is always turned into text, and replies read aloud, on this server.';
  if (!data)
    return (
      <Card title={title} description={description}>
        {error ? <p className="error-text">Could not load: {error}</p> : <p className="muted card-loading">Loading…</p>}
      </Card>
    );
  const local = data.settings.sources[0] === 'local';
  return (
    <Card
      title={title}
      description={description}
      actions={<LinkButton to="/settings/ai">Open Settings, AI</LinkButton>}
    >
      <p className="model-where">{whereVoiceRuns(data)}</p>
      {local && (data.runner_stopped || !data.runner_reachable) && (
        <Callout
          tone="info"
          title={data.runner_stopped ? 'The model runner is stopped' : 'The model runner is not answering'}
        >
          The local model answers the voice assistant only while it runs: start it from Home.
        </Callout>
      )}
    </Card>
  );
}
