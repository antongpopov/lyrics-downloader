#!/bin/zsh
# Builds "Lyrics Downloader.app" for one Mac architecture, signs it with a Developer ID, notarizes
# and staples it, and leaves dist/LyricsDownloader-<version>-mac-<arch>.zip.
#
#   scripts/build_mac.sh arm64          # Apple silicon
#   scripts/build_mac.sh x86_64         # Intel (built under Rosetta)
#   scripts/build_mac.sh arm64 --upload # also attach the zip to the GitHub release v<version>
#
# Needs: uv (https://docs.astral.sh/uv/), a "Developer ID Application" certificate in the keychain,
# and an App Store Connect API key for notarization in ASC_KEY_PATH / ASC_KEY_ID / ASC_ISSUER_ID.
# Without the certificate and key it still builds, unsigned (SIGN=0 forces that).
set -euo pipefail
ARCH=${1:?usage: build_mac.sh arm64|x86_64 [--upload]}
UPLOAD=${2:-}
cd "${0:A:h}/.."
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' lyrics_downloader.py)
APP="Lyrics Downloader"
OUT=dist; WORK=build/mac-$ARCH
RUN=(); [[ $ARCH == x86_64 ]] && RUN=(arch -x86_64)

echo "== Python ($ARCH)"
uv python install -q cpython-3.12-macos-$ARCH
rm -rf $WORK && mkdir -p $WORK $OUT
uv venv -q -p cpython-3.12-macos-$ARCH $WORK/venv
uv pip install -q -p $WORK/venv/bin/python tinytag truststore pyinstaller

echo "== PyInstaller"
$RUN $WORK/venv/bin/pyinstaller --noconfirm --log-level WARN --windowed --name "$APP" \
  --icon "$PWD/assets/icon.icns" --osx-bundle-identifier de.apopov.lyrics-downloader \
  --distpath $WORK/dist --workpath $WORK/work --specpath $WORK lyrics_downloader_gui.py
BUNDLE="$WORK/dist/$APP.app"
plutil -replace CFBundleShortVersionString -string "$VERSION" "$BUNDLE/Contents/Info.plist"
plutil -replace CFBundleVersion -string "$VERSION" "$BUNDLE/Contents/Info.plist"
plutil -replace NSHumanReadableCopyright -string "MIT licence · github.com/antongpopov/lyrics-downloader" "$BUNDLE/Contents/Info.plist"
echo "   $(lipo -archs "$BUNDLE/Contents/MacOS/$APP"), $(du -sh "$BUNDLE" | cut -f1)"

ZIP="$OUT/LyricsDownloader-$VERSION-mac-$ARCH.zip"
# The download: a folder with the app and `lyrics-downloader`, the text version (it runs the
# app's own program with arguments, so it needs no Python).
package() {
  local P="$WORK/package/Lyrics Downloader"
  rm -rf "$WORK/package" && mkdir -p "$P"
  ditto "$BUNDLE" "$P/$APP.app"
  cp scripts/lyrics-downloader-mac "$P/lyrics-downloader"
  chmod +x "$P/lyrics-downloader"
  rm -f "$ZIP"
  ditto -c -k --norsrc --keepParent "$P" "$ZIP"   # no ._ metadata files
}
IDENTITY=$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application:[^"]*\)".*/\1/p' | head -1)
if [[ ${SIGN:-1} == 0 || -z $IDENTITY ]]; then
  echo "== no Developer ID Application certificate: leaving the app UNSIGNED"
  package
else
  echo "== Signing as $IDENTITY"
  # Inside out: every binary first, then the bundle, all with the hardened runtime.
  # Each file is retried (the timestamp server hiccups now and then) and a file that still fails
  # stops the build: a silently skipped one only shows up later as a notarization rejection.
  sign() { codesign --force --timestamp --options runtime --entitlements assets/entitlements.plist -s "$IDENTITY" "$1"; }
  find "$BUNDLE/Contents" -type f \( -name "*.so" -o -name "*.dylib" -o -perm -u+x \) -print0 |
    while IFS= read -r -d '' f; do
      file -b "$f" | grep -q Mach-O || continue
      sign "$f" 2>/dev/null || { sleep 3; sign "$f" 2>/dev/null; } || { sleep 10; sign "$f"; }
    done
  codesign --force --timestamp --options runtime --entitlements assets/entitlements.plist -s "$IDENTITY" "$BUNDLE"
  codesign --verify --deep --strict "$BUNDLE"
  ditto -c -k --keepParent "$BUNDLE" "$ZIP"   # notarization submission: the app alone
  echo "== Notarizing (a few minutes)"
  xcrun notarytool submit "$ZIP" --key "${ASC_KEY_PATH:?}" --key-id "${ASC_KEY_ID:?}" --issuer "${ASC_ISSUER_ID:?}" --wait
  xcrun stapler staple "$BUNDLE"
  spctl --assess --type execute -v "$BUNDLE"
  rm "$ZIP" && package
fi
echo "== $ZIP"

if [[ $UPLOAD == --upload ]]; then
  gh release view "v$VERSION" >/dev/null 2>&1 || gh release create "v$VERSION" --title "v$VERSION" --notes "Lyrics Downloader $VERSION"
  gh release upload "v$VERSION" "$ZIP" --clobber
fi
