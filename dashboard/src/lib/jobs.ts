/** A job that has not finished yet: the page follows it until it does. */
export const jobActive = (state: string | undefined) =>
  !state || ['queued', 'running', 'waiting_for_confirmation'].includes(state);

/** How often to follow a job that is still running. */
export const JOB_POLL_MS = 3000;
