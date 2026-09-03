import { useCallback, useEffect } from 'react'
import { useAppStore } from '@shared/stores/appStore'
import { useApi } from './useApi'

// Several mounted Generate components consume this hook. Keep one poller per
// server job so a single refresh cannot multiply API traffic threefold.
const activePolls = new Set<string>()

export function useGeneration() {
  const { currentJob, setCurrentJob, updateCurrentJob, pushMeshUrl } = useAppStore()
  const { pollJobStatus } = useApi()

  const pollUntilDone = async (jobId: string) => {
    if (activePolls.has(jobId)) return
    activePolls.add(jobId)
    try {
      while (true) {
        await new Promise((r) => setTimeout(r, 1000))

        const result = await pollJobStatus(jobId)

        if (result.status === 'cancelled') {
          setCurrentJob(null)
          break
        }

        if (result.status === 'done') {
          updateCurrentJob({ status: 'done', progress: 100, outputUrl: result.outputUrl, originalOutputUrl: result.outputUrl })
          if (result.outputUrl) pushMeshUrl(result.outputUrl)
          break
        }

        if (result.status === 'error' || result.status === 'interrupted') {
          updateCurrentJob({ status: 'error', error: result.error ?? 'The server restarted before this generation completed.' })
          break
        }

        updateCurrentJob({
          progress: result.progress,
          step: result.step,
        })
      }
    } finally {
      activePolls.delete(jobId)
    }
  }

  // A persisted active job may outlive the page that started it. Reconnect to
  // the server task automatically when Generate mounts after a refresh.
  useEffect(() => {
    const serverJobId = currentJob?.serverJobId
    if (!serverJobId || (currentJob.status !== 'uploading' && currentJob.status !== 'generating')) return
    void pollUntilDone(serverJobId).catch((error) => {
      updateCurrentJob({
        status: 'error',
        error: error instanceof Error ? error.message : String(error),
      })
    })
    // The poller is guarded by activePolls; changing UI state must not restart it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentJob?.serverJobId, currentJob?.status, updateCurrentJob])

  const reset = useCallback(() => setCurrentJob(null), [setCurrentJob])

  return { currentJob, reset }
}
