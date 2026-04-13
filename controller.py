"""
Traffic Monitoring and Statistics Collector - Ryu Controller
============================================================
Collects flow statistics, displays packet/byte counts,
performs periodic monitoring, and generates simple reports.

Author: SDN Mininet Project
Controller: Ryu OpenFlow 1.3
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ipv4, tcp, udp
from ryu.lib import hub

import time
import datetime
import os

# ─────────────────────────────────────────────
#  Configuration
# ─────────────────────────────────────────────
STATS_POLL_INTERVAL = 5       # seconds between stat polls
REPORT_INTERVAL     = 30      # seconds between full reports
REPORT_FILE         = "/tmp/traffic_report.txt"


class TrafficMonitor(app_manager.RyuApp):
    """
    Ryu app that:
      1. Acts as a learning switch (installs forwarding rules)
      2. Periodically requests flow stats from every switch
      3. Displays per-flow packet/byte counts to stdout
      4. Writes a periodic summary report to REPORT_FILE
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(TrafficMonitor, self).__init__(*args, **kwargs)

        # MAC -> (datapath, port)  learning table
        self.mac_to_port = {}

        # datapath_id -> datapath object  (alive switches)
        self.datapaths = {}

        # Statistics store
        # { dpid: { (in_port, eth_src, eth_dst): {packets, bytes, duration} } }
        self.flow_stats = {}

        # Total counters per switch  { dpid: {packets, bytes} }
        self.switch_totals = {}

        # Report counter
        self.report_count = 0

        # Start background monitoring thread
        self.monitor_thread = hub.spawn(self._monitor_loop)

        self.logger.info("=" * 60)
        self.logger.info("  Traffic Monitor Controller  started")
        self.logger.info("  Poll interval : %ds", STATS_POLL_INTERVAL)
        self.logger.info("  Report interval: %ds", REPORT_INTERVAL)
        self.logger.info("=" * 60)

    # ─────────────────────────────────────────────
    #  Switch lifecycle events
    # ─────────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """Install table-miss flow entry so unmatched packets reach the controller."""
        datapath = ev.msg.datapath
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser

        # Table-miss: send to controller, lowest priority
        match  = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self._add_flow(datapath, 0, match, actions)

        self.logger.info("[Switch %016x] connected – table-miss rule installed",
                         datapath.id)

    @set_ev_cls(ofp_event.EventOFPStateChange,
                [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        """Track connected/disconnected switches."""
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
            self.flow_stats[datapath.id]    = {}
            self.switch_totals[datapath.id] = {"packets": 0, "bytes": 0}
            self.logger.info("[Switch %016x] registered for monitoring",
                             datapath.id)
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(datapath.id, None)
            self.logger.info("[Switch %016x] disconnected", datapath.id)

    # ─────────────────────────────────────────────
    #  Packet-in handler (learning switch logic)
    # ─────────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg      = ev.msg
        datapath = msg.datapath
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser
        in_port  = msg.match["in_port"]

        pkt     = packet.Packet(msg.data)
        eth_pkt = pkt.get_protocols(ethernet.ethernet)[0]
        dst     = eth_pkt.dst
        src     = eth_pkt.src
        dpid    = datapath.id

        # Learn the source MAC
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        # Determine output port
        out_port = (self.mac_to_port[dpid][dst]
                    if dst in self.mac_to_port[dpid]
                    else ofproto.OFPP_FLOOD)

        actions = [parser.OFPActionOutput(out_port)]

        # Install a flow rule if we know the destination
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            # idle_timeout=10 so short-lived flows age out and stats are visible
            self._add_flow(datapath, 1, match, actions,
                           idle_timeout=10, hard_timeout=30)

        # Forward the buffered packet
        data = None if msg.buffer_id != ofproto.OFP_NO_BUFFER else msg.data
        out  = parser.OFPPacketOut(datapath=datapath,
                                   buffer_id=msg.buffer_id,
                                   in_port=in_port,
                                   actions=actions,
                                   data=data)
        datapath.send_msg(out)

    # ─────────────────────────────────────────────
    #  Flow statistics reply handler
    # ─────────────────────────────────────────────

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        """Parse flow stats and update internal tables."""
        body     = ev.msg.body
        datapath = ev.msg.datapath
        dpid     = datapath.id

        total_pkts  = 0
        total_bytes = 0

        self.logger.info("")
        self.logger.info("  ┌─ Flow Stats  Switch %016x  ─────────────────────────────┐", dpid)
        self.logger.info("  │  %-5s  %-17s  %-17s  %10s  %12s  │",
                         "Port", "Src MAC", "Dst MAC", "Packets", "Bytes")
        self.logger.info("  │  %s  │", "─" * 68)

        for stat in sorted(body, key=lambda s: s.packet_count, reverse=True):
            # Skip table-miss entry (priority 0, no match fields)
            if stat.priority == 0:
                continue

            match    = stat.match
            in_port  = match.get("in_port", "?")
            eth_src  = match.get("eth_src",  "??:??:??:??:??:??")
            eth_dst  = match.get("eth_dst",  "??:??:??:??:??:??")
            pkts     = stat.packet_count
            byts     = stat.byte_count
            duration = stat.duration_sec + stat.duration_nsec / 1e9

            key = (in_port, eth_src, eth_dst)
            self.flow_stats[dpid][key] = {
                "packets":  pkts,
                "bytes":    byts,
                "duration": duration,
            }

            total_pkts  += pkts
            total_bytes += byts

            self.logger.info("  │  %-5s  %-17s  %-17s  %10d  %12d  │",
                             in_port, eth_src, eth_dst, pkts, byts)

        self.logger.info("  │  %s  │", "─" * 68)
        self.logger.info("  │  TOTAL  %-17s  %-17s  %10d  %12d  │",
                         "", "", total_pkts, total_bytes)
        self.logger.info("  └─────────────────────────────────────────────────────────────┘")

        self.switch_totals[dpid] = {"packets": total_pkts, "bytes": total_bytes}

    # ─────────────────────────────────────────────
    #  Background monitoring loop
    # ─────────────────────────────────────────────

    def _monitor_loop(self):
        """Periodically request stats; generate report every REPORT_INTERVAL."""
        last_report = time.time()

        while True:
            hub.sleep(STATS_POLL_INTERVAL)
            self._request_stats_all()

            if time.time() - last_report >= REPORT_INTERVAL:
                self._generate_report()
                last_report = time.time()

    def _request_stats_all(self):
        """Send OFPFlowStatsRequest to every known switch."""
        for dp in self.datapaths.values():
            self._request_flow_stats(dp)

    def _request_flow_stats(self, datapath):
        """Request all flow entries from a single switch."""
        parser  = datapath.ofproto_parser
        ofproto = datapath.ofproto
        req = parser.OFPFlowStatsRequest(datapath,
                                         flags=0,
                                         table_id=ofproto.OFPTT_ALL,
                                         out_port=ofproto.OFPP_ANY,
                                         out_group=ofproto.OFPG_ANY,
                                         match=parser.OFPMatch())
        datapath.send_msg(req)

    # ─────────────────────────────────────────────
    #  Report generator
    # ─────────────────────────────────────────────

    def _generate_report(self):
        """Write a summary report to REPORT_FILE and log a banner."""
        self.report_count += 1
        now  = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sep  = "=" * 64

        lines = [
            sep,
            f"  TRAFFIC MONITORING REPORT  #{self.report_count}",
            f"  Generated: {now}",
            sep,
            "",
        ]

        if not self.datapaths:
            lines.append("  No switches currently connected.")
        else:
            for dpid, totals in self.switch_totals.items():
                lines.append(f"  Switch DPID : {dpid:016x}")
                lines.append(f"  Total Packets : {totals['packets']:,}")
                lines.append(f"  Total Bytes   : {totals['bytes']:,}  "
                             f"({totals['bytes'] / 1024:.2f} KB)")
                lines.append("")
                lines.append(f"  {'Port':<6}  {'Src MAC':<17}  {'Dst MAC':<17}"
                             f"  {'Pkts':>10}  {'Bytes':>12}  {'Dur(s)':>8}")
                lines.append("  " + "-" * 76)

                flows = self.flow_stats.get(dpid, {})
                if flows:
                    for (port, src, dst), s in sorted(
                            flows.items(),
                            key=lambda x: x[1]["bytes"],
                            reverse=True):
                        lines.append(
                            f"  {str(port):<6}  {src:<17}  {dst:<17}"
                            f"  {s['packets']:>10,}  {s['bytes']:>12,}"
                            f"  {s['duration']:>8.2f}"
                        )
                else:
                    lines.append("  (no flows yet)")

                lines.append("")

        lines.append(sep)
        report_text = "\n".join(lines)

        # Write to file
        try:
            with open(REPORT_FILE, "a") as f:
                f.write(report_text + "\n\n")
        except IOError as e:
            self.logger.warning("Could not write report file: %s", e)

        # Print to controller log
        for line in lines:
            self.logger.info(line)

        self.logger.info("  [Report saved to %s]", REPORT_FILE)

    # ─────────────────────────────────────────────
    #  Helper: install flow rule
    # ─────────────────────────────────────────────

    def _add_flow(self, datapath, priority, match, actions,
                  idle_timeout=0, hard_timeout=0):
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS, actions)]

        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst,
            idle_timeout=idle_timeout,
            hard_timeout=hard_timeout,
        )
        datapath.send_msg(mod)
