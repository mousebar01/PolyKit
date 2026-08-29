import type { TranslationKey } from '@shared/i18n'
import type { AssetCapability } from '../../shared/types/assetLibrary'

export const CAPABILITY_LABEL_KEYS: Record<AssetCapability, TranslationKey> = {
  image: 'assets.capabilityImages',
  mesh: 'assets.capabilityMesh',
  'rigged-mesh': 'assets.capabilityRiggedMesh',
  'animation-motion': 'assets.capabilityAnimations',
  'landmarks-sidecar': 'assets.capabilityLandmarks',
  'generated-world': 'assets.capabilityGeneratedWorlds',
  'scene-manifest': 'assets.capabilitySceneManifests',
}
