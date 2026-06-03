import { EmailFeature } from '@/features/email/ui';

// Thin shell route — mounts the self-contained features/email module.
export default function EmailPage() {
  return <EmailFeature />;
}
