#!/usr/bin/env python3

# Reports, for each file in a directory of a Lean project, the number of modules that Lean
# loads to elaborate the file.
# The directory name is provided as a command line argument.
# The counts are printed to the console as a JSON dictionary.
#
# A file without a `module` header loads its whole transitive import closure.
# A `module` file loads its direct imports and, from each of them, the closure of their
# `public` imports.  An `import all X` also loads the direct imports of `X`, by the same rule.
# The `meta` keyword does not change which modules are loaded.

import os
import re
import json
import sys

# Lean's import grammar is `(public)? (meta)? import (all)? <module>`, and an import may
# be followed by a trailing comment (`-- shake: keep`, ...), so the module name ends at
# the first whitespace or comment opener after the keywords.  The comment need not be
# separated from the module name: `import Mathlib.Bar-- shake: keep` is one token.
IMPORT_RE = re.compile(
    r'^(?P<public>public\s+)?(?:meta\s+)?import(?P<all>\s+all)?\s+(?P<ref>\S+?)(?=\s|--|/-|$)')

# The `module` keyword starts the header of a file that uses the module system.
MODULE_RE = re.compile(r'^module(?=\s|--|/-|$)')

def get_import_specs(directory):
    """Map each module to `(is_module, imports)`.

    `is_module` tells whether the file has a `module` header.  `imports` lists one
    `(ref, is_public, is_all)` triple per import line, in file order.
    """
    file_specs = {}

    # Iterate over all Lean files in the given directory
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.lean'):
                # Full path to the file
                file_path = os.path.join(root, file)

                # Normalize the filename
                module_name = file_path.replace('/', '.').replace('.lean', '')

                # Open the file and read it line by line
                is_module = False
                imports = []
                with open(file_path, 'r') as f:
                    for line in f:
                        # Stop reading the file if the line contains `/-!`
                        if '/-!' in line:
                            break
                        if MODULE_RE.match(line):
                            is_module = True
                            continue
                        # Find an import statement
                        match = IMPORT_RE.match(line)
                        if match:
                            imports.append((match.group('ref'), match.group('public') is not None,
                                            match.group('all') is not None))

                file_specs[module_name] = (is_module, imports)

    return file_specs

def get_imports(directory):
    """Map each module to the list of modules it imports, in file order."""
    return {module: [ref for ref, _, _ in imports]
            for module, (_, imports) in get_import_specs(directory).items()}

def get_transitive_imports(file_imports):
    # Initialize a dictionary to store the transitive imports
    transitive_imports = {}
    # Initialize a set to store the visited files
    visited = set()

    def dfs(file):
        if file not in visited:
            visited.add(file)
            if file in file_imports:
                transitive_imports[file] = set(file_imports[file])
                for import_module in file_imports[file]:
                    if import_module in file_imports:
                        transitive_imports[file].update(dfs(import_module))
            else:
                transitive_imports[file] = set()
        return transitive_imports[file]

    # Compute the transitive imports for each file
    for file in file_imports:
        dfs(file)

    return transitive_imports

def get_loaded_imports(file_specs):
    """Map each module to the set of modules that Lean loads to elaborate it.

    Modules outside the walked tree are boundary leaves, as in `get_transitive_imports`.
    """
    all_imports = {module: [ref for ref, _, _ in imports]
                   for module, (_, imports) in file_specs.items()}
    transitive = get_transitive_imports(all_imports)

    # `public_closure[m]` holds the modules that `m` re-exports: the closure of its
    # `public` imports.  A file without a `module` header re-exports all its imports.
    public_closure = {}

    def public_dfs(module):
        if module not in public_closure:
            public_closure[module] = set()
            result = set()
            is_module, imports = file_specs.get(module, (False, []))
            for ref, is_public, _ in imports:
                if is_public or not is_module:
                    result.add(ref)
                    result |= public_dfs(ref)
            public_closure[module] = result
        return public_closure[module]

    # `loaded[m]` holds what `m` loads when Lean elaborates it, or when another module
    # reaches it through `import all`.
    loaded = {}

    def loaded_dfs(module):
        if module not in loaded:
            loaded[module] = set()
            is_module, imports = file_specs.get(module, (False, []))
            if not is_module:
                result = set(transitive.get(module, ()))
            else:
                result = set()
                for ref, _, is_all in imports:
                    result.add(ref)
                    result |= loaded_dfs(ref) if is_all else public_dfs(ref)
            loaded[module] = result
        return loaded[module]

    return {module: loaded_dfs(module) for module in file_specs}

def count_transitive_imports(transitive_imports):
    # Initialize a dictionary to store the counts
    count_imports = {}

    # Iterate over the dictionary of transitive imports
    for file, imports in transitive_imports.items():
        # The count is the size of the set of transitive imports
        count_imports[file] = len(imports)

    return count_imports

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv

    # Check if the directory name is provided as a command line argument
    if not argv:
        print("Please provide the directory name as a command line argument.")
        return 1

    # Get the directory name from the command line argument
    directory = argv[0]

    # Compute the counts
    counts = count_transitive_imports(get_loaded_imports(get_import_specs(directory)))

    # Print the counts in JSON format
    print(json.dumps(counts))

    return 0


if __name__ == '__main__':
    sys.exit(main())
