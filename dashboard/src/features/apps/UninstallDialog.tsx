import { useState } from 'react';
import { postJsonApi, type Service } from '../../api';
import { Button } from '../../components/Button';
import { Dialog } from '../../components/Dialog';
import { useAction } from '../../lib/useAction';
import { useDashboard } from '../../state/dashboard';

/** Uninstall keeps data unless the owner picks deletion and types the app's name. */
export function UninstallDialog({ service, open, onClose }: { service: Service; open: boolean; onClose: () => void }) {
  const { refresh } = useDashboard();
  const { pending, run } = useAction();
  const [deleteData, setDeleteData] = useState(false);
  const [typed, setTyped] = useState('');
  const confirmed = !deleteData || typed.trim().toLowerCase() === service.name.toLowerCase();
  const close = () => {
    setDeleteData(false);
    setTyped('');
    onClose();
  };
  const submit = async () => {
    const action = deleteData ? 'uninstall_delete_data' : 'uninstall';
    const result = await run(
      action,
      () => postJsonApi(`/api/v1/services/${service.id}/actions`, { action, confirm: deleteData ? typed.trim() : '' }),
      `Uninstalling ${service.name}`,
    );
    if (result === undefined) return;
    close();
    void refresh();
  };
  return (
    <Dialog
      open={open}
      onClose={close}
      title={`Uninstall ${service.name}?`}
      description="The app stops, its web address and sign-in are removed, and it disappears from chat."
      footer={
        <>
          <Button onClick={close}>Cancel</Button>
          <Button variant="danger" disabled={!confirmed} loading={Boolean(pending)} onClick={() => void submit()}>
            {deleteData ? 'Uninstall and delete data' : 'Uninstall'}
          </Button>
        </>
      }
    >
      <fieldset className="choice-list">
        <legend className="sr-only">What happens to {service.name}'s data</legend>
        <label className="choice">
          <input type="radio" name="uninstall-data" checked={!deleteData} onChange={() => setDeleteData(false)} />
          <span>
            <strong>Keep my data</strong>
            <small>
              Files, accounts and settings stay on this server. Install the app again to pick up where you left off.
            </small>
          </span>
        </label>
        <label className="choice choice-danger">
          <input type="radio" name="uninstall-data" checked={deleteData} onChange={() => setDeleteData(true)} />
          <span>
            <strong>Delete everything</strong>
            <small>
              Permanently deletes everything stored in {service.name} and frees the disk space. This cannot be undone.
            </small>
          </span>
        </label>
      </fieldset>
      {deleteData && (
        <label className="field">
          <span>
            Type <strong>{service.name}</strong> to confirm
          </span>
          <input autoComplete="off" autoFocus value={typed} onChange={(event) => setTyped(event.target.value)} />
        </label>
      )}
    </Dialog>
  );
}
