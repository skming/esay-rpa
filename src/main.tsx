import '@xyflow/react/dist/style.css';
import './styles.css';

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import App from './App';
import { ErrorBoundary } from './components/layout/ErrorBoundary';

const root = document.getElementById('root');

if (root === null) {
  throw new Error('Root element #root is missing.');
}

createRoot(root).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>
);
