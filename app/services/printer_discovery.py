from __future__ import annotations

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from http.client import HTTPConnection


class PrinterDiscoveryError(Exception):
    """Raised when discovery input or execution fails."""


@dataclass(slots=True)
class DiscoveredPrinter:
    host: str
    moonraker: bool = False
    ssh: bool = False
    moonraker_status: str | None = None
    ssh_banner: str | None = None

    def merge(self, other: "DiscoveredPrinter") -> None:
        self.moonraker = self.moonraker or other.moonraker
        self.ssh = self.ssh or other.ssh
        if not self.moonraker_status and other.moonraker_status:
            self.moonraker_status = other.moonraker_status
        if not self.ssh_banner and other.ssh_banner:
            self.ssh_banner = other.ssh_banner


class PrinterDiscoveryService:
    def suggest_scan_cidrs(self) -> list[str]:
        addresses = self._discover_local_ipv4_addresses()
        cidrs: set[str] = set()
        for address in addresses:
            try:
                network = ipaddress.ip_network(f"{address}/24", strict=False)
            except ValueError:
                continue
            cidrs.add(str(network))
        if not cidrs:
            cidrs.add("192.168.1.0/24")
        return sorted(cidrs)

    def scan(
        self,
        cidr: str,
        *,
        timeout: float = 0.35,
        max_hosts: int = 254,
        workers: int = 64,
    ) -> list[DiscoveredPrinter]:
        network = self._parse_network(cidr)
        if max_hosts <= 0:
            raise PrinterDiscoveryError("max_hosts must be greater than 0.")

        hosts = [str(host) for host in network.hosts()][:max_hosts]
        if not hosts:
            return []

        discovered: dict[str, DiscoveredPrinter] = {}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {
                executor.submit(self._probe_host, host, timeout): host
                for host in hosts
            }
            for future in as_completed(futures):
                host = futures[future]
                try:
                    probe_results = future.result()
                except Exception:  # noqa: BLE001
                    continue
                for result in probe_results:
                    existing = discovered.get(host)
                    if existing is None:
                        discovered[host] = result
                    else:
                        existing.merge(result)

        return sorted(
            discovered.values(),
            key=lambda item: tuple(int(part) for part in item.host.split(".")),
        )

    @staticmethod
    def _parse_network(cidr: str) -> ipaddress.IPv4Network:
        raw = cidr.strip()
        if not raw:
            raise PrinterDiscoveryError("CIDR range is empty.")
        try:
            network = ipaddress.ip_network(raw, strict=False)
        except ValueError as exc:
            raise PrinterDiscoveryError(f"Invalid CIDR range '{cidr}'.") from exc
        if not isinstance(network, ipaddress.IPv4Network):
            raise PrinterDiscoveryError("Only IPv4 CIDR ranges are supported.")
        if network.num_addresses <= 2:
            raise PrinterDiscoveryError("CIDR range does not contain host addresses.")
        return network

    @staticmethod
    def _discover_local_ipv4_addresses() -> set[str]:
        addresses: set[str] = set()

        try:
            for _, _, _, _, sockaddr in socket.getaddrinfo(
                socket.gethostname(), None, family=socket.AF_INET
            ):
                host = sockaddr[0]
                if host and not host.startswith("127."):
                    addresses.add(host)
        except OSError:
            pass

        # Preferred route lookup - does not send traffic.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                route_ip = sock.getsockname()[0]
                if route_ip and not route_ip.startswith("127."):
                    addresses.add(route_ip)
        except OSError:
            pass

        private = {
            ip
            for ip in addresses
            if ipaddress.ip_address(ip).is_private
            and not ipaddress.ip_address(ip).is_loopback
        }
        return private or addresses

    def _probe_host(self, host: str, timeout: float) -> list[DiscoveredPrinter]:
        results: list[DiscoveredPrinter] = []
        moonraker = self._probe_moonraker(host, timeout)
        if moonraker is not None:
            results.append(moonraker)

        ssh = self._probe_ssh(host, timeout)
        if ssh is not None:
            results.append(ssh)
        return results

    @staticmethod
    def _probe_moonraker(host: str, timeout: float) -> DiscoveredPrinter | None:
        conn = None
        try:
            conn = HTTPConnection(host, 7125, timeout=timeout)
            conn.request("GET", "/server/info")
            response = conn.getresponse()
            body = response.read().decode("utf-8", errors="ignore").lower()
            if response.status == 200 and (
                "moonraker" in body or "klippy" in body or "result" in body
            ):
                return DiscoveredPrinter(
                    host=host,
                    moonraker=True,
                    moonraker_status=f"http {response.status}",
                )
        except OSError:
            return None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass
        return None

    @staticmethod
    def _probe_ssh(host: str, timeout: float) -> DiscoveredPrinter | None:
        sock = None
        try:
            sock = socket.create_connection((host, 22), timeout=timeout)
            sock.settimeout(timeout)
            banner = ""
            try:
                data = sock.recv(128)
                banner = data.decode("utf-8", errors="ignore").strip()
            except OSError:
                banner = ""
            return DiscoveredPrinter(
                host=host,
                ssh=True,
                ssh_banner=(banner or "port 22 open"),
            )
        except OSError:
            return None
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
