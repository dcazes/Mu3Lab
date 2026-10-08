import { useEffect, useState } from 'react';
import { api, postApi, postJsonApi } from '../../api';
import { Button, LinkButton } from '../../components/Button';
import { useDashboard } from '../../state/dashboard';

type Operation = {
  id: string;
  app: string;
  tool: string;
  server: string;
  connector_revision: string;
  state: string;
  expires_at: number;
  arguments?: Record<string, unknown>;
};
type Reply = { operation: Operation; execution?: { result?: unknown } };

/** Approval is a human decision over the stored request, never model-authored prose. */
export function ToolApprovalPage({ id }: { id: string }) {
  const { data } = useDashboard();
  const [operation, setOperation] = useState<Operation | null>(null);
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<unknown>(null);
  const base = `/api/v1/tool-approvals/${encodeURIComponent(id)}`;
  useEffect(() => {
    if (!data.identity.is_admin) return;
    let stopped = false;
    const load = () =>
      api<Reply>(base)
        .then((reply) => {
          if (!stopped) setOperation(reply.operation);
        })
        .catch((reason) => {
          if (!stopped) setError(reason instanceof Error ? reason.message : 'Approval could not be loaded.');
        });
    void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [base, data.identity.is_admin]);
  const decide = async (decision: 'approve' | 'reject' | 'execute') => {
    setPending(true);
    setError('');
    try {
      const reply =
        decision === 'execute'
          ? await postApi<Reply>(`${base}/execute`)
          : await postJsonApi<Reply>(`${base}/decision`, { decision });
      setOperation(reply.operation);
      setResult(reply.execution?.result || null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The decision could not be confirmed. Refresh its status.');
    } finally {
      setPending(false);
    }
  };
  if (!data.identity.is_admin)
    return (
      <div className="page">
        <h1>Operator access required</h1>
        <p>Shared connector credentials are available only to operators.</p>
      </div>
    );
  const app = data.services.services.find((item) => item.id === operation?.app);
  return (
    <div className="page stack">
      <h1>Review a tool change</h1>
      <p>Approve only if these exact inputs describe the change you want. Approval runs this stored request once.</p>
      {error && <p role="alert">{error}</p>}
      {!operation && !error && <p role="status">Loading approval…</p>}
      {operation && (
        <>
          <h2>
            {app?.name || operation.app}: {operation.tool}
          </h2>
          <p>Connector: {operation.server}. Scope: shared application data, operators only.</p>
          <p role="status">Status: {operation.state}</p>
          {operation.arguments && (
            <pre className="log" aria-label="Exact tool arguments">
              {JSON.stringify(operation.arguments, null, 2)}
            </pre>
          )}
          {operation.state === 'pending' && (
            <div className="button-row">
              <Button variant="primary" loading={pending} disabled={pending} onClick={() => void decide('approve')}>
                Approve and run
              </Button>
              <Button disabled={pending} onClick={() => void decide('reject')}>
                Reject
              </Button>
            </div>
          )}
          {operation.state === 'approved' && (
            <Button disabled={pending} onClick={() => void decide('execute')}>
              Run approved request
            </Button>
          )}
          {operation.state === 'dispatching' && (
            <p>
              The dispatch attempt has started. Refresh this page for its outcome; do not request a duplicate change.
            </p>
          )}
          {operation.state === 'outcome_unknown' && (
            <p>
              The connector may have completed the change. Inspect the app before creating a new request. This operation
              will not be sent again.
            </p>
          )}
          {result !== null && (
            <pre className="log" aria-label="Tool result">
              {JSON.stringify(result, null, 2)}
            </pre>
          )}
        </>
      )}
      <LinkButton to="/chat">Return to chat</LinkButton>
    </div>
  );
}

export function ToolApprovalsPage() {
  const { data } = useDashboard();
  const [operations, setOperations] = useState<Operation[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!data.identity.is_admin) return;
    let stopped = false;
    const load = () =>
      api<{ operations: Operation[] }>('/api/v1/tool-approvals')
        .then((reply) => {
          if (!stopped) setOperations(reply.operations);
        })
        .catch((reason) => {
          if (!stopped) setError(reason instanceof Error ? reason.message : 'Approvals could not be loaded.');
        });
    void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [data.identity.is_admin]);
  if (!data.identity.is_admin)
    return (
      <div className="page">
        <h1>Operator access required</h1>
      </div>
    );
  return (
    <div className="page stack">
      <h1>Tool change requests</h1>
      <p>Review the exact inputs before approving a change to shared application data.</p>
      {error && <p role="alert">{error}</p>}
      {operations.length === 0 && <p>No change requests.</p>}
      {operations.map((operation) => (
        <div className="row" key={operation.id}>
          <span>
            {operation.app}: {operation.tool} — {operation.state}
          </span>
          <LinkButton to={`/tool-approvals/${operation.id}`}>Review</LinkButton>
        </div>
      ))}
      <LinkButton to="/chat">Return to chat</LinkButton>
    </div>
  );
}
