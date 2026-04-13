"""
Traffic Monitoring and Statistics Collector - POX Controller
"""
from pox.core import core
from pox.lib.util import dpid_to_str
from pox.lib.recoco import Timer
import pox.openflow.libopenflow_01 as of
from pox.lib.revent import *
import datetime
import time

log = core.getLogger()

STATS_INTERVAL = 5
REPORT_INTERVAL = 30
REPORT_FILE = "/tmp/traffic_report.txt"

class TrafficMonitor(EventMixin):
    def __init__(self):
        self.mac_to_port = {}
        self.flow_stats = {}
        self.switch_totals = {}
        self.report_count = 0
        self.connections = {}
        self.listenTo(core.openflow)
        Timer(STATS_INTERVAL, self._request_stats, recurring=True)
        Timer(REPORT_INTERVAL, self._generate_report, recurring=True)
        log.info("=" * 55)
        log.info("  Traffic Monitor Controller started")
        log.info("  Stats every %ds | Report every %ds", STATS_INTERVAL, REPORT_INTERVAL)
        log.info("=" * 55)

    def _handle_ConnectionUp(self, event):
        self.connections[event.dpid] = event.connection
        self.flow_stats[event.dpid] = {}
        self.switch_totals[event.dpid] = {"packets": 0, "bytes": 0}
        log.info("[Switch %s] connected", dpid_to_str(event.dpid))

    def _handle_ConnectionDown(self, event):
        self.connections.pop(event.dpid, None)
        log.info("[Switch %s] disconnected", dpid_to_str(event.dpid))

    def _handle_PacketIn(self, event):
        packet = event.parsed
        if not packet.parsed:
            return

        dpid = event.dpid
        in_port = event.port
        src = str(packet.src)
        dst = str(packet.dst)

        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
            # Install flow rule
            msg = of.ofp_flow_mod()
            msg.match.dl_src = packet.src
            msg.match.dl_dst = packet.dst
            msg.match.in_port = in_port
            msg.idle_timeout = 10
            msg.hard_timeout = 30
            msg.priority = 10
            msg.actions.append(of.ofp_action_output(port=out_port))
            event.connection.send(msg)
        else:
            out_port = of.OFPP_FLOOD

        # Forward packet
        msg = of.ofp_packet_out()
        msg.data = event.ofp
        msg.actions.append(of.ofp_action_output(port=out_port))
        event.connection.send(msg)

    def _handle_FlowStatsReceived(self, event):
        dpid = event.connection.dpid
        total_pkts = 0
        total_bytes = 0

        log.info("")
        log.info("  ┌─ Flow Stats  Switch %s ──────────────────────────┐", dpid_to_str(dpid))
        log.info("  │  %-5s  %-17s  %-17s  %8s  %10s  │", "Port", "Src MAC", "Dst MAC", "Packets", "Bytes")
        log.info("  │  %s  │", "-" * 65)

        flows = []
        for stat in event.stats:
            if stat.priority == 0:
                continue
            in_port = stat.match.in_port or "?"
            src = str(stat.match.dl_src) if stat.match.dl_src else "??"
            dst = str(stat.match.dl_dst) if stat.match.dl_dst else "??"
            pkts = stat.packet_count
            byts = stat.byte_count
            dur  = stat.duration_sec

            key = (in_port, src, dst)
            self.flow_stats[dpid][key] = {"packets": pkts, "bytes": byts, "duration": dur}
            total_pkts  += pkts
            total_bytes += byts
            flows.append((in_port, src, dst, pkts, byts))

        for (p, s, d, pk, by) in sorted(flows, key=lambda x: x[3], reverse=True):
            log.info("  │  %-5s  %-17s  %-17s  %8d  %10d  │", p, s, d, pk, by)

        log.info("  │  %s  │", "-" * 65)
        log.info("  │  TOTAL  %-35s  %8d  %10d  │", "", total_pkts, total_bytes)
        log.info("  └──────────────────────────────────────────────────────────────┘")

        self.switch_totals[dpid] = {"packets": total_pkts, "bytes": total_bytes}

    def _request_stats(self):
        for dpid, conn in self.connections.items():
            conn.send(of.ofp_stats_request(body=of.ofp_flow_stats_request()))

    def _generate_report(self):
        self.report_count += 1
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sep = "=" * 60
        lines = [sep, f"  TRAFFIC REPORT #{self.report_count}  |  {now}", sep, ""]

        for dpid, totals in self.switch_totals.items():
            lines.append(f"  Switch : {dpid_to_str(dpid)}")
            lines.append(f"  Packets: {totals['packets']:,}")
            lines.append(f"  Bytes  : {totals['bytes']:,}  ({totals['bytes']/1024:.2f} KB)")
            lines.append("")
            lines.append(f"  {'Port':<6} {'Src MAC':<17} {'Dst MAC':<17} {'Pkts':>8} {'Bytes':>10} {'Dur':>6}")
            lines.append("  " + "-" * 68)
            flows = self.flow_stats.get(dpid, {})
            if flows:
                for (port, src, dst), s in sorted(flows.items(), key=lambda x: x[1]['bytes'], reverse=True):
                    lines.append(f"  {str(port):<6} {src:<17} {dst:<17} {s['packets']:>8,} {s['bytes']:>10,} {s['duration']:>6}s")
            else:
                lines.append("  (no flows yet)")
            lines.append("")

        lines.append(sep)
        text = "\n".join(lines)

        try:
            with open(REPORT_FILE, "a") as f:
                f.write(text + "\n\n")
        except Exception as e:
            log.warning("Could not write report: %s", e)

        for line in lines:
            log.info(line)
        log.info("  [Report saved -> %s]", REPORT_FILE)

def launch():
    core.registerNew(TrafficMonitor)
