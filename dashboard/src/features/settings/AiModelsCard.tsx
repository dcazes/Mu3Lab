import { Cpu, ShieldCheck } from 'lucide-react';
import { type FormEvent, useCallback, useEffect, useState } from 'react';
import {
  type AiModels,
  type AiModelsRequest,
  type JobDetailResponse,
  type JobResponse,
  type LocalModel,
  putJsonApi,
} from '../../api';
import { Button } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { Callout, Card, Collapsible, Facts } from '../../components/Layout';
import { Badge } from '../../components/Status';
import { humanize, relativeTime } from '../../lib/format';
import { useAction } from '../../lib/useAction';
import { useApi } from '../../lib/useApi';

function option(model: LocalModel): string {
  return `${model.name} · ${model.download_gb} GB download, about ${model.memory_gb} GB memory`;
}

/** Where chat runs, in words for the person choosing a model. */
export function whereChatRuns(data: Pick<AiModels, 'settings' | 'chat_models'>): string {
  const local = data.chat_models.find((model) => model.id === data.settings.chat_model);
  if (data.settings.privacy === 'local_only')
    return `Only on this server${local ? `, with ${local.name}` : ''}. Nothing is sent to outside providers.`;
  return local
    ? `Through your outside providers by default. ${local.name} (private) runs on this server and can be chosen in chat.`
    : 'Through your outside providers. No local chat model is chosen.';
}

function JobLog({ id, finished }: { id: string; finished: () => void }) {
  const job = useApi<JobDetailResponse>(`/api/v1/jobs/${id}`, { interval: 3000 });
  const state = job.data?.job.state;
  const running = !state || ['queued', 'running'].includes(state);
  useEffect(() => {
    if (!running) finished();
  }, [running, finished]);
  if (!job.data) return null;
  if (state === 'failed')
    return (
      <Callout tone="danger" title="The AI model settings were not changed">
        {job.data.job.detail}
      </Callout>
    );
  if (!running) return null;
  return (
    <Collapsible title={`Changing AI models: ${humanize(job.data.job.step_id || job.data.job.state)}`}>
      <pre className="log">
        {job.data.events
          .slice(-8)
          .map((event) => event.detail)
          .join('\n')}
      </pre>
    </Collapsible>
  );
}

/** The household switch for outside AI providers, the local chat model and the search model (R31). */
export function AiModelsCard({ readOnly = false }: { readOnly?: boolean }) {
  const { data, error, reload } = useApi<AiModels>('/api/v1/ai/models', { interval: 15000 });
  const confirm = useConfirm();
  const { pending, run } = useAction();
  const [edits, setEdits] = useState<AiModelsRequest | null>(null);
  const [jobId, setJobId] = useState('');
  const [running, setRunning] = useState(false);
  const finished = useCallback(() => {
    setRunning(false);
    void reload();
  }, [reload]);
  if (error)
    return (
      <Card title="Where AI runs">
        <p className="error-text">Could not load: {error}</p>
      </Card>
    );
  if (!data) return null;
  const saved = data.settings;
  const draft = edits ?? {
    privacy: saved.privacy,
    chat_model: saved.chat_model,
    embedding_model: saved.embedding_model,
    runner: data.runner,
  };
  const chosenRunner = draft.runner || data.runner;
  const runnerChanged = chosenRunner !== data.runner;
  const changed =
    draft.privacy !== saved.privacy ||
    draft.chat_model !== saved.chat_model ||
    draft.embedding_model !== saved.embedding_model ||
    runnerChanged;
  const embeddingChanged = draft.embedding_model !== saved.embedding_model;
  const locked = data.embedding_locked_by.length > 0;
  const busy = running || Boolean(data.active_job_id);
  const missingLocal = draft.privacy === 'local_only' && !draft.chat_model;
  const chosenChat = data.chat_models.find((model) => model.id === draft.chat_model);
  const chosenEmbedding = data.embedding_models.find((model) => model.id === draft.embedding_model);
  const runnerName = (id: string) => data.runners.find((runner) => runner.id === id)?.name || 'the model runner';

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const warnings = [
      draft.privacy === 'local_only' && saved.privacy !== 'local_only'
        ? 'Chat will use only the local model. Outside provider keys stay saved but are not used.'
        : '',
      embeddingChanged
        ? 'Document search in every app has to index its documents again with the new search model. Until it does, search may find nothing.'
        : '',
      runnerChanged
        ? `${runnerName(chosenRunner)} downloads the chosen models first; until they work, ${runnerName(data.runner)} keeps answering.`
        : '',
      chosenChat && !chosenChat.downloaded && !runnerChanged
        ? `${chosenChat.name} is downloaded first (${chosenChat.download_gb} GB).`
        : '',
    ].filter(Boolean);
    if (
      warnings.length &&
      !(await confirm({
        title: 'Change where AI runs?',
        description: warnings.join(' '),
        confirmLabel: 'Change',
        tone: embeddingChanged ? 'danger' : 'primary',
      }))
    )
      return;
    const result = await run(
      'models',
      () => putJsonApi<JobResponse>('/api/v1/ai/models', draft),
      'Mu3Lab is downloading and checking the models. Nothing changes until they work.',
    );
    if (result) {
      setEdits(null);
      setJobId(result.job.id);
      setRunning(true);
    }
  };

  return (
    <Card
      title="Where AI runs"
      description="Choose whether chat may use outside AI providers, and which models run on this server. Only models reviewed for Mu3Lab are offered."
    >
      {saved.privacy === 'local_only' ? (
        <Callout tone="success" icon={ShieldCheck} title="Local only">
          {whereChatRuns(data)}
        </Callout>
      ) : (
        <Callout tone="info" icon={Cpu} title="Outside providers allowed">
          {whereChatRuns(data)} Messages sent to an outside provider, and any documents an assistant includes, leave
          this server.
        </Callout>
      )}
      {!data.runner_reachable && (
        <Callout tone="warning" title={`${runnerName(data.runner)} is not answering`}>
          Download states may be out of date until {runnerName(data.runner)} is running again.
        </Callout>
      )}
      <form className="form" onSubmit={(event) => void save(event)}>
        <fieldset className="choice-list" disabled={readOnly || busy}>
          <legend>Where chat may send messages</legend>
          <label className="choice">
            <input
              type="radio"
              name="ai-privacy"
              checked={draft.privacy === 'outside_allowed'}
              onChange={() => setEdits({ ...draft, privacy: 'outside_allowed' })}
            />
            <span>
              <strong>Outside providers allowed</strong>
              <small>Chat uses the providers you connect below; the local model stays a private choice.</small>
            </span>
          </label>
          <label className="choice">
            <input
              type="radio"
              name="ai-privacy"
              checked={draft.privacy === 'local_only'}
              onChange={() => setEdits({ ...draft, privacy: 'local_only' })}
            />
            <span>
              <strong>Local only</strong>
              <small>
                Every chat runs on this server. If the local model stops working, chat stops too: it never falls back to
                an outside provider.
              </small>
            </span>
          </label>
        </fieldset>
        <label className="field">
          <span>Model runner</span>
          <select
            aria-label="Model runner"
            disabled={readOnly || busy}
            value={chosenRunner}
            onChange={(event) => setEdits({ ...draft, runner: event.target.value })}
          >
            {data.runners.map((runner) => (
              <option key={runner.id} value={runner.id} disabled={!runner.installed}>
                {runner.installed ? runner.name : `${runner.name} (install it from Apps first)`}
              </option>
            ))}
          </select>
          <small className="muted">
            {data.runners.find((runner) => runner.id === chosenRunner)?.has_ui
              ? 'Runs the models below. Administrators can also open it to try and fine-tune other models.'
              : 'Runs the models below. It has no web app of its own.'}
          </small>
        </label>
        <label className="field">
          <span>Local chat model</span>
          <select
            aria-label="Local chat model"
            disabled={readOnly || busy}
            value={draft.chat_model}
            onChange={(event) => setEdits({ ...draft, chat_model: event.target.value })}
          >
            <option value="">None</option>
            {data.chat_models.map((model) => (
              <option key={model.id} value={model.id}>
                {option(model)}
              </option>
            ))}
          </select>
          {chosenChat && (
            <small className="muted">
              {chosenChat.note} {chosenChat.downloaded ? <Badge tone="green">Downloaded</Badge> : null}
            </small>
          )}
        </label>
        <label className="field">
          <span>Search model</span>
          <select
            aria-label="Search model"
            disabled={readOnly || busy || locked}
            value={draft.embedding_model}
            onChange={(event) => setEdits({ ...draft, embedding_model: event.target.value })}
          >
            {data.embedding_models.map((model) => (
              <option key={model.id} value={model.id}>
                {option(model)}
              </option>
            ))}
          </select>
          {chosenEmbedding && <small className="muted">{chosenEmbedding.note}</small>}
        </label>
        {locked && (
          <p className="muted">
            {data.embedding_locked_by.join(', ')} keeps a search index built for one search model, so the search model
            cannot change while it is installed.
          </p>
        )}
        {embeddingChanged && (
          <Callout tone="warning" title="Documents need indexing again">
            Apps that search your documents index them again with the new model.
          </Callout>
        )}
        {missingLocal && <p className="error-text">Local only needs a local chat model.</p>}
        {!readOnly && (
          <div className="button-row">
            <Button type="submit" size="sm" loading={pending === 'models'} disabled={!changed || missingLocal || busy}>
              Save and apply
            </Button>
          </div>
        )}
      </form>
      {(jobId || data.active_job_id) && <JobLog id={jobId || data.active_job_id} finished={finished} />}
      <Facts
        items={[
          { label: 'Last changed', value: saved.changed_at ? relativeTime(saved.changed_at) : 'Never' },
          { label: 'Changed by', value: saved.changed_by || '—' },
        ]}
      />
    </Card>
  );
}
