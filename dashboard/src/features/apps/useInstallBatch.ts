import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import {
  api,
  type InstallBatch,
  type InstallBatchJob,
  type InstallBatchResponse,
  postApi,
  postJsonApi,
} from '../../api';
import { useConfirm } from '../../components/Dialog';
import { errorText } from '../../lib/format';
import { useDashboard } from '../../state/dashboard';

const FINISHED = ['succeeded', 'cancelled', 'completed_with_failures', 'reset', 'reset_failed'];
const RESET_DONE = 'Installation reset. Select apps to try again.';

export function useInstallBatch() {
  const confirm = useConfirm();
  const { refresh } = useDashboard();
  const [batch, setBatch] = useState<InstallBatch | null>(null);
  const [job, setJob] = useState<InstallBatchJob | null>(null);
  const [pending, setPending] = useState(false);

  const apply = useCallback((result: InstallBatchResponse) => {
    if (result.batch?.state === 'reset') {
      setBatch(null);
      setJob(null);
      toast.success(RESET_DONE);
      return;
    }
    setBatch(result.batch);
    setJob(result.current_job || null);
  }, []);

  useEffect(() => {
    api<InstallBatchResponse>('/api/v1/service-install-batches')
      .then(apply)
      .catch(() => undefined);
  }, [apply]);

  const batchId = batch?.id;
  const batchState = batch?.state;
  useEffect(() => {
    if (!batchId || !batchState || FINISHED.includes(batchState)) return;
    const timer = window.setInterval(() => {
      api<InstallBatchResponse>(`/api/v1/service-install-batches/${batchId}`)
        .then(apply)
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [batchId, batchState, apply]);

  const install = async (serviceIds: string[], names: string[]) => {
    if (
      !(await confirm({
        title: `Install ${serviceIds.length} app${serviceIds.length === 1 ? '' : 's'}?`,
        description: `${names.join(', ')} will be installed one at a time, along with anything they depend on.`,
        confirmLabel: 'Install',
      }))
    )
      return false;
    setPending(true);
    try {
      const result = await postJsonApi<{ batch: InstallBatch }>('/api/v1/services/install-batch', {
        service_ids: serviceIds,
      });
      setBatch(result.batch);
      setJob(null);
      toast.success('Installation started');
      void refresh();
      return true;
    } catch (error) {
      toast.error(errorText(error));
      return false;
    } finally {
      setPending(false);
    }
  };

  const act = async (action: 'cancel' | 'reset') => {
    if (!batch) return;
    if (
      action === 'reset' &&
      !(await confirm({
        title: 'Reset the failed installation?',
        description:
          'Failed containers and temporary setup files are cleaned up in the background. App data, accounts, and databases are kept, and nothing is downloaded again.',
        confirmLabel: 'Reset',
      }))
    )
      return;
    try {
      const result = await postApi<InstallBatchResponse & { message?: string }>(
        `/api/v1/service-install-batches/${batch.id}/${action}`,
      );
      apply(result);
      if (action === 'reset' && result.batch?.state !== 'reset') toast.info(result.message || 'Cleanup started.');
    } catch (error) {
      // The reset may have completed even though its response was lost.
      if (action === 'reset') {
        try {
          const refreshed = await api<InstallBatchResponse>('/api/v1/service-install-batches');
          if (!refreshed.batch) {
            setBatch(null);
            setJob(null);
            toast.success(RESET_DONE);
            return;
          }
          apply(refreshed);
          return;
        } catch {
          /* report the original failure */
        }
      }
      toast.error(errorText(error));
    }
    void refresh();
  };

  return { batch, job, pending, install, act, dismiss: () => setBatch(null) };
}
