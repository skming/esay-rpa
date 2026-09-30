import { useId, type ReactElement } from 'react';

import { cn } from '../../lib/utils';

export function AiAssistantMark({ busy }: { busy: boolean }): ReactElement {
  const maskId = useId().replaceAll(':', '');
  const shadeId = `${maskId}-shade`;

  return (
    <svg
      aria-hidden="true"
      className={cn('ai-assistant-blob h-full w-full overflow-visible', busy && 'ai-assistant-blob-busy')}
      fill="none"
      focusable="false"
      viewBox="3 3 26 26"
    >
      <defs>
        <linearGradient gradientUnits="userSpaceOnUse" id={shadeId} x1="7" x2="25" y1="5" y2="28">
          <stop offset="0" stopColor="white" stopOpacity="0.42" />
          <stop offset="0.46" stopColor="white" stopOpacity="0.06" />
          <stop offset="1" stopColor="#172554" stopOpacity="0.26" />
        </linearGradient>
        <mask height="32" id={maskId} maskUnits="userSpaceOnUse" width="32" x="0" y="0">
          <rect fill="white" height="32" width="32" />
          <g className="ai-assistant-blob-gaze">
            <g className="ai-assistant-blob-sleepy-eyes">
              <g className="ai-assistant-blob-blink">
                <path
                  d="M8.64 15.04c1.48 1.02 2.93 1.03 4.36.03"
                  stroke="black"
                  strokeLinecap="round"
                  strokeWidth="1.85"
                />
                <path
                  d="M19.01 15.08c1.44.98 2.87.97 4.29-.05"
                  stroke="black"
                  strokeLinecap="round"
                  strokeWidth="1.85"
                />
              </g>
            </g>
            <g className="ai-assistant-blob-wide-eyes">
              <g className="ai-assistant-blob-blink" fill="black">
                <ellipse cx="10.92" cy="14.67" rx="1.62" ry="3.03" />
                <ellipse cx="21.11" cy="14.35" rx="1.62" ry="3.03" />
              </g>
            </g>
          </g>
        </mask>
      </defs>
      <g className="ai-assistant-blob-body" mask={`url(#${maskId})`}>
        <path
          d="M16.24 3.52c6.8-.16 11.85 4.98 11.98 11.86.14 7.11-4.64 12.58-11.67 12.91-7.12.34-12.31-4.25-12.75-11.52C3.37 9.7 8.86 3.7 16.24 3.52Z"
          fill="currentColor"
        />
        <path
          d="M16.24 3.52c6.8-.16 11.85 4.98 11.98 11.86.14 7.11-4.64 12.58-11.67 12.91-7.12.34-12.31-4.25-12.75-11.52C3.37 9.7 8.86 3.7 16.24 3.52Z"
          fill={`url(#${shadeId})`}
        />
      </g>
    </svg>
  );
}
