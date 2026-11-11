#!/usr/bin/env bash
# Install the system libraries Qt needs on an Ubuntu CI runner.
#
# The runners' connection to Ubuntu's package mirrors is sometimes so slow
# that a few hundred kilobytes take minutes, or apt waits with no output
# until the job is killed.  So the packages (12 small .deb files) are kept in
# QT_DEBS (a folder the workflow caches between runs) and installed from
# there; the mirrors are only needed when that folder is empty or out of date.
set -uo pipefail

cache="${QT_DEBS:-$HOME/.cache/qt-debs}"
packages=(libegl1 libfontconfig1 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4
          libxcb-keysyms1 libxcb-shape0 libxcb-xinerama0)

installed() {        # every one of the packages, not just most of them
    local ok
    ok=$(dpkg-query -W -f='${Status}\n' "${packages[@]}" 2>/dev/null \
         | grep -c "install ok installed")
    [ "$ok" -eq "${#packages[@]}" ]
}

if installed; then
    echo "The Qt libraries are already installed."
    exit 0
fi

# 1. From the cache: no network at all.
if compgen -G "$cache/*.deb" > /dev/null; then
    echo "Installing the Qt libraries from the cache ($cache)."
    sudo dpkg -i --skip-same-version "$cache"/*.deb > /dev/null 2>&1
    if installed; then
        exit 0
    fi
    echo "The cached packages no longer fit this runner; downloading them again." >&2
    rm -rf "$cache"
fi

# 2. From a package mirror, keeping the downloaded files for the cache.  No one
#    mirror is reliable from the runners (the default one stalls on some days,
#    archive.ubuntu.com on others, and a stalled download waits 20 s per
#    package and retry: longer than the job's time limit).  So each mirror in
#    turn gets 90 seconds and one retry, over IPv4, before the next is tried
#    (the whole step stays well inside its 15 minutes).
apt=(sudo timeout 90 apt-get -o Acquire::ForceIPv4=true -o Acquire::Retries=1
     -o Acquire::http::Timeout=15 -o APT::Keep-Downloaded-Packages=true)
sudo rm -f /etc/apt/apt.conf.d/docker-clean        # would delete the downloaded files
mirrors=/etc/apt/apt-mirrors.txt
[ -f "$mirrors" ] && sudo cp "$mirrors" "$mirrors.runner"
for mirror in default http://archive.ubuntu.com/ubuntu/ http://mirrors.edge.kernel.org/ubuntu/ \
              http://azure.archive.ubuntu.com/ubuntu/; do
    if [ "$mirror" != default ]; then
        [ -f "$mirrors" ] || break                 # a runner without the mirror list: one try only
        printf '%s\tpriority:1\n' "$mirror" | sudo tee "$mirrors" > /dev/null
    fi
    echo "Downloading the Qt libraries (mirror: $mirror)."
    "${apt[@]}" install -y --no-install-recommends "${packages[@]}"
    if installed; then
        break
    fi
    echo "That mirror did not deliver; trying the next one." >&2
done
[ -f "$mirrors.runner" ] && sudo mv "$mirrors.runner" "$mirrors"
if ! installed; then
    # The runner's package lists are usually recent enough, so they were not
    # updated above (that alone downloads tens of megabytes); now they are.
    echo "Updating the package lists and trying once more."
    sudo timeout 180 apt-get -o Acquire::ForceIPv4=true update \
        && "${apt[@]}" install -y --no-install-recommends "${packages[@]}"
fi
if ! installed; then
    echo "Could not install the Qt libraries." >&2
    exit 1
fi
mkdir -p "$cache"
cp /var/cache/apt/archives/*.deb "$cache"/ 2>/dev/null || true
echo "Kept $(ls "$cache" | wc -l) packages in the cache for the next run."
