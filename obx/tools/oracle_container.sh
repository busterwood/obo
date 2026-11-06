#!/usr/bin/env bash
# usage: obx/tools/oracle_container.sh create|start|stop|status|logs|rm
#
# The Oracle Database Free container for the live `oracle` tests (design/oracle.md):
# rootless podman, image docker.io/gvenzl/oracle-free:slim, named obx-oracle, port 1521
# on 127.0.0.1 only. `create` pulls the image, makes random passwords for SYSTEM and for
# the test user APP (in the pluggable database FREEPDB1), starts the container and writes
# the test connection string to ~/.obo-oracle-dsn (mode 600). No password is printed.
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
DSN_FILE=$HOME/.obo-oracle-dsn
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
    $P run -d --name $NAME --restart=on-failure -p 127.0.0.1:1521:1521 --env-file "$env_file" $IMAGE >/dev/null
    rc=$?
    rm -f "$env_file"
    [ $rc -eq 0 ] || exit $rc
    printf 'User Id=app;Password=%s;Data Source=localhost:1521/FREEPDB1\n' "$app" > "$DSN_FILE"
    chmod 600 "$DSN_FILE"
    printf 'User Id=system;Password=%s;Data Source=localhost:1521/FREEPDB1\n' "$sys" > "$DSN_FILE.system"
    chmod 600 "$DSN_FILE.system"
    echo "created $NAME; test DSN in $DSN_FILE"
    wait_ready
    ;;
  start) $P start $NAME >/dev/null && wait_ready ;;
  stop) $P stop $NAME >/dev/null && echo stopped ;;
  status) $P ps -a --filter name=$NAME --format '{{.Names}} {{.Status}} {{.Ports}}' ;;
  logs) $P logs --tail 40 $NAME 2>&1 | grep -v -i password ;;
  rm) $P rm -f $NAME >/dev/null && rm -f "$DSN_FILE" "$DSN_FILE.system" && echo removed ;;
  *) echo "usage: $0 create|start|stop|status|logs|rm" >&2; exit 2 ;;
esac
