"""Configure pinned Scapy for file-only use before importing protocol layers."""


def configure_offline() -> None:
    from scapy.config import conf
    from scapy.interfaces import NetworkInterfaceDict

    class OfflineInterfaces(NetworkInterfaceDict):
        # Scapy 2.6.1 otherwise opens a netlink socket during layer imports.
        def reload(self):
            self.clear()

        def load_confiface(self):
            pass

    conf.route_autoload = False
    conf.route6_autoload = False
    conf.ifaces = OfflineInterfaces()
