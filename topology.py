"""
Custom Mininet Topology for Traffic Monitoring Project
======================================================
Topology:
          h1 (10.0.0.1)
           |
          s1 ── s2 ── h3 (10.0.0.3)
           |
          h2 (10.0.0.2)

  - 2 switches (s1, s2)
  - 3 hosts (h1, h2 on s1 | h3 on s2)
  - Controller: Ryu on localhost:6633

Usage:
  sudo python3 topology.py
"""

from mininet.net    import Mininet
from mininet.node   import Controller, RemoteController, OVSKernelSwitch
from mininet.cli    import CLI
from mininet.link   import TCLink
from mininet.log    import setLogLevel, info
from mininet.topo   import Topo


class TrafficMonitorTopo(Topo):
    """
    Two-switch linear topology with 3 hosts:
       h1 -- s1 -- s2 -- h3
              |
             h2
    """
    def build(self):
        # Switches
        s1 = self.addSwitch("s1", protocols="OpenFlow13")
        s2 = self.addSwitch("s2", protocols="OpenFlow13")

        # Hosts
        h1 = self.addHost("h1", ip="10.0.0.1/24", mac="00:00:00:00:00:01")
        h2 = self.addHost("h2", ip="10.0.0.2/24", mac="00:00:00:00:00:02")
        h3 = self.addHost("h3", ip="10.0.0.3/24", mac="00:00:00:00:00:03")

        # Links  (bw=10 Mbps, delay=2ms, loss=0%)
        self.addLink(h1, s1, bw=10, delay="2ms")
        self.addLink(h2, s1, bw=10, delay="2ms")
        self.addLink(s1, s2, bw=10, delay="5ms")
        self.addLink(h3, s2, bw=10, delay="2ms")


def run():
    setLogLevel("info")

    topo = TrafficMonitorTopo()
    net  = Mininet(
        topo=topo,
        switch=OVSKernelSwitch,
        controller=None,       # we attach a remote (Ryu) controller below
        link=TCLink,
        autoSetMacs=False,
        waitConnected=True,
    )

    # Attach remote Ryu controller
    c0 = net.addController(
        "c0",
        controller=RemoteController,
        ip="127.0.0.1",
        port=6633,
    )

    net.start()

    info("\n" + "=" * 56 + "\n")
    info("  Topology started\n")
    info("  Hosts  : h1(10.0.0.1)  h2(10.0.0.2)  h3(10.0.0.3)\n")
    info("  Switches: s1, s2\n")
    info("  Controller: Ryu @ 127.0.0.1:6633\n")
    info("=" * 56 + "\n\n")

    # ── Quick connectivity test ──────────────────────────────
    info(">>> Running pingAll to seed flow table...\n")
    net.pingAll()

    # ── Drop into interactive CLI ────────────────────────────
    info("\n>>> Entering Mininet CLI. Try:\n")
    info("    h1 ping -c 5 h3\n")
    info("    h1 iperf -s &   h2 iperf -c 10.0.0.1 -t 10\n")
    info("    h1 iperf -s &   h3 iperf -c 10.0.0.1 -t 10\n")
    info("    sh cat /tmp/traffic_report.txt\n\n")

    CLI(net)
    net.stop()


if __name__ == "__main__":
    run()
