"""Check the executable night plan's scope, ordering and conditional balanced blocks."""
import importlib.util
from pathlib import Path
import unittest
import datetime as dt
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('night',Path(__file__).with_name('prepare-power-night.py'))
night=importlib.util.module_from_spec(spec)
spec.loader.exec_module(night)


class NightPlanTest(unittest.TestCase):
    def setUp(self): self.legs=night.build_legs('verify')

    def test_screening_is_first_and_hardware_only(self):
        self.assertEqual(['exo-screen']*4,[r['purpose'] for r in self.legs[:4]])
        self.assertEqual(list(night.CODECS),[r['codec'] for r in self.legs[:4]])
        hardware=[r for r in self.legs if r['purpose']!='av1-conditional']
        self.assertEqual(68,len(hardware))
        self.assertTrue(all(r['decoderMode']=='automatic' and r['codec']!='av1' for r in hardware))

    def test_software_only_after_every_hardware_candidate(self):
        software=[r for r in self.legs if r['decoderMode']=='software']
        self.assertEqual(16,len(software))
        self.assertTrue(all(r['ordinal']>68 and r['purpose']=='av1-conditional' and r['codec']=='av1' for r in software))
        self.assertEqual({'exo','libmpv','vlc'},{r['engine'] for r in software})

    def test_each_non_screen_block_is_complete_balanced_and_single_purpose(self):
        groups={}
        for row in self.legs[4:]: groups.setdefault(row['comparisonBlock'],[]).append(row)
        self.assertEqual(20,len(groups))
        for rows in groups.values():
            self.assertEqual([1,2,3,4],[r['plannedOrdinal'] for r in rows])
            order=[r['engine'] for r in rows]
            self.assertEqual(order,order[::-1])
            self.assertEqual(2,len(set(order)))
            self.assertTrue(all(r['plannedOrder']==order for r in rows))
            self.assertEqual(1,len({r['purpose'] for r in rows}))

    def test_labels_are_unique_and_mpv_vlc_get_both_orders_for_every_hardware_codec(self):
        self.assertEqual(84,len({r['label'] for r in self.legs}))
        self.assertEqual(list(range(1,85)),[r['ordinal'] for r in self.legs])
        for codec in night.CODECS:
            rows=[r for r in self.legs if r['codec']==codec and r['purpose']=='matrix']
            self.assertEqual(8,len(rows))
            self.assertEqual(['libmpv','vlc','vlc','libmpv','vlc','libmpv','libmpv','vlc'],[r['engine'] for r in rows])

    def test_updated_build_matrix_does_not_skip_exo_using_old_power(self):
        rows = night.build_legs('updated', ('avc',), full_matrix=True)
        self.assertEqual(32, len(rows))
        self.assertFalse(any(row['purpose'] in ('exo-screen', 'exo-conditional') for row in rows))
        self.assertEqual({'exo','libmpv','vlc'}, {row['engine'] for row in rows if row['decoderMode']=='automatic'})
        self.assertEqual(8, sum(row['engine']=='libmpv' and row['decoderMode']=='automatic' for row in rows))

    def test_cohort_binds_name_apks_and_renderer(self):
        cutoff = dt.datetime.fromisoformat('2026-10-18T07:00:00+08:00')
        base = night.cohort_name('night', cutoff, 'a'*64, 'b'*64, 'gpu-next')
        for name, main, test, renderer in [('other','a'*64,'b'*64,'gpu-next'),
                ('night','c'*64,'b'*64,'gpu-next'), ('night','a'*64,'c'*64,'gpu-next'),
                ('night','a'*64,'b'*64,'gpu')]:
            self.assertNotEqual(base, night.cohort_name(name, cutoff, main, test, renderer))

    def test_plan_refuses_partial_fixture_coverage(self):
        legs = night.build_legs('updated', ('avc',), full_matrix=True)
        with self.assertRaisesRegex(AssertionError, 'av1-1080p24.mkv'):
            night.verify_fixture_coverage(legs, [{'file':'avc-1080p24.mkv'}])
        night.verify_fixture_coverage(legs, [{'file':'avc-1080p24.mkv'}, {'file':'av1-1080p24.mkv'}])

    def test_plan_rejects_names_that_runner_cannot_accept(self):
        for name in ('夜01', '../night', 'x'*21, 'night-01'):
            with self.subTest(name=name), patch('sys.argv', ['prepare-power-night.py', '--output', 'unused',
                    '--serial', 'unused', '--name', name, '--av1-before', '2026-10-18T07:00:00+08:00',
                    '--artifact-report', 'unused']):
                with self.assertRaisesRegex(AssertionError, 'ASCII'):
                    night.main()


if __name__=='__main__': unittest.main()
