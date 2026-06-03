import { CalendarFeature } from '@/features/calendar/ui';

// Thin shell route — mounts the self-contained features/calendar module.
export default function CalendarPage() {
  return <CalendarFeature />;
}
