import { SessionsFeature } from '@/features/sessions/ui';

// Thin shell route — all logic lives in the self-contained features/sessions module.
export default function SessionsPage() {
  return <SessionsFeature />;
}
