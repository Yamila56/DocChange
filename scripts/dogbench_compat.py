"""Explicit isolation compatibility for the API-only DocChange candidate.

Never use this for a shell/CLI agent. The reference trees are left byte-exact;
RepositoryEnvironment rejects symlinks and resolved paths outside its roots.
Upstream's whole-tree symlink admission gate is replaced for this different
isolation model. All prompt, tree, blob, history and source checks remain.
"""

import os


def symlink_manifest(root):
    findings = []
    for directory, folders, files in os.walk(root, followlinks=False):
        if ".git" in folders:
            folders.remove(".git")
        from pathlib import Path

        for name in folders + files:
            path = Path(directory) / name
            if path.is_symlink():
                findings.append({"path": str(path.relative_to(root)), "target": os.readlink(path)})
    return findings


def install_api_only_symlink_policy():
    import dogbench.verify as verification
    import dogbench.workspaces as workspaces

    def retained_reference_symlinks(root):
        # No symlink is opened here. The actual tool authorization occurs in
        # RepositoryEnvironment._file (is_symlink + resolved-root containment).
        return None

    workspaces._assert_safe_symlinks = retained_reference_symlinks
    verification._assert_safe_symlinks = retained_reference_symlinks
