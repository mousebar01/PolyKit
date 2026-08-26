import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type Language = 'zh-CN' | 'en-US'
export type BackendStatus = 'not_started' | 'starting' | 'ready' | 'error'
export type SetupStatus = 'idle' | 'checking' | 'needed' | 'installing' | 'done' | 'error'
export interface SetupProgress { step: string; percent: number; currentPackage?: string }

export type GenerationStatus =
  | 'idle'
  | 'uploading'
  | 'generating'
  | 'done'
  | 'error'

export interface GenerationJob {
  id: string
  /** Server-side id used to reconnect polling after a browser refresh. */
  serverJobId?: string
  imageFile: string
  status: GenerationStatus
  progress: number
  step?: string
  outputUrl?: string
  originalOutputUrl?: string   // mesh URL before any optimization
  modelId?: string             // model used for this generation
  originalTriangles?: number   // polygon count of the original mesh
  generationOptions?: GenerationOptions
  error?: string
  createdAt: number
}

export interface GenerationOptions {
  modelId: string
  remesh: 'quad' | 'triangle' | 'none'
  enableTexture: boolean
  textureResolution: number
  modelParams: Record<string, any>
}

export interface LightSettings {
  mainIntensity: number
  mainColor: string
  fillIntensity: number
  fillColor: string
  ambientIntensity: number
  envIntensity: number
}

export interface AppToast {
  id: number
  message: string
  durationMs?: number
}

const DEFAULT_OPTIONS: GenerationOptions = {
  modelId: '',
  remesh: 'quad',
  enableTexture: false,
  textureResolution: 512,
  modelParams: {},
}

export const DEFAULT_LIGHT_SETTINGS: LightSettings = {
  // Matches the offline debug renderer's flat studio rig: two soft directional
  // lights (key ~0.8 / fill ~0.35) + high ambient (0.45) that lifts dark albedo
  // (black cat) out of "void" shadows, NO IBL (envIntensity 0).
  // All live-adjustable from the Lighting popover (Reset returns here).
  mainIntensity: 0.8,
  mainColor: '#ffffff',
  fillIntensity: 0.35,
  fillColor: '#ffffff',
  ambientIntensity: 0.45,
  envIntensity: 0.0,
}

interface AppState {
  // Backend
  backendStatus: BackendStatus
  apiUrl: string
  backendError: string | null

  // Current generation
  currentJob: GenerationJob | null

  // Selected image (shared between ImageUpload and the Generate button)
  selectedImagePath: string | null
  setSelectedImagePath: (path: string | null) => void
  selectedImagePreviewUrl: string | null
  setSelectedImagePreviewUrl: (url: string | null) => void
  selectedImageData: string | null   // base64 content for drag & drop (when path is unavailable)
  setSelectedImageData: (data: string | null) => void

  // Generation options
  generationOptions: GenerationOptions

  // Mesh stats (set by Viewer3D, read by GenerationHUD)
  meshStats: { vertices: number; triangles: number } | null
  setMeshStats: (stats: { vertices: number; triangles: number } | null) => void

  // Mesh selection (set by Viewer3D click, read by the Generate tools bar)
  meshSelected: boolean
  setMeshSelected: (selected: boolean) => void

  // Setup
  setupStatus:    SetupStatus
  setupProgress:  SetupProgress | null
  setupError:     string | null
  defaultDataDir: string
  platform: string
  arch: string
  checkSetup:     () => Promise<void>
  runSetup:       () => Promise<void>
  saveDataDir:    (baseDir: string) => Promise<void>

  // Error modal
  errorModal: string | null
  showError: (message: string) => void
  hideError: () => void

  // Toast
  toast: AppToast | null
  showToast: (message: string, durationMs?: number) => void
  hideToast: () => void

  // Mesh URL history (undo/redo)
  meshHistory: string[]
  historyIndex: number
  pushMeshUrl: (url: string) => void
  undoMesh: () => void
  redoMesh: () => void
  clearMeshHistory: () => void

  // UI preferences
  language: Language
  setLanguage: (language: Language) => void
  showRamIndicator: boolean
  setShowRamIndicator: (v: boolean) => void

  // 3D viewer lighting
  lightSettings: LightSettings
  setLightSettings: (settings: LightSettings) => void

  // Actions
  initApp: () => Promise<void>
  setCurrentJob: (job: GenerationJob | null) => void
  updateCurrentJob: (patch: Partial<GenerationJob>) => void
  setGenerationOptions: (patch: Partial<GenerationOptions>) => void
}

export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      backendStatus: 'not_started',
      apiUrl: '',
      backendError: null,

      setupStatus: 'idle',
      setupProgress: null,
      setupError: null,
      defaultDataDir: '',
      platform: '',
      arch: '',

      checkSetup: async () => {
        set({ setupStatus: 'checking' })
        const { needed, defaultDataDir, platform, arch } = await window.polykit.setup.check()
        set({ setupStatus: needed ? 'needed' : 'done', defaultDataDir, platform, arch })
      },

      saveDataDir: async (baseDir: string) => {
        await window.polykit.setup.saveDataDir(baseDir)
        get().runSetup()
      },

      runSetup: async () => {
        set({ setupStatus: 'installing', setupProgress: null, setupError: null })

        window.polykit.setup.offProgress()
        window.polykit.setup.offComplete()
        window.polykit.setup.offError()

        window.polykit.setup.onProgress((data) => {
          set({ setupProgress: data })
        })
        window.polykit.setup.onComplete(() => {
          set({ setupStatus: 'done', setupProgress: null })
        })
        window.polykit.setup.onError((data) => {
          set({ setupStatus: 'error', setupError: data.message })
        })

        // Fire and forget — progress comes via IPC events
        window.polykit.setup.run()
      },

      errorModal: null,
      showError: (message) => set({ errorModal: message }),
      hideError: () => set({ errorModal: null }),

      toast: null,
      showToast: (message, durationMs) => set({ toast: { id: Date.now(), message, durationMs } }),
      hideToast: () => set({ toast: null }),

      meshHistory: [],
      historyIndex: -1,

      pushMeshUrl: (url) => {
        const { meshHistory, historyIndex } = get()
        if (meshHistory[historyIndex] === url) return
        const next = [...meshHistory.slice(0, historyIndex + 1), url]
        set({ meshHistory: next, historyIndex: next.length - 1 })
      },

      undoMesh: () => {
        const { meshHistory, historyIndex } = get()
        if (historyIndex <= 0) return
        const newIndex = historyIndex - 1
        set({ historyIndex: newIndex })
        get().updateCurrentJob({ outputUrl: meshHistory[newIndex] })
      },

      redoMesh: () => {
        const { meshHistory, historyIndex } = get()
        if (historyIndex >= meshHistory.length - 1) return
        const newIndex = historyIndex + 1
        set({ historyIndex: newIndex })
        get().updateCurrentJob({ outputUrl: meshHistory[newIndex] })
      },

      clearMeshHistory: () => set({ meshHistory: [], historyIndex: -1 }),

      showRamIndicator: true,
      setShowRamIndicator: (v) => set({ showRamIndicator: v }),

      language: 'zh-CN',
      setLanguage: (language) => set({ language }),

      lightSettings: DEFAULT_LIGHT_SETTINGS,
      setLightSettings: (settings) => set({ lightSettings: settings }),

      currentJob: null,
      selectedImagePath: null,
      setSelectedImagePath: (path) => set({ selectedImagePath: path }),
      selectedImagePreviewUrl: null,
      setSelectedImagePreviewUrl: (url) => set({ selectedImagePreviewUrl: url }),
      selectedImageData: null,
      setSelectedImageData: (data) => set({ selectedImageData: data }),
      generationOptions: DEFAULT_OPTIONS,
      meshStats: null,
      setMeshStats: (stats) => set({ meshStats: stats }),
      meshSelected: false,
      setMeshSelected: (selected) => set({ meshSelected: selected }),
      initApp: async () => {
        set({ backendStatus: 'starting', backendError: null })

        window.polykit.python.offCrashed()
        window.polykit.python.onCrashed(({ code }) => {
          const msg = `FastAPI process crashed unexpectedly (exit code: ${code ?? 'unknown'})`
          set({ backendStatus: 'error', apiUrl: '', backendError: msg })
          get().showError(msg)
        })

        try {
          const result = await window.polykit.python.start()
          if (!result.success) throw new Error(result.error ?? 'Failed to start backend')
          const info = await window.polykit.app.info()
          set({
            backendStatus: 'ready',
            apiUrl: info.apiUrl,
            platform: info.platform,
            arch: info.arch,
          })
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err)
          set({ backendStatus: 'error', backendError: msg })
          get().showError(msg)
        }
      },

      setCurrentJob: (job) => set({ currentJob: job, meshStats: job === null ? null : get().meshStats }),

      updateCurrentJob: (patch) => {
        const current = get().currentJob
        if (!current) return
        set({ currentJob: { ...current, ...patch } })
      },

      setGenerationOptions: (patch) => {
        set((state) => ({ generationOptions: { ...state.generationOptions, ...patch } }))
      },
    }),
    {
      name: 'polykit-store',
      partialize: (state) => ({
        // An upload has no server task yet; persisting it would leave a
        // permanent "uploading" spinner if the browser is refreshed mid-upload.
        currentJob: state.currentJob?.serverJobId ? state.currentJob : null,
        generationOptions: state.generationOptions,
        language: state.language,
        showRamIndicator: state.showRamIndicator,
        lightSettings: state.lightSettings,
      }),
    }
  )
)
