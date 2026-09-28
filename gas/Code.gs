/**
 * Cash 27 Drive backup.
 *
 * Once a night this copies Cash 27 into the Drive of the Google account that
 * runs it: the database (.db and .sql), every document, and a readable Sheet
 * per fiscal year. It only ever reads from Cash 27.
 *
 * Setup (README.md, "Drive backup"):
 *   1. Project Settings > Script properties: CASH_URL (https://your-domain)
 *      and CASH_TOKEN (Cash 27 > Settings > Drive backup > Copy).
 *   2. Run setup() once. It creates the "Cash 27" folder and the nightly
 *      trigger, and is safe to run again.
 *   3. Run nightlySync() once for the first copy.
 *
 * Drive layout:
 *   Cash 27/
 *     Sync log                 one row per run, and the database snapshots kept
 *     Documents/               every document. Never delete from here: older
 *                              database snapshots may name any of them.
 *     Config/                  latest print settings
 *     FY27/FY27 Ledger         Expense report and Cash ledger tabs
 *     FY27/Database/           .db and .sql snapshots taken during FY27
 */

const MASTER_NAME = 'Cash 27';
const LOG_NAME = 'Sync log';
const RUN_HOUR = 2;  // Local hour, in appsscript.json's time zone.
const KEEP_EVERY_SNAPSHOT_DAYS = 30;
const NUMBER_FORMATS = {
  'MXN': '0.00', 'USD': '0.00', 'Calc USD': '0.00',
  'Safe Balance USD': '0.00', 'Safe Balance MXN': '0.00', 'ER': '0.000',
};
const SNAPSHOT_HEADER = ['Stored', 'Fiscal year', 'File', 'File ID', 'Signature', 'SHA-256', 'Status'];
const RUN_HEADER = ['Started', 'Finished', 'Result', 'Database', 'Files copied', 'Sheets updated', 'Problems'];


function setup() {
  settings_();
  const places = places_([]);
  cashJson_('/api/sync/manifest');  // The URL and token work.
  for (const trigger of ScriptApp.getProjectTriggers()) {
    if (trigger.getHandlerFunction() === 'nightlySync') ScriptApp.deleteTrigger(trigger);
  }
  ScriptApp.newTrigger('nightlySync').timeBased().everyDays(1).atHour(RUN_HOUR).create();
  Logger.log('Ready. Drive folder: ' + places.master.getUrl() +
             '. Run nightlySync() now for the first copy.');
}


function nightlySync() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) {
    Logger.log('Another run is still going.');
    return;
  }
  const started = new Date();
  const run = {database: '', copied: 0, sheets: 0, problems: []};
  let places = null;
  try {
    places = places_(run.problems);
    // Each step carries on after another fails; the run reports them all.
    const step = (name, work) => {
      try { work(); } catch (error) { run.problems.push(name + ': ' + (error.message || error)); }
    };
    let database = null, manifest = null, sheets = null, index = null;
    // The manifest comes after the database so it lists every document the
    // stored snapshot names.
    step('Database download', () => { database = downloadDatabase_(); });
    step('Manifest', () => {
      manifest = cashJson_('/api/sync/manifest');
      for (const problem of manifest.problems) run.problems.push('Cash 27: ' + problem);
    });
    if (database && manifest) step('Database', () => storeDatabase_(places, database, manifest, run));
    step('Snapshots', () => checkSnapshots_(places, run));
    if (manifest) step('Drive index', () => { index = driveIndex_(places, manifest); });
    // Without the index the Sheets would lose their document links.
    if (index) step('Sheets', () => {
      sheets = cashJson_('/api/sync/sheets');
      updateSheets_(places, sheets, index, run);
    });
    if (manifest && index) step('Files', () => copyFiles_(places, manifest, index, run));
    // Newly copied documents get their links tonight rather than tomorrow.
    if (sheets && index && run.copied) step('Sheet links', () => updateSheets_(places, sheets, index, run));
    if (!run.problems.length) {
      step('Completion', () => cash_('/api/sync/complete', {
        method: 'post', contentType: 'application/json', payload: '{}'}));
    }
  } catch (error) {
    run.problems.push(error.message || String(error));
  } finally {
    try {
      if (places) {
        places.runs.appendRow([started, new Date(), run.problems.length ? 'Problems' : 'OK',
                               run.database, run.copied, run.sheets, run.problems.join('\n')]);
      }
    } finally {
      lock.releaseLock();
    }
  }
  if (run.problems.length) {
    const text = 'The Cash 27 Drive backup finished with problems:\n\n- ' +
                 run.problems.join('\n- ') +
                 (places ? '\n\nSync log: ' + places.log.getUrl() : '');
    MailApp.sendEmail(Session.getEffectiveUser().getEmail(), 'Cash 27 Drive backup: problems', text);
    throw new Error(run.problems.join('; '));
  }
}


// ---- Cash 27 ----

function settings_() {
  const props = PropertiesService.getScriptProperties();
  const url = (props.getProperty('CASH_URL') || '').replace(/\/+$/, '');
  const token = props.getProperty('CASH_TOKEN') || '';
  if (!/^https?:\/\//.test(url) || !token) {
    throw new Error('Set the CASH_URL and CASH_TOKEN script properties first ' +
                    '(Project Settings > Script properties).');
  }
  return {url: url, token: token};
}

function cash_(path, options) {
  const s = settings_();
  const response = UrlFetchApp.fetch(s.url + path, Object.assign({
    headers: {Authorization: 'Bearer ' + s.token},
    muteHttpExceptions: true,
    followRedirects: false,
  }, options || {}));
  const code = response.getResponseCode();
  if (code !== 200) {
    let message = response.getContentText().slice(0, 300);
    try { message = JSON.parse(response.getContentText()).error.message; } catch (e) {}
    throw new Error(path + ' answered ' + code + ': ' + message);
  }
  return response;
}

function cashJson_(path) {
  return JSON.parse(cash_(path).getContentText()).data;
}

function header_(response, name) {
  const headers = response.getAllHeaders();
  for (const key in headers) {
    if (key.toLowerCase() === name.toLowerCase()) return headers[key];
  }
  return null;
}

function sha256_(bytes) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, bytes)
    .map(b => ((b + 256) % 256).toString(16).padStart(2, '0')).join('');
}


// ---- Drive places ----

function places_(problems) {
  const props = PropertiesService.getScriptProperties();
  let master = null;
  const id = props.getProperty('MASTER_FOLDER_ID');
  if (id) {
    try {
      master = DriveApp.getFolderById(id);
      if (master.isTrashed()) master = null;
    } catch (e) { master = null; }
    if (!master) problems.push('The "' + MASTER_NAME + '" folder was missing; started a new one. ' +
                               'Older database snapshots are no longer tracked.');
  }
  if (!master) {
    master = DriveApp.getRootFolder().createFolder(MASTER_NAME);
    props.setProperty('MASTER_FOLDER_ID', master.getId());
    props.deleteProperty('LOG_ID');
    props.deleteProperty('LAST_DB_SIGNATURE');
  }
  const log = spreadsheet_('LOG_ID', master, LOG_NAME);
  return {
    master: master, log: log,
    runs: tab_(log, 'Runs', RUN_HEADER),
    snapshots: tab_(log, 'Snapshots', SNAPSHOT_HEADER),
    documents: child_(master, 'Documents'),
    config: child_(master, 'Config'),
  };
}

function child_(parent, name) {
  const found = parent.getFoldersByName(name);
  while (found.hasNext()) {
    const folder = found.next();
    if (!folder.isTrashed()) return folder;
  }
  return parent.createFolder(name);
}

function spreadsheet_(key, folder, name) {
  const props = PropertiesService.getScriptProperties();
  const id = props.getProperty(key);
  if (id) {
    try {
      if (!DriveApp.getFileById(id).isTrashed()) return SpreadsheetApp.openById(id);
    } catch (e) {}
  }
  const found = folder.getFilesByName(name);
  while (found.hasNext()) {
    const file = found.next();
    if (!file.isTrashed()) {
      props.setProperty(key, file.getId());
      return SpreadsheetApp.openById(file.getId());
    }
  }
  const created = SpreadsheetApp.create(name);
  DriveApp.getFileById(created.getId()).moveTo(folder);
  props.setProperty(key, created.getId());
  return created;
}

function tab_(spreadsheet, name, header) {
  let sheet = spreadsheet.getSheetByName(name);
  if (!sheet) {
    sheet = spreadsheet.insertSheet(name);
    if (header) {
      sheet.appendRow(header);
      sheet.setFrozenRows(1);
    }
    const blank = spreadsheet.getSheetByName('Sheet1');
    if (blank && spreadsheet.getSheets().length > 1) spreadsheet.deleteSheet(blank);
  }
  return sheet;
}


// ---- Database ----

function downloadDatabase_() {
  // A write between the two downloads gives different contents; try again.
  for (let attempt = 0; attempt < 3; attempt++) {
    const parts = {};
    for (const suffix of ['db', 'sql']) {
      const response = cash_('/api/sync/database.' + suffix);
      const blob = response.getBlob();
      if (sha256_(blob.getBytes()) !== header_(response, 'X-Content-SHA256')) {
        throw new Error('database.' + suffix + ' arrived damaged');
      }
      const disposition = header_(response, 'Content-Disposition') || '';
      const name = (disposition.match(/filename="?([^";]+)"?/) || [])[1];
      if (!/^cash-27-\d{8}T\d{6}Z\.(db|sql)$/.test(name || '')) {
        throw new Error('database.' + suffix + ' has an unexpected name: ' + name);
      }
      parts[suffix] = {blob: blob.setName(name), name: name,
                       signature: header_(response, 'X-Database-Signature'),
                       sha256: header_(response, 'X-Content-SHA256')};
    }
    if (parts.db.signature === parts.sql.signature) return parts;
  }
  throw new Error('the database kept changing between the .db and .sql downloads');
}

function storeDatabase_(places, database, manifest, run) {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty('LAST_DB_SIGNATURE') === database.db.signature) {
    run.database = 'unchanged';
    return;
  }
  const folder = child_(child_(places.master, manifest.fiscal_label), 'Database');
  for (const part of [database.db, database.sql]) {
    const file = folder.createFile(part.blob);
    file.setDescription('sha256 ' + part.sha256 + '\nsignature ' + part.signature);
    places.snapshots.appendRow([new Date(), manifest.fiscal_label, part.name, file.getId(),
                                part.signature, part.sha256, 'kept']);
  }
  props.setProperty('LAST_DB_SIGNATURE', database.db.signature);
  run.database = 'stored ' + database.db.name.replace(/\.db$/, '');
}

/** Report snapshots that disappeared, and apply retention: every snapshot
 *  for 30 days, then the first one of each month. */
function checkSnapshots_(places, run) {
  const sheet = places.snapshots;
  const rows = sheet.getDataRange().getValues().slice(1);
  const stamp = name => String(name).replace(/^cash-27-/, '').replace(/\.(db|sql)$/, '');
  const firstOfMonth = {};
  for (const row of rows) {
    if (row[6] === 'missing') continue;
    const s = stamp(row[2]);
    const month = s.slice(0, 6);
    if (!firstOfMonth[month] || s < firstOfMonth[month]) firstOfMonth[month] = s;
  }
  const cutoff = Utilities.formatDate(
    new Date(Date.now() - KEEP_EVERY_SNAPSHOT_DAYS * 86400000), 'UTC', "yyyyMMdd'T'HHmmss'Z'");
  rows.forEach((row, i) => {
    if (row[6] !== 'kept') return;
    let file = null;
    try {
      file = DriveApp.getFileById(row[3]);
      if (file.isTrashed()) file = null;
    } catch (e) { file = null; }
    const status = sheet.getRange(i + 2, 7, 1, 1);
    if (!file) {
      run.problems.push('Database snapshot ' + row[2] + ' is missing from Drive');
      status.setValues([['missing']]);
      return;
    }
    const s = stamp(row[2]);
    if (s < cutoff && firstOfMonth[s.slice(0, 6)] !== s) {
      file.setTrashed(true);
      status.setValues([['pruned']]);
    }
  });
}


// ---- Documents and settings ----

/** What Drive already holds. Documents never change, so a file with the
 *  same unique name and size is the same file; settings do change, so they
 *  are matched by the hash recorded on each copy. */
function driveIndex_(places, manifest) {
  const listing = folder => {
    const files = {};
    const all = folder.getFiles();
    while (all.hasNext()) {
      const file = all.next();
      if (!file.isTrashed()) files[file.getName()] = file;
    }
    return files;
  };
  const index = {documents: listing(places.documents), bySha: {}};
  for (const entry of manifest.files) {
    const held = entry.kind === 'document' ? index.documents[entry.name] : null;
    if (held && held.getSize() === entry.size) index.bySha[entry.sha256] = held;
  }
  return index;
}

function copyFiles_(places, manifest, index, run) {
  for (const entry of manifest.files) {
    if (entry.kind === 'config') {
      copyConfig_(places, entry, run);
      continue;
    }
    if (index.bySha[entry.sha256]) continue;
    const names = index.documents;
    const held = names[entry.name];
    if (held) {
      // Same name, different contents: keep it aside, never delete it.
      held.setName('DIFFERS ' + entry.name);
      run.problems.push('The Drive copy of ' + entry.name + ' did not match; kept it as "DIFFERS ' +
                        entry.name + '" and copied it again');
    }
    const file = places.documents.createFile(fetchFile_(entry));
    file.setDescription('sha256 ' + entry.sha256);
    names[entry.name] = file;
    index.bySha[entry.sha256] = file;
    run.copied++;
  }
}

function copyConfig_(places, entry, run) {
  const parts = entry.path.replace(/^config\//, '').split('/');
  let folder = places.config;
  for (const part of parts.slice(0, -1)) folder = child_(folder, part);
  const same = [];
  const found = folder.getFilesByName(entry.name);
  while (found.hasNext()) {
    const file = found.next();
    if (!file.isTrashed()) same.push(file);
  }
  if (same.some(file => file.getDescription() === 'sha256 ' + entry.sha256)) return;
  const file = folder.createFile(fetchFile_(entry));
  file.setDescription('sha256 ' + entry.sha256);
  // Settings are replaced by their newer version.
  for (const old of same) old.setTrashed(true);
  run.copied++;
}

function fetchFile_(entry) {
  const blob = cash_('/api/sync/files/' + entry.sha256).getBlob();
  const bytes = blob.getBytes();
  if (bytes.length !== entry.size || sha256_(bytes) !== entry.sha256) {
    throw new Error(entry.path + ' arrived damaged');
  }
  return blob.setName(entry.name);
}


// ---- Sheets ----

function updateSheets_(places, data, index, run) {
  const props = PropertiesService.getScriptProperties();
  const links = {};
  for (const sha in index.bySha) links[sha] = index.bySha[sha].getUrl();
  for (const year of data.years) {
    let linked = 0;
    for (const tab of year.tabs) {
      for (const docs of tab.documents) {
        for (const doc of docs) if (doc.sha256 && links[doc.sha256]) linked++;
      }
    }
    const sheetKey = 'SHEET_' + year.fiscal_year;
    const folder = child_(places.master, year.fiscal_label);
    const spreadsheet = spreadsheet_(sheetKey, folder, year.fiscal_label + ' Ledger');
    // Rewritten when the year's content or its links change, or the
    // spreadsheet or one of its tabs was replaced or removed.
    const key = [spreadsheet.getId(), year.revision, linked].join(':');
    const complete = year.tabs.every(tab => spreadsheet.getSheetByName(tab.name));
    if (complete && props.getProperty(sheetKey + '_KEY') === key) continue;
    const note = 'Generated nightly from Cash 27; edits here are overwritten. Updated ' +
                 Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm') +
                 ', ledger revision ' + data.ledger_revision + '.';
    for (const tab of year.tabs) writeTab_(spreadsheet, tab, links, note);
    props.setProperty(sheetKey + '_KEY', key);
    run.sheets++;
  }
}

function writeTab_(spreadsheet, tab, links, note) {
  const sheet = tab_(spreadsheet, tab.name, null);
  for (const protection of sheet.getProtections(SpreadsheetApp.ProtectionType.SHEET)) protection.remove();
  sheet.clear();
  const columns = tab.columns.concat(['Documents']);
  const width = columns.length;
  const values = [[note].concat(new Array(width - 1).fill('')), columns]
    .concat(tab.rows.map(row => row.map(cell_).concat([''])));
  sheet.getRange(1, 1, values.length, width).setValues(values);
  sheet.getRange(2, 1, 1, width).setFontWeight('bold');
  sheet.setFrozenRows(2);
  if (tab.rows.length) {
    sheet.getRange(3, width, tab.rows.length, 1)
      .setRichTextValues(tab.documents.map(docs => [documentsText_(docs, links)]));
    columns.forEach((column, i) => {
      const format = column === 'Date' ? 'yyyy-mm-dd' : NUMBER_FORMATS[column];
      if (format) sheet.getRange(3, i + 1, tab.rows.length, 1).setNumberFormat(format);
    });
  }
  sheet.protect().setWarningOnly(true).setDescription('Written by the Cash 27 Drive backup');
}

/** Text that a spreadsheet would read as a formula stays text. */
function cell_(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string' && /^[=+\-@]/.test(value)) return "'" + value;
  return value;
}

function documentsText_(docs, links) {
  if (!docs.length) return SpreadsheetApp.newRichTextValue().setText('No documents linked').build();
  const pieces = docs.map(doc => ({
    text: doc.reference + (doc.sha256 ? (links[doc.sha256] ? '' : ' (not yet in Drive)')
      : doc.role ? ' (missing PDF)' : ' (not attached)'),
    url: doc.sha256 ? links[doc.sha256] : null,
  }));
  const builder = SpreadsheetApp.newRichTextValue().setText(pieces.map(p => p.text).join(', '));
  let at = 0;
  for (const piece of pieces) {
    if (piece.url) builder.setLinkUrl(at, at + piece.text.length, piece.url);
    at += piece.text.length + 2;
  }
  return builder.build();
}
