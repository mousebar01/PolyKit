import { createContext, useContext } from 'react'

interface WorkflowNodeExecutionContextValue {
  isRunning: boolean
  runToHere: (nodeId: string) => void
}

const DEFAULT_VALUE: WorkflowNodeExecutionContextValue = {
  isRunning: false,
  runToHere: () => {},
}

export const WorkflowNodeExecutionContext = createContext<WorkflowNodeExecutionContextValue>(DEFAULT_VALUE)

export function useWorkflowNodeExecution(): WorkflowNodeExecutionContextValue {
  return useContext(WorkflowNodeExecutionContext)
}
