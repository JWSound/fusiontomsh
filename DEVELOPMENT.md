# Fusion add-in development

This repository is configured for Fusion's supported Python debugging workflow in VS Code.

## One-time setup

1. Install the VS Code **Python**, **Python Debugger**, and **Codex** extensions.
2. In Fusion, open **Utilities > Add-Ins > Scripts and Add-Ins**.
3. Add this repository with **Script or add-in from device** if it is not already listed.
4. Disable **Run on Startup** while developing so Fusion does not load a second copy before the debugger attaches.
5. Select `MSHExport` and choose **Edit in code editor**. Fusion uses this action to prepare its VS Code debug bridge.

The checked-in `.vscode/launch.json` attaches to Fusion on `localhost:9000`. The API stubs under the Fusion user-data folder provide completion and type information for `adsk` modules.

## Debug an event handler

1. In Fusion's Scripts and Add-Ins dialog, stop `MSHExport` if it is running.
2. In VS Code, place breakpoints in `GmshFEMInputChangedHandler.notify` and `_add_fem_group_inputs`.
3. Press **F5** and choose **Python: Attach**. Fusion loads the add-in and calls `run`.
4. Return to Fusion, open **Export FEM MSH**, and click **Add Group**.
5. Inspect `event_args.input.id`, `state.group_ids`, and any exception in the VS Code debugger.
6. Use **Disconnect**, not **Restart**, when finishing a debug session. Stop the add-in in Fusion before starting another session if needed.

`MSHExport.run` reloads the repository's local Python modules before registering commands. This prevents Fusion's long-lived Python process from retaining stale helper-module function signatures between add-in restarts.

## Read the add-in log

FEM dialog lifecycle and input events are also written to Fusion's application log with the prefix `MSHExport FEM:`. This gives Codex a durable artifact to inspect even when no breakpoint is active.

From a VS Code terminal:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/tail_fusion_log.ps1
```

To watch events while clicking in Fusion:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/tail_fusion_log.ps1 -Follow
```

The VS Code task **Fusion: Tail MSHExport Log** runs the same command.

## Local verification

Logic that does not require Fusion's process should remain in ordinary Python modules and have unit tests under `tests/`:

```powershell
python -B -m unittest discover -s tests -v
```

Fusion API UI behavior still requires Fusion as the host, but breakpoints plus the persistent app log make the event path observable rather than relying on UI symptoms alone.
