#!/bin/sh
# Host-level firewall rules for the trellum-sandbox network (172.30.100.0/24).
#
# Docker's own isolation already stops sandboxes from reaching the compose
# network (Postgres, web, worker). Two things it does NOT block, and that only
# a host rule can:
#   - cloud instance metadata (169.254.169.254) — on a cloud VM this hands out
#     the instance's credentials, so blocking it is MANDATORY there.
#   - services bound to the host itself.
#
# Run once per host, with the stack up, then persist via your firewall tooling
# (iptables-persistent, firewalld, the cloud's rules). Idempotent.
#
#   sudo docker/harden-sandbox-net.sh
set -e

SUBNET="${TRELLUM_SANDBOX_SUBNET:-172.30.100.0/24}"

add_once() {
    # $1 = chain, rest = rule. Insert only if an identical rule is absent.
    chain="$1"; shift
    if ! iptables -C "$chain" "$@" 2>/dev/null; then
        iptables -I "$chain" "$@"
    fi
}

# Block link-local / cloud metadata from the sandbox subnet (forwarded traffic
# passes through DOCKER-USER).
add_once DOCKER-USER -s "$SUBNET" -d 169.254.0.0/16 -j DROP

# Block sandbox -> host services (container-to-host lands in INPUT).
add_once INPUT -s "$SUBNET" -m conntrack --ctstate NEW -j DROP

# Strict variant: also cut private LAN ranges. Leave off by default — a
# self-hoster may run a warehouse on the LAN. Uncomment deliberately:
# add_once DOCKER-USER -s "$SUBNET" -d 10.0.0.0/8 -j DROP
# add_once DOCKER-USER -s "$SUBNET" -d 172.16.0.0/12 -j DROP
# add_once DOCKER-USER -s "$SUBNET" -d 192.168.0.0/16 -j DROP

echo "sandbox network hardening applied for $SUBNET"
