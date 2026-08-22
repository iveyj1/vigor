#!/usr/bin/env bash
set -euo pipefail
set -E

# Port of install-mint-apps for OpenMandriva ROME/Rolling x86_64.
#
# User choices:
#   - Target: OpenMandriva Rolling / ROME.
#   - Architecture: x86_64 only.
#   - Allow non-OpenMandriva upstream binaries.
#   - Run a system distro-sync before installing apps.
#
# Main differences from Mint/apt version:
#   - APT repositories are not reused.
#   - .deb packages are not installed.
#   - Package names are installed through candidate lists because OpenMandriva
#     names differ from Debian/Fedora in some cases.
#   - tldr is installed as tealdeer where practical, avoiding pipx/ensurepip.
#
# Usage:
#   ./install-openmandriva-apps
#   ./install-openmandriva-apps --no-upgrade
#   ./install-openmandriva-apps --no-third-party
#   ./install-openmandriva-apps --dry-run

trap 'echo; echo "Error on line $LINENO" >&2; echo' ERR
trap 'echo "Interrupted; cleaning up..."; trap - SIGINT; kill -INT $$' SIGINT

LOCAL_BIN="$HOME/.local/bin"
LOCAL_OPT="$HOME/.local/opt"
TMPDIR_INSTALL="${TMPDIR:-/tmp}/omv-app-install.$$"

DO_UPGRADE=1
ALLOW_THIRD_PARTY=1
DRY_RUN=0

PYENV_DIR="$LOCAL_BIN/pyenv"
FONT_DIR="$HOME/.local/share/fonts/NerdFonts/DroidSansMono"

mkdir -p "$LOCAL_BIN" "$LOCAL_OPT" "$HOME/.local/src" "$TMPDIR_INSTALL"
trap 'rm -rf "$TMPDIR_INSTALL"' EXIT

log() {
    printf '\n==> %s\n' "$*"
}

warn() {
    printf '\nWARNING: %s\n' "$*" >&2
}

have() {
    command -v "$1" >/dev/null 2>&1
}

run() {
    printf '+ '
    printf '%q ' "$@"
    printf '\n'
    if ((DRY_RUN == 0)); then
        "$@"
    fi
}

try_run() {
    printf '+ '
    printf '%q ' "$@"
    printf '\n'
    if ((DRY_RUN == 0)); then
        "$@"
    fi
}

sudo_run() {
    run sudo "$@"
}

sudo_try_run() {
    try_run sudo "$@"
}

parse_args() {
    while (($#)); do
        case "$1" in
            --no-upgrade)
                DO_UPGRADE=0
                shift
                ;;
            --no-third-party)
                ALLOW_THIRD_PARTY=0
                shift
                ;;
            --dry-run)
                DRY_RUN=1
                shift
                ;;
            -h|--help)
                sed -n '1,42p' "$0"
                exit 0
                ;;
            *)
                echo "Unknown option: $1" >&2
                exit 2
                ;;
        esac
    done
}

pick_dnf() {
    if have dnf5; then
        echo dnf5
    elif have dnf; then
        echo dnf
    else
        echo "dnf/dnf5 not found. This script expects OpenMandriva." >&2
        exit 1
    fi
}

DNF="$(pick_dnf)"

require_x86_64() {
    if [[ "$(uname -m)" != "x86_64" ]]; then
        echo "This port is intentionally x86_64-only. Detected: $(uname -m)" >&2
        exit 1
    fi
}

require_openmandriva() {
    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        case "${ID:-}" in
            openmandriva|openmandriva_lx|openmandriva-rolling)
                return 0
                ;;
        esac
        warn "This does not look like OpenMandriva: ID=${ID:-unknown}. Continuing anyway."
    else
        warn "/etc/os-release not readable; continuing without distro check."
    fi
}

remove_if_installed() {
    local pkg
    for pkg in "$@"; do
        if rpm -q "$pkg" >/dev/null 2>&1; then
            sudo_try_run "$DNF" remove -y "$pkg" || true
        fi
    done
}

install_any_pkg() {
    local label="$1"
    shift

    local pkg
    for pkg in "$@"; do
        log "Trying package for $label: $pkg"
        if sudo_try_run "$DNF" --refresh install -y "$pkg"; then
            return 0
        fi
    done

    warn "Could not install $label from enabled repositories. Tried: $*"
    return 1
}

install_pkg_group() {
    local label="$1"
    shift

    if ! install_any_pkg "$label" "$@"; then
        return 0
    fi
}


github_latest_asset_url() {
    local repo="$1"
    local grep_pattern="$2"

    curl -fsSL "https://api.github.com/repos/${repo}/releases/latest" \
        | grep '"browser_download_url"' \
        | cut -d '"' -f 4 \
        | grep -E "$grep_pattern" \
        | head -n1
}

install_executable_from_tarball() {
    local label="$1"
    local url="$2"
    local exe_name="$3"
    local archive="$TMPDIR_INSTALL/${label}.tar.gz"
    local unpack="$TMPDIR_INSTALL/${label}-unpack"

    log "Installing $label from upstream tarball"
    rm -rf "$unpack"
    mkdir -p "$unpack"
    run curl -fL "$url" -o "$archive"
    run tar -xzf "$archive" -C "$unpack"

    local exe_path
    exe_path="$(find "$unpack" -type f -name "$exe_name" -perm /111 | head -n1 || true)"
    if [[ -z "$exe_path" ]]; then
        exe_path="$(find "$unpack" -type f -name "$exe_name" | head -n1 || true)"
    fi
    if [[ -z "$exe_path" ]]; then
        echo "Could not find executable '$exe_name' in $url" >&2
        return 1
    fi

    run install -m 0755 "$exe_path" "$LOCAL_BIN/$exe_name"
}

install_executable_from_url() {
    local label="$1"
    local url="$2"
    local dest_name="$3"
    local dest="$LOCAL_BIN/$dest_name"

    log "Installing $label from upstream binary"
    run curl -fL "$url" -o "$dest"
    run chmod 0755 "$dest"
}

run_rolling_upgrade() {
    if ((DO_UPGRADE == 0)); then
        log "Skipping system upgrade"
        return 0
    fi

    log "Running OpenMandriva Rolling distro-sync"
    sudo_try_run "$DNF" clean all || true
    try_run "$DNF" clean all || true

    if ! sudo_try_run "$DNF" distro-sync --refresh --allowerasing; then
        warn "distro-sync with --allowerasing failed; retrying without --allowerasing."
        sudo_run "$DNF" distro-sync --refresh
    fi
}

install_core_packages() {
    log "Installing core packages"

    # Install logical groups one at a time. This is slower than one large DNF
    # transaction, but it handles OpenMandriva package-name variance better.
    install_pkg_group "git" git
    install_pkg_group "manual pager" mandoc man-db man-pages
    install_pkg_group "ranger" ranger
    install_pkg_group "tmux" tmux
    install_pkg_group "ripgrep" ripgrep rg
    install_pkg_group "fd" fd fd-find fdfind
    install_pkg_group "fzf" fzf
    install_pkg_group "lazygit" lazygit
    install_pkg_group "lg" lg
    install_pkg_group "make" make
    install_pkg_group "cmake" cmake
    install_pkg_group "C compiler" clang gcc
    install_pkg_group "C++ compiler" clang gcc-c++ g++
    install_pkg_group "C library headers" glibc-devel libc-devel
    install_pkg_group "groff" groff
    install_pkg_group "xz" xz xz-utils
    install_pkg_group "curl" curl
    install_pkg_group "Python" python python3
    install_pkg_group "Python pip" python-pip python3-pip pip
    install_pkg_group "Python virtualenv" python-virtualenv python3-virtualenv virtualenv
    install_pkg_group "tree" tree
    install_pkg_group "serial terminal" tio picocom minicom
    install_pkg_group "X clipboard xsel" xsel
    install_pkg_group "X clipboard xclip" xclip
    install_pkg_group "OpenSSH client" openssh-clients openssh-client openssh
    install_pkg_group "tar" tar
    install_pkg_group "gzip" gzip
    install_pkg_group "unzip" unzip
    install_pkg_group "fontconfig" fontconfig
    install_pkg_group "CA certificates" rootcerts ca-certificates
    install_pkg_group "FUSE for AppImage" fuse fuse2 fuse3
    install_pkg_group "cups" cups cups-filters system-config-printer gutenprint-cups hplip brdriver
    install_pkg_group "print" enscript
}

install_wezterm() {
    log "Installing WezTerm"
    if install_any_pkg "WezTerm" wezterm WezTerm; then
        return 0
    fi

    if ((ALLOW_THIRD_PARTY == 0)); then
        warn "Skipping upstream WezTerm because --no-third-party was used."
        return 0
    fi

    local url appdir appimage
    url="$(github_latest_asset_url wez/wezterm 'AppImage$' || true)"
    if [[ -z "$url" ]]; then
        warn "Could not find a WezTerm AppImage in the latest GitHub release."
        return 0
    fi

    appdir="$LOCAL_OPT/wezterm"
    appimage="$appdir/wezterm.AppImage"
    run mkdir -p "$appdir"
    run curl -fL "$url" -o "$appimage"
    run chmod 0755 "$appimage"
    run ln -sfn "$appimage" "$LOCAL_BIN/wezterm"

    warn "WezTerm was installed as an upstream AppImage. If it does not start, check FUSE/libfuse compatibility on this OpenMandriva install."
}

install_tpm() {
    log "Installing tmux plugin manager"
    run rm -rf "$HOME/.tmux/plugins/tpm" "$HOME/.config/tmux/plugins/tpm"
    run mkdir -p "$HOME/.config/tmux/plugins"
    run git clone https://github.com/tmux-plugins/tpm "$HOME/.config/tmux/plugins/tpm"
}


install_tldr() {
    log "Installing tldr client"
    remove_if_installed tldr tealdeer tealdeer-rs

    if install_any_pkg "tldr client" tealdeer tldr tealdeer-rs; then
        if have tldr; then
            try_run tldr --update || true
        elif have tealdeer; then
            try_run tealdeer --update || true
            run ln -sfn "$(command -v tealdeer)" "$LOCAL_BIN/tldr"
        fi
        return 0
    fi

    if ((ALLOW_THIRD_PARTY == 0)); then
        warn "Skipping upstream tealdeer because --no-third-party was used."
        return 0
    fi

    local url
    url="$(github_latest_asset_url tealdeer-rs/tealdeer 'tealdeer-linux-x86_64-musl$' || true)"
    if [[ -z "$url" ]]; then
        warn "Could not find tealdeer-linux-x86_64-musl in the latest GitHub release."
        return 0
    fi

    install_executable_from_url "tealdeer/tldr" "$url" tldr
    try_run "$LOCAL_BIN/tldr" --update || true
}

install_droidsansmono_nerd_font() {
    log "Installing DroidSansM Nerd Font Mono"

    if fc-match "DroidSansM Nerd Font Mono" 2>/dev/null | grep -qi "DroidSansM"; then
        echo "DroidSansM Nerd Font Mono already visible to fontconfig."
        return 0
    fi

    if ((ALLOW_THIRD_PARTY == 0)); then
        warn "Skipping upstream DroidSansM Nerd Font Mono because --no-third-party was used."
        return 0
    fi

    if ! have unzip; then
        warn "unzip is not available; cannot install Nerd Font zip."
        return 0
    fi

    local url archive unpack
    url="$(github_latest_asset_url ryanoasis/nerd-fonts 'DroidSansMono\.zip$' || true)"
    if [[ -z "$url" ]]; then
        warn "Could not find DroidSansMono.zip in the latest Nerd Fonts GitHub release."
        return 0
    fi

    archive="$TMPDIR_INSTALL/DroidSansMono.zip"
    unpack="$TMPDIR_INSTALL/DroidSansMono"
    rm -rf "$unpack"
    run mkdir -p "$unpack" "$FONT_DIR"
    run curl -fL "$url" -o "$archive"
    run unzip -o "$archive" -d "$unpack"

    local mono_font
    mono_font="$(find "$unpack" -type f -name 'DroidSansMNerdFontMono-Regular.otf' | head -n1 || true)"
    if [[ -z "$mono_font" ]]; then
        warn "DroidSansMNerdFontMono-Regular.otf was not found in the Nerd Font archive."
        return 0
    fi

    run install -m 0644 "$mono_font" "$FONT_DIR/"
    try_run fc-cache -f "$HOME/.local/share/fonts" || true
}

install_glow() {
    log "Installing Glow"
    if install_any_pkg "Glow" glow; then
        return 0
    fi

    if ((ALLOW_THIRD_PARTY == 0)); then
        warn "Skipping upstream Glow because --no-third-party was used."
        return 0
    fi

    local url
    url="$(github_latest_asset_url charmbracelet/glow 'Linux_x86_64\.tar\.gz$' || true)"
    if [[ -z "$url" ]]; then
        warn "Could not find a Glow Linux_x86_64 tarball in the latest GitHub release."
        return 0
    fi

    install_executable_from_tarball "glow" "$url" glow
}

make_ssh_key() {
    log "Checking ssh key"
    run mkdir -p "$HOME/.ssh"
    run chmod 700 "$HOME/.ssh"

    if [[ ! -s "$HOME/.ssh/id_ed25519.pub" ]]; then
        run ssh-keygen -t ed25519 -N '' -f "$HOME/.ssh/id_ed25519"
    else
        echo "Existing id_ed25519.pub found."
    fi
}

make_python_venv() {
    log "Checking Python venv"

    local py=""
    if have python3; then
        py="$(command -v python3)"
    elif have python; then
        py="$(command -v python)"
    else
        warn "No python/python3 command found; skipping venv creation."
        return 0
    fi

    if [[ -d "$PYENV_DIR" ]]; then
        echo "Existing Python venv found: $PYENV_DIR"
        return 0
    fi

    if [[ -d "$LOCAL_BIN/venv" ]]; then
        warn "Old Python venv path still exists: $LOCAL_BIN/venv. Leaving it untouched."
    fi

    if try_run "$py" -m venv "$PYENV_DIR"; then
        return 0
    fi

    warn "Python venv creation with pip failed, probably because ensurepip is unavailable. Creating pyenv without pip."
    try_run "$py" -m venv --without-pip "$PYENV_DIR" || warn "Could not create Python venv at $PYENV_DIR."
}

main() {
    parse_args "$@"
    require_x86_64
    require_openmandriva

    log "Using package manager: $DNF"
    run_rolling_upgrade
    install_core_packages
    install_wezterm
    install_tpm
    install_tldr
    make_ssh_key
    make_python_venv
    install_droidsansmono_nerd_font
    install_glow

    log "Done"
    echo "Ensure this appears early in PATH if using user-installed fallbacks:"
    echo "    export PATH=\"$LOCAL_BIN:\$PATH\""
}

main "$@"
