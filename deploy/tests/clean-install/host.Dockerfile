# A throwaway "fresh host": Ubuntu 22.04 with systemd, Docker and nginx.
# Used by run.sh; never deploy this.
FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y -qq systemd systemd-sysv docker.io nginx python3-venv git curl iptables sudo ca-certificates openssl >/dev/null \
 && systemctl mask getty@tty1.service systemd-logind.service >/dev/null 2>&1; true
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
