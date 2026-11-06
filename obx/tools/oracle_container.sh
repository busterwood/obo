#!/usr/bin/env bash
# usage: obx/tools/oracle_container.sh create|grants|tls|start|stop|status|logs|rm
#
# The Oracle Database Free container for the live `oracle` tests (design/oracle.md):
# rootless podman, image docker.io/gvenzl/oracle-free:slim, named obx-oracle, ports 1521
# (TCP) and 2484 (TCPS) on 127.0.0.1 only. `create` pulls the image, makes random
# passwords for SYSTEM and for the test user APP (in the pluggable database FREEPDB1),
# starts the container, writes the test connection string to ~/.obo-oracle-dsn (mode
# 600) and runs `tls` (which also pulls the full image once, for its orapki). No
# password is printed.
#
# After a reboot (WSL has no systemd user session, so the restart policy does not bring
# it back): obx/tools/oracle_container.sh start.
#
# Podman needs a runtime directory; WSL has no /run/user/$UID, so one is made here.
set -u
if [ -z "${XDG_RUNTIME_DIR:-}" ] || [ ! -w "${XDG_RUNTIME_DIR:-}" ]; then
  export XDG_RUNTIME_DIR=/tmp/obx-podman-$(id -u)
  mkdir -p -m 700 "$XDG_RUNTIME_DIR"
fi
NAME=obx-oracle
IMAGE=docker.io/gvenzl/oracle-free:slim
PKI_IMAGE=docker.io/gvenzl/oracle-free:full   # only for orapki in `tls`
DSN_FILE=$HOME/.obo-oracle-dsn
CA_FILE=$HOME/.obo-oracle-ca.pem
TLS_FILE=$HOME/.obo-oracle-tls
P="podman --cgroup-manager=cgroupfs"

pw() { local s; s=$(head -c 200 /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 23); echo "P${s}"; }

wait_ready() {
  for _ in $(seq 1 180); do
    if $P logs $NAME 2>/dev/null | grep -q 'DATABASE IS READY TO USE'; then echo "ready"; return 0; fi
    if [ "$($P inspect -f '{{.State.Running}}' $NAME 2>/dev/null)" != true ]; then echo "not running"; return 1; fi
    sleep 5
  done
  echo "not ready after 15 minutes"; return 1
}

case "${1:-}" in
  create)
    if $P container exists $NAME 2>/dev/null; then echo "$NAME exists: use start"; exit 1; fi
    $P pull -q $IMAGE >/dev/null || exit 1
    sys=$(pw); app=$(pw)
    umask 077
    env_file=$(mktemp)
    printf 'ORACLE_PASSWORD=%s\nAPP_USER=app\nAPP_USER_PASSWORD=%s\n' "$sys" "$app" > "$env_file"
    $P run -d --name $NAME --restart=on-failure -p 127.0.0.1:1521:1521 -p 127.0.0.1:2484:2484 \
      --env-file "$env_file" $IMAGE >/dev/null
    rc=$?
    rm -f "$env_file"
    [ $rc -eq 0 ] || exit $rc
    printf 'User Id=app;Password=%s;Data Source=localhost:1521/FREEPDB1\n' "$app" > "$DSN_FILE"
    chmod 600 "$DSN_FILE"
    printf 'User Id=system;Password=%s;Data Source=localhost:1521/FREEPDB1\n' "$sys" > "$DSN_FILE.system"
    chmod 600 "$DSN_FILE.system"
    echo "created $NAME; test DSN in $DSN_FILE"
    wait_ready && "$0" grants && "$0" tls
    ;;
  grants)
    # Read access for APP to its session's statistics and open cursors, which
    # oracle_live_out checks for cursor leaks. No password: sqlplus / as sysdba.
    $P exec -i $NAME bash -c 'sqlplus -s / as sysdba' <<'EOF' >/dev/null || exit 1
whenever sqlerror exit failure
alter session set container = FREEPDB1;
grant select on sys.v_$mystat to app;
grant select on sys.v_$statname to app;
grant select on sys.v_$open_cursor to app;
exit
EOF
    echo "granted APP the session views"
    ;;
  tls)
    # A self-signed certificate for CN=localhost in an auto-login wallet, and a TCPS
    # listener on 2484 next to TCP 1521. The slim image has no Java, so orapki runs in
    # a throwaway container of the full image ($PKI_IMAGE, pulled once) and the wallet
    # is copied across. (A PEM wallet made with openssl was refused: ORA-28759.) The
    # certificate goes to $CA_FILE (the CA the client trusts) and the Data Source to
    # $TLS_FILE, which oracle_live_tls appends to the test DSN; neither holds a secret.
    if ! $P exec $NAME test -f /opt/oracle/tls/cwallet.sso; then
      $P rm -f $NAME-pki >/dev/null 2>&1
      $P run --name $NAME-pki --entrypoint /bin/bash $PKI_IMAGE -c '
        set -e; W=/tmp/tls; mkdir -p $W
        orapki wallet create -wallet $W -auto_login_only >/dev/null
        orapki wallet add -wallet $W -dn CN=localhost -keysize 2048 -sign_alg sha256 -self_signed \
          -validity 3650 -addext_san DNS:localhost -auto_login_only >/dev/null
        orapki wallet export -wallet $W -dn CN=localhost -cert $W/cert.pem >/dev/null' || exit 1
      tmp=$(mktemp -d)
      $P cp $NAME-pki:/tmp/tls "$tmp/tls" || exit 1
      $P rm -f $NAME-pki >/dev/null
      $P cp "$tmp/tls" $NAME:/opt/oracle/ || exit 1
      rm -rf "$tmp"
      $P exec -u root $NAME chown -R oracle:oinstall /opt/oracle/tls || exit 1
    fi
    $P exec -i $NAME bash -s <<'EOF' || exit 1
set -e
W=/opt/oracle/tls
A=$ORACLE_HOME/network/admin
if ! grep -q TCPS $A/listener.ora; then
  cat > $A/listener.ora <<LSN
LISTENER =
  (DESCRIPTION_LIST =
    (DESCRIPTION =
      (ADDRESS = (PROTOCOL = IPC)(KEY = EXTPROC_FOR_FREE))
      (ADDRESS = (PROTOCOL = TCP)(HOST = )(PORT = 1521))
      (ADDRESS = (PROTOCOL = TCPS)(HOST = )(PORT = 2484))
    )
  )

DEFAULT_SERVICE_LISTENER = FREE
SSL_CLIENT_AUTHENTICATION = FALSE
WALLET_LOCATION = (SOURCE = (METHOD = FILE)(METHOD_DATA = (DIRECTORY = $W)))
LSN
  printf 'SSL_CLIENT_AUTHENTICATION = FALSE\nWALLET_LOCATION = (SOURCE = (METHOD = FILE)(METHOD_DATA = (DIRECTORY = %s)))\n' $W >> $A/sqlnet.ora
  lsnrctl stop >/dev/null
  lsnrctl start >/dev/null
  echo 'ALTER SYSTEM REGISTER;' | sqlplus -s / as sysdba >/dev/null
fi
EOF
    umask 077
    $P exec $NAME cat /opt/oracle/tls/cert.pem > "$CA_FILE" || exit 1
    chmod 644 "$CA_FILE"
    printf 'Data Source=tcps://localhost:2484/FREEPDB1;CA File=%s\n' "$CA_FILE" > "$TLS_FILE"
    echo "TCPS on 127.0.0.1:2484; CA in $CA_FILE, TLS Data Source in $TLS_FILE"
    ;;
  start) $P start $NAME >/dev/null && wait_ready ;;
  stop) $P stop $NAME >/dev/null && echo stopped ;;
  status) $P ps -a --filter name=$NAME --format '{{.Names}} {{.Status}} {{.Ports}}' ;;
  logs) $P logs --tail 40 $NAME 2>&1 | grep -v -i password ;;
  rm) $P rm -f $NAME >/dev/null && rm -f "$DSN_FILE" "$DSN_FILE.system" "$CA_FILE" "$TLS_FILE" && echo removed ;;
  *) echo "usage: $0 create|tls|start|stop|status|logs|rm" >&2; exit 2 ;;
esac
