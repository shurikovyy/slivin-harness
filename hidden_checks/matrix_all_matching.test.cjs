/** @jest-environment jsdom */

const fs = require('node:fs');
const path = require('node:path');

const WORKSPACE = process.env.SLIVIN_HARNESS_WORKSPACE;

if (!WORKSPACE) {
  throw new Error('SLIVIN_HARNESS_WORKSPACE is not set');
}

const source = (...parts) =>
  path.join(WORKSPACE, ...parts);


describe('Matrix all-matching bulk selection contract', () => {
  let DataTable;
  let table;

  beforeAll(() => {
    window.DataTableModules = {};

    [
      source(
        'static', 'js', 'components', 'datatable',
        'filters', 'state.js'
      ),
      source(
        'static', 'js', 'components', 'datatable',
        'selection', 'core.js'
      ),
      source(
        'static', 'js', 'components', 'datatable',
        'selection', 'bulk_edit.js'
      ),
    ].forEach((sourcePath) => {
      window.eval(
        fs.readFileSync(sourcePath, 'utf8')
      );
    });

    DataTable = class DataTable {};

    window.DataTableModules['filters-state'](
      DataTable,
      window,
    );

    window.DataTableModules.selection(
      DataTable,
      window,
    );

    window.DataTableModules['selection-bulk-edit'](
      DataTable,
      window,
    );
  });


  beforeEach(() => {
    document.body.innerHTML = `
      <div id="matrix-test-container">
        <div class="datatable-headbar"></div>

        <div id="matrix-test-selection-info">
          <span id="matrix-test-selection-count"></span>
        </div>

        <div id="matrix-test-bulk-actions">
          <button
            class="bulk-edit-btn"
            data-field="mark"
          >
            Buffer status
          </button>

          <button
            class="bulk-edit-btn"
            data-field="raspred_utz_mark"
            data-value="true"
          >
            Подтвердить распред
          </button>
        </div>

        <div id="matrix-test-active-filters-row">
          <div id="matrix-test-active-filters-chips"></div>
          <div id="matrix-test-filter-bulk-actions"></div>
        </div>

        <input
          id="matrix-test-select-all"
          type="checkbox"
        >

        <table id="matrix-test-table">
          <tbody>
            <tr
              class="datatable-row"
              data-id="current-1"
            >
              <td>
                <input
                  class="row-select"
                  type="checkbox"
                >
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    `;

    table = new DataTable();

    table.tableId = 'matrix-test';
    table.container =
      document.getElementById('matrix-test-container');
    table.tableElement =
      document.getElementById('matrix-test-table');

    table.config = {
      api: {
        url: '/api/matrix/',
      },

      grouping: {
        enabled: false,
        groupBy: null,
      },

      selection: {
        enabled: true,
        type: 'multiple',
        allAcrossPages: true,
      },

      bulkActions: [
        {
          field: 'mark',
          title: 'Buffer status',
        },
        {
          field: 'raspred_utz_mark',
          value: true,
          title: 'Подтвердить распред',
        },
      ],

      filterBulkActions: [
        {
          field: 'mark',
          title: 'Buffer status',
        },
      ],

      events: {
        onSelectionChange: jest.fn(),
      },
    };

    table.selectedRows = new Set();
    table.selectedRowData = new Map();

    table.filters = {
      article_1c: '1005939',
    };

    table.getActiveFiltersForDisplay =
      jest.fn(() => [
        {
          baseField: 'article_1c',
          label: 'Article 1C',
          valueDisplay: '1005939',
        },
      ]);

    table.formatFilterValueDisplay =
      jest.fn((field, value) => String(value));

    table.cacheSelectedRowData = jest.fn();
    table.updateSelectionSummary = jest.fn();
    table.showNotification = jest.fn();
  });


  test(
    '"Выбрать все N найденных" остаётся явным selection scope',
    () => {
      table.selectionAllMatching = {
        active: true,
        token: 'all-matching-token',
        count: 125,
        excludedIds: new Set(),
      };

      table.updateSelectionCounter();

      const bulkActions =
        document.getElementById(
          'matrix-test-bulk-actions'
        );

      const confirm =
        document.querySelector(
          '[data-field="raspred_utz_mark"]'
        );

      expect(
        bulkActions.classList.contains(
          'is-filter-scope'
        )
      ).toBe(false);

      expect(confirm.style.display).toBe('');

      const payload =
        table.getCurrentBulkSelectionPayload();

      expect(
        payload.selection_token
      ).toBe('all-matching-token');

      // Empty exclusions may be serialized as an absent field or [].
      expect(
        payload.excluded_ids ?? []
      ).toEqual([]);
    }
  );


  test(
    'обычный filter-only scope по-прежнему не показывает Подтвердить распред',
    () => {
      table.updateSelectionCounter();

      const confirm =
        document.querySelector(
          '[data-field="raspred_utz_mark"]'
        );

      expect(
        table.hasFilterBulkActionScope()
      ).toBe(true);

      expect(confirm.style.display).toBe('none');
    }
  );


  test(
    'ручной checkbox selection по-прежнему показывает Подтвердить распред',
    () => {
      table.selectedRows.add('current-1');

      table.updateSelectionCounter();

      const confirm =
        document.querySelector(
          '[data-field="raspred_utz_mark"]'
        );

      expect(
        table.hasFilterBulkActionScope()
      ).toBe(false);

      expect(confirm.style.display).toBe('');
    }
  );
});