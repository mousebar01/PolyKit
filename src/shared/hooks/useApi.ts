import axios from 'axios'
import { useAppStore, type GenerationOptions } from '@shared/stores/appStore'

export function useApi() {
  const apiUrl = useAppStore((state) => state.apiUrl)
  const client = axios.create({ baseURL: apiUrl })

  async function generateFromImage(
    imagePath: string,
    options: GenerationOptions,
    imageData?: string,
    signal?: AbortSignal,
  ): Promise<{ jobId: string }> {
    // Uploaded browser files are kept behind the runtime adapter until they
    // are sent to the server. The multipart compatibility endpoint now
    // compiles the upload into the same ExecutionPlan used by canonical Runs.
    const base64 = imageData ?? await window.polykit.fs.readFileBase64(imagePath)
    const byteArray = Uint8Array.from(atob(base64), (character) => character.charCodeAt(0))
    const blob = new Blob([byteArray], { type: 'image/png' })
    const filename = imagePath.split(/[\\/]/).pop() ?? 'image.png'

    const formData = new FormData()
    formData.append('image', blob, filename)
    formData.append('model_id', options.modelId)
    formData.append('remesh', options.remesh)
    formData.append('enable_texture', String(options.enableTexture))
    formData.append('texture_resolution', String(options.textureResolution))
    formData.append('params', JSON.stringify(options.modelParams))

    const { data } = await client.post<{ run_id: string }>('/workflow-runs/from-image', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      signal,
    })
    return { jobId: data.run_id }
  }

  async function pollJobStatus(jobId: string): Promise<{
    status: 'pending' | 'running' | 'done' | 'error' | 'cancelled' | 'interrupted'
    progress: number
    step?: string
    outputUrl?: string
    error?: string
  }> {
    const { data } = await client.get(`/runs/${jobId}`)
    return { ...data, outputUrl: data.output_url }
  }

  async function optimizeMesh(
    path: string,
    targetFaces: number,
  ): Promise<{ url: string; faceCount: number }> {
    const { data } = await client.post<{ url: string; face_count: number }>('/optimize/mesh', {
      path,
      target_faces: targetFaces,
    })
    return { url: data.url, faceCount: data.face_count }
  }

  async function cancelJob(jobId: string): Promise<void> {
    await client.delete(`/runs/${jobId}`).catch(() => {})
  }

  async function smoothMesh(
    path: string,
    iterations: number,
  ): Promise<{ url: string }> {
    const { data } = await client.post<{ url: string }>('/optimize/smooth', {
      path,
      iterations,
    })
    return { url: data.url }
  }

  async function importMesh(filePath: string): Promise<{ url: string }> {
    const { data } = await client.post<{ url: string }>('/optimize/import-by-path', { path: filePath })
    return { url: data.url }
  }

  return {
    generateFromImage,
    pollJobStatus,
    cancelJob,
    optimizeMesh,
    smoothMesh,
    importMesh,
  }
}
