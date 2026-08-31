# World Builder

PolyKit 的 World Builder 是一组 **World domain APIs、确定性 validators、Workflow recipes 和 runtime contracts**。它不属于聊天系统，也不要求嵌入式 Agent runtime。

任何客户端都通过同一套 FastAPI 能力创建和修改世界：Web、CLI、自动化脚本或其他 HTTP 调用方没有特殊权限，也不复制领域逻辑。

## Boundary

```text
Web / CLI / automation
        ↓
     World API
        ↓
World domain compiler + validators
        ↓
Workflow Engine / Workflow Runs
        ↓
Node Packs
        ↓
Blender / local models / processors
        ↓
Artifacts / GLB / validation evidence
```

FastAPI 是权威运行时。Three.js 负责浏览器中的交互预览；Blender MCP 可以作为独立的开发或 authoring 集成，但不是 World Builder 的控制面。

## Source of truth

Schema-v2 `WorldDocument` 保存产品/领域事实：

- intent
- `BuildSpec`
- `ScenePlan`
- `GameSpec`
- construction / visual / gameplay quality facts
- artifact references

工作流执行状态不写进 `WorldDocument`。长任务的生命周期、节点进度、错误、事件和 evidence refs 属于现有 `WorkflowRun`。

因此：

```text
WorldDocument = what the world is
WorkflowRun   = what computation is/was running
```

视觉验证也遵守同一边界：完整的 `VisualValidationReport` 是 Workflow/evidence artifact；`runtime.quality.visual` 只保存紧凑的领域质量事实或 report reference，不保存视觉制作阶段、重试或回滚状态。

## Typical flow

1. 创建或保存 World。
2. 编译语义 `ScenePlan`。
3. 使用 World build bridge 把 `BuildSpec` 编译成标准 WorkflowRun。
4. Workflow Engine 通过 Node Packs 执行 Blender / model / process 节点。
5. 将完成的 workspace artifact 绑定回稳定的 world object id。
6. 组合场景。
7. 运行 spec / blockout / construction / visual / gameplay / final validators。
8. 通过 WorkflowRun inspect 查看执行证据；检查操作本身不推进或修改任务。

Validators 只报告事实和证据，不决定聊天或客户端下一步要做什么。缺失的视觉或体积证据不能被静默当作通过。

视觉验证采用 evidence-first 规则：deterministic image metrics、semantic review 和 World/geometry spatial checks 是独立证据来源；全局图像相似度不能覆盖 P0、材质类别、camera 或真实几何关系失败。详情见 [Visual validation](visual-validation.md)。

## CLI

`tools/polykit-cli/polykit.py` 是普通的 JSON-first HTTP automation client。例如：

```bash
python tools/polykit-cli/polykit.py world create --name cabin
python tools/polykit-cli/polykit.py world get <world-id>
python tools/polykit-cli/polykit.py world compile-scene <world-id> --json scene.json
python tools/polykit-cli/polykit.py world build-structure <world-id>
python tools/polykit-cli/polykit.py workflow-run inspect <run-id>
python tools/polykit-cli/polykit.py world attach-asset <world-id> <object-id> Workflows/cabin.glb
python tools/polykit-cli/polykit.py world validate <world-id> world.visual.validate --run-id <run-id>
python tools/polykit-cli/polykit.py world validate <world-id> world.final.validate
```

CLI 只调用 HTTP API。World artifact 绑定、验证、WorkflowRun 生命周期等规则仍由服务端实现。

## Relevant modules

| Concern | Location |
| --- | --- |
| World document creation / artifact binding | `api/services/world_domain.py` |
| World persistence | `api/services/world_store.py` |
| World runtime quality | `api/services/world_runtime.py` |
| Deterministic world validators | `api/services/world_validation.py` |
| Visual report + reference image metrics | `api/services/visual_validation.py` |
| World → Workflow recipes | `api/services/world_workflows.py` |
| World HTTP API | `api/routers/workspace_worlds.py`, `api/routers/world_artifacts.py` |
| Workflow execution / observability | `api/services/workflow_engine.py`, `api/services/run_observability.py` |
| Browser world runtime | `src/areas/worlds/` |
| Automation CLI | `tools/polykit-cli/polykit.py` |

## Design rules

- No second durable task state machine beside `WorkflowRun`.
- No workflow stage state inside `WorldDocument`.
- No CLI-side duplication of domain mutations.
- No browser-side execution of model/process nodes.
- Construction contacts and tolerances are measured deterministically.
- `inside` / `passes-through` require volumetric evidence.
- Missing visual evidence remains `needs_review` or `fail` according to the required gate; it is never an invented pass.
- A semantic or spatial visual failure cannot be waived by aggregate image similarity.
