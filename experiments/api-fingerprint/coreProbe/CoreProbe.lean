/-
Copyright (c) 2026 Damiano Testa. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Damiano Testa
-/
import Lean

/-!
# PROBE: the API dump's Lean-core fields, compiled

`ARCHITECTURE.md`, *The probe that decides the Lean layout*. This is #99's dumper
(`scripts/pr_summary/dumpReasonableDecls.lean` at `6d4d41a`) with everything that needs more
than `import Lean` taken out, so that it builds into an executable, as `apiHasher` does. The
definitions are copied unchanged, except:

* `d` hashes every docstring. Whether a docstring is the one `alias` generates needs Batteries,
  so the project script writes that as `dg` (`ProjectProbe.lean`).
* `x` and `al`, and their reads of `to_additive`, `to_dual` and `alias`, are left to the project
  script, and so is `imports.json`.
* The file writer and the empty-extension gate are pulled out as `writeDumpFile` and `gate`, so
  that the project script can use them too (probe (b)).

It asks two things:

* (a) Does the executable read the Lean-core extensions' entries as `lean --run` does? Its rows
  should equal #99's on its fields.
* (b) Can a `lean --run` script import this module beside Mathlib's? `ProjectProbe.lean` does.
-/

open Lean

namespace CoreProbe

/-! ## Which declarations: the framework -/

/-- A constant is "reasonable" iff it is not `private`, not `unsafe`, and not
flagged as an internal detail (`Lean.Name.isInternalDetail`). -/
def isReasonable (n : Name) (ci : ConstantInfo) : Bool :=
  !n.isInternalDetail && !isPrivateName n && !ci.isUnsafe

/-- The `Name` of the module in which `n` is defined, or `none` if `n` has no
source module (e.g. it was synthesised in the current "input" module). -/
def sourceModule? (env : Environment) (n : Name) : Option Name := do
  let idx ← env.getModuleIdxFor? n
  env.allImportedModuleNames[idx.toNat]?

/-- True iff `m` is `prefix.<...>` (or `prefix` itself) for some `prefix` in `roots`.
An empty `roots` accepts everything. -/
def underAnyRoot (roots : Array Name) (m : Name) : Bool :=
  roots.isEmpty || roots.any (·.isPrefixOf m)

/-- True iff `n`'s source module is `prefix.<...>` for some `prefix` in `roots`.
An empty `roots` accepts everything. -/
def inAnyRoot (env : Environment) (roots : Array Name) (n : Name) : Bool :=
  if roots.isEmpty then true
  else match sourceModule? env n with
    | none   => false
    | some m => underAnyRoot roots m

/-- A reasonable declaration under the roots. This includes a rendering of the name as a string
for efficiency. -/
structure ReasonableDecl where
  str  : String
  name : Name
  info : ConstantInfo

/-- Iterate the constants once and collect the reasonable declarations under the roots,
sorted by name. -/
def reasonableDecls (env : Environment) (roots : Array Name) : Array ReasonableDecl :=
  let decls := env.constants.fold (init := (#[] : Array ReasonableDecl)) fun acc n ci =>
    if isReasonable n ci && inAnyRoot env roots n then
      acc.push { str := n.toString, name := n, info := ci }
    else acc
  decls.qsort (·.str < ·.str)

/-- The modules under the roots, sorted by name. -/
def rootModules (env : Environment) (roots : Array Name) : Array Name :=
  env.allImportedModuleNames.filter (underAnyRoot roots) |>.qsort (·.toString < ·.toString)

/-- The format of every file of the dump: the header, and rows keyed by `n` or `mod`. -/
def apiSchema : Nat := 1

/-- The fields fed by an extension read as empty, with a warning for each (*Extensions read as
empty*). `counts` names each extension with how much was read from it; `fedBy` names the fields
each feeds. -/
def gate (counts : List (String × Nat)) (fedBy : List (String × List String)) :
    IO (List String) := do
  let mut dropped : List String := []
  for (what, n) in counts do
    if n == 0 then
      let fs := (List.lookup what fedBy).getD []
      IO.eprintln s!"warning: {what} read as empty; leaving out {", ".intercalate fs}"
      dropped := dropped ++ fs
  return dropped

/-- Write one file of the dump: the header with the fields not dropped, then the rows with the
dropped fields taken out. With `sparse`, a row left with nothing but its key is not written:
only the core's file has a row for every declaration. -/
def writeDumpFile (outFile : System.FilePath) (fields : List (String × Nat))
    (dropped : List String) (rows : Array (List (String × Json))) (sparse : Bool := false) :
    IO Nat := do
  let fields := fields.filter (!dropped.contains ·.1)
  let header := Json.mkObj [("schema", toJson apiSchema),
    ("fields", Json.mkObj (fields.map fun (k, v) => (k, toJson v)))]
  IO.FS.withFile outFile .write fun h => do
    h.putStrLn header.compress
    let mut written := 0
    for r in rows do
      let r := r.filter (!dropped.contains ·.1)
      if sparse && r.length ≤ 1 then continue
      h.putStrLn (Json.mkObj r).compress
      written := written + 1
    return written

def optJson : Option String → Json
  | some s => toJson s
  | none => .null

/-! ## The core's fields -/

/-- The fields the core writes, with their versions: #99's `apiFields` but `x` and `al`. -/
def coreFields : List (String × Nat) := [
  ("n", 1), ("k", 1), ("m", 1), ("d", 1), ("a", 1), ("p", 1), ("mt", 1), ("g", 1),
  ("dep", 1), ("rs", 1), ("l", 1),
  ("mod", 1), ("dm", 1), ("md", 1), ("imp", 1), ("src", 1)]

/-- The names used in the `k` field. -/
def declKind : ConstantInfo → String
  | .axiomInfo _ => "ax" | .defnInfo _ => "def" | .thmInfo _ => "thm"
  | .opaqueInfo _ => "opaq" | .quotInfo _ => "quot"
  | .inductInfo _ => "ind" | .ctorInfo _ => "ctor" | .recInfo _ => "rec"

/-! ### Attributes (`a`), from module entries -/

/-- Run one extension's import hook over its stored entries and install the result, as the full
import does for every extension. Returns how many entries it read. -/
def loadExt {α β σ : Type} [Inhabited σ] (ext : PersistentEnvExtension α β σ)
    (env : Environment) : IO (Environment × Nat) := do
  let s := ext.toEnvExtension.getState (asyncMode := .sync) env
  let state ← ext.addImportedFn s.importedEntries { env, opts := {} }
  return (ext.toEnvExtension.setState (asyncMode := .sync) (log := .unlogged) env { s with state },
    s.importedEntries.foldl (· + ·.size) 0)

/-- The global entries of a scoped extension, in import order. -/
def globalEntries {α β σ : Type} [Inhabited (ScopedEnvExtension.StateStack α β σ)]
    (env : Environment) (ext : ScopedEnvExtension α β σ) : Array α := Id.run do
  let mut out := #[]
  for i in [:env.header.moduleNames.size] do
    for e in ext.ext.getModuleEntries env i do
      if let .global a := e then out := out.push a
  return out

/-- What `SimpTheorems.isLemma (.decl n)` and `toUnfold.contains n` answer, for one simp set. -/
structure SimpNames where
  fwd        : NameSet := {}
  bwd        : NameSet := {}
  toUnfold   : NameSet := {}
  unfoldThms : NameMap (Array Name) := {}
  deriving Inhabited

/-- `SimpTheorems.eraseCore` on the sets above, including its walk over equation lemmas. -/
partial def SimpNames.erase (s : SimpNames) (n : Name) (inv : Bool) : SimpNames :=
  let s := if inv then { s with bwd := s.bwd.erase n } else { s with fwd := s.fwd.erase n }
  let s := { s with toUnfold := s.toUnfold.erase n }
  match s.unfoldThms.find? n with
  | some thms => thms.foldl (fun s t => s.erase t false) s
  | none => s

/-- `SimpTheorems.addSimpEntry` on the sets above. Adding `← n` drops a forward `n`, and the
other way round. -/
def SimpNames.add (s : SimpNames) : Meta.SimpEntry → SimpNames
  | .thm { origin := .decl n _ inv, .. } =>
    let s := if (if inv then s.fwd else s.bwd).contains n then s.erase n !inv else s
    if inv then { s with bwd := s.bwd.insert n } else { s with fwd := s.fwd.insert n }
  | .thm _ => s
  | .toUnfold n => { s with toUnfold := s.toUnfold.insert n }
  | .toUnfoldThms n thms => { s with unfoldThms := s.unfoldThms.insert n thms }

def simpNames (env : Environment) (ext : Meta.SimpExtension) : SimpNames :=
  (globalEntries env ext).foldl SimpNames.add {}

/-- The attribute states the dump reads, as name sets. -/
structure EntryIndex where
  simp     : SimpNames
  simpSets : Array (String × SimpNames)
  /-- `addInstanceEntry` keeps the last entry for a name. -/
  instPrio : NameMap Nat
  ext      : NameSet

def EntryIndex.build (env : Environment) : EntryIndex :=
  let nc := Meta.NormCast.normCastExt
  { simp := simpNames env Meta.simpExtension
    simpSets := #[("norm_cast", simpNames env nc.up), ("norm_cast", simpNames env nc.down),
      ("norm_cast", simpNames env nc.squash),
      ("push_cast", simpNames env Meta.NormCast.pushCastExt)]
    instPrio := (globalEntries env Meta.instanceExtension).foldl (init := {}) fun m e =>
      match e.globalName? with
      | some n => m.insert n e.priority
      | none => m
    ext := (globalEntries env Meta.Ext.extExtension).foldl (·.insert ·.declName) {} }

/-- `n`'s attributes, sorted, from an `EntryIndex` and an environment whose plain-map states
have been loaded (`loadExt`). -/
def entryAttrs (env : Environment) (ix : EntryIndex) (n : Name) : Array String := Id.run do
  let mut out : Array String := #[]
  if ix.simp.fwd.contains n then out := out.push "simp"
  for (tag, s) in ix.simpSets do
    if s.fwd.contains n && !out.contains tag then out := out.push tag
  if ix.simp.toUnfold.contains n then out := out.push "simp-unfold"
  if let some prio := ix.instPrio.find? n then out := out.push s!"instance:{prio}"
  match getReducibilityStatusCore env n with
  | .semireducible => pure ()
  | s => out := out.push s!"reducibility:{s.toAttrString}"
  if ix.ext.contains n then out := out.push "ext"
  if defeqAttr.hasTag env n then out := out.push "defeq"
  return out.qsort (· < ·)

/-! ### Provenance (`g`) and deprecations -/

/-- The inductive type `n` is a constructor or structure field of. -/
def ownerType? (env : Environment) (n : Name) : Option Name :=
  match env.find? n with
  | some (.ctorInfo c) => some c.induct
  | _ => do
    let p ← env.getProjectionFnInfo? n
    let .ctorInfo c ← env.find? p.ctorName | none
    return c.induct

/-- Whether `n` looks made by an attribute, from its declaration range, or `none` if it has no
range. The project script needs this too, for `to_dual`'s order guard. -/
def generatedByRange? (env : Environment) (n : Name) : Option Bool :=
  (declRangeExt.find? env ((ownerType? env n).getD n)).map fun r =>
    r.range.pos == r.selectionRange.pos && r.range.endPos == r.selectionRange.endPos

/-- `g`: `generatedByRange?`, false without a range. -/
def generatedHeuristic (env : Environment) (n : Name) : Bool :=
  (generatedByRange? env n).getD false

/-- The `@[deprecated]` entry: the replacement, the `since` date and the message. -/
def deprecationJson? (env : Environment) (n : Name) : Option Json :=
  (Linter.deprecatedAttr.getParam? env n).map fun d =>
    Json.mkObj [("to", optJson (d.newName?.map toString)), ("since", optJson d.since?),
      ("text", optJson d.text?)]

/-! ### Rows -/

/-- The extensions the core reads, with the fields each feeds (*Extensions read as empty*). -/
def coreFedBy : List (String × List String) :=
  [("simp", ["a"]), ("norm_cast", ["a"]), ("push_cast", ["a"]), ("instances", ["a"]),
   ("ext", ["a"]), ("matchers", ["rs"]), ("declaration ranges", ["g", "l"])]

/-- One declaration's row, as key-value pairs, leaving out empty values. `d` hashes every
docstring; the project script's `dg` says which are `alias`'s. -/
def declRow (env : Environment) (ix : EntryIndex) (d : ReasonableDecl)
    (line : Option Nat) : IO (List (String × Json)) := do
  let n := d.name
  let ci := d.info
  let str (h : UInt64) := toJson (toString h)
  let docHash := (← findDocString? env n).map (hash ·.trimAscii.copy)
  let attrs := entryAttrs env ix n
  let opt (k : String) : Option Json → List (String × Json)
    | some j => [(k, j)]
    | none => []
  let flag (k : String) (b : Bool) : List (String × Json) := if b then [(k, toJson true)] else []
  return [("n", toJson d.str), ("k", toJson (declKind ci)),
      ("m", toJson ((sourceModule? env n).getD .anonymous).toString)]
    ++ opt "d" (docHash.map str)
    ++ (if attrs.isEmpty then [] else [("a", toJson attrs)])
    ++ flag "p" (isProtected env n)
    ++ flag "mt" (isMarkedMeta env n)
    ++ flag "g" (generatedHeuristic env n)
    ++ opt "dep" (deprecationJson? env n)
    ++ flag "rs" (isReservedName env n)
    ++ opt "l" (line.map toJson)

/-- The hash of a module's source file, found through `LEAN_SRC_PATH`, or `none`. -/
def sourceHash? (sp : SearchPath) (mod : Name) : IO (Option UInt64) := do
  let some p ← sp.findModuleWithExt "lean" mod | return none
  return some (hash (← IO.FS.readBinFile p))

/-- One module's row, as key-value pairs, leaving out empty values. -/
def moduleRow (env : Environment) (sp : SearchPath) (mod : Name) : IO (List (String × Json)) := do
  let some idx := env.getModuleIdx? mod | return [("mod", toJson mod.toString)]
  let dm? := env.getDeprecatedModuleByIdx? idx
  let docs := (getModuleDoc? env mod).getD #[]
  let imps := match dm?, env.header.moduleData[idx.toNat]? with
    | some _, some d => d.imports.map (toJson ·.module.toString)
    | _, _ => #[]
  let src ← sourceHash? sp mod
  return [("mod", toJson mod.toString)]
    ++ (dm?.map fun e => ("dm", Json.mkObj [("since", optJson e.since?),
          ("text", optJson e.message?)])).toList
    ++ (if docs.isEmpty then []
        else [("md", toJson (toString (hash (docs.map (·.doc.trimAscii.copy)).toList)))])
    ++ (if imps.isEmpty then [] else [("imp", Json.arr imps)])
    ++ (src.map fun h => ("src", toJson (toString h))).toList

/-- Write the core's file: the header, a row per declaration, then a row per module under the
roots. Returns the number of declaration rows and of module rows. -/
def writeCoreDump (env : Environment) (roots : Array Name)
    (decls : Array ReasonableDecl) (outFile : System.FilePath) : IO (Nat × Nat) := do
  let (env, matchers) ← loadExt Meta.Match.Extension.extension env
  let (env, _) ← loadExt reducibilityExtraExt.ext env
  let ix := EntryIndex.build env
  let walk : CoreM (Array (List (String × Json)) × Nat) := do
    let mut rows := #[]
    let mut ranged := 0
    for d in decls do
      let line := (← findDeclarationRanges? d.name).map (·.selectionRange.pos.line)
      if line.isSome then ranged := ranged + 1
      rows := rows.push (← declRow env ix d line)
    return (rows, ranged)
  let ((rows, ranged), _) ← walk.toIO { fileName := "<coreProbe>", fileMap := default } { env }
  let counts := [("simp", ix.simp.fwd.size + ix.simp.bwd.size),
    ("norm_cast", (ix.simpSets.filter (·.1 == "norm_cast")).foldl (· + ·.2.fwd.size) 0),
    ("push_cast", (ix.simpSets.filter (·.1 == "push_cast")).foldl (· + ·.2.fwd.size) 0),
    ("instances", ix.instPrio.size), ("ext", ix.ext.size), ("matchers", matchers),
    ("declaration ranges", ranged)]
  let dropped ← gate counts coreFedBy
  let mods := rootModules env roots
  let sp ← getSrcSearchPath
  let modRows ← mods.mapM (moduleRow env sp)
  let _ ← writeDumpFile outFile coreFields dropped (rows ++ modRows)
  return (decls.size, mods.size)

end CoreProbe
