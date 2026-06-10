import { onBeforeUnmount, onMounted, shallowRef, type Ref, type ShallowRef } from 'vue'
import { isSupported } from 'govuk-frontend'

interface GovukComponentConstructor<InstanceType, ConfigType> {
  new ($root: Element | null, config?: ConfigType): InstanceType
  moduleName: string
}

/**
 * Initialise a govuk-frontend JS component on a template ref.
 *
 * Instantiates in `onMounted` (skipped when govuk-frontend reports the
 * environment unsupported — progressive enhancement) and cleans up on
 * unmount. govuk-frontend v6 components have no destroy() method; cleanup
 * means dropping the instance and removing the `data-<module>-init` marker
 * so a re-used root element could be initialised again.
 */
export function useGovukComponent<InstanceType, ConfigType>(
  root: Ref<HTMLElement | null>,
  ComponentClass: GovukComponentConstructor<InstanceType, ConfigType>,
  config?: ConfigType,
): ShallowRef<InstanceType | null> {
  const instance = shallowRef<InstanceType | null>(null)

  onMounted(() => {
    if (root.value && isSupported()) {
      instance.value = new ComponentClass(root.value, config)
    }
  })

  onBeforeUnmount(() => {
    root.value?.removeAttribute(`data-${ComponentClass.moduleName}-init`)
    instance.value = null
  })

  return instance
}
