import { useCallback, useState } from 'react';
import { toast } from 'sonner';
import { errorText } from './format';

/**
 * Run one mutation at a time per component, reporting the outcome as a toast.
 * Returns the result, or undefined when the action failed.
 */
export function useAction() {
  const [pending, setPending] = useState('');
  const run = useCallback(
    async <T>(key: string, action: () => Promise<T>, success?: string | ((result: T) => string)) => {
      setPending(key);
      try {
        const result = await action();
        const message = typeof success === 'function' ? success(result) : success;
        if (message) toast.success(message);
        return result;
      } catch (error) {
        toast.error(errorText(error));
        return undefined;
      } finally {
        setPending('');
      }
    },
    [],
  );
  return { pending, run };
}
