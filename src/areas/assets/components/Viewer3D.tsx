import { Component, lazy, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode, ErrorInfo, MutableRefObject } from 'react'
import { Canvas, useFrame, useLoader, useThree } from '@react-three/fiber'
import type { ThreeEvent } from '@react-three/fiber'
import { Environment, GizmoHelper, Lightformer, OrbitControls, useGizmoContext, useGLTF } from '@react-three/drei'
import { EffectComposer, Outline, Select, Selection } from '@react-three/postprocessing'
import * as THREE from 'three'
import { OBJLoader } from 'three/examples/jsm/loaders/OBJLoader.js'
import { computeBoundsTree, disposeBoundsTree, acceleratedRaycast } from 'three-mesh-bvh'

// Patch THREE pour utiliser BVH sur tous les meshes — réduit le raycast O(N) → O(log N)
THREE.BufferGeometry.prototype.computeBoundsTree = computeBoundsTree as any
THREE.BufferGeometry.prototype.disposeBoundsTree = disposeBoundsTree as any
THREE.Mesh.prototype.raycast = acceleratedRaycast
import type { SplatViewerHandle } from './SplatViewer'
import { useGeneration } from '@shared/hooks/useGeneration'
import { useAppStore } from '@shared/stores/appStore'
import { useI18n } from '@shared/i18n'
import { ViewerSeparationControl, ViewerToolbar, type ViewMode } from './ViewerToolbar'
import type { LightSettings } from '@shared/stores/appStore'
import { DEFAULT_LIGHT_SETTINGS } from '@shared/stores/appStore'
const SplatViewer = lazy(() => import('./SplatViewer'))

export type GizmoMode = 'translate' | 'rotate' | 'scale'

const SELECTION_OUTLINE_VISIBLE_COLOR = 0x8b5cf6
const SELECTION_OUTLINE_HIDDEN_COLOR = 0x5b21b6
const SELECTION_OUTLINE_EDGE_STRENGTH = 2.5
const SELECTION_OUTLINE_BLUR = false
const SELECTION_OUTLINE_MULTISAMPLING = 0
const SELECTION_OUTLINE_RESOLUTION_SCALE = 0.5
// Loading a very large GLB into the browser can block the main thread while
// GLTFLoader parses buffers and WebGL uploads them. Keep the viewer responsive
// and let the server-side decimator handle these files instead.
const MAX_VIEWER_MODEL_BYTES = 256 * 1024 * 1024

// ---------------------------------------------------------------------------
// Procedural textures
// ---------------------------------------------------------------------------

function createMatcapTexture(): THREE.CanvasTexture {
  const size = 128
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = size
  const ctx = canvas.getContext('2d')!
  const grad = ctx.createRadialGradient(size * 0.35, size * 0.3, 0, size / 2, size / 2, size / 2)
  grad.addColorStop(0, '#ffffff')
  grad.addColorStop(0.45, '#aaaaaa')
  grad.addColorStop(1, '#222222')
  ctx.fillStyle = grad
  ctx.fillRect(0, 0, size, size)
  return new THREE.CanvasTexture(canvas)
}

function createCheckerTexture(): THREE.CanvasTexture {
  const size = 256
  const tileCount = 8
  const tileSize = size / tileCount
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = size
  const ctx = canvas.getContext('2d')!
  for (let row = 0; row < tileCount; row++) {
    for (let col = 0; col < tileCount; col++) {
      ctx.fillStyle = (row + col) % 2 === 0 ? '#e0e0e0' : '#888888'
      ctx.fillRect(col * tileSize, row * tileSize, tileSize, tileSize)
    }
  }
  const tex = new THREE.CanvasTexture(canvas)
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping
  return tex
}

/**
 * GLTFLoader normally configures these values for us.  Generated GLBs are
 * produced by more than one exporter though, and some of them leave the
 * color-space/update flags implicit.  Normalise the material at the viewer
 * boundary so an embedded base-color image is uploaded and displayed
 * consistently in the browser.
 */
function prepareSceneMaterials(scene: THREE.Object3D): void {
  scene.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return

    const materials = Array.isArray(child.material) ? child.material : [child.material]
    materials.forEach((material) => {
      const textured = material as THREE.Material & {
        map?: THREE.Texture | null
        emissiveMap?: THREE.Texture | null
      }

      // Base-color and emissive images are authored in sRGB.  Data maps such
      // as normal/roughness maps intentionally keep their linear encoding.
      for (const texture of [textured.map, textured.emissiveMap]) {
        if (!texture) continue
        texture.colorSpace = THREE.SRGBColorSpace
        texture.needsUpdate = true
      }

      // Keep untextured generated meshes on the same neutral studio-gray
      // baseline as the library snapshot. A pure white default material clips
      // under the key/fill rig and hides silhouette detail.
      const hasVertexColors = child.geometry.getAttribute('color') !== undefined
      const colorMaterial = material as THREE.Material & { color?: THREE.Color }
      if (!textured.map && !hasVertexColors && colorMaterial.color && !material.userData.polyKitNeutralStudio) {
        colorMaterial.color.multiplyScalar(0.72)
        material.userData.polyKitNeutralStudio = true
      }

      // Trellis meshes can contain back-facing triangles after remeshing.  A
      // two-sided material keeps the texture visible while the user inspects
      // the asset from every angle.
      material.side = THREE.DoubleSide
      material.needsUpdate = true
    })
  })
}

// ---------------------------------------------------------------------------
// CanvasCapture — exposes gl.domElement ref outside Canvas
// ---------------------------------------------------------------------------

function CanvasCapture({
  domRef,
}: {
  domRef: React.MutableRefObject<HTMLCanvasElement | null>
}): null {
  const { gl } = useThree()
  useEffect(() => {
    domRef.current = gl.domElement
    // eslint-disable-next-line react-hooks/exhaustive-deps -- domRef is a stable ref
  }, [gl])
  return null
}

// ---------------------------------------------------------------------------
// ModelErrorBoundary — catches useGLTF load failures (e.g. 404)
// ---------------------------------------------------------------------------

interface ErrorBoundaryProps {
  children: ReactNode
  fallback: ReactNode
  resetKey?: string | null
  onError?: () => void
}

interface ErrorBoundaryState {
  hasError: boolean
}

class ModelErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false }

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.warn('[Viewer3D] Failed to load model:', error.message, info.componentStack)
    this.props.onError?.()
  }

  componentDidUpdate(prevProps: ErrorBoundaryProps): void {
    if (prevProps.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false })
    }
  }

  render(): ReactNode {
    return this.state.hasError ? this.props.fallback : this.props.children
  }
}

function resolveViewerUrl(apiUrl: string, url: string | undefined): string | null {
  if (!url) return null
  if (/^(?:https?:|blob:|data:)/i.test(url)) return url
  return `${apiUrl}${url}`
}

function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

function ModelLoadError(): JSX.Element {
  const { t } = useI18n()

  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-background px-6 text-muted-foreground">
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <circle cx="12" cy="12" r="10" />
        <line x1="15" y1="9" x2="9" y2="15" />
        <line x1="9" y1="9" x2="15" y2="15" />
      </svg>
      <div className="text-center">
        <p className="text-sm font-medium text-foreground">{t('assets.modelLoadError')}</p>
        <p className="mt-1 max-w-xs text-xs text-muted-foreground">{t('assets.modelLoadErrorHint')}</p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// MeshModel
// ---------------------------------------------------------------------------

interface MeshModelProps {
  url: string
  jobId: string
  viewMode: ViewMode
  selected: boolean
  onStats: (stats: { vertices: number; triangles: number }) => void
  onSelect: () => void
  onObject: (obj: THREE.Object3D | null) => void
  onLoaded: () => void
  /** Inspection-only part spread (0 = assembled). */
  separation: number
  onPartCount: (count: number) => void
}

function MeshModel({ url, jobId, viewMode, selected, onStats, onSelect, onObject, onLoaded, separation, onPartCount }: MeshModelProps): JSX.Element {
  const extension = url.split('?')[0]?.split('.').pop()?.toLowerCase()
  const common = { url, jobId, viewMode, selected, onStats, onSelect, onObject, onLoaded, separation, onPartCount }
  return extension === 'obj' ? <ObjMeshModel {...common} /> : <GltfMeshModel {...common} />
}

function GltfMeshModel(props: MeshModelProps): JSX.Element {
  const { scene } = useGLTF(props.url)
  return <SceneMeshModel {...props} scene={scene} loaderType="gltf" />
}

function ObjMeshModel(props: MeshModelProps): JSX.Element {
  const scene = useLoader(OBJLoader, props.url)
  return <SceneMeshModel {...props} scene={scene} loaderType="obj" />
}

function hasMeshDescendant(object: THREE.Object3D): boolean {
  let found = false
  object.traverse((child) => { if (child instanceof THREE.Mesh) found = true })
  return found
}

/**
 * Top-level "part" nodes of a loaded model. Multipart GLBs put one object per
 * part; some exporters wrap everything in a single group, so unwrap one level.
 */
function collectParts(scene: THREE.Object3D): THREE.Object3D[] {
  const direct = scene.children.filter(hasMeshDescendant)
  if (direct.length >= 2) return direct
  if (direct.length === 1) {
    const nested = direct[0].children.filter(hasMeshDescendant)
    if (nested.length >= 2) return nested
  }
  return direct
}

function SceneMeshModel({
  url,
  viewMode,
  selected,
  onStats,
  onSelect,
  onObject,
  onLoaded,
  scene,
  loaderType,
  separation,
  onPartCount,
}: MeshModelProps & {
  scene: THREE.Group | THREE.Scene
  loaderType: 'gltf' | 'obj'
}): JSX.Element {
  const captured = useRef(false)
  const edgeHelpers = useRef<THREE.LineSegments[]>([])

  // Part separation (inspection only): remember each part's base pose once per
  // model and offset it along its own away-direction as the slider moves.
  const partDataRef = useRef<{ child: THREE.Object3D; base: THREE.Vector3; dir: THREE.Vector3 }[]>([])
  const measuredSceneRef = useRef<THREE.Object3D | null>(null)

  useEffect(() => {
    if (measuredSceneRef.current !== scene) {
      measuredSceneRef.current = scene
      // A cached model may still carry a stale gizmo pose — measure from a
      // clean frame so part directions are computed in the model's own space
      // (the centering effect does the same before it re-centers the scene).
      scene.position.set(0, 0, 0)
      scene.rotation.set(0, 0, 0)
      scene.scale.set(1, 1, 1)
      const parts = collectParts(scene)
      const recorded = parts.map((child) => {
          const center = new THREE.Box3().setFromObject(child).getCenter(new THREE.Vector3())
          return { child, base: child.position.clone(), center }
        })
      if (recorded.length >= 2) {
        const modelCenter = recorded
          .reduce((acc, part) => acc.add(part.center), new THREE.Vector3())
          .divideScalar(recorded.length)
        partDataRef.current = recorded.map((part) => ({
          child: part.child,
          base: part.base,
          dir: new THREE.Vector3().subVectors(part.center, modelCenter),
        }))
      } else {
        partDataRef.current = []
      }
      onPartCount(partDataRef.current.length)
    }
    for (const part of partDataRef.current) {
      part.child.position.copy(part.base).addScaledVector(part.dir, separation)
    }
  }, [scene, separation, onPartCount])

  // Expose the scene object so Viewer3D can attach the transform gizmo to it.
  useEffect(() => {
    onObject(scene)
    return () => onObject(null)
  }, [scene, onObject])

  // Prepare maps before the first visible frame.  useGLTF resolves after the
  // image resources have loaded, but the renderer still needs one explicit
  // texture update for GLBs produced by trimesh/export pipelines.
  useLayoutEffect(() => {
    prepareSceneMaterials(scene)
    const frame = window.requestAnimationFrame(onLoaded)
    return () => window.cancelAnimationFrame(frame)
  }, [scene, onLoaded])

  // Free GPU resources and loader cache when this model is replaced or unmounted
  useEffect(() => {
    return () => {
      if (loaderType === 'obj') {
        useLoader.clear(OBJLoader, url)
      } else {
        useGLTF.clear(url)
      }
      scene.traverse((child) => {
        if (child instanceof THREE.Mesh) {
          child.geometry.dispose()
          const materials = Array.isArray(child.material) ? child.material : [child.material]
          materials.forEach((m: THREE.Material) => m.dispose())
        }
      })
    }
  }, [loaderType, scene, url])

  // Compute BVH after the first frame. Building it synchronously here makes a
  // large mesh appear to be stuck in loading even after GLTF parsing finished.
  // The accelerated raycast helper has a safe native Three.js fallback while
  // the tree is being prepared.
  useEffect(() => {
    let cancelled = false
    let prepared = false
    const timer = window.setTimeout(() => {
      if (cancelled) return
      prepared = true
      scene.traverse((child) => {
        if (child instanceof THREE.Mesh) {
          ;(child.geometry as any).computeBoundsTree()
          const mats = Array.isArray(child.material) ? child.material : [child.material]
          mats.forEach((m: THREE.Material) => { m.side = THREE.DoubleSide })
        }
      })
    }, 150)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
      if (prepared) {
        scene.traverse((child) => {
          if (child instanceof THREE.Mesh) {
            (child.geometry as any).disposeBoundsTree?.()
          }
        })
      }
    }
  }, [scene])

  // Centre the mesh on the grid. Runs only on first load / model change — never
  // on plain re-renders, so a live gizmo transform is not silently overwritten.
  useEffect(() => {
    let cancelled = false
    // Let the browser paint the parsed scene before walking millions of
    // vertices for centring and statistics.
    const timer = window.setTimeout(() => {
      if (cancelled) return

      // Clear any cached transform before measuring (useGLTF may reuse a scene
      // that still carries an earlier gizmo pose).
      scene.position.set(0, 0, 0)
      scene.rotation.set(0, 0, 0)
      scene.scale.set(1, 1, 1)
      const box = new THREE.Box3().setFromObject(scene)
      const center = new THREE.Vector3()
      box.getCenter(center)
      scene.position.set(-center.x, -box.min.y, -center.z)

      // Compute stats
      let vertices = 0
      let triangles = 0
      scene.traverse((child) => {
        if (child instanceof THREE.Mesh) {
          vertices += child.geometry.attributes.position?.count ?? 0
          triangles += child.geometry.index
            ? child.geometry.index.count / 3
            : (child.geometry.attributes.position?.count ?? 0) / 3
        }
      })
      const roundedTriangles = Math.round(triangles)
      onStats({ vertices: Math.round(vertices), triangles: roundedTriangles })
    }, 0)

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [scene, onStats])

  // Thumbnail capture (kept for future use)
  useEffect(() => {
    captured.current = false
  }, [url])

  // Material swapping based on viewMode
  useEffect(() => {
    // Remove any edge helpers from previous wireframe pass
    edgeHelpers.current.forEach((lines) => {
      lines.parent?.remove(lines)
      lines.geometry.dispose()
      const material = lines.material
      if (Array.isArray(material)) material.forEach((item) => item.dispose())
      else material.dispose()
    })
    edgeHelpers.current = []

    scene.traverse((child) => {
      if (!(child instanceof THREE.Mesh)) return

      // Save original material on first visit
      if (!child.userData.originalMaterial) {
        child.userData.originalMaterial = child.material
      }

      let next: THREE.Material
      switch (viewMode) {
        case 'wireframe': {
          // Keep topology readable without the saturated selection-green
          // silhouette. Dense generated meshes can still look visually full,
          // but a neutral translucent stroke makes that density apparent and
          // matches the Blender viewport's subdued wireframe treatment.
          next = new THREE.MeshBasicMaterial({
            color: 0xb7c0cc,
            wireframe: true,
            transparent: true,
            opacity: 0.7,
            depthWrite: false,
          })
          break
        }
        case 'normals':
          // Ensure vertex normals exist — AI-generated meshes often skip this
          child.geometry.computeVertexNormals()
          next = new THREE.MeshNormalMaterial({ side: THREE.DoubleSide })
          break
        case 'matcap':
          next = new THREE.MeshMatcapMaterial({ matcap: createMatcapTexture() })
          break
        case 'uv':
          next = new THREE.MeshBasicMaterial({ map: createCheckerTexture() })
          break
        default:
          next = child.userData.originalMaterial as THREE.Material
      }

      child.material = next
    })
  }, [scene, viewMode])

  return (
    <Select enabled={selected}>
      <primitive
        object={scene}
        onClick={(e: { stopPropagation: () => void }) => { e.stopPropagation(); onSelect() }}
      />
    </Select>
  )

}

// ---------------------------------------------------------------------------
// Orientation gizmo — six floating positive/negative axis bubbles.
// ---------------------------------------------------------------------------

function makeAxisLabelTexture(label: string, color: string, hovered: boolean): THREE.CanvasTexture {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 64
  const ctx = canvas.getContext('2d')!

  // Blender turns the active axis into a light, filled target. Keep a thin
  // axis-coloured rim so the hover state is unmistakable without losing the
  // X/Y/Z colour coding.
  if (hovered) {
    ctx.beginPath()
    ctx.arc(32, 32, 19, 0, 2 * Math.PI)
    ctx.closePath()
    ctx.fillStyle = 'rgba(244, 246, 249, 0.96)'
    ctx.fill()
  }

  ctx.beginPath()
  ctx.arc(32, 32, 16, 0, 2 * Math.PI)
  ctx.closePath()
  ctx.globalAlpha = hovered ? 1 : 0.78
  ctx.fillStyle = hovered ? '#e5e7eb' : color
  ctx.fill()
  if (hovered) {
    ctx.globalAlpha = 0.85
    ctx.lineWidth = 2
    ctx.strokeStyle = color
    ctx.stroke()
  }
  ctx.globalAlpha = 1
  ctx.font = `${label.length > 1 ? 14 : 18}px Arial, sans-serif`
  ctx.textAlign = 'center'
  ctx.fillStyle = hovered ? '#24272b' : '#ffffff'
  ctx.fillText(label, 32, 41)
  return new THREE.CanvasTexture(canvas)
}

const GIZMO_AXES: {
  key: string
  axis: 'x' | 'y' | 'z'
  sign: 1 | -1
  letter: string
  color: string
  pos: [number, number, number]
}[] = [
  { key: 'x+', axis: 'x', sign: 1, letter: 'X', color: '#f87171', pos: [1, 0, 0] },
  { key: 'x-', axis: 'x', sign: -1, letter: '−X', color: '#f87171', pos: [-1, 0, 0] },
  { key: 'y+', axis: 'y', sign: 1, letter: 'Y', color: '#4ade80', pos: [0, 1, 0] },
  { key: 'y-', axis: 'y', sign: -1, letter: '−Y', color: '#4ade80', pos: [0, -1, 0] },
  { key: 'z+', axis: 'z', sign: 1, letter: 'Z', color: '#60a5fa', pos: [0, 0, 1] },
  { key: 'z-', axis: 'z', sign: -1, letter: '−Z', color: '#60a5fa', pos: [0, 0, -1] },
]

function AxisLine({ color, axis, sign }: { color: string; axis: 'x' | 'y' | 'z'; sign: 1 | -1 }) {
  const rotation: [number, number, number] = axis === 'x'
    ? [0, 0, sign < 0 ? Math.PI : 0]
    : axis === 'y'
      ? [0, 0, sign < 0 ? -Math.PI / 2 : Math.PI / 2]
      : [0, sign < 0 ? Math.PI / 2 : -Math.PI / 2, 0]

  return (
    <group rotation={rotation}>
      <mesh position={[0.4, 0, 0]}>
        <boxGeometry args={[0.8, 0.05, 0.05]} />
        <meshBasicMaterial color={color} toneMapped={false} />
      </mesh>
    </group>
  )
}

function AxisBubble({
  letter,
  color,
  pos,
  showLabel = true,
}: {
  letter: string
  color: string
  pos: [number, number, number]
  showLabel?: boolean
}) {
  const { tweenCamera } = useGizmoContext()
  const [hovered, setHovered] = useState(false)
  const labelVisible = showLabel || hovered
  const texture = useMemo(
    () => makeAxisLabelTexture(labelVisible ? letter : '', color, hovered),
    [letter, color, hovered, labelVisible],
  )

  useEffect(() => () => texture.dispose(), [texture])

  return (
    <sprite
      position={pos}
      scale={hovered ? 1.24 : 1}
      onPointerDown={(e) => { tweenCamera(e.object.position); e.stopPropagation() }}
      onPointerOver={(e) => { e.stopPropagation(); setHovered(true) }}
      onPointerOut={() => setHovered(false)}
    >
      <spriteMaterial map={texture} alphaTest={0.3} toneMapped={false} />
    </sprite>
  )
}

function GizmoBubbles() {
  return (
    <group scale={40}>
      {GIZMO_AXES.filter((axis) => axis.sign > 0).map((axis) => (
        <AxisLine key={`line-${axis.key}`} color={axis.color} axis={axis.axis} sign={axis.sign} />
      ))}
      <mesh>
        <sphereGeometry args={[0.11, 16, 8]} />
        <meshBasicMaterial color="#a1a1aa" toneMapped={false} />
      </mesh>
      {GIZMO_AXES.map((axis) => (
        <AxisBubble
          key={axis.key}
          letter={axis.letter}
          color={axis.color}
          pos={axis.pos}
          showLabel={axis.sign > 0}
        />
      ))}
    </group>
  )
}

// Keep the empty viewer useful instead of presenting a dead canvas. This is a
// lightweight Blender-style starter scene: a perspective grid, origin axes,
// and the familiar cube/camera/light silhouettes that establish scale and
// communicate that the viewport is ready for inspection.
function DefaultViewportScene(): JSX.Element {
  return (
    <>
      {/* Use the same native Three grid as the loaded-model path. It is
          reliable in the empty scene too, without Drei's infinite-grid
          shader depending on a model or camera update. */}
      <gridHelper args={[50, 50, '#474747', '#363636']} />

      <mesh position={[0, 0.5, 0]} castShadow>
        <boxGeometry args={[1, 1, 1]} />
        <meshStandardMaterial color="#8d8d8d" roughness={0.72} metalness={0.04} />
      </mesh>

      <group position={[3.1, 2.3, 2.9]} rotation={[-0.35, 0.7, 0.18]}>
        <mesh>
          <boxGeometry args={[0.8, 0.5, 0.42]} />
          <meshBasicMaterial color="#131313" wireframe toneMapped={false} />
        </mesh>
        <mesh position={[0, 0, -0.3]} rotation={[Math.PI / 2, 0, 0]}>
          <coneGeometry args={[0.22, 0.38, 4]} />
          <meshBasicMaterial color="#131313" wireframe toneMapped={false} />
        </mesh>
      </group>

      <group position={[-2.3, 3.1, 1.2]}>
        <mesh>
          <sphereGeometry args={[0.22, 12, 8]} />
          <meshBasicMaterial color="#171717" wireframe toneMapped={false} />
        </mesh>
        <pointLight intensity={0.8} distance={10} />
      </group>
    </>
  )
}

// ---------------------------------------------------------------------------
// Transform gizmos — custom move / rotate / scale handles (shared style)
// ---------------------------------------------------------------------------

type GizmoAxis = 'x' | 'y' | 'z'
type TranslateHandleId = GizmoAxis | 'xy' | 'yz' | 'xz'
type ScaleHandleId = GizmoAxis | 'xyz'

const AXIS_COLORS: Record<GizmoAxis, string> = {
  x: '#f87171',
  y: '#4ade80',
  z: '#60a5fa',
}

const AXIS_DIR: Record<GizmoAxis, [number, number, number]> = {
  x: [1, 0, 0],
  y: [0, 1, 0],
  z: [0, 0, 1],
}

// Orient a +Y cylinder/cone/box onto each axis.
const AXIS_ROTATION: Record<GizmoAxis, [number, number, number]> = {
  x: [0, 0, -Math.PI / 2],
  y: [0, 0, 0],
  z: [Math.PI / 2, 0, 0],
}

// Orient a default-XY torus so its ring spins around each axis.
const RING_ROTATION: Record<GizmoAxis, [number, number, number]> = {
  x: [0, Math.PI / 2, 0],
  y: [Math.PI / 2, 0, 0],
  z: [0, 0, 0],
}

// Two-axis plane handles, coloured by their locked (normal) axis.
const PLANE_HANDLES: {
  id: 'xy' | 'yz' | 'xz'
  normal: [number, number, number]
  color: string
  position: [number, number, number]
  rotation: [number, number, number]
}[] = [
  { id: 'xy', normal: [0, 0, 1], color: AXIS_COLORS.z, position: [0.26, 0.26, 0], rotation: [0, 0, 0] },
  { id: 'yz', normal: [1, 0, 0], color: AXIS_COLORS.x, position: [0, 0.26, 0.26], rotation: [0, -Math.PI / 2, 0] },
  { id: 'xz', normal: [0, 1, 0], color: AXIS_COLORS.y, position: [0.26, 0, 0.26], rotation: [Math.PI / 2, 0, 0] },
]

const GIZMO_SCREEN_SIZE = 0.12

function lightenColor(hex: string, amount = 0.5): string {
  return '#' + new THREE.Color(hex).lerp(new THREE.Color('#ffffff'), amount).getHexString()
}

function intersectPlane(ray: THREE.Ray, origin: THREE.Vector3, normal: THREE.Vector3): THREE.Vector3 | null {
  const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(normal, origin)
  const hit = new THREE.Vector3()
  return ray.intersectPlane(plane, hit) ? hit : null
}

// Shared plumbing: follow the object, keep a constant on-screen size, and run
// the pointer-drag lifecycle (window listeners + OrbitControls locking).
function useGizmoBase(object: THREE.Object3D) {
  const camera = useThree((s) => s.camera)
  const gl = useThree((s) => s.gl)
  const raycaster = useThree((s) => s.raycaster)
  const controls = useThree((s) => s.controls) as { enabled: boolean } | null

  const groupRef = useRef<THREE.Group>(null)
  const ndc = useRef(new THREE.Vector2())
  const moveRef = useRef<((ev: PointerEvent) => void) | null>(null)
  const endRef = useRef<(() => void) | null>(null)

  useFrame(() => {
    const g = groupRef.current
    if (!g) return
    object.getWorldPosition(g.position)
    g.scale.setScalar(Math.max(camera.position.distanceTo(g.position) * GIZMO_SCREEN_SIZE, 0.001))
  })

  const pointerRay = useCallback((ev: PointerEvent): THREE.Ray => {
    const rect = gl.domElement.getBoundingClientRect()
    ndc.current.set(
      ((ev.clientX - rect.left) / rect.width) * 2 - 1,
      -((ev.clientY - rect.top) / rect.height) * 2 + 1,
    )
    raycaster.setFromCamera(ndc.current, camera)
    return raycaster.ray
  }, [camera, gl, raycaster])

  const stop = useCallback(() => {
    if (!moveRef.current) return
    window.removeEventListener('pointermove', moveRef.current)
    window.removeEventListener('pointerup', stop)
    moveRef.current = null
    endRef.current?.()
    endRef.current = null
    if (controls) controls.enabled = true
    gl.domElement.style.cursor = ''
  }, [controls, gl])

  const start = useCallback((onMove: (ev: PointerEvent) => void, onEnd?: () => void) => {
    moveRef.current = onMove
    endRef.current = onEnd ?? null
    if (controls) controls.enabled = false
    gl.domElement.style.cursor = 'grabbing'
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', stop)
  }, [controls, gl, stop])

  useEffect(() => stop, [stop])  // release the drag if unmounted mid-interaction

  return { camera, groupRef, pointerRay, start }
}

function hoverHandlers<T extends string>(
  id: T,
  setHovered: (value: T | null) => void,
  onDown: (e: ThreeEvent<PointerEvent>) => void,
) {
  return {
    onPointerOver: (e: ThreeEvent<PointerEvent>) => { e.stopPropagation(); setHovered(id) },
    onPointerOut: () => setHovered(null),
    onPointerDown: onDown,
  }
}

function GizmoArrow({ color, active }: { color: string; active: boolean }): JSX.Element {
  const tint = active ? lightenColor(color) : color
  return (
    <group>
      {/* Invisible, fat hit target spanning the whole arm */}
      <mesh position={[0, 0.55, 0]}>
        <cylinderGeometry args={[0.09, 0.09, 1.1, 8]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      {/* Shaft */}
      <mesh position={[0, 0.48, 0]} renderOrder={999}>
        <cylinderGeometry args={[0.014, 0.014, 0.66, 16]} />
        <meshBasicMaterial color={tint} toneMapped={false} transparent depthTest={false} depthWrite={false} />
      </mesh>
      {/* Arrowhead */}
      <mesh position={[0, 0.9, 0]} renderOrder={999}>
        <coneGeometry args={[0.055, 0.2, 20]} />
        <meshBasicMaterial color={tint} toneMapped={false} transparent depthTest={false} depthWrite={false} />
      </mesh>
    </group>
  )
}

function GizmoScaleArm({ color, active }: { color: string; active: boolean }): JSX.Element {
  const tint = active ? lightenColor(color) : color
  return (
    <group>
      {/* Invisible, fat hit target — starts above the centre cube so a
          centre click hits the uniform-scale handle, not an axis */}
      <mesh position={[0, 0.6, 0]}>
        <cylinderGeometry args={[0.09, 0.09, 0.8, 8]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      {/* Shaft */}
      <mesh position={[0, 0.42, 0]} renderOrder={999}>
        <cylinderGeometry args={[0.014, 0.014, 0.7, 16]} />
        <meshBasicMaterial color={tint} toneMapped={false} transparent depthTest={false} depthWrite={false} />
      </mesh>
      {/* Cube head */}
      <mesh position={[0, 0.84, 0]} renderOrder={999}>
        <boxGeometry args={[0.11, 0.11, 0.11]} />
        <meshBasicMaterial color={tint} toneMapped={false} transparent depthTest={false} depthWrite={false} />
      </mesh>
    </group>
  )
}

function GizmoRing({ color, active }: { color: string; active: boolean }): JSX.Element {
  const tint = active ? lightenColor(color) : color
  return (
    <group>
      {/* Invisible, fat hit target */}
      <mesh>
        <torusGeometry args={[0.9, 0.06, 8, 48]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      <mesh renderOrder={999}>
        <torusGeometry args={[0.9, 0.012, 12, 64]} />
        <meshBasicMaterial color={tint} toneMapped={false} transparent depthTest={false} depthWrite={false} />
      </mesh>
    </group>
  )
}

function GizmoPlane({ color, active }: { color: string; active: boolean }): JSX.Element {
  return (
    <mesh renderOrder={998}>
      <planeGeometry args={[0.26, 0.26]} />
      <meshBasicMaterial
        color={active ? lightenColor(color) : color}
        transparent
        opacity={active ? 0.6 : 0.28}
        side={THREE.DoubleSide}
        toneMapped={false}
        depthTest={false}
        depthWrite={false}
      />
    </mesh>
  )
}

function TranslateGizmo({ object, onDragStart, onDragEnd }: { object: THREE.Object3D; onDragStart?: () => void; onDragEnd?: () => void }): JSX.Element {
  const { camera, groupRef, pointerRay, start } = useGizmoBase(object)
  const [hovered, setHovered] = useState<TranslateHandleId | null>(null)
  const [activeId, setActiveId] = useState<TranslateHandleId | null>(null)
  const drag = useRef<{
    axisDir: THREE.Vector3 | null
    planeNormal: THREE.Vector3
    origin: THREE.Vector3
    startHit: THREE.Vector3
    startPos: THREE.Vector3
  } | null>(null)

  const beginDrag = useCallback((id: TranslateHandleId, e: ThreeEvent<PointerEvent>) => {
    e.stopPropagation()
    const origin = new THREE.Vector3()
    object.getWorldPosition(origin)
    const startPos = object.position.clone()

    let axisDir: THREE.Vector3 | null = null
    let planeNormal: THREE.Vector3
    if (id === 'x' || id === 'y' || id === 'z') {
      axisDir = new THREE.Vector3(...AXIS_DIR[id])
      // Drag plane: contains the axis and faces the camera as much as possible.
      const view = new THREE.Vector3().subVectors(camera.position, origin)
      planeNormal = view.sub(axisDir.clone().multiplyScalar(view.dot(axisDir)))
      if (planeNormal.lengthSq() < 1e-6) planeNormal.set(axisDir.y ? 1 : 0, axisDir.y ? 0 : 1, 0)
      planeNormal.normalize()
    } else {
      planeNormal = new THREE.Vector3(...PLANE_HANDLES.find((p) => p.id === id)!.normal)
    }

    const startHit = intersectPlane(e.ray, origin, planeNormal)
    if (!startHit) return
    drag.current = { axisDir, planeNormal, origin, startHit, startPos }
    setActiveId(id)
    onDragStart?.()
    start((ev) => {
      const d = drag.current
      if (!d) return
      const hit = intersectPlane(pointerRay(ev), d.origin, d.planeNormal)
      if (!hit) return
      const delta = new THREE.Vector3().subVectors(hit, d.startHit)
      if (d.axisDir) {
        object.position.copy(d.startPos).addScaledVector(d.axisDir, delta.dot(d.axisDir))
      } else {
        object.position.copy(d.startPos).add(delta)
      }
    }, () => { drag.current = null; setActiveId(null); onDragEnd?.() })
  }, [object, camera, pointerRay, start, onDragStart, onDragEnd])

  return (
    <group ref={groupRef} renderOrder={999}>
      {/* Central origin handle (decorative — never blocks picking) */}
      <mesh raycast={() => null} renderOrder={999}>
        <sphereGeometry args={[0.05, 20, 20]} />
        <meshBasicMaterial color="#e4e4e7" toneMapped={false} transparent depthTest={false} depthWrite={false} />
      </mesh>

      {(['x', 'y', 'z'] as GizmoAxis[]).map((axis) => (
        <group key={axis} rotation={AXIS_ROTATION[axis]} {...hoverHandlers<TranslateHandleId>(axis, setHovered, (e) => beginDrag(axis, e))}>
          <GizmoArrow color={AXIS_COLORS[axis]} active={hovered === axis || activeId === axis} />
        </group>
      ))}

      {PLANE_HANDLES.map((plane) => (
        <group key={plane.id} position={plane.position} rotation={plane.rotation} {...hoverHandlers<TranslateHandleId>(plane.id, setHovered, (e) => beginDrag(plane.id, e))}>
          <GizmoPlane color={plane.color} active={hovered === plane.id || activeId === plane.id} />
        </group>
      ))}
    </group>
  )
}

function RotateGizmo({ object, onDragStart, onDragEnd }: { object: THREE.Object3D; onDragStart?: () => void; onDragEnd?: () => void }): JSX.Element {
  const { groupRef, pointerRay, start } = useGizmoBase(object)
  const [hovered, setHovered] = useState<GizmoAxis | null>(null)
  const [activeId, setActiveId] = useState<GizmoAxis | null>(null)
  const drag = useRef<{
    axisDir: THREE.Vector3
    origin: THREE.Vector3
    startVec: THREE.Vector3
    startQuat: THREE.Quaternion
  } | null>(null)

  const beginDrag = useCallback((axis: GizmoAxis, e: ThreeEvent<PointerEvent>) => {
    e.stopPropagation()
    const origin = new THREE.Vector3()
    object.getWorldPosition(origin)
    const axisDir = new THREE.Vector3(...AXIS_DIR[axis]).normalize()
    // Rotation happens in the plane perpendicular to the axis (the ring's plane).
    const startHit = intersectPlane(e.ray, origin, axisDir)
    if (!startHit) return
    const startVec = new THREE.Vector3().subVectors(startHit, origin)
    if (startVec.lengthSq() < 1e-9) return
    drag.current = { axisDir, origin, startVec, startQuat: object.quaternion.clone() }
    setActiveId(axis)
    onDragStart?.()
    start((ev) => {
      const d = drag.current
      if (!d) return
      const hit = intersectPlane(pointerRay(ev), d.origin, d.axisDir)
      if (!hit) return
      const cur = new THREE.Vector3().subVectors(hit, d.origin)
      // Signed angle between the start and current vectors, around the axis.
      const cross = new THREE.Vector3().crossVectors(d.startVec, cur)
      const angle = Math.atan2(cross.dot(d.axisDir), d.startVec.dot(cur))
      const q = new THREE.Quaternion().setFromAxisAngle(d.axisDir, angle)
      object.quaternion.copy(d.startQuat).premultiply(q)
    }, () => { drag.current = null; setActiveId(null); onDragEnd?.() })
  }, [object, pointerRay, start, onDragStart, onDragEnd])

  return (
    <group ref={groupRef} renderOrder={999}>
      {(['x', 'y', 'z'] as GizmoAxis[]).map((axis) => (
        <group key={axis} rotation={RING_ROTATION[axis]} {...hoverHandlers<GizmoAxis>(axis, setHovered, (e) => beginDrag(axis, e))}>
          <GizmoRing color={AXIS_COLORS[axis]} active={hovered === axis || activeId === axis} />
        </group>
      ))}
    </group>
  )
}

function ScaleGizmo({ object, onDragStart, onDragEnd }: { object: THREE.Object3D; onDragStart?: () => void; onDragEnd?: () => void }): JSX.Element {
  const { camera, groupRef, pointerRay, start } = useGizmoBase(object)
  const [hovered, setHovered] = useState<ScaleHandleId | null>(null)
  const [activeId, setActiveId] = useState<ScaleHandleId | null>(null)
  const drag = useRef<{
    axisDir: THREE.Vector3 | null
    planeNormal: THREE.Vector3
    origin: THREE.Vector3
    startProj: number
    startScale: THREE.Vector3
    armLength: number
  } | null>(null)

  const beginDrag = useCallback((id: ScaleHandleId, e: ThreeEvent<PointerEvent>) => {
    e.stopPropagation()
    const origin = new THREE.Vector3()
    object.getWorldPosition(origin)
    // World length of one local unit — maps drag distance to a sensible factor.
    const armLength = Math.max(groupRef.current?.scale.x ?? 1, 1e-4)

    let axisDir: THREE.Vector3 | null = null
    let planeNormal: THREE.Vector3
    if (id === 'xyz') {
      planeNormal = new THREE.Vector3().subVectors(camera.position, origin).normalize()
    } else {
      axisDir = new THREE.Vector3(...AXIS_DIR[id])
      const view = new THREE.Vector3().subVectors(camera.position, origin)
      planeNormal = view.sub(axisDir.clone().multiplyScalar(view.dot(axisDir)))
      if (planeNormal.lengthSq() < 1e-6) planeNormal.set(axisDir.y ? 1 : 0, axisDir.y ? 0 : 1, 0)
      planeNormal.normalize()
    }

    const startHit = intersectPlane(e.ray, origin, planeNormal)
    if (!startHit) return
    const startRel = new THREE.Vector3().subVectors(startHit, origin)
    const startProj = axisDir ? startRel.dot(axisDir) : startRel.length()
    drag.current = { axisDir, planeNormal, origin, startProj, startScale: object.scale.clone(), armLength }
    setActiveId(id)
    onDragStart?.()
    start((ev) => {
      const d = drag.current
      if (!d) return
      const hit = intersectPlane(pointerRay(ev), d.origin, d.planeNormal)
      if (!hit) return
      const rel = new THREE.Vector3().subVectors(hit, d.origin)
      const proj = d.axisDir ? rel.dot(d.axisDir) : rel.length()
      const factor = Math.max(0.01, 1 + (proj - d.startProj) / d.armLength)
      if (d.axisDir) {
        const s = d.startScale.clone()
        if (d.axisDir.x) s.x = Math.max(0.01, d.startScale.x * factor)
        if (d.axisDir.y) s.y = Math.max(0.01, d.startScale.y * factor)
        if (d.axisDir.z) s.z = Math.max(0.01, d.startScale.z * factor)
        object.scale.copy(s)
      } else {
        object.scale.copy(d.startScale).multiplyScalar(factor)
      }
    }, () => { drag.current = null; setActiveId(null); onDragEnd?.() })
  }, [object, camera, pointerRay, start, groupRef, onDragStart, onDragEnd])

  const uniformActive = hovered === 'xyz' || activeId === 'xyz'

  return (
    <group ref={groupRef} renderOrder={999}>
      {/* Central cube — uniform scale */}
      <mesh {...hoverHandlers<ScaleHandleId>('xyz', setHovered, (e) => beginDrag('xyz', e))} renderOrder={999}>
        <boxGeometry args={[0.12, 0.12, 0.12]} />
        <meshBasicMaterial color={uniformActive ? lightenColor('#e4e4e7') : '#e4e4e7'} toneMapped={false} transparent depthTest={false} depthWrite={false} />
      </mesh>

      {(['x', 'y', 'z'] as GizmoAxis[]).map((axis) => (
        <group key={axis} rotation={AXIS_ROTATION[axis]} {...hoverHandlers<ScaleHandleId>(axis, setHovered, (e) => beginDrag(axis, e))}>
          <GizmoScaleArm color={AXIS_COLORS[axis]} active={hovered === axis || activeId === axis} />
        </group>
      ))}
    </group>
  )
}

// ---------------------------------------------------------------------------
// Viewer3D
// ---------------------------------------------------------------------------

type TransformSnapshot = { p: THREE.Vector3; q: THREE.Quaternion; s: THREE.Vector3 }

export default function Viewer3D({ lightSettings = DEFAULT_LIGHT_SETTINGS, gizmoMode = null, gizmoUndoRef, forceEmpty = false }: { lightSettings?: LightSettings; gizmoMode?: GizmoMode | null; gizmoUndoRef?: MutableRefObject<(() => boolean) | null>; forceEmpty?: boolean }): JSX.Element {
  const { currentJob } = useGeneration()
  const apiUrl = useAppStore((s) => s.apiUrl)
  const { t } = useI18n()

  const setStoreMeshStats = useAppStore((s) => s.setMeshStats)
  const meshStats = useAppStore((s) => s.meshStats)
  const setCurrentJob = useAppStore((s) => s.setCurrentJob)

  const [viewMode, setViewMode] = useState<ViewMode>('solid')
  const [autoRotate, setAutoRotate] = useState(false)
  const [separation, setSeparation] = useState(0)
  const [partCount, setPartCount] = useState(0)
  const [separationOpen, setSeparationOpen] = useState(false)
  const selected = useAppStore((s) => s.meshSelected)
  const setSelected = useAppStore((s) => s.setMeshSelected)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const splatRef = useRef<SplatViewerHandle | null>(null)

  const [meshObject, setMeshObject] = useState<THREE.Object3D | null>(null)
  const [modelLoadPhase, setModelLoadPhase] = useState<'idle' | 'loading' | 'ready' | 'error' | 'blocked'>('idle')
  const [modelSizeCheck, setModelSizeCheck] = useState<{
    url: string | null
    status: 'idle' | 'checking' | 'ok' | 'too-large'
    bytes: number | null
  }>({ url: null, status: 'idle', bytes: null })

  // Local gizmo-transform history (live TRS), undoable with Ctrl+Z. A snapshot
  // is taken when a drag starts and committed on release only if it changed.
  const transformHistory = useRef<TransformSnapshot[]>([])
  const pendingTransform = useRef<TransformSnapshot | null>(null)

  const outputUrl = forceEmpty ? '' : currentJob?.outputUrl ?? ''
  const modelUrl = !forceEmpty && currentJob?.status === 'done' && currentJob.outputUrl
    ? resolveViewerUrl(apiUrl, currentJob.outputUrl)
    : null

  // A .ply/.splat reaching the viewer is always a Gaussian splat here: mesh
  // plys are converted to GLB on import and workflow mesh outputs are .glb.
  const isSplat = /\.(ply|splat)$/i.test(outputUrl)

  useEffect(() => {
    setModelLoadPhase(modelUrl ? 'loading' : 'idle')
    // A new model must start assembled with its part count unknown.
    setSeparation(0)
    setPartCount(0)
    setSeparationOpen(false)
  }, [modelUrl, isSplat])

  // Probe the server-owned file before mounting GLTFLoader. A HEAD request is
  // cheap and prevents a multi-hundred-megabyte download from freezing the
  // browser while the user is trying to use the asset controls.
  useEffect(() => {
    const inlineModel = !modelUrl || /^(?:blob:|data:)/i.test(modelUrl)
    if (inlineModel || isSplat) {
      setModelSizeCheck({ url: modelUrl, status: 'ok', bytes: null })
      return
    }

    const controller = new AbortController()
    setModelSizeCheck({ url: modelUrl, status: 'checking', bytes: null })

    fetch(modelUrl, { method: 'HEAD', cache: 'no-store', signal: controller.signal })
      .then((response) => {
        if (!response.ok) return null
        const rawLength = response.headers.get('content-length')
        const bytes = rawLength ? Number(rawLength) : 0
        return Number.isFinite(bytes) && bytes > 0 ? bytes : null
      })
      .catch(() => null)
      .then((bytes) => {
        if (controller.signal.aborted) return
        const tooLarge = bytes !== null && bytes > MAX_VIEWER_MODEL_BYTES
        setModelSizeCheck({ url: modelUrl, status: tooLarge ? 'too-large' : 'ok', bytes })
        if (tooLarge) setModelLoadPhase('blocked')
      })

    return () => controller.abort()
  }, [modelUrl, isSplat])

  const canRenderMesh = Boolean(
    modelUrl
      && currentJob
      && !isSplat
      && (/^(?:blob:|data:)/i.test(modelUrl)
        || (modelSizeCheck.url === modelUrl && modelSizeCheck.status === 'ok')),
  )
  const modelIsTooLarge = Boolean(
    modelUrl
      && modelSizeCheck.url === modelUrl
      && modelSizeCheck.status === 'too-large',
  )

  const handleModelLoaded = useCallback(() => {
    setModelLoadPhase('ready')
  }, [])

  const handleModelError = useCallback(() => {
    setModelLoadPhase('error')
  }, [])

  // The splat viewer needs binary .splat — route raw workspace .ply through the
  // conversion endpoint; import URLs already point at a .splat via serve-file.
  const splatUrl = outputUrl.startsWith('/workspace/')
    ? `${apiUrl}/optimize/ply-to-splat?path=${encodeURIComponent(outputUrl.slice('/workspace/'.length))}`
    : modelUrl

  // Reset view state when model changes
  useEffect(() => {
    setSelected(false)
    setViewMode('solid')
    setStoreMeshStats(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only when the model changes; setters are stable
  }, [modelUrl])

  // Clear the shared selection when the viewer unmounts — the store would
  // otherwise keep it set and flash a stale selection on the next mount.
  useEffect(() => () => setSelected(false), [setSelected])

  // Delete key removes the model from the scene
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== 'Delete') return
      if (document.activeElement instanceof HTMLInputElement) return
      if (!selected) return
      setCurrentJob(null)
      setSelected(false)
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- setSelected is a stable store setter
  }, [selected, setCurrentJob])

  const handleScreenshot = () => {
    const dataUrl = isSplat
      ? splatRef.current?.screenshot() ?? null
      : canvasRef.current?.toDataURL('image/png') ?? null
    if (!dataUrl) return
    const link = document.createElement('a')
    link.download = `polykit-${Date.now()}.png`
    link.href = dataUrl
    link.click()
  }

  // Snapshot the pre-drag pose when a gizmo manipulation starts.
  const handleGizmoDragStart = useCallback(() => {
    if (meshObject) {
      pendingTransform.current = {
        p: meshObject.position.clone(),
        q: meshObject.quaternion.clone(),
        s: meshObject.scale.clone(),
      }
    }
  }, [meshObject])

  // Commit the snapshot on release, but only if the pose actually changed.
  const handleGizmoDragEnd = useCallback(() => {
    const before = pendingTransform.current
    pendingTransform.current = null
    if (!before || !meshObject) return
    const changed = !meshObject.position.equals(before.p)
      || !meshObject.quaternion.equals(before.q)
      || !meshObject.scale.equals(before.s)
    if (changed) transformHistory.current.push(before)
  }, [meshObject])

  // Revert the most recent gizmo manipulation. Returns false when there is
  // nothing to undo, so the caller can fall back to the mesh-history undo.
  const undoTransform = useCallback((): boolean => {
    const prev = transformHistory.current.pop()
    if (!prev || !meshObject) return false
    meshObject.position.copy(prev.p)
    meshObject.quaternion.copy(prev.q)
    meshObject.scale.copy(prev.s)
    return true
  }, [meshObject])

  // Expose transform-undo so the page's Ctrl+Z undoes gizmo edits first.
  useEffect(() => {
    if (!gizmoUndoRef) return
    gizmoUndoRef.current = undoTransform
    return () => { if (gizmoUndoRef.current === undoTransform) gizmoUndoRef.current = null }
  }, [gizmoUndoRef, undoTransform])

  // Drop the transform history when the model changes.
  useEffect(() => {
    transformHistory.current = []
    pendingTransform.current = null
  }, [modelUrl])

  // Memoise the post-processing stack so its children stay referentially stable.
  // @react-three/postprocessing rebuilds (recompiles) all EffectPasses whenever the
  // <EffectComposer> children identity changes; without this, every Viewer3D re-render
  // (e.g. dragging a Lighting slider) recompiles the outline shader. The Outline still
  // tracks selection through the <Selection> context, so nothing here needs to depend
  // on render state.
  const postProcessing = useMemo(() => (
    <EffectComposer
      autoClear={false}
      multisampling={SELECTION_OUTLINE_MULTISAMPLING}
      resolutionScale={SELECTION_OUTLINE_RESOLUTION_SCALE}
      frameBufferType={THREE.HalfFloatType}
    >
      <Outline
        blur={SELECTION_OUTLINE_BLUR}
        edgeStrength={SELECTION_OUTLINE_EDGE_STRENGTH}
        visibleEdgeColor={SELECTION_OUTLINE_VISIBLE_COLOR}
        hiddenEdgeColor={SELECTION_OUTLINE_HIDDEN_COLOR}
        xRay={false}
      />
    </EffectComposer>
  ), [])


  return (
    <ModelErrorBoundary
      resetKey={modelUrl}
      onError={handleModelError}
      fallback={<ModelLoadError />}
    >
      <div className="relative h-full w-full bg-[#303030]">
        {/* Splat path → fully isolated viewer (mkkellogg, outside R3F) */}
        {modelUrl && isSplat && splatUrl ? (
          <Suspense fallback={<div className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground">{t('assets.loadingSplat')}</div>}>
            <SplatViewer ref={splatRef} url={splatUrl} autoRotate={autoRotate} />
          </Suspense>
        ) : null}

        {/* Mesh path → the interactive Canvas scene */}
        {!isSplat && (
        <Canvas
          className="relative z-10"
          onPointerMissed={() => setSelected(false)}
          camera={{ position: [0, 1.5, 4], fov: 45 }}
          dpr={[1, 2]}
          gl={{
            antialias: true,
            alpha: true,
            preserveDrawingBuffer: true,
            outputColorSpace: THREE.SRGBColorSpace,
          }}
        >
          {/* Blender-style neutral graphite viewport; the app chrome stays near-black. */}
          {modelUrl && <color attach="background" args={['#303030']} />}
          <CanvasCapture domRef={canvasRef} />
          <ambientLight intensity={lightSettings.ambientIntensity ?? DEFAULT_LIGHT_SETTINGS.ambientIntensity} />
          <Environment background={false}>
            <Lightformer intensity={2 * (lightSettings.envIntensity ?? DEFAULT_LIGHT_SETTINGS.envIntensity)} position={[0, 4, 4]} scale={8} />
            <Lightformer intensity={0.5 * (lightSettings.envIntensity ?? DEFAULT_LIGHT_SETTINGS.envIntensity)} position={[-4, 2, -4]} scale={6} />
            <Lightformer intensity={0.3 * (lightSettings.envIntensity ?? DEFAULT_LIGHT_SETTINGS.envIntensity)} position={[4, 1, -4]} scale={6} />
          </Environment>

          {modelUrl ? <gridHelper args={[10, 20, '#474747', '#383838']} /> : <DefaultViewportScene />}
          {/* Keep the floor-plane axes visible for loaded models. The empty
              starter scene also shows the vertical axis to establish depth. */}
          <axesHelper args={[5]} scale={modelUrl ? [1, 0, 1] : [1, 1, 1]} />

          {canRenderMesh && modelUrl && currentJob ? (
            <Selection enabled={selected}>
              {postProcessing}
              <Suspense fallback={null}>
                <directionalLight position={[5, 8, 5]} color={lightSettings.mainColor} intensity={lightSettings.mainIntensity} castShadow />
                <directionalLight position={[-4, 2, -4]} color={lightSettings.fillColor} intensity={lightSettings.fillIntensity} />
                <MeshModel
                  url={modelUrl}
                  jobId={currentJob.id}
                  viewMode={viewMode}
                  selected={selected}
                  onStats={setStoreMeshStats}
                  onSelect={() => setSelected(true)}
                  onObject={setMeshObject}
                  onLoaded={handleModelLoaded}
                  separation={separation}
                  onPartCount={setPartCount}
                />
              </Suspense>
            </Selection>
          ) : null}

          {selected && meshObject && gizmoMode === 'translate' && (
            <TranslateGizmo object={meshObject} onDragStart={handleGizmoDragStart} onDragEnd={handleGizmoDragEnd} />
          )}
          {selected && meshObject && gizmoMode === 'rotate' && (
            <RotateGizmo object={meshObject} onDragStart={handleGizmoDragStart} onDragEnd={handleGizmoDragEnd} />
          )}
          {selected && meshObject && gizmoMode === 'scale' && (
            <ScaleGizmo object={meshObject} onDragStart={handleGizmoDragStart} onDragEnd={handleGizmoDragEnd} />
          )}

          <OrbitControls
            makeDefault
            enablePan
            enableZoom
            enableRotate
            minDistance={0.5}
            maxDistance={20}
            autoRotate={autoRotate}
            autoRotateSpeed={1.5}
            enableDamping
            dampingFactor={0.05}
          />

          <GizmoHelper alignment="top-right" margin={[72, 72]} renderPriority={2}>
            <GizmoBubbles />
          </GizmoHelper>
        </Canvas>
        )}

        {modelUrl && modelLoadPhase === 'loading' && (
          <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center bg-background/35">
            <div className="flex max-w-xs items-center gap-3 rounded-xl border border-divider bg-card/90 px-4 py-3 backdrop-blur-sm" role="status" aria-live="polite">
              <span className="h-5 w-5 shrink-0 animate-spin rounded-full border-2 border-muted border-t-primary" />
              <div className="min-w-0">
                <p className="text-xs font-medium text-foreground">{t('assets.loading3DModel')}</p>
                <p className="mt-0.5 text-[10px] leading-relaxed text-muted-foreground">{t('assets.loading3DModelHint')}</p>
              </div>
            </div>
          </div>
        )}

        {modelIsTooLarge && (
          <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-background/25 px-6">
            <div className="max-w-sm rounded-xl border border-divider bg-card/95 px-5 py-4 text-center backdrop-blur-sm" role="alert">
              <p className="text-sm font-medium text-foreground">{t('assets.viewerModelTooLarge')}</p>
              <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
                {t('assets.viewerModelTooLargeHint', {
                  size: modelSizeCheck.bytes ? formatBytes(modelSizeCheck.bytes) : 'large',
                  limit: formatBytes(MAX_VIEWER_MODEL_BYTES),
                })}
              </p>
            </div>
          </div>
        )}

        {/* Left toolbar — visible only when a model is loaded */}
        {modelUrl && (
          <ViewerToolbar
            viewMode={viewMode}
            autoRotate={autoRotate}
            onViewMode={setViewMode}
            onAutoRotate={() => setAutoRotate((v) => !v)}
            onScreenshot={handleScreenshot}
            showViewModes={!isSplat}
            canSeparate={partCount > 1}
            separationOpen={separationOpen}
            onToggleSeparation={() => setSeparationOpen((open) => !open)}
          />
        )}

        {modelUrl && !isSplat && partCount > 1 && separationOpen && (
          <ViewerSeparationControl separation={separation} onSeparation={setSeparation} />
        )}

        {/* Bottom-left stats overlay */}
        {meshStats && (
          <div className="absolute bottom-4 left-4 pointer-events-none">
            <p className="text-xs text-muted-foreground">
              {meshStats.triangles.toLocaleString()} tri &bull; {meshStats.vertices.toLocaleString()} verts
            </p>
          </div>
        )}

        {/* Bottom-right hint */}
        {modelUrl && (
          <div className="absolute bottom-4 right-4 pointer-events-none">
            <p className="text-xs text-muted-foreground/70">
              {selected
                ? <>{t('assets.clickMeshToSelect')} &bull; <span className="text-muted-foreground">{t('assets.deleteToRemove')}</span></>
                : `${t('assets.dragToRotate')} • ${t('assets.scrollToZoom')}`
              }
            </p>
          </div>
        )}
      </div>
    </ModelErrorBoundary>
  )
}
