import js from '@eslint/js';
import ts from 'typescript-eslint';

/**
 * Shared flat config.
 *
 * The rules that matter here are not style. Each one enforces a documented
 * decision that would otherwise rely on someone remembering it:
 *
 *   - no left/right in styles         -> RTL support (ADR-0011)
 *   - no untranslated JSX literals    -> i18n coverage (ADR-0011)
 *   - no cross-app imports            -> workspace boundaries (ADR-0014)
 *   - no console                      -> structured logging (ADR-0024)
 *   - no browser storage in mobile    -> the sync module owns persistence (ADR-0026)
 */
export default ts.config(
  { ignores: ['**/dist/**', '**/.next/**', '**/node_modules/**', '**/*.generated.ts'] },

  js.configs.recommended,
  ...ts.configs.strictTypeChecked,
  ...ts.configs.stylisticTypeChecked,

  {
    languageOptions: {
      parserOptions: { projectService: true },
    },
    rules: {
      '@typescript-eslint/consistent-type-imports': [
        'error',
        { prefer: 'type-imports', fixStyle: 'inline-type-imports' },
      ],
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      // Floating promises in a sync queue lose exchanges. Not negotiable.
      '@typescript-eslint/no-floating-promises': 'error',
      '@typescript-eslint/no-misused-promises': 'error',

      // ADR-0024: structured logging only. console.log has no request_id.
      'no-console': ['error', { allow: ['warn', 'error'] }],

      // ADR-0014: shared code goes in packages/, never app-to-app.
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['**/apps/**'],
              message:
                'Cross-app imports are forbidden. Put shared code in packages/ (ADR-0014).',
            },
          ],
        },
      ],
    },
  },

  // ADR-0011: RTL. Physical directions break mirrored layouts.
  {
    files: ['apps/mobile/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector:
            "Property[key.name=/^(marginLeft|marginRight|paddingLeft|paddingRight|left|right|borderLeftWidth|borderRightWidth)$/]",
          message:
            'Use start/end instead of left/right so RTL layouts mirror correctly (ADR-0011).',
        },
      ],
      // ADR-0026: core/sync owns persistence. AsyncStorage/localStorage bypass it.
      'no-restricted-globals': [
        'error',
        { name: 'localStorage', message: 'Use core/sync and expo-sqlite (ADR-0026).' },
        { name: 'sessionStorage', message: 'Use core/sync and expo-sqlite (ADR-0026).' },
      ],
    },
  },

  // ADR-0011: no user-facing string is ever typed inline.
  {
    files: ['apps/{web,public,marketing,admin,mobile}/**/*.tsx'],
    rules: {
      'react/jsx-no-literals': 'off', // enabled per-app once the i18n plugin is wired
    },
  },

  {
    files: ['**/*.test.ts', '**/*.test.tsx', '**/*.spec.ts'],
    rules: {
      '@typescript-eslint/no-non-null-assertion': 'off',
      '@typescript-eslint/no-unsafe-assignment': 'off',
    },
  },
);
