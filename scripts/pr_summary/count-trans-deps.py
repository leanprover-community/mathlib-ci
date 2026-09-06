#!/usr/bin/env python3

# Reports the number of transitive import dependencies
# of each file in a directory of a Lean project.
# The directory name is provided as a command line argument.
# The number of transitive import dependencies is printed to the console as JSON dictionary.

import os
import re
import json
import sys

# Lean's import grammar is `(public)? (meta)? import (all)? <module>`, and an import may
# be followed by a trailing comment (`-- shake: keep`, ...), so the module name ends at
# the first whitespace or comment opener after the keywords.  The comment need not be
# separated from the module name: `import Mathlib.Bar-- shake: keep` is one token.
IMPORT_RE = re.compile(r'^(?:public\s+)?(?:meta\s+)?import(?:\s+all)?\s+(?P<ref>\S+?)(?=\s|--|/-|$)')

def get_imports(directory):
    # Initialize an empty dictionary
    file_imports = {}

    # Iterate over all Lean files in the given directory
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith('.lean'):
                # Full path to the file
                file_path = os.path.join(root, file)

                # Normalize the filename
                module_name = file_path.replace('/', '.').replace('.lean', '')

                # Open the file and read it line by line
                imports = []
                with open(file_path, 'r') as f:
                    for line in f:
                        # Stop reading the file if the line contains `/-!`
                        if '/-!' in line:
                            break
                        # Find an import statement
                        match = IMPORT_RE.match(line)
                        if match:
                            imports.append(match.groupdict()['ref'])

                # Add the file and its imports to the dictionary
                file_imports[module_name] = imports

    return file_imports

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
    counts = count_transitive_imports(get_transitive_imports(get_imports(directory)))

    # Print the counts in JSON format
    print(json.dumps(counts))

    return 0


if __name__ == '__main__':
    sys.exit(main())
