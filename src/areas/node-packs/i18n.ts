import { useI18n, type TranslationKey } from '@shared/i18n'

export type NodePackTranslationKey = Extract<TranslationKey, `nodePacks.${string}`>
export type NodePacksUiTranslationKey = TranslationKey

/** Node Packs UI uses the canonical nodePacks.* dictionary namespace. */
export function useNodePacksI18n() {
  return useI18n()
}
