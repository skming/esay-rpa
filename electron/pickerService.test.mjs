import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'vitest';
const require = createRequire(import.meta.url);
const { createPickerService } = require('./pickerService.cjs');

function request(requestId) {
  return { requestId, mode: 'pick', browserExecutor: 'extension', flowId: 'flow', nodeId: 'node', field: 'targetSelector', selectionMode: 'single' };
}
function fixture() {
  const events = [];
  const calls = [];
  const sockets = [];
  class FakeSocket {
    constructor(url) { this.url = String(url); sockets.push(this); }
    close() { this.onclose?.(); }
    emit(data) { this.onmessage({ data: JSON.stringify(data) }); }
  }
  const service = createPickerService({
    onResult: event => events.push(event), onCancel: event => events.push(event), onError: event => events.push(event),
    WebSocketCtor: FakeSocket,
    fetchImpl: async (url, options) => {
      calls.push({ url, body: JSON.parse(options.body) });
      return { ok: true };
    }
  });
  return { service, sockets, calls, events };
}

test('extension current-page capture returns facts and the exact target field', async () => {
  const { service, sockets, events } = fixture();
  await service.openPicker(null, request('one'));
  assert.match(sockets[0].url, /requestId=one/);
  sockets[0].emit({ type: 'capture', requestId: 'one', selector: '#next', matches: 1, selectedIncluded: true, usesPosition: false });
  assert.equal(events.length, 1);
  assert.equal(events[0].field, 'targetSelector');
  assert.equal(events[0].flowId, 'flow');
  assert.equal(events[0].matches, 1);
  assert.ok(!('confidence' in events[0]));
});

test('replacement closes before opening, and late events cannot cancel a new session', async () => {
  const { service, sockets, calls, events } = fixture();
  await service.openPicker(null, request('one'));
  await service.openPicker(null, request('two'));
  assert.deepEqual(calls.map(call => [call.url.split('/').at(-1), call.body.requestId]), [['open', 'one'], ['close', 'one'], ['open', 'two']]);
  sockets[0].onclose();
  sockets[0].emit({ type: 'cancel', requestId: 'one' });
  assert.equal(events.length, 1);
  await service.closePicker({ requestId: 'one' });
  assert.equal(calls.length, 3);
  sockets[1].emit({ type: 'cancel', requestId: 'two' });
  assert.deepEqual(events.map(event => event.requestId), ['one', 'two']);
});

test('concurrent opens are serialized and explicit close does not wait for a missing websocket acknowledgement', async () => {
  const { service, calls, events } = fixture();
  await Promise.all([service.openPicker(null, request('one')), service.openPicker(null, request('two'))]);
  await service.closePicker({ requestId: 'two' });
  assert.deepEqual(calls.map(call => call.body.requestId), ['one', 'one', 'two', 'two']);
  assert.deepEqual(events.map(event => event.type), ['cancel', 'cancel']);
});

test('ambiguous single target produces an error, never a successful capture', async () => {
  const { service, sockets, events } = fixture();
  await service.openPicker(null, request('one'));
  sockets[0].emit({ type: 'capture', requestId: 'one', selector: '.duplicate', matches: 2, selectedIncluded: true });
  assert.equal(events[0].type, 'error');
});
