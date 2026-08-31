const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const WORKSPACE = process.env.SLIVIN_HARNESS_WORKSPACE;
const ORACLE_MARKER = 'MATRIX_SEMANTIC_ORACLE_REACHED';

if (!WORKSPACE) {
  throw new Error('SLIVIN_HARNESS_WORKSPACE is not set');
}

const source = (...parts) => path.join(WORKSPACE, ...parts);

class FakeClassList {
  constructor(initial = []) {
    this.values = new Set(initial);
  }
  add(...items) { items.forEach((item) => this.values.add(item)); }
  remove(...items) { items.forEach((item) => this.values.delete(item)); }
  contains(item) { return this.values.has(item); }
  toggle(item, force) {
    if (force === undefined) {
      if (this.values.has(item)) {
        this.values.delete(item);
        return false;
      }
      this.values.add(item);
      return true;
    }
    if (force) this.values.add(item);
    else this.values.delete(item);
    return Boolean(force);
  }
}

class FakeElement {
  constructor({ id = '', classes = [], dataset = {} } = {}) {
    this.id = id;
    this.dataset = { ...dataset };
    this.style = { display: '' };
    this.classList = new FakeClassList(classes);
    this.attributes = new Map();
    this.children = [];
    this.innerHTML = '';
    this.textContent = '';
    this.disabled = false;
    this.checked = false;
    this.value = '';
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
  addEventListener() {}
  removeEventListener() {}
  closest() { return null; }
  querySelectorAll(selector) {
    if (selector === '.bulk-edit-btn') {
      return this.children.filter((child) => child.classList?.contains('bulk-edit-btn'));
    }
    if (selector === '.datatable-filter-chip') return [];
    if (selector === 'input[name="bulkValue"]') {
      return this.children.filter((child) => child.dataset?.bulkValueInput === 'true');
    }
    if (selector === '.datatable-row-selected' || selector === '.row-select') return [];
    return [];
  }
  querySelector(selector) {
    const fieldMatch = selector.match(/^\[data-field="([^"]+)"\]$/);
    if (fieldMatch) {
      return this.children.find((child) => child.dataset?.field === fieldMatch[1]) || null;
    }
    if (selector.includes('input[name="bulkValue"]:checked')) {
      return this.children.find((child) => child.dataset?.bulkValueInput === 'true' && child.checked) || null;
    }
    if (selector.startsWith('input[name="bulkValue"][value="')) {
      const value = selector.match(/value="([^"]+)"/)?.[1];
      return this.children.find((child) => child.dataset?.bulkValueInput === 'true' && String(child.value) === String(value)) || null;
    }
    if (selector.startsWith('label[')) return null;
    return null;
  }
}

class FakeDocument {
  constructor() {
    this.byId = new Map();
  }
  register(element) {
    if (element.id) this.byId.set(element.id, element);
    return element;
  }
  getElementById(id) { return this.byId.get(id) || null; }
  querySelector(selector) {
    const fieldMatch = selector.match(/^\[data-field="([^"]+)"\]$/);
    if (fieldMatch) {
      for (const element of this.byId.values()) {
        const found = element.querySelector?.(selector);
        if (found) return found;
      }
    }
    return null;
  }
  querySelectorAll() { return []; }
}

const loadDataTable = () => {
  const document = new FakeDocument();
  global.document = document;
  global.window = global;
  global.DataTableModules = {};
  global.bootstrap = {
    Modal: class Modal {
      show() {}
      hide() {}
      static getInstance() { return null; }
    },
  };

  for (const file of [
    source('static', 'js', 'components', 'datatable', 'filters', 'state.js'),
    source('static', 'js', 'components', 'datatable', 'selection', 'core.js'),
    source('static', 'js', 'components', 'datatable', 'selection', 'bulk_edit.js'),
  ]) {
    vm.runInThisContext(fs.readFileSync(file, 'utf8'), { filename: file });
  }

  class DataTable {}
  global.DataTableModules['filters-state'](DataTable, global);
  global.DataTableModules.selection(DataTable, global);
  global.DataTableModules['selection-bulk-edit'](DataTable, global);
  return { DataTable, document };
};


const loadSelectionBooleanOptions = (configFile, endMarker) => {
  const text = fs.readFileSync(source(...configFile), 'utf8');
  const marker = 'selection:';
  const end = text.indexOf(endMarker);
  const start = text.lastIndexOf(marker, end);
  if (start < 0 || end < 0 || start >= end) return {};
  const body = text.slice(start + marker.length, end);
  const options = {};
  for (const match of body.matchAll(/^\s*([A-Za-z_$][\w$]*):\s*(true|false),?\s*$/gm)) {
    options[match[1]] = match[2] === 'true';
  }
  return options;
};

const matrixSelectionOptions = () => loadSelectionBooleanOptions(
  ['static', 'js', 'config', 'tableConfigs', 'matrix.js'],
  'selectionSummary:',
);

const distributionSelectionOptions = () => loadSelectionBooleanOptions(
  ['static', 'js', 'config', 'tableConfigs', 'distribution.js'],
  'selectionSummary:',
);

const createMatrixTable = () => {
  const { DataTable, document } = loadDataTable();
  const table = new DataTable();
  table.tableId = 'matrix-test';

  const container = document.register(new FakeElement({ id: 'matrix-test-container' }));
  const selectionInfo = document.register(new FakeElement({ id: 'matrix-test-selection-info' }));
  const selectionCount = document.register(new FakeElement({ id: 'matrix-test-selection-count' }));
  const bulkActions = document.register(new FakeElement({ id: 'matrix-test-bulk-actions' }));
  const activeFiltersRow = document.register(new FakeElement({ id: 'matrix-test-active-filters-row' }));
  const activeFiltersChips = document.register(new FakeElement({ id: 'matrix-test-active-filters-chips' }));
  const filterBulkActions = document.register(new FakeElement({ id: 'matrix-test-filter-bulk-actions' }));
  const selectAll = document.register(new FakeElement({ id: 'matrix-test-select-all' }));
  const tableElement = document.register(new FakeElement({ id: 'matrix-test-table' }));

  const mark = new FakeElement({ classes: ['bulk-edit-btn'], dataset: { field: 'mark' } });
  const confirm = new FakeElement({ classes: ['bulk-edit-btn'], dataset: { field: 'raspred_utz_mark', value: 'true' } });
  bulkActions.appendChild(mark);
  bulkActions.appendChild(confirm);

  container.querySelector = (selector) => selector === '#matrix-test-table' ? tableElement : null;
  container.querySelectorAll = () => [];

  table.container = container;
  table.tableElement = tableElement;
  table.config = {
    api: { url: '/api/matrix/' },
    grouping: { enabled: false, groupBy: null },
    selection: { enabled: true, type: 'multiple', allAcrossPages: true, ...matrixSelectionOptions() },
    bulkActions: [
      { field: 'mark', title: 'Buffer status' },
      { field: 'raspred_utz_mark', value: true, title: 'Подтвердить распред' },
    ],
    filterBulkActions: [{ field: 'mark', title: 'Buffer status' }],
    events: { onSelectionChange: () => {} },
  };
  table.selectedRows = new Set();
  table.selectedRowData = new Map();
  table.filters = { article_1c: '1005939' };
  table.getActiveFiltersForDisplay = () => [{
    baseField: 'article_1c', label: 'Article 1C', valueDisplay: '1005939',
  }];
  table.formatFilterValueDisplay = (_field, value) => String(value);
  table.cacheSelectedRowData = () => {};
  table.updateSelectionSummary = () => {};
  table.showNotification = () => {};
  table.bindElementTooltip = () => {};
  table.getSelectionScopeKey = table.getSelectionScopeKey?.bind(table) || (() => 'scope-current');

  return {
    table,
    document,
    elements: { selectionInfo, selectionCount, bulkActions, activeFiltersRow, activeFiltersChips, filterBulkActions, selectAll, tableElement, mark, confirm },
  };
};

const currentScopeKey = (table) => table.getSelectionScopeKey();

const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};
const deepEqual = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const extractDefinition = (text, startMarker, endMarker) => {
  const start = text.indexOf(startMarker);
  const end = text.indexOf(endMarker, start);
  if (start < 0 || end < 0) throw new Error(`Could not extract ${startMarker}`);
  return text.slice(start, end);
};

const loadDistributionStageGuards = () => {
  const text = fs.readFileSync(source('static', 'js', 'distribution', 'index.js'), 'utf8');
  const selectionStateDefinition = extractDefinition(
    text,
    '    const getDistributionSelectionStageState =',
    '    const distributionSelectionDispatchers',
  );
  const disabledReasonDefinition = extractDefinition(
    text,
    '    const getDistributionBlockDisabledReason =',
    '    function setDistributionActionDisabled',
  );
  return new Function(
    'getSelectedDistributionRows',
    'isDistributionTruthyValue',
    'getUtzPostConfirmationLockReason',
    'normalizeLookupValue',
    `
      const distributionBulkActionGroups = {
        check: 'distribution-check',
        pendingAdm: 'distribution-pending-adm',
        work: 'distribution-work',
        finalize: 'distribution-finalize',
      };
      const distributionBlockMessages = {
        selectionDataMissing: 'Не удалось получить данные выбранных строк',
        controlUnavailable: 'Блок «Контроль» доступен только для строк без подтверждения УТЗ',
        workRequiresUtz: 'Сначала подтвердите УТЗ: без подтверждения УТЗ доступен только блок «Контроль»',
        finalizeRequiresAdm: 'Блок «Завершение» доступен только после подтверждения АДМ',
      };
      ${selectionStateDefinition}
      ${disabledReasonDefinition}
      return { getDistributionSelectionStageState, getDistributionBlockDisabledReason };
    `,
  )(
    (table, ids) => (table.data || []).filter((row) => ids.includes(String(row.id))),
    (value) => value === true,
    () => '',
    (value) => String(value ?? '').trim(),
  );
};

const buildModal = (document, tableId) => {
  const modal = document.register(new FakeElement({ id: `${tableId}-bulk-edit-modal` }));
  const trueInput = new FakeElement({ dataset: { bulkValueInput: 'true' } });
  trueInput.value = 'true';
  const falseInput = new FakeElement({ dataset: { bulkValueInput: 'true' } });
  falseInput.value = 'false';
  modal.appendChild(trueInput);
  modal.appendChild(falseInput);
  document.register(new FakeElement({ id: `${tableId}-bulk-modal-title-text` }));
  document.register(new FakeElement({ id: `${tableId}-bulk-selected-count` }));
  document.register(new FakeElement({ id: `${tableId}-bulk-value-boolean` }));
  document.register(new FakeElement({ id: `${tableId}-bulk-value-custom` }));
  document.register(new FakeElement({ id: `${tableId}-bulk-preview` }));
  document.register(new FakeElement({ id: `${tableId}-bulk-confirm-btn` }));
  return modal;
};

const tests = [
  {
    name: 'current all-matching stays explicit through the filter-chips refresh path',
    run() {
      const { table, elements } = createMatrixTable();
      table.selectionAllMatching = {
        active: true,
        token: 'current-token',
        count: 125,
        excludedIds: new Set(),
        scopeKey: currentScopeKey(table),
      };
      table.updateSelectionCounter();
      table.updateActiveFilterChips();
      assert(!elements.bulkActions.classList.contains('is-filter-scope'), 'current token became filter-only after filter chips refresh');
      assert(elements.confirm.style.display === '', 'normal confirm action is hidden for current token');
      const payload = table.getCurrentBulkSelectionPayload();
      assert(payload.selection_token === 'current-token', 'current token is not used by payload');
    },
  },
  {
    name: 'scope-valid all-matching with exclusions drives count, summary and payload consistently',
    run() {
      const { table, elements } = createMatrixTable();
      const allSummary = { marker: 'all-matching' };
      table.filteredBulkSelection = {
        active: true,
        token: 'old-filter-token',
        count: 2,
        summary: { marker: 'filtered-residue' },
      };
      table.selectionAllMatching = {
        active: true,
        token: 'current-token',
        count: 5,
        excludedIds: new Set(['excluded-1']),
        scopeKey: currentScopeKey(table),
        summary: allSummary,
      };
      table.updateSelectionCounter();
      assert(elements.confirm.style.display === '', 'resident filter residue hid the current all-matching action');
      assert(table.getBulkSelectedCount() === 4, `expected count=4, got ${table.getBulkSelectedCount()}`);
      const payload = table.getCurrentBulkSelectionPayload();
      assert(payload.selection_token === 'current-token', `payload retargeted to ${payload.selection_token}`);
      assert(deepEqual(payload.excluded_ids || [], ['excluded-1']), 'all-matching exclusions were not preserved');
      assert(table.getActiveBulkSummary() === allSummary, 'summary uses a different selection authority');
    },
  },
  {
    name: 'ordinary filter-only scope still hides normal confirm action',
    run() {
      const { table, elements } = createMatrixTable();
      table.updateSelectionCounter();
      assert(table.hasFilterBulkActionScope(), 'filter-only scope was not detected');
      assert(elements.confirm.style.display === 'none', 'normal confirm action leaked into filter-only scope');
    },
  },
  {
    name: 'manual checkbox selection stays authoritative over resident filter-action residue',
    run() {
      const { table, elements } = createMatrixTable();
      table.filteredBulkSelection = { active: true, token: 'old-filter-token', count: 9 };
      table.selectedRows.add('current-1');
      table.updateSelectionCounter();
      assert(elements.confirm.style.display === '', 'manual selection lost normal action');
      assert(deepEqual(table.getCurrentBulkSelectionPayload(), { ids: ['current-1'] }), 'manual IDs did not win payload authority');
      assert(table.getBulkSelectedCount() === 1, 'manual count did not win authority');
    },
  },
  {
    name: 'stale and zero-target all-matching states cannot authorize a new action',
    run() {
      const invalidStates = [
        { active: true, token: 'stale', count: 4, excludedIds: new Set(), scopeKey: 'different-scope' },
        { active: true, token: 'zero', count: 0, excludedIds: new Set(), scopeKey: currentScopeKey(createMatrixTable().table) },
        { active: true, token: 'all-excluded', count: 2, excludedIds: new Set(['1', '2']), scopeKey: currentScopeKey(createMatrixTable().table) },
      ];
      for (const state of invalidStates) {
        const { table, elements } = createMatrixTable();
        // Scope keys are deterministic for identical Matrix fixtures; recompute the current one here.
        if (state.token !== 'stale') state.scopeKey = currentScopeKey(table);
        table.selectionAllMatching = state;
        table.updateSelectionCounter();
        assert(elements.confirm.style.display === 'none', `invalid token exposed normal action: ${state.token}`);
        const payload = table.getCurrentBulkSelectionPayload();
        assert(!payload.selection_token, `invalid token entered payload: ${state.token}`);
      }
    },
  },
  {
    name: 'filter action keeps the target captured before a later selection change during async fetch',
    async run() {
      const { table, document } = createMatrixTable();
      buildModal(document, table.tableId);
      table.config.bulkActions.push({ field: 'archive', value: true, confirmInModal: true });
      table.config.filterBulkActions.push({ field: 'archive', value: true, confirmInModal: true });
      table.resetBulkPreview = () => {};
      table.updateBulkPreview = () => {};
      table.loadBulkRecordsInfo = () => {};
      table.formatBulkNumber = (value) => String(value);
      table.getColumnFilterDisplayLabel = (field) => field;
      table.getCSRFToken = () => '';
      table.applyBulkBooleanUpdateResult = () => ({ updatedCount: 3 });
      table.cleanupModalBackdrop = () => {};
      table.canFetchFilteredBulkSelection = () => true;

      let resolveFilterSelection;
      table.fetchFilteredBulkSelection = () => new Promise((resolve) => {
        resolveFilterSelection = resolve;
      });
      const button = new FakeElement({ dataset: { field: 'archive', value: 'true' } });
      button.innerHTML = 'Archive';

      const startedAction = table.openFilteredBulkAction(button);
      await Promise.resolve();
      assert(typeof resolveFilterSelection === 'function', 'filter selection fetch did not start');

      // The bulk-action button is disabled while fetch is pending, but the table selection
      // controls are not. A later explicit selection can therefore become global state before
      // the action-local filtered selection resolves.
      table.selectionAllMatching = {
        active: true,
        token: 'later-global-B',
        count: 8,
        excludedIds: new Set(),
        scopeKey: currentScopeKey(table),
      };
      resolveFilterSelection({
        active: true,
        token: 'filter-action-A',
        count: 3,
        excludedIds: new Set(),
        summary: { marker: 'action-A' },
      });
      await startedAction;

      let sentPayload = null;
      const previousFetch = global.fetch;
      global.fetch = async (_url, options) => {
        sentPayload = JSON.parse(options.body);
        return { ok: true, async json() { return { updated_count: 3, data: [] }; } };
      };
      try {
        await table.executeBulkEdit();
      } finally {
        global.fetch = previousFetch;
      }
      assert(sentPayload?.selection_token === 'filter-action-A', `started filter action was retargeted to ${sentPayload?.selection_token}`);
    },
  },
  {
    name: 'Distribution token-only selection never makes a stage-dependent action actionable without row stage data',
    run() {
      const { DataTable, document } = loadDataTable();
      const { getDistributionSelectionStageState, getDistributionBlockDisabledReason } = loadDistributionStageGuards();
      const table = new DataTable();
      table.tableId = 'distribution-test';
      const container = document.register(new FakeElement({ id: 'distribution-test-container' }));
      const bulkActions = document.register(new FakeElement({ id: 'distribution-test-bulk-actions' }));
      const stageButton = new FakeElement({ classes: ['bulk-edit-btn'], dataset: { field: 'raspred_mark_adm', value: 'true' } });
      bulkActions.appendChild(stageButton);
      table.container = container;
      table.config = {
        api: { url: '/api/distribution/' },
        grouping: { enabled: false, groupBy: null },
        selection: { enabled: true, type: 'multiple', allAcrossPages: true, ...distributionSelectionOptions() },
        bulkActions: [{ field: 'raspred_mark_adm', value: true, title: 'Подтвердить АДМ' }],
        filterBulkActions: [],
      };
      table.selectedRows = new Set();
      table.selectedRowData = new Map();
      table.filters = {};
      table.data = [{ id: 1, raspred_mark_utz: false, raspred_mark_adm: false }];
      table.updateSelectionSummary = () => {};
      table.updateActiveFilterChips = () => {};
      table.selectionAllMatching = {
        active: true,
        token: 'all-matching-token',
        count: 1,
        excludedIds: new Set(),
        scopeKey: currentScopeKey(table),
      };
      table.updateBulkActionAvailability = () => {
        const stageState = getDistributionSelectionStageState(table);
        const reason = getDistributionBlockDisabledReason(stageState, 'distribution-pending-adm');
        if (reason) {
          stageButton.disabled = true;
          stageButton.setAttribute('aria-disabled', 'true');
        }
      };

      table.updateSelectionCounter();

      const exposed = bulkActions.style.display !== 'none' && stageButton.style.display !== 'none';
      const disabled = stageButton.disabled || stageButton.getAttribute('aria-disabled') === 'true';
      assert(!exposed || disabled, 'Distribution token-only stage action became actionable without materialized row data');
    },
  },
];

(async () => {
  const failures = [];
  for (const test of tests) {
    try {
      await test.run();
      console.log(`PASS ${test.name}`);
    } catch (error) {
      failures.push({ name: test.name, error: error?.stack || String(error) });
      console.error(`FAIL ${test.name}`);
      console.error(error?.stack || String(error));
    }
  }
  console.log(ORACLE_MARKER);
  console.log(`MATRIX_SEMANTIC_SUMMARY pass=${tests.length - failures.length} fail=${failures.length} total=${tests.length}`);
  if (failures.length > 0) process.exitCode = 1;
})();
