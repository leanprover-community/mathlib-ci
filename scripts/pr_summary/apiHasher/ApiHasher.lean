import Lean

/-!
# The API dump's structural hashes

Writes the fields `t`, `te`, `v`, `nu` and `uo` of the API dump
(`../dumpReasonableDecls.lean`, *The API dump*) for every reasonable declaration under the
roots, one JSON object per line: a header, `{"fields":{"nu":1,"t":1,"te":1,"uo":1,"v":1}}`,
with each field's version, then a row for each declaration, keyed by `n` and sorted by name,
leaving out a field whose value is the empty one. `dumpReasonableDecls.lean --hashes FILE`
copies them into its rows.

## Why an executable

The dumper runs under `lean --run`, interpreted, and the hasher interpreted takes 3 to 5
minutes on all of `Mathlib` on a hosted runner. Compiled it takes 10 to 17 s. The dumper as a
whole cannot be compiled cheaply, since it imports Mathlib modules whose native code would
have to be built first, but the hasher needs only `import Lean`. So it is a Lake package of its
own, with no dependencies:

```
lake --dir path/to/apiHasher build                       # from the mathlib checkout
lake env path/to/apiHasher/.lake/build/bin/apihasher --out FILE ROOT [ROOT ...]
```

Both run from the mathlib checkout: the first so that the checkout's `lean-toolchain` picks
the compiler, the second so that `lake env` gives the search path of its oleans.

## Hashing

`t` and `v` come from a structural hasher that erases what does not change a declaration's
meaning to its callers: binder names, `mdata`, universe parameter names, how a level is
written, the order of the arguments in a nest of `max`es, and proofs. It is not
rename-invariant: each declaration is compared with itself, so the constants it mentions count
by name.

* Universe parameters are numbered in **canonical order**: by first appearance in the type,
  then, for those it does not mention, as declared. `Type*` does not keep the declared order,
  which is what `foo.{a, b}` binds, so that is recorded on its own, as `uo`. The universe
  arguments of a constant a term mentions are hashed in that constant's canonical order.
* A **proof** is an application headed by a theorem, or a `fun` whose body is one, and hashes
  as one constant. Lean lifts a proof inside a definition or a statement into a theorem
  `foo._proof_n`, numbered by position, so dropping one renumbers the rest. Terms that differ
  only in proofs are definitionally equal.

`te` is `t` with universe levels left out altogether: sorts hash alike, and so do a constant's
instances at any universes.
-/

open Lean

/-- The fields this writes, with their versions. A field's version is bumped whenever its
values stop being comparable with the old ones (`../dumpReasonableDecls.lean`, *The API
dump*). -/
def hashFields : List (String × Nat) := [("t", 1), ("te", 1), ("v", 1), ("nu", 1), ("uo", 1)]

/-! ## Which declarations

Copies of the dumper's definitions: the two must list the same names in the same order, and the
dumper stops if they do not. -/

/-- `isReasonable` in `dumpReasonableDecls.lean`. -/
def isReasonable (n : Name) (ci : ConstantInfo) : Bool :=
  !n.isInternalDetail && !isPrivateName n && !ci.isUnsafe

/-- `inAnyRoot` in `dumpReasonableDecls.lean`. -/
def inAnyRoot (env : Environment) (roots : Array Name) (n : Name) : Bool :=
  roots.isEmpty || match env.getModuleIdxFor? n with
    | none => false
    | some idx => match env.allImportedModuleNames[idx.toNat]? with
      | some m => roots.any (·.isPrefixOf m)
      | none => false

/-- `reasonableDecls` in `dumpReasonableDecls.lean`. -/
def reasonableDecls (env : Environment) (roots : Array Name) : Array (String × ConstantInfo) :=
  let decls := env.constants.fold (init := #[]) fun acc n ci =>
    if isReasonable n ci && inAnyRoot env roots n then acc.push (n.toString, ci) else acc
  decls.qsort (·.1 < ·.1)

/-! ## Universe parameters in canonical order -/

/-- The universe parameters of `e` in order of first appearance, as `collectLevelParams` finds
them, stopping once it has found `n` of them. Visited subterms are remembered by address:
`collectLevelParams` remembers them by structure, which took 110 s on `Mathlib`. -/
unsafe def firstLevelParams (n : Nat) (e : Expr) : Array Name :=
  ((go e).run (#[], {})).run.2.1
where
  done : StateM (Array Name × Std.HashSet USize) Bool := return (← get).1.size ≥ n
  lvl (u : Level) : StateM (Array Name × Std.HashSet USize) Unit := do
    if !u.hasParam || (← done) then return
    match u with
    | .succ v => lvl v
    | .max a b | .imax a b => do lvl a; lvl b
    | .param p => modify fun (ps, vs) => (if ps.contains p then ps else ps.push p, vs)
    | _ => pure ()
  go (e : Expr) : StateM (Array Name × Std.HashSet USize) Unit := do
    if !e.hasLevelParam || (← done) then return
    let k := ptrAddrUnsafe e
    if (← get).2.contains k then return
    modify fun (ps, vs) => (ps, vs.insert k)
    match e with
    | .proj _ _ s => go s
    | .forallE _ d b _ | .lam _ d b _ => do go d; go b
    | .letE _ t v b _ => do go t; go v; go b
    | .app f a => do go f; go a
    | .mdata _ b => go b
    | .const _ us => for u in us do lvl u
    | .sort u => lvl u
    | _ => pure ()

/-- A constant's universe parameters in canonical order: first appearance in its type, then
the rest as declared. -/
unsafe def canonicalLevelParams (ci : ConstantInfo) : List Name :=
  if ci.levelParams.length < 2 then ci.levelParams else
  let seen := (firstLevelParams ci.levelParams.length ci.type).toList
  seen ++ ci.levelParams.filter (!seen.contains ·)

/-- For each constant whose declared and canonical orders differ, the declared position of each
of its universe parameters, in canonical order. Built once: per reference it would walk the
constant's type each time. -/
unsafe def canonicalArgOrders (env : Environment) : NameMap (Array Nat) :=
  env.constants.fold (init := {}) fun m n ci =>
    if ci.levelParams.length < 2 then m else
    let canon := canonicalLevelParams ci
    if canon == ci.levelParams then m else m.insert n (canon.toArray.map ci.levelParams.idxOf)

/-- `us`, the universe arguments of `n` in its declared order, in its canonical order. -/
def canonicalArgs (orders : NameMap (Array Nat)) (n : Name) (us : List Level) : List Level :=
  if us.length < 2 then us else
  match orders.find? n with
  | some o => if o.size == us.length then o.toList.map (us[·]!) else us
  | none => us

/-! ## The hash -/

def hashBI : BinderInfo → UInt64
  | .default => 101 | .implicit => 103 | .strictImplicit => 107 | .instImplicit => 109

/-- The arguments of a nest of `max`es. -/
def maxArgs : Level → Array Level
  | .max a b => maxArgs a ++ maxArgs b
  | l => #[l]

/-- A level, with each parameter by its position in `lp`. -/
partial def hashLvl (lp : List Name) : Level → UInt64
  | .zero => 13
  | .succ l => mixHash 17 (hashLvl lp l)
  -- `max` is associative and commutative, and Lean orders its arguments by universe name when
  -- it normalises a level, so renaming `Type u` to `Type*` can swap them.
  | l@(.max _ _) => ((maxArgs l).map (hashLvl lp)).qsort (· < ·) |>.foldl mixHash 19
  | .imax a b => mixHash 23 (mixHash (hashLvl lp a) (hashLvl lp b))
  | .param n => mixHash 29 (hash ((lp.findIdx? (· == n)).getD 9999))
  | .mvar _ => 31

/-- Is `n` a theorem? Then any application of it is a proof. -/
def isTheorem (env : Environment) (n : Name) : Bool :=
  (env.find? n).any (·.isTheorem)

/-- Is `e` a proof the hasher erases: a theorem, applied or not, or a `fun` whose body is one? -/
def isErasedProof (env : Environment) : Expr → Bool
  | .lam _ _ b _ => isErasedProof env b
  | e => match e.getAppFn with
    | .const c _ => isTheorem env c
    | _ => false

/-- The hash an erased proof is given, whatever the proof. -/
def proofHash : UInt64 := 61

/-- Hashes of the subterms already seen, by address. -/
abbrev Memo := Std.HashMap USize (UInt64 × UInt64)

/-- The structural hash of `e` (*Hashing*), and the same with universe levels left out, which
`te` is cut from. One walk computes both, and a subterm the term shares is hashed once. A term
with no proof in it hashes as it would if proofs were not erased. -/
unsafe def hashBoth (env : Environment) (orders : NameMap (Array Nat)) (lp : List Name)
    (e : Expr) : UInt64 × UInt64 :=
  (go e).run' {} |>.run
where
  /-- A node already hashed: its hash does not depend on where it is, since a node reached by
  `spine` or `lams` hashes as `go` would hash it there. -/
  memo (e : Expr) (k : StateM Memo (UInt64 × UInt64)) : StateM Memo (UInt64 × UInt64) := do
    let a := ptrAddrUnsafe e
    if let some h := (← get).get? a then return h
    let h ← k
    modify (·.insert a h)
    return h
  both (f : UInt64 → UInt64) (h : UInt64 × UInt64) : UInt64 × UInt64 := (f h.1, f h.2)
  go (e : Expr) : StateM Memo (UInt64 × UInt64) := do
    match e with
    | .bvar i => let h := mixHash 2 (hash i); return (h, h)
    | .fvar _ => return (3, 3)
    | .mvar _ => return (5, 5)
    | .sort l => return (mixHash 7 (hashLvl lp l.normalize), 7)
    | .const n us =>
      if isTheorem env n then return (proofHash, proofHash)
      return (mixHash 11 (mixHash (hash n) ((canonicalArgs orders n us).foldl (init := 0)
          fun h u => mixHash h (hashLvl lp u.normalize))), mixHash 11 (mixHash (hash n) 0))
    | .app .. => memo e do
        if isErasedProof env e then return (proofHash, proofHash) else spine e
    | .lam .. => memo e do
        if isErasedProof env e then return (proofHash, proofHash) else lams e
    | .forallE _ d b bi => memo e do
        let (d, b) := (← go d, ← go b)
        return (mixHash 43 (mixHash d.1 (mixHash b.1 (hashBI bi))),
          mixHash 43 (mixHash d.2 (mixHash b.2 (hashBI bi))))
    | .letE _ t v b _ => memo e do
        let (t, v, b) := (← go t, ← go v, ← go b)
        return (mixHash 47 (mixHash t.1 (mixHash v.1 b.1)),
          mixHash 47 (mixHash t.2 (mixHash v.2 b.2)))
    | .lit l => let h := mixHash 53 (hash l); return (h, h)
    | .mdata _ x => go x
    | .proj s i x => memo e do
        return both (fun h => mixHash 59 (mixHash (hash s) (mixHash (hash i) h))) (← go x)
  /-- An application whose head is not a theorem: neither is any prefix of it. -/
  spine (e : Expr) : StateM Memo (UInt64 × UInt64) := do
    match e with
    | .app f a => memo e do
        let (f, a) := (← spine f, ← go a)
        return (mixHash 13 (mixHash f.1 a.1), mixHash 13 (mixHash f.2 a.2))
    | e => go e
  /-- A `fun` whose body is not a proof: nor is that of any `fun` inside it. -/
  lams (e : Expr) : StateM Memo (UInt64 × UInt64) := do
    match e with
    | .lam _ d b bi => memo e do
        let (d, b) := (← go d, ← lams b)
        return (mixHash 41 (mixHash d.1 (mixHash b.1 (hashBI bi))),
          mixHash 41 (mixHash d.2 (mixHash b.2 (hashBI bi))))
    | e => go e

/-! ## Writing -/

/-- One declaration's row, leaving out empty values. Hashes are written as decimal strings. -/
unsafe def hashRow (env : Environment) (orders : NameMap (Array Nat)) (name : String)
    (ci : ConstantInfo) : Json :=
  -- Universe parameters are numbered canonically, not as declared (*Hashing*).
  let lp := canonicalLevelParams ci
  let str (h : UInt64) := toJson (toString h)
  let ty := hashBoth env orders lp ci.type
  let value := if ci.isTheorem then none else ci.value?
  Json.mkObj <| [("n", toJson name), ("t", str ty.1), ("te", toJson (toString ty.2.toUInt32))]
    ++ (value.map fun v => ("v", str (hashBoth env orders lp v).1)).toList
    ++ (if ci.levelParams.isEmpty then [] else [("nu", toJson ci.levelParams.length)])
    ++ (if lp == ci.levelParams then [] else [("uo", Json.mkObj [
        ("d", toJson (ci.levelParams.map toString)), ("p", toJson (ci.levelParams.map lp.idxOf))])])

def usage : String := "usage: apihasher --out FILE ROOT [ROOT ...]"

unsafe def main : List String → IO UInt32
  | "--out" :: out :: roots@(_ :: _) => do
    if roots.any (·.startsWith "-") then
      IO.eprintln usage
      return 2
    let roots := roots.toArray.map (·.toName)
    initSearchPath (← findSysroot)
    withImportModules (roots.map ({ module := · })) {} (trustLevel := 1024) fun env => do
      let decls := reasonableDecls env roots
      let orders := canonicalArgOrders env
      IO.FS.withFile out .write fun h => do
        h.putStrLn (Json.mkObj
          [("fields", Json.mkObj (hashFields.map fun (k, v) => (k, toJson v)))]).compress
        for (name, ci) in decls do
          h.putStrLn (hashRow env orders name ci).compress
      IO.println s!"Wrote the hashes of {decls.size} declarations to {out}"
      return 0
  | _ => do
    IO.eprintln usage
    return 2
