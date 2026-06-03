import { ChatFeature } from '@/features/chat/ui';

// The hub's main surface — chat tied to the admiral.
export default function HomePage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="font-mono text-2xl font-bold tracking-tight text-gray-100">Admiral</h1>
        <p className="mt-1 text-sm text-gray-400">
          Command the fleet. The admiral dispatches captains and streams results back here.
        </p>
      </div>
      <ChatFeature />
    </div>
  );
}
