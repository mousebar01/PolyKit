import { Canvas } from '@react-three/fiber'
import { OrbitControls, useGLTF } from '@react-three/drei'
import * as THREE from 'three'
import { Component, Suspense, useMemo, useRef, type ReactNode } from 'react'
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib'
import type { AssetProtoSpec, Instance, WorldSpec } from '../runtime/types'
import type { WorldAssetArtifact } from '../types'
import { workspaceUrl } from '../worldApi'

interface WorldCanvasProps {
  spec: WorldSpec
  instances: Instance[]
  selectedProtoId: string | null
  artifacts?: Record<string, WorldAssetArtifact>
  backgroundColor?: string
}

export const WORLD_VIEWER_BACKGROUND_COLOR = '#151b23'

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
    const center = bounds.getCenter(new THREE.Vector3())
    const height = Math.max(size.y, 0.001)
    // World instances use a semantic placement centre on X/Z and a contact
    // height on Y. Blender assets may have been authored with an arbitrary
    // object origin, so normalize all three axes before applying the instance
    // transform. This keeps Blender-authored GLBs aligned with ScenePlan
    // composition and prevents off-centre assets from drifting in the world.
    clone.position.x -= center.x
    clone.position.y -= bounds.min.y
    clone.position.z -= center.z
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

function WorldScene({ spec, instances, selectedProtoId, artifacts = {}, backgroundColor }: WorldCanvasProps): JSX.Element {
  const prototypes = useMemo(() => new Map(spec.assets.map((asset) => [asset.id, asset])), [spec.assets])
  const sun = useMemo(() => {
    const azimuth = THREE.MathUtils.degToRad(spec.sky.sunAzimuth)
    const elevation = THREE.MathUtils.degToRad(Math.max(8, spec.sky.sunElevation))
    return [Math.cos(azimuth) * Math.cos(elevation) * spec.size * 0.55, Math.sin(elevation) * spec.size * 0.55, Math.sin(azimuth) * Math.cos(elevation) * spec.size * 0.55] as [number, number, number]
  }, [spec.sky, spec.size])
  return (
    <>
      <color attach="background" args={[backgroundColor ?? spec.sky.fogColor]} />
      <fog attach="fog" args={[backgroundColor ?? spec.sky.fogColor, spec.size * 0.2, spec.size * 1.3]} />
      <ambientLight color={spec.sky.ambientColor} intensity={spec.sky.ambientIntensity * 1.2} />
      <directionalLight position={sun} color={spec.sky.sunColor} intensity={spec.sky.sunIntensity} castShadow />
      {/* Production meshes come from Blender workspace artifacts. The World
          canvas is intentionally an artifact loader/interaction surface; it
          must not synthesize terrain, props, or other final geometry. */}
      {spec.assets.map((asset) => {
        const prototype = prototypes.get(asset.id)
        const assetInstances = instances.filter((instance) => instance.protoId === asset.id)
        if (!prototype || assetInstances.length === 0) return null
        const workspacePath = artifacts[asset.id]?.mesh?.workspace_path
        if (!workspacePath) return null
        const url = workspaceUrl(workspacePath)
        return (
          <ArtifactErrorBoundary key={`${asset.id}:${workspacePath}`} fallback={null}>
            <Suspense fallback={null}>
              <ArtifactInstances instances={assetInstances} prototype={prototype} url={url} selected={selectedProtoId === asset.id} />
            </Suspense>
          </ArtifactErrorBoundary>
        )
      })}
      <gridHelper args={[spec.size, 28, '#5f6a69', '#394544']} position-y={0} />
    </>
  )
}

export default function WorldCanvas(props: WorldCanvasProps): JSX.Element {
  const controlsRef = useRef<OrbitControlsImpl | null>(null)
  const extent = Math.max(props.spec.size, 1)

  return (
    <div
      className="relative h-full w-full focus-within:ring-1 focus-within:ring-ring/60"
      onPointerDown={(event) => {
        if (event.target instanceof HTMLCanvasElement) event.currentTarget.querySelector<HTMLElement>('[data-viewport-canvas]')?.focus()
      }}
      onDoubleClick={(event) => {
        if (!(event.target instanceof HTMLCanvasElement)) return
        event.preventDefault()
        controlsRef.current?.reset()
      }}
      onKeyDown={(event) => {
        if (event.key !== 'Home') return
        event.preventDefault()
        controlsRef.current?.reset()
      }}
    >
      <Canvas
        data-viewport-canvas="true"
        tabIndex={0}
        className="outline-none"
        camera={{ position: [extent * 1.4, extent * 0.9, extent * 1.6], fov: 42, near: 0.01, far: Math.max(1200, extent * 20) }}
        dpr={[1, 1.7]}
        shadows
        gl={{ antialias: true, powerPreference: 'high-performance' }}
      >
        <WorldScene {...props} />
        <OrbitControls
          ref={controlsRef}
          makeDefault
          enableDamping
          dampingFactor={0.08}
          enablePan
          enableZoom
          enableRotate
          keyEvents
          zoomToCursor
          mouseButtons={{ LEFT: undefined, MIDDLE: THREE.MOUSE.ROTATE, RIGHT: undefined }}
          maxPolarAngle={Math.PI * 0.48}
          minDistance={Math.max(1, extent * 0.08)}
          maxDistance={Math.max(100, extent * 8)}
          target={[0, 0, 0]}
        />
      </Canvas>
    </div>
  )
}
