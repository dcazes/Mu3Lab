import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToolApprovalPage } from './ToolApprovalPage';

const mocks = vi.hoisted(() => ({ api: vi.fn(), postJsonApi: vi.fn(), postApi: vi.fn(), admin: true }));
vi.mock('../../api', () => mocks);
vi.mock('../../state/dashboard', () => ({
  useDashboard: () => ({
    data: { identity: { is_admin: mocks.admin }, services: { services: [{ id: 'demo', name: 'Demo' }] } },
  }),
}));
const operation = {
  id: 'a'.repeat(32),
  app: 'demo',
  tool: 'rename',
  server: 'demo',
  connector_revision: '1',
  state: 'pending',
  expires_at: Date.now() / 1000 + 300,
  arguments: { name: '<script>private</script>' },
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
beforeEach(() => {
  mocks.admin = true;
  mocks.api.mockResolvedValue({ operation });
  mocks.postJsonApi.mockResolvedValue({ operation: { ...operation, state: 'succeeded' } });
});

describe('human tool approval', () => {
  it('shows exact stored inputs and sends only the decision and operation ID', async () => {
    render(<ToolApprovalPage id={operation.id} />);
    expect(await screen.findByLabelText('Exact tool arguments')).toHaveTextContent('<script>private</script>');
    expect(document.querySelector('script')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Approve and run' }));
    await waitFor(() =>
      expect(mocks.postJsonApi).toHaveBeenCalledWith(`/api/v1/tool-approvals/${operation.id}/decision`, {
        decision: 'approve',
      }),
    );
    expect(await screen.findByText('Status: succeeded')).toBeInTheDocument();
  });
  it('rejects without submitting a dispatch or mutable inputs', async () => {
    render(<ToolApprovalPage id={operation.id} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Reject' }));
    await waitFor(() =>
      expect(mocks.postJsonApi).toHaveBeenCalledWith(`/api/v1/tool-approvals/${operation.id}/decision`, {
        decision: 'reject',
      }),
    );
    expect(mocks.postApi).not.toHaveBeenCalled();
  });
  it('offers no replay for an unknown outcome', async () => {
    mocks.api.mockResolvedValue({ operation: { ...operation, state: 'outcome_unknown', arguments: undefined } });
    render(<ToolApprovalPage id={operation.id} />);
    expect(await screen.findByText(/connector may have completed/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Approve and run' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Run approved request' })).not.toBeInTheDocument();
  });
  it('members cannot review shared connector inputs', () => {
    mocks.admin = false;
    render(<ToolApprovalPage id={operation.id} />);
    expect(screen.getByText('Operator access required')).toBeInTheDocument();
    expect(screen.queryByLabelText('Exact tool arguments')).not.toBeInTheDocument();
  });
});
