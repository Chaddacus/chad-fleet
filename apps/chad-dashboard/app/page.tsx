import ChatPanel from './ChatPanel';

export default function HomePage() {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="font-mono text-2xl font-bold tracking-tight text-gray-100">
          Fleet Chat
        </h1>
        <p className="mt-1 text-sm text-gray-400">
          Ask the fleet anything. Responses are rendered as live views.
        </p>
      </div>
      <ChatPanel />
    </div>
  );
}
