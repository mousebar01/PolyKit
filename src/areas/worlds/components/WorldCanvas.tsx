import { Canvas } from '@react-three/fiber'
import { OrbitControls, useGLTF } from '@react-three/drei'
import * as THREE from 'three'
import { Component, Suspense, useMemo, type ReactNode } from 'react'
import type { BuiltTerrain } from '../runtime/terrain'
import type { AssetProtoSpec, Instance, WorldSpec } from '../runtime/types'
import { buildProceduralGeometry, proceduralMaterial } from '../runtime/procedural'
import type { WorldAssetArtifact } from '../types'
import { workspaceUrl } from '../worldApi'

interface WorldCanvasProps {
  spec: WorldSpec
  terrain: BuiltTerrain
  instances: Instance[]
  selectedProtoId: string | null
  artifacts?: Record<string, WorldAssetArtifact>
}

const FALLBACK_COLORS = ['#77869b', '#8e9f74', '#b4966a', '#526f63', '#7b7b75']

function TerrainMesh({ spec, terrain }: { spec: WorldSpec; terrain: BuiltTerrain }): JSX.Element {
  const geometry = useMemo(() => {
    const result = new THREE.PlaneGeometry(spec.size, spec.size, terrain.res - 1, terrain.res - 1)
    result.rotateX(-Math.PI / 2)
    const position = result.getAttribute('position')
    const colors = new Float32Array(position.count * 3)
    for (let index = 0; index < position.count; index += 1) {
      const height = terrain.heights[index] ?? 0
      position.setY(index, height)
      const regionIndex = terrain.dominant[index]
      const color = new THREE.Color(
        regionIndex >= 0
          ? spec.regions[regionIndex]?.material.color ?? FALLBACK_COLORS[regionIndex % FALLBACK_COLORS.length]
          : '#69756b',
      )
      const shade = THREE.MathUtils.clamp(0.78 + (height - terrain.minHeight) / Math.max(1, terrain.maxHeight - terrain.minHeight) * 0.28, 0.68, 1.05)
      color.multiplyScalar(shade)
      colors[index * 3] = color.r
      colors[index * 3 + 1] = color.g
      colors[index * 3 + 2] = color.b
    }
    result.setAttribute('color', new THREE.BufferAttribute(colors, 3))
    position.needsUpdate = true
    result.computeVertexNormals()
    return result
  }, [spec, terrain])

  return (
    <mesh geometry={geometry} receiveShadow>
      <meshStandardMaterial vertexColors roughness={0.94} metalness={0.02} />
    </mesh>
  )
}

function Water({ spec }: { spec: WorldSpec }): JSX.Element {
  return (
    <mesh rotation-x={-Math.PI / 2} position-y={spec.seaLevel - 0.25}>
      <planeGeometry args={[spec.size * 1.12, spec.size * 1.12]} />
      <meshStandardMaterial color="#315d72" transparent opacity={0.62} roughness={0.18} metalness={0.08} />
    </mesh>
  )
}

function ScatterInstance({ instance, prototype, geometry, selected }: { instance: Instance; prototype: AssetProtoSpec; geometry: THREE.BufferGeometry; selected: boolean }): JSX.Element {
  const material = useMemo(() => {
    const next = proceduralMaterial(selected ? '#d8a958' : undefined)
    next.color.set(selected ? '#e6c77a' : '#ffffff')
    return next
  }, [selected])
  return (
    <group
      position={instance.position}
      rotation={instance.rotation}
      scale={instance.scale * prototype.targetHeight}
    >
      <mesh geometry={geometry} material={material} castShadow receiveShadow />
    </group>
  )
}

function prepareArtifactMaterials(scene: THREE.Object3D): void {
  scene.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return
    // Object3D.clone(true) keeps material references shared with the loader
    // cache. Clone them per instance so selection highlighting never changes
    // every copy of the same GLB.
    const sourceMaterials = Array.isArray(child.material) ? child.material : [child.material]
    const materials = sourceMaterials.map((material) => material.clone())
    child.material = Array.isArray(child.material) ? materials : materials[0]
    for (const material of materials) {
      const textured = material as THREE.Material & { map?: THREE.Texture | null; emissiveMap?: THREE.Texture | null; color?: THREE.Color }
      for (const texture of [textured.map, textured.emissiveMap]) {
        if (!texture) continue
        texture.colorSpace = THREE.SRGBColorSpace
        texture.needsUpdate = true
      }
      material.side = THREE.DoubleSide
      material.needsUpdate = true
    }
  })
}

function ArtifactInstance({ instance, prototype, url, selected }: { instance: Instance; prototype: AssetProtoSpec; url: string; selected: boolean }): JSX.Element {
  const { scene } = useGLTF(url)
  const model = useMemo(() => {
    const clone = scene.clone(true)
    prepareArtifactMaterials(clone)
    const bounds = new THREE.Box3().setFromObject(clone)
    const size = bounds.getSize(new THREE.Vector3())
    const height = Math.max(size.y, 0.001)
    clone.position.y -= bounds.min.y
    if (selected) {
      clone.traverse((child) => {
        if (!(child instanceof THREE.Mesh)) return
        const materials = Array.isArray(child.material) ? child.material : [child.material]
        materials.forEach((material) => {
          const colorMaterial = material as THREE.Material & { color?: THREE.Color }
          if (colorMaterial.color) colorMaterial.color.set('#e6c77a')
        })
      })
    }
    return { clone, height }
  }, [scene, selected])

  return (
    <group
      position={instance.position}
      rotation={instance.rotation}
      scale={(instance.scale * prototype.targetHeight) / model.height}
    >
      <primitive object={model.clone} dispose={null} />
    </group>
  )
}

function ArtifactInstances({ instances, prototype, url, selected }: { instances: Instance[]; prototype: AssetProtoSpec; url: string; selected: boolean }): JSX.Element {
  return (
    <>
      {instances.map((instance) => (
        <ArtifactInstance key={instance.id} instance={instance} prototype={prototype} url={url} selected={selected} />
      ))}
    </>
  )
}

class ArtifactErrorBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state: { failed: boolean } = { failed: false }

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true }
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}

function ProceduralInstances({ instances, prototype, geometry, selected }: { instances: Instance[]; prototype: AssetProtoSpec; geometry: THREE.BufferGeometry; selected: boolean }): JSX.Element {
  return (
    <>
      {instances.map((instance) => (
        <ScatterInstance key={instance.id} instance={instance} prototype={prototype} geometry={geometry} selected={selected} />
      ))}
    </>
  )
}

function WorldScene({ spec, terrain, instances, selectedProtoId, artifacts = {} }: WorldCanvasProps): JSX.Element {
  const prototypes = useMemo(() => new Map(spec.assets.map((asset) => [asset.id, asset])), [spec.assets])
  const geometries = useMemo(() => {
    const map = new Map<string, THREE.BufferGeometry>()
    for (const asset of spec.assets) {
      if (asset.proceduralHint) map.set(asset.id, buildProceduralGeometry(asset.proceduralHint, asset.id))
    }
    return map
  }, [spec.assets])
  const sun = useMemo(() => {
    const azimuth = THREE.MathUtils.degToRad(spec.sky.sunAzimuth)
    const elevation = THREE.MathUtils.degToRad(Math.max(8, spec.sky.sunElevation))
    return [Math.cos(azimuth) * Math.cos(elevation) * spec.size * 0.55, Math.sin(elevation) * spec.size * 0.55, Math.sin(azimuth) * Math.cos(elevation) * spec.size * 0.55] as [number, number, number]
  }, [spec.sky, spec.size])
  return (
    <>
      <color attach="background" args={[spec.sky.fogColor]} />
      <fog attach="fog" args={[spec.sky.fogColor, spec.size * 0.2, spec.size * 1.3]} />
      <ambientLight color={spec.sky.ambientColor} intensity={spec.sky.ambientIntensity * 1.2} />
      <directionalLight position={sun} color={spec.sky.sunColor} intensity={spec.sky.sunIntensity} castShadow />
      <TerrainMesh spec={spec} terrain={terrain} />
      <Water spec={spec} />
      {spec.assets.map((asset) => {
        const prototype = prototypes.get(asset.id)
        const geometry = geometries.get(asset.id)
        const assetInstances = instances.filter((instance) => instance.protoId === asset.id)
        if (!prototype || assetInstances.length === 0) return null
        const workspacePath = artifacts[asset.id]?.mesh?.workspace_path
        if (!workspacePath) {
          return geometry
            ? <ProceduralInstances key={asset.id} instances={assetInstances} prototype={prototype} geometry={geometry} selected={selectedProtoId === asset.id} />
            : null
        }
        const url = workspaceUrl(workspacePath)
        const fallback = geometry
          ? <ProceduralInstances instances={assetInstances} prototype={prototype} geometry={geometry} selected={selectedProtoId === asset.id} />
          : null
        return (
          <ArtifactErrorBoundary key={`${asset.id}:${workspacePath}`} fallback={fallback}>
            <Suspense fallback={fallback}>
              <ArtifactInstances instances={assetInstances} prototype={prototype} url={url} selected={selectedProtoId === asset.id} />
            </Suspense>
          </ArtifactErrorBoundary>
        )
      })}
      <gridHelper args={[spec.size, 28, '#5f6a69', '#394544']} position-y={terrain.minHeight - 0.08} />
    </>
  )
}

export default function WorldCanvas(props: WorldCanvasProps): JSX.Element {
  return (
    <Canvas
      camera={{ position: [150, 120, 180], fov: 42, near: 0.1, far: 1200 }}
      dpr={[1, 1.7]}
      shadows
      gl={{ antialias: true, powerPreference: 'high-performance' }}
    >
      <WorldScene {...props} />
      <OrbitControls makeDefault enableDamping dampingFactor={0.08} maxPolarAngle={Math.PI * 0.48} minDistance={30} maxDistance={480} target={[0, 0, 0]} />
    </Canvas>
  )
}
