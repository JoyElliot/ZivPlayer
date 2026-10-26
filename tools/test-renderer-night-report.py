import copy
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('report',Path(__file__).with_name('report-renderer-night.py'))
report=importlib.util.module_from_spec(spec);spec.loader.exec_module(report)

def fixture():
    conditions=['libmpv@gpu','libmpv@gpu-next']
    identity=dict(zip(report.IDENTITY,['avc','fixturehash','automatic','current-app','cohort','main','test']))
    block=dict(identity,complete=True,conditions=conditions,a=conditions[0],b=conditions[1],
               comparisonBlock='block1',aMinusBMw=100,conditionMeanPowerMw=dict(zip(conditions,[1000,900])))
    rows=[dict(identity,engine='libmpv',requestedMpvVideoOutput=c.split('@')[1],actualDecoder='mediacodec',
               actualSoftwareDecoder=False,batteryStratum='normal',temperatureStartC=34.5,batteryStartPercent=75)
          for c in [conditions[0],conditions[1],conditions[1],conditions[0]]]
    return block,rows

class Stratification(unittest.TestCase):
    def test_decoder_mismatch_cannot_be_renderer_benefit(self):
        b,rows=fixture();rows[1]['actualDecoder']=rows[2]['actualDecoder']='mediacodec-copy'
        result=report.stratify(b,rows)
        self.assertIn('renderer_comparison_decoder_mismatch',result['aggregationIssues'])
        self.assertEqual(report.comparisons([result]),[])

    def test_apks_and_strata_do_not_pool(self):
        b,rows=fixture();baseline=report.stratify(b,rows)
        self.assertTrue(baseline['aggregationEligible'])
        for field,value in [('mainApkSha256','other'),('testApkSha256','other'),('fixtureSha256','other'),
                            ('measurementCohort','other'),('temperatureStartC',35.5),('batteryStartPercent',65)]:
            with self.subTest(field=field):
                bb=copy.deepcopy(b); rr=copy.deepcopy(rows)
                if field in report.IDENTITY:bb[field]=value
                for r in rr:r[field]=value
                second=report.stratify(bb,rr)
                self.assertEqual(len(report.comparisons([baseline,second])),2)
        self.assertEqual(report.comparisons([baseline,baseline])[0]['blockCount'],2)

    def test_boundary_and_missing_decoder_remain_unpooled(self):
        for field,value in [('temperatureStartC',35.0),('batteryStartPercent',69),('actualDecoder',None),
                            ('actualSoftwareDecoder',True),('batteryStratum',None)]:
            with self.subTest(field=field):
                b,rows=fixture();rows[0][field]=value
                self.assertFalse(report.stratify(b,rows)['aggregationEligible'])

if __name__=='__main__':unittest.main()
