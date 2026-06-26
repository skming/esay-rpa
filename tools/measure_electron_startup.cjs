#!/usr/bin/env node

const { spawn } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

function parseArgs(argv) {
  const args = {
    appPath: 'release/mac-arm64/Easy RPA.app/Contents/MacOS/Easy RPA',
    runs: 5,
    timeoutMs: 10000,
    maxAverageMs: 3000,
    output: 'output/bench/electron-startup.json',
    enforce: true
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--app-path') {
      args.appPath = argv[++index];
    } else if (arg === '--runs') {
      args.runs = Number(argv[++index]);
    } else if (arg === '--timeout-ms') {
      args.timeoutMs = Number(argv[++index]);
    } else if (arg === '--max-average-ms') {
      args.maxAverageMs = Number(argv[++index]);
    } else if (arg === '--output') {
      args.output = argv[++index];
    } else if (arg === '--no-enforce') {
      args.enforce = false;
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  if (!Number.isInteger(args.runs) || args.runs < 1) {
    throw new Error('--runs 必须是大于等于 1 的整数');
  }
  if (!Number.isFinite(args.timeoutMs) || args.timeoutMs <= 0) {
    throw new Error('--timeout-ms 必须大于 0');
  }
  if (!Number.isFinite(args.maxAverageMs) || args.maxAverageMs <= 0) {
    throw new Error('--max-average-ms 必须大于 0');
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const appPath = path.resolve(args.appPath);
  if (!fs.existsSync(appPath)) {
    throw new Error(`应用不存在: ${appPath}`);
  }

  const samples = [];
  for (let run = 1; run <= args.runs; run += 1) {
    samples.push(await measureOnce({ appPath, timeoutMs: args.timeoutMs, run }));
  }

  const startupValues = samples.map((sample) => sample.startupMs);
  const averageMs = round(mean(startupValues));
  const result = {
    appPath,
    runs: args.runs,
    averageMs,
    p95Ms: round(percentile(startupValues, 95)),
    minMs: Math.min(...startupValues),
    maxMs: Math.max(...startupValues),
    maxAverageMs: args.maxAverageMs,
    passed: averageMs <= args.maxAverageMs,
    measuredAt: new Date().toISOString(),
    samples
  };

  const outputPath = path.resolve(args.output);
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
  console.log(JSON.stringify(result, null, 2));

  if (args.enforce && !result.passed) {
    process.exitCode = 1;
  }
}

function measureOnce({ appPath, timeoutMs, run }) {
  return new Promise((resolve, reject) => {
    const startedAt = Date.now();
    const child = spawn(appPath, [], {
      cwd: process.cwd(),
      env: {
        ...process.env,
        RPA_BACKEND_URL: 'http://127.0.0.1:65535',
        RPA_STARTUP_PROBE: '1',
        RPA_STARTUP_STARTED_AT: String(startedAt)
      },
      stdio: ['ignore', 'pipe', 'pipe']
    });

    let output = '';
    const timer = setTimeout(() => {
      child.kill('SIGTERM');
      reject(new Error(`第 ${run} 次冷启动超过 ${timeoutMs}ms\n${output.trim()}`));
    }, timeoutMs);

    const handleChunk = (chunk) => {
      output += chunk.toString();
      for (const line of output.split(/\r?\n/)) {
        if (!line.includes('RPA_STARTUP_READY')) {
          continue;
        }
        try {
          const parsed = JSON.parse(line);
          clearTimeout(timer);
          child.kill('SIGTERM');
          resolve({
            run,
            startupMs: parsed.startupMs,
            readyAt: parsed.readyAt,
            packaged: parsed.packaged,
            platform: parsed.platform,
            arch: parsed.arch
          });
        } catch (error) {
          clearTimeout(timer);
          child.kill('SIGTERM');
          reject(error);
        }
      }
    };

    child.stdout.on('data', handleChunk);
    child.stderr.on('data', handleChunk);
    child.on('error', (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on('exit', (code, signal) => {
      if (signal === 'SIGTERM') {
        return;
      }
      clearTimeout(timer);
      reject(new Error(`第 ${run} 次冷启动进程提前退出 code=${code} signal=${signal}\n${output.trim()}`));
    });
  });
}

function mean(values) {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function percentile(values, percentileValue) {
  const ordered = [...values].sort((a, b) => a - b);
  const index = Math.min(Math.max(Math.round((percentileValue / 100) * (ordered.length - 1)), 0), ordered.length - 1);
  return ordered[index];
}

function round(value) {
  return Math.round(value * 100) / 100;
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
