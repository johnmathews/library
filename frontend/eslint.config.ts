import { globalIgnores } from 'eslint/config'
import { defineConfigWithVueTs, vueTsConfigs } from '@vue/eslint-config-typescript'
import pluginVue from 'eslint-plugin-vue'
import pluginVueA11y from 'eslint-plugin-vuejs-accessibility'

export default defineConfigWithVueTs(
  {
    name: 'app/files-to-lint',
    files: ['**/*.{ts,mts,tsx,vue}'],
  },

  globalIgnores(['**/dist/**', '**/dist-ssr/**', '**/coverage/**']),

  pluginVue.configs['flat/essential'],

  // The a11y work in this app — 172 aria-* attributes, 54 roles, a native
  // <dialog> with focus restore — had nothing protecting it. This plugin is that
  // protection.
  //
  // Deliberately NOT paired with an upgrade from vue's flat/essential to
  // flat/recommended, which the plan proposed as "near-zero cost". Measured, it
  // is not: flat/recommended adds 1,598 pure-formatting violations
  // (html-indent, max-attributes-per-line, singleline-html-element-content-
  // newline, …) against 55 real a11y ones. That is a vast diff for no reader
  // benefit and it would bury the signal this plugin exists to surface. The
  // formatting upgrade is a separate decision; the a11y rules are the point.
  ...pluginVueA11y.configs['flat/recommended'],

  {
    name: 'app/a11y-ratchet',
    files: ['**/*.vue'],
    rules: {
      // RATCHET — each entry names what removes it. Landing the plugin with the
      // noisiest rules off, rather than fixing 39 call sites in one commit, is
      // the same shape as W15's mypy overrides: the gate is real from its first
      // run for everything else, and new code cannot regress the rules that ARE
      // on.
      //
      // label-has-for (31): the rule wants an explicit for/id pair even where
      // the control is NESTED inside its <label>, which is valid HTML and is
      // this codebase's prevailing pattern. Enabling it as-is would mean
      // inventing 31 ids for no assistive-technology gain. EXIT: configure it
      // with `required: { some: ['nesting', 'id'] }` and enable, once someone
      // has confirmed each site really does nest.
      'vuejs-accessibility/label-has-for': 'off',
      // no-static-element-interactions (8): each needs a real decision — either
      // the div becomes a <button>, or it gains role + tabindex + key handlers.
      // Mechanical suppression would be wrong. EXIT: convert the eight sites,
      // starting with the ones that are already visually buttons.
      'vuejs-accessibility/no-static-element-interactions': 'off',

      // NOT a ratchet — this one is off on purpose and should stay off.
      // It fires on `role="list"` on a <ul>, calling it redundant. It is not:
      // Tailwind preflight sets `list-style: none` on every ul (preflight.css
      // line 200), and Safari drops list semantics from an unmarkered list, so
      // the explicit role is what restores them for VoiceOver. Obeying the rule
      // here would silently remove list semantics to satisfy a linter. The
      // genuinely redundant case in this codebase (`<fieldset role="group">`)
      // was fixed rather than suppressed.
      'vuejs-accessibility/no-redundant-roles': 'off',

      'vuejs-accessibility/form-control-has-label': 'error',

      // click-events-have-key-events (4): the sites are a native <dialog>, a
      // modal backdrop, a delegated container click that closes the sidebar, and
      // a thread <li>. Most are already keyboard-operable by other means —
      // <dialog> handles Escape natively, and Enter on a nested <a> fires click
      // — so blanket key handlers would be noise. EXIT: review the four
      // individually; the thread <li> is the one likely to need a real button.
      'vuejs-accessibility/click-events-have-key-events': 'off',

      'vuejs-accessibility/mouse-events-have-key-events': 'error',
    },
  },

  vueTsConfigs.recommended,
)
