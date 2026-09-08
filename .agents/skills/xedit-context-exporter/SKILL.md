---
name: xedit-context-exporter
description: Export deterministic Skyrim dialogue context from an original ESP/ESM/ESL with xEdit, including DIAL to child INFO relationships, quest/branch/topic metadata, prompts, conditions, responses, and structurally resolved speaker candidates. Use when xTranslator XML no longer preserves enough structure to associate INFO lines with their real dialogue topics or speakers, or when translation needs plugin-grounded dialogue context without using the xEdit GUI.
compatibility: Requires Windows, Python 3.10+, xEdit under tools/xEdit, and an installed Skyrim Special Edition Data directory containing the target plugin's masters. Uses xEdit in command-line Script mode and never modifies the source plugin.
metadata:
  version: "0.5.0"
  status: "已退役 2026-09-08，由 Mutagen 导出器接替（见下）"
---

> **退役说明（2026-09-08）**：xEdit 命令行模式导出在 Druadach 上超时（>10 分钟无完成标记），且首次缓存构建极慢。已改用 Mutagen（C# 库，tools/Mutagen 克隆 + .agents/skills/mutagen-dialogue-exporter/scripts/DialogueExport）以 overlay 模式解析插件：Druadach.esm 全量 DIAL→INFO 导出仅 **1.3 秒**（5235 DIAL / 8089 INFO，含 speaker 解析、prompt、responses、conditions），与 xTranslator 的 INFO EDID 连接率 99.2%。产物规范：`.work/<plugin>-mutagen-dialogue.json`。下文保留作为历史参考与兑备。

# xEdit Context Exporter

Use xEdit as a read-only parsing backend to recover dialogue structure that xTranslator XML does not preserve.

The first version deliberately exports only relationships that can be read deterministically from plugin structure. Do not infer a speaker, quest relationship, or dialogue order when the plugin does not encode it directly.

## Run the exporter

From the project root:

```text
py -3 .agents/skills/xedit-context-exporter/scripts/run_xedit_context_export.py mods/evgSIRENROOT.esm/evgSIRENROOT.esm
```

By default the JSON is written next to the plugin as:

```text
mods/<plugin>/<plugin-stem>_dialogue_context.json
```

Choose another output path with:

```text
py -3 .agents/skills/xedit-context-exporter/scripts/run_xedit_context_export.py <plugin> --output <file.json>
```

Use `--game-data` only when automatic Skyrim SE Data discovery is wrong. Use `--xedit` only when the project xEdit executable is somewhere other than `tools/xEdit/`.

When dialogue conditions intentionally refer to actors from an optional or soft-dependency plugin without declaring that plugin as a master, load it as context only:

```text
py -3 .agents/skills/xedit-context-exporter/scripts/run_xedit_context_export.py <target-plugin> --context-plugin ccbgssse001-fish.esm
```

Repeat `--context-plugin` for multiple context providers. A bare filename is resolved from the installed Skyrim Data directory. The wrapper reads each context plugin's own master list and stages any additional required masters recursively, but it never edits the target plugin's declared masters.

## What the wrapper does

The Python wrapper:

1. Reads the target plugin's TES4 header to obtain its declared masters.
2. Locates Skyrim Special Edition's Data directory, or uses `--game-data`.
3. Creates an isolated project-local staging Data directory under `.work/xedit-context-exporter/`.
4. Symlinks each required master from the real game Data directory and symlinks the target plugin from the MOD directory.
5. Generates a dedicated `plugins.txt` containing the target masters, target plugin, and any explicitly requested context-only plugins plus their required dependencies.
6. Launches xEdit in SSE Script mode with `-D`, `-P`, `-autoload`, `-script`, and `-autoexit`.
7. Watches for the deterministic JSON completion marker emitted by `ExportDialogueContext.pas`.
8. Gives xEdit a brief opportunity to honor `-autoexit`; if xEdit 4.1.5f remains idle after the final JSON is stable, terminates that read-only process instead of waiting forever.
9. If xEdit exits during the narrow capture window, accepts the staging JSON only after it successfully parses and validates; this avoids discarding a complete export because of process-shutdown timing.
10. Validates that the JSON belongs to the requested plugin, then moves it to the requested output path.

The real Skyrim Data directory and original MOD plugin remain untouched.

## Exported structure

The wrapper writes the target plugin name to a staging config file. The Pascal script resolves that configured file explicitly instead of assuming the final loaded file is the target. This lets optional context plugins load alongside the target without changing which plugin is being exported.

For each `DIAL` record owned by the target plugin it exports:

- load-order FormID and local FormID
- EditorID
- topic text (`FULL`)
- category and subtype
- linked Quest identity
- linked Branch identity
- child `INFO` records from `ChildGroup(DIAL)`

For each child `INFO` it exports:

- load-order FormID and local FormID
- EditorID when present
- prompt (`RNAM - Prompt`)
- explicit `ANAM - Speaker` link when present
- previous INFO link when present
- all Conditions as raw readable evidence plus condition function
- every response number and response text
- emotion type/value
- script notes

The load-order FormID is important for joining xTranslator entries such as `[04026BC9]` back to their real INFO records.

The exporter also emits target-plugin `QUST` records and their alias entries. Alias output includes the numeric alias ID, xEdit path/name, known Unique Actor / Forced Reference / External Alias / Voice Types links, and an immediate-child raw field listing. The raw field listing is intentional during recovery work: use it to discover the actual alias structures present in a MOD before adding more specific parsing rules.

## Deterministic versus inferred context

Treat these as deterministic:

- DIAL -> child INFO membership from `ChildGroup(DIAL)`
- DIAL -> Quest link from `QNAM - Quest`
- DIAL -> Branch link from `BNAM - Branch`
- explicit INFO speaker from `ANAM - Speaker`
- response text and metadata stored directly on INFO

When an INFO has no explicit speaker, the exporter resolves two deterministic condition patterns to a concrete NPC when possible:

- `GetIsID`: resolve the referenced NPC or actor reference to its NPC base record.
- `GetIsAliasRef`: resolve the referenced quest alias only when it is backed by a Unique Actor, a Forced Reference whose base is an NPC, or an External Alias Reference chain that ultimately resolves that way.

Resolved actors appear in `speaker_candidates` together with an explicit `method` and `evidence` field. Conditions such as faction, race, cell, and arbitrary quest state are preserved as evidence but are not promoted to a specific speaker merely because their candidate set happens to be small.

For `GetIsVoiceType`, export the linked `VTYP` record, every NPC that directly references that voice type through `VTCK - Voice`, recursively discovered template descendants, and an `effective_npc_users` set. The effective set follows the direct-voice-else-template rule used by xEdit's bundled `Export dialogues.pas`: an NPC with its own `VTCK` uses that voice; otherwise voice resolution follows `TPLT - Template`.

If `effective_npc_users` contains exactly one NPC, promote it to `speaker_candidates` as `condition:GetIsVoiceType:unique_effective_npc`. If several effective NPC records all resolve to the same template root and have the same non-empty display name, promote that template root as `condition:GetIsVoiceType:single_character_template_lineage`; this narrow case covers alternate records for one character such as live/dead variants. Broad shared voices remain unresolved.

Any `RACE` records referencing the VTYP are also exported. A race-backed voice type remains broad even when the direct NPC list is short. Do not infer a speaker merely from a Voice Type EditorID that appears to contain a character name; the promotion rules must be satisfied by record links and template structure.

For common linked condition parameters, preserve their actual referenced records as structured JSON too. This currently includes Cell, Faction, Race, Global, Quest, Inventory Object, and Actor Base parameters when present. These fields are intended for deterministic intersection of condition evidence in later recovery passes.

Each condition also carries its native CTDA type flags, human-readable Run-On value when xEdit exposes it, and a linked Run-On reference when present. Do not intersect condition predicates as if they all apply to the speaker until these fields show that they operate on the same subject and their OR semantics are understood.

For `GetInFaction` / `GetFactionRank`, also export NPC records that directly reference the faction. This allows later passes to intersect faction membership with voice-type or other actor evidence without relying on names or dialogue semantics.

Do not treat faction backlinks as complete by assumption. The exporter also scans winning `NPC_` records from every loaded file and checks their `Factions` entries forward against the condition faction, exporting that independently as `scanned_direct_npc_members`. Use discrepancies between the backlink and forward-scan sets as evidence that the backlink index is incomplete rather than silently dropping actors.

Faction evidence also includes recursively discovered NPC template descendants of direct faction members. Treat this as an inheritance superset until template flags are checked: it is useful for proving that no candidate exists in the static template graph, but a listed descendant still needs template-flag validation before claiming the faction is actually inherited at runtime.

For `GetInCell`, inspect the persistent and temporary child reference groups across the master CELL and every CELL override, then export the base NPCs of placed `ACHR` records. Bethesda child references can be distributed across CELL versions, so looking only at the winning CELL override is incomplete. The merged set provides structural evidence for actors placed in that cell, while still not proving runtime presence when packages can move actors.

NPCs emitted as Voice Type users, Faction members, or Cell actor bases also include their direct template, race, direct voice type, and direct faction entries. These are raw inheritance clues; a direct template link alone does not prove that every field is inherited because NPC template flags control individual data categories.

Scene-bound dialogue and non-unique alias conditions remain a later layer. Do not infer a specific NPC from them yet.

## Safety boundaries

- Never save, clean, compact, renumber, or otherwise edit the plugin.
- Never run an xEdit script containing save dialogs, confirmation dialogs, or mutation logic as part of this workflow.
- Keep all staging files and xEdit caches under `.work/xedit-context-exporter/`.
- Do not copy the target plugin into the real game Data directory.
- Do not load unrelated user plugins. The generated `plugins.txt` should contain only declared masters and the target.
- Context-only plugins are an explicit exception to the previous rule: load them only when the user/project needs their records to resolve a soft dependency or compatibility context. Do not silently sweep the user's whole load order into the export.
- A context-only plugin remains context only. Never rewrite it into the target plugin's TES4 `MAST` list or describe it as a declared dependency unless the target actually declares it.
- If a declared master is missing from the resolved game Data directory, fail clearly rather than silently substituting another file.
- A command timeout is an execution failure, not proof that the plugin is invalid. xEdit's first cache build can be slow.
- Do not use process exit alone as the success signal. In the tested xEdit 4.1.5f Script mode, the final JSON can be complete while the otherwise idle process remains open despite `-autoexit`.

## Validate changes

Before using a modified Skill, follow `.agents/skills/skill-creator/SKILL.md` and run its `quick-validate` preflight and validator.

Then at minimum run:

```text
py -3 -m py_compile .agents/skills/xedit-context-exporter/scripts/run_xedit_context_export.py
py -3 .agents/skills/xedit-context-exporter/scripts/run_xedit_context_export.py mods/evgSIRENROOT.esm/evgSIRENROOT.esm
```

For Sirenroot, validate mechanically that:

- the source ESM hash is unchanged;
- the JSON identifies `evgSIRENROOT.esm` as its target;
- at least one DIAL contains one or more child INFO records;
- INFO load-order FormIDs can be found among the `INFO:NAM1` FormIDs exported by xTranslator;
- no output was written into the real Skyrim Data directory.

