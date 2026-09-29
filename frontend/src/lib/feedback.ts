import { createContext, useContext } from 'react';

export interface Feedback {
  /** Shows an error in the page banner; silent errors (handled 401s, cancellations) are ignored. */
  showError: (error: unknown) => void;
  clearError: () => void;
  /** Announces a short status message through the polite live region. */
  announce: (message: string) => void;
}

export const FeedbackContext = createContext<Feedback | null>(null);

export function useFeedback(): Feedback {
  const feedback = useContext(FeedbackContext);
  if (!feedback) throw new Error('useFeedback must be used inside FeedbackContext.');
  return feedback;
}
