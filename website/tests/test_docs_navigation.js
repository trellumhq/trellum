const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const script = fs.readFileSync(require('node:path').join(__dirname, '../static/site.js'), 'utf8');

function run({saved, storageThrows = false, mobile = false, noSide = false} = {}) {
  const side = {scrollTop: 0, scrollHeight: 2000, clientHeight: 500};
  const listeners = {};
  const values = new Map(saved === undefined ? [] : [['trellum-docs-side:/docs/latest', String(saved)]]);
  const storage = {
    getItem(key) { if (storageThrows) throw new Error('blocked'); return values.get(key) ?? null; },
    setItem(key, value) { if (storageThrows) throw new Error('blocked'); values.set(key, value); },
  };
  const context = {
    document: {
      getElementById(id) { return id === 'docs-side' && !noSide ? side : null; },
      querySelectorAll() { return []; },
    },
    window: {
      location: {pathname: '/docs/latest/workflow/working-with-ai-agents/'},
      sessionStorage: storage,
      matchMedia() { return {matches: mobile}; },
      addEventListener(name, fn) { listeners[name] = fn; },
    },
    navigator: {},
    setTimeout,
    console,
  };
  vm.runInNewContext(script, context);
  return {side, listeners, values};
}

test('restores the saved docs sidebar scroll and saves its latest position', () => {
  const result = run({saved: 855});
  assert.equal(result.side.scrollTop, 855);
  result.side.scrollTop = 412;
  result.listeners.pagehide();
  assert.equal(result.values.get('trellum-docs-side:/docs/latest'), '412');
});

test('disabled session storage fails open', () => {
  const result = run({storageThrows: true});
  assert.equal(result.side.scrollTop, 0);
  assert.doesNotThrow(() => result.listeners.pagehide());
});

test('mobile sidebar state does not overwrite the desktop position', () => {
  const result = run({saved: 855, mobile: true});
  assert.equal(result.side.scrollTop, 0);
  result.listeners.pagehide();
  assert.equal(result.values.get('trellum-docs-side:/docs/latest'), '855');
});

test('clamps out-of-range values and ignores invalid values', () => {
  assert.equal(run({saved: 9999}).side.scrollTop, 1500);
  assert.equal(run({saved: 'not-a-number'}).side.scrollTop, 0);
});

test('pages without a docs sidebar remain safe', () => {
  assert.doesNotThrow(() => run({noSide: true}));
});
