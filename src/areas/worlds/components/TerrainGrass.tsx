import { useEffect, useMemo } from 'react'
import { useFrame } from '@react-three/fiber'

import { buildTerrainGrass, disposeTerrainGrass } from '../runtime/grassField'
import type { BuiltTerrain } from '../runtime/terrain'
import type { WorldSpec } from '../runtime/types'

interface TerrainGrassProps {
  spec: WorldSpec
  terrain: BuiltTerrain
}

/**
 * Legacy planning/test preview only. Production WorldCanvas loads Blender
 * workspace artifacts and never mounts this browser-generated field.
 */
export default function TerrainGrass({ spec, terrain }: TerrainGrassProps): JSX.Element | null {
  const field = useMemo(() => buildTerrainGrass(spec, terrain), [spec, terrain])

  useEffect(() => () => disposeTerrainGrass(field), [field])

  useFrame(({ clock }) => {
    field.mesh.material.uniforms.uTime.value = clock.getElapsedTime()
  })

  if (field.bladeCount === 0) return null
  return <primitive object={field.mesh} />
}
