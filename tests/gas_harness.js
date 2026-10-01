// Runs gas/Code.gs against in-memory stand-ins for the Google services it
// uses, calling real Cash 27 routes through a Flask subprocess (or curl). State (script
// properties, Drive, spreadsheets, triggers, mail) persists in a JSON file so
// tests can run nightlySync() repeatedly and inspect the result.
//
//   node tests/gas_harness.js STATE.json FUNCTION [FUNCTION...]
//
// Prints {"error": message-or-null} for the last function run.

const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');
const {spawnSync} = require('child_process');

const [statePath, ...functions] = process.argv.slice(2);
const state = Object.assign({
  props: {}, nextId: 1, items: {root: {id: 'root', folder: true, name: 'My Drive', parent: null, trashed: false}},
  spreadsheets: {}, triggers: [], mails: [], logs: [], now: null,
  // The Drive API's page size (tests shrink it to exercise paging), whether
  // it refuses requests, and how many requests and per-file lookups ran.
  drivePageSize: 1000, driveApiDisabled: false, calls: {},
}, fs.existsSync(statePath) ? JSON.parse(fs.readFileSync(statePath, 'utf8')) : {});

const newId = prefix => `${prefix}${state.nextId++}`;
const signed = buffer => Array.from(buffer, b => (b > 127 ? b - 256 : b));
const unsigned = bytes => Buffer.from(bytes.map(b => (b + 256) % 256));

function iterator(list) {
  let i = 0;
  return {hasNext: () => i < list.length, next: () => list[i++]};
}

class Blob {
  constructor(bytes, name, type) { this.bytes = bytes; this.name = name; this.type = type; }
  getBytes() { return signed(this.bytes); }
  getName() { return this.name; }
  setName(name) { this.name = name; return this; }
  getDataAsString() { return this.bytes.toString('utf8'); }
}

const count = name => { state.calls[name] = (state.calls[name] || 0) + 1; };

class File {
  constructor(id) { this.id = id; }
  get item() { return state.items[this.id]; }
  getId() { return this.id; }
  getName() { return this.item.name; }
  setName(name) { this.item.name = name; return this; }
  getSize() { count('getSize'); return Buffer.from(this.item.content || '', 'base64').length; }
  getMimeType() { return this.item.mime || 'application/pdf'; }
  getTargetId() { return this.item.targetId || null; }
  getDescription() { return this.item.description || null; }
  setDescription(text) { this.item.description = text; return this; }
  isTrashed() { return this.item.trashed; }
  setTrashed(value) { this.item.trashed = value; return this; }
  getUrl() { count('getUrl'); return `https://drive.test/file/${this.id}`; }
  moveTo(folder) { this.item.parent = folder.getId(); return this; }
  getBlob() { return new Blob(Buffer.from(this.item.content, 'base64'), this.item.name, this.item.mime); }
}

class Folder {
  constructor(id) { this.id = id; }
  get item() { return state.items[this.id]; }
  getId() { return this.id; }
  getName() { return this.item.name; }
  getUrl() { return `https://drive.test/folder/${this.id}`; }
  isTrashed() { return this.item.trashed; }
  setTrashed(value) { this.item.trashed = value; return this; }
  children(folder) {
    // Like DriveApp, listings include trashed items.
    return Object.values(state.items).filter(i => i.parent === this.id && !!i.folder === folder);
  }
  getFoldersByName(name) { return iterator(this.children(true).filter(i => i.name === name).map(i => new Folder(i.id))); }
  getFiles() { return iterator(this.children(false).map(i => new File(i.id))); }
  getFilesByName(name) { return iterator(this.children(false).filter(i => i.name === name).map(i => new File(i.id))); }
  createFolder(name) {
    const id = newId('folder');
    state.items[id] = {id, folder: true, name, parent: this.id, trashed: false};
    return new Folder(id);
  }
  createShortcut(targetId) {
    if (state.failShortcutOnce) { state.failShortcutOnce = false; throw new Error('Interrupted shortcut creation'); }
    const id = newId('shortcut');
    state.items[id] = {id, folder: false, name: state.items[targetId].name, parent: this.id,
      trashed: false, mime: 'application/vnd.google-apps.shortcut', targetId, content: ''};
    return new File(id);
  }
  createFile(blob) {
    const id = newId('file');
    state.items[id] = {id, folder: false, name: blob.getName(), parent: this.id, trashed: false,
                       content: blob.bytes.toString('base64'), mime: blob.type};
    return new File(id);
  }
}

const DriveApp = {
  getRootFolder: () => new Folder('root'),
  getFolderById(id) {
    if (!state.items[id] || !state.items[id].folder) throw new Error('No folder ' + id);
    return new Folder(id);
  },
  getFileById(id) {
    if (!state.items[id] || state.items[id].folder) throw new Error('No file ' + id);
    return new File(id);
  },
};

class Range {
  constructor(sheet, row, column, rows, columns) {
    Object.assign(this, {sheet, row, column, rows, columns});
  }
  each(values, put) {
    if (values.length !== this.rows || values.some(r => r.length !== this.columns)) {
      throw new Error('The data does not match the range');
    }
    values.forEach((r, i) => r.forEach((v, j) => put(this.row + i - 1, this.column + j - 1, v)));
    return this;
  }
  setValues(values) {
    return this.each(values, (r, c, v) => { this.sheet.cell(r, c).value = v; delete this.sheet.cell(r, c).links; });
  }
  setRichTextValues(values) {
    return this.each(values, (r, c, v) => Object.assign(this.sheet.cell(r, c), {value: v.text, links: v.links}));
  }
  setNumberFormat(format) {
    for (let c = this.column; c < this.column + this.columns; c++) this.sheet.data.formats[c - 1] = format;
    return this;
  }
  setFontWeight() { return this; }
}

class Sheet {
  constructor(data) { this.data = data; }
  getName() { return this.data.name; }
  cell(r, c) {
    const rows = this.data.rows;
    while (rows.length <= r) rows.push([]);
    while (rows[r].length <= c) rows[r].push({value: ''});
    return rows[r][c];
  }
  appendRow(values) {
    const row = this.data.rows.length;
    values.forEach((v, c) => { this.cell(row, c).value = v instanceof Date ? v.toISOString() : v; });
    return this;
  }
  getRange(row, column, rows, columns) { return new Range(this, row, column, rows || 1, columns || 1); }
  getDataRange() {
    const width = Math.max(0, ...this.data.rows.map(r => r.length));
    return {getValues: () => this.data.rows.map(r => Array.from({length: width}, (_, c) => (r[c] ? r[c].value : '')))};
  }
  clear() { this.data.rows = []; this.data.formats = {}; return this; }
  setFrozenRows(n) { this.data.frozen = n; return this; }
  getProtections() {
    return this.data.protections.map((p, i) => ({remove: () => { this.data.protections.splice(i, 1); }}));
  }
  protect() {
    const protection = {warningOnly: false, description: ''};
    this.data.protections.push(protection);
    const handle = {
      setWarningOnly: value => { protection.warningOnly = value; return handle; },
      setDescription: value => { protection.description = value; return handle; },
    };
    return handle;
  }
}

class Spreadsheet {
  constructor(id) { this.id = id; }
  get data() { return state.spreadsheets[this.id]; }
  getId() { return this.id; }
  getUrl() { return `https://sheets.test/${this.id}`; }
  getSheets() { return this.data.sheets.map(s => new Sheet(s)); }
  getSheetByName(name) {
    const found = this.data.sheets.find(s => s.name === name);
    return found ? new Sheet(found) : null;
  }
  insertSheet(name) {
    const data = {name, rows: [], formats: {}, frozen: 0, protections: []};
    this.data.sheets.push(data);
    return new Sheet(data);
  }
  deleteSheet(sheet) { this.data.sheets = this.data.sheets.filter(s => s.name !== sheet.getName()); }
}

const SpreadsheetApp = {
  ProtectionType: {SHEET: 'SHEET'},
  create(name) {
    const id = newId('sheet');
    state.items[id] = {id, folder: false, name, parent: 'root', trashed: false, content: '',
                       mime: 'application/vnd.google-apps.spreadsheet'};
    state.spreadsheets[id] = {sheets: [{name: 'Sheet1', rows: [], formats: {}, frozen: 0, protections: []}]};
    return new Spreadsheet(id);
  },
  openById(id) {
    if (!state.spreadsheets[id]) throw new Error('No spreadsheet ' + id);
    return new Spreadsheet(id);
  },
  newRichTextValue() {
    const value = {text: '', links: []};
    const builder = {
      setText: text => { value.text = text; return builder; },
      setLinkUrl: (start, end, url) => { value.links.push({start, end, url}); return builder; },
      build: () => value,
    };
    return builder;
  },
};

/** Drive API v3 files.list, as much of it as the script uses. */
function driveList(url, options) {
  count('driveList');
  const reply = (code, body) => ({
    getResponseCode: () => code, getAllHeaders: () => ({}),
    getContentText: () => JSON.stringify(body), getBlob: () => null,
  });
  if (state.driveApiDisabled) return reply(403, {error: {message: 'Drive API has not been used'}});
  if (options.headers.Authorization !== 'Bearer oauth-token') return reply(401, {});
  const params = new URL(url).searchParams;
  const folder = params.get('q').match(/^'([^']+)' in parents/)[1];
  const live = Object.values(state.items).filter(i => i.parent === folder && !i.trashed && !i.folder);
  const start = Number(params.get('pageToken') || 0);
  const size = Math.min(Number(params.get('pageSize')), state.drivePageSize);
  const files = live.slice(start, start + size).map(i => ({
    id: i.id, name: i.name, size: String(Buffer.from(i.content || '', 'base64').length),
    webViewLink: `https://drive.test/file/${i.id}`, mimeType: i.mime || 'application/pdf',
    description: i.description, shortcutDetails: i.targetId ? {targetId: i.targetId} : undefined,
  }));
  return reply(200, start + size < live.length ? {files, nextPageToken: String(start + size)} : {files});
}

function fetch(url, options) {
  if (url.startsWith('https://www.googleapis.com/drive/v3/files')) return driveList(url, options);
  if (state.httpRoot) {
    const response = spawnSync(state.python, [path.join(__dirname, 'gas_http.py'),
      state.httpRoot, new URL(url).pathname + new URL(url).search, JSON.stringify(options)],
      {encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 30000});
    if (response.status !== 0) throw new Error(response.stderr || 'Flask transport failed');
    const result = JSON.parse(response.stdout);
    let body = Buffer.from(result.body, 'base64');
    if (state.legacyManifest && new URL(url).pathname === '/api/sync/manifest') {
      const payload = JSON.parse(body);
      for (const entry of payload.data.files) {
        delete entry.document_id;
        delete entry.object_role;
        delete entry.canonical_filename;
      }
      body = Buffer.from(JSON.stringify(payload));
    }
    return {getResponseCode: () => result.status, getAllHeaders: () => result.headers,
      getContentText: () => body.toString('utf8'),
      getBlob: () => new Blob(body, null, result.headers['Content-Type'])};
  }
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'gas-'));
  const args = ['-s', '-D', path.join(dir, 'h'), '-o', path.join(dir, 'b'),
                '-X', (options.method || 'get').toUpperCase()];
  for (const [name, value] of Object.entries(options.headers || {})) args.push('-H', `${name}: ${value}`);
  if (options.payload !== undefined) {
    args.push('-H', `Content-Type: ${options.contentType}`, '--data-binary', options.payload);
  }
  args.push(url);
  const result = spawnSync('curl', args);
  if (result.status !== 0) throw new Error('Could not reach ' + url);
  const lines = fs.readFileSync(path.join(dir, 'h'), 'utf8').split('\r\n');
  const code = Number(lines[0].split(' ')[1]);
  const headers = {};
  for (const line of lines.slice(1)) {
    const at = line.indexOf(':');
    if (at > 0) headers[line.slice(0, at)] = line.slice(at + 1).trim();
  }
  const body = fs.readFileSync(path.join(dir, 'b'));
  fs.rmSync(dir, {recursive: true});
  return {
    getResponseCode: () => code,
    getAllHeaders: () => headers,
    getContentText: () => body.toString('utf8'),
    getBlob: () => new Blob(body, null, headers['Content-Type']),
  };
}

function formatDate(date, zone, pattern) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).formatToParts(date).map(p => [p.type, p.value]));
  return pattern.replace(/'([^']*)'|yyyy|MM|dd|HH|mm|ss/g, (token, literal) => literal !== undefined ? literal
    : {yyyy: parts.year, MM: parts.month, dd: parts.day, HH: parts.hour, mm: parts.minute, ss: parts.second}[token]);
}

const RealDate = Date;
class FixedDate extends RealDate {
  constructor(...args) { super(...(args.length ? args : [state.now || RealDate.now()])); }
  static now() { return state.now || RealDate.now(); }
}

const context = {
  console,
  Date: FixedDate,
  DriveApp,
  SpreadsheetApp,
  UrlFetchApp: {fetch},
  Utilities: {
    newBlob: data => new Blob(Buffer.from(data, 'utf8'), '', 'text/plain'),
    DigestAlgorithm: {SHA_256: 'sha256'},
    computeDigest: (algorithm, bytes) => signed(crypto.createHash(algorithm).update(unsigned(bytes)).digest()),
    formatDate,
  },
  PropertiesService: {getScriptProperties: () => ({
    getProperty: key => (key in state.props ? state.props[key] : null),
    setProperty: (key, value) => { state.props[key] = String(value); },
    deleteProperty: key => { delete state.props[key]; },
  })},
  LockService: {getScriptLock: () => ({tryLock: () => true, releaseLock: () => {}})},
  ScriptApp: {
    getOAuthToken: () => 'oauth-token',
    getProjectTriggers: () => state.triggers.map((t, i) => ({getHandlerFunction: () => t.handler, index: i})),
    deleteTrigger: trigger => { state.triggers.splice(trigger.index, 1); },
    newTrigger(handler) {
      const trigger = {handler};
      const builder = {
        timeBased: () => builder,
        everyDays: days => { trigger.everyDays = days; return builder; },
        atHour: hour => { trigger.atHour = hour; return builder; },
        create: () => { state.triggers.push(trigger); },
      };
      return builder;
    },
  },
  Session: {
    getEffectiveUser: () => ({getEmail: () => 'owner@example.com'}),
    getScriptTimeZone: () => 'America/Los_Angeles',
  },
  MailApp: {sendEmail: (to, subject, body) => { state.mails.push({to, subject, body}); }},
  Logger: {log: message => { state.logs.push(String(message)); }},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'gas', 'Code.gs'), 'utf8'), context);

let error = null;
for (const name of functions) {
  try {
    error = null;
    context[name]();
  } catch (e) {
    error = e.message || String(e);
  }
}
fs.writeFileSync(statePath, JSON.stringify(state));
process.stdout.write(JSON.stringify({error}));
