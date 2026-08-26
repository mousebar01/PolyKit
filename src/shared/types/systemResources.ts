export interface ResourceMemory {
  used: number | null
  total: number | null
}

export interface SystemGpuResource {
  index: number
  name: string
  usage: number | null
  memory: ResourceMemory
  temperature: number | null
}

export interface SystemResourceSnapshot {
  cpu: {
    usage: number | null
    cores: number | null
  }
  memory: ResourceMemory & {
    available: number | null
  }
  gpus: SystemGpuResource[]
  sampled_at: number
  cache_seconds: number
}
