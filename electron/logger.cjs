const fs = require('node:fs');
const path = require('node:path');
const { StringDecoder } = require('node:string_decoder');

const RETENTION_DAYS = 30;

function redactLogText(value) {
  // Bare token/secret labels can be business text; quoted fields and query parameters identify credential contexts.
  return String(value)
    .replace(/\x1b\[[0-9;]*m/g, '')
    .replace(/((?:\b(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|(?:client|api)[_-]?secret|password)\b["']?|"(?:token|secret)"|'(?:token|secret)')\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:(?:Bearer|Basic)\s+)?[^\s"',;}&#]+)/gi, '$1[REDACTED]')
    .replace(/\b(?:sk-|rc-)[A-Za-z0-9_-]{16,}/g, '[REDACTED]')
    .replace(/(:\/\/)[^/\s:@]+:[^/\s@]+(?=@)/g, '$1[REDACTED]')
    .replace(/([?&](?:token|secret)=)[^\s"',;}&#]+/gi, '$1[REDACTED]')
    .replace(/(\bBearer\s+)[A-Za-z0-9._~+/-]+=*/gi, '$1[REDACTED]');
}

function createFileLogger(filename, { retentionDays = RETENTION_DAYS, now = () => new Date() } = {}) {
  let initialized = false;
  let failureReported = false;
  const prune = (date) => {
    const cutoff = date.getTime() - retentionDays * 86400000;
    for (const entry of fs.readdirSync(path.dirname(filename))) {
      if (!entry.startsWith(`${path.basename(filename)}.`)) continue;
      const suffix = entry.slice(path.basename(filename).length + 1);
      if (!/^\d{4}-\d{2}-\d{2}$/.test(suffix)) continue;
      const archive = path.join(path.dirname(filename), entry);
      const stat = fs.lstatSync(archive);
      if (stat.isFile() && Date.parse(suffix) < cutoff) fs.unlinkSync(archive);
    }
  };
  let activeDay = null;
  return {
    write(level, message) {
      try {
        const date = now();
        const day = date.toISOString().slice(0, 10);
        if (!initialized) {
          fs.mkdirSync(path.dirname(filename), { recursive: true });
          prune(date);
          activeDay = fs.existsSync(filename) ? fs.statSync(filename).mtime.toISOString().slice(0, 10) : day;
          initialized = true;
        }
        const clean = redactLogText(message);
        const bounded = clean.length > 16000 ? `${clean.slice(0, 8000)}\n[truncated]\n${clean.slice(-8000)}` : clean;
        const line = `${date.toISOString()} ${level} ${bounded}\n`;
        if (activeDay !== day) {
          if (fs.existsSync(filename)) fs.renameSync(filename, `${filename}.${activeDay}`);
          activeDay = day;
          prune(date);
        }
        fs.appendFileSync(filename, line, { encoding: 'utf8', mode: 0o600 });
      } catch (error) {
        // A full/read-only disk must not turn a diagnostic write into an application failure.
        if (!failureReported) {
          failureReported = true;
          console.error(`Application log unavailable: ${redactLogText(error.message)}`);
        }
      }
    }
  };
}

function captureProcessOutput(child, logger, label, onOutput) {
  for (const stream of [child.stdout, child.stderr]) {
    if (!stream) continue;
    const decoder = new StringDecoder('utf8');
    let tail = '';
    const consume = (text) => {
      if (text) onOutput?.(text);
      tail += text;
      const lines = tail.split(/[\r\n]+/);
      tail = lines.pop();
      for (const line of lines) {
        if (line.trim()) logger.write('INFO', `${label} ${line}`);
      }
      if (tail.length > 16000) {
        logger.write('INFO', `${label} ${tail}`);
        tail = '';
      }
    };
    stream.on('data', (chunk) => consume(decoder.write(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk))));
    stream.on('end', () => {
      consume(decoder.end());
      if (tail.trim()) logger.write('INFO', `${label} ${tail}`);
      tail = '';
    });
  }
}

module.exports = { createFileLogger, captureProcessOutput, redactLogText };
