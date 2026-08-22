const path = require('node:path');

module.exports = {
  rootDir: path.resolve(__dirname),
  testEnvironment: 'jsdom',
  testMatch: ['**/*.test.cjs'],
  clearMocks: true,
};
