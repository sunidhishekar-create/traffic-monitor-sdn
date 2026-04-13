"""
Test Scenarios for Traffic Monitoring Project
=============================================
Run this inside the Mininet CLI using:
    mininet> py exec(open('/home/user/traffic_monitor/test_scenarios.py').read())

Or run from outside via:
    sudo python3 test_scenarios.py

Scenario 1: ICMP ping flood  (shows packet count growth)
Scenario 2: TCP iperf stream (shows large byte count)
"""

import time
import subprocess


def scenario1_ping_flood(net):
    """
    Scenario 1: h1 pings h3 repeatedly.
    Expected: flow table entries grow, packet/byte counts increase.
    """
    print("\n" + "=" * 56)
    print("  SCENARIO 1 – ICMP Ping flood")
    print("  h1 -> h3  (50 pings, 0.1s interval)")
    print("=" * 56)

    h1 = net.get("h1")
    result = h1.cmd("ping -c 50 -i 0.1 10.0.0.3")
    print(result)
    print("[Scenario 1 complete]\n")


def scenario2_iperf_tcp(net):
    """
    Scenario 2: iperf TCP stream from h2 to h3.
    Expected: very large byte count in flow stats.
    """
    print("\n" + "=" * 56)
    print("  SCENARIO 2 – TCP iperf stream")
    print("  h2 (client) -> h3 (server)  for 15 seconds")
    print("=" * 56)

    h2 = net.get("h2")
    h3 = net.get("h3")

    # Start iperf server on h3
    h3.cmd("iperf -s -D")
    time.sleep(1)

    # Run iperf client on h2
    result = h2.cmd("iperf -c 10.0.0.3 -t 15")
    print(result)

    h3.cmd("kill %iperf")
    print("[Scenario 2 complete]\n")


def show_flow_tables(net):
    """Dump OVS flow tables from both switches."""
    print("\n" + "=" * 56)
    print("  OVS Flow Tables")
    print("=" * 56)
    for sw in ["s1", "s2"]:
        print(f"\n  -- {sw} --")
        result = subprocess.run(
            ["sudo", "ovs-ofctl", "-O", "OpenFlow13", "dump-flows", sw],
            capture_output=True, text=True
        )
        print(result.stdout or result.stderr)


# When called from Mininet CLI via exec():
# The 'net' variable is available in the CLI's global scope.
try:
    scenario1_ping_flood(net)
    time.sleep(6)   # let controller poll stats once

    scenario2_iperf_tcp(net)
    time.sleep(6)   # let controller poll stats again

    show_flow_tables(net)

    print("\n[All scenarios done. Check controller terminal for stats.]")
    print("[Report file: /tmp/traffic_report.txt]\n")

except NameError:
    print("Run this inside the Mininet CLI with:")
    print("  mininet> py exec(open('test_scenarios.py').read())")
