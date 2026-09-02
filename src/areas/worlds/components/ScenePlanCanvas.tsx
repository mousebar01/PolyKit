import { Component, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls, PointerLockControls, useGLTF } from '@react-three/drei'
import * as THREE from 'three'
import { Gamepad2 } from 'lucide-react'
import { Button } from '@shared/components/ui/button'
import { useI18n } from '@shared/i18n'
import type { ScenePlan, ScenePlanInstance, ScenePlanObject } from '../runtime/scenePlan'
import { buildProceduralGeometry, proceduralMaterial } from '../runtime/procedural'
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

const PROCEDURAL_HINTS = new Set<string>([
  'tree',
  'pine',
  'palm',
  'cactus',
  'rock',
  'boulder',
  'grass',
  'crystal',
  'hut',
  'monolith',
])

type ProceduralPreviewHint = Parameters<typeof buildProceduralGeometry>[0]

function roleColor(role: string): string {
  return ROLE_COLORS[role] ?? '#8b93a1'
}

function proceduralHintFor(object: ScenePlanObject): ProceduralPreviewHint | null {
  const raw = object.constraints?.proceduralHint ?? object.constraints?.procedural_hint
  const value = typeof raw === 'string' ? raw.trim().toLowerCase() : ''
  return PROCEDURAL_HINTS.has(value) ? value as ProceduralPreviewHint : null
}

function PlanBox({ instance, object }: { instance: ScenePlanInstance; object: ScenePlanObject }): JSX.Element {
  // Blockout-only fallback for planning before a Blender asset is available.
  // This geometry is never persisted, exported, or accepted as build evidence.
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

function PlanProcedural({
  instance,
  object,
  hint,
}: {
  instance: ScenePlanInstance
  object: ScenePlanObject
  hint: ProceduralPreviewHint
}): JSX.Element {
  const geometry = useMemo(
    () => buildProceduralGeometry(hint, `${object.id}:${instance.id}`),
    [hint, instance.id, object.id],
  )
  const material = useMemo(() => proceduralMaterial(), [])
  const targetHeight = Math.max(0.05, object.size[1] * instance.scale)

  useEffect(() => () => geometry.dispose(), [geometry])
  useEffect(() => () => material.dispose(), [material])

  return (
    <mesh
      geometry={geometry}
      material={material}
      position={instance.position}
      rotation={instance.rotation}
      scale={targetHeight}
      castShadow
      receiveShadow
    />
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

interface WalkCollider {
  centerX: number
  centerZ: number
  halfX: number
  halfZ: number
}

function WalkthroughController({
  plan,
  active,
  onLockedChange,
}: {
  plan: ScenePlan
  active: boolean
  onLockedChange: (locked: boolean) => void
}): JSX.Element {
  const { camera } = useThree()
  const keys = useRef<Record<string, boolean>>({})
  const colliders = useMemo<WalkCollider[]>(() => {
    const objectById = new Map(plan.objects.map((object) => [object.id, object]))
    return plan.instances.flatMap((instance) => {
      const object = objectById.get(instance.objectId)
      // Surface-mounted props are above the walking plane. The simplified
      // collider deliberately uses semantic dimensions instead of detailed
      // render meshes, as recommended by the game-systems reference.
      if (!object || object.role === 'room' || object.role === 'background' || instance.position[1] > 0.2) return []
      return [{
        centerX: instance.position[0],
        centerZ: instance.position[2],
        halfX: object.size[0] * instance.scale / 2,
        halfZ: object.size[2] * instance.scale / 2,
      }]
    })
  }, [plan])
  const spawn = useMemo<[number, number, number]>(() => {
    const objectById = new Map(plan.objects.map((object) => [object.id, object]))
    const roomInstance = plan.instances.find((instance) => {
      const object = objectById.get(instance.objectId)
      return object?.role === 'room'
    })
    const room = roomInstance ? objectById.get(roomInstance.objectId) : undefined
    if (!roomInstance || !room) return [0, Math.min(1.6, Math.max(1, plan.bounds.height * 0.42)), 0]
    return [
      roomInstance.position[0],
      Math.min(1.6, Math.max(1, room.size[1] * roomInstance.scale * 0.42)),
      roomInstance.position[2] - room.size[2] * roomInstance.scale * 0.32,
    ]
  }, [plan])
  const forward = useMemo(() => new THREE.Vector3(), [])
  const right = useMemo(() => new THREE.Vector3(), [])
  const movement = useMemo(() => new THREE.Vector3(), [])
  const up = useMemo(() => new THREE.Vector3(0, 1, 0), [])
  const playerRadius = 0.28

  useEffect(() => {
    const onBlur = () => { keys.current = {} }
    const handleKey = (event: KeyboardEvent, pressed: boolean) => {
      if (!['KeyW', 'KeyA', 'KeyS', 'KeyD', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.code)) return
      keys.current[event.code] = pressed
      if (active) event.preventDefault()
    }
    const onKeyDown = (event: KeyboardEvent) => handleKey(event, true)
    const onKeyUp = (event: KeyboardEvent) => handleKey(event, false)
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('keyup', onKeyUp)
    window.addEventListener('blur', onBlur)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('keyup', onKeyUp)
      window.removeEventListener('blur', onBlur)
    }
  }, [active])

  useEffect(() => {
    if (!active) {
      if (document.pointerLockElement) document.exitPointerLock()
      onLockedChange(false)
      return
    }
    camera.position.set(...spawn)
    camera.rotation.set(0, Math.PI, 0)
    camera.rotation.order = 'YXZ'
  }, [active, camera, onLockedChange, spawn])

  useFrame((_, delta) => {
    if (!active) return
    const forwardAmount = (keys.current.KeyW || keys.current.ArrowUp ? 1 : 0) - (keys.current.KeyS || keys.current.ArrowDown ? 1 : 0)
    const rightAmount = (keys.current.KeyD || keys.current.ArrowRight ? 1 : 0) - (keys.current.KeyA || keys.current.ArrowLeft ? 1 : 0)
    if (forwardAmount === 0 && rightAmount === 0) return
    camera.getWorldDirection(forward)
    forward.y = 0
    if (forward.lengthSq() < 0.0001) return
    forward.normalize()
    right.crossVectors(forward, up).normalize()
    movement.set(0, 0, 0).addScaledVector(forward, forwardAmount).addScaledVector(right, rightAmount)
    if (movement.lengthSq() < 0.0001) return
    movement.normalize().multiplyScalar(Math.min(delta, 0.05) * 2.8)

    const blocked = (x: number, z: number): boolean => colliders.some((collider) => (
      Math.abs(x - collider.centerX) < collider.halfX + playerRadius
      && Math.abs(z - collider.centerZ) < collider.halfZ + playerRadius
    ))
    const limitX = Math.max(playerRadius, plan.bounds.width / 2 - playerRadius)
    const limitZ = Math.max(playerRadius, plan.bounds.depth / 2 - playerRadius)
    const nextX = Math.max(-limitX, Math.min(limitX, camera.position.x + movement.x))
    const nextZ = Math.max(-limitZ, Math.min(limitZ, camera.position.z + movement.z))
    if (!blocked(nextX, camera.position.z)) camera.position.x = nextX
    if (!blocked(camera.position.x, nextZ)) camera.position.z = nextZ
  })

  return <PointerLockControls selector='[data-viewport-canvas="true"]' onLock={() => onLockedChange(true)} onUnlock={() => onLockedChange(false)} minPolarAngle={Math.PI * 0.28} maxPolarAngle={Math.PI * 0.72} pointerSpeed={0.75} />
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
        const proxyHint = object.role === 'room' || object.role === 'background' ? null : proceduralHintFor(object)
        const fallback = proxyHint
          ? <PlanProcedural key={instance.id} instance={instance} object={object} hint={proxyHint} />
          : <PlanBox key={instance.id} instance={instance} object={object} />
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
  const { language } = useI18n()
  const zh = language === 'zh-CN'
  const [walkthrough, setWalkthrough] = useState(false)
  const [locked, setLocked] = useState(false)
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
        {walkthrough ? (
          <WalkthroughController plan={plan} active onLockedChange={setLocked} />
        ) : (
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
        )}
      </Canvas>
      <div className="absolute bottom-3 left-3 z-10 flex items-center gap-2">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className={`size-8 border border-divider bg-background/85 text-muted-foreground backdrop-blur-sm hover:bg-background hover:text-foreground ${walkthrough ? 'bg-primary/15 text-primary' : ''}`}
          aria-pressed={walkthrough}
          aria-label={zh ? '场景漫游' : 'Walk scene'}
          title={zh ? '场景漫游' : 'Walk scene'}
          onClick={() => {
            setWalkthrough((value) => !value)
            setLocked(false)
          }}
        >
          <Gamepad2 className="size-4" aria-hidden="true" />
        </Button>
        {walkthrough && (
          <span className="rounded-md border border-divider bg-background/85 px-2 py-1 text-[10px] text-muted-foreground backdrop-blur-sm">
            {locked ? (zh ? 'WASD / Esc 退出' : 'WASD / Esc to exit') : (zh ? '点击画布开始漫游' : 'Click canvas to walk')}
          </span>
        )}
      </div>
    </div>
  )
}
