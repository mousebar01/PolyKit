import { Component, Suspense, useLayoutEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { Environment, Lightformer, useGLTF } from '@react-three/drei'
import * as THREE from 'three'

/**
 * Tiny interactive 3D preview for an asset-library card, in the spirit of
 * Meshy: a small three.js scene that auto-rotates while the card is hovered.
 * The server serves a lightweight preview GLB (simplified geometry +
 * downscaled textures), so this never downloads the full 100MB+ mesh.
 *
 * Only mounted while hovered (see AssetCard), so at most one WebGL context
 * is alive at a time. The canvas is transparent until the model draws, so
 * the resting PNG thumbnail stays visible underneath while it loads.
 */

function preparePreviewMaterials(scene: THREE.Object3D): void {
  scene.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return
    const materials = Array.isArray(child.material) ? child.material : [child.material]
    for (const material of materials) {
      const textured = material as THREE.Material & {
        map?: THREE.Texture | null
        emissiveMap?: THREE.Texture | null
      }
      // Base-color images are authored in sRGB; GLTFLoader sometimes leaves
      // the color space implicit, so normalise it at the boundary.
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

function PreviewModel({ url }: { url: string }): JSX.Element {
  const { scene } = useGLTF(url)
  const root = useRef<THREE.Group>(null)
  const { camera } = useThree()

  useLayoutEffect(() => { preparePreviewMaterials(scene) }, [scene])

  // Fit the model into a unit box, centered at the origin.
  useLayoutEffect(() => {
    const box = new THREE.Box3().setFromObject(scene)
    const size = box.getSize(new THREE.Vector3())
    const center = box.getCenter(new THREE.Vector3())
    const maxDim = Math.max(size.x, size.y, size.z) || 1
    const scale = 1 / maxDim
    scene.position.set(-center.x * scale, -center.y * scale, -center.z * scale)
    scene.scale.setScalar(scale)
    if (root.current) root.current.rotation.y = -0.6 // nice 3/4 initial angle (front is +Z)
    camera.lookAt(0, 0, 0)
  }, [scene, camera])

  useFrame((_, delta) => {
    if (root.current) root.current.rotation.y += delta * 0.55
  })

  return (
    <group ref={root}>
      <primitive object={scene} />
    </group>
  )
}

class PreviewErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true }
  }
  render(): ReactNode {
    return this.state.failed ? null : this.props.children
  }
}

export default function AssetPreview3D({ url, className }: { url: string; className?: string }): JSX.Element {
  return (
    <PreviewErrorBoundary>
      <Canvas
        className={className}
        frameloop="always"
        dpr={[1, 2]}
        gl={{ antialias: true, alpha: true, powerPreference: 'high-performance' }}
        camera={{ position: [0, 0.35, 2.4], fov: 40 }}
        style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}
        aria-hidden="true"
      >
        <ambientLight intensity={0.55} />
        <directionalLight position={[4, 6, 5]} intensity={1.1} />
        <directionalLight position={[-4, 2, -4]} intensity={0.35} />
        <Environment resolution={64}>
          <Lightformer intensity={1.2} position={[0, 4, 4]} scale={6} />
          <Lightformer intensity={0.4} position={[-4, 2, -4]} scale={5} />
          <Lightformer intensity={0.3} position={[4, 1, -4]} scale={5} />
        </Environment>
        <Suspense fallback={null}>
          <PreviewModel url={url} />
        </Suspense>
      </Canvas>
    </PreviewErrorBoundary>
  )
}
