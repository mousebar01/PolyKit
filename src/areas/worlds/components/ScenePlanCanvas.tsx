import { Component, Suspense, useMemo, type ReactNode } from 'react'
import { Canvas } from '@react-three/fiber'
import { OrbitControls, useGLTF } from '@react-three/drei'
import * as THREE from 'three'
import type { ScenePlan, ScenePlanInstance, ScenePlanObject } from '../runtime/scenePlan'
import { workspaceUrl } from '../worldApi'

interface ScenePlanCanvasProps {
  plan: ScenePlan
  backgroundColor?: string
  /** Artifact references attached after the plan was compiled. */
  artifacts?: Record<string, { mesh?: { workspace_path?: string } | null }>
}

const ROLE_COLORS: Record<string, string> = {
  room: '#718096',
  background: '#718096',
  context: '#aa8f67',
  hero: '#d39b56',
  manipulated: '#6ea4c7',
  distractor: '#829b78',
}

function roleColor(role: string): string {
  return ROLE_COLORS[role] ?? '#8b93a1'
}

function PlanBox({ instance, object }: { instance: ScenePlanInstance; object: ScenePlanObject }): JSX.Element {
  const dimensions = object.size.map((value) => Math.max(0.05, value * instance.scale)) as [number, number, number]
  const container = object.role === 'room' || object.role === 'background'
  return (
    <group position={instance.position} rotation={instance.rotation}>
      <mesh position-y={dimensions[1] / 2} castShadow={!container} receiveShadow={!container}>
        <boxGeometry args={dimensions} />
        <meshStandardMaterial
          color={roleColor(object.role)}
          transparent={container}
          opacity={container ? 0.08 : 0.92}
          roughness={container ? 1 : 0.78}
          metalness={0.02}
          wireframe={container}
        />
      </mesh>
    </group>
  )
}

function prepareModel(scene: THREE.Object3D): { clone: THREE.Object3D; size: THREE.Vector3; center: THREE.Vector3; minY: number } {
  const clone = scene.clone(true)
  clone.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return
    const materials = Array.isArray(child.material) ? child.material : [child.material]
    materials.forEach((material) => {
      material.side = THREE.DoubleSide
      material.needsUpdate = true
    })
  })
  const bounds = new THREE.Box3().setFromObject(clone)
  return { clone, size: bounds.getSize(new THREE.Vector3()), center: bounds.getCenter(new THREE.Vector3()), minY: bounds.min.y }
}

function PlanAsset({ instance, object, url }: { instance: ScenePlanInstance; object: ScenePlanObject; url: string }): JSX.Element {
  const { scene } = useGLTF(url)
  const model = useMemo(() => prepareModel(scene), [scene])
  const target = object.size.map((value) => Math.max(0.05, value * instance.scale)) as [number, number, number]
  const source = model.size
  const scale = Math.min(target[0] / Math.max(source.x, 0.001), target[1] / Math.max(source.y, 0.001), target[2] / Math.max(source.z, 0.001))
  // ScenePlan positions are contact points. Centre the source mesh in X/Z
  // and lift its lowest vertex to the requested contact height, just like the
  // server-side GLB composer.
  const position: [number, number, number] = [
    instance.position[0] - model.center.x * scale,
    instance.position[1] - model.minY * scale,
    instance.position[2] - model.center.z * scale,
  ]
  return (
    <group position={position} rotation={instance.rotation} scale={scale}>
      <primitive object={model.clone} dispose={null} />
    </group>
  )
}

class PlanAssetErrorBoundary extends Component<{ children: ReactNode; fallback: ReactNode }, { failed: boolean }> {
  state: { failed: boolean } = { failed: false }

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true }
  }

  render(): ReactNode {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}

function ScenePlanWorld({ plan, artifacts = {} }: { plan: ScenePlan; artifacts?: ScenePlanCanvasProps['artifacts'] }): JSX.Element {
  const objectById = useMemo(() => new Map(plan.objects.map((object) => [object.id, object])), [plan.objects])
  const extent = Math.max(plan.bounds.width, plan.bounds.depth, plan.bounds.height)
  return (
    <>
      <ambientLight intensity={1.45} color="#dce8f5" />
      <directionalLight position={[extent * 0.7, extent * 1.2, extent * 0.5]} intensity={2.2} color="#fff3dc" castShadow />
      <gridHelper args={[Math.max(plan.bounds.width, plan.bounds.depth) * 1.2, 24, '#516071', '#303b4a']} position-y={-0.01} />
      {plan.instances.map((instance) => {
        const object = objectById.get(instance.objectId)
        if (!object) return null
        const assetPath = object.asset?.workspacePath ?? artifacts[object.id]?.mesh?.workspace_path
        const fallback = <PlanBox key={instance.id} instance={instance} object={object} />
        if (!assetPath || object.role === 'room' || object.role === 'background') return fallback
        const url = workspaceUrl(assetPath)
        return (
          <PlanAssetErrorBoundary key={`${instance.id}:${assetPath}`} fallback={fallback}>
            <Suspense fallback={fallback}>
              <PlanAsset instance={instance} object={object} url={url} />
            </Suspense>
          </PlanAssetErrorBoundary>
        )
      })}
    </>
  )
}

export default function ScenePlanCanvas({ plan, backgroundColor = '#151b23', artifacts }: ScenePlanCanvasProps): JSX.Element {
  const controlsTarget: [number, number, number] = [0, Math.max(plan.bounds.height * 0.18, 0.5), 0]
  const extent = Math.max(plan.bounds.width, plan.bounds.depth, plan.bounds.height)
  return (
    <div className="relative h-full w-full focus-within:ring-1 focus-within:ring-ring/60">
      <Canvas
        data-viewport-canvas="true"
        tabIndex={0}
        className="outline-none"
        camera={{ position: [extent * 1.15, extent * 0.9, extent * 1.25], fov: 42, near: 0.01, far: Math.max(100, extent * 20) }}
        dpr={[1, 1.7]}
        shadows
        gl={{ antialias: true, powerPreference: 'high-performance' }}
      >
        <color attach="background" args={[backgroundColor]} />
        <ScenePlanWorld plan={plan} artifacts={artifacts} />
        <OrbitControls
          makeDefault
          enableDamping
          dampingFactor={0.08}
          enablePan
          enableZoom
          enableRotate
          keyEvents
          zoomToCursor
          mouseButtons={{ LEFT: undefined, MIDDLE: THREE.MOUSE.ROTATE, RIGHT: undefined }}
          maxPolarAngle={Math.PI * 0.49}
          minDistance={Math.max(0.2, extent * 0.12)}
          maxDistance={Math.max(100, extent * 12)}
          target={controlsTarget}
        />
      </Canvas>
    </div>
  )
}
