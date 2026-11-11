# SPDX-License-Identifier: MIT
# Remember verified ownership before dpkg replaces the old conffile registry.
# Ordinary package files are removed by dpkg without maintscript intervention.
retire_mesa_conffile() {
    retire_conf_package=$1
    retire_conf_version=$2
    retire_conf_path=$3
    shift 3
    retire_conf_state=/var/lib/$retire_conf_package/mesa-conffile-migrations
    retire_conf_key=$(printf '%s' "$retire_conf_path" | tr / _)
    retire_conf_marker=$retire_conf_state/$retire_conf_key
    if [ "$DPKG_MAINTSCRIPT_NAME" = preinst ]; then
        if dpkg-query -W -f='${Conffiles}\n' "$retire_conf_package" 2>/dev/null |
            awk -v path="$retire_conf_path" '$1 == path { found=1 } END { exit !found }' &&
            dpkg-query -L "$retire_conf_package" 2>/dev/null | grep -Fqx "$retire_conf_path"; then
            install -d -m 700 "$retire_conf_state"
            printf '%s\n' "$retire_conf_path" > "$retire_conf_marker.tmp"
            mv "$retire_conf_marker.tmp" "$retire_conf_marker"
        fi
    fi
    [ -f "$retire_conf_marker" ] || return 0
    if [ "$(cat "$retire_conf_marker")" != "$retire_conf_path" ]; then
        echo 'Invalid Mesa conffile migration record' >&2
        return 1
    fi
    dpkg-maintscript-helper rm_conffile "$retire_conf_path" "$retire_conf_version" \
        "$retire_conf_package" -- "$@"
    case "$DPKG_MAINTSCRIPT_NAME" in
    postinst|postrm)
        if [ ! -e "$retire_conf_path.dpkg-bak" ] &&
           [ ! -e "$retire_conf_path.dpkg-backup" ] &&
           [ ! -e "$retire_conf_path.dpkg-remove" ]; then
            rm "$retire_conf_marker"
            rmdir "$retire_conf_state" 2>/dev/null || true
            rmdir "/var/lib/$retire_conf_package" 2>/dev/null || true
        fi
        ;;
    esac
}
