import { useAppStore, type Language } from '@shared/stores/appStore'
import type { ParamSchema } from '@shared/types/runtime.d'

type ParamLocaleText = {
  label?: string
  tooltip?: string
}

type OptionLocaleText = {
  label?: string
}

type LocalizedOption = NonNullable<ParamSchema['options']>[number] & {
  i18n?: Record<string, OptionLocaleText>
}

export type LocalizedParamSchema = Omit<ParamSchema, 'options'> & {
  /**
   * Presentation-only translations. IDs, values, defaults and show_if stay in
   * the source schema and are never translated.
   */
  i18n?: Record<string, ParamLocaleText>
  options?: LocalizedOption[]
}

export function localizedParamLabel(param: LocalizedParamSchema, language: Language): string {
  return param.i18n?.[language]?.label ?? param.label
}

export function localizedParamTooltip(param: LocalizedParamSchema, language: Language): string | undefined {
  return param.i18n?.[language]?.tooltip ?? param.tooltip
}

export function localizedOptionLabel(option: LocalizedOption, language: Language): string {
  return option.i18n?.[language]?.label ?? option.label ?? String(option.value)
}

/**
 * Return a ParamSchema-compatible view whose display fields resolve against the
 * current app language. The machine-facing fields remain untouched.
 *
 * Getters intentionally read the Zustand store at access time. Workflow node
 * pack lists are memoized, so this keeps their labels live when the language is
 * changed without rebuilding the workflow graph or changing parameter values.
 */
export function localizeParamSchema(schema: ParamSchema[]): ParamSchema[] {
  return (schema as LocalizedParamSchema[]).map((param) => {
    const options = param.options?.map((option) => ({
      ...option,
      get label() {
        return localizedOptionLabel(option, useAppStore.getState().language)
      },
    }))

    return {
      ...param,
      options,
      get label() {
        return localizedParamLabel(param, useAppStore.getState().language)
      },
      get tooltip() {
        return localizedParamTooltip(param, useAppStore.getState().language)
      },
    }
  })
}
