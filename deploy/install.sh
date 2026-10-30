#!/usr/bin/env bash
# Instala o actualiza vertice-content-agent en el VPS (AlmaLinux 9, como root).
# Es idempotente: se puede ejecutar las veces que haga falta.
#
#   git clone git@github.com:miguelmartt/vertice-content-agent.git /root/vertice-content-agent
#   bash /root/vertice-content-agent/deploy/install.sh
#
# No arranca el servicio hasta que /etc/vertice-content-agent/env tenga los tokens.

set -Eeuo pipefail
shopt -s inherit_errexit 2>/dev/null || true

readonly APP="vertice-content-agent"
readonly SERVICE_USER="vca"
readonly SRC_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly INSTALL_DIR="/opt/${APP}"
readonly STATE_DIR="/var/lib/${APP}"
readonly CONF_DIR="/etc/${APP}"
readonly ENV_FILE="${CONF_DIR}/env"
readonly UNIT_FILE="/etc/systemd/system/${APP}.service"

info() { printf '==> %s\n' "$*"; }
warn() { printf 'AVISO: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
trap 'die "fallo en la línea ${LINENO}"' ERR

[[ ${EUID} -eq 0 ]] || die "ejecútalo como root"
[[ -f "${SRC_DIR}/vca/__main__.py" ]] || die "no encuentro el código en ${SRC_DIR}"
command -v systemctl >/dev/null || die "hace falta systemd"
command -v git >/dev/null || die "falta git (dnf install git)"
command -v python3 >/dev/null || die "falta python3"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || die "hace falta Python 3.9 o superior"

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    info "Creando el usuario de sistema ${SERVICE_USER}"
    useradd --system --home-dir "${STATE_DIR}" --shell /sbin/nologin "${SERVICE_USER}"
fi

info "Copiando el código a ${INSTALL_DIR}"
install -d -o root -g root -m 0755 "${INSTALL_DIR}"
staging="$(mktemp -d "${INSTALL_DIR}/.vca.XXXXXX")"
cp -a "${SRC_DIR}/vca" "${staging}/vca"
chown -R root:root "${staging}"
chmod -R u=rwX,go=rX "${staging}"
if [[ -d "${INSTALL_DIR}/vca" ]]; then
    mv "${INSTALL_DIR}/vca" "${staging}/vca.old"
fi
mv "${staging}/vca" "${INSTALL_DIR}/vca"
rm -r --one-file-system -- "${staging}"

install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 "${STATE_DIR}"
install -d -o root -g "${SERVICE_USER}" -m 0750 "${CONF_DIR}"
if [[ ! -f "${ENV_FILE}" ]]; then
    install -o root -g "${SERVICE_USER}" -m 0640 "${SRC_DIR}/.env.example" "${ENV_FILE}"
    info "Creado ${ENV_FILE}: rellénalo antes de arrancar"
fi

install -o root -g root -m 0644 "${SRC_DIR}/deploy/${APP}.service" "${UNIT_FILE}"
systemctl daemon-reload
systemctl enable "${APP}" >/dev/null

# Atajo para la línea de órdenes: `vca status`, `vca research`… como el usuario del servicio.
cat > /usr/local/bin/vca <<EOF
#!/usr/bin/env bash
exec runuser -u ${SERVICE_USER} -- env -i HOME=${STATE_DIR} PATH=${STATE_DIR}/.local/bin:/usr/local/bin:/usr/bin:/bin \\
    bash -c 'set -a; . ${ENV_FILE}; set +a; cd ${INSTALL_DIR} && VCA_STATE_DIR=${STATE_DIR} exec python3 -m vca "\$@"' vca "\$@"
EOF
chmod 0755 /usr/local/bin/vca

# Comprobaciones de lo que no instala este script.
php_bin="$(sed -n 's/^VCA_PHP_BIN=//p' "${ENV_FILE}" | tail -n1)"
command -v "${php_bin:-php}" >/dev/null || warn "no encuentro PHP (${php_bin:-php}); en el VPS suele ser php83"
claude_bin="${STATE_DIR}/.local/bin/claude"
if [[ ! -x "${claude_bin}" ]]; then
    warn "Claude Code no está instalado para ${SERVICE_USER}. Instálalo con:"
    warn "  runuser -u ${SERVICE_USER} -- env HOME=${STATE_DIR} bash -c 'curl -fsSL https://claude.ai/install.sh | bash'"
fi

if grep -qE '^VCA_TELEGRAM_TOKEN=.+' "${ENV_FILE}" && grep -qE '^CLAUDE_CODE_OAUTH_TOKEN=.+' "${ENV_FILE}"; then
    info "Comprobando la configuración"
    if /usr/local/bin/vca check; then
        systemctl restart "${APP}"
        info "Servicio en marcha: journalctl -u ${APP} -f"
    else
        warn "La comprobación ha fallado: corrige ${ENV_FILE} y vuelve a ejecutar este script"
    fi
else
    info "Servicio habilitado pero parado: rellena ${ENV_FILE} y vuelve a ejecutar este script"
fi
