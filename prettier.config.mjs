/** @type {import("prettier").Config} */
export default {
  semi: true,
  singleQuote: true,
  trailingComma: 'all',
  printWidth: 100,
  tabWidth: 2,
  arrowParens: 'always',
  endOfLine: 'lf',
  overrides: [
    { files: '*.md', options: { proseWrap: 'always', printWidth: 90 } },
    { files: '*.{yml,yaml}', options: { singleQuote: false } },
  ],
};
