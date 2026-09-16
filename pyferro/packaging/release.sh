#!/usr/bin/env bash
# Release PyFERRO: check, test, tag, push, build the offline bundle.
#
#   1. edit ferro/__init__.py   -> __version__ = "1.1.0"      (the only version)
#   2. add a "## 1.1.0 — <date>" section to CHANGELOG.md
#   3. ./packaging/release.sh [--dry-run] [--publish]
#
# --dry-run   print every step without changing anything
# --publish   also attach the bundle to a GitHub release (needs gh; ~350 MB)
set -euo pipefail
cd "$(dirname "$0")/.."

DRY=0
PUBLISH=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --publish) PUBLISH=1 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

step() { printf '\n==> %s\n' "$*"; }
run()  { if (( DRY )); then printf '    [dry-run] %s\n' "$*"; else "$@"; fi; }
die()  { printf '\nrelease.sh: %s\n' "$*" >&2; exit 1; }

VERSION=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' ferro/__init__.py)
TAG="v${VERSION}"
ZIP="dist/PyFERRO-${VERSION}-win64-offline.zip"

step "Releasing PyFERRO ${VERSION} (tag ${TAG})"
(( DRY )) && echo "    dry run: nothing will be committed, pushed or built"

# --- checks -----------------------------------------------------------------
step "Checking the working tree"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "__version__ is '${VERSION}', expected e.g. 1.1.0"
[[ -z "$(git status --porcelain)" ]] || die "uncommitted changes — commit them first (git status)"
branch=$(git rev-parse --abbrev-ref HEAD)
[[ "$branch" == "main" ]] || die "on branch '${branch}', expected main"
git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null && die "tag ${TAG} already exists"
grep -q "^## ${VERSION}" CHANGELOG.md || die "CHANGELOG.md has no '## ${VERSION}' section"

git fetch -q origin
behind=$(git rev-list --count "HEAD..origin/${branch}")
[[ "$behind" == "0" ]] || die "origin/${branch} has ${behind} commit(s) you do not — pull first"
echo "    clean, on main, up to date, ${TAG} free, changelog entry present"

# --- test -------------------------------------------------------------------
step "Running the test suite"
run pixi run -e test test

# --- build ------------------------------------------------------------------
step "Building the offline Windows bundle"
run ./packaging/build_offline.sh
if (( ! DRY )); then
    [[ -f "$ZIP" ]] || die "expected ${ZIP} but it was not produced"
    echo "    $(du -h "$ZIP" | cut -f1)  ${ZIP}"
fi

# --- tag and push -----------------------------------------------------------
step "Tagging and pushing"
run git tag -a "$TAG" -m "PyFERRO ${VERSION}"
run git push origin "$branch"
run git push origin "$TAG"

# --- optional GitHub release ------------------------------------------------
notes=$(awk -v v="## ${VERSION}" 'index($0, v) == 1 {f = 1; next} /^## / {f = 0} f' CHANGELOG.md)
if (( PUBLISH )); then
    step "Publishing the GitHub release"
    command -v gh >/dev/null || die "gh is not installed"
    run gh release create "$TAG" "$ZIP" --title "PyFERRO ${VERSION}" --notes "$notes"
else
    step "Not publishing to GitHub (pass --publish to attach the bundle)"
    echo "    gh release create ${TAG} ${ZIP} --title \"PyFERRO ${VERSION}\" --notes-file -"
fi

step "Done"
cat <<EOF
    Version   ${VERSION}
    Tag       ${TAG}
    Bundle    ${ZIP}
    Next      copy the zip to the lab PC (see README section 1)
EOF
