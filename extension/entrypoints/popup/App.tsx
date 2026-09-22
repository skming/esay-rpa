import { BackendAddressRow } from './components/BackendAddressRow';
import { BridgeStatusCard } from './components/BridgeStatusCard';
import { useConnectionStatus } from './hooks/useConnectionStatus';

function App() {
  const connection = useConnectionStatus();
  const connected = connection.status?.connected ?? false;

  return (
    <div className="w-80 overflow-hidden bg-surface text-ink">
      <header className="flex items-center gap-3 border-b border-rule px-4 py-3">
        <img src="/icon/48.png" alt="" className="h-7 w-7 rounded-md" />
        <div className="min-w-0 flex-1">
          <h1 className="text-[13px] font-semibold leading-tight text-ink">Easy RPA</h1>
          <p className="mt-0.5 truncate text-[10px] leading-tight text-ink-3">当前 Chrome 执行器</p>
        </div>
      </header>

      <main className="space-y-3 px-4 py-3.5">
        <BridgeStatusCard connected={connected} phase={connection.phase} />
        {connection.status !== null && (
          <BackendAddressRow backendBaseUrl={connection.status.backendBaseUrl} />
        )}
      </main>
    </div>
  );
}

export default App;
