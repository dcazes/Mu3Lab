import { type FormEvent, useCallback, useEffect, useState } from 'react';
import {
  type AiModels,
  type AiModelsRequest,
  type AiTryResult,
  type JobDetailResponse,
  type JobResponse,
  type LocalModel,
  postJsonApi,
  putJsonApi,
} from '../../api';
import { Button } from '../../components/Button';
import { useConfirm } from '../../components/Dialog';
import { Callout, Card, Collapsible } from '../../components/Layout';
import { Badge } from '../../components/Status';
import { DownloadStatus } from '../apps/InstallProgress';
import { humanize } from '../../lib/format';
import { useAction } from '../../lib/useAction';
import { type ApiState, useApi } from '../../lib/useApi';
import { JOB_POLL_MS, jobActive } from '../../lib/jobs';

export function option(model: LocalModel): string {
  return `${model.name} · ${model.download_gb} GB download, about ${model.memory_gb} GB memory`;
}

type Source = AiModelsRequest['sources'][number];

const SOURCES: Record<Source, { title: string; body: string }> = {
  local: {
    title: 'Local models',
    body: 'Private: answers on this server with the chat model chosen below. Nothing you send leaves it.',
  },
  outside: {
    title: 'Outside providers',
    body: 'The providers you connect below. Messages, and any documents an assistant includes, leave this server.',
  },
};

/** Where chat runs, in words for the person choosing a model. */
export function whereChatRuns(data: Pick<AiModels, 'settings' | 'chat_models'>): string {
  const local = data.chat_models.find((model) => model.id === data.settings.chat_model);
  const [first, second] = data.settings.sources;
  if (first === 'local' && !second)
    return `Chat runs only on this server${local ? `, with ${local.name}` : ''}. Chat sends nothing to outside providers.`;
  if (first === 'local')
    return `Chat runs on this server with ${local?.name ?? 'the local model'} by default. Outside providers can be chosen in chat for harder questions.`;
  return second && local
    ? `Through your outside providers by default. ${local.name} (private) runs on this server and can be chosen in chat.`
    : 'Through your outside providers. Local models are off.';
}

const FIT: Record<string, { label: string; tone: 'green' | 'amber' | 'gray' }> = {
  good: { label: 'Runs well here', tone: 'green' },
  slow: { label: 'Slow here', tone: 'amber' },
  too_big: { label: 'Too big for this computer now', tone: 'gray' },
};

function fitText(model: LocalModel): string {
  if (model.fit === 'good') return model.fit_where === 'gpu' ? 'Runs on the graphics card.' : 'Runs on the processor.';
  if (model.fit === 'slow') return 'Fits in memory but runs on the processor, so answers come slowly.';
  if (model.fit === 'too_big') return 'Needs more memory than is free beside your other apps.';
  return '';
}

/** A test answer from one model, or the outside providers, shown under it. */
function TryResult({ result }: { result: AiTryResult }) {
  return (
    <div className="model-try" aria-live="polite">
      <p>{result.answer || 'No answer came back.'}</p>
      <small className="muted">
        {result.name} on {result.where}: {result.seconds} s
        {result.loading_seconds ? ` (${result.loading_seconds} s to load it)` : ''}
        {result.tokens_per_second ? `, about ${result.tokens_per_second} tokens a second` : ''}.
      </small>
    </div>
  );
}

/**
 * A settings change's progress. ``finished`` runs when it ends, and also when its progress cannot be read:
 * the card then goes by the server's own active job (``active_job_id``), so it never stays locked.
 */
export function JobLog({ id, finished }: { id: string; finished: (lost: boolean) => void }) {
  const job = useApi<JobDetailResponse>(`/api/v1/jobs/${id}`, {
    interval: (data) => (jobActive(data?.job.state) ? JOB_POLL_MS : 0),
  });
  const state = job.data?.job.state;
  const running = !state || ['queued', 'running'].includes(state);
  const lost = Boolean(job.error);
  useEffect(() => {
    if (!running || lost) finished(lost);
  }, [running, lost, finished]);
  if (lost && running)
    return (
      <Callout tone="warning" title="Progress could not be shown">
        {job.error} The change goes on; this card unlocks when it finishes.
      </Callout>
    );
  if (!job.data) return null;
  if (state === 'failed')
    return (
      <Callout tone="danger" title="Nothing was changed">
        {job.data.job.detail}
      </Callout>
    );
  if (!running) return null;
  const download = job.data.download;
  // The bar shows how far a download has got, so the line above it names the step, not the percentage.
  const latest = job.data.events
    .map((event) => event.detail)
    .filter((detail) => !(download?.state === 'downloading' && /: \d+% of /.test(detail)))
    .slice(-1)[0];
  return (
    <div className="model-job" aria-live="polite">
      <p className="model-job-step">{latest || humanize(job.data.job.step_id || job.data.job.state)}</p>
      {/* A model download shows the same bar as an app download: bytes, speed and time left. */}
      {download && download.state === 'downloading' && <DownloadStatus download={download} />}
      <Collapsible title="Live activity">
        <pre className="log">
          {job.data.events
            .slice(-8)
            .map((event) => event.detail)
            .join('\n')}
        </pre>
      </Collapsible>
    </div>
  );
}

/** The household switch for outside AI providers, the local chat model and the search model (R31). */
export function AiModelsCard({ models, readOnly = false }: { models: ApiState<AiModels>; readOnly?: boolean }) {
  const { data, error, reload } = models;
  const confirm = useConfirm();
  const { pending, run } = useAction();
  const [edits, setEdits] = useState<AiModelsRequest | null>(null);
  const [jobId, setJobId] = useState('');
  const [running, setRunning] = useState(false);
  const [tried, setTried] = useState<Record<string, AiTryResult>>({});
  const finished = useCallback(
    (lost: boolean) => {
      setRunning(false);
      // Unread progress: the server's active job (polled below) keeps the card locked while it runs.
      if (lost) setJobId('');
      void reload();
    },
    [reload],
  );
  const title = 'Where AI runs';
  const description =
    'Choose where chat runs and which models run on this server. Only models reviewed for Mu3Lab are offered, each marked by how well it fits beside your other apps.';
  if (!data)
    return (
      <Card title={title} description={description}>
        {error ? (
          <p className="error-text">Could not load: {error}</p>
        ) : (
          <p className="muted card-loading card-loading-tall">Loading…</p>
        )}
      </Card>
    );
  const saved = data.settings;
  const draft = edits ?? {
    sources: saved.sources,
    chat_model: saved.chat_model,
    embedding_model: saved.embedding_model,
    runner: data.runner,
  };
  const chosenRunner = draft.runner || data.runner;
  const runnerChanged = chosenRunner !== data.runner;
  const changed =
    draft.sources.join() !== saved.sources.join() ||
    draft.chat_model !== saved.chat_model ||
    draft.embedding_model !== saved.embedding_model ||
    runnerChanged;
  const embeddingChanged = draft.embedding_model !== saved.embedding_model;
  const locked = data.embedding_locked_by.length > 0;
  const busy = running || Boolean(data.active_job_id);
  const localOn = draft.sources.includes('local');
  const missingLocal = localOn && !draft.chat_model;
  // Turned-on sources in their order, then the ones turned off.
  const rows = [
    ...draft.sources,
    ...(['local', 'outside'] as const).filter((source) => !draft.sources.includes(source)),
  ];
  const toggle = (source: Source) =>
    setEdits({
      ...draft,
      sources: draft.sources.includes(source)
        ? draft.sources.filter((other) => other !== source)
        : [...draft.sources, source],
      chat_model: source === 'local' ? draft.chat_model || data.suggested : draft.chat_model,
    });
  const suggested = data.chat_models.find((model) => model.id === data.suggested);
  const tryIt = async (target: string) => {
    const result = await run(`try-${target}`, () =>
      postJsonApi<AiTryResult>('/api/v1/ai/models/try', { target, prompt: '' }),
    );
    if (result) setTried((current) => ({ ...current, [target]: result }));
  };
  const remove = async (model: LocalModel) => {
    if (
      !(await confirm({
        title: `Remove ${model.name}?`,
        description: `This frees ${model.download_gb} GB of disk space. Choosing it again downloads it again.`,
        confirmLabel: 'Remove',
        tone: 'danger',
      }))
    )
      return;
    if (
      await run(
        `remove-${model.id}`,
        () => postJsonApi(`/api/v1/ai/models/${model.id}/remove`, {}),
        `${model.name} removed`,
      )
    )
      void reload();
  };
  const chosenChat = data.chat_models.find((model) => model.id === draft.chat_model);
  const chosenEmbedding = data.embedding_models.find((model) => model.id === draft.embedding_model);
  const runnerName = (id: string) => data.runners.find((runner) => runner.id === id)?.name || 'the model runner';

  const save = async (event: FormEvent) => {
    event.preventDefault();
    const outsideOn = draft.sources.includes('outside');
    const warnings = [
      draft.sources[0] === 'local' && saved.sources[0] !== 'local' && chosenChat && outsideOn
        ? `Chat will answer on this server with ${chosenChat.name}. Outside providers stay a choice in chat.`
        : '',
      !outsideOn && saved.sources.includes('outside')
        ? data.voice.matches_chat || data.voice.privacy === 'local_only'
          ? 'Chat will use only the local model. Outside provider keys stay saved but are not used.'
          : 'Chat will use only the local model. The voice assistant keeps its own setting and still uses outside providers.'
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
      // Voice reads chat's setting until it has its own, so say which way it goes.
      draft.sources.join() !== saved.sources.join() || draft.chat_model !== saved.chat_model
        ? data.voice.matches_chat
          ? 'The voice assistant follows this setting too.'
          : 'The voice assistant keeps its own setting (Settings, Voice Assistant).'
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
    <Card title={title} description={description}>
      {/* The selected option below says the rest; this line adds which model runs where. */}
      <p className="model-where">{whereChatRuns(data)}</p>
      {/* What fits this computer beside the other apps, while local models are off. */}
      {!saved.sources.includes('local') && data.suggestion_reason && (
        <Callout
          tone="info"
          title={suggested ? `Suggested for this computer: ${suggested.name}` : 'Suggested: outside providers'}
        >
          {data.suggestion_reason}
        </Callout>
      )}
      {!data.runner_reachable && (
        <Callout tone="warning" title={`${runnerName(data.runner)} is not answering`}>
          Download states may be out of date until {runnerName(data.runner)} is running again.
        </Callout>
      )}
      <form className="form" onSubmit={(event) => void save(event)}>
        <fieldset className="choice-list" disabled={readOnly || busy}>
          <legend>Where chat runs</legend>
          {rows.map((source, index) => {
            const on = draft.sources.includes(source);
            return (
              <div key={source} className="source-row">
                <label className="choice">
                  <input
                    type="checkbox"
                    checked={on}
                    // At least one stays on: chat needs somewhere to run.
                    disabled={on && draft.sources.length === 1}
                    onChange={() => toggle(source)}
                  />
                  <span>
                    <strong>
                      {SOURCES[source].title}
                      {on && draft.sources.length > 1 && (
                        <Badge tone={index === 0 ? 'blue' : 'gray'}>
                          {index === 0 ? 'First' : 'Second, a choice in chat'}
                        </Badge>
                      )}
                    </strong>
                    <small>{SOURCES[source].body}</small>
                  </span>
                </label>
                {on && index === 1 && !readOnly && (
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={`Put ${SOURCES[source].title.toLowerCase()} first`}
                    onClick={() => setEdits({ ...draft, sources: [...draft.sources].reverse() })}
                  >
                    Move up
                  </Button>
                )}
              </div>
            );
          })}
          <small className="muted">
            {draft.sources.length > 1
              ? 'The first answers in every app. The second is a choice in the chat model menu; it never answers on its own when the first fails.'
              : draft.sources[0] === 'local'
                ? 'If the local model stops working, chat stops too: it never falls back to an outside provider. The voice assistant has its own setting.'
                : 'Turn on local models too to offer a private choice in chat.'}
          </small>
          {localOn && draft.sources[0] === 'outside' && (
            <Callout tone="info" title="Local models are second">
              Chat{data.voice.matches_chat ? ' and the voice assistant' : ''} still answer through outside providers by
              default. Move local models up to answer on this server first.
            </Callout>
          )}
        </fieldset>
        {/* The runner and the local chat model matter only while chat stays on this server. */}
        {/* The runner is only worth a choice when more than one exists. */}
        {data.runners.length > 1 && (
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
            <small className="muted">Runs the models below.</small>
          </label>
        )}
        <fieldset className="model-list" disabled={readOnly || busy}>
          <legend>Local chat model</legend>
          {!localOn && <small className="muted">Used when local models are on.</small>}
          {data.chat_models.map((model) => {
            const fit = FIT[model.fit || ''];
            return (
              <div key={model.id} className="model-entry">
                <label className="model-row">
                  <input
                    type="radio"
                    name="chat-model"
                    checked={draft.chat_model === model.id}
                    onChange={() => setEdits({ ...draft, chat_model: model.id })}
                  />
                  <span className="model-row-text">
                    <strong>
                      {model.name}
                      {model.id === data.suggested && <Badge tone="blue">Suggested</Badge>}
                      {fit && <Badge tone={fit.tone}>{fit.label}</Badge>}
                      {model.downloaded && <Badge>Downloaded</Badge>}
                    </strong>
                    <small>
                      {model.note} {fitText(model)} {model.download_gb} GB download, about {model.memory_gb} GB memory.
                    </small>
                  </span>
                  {model.downloaded && !readOnly && (
                    <span className="model-row-actions">
                      <Button
                        size="sm"
                        variant="ghost"
                        loading={pending === `try-${model.id}`}
                        aria-label={`Try ${model.name}`}
                        onClick={() => void tryIt(model.id)}
                      >
                        Try it
                      </Button>
                      {!model.in_use && (
                        <Button
                          size="sm"
                          variant="ghost"
                          loading={pending === `remove-${model.id}`}
                          aria-label={`Remove ${model.name}`}
                          onClick={() => void remove(model)}
                        >
                          Remove
                        </Button>
                      )}
                    </span>
                  )}
                </label>
                {tried[model.id] && <TryResult result={tried[model.id]} />}
              </div>
            );
          })}
          <small className="muted">
            Downloaded models stay on this server, so switching back is instant. Try it sends one test question.
          </small>
        </fieldset>
        {saved.sources.includes('outside') && !readOnly && (
          <div className="model-entry">
            <Button size="sm" variant="ghost" loading={pending === 'try-outside'} onClick={() => void tryIt('outside')}>
              Try your outside providers
            </Button>
            {tried.outside && <TryResult result={tried.outside} />}
          </div>
        )}
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
        {missingLocal && <p className="error-text">Choose a local chat model to run chat on this server.</p>}
        {!readOnly && (
          <div className="button-row">
            <Button type="submit" size="sm" loading={pending === 'models'} disabled={!changed || missingLocal || busy}>
              Save and apply
            </Button>
            {changed && !busy && <small className="muted">Not saved yet: nothing changes until you save.</small>}
          </div>
        )}
      </form>
      {(jobId || data.active_job_id) && <JobLog id={jobId || data.active_job_id} finished={finished} />}
    </Card>
  );
}
