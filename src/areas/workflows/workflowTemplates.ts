import type { Workflow, WFEdge, WFNode } from '@shared/types/runtime.d'

export interface WorkflowTemplate {
  templateId: string
  /** Primary node pack this template belongs to (display grouping). */
  nodePackId: string
  /** Node pack ids the template needs installed to run (e.g. ["trellis2"]). */
  requires?: string[]
  name: string
  description: string
  nodes: WFNode[]
  edges: WFEdge[]
}

// Auto-discover every template JSON in ./templates — dropping a validated
// workflow file into that folder makes it a template with no code changes.
const templateModules = import.meta.glob('./templates/*.json', { eager: true }) as Record<
  string,
  { default: WorkflowTemplate }
>

const TEMPLATES: WorkflowTemplate[] = Object.values(templateModules).map((mod) => mod.default)

function newId(): string {
  return crypto.randomUUID()
}

export function instantiateWorkflowTemplate(template: WorkflowTemplate): Workflow {
  const now = new Date().toISOString()
  const idMap = new Map(template.nodes.map((node) => [node.id, newId()]))
  const nodes = template.nodes.map((node) => ({
    ...structuredClone(node),
    id: idMap.get(node.id)!,
  }))
  const edges = template.edges.map((edge) => ({
    ...structuredClone(edge),
    id: newId(),
    source: idMap.get(edge.source)!,
    target: idMap.get(edge.target)!,
  }))
  return {
    id: newId(),
    name: template.name,
    description: template.description,
    templateId: template.templateId,
    nodes,
    edges,
    createdAt: now,
    updatedAt: now,
  }
}

export function getWorkflowTemplates(): WorkflowTemplate[] {
  return TEMPLATES
}
