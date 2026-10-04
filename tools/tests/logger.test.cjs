const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { PassThrough } = require('node:stream');
const { createFileLogger, captureProcessOutput, redactLogText } = require('../../electron/logger.cjs');

test('redaction removes quoted passphrases and URL credentials', () => {
  assert.equal(redactLogText('password="a long passphrase"'), 'password=[REDACTED]');
  assert.equal(redactLogText('https://user:credential@provider.test/?token=credential&region=cn'), 'https://[REDACTED]@provider.test/?token=[REDACTED]&region=cn');
});

test('redaction distinguishes credentials from business text', () => {
  const cases = [
    ['token=123 secret=公开标签 prompt_tokens=200', 'token=123 secret=公开标签 prompt_tokens=200'],
    ['token: count secret: label password_strength=strong', 'token: count secret: label password_strength=strong'],
    ['{"token": "sample-credential", "secret": "sample-credential"}', '{"token": [REDACTED], "secret": [REDACTED]}'],
    ["{'token': 'sample-credential'}", "{'token': [REDACTED]}"],
    ['clientSecret=sample-credential api_secret=sample-credential', 'clientSecret=[REDACTED] api_secret=[REDACTED]'],
    ['request Bearer sample-credential failed', 'request Bearer [REDACTED] failed'],
    ['Authorization: Basic sample-credential', 'Authorization: [REDACTED]'],
    ['GET /api?token=sample-credential&secret=sample-credential&page=2#section', 'GET /api?token=[REDACTED]&secret=[REDACTED]&page=2#section'],
    ['https://provider.test/?token=sample-credential#section', 'https://provider.test/?token=[REDACTED]#section'],
  ];
  for (const [text, expected] of cases) assert.equal(redactLogText(text), expected);
});

test('same-day diagnostics continue past the previous size threshold', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rpa-log-'));
  try {
    const filename = path.join(dir, 'electron.log');
    const logger = createFileLogger(filename);
    for (let index = 0; index < 600; index += 1) logger.write('INFO', '运行数据'.repeat(1000));
    assert.ok(fs.statSync(filename).size > 5 * 1024 * 1024);
    assert.deepEqual(fs.readdirSync(dir), ['electron.log']);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('diagnostic files rotate daily and redact secrets', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rpa-log-'));
  try {
    const filename = path.join(dir, 'backend-process.log');
    let date = new Date('2026-10-01T12:00:00Z');
    const logger = createFileLogger(filename, { now: () => date });
    for (let index = 0; index < 20; index += 1) logger.write('ERROR', `step=${index} api_key=sample-credential 运行错误`);
    assert.deepEqual(fs.readdirSync(dir), ['backend-process.log']);
    date = new Date('2026-10-02T12:00:00Z');
    logger.write('INFO', 'next day');
    assert.deepEqual(fs.readdirSync(dir).sort(), ['backend-process.log', 'backend-process.log.2026-10-01']);
    for (const file of fs.readdirSync(dir)) {
      const content = fs.readFileSync(path.join(dir, file), 'utf8');
      assert.ok(!content.includes('sample-credential'));
    }
    assert.ok(fs.readFileSync(`${filename}.2026-10-01`, 'utf8').includes('[REDACTED]'));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('process capture handles UTF-8 and credentials split across chunks and flushes final line', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rpa-log-'));
  try {
    const filename = path.join(dir, 'backend-process.log');
    const child = { stdout: new PassThrough(), stderr: new PassThrough() };
    captureProcessOutput(child, createFileLogger(filename), 'backend');
    const buffer = Buffer.from('运行 Authorization: Bearer sample-credential\nfinal traceback');
    child.stderr.write(buffer.subarray(0, 2));
    child.stderr.write(buffer.subarray(2, 28));
    child.stderr.end(buffer.subarray(28));
    await new Promise((resolve) => setImmediate(resolve));
    const text = fs.readFileSync(filename, 'utf8');
    assert.ok(text.includes('运行'));
    assert.ok(text.includes('final traceback'));
    assert.ok(!text.includes('sample-credential'));
    assert.ok(!text.includes('�'));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('cleanup respects retention and leaves unrelated files alone', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rpa-log-'));
  try {
    const filename = path.join(dir, 'electron.log');
    fs.writeFileSync(`${filename}.2026-08-01`, 'old');
    fs.writeFileSync(`${filename}.2026-09-20`, 'recent');
    fs.writeFileSync(path.join(dir, 'unrelated.log'), 'keep');
    createFileLogger(filename, { now: () => new Date('2026-10-01T12:00:00Z') }).write('INFO', 'start');
    assert.deepEqual(fs.readdirSync(dir).sort(), ['electron.log', 'electron.log.2026-09-20', 'unrelated.log']);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
