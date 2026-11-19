/-
Copyright (c) 2026 Damiano Testa. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Damiano Testa
-/
import Lean
import ImportGraph.Imports.ImportGraph
import ImportGraph.Graph.TransitiveClosure
import Mathlib.Tactic.ToDual
import Mathlib.Tactic.ToAdditive
import Batteries.Tactic.Alias
-- Probe (b): the core package's module, beside Mathlib's. Its `.lake/build/lib/lean` must be on
-- `LEAN_PATH`.
import CoreProbe

/-!
# PROBE: the project script, for Mathlib

`ARCHITECTURE.md`, *Processes and files*. The fields that need Mathlib's and Batteries' own
extensions, from #99's dumper (`6d4d41a`) unchanged, written through the core package's
framework: the same `reasonableDecls`, `gate` and `writeDumpFile`.

* `x` and `al`, as in #99;
* `dg`: true where the docstring is the one `alias` generates (`isAliasDocstring`), which #99
  left out of `d`;
* `imports.json`, as in #99.

```
LEAN_PATH="path/to/coreProbe/.lake/build/lib/lean:$(lake env printenv LEAN_PATH)" \
  lake env lean --run path/to/ProjectProbe.lean --out-dir DIR ROOT [ROOT ...]
```

It writes `DIR/api.mathlib.jsonl`, a header and a row only for a declaration with one of its
fields, and `DIR/imports.json`.
-/

open Lean CoreProbe

/-- The fields this writes, with their versions. -/
def projectFields : List (String × Nat) := [("n", 1), ("x", 1), ("al", 1), ("dg", 1)]

/-- The extensions read here, with the fields each feeds. -/
def projectFedBy : List (String × List String) := [("to_additive", ["x"]), ("to_dual", ["x"])]

/-- `x`, as translation ↦ source, and how many `to_dual` pairs the guard rejects. #99's
`translationSources`, unchanged. -/
def translationSources (env : Environment) : NameMap Name × Nat := Id.run do
  let mut back : NameMap Name := {}
  let mut conflicts := 0
  for t in [Mathlib.Tactic.ToAdditive.data, Mathlib.Tactic.ToDual.data] do
    let mut seen : NameMap Name := {}
    for i in [:env.header.moduleNames.size] do
      for (src, info) in t.translations.getModuleEntries env i do
        let tgt := info.translation
        if seen.find? src == some tgt || seen.contains tgt then continue
        seen := seen.insert tgt src
        if t.isDual && src != tgt && !src.isInternalDetail && !tgt.isInternalDetail &&
            generatedByRange? env src == some true && generatedByRange? env tgt == some false then
          conflicts := conflicts + 1
    for (tgt, src) in seen do
      unless back.contains tgt do
        back := back.insert tgt src
  return (back, conflicts)

/-- What `alias` registered `n` as an alias of, and in which direction for an `Iff`. -/
def aliasJson? (env : Environment) (n : Name) : Option Json :=
  (Batteries.Tactic.Alias.aliasExt.find? env n).map fun
    | .plain t => Json.mkObj [("k", "plain"), ("of", toJson t.toString)]
    | .forward t => Json.mkObj [("k", "forward"), ("of", toJson t.toString)]
    | .reverse t => Json.mkObj [("k", "reverse"), ("of", toJson t.toString)]

/-- Whether `doc` is the docstring `alias` gives an alias written without one. -/
def isAliasDocstring (env : Environment) (n : Name) (doc : String) : Bool :=
  match Batteries.Tactic.Alias.aliasExt.find? env n with
  | some info =>
    let gen := info.toString
    doc.trimAscii.copy == gen || doc.startsWith s!"{gen}\n\n---\n\n"
  | none => false

def projectRow (env : Environment) (back : NameMap Name) (d : ReasonableDecl) :
    IO (List (String × Json)) := do
  let n := d.name
  let dg := match ← findDocString? env n with
    | some doc => isAliasDocstring env n doc
    | none => false
  return [("n", toJson d.str)]
    ++ ((back.find? n).map fun s => ("x", toJson s.toString)).toList
    ++ ((aliasJson? env n).map ("al", ·)).toList
    ++ (if dg then [("dg", toJson true)] else [])

/-- #99's `writeImportsDump`, unchanged. -/
def writeImportsDump (env : Environment) (roots : Array Name) (outFile : System.FilePath) :
    IO Nat := do
  let fullGraph := env.importGraph
  let restricted : NameMap (Array Name) :=
    fullGraph.foldl (init := ({} : NameMap (Array Name))) fun acc src deps =>
      if roots.any (·.isPrefixOf src) then acc.insert src deps
      else acc
  let closure := restricted.transitiveClosure
  let pairs : Array (String × Json) :=
    closure.foldl (init := #[]) fun acc n s =>
      if roots.any (fun p => p != n && p.isPrefixOf n) then
        acc.push (n.toString, Json.num s.size)
      else acc
  let sorted := pairs.qsort (fun a b => a.1 < b.1)
  IO.FS.writeFile outFile (Json.mkObj sorted.toList).compress
  return sorted.size

unsafe def run (dir : System.FilePath) (roots : Array Name) : IO UInt32 := do
  Lean.initSearchPath (← Lean.findSysroot)
  Lean.enableInitializersExecution
  IO.FS.createDirAll dir
  Lean.withImportModules (roots.map ({ module := · })) {} (trustLevel := 1024) fun env => do
    let decls := reasonableDecls env roots
    let translations (t : Mathlib.Tactic.Translate.TranslateData) :
        PersistentEnvExtension _ _ (List _ × Thunk (NameMap _)) := t.translations
    let (env, additive) ← loadExt (translations Mathlib.Tactic.ToAdditive.data) env
    let (env, dual) ← loadExt (translations Mathlib.Tactic.ToDual.data) env
    let (back, conflicts) := translationSources env
    let mut dropped ← gate [("to_additive", additive), ("to_dual", dual)] projectFedBy
    if conflicts > 0 then
      IO.eprintln s!"warning: to_dual's entry order disagrees with the declaration ranges on \
        {conflicts} pairs; leaving out x"
      dropped := dropped ++ ["x"]
    let rows ← decls.mapM (projectRow env back)
    let n ← writeDumpFile (dir / "api.mathlib.jsonl") projectFields dropped rows (sparse := true)
    IO.println s!"Wrote the project fields of {n} of {decls.size} declarations"
    let ni ← writeImportsDump env roots (dir / "imports.json")
    IO.println s!"Wrote {ni} transitive-import counts"
    return 0

unsafe def main : List String → IO UInt32
  | "--out-dir" :: dir :: roots@(_ :: _) => run dir (roots.toArray.map (·.toName))
  | _ => do
    IO.eprintln "usage: ProjectProbe.lean --out-dir DIR ROOT [ROOT ...]"
    return 2
