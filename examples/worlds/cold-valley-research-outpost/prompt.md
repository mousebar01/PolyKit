# Cold Valley Research Outpost

## Prompt

创建一个建在寒冷山谷中的废弃科幻研究前哨站。场景主体是一座位于山谷中央的小型研究基地，由两栋低矮的混凝土建筑、一座连接它们的金属走廊、入口台阶和一个小型通讯平台组成。基地周围有崎岖地形、岩石、少量耐寒松树和散落的工业废料。

在主建筑入口附近放置一台大型、独特的老式科幻通讯终端，作为场景的 Hero Asset。它应该明显比普通道具精细，有厚重的金属外壳、天线、控制面板和长期暴露在恶劣天气中的磨损感。基地旁边再放置几个重复使用的工业储物箱和设备箱，但不要让这些次要物件抢过通讯终端的视觉重点。

整体风格偏写实、功能主义、冷战时期工业设计与轻度科幻结合。建筑结构应该合理、可以实际使用，不要做成纯概念艺术造型。保持清晰的道路、入口和建筑之间的空间关系。环境应该显得荒凉、寒冷、长期无人维护，但不要用大量随机杂物填满场景。

## Structured intent

- Deliverable: navigable exterior environment with a hero three-quarter review camera.
- Focal hierarchy: weathered communications terminal → two-building base and connector → valley terrain, pines, rocks, and restrained industrial debris.
- Spatial invariants: the two buildings remain separate manufactured masses, the metal corridor bridges them, both entrances face the approach path, and the communications platform stays beside (not inside) the main entry.
- Scale hypothesis: metric Blender scene, one Blender unit equals one metre; building height is approximately 4 m and the hero terminal is approximately 4.5 m including its antenna.
- Construction grammar: beveled hard-surface building masses, real repeated stair offsets via Array modifiers, independent corridor frame/panels, modular crates, linked low-poly pine instances, and direct unique hero terminal assembly.
- Material intent: cold weathered concrete, painted/corroded steel, frosted glass, compact snow/rock terrain, and restrained emissive cyan controls.
- Lighting intent: low winter sun from camera-left, cold sky fill, readable dark-side planes, and no dramatic colored lights that hide geometry.

## Local test

The first executable test is the deterministic Blender builder next to this file:

```sh
/home/sy/.local/bin/blender -b --python examples/worlds/cold-valley-research-outpost/build_scene.py -- \
  --output-dir /home/sy/.polykit/workspace/Workflows/cold_valley_research_outpost
```

It writes a `.blend`, a portable `.glb`, hero/overview/top review PNGs, render evidence, and a validation report to the workspace. The generated Blender artifact is the production result; this prompt and the scene plan are the editable intent records.
