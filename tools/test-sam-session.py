#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host tests for the SAM link bring-up models: tools/sam_session.py, sam_transcript.py, sam_hid_desc.py, geni_uart_plan.py.

    python3 tools/test-sam-session.py

What a pass proves: the models agree with the facts read in the pinned public sources named in each module (frame and
request rules, the bring-up order, the engine-clock arithmetic), and each fault the tests inject is handled as the written
policy says (a request fails closed after the documented tries, a lying descriptor is refused, a noisy line cannot fill the
transmitter). What it does not prove: anything about a real controller or about the unit (UNKNOWN until the first capture,
docs/SP11-INPUT-STUDY.md section 5). The vectors are computed, not captured. No hardware, no network, no files outside memory.
"""
import dataclasses
import random
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import geni_uart_plan as gp  # noqa: E402
import sam_frames as fr  # noqa: E402
import sam_hid_desc as hd  # noqa: E402
import sam_session as ss  # noqa: E402
import sam_transcript as st  # noqa: E402

KBD = hd.boot_keyboard(1)
PTP = hd.ptp_touchpad()


def make(faults=None, **kw):
    devices = [ss.HidDevice(1, 0x045E, 0x0C01, 1, KBD), ss.HidDevice(3, 0x045E, 0x0C02, 1, PTP)]
    ec = ss.FakeEc(devices, faults=faults, **kw)
    host = ss.HostStack()
    return host, ec, ss.Wire(host, ec)


def survey_words(dfs=False, levels=None, qup=0x20050000, div=0, gpll0_l=31, alpha=0x4000, user=1 << 10, mode=(1 << 31) | 1,
                 gpll0_vote=1, dfs_level=0):
    """A synthetic read-only snapshot (computed, never captured): GPLL0 at 600 MHz, the first eight table rows as DFS levels."""
    words = {'cbcr': 0x80004000, 'vote': 0x810223D0, 'gpll0_vote': gpll0_vote, 'div': div, 'dfs': (1 | dfs_level << 1) if dfs else 0,
             'cmd': 0, 'cfg': 0, 'm': 0, 'n': 0, 'd': 0, 'gpll0_mode': mode, 'gpll0_opmode': 0, 'gpll0_status': 0,
             'gpll0_l': gpll0_l, 'gpll0_alpha': alpha, 'gpll0_user': user, 'qup_ver': qup}
    for i, row in enumerate(levels or gp.S2_TABLE[:8]):
        e = gp.rcg2_encode(row)
        words[f'lv_cfg{i}'], words[f'lv_m{i}'], words[f'lv_n{i}'] = e['cfg'], e['m'], e['n']
    return words


ROW_128M = (128_000_000, gp.SRC_GPLL0_MAIN, 1, 16, 75)      # 600 MHz x 16/75: 64 MHz x 2 for a clock with an in-engine /2


def full_plan():
    plan = gp.build_clock_plan(survey_words())
    return plan, plan.rows + gp.uart_rows(plan, 16)


class GeniPlan(unittest.TestCase):
    def test_every_table_row_decodes_to_its_frequency(self):
        # With the source PLL at 600 MHz every pinned row must decode back to its listed rate: the check that ties the
        # source table, the encoder and the independent decoder together.
        for row in gp.S2_TABLE:
            words = gp.rcg2_encode(row)
            self.assertEqual(gp.rcg2_decode_hz(words), row[0], row)

    def test_64mhz_words(self):
        w = gp.rcg2_encode((64_000_000, gp.SRC_GPLL0_EVEN, 1, 16, 75))
        self.assertEqual((w['m'], w['n'], w['d'], w['cfg']), (16, 0xFFC4, 0xFFB4, 0x2601))

    def test_baud_plans(self):
        self.assertEqual(gp.baud_plan(4_000_000, gp.QUP_SE_VERSION_2_5)['clk_hz'], 64_000_000)
        self.assertTrue(gp.baud_plan(4_000_000, gp.QUP_SE_VERSION_2_5)['exact'])
        # Below QUP 2.5 the sampling doubles and the table has no 128 MHz entry: 4 Mbps is unreachable, which is why the
        # wrapper's version word is read before anything is written.
        old = gp.baud_plan(4_000_000, 0x20040000)
        self.assertFalse(old['exact'])
        self.assertEqual(old['baud'], 3_125_000)
        self.assertTrue(gp.baud_plan(115_200, gp.QUP_SE_VERSION_2_5)['exact'])
        perf = gp.perf_table([r[0] for r in gp.S2_TABLE])
        self.assertEqual((len(perf), perf[6]), (11, 64_000_000))
        self.assertEqual(gp.perf_table([5, 10, 0, 20]), [5, 10])                  # a zero row ends the table
        self.assertEqual(gp.perf_table([5, 10, 10, 20]), [5, 10, 20])             # a duplicate row is skipped by the round-up
        self.assertEqual(gp.perf_table([5, 10]), [5, 10])                         # the walk ends when the answer repeats

    def test_the_dfs_flag_decides_the_plan(self):
        plain = gp.build_clock_plan(survey_words())
        self.assertEqual((plain.mode, plain.clk_sel, plain.div), ('plain', 6, 1))
        self.assertEqual([s.name for s in plain.steps],
                         ['s2_vote', 'cbcr_on', 'rcg_m', 'rcg_n', 'rcg_d', 'rcg_cfg', 'rcg_update', 'rcg_updated'])
        dfs = gp.build_clock_plan(survey_words(dfs=True, dfs_level=3))
        self.assertEqual((dfs.mode, dfs.clk_sel), ('dfs', 6))
        self.assertEqual([s.name for s in dfs.steps], ['s2_vote', 'cbcr_on'])       # no RCG row: Linux never writes a DFS RCG
        self.assertFalse(any(r.name.startswith('rcg') for r in dfs.rows))

    def test_clock_rows_refuse_a_dfs_rcg(self):
        self.assertEqual(len(gp.clock_rows(64_000_000, 0)), 6)
        for flag in (1, 0x1F, None):                                                # set, set with a level, never read
            with self.assertRaises(gp.PlanError, msg=str(flag)):
                gp.clock_rows(64_000_000, flag)

    def test_the_selector_is_the_level_that_makes_the_rate(self):
        # The static plain-table index (6) would be wrong here: the 64 MHz level is number 3 in this unit's (synthetic) table.
        levels = [gp.S2_TABLE[i] for i in (0, 1, 2, 6, 7, 8, 9, 10)]
        self.assertEqual(gp.build_clock_plan(survey_words(dfs=True, levels=levels)).clk_sel, 3)
        # A 128 MHz level with the in-engine divider at 2 makes the same 64 MHz.
        levels = list(gp.S2_TABLE[:5]) + [ROW_128M] + list(gp.S2_TABLE[9:11])
        plan = gp.build_clock_plan(survey_words(dfs=True, levels=levels))
        self.assertEqual((plan.clk_sel, plan.div), (5, 2))
        self.assertEqual(plan.baud['baud'], 4_000_000)

    def test_a_dfs_table_that_cannot_make_the_baud_stops(self):
        no_64 = list(gp.S2_TABLE[:6]) + list(gp.S2_TABLE[7:9])                      # 48 and 80 MHz, no 64
        descending = list(reversed(gp.S2_TABLE[:8]))
        for kw in (dict(levels=no_64), dict(levels=descending), dict(dfs_level=9)):
            with self.assertRaises(gp.PlanError, msg=str(kw)):
                gp.build_clock_plan(survey_words(dfs=True, **kw))

    def test_stop_conditions(self):
        for kw in (dict(div=1), dict(qup=0x20040000), dict(mode=1), dict(gpll0_l=30), dict(alpha=0), dict(user=0), dict(gpll0_vote=0)):
            with self.assertRaises(gp.PlanError, msg=str(kw)):
                gp.build_clock_plan(survey_words(**kw))
        for name in gp.IDENTITY_WORDS:
            words = survey_words()
            words[name] = gp.FULL
            with self.assertRaises(gp.PlanError, msg=name):
                gp.build_clock_plan(words)
        words = survey_words()
        del words['dfs']
        with self.assertRaises(gp.PlanError):
            gp.build_clock_plan(words)

    def test_the_snapshot_reads_what_the_plan_decides_on(self):
        names = {r[0] for r in gp.SURVEY_READS}
        self.assertTrue({'dfs', 'div', 'gpll0_alpha', 'qup_ver', 'cbcr', 'vote'} <= names)
        self.assertTrue({f'lv_{k}{i}' for k in ('cfg', 'm', 'n') for i in range(8)} <= names)
        addrs = {off for _, block, off, _ in gp.SURVEY_READS if block == 'gcc'}
        self.assertIn(0x4229C, addrs)
        self.assertIn(0x422A4, addrs)
        self.assertIn(0x422E4, addrs)
        self.assertIn(0x42324, addrs)
        engine = {off for _, off in gp.ENGINE_READS}
        self.assertTrue({0x2AC, 0x22C, 0x64, 0x40, 0x68, 0xE28} <= engine)
        self.assertFalse(any(0x700 <= off < 0x800 for off in engine))              # a receive read pops bytes

    def test_uart_rows_take_their_selector_and_order_from_the_plan(self):
        plan, rows = full_plan()
        uart = rows[len(plan.rows):]
        names = [r.name for r in uart]
        self.assertEqual({r.name: r.value for r in uart}['clk_sel'], plan.clk_sel)
        self.assertLess(names.index('m_irq_clear'), names.index('cgc_ctrl'))         # Linux clears status before IO init
        self.assertLess(names.index('rx_pack0'), names.index('m_irq_clear'))
        self.assertEqual(names[-1], 'start_read')
        self.assertTrue({'manual_rfr', 'loopback'} <= set(names))
        self.assertFalse({0x614, 0x644, 0xE1C} & {r.offset for r in uart})           # interrupt enables stay as found
        kinds = {r.name: r.kind for r in uart}
        self.assertEqual((kinds['m_irq_clear'], kinds['s_irq_clear'], kinds['start_read'], kinds['rx_stale']),
                         ('w1c', 'w1c', 'cmd', 'rmw'))
        self.assertEqual({r.name: r.value for r in uart}['rx_rfr_wm'], 14)
        with self.assertRaises(gp.PlanError):
            gp.uart_rows(plan, 2)

    def test_rows_pass_and_weakened_rows_fail(self):
        plan, rows = full_plan()
        self.assertTrue(gp.verify_rows(rows))
        r = {row.name: row for row in rows}
        weak = [
            dataclasses.replace(r['rcg_cfg'], mask=gp.FULL),                              # a wider mask on the RCG config
            dataclasses.replace(r['rcg_m'], mask=gp.FULL),
            dataclasses.replace(r['s2_vote'], mask=gp.FULL, value=gp.VOTE_S2_BIT),        # clobbers other clocks' votes
            dataclasses.replace(r['rcg_update'], kind='rmw'),
            dataclasses.replace(r['rcg_update'], mask=0x3, value=0x3),                    # CMD_ROOT_EN with the update
            dataclasses.replace(r['clk_sel'], mask=gp.FULL),
            dataclasses.replace(r['tx_cfg'], mask=gp.FULL),
            dataclasses.replace(r['start_read'], value=0x2 << 27),                        # another command opcode
            dataclasses.replace(r['m_irq_clear'], value=0),
            dataclasses.replace(r['rx_stale'], kind='w1c'),
            gp.Row('divider', 'gcc', gp.S2_DIV, 0xF, 1, '', ''),                          # the firmware-owned divider
            gp.Row('m_cmd', 'se2', 0x600, gp.FULL, 1 << 27, '', '', 'cmd'),               # the primary command register
            gp.Row('m_abort', 'se2', 0x604, gp.FULL, 2, '', '', 'cmd'),
            gp.Row('s_abort', 'se2', 0x634, gp.FULL, 2, '', '', 'cmd'),
            gp.Row('tx_fifo', 'se2', 0x700, gp.FULL, 0, '', ''), gp.Row('rx_fifo', 'se2', 0x780, gp.FULL, 0, '', ''),
            gp.Row('fifo_tail', 'se2', 0x7F0, gp.FULL, 0, '', ''),                        # the end of the port range
            gp.Row('m_irq_en', 'se2', 0x614, gp.FULL, 0, '', ''),
            gp.Row('far', 'se2', 0x2004, 1, 1, '', ''), gp.Row('range', 'se2', 0x294, 0xFF, 0x1FF, '', ''),
            gp.Row('align', 'se2', 0x295, 0xFF, 1, '', ''), gp.Row('other_clock', 'gcc', 0x23008, 1, 0, '', ''),
            gp.Row('gpll0', 'gcc', 0x0, 1, 0, '', ''), gp.Row('gpll0_vote', 'gcc', gp.VOTE_GPLL0, 1, 0, '', ''),
        ]
        for row in weak:
            with self.assertRaises(gp.PlanError, msg=row.name):
                gp.verify_rows([row])
        with self.assertRaises(gp.PlanError):
            gp.verify_rows(rows + [rows[0]])
        with self.assertRaises(gp.PlanError):
            gp.clock_rows(128_000_000, 0)

    def test_the_allow_list_is_exact(self):
        plan, rows = full_plan()
        allow = gp.allowlist(rows)
        self.assertTrue(gp.verify_rows(rows, allow))
        # A row that is a reviewed shape but not the reviewed value is refused by the exact list (and not by the shape check).
        changed = [dataclasses.replace(r, value=r.value ^ 1) if r.name == 'rx_stale' else r for r in rows]
        self.assertTrue(gp.verify_rows(changed))
        with self.assertRaises(gp.PlanError):
            gp.verify_rows(changed, allow)
        with self.assertRaises(gp.PlanError):
            gp.verify_rows(rows[:-1] + [dataclasses.replace(rows[-1], value=gp.UART_START_READ)], frozenset(gp.allowlist(rows[:-1])))
        self.assertEqual(gp.rows_digest(rows), gp.rows_digest(list(rows)))
        self.assertNotEqual(gp.rows_digest(rows), gp.rows_digest(changed))

    def test_the_undo_is_complete(self):
        # Clearing the vote undoes only the gate: the RCG words, the engine's words and the receive command stay unless restored.
        for mode_words in (survey_words(), survey_words(dfs=True)):
            plan = gp.build_clock_plan(mode_words)
            rows = plan.rows + gp.uart_rows(plan, 16)
            recorded = {r.name: 0 for r in rows}
            recorded['vote'] = 0x810223D0
            steps = gp.undo_steps(plan, rows, recorded, started_rx=True)
            self.assertTrue(gp.verify_undo(plan, rows, steps, recorded, started_rx=True))
            names = [s.name for s in steps]
            self.assertEqual([n for n in names if n != 'branch_off_delay'][-1], 'vote_clear')
            self.assertEqual(('rcg_m_undo' in names, 'rcg_updated_undo' in names), (plan.mode == 'plain',) * 2)
            self.assertLess(names.index('rx_cancel'), names.index('rx_drain'))
            self.assertLess(names.index('rx_idle'), names.index('stop_tx_undo'))
            vote = [s for s in steps if s.name == 'vote_clear'][0].row
            self.assertEqual((vote.mask, vote.value), (gp.VOTE_S2_BIT, 0))
            # a vote someone else already held is not dropped
            held = dict(recorded, vote=0x810223D0 | gp.VOTE_S2_BIT)
            held_steps = gp.undo_steps(plan, rows, held)
            self.assertNotIn('vote_clear', [s.name for s in held_steps])
            self.assertTrue(gp.verify_undo(plan, rows, held_steps, held))
            with self.assertRaises(gp.PlanError):                                  # ... but one we set must be dropped
                gp.verify_undo(plan, rows, held_steps, recorded)
            for omit in ('vote_clear', 'stop_tx_undo', 'rx_cancel', 'rx_drain') + (('rcg_cfg_undo', 'rcg_update_undo') if plan.mode == 'plain' else ()):
                partial = [s for s in steps if s.name != omit]
                with self.assertRaises(gp.PlanError, msg=omit):
                    gp.verify_undo(plan, rows, partial, recorded, started_rx=True)
            reordered = [s for s in steps if s.name not in ('vote_clear', 'branch_off_delay')]
            reordered.insert(3, [s for s in steps if s.name == 'vote_clear'][0])
            with self.assertRaises(gp.PlanError):
                gp.verify_undo(plan, rows, reordered, recorded, started_rx=True)
        self.assertGreaterEqual(len(gp.IRREVERSIBLE), 5)

    def test_engine_gate(self):
        good = {'fw': 0x0200, 'ifd': 0, 'status': 0, 'hw0': 16 << 16, 'hw1': 16 << 16}
        self.assertEqual(gp.engine_stops(good, 0x20050000), [])
        for change, text in (({'fw': 0xFF00}, 'no firmware'), ({'fw': 0x0900}, 'not the UART'), ({'ifd': 1}, 'IF_DISABLE'),
                             ({'status': 1}, 'command active'), ({'status': 1 << 12}, 'command active'), ({'hw1': 0}, 'implausible'),
                             ({'ifd': gp.FULL}, 'all ones')):
            stops = gp.engine_stops(dict(good, **change), 0x20050000)
            self.assertTrue(any(text in s for s in stops), (change, stops))
        self.assertEqual(gp.decode_hw_params(16 << 16, 32 << 16 | 32 << 24, 0x20050000)['rx_depth'], 32)
        self.assertEqual(gp.decode_hw_params(0, 0x00800000, 0x300A0000)['rx_depth'], 128)      # 8 bits wide from QUP 3.10

    def test_packing_and_decoders(self):
        self.assertEqual(gp.packing_words(), (0x0004380E, 0x000C3E0E))
        self.assertEqual(gp.decode_qup_version(0x20050000), {'major': 2, 'minor': 5, 'step': 0, 'sampling': 16})
        self.assertTrue(gp.decode_fw_revision(0x0200)['uart'])
        self.assertTrue(gp.decode_fw_revision(0xFF00)['no_firmware'])
        self.assertEqual(gp.decode_fifo_status(0x80000000 | (3 << 28) | 5)['bytes'], 4 * 4 + 3)
        self.assertEqual(gp.decode_fifo_status(0)['bytes'], 0)
        self.assertEqual(gp.fifo_fill_us(), 160)
        self.assertTrue(gp.decode_cbcr(0x80004000)['off'])
        pll = gp.decode_gpll0((1 << 31) | 1, 31, 0x4000, 1 << 10)
        self.assertEqual((pll['pll_hz'], pll['even_hz'], pll['locked']), (600_000_000, 300_000_000, True))


class Session(unittest.TestCase):
    def test_bring_up_order_state_and_ids(self):
        host, ec, wire = make()
        res = ss.bring_up(wire)
        self.assertEqual(res.steps, ['fw-version', 'd0-entry', 'display-on', 'kip-events', 'kip-state',
                                     'hid1-hid-descriptor', 'hid1-attributes', 'hid1-report-descriptor', 'hid1-events',
                                     'hid3-hid-descriptor', 'hid3-attributes', 'hid3-report-descriptor', 'hid3-events'])
        self.assertEqual(res.devices[1]['report_descriptor'], KBD)
        self.assertEqual(res.devices[3]['report_descriptor'], PTP)
        self.assertEqual((res.devices[1]['vendor'], res.devices[1]['product']), (0x045E, 0x0C01))
        self.assertTrue(ec.d0 and not ec.display_off)
        self.assertEqual(sorted(ec.reg_events), [(0x15, 1), (0x15, 3)])
        self.assertEqual(ec.sam_events, {0x0E: (1, 0x0E)})
        self.assertEqual(ec.reg_events[(0x15, 1)], (0, 0x15))          # HID events are enabled with flags 0 (surface_hid.c:216)
        rqids = [c[3] for c in []] or [ss.command_fields(p)[4] for p in self._tx_commands(wire)]
        self.assertEqual(rqids[:3], [0, 39, 40])
        self.assertFalse(any(1 <= r <= 38 for r in rqids))
        self.assertEqual(len(set(rqids)), len(rqids))
        rep = st.analyze(st.from_wire(wire.log))
        self.assertEqual(rep.violations, [])

    @staticmethod
    def _tx_commands(wire):
        scan = ss.FrameScanner()
        out = []
        for _, d, data in wire.log:
            if d == 'tx':
                out += [it[3] for it in scan.feed(data, 0) if it[0] == 'frame' and it[1] == fr.TYPE_DATA_SEQ]
        return out

    def test_input_event_after_enable(self):
        host, ec, wire = make()
        ss.bring_up(wire)
        report = bytes((1, 0, 0, 4, 0, 0, 0, 0, 0))
        ec.press(1, report, wire.t_us)
        wire.advance(5000)
        self.assertEqual([(e.tc, e.sid, e.iid, e.data) for e in host.events], [(0x15, 2, 1, report)])
        self.assertIsNone(ec.pending)

    def test_hid_events_are_enabled_unsequenced(self):
        # Linux enables HID input events with flags 0 (surface_hid.c:216) and only the KIP hub asks for sequenced ones
        # (surface_aggregator_hub.c:179): the wire bytes are pinned, and a report arrives as DATA_NSQ, with no ACK pacing.
        host, ec, wire = make()
        ss.bring_up(wire)
        enables = [c[4] for c in ec.log if c[0] == ss.TC_REG and c[3] == ss.CID_REG_EVENT_ENABLE]
        self.assertEqual(enables, [bytes.fromhex('1500150001'), bytes.fromhex('1500150003')])
        self.assertEqual([c[4] for c in ec.log if c[0] == ss.TC_SAM and c[3] == ss.CID_SAM_EVENT_ENABLE], [bytes.fromhex('0e010e0000')])
        before = len(wire.log)
        for key in (4, 5, 6):
            ec.press(1, bytes((1, 0, 0, key, 0, 0, 0, 0, 0)), wire.t_us)
        wire.step(1000)
        self.assertEqual([e.data[3] for e in host.events], [4, 5, 6])              # three reports in one tick
        rep = st.analyze(st.from_wire(wire.log[before:]))
        self.assertEqual([e['sequenced'] for e in rep.events], [False] * 3)
        self.assertEqual(host.stats['ack_unmatched'], 0)
        self.assertIsNone(ec.pending)
        ss.tear_down(wire)
        disables = [c[4] for c in ec.log if c[0] == ss.TC_REG and c[3] == ss.CID_REG_EVENT_DISABLE]
        self.assertEqual(disables, enables)

    def test_events_before_enable(self):
        host, ec, wire = make()
        ec.press(1, b'\x01', 0)
        wire.advance(5000)
        self.assertEqual(len(host.events), 0)
        self.assertEqual(ec.stats['event_not_enabled'], 1)
        # The pinned docs say HID input events seem exempt from enabling (internal.rst, "Event Registration"): the
        # passive-capture case. The model supports both so the first capture can say which is true.
        host, ec, wire = make(hid_events_without_enable=True)
        ec.press(1, b'\x01', 0)
        wire.advance(5000)
        self.assertEqual(len(host.events), 1)
        self.assertEqual(ec.stats['event_not_enabled'], 0)

    def test_display_off_holds_events_until_display_on(self):
        host, ec, wire = make()
        ss.bring_up(wire)
        ss.do_request(wire, ss.TC_SAM, ss.TID_SAM, 0, ss.CID_DISPLAY_OFF)
        ec.press(1, b'\x01\x02', wire.t_us)
        wire.advance(5000)
        self.assertTrue(ec.wake_line)
        self.assertEqual(len(host.events), 0)
        ss.do_request(wire, ss.TC_SAM, ss.TID_SAM, 0, ss.CID_DISPLAY_ON)
        wire.advance(5000)
        self.assertFalse(ec.wake_line)
        self.assertEqual([e.data for e in host.events], [b'\x01\x02'])

    def test_lost_request_is_resent_and_executed_once(self):
        host, ec, wire = make(faults=ss.Faults(deaf=True))
        req = host.submit(ss.TC_SAM, ss.TID_SAM, 0, ss.CID_FW_VERSION, b'', True, wire.t_us)
        wire.advance(1_100_000)
        self.assertEqual(host.stats['resent'], 1)
        ec.faults.deaf = False
        self.assertTrue(wire.run_until(lambda: req.finished, 2_000_000))
        self.assertEqual(req.state, 'done')
        self.assertEqual(len([c for c in ec.log if c[3] == ss.CID_FW_VERSION]), 1)

    def test_dead_link_fails_after_three_transmissions(self):
        host, ec, wire = make(faults=ss.Faults(deaf=True))
        with self.assertRaises(ss.SamError) as ctx:
            ss.bring_up(wire)
        self.assertEqual(ctx.exception.step, 'fw-version')
        sent = [p for p in self._tx_commands(wire)]
        self.assertEqual(len(sent), ss.PACKET_TRIES)
        self.assertEqual(len(set(sent)), 1)                       # identical frames: same sequence id and request id
        self.assertEqual(ec.log, [])

    def test_acked_but_unanswered_request_times_out_without_resend(self):
        host, ec, wire = make(faults=ss.Faults(silent=True))
        with self.assertRaises(ss.SamError):
            ss.bring_up(wire)
        self.assertEqual(len(self._tx_commands(wire)), 1)
        self.assertEqual(host.stats['request_failed_request-timeout'], 1)

    def test_corrupt_response_is_nakked_and_resent(self):
        host, ec, wire = make()
        state = {'done': False}

        def damage(t, data):
            scan = ss.FrameScanner()
            frames = [it for it in scan.feed(data, t) if it[0] == 'frame']
            if not state['done'] and any(f[1] == fr.TYPE_DATA_SEQ for f in frames):
                state['done'] = True
                return data[:-1] + bytes((data[-1] ^ 0x80,))          # break the payload CRC of the first response
            return data
        wire.hooks['rx'] = damage
        req = host.submit(ss.TC_SAM, ss.TID_SAM, 0, ss.CID_FW_VERSION, b'', True, wire.t_us)
        self.assertTrue(wire.run_until(lambda: req.finished, 3_000_000))
        self.assertEqual(req.state, 'done')
        self.assertGreaterEqual(host.stats['nak_sent'], 1)
        self.assertEqual(ec.stats['resent'], 1)

    def test_duplicated_frames_are_processed_once(self):
        host, ec, wire = make(faults=ss.Faults(duplicate_tx=40))
        ss.bring_up(wire)
        self.assertEqual(sorted(ec.reg_events), [(0x15, 1), (0x15, 3)])
        self.assertGreater(host.stats['repeat_ignored'], 0)

    def test_foreign_target_and_unmatched_responses_are_ignored(self):
        for faults, counter in ((ss.Faults(wrong_tid=1), 'not_for_us'), (ss.Faults(wrong_rqid=1), 'response_unmatched')):
            host, ec, wire = make(faults=faults)
            req = host.submit(ss.TC_SAM, ss.TID_SAM, 0, ss.CID_FW_VERSION, b'', True, wire.t_us)
            wire.run_until(lambda: req.finished, 3_500_000)
            self.assertEqual(req.state, 'failed')
            self.assertEqual(host.stats[counter], 1, counter)

    def test_lying_descriptors_are_refused(self):
        for faults, text in ((ss.Faults(descriptor_slice_over=500), 'lies'), (ss.Faults(descriptor_short_total=10), 'ended')):
            host, ec, wire = make(faults=faults)
            with self.assertRaises(ss.SamError) as ctx:
                ss.bring_up(wire)
            self.assertIn(text, str(ctx.exception))
            self.assertEqual(sorted(ec.reg_events), [])                   # no event source was enabled after the failure

    def test_a_slice_that_does_not_advance_fails_closed(self):
        # A controller that answers length 0, or the same offset, forever would hold Linux's own loop (surface_hid.c:77-102).
        for fault in ('descriptor_stall', 'descriptor_rewind'):
            host, ec, wire = make(faults=ss.Faults(**{fault: True}))
            seen = {'n': 0}

            def runaway(t, data, seen=seen):
                seen['n'] += 1
                if seen['n'] > 80:
                    raise AssertionError('the descriptor read does not stop')
                return data
            wire.hooks['tx'] = runaway
            with self.assertRaises(ss.SamError) as ctx:
                ss.bring_up(wire)
            self.assertIn('does not advance', str(ctx.exception), fault)
            self.assertLess(seen['n'], 80)

    def test_the_descriptor_read_is_bounded_by_what_it_needs(self):
        host, ec, wire = make(max_slice=1)                      # advances, but one byte at a time: 334 bytes need 334 requests
        with self.assertRaises(ss.SamError) as ctx:
            ss.bring_up(wire)
        self.assertIn('does not finish', str(ctx.exception))
        self.assertLess(len(self._tx_commands(wire)), 90)
        host, ec, wire = make()
        ec.devices[1] = ss.HidDevice(1, 0x045E, 0x0C01, 1, bytes(5000))      # a report descriptor longer than any message
        with self.assertRaises(ss.SamError) as ctx:
            ss.bring_up(wire)
        self.assertIn('out of bounds', str(ctx.exception))

    def test_the_slice_bound_counts_the_ack_in_front_of_the_response(self):
        # The controller acknowledges the request (10 bytes) and sends the response right behind it, before the host can
        # poll: what must fit the FIFO is ACK + response = 10 + (10 + 8 + 10 + slice), not the response alone.
        peaks = {}
        for slice_len in (26, 30, ss.SLICE_DATA):
            host, ec, wire = make()
            res = ss.bring_up(wire, slice_len=slice_len)
            self.assertEqual(res.devices[3]['report_descriptor'], PTP)
            peaks[slice_len] = max(len(d) for _, direction, d in wire.log if direction == 'rx')
        self.assertEqual(peaks, {26: 64, 30: 68, 118: 156})

    def test_refusal_stops_the_script(self):
        host, ec, wire = make(faults=ss.Faults(nonzero_status=1))
        with self.assertRaises(ss.SamError) as ctx:
            ss.bring_up(wire)
        self.assertEqual(ctx.exception.step, 'd0-entry')
        self.assertEqual(ec.log[-1][3], ss.CID_D0_ENTRY)

    def test_pacing_invariants(self):
        host, ec, wire = make()
        reqs = [host.submit(ss.TC_SAM, ss.TID_SAM, 0, ss.CID_FW_VERSION, b'', True, 0) for _ in range(8)]
        for _ in range(200):
            wire.step(1000)
            self.assertLessEqual(len(host.inflight), 3)
            self.assertLessEqual(1 if host.pending else 0, 1)
        self.assertTrue(all(r.state == 'done' for r in reqs))

    def test_at_most_three_requests_wait_for_answers(self):
        # A controller that acknowledges but never answers: the fourth request must not even be transmitted.
        host, ec, wire = make(faults=ss.Faults(silent=True))
        for _ in range(8):
            host.submit(ss.TC_SAM, ss.TID_SAM, 0, ss.CID_FW_VERSION, b'', True, 0)
        wire.advance(100_000)
        self.assertEqual(len(self._tx_commands(wire)), 3)
        self.assertEqual(len(host.inflight), 3)

    def test_repeat_filter_window(self):
        host = ss.HostStack()
        def frame(seq):
            return fr.build_command(fr.TYPE_DATA_SEQ, seq, 0x15, 0, 2, 1, 0x15, 0, b'\x01')
        host.feed(b''.join(frame(s) for s in range(9)), 0)         # 0..8: the window now holds 1..8
        self.assertEqual(len(host.events), 9)
        host.feed(frame(1), 1)                                      # inside the window: ignored (but acknowledged)
        host.feed(frame(0), 2)                                      # fell out of the window: taken again
        self.assertEqual(len(host.events), 10)
        self.assertEqual(host.stats['repeat_ignored'], 1)
        acks = [it for it in ss.FrameScanner().feed(host.poll_tx(3), 3) if it[0] == 'frame' and it[1] == fr.TYPE_ACK]
        self.assertEqual(len(acks), 11)

    def test_noise_cannot_fill_the_transmitter(self):
        host = ss.HostStack()
        rng = random.Random(7)
        for ms in range(200):
            host.feed(bytes(rng.randrange(256) for _ in range(64)) + b'\xaa\x55\x00\x00', ms * 1000)
        self.assertLessEqual(host.stats['nak_sent'], 200 * 1000 // ss.NAK_MIN_INTERVAL_US + 1)
        self.assertGreater(host.stats['nak_sent'], 1)

    def test_stalled_frame_is_abandoned(self):
        host = ss.HostStack()
        header = fr.build_frame(fr.TYPE_DATA_SEQ, 1, bytes(4000))[:8]        # a frame that claims 4000 bytes and stops
        host.feed(header, 0)
        host.poll_tx(150_000)
        self.assertEqual(host.stats['junk_stalled'], 1)
        good = fr.build_command(fr.TYPE_DATA_SEQ, 2, 0x15, 0, 2, 1, 0x15, 0, b'\x01')
        host.feed(good, 1_000_000)
        self.assertEqual(len(host.events), 1)

    def test_a_growing_frame_is_not_flushed(self):
        # The timer measures silence, not age: a long frame that keeps arriving, slowly, is still a frame.
        frame = fr.build_command(fr.TYPE_DATA_NSQ, 0, 0x15, 0, 2, 1, 0x15, 0, bytes(range(200)))        # 218 bytes
        for chunk, gap_us in ((1, 2_000), (64, 50_000)):
            host, t = ss.HostStack(), 0
            for i in range(0, len(frame), chunk):
                host.feed(frame[i:i + chunk], t)
                for k in (1, 2):
                    host.poll_tx(t + k * gap_us // 3)
                t += gap_us
            self.assertEqual(host.stats['junk_stalled'], 0, chunk)
            self.assertEqual(len(host.events), 1, chunk)
        host = ss.HostStack()                                                   # and real silence still flushes
        host.feed(frame[:100], 0)
        host.poll_tx(60_000)
        self.assertEqual(host.stats['junk_stalled'], 0)
        host.poll_tx(160_000)
        self.assertEqual(host.stats['junk_stalled'], 1)

    def test_chunking_does_not_change_the_result(self):
        rng = random.Random(3)
        stream = b'\x00\xaa' + b''.join(fr.build_command(fr.TYPE_DATA_SEQ, i, 0x15, 0, 2, 1, 0x15, 0, bytes([i])) for i in range(6))
        reference = ss.FrameScanner()
        want = [i for i in reference.feed(stream, 0) if i[0] == 'frame']
        for _ in range(200):
            scan, got, pos = ss.FrameScanner(), [], 0
            while pos < len(stream):
                n = rng.randint(1, 9)
                got += [i for i in scan.feed(stream[pos:pos + n], 0) if i[0] == 'frame']
                pos += n
            self.assertEqual(got, want)
            self.assertEqual(scan.stats['bad_sync'], reference.stats['bad_sync'])

    def test_tear_down_is_best_effort(self):
        host, ec, wire = make()
        ss.bring_up(wire)
        steps = ss.tear_down(wire)
        self.assertEqual(steps, ['hid1-events-off', 'hid3-events-off', 'kip-events-off', 'display-off', 'd0-exit'])
        self.assertEqual((ec.reg_events, ec.sam_events, ec.d0, ec.display_off), ({}, {}, False, True))
        host, ec, wire = make(faults=ss.Faults(deaf=True))
        self.assertTrue(all(s.endswith('failed') for s in ss.tear_down(wire)))

    def test_fuzzed_wire_never_breaks_invariants(self):
        # Bit flips, dropped and duplicated chunks on both directions; the run may fail (SamError) but never wedge, and the
        # request ids on the wire stay outside the event range.
        failures = 0
        for seed in range(60):
            rng = random.Random(seed)
            host, ec, wire = make()

            def damage(t, data, rng=rng):
                roll = rng.random()
                if roll < 0.03:
                    return b''
                if roll < 0.06:
                    return data + data
                if roll < 0.10:
                    pos = rng.randrange(len(data))
                    return data[:pos] + bytes((data[pos] ^ (1 << rng.randrange(8)),)) + data[pos + 1:]
                return data
            wire.hooks['tx'], wire.hooks['rx'] = damage, damage
            wire.chunk = rng.choice((0, 7, 64))
            try:
                ss.bring_up(wire)
            except ss.SamError:
                failures += 1
            wire.advance(200_000, 5000)
            self.assertLessEqual(len(host.inflight), ss.MAX_PENDING_REQUESTS)
            rep = st.analyze(st.from_wire(wire.log))
            for rq in rep.requests:
                self.assertFalse(1 <= rq['rqid'] <= 38)
        self.assertLess(failures, 60)                          # not every damaged run may fail: retransmission works


class Transcripts(unittest.TestCase):
    def run_model(self):
        host, ec, wire = make()
        ss.bring_up(wire)
        ec.press(1, bytes(9), wire.t_us)
        wire.advance(5000)
        return st.from_wire(wire.log)

    def test_format_round_trip_and_errors(self):
        records = self.run_model()
        text = st.format_transcript(records, ['computed, not captured'])
        self.assertEqual(st.parse_transcript(text), records)
        for bad in ('1 up aa', 'x rx aa', '1 rx zz', '5 rx aa\n4 rx aa', '1 rx', '1 rx  '):
            with self.assertRaises(st.TranscriptError, msg=bad):
                st.parse_transcript(bad)

    def test_clean_run_has_no_violations_and_lists_the_requests(self):
        rep = st.analyze(self.run_model())
        self.assertEqual(rep.violations, [])
        self.assertEqual(len(rep.requests), 15)
        self.assertEqual(len(rep.events), 1)
        self.assertTrue(all(r['answered_after_us'] is not None for r in rep.requests))

    def test_violations_are_reported(self):
        records = self.run_model()
        # cut the first ACK (10 bytes) out of the first host record that begins with one
        kept, cut = [], False
        for r in records:
            if r.direction == 'tx' and not cut and r.data[2] == fr.TYPE_ACK:
                cut = True
                if len(r.data) > 10:
                    kept.append(st.Record(r.t_us, 'tx', r.data[10:]))
                continue
            kept.append(r)
        rep = st.analyze(kept)
        self.assertTrue(any('was unacknowledged' in v for v in rep.violations), rep.violations)
        stray = fr.build_command(fr.TYPE_DATA_SEQ, 99, 0x01, 0, 1, 0, 0x7777, 0x13, b'\x00\x00\x00\x00')
        rep = st.analyze([st.Record(0, 'rx', stray)])
        self.assertTrue(any('not asked' in v for v in rep.violations))
        foreign = fr.build_command(fr.TYPE_DATA_NSQ, 0, 0x15, 3, 2, 1, 0x15, 0, b'\x01')
        rep = st.analyze([st.Record(0, 'rx', foreign)])
        self.assertTrue(any('not the host' in v for v in rep.violations))
        evid = fr.build_command(fr.TYPE_DATA_SEQ, 0, 0x01, 1, 0, 0, 5, 0x13)
        rep = st.analyze([st.Record(0, 'tx', evid)])
        self.assertTrue(any('event id' in v for v in rep.violations))
        rep = st.analyze([st.Record(0, 'rx', b'\x01\x02\x03' + fr.build_ack(0))])
        self.assertEqual(rep.junk['rx'], 1)

    def test_retransmits_are_counted(self):
        frame = fr.build_command(fr.TYPE_DATA_SEQ, 4, 0x15, 0, 2, 1, 0x15, 0, b'\x01')
        rep = st.analyze([st.Record(0, 'rx', frame), st.Record(1_000_000, 'rx', frame), st.Record(2_000_000, 'rx', frame)])
        self.assertEqual(rep.retransmits, 2)
        self.assertTrue(any('never acknowledged' in v for v in rep.violations))

    def test_word_order_detection(self):
        stream = fr.build_command(fr.TYPE_DATA_SEQ, 1, 0x15, 0, 2, 1, 0x15, 0, bytes(range(11)))
        padded = stream + bytes(-len(stream) % 4)
        for order, want in (('little', 'le'), ('big', 'be')):
            words = [int.from_bytes(padded[i:i + 4], order) for i in range(0, len(padded), 4)]
            got, scores = st.best_word_order(words)
            self.assertEqual(got, want, scores)
        # a short last word: only the first `last_valid` bytes of it are data
        short = stream + b'\xde'
        padded_short = short + bytes(-len(short) % 4)
        words = [int.from_bytes(padded_short[i:i + 4], 'little') for i in range(0, len(padded_short), 4)]
        self.assertEqual(st.unpack_words(words, 'le', last_valid=(len(short) % 4) or 4), short)
        self.assertEqual(st.best_word_order([0xDEADBEEF, 0x12345678])[0], 'ambiguous')
        with self.assertRaises(ValueError):
            st.unpack_words([0], 'xx')

    def test_classify(self):
        self.assertEqual(st.classify_capture(b'')['verdict'], 'SILENT')
        self.assertEqual(st.classify_capture(b'\xff' * 50)['verdict'], 'STUCK')
        self.assertEqual(st.classify_capture(bytes(range(3, 60)))['verdict'], 'NOISE')
        self.assertEqual(st.classify_capture(b'\x01\xaa\x55\x04\x00')['verdict'], 'SYN_NO_FRAME')
        frame = fr.build_command(fr.TYPE_DATA_SEQ, 4, 0x15, 0, 2, 1, 0x15, 0, b'\x01')
        result = st.classify_capture(frame * 3)
        self.assertEqual((result['verdict'], result['repeats']), ('FRAMES', 2))


class HidDescriptors(unittest.TestCase):
    def test_fixture_shapes(self):
        lay = hd.parse(hd.boot_keyboard())
        self.assertEqual((lay.report_bytes('input', 0), lay.report_bytes('output', 0)), (8, 1))
        lay = hd.parse(hd.nkro_keyboard())
        self.assertEqual(lay.report_bytes('input', 1), 23)
        p = hd.ptp_layout(hd.parse(PTP))
        self.assertEqual(len(p.fingers), 5)
        self.assertEqual((p.input_mode.report_id, p.contact_count.bit_size), (2, 8))
        self.assertEqual(hd.parse(PTP).report_bytes('input', 1), 30)

    def test_keyboard_model_order_and_refusals(self):
        lay = hd.parse(KBD)
        km = hd.KeyboardModel(lay)
        press = hd.pack(lay, 'input', 1, {(0, 1): 1, (2, 0): 4, (2, 1): 5})
        self.assertEqual(km.translate(press), [(7, 0xE1, 1), (7, 4, 1), (7, 5, 1)])
        self.assertEqual(km.translate(press), [])
        change = hd.pack(lay, 'input', 1, {(0, 0): 1, (2, 1): 5, (2, 2): 6})
        # releases first (modifier, then keys), then presses (modifier, then keys): the A16's order
        self.assertEqual(km.translate(change), [(7, 0xE1, 0), (7, 4, 0), (7, 0xE0, 1), (7, 6, 1)])
        before = (km.held['mods'], km.held['keys'])
        self.assertIsNone(km.translate(hd.pack(lay, 'input', 1, {(2, 0): 1})))           # ErrorRollOver
        self.assertIsNone(km.translate(press + b'\x00'))                                  # wrong length
        self.assertIsNone(km.translate(bytes((9,)) + press[1:]))                          # unknown report id
        self.assertEqual((km.held['mods'], km.held['keys']), before)

    def test_bitmap_and_consumer(self):
        lay = hd.parse(hd.nkro_keyboard())
        km = hd.KeyboardModel(lay)
        report = hd.pack(lay, 'input', 1, {(1, 0): 1, (1, 10): 1})
        self.assertEqual(km.translate(report), [(7, 4, 1), (7, 14, 1)])
        lay = hd.parse(hd.consumer_keys())
        km = hd.KeyboardModel.__new__(hd.KeyboardModel)
        km.layout, km.mods, km.array, km.bitmap, km.ids, km.held, km.consumer_held = lay, {}, {}, {}, {2}, {}, {}
        km.consumer = {2: [f for f in lay.fields if f.kind == 'input']}
        self.assertEqual(km.translate(hd.pack(lay, 'input', 2, {(0, 0): 0xE9})), [(0x0C, 0xE9, 1)])
        self.assertEqual(km.translate(hd.pack(lay, 'input', 2, {(0, 0): 0})), [(0x0C, 0xE9, 0)])

    def test_malformed_descriptors(self):
        good = hd.boot_keyboard()
        cases = {
            'empty': b'', 'truncated': good[:-1] + good[-1:] * 0 + b'\x05', 'reserved': b'\x0c',
            'unclosed': good[:-1], 'unbalanced': good + b'\xc0', 'no field': b'\x05\x01\x09\x06\xa1\x01\xc0',
            'outside': b'\x75\x08\x95\x01\x81\x00', 'no size': b'\x05\x01\x09\x06\xa1\x01\x81\x00\xc0',
            'pop': b'\xb4', 'report id 0': b'\x05\x01\x09\x06\xa1\x01\x85\x00\xc0',
            'mixed': hd.Builder().page(1).usage(6).collection(1).size(8).count(1).input(2).report_id(1).input(2).end().bytes(),
        }
        for name, desc in cases.items():
            with self.assertRaises(hd.DescError, msg=name):
                hd.parse(desc)
        field = b'\x75\x08\x95\x01\x81\x00'
        deep = bytes([0xA1, 0x01] * 33) + field + b'\xc0' * 33                              # one past the depth limit
        with self.assertRaises(hd.DescError):
            hd.parse(deep)
        hd.parse(bytes([0xA1, 0x01] * 32) + field + b'\xc0' * 32)                           # exactly at it is fine
        with self.assertRaises(hd.DescError):
            hd.parse(b'\xa4' * 17)                                                           # push overflow
        with self.assertRaises(hd.DescError):
            hd.parse(bytes(4097))

    def test_declared_sizes_are_bounded(self):
        # A descriptor of a few dozen bytes can declare a report count or a usage range in the billions; the parser must refuse
        # before building anything from it. (65536 is used here: small enough to be harmless on a parser without the bound.)
        def one(count=8, size=1, usages=None, flags=2):
            b = hd.Builder().page(7).usage(6).collection(1).size(size).count(count)
            if usages:
                b.umin(usages[0]).umax(usages[1])
            return b.input(flags).end().bytes()
        for desc in (one(count=1 << 16), one(count=4097), one(usages=(0, 1 << 16)), one(usages=(0, 4096)),
                     one(size=32, count=4096)):
            self.assertLess(len(desc), 40)
            with self.assertRaises(hd.DescError):
                hd.parse(desc)
        hd.parse(one(count=4096))                                           # at the limits: fine
        hd.parse(one(usages=(0, 4095)))
        many = hd.Builder().page(7).usage(6).collection(1)
        for _ in range(40):
            many.size(8).count(125).input(2)                                  # 40 x 1000 bits: a report larger than any message
        with self.assertRaises(hd.DescError):
            hd.parse(many.end().bytes())
        bitmaps = hd.Builder().page(7).usage(6).collection(1).size(1).count(4096)
        for i in range(3):
            bitmaps.umin(0).umax(4095).input(2)                               # 3 x 4096 expanded usages
        with self.assertRaises(hd.DescError):
            hd.parse(bitmaps.end().bytes())
        two = hd.Builder().page(7).usage(6).collection(1).size(1).count(4096)
        for i in range(2):
            two.umin(0).umax(4095).input(2)
        hd.parse(two.end().bytes())

    def test_random_bytes_only_ever_raise_descerror(self):
        rng = random.Random(11)
        for _ in range(3000):
            blob = bytes(rng.randrange(256) for _ in range(rng.randint(1, 80)))
            try:
                hd.parse(blob)
            except hd.DescError:
                pass
        base = bytearray(PTP)
        for _ in range(1500):
            mutated = bytearray(base)
            mutated[rng.randrange(len(mutated))] = rng.randrange(256)
            try:
                lay = hd.parse(bytes(mutated))
                hd.ptp_layout(lay)
            except hd.DescError:
                pass

    def test_descriptors_survive_the_sam_transport(self):
        # 334 bytes need three 118-byte slices; the last slice is short and ends the read.
        host, ec, wire = make()
        res = ss.bring_up(wire)
        self.assertEqual(hd.ptp_layout(hd.parse(res.devices[3]['report_descriptor'])).report_id, 1)
        self.assertEqual(hd.parse(res.devices[1]['report_descriptor']).report_bytes('input', 1), 9)
        slices = [c for c in ec.log if c[0] == ss.TC_HID and c[3] == ss.HID_GET_DESCRIPTOR and c[2] == 3 and c[4][0] == 1]
        self.assertEqual(len(slices), 3)


def unbounded_read_descriptor(wire, iid, entry, total, step, slice_len=ss.SLICE_DATA):
    """The sliced read as v2 had it, and as Linux's surface_hid.c:77-102 has it: the offset and length the controller answers
    are trusted, so a zero-length or rewinding answer repeats the request forever."""
    out = bytearray(total)
    offset, got_end = 0, False
    while not got_end and offset < total:
        payload = bytes((entry,)) + offset.to_bytes(4, 'little') + slice_len.to_bytes(4, 'little') + b'\x00'
        r = ss.do_request(wire, ss.TC_HID, ss.TID_KIP, iid, ss.HID_GET_DESCRIPTOR, payload, step).response
        off, length, end = int.from_bytes(r[1:5], 'little'), int.from_bytes(r[5:9], 'little'), r[9]
        length = min(length, total - off)
        out[off:off + length] = r[10:10 + length]
        offset, got_end = off + length, bool(end)
    return bytes(out)


class Mutants(unittest.TestCase):
    """Each seeded bug must make the test that guards it fail: the tests can fail."""

    @staticmethod
    def fails(case, name):
        result = unittest.TestResult()
        unittest.TestSuite([case(name)]).run(result)
        return not result.wasSuccessful()

    def test_seeded_bugs_are_caught(self):
        from unittest import mock
        cases = [
            (mock.patch.object(ss, 'MAX_PENDING_REQUESTS', 6), Session, 'test_at_most_three_requests_wait_for_answers'),
            (mock.patch.object(ss, 'PACKET_TRIES', 5), Session, 'test_dead_link_fails_after_three_transmissions'),
            (mock.patch.object(ss, 'NAK_MIN_INTERVAL_US', 0), Session, 'test_noise_cannot_fill_the_transmitter'),
            (mock.patch.object(ss, 'SEEN_SEQS', 1), Session, 'test_repeat_filter_window'),
            (mock.patch.object(ss, 'STALL_FLUSH_US', 10 ** 12), Session, 'test_stalled_frame_is_abandoned'),
            (mock.patch.object(ss.fr, 'rqid_next_request', lambda r: (r + 1) & 0xFFFF), Session,
             'test_bring_up_order_state_and_ids'),
            (mock.patch.dict(gp.SRC_HZ, {gp.SRC_GPLL0_EVEN: 299_000_000}), GeniPlan,
             'test_every_table_row_decodes_to_its_frequency'),
            (mock.patch.object(gp, 'CFG_MODE_DUAL_EDGE', 0), GeniPlan, 'test_64mhz_words'),
            (mock.patch.object(hd, 'MAX_DEPTH', 10 ** 6), HidDescriptors, 'test_malformed_descriptors'),
            (mock.patch.object(st, 'ACK_GRACE_US', 10 ** 12), Transcripts, 'test_retransmits_are_counted'),
            # the corrections of the review (C1-C5, C10, C11): each fix must have a test that notices its removal
            (mock.patch.object(ss, 'STALL_FLUSH_US', 1), Session, 'test_a_growing_frame_is_not_flushed'),
            (mock.patch.object(ss, 'HID_EVENT_FLAGS', 1), Session, 'test_hid_events_are_enabled_unsequenced'),
            (mock.patch.object(ss, 'REPORT_DESC_MAX', 10 ** 6), Session, 'test_the_descriptor_read_is_bounded_by_what_it_needs'),
            (mock.patch.object(ss, 'read_descriptor', unbounded_read_descriptor), Session,
             'test_a_slice_that_does_not_advance_fails_closed'),
            (mock.patch.object(ss, 'read_descriptor', unbounded_read_descriptor), Session,
             'test_the_descriptor_read_is_bounded_by_what_it_needs'),
            (mock.patch.multiple(hd, MAX_COUNT=10 ** 9, MAX_FIELD_BITS=10 ** 9, MAX_REPORT_BITS=10 ** 9, MAX_USAGE_RANGE=10 ** 9,
                                 MAX_TOTAL_USAGES=10 ** 9), HidDescriptors, 'test_declared_sizes_are_bounded'),
            (mock.patch.dict(gp.GCC_ALLOWED, {gp.RCG_CFG: (gp.FULL, 'rmw')}), GeniPlan, 'test_rows_pass_and_weakened_rows_fail'),
            (mock.patch.object(gp, 'verify_undo', lambda *a, **k: True), GeniPlan, 'test_the_undo_is_complete'),
            (mock.patch.object(gp, 'decode_dfs_cmd', lambda w: {'dfs': False, 'level': 0}), GeniPlan, 'test_the_dfs_flag_decides_the_plan'),
            (mock.patch.object(gp, 'decode_dfs_cmd', lambda w: {'dfs': False, 'level': 0}), GeniPlan, 'test_clock_rows_refuse_a_dfs_rcg'),
        ]
        for patcher, case, name in cases:
            with self.subTest(name):
                self.assertFalse(self.fails(case, name), 'the unmutated test must pass')
                with patcher:
                    self.assertTrue(self.fails(case, name), 'the seeded bug went unnoticed')


if __name__ == '__main__':
    unittest.main(verbosity=1)
