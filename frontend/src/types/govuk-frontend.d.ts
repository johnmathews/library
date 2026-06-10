// govuk-frontend v6 ships no TypeScript declarations; declare the surface we
// use. Constructors throw SupportError when `<body class="govuk-frontend-
// supported">` is absent — guard with `isSupported()` before instantiating.
declare module 'govuk-frontend' {
  export function isSupported($scope?: HTMLElement | null): boolean

  export class Component<RootType extends HTMLElement = HTMLElement> {
    constructor($root: Element | null)
    static moduleName: string
    get $root(): RootType
  }

  export class Button extends Component {
    constructor($root: Element | null, config?: { preventDoubleClick?: boolean })
    static moduleName: string
  }

  export class ErrorSummary extends Component {
    constructor($root: Element | null, config?: { disableAutoFocus?: boolean })
    static moduleName: string
  }

  export class FileUpload extends Component {
    constructor($root: Element | null, config?: object)
    static moduleName: string
  }

  export class NotificationBanner extends Component {
    constructor($root: Element | null, config?: { disableAutoFocus?: boolean })
    static moduleName: string
  }

  export class ServiceNavigation extends Component {
    constructor($root: Element | null)
    static moduleName: string
  }

  export class SkipLink extends Component {
    constructor($root: Element | null)
    static moduleName: string
  }

  export const version: string
}
