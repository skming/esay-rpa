import { BackendAddressRow } from './components/BackendAddressRow';
import { BridgeStatusCard } from './components/BridgeStatusCard';
import { useConnectionStatus } from './hooks/useConnectionStatus';
import { getBackendBaseUrlLabel } from './lib/connection';

function App() {
  const status = useConnectionStatus();
  const connected = status?.connected ?? false;
  const backendBaseUrl = getBackendBaseUrlLabel(status);

  return (
    <div
      className={[
        'relative isolate w-80 overflow-hidden bg-[linear-gradient(180deg,rgba(248,250,252,0.92),rgba(255,255,255,0.98)_42%),var(--color-surface)] text-ink',
        'before:pointer-events-none before:absolute before:inset-0 before:z-0 before:border before:border-transparent before:opacity-0',
        connected
          ? 'before:border-live-line before:opacity-100 before:shadow-[inset_0_0_0_1px_rgba(59,130,246,0.08),0_0_0_1px_rgba(59,130,246,0.18),0_0_22px_rgba(59,130,246,0.28)] before:animate-[bridge-shell-breathe_2.6s_ease-in-out_infinite] motion-reduce:before:animate-none'
          : '',
      ].join(' ')}
    >
      <header className="relative z-10 flex items-center gap-3 border-b border-rule px-4 py-3">
        <img src="/icon/48.png" alt="" className="h-8 w-8 rounded-md shadow-xs" />
        <div className="min-w-0 flex-1">
          <h1 className="text-sm font-semibold leading-tight text-ink">Easy RPA Bridge</h1>
          <p className="truncate text-xs leading-tight text-ink-3">浏览器扩展执行器</p>
        </div>
        <span
          aria-hidden="true"
          className={[
            'relative h-2 w-2 shrink-0 rounded-full',
            connected
              ? 'bg-live shadow-running after:absolute after:-inset-1.25 after:rounded-full after:border-[1.5px] after:border-live after:animate-[bridge-live-ping_1.9s_cubic-bezier(0.16,1,0.3,1)_infinite] motion-reduce:after:animate-none'
              : 'bg-ink-4',
          ].join(' ')}
        />
      </header>

      <main className="relative z-10 space-y-3 px-4 py-4">
        <BridgeStatusCard connected={connected} />
        <BackendAddressRow backendBaseUrl={backendBaseUrl} />
      </main>
    </div>
  );
}

export default App;
