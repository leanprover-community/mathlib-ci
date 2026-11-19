import CoreProbe

/-!
# PROBE: the core's executable

```
lake --dir path/to/coreProbe build                          # from the mathlib checkout
lake env path/to/coreProbe/.lake/build/bin/coreprobe --out FILE ROOT [ROOT ...]
```
-/

open Lean CoreProbe

def usage : String := "usage: coreprobe --out FILE ROOT [ROOT ...]"

unsafe def main : List String → IO UInt32
  | "--out" :: out :: roots@(_ :: _) => do
    if roots.any (·.startsWith "-") then
      IO.eprintln usage
      return 2
    let roots := roots.toArray.map (·.toName)
    initSearchPath (← findSysroot)
    withImportModules (roots.map ({ module := · })) {} (trustLevel := 1024) fun env => do
      let decls := reasonableDecls env roots
      let (nd, nm) ← writeCoreDump env roots decls out
      IO.println s!"Wrote the core fields of {nd} declarations and {nm} modules to {out}"
      return 0
  | _ => do
    IO.eprintln usage
    return 2
