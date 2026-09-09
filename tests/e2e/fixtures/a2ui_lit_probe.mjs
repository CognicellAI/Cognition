/**
 * Test-only v1 candidate transport adapter over pinned upstream Lit widgets.
 * Dependency setup, including the web_core override, is documented in
 * docs/concepts/a2a/a2ui.md.
 * These packages export a v0.9 engine. The adapter implements v1 inline
 * createSurface initialization for the shared Text/Column/Button test subset;
 * it does not claim full v1 renderer conformance or ship in Cognition runtime.
 */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {resolve} from 'node:path';
const require = createRequire(resolve(process.argv[2], '../package.json'));
const load = spec => import(pathToFileURL(require.resolve(spec)).href);
const {JSDOM} = await load('jsdom');
const dom = new JSDOM('<!doctype html><html><body></body></html>', {pretendToBeVisual: true});
for (const key of ['window', 'document', 'HTMLElement', 'Element', 'Document', 'Node', 'ShadowRoot', 'CustomEvent', 'Event', 'MouseEvent', 'customElements', 'CSSStyleSheet', 'MutationObserver', 'navigator']) {
  Object.defineProperty(globalThis, key, {value: key === 'window' ? dom.window : dom.window[key], configurable: true});
}
// JSDOM does not implement constructable/adopted stylesheets yet.
for (const prototype of [Document.prototype, ShadowRoot.prototype]) {
  if (!('adoptedStyleSheets' in prototype)) {
    const sheets = new WeakMap();
    Object.defineProperty(prototype, 'adoptedStyleSheets', {
      get() {if (!sheets.has(this)) sheets.set(this, []); return sheets.get(this);},
      set(value) {sheets.set(this, value);},
    });
  }
}
if (!CSSStyleSheet.prototype.replaceSync) CSSStyleSheet.prototype.replaceSync = function () {};
const {basicCatalog} = await load('@a2ui/lit/v0_9');
const {Catalog, MessageProcessor} = await load('@a2ui/web_core/v0_9');
const catalogId = 'https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json';
const catalog = new Catalog(catalogId, [...basicCatalog.components.values()], [...basicCatalog.functions.values()]);
let action;
const processor = new MessageProcessor([catalog], value => {action = value;});
let input = '';
for await (const chunk of process.stdin) input += chunk;
const payload = JSON.parse(input);
function apply(messages) {
  for (const message of messages) {
    assert.equal(message.version, 'v1.0');
    if (message.createSurface) {
      const {surfaceId, components, dataModel} = message.createSurface;
      processor.processMessages([{...message, createSurface: {...message.createSurface, catalogId}}]);
      if (components) processor.processMessages([{version: 'v1.0', updateComponents: {surfaceId, components}}]);
      if (dataModel) processor.processMessages([{version: 'v1.0', updateDataModel: {surfaceId, value: dataModel}}]);
    } else {
      processor.processMessages([message]);
    }
  }
}
function nodes(root) {
  return [root, ...[...(root.children || [])].flatMap(nodes), ...(root.shadowRoot ? nodes(root.shadowRoot) : [])];
}
async function settle() {
  for (let i = 0; i < 8; i++) {
    await Promise.all(nodes(document.body).map(node => node.updateComplete));
    await new Promise(resolve => setTimeout(resolve, 0));
  }
}
function visibleText(root) {
  if (['STYLE', 'SCRIPT'].includes(root.nodeName)) return '';
  return [...root.childNodes].map(node => node.nodeType === 3 ? node.textContent : visibleText(node)).join(' ') + (root.shadowRoot ? visibleText(root.shadowRoot) : '');
}
apply(payload.initial_messages);
const surfaceId = payload.initial_messages.find(m => m.createSurface).createSurface.surfaceId;
const element = document.createElement('a2ui-surface');
element.surface = processor.model.getSurface(surfaceId);
document.body.append(element);
await settle();
const before = visibleText(element).trim();
assert.ok(before.length > 0, 'Initial UI rendered text');
const button = nodes(element).find(node => node.tagName === 'BUTTON');
if (payload.require_button) assert.ok(button, 'Agent-generated button rendered');
if (button) {button.click(); await settle(); assert.ok(action, 'Renderer emitted an action');}
if (payload.action_messages) {apply(payload.action_messages); await settle();}
const after = visibleText(element).trim();
if (payload.action_messages) assert.notEqual(after, before, 'Follow-up visibly changed the original surface');
process.stdout.write(JSON.stringify({before, after, action}));
dom.window.close();
