/**
 * Stub for govuk-frontend — replaces the real package during the Mosaic
 * reskin (Phase 0).  All govuk components are wired to no-ops so that the
 * build succeeds while the govuk Vue wrappers are awaiting replacement in
 * later phases.  Nothing here runs in the real UI; the govuk wrapper
 * components will be deleted before the app ships.
 */

export function isSupported(_$scope?: HTMLElement | null): boolean {
  return false
}

export class Component {
  static moduleName = ''
  constructor(_$root: Element | null) {}
  get $root(): HTMLElement {
    return document.body
  }
}

export class Button extends Component {
  static moduleName = 'govuk-button'
  constructor($root: Element | null, _config?: { preventDoubleClick?: boolean }) {
    super($root)
  }
}

export class ErrorSummary extends Component {
  static moduleName = 'govuk-error-summary'
  constructor($root: Element | null, _config?: { disableAutoFocus?: boolean }) {
    super($root)
  }
}

export class FileUpload extends Component {
  static moduleName = 'govuk-file-upload'
  constructor($root: Element | null, _config?: object) {
    super($root)
  }
}

export class NotificationBanner extends Component {
  static moduleName = 'govuk-notification-banner'
  constructor($root: Element | null, _config?: { disableAutoFocus?: boolean }) {
    super($root)
  }
}

export class ServiceNavigation extends Component {
  static moduleName = 'govuk-service-navigation'
  constructor($root: Element | null) {
    super($root)
  }
}

export class SkipLink extends Component {
  static moduleName = 'govuk-skip-link'
  constructor($root: Element | null) {
    super($root)
  }
}

export const version = '0.0.0-stub'
